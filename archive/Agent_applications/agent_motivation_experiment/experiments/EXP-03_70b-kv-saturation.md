# EXP-03/04 — 70B switch: reaching real GPU-KV saturation

**Status**: done · **Date**: 2026-07-08 · **Model**: Meta-Llama-3-70B-Instruct (4×TP2, gpu-mem-util 0.9)

## Why
With Llama-3-8B on B200s the KV pool is enormous (96,346 blocks = 1.54M tokens/engine
at util 0.6) so KV never exceeded ~5% — KV-pressure phenomena (preemption, migration)
were unreachable. Switching to 70B shrinks the pool (36,570 blocks = 585k tokens/engine
at util 0.9), grows per-token KV 2.5×, and slows decode ~8×, so the same workload
actually fills KV. (Diagnosis note: low KV was NOT a KV-management bug — prefix cache
worked fine at ~80% hit; the model was simply too small for the hardware.)

## Setup deltas vs EXP-02b
- Engine: `Meta-Llama-3-70B-Instruct`, `--gpu-memory-utilization 0.9` (live LWS patched;
  source manifests updated — NB: source yamls are ${REPOSITORY} templates, never raw
  `kubectl apply -f` them).
- Gateway `--tokenizer-path` → 70B snapshot. Control plane stays at the 8-core fix.
- Runner passes `--model meta-llama/Meta-Llama-3-70B-Instruct`.
- Workload unchanged: sharegpt_request_level_poisson, rate-sweep, 5 min/condition,
  MP procs=8, cold restart per condition.

## Runs
- **EXP-03** (`exp03_70b_ratesweep`, rpm 300..4800 = 5..80 req/s, 2048 client conc)
- **EXP-04** (`exp04_70b_hiconc`, rpm 300..7200 = 5..120 req/s, *intended* 8192 conc but
  a flag bug kept it at 2048 — see "bug" below; still adds the 100/120 points)
- **EXP-04b** (`exp04b_70b_hiconc`, rpm 2400..7200 = 40..120 req/s, **true 8192 conc**
  after the fix; 5/10/20 not re-run — their concurrency demand (<300) never touches
  either cap, so EXP-04's points stand)

## Results (steady window [60s, dur-20s])

### EXP-04b (8192 concurrency) — the saturation regime
| offered | ok tput | comp% | out tok/s | gw_cur pk/steady | KV steady per-eng | preempt Σ4 |
|---|---|---|---|---|---|---|
| 40  | 41.8 | 95 | 16,865 | 1188/599   | 22-24%          | 0    |
| 60  | 58.3 | 87 | 22,255 | 2236/2054  | 70-75%          | 0    |
| 80  | 58.0 | 66 | 22,027 | 7999/5285  | **99-100% all** | **3,783** |
| 100 | 54.9 | 53 | 21,049 | 8192/7242  | **100% all**    | **4,587** |
| 120 | 51.6 | 45 | 19,886 | 8192/7818  | **100% all**    | **4,367** |

(EXP-03/04 at 2048 conc: same ok-tput curve — peak ~58.5 @60 declining to ~50 @120 —
but KV steady only 63-86% and preemptions ≤103. Raising client concurrency 4× did NOT
change throughput; it converted into resident KV pressure.)

## Findings
1. **True fleet-wide KV saturation achieved**: at ≥80 req/s all four engines sit at
   **100% KV for the entire steady window**, with **thousands of preemptions**
   (recompute churn) — the regime where KV-aware scheduling/migration matters.
2. **Server throughput ceiling ≈ 58 req/s / ~22.4k out-tok/s** (knee at 60), declining
   under overload (51.6 @120) — genuine congestion collapse at the engine layer
   (injection 87-100/s exceeded achieved; client is not the limiter).
3. **Throughput is invariant to client concurrency** past ~2k: 2048 vs 8192 conc gives
   identical ok-tput; extra in-flight work only deepens KV pressure and queueing.
4. **Under saturation, TTFT explodes while TBT stays flat**: per-engine mean TTFT climbs
   linearly 15s → ~90-100s across the 5-min run (unbounded engine-queue growth in
   open loop) while ITL holds ~100-150 ms/token — the SLO damage is queueing-shaped,
   not decode-shaped.
5. **Cold-start thundering herd** (seen at 2048 conc, rpm 1200/2400/3600/6000): right
   after a cold restart all CMS load metrics are 0/stale for ~0.5-1s; the initial
   open-loop backlog all lands on the argmin tie-winner. That one engine over-admits
   (running spike 575-1024), hits KV 94-100%, and its windowed mean TTFT reached **49s
   vs 0.3s on the other three** (rpm6000/EXP-04). At ≥40 req/s the hot engine stays hot
   for the whole run because the dispatch metric (all_prefills_tokens_num) does not see
   resident decode load.
6. **Migration still zero**: `neutral_load` requires dst projected-KV < threshold
   0.003 (0.3%) — under load no destination ever qualifies, even with a 30%p KV gap
   (60 req/s: eng 96% vs 65%). The 44 "rescheduling" counts at rpm300 (EXP-03) were
   failed `neutral_failover` attempts during cold start, not load migrations.
   → To demonstrate migration: raise `--rescheduling-neutral-load-threshold` to ~0.7.

## Bug found & fixed during EXP-04
`--load-threads 1024` was silently reset to 256 by the MP-default logic
(`if load_procs>1 and load_threads==1024: 256`) because an explicit 1024 was
indistinguishable from the default. Fixed with a None-sentinel default in
`run_experiment.py`; `run_config.json` now records the effective value (verify it
in the first condition of any sweep).

## Artifacts
- Summary: this file; plots under `results/aggregate_analysis/exp03_plots/` and
  `exp04_plots/`, `exp04b_plots/` (throughput_vs_rate, llumnix_/engine_ split,
  latency_rpm_* per-engine TTFT/ITL).
- Per-engine latency method: `analysis_scripts/request_level/plot_engine_latency.py`
  (windowed Δsum/Δcount of engine TTFT/ITL histograms — the only per-engine
  attribution; excludes gateway queueing by construction).

## Next
- **EXP-05 (proposed)**: migration ON-that-can-fire vs OFF at 60-80 req/s —
  `rescheduling-neutral-load-threshold 0.003 → 0.7`, compare hot-engine TTFT/ITL and
  tail latency with/without migration. This directly tests Llumnix's core claim on
  the observed herd/imbalance pathology.
