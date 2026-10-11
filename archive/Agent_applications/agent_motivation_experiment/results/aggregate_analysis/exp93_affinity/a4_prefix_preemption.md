# EXP-93 class preference, dimension A4: prefix cache hit rate and preemption

Two engine-side effects that class grouping should produce and that no request-level
attainment number can show. Written 2026-08-24. Read-only analysis of four existing runs;
nothing was launched and no run directory was modified.

## 0. One-paragraph answer

The class preference produces a **small and partly noise-limited gain in prefix cache hit
rate** (fleet-wide +3.0 points on the query-pooled estimator with a repeat spread of at most
0.4 points, +1.8 points on the prefill-work-weighted estimator with a repeat spread of 1.6
points on the preference-on arm) and a **clear increase in preemption** (16.19 and 17.68
preemptions per 1,000 admitted requests with the preference on, against 10.43 and 10.32 with
it off; the difference of 6.6 is far outside the per-arm repeat spreads of 1.49 and 0.11).
The prefix gain does not arise by the mechanism the theory predicts. Grouping did create a
dedicated engine, but the class it dedicated the engine to was deep research, whose prompts
have the *lowest* intrinsic prefix reuse of the three classes, so that engine's hit rate
*fell* to 35.5%, the lowest of all sixteen engine-runs measured here. The fleet-level gain is
the residue of the other three engines becoming more chat-heavy. Meanwhile the same
concentration pinned one engine at KV occupancy p90 = 99.9-100.0% for forty-five consecutive
minutes and made it evict 1,411 times, more than the two evicting engines of either
preference-off run combined. On this dimension the preference is a net engine-side cost.

## 1. The runs and what actually differs between them

| arm | run directory | admitted | rejected | output tokens (admitted) |
|---|---|---|---|---|
| preference ON, repeat 1 | `results/260822_2141_exp93r1_fspfx_shift` | 79,518 | 18,737 (19.1%) | 42,260,650 |
| preference ON, repeat 2 | `results/260823_0007_exp93br1_fspfx_shift` | 79,822 | 18,409 (18.7%) | 42,365,280 |
| preference OFF, repeat 1 | `results/260823_0721_exp93nr1_fsnoaff_shift` | 79,162 | 19,078 (19.4%) | 41,792,567 |
| preference OFF, repeat 2 | `results/260823_0834_exp93nbr1_fsnoaff_shift` | 79,523 | 18,724 (19.1%) | 41,859,023 |

Arrivals are 98,231-98,255 in every run (spread 0.02%), admitted requests span 79,162-79,822
(0.8%), and admitted output tokens span 41.79M-42.37M (1.4%). The work done is therefore
close enough between the arms that normalising by admitted requests and by output tokens gives
the same ordering; both normalisations are reported below.

No `PRERUN` directory exists for these four runs, so none was excluded.

**The arms differ in one flag only.** The `fsnoaff` arm definition in the driver sets
`FS_AFFINITY=false` and, in the EXP-93 snapshot of that driver, pins `FS_PREFIX=true`
explicitly so that the prefix-aware prefill charge — which is a separate mechanism and would
otherwise have been left to the compiled default — matches the `fspfx` arm. EXP-93 section 9
records that the scheduler startup line read `affinity=false, affweight=0.00` with `prefix=true`
and the remaining five FluidServe flags identical to `fspfx`, and that the driver's own
verification step passed. This matters here more than on any other dimension: had the prefix
flag differed, the entire hit-rate comparison in section 3 would measure the wrong thing.

All four engines (ports 8000-8003) report non-zero counters in all four runs, so there is no
stale-instance identifier to discard.

## 2. A measurement correction that has to come first: the vLLM query counter is
   inflated by waiting-queue retries

The instruction was to compute the hit rate as total hits over total queries from
`vllm:prefix_cache_hits_total` and `vllm:prefix_cache_queries_total`, both cumulative counters,
using the difference between the first and last sample of each run. Neither counter resets in
any of the sixteen engine-runs (zero decreases across 3,725-3,729 one-second samples per
engine), so that difference is well defined. Taken literally it gives this:

| arm | engine | raw hits | raw queries | raw hit rate | queries / prompt tokens |
|---|---|---|---|---|---|
| ON r1 | 8000 | 393,856,896 | 1,683,759,172 | 23.4% | 44.7 |
| ON r1 | 8001 | 46,365,760 | 98,743,654 | 47.0% | 2.9 |
| ON r1 | 8002 | 54,520,416 | 109,097,520 | 50.0% | 2.9 |
| ON r1 | 8003 | 405,707,680 | 1,659,801,379 | 24.4% | 50.3 |
| ON r2 | 8002 | 792,028,336 | 3,414,321,303 | 23.2% | 112.0 |
| OFF r1 | 8000 | 334,751,712 | 1,362,224,663 | 24.6% | 37.6 |
| OFF r1 | 8001 | 336,709,264 | 1,316,653,884 | 25.6% | 40.8 |

