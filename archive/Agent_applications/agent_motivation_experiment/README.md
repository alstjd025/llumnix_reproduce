# Agent Motivation Experiment

SGLang 서버에 multi-call coding agent workload를 부하로 넣고, server throughput이 좋아 보여도 application/job-level goodput이 무너지는 구간을 측정하는 실험 프로젝트입니다.

현재 기본 workload는 SWE-bench Lite 문제를 입력으로 쓰는 synthetic coding agent입니다. 한 job은 여러 번의 LLM call로 구성되고, call-level latency goodput과 job-level end-to-end goodput을 함께 기록합니다.

`swe_bench_coding_tool_delay` workload는 같은 agent chain을 사용하되, prompt에 `Tool result:`가 붙는 call boundary에서만 deterministic simulated tool-call interval을 넣습니다. Delay는 100ms-10s 범위, 평균 3s의 scaled beta 분포이고, task/replay/call boundary별 hash seed로 고정됩니다.

`swe_bench_coding_parallel_tool_delay` workload는 같은 call 수와 stage sequence를 유지하면서, 연속된 `Locate` call들을 하나의 `execution_round`에서 병렬로 실행합니다. Call별 latency/token/TBT는 계속 `metrics.csv`에 저장되고, round/dependency 구조는 `parallel_calls.csv`와 분석용 `application_parallel_calls.csv`에 따로 저장됩니다.

`codingagent_request_level_poisson` workload는 위 세 workload와 달리 **job/chain 개념이 없습니다.** 각 Poisson arrival이 하나의 독립적인 LLM request이고, 서버의 request-level Halo admission gate와 짝을 이룹니다. `swe_bench_coding` baseline run을 `--record-transcript`로 돌려 만든 transcript JSONL을 literal하게 재생(replay)하며, transcript 안에 들어 있는 concurrency-1 TTFT/TBT/e2e가 per-request goodput baseline이 됩니다. Goodput은 `analysis_scripts/request_level/parse_request_metrics.py`로 계산합니다.

## Two serving backends: SGLang vs Llumnix (`--engine`)

이 러너는 **두 서빙 백엔드**를 지원합니다. `--engine` 플래그로 고릅니다.

- **`--engine sglang`** (기본): 원격 NXC7의 SGLang을 ssh+tmux로 제어. 이 README의
  나머지 부분(Quick Start 등)이 이 경로입니다. `chat.completions` + Halo 사용.
- **`--engine llumnix`**: NXC13 k3s에 떠 있는 **Llumnix**(migration 서빙 스택)에 부하.
  게이트웨이가 `/v1/completions`만 받으므로 Llama-3 템플릿 completions 어댑터를 쓰고,
  서버제어는 k8s(재시작), 서버측 metric은 Prometheus `/metrics`를 각 계층에서 긁어
  `server_metrics/*.jsonl`로 저장합니다. **러너를 클러스터 내부 파드로 실행**해
  port-forward 병목을 없앱니다.

> **Llumnix에서 실험하는 법 → [`k8s/README.md`](k8s/README.md)** 를 보세요. (전제조건,
> `runner-job.yaml` env로 λ/duration 설정, 실행/모니터링, 결과·분석, 조건별 완전
> 재시작(`--restart-per-condition`), 트러블슈팅까지 자립적으로 정리돼 있습니다.)
>
> 빠른 시작:
> ```bash
> kubectl apply -f k8s/runner-rbac.yaml            # 최초 1회 (RBAC)
> # k8s/runner-job.yaml 의 env(LAMBDA_LIST / DURATION_MIN / RESTART_PER_CONDITION ...) 편집
> kubectl -n llumnix delete job bench-runner --ignore-not-found
> kubectl apply -f k8s/runner-job.yaml             # 실험 실행
> kubectl -n llumnix logs -f job/bench-runner      # 진행 확인
> # 결과: results/<YYMMDD_HHMM>_<SESSION>_lambda_<λ>/  (metrics.csv + server_metrics/)
> ```

## Quick Start

아래 명령은 이 디렉터리(`Agent_applications/agent_motivation_experiment`)에서 실행하는 것을 기준으로 합니다.

실험 실행은 로컬 tmux의 `motivation` session에서 수행합니다. 세션이 없으면 먼저 만들고, 긴 run은 해당 세션 안에서 `run_experiment.py`로 실행합니다.

```bash
tmux new -s motivation
```

### Baseline 만들기

