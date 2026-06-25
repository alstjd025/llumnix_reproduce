# Migration · RDMA 조사 + 8-GPU 노드 핸드오프

작성: 2026-06-25 (NXC7, 4×B200 환경에서 조사). 대상 독자: **8-GPU 풀노드로 옮긴 뒤 새로 깔린 Claude**.
목적: 4-GPU 환경에서 migration을 켜보려다 알아낸 것 전부 + 8-GPU에서 바로 이어서 할 일.

---

## 0. TL;DR

- **베이스 셋업(k3s in GPU 컨테이너 + Llumnix neutral/load-balance)은 `ms_dev/scripts/00~04`로 완전 자동화**돼 있다. 8-GPU 노드에서도 그대로 쓰면 된다(아래 §4의 스케일 값만 8로).
- **이 환경(4-GPU)에서 migration은 못 켰다.** 이유는 RDMA 부재인데, 그 RDMA 부재의 근본 원인은 **"4-GPU는 8-GPU 서버의 절반 조각 테넌트라 InfiniBand가 컨테이너에 노출 안 됨"**(cgroup이 `/dev/infiniband` 열기를 EPERM으로 차단).
- **8-GPU 풀노드에는 IB가 딸려온다**(동료 노드 NXC11에서 `ibv_devinfo` → 10 HCA PORT_ACTIVE 확인). 그래서 **8-GPU에서는 migration이 RDMA로 동작 가능**하다. §5가 그 켜는 법.

---

## 1. Llumnix가 migration을 어떻게 하는가 (코드 추적 결과)

이미지: `vllm 0.12.1.dev`, `llumnix`·`mooncake`·`nixl 0.8.0` 모두 `/usr/local/lib/python3.12/dist-packages/` 에 설치됨.

- **KV 전송 엔진 = Mooncake** (NIXL 아님). migration frontend가 `from mooncake.mooncake_connector_v1 import ...` 한다.
  - `llumnix/migration_frontend/vllm_v1/mooncake_migration_frontend.py`
- **migration은 `--kv-transfer-config`(KV connector)가 vllm에 있어야 켜진다.** 없으면 Llumnix가 자동 비활성:
  - `llumnix/vllm_llumlet_proxy.py:50` → `"Migration enabled but no kv_transfer_config found. Migration will be disabled."` → `LLUMNIX_ENABLE_MIGRATION=0` 강제.
  - 우리 neutral.yaml은 dense 단순화하며 `--kv-transfer-config`를 들어냈다 → 그래서 꺼졌던 것(=RDMA 때문이 아니라 config 때문).
- **connector 두 종류** (`vllm_llumlet_proxy.py`):
  - `HybridConnector` → `KVTMigrationFrontend`. **naming 서버 필요**(`connect_naming(naming_url)`), 추가 인프라. 회피.
  - `MooncakeConnector` → `MooncakeMigrationFrontend`. **P2P 핸드셰이크, 외부 서버 불필요.** 단독 migration엔 이게 단순.
  - `kv_role:"kv_both"` 이면 instance type이 **NEUTRAL** 유지 (engine_client.py:201). 즉 neutral 모드 + migration 조합이 됨.
- 레포의 표준 예시(참고): `deploy/pd*/`, `deploy/slo-aware/` 의 vllm 커맨드에
  `--kv-transfer-config '{"kv_connector":"HybridConnector","kv_role":"kv_both","kv_connector_extra_config":{"backend":"kvs+kvt", ... "protocol":"rdma","device_name":"erdma_0", "global_segment_size":"50gb"}}'`
  형태가 있다. backend 값: `kvt`, `kvt+migration`, `kvs+kvt`. **protocol·device_name이 RDMA를 가리킴.**

## 2. RDMA vs NVLink vs TCP — 전송로 정리

