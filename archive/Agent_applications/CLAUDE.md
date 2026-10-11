# Agent Notes (repo root)

이 저장소(`alstjd025/Agent_applications`)는 agent workload 부하 실험 코드를 담습니다.
핵심은 [`agent_motivation_experiment/`](agent_motivation_experiment/) — SGLang server throughput이
멀쩡해 보여도 **application/job-level goodput이 무너지는 구간**을 SWE-bench Lite 기반
synthetic coding agent workload로 측정합니다.

이 파일은 인덱스이고, 실제 실험 규칙·명령은 하위 문서가 더 자세합니다.

> **바깥 저장소(`llumnix_reproduce`)와의 분담** — Llumnix 스케줄러 정책 실험을 할 때는
> 규칙과 상태가 저쪽에 있습니다.
> - **규칙**(반복해서 걸렸던 함정 A~F, 결과 판정 규칙, 서술 규칙): [`../CLAUDE.md`](../CLAUDE.md).
>   세션마다 항상 로드되므로 따로 열 필요는 없습니다.
> - **상태**(지금 무엇이 도는가, 어느 절이 정본인가, 인용하면 안 되는 수치):
>   [`../ms_dev/notes/STATUS.md`](../ms_dev/notes/STATUS.md). 실험이 끝날 때마다 바뀌므로
>   판단하기 전에 읽습니다.
>
> 이 파일과 `agent_motivation_experiment/CLAUDE.md`는 **이 저장소(부하 생성기·워크로드·
> 분석 스크립트)의 규칙**만 담습니다. 두 그림 스타일 절이 비슷해 보이지만 서로 다릅니다 —
> 여기 것은 job-level SGLang 실험용(파랑 = request/call-level, 빨강 = job-level)이고,
> `exp-plot` 스킬 것은 FluidServe 정책 비교용(파랑 = FluidServe, 빨강 = PolyServe)이라
> 색이 뜻하는 역할이 반대입니다. 섞어 쓰면 안 됩니다.

---

## Repo Layout

```
.
├── CLAUDE.md                          # 이 파일
├── .gitignore                         # .venv/, __pycache__/, *.pyc, *.log, *.csv
├── agent_motivation_experiment/       # ★ 메인 프로젝트. 자체 README/CLAUDE.md/AGENTS.md 보유
├── L40s_forwarding.md                 # L40S 노드용 SSH 포트포워딩 메모
└── safe_push.sh                       # 공유 GitHub 계정에서 push 시 cached creds 먼저 reject 후 push
```

> 참고: 이 repo는 부모 디렉토리 `/home/nxc/mskim/agent/` 안에 위치합니다.
> (NXC13 작업 호스트에서는 `/home/nxclab/llumnix_reproduce/Agent_applications`이고,
> 부모는 `llumnix_reproduce` 저장소입니다. 아래 백업·터널 스크립트 설명은 앞의 호스트 기준입니다.)
> 부모 쪽에는 결과 백업(`backup/`), 샘플 metrics(`swe_bench_test_data/`), 수동 SSH 터널 스크립트
> (`pipe_sglang.sh`, `kill_pipe_sglang.sh`) 등이 있지만 **버전관리 대상이 아닙니다.**

---

## Where to Look First

작업 종류별 진입 순서:

- **실험 실행/수정/디버깅 (SGLang@NXC7)** →
  1. [`agent_motivation_experiment/README.md`](agent_motivation_experiment/README.md) (Quick Start, 모드별 명령)
  2. [`agent_motivation_experiment/CLAUDE.md`](agent_motivation_experiment/CLAUDE.md) (core flow, server ownership, admission control 검사 절차)
  3. [`agent_motivation_experiment/AGENTS.md`](agent_motivation_experiment/AGENTS.md)
- **Llumnix 서빙(NXC13 k3s)에 부하 실험** → [`agent_motivation_experiment/k8s/README.md`](agent_motivation_experiment/k8s/README.md)
  (`--engine llumnix`, 인클러스터 runner 파드, 조건별 재시작, 서버측 metric 수집). 개념은 위 CLAUDE.md "Engine profiles" 절.
- **워크로드 어댑터 변경** →
  1. [`agent_motivation_experiment/workloads/AGENTS.md`](agent_motivation_experiment/workloads/AGENTS.md)
  2. 해당 workload 폴더의 `AGENTS.md` (예: `swe_bench_coding_tool_delay/`, `swe_bench_coding_parallel_tool_delay/`)
- **분석 스크립트** → [`agent_motivation_experiment/analysis_scripts/`](agent_motivation_experiment/analysis_scripts/)

---

## Top-level Rules

이 repo 차원에서 통용되는 규칙. 하위 프로젝트가 더 구체적인 규칙을 두면 그쪽이 우선합니다.

- **SGLang 라이프사이클은 `agent_motivation_experiment/run_experiment.py`가 owner.**
  부모 디렉토리의 수동 SSH 터널 스크립트(`pipe_sglang.sh` / `kill_pipe_sglang.sh`)는
  사용자가 명시적으로 저수준 디버깅을 요청한 경우에만 사용. 평소 admission/throughput 의문은
  `run_experiment.py` 의 `single` / `rate-sweep` / `poisson-sweep` 모드로 풀고, 결과 디렉토리
  (`metrics.csv`, `errors.log`, `agent_logs/`, `server_session/`)에서 답을 찾습니다.
- **SGLang 서버는 원격 SSH host `NXC7` 위에서 동작.** 러너가 tmux를 통해 시작/중지/세션 fetch까지 담당.
- **장기 실험은 로컬 tmux `motivation` 세션에서.** 세션이 없으면 `tmux new -s motivation`.
- **Push는 [`safe_push.sh`](safe_push.sh)** — 공유 GitHub 계정 자격증명이 캐시되어 충돌하는 환경이라,
  스크립트가 cached creds를 먼저 reject한 뒤 push.
