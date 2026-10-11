# Request-level Analysis Scripts

For workloads whose `metrics.csv` carries **independent requests** (no
job/chain): the runner writes one row per request with `agent=="request"`
plus one `job_summary` row per request as a per-task summary. Two
flavors:

| Workload | Has solo baseline? | Per-run parser |
|---|---|---|
| `codingagent_request_level_poisson` | Yes — carried inside the transcript JSONL (TTFT/TBT/e2e per request) | `parse_request_metrics.py` |
| `sharegpt_request_level_poisson` | **No** — direct, absolute SLO model | `parse_request_summary.py` |

For multi-call job workloads use `../job_level/` instead.

## Scripts

### `parse_request_metrics.py` — codingagent (transcript + τ)

Per-run. Joins each `agent=="request"` row with its solo baseline from
the transcript JSONL recorded by the `swe_bench_coding --record-transcript`
run; computes per-request **e2e / TTFT / TBT goodput** as
`latency < baseline × τ` on each dimension.

Outputs:
- `<run>/analysis/request_metrics.csv`
- `<run>/analysis/request_summary.csv` (rate, percentiles, throughput)

Classification follows CLAUDE.md §Run-boundary cutoffs: rejected /
server-terminated / missing-baseline → unclassified.

### `parse_request_summary.py` — sharegpt (direct, no baseline)

Per-run. Reads `agent=="request"` rows and writes **raw load statistics
only** — no goodput, no τ. Reports counts (ok / terminated / error /
rejected), throughput (req/s, output tokens/s), and latency / TTFT / TBT
percentile distributions (computed over `success==True` rows only).

Outputs:
- `<run>/analysis/request_metrics.csv` (cleaned per-request rows)
- `<run>/analysis/request_summary.csv` (one-row aggregate)

Absolute-SLO goodput is a deliberate TODO. When thresholds are decided,
either extend this script or add a sibling `parse_request_goodput.py`.

### `summarize_lambda_sweep.py` — cross-λ

Aggregate one `request_summary.csv` per run into one table indexed by λ.
Used after running `parse_request_summary.py` on each run of a sweep.

Outputs:
- `<output-dir>/lambda_summary.csv`
- optional `--plot-png`: `lambda_throughput.png`, `lambda_latency_p90.png`,
  `lambda_server_terminated_pct.png`

Pairs naturally with `parse_request_summary.py`. To do the analogous
sweep summary for the transcript+τ flavor, parse each run with
`parse_request_metrics.py` first and then collect its `request_summary.csv`
columns separately.

### `plot_server_throughput_overlay.py` — cross-λ throughput-vs-time

For each run, reads `analysis/server_metrics.csv` (run
`../parse_server_logs.py` first) and overlays one
`gen_throughput (tokens/s)` vs `minutes-since-first-decode` line per
lambda. Optional rolling-mean smoothing via `--smooth-window N`.

### `plot_tbt_p90_threshold.py` — per-λ TBT-p90 attainment curves

For each run, computes the % of **successful** requests whose
per-request `tbt_p90_ms` is at or below each threshold T in {50, 75,
100, 125, 150, 175, 200} ms (override with `--thresholds`), and plots
one line per λ. `--csv-output` also dumps the attainment table.

## Invariants

- `agent == "request"` is set by `invoke_with_tracking(agent_label="request")`;
  do not change without updating these scripts.
- `task_id` may have a `__rNN` replay suffix when pool cycling occurs;
  strip with `__rNN$` regex when joining cross-run.
- For the codingagent flavor, classification of unclassified jobs follows
  `agent_motivation_experiment/CLAUDE.md` §Run-boundary cutoffs and
  §Rejected-at-start exclusion.
