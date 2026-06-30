# 세션 기록 — 이슈 & 해결 + 스크립트 수정 (2026-06-25, NXC13)

이 문서는 **NXC7(4-GPU) → NXC13(8-GPU 풀노드) 이전 후 처음부터 셋업하면서 만난 모든 이슈와 해결책**, 그리고
**설치/배포 스크립트에 반영한·반영해야 할 수정사항**을 한곳에 모은 것이다. migration의 깊은 레시피는
[`migration-rdma-and-8gpu-handoff.md` §9](migration-rdma-and-8gpu-handoff.md)가 진실원이고, 여기는 그 §9를 포함한
전체 작업의 인덱스 + 운영 트러블슈팅이다.

## 0. 한 줄 결과
- 베이스 셋업(`00~04`) → **8-GPU 동작**, 이후 **4 인스턴스 × TP=2**로 재구성, **RDMA live migration 엔드투엔드 동작**까지 달성.
- 막혔던 IB/RDMA는 환경 변경으로 해소됐고, migration은 *환경*이 아니라 *설정 3가지*로 풀렸다.
- 최종 커밋: `6d69a00`(8-GPU) → `f0c66be`(4×TP2) → `967384f`(migration 동작) → `93708fa`(핸드오프 §9). **push는 안 함**(사용자 요청).

---

## 1. 셋업/환경 이슈 (스크립트로 고침)

| # | 이슈 | 증상 | 해결 |
|---|---|---|---|
| S1 | **repo 경로가 스크립트 가정과 다름** | repo가 `/home/nxclab/llumnix_reproduce`인데 `lib.sh`는 `REPO_DIR=/home/nxclab/llumnix` 하드코딩 → 배포가 엉뚱한 경로를 봄 | **`lib.sh` 수정**: `REPO_DIR`을 `lib.sh` 위치(`repo/ms_dev/scripts/lib.sh`)에서 자동 유도. 이제 어디 clone해도 동작. (세션 중엔 `REPO_DIR=... ` 수동 export로 우회) |
| S2 | **kubectl이 비대화형 PATH에 없음** | `bash ./03-deploy.sh`에서 `have kubectl` 실패 가능(로그인 셸엔 있으나 cron/CI엔 없음) | **`lib.sh` 수정**: `/usr/local/bin`을 PATH에 보장 + `KUBECONFIG` 폴백(`~/.kube/config` 없으면 `/etc/rancher/k3s/k3s.yaml`). 세션 중엔 `export PATH=/usr/local/bin:$PATH KUBECONFIG=...`로 우회 |
| S3 | **새 컨테이너에 git identity 없음** | `git commit` → `Author identity unknown ... unable to auto-detect email` | 수동: `git config user.name alstjd025 && git config user.email alstjd025@gmail.com`(기존 커밋 author와 동일). overlay라 컨테이너 재생성 시 또 사라짐 → **콜드스타트 체크리스트에 추가**(아래 §4) |
| S4 | hostname 유도 노드명 | 노드명이 `nxc7`→`nxc13`로 또 바뀜 | 이미 `lib.sh`가 hostname에서 유도 → **수정 불필요**(설계가 맞았음). 주석만 `nxc13`로 갱신 |

> S1·S2는 `lib.sh`에 **이번에 코드로 반영함**. 이제 `00~04`를 `REPO_DIR`/`PATH` 수동 export 없이 그냥 실행 가능.

---

## 2. 토폴로지 이슈 (YAML, 커밋됨)