The last column is the tell. On engines whose waiting queue stays empty the counter records
about 2.9 queried tokens per prompt token, a constant per-request overcount that cancels in the
hit-over-query ratio. On engines that develop a waiting queue it records up to 112 queried
tokens per prompt token. Sampling the counter's own derivative shows why: on engine 8000 of
ON repeat 1 the query rate is 10,700-42,800 tokens per second for the first 2,460 s while the
waiting queue is empty, and 1,256,000-2,252,000 tokens per second from 2,860 s onward, when the
queue holds 10-22 requests. The prompt-token rate does not change across that transition. The
vLLM V1 scheduler queries the prefix cache for a waiting request every time it reconsiders it,
so a request parked at the head of a full engine's queue is counted once per scheduler step,
several thousand times over its wait, and it contributes both a query and its own hits.

The consequence is that **the counter's face-value hit rate is a queueing measurement, not a
routing measurement**: it reads 23-26% on every congested engine and 45-52% on every
uncongested one, in both arms, and it would report the preference-off arm as marginally
*better* (fleet-wide 26.7% and 26.8%, against 25.4% and 25.5% with the preference on) purely
because the off arm spent slightly less time with a queue.

**Correction used here.** Accumulate the hit and query differences only across consecutive
one-second samples where that engine's `vllm:num_requests_waiting` is zero at both endpoints,
then take total hits over total queries within that set. This is still a total-over-total
aggregation, not a mean of per-window ratios — the error this project has recorded at up to
18.5 points — but the total is taken over a restricted set of intervals. Two checks say the
correction works:

- After the filter the queries-per-prompt-token ratio settles at 2.30-2.85 on every one of the
  sixteen engine-runs, against 2.73-112.0 before it. The inflation is gone and what remains is
  the constant per-request overcount that cancels in the ratio.
- On the nine engine-runs that never developed a sustained queue (waiting-queue p90 of 1-2
  requests), the filter moves the hit rate by only +0.7 to +4.8 points, mean +2.3. That
  residual bias is present in both arms and largely cancels in the arm difference.

**Coverage.** The clean intervals carry 18.7-54.2% of each engine's prefill work, measured as
the share of that engine's `vllm:prompt_tokens_total` increment that falls inside them. The
single worst case is engine 8002 of ON repeat 2, the permanently saturated one, at 18.7%. So
the corrected estimate is built from between a fifth and a half of the actual prefill work on
every engine, not from a marginal sliver. It is nonetheless a selected sample — the periods
when an engine had no queue — and section 6 says what that leaves unverified.

## 3. (a) Prefix cache hit rate

### 3.1 Per engine

Hit rate is the queue-clean estimator of section 2. Class shares are shares of *input tokens*
dispatched to that engine, not of requests, because the hit rate is token-weighted; the
request-count shares are given too since the fleet-composition claims elsewhere in EXP-93 use
those. Attribution of requests to engines is `attribute_engines`, which matched 100.0% of
admitted requests in all four runs (79,518 / 79,822 / 79,162 / 79,523).

| arm | engine | requests | chat tok% | deepresearch tok% | swe tok% | clean hit rate | raw hit rate | preemptions |
|---|---|---|---|---|---|---|---|---|
| ON r1 | 8000 | 18,791 | 22.6% | 44.7% | 32.7% | 51.8% | 23.4% | 564 |
| ON r1 | 8001 | 27,068 | 41.4% | 31.8% | 26.8% | 48.2% | 47.0% | 7 |
| ON r1 | 8002 | 19,030 | 23.3% | 30.1% | 46.6% | 54.8% | 50.0% | 0 |
| ON r1 | 8003 | 14,629 | 17.3% | 64.1% | 18.6% | 44.7% | 24.4% | 716 |
| ON r2 | 8000 | 24,726 | 35.6% | 30.4% | 34.0% | 48.3% | 47.6% | 0 |
| ON r2 | 8001 | 15,247 | 13.2% | 29.2% | 57.5% | 55.8% | 52.2% | 0 |
| ON r2 | 8002 | 7,502 | 4.2% | **90.7%** | 5.1% | **35.5%** | 23.2% | **1,411** |
| ON r2 | 8003 | 32,347 | 50.0% | 24.6% | 25.5% | 51.3% | 50.2% | 0 |
| OFF r1 | 8000 | 17,769 | 22.5% | 42.6% | 34.9% | 50.3% | 24.6% | 395 |
| OFF r1 | 8001 | 17,553 | 25.4% | 55.9% | 18.7% | 40.9% | 25.6% | 431 |
| OFF r1 | 8002 | 21,722 | 28.5% | 33.1% | 38.5% | 49.0% | 47.4% | 0 |
| OFF r1 | 8003 | 22,118 | 27.9% | 30.4% | 41.7% | 50.0% | 48.0% | 0 |
| OFF r2 | 8000 | 20,411 | 26.6% | 36.6% | 36.8% | 48.5% | 44.9% | 0 |
| OFF r2 | 8001 | 18,887 | 27.9% | 51.8% | 20.3% | 42.5% | 25.5% | 433 |
| OFF r2 | 8002 | 17,919 | 23.0% | 41.7% | 35.3% | 49.0% | 24.8% | 388 |
| OFF r2 | 8003 | 22,306 | 28.8% | 32.8% | 38.4% | 49.0% | 47.3% | 0 |