- **L40S GPU 노드 작업이 필요하면** [`L40s_forwarding.md`](L40s_forwarding.md) 의 SSH 포트포워딩 명령부터 확인.

---

## Operating Conventions (전역)

- 사용자가 명확히 실행/수정/테스트를 요청하면 바로 진행한다.
- 요구가 애매하거나 실험 설정이 결과를 크게 바꿀 수 있으면 먼저 짧게 확인한다.
- 큰 코드 변경은 pseudocode/스켈레톤을 먼저 보여주고 피드백을 받은 뒤 진행한다.
- `metrics.csv`, `tbt_events.jsonl`, `application_*.csv` 등 **run output schema는 임의 변경 금지**.
  새 분석은 새 CSV을 추가하는 방향으로.
- 분석 스크립트를 옮기거나 편집한 뒤에는
  `python -m py_compile analysis_scripts/*.py run_experiment.py` 로 syntax sanity check.
- **실험 결과를 보여주거나 정리/요약/비교/플롯을 만드는 요청**("보여줘/정리해줘/결과 만들어줘/비교해줘")이 들어오면, 표·그림을 만들기 전에 measurement choice를 다시 적어 확인을 받는다. 무엇을 다시 적어야 하는지(metric / definition / run scope)와 이전 대화의 default를 재사용하지 않는 이유는 [`agent_motivation_experiment/CLAUDE.md`](agent_motivation_experiment/CLAUDE.md) 의 **"Confirmation Before Reporting Results"** 에 정본이 있다.
- **서술**은 비유·관용구를 쓰지 않고 시스템 분야에서 보편적으로 쓰이는 용어로, 축약하지 않고, 수치 옆에 그 수치의 맥락(분모·반복 횟수·어느 run)을 같이 적는다. 정본은 [`../CLAUDE.md`](../CLAUDE.md) 의 "서술 규칙".

---

## Project Halo (client wiring)

`run_experiment.py --halo-enabled` activates the client side of Project
Halo — **request-level** admission control on the sglang server (the
2026-05-19 refactor moved Halo from job-level to request-level: no job
pre-registration, no `halo_job_id`). Every request carries per-request
`halo_ttft_slo` / `halo_tbt_slo` / `halo_e2e_slo` fields. Requires the
server to also have `--halo-enabled`; the runner probes `GET /halo/status`
at startup and aborts on mismatch. Full guide:
[`agent_motivation_experiment/CLAUDE.md`](agent_motivation_experiment/CLAUDE.md)
§"Halo". New-workload integration:
[`agent_motivation_experiment/workloads/AGENTS.md`](agent_motivation_experiment/workloads/AGENTS.md)
§"Halo-compatible Workloads".

## Multi-node + request-level experiments

Experiments now run against **2 SGLang instances** (B200×2 each).
`run_experiment.py --node {nxc7-1,nxc7-2}` selects one per run (ssh host,
base URL, remote tmux). The `codingagent_request_level_poisson` workload
sends a flat Poisson stream of independent requests (replayed verbatim
from a recorded transcript) instead of multi-call jobs; parse it with
`analysis_scripts/parse_request_metrics.py`. See
[`agent_motivation_experiment/CLAUDE.md`](agent_motivation_experiment/CLAUDE.md)
§"GPU nodes" and §"Request-level workload".

## Quick Glossary

- **call goodput** — `call_latency < baseline_call_latency * tau` 인 LLM call 비율
- **job goodput** — `job_latency < baseline_job_latency * tau` 인 multi-call job 비율. **분모는 classifiable jobs만** — run 끝에서 잘린(`is_server_terminated=True`이고 reject/timeout 아닌) job은 outcome 미정으로 분모에서 제외 (자세한 정의는 [`agent_motivation_experiment/CLAUDE.md`](agent_motivation_experiment/CLAUDE.md) 의 "Run-boundary cutoffs" 참고)
- **tau (`--tau`)** — goodput 임계 배수 (보통 3.0)
- **baseline** — concurrency 1로 task별 기준 latency 수집한 run. `--baseline-dir` 로 다른 run에 주입
- **session-name (`--session-name`)** — 로컬 결과 디렉토리 suffix이자, 원격 SGLang 서버의 runtime session 이름. 로컬/원격 로그 매칭 키
- **execution_round** — `swe_bench_coding_parallel_tool_delay` 에서 같은 dependency barrier 뒤에 동시에 출발하는 call 그룹 (`wave` 아님)
- **transition_time** — `tool_delay` workload의 job summary 필드. 그 job에서 application-side로 simulate한 tool call interval 총합
- **NXC7 / NXC7-1 / NXC7-2** — 원격 SGLang 서버 SSH host alias. `NXC7-1`/`NXC7-2`는 B200×2 instance 2개이고 `--node {nxc7-1,nxc7-2}`로 선택
- **request-level Poisson** — `codingagent_request_level_poisson` workload. job/chain이 아니라 개별 LLM request가 Poisson(λ = requests/sec)으로 도착. transcript를 literal replay
- **transcript** — concurrency-1 `swe_bench_coding` run을 `--record-transcript`로 녹화한 JSONL. 각 줄 = agent call 1개의 full prompt + solo TTFT/TBT/e2e baseline

---

## Memory / Auto-context Notes

- 자동 메모리에 저장된 Motivation Experiment Timeout Settings:
  `PER_CALL_TIMEOUT = 120s` (TTFT), `IDLE_TIMEOUT = 60s` — agent_motivation_experiment에서 기준 타임아웃.
- 새 하위 프로젝트가 생기면 이 파일의 **Repo Layout**과 **Where to Look First**를 갱신하세요.