Baseline은 concurrency 1에서 task별 기준 latency를 수집합니다. 이후 `--tau` goodput threshold와 job timeout 계산에 사용됩니다.

```bash
python run_experiment.py \
  --workload swe_bench_coding \
  --mode baseline \
  --end-index 300 \
  --restart-server \
  --session-name baseline_swe
```

### 단일 Poisson run

```bash
python run_experiment.py \
  --workload swe_bench_coding \
  --mode single \
  --lambda-val 0.2 \
  --baseline-dir results/baseline_20260424-180204 \
  --duration-min 120 \
  --tau 3.0 \
  --restart-server \
  --session-name lambda_0p2_test
```

Tool-call interval을 모사하는 workload를 쓰려면 workload 이름만 바꿉니다.

```bash
python run_experiment.py \
  --workload swe_bench_coding_tool_delay \
  --mode single \
  --lambda-val 0.2 \
  --baseline-dir results/baseline_20260424-180204 \
  --duration-min 120 \
  --tau 3.0 \
  --restart-server \
  --session-name lambda_0p2_tool_delay
```

Parallel agent round까지 모사하려면 다음 workload를 사용합니다.

```bash
python run_experiment.py \
  --workload swe_bench_coding_parallel_tool_delay \
  --mode single \
  --lambda-val 0.2 \
  --baseline-dir results/baseline_20260424-180204 \
  --duration-min 120 \
  --tau 3.0 \
  --restart-server \
  --session-name lambda_0p2_parallel_tool_delay
```

### Poisson sweep

Sweep 모드는 기본적으로 각 λ condition마다 SGLang 서버를 stop/start합니다. `--session-name`을 주면 condition suffix가 자동으로 붙습니다.

```bash
python run_experiment.py \
  --workload swe_bench_coding \
  --mode poisson-sweep \
  --baseline-dir results/baseline_20260424-180204 \
  --lambda-list 0.01,0.02,0.05,0.1,0.2 \
  --duration-min 120 \
  --tau 3.0 \
  --session-name hicache_sweep
```

예를 들어 λ=0.2 condition은 원격 SGLang session name이 `hicache_sweep_lambda_0p2`가 되고, 로컬 결과 폴더는 `results/YYMMDD_HHMM_hicache_sweep_lambda_0p2/` 형태가 됩니다.

### Fixed-rate sweep

```bash
python run_experiment.py \
  --workload swe_bench_coding \
  --mode rate-sweep \
  --baseline-dir results/baseline_20260424-180204 \
  --rate-list 3,6,12,30,60 \
  --duration-min 60 \
  --tau 3.0 \
  --session-name rpm_sweep
```

### Request-level Poisson (transcript replay)

`codingagent_request_level_poisson` workload은 job 단위가 아니라 request 단위로
Poisson 부하를 보냅니다. 먼저 transcript를 녹화한 뒤(이 run이 baseline 역할도 겸함),
그 transcript를 replay 합니다.

```bash
# 1) Record transcript (= baseline). concurrency 1.
python run_experiment.py \
  --workload swe_bench_coding \
  --mode baseline \
  --node nxc7-1 \
  --end-index 300 \
  --restart-server \
  --record-transcript results/transcripts/swe_b200x2.jsonl \
  --session-name record_b200x2

# 2) Replay as a request-level Poisson sweep (λ = requests/sec).
python run_experiment.py \
  --workload codingagent_request_level_poisson \
  --mode poisson-sweep \
  --node nxc7-1 \
  --transcript-file results/transcripts/swe_b200x2.jsonl \
  --lambda-list 1,2,5,10,20 \
  --duration-min 60 \
  --tau 3.0 \
  --halo-enabled \
  --session-name req_sweep
```

이 workload은 `--baseline-dir`이 필요 없습니다(baseline이 transcript 안에 있음).
Per-request goodput은 `analysis_scripts/request_level/parse_request_metrics.py`로 계산합니다.

## GPU 노드 선택 (`--node`)

SGLang 서버 instance가 2개입니다(`nxc7-1`, `nxc7-2`). `--node`로 하나를 고르면
ssh host / server base URL / 원격 tmux session이 profile에서 자동 설정됩니다.
한 run에서는 한 노드만 씁니다. `--server-base-url` / `--sglang-ssh-host` /
`--sglang-tmux-session`을 명시하면 profile을 덮어씁니다. 로컬 터널
(`pipe_sglang_1.sh` → `:8080`, `pipe_sglang_2.sh` → `:8081`)이 떠 있어야 합니다.