- **NVLink** = 노드 *안* GPU↔GPU 직통(이 환경 NV18 = 18링크×53GB/s≈956GB/s, P2P 전부 OK). `/dev/nvidia*`로 접근, cgroup 허용됨. **TP=N의 GPU간 통신은 이걸 씀**(RDMA 아님).
- **RDMA/InfiniBand** = 노드 *간*(머신↔머신) NIC 경유. `/dev/infiniband`로 접근.
- **Mooncake의 transfer engine(`engine.so`)이 가진 전송로는 RDMA와 TCP 둘뿐. NVLink transport 없음.**
  - `mooncake_connector_v1.py:376`: `engine.initialize(host, "P2PHANDSHAKE", "rdma", "")` — **protocol "rdma" 하드코딩**, `MOONCAKE_PROTOCOL` env 안 읽음.
  - RDMA 디바이스가 없으면 `"No RDMA devices found"` 후 **TcpTransport로 그레이스풀 폴백**(rc=0). 즉 RDMA 없어도 init은 통과하나 전송은 TCP(=GPU→호스트DRAM→소켓→DRAM→GPU, 느림).
- 결론: **NVLink로 migration하려면 Mooncake 말고 NCCL/CUDA-IPC 쓰는 connector가 필요**(이 빌드엔 없음). 정공법은 RDMA.

## 3. 4-GPU(NXC7)에서 migration이 막힌 진짜 이유

- `/dev/infiniband` 노드 없음. `gcsudo mknod`로 만들어도 **open() → EPERM**(cgroup v2 device 필터가 major 231/10:121 차단). `/dev/null`은 열리는데 IB만 막힘 = 화이트리스트 누락. **컨테이너 생성 시점 정책이라 안에서 못 바꿈.**
- 왜? **4-GPU = 8-GPU 물리서버의 절반 조각(공유 테넌트)**. 증거: GPU 4개인데 호스트 sysfs(`/sys/class/infiniband`)엔 **IB NIC 10개**(풀노드분)가 보임. 공유 HCA에서 RDMA 테넌트 격리가 어렵고 rail-optimized 설계가 노드단위라, **IB는 풀노드(8-GPU) 임대에만 노출**된다.
- migration 켜기 실험 결과(참고, 반복 말 것): `LLUMNIX_ENABLE_MIGRATION=1` + `--kv-transfer-config '{"kv_connector":"MooncakeConnector","kv_role":"kv_both",...}'` + 엔진별 `VLLM_MOONCAKE_SIDE_CHANNEL_PORT` 오프셋까지 줬더니 — vllm은 config 수락했으나 **EngineCore가 `_initialize_kv_caches → initialize_from_config`(KV connector가 87GB KV 메모리 등록)에서 크래시**(crashloop, exit). RDMA 부재 + "한 파드에 독립 엔진 4개"(kv_port 14579 충돌) 토폴로지가 겹친 결과로 추정. → known-good(migration OFF)로 롤백함.

---

## 4. 8-GPU 노드에서: 베이스 셋업 (먼저 이거부터)

1. repo clone (이 repo가 단일 진실원):
   ```bash
   git clone https://github.com/alstjd025/llumnix_reproduce.git /home/nxclab/llumnix
   cd /home/nxclab/llumnix/ms_dev/scripts
   ```
2. 콜드스타트 0→4 (스크립트가 hostname·gcsudo·apparmor 등 환경차이 자동 처리):
   ```bash
   ./00-install-k3s.sh && ./01-prepare-k3s-host.sh && ./02-cluster-prereqs.sh
   ./03-deploy.sh && ./04-smoke-test.sh
   ```
3. **8-GPU로 스케일**: `deploy/neutral/full-mode-scheduling/load-balance/neutral.yaml` 에서
   `DP_SIZE_LOCAL`·`nvidia.com/gpu`(req+lim)·discovery `--dp_size_local` 세 값을 **4→8**. (배경: README §4)
   - ⚠️ 단, migration까지 갈 거면 "한 파드에 8엔진"은 §3의 kv_port 충돌을 악화시킨다. **migration이 목표면 §5의 "파드당 엔진 1개" 토폴로지를 먼저 고려.**

## 5. 8-GPU 노드에서: migration 켜기 (핵심 핸드오프)

