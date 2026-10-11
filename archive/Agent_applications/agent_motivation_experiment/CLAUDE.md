# Agent Notes

This project measures how application-level goodput collapses under load even when SGLang server throughput looks healthy.

## Core Flow

0. Read `README.md` first before running or modifying experiments. Follow the README command flow unless the user explicitly asks for a different low-level path.
1. `run_experiment.py` runs local application workloads against an OpenAI-compatible SGLang endpoint.
2. The SGLang server runs remotely on SSH host `NXC7` and is controlled by this runner through tmux.
3. Results go under `results/<YYMMDD_HHMM>_<session-name>/` (e.g.
   `results/260509_1214_admission_lambda_0p2/`); same `YYMMDD_HHMM_` prefix the
   remote sglang session folder uses. On collision within the same minute the
   runner appends `_v2`, `_v3`, ... With no `--session-name` the dir is just
   `<YYMMDD_HHMM>`.
4. If `--session-name` is provided, the runner builds the prefixed name in
   `condition_session_name()` and passes it to the remote server with
   `--session-name`. The remote sglang prefix-pass is idempotent (sees the
   prefix and uses the name as-is), so both sides agree on the resolved name.
   The runner then fetches the remote runtime session with `rsync`, stores it
   in `server_session/`, and copies `server.stderr*` to the run root.
5. Analysis scripts live in `analysis_scripts/`.
6. Run long experiments from the local tmux `motivation` session.

## Server Ownership

- Treat `run_experiment.py` as the owner of the remote SGLang lifecycle.
- Do not manually start/stop SGLang, create SSH tunnels, or probe remote process state as the first move for normal experiment questions.
- Use `--restart-server` for `single`, `baseline`, and `sweep` runs that need a fresh server.
- Use the default restart behavior for `rate-sweep` and `poisson-sweep`; only use `--no-server-restart` when the user explicitly wants to reuse a running server.
- If a custom admission-control server configuration is needed, pass it through `--sglang-start-cmd` so the runner still owns start/stop/fetch.

## Experiment log + independence rule (Llumnix experiments)

Every Llumnix experiment is recorded under [`experiments/`](experiments/) as
`EXP-NN_*.md` (why / hypothesis / what-to-observe / exact settings / result);
`experiments/README.md` is the index + methodology. **Ground rule: every
experimental condition cold-restarts the whole engine** (engine pod + control
plane) so each condition is fully independent — e.g. **a λ-sweep restarts per λ**.
This is `--restart-per-condition` (env `RESTART_PER_CONDITION=1` in
`k8s/runner-job.yaml`); keep it ON for all recorded experiments. See also the
engine-profile section below and [`k8s/README.md`](k8s/README.md).

## Engine profiles (`--engine sglang` | `--engine llumnix`)

The runner targets one of two serving backends. Default `sglang` (everything in
"Core Flow" / "Server Ownership" above). `--engine llumnix` targets the Llumnix
migration stack in k3s on NXC13 — **operational guide: [`k8s/README.md`](k8s/README.md)**.

Under `--engine llumnix` the profile (resolved in `main()`) automatically:
- uses `/v1/completions` via a Llama-3-templated adapter (`LlumnixCompletionsLLM`
  in `workloads/swe_bench_coding/agent.py`; gateway rejects chat) — threaded to
  all workloads via `RunContext.api/model/max_tokens`;
- disables Halo and the per-request `KVCacheMonitor`; defaults the model to
  `meta-llama/Meta-Llama-3-8B-Instruct`;
- replaces ssh/tmux server control with k8s: `--restart-per-condition` cold-restarts
  the engine (`kubectl delete pod neutral-0`) + control plane (`rollout restart
  scheduler,gateway`) before each condition (`llumnix_deploy.py:restart_llumnix`);
- runs a background collector (`llumnix_metrics.py`) scraping Prometheus `/metrics`
  from engines/scheduler/gateway into `server_metrics/*.jsonl`, finalized (with
  migration-log capture) by `finish_server_session`.

`--in-cluster` (set when running as the `bench-runner` pod, see `k8s/`) points the
load generator and collector at k8s DNS (`gateway`, `scheduler`, `neutral-0.neutral`)
instead of localhost port-forwards — this is the path for real load (port-forward
caps ~7-8 req/s). The SGLang path is unchanged; all llumnix logic is additive.

