# 환경 재구축과 실험 실행

**2026-10-10에 초기화된 컨테이너에서 이 순서로 실제로 다시 세웠고, 논문 값이 재현되는 것을 확인했다**(§6).
그때의 원 기록은 `archive/llumnix_reproduce/ms_dev/scripts/clean_backup_scripts/README.md`의 2026-10-10 항목이고,
그 이전 절차서는 같은 디렉토리의 `RESTORE-2026-08-28.md`다. 실험을 걸기 전에는 `exp-run` 스킬과
`.claude/skills/exp-run/traps.md`를 읽는다.

## 0. 이 노드에 대해 알아야 할 것

- **노드 NXC13은 GPU Docker 컨테이너다.** B200 8장, CPU 72코어, RAM 약 2.2 TB. 루트(`/`)는 overlay
  파일시스템이고, 로컬 xfs `/NHNHOME`(3.2 TB)과 Lustre(`/NHNHOME/storage`, `/NHNHOME/share`,
  `/NHNHOME/huggingface`)가 붙어 있다.
- **권한 상승은 `gcsudo <명령>`**(root로 실행). `sudo`는 안 된다. `gcsudo`는 명령마다 별도 mount namespace에서
  돌기 때문에, k3s가 보아야 하는 마운트는 `gcsudo nsenter -t 1 -m -- mount ...`로 PID 1(systemd)의 namespace에 건다.
- **컨테이너를 다시 만들면** k3s, `/usr/local/bin`, `/etc`의 설정, PID 1의 마운트, `/home/nxclab` 전체가 없어진다.
  남는 것은 Lustre와 GitHub의 두 저장소다(로컬 xfs `/NHNHOME`도 2026-10-06 초기화 때는 비어 있었다).
- 모델 가중치는 Lustre `/NHNHOME/huggingface/hub/`에 있다(Llama-3.1-70B-Instruct snapshot `1605565b…`).

## 1. 데이터가 어디 있나

| 무엇 | 어디 |
|---|---|
| 두 저장소 | GitHub. 바깥 `alstjd025/llumnix_reproduce`, 안쪽 `alstjd025/Agent_applications` |
| git이 나르지 않는 빌드·실행 필수 파일 | Lustre `/NHNHOME/storage/home_backup_2026-08-28/llumnix_reproduce/` (§2.1) |
| 도구(Go 툴체인, 바이너리 사슬 `bin-backup/`, `staging/`, 실험 체인 스크립트) | `/home/nxclab/tools/` — 2026-10-06 초기화 전 백업 `/NHNHOME/share/nxclab_backup_1006/home_misc_1006.tgz`에서 복원돼 있다 |
| 실험 결과 run (2026-08-27까지, 1,296개) | Lustre `/NHNHOME/storage/home_backup_2026-08-28/results_priority/results/` |
| 실험 결과 run (전부 1,841개, **EXP-108 이후 545개는 여기에만**) | 사용자의 맥 외장 SSD `/Volumes/volume1/nxc13_backup_2026-09-23/` |
| 토큰별 타이밍(`tbt_events.jsonl`)·agent 로그 | Lustre `.../home_backup_2026-08-28/results_archive_2026-10-05/` (zstd 압축) |

**논문 그림을 다시 그리거나 다시 채점하려면 EXP-108 이후 run을 맥 SSD에서 가져와야 한다**(2026-10-11 현재
아직 안 가져왔다). 꺼내는 명령은 `archive/llumnix_reproduce/ms_dev/scripts/clean_backup_scripts/README.md` §3.

## 2. 저장소와 git이 나르지 않는 파일

```bash
cd /home/nxclab && git clone <바깥 저장소> llumnix_reproduce && cd llumnix_reproduce
git clone <안쪽 저장소> Agent_applications
git config user.name alstjd025 && git config user.email alstjd025@gmail.com   # 두 저장소 모두. 컨테이너 초기화로 사라진다
```