**전제 먼저 검증** — 컨테이너에 IB가 실제 노출됐는지(풀노드면 됨):
```bash
ls -l /dev/infiniband/        # uverbs*/rdma_cm 있어야
ibv_devinfo                   # HCA 여러 개 + state PORT_ACTIVE 면 OK
python3 -c "import os;os.open('/dev/infiniband/uverbs0',os.O_RDWR)"  # EPERM 안 나야 함
```
→ 여기서 HCA가 안 잡히면 8-GPU인데도 IB 미노출이니 **운영자에게 IB 패스스루 요청**(아래 §6).

IB가 되면, 순서:

1. **동료(NXC11, 8-GPU, migration 동작 중) 설정을 받아 그대로 이식하는 게 최선.** 동료 vllm **파드 안**에서:
   ```bash
   ps aux | grep "vllm serve"            # --kv-transfer-config 전체 (connector/backend/protocol/device_name)
   env | grep -iE 'MOONCAKE|LLUMNIX|KV|NCCL|GLOO'
   ibv_devinfo | head                    # 파드 안에서도 HCA 잡히는지
   ```
   그리고 동료 **파드 YAML**에서 `/dev/infiniband`를 파드에 어떻게 넣었는지(hostPath 마운트 / RDMA device-plugin / `resources: rdma/hca`)와 securityContext(privileged/IPC_LOCK) 확인.

2. **vllm 파드에 IB 디바이스 주입** (바깥 컨테이너에 IB 있어도 *중첩된 파드*는 별도 `/dev`라 또 넣어야 함). `neutral.yaml`에:
   - `volumes`: `hostPath: {path: /dev/infiniband, type: Directory}` → `volumeMounts: /dev/infiniband`
   - 또는 동료가 RDMA device-plugin을 쓰면 그 방식. securityContext에 IB 접근 권한.
   - (이 환경 CapBnd에 IPC_LOCK 없었음. 8-GPU 풀노드는 다를 수 있으니 `grep CapBnd /proc/self/status`로 재확인. memlock은 `ulimit -l`=unlimited면 IPC_LOCK 없이도 reg_mr 될 수 있음.)

3. **config**:
   - `LLUMNIX_ENABLE_MIGRATION=1`
   - vllm serve에 `--kv-transfer-config` 추가 — **동료 값을 그대로 쓰는 게 1순위.** 동료 값을 못 받으면 출발점:
     `'{"kv_connector":"MooncakeConnector","kv_role":"kv_both","kv_connector_extra_config":{}}'`
     (HybridConnector를 쓰면 naming 서버가 필요하니 동료가 그걸 어떻게 띄웠는지 확인.)
   - **토폴로지: 파드당 엔진 1개** 권장(LWS size 키우거나 멀티 파드). 그래야 §3의 `kv_port=14579`/side-channel 포트 충돌이 없다. "한 파드 N엔진"은 migration엔 부적합.

4. **검증**: 파드 Ready 후, 한쪽 인스턴스에 부하 몰아주고 scheduler 로그/`redis`의 `instance_status`에서 **migrate_in/out** 카운터가 오르는지, gateway 로그에서 migration 이벤트가 보이는지 확인.

## 6. 운영자에게 (IB가 8-GPU인데도 안 보일 때만)

> "호스트 패브릭은 정상(`/sys/class/infiniband`에 mlx5 다수, `rdma link` ACTIVE)인데 컨테이너 cgroup이 `/dev/infiniband` 열기를 EPERM으로 막는다. 동료의 NXC11(8-GPU)처럼 `--device /dev/infiniband` + `--ulimit memlock=-1`(+가능하면 `--cap-add IPC_LOCK`)로 재기동해달라."

---

## 7. 새 Claude에게 처음 할 말 (복붙용)