Server-side metric note: `scheduler_rescheduling_total` (the migration-decision
counter) is only exposed after a rescheduling actually happens; ground-truth KV
transfers live in `server_metrics/migration_events.log`, not a metric.

## Halo (Project Halo, request-level)

> Halo was refactored job-level → **request-level** on 2026-05-19.
> There is no job pre-registration anymore: `POST /halo/programs`,
> `halo_job_id`, and `halo_job_done` were removed. Admission is fully
> per-request.

Pass `--halo-enabled` to `run_experiment.py` to enable client-side Halo
wiring. The runner probes `GET /halo/status` once at startup and aborts
if the server's Halo state doesn't match (strict policy).

When on, every `chat.completions` request carries three per-request SLO
fields in `extra_body` (set by `make_llm`):

- `halo_ttft_slo` — `--halo-ttft-slo`
- `halo_tbt_slo` — `--halo-tbt-slo`
- `halo_e2e_slo` — `--halo-e2e-slo`

Each flag defaults to `--tau`. With the server's `slo_mode=ratio`
(default) these are slowdown ratios vs the solo baseline; `halo_e2e_slo`
is always a ratio. No job is registered; the server's request-level
admission gate evaluates each request independently.

**Server side must also be launched with `--halo-enabled`** plus an
`--halo-admission-policy`. Client-on/server-off (and vice versa) is
detected at runner startup and aborts.

Reject handling: a rejected request fails admission with HTTP 400; the
client's `_detect_admission_rejection` records it in `metrics.csv` as
`is_rejected=True, rejection_reason=HALO_*` (`HALO_KV_CAP`,
`HALO_VSS_PREDICTED`, `HALO_ADMISSION_PREDICTED`, …).

Server-side API reference: `ms_dev/halo_dev/halo_api_reference.md` in the
sglang repo. Client helper: [workloads/halo_helpers.py](workloads/halo_helpers.py).

## GPU nodes (`--node`)

Two SGLang instances are available (B200×2 each). `--node {nxc7-1,nxc7-2}`
selects one: it sets the ssh host, server base URL, and remote tmux
session from a profile (`NODE_PROFILES` in `run_experiment.py`). One node
per run. Explicit `--server-base-url` / `--sglang-ssh-host` /
`--sglang-tmux-session` still override the profile. Local tunnels
(`pipe_sglang_1.sh` → `:8080`, `pipe_sglang_2.sh` → `:8081`) must be up.

## Request-level workload + transcript flow

`codingagent_request_level_poisson` is a flat, open-loop request stream:
each Poisson arrival is one **independent** LLM request (no job/chain).
It replays recorded agent calls verbatim ("literal replay"), so it needs
a transcript file:

1. **Record** (also produces the baseline): a concurrency-1
   `swe_bench_coding --mode baseline --record-transcript <path>` run
   writes `<path>` — one JSONL line per agent call with the full prompt
   and that call's solo TTFT/TBT/e2e.
2. **Replay**: `--workload codingagent_request_level_poisson
   --transcript-file <path> --mode poisson-sweep` (λ = requests/sec).