### 2.1 백업에서만 오는 것 — 없으면 그 자리에서 멈춘다

```bash
B=/NHNHOME/storage/home_backup_2026-08-28/llumnix_reproduce
R=/home/nxclab/llumnix_reproduce; A=$R/Agent_applications/agent_motivation_experiment
for f in pkg/cms/cms.pb.go pkg/resolver/redis_discovery.pb.go \
         pkg/scheduler/llumlet/llumlet_server.pb.go pkg/scheduler/llumlet/llumlet_server_grpc.pb.go \
         patches/vllm-sched/VLLM_VERSION.txt; do cp -p "$B/$f" "$R/$f"; done
mkdir -p $R/.git/modules/lib && rsync -a $B/.git/modules/lib/sglang $R/.git/modules/lib/
rsync -a $B/lib/sglang/ $R/lib/sglang/
rsync -a $B/Agent_applications/agent_motivation_experiment/.venv $A/
rsync -a --ignore-existing $B/Agent_applications/agent_motivation_experiment/traces/ $A/traces/
for f in transcript_swe_calls.jsonl transcript_swe_calls_mix1500.jsonl; do
  cp -p $B/Agent_applications/agent_motivation_experiment/workloads/codingagent_request_level_poisson/data/$f \
        $A/workloads/codingagent_request_level_poisson/data/; done
```

| 파일 | 없으면 |
|---|---|
| `*.pb.go` 넷 | `go build`가 `undefined: InstanceStatus`로 실패한다. 저장소에 코드 생성 단계가 없어 이 사본이 유일하다 |
| `.git/modules/lib/sglang`, `lib/sglang` | 게이트웨이를 빌드할 수 없다(Rust 바인딩) |
| 실험 저장소의 `.venv` | 러너 Job이 첫 줄에서 죽는다 |
| `traces/`의 git 미추적 파일 | 한 시간 trace(`dyn60_shift_m2Am1B_b1045.csv`, 측정 구간 98,649건)가 없어 동적 실험을 못 돌린다 |
| swe transcript 둘 | md5 기준값은 `workloads/codingagent_request_level_poisson/data/README.md` |

### 2.2 바이너리 — `bin/`은 스케줄러·게이트웨이 파드에 hostPath로 마운트된다

| 파일 | md5 앞 8자리 | 어디서 | 무엇 |
|---|---|---|---|
| `bin/scheduler-exp07` | `6dc9f035` | `tools/staging/scheduler-exp107t` | EXP-108·109(논문 주 실험)가 쓴 바이너리. 2026-10-10 재현에 쓴 것 |
| (대안) | `063e1c85` | `tools/bin-backup/scheduler-exp07.pre-exp140` | EXP-131~139 |
| (대안) | `9e8f68b6` | `tools/staging/scheduler-usec` | 제출 시점 배포본(EXP-140의 마이크로초 계측 포함) |
| `bin/gateway-exp10` | `0efb664a` | Lustre 백업의 `bin/` | 2026-07-27 빌드 |

배포 중인 파드의 바이너리를 바꿀 때는 `cp`로 덮어쓰지 말고 새 이름으로 복사한 뒤 `mv`한다(실행 중인 파일은
`Text file busy`). **확인은 파드 안에서 `md5sum /proc/1/exe`** 한 가지뿐이다. 소스에서 빌드하는 명령은 `CLAUDE.md`.

## 3. k3s 클러스터

```bash
cd ms_dev/scripts
./00-install-k3s.sh        # NVIDIA Container Toolkit + k3s v1.35.5 설치, 시작은 하지 않는다
./01-prepare-k3s-host.sh   # 컨테이너 안에서 k3s를 돌리기 위한 우회 여섯 가지를 건 뒤 k3s 시작
./02-cluster-prereqs.sh    # GPU device plugin + LeaderWorkerSet, nvidia.com/gpu = 8 확인
```