The preference-off engines all sit within 22.5-28.8% chat tokens and 30.4-55.9% deep research
tokens; the fleet is close to uniform. The preference-on engines span 4.2-50.0% chat tokens and
24.6-90.7% deep research tokens. Grouping happened. The extreme case, engine 8002 of ON
repeat 2 at 90.7% deep research tokens, is the dedicated engine the theory asks for, and its
hit rate is the lowest of the sixteen at 35.5%.

### 3.2 Fleet-wide, two aggregations

| arm | raw fleet hit rate (unusable, section 2) | clean, query-pooled | clean, prefill-work-weighted | token-weighted spread across engines |
|---|---|---|---|---|
| ON r1 | 25.4% | 50.6% | 50.1% | 3.8 pts |
| ON r2 | 25.5% | 50.8% | 48.5% | 7.3 pts |
| OFF r1 | 26.7% | 47.9% | 47.7% | 3.8 pts |
| OFF r2 | 26.8% | 47.5% | 47.3% | 2.7 pts |

The query-pooled figure is the sum of clean hits over the sum of clean queries across all four
engines. It gives **+3.0 points for the preference (50.7% mean against 47.7% mean), with a
repeat spread of 0.2 points on the ON arm and 0.4 on the OFF arm**, so the difference is
several times the spread. But it under-weights congested engines, whose clean intervals carry a
smaller share of their queries, and congested engines are exactly the ones the preference
creates. The work-weighted figure corrects for that by weighting each engine's clean hit rate
by that engine's total prompt tokens. It gives **+1.8 points (49.3% mean against 47.5% mean),
with a repeat spread of 1.6 points on the ON arm and 0.4 on the OFF arm**. Under this
aggregation the difference is only marginally larger than the ON arm's own repeat spread, and
by this project's rule it should not be read as an established difference.

Both aggregations agree on sign. The honest statement is that the preference raises the
fleet-wide prefix cache hit rate by somewhere between 1.8 and 3.0 points on a base of about
48%, and that the lower end of that range is not separable from repeat-to-repeat variation.
Neither end resembles the effect the theory anticipates, and which this project has previously
recorded on a static class partition (PolyServe's chat engine at 94.5% against its agent
engines at 66.7%).

### 3.3 Per segment

The trace is four fifteen-minute segments from
`traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.plan.json`: s0_m2 at 93.0% chat, s1_A at an
even 33.3/33.3/33.3 mix, s2_m1 at 76.9% chat, s3_B at 60% chat and 30% deep research. Hit rate
below is the query-pooled clean estimator restricted to each segment; "cov" is the share of that
segment's fleet prefill work inside the clean intervals.

| arm | s0_m2 | s1_A | s2_m1 | s3_B |
|---|---|---|---|---|
| ON r1 | 41.5% (cov 53%) | 61.9% (cov 43%) | 45.3% (cov 41%) | 47.3% (cov 34%) |
| ON r2 | 41.0% (cov 56%) | 63.0% (cov 42%) | 46.1% (cov 44%) | 47.1% (cov 34%) |
| OFF r1 | 37.3% (cov 53%) | 60.9% (cov 45%) | 41.8% (cov 41%) | 44.8% (cov 33%) |
| OFF r2 | 36.6% (cov 51%) | 60.3% (cov 43%) | 41.5% (cov 42%) | 44.8% (cov 37%) |

Difference (ON mean minus OFF mean), with the larger of the two per-arm repeat spreads in
brackets: s0 **+4.3** (0.7), s1 **+1.9** (1.1), s2 **+4.1** (0.8), s3 **+2.4** (0.2). All four
are above the repeat spread and all four have the same sign, which is why section 3.2 accepts
the sign while declining to pin the magnitude.

Two things about this table are worth stating rather than smoothing over. First, s0_m2 is
93.0% chat, a segment in which there is almost nothing to group, and it shows the *largest*
gain of the four. That is not the grouping mechanism; whatever produces it is something the
preference does to routing that is not class separation, and this analysis does not identify
it. Second, EXP-93 section 6 records that swe's prefix reuse is inflated during s1_A, because
the transcript pool holds 1,500 items and swe is a third of arrivals there, putting the
interval between repeats of the same transcript at about 2.7 minutes, inside the scheduler's
prefix index retention of about 2.8 minutes. The s1_A column should not carry weight in a
prefix argument. It happens to be the column with the smallest arm difference.

