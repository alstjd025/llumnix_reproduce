# Agent Notes

This project measures how application-level goodput collapses under load even when SGLang server throughput looks healthy.

## Core Flow

0. Read `README.md` first before running or modifying experiments. Follow the README command flow unless the user explicitly asks for a different low-level path.
1. `run_experiment.py` runs local application workloads against an OpenAI-compatible SGLang endpoint.
2. The SGLang server runs remotely on SSH host `NXC7` and is controlled by this runner through tmux.
3. Results go under `results/<YYMMDD_HHMM>_<session-name>/` (matches the
   remote sglang session-folder format; same-minute collisions get a
   `_v2`/`_v3` suffix).
4. If `--session-name` is provided, the runner builds the prefixed name in
   `condition_session_name()` and passes it to the remote server. The remote
   sglang prefix step is idempotent, so both sides agree on the resolved
   name. The runner then fetches the remote runtime session with `rsync`,
   stores it in `server_session/`, and copies `server.stderr*` to the run
   root.
5. Analysis scripts live in `analysis_scripts/`.
6. Run long experiments from the local tmux `motivation` session.

## Server Ownership

- Treat `run_experiment.py` as the owner of the remote SGLang lifecycle.
- Do not manually start/stop SGLang, create SSH tunnels, or probe remote process state as the first move for normal experiment questions.
- Use `--restart-server` for `single`, `baseline`, and `sweep` runs that need a fresh server.
- Use the default restart behavior for `rate-sweep` and `poisson-sweep`; only use `--no-server-restart` when the user explicitly wants to reuse a running server.
- If a custom admission-control server configuration is needed, pass it through `--sglang-start-cmd` so the runner still owns start/stop/fetch.

## Admission Control Checks

When testing admission-control rejection behavior:

1. Run the smallest useful traffic experiment through `run_experiment.py` (`single` with `--lambda-val`, `--rpm`, or `--concurrency` is usually enough).
2. Use a distinct `--session-name` so local and remote logs can be matched.
3. Inspect `metrics.csv` for failed `chain_call_*` rows (`success=False`, `is_error=True`, and `error_msg`) and `job_summary` rows (`job_completed=False`).
4. Inspect `errors.log` and `agent_logs/` for the client-visible exception text.
5. Inspect fetched `server_session/` or root `server.stderr.log` for server-side admission/reject messages.
6. If a streaming request is rejected, remember that the current client path in `workloads/swe_bench_coding/agent.py` may attempt a non-streaming fallback before marking the call failed.

## Important Paths

| Path | Purpose |
|---|---|
| `run_experiment.py` | Main experiment runner and SGLang orchestration |
| `workloads/swe_bench_coding/` | Default SWE-bench Lite synthetic coding workload |
| `workloads/swe_bench_coding_tool_delay/` | SWE-bench workload with deterministic simulated tool-call intervals |
| `workloads/swe_bench_coding_parallel_tool_delay/` | SWE-bench workload with parallel execution rounds and deterministic tool-call intervals |
| `metrics_tracker.py` | Writes `metrics.csv` and `tbt_events.jsonl` |
| `agent_logger.py` | Writes per-job prompt/response logs |
| `analysis_scripts/job_level/parse_application_metrics.py` | Builds application analysis CSVs |
| `analysis_scripts/job_level/plot_application_metrics.py` | Builds application figures |
| `analysis_scripts/parse_server_logs.py` | Parses `server.stderr*` into `server_metrics.csv` |
| `analysis_scripts/plot_server_metrics.py` | Builds server-side figures |
| `results/` | Run outputs |
| `results/aggregate_analysis/` | Cross-run analysis outputs |
| `workloads/AGENTS.md` | Workload adapter notes |

## Common Commands

Run a Poisson sweep:

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

Postprocess one run:

```bash
python analysis_scripts/job_level/parse_application_metrics.py results/<run>
python analysis_scripts/parse_server_logs.py results/<run>
python analysis_scripts/job_level/plot_application_metrics.py results/<run>
python analysis_scripts/plot_server_metrics.py results/<run>
```

Build cross-run summaries:

```bash
python analysis_scripts/job_level/plot_lambda_slowdown_goodput.py \
  --results-dir results \
  --output-dir results/aggregate_analysis/lambda_slowdown_goodput

python analysis_scripts/job_level/analyze_job_call_slowdown_by_release.py \
  --results-dir results \
  --baseline-dir results/baseline_20260424-180204 \
  --output-dir results/aggregate_analysis/job_call_slowdown_by_release_time
```

## Goodput

Baseline latencies are loaded from `--baseline-dir`.

```text
call_goodput = call_latency < baseline_call_latency * tau
job_goodput  = job_latency  < baseline_job_latency  * tau
```

Call/job cross buckets are written to:

```text
analysis/application_call_job_goodput.csv
analysis/application_call_job_goodput_summary.csv
analysis/application_job_call_goodput_summary.csv
```

For `swe_bench_coding_tool_delay`, job timing with and without simulated tool-call intervals is written separately:

```text
analysis/application_job_transition_adjusted.csv
analysis/application_job_transition_adjusted_summary.csv
```

The raw job summary `transition_time` field is the total application-side simulated tool delay for that job. The existing `application_jobs.csv` and `application_summary.csv` should remain compatible with older runs.

For `swe_bench_coding_parallel_tool_delay`, call-level performance still comes from `metrics.csv`. Parallel structure is recorded separately:

```text
parallel_calls.csv
analysis/application_parallel_calls.csv
analysis/application_parallel_rounds.csv
```

Use `execution_round`, not `wave`, for the group of calls that start after the same dependency barrier.

## Editing Guidance

- Keep run output schemas stable unless explicitly asked to migrate them.
- Prefer adding new analysis CSVs over mutating existing CSVs.
- Keep analysis scripts in `analysis_scripts/`.
- Keep cross-run outputs in `results/aggregate_analysis/`, not at the `results/` root.
- Check `workloads/AGENTS.md` and the nearest workload-specific `AGENTS.md` before changing workload adapters.
- Run `python -m py_compile analysis_scripts/*.py run_experiment.py` after moving or editing scripts.
- 사용자가 명확히 실행/수정/테스트를 요청하면 바로 진행한다.
- 요구사항이 애매하거나 실험 설정이 결과를 크게 바꿀 수 있으면 먼저 짧게 확인한다.
- 큰 코드 변경이 필요한 경우에는 먼저 간단한 pseudocode나 코드 스켈레톤을 보여주고 피드백을 받은 뒤 진행한다.