## Server Flow

Application runner는 로컬에서 실행되고, SGLang 서버는 SSH host `NXC7`에서 tmux session을 통해 실행됩니다.

기본 서버 시작 명령:

```bash
cd /home/nxclab/sglang/ms_dev/expctl && python3 run_experiment.py --mode single --single-port 31000
```

`--session-name`을 주면 application runner가 서버 시작 명령에 `--session-name <name>`을 자동으로 붙입니다. 원격 서버는 아래 위치에 session folder를 만듭니다.

```text
/home/nxclab/sglang/ms_dev/runtime/sessions/<session-name>/
```

각 condition이 끝나면 runner는 원격 session folder를 자동으로 가져옵니다.

```text
results/YYMMDD_HHMM_<session-name>/
├── server_session/      # 원격 session folder 전체 rsync
└── server.stderr.log    # 기존 parser가 바로 읽을 수 있게 root에도 복사
```

관련 옵션:

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--session-name` | `None` | 원격 SGLang session base name |
| `--remote-session-root` | `/home/nxclab/sglang/ms_dev/runtime/sessions` | 원격 session root |
| `--server-session-subdir` | `server_session` | 로컬 run folder 안에 저장할 subdir |
| `--server-fetch-timeout-sec` | `1800` | `rsync` timeout |
| `--no-fetch-server-session` | off | 원격 server session 자동 fetch 비활성화 |
| `--no-server-restart` | off | rate/poisson sweep에서 condition별 서버 재시작 생략 |
| `--restart-server` | off | baseline/single/sweep 실행 전에 서버 재시작 |

주의: `--no-server-restart`를 쓰면 runner가 새 서버 session을 시작하지 않으므로 condition별 `--session-name`도 원격 서버에 전달되지 않습니다.

## Postprocessing

분석 스크립트는 `analysis_scripts/` 아래에 있습니다.

Application-side CSV와 figure:

```bash
python analysis_scripts/job_level/parse_application_metrics.py results/20260505-210642_hicache
python analysis_scripts/job_level/plot_application_metrics.py results/20260505-210642_hicache
```

Server-side CSV와 figure:

```bash
python analysis_scripts/parse_server_logs.py results/20260505-210642_hicache
python analysis_scripts/plot_server_metrics.py results/20260505-210642_hicache
```

λ sweep 전체 비교:

```bash
python analysis_scripts/job_level/plot_lambda_slowdown_goodput.py \
  --results-dir results \
  --output-dir results/aggregate_analysis/lambda_slowdown_goodput
```

Job release time 기준 slowdown 분석:

```bash
python analysis_scripts/job_level/analyze_job_call_slowdown_by_release.py \
  --baseline-dir results/baseline_20260424-180204 \
  --run 0.1=results/20260430-173659 \
  --run 0.2=results/20260430-193949
```

## Result Directory

`results/`는 두 종류의 산출물을 담습니다.

- `results/YYMMDD_HHMM[_session-name]/`: 단일 실험 run
- `results/aggregate_analysis/`: 여러 run을 묶어 만든 cross-run 분석 산출물

현재 aggregate output 예시:

```text
results/aggregate_analysis/
├── job_call_slowdown_by_release_time/
└── lambda_slowdown_goodput_20260430_sweep/
```

새 run의 기본 구조:

```text
results/YYMMDD_HHMM_<session-name>/
├── run_config.json
├── metrics.csv
├── errors.log
├── tbt_events.jsonl
├── parallel_calls.csv
├── server.stderr.log
├── agent_logs/
├── server_session/
├── analysis/
│   ├── application_calls.csv
│   ├── application_jobs.csv
│   ├── application_summary.csv
│   ├── application_timeseries.csv
│   ├── application_call_job_goodput.csv
│   ├── application_call_job_goodput_summary.csv
│   ├── application_job_call_goodput_summary.csv
│   ├── application_job_transition_adjusted.csv
│   ├── application_job_transition_adjusted_summary.csv
│   ├── application_parallel_calls.csv
│   ├── application_parallel_rounds.csv
│   └── server_metrics.csv
└── figures/
    ├── application_throughput_goodput.png
    ├── wcr.png
    ├── call_job_goodput_breakdown.png
    ├── job_call_goodput_breakdown.png
    └── server_metrics_*.png
