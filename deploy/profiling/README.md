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
| `ttft.json` | **one** measured operating point. Sustained prefill-saturated windows only occur at a full chunk, because chunked prefill always fills the budget and partial chunks happen only on a request's last chunk. 123 windows / 413.8 s / 5.34 M tokens give 12,917 tok/s, cross-checked at 13,300 tok/s by an independent blocking-quantile estimate. The curve is then `t(c) = 16.43 ms + c / 13,263 tok/s`. | **provisional** |

Sanity check against upstream's own table (`Qwen3-32B` on H20, which ships at
1,412 tok/s prefill): our 13.3k tok/s for a 70B on 2×B200 is a 9.7× ratio where
hardware and model scaling predict ~14×, the gap being our lower MFU. Upstream's
curve also confirms the `floor + slope` shape used here.

**Replace `ttft.json` before any conclusion that depends on TTFT accuracy.** The
right source is a dedicated idle-engine prompt-length sweep — that is how
upstream built theirs (42 prompt lengths, 3 reps each). Planned before the P4
PolyServe experiment.

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
