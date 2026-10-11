# Dev note — multiprocess load generator (`--load-procs`)

**Branch**: `feat/multiprocess-load-generator` · **Date**: 2026-07-06

## Problem
A single `run_experiment.py` process tops out at ~17 req/s (Python GIL: per-request
stream parsing + tiktoken + metrics serialize on one interpreter). The B200 fleet is
far faster, so one client cannot overload it — the client, not the fleet, saturates.
Not a band-aid target: we want the runner itself to sustain high load.

## Chosen design: multiprocessing inside one runner pod
Fan the **load generation** across N OS processes (each its own interpreter → no shared
GIL → ~N× throughput), while keeping **one coordinator** so the "restart the whole
engine every condition" independence rule still holds (multi-pod would fight over the
shared engine restart; multiprocessing inside one pod does not).

```
parent (coordinator, 1 process)
  ├─ per condition: cold-restart engine (condition_server_prep)  [unchanged]
  ├─ setup run dir + start server-side metrics collector          [unchanged, parent owns it]
  ├─ spawn N load-worker processes (spawn context) ───────────────┐
  │     each worker:                                              │  each at rate/N (or λ/N),
  │       - loads workload + its own task pool (seed = base+idx)  │  reuses the EXISTING
  │       - runs the existing _run_with_{rate,poisson,trace}      │  per-request path
  │         loop, writing to shard files under <run>/shards/      │  (make_llm → invoke_
  │       - returns its stats dict                                │   with_tracking) unchanged
  ├─ wait for all workers ────────────────────────────────────────┘
  ├─ merge shards → metrics.csv / tbt_events.jsonl / agent_logs / errors.log
  ├─ aggregate stats, print summary
  └─ finish_server_session: stop collector + capture migration logs  [unchanged]
```

## Key points
- **Reuse, not rewrite**: a worker is just a `MotivationExperimentRunner` with
  `load_procs=1`, `llumnix_cfg=None` (no collector), `enable_server_metrics=False`
  (no restart, no per-request scrape), pointed at shard paths, calling the existing
  `_run_with_rate_duration` / `_run_with_poisson_duration` / `_run_with_trace_duration`.
  So the adapter, metrics schema, workloads, and analysis scripts are untouched.
- **Coordinator stays single**: restart-per-condition + collector run in the parent
  only. Workers never restart or scrape. → independence rule intact.
- **Rate split**: worker k drives `rate/N` (fixed-rate) or `λ/N` (Poisson, additive) or
  the same trace scaled by 1/N. Combined offered load = the requested rate.
- **Task pools**: each worker builds its own pool with `seed = base_seed + k` so streams
  differ; disjoint enough to avoid pathological exact-duplicate prefix-cache masking.
- **Merge**: shard `metrics.part{k}.csv` → one `metrics.csv` (single header); shard
  `tbt.part{k}.jsonl` → `tbt_events.jsonl`; per-worker `agent_logs_p{k}/` and
  `errors.p{k}.log` merged. Analysis reads the merged files exactly as before.
- **New flags**: `--load-procs N` (default 1 = old behavior), `--load-threads`
  (per-worker ThreadPool cap, default 256 in MP mode). Recorded in `run_config.json`.
- **spawn context** (not fork): avoids fork-after-threads hazards (parent has a live
  collector thread). Workers get plain picklable config (args namespace + strings).

## Extra fix found during testing: pooled HTTP session
First MP scale test hit mass connection errors (`HTTPConnectionPool ... Max retries`,
`RemoteDisconnected`). Cause: `LlumnixCompletionsLLM` did a bare `requests.post` per
call → new TCP connection every request → churn / ephemeral-port exhaustion under
high concurrency. Fixed with a **process-shared `requests.Session` + large connection
pool** (`pool_maxsize=1024`, keep-alive) in `agent.py` (`_llumnix_http_session()`).
Required for the client to sustain high load.

## Test results (mp_test2: λ=40, load-procs=4, 2 min, no restart)
- MP works: 4 shards produced and **merged** into one `metrics.csv` (4847 rows ≈
  4800 offered — no duplication).
- **Client bottleneck fixed**: MP drove the system to saturation — `gateway_current`
  hit **1024** (hard gateway concurrency cap), `gateway_pending`=544. Single process
  never passed ~17 req/s.
- **New bottleneck = gateway control plane, NOT the GPU fleet**: with short ShareGPT
  outputs engines stayed ~20-25 running each, waiting ~1-3, **KV ~1.4%** (idle) while
  the gateway pegged at 1024. Full-mode does a per-request `scheduler:8088/schedule`
  round-trip that serializes throughput before the GPUs are stressed → GPU-KV "fleet
  overload" is not reachable via the gateway with short requests; the *serving system*
  overloads at the gateway first (queue/shed → latency explodes → goodput collapses),
  which is itself the phenomenon to measure.
- High failure count at λ=40 is mostly open-loop backlog cut at the duration boundary
  ("server terminated during streaming") — expected for an overloaded open-loop system.

## Status
- [x] args + runner load_procs/load_threads
- [x] `_run_multiprocess` + `_mp_load_worker` + shard merge
- [x] `_run_with_*` return stats + branch to MP when load_procs>1
- [x] py_compile + correctness test (shards merge; rows ≈ offered)
- [x] pooled HTTP session fix (connection churn under load)
- [x] scale test: MP drives system to saturation (gateway_current=1024); bottleneck
      is the gateway control plane, GPU fleet ~idle with short requests
- [x] EXP-02 rate-sweep (5→30 req/s, MP procs=8, restart-per-condition) — DONE: clean congestion-collapse curve (knee ~8-10 req/s, goodput 100%→25%); bottleneck = gateway control plane (gw_current→1357), GPU fleet idle (KV 2-4%). See EXP-02.
- [ ] follow-up for true GPU-KV overload: heavier/long-output requests or raise the
      gateway concurrency cap (config, not code)
