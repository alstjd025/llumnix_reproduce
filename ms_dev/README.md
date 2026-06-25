# Llumnix on NXC7-1 — 사용 방법 (ms_dev)

이 폴더는 **NXC 계열 GPU 컨테이너에서 Llumnix v1을 띄우고 쓰는 방법**을 기록하고 스크립트화한 것이다.
현재 동작 구성: **neutral 모드 / full-mode-scheduling / load-balance**, 모델은 **Llama-3-8B-Instruct**,
**B200에 GPU당 인스턴스 1개**(현 `NXC7` 호스트는 **4장 → 인스턴스 4개**)를 올려 Llumnix 스케줄러가
로드밸런싱한다. GPU 개수는 `neutral.yaml`의 `DP_SIZE_LOCAL`·`nvidia.com/gpu`·discovery `--dp_size_local`
세 값으로 정해진다(현재 4; 2장 환경이면 2).

> 작성/검증: 2026-06-23 (NXC7-1, 2×B200). 스크립트화·자기완결화: 2026-06-24.
> **2026-06-24 마이그레이션: 새 호스트 `NXC7`(4×B200)로 이전** — 노드 이름이 `nxc7-1`→`nxc7`로
> 바뀌어 `lib.sh`가 hostname에서 유도하도록 했고, 이 호스트의 `gcsudo`(argv 단어분할) 이슈와
> AppArmor 이슈를 스크립트에 코드화했다(트러블슈팅 ⑤⑥). 4-GPU로 스케일업해 검증 완료(12/11/12/12 분산).
> **배경 지식은 전부 `notes/` 에 박제돼 있다.** 이 폴더 하나로 처음부터 재현 가능하다 — 외부 메모리 불필요.

---

## ⭐ 새 컨테이너에서 맨바닥부터 (COLD START)

이 컨테이너를 날리고 새로 만들면 **overlay 루트(`/`)에 있던 건 전부 사라진다**:
이 repo 클론, k3s, NVIDIA 토킷, `/usr/local/bin/gcsudo` 래퍼, Claude 메모리까지. 영속은
`/NHNHOME`(로컬 nvme xfs)뿐이다. **그래서 `ms_dev/`(스크립트·노트·패치)는 이제 git repo에
커밋돼 있다** — 이 repo(`alstjd025/llumnix_reproduce`)가 단일 진실원이고, 콜드스타트는 그냥 clone이다.
(예전엔 `/NHNHOME/llumnix-ms_dev`에 rsync 백업했으나, git 커밋으로 대체함.)

`/NHNHOME/k3s-containerd`(vLLM 25GB 이미지)와 HF 모델 캐시(`/NHNHOME/huggingface`)가 영속이면
**재pull·재다운로드 없음.**
> ⚠️ **다른 물리 호스트로 옮기면 `/NHNHOME`도 다른 디스크다.** 2026-06-24 `NXC7`로 이전했을 때
> `/NHNHOME`에는 HF 캐시는 있었지만 `k3s-containerd`(이미지)는 없었다 → vLLM 이미지를 알리바바에서
> 재-pull했다(네트워크 정상, 수 분). `gcsudo`도 새 호스트에선 셸 alias뿐이라 `00`/`01`이
> `ensure_gcsudo`로 래퍼를 다시 설치한다(자동).

새 컨테이너에서 순서대로:
```bash
# 1) repo clone (ms_dev가 repo 안에 들어있으므로 별도 복원 불필요)
git clone https://github.com/alstjd025/llumnix_reproduce.git /home/nxclab/llumnix

# 2) 0→4 한 흐름 (00이 토킷+k3s 설치, 01이 우회설정+기동, 03이 YAML 패치까지 자동 적용)
cd /home/nxclab/llumnix/ms_dev/scripts
./00-install-k3s.sh        # LAYER 0: NVIDIA 토킷 + k3s 설치 (설치만, 기동 안 함)
./01-prepare-k3s-host.sh   # LAYER 1: 컨테이너 우회설정 + k3s 기동
./02-cluster-prereqs.sh    # LAYER 2: device plugin + LWS
./03-deploy.sh             # LAYER 3: deploy 패치 적용 후 배포 (모델 로드까지 대기)
./04-smoke-test.sh         # 동작 + 로드밸런싱 확인
```
deploy YAML 수정분은 이 repo에 커밋돼 있다(`deploy/.../{neutral,gateway}.yaml`, `base/kustomization.yaml`).
`llumnix-deploy.patch`도 같은 내용을 담고 있어 `03`의 `ensure_deploy_patch`가 검사하지만, 커밋돼 있으면
"이미 적용됨"으로 skip한다. 작업 후엔 그냥 `git add -A && git commit && git push`로 보존한다.