### 3.4 Where the gain comes from: class mix, not grouping

Fitting the clean per-engine hit rate on the engine's deep research and chat input-token shares
across all sixteen engine-runs gives

    clean hit rate (%) = 69.30 - 0.3568 x (deepresearch token %) - 0.2395 x (chat token %),  R^2 = 0.856

The correlation between an engine's deep research token share and its hit rate is **-0.850**
across the sixteen engine-runs; for chat share it is +0.303. Deep research prompts average
3,814 input tokens and reuse little of them; the hit rate an engine reports is largely a
statement about which class it was given.

The token-weighted mean residual from this fit is +2.14 and +0.21 points on the two
preference-on runs and -1.06 and -1.19 on the two preference-off runs: a **+2.3 point residual
gain that class mix does not explain, against an ON-arm repeat spread of 1.93 points**. That is
the size of the grouping benefit proper, and it is not separable from repeat variation.

So the mechanism is the reverse of the prediction in an important way. The preference does
concentrate a class; the class it concentrates is the one with the least prefix reuse; the
dedicated engine's hit rate falls rather than rises; and the fleet-level gain such as it is
comes from the remaining engines becoming more chat-heavy, which is a redistribution rather
than the creation of a cache-friendly specialist.

## 4. (b) Preemption

Counts are the difference in `vllm:num_preemptions_total` between the first and last sample of
each engine's series. No counter decreases anywhere, so no engine restarted mid-run and no
reset correction is needed. Extraction follows `engine_occupancy.py`, which uses the same
counter, the same last-minus-first difference, and the same `pick`-by-prefix key matching.

### 4.1 Fleet total, normalised by work

| arm | preemptions | per 1,000 admitted requests | per 1,000,000 output tokens | recomputed prompt tokens (lower bound) | as % of fleet prefill |
|---|---|---|---|---|---|
| ON r1 | 1,287 | 16.19 | 30.5 | 2,688,254 | 1.9% |
| ON r2 | 1,411 | 17.68 | 33.3 | 5,667,902 | 4.0% |
| OFF r1 | 826 | 10.43 | 19.8 | 1,560,291 | 1.1% |
| OFF r2 | 821 | 10.32 | 19.6 | 1,475,598 | 1.0% |

Per 1,000 admitted requests: ON mean 16.94 with a repeat spread of 1.49, OFF mean 10.38 with a
repeat spread of 0.11. The difference is **+6.56, which is 4.4 times the larger repeat spread**.
Per 1,000,000 output tokens: ON mean 31.9 (spread 2.8), OFF mean 19.7 (spread 0.2), difference
+12.2. Both normalisations give the same answer: **the class preference raises preemption by
about 63%.** This is the one number on this dimension that is unambiguously outside the noise.

The recomputed-token column multiplies each engine's preemption count by the mean input token
count of the requests dispatched to that engine, and divides by fleet prefill. Because vLLM V1
preempts by recompute, a preempted request re-prefills its whole prompt and the tokens it has
already generated, so the prompt alone is a lower bound. Read as a lower bound the preference
costs about 2 extra points of prefill work fleet-wide (ON 1.9% and 4.0%, OFF 1.1% and 1.0%) —
but note the ON repeat spread here is 2.1 points, as large as the difference, because this
quantity depends on which engine took the concentration and how long its requests were. The
count-based normalisation is the robust one; the token-cost figure is indicative.

### 4.2 Where in the fleet, and where in the hour