| # | 이슈 | 해결 |
|---|---|---|
| T1 | 8-GPU로 스케일 | `neutral.yaml` 세 값(`DP_SIZE_LOCAL`/`nvidia.com/gpu` req+lim/discovery `--dp_size_local`)을 4→8. 커밋 `6d69a00` |
| T2 | **GPU 2개씩 × 4 인스턴스(TP=2) 요청** | `DP_SIZE_LOCAL 8→4`, `TP_SIZE 1→2`, discovery `--dp_size_local 4`, `nvidia.com/gpu`는 8 유지. GPU 핀닝을 `CUDA_VISIBLE_DEVICES`를 `TP_SIZE` 기반 범위(`seq`)로 일반화(인스턴스 i → GPU `[TP*i .. TP*i+TP-1]`). 커밋 `f0c66be` |
| **T3** | **TP=2가 깨진 출력(garbage)을 냄** ⚠️ | **증상**: TP=1은 정상인데 TP=2면 4엔진 전부 "The capital of France is"→"the best way to get the most out of the most out"(반복/무의미). `temperature=0`이라 결정적. **NVLink·P2P는 멀쩡**(`nvidia-smi topo -m`=NV18, P2P 전부 OK)인데도 깨짐. **원인**: vLLM의 **custom all-reduce 커널이 B200에서 TP>1일 때 잘못된 값**을 냄(NCCL 아닌 자체 P2P all-reduce). **해법**: vllm serve에 **`--disable-custom-all-reduce`**(NCCL all-reduce로 폴백) → 즉시 정상("Paris…", "2+2=4"). 커밋 `967384f` 위에 적용. **migration과 무관**(migration OFF에서도 동일했음). **TP>1 쓰면 항상 이 플래그 필요.** |

> TODO(스크립트화): 지금은 토폴로지가 YAML 하드코딩. `DP_SIZE_LOCAL`/`TP_SIZE`를 env로 받아 `03-deploy.sh`가 `sed`/kustomize로 주입하면 한 줄로 바뀜. (README §7 TODO와 동일)

---

## 3. Migration 이슈 (핵심) — 막힌 지점과 해결

> 깊은 레시피·로그 근거는 **[handoff §9](migration-rdma-and-8gpu-handoff.md)**. 여기는 "무엇이 왜 막혔고 무엇이 정답인지" 요약.

| # | 막힌 지점 | 잘못된 길(반복 말 것) | 정답 |
|---|---|---|---|
| M1 | connector 미등록 | `MooncakeConnector`가 vLLM 팩토리에 없음 → `Unsupported connector type` | (1차) `kv_connector_module_path` 추가 → 그래도 다음 단계서 깨짐 |
| M2 | **Mooncake import 깨짐** | `mooncake_connector_v1`이 구버전 vLLM API(`backend_name_to_enum`, `_Backend.*`) 참조 → `ImportError` | 공식 문서: **Mooncake는 vLLM>0.12.0에서 migration 미지원**(우리 0.12.1). → **Blade-KVT(HybridConnector) 사용** |
| M3 | naming 서비스 | `HybridConnector`가 `connect_naming` → `unrecognized naming url`. EAS(Alibaba) naming인 줄 알고 "불가" 결론 | **`naming_url:"file:/dir"`** (blade_kvt FSNAMING=파일 기반). EAS 불필요. 공유 디렉토리만 있으면 됨 |
| M4 | migration 미발동 | `backend:"kvt"`만 줌 → PD 전송만, migration 안 켜짐 | **`backend:"kvt+migration"`** |
| M5 | **포트 충돌(EADDRINUSE)** | 단일 파드 N엔진이 같은 IP라 ACCL TCP listener 기본 포트 `31218/31219` 충돌 → 3/4 엔진 사망. (`xsimple_tcp_listener.cc:104 bind failed to port 31218, errno 98`) | 엔진별 **`BLLM_KVTRANS_PORT_BASE=$((31218+i*16))`** + side_channel/rpc_port도 엔진별 오프셋 |
| M6 | **스케줄러가 명령을 안 냄** | `--enable-rescheduling`만 줌 → config엔 찍히나 rescheduling 루프가 안 돔(엔진은 준비됐는데 영원히 idle) | **`--colocated-rescheduling-mode=true`** 가 루프를 돌림. (`rescheduling_policy.go:103 Starting rescheduling loop`) |
| M7 | 임계값 과다 | KV 캐시가 인스턴스당 ~154만 토큰이라 기본 임계 1은 사실상 안 걸림 | 데모용 `--rescheduling-neutral-load-threshold 0.003`, `--rescheduling-load-balance-threshold 0.1`, 정책 `neutral_load,neutral_failover` |
| M8 | 완료 KV전송 관측 catch-22 | 게이트웨이 부하=균등(불균형 X), 직접 부하=스케줄러 미추적("No requests to migrate") | 명령 경로는 검증됨(엔진이 `Received Migration request` 수신). **완료 데모는 failover(인스턴스 kill) 또는 정식 벤치 필요** — 튜닝 영역, 블로커 아님 |