Goodput is per-request (e2e/TTFT/TBT vs the recorded baseline × τ);
parse it with `analysis_scripts/request_level/parse_request_metrics.py`.

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
| `run_experiment.py` | Main experiment runner + SGLang/Llumnix orchestration (`--engine`) |
| `llumnix_metrics.py` | Llumnix per-run Prometheus `/metrics` collector (`server_metrics/*.jsonl`) |
| `llumnix_deploy.py` | Llumnix k8s cold-restart helper (`--restart-per-condition`) |
| `k8s/` | In-cluster runner pod: `runner-rbac.yaml`, `runner-job.yaml`, `README.md` |
| `analysis_scripts/parse_llumnix_metrics.py` | Folds `server_metrics/*.jsonl` → `analysis/llumnix_server_metrics{,_summary}.csv` |
| `workloads/swe_bench_coding/` | Default SWE-bench Lite synthetic coding workload (job-level) |
| `workloads/swe_bench_coding_tool_delay/` | SWE-bench workload with deterministic simulated tool-call intervals |
| `workloads/swe_bench_coding_parallel_tool_delay/` | SWE-bench workload with parallel execution rounds and deterministic tool-call intervals |
| `workloads/codingagent_request_level_poisson/` | Request-level Poisson workload — replays recorded agent calls as independent requests |
| `workloads/sharegpt_request_level_poisson/` | Request-level Poisson workload — downloads ShareGPT and sends multi-turn chat turns as independent requests (direct; no transcript/baseline; absolute-SLO goodput) |
| `workloads/searcharena_request_level_poisson/` | Request-level Poisson workload — reconstructs deep-research synthesis requests (K search-grounded notes + question) from Search Arena; mid-length input (~4.1k tok, ~910-tok system prompt cached), direct/absolute-SLO like sharegpt |
| `metrics_tracker.py` | Writes `metrics.csv` and `tbt_events.jsonl` |
| `agent_logger.py` | Writes per-job prompt/response logs |
| `analysis_scripts/job_level/parse_application_metrics.py` | Builds application analysis CSVs (job workloads) |
| `analysis_scripts/request_level/parse_request_metrics.py` | Per-request goodput CSVs (codingagent transcript+τ flavor) |
| `analysis_scripts/request_level/parse_request_summary.py` | Per-run raw-load stats (ShareGPT direct flavor — no baseline, no goodput yet) |
| `analysis_scripts/request_level/summarize_lambda_sweep.py` | Cross-λ summary table + optional plots for request-level sweeps |
| `analysis_scripts/job_level/plot_application_metrics.py` | Builds application figures |
| `analysis_scripts/parse_server_logs.py` | Parses `server.stderr*` into `server_metrics.csv` |
| `analysis_scripts/plot_server_metrics.py` | Builds server-side figures |
| `analysis_scripts/job_level/plot_lambda_slowdown_goodput.py` | Cross-run λ→slowdown/goodput summary CSVs and plots |
| `analysis_scripts/job_level/plot_latency_slowdown_cdf.py` | Latency slowdown CDF figure across runs |
| `analysis_scripts/job_level/analyze_job_call_slowdown_by_release.py` | Per-λ release-time job/call slowdown analysis |
| `analysis_scripts/job_level/analyze_motivation.py` | Motivation summary figure across runs |
| `analysis_scripts/job_level/summarize_sweep_window.py` | Re-aggregates `application_summary.csv` columns over a `[start_min, end_min]` time window across runs (skips warmup/saturation) |
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

Re-aggregate summary over a steady-state window (e.g. drop the first 20 min of warmup):

```bash
python analysis_scripts/job_level/summarize_sweep_window.py \
  --run-dirs results/260510_*tau5_lambda_* \
  --window-min 20 80 \
  --output-csv results/aggregate_analysis/sweep_window_summary_tau5_20to80min.csv \
  --print-markdown \
  --plot-png results/aggregate_analysis/sweep_window_goodput_vs_lambda_tau5_20to80min.png
```

Filters: calls by `start_time`, jobs by `job_submit_time`, both relative to each run's first call. Uses the same `build_summary` as the per-run parser so columns stay in lockstep with `application_summary.csv`. `--plot-png` produces a `λ → call/job goodput rate` line plot (SLO attainment view) over the same window.

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

### Window filtering (steady-state goodput)

