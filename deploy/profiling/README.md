# Latency profiling tables

`pkg/scheduler/policy.GetLatencyPredictor()` loads these two files and calls
`klog.Fatalf` if either is missing or empty, so every SLO-aware policy (`slo`,
and PolyServe) needs them to exist before the scheduler will start at all.

```
llama31-70b-b200-tp2/
  ttft.json   per-STEP prefill cost vs chunk size
  tpot.json   decode step time vs (batch size, KV tokens per request)
```

Regenerate with:

```bash
python3 ms_dev/scripts/gen_profiling_from_stepdump.py \
  --results-glob 'Agent_applications/agent_motivation_experiment/results/*exp16*/server_metrics/sched_steps.jsonl' \
  --out-dir deploy/profiling/llama31-70b-b200-tp2 \
  --timestamp '<now>'
```

Install into the live scheduler (ConfigMap, ~80 KB) and switch policy:

```bash
python3 ms_dev/scripts/set_scheduler_profiling.py --policy slo
python3 ms_dev/scripts/set_scheduler_profiling.py --policy load-balance   # revert
```

## How Llumnix reads them

`ttft.json` is **not** whole-prompt TTFT. `predictTtftLatencyByChunkPrefill()`
splits a pending prefill into `ceil(n / maxNumBatchedTokens)` chunks and sums
`Predict(chunk) + one decode step` per chunk, so each entry is the cost of one
scheduler step prefilling `tokens_num` tokens. Our engines report
`max_num_batched_tokens: 8192`, so only entries up to 8192 are ever queried; the
tail beyond it exists so a metadata mismatch cannot fall off the axis.

`InterpolationPredictor` picks a bounding box from the **marginal** axis values
and then requires all four corners to be present. A missing corner, or a query
outside the axis range, is an error that surfaces as `+Inf` predicted latency —
which silently drops that instance from every SLO filter. Both tables are
therefore emitted as complete cartesian grids spanning well past the observed
range (batch to 2048, KV/req to 65536).

`tokens_per_request` is the **logical** KV count (sum of `num_computed_tokens`),
which prefix caching inflates far past physical capacity — EXP-16 saw 7.0M
aggregate on a fleet whose physical KV holds ~0.6M. That is intended: Llumnix's
`allDecodesTokensNum` is the same logical quantity, so table and query agree.

## Provenance and confidence

Source: EXP-16 per-step scheduler dumps, 431,440 steps across 13 runs, 0
unparseable lines. Those runs used `llumnix_sched.InstrumentedScheduler` — stock
`AsyncScheduler` ordering plus a JSONL log, adding no scheduling logic and
leaving the token budget at the engine's fixed 8192. QoServe runs are excluded
by the generator (`deadline_sched.DeadlineScheduler` resizes chunks every step,
so its step times do not describe the stock engine).

`interval_ms` is the enter-to-enter gap between `schedule()` calls. Under async
scheduling the scheduler runs a step ahead of the model, so this gap is
**bimodal**: ~0.5 ms when it ran ahead, or the true forward time when it blocked
on a full output queue. Per-step attribution is therefore unusable in general,
and the two tables inherit very different confidence:

| table | basis | confidence |
|---|---|---|
| `tpot.json` | 377,838 decode-only steps. A long homogeneous decode phase throttles the scheduler to the forward rate, so the per-cell **median** is the true step time. 78 of 247 cells measured directly (median 2,194 samples/cell); the rest filled from `t = c0 + c_kv·(B·T) + c_nd·B` (R²=0.93) corrected by neighbouring cells' log-residuals, then isotonized so the surface is non-decreasing on both axes. | **good** |
| `ttft.json` | **Directly measured** by `ms_dev/scripts/measure_ttft_sweep.py`: one request at a time against an idle engine, timing the first streamed token, 20 prompt lengths × 5 repetitions. Up to the engine's 8192-token budget a prompt is a single scheduler step, which is exactly what `predictTtftLatencyByChunkPrefill` sums over. Points past 8192 are linearly extrapolated and flagged `"source": "extrapolated"`; they exist only so a metadata mismatch cannot fall off the axis. | **good** |

### The TTFT table was rebuilt (2026-07-25)

The first version was derived from the EXP-16 step dumps and could pin down only
**one** operating point, for the reason above: prefill's only sustained regime is
a full chunk. Everything else was a linear model through that anchor, and it was
wrong — the anchor came from saturated windows that had ~18 decodes running
alongside, so it charged prefill for decode work:

| chunk | old model | measured | old error |
|---|---|---|---|
| 256 | 35.7 ms | 26.4 ms | +35% |
| 1024 | 93.6 ms | 80.1 ms | +17% |
| 8192 | 634 ms | 519.7 ms | +22% |

The real curve is not linear either. It is flat at ~20.5 ms up to 64 tokens
(memory-bound, same floor a decode step pays) and then steepens — the local rate
runs 0.025 ms/token between 16 and 1024 but 0.063 ms/token above 2048, which is
attention growing with sequence length. Upstream's own table has the same shape.

Two controls make the sweep trustworthy. Every repetition draws **fresh random
token ids**, so no two requests share a prefix and the prefix cache cannot serve
a repeat as a few-millisecond hit. And a discarded warm-up round runs first, so
CUDA graph capture is not folded into the first measurement.

**Known limitation.** A per-chunk table indexed only by chunk size cannot see
that later chunks attend to the KV earlier chunks left behind. Summing the table
in 8192-token chunks therefore under-predicts a long prefill, and the sweep
measures by how much:

| prompt | measured | chunk-sum | ratio |
|---|---|---|---|
| 12,288 | 814.4 ms | 783.9 ms | 1.04 |
| 16,384 | 1120.8 ms | 1039.4 ms | 1.08 |
| 24,576 | 1769.4 ms | 1559.1 ms | 1.13 |

Under 5% for two chunks, 13% at three. This is a property of the table shape
Llumnix defines, not of the measurement, so it cannot be fixed here — it is a
floor on TTFT-prediction accuracy for long prompts, which matters for the swe
class (~22k input tokens).

The measured values include a small fixed HTTP and tokenizer overhead (the
~20.5 ms floor versus 17 ms for a decode step). It is left in: it is under 1% of
a full chunk, and upstream's tables carry the same overhead.

## Verified

Loaded by the real binary (`bin/scheduler-exp07`, built from this branch) with
`--scheduling-policy slo`:

```
create scheduler with policy: slo
Initialized LatencyPredictor with 26 TTFT points and 247 TPOT points
```

No `Fatalf`, and no `Predict error` / `outside the data range` over a two-minute
run against live CMS state. The generator additionally replays Go's
`findBoundingBox` + four-corner lookup in Python and asserts that every
realistic query resolves, including batch 1132 (the running depth EXP-14 FIFO
reached at overload) and 27,953 KV tokens per request (the EXP-16 maximum).