**RDMA 주입(공통):** `privileged:true` + caps `IPC_LOCK/SYS_RAWIO/SYS_RESOURCE`, `ulimit -SHl unlimited`,
`/dev/infiniband` hostPath, env `VLLM_KV_TRANS_PROTOCOL=rdma`, `LLUMNIX_ENABLE_MIGRATION=1`.
이 환경(NXC13)은 CapBnd=`a92c75fb`(IPC_LOCK 포함)·memlock unlimited·`/dev/infiniband` open OK라 전부 충족(NXC7 4-GPU와의 결정적 차이).

**검증된 증거:** KVT 2.0가 실제 `mlx5_0~9` NIC에서 `RDMA_DIRECT`, `MigrationFrontend initialized successfully`×4,
스케줄러 `Generate rescheduling pairs, count:1`, 엔진 `rpc_server.py:252 Received Migration request`.

---

## 4. 스크립트 수정 사항 (반영 + TODO)

### 4.1 이번에 반영함 (`lib.sh`)
- **REPO_DIR 자동 유도** — 클론 경로 무관(S1). 
- **PATH에 `/usr/local/bin` 보장 + KUBECONFIG 폴백**(S2).

### 4.2 콜드스타트 체크리스트에 추가할 것 (문서)
- **git identity**(S3): 컨테이너 재생성 후 커밋 전에
  `git config user.name alstjd025 && git config user.email alstjd025@gmail.com`.

### 4.3 TODO (다음에 스크립트화하면 좋은 것)
- `03-deploy.sh` **토폴로지 파라미터화**: `DP_SIZE_LOCAL`/`TP_SIZE`를 env로 받아 YAML에 주입(T2 수동편집 제거).
- **migration 토글**: `ENABLE_MIGRATION=1`이면 `neutral.yaml`에 KVT/RDMA 블록 + 엔진별 포트 + 스케줄러 `--colocated-rescheduling-mode`를 자동 주입하는 오버레이/패치. 지금은 YAML에 하드코딩(커밋 `967384f`).
- **migration 완료 데모 스크립트**(`06-migration-smoke.sh` 아이디어): 인스턴스 하나 `kubectl delete pod`로 죽여 `neutral_failover` 발동 → `loaded/saved` 카운터·`scheduler_rescheduling_total`로 확인.
- `00`/`01`이 git identity도 멱등하게 깔도록(선택).

---

## 5. 현재 상태 / 빠른 재현
- 배포 상태: **4 인스턴스 × TP=2, migration 활성, 정상 서빙**(`neutral.yaml`/`scheduler.yaml`은 커밋 `967384f`).
- 콜드스타트(이제 수동 export 불필요):
  ```bash
  git clone https://github.com/alstjd025/llumnix_reproduce.git /home/nxclab/llumnix_reproduce  # 어디든 OK
  cd /home/nxclab/llumnix_reproduce/ms_dev/scripts
  git -C /home/nxclab/llumnix_reproduce config user.name alstjd025
  git -C /home/nxclab/llumnix_reproduce config user.email alstjd025@gmail.com   # 커밋하려면
  ./00-install-k3s.sh && ./01-prepare-k3s-host.sh && ./02-cluster-prereqs.sh && ./03-deploy.sh && ./04-smoke-test.sh
  ```
- migration까지 켜려면: 커밋 `967384f`의 `neutral.yaml`+`scheduler.yaml`이 그대로 적용된다(이미 커밋됨). 끄려면 `6d69a00`의 두 YAML로.