---

## 0. 한눈에 (LLM 엔지니어용 번역)

평소엔 `vllm serve <model>` 을 셸에서 직접 띄운다. 여기서는 그 대신 **Kubernetes(k3s)에게
컨테이너 묶음을 띄우라고 명세서(YAML)를 제출**한다. Llumnix는 vLLM 앞단/옆단에 붙는 풀스택이라
다음 컨테이너들이 협력한다:

| 컨테이너 | 역할 | LLM 비유 |
|---|---|---|
| **gateway** (:8089) | OpenAI API 진입점. 토크나이즈→토큰수 계산→라우팅 | 똑똑한 LB + API 프론트 |
| **scheduler** | 어느 인스턴스로 보낼지 초기 라우팅(load-balance 정책) | 글로벌 스케줄러 |
| **redis** | 인스턴스 실시간 상태 저장소(CMS) | 클러스터 상태 DB |
| **neutral-0** | vLLM 엔진 2개(`vllm serve`가 컨테이너 안에서 GPU당 1개) + discovery 사이드카 | 우리가 아는 vLLM ×2 |

"배포(deploy)" = 이 명세서를 k3s에 `apply` 해서 위 컨테이너들이 GPU/네트워크에 배치되고
살아있게 관리되도록 하는 것.

---

## 1. 이 환경의 핵심 — 생명주기가 다른 3계층

스크립트를 3개 계층으로 나눈 이유다. **무엇이 언제 사라지는가**가 다르다.

```
LAYER 0  k3s+토킷 설치        ── 컨테이너 "재생성" 때 사라짐(overlay /) → 재생성 후 1번  00-install-k3s.sh
LAYER 1  호스트 우회설정      ── 컨테이너 "재생성" 때 사라짐  → 재생성 후 1번 재적용   01-prepare-k3s-host.sh
LAYER 2  클러스터 prereq      ── k3s 데이터가 살아있는 한 유지 → idempotent ensure     02-cluster-prereqs.sh
LAYER 3  Llumnix 워크로드     ── 그냥 namespace, 언제든 재배포 → deploy/test/teardown  03 / 04 / 99
```
> LAYER 0/1은 둘 다 "컨테이너 재생성 시 사라짐"이지만 나눈 이유: **00은 무거운 설치(apt·바이너리,
> 네트워크 필요), 01은 가벼운 mount/symlink 우회 + 기동.** k3s가 단순 재시작된 경우엔 00·01 둘 다
> no-op이라 그냥 03부터 하면 된다. 컨테이너 자체가 새로 떴을 때만 00→01이 실제로 일한다.

- 이 "호스트"는 사실 **GPU가 붙은 Docker 컨테이너**다(베어메탈 아님). 그래서 컨테이너 안에서
  k8s를 돌리려고 7가지 중첩(nesting) 이슈를 우회했고, 그중 일부(마운트/심링크)는 **컨테이너가
  새로 뜨면 사라진다.** → LAYER 1.
- k3s가 단순 재시작(`systemctl restart k3s`)된 경우엔 LAYER 1의 bind-mount가 PID1 ns에 살아있어
  보통 그대로 동작한다. **컨테이너 자체가 재생성된 경우에만** `01`을 다시 돌리면 된다.

---

## 2. 빠른 시작

```bash
cd /home/nxclab/llumnix/ms_dev/scripts

# (컨테이너가 방금 재생성됐을 때만) k3s+토킷 설치 — 이미 설치돼 있으면 skip
./00-install-k3s.sh

# (컨테이너가 방금 재생성됐을 때만) 호스트 준비 + k3s 기동
./01-prepare-k3s-host.sh          # k3s가 이미 Ready면 알아서 skip

# 클러스터 prereq 보장 (device plugin, LWS) — 이미 있으면 skip
./02-cluster-prereqs.sh

# Llumnix 배포 (모델 로드까지 대기, 수 분)
./03-deploy.sh

# 동작 + 로드밸런싱 확인
./04-smoke-test.sh
```