01이 거는 우회는 2026-06의 셋업 기록(`archive/llumnix_reproduce/ms_dev/notes/llumnix-k3s-setup-notes.md`)의
이슈 일곱 가지를 코드로 옮긴 것이다: containerd 저장 위치를 로컬 xfs로 bind mount하고 `overlayfs` snapshotter 사용
(overlay 위의 overlay는 커널이 거부하고, `native`는 vLLM 이미지에서 디스크를 수백 GB 쓴다), `/dev/kmsg` →
`/dev/console`, 읽기 전용 `/proc/sys`의 kubelet 설정값 둘과 `net.*` 우회, containerd의 AppArmor 끄기, CCM 끄기.
⚠ apt가 최신판을 주므로 2026-10-10에는 Toolkit이 6월의 1.19.1이 아니라 1.20.1로 들어갔다(문제는 없었다).

## 4. Llumnix 배포

```bash
cd deploy && ./group_deploy.sh llumnix neutral/full-mode-scheduling/load-balance
```

- **`ms_dev/scripts/03-deploy.sh`는 쓰지 않는다**(2026-10-11에 archive로 옮겼다). 6월의 YAML 패치(Llama-3-8B,
  B200 2장용)를 적용하려 하는데, 지금 YAML은 2026-08-31에 실제 구성으로 맞춘 것이라 정방향으로도 역방향으로도
  깨끗이 적용되지 않는다.
- 지금 YAML의 구성: Llama-3.1-70B-Instruct, 인스턴스 4대 × TP=2(`DP_SIZE_LOCAL=4`), `--max-model-len 40960`,
  migration 끔(`LLUMNIX_ENABLE_MIGRATION=0`), 엔진 쪽 스케줄러 인자 없음, 스케줄러·게이트웨이는 `bin/`의 바이너리.
  2026-10-10에 렌더링한 매니페스트를 2026-08-28의 실제 클러스터 덤프(Lustre 백업의 `cluster_state/`)와 대조했고,
  다른 것은 조건마다 `set_scheduler_profiling.py`가 설정하는 플래그뿐이었다.
- 엔진 이미지(약 25 GB)는 2026-10-10에 3분 만에 받았다. 70B 가중치 로딩은 약 7분. **엔진이 떴는지는 파드의
  `ready`가 아니라 포트 넷에 직접 묻는다**(readiness probe가 없어 `ready`는 프로세스 시작만 뜻한다):

```bash
for p in 8000 8001 8002 8003; do kubectl -n llumnix exec neutral-0 -c vllm -- curl -s -m3 localhost:$p/v1/models | grep -c 70B; done
```

### 4.1 러너가 읽는 것 — 둘 다 없으면 러너 파드가 멈춘다

```bash
kubectl apply -f Agent_applications/agent_motivation_experiment/k8s/runner-rbac.yaml
# configmap/llumnix-model: switch_model.py의 모델 표에서 만든다. `--to`는 엔진·게이트웨이까지 재시작하므로 ConfigMap만:
cd ms_dev/scripts && python3 -I -c "
import json, importlib.util as u
s=u.spec_from_file_location('sm','switch_model.py'); m=u.module_from_spec(s); s.loader.exec_module(m)
d=m.MODELS['llama31-70b-b200-tp2']
print(json.dumps({'apiVersion':'v1','kind':'ConfigMap','metadata':{'name':'llumnix-model','namespace':'llumnix'},
 'data':{'MODEL_ID':d['model'],'MAX_MODEL_LEN':str(d['max_model_len']),'PROFILE_DIR':d['profile_dir'],
         'DP':str(d['dp']),'ENGINE_PORTS':','.join(str(8000+i) for i in range(d['dp']))}}))" | kubectl apply -f -
python3 switch_model.py --verify llama31-70b-b200-tp2      # 엔진·게이트웨이·러너 설정이 같은 모델을 가리키는지
```