```

`metrics.csv`는 `agent` 컬럼으로 row type을 구분합니다.

Admission control reject는 call row에서 명시적으로 표시됩니다.

```text
is_rejected=True
rejection_reason=TBT_RATIO | TTFT_RATIO | ADMISSION_REJECTED | ...
success=False
is_error=True
error_msg=Call <n>: admission rejected (...)
```

`parse_application_metrics.py`는 이 값을 `application_calls.csv`, `application_summary.csv`, `application_timeseries.csv`에 전달하고, `plot_application_metrics.py`의 throughput/goodput figure에는 cumulative admission rejection rate도 함께 그립니다.

| `agent` 값 | 의미 |
|---|---|
| `chain_call_understand` | Understand stage LLM call |
| `chain_call_locate` | Locate stage LLM call |
| `chain_call_plan` | Plan stage LLM call |
| `chain_call_implement` | Implement stage LLM call |
| `chain_call_verify` | Verify stage LLM call |
| `chain_call_debug` | Debug stage LLM call |
| `job_summary` | job 단위 요약 |

## Goodput Definitions

Baseline run에서 각 task/call의 기준 latency를 만든 뒤, 실험 run에서는 `tau` 배수 threshold를 적용합니다.

```text
call_goodput = call_latency < baseline_call_latency * tau
job_goodput  = job_latency  < baseline_job_latency  * tau
job_timeout  = baseline_job_latency * tau
```

`application_call_job_goodput_summary.csv`는 call row를 parent job의 goodput 여부와 cross-tab으로 나눕니다.

| bucket | 의미 |
|---|---|
| `call_goodput__job_not_goodput` | 개별 call은 threshold 안이지만 job 전체는 goodput 실패 |
| `call_not_goodput__job_not_goodput` | call과 job 모두 goodput 실패 |
| `call_goodput__job_goodput` | call과 job 모두 goodput |
| `call_not_goodput__job_goodput` | 일부 call은 느렸지만 job 전체는 goodput |
| `unclassified` | baseline 매칭 부족 등으로 goodput 판정 불가 |

Tool-delay workload에서는 `job_summary` row의 `transition_time`이 job 안에서 실제로 sleep한 simulated tool-call interval 총합입니다. 기존 `application_jobs.csv`와 `application_summary.csv`는 그대로 두고, tool delay 포함/제외 job timing 비교는 별도 CSV에 저장합니다.

| 파일 | 의미 |
|---|---|
| `application_job_transition_adjusted.csv` | job별 `latency_with_transition_s`, `transition_time_s`, `latency_without_transition_s`, with/without transition slowdown/goodput |
| `application_job_transition_adjusted_summary.csv` | transition 포함/제외 job goodput rate와 slowdown 요약 |

Parallel workload에서는 call별 상세 metric은 `metrics.csv`와 `application_calls.csv`를 그대로 사용합니다. 병렬 구조만 별도 CSV에 저장합니다.

| 파일 | 의미 |
|---|---|
| `parallel_calls.csv` | runtime에서 기록한 call별 `execution_round`, dependency, round size, tool delay 구조 |
| `application_parallel_calls.csv` | `parallel_calls.csv`에 call latency/goodput metric을 join한 분석 CSV |
| `application_parallel_rounds.csv` | round별 wall time, call latency 합, overlap saving, token 합계 |

## Project Layout

| 경로 | 역할 |
|---|---|
| `run_experiment.py` | 실험 CLI, workload 실행, SGLang stop/start/fetch orchestration |
| `metrics_tracker.py` | `metrics.csv`, `tbt_events.jsonl` 기록 |
| `agent_logger.py` | job별 prompt/response 로그 기록 |
| `workloads/` | workload adapter 구현 |
| `workloads/swe_bench_coding/` | SWE-bench Lite synthetic coding agent |
| `workloads/swe_bench_coding_tool_delay/` | `Tool result:` boundary에 deterministic interval을 넣는 SWE-bench workload |
| `workloads/swe_bench_coding_parallel_tool_delay/` | 연속 Locate call을 같은 `execution_round`에서 병렬 실행하는 workload |
| `workloads/codingagent_request_level_poisson/` | transcript를 literal replay하는 request-level Poisson workload (job 개념 없음) |
| `analysis_scripts/` | metric parsing, aggregation, plotting scripts |
| `results/` | 실험 결과 |
| `results/aggregate_analysis/` | 여러 run을 묶은 분석 산출물 |
| `figures/` | 수동/공통 figure output |
| `data_backups/` | 과거 결과 backup |
| `legacy/` | 현재 메인 경로에서 쓰지 않는 이전 분석 스크립트 |

## Analysis Scripts

| 스크립트 | 역할 |
|---|---|
| `analysis_scripts/job_level/parse_application_metrics.py` | `metrics.csv`를 application analysis CSV로 정규화 (job workload) |
| `analysis_scripts/request_level/parse_request_metrics.py` | request-level workload의 `metrics.csv`를 per-request goodput CSV로 정규화 |
| `analysis_scripts/parse_server_logs.py` | `server.stderr*`를 `server_metrics.csv`로 파싱 |
| `analysis_scripts/job_level/plot_application_metrics.py` | throughput/goodput/WCR/call-job breakdown 그림 생성 |
| `analysis_scripts/plot_server_metrics.py` | server decode/prefill/request stats 그림 생성 |
| `analysis_scripts/job_level/plot_lambda_slowdown_goodput.py` | λ별 call slowdown과 goodput 비교 |
| `analysis_scripts/job_level/plot_latency_slowdown_cdf.py` | latency slowdown CDF 생성 |
| `analysis_scripts/job_level/analyze_job_call_slowdown_by_release.py` | release time 기준 job/call slowdown 분석 |
| `analysis_scripts/job_level/analyze_motivation.py` | 여러 run을 묶은 motivation summary figure 생성 |

## Workload Model

`swe_bench_coding`은 SWE-bench Lite 문제를 입력으로 쓰는 synthetic multi-call agent입니다.

```text
Understand -> Locate(1-4) -> Plan -> Implement(1-3) -> Verify
-> Debug -> Implement -> Verify ...
```

각 job은 `chain_min`부터 `chain_max` 사이의 call chain length를 갖습니다. System prompt는 모든 call에서 동일하고, replay별 nonce는 첫 user message에 들어갑니다.

`swe_bench_coding_tool_delay`는 같은 chain/stage/prompt를 쓰며, 현재 call prompt에 `Tool result:`가 붙는 경우에만 call 시작 전에 sleep합니다. Delay는 `sha256(tool_delay_beta_v1|base_instance_id|replay_index|boundary_call_index)`로 seed를 만들기 때문에 같은 SWE-bench task/replay/boundary는 모든 실험에서 같은 interval을 갖습니다.

`swe_bench_coding_parallel_tool_delay`는 `swe_bench_coding_tool_delay`를 기반으로 하며, 총 call 수는 바꾸지 않습니다. `Understand` 이후 연속된 `Locate` stage들을 하나의 `execution_round`로 묶어 동시에 요청하고, 이후 `Plan`은 그 round의 모든 결과에 의존합니다. Round에 여러 tool-result call이 있으면 round delay는 해당 call들의 deterministic delay 중 `max`를 사용합니다.

## Reproducibility

- `--seed`는 workload RNG, task replay generation, Poisson arrival sampling, LLM request seed에 사용됩니다.
- rate/poisson sweep은 각 condition마다 같은 seed로 fresh task pool을 만듭니다.
- `swe_bench_coding` LLM 요청은 `temperature=0.0`, `top_p=1.0`, `seed=<--seed>`를 사용합니다.
- `swe_bench_coding_tool_delay`의 tool interval은 workload RNG와 별개로 task/replay/call boundary hash에서 결정됩니다.
- `swe_bench_coding_parallel_tool_delay`의 parallel round 구조는 stage sequence에서 deterministic하게 결정되며 별도 RNG를 쓰지 않습니다.
- `run_config.json`에 workload metadata와 reproducibility 설정을 저장합니다.
- 서버까지 bitwise 동일하게 만들려면 SGLang의 model, parallelism, scheduler, random seed도 고정해야 합니다.

## Adding A Workload

새 workload는 `workloads/<name>/workload.py`로 추가합니다. `run_experiment.py`는 `--workload <name>`으로 adapter를 로드합니다.

Workload adapter가 제공해야 하는 주요 메서드:

```text
load_dataset(args, workload_config)
build_baseline_tasks(dataset, replay_count, rng, args, workload_config)
create_task_pool(dataset, baseline_latencies, rng, args, workload_config)
run_job(task, context)
task_log_info(task)
metadata(args, workload_config)
reproducibility_config(args, workload_config)
```