직접 호출 예:
```bash
kubectl port-forward -n llumnix svc/gateway 8089:8089 &
curl -s localhost:8089/v1/completions -H 'Content-Type: application/json' \
  -d '{"model":"meta-llama/Meta-Llama-3-8B-Instruct","prompt":"The capital of France is","max_tokens":16}'
```

정리:
```bash
./99-teardown.sh          # namespace만 삭제 (k3s/prereq는 유지)
./99-teardown.sh --k3s    # k3s까지 정지
```

---

## 3. 스크립트 설명

| 스크립트 | 계층 | 하는 일 | 재실행 안전성 |
|---|---|---|---|
| `lib.sh` | — | 공통 설정(NS/MODE/MODEL/PORT…)·헬퍼·`ensure_deploy_patch`·**`ensure_gcsudo`**(arg-보존 래퍼 설치). `NODE`는 hostname에서 유도. 다른 스크립트가 source | — |
| `00-install-k3s.sh` | 0 | NVIDIA Container Toolkit(apt) + k3s(`v1.35.5+k3s1`, `--snapshotter overlayfs`) 설치, `config.yaml` 작성, kubeconfig 배치. **설치만, 기동은 01** | 설치돼 있으면 skip |
| `01-prepare-k3s-host.sh` | 1 | containerd→xfs bind, `/dev/kmsg` 심링크, sysctl·`/proc/sys/net` bind(PID1 ns), **AppArmor-disable 드롭인**, k3s 재시작, taint 제거 | k3s Ready면 no-op (`--force`로 강제) |
| `02-cluster-prereqs.sh` | 2 | NVIDIA device plugin(v0.17.1) + LWS(v0.9.0) 설치, GPU·컨트롤러 준비 대기 | 설치돼 있으면 skip |
| `03-deploy.sh` | 3 | `ensure_deploy_patch`(새 클론이면 YAML 패치 적용) → `deploy/group_deploy.sh`로 kustomize apply + vLLM Ready 대기 | re-apply = 업데이트 |
| `04-smoke-test.sh` | — | `/v1/models`·`/v1/completions` 호출, N개 요청을 엔진별로 분산 집계 | 읽기 전용 |
| `99-teardown.sh` | 3 | namespace 삭제 (옵션 `--k3s`로 k3s 정지) | 멱등 |
| `sync-to-nhnhome.sh` | — | `ms_dev/` 전체를 `/NHNHOME/llumnix-ms_dev`로 백업(영속). `--restore`로 복원 | 멱등(mirror) |

> `notes/` = 예전 Claude 메모리(k3s 설치 레시피, 컨테이너 제약, sudo 제약, 환경 평가 등)를 박제한 배경 문서.
> `llumnix-deploy.patch` = deploy YAML 수정분(uncommitted)을 박제한 것 — `03`이 새 클론에 자동 적용.

모든 값은 환경변수로 덮어쓸 수 있다. 예:
```bash
NS=llumnix2 MODE=neutral/lite-mode-scheduling/load-balance ./03-deploy.sh
```

---

## 4. 무엇을 바꿨나 (기본 레포 대비)

기본 `deploy/`는 **Qwen3-30B-A3B-FP8(MoE) + wide expert-parallel + RDMA(DeepEP/NVSHMEM-IBGDA)**
전제로 튜닝돼 있다. 이 환경엔 RDMA(`/dev/infiniband/`)가 없고 GPU도 2장이라, 다음을 수정했다.
(파일은 `deploy/neutral/full-mode-scheduling/load-balance/` 와 `deploy/base/`)