> 이 디렉토리는 llumnix(OSDI'24) 재현 repo야. `ms_dev/README.md`와 `ms_dev/notes/`를 먼저 다 읽어줘.
> 특히 `ms_dev/notes/migration-rdma-and-8gpu-handoff.md`가 직전 작업 핸드오프야.
> 지금 환경은 **8-GPU 풀노드**라서 이전 4-GPU 환경과 달리 **InfiniBand가 노출돼 있을 것**(`ibv_devinfo`로 먼저 확인).
> 할 일: (1) `ms_dev/scripts/00~04`로 베이스 셋업(8-GPU로 스케일), (2) 핸드오프 §5대로 **migration까지 켜기**.
> 막히면 §3의 이미 검증된 실패를 반복하지 말고, 동료 NXC11의 working 설정을 받아 이식하는 §5-1을 우선해.
> **※ 2026-06-25 NXC13에서 (1)·(2) 모두 완료. migration 켜졌다(엔드투엔드 동작). 작동 레시피는 §9.**
> **§8은 틀린 중간기록(읽되 결론은 믿지 말 것), §9가 진실원.** 핵심: Blade-KVT(HybridConnector `kvt+migration`) +
> `naming_url:file:` + 엔진별 `BLLM_KVTRANS_PORT_BASE` + 스케줄러 `--colocated-rescheduling-mode=true`.

> ⚠️ 이 핸드오프 문서가 새 노드의 clone에 있으려면 **이 변경이 GitHub에 push돼 있어야 한다**(아래).

---

## 8. 8-GPU(NXC13)에서 실제로 해본 결과 — 2026-06-25 (중요 업데이트)

> ⚠️ **정정(2026-06-25 후반): 이 §8의 "migration은 못 켰다" 결론은 틀렸다. migration은 켜진다.**
> §8.3은 MooncakeConnector(import 깨짐)와 잘못된 토폴로지 가정으로 막힌 *중간 기록*이다.
> **실제 작동하는 레시피·근거는 아래 §9에 있다. §9가 최신 진실원.** §8은 디버깅 경로 기록으로만 남긴다.

대상 호스트가 바뀌었다: **NXC13, B200 ×8 풀노드**. §0/§7이 기대한 환경이다.

### 8.1 베이스 셋업: 성공
- `00~04` 그대로, `neutral.yaml` 세 값(`DP_SIZE_LOCAL`/`nvidia.com/gpu` req+lim/discovery `--dp_size_local`)을 **8**로 스케일 → **8엔진 Ready, 로드밸런싱 동작**(24요청 → 3/4/5/7/5/4/4/3). 커밋 `6d69a00`(known-good).
- 노드명은 `nxc13`(hostname 유도 정상). vLLM 25GB 이미지는 `/NHNHOME`에 없어 재-pull(수 분).

### 8.2 IB/RDMA 전제: **이번엔 충족됨** (§3의 블로커는 사라졌다)
- `ls /dev/infiniband` → `uverbs0~9`(`crw-rw-rw-`), `ibv_devinfo` → mlx5_0..9 `PORT_ACTIVE`.
- `python3 -c "import os;os.open('/dev/infiniband/uverbs0',os.O_RDWR)"` → **EPERM 안 남**(4-GPU에선 났음).
- `grep CapBnd /proc/self/status` → `a92c75fb` (= **IPC_LOCK 포함**, 4-GPU의 `a92c35fb`엔 없었음), `ulimit -l` = unlimited.
- 즉 **RDMA reg_mr 가능** → §3에서 크래시하던 "KV connector가 KV메모리 등록"을 **넘어섰다**. 막힌 지점이 RDMA가 아니라 그 위(connector/naming SW 계층)로 이동했다.

### 8.3 migration 시도와 **새 블로커(이미지 SW 갭)** — 검증된 실패, 반복 말 것
single-pod 유지(파드당 8엔진) + RDMA 주입(`privileged`+`IPC_LOCK/SYS_RAWIO/SYS_RESOURCE`, `ulimit -SHl unlimited`, `/dev/infiniband` hostPath, `VLLM_KV_TRANS_PROTOCOL=rdma`) + `LLUMNIX_ENABLE_MIGRATION=1`로 두 connector를 모두 시도. 코드 추적으로 알아낸 것:

- **side_channel_port 공식**(mooncake_connector_v1.py:200): `6557 + dp_rank*tp`. 우리 8엔진은 전부 독립 `vllm serve`(dp_rank=0,tp=1) → **전부 6557로 충돌**(=§3가 본 것). 엔진별 `VLLM_MOONCAKE_SIDE_CHANNEL_PORT=6557+i`로 회피 가능. mooncake rpc_port는 `get_rpc_port()`로 P2PHANDSHAKE 자동할당이라 14579 충돌은 실제론 안 남.
- **MooncakeConnector**(P2P, naming 불필요 — §1이 권한 길): vLLM `KVConnectorFactory` 빌트인 레지스트리에 **미등록**. `kv_connector_module_path:"mooncake.mooncake_connector_v1"`로 동적로드는 되나, 그 모듈이 **구버전 vLLM API에 의존**해서 import 자체가 깨짐:
  - `mooncake_connector_v1.py:25` `from vllm.attention.selector import backend_name_to_enum` → **이 vLLM엔 그 심볼 없음**.
  - 추가로 `from vllm.platforms import _Backend`(import 실패), `_Backend.FLASHINFER_VLLM_V1` / `PALLAS_VLLM_V1`(현 `AttentionBackendEnum`엔 그 멤버명 없음, line 432-433) 등 **여러 옛 API**를 씀. = 한 줄 shim 아님, 모듈 전체가 구버전용.
  - llumnix 에러 메시지가 직접 말함: *"ensure 'mooncake_connector_v1' is correctly installed **with the llumnix patch**"* → **이 이미지엔 그 패치가 미적용.**
- **HybridConnector**(backend `kvt`): 팩토리에 **빌트인 등록 + llumnix compat 셰임 보유**. import OK, KV 등록 OK. 그런데 migration frontend가 **naming 서비스 필수**:
  - `kvt_migration_frontend.py:126` `naming_url = kv_transfer_config.get_from_extra_config("naming_url","badbad")` → 기본 `badbad` → `connect_naming()` → **`RuntimeError: unrecognized naming url`**.
  - naming 클라이언트 = `EASNamingClient`(Alibaba EAS naming, `kvtransfer_ops.*.so`). blade_kvt의 KVS backend는 `naming_url=="fake://"`면 naming을 건너뛰지만(P2P), **llumnix의 kvt frontend는 fake:// 우회가 없어 무조건 connect_naming 호출** → 회피 불가.
  - 레포 참조본(`slo-aware/base`, `pd*`)은 전부 HybridConnector지만 **naming_url을 안 줌** → 그쪽은 migration frontend(naming)를 안 켜고 PD KV전송만 쓰는 구성이거나, EAS가 있는 환경 전제. 우리처럼 `LLUMNIX_ENABLE_MIGRATION=1`+neutral이면 naming에서 막힌다.

### 8.4 결론 + 다음 사람 할 일
- **환경(IB/RDMA)은 더 이상 블로커가 아니다.** 막는 건 **이 vLLM 이미지(`vllm:20260306-165123`)의 패키징**: (a) mooncake 모듈이 이 vLLM에 맞게 패치 안 됨, (b) KVT migration이 Alibaba EAS naming을 요구.
- **그래서 §5-1(동료 NXC11의 working 설정 이식)이 여전히 정답이다.** 단 이제 필요한 건 "config"만이 아니라 **NXC11이 쓰는 vLLM 이미지 태그**(= llumnix-patched mooncake가 들어간 빌드)와, KVT라면 그들이 띄운 **naming 서비스 주소(`naming_url`)**다. NXC11 vllm 파드에서:
  - `ps aux | grep vllm` 의 `--kv-transfer-config` 전체(특히 `kv_connector`/`naming_url`/`backend`),
  - `kubectl get pod <그쪽 vllm> -o yaml | grep image:` (이미지 태그),
  - `python3 -c "import mooncake.mooncake_connector_v1"` 가 그쪽 이미지에선 되는지(되면 패치된 빌드),
  - naming 파드/서비스가 있는지(`kubectl get pod,svc -A | grep -iE 'naming|eas|etcd'`).
- 대안: 이 vLLM 버전과 맞는 mooncake 휠(llumnix patch 포함)을 구해 이미지에 넣거나, EAS-호환 naming을 세우는 것 — 둘 다 외부 아티팩트 필요.
- ⚠️ **반복 말 것**: ① MooncakeConnector를 손으로 shim(여러 심볼 skew, 런타임에서 또 터짐), ② 현 이미지로 KVT+`naming_url=fake://`(llumnix frontend가 무시), ③ single-pod에서 포트만 바꿔 재시도(포트는 이미 원인 아님, SW 갭이 원인).

> 작업 후 known-good(`6d69a00`, migration OFF, 8엔진 로드밸런싱)으로 롤백해 둠. migration용 YAML 편집분은 **커밋 안 함**(블로커 때문). 위 분석이 단일 진실원.

---

## 9. ✅ migration 켜기 — 실제 작동 레시피 (2026-06-25, 검증됨)

§8의 결론을 뒤집는다. **이 공식 이미지(`vllm:20260306-165123`, vLLM 0.12.1)로 migration이 엔드투엔드로 동작한다.** 커밋 `967384f`에 적용돼 있다(neutral.yaml=4×TP2 + migration, scheduler.yaml=rescheduling). 막힌 건 환경이 아니라 **설정 3가지**였다.

### 9.1 왜 §8이 틀렸나 (핵심 교정)
- **Mooncake가 아니라 Blade-KVT(HybridConnector)가 정답.** 공식 문서 `docs/source/design/llumlet/request_migration.md:63`: *"Mooncake Transfer Engine은 vLLM >0.12.0에서 migration 미지원, Blade-KVT를 써라."* 우리 이미지는 0.12.1 → MooncakeConnector는 import부터 깨지는 게 정상. §8.3①은 헛수고.
- **EAS naming 불필요.** `docs/source/user_manual/llumlet_conf.md`의 "Migration Setup"에 `naming_url:"file:{dir}"` 템플릿이 있다. blade_kvt 네이티브에 **FSNAMING(파일 기반 naming)** 지원(`BLLM_KVTRANS_FSNAMING_*`). 공유 디렉토리 하나면 된다. §8.3의 "EAS 필요" 결론은 틀렸다.
- 공식 deploy 레시피는 전부 `LLUMNIX_ENABLE_MIGRATION=0`이라 "out-of-box로 migration 데모"는 없다. 직접 켜야 한다.

### 9.2 엔진 쪽 (`neutral.yaml`) — 켜는 데 필요한 것 전부
1. **vLLM 커맨드에 `--kv-transfer-config`** (엔진별로 값 다르게, 단일 파드 N엔진이라):
   `'{"kv_connector":"HybridConnector","kv_role":"kv_both","kv_connector_extra_config":{"backend":"kvt+migration","naming_url":"file:'"$NAMING_DIR"'","kvt_inst_id":"neutral-0-'"$i"'","rpc_port":'"$RPC_PORT"'}}'`
   - `backend:"kvt+migration"` ← migration을 켜는 값("kvt"만 쓰면 PD 전송만, migration 안 됨).
   - `naming_url:"file:/tmp/llumnix-kvt-naming"` ← 파일 naming, 시작 시 `mkdir -p`. (kv_role `kv_both` → NEUTRAL 유지)
   - 엔진별 고유 `kvt_inst_id`, 엔진별 `rpc_port`(=28000+i*TP_SIZE).
2. **`BLLM_KVTRANS_PORT_BASE=$((31218 + i*16))`** (엔진별!) ← **이게 빠지면 3/4 엔진이 죽는다.** ACCL/barex의 TCP 핸드셰이크 listener가 기본 포트 **31218/31219 고정**인데, 단일 파드 N엔진이 같은 IP라 `bind ... errno 98(EADDRINUSE)`로 충돌. 인스턴스별로 포트 윈도우를 띄워줘야 함. (증상: `xsimple_tcp_listener.cc:104 listen_v4, bind failed to port 31218`)
3. **RDMA 주입**: `securityContext.privileged:true` + caps `IPC_LOCK/SYS_RAWIO/SYS_RESOURCE`, 스크립트에 `ulimit -SHl unlimited`, `/dev/infiniband` hostPath 마운트, env `VLLM_KV_TRANS_PROTOCOL=rdma`, `LLUMNIX_ENABLE_MIGRATION=1`.
   - 이 환경 CapBnd=`a92c75fb`(IPC_LOCK 포함), memlock unlimited, `/dev/infiniband` open OK → 전부 충족(§8.2).

### 9.3 스케줄러 쪽 (`scheduler.yaml`) — 이게 빠지면 엔진이 준비돼도 영원히 idle
- **`--colocated-rescheduling-mode=true`** ← rescheduling 루프를 *실제로 돌리는* 플래그. ⚠️ **`--enable-rescheduling`은 효과 없다**(config엔 찍히지만 루프가 안 돈다). 참조본 `slo-aware/adaptive-pd/scheduler.yaml`이 colocated 모드를 씀.
- `--rescheduling-policies neutral_load,neutral_failover` (neutral 모드는 `neutral_load`가 부하기반 정책. 스케줄러 로그 `rescheduling_policy.go:93 Rescheduling initialized, policies:[neutral_load neutral_failover]`로 수락 확인).
- 데모용으로 임계값 낮춤: `--rescheduling-neutral-load-threshold 0.003`, `--rescheduling-load-balance-threshold 0.1`. (KV 캐시가 인스턴스당 ~154만 토큰이라 기본 임계 1은 사실상 안 걸린다.)

### 9.4 검증된 증거 (엔드투엔드)
- 엔진: `kv_transfer_impl.py: init kvt client/server ... protocols=[RDMA_DIRECT]`, ACCL가 실제 `mlx5_0~9` 잡음, `kvt_migration_frontend.py:95 MigrationFrontend initialized successfully` ×4.
- 스케줄러: 부하 불균형 시 `rescheduling_policy.go:132 Generate rescheduling pairs, count: 1`.
- 엔진 수신: `rpc_server.py:252 Received Migration request. MigrationParams(migration_type='TOKEN', mig_req_policy='SR', num_reqs=1, num_tokens=1024)`. → 스케줄러→llumlet→엔진 명령 경로 완전 동작.

### 9.5 완료된 KV 전송 한 건을 "보려면" (다음 사람용 튜닝)
관측의 catch-22: migration은 **불균형**에서 발동하는데 —
- 게이트웨이 경유 부하 → 스케줄러가 추적하지만 load-balance dispatch가 균등 → 불균형 안 생김 → 쌍 0개.
- 인스턴스에 직접(`localhost:8000`) 부하 → 불균형은 생기지만 스케줄러 local account에 없는 요청 → 명령은 나도 `kvt_migration_frontend.py:254 No requests to migrate`.
→ **완료 데모엔 "스케줄러가 추적하는 불균형"이 필요.** 방법: (a) 정식 벤치(`deploy/benchmark/`)로 이질적 길이 부하를 게이트웨이에 흘려 자연 불균형 유도, (b) `--scheduling-policy flood`로 한쪽에 몰아주기, (c) failover migration(인스턴스 하나 죽여 `neutral_failover` 발동) — 이게 추적/불균형 둘 다 만족해 가장 확실. `loaded/saved`(`hybrid_modules.py:843 HybridScheduler status`)나 `scheduler_rescheduling_total`(observability 대시보드)로 확인.

### 9.6 운영 주의
- 데모용 낮은 임계값은 실부하에서 migration thrashing 위험 → 운영 시 `rescheduling-*-threshold` 올릴 것.
- 단일 파드 N엔진은 모든 포트(side_channel/rpc/ACCL/listener)가 IP당 충돌하니 **엔진별 포트 오프셋이 필수**. 멀티노드/파드당 1엔진이면 이 복잡성은 사라진다(각자 IP).