Preemptions per segment, fleet-wide (counter difference across each segment's endpoints):

| arm | s0_m2 (93% chat) | s1_A (even) | s2_m1 (77% chat) | s3_B (60% chat) | total |
|---|---|---|---|---|---|
| ON r1 | 0 | 417 | 316 | 527 | 1,287 |
| ON r2 | 0 | 521 | 339 | 504 | 1,411 |
| OFF r1 | 0 | 389 | 42 | 384 | 826 |
| OFF r2 | 0 | 398 | 35 | 362 | 821 |

Neither arm preempts at all in s0_m2. In s1_A, the segment that first loads the fleet with an
even mix, ON means 469 (spread 104) against OFF 393.5 (spread 9): a difference of 75.5 that is
smaller than the ON arm's own repeat spread and therefore **not a difference**. The gap opens
after s1_A. In s2_m1 the chat share returns to 76.9% and the preference-off fleet essentially
stops evicting — 42 and 35 preemptions, a spread of 7 — while the preference-on fleet stays at
316 and 339, a spread of 23. That is **8.5 times as many preemptions, with both repeat spreads
an order of magnitude below the gap**. In s3_B the ratio is 1.4 times (ON 515.5, spread 23;
OFF 373, spread 22), still clearly outside the spreads.

The KV occupancy per engine per segment says what is happening. Percentiles rather than means,
because occupancy has a ceiling at 100% and the mean hides how often an engine is against it.
Each cell is that engine's KV p90 in s0 / s1 / s2 / s3:

| arm | 8000 | 8001 | 8002 | 8003 |
|---|---|---|---|---|
| ON r1 | 37 / 48 / 90 / 100 | 45 / 51 / 42 / 51 | 41 / 49 / 51 / 54 | 39 / **100 / 100** / 56 |
| ON r2 | 39 / 48 / 47 / 53 | 43 / 50 / 51 / 55 | 43 / **100 / 100 / 100** | 37 / 50 / 45 / 55 |
| OFF r1 | 41 / 49 / 54 / 100 | 40 / **100** / 92 / 54 | 39 / 49 / 47 / 52 | 43 / 48 / 49 / 52 |
| OFF r2 | 39 / 64 / 79 / 53 | 46 / **100** / 64 / 56 | 38 / 48 / 47 / 100 | 40 / 49 / 54 / 54 |

With the preference off, saturation is transient and it moves: engine 8001 of OFF r1 is at p90
= 100% in s1 and has relaxed to 54% by s3, while 8000 saturates only in s3. With the preference
on in repeat 2, engine 8002 reaches p90 = 100% in s1 and is still there in s2 and s3 — forty-five
consecutive minutes with the pool essentially full — and it accumulates all 1,411 of that run's
preemptions, 188.1 per 1,000 requests dispatched to it, against 21.7-24.6 per 1,000 on the
evicting engines of either preference-off run. That engine's KV p50 over the whole hour is
99.0%, against 41.2-43.6% for the other three engines of the same run.

The answer to the question the task poses — a homogeneous batch may be easier to fit, or
concentration may push one engine into eviction — is **concentration pushes one engine into
eviction**, and the reason is visible in the class it concentrates. Deep research carries 3,814
input tokens and 969 output tokens per request, so a batch made mostly of deep research holds
far more KV per request than the fleet average. Engine 8002 of ON repeat 2 received 90.7% of
its input tokens as deep research and completed only 7,502 requests, the fewest of any engine
in any run, while consuming 30.1M prompt tokens. Grouping by class groups by KV footprint,
because in this workload the class *is* the footprint.

What the preference adds beyond the concentration itself is **persistence**. Both arms saturate
an engine during s1_A, when the even mix loads the fleet, and both arms preempt at the same
rate there. When the mix returns to 76.9% chat in s2_m1 the preference-off fleet redistributes
and eviction almost stops; the preference keeps sending each class back to the engine that
already holds the most of it, so the deep research pile stays where it is, that engine stays
against the KV ceiling, and it keeps paying recompute for another half hour.

## 5. Verdict on this dimension

Neither engine-side effect delivers the benefit the theory predicts.

- **Prefix cache hit rate: a small gain, direction consistent, magnitude not established.**
  +3.0 points query-pooled (repeat spreads 0.2 and 0.4) and +1.8 points work-weighted (repeat
  spreads 1.6 and 0.4) on a base of about 48%. Positive in all four segments. But once the
  engine's class mix is regressed out, the residual is +2.3 points against an ON-arm repeat
  spread of 1.93, and the largest per-segment gain is in the 93%-chat segment where there is
  nothing to group. The dedicated engine the preference creates has the *lowest* hit rate in the
  study, 35.5%, because the class it dedicates is the one with the least prefix reuse.
- **Preemption: a clear cost.** 16.19 and 17.68 per 1,000 admitted requests with the preference,
  10.43 and 10.32 without; 63% more, with repeat spreads of 1.49 and 0.11 against a difference
  of 6.56. Localised to the two segments after the mix relaxes, where the preference holds a
  deep-research pile on one engine that stays at KV p90 = 100% for forty-five minutes.

Taken together, on the engine side the class preference is a net cost in this trace. It does
not follow that the preference is a net cost overall: the pace-argument benefit it is supposed
to deliver is a request-level quantity, and the already-measured 1.4-point total attainment
difference is the summary of that. What this analysis rules out is the hypothesis that the
preference is paying for itself through cache locality or through easier-to-fit homogeneous
batches. Neither is true here.

## 6. What was not verified

- **Per-class prefix hit rate was not measured directly.** vLLM reports the counter per engine,
  not per class. The claim that deep research has the lowest reuse rests on the -0.850
  correlation between an engine's deep-research input-token share and its clean hit rate across
  sixteen engine-runs, and on the extreme case (90.7% deep research, 35.5% hit rate). It is an
  inference from the cross-engine variation, not a direct measurement.
- **The clean-interval estimator is a selected sample.** It uses only the intervals in which an
  engine's waiting queue was empty at both endpoints, which covers 18.7-54.2% of each engine's
  prefill work. If the hit rate genuinely differs between queued and unqueued periods for
  reasons other than the counter inflation, the estimator is biased. The +0.7 to +4.8 point
  shift it produces on never-queued engines shows some such bias exists; whether it is equal
  between the arms was not established, only that it is present in both.
- **Sub-second queueing is invisible.** Samples are one per second and the scheduler runs many
  steps per second, so an interval marked clean may contain brief queueing. This can only add
  inflation to the clean set, which biases the corrected hit rate downward, in both arms.
- **The s0_m2 result is unexplained.** The preference gains +4.3 points there in a segment that
  is 93.0% chat. No mechanism for that is offered and no candidate was tested.
- **Recompute cost is a lower bound and its magnitude is noisy.** It counts prompt tokens only,
  not the generated tokens a preempted request must also redo, and the ON-arm repeat spread on
  that figure (2.1 points of fleet prefill) is as large as the arm difference. The preemption
  *count* is the robust quantity; the token cost is indicative.
- **The two arms ran in different sessions** — the preference-on runs on 2026-08-23 from 13:40
  to 18:33 KST, the preference-off runs after 23:30 KST the same day (EXP-93 validity note V10).
  Under this project's 2026-08-01 rule that does not invalidate the comparison, but it means the
  repeat spreads reported here do not contain any session-to-session term, so they may understate
  the true uncertainty. The preemption difference (6.56 against spreads of 1.49 and 0.11) is
  large enough to survive a plausible session term; the 1.8-point work-weighted hit-rate
  difference is not.
- **Two repeats per arm.** Every spread quoted here is the range of two values, which is a weak
  estimate of variability.
- **`instance_cms_kv_cache_usage_ratio_projected` was not used anywhere.** All KV figures come
  from the engines' own `vllm:kv_cache_usage_perc`.
- **No causal test.** Nothing here isolates the preference from any interaction it may have with
  the other FluidServe mechanisms that were left on in both arms.

## 7. How to reproduce

Per-engine counters, both raw and queue-clean, and the preemption counts come from
`<run>/server_metrics/engine_800{0,1,2,3}.jsonl`, keys `vllm:prefix_cache_hits_total`,
`vllm:prefix_cache_queries_total`, `vllm:num_preemptions_total`, `vllm:num_requests_waiting`,
`vllm:prompt_tokens_total`, `vllm:kv_cache_usage_perc`. The raw columns and the preemption
totals are reproduced directly by

    python3 analysis_scripts/request_level/engine_occupancy.py \
      --runs 'results/*exp93*_fs*_shift' --summary-only

Request-to-engine attribution is `exp41_engine_view.attribute_engines`; arrival, class and
admission columns are `exp22_fluidserve.load_run`; segment boundaries are
`exp93_mix_shift.read_plan` over
`traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.plan.json`. The queue-clean estimator is
described in full in section 2 and is a filter over consecutive samples, not a new metric.

---

## Appendix A. Adversarial re-derivation (2026-08-24)

An independent check of the headline, run against the four raw run directories without
reusing this document's extraction path. The headline survives. Four numbers in the body do
not reproduce exactly, and one sentence of the mechanism needs restating; none of them changes
the verdict.

### A.1 What was re-derived, and by which route

**Preemption counts, by summing positive first differences instead of taking last minus
first.** The two routes agree exactly, and the intermediate quantities show why. Each engine
series carries exactly one `vllm:num_preemptions_total` label set, so no series was silently
summed or dropped. Every series starts at 0 and never decreases across 3,725-3,729 samples, so
the engines were restarted for each condition and no reset correction is possible or needed.
No sample carries `ok: false` in any of the sixteen engine-runs. The series span -55 s to
+3,670 s relative to the first arrival, and the last request of each run ends at 3,662-3,666 s,
so the counter window contains the whole load window in every run.

| arm | last minus first | sum of positive deltas | per-engine split |
|---|---|---|---|
| ON r1 | 1,287 | 1,287 | 564 / 7 / 0 / 716 |
| ON r2 | 1,411 | 1,411 | 0 / 0 / 1,411 / 0 |
| OFF r1 | 826 | 826 | 395 / 431 / 0 / 0 |
| OFF r2 | 821 | 821 | 0 / 433 / 388 / 0 |

The three zero-valued engines of ON repeat 2 are genuine zeros rather than stale identifiers:
those same engines report 35.6M, 41.0M and 35.9M prompt tokens over the run.

**The denominator, counted directly from `metrics.csv` instead of through
`exp22_fluidserve.load_run`.** Counting rows with `agent != "job_summary"` and splitting on
`is_rejected` gives 99,242 arrivals in all four runs and admitted counts of 80,476 / 80,813 /
80,129 / 80,500. Section 1 of this document reports 79,518 / 79,822 / 79,162 / 79,523, which is
1.2% lower in every run.

### A.2 The one defect found: numerator and denominator use different windows

`load_run` trims a warmup and a drain interval from `metrics.csv`, so this document's
"admitted" counts only requests that arrived inside the trimmed window. The preemption counter,
by contrast, is differenced across the entire run. The rate in section 4.1 therefore divides a
whole-run numerator by a trimmed-window denominator, which is the same-name-different-sample
error this project has recorded before.

The error is small and it is uniform across the arms, for a reason that can be checked rather
than assumed: no run preempts at all before t = 60 s or after t = 3,660 s, so the numerator
gains nothing from the untrimmed period. Recomputing both quantities over the whole run:

| quantity | ON r1 | ON r2 | OFF r1 | OFF r2 | ON mean (spread) | OFF mean (spread) | difference |
|---|---|---|---|---|---|---|---|
| admitted, whole run | 80,476 | 80,813 | 80,129 | 80,500 | 80,645 (337) | 80,315 (371) | +0.4% |
| preemptions per 1,000 admitted | 15.99 | 17.46 | 10.31 | 10.20 | **16.73 (1.47)** | **10.26 (0.11)** | **+6.47, +63.0%** |
| preemptions per 1M admitted output tokens | 30.22 | 33.03 | 19.60 | 19.45 | 31.63 (2.81) | 19.53 (0.15) | +12.10 |

The corrected gap is **+6.47 per 1,000 admitted requests rather than +6.56**, and the ratio is
unchanged at +63%. Restricting the denominator further, to admitted requests that completed
without truncation or error, gives 16.11 / 17.58 against 10.38 / 10.27 and the same +63%. The
headline is insensitive to which of the three denominators is used.

### A.3 Failure modes checked and not found

- **No PRERUN directory exists for any EXP-93 run** (`results/*exp93*PRERUN*` matches nothing),
  so no warmup run entered a count.
- **The comparison does not filter out rejections.** Rejected requests are counted as arrivals
  and excluded only from the admitted denominator, which is the definition the headline uses.
  Rejection rates are 18.91 / 18.57 / 19.26 / 18.89 percent, so the arms reject within 0.7
  points of each other and the denominators are comparable.
- **The hour-long aggregate does not hide a moving target.** Re-deriving the per-segment counts
  with segment boundaries taken from the plan file and time measured from the first arrival
  gives s0 0 / 0 / 0 / 0, s1 440 / 556 / 420 / 429, s2 293 / 304 / 11 / 4, s3 554 / 551 / 395 /
  388. These differ from section 4.2 by up to 38 counts per cell, because assigning a
  preemption to a segment depends on the time origin, and section 4.2 does not record which
  origin it used. The conclusion is not sensitive to the choice: s1 remains a non-difference
  (ON spread 116 against a gap of 73.5), and the s2 gap is if anything larger under this
  alignment, 298.5 against 7.5 rather than 327.5 against 38.5.
- **KV occupancy is reported as percentiles, not means, and the percentiles reproduce.** Engine
  8002 of ON repeat 2 has an hour-long p50 of 99.1% against 41.6-43.9% for the other three
  engines of the same run, and p90 = 100% in s1, s2 and s3.
- **Migration is not a hidden second difference.** The migration event logs contain four
  completed migrations in ON repeat 2 and none in the other three runs, against 821-1,411
  preemptions.
- **The extra preemption is not bought by admitting more work.** In s2_m1, the segment carrying
  the whole effect, the preference-on arm admitted *fewer* requests than the preference-off arm
  (21,365 and 21,431 against 21,743 and 21,662) while admitting 1.6% more input tokens (32.7M
  and 32.6M against 32.2M and 32.1M). A 1.6% difference in admitted prefill work cannot produce
  a 27-fold difference in eviction.

### A.4 The session confound, quantified rather than waved at

Caveat (f) is the one that could still overturn the magnitude, and it deserves a number. This
project has measured a 26% movement in preemption count between two runs of an identical
configuration in different sessions (EXP-41 and EXP-44, 1,471 against 1,852), while the
attainment scores of those same two runs moved by less than 0.5 points. Preemption is precisely
the quantity that does not travel between sessions. Both preference-on runs sit in one session
block and both preference-off runs in another, so the spreads of 1.47 and 0.11 contain no
session term at all.

Two facts bound the damage. First, a 26% session term applied against the ON arm would still
leave 1,349 / 1.26 = 1,071 against 823.5, a 30% excess. The sign and the order of magnitude
survive; the *point estimate* of 63% does not deserve two significant figures. Second, the two
sessions can be compared directly in s0_m2, the segment in which neither arm preempts, both
arms admit the same work, and the routing difference has had no time to act:

| | ON r1 | ON r2 | OFF r1 | OFF r2 |
|---|---|---|---|---|
| engine-reported inter-token latency, s0 | 38.00 ms | 37.88 ms | 37.68 ms | 37.75 ms |
| generation tokens, s0 | 9.67M | 9.64M | 9.57M | 9.56M |
| prompt tokens, s0 | 21.39M | 21.35M | 21.36M | 21.33M |
| fleet KV p90, s0 | 40.4% | 41.4% | 41.0% | 40.6% |

The two sessions are indistinguishable on all four quantities, at a between-session spread
smaller than the between-repeat spread within either arm. There is no detectable session-level
difference in engine speed or memory behaviour to which the preemption gap could be assigned.

### A.5 The hit-rate result reproduces under a filter that does not use the waiting queue

The queue-clean estimator of section 2 selects intervals by `num_requests_waiting == 0`, which
makes coverage a function of congestion and therefore of the arm. An independent filter avoids
that circularity: accept an interval when its queried tokens are at most five times its prompt
tokens, which rejects retry inflation directly without looking at the queue. Coverage rises
from 18.7-54.2% to 31.3-92.9% of each engine's prefill work.

| estimator | ON r1 | ON r2 | OFF r1 | OFF r2 | difference |
|---|---|---|---|---|---|
| queue-clean, query-pooled (section 3.2, reproduced exactly) | 50.6% | 50.8% | 47.9% | 47.5% | +3.0 |
| queue-clean, work-weighted (section 3.2, reproduced exactly) | 50.1% | 48.5% | 47.7% | 47.3% | +1.8 |
| query-ratio filter, query-pooled | 51.2% | 52.1% | 49.0% | 48.5% | +2.9 |
| query-ratio filter, work-weighted | 50.8% | 49.6% | 48.7% | 48.3% | +1.7 (ON spread 1.2) |

The per-engine clean hit rates of section 3.1 reproduce to the tenth of a point, the class-mix
regression reproduces at R² = 0.856 with a deep-research coefficient of -0.359, and the
deep-research-share correlation reproduces at -0.849. The joins behind the class shares matched
100% of admitted requests in all four runs. Section 3's conclusion stands: the gain is +1.7 to
+3.0 points, and the work-weighted end of that range is not separable from the preference-on
arm's own repeat spread.

### A.6 One mechanism sentence that needs restating

Section 4.2 argues that grouping by class groups by KV footprint, and supports it with "deep
research carries 3,814 input tokens and 969 output tokens per request". Two problems.

First, those per-class means do not reproduce. Over admitted requests, pooled across the four
runs, the means are chat 629 input / 421 output, deep research 3,907 / 960, and swe 6,066 /
503; the per-run values vary by less than 1%. No filter tried here produces 3,814 / 969 or the
533 / 389 quoted for chat.

Second, and more substantively, **swe carries the largest prompt of the three classes**, 6,066
tokens against deep research's 3,907. Prompt size alone therefore predicts that the swe-heavy
engines should evict, and they did not: engine 8002 of ON repeat 1 took 46.6% of its input
tokens as swe and preempted zero times, and engine 8001 of ON repeat 2 took 57.3% as swe and
also preempted zero times. What separates deep research is not the size of the footprint but
how long it is held and how much of it there is. A deep-research request decodes 960 tokens
against swe's 503, so it occupies its blocks for roughly twice as long, and the trace delivers
21,271 deep-research arrivals against 12,886 swe. The claim to make is that the preference
concentrates the class with the largest total KV occupancy-time, not the class with the largest
prompt.

A smaller wording point in the same paragraph: engine 8002 of ON repeat 2 was *dispatched*
7,580 requests (7,502 inside the trimmed window), which is a dispatch count and not, as
written, a count of requests it completed.

### A.7 Verdict

**The headline survives.** Re-derived by an independent route and with the window mismatch
repaired, the class preference raises preemption from 10.31 and 10.20 per 1,000 admitted
requests to 15.99 and 17.46, a gap of **+6.47 against repeat spreads of 1.47 and 0.11**, or
+63%. The effect is not produced by a difference in admitted work, by a stale or reset counter,
by a PRERUN directory, by migration, or by a detectable session-level difference in engine
behaviour. The prefix-cache half of the claim also survives, including its own hedge: the gain
is +1.7 to +3.0 points depending on aggregation, and the lower end sits inside the
preference-on arm's repeat spread.

Two corrections to carry forward: the rate is **6.47 rather than 6.56** per 1,000 admitted
requests, because section 4.1 divided a whole-run preemption count by a warmup-and-drain-trimmed
request count; and the per-class token means quoted in section 4.2 should read 3,907 / 960 for
deep research and 629 / 421 for chat, with the footprint argument restated in terms of
occupancy-time rather than prompt size.