- **`base/kustomization.yaml`**: `monitoring.yaml` 제거 (Prometheus-Operator CRD 없음).
- **`neutral.yaml`**:
  - 모델 → `meta-llama/Meta-Llama-3-8B-Instruct`, `vllm serve` 명령을 dense 구성으로 단순화
    (MoE/EP/DP/DeepEP/NVSHMEM 플래그·env 전부 제거 → `--tensor-parallel-size 1 --max-model-len 8192
    --gpu-memory-utilization 0.6 --async-scheduling`, GPU당 서버 1개).
  - 모델 소스 = **Lustre의 기존 HF 캐시**: `VLLM_USE_MODELSCOPE=false`, `HF_HUB_OFFLINE=1`,
    `TRANSFORMERS_OFFLINE=1`, `HF_HUB_CACHE=/hf-cache/hub`. `/NHNHOME/huggingface` 를
    hostPath로 `/hf-cache`에 RO 마운트. → **다운로드·HF 토큰 불필요.**
  - GPU `4→2`(requests·limits), `DP_SIZE_LOCAL 4→2`, discovery `--dp_size_local 4→2`.
  - **securityContext의 `IPC_LOCK`/`SYS_RAWIO` cap 제거** (트러블슈팅 ① 참고).
  - **`imagePullPolicy: Always → IfNotPresent`** (트러블슈팅 ② 참고).
- **`gateway.yaml`**: modelscope 토크나이저 다운로드 initContainer 삭제 → 같은 HF 캐시 마운트,
  `--tokenizer-path`를 Llama3 스냅샷 디렉토리로, `--max-model-len 40960→8192`.

> 이 수정들은 레포 안에 uncommitted 상태로 들어가 있다. (`git status`로 보임)

---

## 5. 트러블슈팅 (이 환경에서 실제로 겪은 것)

**① 파드가 `StartError: unable to apply caps: operation not permitted` (exit 128)**
vLLM 컨테이너가 `IPC_LOCK`/`SYS_RAWIO` capability를 추가하려는데, 이 Docker 컨테이너의 capability
bounding set(`CapBnd a92c35fb`)에 그게 없어서 자식 컨테이너에 줄 수 없다. 두 cap은 RDMA/GDR용이라
dense·no-RDMA 추론엔 불필요 → **제거**. (새 GPU 파드가 cap을 추가하면 먼저
`grep CapBnd /proc/self/status`로 가능 여부 확인.)

**② vLLM 이미지 `ErrImagePull` (`connection reset`), 그런데 이미지는 이미 캐시됨**
`imagePullPolicy: Always`라 캐시를 무시하고 25GB 이미지를 재-pull → 알리바바 베이징 레지스트리의
auth 토큰 발급이 간헐적으로 끊김. **`IfNotPresent`로 바꾸면** 캐시된 이미지를 오프라인으로 사용.

**③ `/v1/chat/completions` 가 400 "Invalid OpenAI request"**
이 Llumnix gateway 빌드는 **`/v1/completions`(텍스트 컴플리션)만** 지원한다. chat 포맷은 미구현.
`/v1/models`는 동작. 채팅 템플릿이 필요하면 클라이언트 쪽에서 프롬프트를 직접 구성해야 한다.

**④ 모델이 안 떠요 / 로그 보기**
```bash
kubectl get pods -n llumnix -o wide
kubectl logs -n llumnix neutral-0 -c vllm --tail=80          # 현재
kubectl logs -n llumnix neutral-0 -c vllm --previous --tail=80   # 직전 크래시
kubectl describe pod neutral-0 -n llumnix                    # 종료 사유/이벤트
```
참고: vLLM 로그의 `ERROR: ... result=11` (persistenced/MPS 관련 NVML 경고)은 **양성**이다. GPU는 정상.

**⑤ `00`/`01`이 `gcsudo`에서 즉사하거나 k3s install이 `env: '--write-kubeconfig-mode': No such file`로 실패**
이 호스트(NXC7, 4×B200)의 `gcsudo`는 **셸 alias**라서 비대화형 스크립트엔 아예 없고, 실제
escalator(setuid `exeCTNCmd`)는 **argv를 공백 기준으로 단어분할**한다 → 공백 포함 단일 인자
(`INSTALL_K3S_EXEC="server --flag val ..."`, `sh -c '<스크립트>'`)가 전부 깨진다.
→ `lib.sh`의 **`ensure_gcsudo`**가 argv를 임시 스크립트로 마샬링(`printf %q`)해 보존하는 래퍼를
`/usr/local/bin/gcsudo`에 설치한다. `00`/`01` 시작 시 자동 호출되며 멱등. (래퍼는 overlay에 있어
컨테이너 재생성 때 사라지지만 `00`/`01`이 다시 깐다.)