When measuring goodput over a `[start, end]` minute window (e.g. to skip
warmup), filter **jobs by `job_submit_time ∈ window`** and then derive
`calls` as the calls whose parent job is in that windowed job set
(i.e. tied to the parent job's submit time). Do **not** filter calls
independently by `start_time ∈ window`.

- Semantic: "of the jobs released into the system during steady-state load, what fraction met the SLO?" — the queueing-theory standard for goodput.
- Why submit-tied: the job's outcome is determined by the load it sees from the moment it arrives; the call-level rate should be computed over the same chains so call/job comparisons stay on a single population.
- What can go wrong with independent filters: at high load, calls of jobs submitted *before* the window may extend into the window (long chains hitting saturation), while jobs submitted *in* the window may have calls extending past it. Filtering each by their own timestamp mixes these populations and can make `call_goodput < job_goodput` even when chain amplification should make the opposite true.
- Run-boundary cutoffs at the window end (jobs submitted near `end` whose chain doesn't finish before the run terminates) are handled separately via the "Run-boundary cutoffs" rules below — they become `job_goodput_bool = NaN` and drop out of the denominator.

`summarize_sweep_window.py` implements this filter; `parse_application_metrics.py`'s per-run `application_summary.csv` is computed over the whole run with no windowing.

### Run-boundary cutoffs (unclassified jobs)

Jobs that did not complete because the run ended (`is_server_terminated=True`)
and that were not also admission-rejected or `is_job_timeout=True` have an
**unknown outcome** — they were neither verified to meet the SLO nor verified
to violate it. The parser treats these as **unclassified**:

- `parse_application_metrics.py` `add_tau_goodput()` sets `job_goodput_bool = NaN` for such jobs (run-boundary cutoff condition: `is_server_terminated & ~is_rejected & ~is_job_timeout`).
- All downstream goodput rates use `classifiable_jobs = job_goodput_bool.notna()` as the denominator, so unclassified jobs drop out instead of counting as SLO misses.
- `wasted_tokens` only sums tokens from jobs **explicitly** classified as not-goodput. Unclassified-job tokens are excluded from `wasted_compute_ratio`.
- `summarize_sweep_window.py` records `output_goodput_tokens_per_s`, `output_wasted_tokens_per_s`, and `output_unclassified_tokens_per_s` separately, and looks up the classification from the **full** jobs table (not the windowed slice) so a call whose `start_time` is in the analysis window but whose parent job's `submit_time` is just before the window still gets attributed correctly.
- The token-throughput stacked-bar plot uses **classified throughput only** (goodput + wasted) as the bar height, so the two segments sum to 100% within each bar. The unclassified bucket stays in the CSV for inspection but is omitted from the figure.

**Why excluded, not counted as a miss:** A job cut off by run end may well have been on track for goodput. Counting it as "not goodput" systematically penalizes low-λ runs (where chain duration exceeds inter-arrival, so jobs released near the run end can't finish before termination). The classified-only rate isolates real SLO behavior.

If you change the run duration or window, the unclassified count shifts accordingly; report it alongside the goodput rate when the share is non-trivial.

### Rejected-at-start exclusion (unclassified jobs)

In addition to run-boundary cutoffs, jobs whose **very first call** (lowest
`call_index`) was rejected by admission control are also treated as
**unclassified** — the system declined the job before it produced any output,
so it never made a real SLO attempt.

- `parse_application_metrics.py` `add_tau_goodput()` builds the
  `rejected_at_start_ids` set from the calls table (first call's
  `is_rejected_bool=True`) and excludes those jobs via
  `classifiable = ... & ~rejected_at_start`.
- **Mid-chain rejected jobs are NOT excluded**: a job that succeeded on
  call 1-2 and was then rejected at call 3 *did* expend system effort, so
  it stays in the denominator as a not-goodput attempt. Halo's design
  (admission decision only at first call) tends to produce only
  from-start rejects; admission-ratio-style policies can produce
  mid-chain rejects too.
- Backward compatibility: for runs **without** admission control no calls
  are rejected, so `rejected_at_start_ids` is empty and behavior is
  unchanged.

**Why excluded, not counted as a miss:** "rejected before doing anything"
and "ran and missed the SLO" are different events. Lumping the former
into the not-goodput bucket conflates admission-policy aggressiveness
with application-level SLO failure rate. Keeping them in the
goodput-rate numerator/denominator would also reward over-conservative
admission (rejecting more → "fewer slow jobs ran" → higher rate) in a
way that hides the real attainment among admitted work.

## Confirmation Before Reporting Results

When the user asks to **show / summarize / build / compare** experiment results
("보여줘 / 정리해줘 / 결과 만들어줘 / 비교해줘"), restate the measurement
choices before producing tables or figures, and ask for confirmation:

1. **Metric**: call goodput rate, job goodput rate, rejection rate, slowdown distribution, throughput, wasted tokens, etc.
2. **Definition**: denominator (e.g. classified jobs only vs all jobs), tau threshold, baseline source, window range (warmup / drain handling).
3. **Run scope**: which runs are in / out, baseline location.

Restate even when the user is following up on a prior measurement — call out
the inherited choices in one or two lines so the user can correct them.
Do not silently reuse defaults from earlier in the conversation if the new
ask might want different ones (e.g. switching from per-run summary to a
windowed steady-state summary).

If a metric definition has recently changed (see "Run-boundary cutoffs"
above for the current definition of `job_goodput_rate`), note that in the
restatement so the user knows which version is being used.

## Figure Styling (paper figures)

Project-wide defaults for paper-grade figures. Apply these to every new plot
intended for the paper so the whole paper looks consistent. Figure size and
inner axes box can vary per figure; the items below should stay fixed unless
explicitly relaxed.

> **Not the same as the FluidServe policy figures.** The `exp-plot` skill in the
> outer repository carries a second style block for scheduler-policy comparison
> figures, where the colours are bound to arms (`#1f77b4` FluidServe,
> `#d62728` PolyServe, `#2ca02c` Llumnix SLO) rather than to metric levels. Here
> blue is the request/call-level metric and red is the job-level one, so the two
> palettes assign the same hex to opposite roles. Pick the block that matches
> what the figure compares and do not mix them. The physical-size rules
> (never `bbox_inches="tight"`, draw at the column width, `pdf.fonttype: 42`)
> hold for both and are written out in `exp-plot`.

Reference implementation: [analysis_scripts/job_level/summarize_sweep_window.py](analysis_scripts/job_level/summarize_sweep_window.py)
(`plot_goodput_vs_lambda`).

### rcParams (apply via `plt.rc_context`)

```python
PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "axes.linewidth": 0.75,
    "legend.fontsize": 8,
    "legend.frameon": False,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.major.size": 3.0,
    "ytick.major.size": 3.0,
    "xtick.major.width": 0.7,
    "ytick.major.width": 0.7,
    "lines.linewidth": 1.4,
    "lines.markersize": 4.5,
}
```

### Layout

- All four spines visible (full frame).
- y-axis dotted grid only: `linestyle=":"`, `linewidth=0.7`, `alpha=0.6`. No x-axis grid.
- Legend frameless, placed above the axes: `loc="lower center"`, `bbox_to_anchor=(0.5, 1.02)`, `ncol=N` (horizontal).
- Marker edge: white, 0.5 pt (helps when markers overlap).
- Save with `dpi=300`. Do **not** use `bbox_inches="tight"` when the inner axes box must hit a target physical size; pin it with `ax.set_position()` instead.
- For ACM 2-column (`acmart` `sigconf`): single-column max width is 3.33 in. If the inner axes box plus margins exceed that, let LaTeX scale via `\includegraphics[width=\columnwidth]`.

### Color palette

Use matplotlib `tab10` in this order so colors stay tied to roles across figures.

| Role | Hex |
|---|---|
| Request/call-level metric (primary) | `#1f77b4` (blue) |
| Job-level metric (secondary) | `#d62728` (red) |
| Third series | `#2ca02c` (green) |
| Fourth series | `#9467bd` (purple) |

If colorblind-safe output is required for a venue, swap to the Wong palette: `#0072B2`, `#D55E00`, `#009E73`, `#CC79A7`.

### Naming

- For latency-SLO attainment **rates** (the goodput rate metrics): use **"Request-level SLO attainment"** and **"Job-level SLO attainment"** in legends and prose. y-axis label: `"SLO attainment (%)"`.
- For token-level throughput plots (stacked-area, total tokens generated in the analysis window): use **"Goodput tokens"** for the SLO-meeting portion and **"Wasted tokens"** for the rest. The stack total = throughput. y-axis label: `"Output tokens (M)"` with values in millions.
- Avoid "call goodput" / "job goodput" in legends.
- Arrival-rate x-axis label: `r"Arrival rate $\lambda$ (jobs/sec)"`. On dense log-axis ticks, rotate labels 45°, `ha="right"`, `rotation_mode="anchor"`. Invert the axis so higher load is on the left.
- For rejection rate plotted alongside goodput, use a **twin y-axis** on the right labeled `"Rejection rate (%)"`. Keep tick/label/spine colors at the default (black) — only the rejection line itself carries its color (orange dashed).

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