`configmap/llumnix-model`이 없으면 러너 파드가 `CreateContainerConfigError`로 멈추고, 드라이버는 Job을 계속
기다리며 로그에 아무것도 남기지 않는다(2026-10-10에 59분을 잃었다).

### 4.2 아직 다시 세우지 않은 것

- **llm-d 스택**(`deploy/llmd/`, InferencePool 경로라 거절이 가능한 형태). llm-d 기준선을 다시 재려면 필요하다.
  계획과 절차는 `archive/llumnix_reproduce/ms_dev/notes/llmd-baseline.md`.
- Qwen2.5-72B·Llama-3.1-8B 함대. `switch_model.py --to <모델>`이 여덟 자리를 한 번에 바꾼다.

## 5. 실험 실행

```bash
cd Agent_applications/agent_motivation_experiment/k8s/exp07
# 정적 한 셀 (arm, 반복 번호, 도착률 rpm, 분)
SESSION_PREFIX_BASE=<새 접두어> setsid nohup ./run_exp108_t75.sh onerep fsv3capgnofrct75 1 2100 8 \
    > /home/nxclab/tools/<로그> 2>&1 < /dev/null & disown
# 한 시간 trace
SESSION_PREFIX_BASE=<새 접두어> ./run_exp109_hour.sh onerep fsv3capgnofrct75 1 shift
# 감시는 같은 턴에 (PID 형식)
/home/nxclab/tools/watch_experiment.sh <PID> /home/nxclab/tools/<로그> 'results/*<접두어>*' 0
```

- 드라이버는 조건마다 엔진을 cold restart한다(약 7분). 정적 8분 조건 하나가 약 15분, 한 시간 trace가 약 70분.
- **세션 접두어는 매번 새로 짓는다.** 결과 디렉토리 이름의 시각은 러너 파드 기준 UTC−7이다(KST는 +16시간).
- **결과 디렉토리는 조건이 시작될 때 생긴다.** 끝났는지는 드라이버 로그의 `DONE`과 프로세스 생존으로 본다.
- 확인할 것: 드라이버 로그의 `verified: every FluidServe flag matches what was applied`, 그리고
  `analysis_scripts/request_level/run_health.py 'results/*<접두어>*'`(엔진 4/4, 목표 도착률 달성).

### 5.1 채점

```bash
cd Agent_applications/agent_motivation_experiment
FS_SWE_TBT_MS=75 FS_SWE_TTFT_S=7 .venv/bin/python analysis_scripts/request_level/deadline_ladder_attainment.py \
    --runs 'results/*<접두어>*' --frac 0.95 --out <표.csv>
```

`FS_SWE_TBT_MS`·`FS_SWE_TTFT_S`가 없으면 로더가 채점을 거부한다(swe를 per-token 형태로 약속한 run을 e2e로
채점하지 않게 막는 검사).

## 6. 마지막 재현 확인 (2026-10-10)

EXP-108 정적 35 req/s의 FluidServe·PolyServe 각 2회와 EXP-109 한 시간 trace의 FluidServe 1회를 다시 쟀고,
모든 지표가 논문 값과 반복 간 편차 안에서 일치했다. 수치는 `evidence.md`의 재현 확인 절.

### 6.1 재현이 보장하지 않는 것

- **엔진의 `--api-server-count`가 git에 없다.** 2026-09-04부터 실행 중인 클러스터에 직접 패치하는 방식
  (`ms_dev/scripts/set_api_server_count.py`)으로 넣었다. EXP-108·109(08-31~09-02)는 기본값 1로 돌았고 지금 YAML도
  1이다. EXP-131~139가 어느 값으로 돌았는지는 확정되지 않았다(옛 STATUS 기록).
- preemption 횟수는 세션이 바뀌면 같은 조건에서 2.1~2.6배 달라졌다. 재현 판정에 쓰지 않는다.
