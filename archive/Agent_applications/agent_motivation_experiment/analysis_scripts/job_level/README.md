# Job-level Analysis Scripts

For workloads whose `metrics.csv` carries **multi-call jobs**: each task
runs a chain of LLM calls and the runner writes one row per call
(`agent=="chain_call_*"`) plus one `job_summary` row per job. Applies to:

- `swe_bench_coding`
- `swe_bench_coding_tool_delay`
- `swe_bench_coding_parallel_tool_delay`

For request-level workloads (one LLM request per task, no chain), use
`../request_level/` instead.

## Per-run

| Script | Purpose |
|---|---|
| `parse_application_metrics.py` | Normalize `metrics.csv` into `analysis/application_calls.csv`, `application_jobs.csv`, `application_summary.csv`. Computes call/job goodput against `--baseline-dir` × τ. |
| `plot_application_metrics.py` | Throughput / goodput / WCR / call-job breakdown figures from the application CSVs. |
| `plot_latency_slowdown_cdf.py` | Call/job latency-slowdown CDF vs the no-load baseline. |

## Cross-run (sweep)

| Script | Purpose |
|---|---|
| `plot_lambda_slowdown_goodput.py` | λ → call slowdown / goodput summary plots and CSVs. |
| `summarize_sweep_window.py` | Re-aggregate `application_summary.csv` over a `[start_min, end_min]` window across runs (steady-state goodput). |
| `analyze_job_call_slowdown_by_release.py` | Per-λ release-time job/call slowdown analysis (long-tail vs warmup). |
| `analyze_motivation.py` | Motivation summary figure across runs. |

## Halo (admission-control) comparisons

| Script | Purpose |
|---|---|
| `compare_admission_goodput_throughput.py` | Compare matched runs with vs without Halo admission control (goodput + server decode throughput). Despite the name, operates on `application_*.csv`. |
| `decompose_latency_factors.py` | Decompose TTFT/TBT into server-side factors for one admission vs no-admission pair at the same λ/seed. |
| `diagnose_saturation_cliff.py` | 4-panel time-series + latency-distribution + job-kill stats for one admission vs no-admission pair. |

## Source-of-truth invariants

- Call-level latency / TBT / tokens come from `metrics.csv` (`chain_call_*` rows).
- Job-level latency / completion / wasted tokens come from `job_summary` rows.
- Goodput / slowdown definitions and the run-boundary cutoff / rejected-at-start
  rules live in `agent_motivation_experiment/CLAUDE.md` §Goodput. Keep
  those in sync if these scripts are touched.