**⑥ 모든 파드가 `CreateContainerError` (`apparmor_parser: ... Read-only file system`, exit 226)**
호스트 커널 AppArmor=Y인데 컨테이너 securityfs가 read-only라 containerd가 프로파일을 못 올린다.
→ `01`의 [5/6] 단계가 containerd 드롭인
(`config-v3.toml.d/10-disable-apparmor.toml`, `disable_apparmor = true`)을 만든다. 이 드롭인은
**overlay rootfs**(데이터 루트 xfs가 아님)라 컨테이너 재생성 때 사라지므로 `01`이 매번 재생성한다.

**⑦ `kubectl port-forward`로 gateway 호출이 간헐적으로 끊김(특히 긴 요청)**
샌드박스 환경 이슈일 수 있다. 확실히 검증하려면 **클러스터 내부에서** 직접 치면 된다:
```bash
kubectl exec -n llumnix neutral-0 -c vllm -- \
  curl -s gateway:8089/v1/completions -H 'Content-Type: application/json' \
  -d '{"model":"meta-llama/Meta-Llama-3-8B-Instruct","prompt":"The capital of France is","max_tokens":12}'
```
엔진 단독 확인은 같은 방식으로 `localhost:8000`(8000~8003 중 하나)을 친다.

---

## 6. 제약 / 다음 단계

- **이 4-GPU 환경에선 불가**: live request migration, PD 분리(kvt), PD-KVS, SLO-aware. 전부 KV 전송에
  RDMA(`/dev/infiniband/`)가 필요한데 **이 컨테이너엔 IB가 노출돼 있지 않다**(cgroup이 open을 EPERM 차단).
  현재 구성은 `LLUMNIX_ENABLE_MIGRATION=0`(초기 라우팅 로드밸런싱만).
- **왜 안 되나 / 8-GPU에선 되나**: 4-GPU는 8-GPU 서버의 절반 조각 테넌트라 IB 미노출. **8-GPU 풀노드엔 IB가 딸려와
  migration이 RDMA로 동작**한다(동료 노드에서 확인). 전체 조사·코드추적·8-GPU 켜는 법은
  **[`notes/migration-rdma-and-8gpu-handoff.md`](notes/migration-rdma-and-8gpu-handoff.md)** 에 정리(다음 작업 핸드오프).
- **확장 후보**: 두 번째 동일 B200 호스트를 k3s **worker로 join** → 멀티노드 cross-node 로드밸런싱.
  (LAYER 1 우회설정을 그 호스트에도 적용하고 `k3s agent`로 join. 아직 미구현.)

---

## 7. 향후 스크립트화 아이디어 (TODO)

- ✅ **(완료 2026-06-24) k3s/토킷 설치 스크립트화** — `00-install-k3s.sh`. 예전엔 설치가 메모리
  레시피에만 있어 새 컨테이너에서 `01`이 "k3s 없음"으로 실패했음. 이제 `00`이 메움.
- ✅ **(완료) 자기완결화** — 메모리를 `notes/`로, YAML 수정분을 `llumnix-deploy.patch`로 박제하고
  `/NHNHOME`에 백업(`sync-to-nhnhome.sh`). 외부 메모리 없이 이 폴더만으로 재현 가능.
- `03-deploy.sh` 를 **모델 파라미터화**: 지금은 YAML이 Llama3로 하드코딩. `MODEL_ID` +
  스냅샷 경로를 받아 kustomize patch/`sed`로 주입하면 모델 교체가 한 줄이 된다.
- `01` 의 bind 단계들을 **systemd `ExecStartPre=` / oneshot 유닛**으로 등록해서 k3s 시작 시
  자동 적용(수동 실행 제거). 단, 컨테이너 재생성 시 유닛 자체가 사라지므로 entrypoint 훅이 더 확실.
- 2번째 노드 join 스크립트(`05-join-worker.sh`): 서버 토큰 + `k3s agent` + LAYER1 우회.
- `make`/단일 `up.sh` 래퍼로 `01→02→03→04` 묶기.
