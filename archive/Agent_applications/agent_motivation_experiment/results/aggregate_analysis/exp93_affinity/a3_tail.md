# A3 — Does the class preference improve the latency tail?

EXP-93, mix-shift trace `dyn60_shift_m2Am1B_b1045`, four one-hour runs, two repeats per arm.

| arm | flag | runs |
|---|---|---|
| ON | `--fluidserve-class-affinity` on (`fspfx`) | `results/260822_2141_exp93r1_fspfx_shift`, `results/260823_0007_exp93br1_fspfx_shift` |
| OFF | preference off (`fsnoaff`) | `results/260823_0721_exp93nr1_fsnoaff_shift`, `results/260823_0834_exp93nbr1_fsnoaff_shift` |

No directory used here contains `PRERUN`.

## 0. Answer

The preference does not improve the within-request tail, which is the quantity this
report was asked to test and the quantity on which FluidServe currently loses to llm-d.
The median chat request's own p90 token gap rises from 56.2 ms with the preference off
to 64.9 ms with it on, and the median within-request shape ratio p90/p50 rises from
1.81 to 2.05. Both differences exceed the repeat spread by a factor of five or more,
and both hold for all three classes. A single within-request quantity moves the other
way for chat: the median worst gap falls from 506.6 ms to 412.6 ms, and the share of
chat requests whose worst gap lands in the 500-600 ms band falls from 52.6% to 32.7%.

The across-request tail does improve, and the improvement is large: the p99 over chat
requests of each request's own mean inter-token latency falls from 63.7 ms (off) to
52.0 ms (on), against a repeat spread of at most 0.31 ms. That improvement is not an
independent finding. It sits at the 95th to 99th percentile of the chat distribution,
which straddles the 50 ms chat per-token budget, so it is the already-measured
chat attainment gain of +2.7 points seen at finer resolution rather than a benefit
appearing where attainment does not move.

## 1. What was measured, and how the two quantities differ

**Across-request tail.** Each completed request contributes one number, its mean
inter-token latency. The report takes percentiles over requests. The per-request value
is `itl_ms` from `exp22_fluidserve.load_run`, which is `(e2e - ttft) / (output_tokens - 1)`.

**Within-request tail.** Each completed request contributes the p50, p90, p95 and max of
its OWN token gaps, as recorded by the client in `metrics.csv`
(`tbt_p50_ms`, `tbt_p90_ms`, `tbt_p95_ms`, `tbt_max_ms`). The report takes the median
over requests of each of those. One streamed chunk carries one token, so a chunk gap is
a token gap and the columns need no division; the comment at
`workloads/swe_bench_coding/agent.py:1001` records the measurement that established this
(chunks received are 0.991 of the tokens the server reports generating) and these runs
post-date the 2026-07-30 fix. The 700 GB `tbt_events.jsonl` files were not opened.

**Shape.** The ratio p90/p50 of one request's own gaps, median over requests. This
separates unevenness from level: a request served twice as slowly but at a steady pace
has the same shape ratio.

**Population.** Completed requests only: `~rejected & ~errored & ~cutoff` from `load_run`,
which removes rejections and run-boundary cutoffs whose latencies are lower bounds rather
than lengths. Within-request statistics additionally require `tbt_sample_count >= 30`,
so that a request's own p90 is taken over at least 30 gaps. Section 5 shows the result
does not depend on that filter.

## 2. Headline table — whole hour, per class, both repeats

Difference is ON minus OFF, averaged over repeats. Repeat spread is the larger of the two
arms' |repeat 1 - repeat 2|. "exceeds" is |difference| > repeat spread.

| class | quantity | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|---|
| chat | across p90 of per-req mean ITL (ms) | 48.43 | 48.19 | 48.89 | 48.75 | -0.51 | 0.24 | yes |
| chat | across **p99** of per-req mean ITL (ms) | 51.96 | 52.00 | 63.51 | 63.83 | **-11.69** | 0.31 | yes |
| chat | within med of own p50 gap (ms) | 31.44 | 31.52 | 30.74 | 30.69 | +0.76 | 0.08 | yes |
| chat | within med of own **p90** gap (ms) | 64.75 | 65.04 | 57.12 | 55.36 | **+8.65** | 1.76 | yes |
| chat | within med of own **max** gap (ms) | 414.49 | 410.67 | 507.03 | 506.09 | **-93.98** | 3.82 | yes |
| chat | within med of own **p90/p50** | 2.04 | 2.05 | 1.84 | 1.79 | **+0.23** | 0.05 | yes |
| deepresearch | across p90 of per-req mean ITL (ms) | 70.51 | 71.73 | 69.80 | 70.21 | +1.11 | 1.22 | NO |
| deepresearch | across p99 of per-req mean ITL (ms) | 74.71 | 75.94 | 73.23 | 73.76 | +1.83 | 1.22 | yes |
| deepresearch | within med of own p50 gap (ms) | 32.36 | 32.30 | 31.54 | 31.25 | +0.94 | 0.29 | yes |
| deepresearch | within med of own **p90** gap (ms) | 65.26 | 67.14 | 60.44 | 59.26 | **+6.35** | 1.88 | yes |
| deepresearch | within med of own max gap (ms) | 542.65 | 541.23 | 541.85 | 540.40 | +0.82 | 1.45 | NO |
| deepresearch | within med of own **p90/p50** | 2.00 | 2.08 | 1.90 | 1.89 | **+0.14** | 0.07 | yes |
| swe | across p90 of per-req mean ITL (ms) | 49.68 | 49.75 | 49.68 | 49.58 | +0.09 | 0.10 | NO |
| swe | across p99 of per-req mean ITL (ms) | 71.94 | 56.53 | 65.94 | 65.93 | -1.70 | **15.41** | NO |
| swe | within med of own p50 gap (ms) | 30.64 | 30.61 | 30.75 | 30.39 | +0.05 | 0.36 | NO |
| swe | within med of own p90 gap (ms) | 59.87 | 62.01 | 59.03 | 57.04 | +2.90 | 2.14 | yes |
| swe | within med of own max gap (ms) | 527.31 | 518.02 | 527.52 | 529.39 | -5.79 | 9.30 | NO |
| swe | within med of own p90/p50 | 1.92 | 1.98 | 1.89 | 1.86 | +0.07 | 0.07 | yes (marginal) |

Completed-request counts (ON r1 / ON r2 / OFF r1 / OFF r2), the denominators of every
row above: chat 53,652 / 53,869 / 53,590 / 54,094; deepresearch 14,696 / 14,586 /
14,031 / 14,074; swe 7,267 / 7,444 / 7,669 / 7,444.

Two rows fail the repeat test and must not be read as differences. The swe across-request
p99 has a repeat spread of 15.41 ms inside the ON arm alone (71.94 against 56.53) while the
OFF arm's two repeats agree to 0.01 ms; nothing can be said about swe's across-request tail
from these two repeats. The deepresearch across-request p90 difference of 1.11 ms sits under
its 1.22 ms spread.

## 3. Where in the distribution the across-request gain sits

Percentiles over chat requests of each request's own mean inter-token latency (ms),
completed chat requests only, both repeats.

| arm | rep | q50 | q75 | q90 | q95 | q97 | q98 | q99 | q99.5 |
|---|---|---|---|---|---|---|---|---|---|
| OFF | r1 | 44.62 | 46.57 | 48.89 | 50.13 | 52.59 | 56.65 | 63.51 | 70.82 |
| OFF | r2 | 44.68 | 46.67 | 48.75 | 50.35 | 53.17 | 56.97 | 63.83 | 71.09 |
| ON | r1 | 44.44 | 46.54 | 48.43 | 49.30 | 49.94 | 50.52 | 51.96 | 54.80 |
| ON | r2 | 44.52 | 46.58 | 48.19 | 48.95 | 49.56 | 50.22 | 52.00 | 55.43 |

The two arms agree to within 0.5 ms up to q90 and separate above it. The chat per-token
budget is 50 ms. With the preference off the chat distribution crosses 50 ms at about q95;
with it on it crosses at about q98. The 2.7-point chat attainment gain already recorded for
this experiment is that shift of the crossing point, so the across-request tail result
restates the attainment result rather than adding an independent one.

The same quantity derived a second way agrees. `tbt_mean_ms` is the client's mean of the
per-request chunk gaps and is computed from arrival timestamps, not from `(e2e - ttft)`.
Its p99 over chat requests is 52.64 / 52.42 ms (ON) and 62.83 / 63.05 ms (OFF), within
0.7 ms of the `itl_ms` values in every one of the twelve class-by-arm-by-repeat cells.

## 4. What changes inside a single request

Median over chat requests of that request's own gap percentile (ms), completed chat
requests with at least 30 gaps.

| arm | rep | own p50 | own p75 | own p80 | own p85 | own p90 | own p95 | own max |
|---|---|---|---|---|---|---|---|---|
| OFF | r1 | 30.74 | 33.88 | 35.34 | 38.88 | 57.12 | 127.04 | 507.03 |
| OFF | r2 | 30.69 | 33.74 | 35.10 | 38.32 | 55.36 | 126.96 | 506.09 |
| ON | r1 | 31.44 | 35.26 | 37.27 | 43.30 | 64.75 | 122.79 | 414.49 |
| ON | r2 | 31.52 | 35.16 | 37.08 | 42.94 | 65.04 | 122.04 | 410.67 |

The preference makes the middle of a chat request's own gap distribution worse and the
extreme better. From p50 to p90 the ON arm is above the OFF arm, by 0.8 ms at p50 rising
to 8.7 ms at p90. At p95 the order reverses by 4.6 ms, and at the maximum it reverses by
94.0 ms. Every one of those differences exceeds the repeat spread, which is at most
1.76 ms for the percentile columns and 3.82 ms for the maximum.

The distribution of the per-request worst gap shows the reversal directly. Percentage of
completed chat requests whose largest single token gap falls in each band:

| worst gap (ms) | OFF r1 | OFF r2 | ON r1 | ON r2 |
|---|---|---|---|---|
| 0-100 | 2.06 | 2.01 | 1.91 | 1.72 |
| 100-200 | 3.64 | 3.51 | 12.51 | 13.17 |
| 200-300 | 6.28 | 6.92 | 18.14 | 15.62 |
| 300-400 | 10.85 | 10.86 | 14.88 | 17.75 |
| 400-500 | 22.95 | 22.58 | 17.82 | 19.45 |
| **500-600** | **52.59** | **52.56** | **33.66** | **31.67** |
| 600+ | 1.63 | 1.56 | 1.09 | 0.62 |

With the preference off, 52.6% of chat requests (both repeats within 0.03 points) have a
worst gap of 500-600 ms. With it on, 32.7% do (repeats 33.66 and 31.67, spread 1.99
points). The share of chat requests that never see a gap above 300 ms rises from
12.0 / 12.4% to 32.6 / 30.5%.

The frequency of long gaps within a request also falls for chat. The share of completed
chat requests whose own p95 gap exceeds 150 ms, meaning at least 5% of that request's
tokens waited longer than 150 ms, is 14.66 / 13.36% with the preference on and
19.42 / 18.91% with it off.

## 5. The within-request result is not an artifact of the filters

Chat, whole hour, under four different populations. The `tbt_sample_count >= 30` filter
drops 4.5% of completed chat requests; the length band removes any composition effect from
output length, whose median is 410-411 tokens in all four runs.

| filter | ON r1 p90 | ON r2 p90 | OFF r1 p90 | OFF r2 p90 | ON r1 shape | ON r2 shape | OFF r1 shape | OFF r2 shape |
|---|---|---|---|---|---|---|---|---|
| no filter | 64.17 | 64.44 | 56.61 | 54.92 | 2.03 | 2.04 | 1.83 | 1.78 |
| sample count >= 30 | 64.75 | 65.04 | 57.12 | 55.36 | 2.04 | 2.05 | 1.84 | 1.79 |
| sample count >= 100 | 65.41 | 65.75 | 57.77 | 56.05 | 2.06 | 2.07 | 1.86 | 1.81 |
| output 350-450 tokens | 65.71 | 65.90 | 57.77 | 56.06 | 2.06 | 2.07 | 1.86 | 1.80 |

## 6. Even-mix segment (s1_A, 960-1860 s, target mix 33.3/33.3/33.3)

| class | quantity | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|---|
| chat | across p90 (ms) | 49.36 | 49.48 | 49.87 | 49.97 | -0.50 | 0.12 | yes |
| chat | across p99 (ms) | 54.77 | 57.71 | 64.78 | 66.80 | -9.54 | 2.94 | yes |
| chat | within own p90 (ms) | 61.14 | 66.12 | 60.73 | 59.43 | +3.55 | 4.98 | NO |
| chat | within own max (ms) | 517.69 | 508.75 | 523.56 | 529.27 | -13.19 | 8.94 | yes (marginal) |
| chat | within p90/p50 | 1.97 | 2.10 | 1.97 | 1.95 | +0.07 | 0.13 | NO |
| deepresearch | across p99 (ms) | 75.37 | 76.26 | 73.35 | 73.95 | +2.16 | 0.89 | yes |
| deepresearch | within own p90 (ms) | 68.03 | 74.85 | 64.07 | 61.96 | +8.42 | 6.82 | yes (marginal) |
| deepresearch | within p90/p50 | 2.12 | 2.33 | 2.05 | 2.00 | +0.20 | 0.21 | NO |
| swe | across p99 (ms) | 74.38 | 56.88 | 64.49 | 66.54 | +0.12 | 17.49 | NO |
| swe | within own p90 (ms) | 65.75 | 67.15 | 61.29 | 59.11 | +6.25 | 2.18 | yes |
| swe | within p90/p50 | 2.09 | 2.14 | 1.98 | 1.94 | +0.16 | 0.05 | yes |

Counts (ON r1 / r2, OFF r1 / r2): chat 6,503 / 6,252 / 6,230 / 6,280; deepresearch
4,881 / 4,758 / 4,659 / 4,708; swe 4,505 / 4,694 / 4,772 / 4,625.

The even-mix segment separates the two findings. The chat across-request p99 gain survives
here at -9.54 ms against a 2.94 ms spread, so it is not produced only by the chat-heavy
segments. The chat within-request gains and losses do not survive: the p90 difference of
+3.55 ms sits under the ON arm's own 4.98 ms repeat spread, and the shape ratio difference
of +0.07 sits under its 0.13 spread. The reduction in the chat worst gap, which is 94 ms
over the whole hour, is only 13 ms here. When every instance holds all three classes by
construction, the preference has fewer instances it can free.

## 7. Per-segment worst gap for chat

Median over completed chat requests of that request's largest single token gap (ms).
Segment mixes are chat/deepresearch/swe from the plan file.

| segment | mix | ON r1 | ON r2 | OFF r1 | OFF r2 |
|---|---|---|---|---|---|
| s0_m2 (60-960 s) | 93/5/2 | 302.61 | 325.91 | 466.14 | 455.25 |
| s1_A (960-1860 s) | 33/33/33 | 517.69 | 508.75 | 523.56 | 529.27 |
| s2_m1 (1860-2760 s) | 77/15/8 | 352.91 | 360.55 | 517.55 | 518.09 |
| s3_B (2760-3660 s) | 60/30/10 | 498.08 | 480.59 | 510.25 | 505.48 |

The reduction in the chat worst gap tracks how skewed the mix is. It is 140-155 ms in the
two chat-dominated segments, 12-30 ms in the 60/30/10 segment, and inside the repeat
spread in the even-mix segment.

## 8. Mechanism — what is established and what is not

Two engine-side observations are consistent with the split result, and neither of them
establishes it.

The fleet-average running batch is the same in both arms, but its distribution across
instances is not. Mean of `vllm:num_requests_running` over the whole run, per instance,
from `server_metrics/engine_800{0,1,2,3}.jsonl` (3,725-3,729 scrapes per instance; all four
files carry nonzero counters in all four runs, so no stale instance id is involved):

| arm | rep | per-instance means | fleet mean | busiest instance p90 |
|---|---|---|---|---|
| OFF | r1 | 123.6 / 128.8 / 135.8 / 137.2 | 131.4 | 200.0 |
| OFF | r2 | 132.6 / 131.0 / 123.5 / 138.4 | 131.3 | 208.4 |
| ON | r1 | 130.4 / 159.7 / 122.6 / 125.1 | 134.4 | 253.0 |
| ON | r2 | 146.0 / 109.1 / 105.4 / 177.2 | 134.4 | 250.0 |

With the preference on, one instance runs a batch 20-35% larger than any instance runs with
it off, while the fleet average moves by 3 requests out of 131. A larger decode batch makes
each step longer and makes step time vary more, which would raise the middle of a request's
own gap distribution without adding long stalls. That is the shape of the observed
within-request change, but this report did not attribute individual requests to instances
and therefore did not check that the chat requests with the raised p90 are the ones on the
enlarged batch.

The reduction in the chat worst gap is consistent with fewer long prefill chunks landing on
chat-carrying instances: the 500-600 ms band that empties out is where a chunked prefill of a
multi-thousand-token prompt would appear, and deepresearch and swe prompts average 3,814 and
6,215 tokens against chat's 533. This report did not verify from engine-side timing that a
500-600 ms gap is a prefill chunk. `vllm:request_prefill_time_seconds_sum` and `_count` are
present in the scrapes and were not decomposed.

## 9. Confounds and what was not verified

**The two arms do not serve the same requests.** Rejection rates differ by class, so the
completed populations differ. Percentage of arrivals rejected (ON r1 / ON r2 / OFF r1 / OFF r2):
chat 10.84 / 10.44 / 10.93 / 10.11; deepresearch 29.41 / 29.94 / 32.65 / 32.41; swe
43.29 / 41.91 / 40.18 / 41.91. Chat and swe rejection rates overlap between arms. Deepresearch
does not: the ON arm admits about 3 points more of it, 14,696 / 14,586 completed against
14,031 / 14,074. The deepresearch within-request regression is therefore measured on a 4%
larger admitted population and part of it may be that extra load rather than the preference
itself. This report did not test that by subsampling to a matched population.

**Not verified.**
- No per-engine attribution of requests. `exp41_engine_view.attribute_engines` was not run,
  so no statement here connects a request's tail to the instance that served it.
- No use of `tbt_events.jsonl`. Every within-request number comes from the client's
  per-request summary columns.
- The client's chunk gaps include any coalescing in the HTTP or SSE path. The median
  request's own p50 gap is about 31 ms while its mean gap is about 44 ms, so the gap
  distribution is right-skewed within a request. Whether some of that skew is client-side
  buffering rather than engine scheduling was not tested. It affects both arms, and both
  arms ran the same client, so it should not create the ON-versus-OFF differences reported
  here; it does affect how the absolute shape ratio should be read.
- Engine-side step timing was not decomposed, so the attribution of the 500-600 ms band to
  prefill chunks is inference from prompt lengths, not measurement.
- Only two repeats per arm. Every "exceeds" verdict in this report compares a difference to
  the larger of two single-repeat spreads, which is a weak estimate of variance.
- No comparison against llm-d. The motivation for testing this dimension is that FluidServe
  loses to llm-d on within-request smoothness, but the llm-d runs of this trace were not
  read here.

## 10. Reproduce

```bash
cd /home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
python3 /home/nxclab/tools/a3_scratch/a3_final.py   # section 2 and 6 tables
python3 /home/nxclab/tools/a3_scratch/a3_tail4.py   # sections 4 and 7
python3 /home/nxclab/tools/a3_scratch/a3_tail5.py   # sections 5 and 8
```

---

# Appendix A — Adversarial verification (2026-08-24)

An independent check re-derived the headline from the raw per-chunk event log rather
than from the client's per-request summary columns. The numbers in sections 2 through 8
reproduce. The verdict in section 0 does not.

**The arithmetic survives. The reading "the preference makes each request's token stream
less even" fails.** The preference moves gap mass out of a chat request's far tail and
into its middle at an unchanged mean gap. The median chat request's own p90 gap rises,
as reported, but its own p95, p99 and maximum fall, its own standard deviation falls
from 55.4 ms to 46.8 ms, its coefficient of variation falls from 1.24 to 1.03, and the
Gini coefficient of its own gaps falls from 0.347 to 0.326. Every one of those
dispersion measures says the stream is more even with the preference on, and each
exceeds the repeat spread by a factor of 7 or more.

## A.1 The independent route

`tbt_events.jsonl` was opened for all four runs, which section 9 lists as not verified.
Each line is one request and carries every chunk's `inter_arrival_ms`. A streaming
extractor recomputed each request's own p50, p75, p85, p90, p95, p99, maximum, mean,
standard deviation and Gini coefficient from those raw gaps, then merged the result onto
`metrics.csv` on `(task_id, iteration, call_index)`. That key is unique in both tables in
all four runs, so no row multiplied. The four event files hold 99,242 request records
each, which is the trace's arrival count and is identical across arms; 78,088 to 78,725 of
them carry chunk gaps.

The client's `summarize_tbt_ms` uses the same linear-interpolation percentile as
`numpy.percentile`, so the two routes are comparable directly. They agree: over every
completed chat request in all four runs, the largest disagreement between the recomputed
p90 and the recorded `tbt_p90_ms` is 0.0001 ms, and `tbt_sample_count` equals the number
of gaps found in the event log for every request.

Two confounds close here. Chunks per token is 0.9935 with the preference on and 0.9935
with it off, so neither arm coalesces more than the other and a chunk gap is a token gap
in both. Median output length is 410 to 411 tokens in all four runs.

## A.2 The dispersion measures reverse the verdict

Median over completed chat requests with at least 30 gaps, whole hour, of that request's
own statistic. Recomputed from the chunk arrival offsets.

| chat, own-gap statistic | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|
| p50 (ms) | 31.44 | 31.52 | 30.74 | 30.69 | +0.76 | 0.08 | yes |
| p90 (ms) | 64.75 | 65.04 | 57.12 | 55.36 | +8.65 | 1.76 | yes |
| p95 (ms) | 122.79 | 122.04 | 127.04 | 126.96 | **-4.59** | 0.75 | yes |
| p99 (ms) | 263.23 | 265.78 | 319.54 | 322.35 | **-56.44** | 2.81 | yes |
| max (ms) | 414.49 | 410.67 | 507.03 | 506.09 | -93.98 | 3.82 | yes |
| mean (ms) | 44.96 | 45.05 | 45.07 | 45.16 | -0.11 | 0.08 | yes |
| standard deviation (ms) | 46.74 | 46.84 | 55.17 | 55.60 | **-8.60** | 0.43 | yes |
| coefficient of variation | 1.029 | 1.033 | 1.233 | 1.245 | **-0.208** | 0.012 | yes |
| Gini coefficient | 0.327 | 0.324 | 0.346 | 0.347 | **-0.021** | 0.003 | yes |
| p90/p50 | 2.042 | 2.053 | 1.840 | 1.793 | +0.231 | 0.047 | yes |
| p95/p50 | 3.903 | 3.865 | 4.132 | 4.130 | **-0.247** | 0.038 | yes |
| p99/p50 | 8.192 | 8.279 | 10.318 | 10.477 | **-2.161** | 0.159 | yes |
| max/p50 | 13.10 | 12.75 | 16.32 | 16.29 | -3.38 | 0.35 | yes |

The mean gap is the same in both arms to 0.11 ms out of 45, so the two arms spend the same
time per token inside a chat request. With the mean fixed, the standard deviation and the
Gini coefficient measure how unevenly that time is distributed across the request's own
tokens, and both are lower with the preference on. The p90/p50 ratio is the only shape
measure that rises, and it rises because it is blind to everything above p90, which is
where the entire difference between the arms sits.

Section 0's claim that the worst gap is the only within-request quantity that improves is
contradicted by section 4 of this report as well: its own p95 column falls from
127.04/126.96 to 122.79/122.04, and section 4's text records that reversal.

## A.3 Where the mass moves

Median over completed chat requests of the share of that request's own gaps in each band,
and of the share of its streaming time spent in gaps above 100 ms.

| chat | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|
| share of own gaps > 50 ms (%) | 13.04 | 12.93 | 11.41 | 11.11 | +1.73 | 0.30 | yes |
| share of own gaps > 100 ms (%) | 6.67 | 6.59 | 6.52 | 6.51 | +0.11 | 0.08 | yes |
| share of own gaps > 200 ms (%) | 2.30 | 2.31 | 2.93 | 2.94 | **-0.63** | 0.02 | yes |
| share of own stream time in gaps > 100 ms (%) | 29.39 | 29.28 | 32.46 | 32.48 | **-3.14** | 0.12 | yes |

The preference adds gaps in the 50-200 ms band and removes gaps above 200 ms. That is why
the p90 rises and the p99 falls at the same time, and it explains the steep step between
own p85 and own p90 in section 4: about 11 to 13 per cent of a chat request's gaps are
long, so the request's own p90 sits on the boundary between the short mode and the long
mode and moves by tens of milliseconds when that fraction changes by one point.

## A.4 The counter-result is more robust than the result it corrects

Under the four populations of section 5, and in each of the four trace segments, the
direction of the dispersion measures never changes. The write-up's own statistic does
change: in the even-mix segment s1_A its p90 difference (+3.55 ms) and shape difference
(+0.07) fall inside the repeat spread, as section 6 states. The dispersion measures
survive there.

Chat, per segment, median over requests:

| segment | statistic | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|---|
| s0_m2 (93/5/2) | std (ms) | 36.95 | 37.90 | 46.37 | 45.81 | -8.66 | 0.95 | yes |
| s0_m2 | coefficient of variation | 0.881 | 0.895 | 1.141 | 1.152 | -0.259 | 0.014 | yes |
| s1_A (33/33/33) | std (ms) | 59.50 | 58.22 | 62.33 | 63.35 | -3.98 | 1.27 | yes |
| s1_A | coefficient of variation | 1.262 | 1.226 | 1.309 | 1.339 | -0.080 | 0.036 | yes |
| s1_A | own p99 (ms) | 343.4 | 333.8 | 358.2 | 365.7 | -23.29 | 9.60 | yes |
| s2_m1 (77/15/8) | std (ms) | 38.46 | 39.42 | 55.71 | 57.67 | -17.75 | 1.96 | yes |
| s2_m1 | coefficient of variation | 0.868 | 0.898 | 1.280 | 1.319 | -0.417 | 0.039 | yes |
| s3_B (60/30/10) | std (ms) | 54.58 | 53.20 | 59.06 | 59.01 | -5.14 | 1.38 | yes |
| s3_B | coefficient of variation | 1.145 | 1.124 | 1.251 | 1.247 | -0.115 | 0.021 | yes |

Under the section 5 populations, whole hour: the coefficient of variation is 1.012/1.015
against 1.223/1.235 with no sample-count filter, 1.047/1.052 against 1.242/1.255 at
sample count 100, and 1.045/1.046 against 1.238/1.252 in the 350-450 output-token band.
The p90 rise of +8.5 to +8.9 ms is equally stable, so the two results coexist under every
filter.

## A.5 The other two classes do not carry the claim either

Recomputed from the chunk gaps, whole hour, same population rule.

For swe the preference changes nothing that survives its repeat spread except the own p90
(+2.90 ms, spread 2.14) and the own p99 (-14.18 ms, spread 10.19), which point in opposite
directions. Its coefficient of variation is 1.316/1.311 with the preference on against
1.317/1.349 with it off, a difference of -0.020 inside a 0.032 spread. Calling swe's sign
the same as chat's holds only for the p90 column.

For deepresearch every percentile from p50 to p99 rises, and so does the mean gap
(+0.50 ms, spread 0.41), while the standard deviation does not move outside its spread
(+1.56 ms, spread 2.05) and the max/p50 ratio falls (-0.84, spread 0.27). Deepresearch is
therefore a uniform slowdown rather than a loss of evenness, and it is measured on a
population the preference itself enlarged: 14,738/14,626 completed against 14,070/14,114,
4.7 per cent more.

## A.6 What was checked and found sound

- Section 8's engine numbers reproduce exactly. Each of the four `engine_800x.jsonl`
  files carries 3,725 to 3,729 scrapes of `vllm:num_requests_running` in every run, with
  no zero-valued or missing instance, so no stale instance identifier enters the mean.
  Per-instance means and the busiest instance's p90 match the table to the digit.
- No directory used is a `PRERUN` directory.
- Caveat 1 is correct. Chat attainment on the admitted denominator is 97.1/97.7 per cent
  with the preference on against 94.7/94.4 with it off, a gain of 2.85 points, and the
  across-request p99 gain sits at exactly the percentiles that cross the 50 ms chat
  budget. The two are one result.

## A.7 Two bookkeeping corrections

The counts in section 2, labelled "the denominators of every row above", are the
denominators of the within-request rows only. They are the counts after the
`tbt_sample_count >= 30` filter. The across-request rows use all completed requests:
chat 57,277 / 57,528 / 57,205 / 57,738 and deepresearch 14,738 / 14,626 / 14,070 / 14,114.
Section 5's statement that the filter drops 4.5 per cent of completed chat requests is
measured against requests that have a recorded gap distribution at all, not against
completed requests; against completed requests it drops 6.3 per cent.

## A.8 Corrected statement

The class preference redistributes the gaps inside a chat request rather than making them
less even. At an unchanged mean gap of 45.0 ms it raises the share of a request's own gaps
in the 50-200 ms band and lowers the share above 200 ms, so the median request's own p90
rises from 56.2 ms to 64.9 ms while its own p99 falls from 320.9 ms to 264.5 ms, its
maximum falls from 506.6 ms to 412.6 ms, and its standard deviation falls from 55.4 ms to
46.8 ms. On the within-request tail the preference helps chat; what it worsens is the
within-request body between p50 and p90.

## A.9 Reproduce the appendix

```bash
cd /home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
# per-request statistics recomputed from the raw chunk gaps (about 90 s per 5.2 GB file)
python3 /home/nxclab/tools/a3_verify/extract_gaps.py results/<run>/tbt_events.jsonl /home/nxclab/tools/a3_verify/<run>.csv
python3 /home/nxclab/tools/a3_verify/extract2.py     results/<run>/tbt_events.jsonl /home/nxclab/tools/a3_verify/f_<run>.csv
```

---

# Appendix B — Second adversarial pass (2026-08-24)

A second independent check tried to refute Appendix A's corrected claim. It rebuilt the
gap distribution from the raw chunk arrival offsets, compared the answered populations,
recomputed everything per trace segment, and scored the shift under every per-token SLO
rule this project's literature review found in use.

**The corrected claim survives as a statement about the distribution. It does not survive
as a statement about benefit without naming the scoring rule.** The mass does move out of
the far tail and into the 50-200 ms band, the movement is not a population effect, and it
holds in all four mix segments. What Appendix A left out is that a chat request's per-token
budget is 50 ms, and every gap the preference adds lands above it. Under the three rules
that average or accumulate over tokens the shift is a gain; under any rule that thresholds
a request's own gap percentile against 50 ms it is a loss, and the loss is 11.8 points of
chat attainment.

## B.1 The route, and what it agrees with

Gaps were recomputed as `diff(arrival_offset_ms)` over each request's `chunk_events`,
rather than by reading the `inter_arrival_ms` field that Appendix A read. The two routes
agree: over the 79,356 request records of `260822_2141_exp93r1_fspfx_shift`, the largest
disagreement in any of p50, p90, p99, maximum, mean and standard deviation is 0.0002 ms,
and the gap count matches exactly. The gap count also equals the client's recorded
`tbt_sample_count` for every completed chat request in all four runs (0 disagreements out
of 56,205 / 56,431 / 56,131 / 56,654).

Every gap statistic below comes from the event log. Class, rejection, first-token latency,
end-to-end latency and output length come from `metrics.csv`. No directory used contains
`PRERUN`. Chunks per output token is 0.9932 / 0.9931 (ON) against 0.9932 / 0.9931 (OFF), and
no chat request in any run used the non-streaming fallback, so a chunk gap is a token gap
in both arms and neither arm coalesces more than the other.

Throughout, "diff" is ON minus OFF averaged over repeats, "spread" is the larger of the two
arms' |repeat 1 - repeat 2|, and "exceeds" means |diff| > spread.

## B.2 The pooled distribution moves as the claim predicts

Appendix A measured the median over requests of each request's own share of gaps per band.
That averages a per-request ratio where a total-over-total is the natural quantity. The
pooled version is computed here instead: all gaps of all completed chat requests in one
population, each bucket's count divided by the total gap count. Whole hour, completed chat
only (rejections, errors and run-boundary cutoffs removed), 23.90 to 24.09 million gaps per
run over 56,131 to 56,654 requests.

| gap band (ms) | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|
| 0-25 | 16.170 | 15.426 | 17.212 | 17.075 | -1.346 | 0.744 | yes |
| 25-50 | 71.279 | 72.079 | 72.100 | 72.360 | -0.551 | 0.801 | NO |
| 50-100 | 6.388 | 6.408 | 4.590 | 4.457 | **+1.874** | 0.133 | yes |
| 100-200 | 3.950 | 3.895 | 3.236 | 3.186 | **+0.711** | 0.055 | yes |
| 200-500 | 2.025 | 2.028 | 2.519 | 2.560 | **-0.513** | 0.041 | yes |
| >500 | 0.189 | 0.165 | 0.342 | 0.362 | **-0.175** | 0.024 | yes |

Percentages are shares of all gaps. The far bucket moves in the claimed direction and by
a large factor: gaps above 500 ms fall from 0.342 / 0.362 per cent of all chat gaps to
0.189 / 0.165 per cent, which is 81,801 / 87,177 gaps falling to 45,202 / 39,583. Gaps above
200 ms fall from 2.861 / 2.922 per cent to 2.214 / 2.192. The 50-100 and 100-200 buckets
absorb more than that, so the total share above 50 ms rises from 10.688 / 10.565 per cent to
12.552 / 12.495.

The claim predicted only the >200 to 50-200 move. Two further movements are present and
were not stated. The 0-25 bucket also loses mass, by 1.35 points against a 0.74 spread, so
the preference removes the fastest gaps as well as the slowest. The 25-50 bucket does not
move outside its spread.

Measured as time rather than as counts, the same table reads:

| gap band (ms) | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|
| 0-25 | 7.473 | 7.201 | 8.067 | 7.985 | -0.689 | 0.272 | yes |
| 25-50 | 53.109 | 53.732 | 52.025 | 52.069 | +1.374 | 0.624 | yes |
| 50-100 | 10.387 | 10.486 | 7.391 | 7.147 | +3.168 | 0.244 | yes |
| 100-200 | 12.738 | 12.582 | 10.543 | 10.325 | +2.226 | 0.218 | yes |
| 200-500 | 13.984 | 13.989 | 17.808 | 18.081 | **-3.958** | 0.273 | yes |
| >500 | 2.309 | 2.010 | 4.168 | 4.394 | **-2.122** | 0.299 | yes |

Percentages are shares of total streaming time. The share of a chat stream spent waiting in
gaps above 200 ms falls from 21.98 / 22.47 per cent to 16.29 / 16.00 per cent.

The pooled mean gap is not unchanged. It falls from 43.339 / 43.441 ms to 42.901 / 42.822 ms,
a difference of -0.530 ms against a 0.100 ms spread. Appendix A's "unchanged mean" is the
median over requests of each request's own mean, which is 45.07 / 45.16 against 44.96 / 45.05.
Both statements are true of different quantities; the pooled one says the preference is also
slightly faster, not only smoother.

## B.3 The population is the same, and the result does not depend on it

The two arms answer the same chat population. Chat arrivals inside the analysis window are
64,300 / 64,284 (ON) against 64,288 / 64,294 (OFF). The chat rejection rate is 10.838 / 10.438
per cent against 10.931 / 10.111 per cent, a difference of +0.117 points against a 0.819 point
spread, so it does not exceed. Completed chat with a gap record is 56,205 / 56,431 against
56,131 / 56,654, a difference of -74.5 requests against a 523 request spread.

The output-length distribution of completed chat is the same at every quantile tested. No
quantile difference exceeds its spread: q05 35.0 in all four runs, q25 195 / 194 / 195 / 195,
q50 395 / 394 / 394 / 394, q75 582 / 581 / 581 / 581, q90 774 / 775 / 775 / 775, q99
1491.9 / 1494.7 / 1497.4 / 1478.0, mean 429.7 / 429.5 / 429.8 / 429.1. Gaps per completed chat
request are 425.7 / 425.6 / 425.8 / 425.1.

Two stronger controls were run anyway.

**Length matching.** Completed chat requests were stratified on the exact `output_tokens`
value, and each run was subsampled to the elementwise minimum count per value, giving four
subsets of 52,893 requests with byte-identical length histograms (94.1 / 93.7 / 94.2 / 93.4
per cent of each run's completed chat retained, seed 20260824). Restricting to at least 30
gaps leaves 50,410 to 50,412 requests per run.

**Pairing.** The trace's arrival times and task identifiers are the same across runs, so the
set of chat requests completed in all four runs can be intersected. That set holds 46,473
requests, 82.8 per cent of the smallest run's completed chat, and 44,199 of them carry at
least 30 gaps in all four runs. This removes every population difference exactly, including
any that length matching cannot see.

| chat, whole hour | population | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|---|
| median own p90 (ms) | unmatched | 64.75 | 65.04 | 57.12 | 55.36 | +8.65 | 1.76 | yes |
| | length-matched | 64.79 | 65.09 | 57.09 | 55.36 | +8.71 | 1.74 | yes |
| | paired | 64.28 | 64.09 | 55.21 | 53.31 | +9.93 | 1.90 | yes |
| median own p99 (ms) | unmatched | 263.23 | 265.78 | 319.54 | 322.35 | -56.44 | 2.81 | yes |
| | length-matched | 262.92 | 265.39 | 319.07 | 321.91 | -56.33 | 2.84 | yes |
| | paired | 243.41 | 248.78 | 315.23 | 317.57 | -70.31 | 5.37 | yes |
| median own max (ms) | unmatched | 414.49 | 410.67 | 507.03 | 506.09 | -93.98 | 3.82 | yes |
| | length-matched | 413.35 | 409.08 | 506.20 | 505.74 | -94.75 | 4.27 | yes |
| | paired | 385.24 | 386.45 | 505.74 | 506.37 | -120.21 | 1.20 | yes |
| median own std (ms) | unmatched | 46.74 | 46.84 | 55.17 | 55.60 | -8.60 | 0.43 | yes |
| | length-matched | 46.71 | 46.78 | 55.13 | 55.57 | -8.60 | 0.44 | yes |
| | paired | 43.72 | 43.74 | 54.45 | 54.94 | -10.97 | 0.49 | yes |
| pooled share of gaps >200 ms (%) | unmatched | 2.216 | 2.194 | 2.863 | 2.923 | -0.689 | 0.060 | yes |
| | length-matched | 2.222 | 2.202 | 2.871 | 2.931 | -0.689 | 0.061 | yes |
| | paired | 2.026 | 2.008 | 2.750 | 2.813 | -0.764 | 0.063 | yes |

Matching does not weaken the effect and pairing strengthens it. The population objection
fails for chat.

**No subgroup of chat is badly hurt.** The rise in the request's own p90 is bounded, and the
falls in its own p99 and maximum are not confined to the median request. Across completed
chat with at least 30 gaps, the request's own p90 rises at q10 (+4.51, spread 1.67), q25
(+8.31), q50 (+8.65), q75 (+8.62), q90 (+6.17) and q95 (+5.42), and falls at q99 (-6.14,
spread 1.84). The request's own p99 falls at every quantile from q10 (-22.58) to q99 (-28.97),
and so does its own maximum, from q10 (-96.95) to q99 (-62.43). The share of chat requests
whose own p90 exceeds 100 ms falls from 1.127 / 1.211 per cent to 0.796 / 0.822 per cent. The
preference moves the bulk of chat into a 60-100 ms own-p90 band: the share with own p90 in
[75, 100) ms rises from 8.61 / 8.45 per cent to 22.30 / 21.88 per cent.

## B.4 The direction holds in all four mix segments

The trace runs chat at 93.0, 33.3, 76.9 and 60.0 per cent of arrivals in four 15-minute
segments, so a pooled hour statistic could move because the arms answered chat in different
proportions at different times. Chat arrivals per segment are identical across runs to within
their spread. Pooled shares below are over completed chat with at least 30 gaps in that
segment.

| segment (chat share of arrivals) | quantity | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|---|
| s0_m2, 60-960 s (93.0%) | share of gaps >200 ms (%) | 1.546 | 1.590 | 1.910 | 1.975 | -0.374 | 0.066 | yes |
| | share of gaps 50-200 ms (%) | 9.823 | 9.314 | 7.293 | 7.222 | +2.311 | 0.509 | yes |
| | share of gaps >500 ms (%) | 0.112 | 0.106 | 0.145 | 0.165 | -0.046 | 0.020 | yes |
| s1_A, 960-1860 s (33.3%) | share of gaps >200 ms (%) | 3.600 | 3.609 | 3.882 | 3.899 | -0.286 | 0.016 | yes |
| | share of gaps 50-200 ms (%) | 8.220 | 8.682 | 7.658 | 7.529 | +0.857 | 0.462 | yes |
| | share of gaps >500 ms (%) | 0.338 | 0.337 | 0.462 | 0.507 | -0.148 | 0.045 | yes |
| s2_m1, 1860-2760 s (76.9%) | share of gaps >200 ms (%) | 1.691 | 1.738 | 3.085 | 3.203 | -1.429 | 0.117 | yes |
| | share of gaps 50-200 ms (%) | 12.044 | 11.962 | 7.907 | 7.360 | +4.369 | 0.547 | yes |
| | share of gaps >500 ms (%) | 0.156 | 0.131 | 0.475 | 0.507 | -0.348 | 0.032 | yes |
| s3_B, 2760-3660 s (60.0%) | share of gaps >200 ms (%) | 3.182 | 2.953 | 3.506 | 3.478 | -0.425 | 0.229 | yes |
| | share of gaps 50-200 ms (%) | 10.080 | 10.508 | 8.641 | 8.697 | +1.625 | 0.427 | yes |
| | share of gaps >500 ms (%) | 0.272 | 0.210 | 0.406 | 0.394 | -0.159 | 0.062 | yes |

The direction holds in every segment and exceeds the repeat spread in every cell. The same
is true on the paired request set, where the >200 ms share falls by 0.399, 0.300, 1.558 and
0.540 points in the four segments against spreads of 0.062, 0.019, 0.100 and 0.275.

**One segment does carry a population difference, and it points the other way from the
artifact hypothesis.** Chat rejection differs by segment even though it does not differ over
the hour. In s2_m1 the ON arm rejects 9.293 / 8.940 per cent of chat against the OFF arm's
6.258 / 6.603 per cent, a difference of +2.686 points against a 0.354 point spread, and s2_m1
is where the tail improvement is largest. Rejecting more chat could in principle leave an
easier population behind. In s0_m2 the ON arm rejects **fewer**, 1.047 / 1.516 per cent against
2.020 / 2.030 per cent, a difference of -0.744 points against a 0.469 point spread, and the
tail still improves there: >200 ms share -0.374 points, >500 ms share -0.046 points, median
own maximum 302.6 / 325.9 ms against 466.1 / 455.2 ms. The improvement therefore appears both
where the preference admits more chat and where it admits less, so admission selection does
not explain it. The paired set, which fixes the population exactly, gives the same s2_m1
result: >200 ms share 1.547 / 1.577 per cent against 3.070 / 3.170 per cent.

## B.5 Which rule calls this a benefit, and which calls it a loss

Chat's budget in this project is a 5 s first token and 50 ms per token. `slo-definitions.md`
records three rules in use in this literature, ordered from strict to loose: rule ①, every
token gap within budget, stated by SLOs-Serve and checked by them over blocks of 10 tokens;
rule ②, the cumulative deadline `t_i <= t_0 + B_ttft + i * B_tpot`, used by PolyServe, QoServe
and JITServe; and rule ③, the per-request mean, used by Scorpio and by this project. AdaGen
reports p90 TBT, and this project's own tail reanalysis scores the request's own p90 against
the budget, which is the rule under which FluidServe loses to llm-d.

Chat attainment, admitted denominator, completed chat with run-boundary cutoffs removed.
Token index for rule ② is the chunk index, which is 0.9932 of the token index in both arms.

| rule | ON r1 | ON r2 | OFF r1 | OFF r2 | diff | spread | exceeds |
|---|---|---|---|---|---|---|---|
| ③ mean per-token <= 50 ms and TTFT <= 5 s (this project's headline) | 96.699 | 97.290 | 94.284 | 93.964 | **+2.870** | 0.592 | yes |
| ② cumulative deadline, PolyServe / QoServe / JITServe | 98.046 | 97.881 | 96.172 | 95.831 | **+1.962** | 0.340 | yes |
| ① as SLOs-Serve implements it: worst 10-token block mean <= 50 ms | 11.460 | 11.589 | 8.482 | 8.158 | **+3.205** | 0.324 | yes |
| own p90 gap <= 50 ms, AdaGen-style, and TTFT <= 5 s | 26.550 | 24.602 | 36.329 | 38.405 | **-11.791** | 2.076 | yes |
| own p95 gap <= 50 ms and TTFT <= 5 s | 10.985 | 10.212 | 13.436 | 13.208 | -2.723 | 0.773 | yes |
| own p99 gap <= 50 ms and TTFT <= 5 s | 2.820 | 2.694 | 3.958 | 3.859 | -1.151 | 0.125 | yes |
| ① literally: every gap <= 50 ms and TTFT <= 5 s | 2.392 | 2.293 | 3.239 | 3.154 | -0.854 | 0.099 | yes |

On the offered denominator, where a rejected chat request counts as a violation, the signs
are the same: +2.456 for rule ③, +1.642 for rule ②, +2.855 for the 10-token block rule, and
-10.585 for the p90 rule.

**The dividing line is not strict versus loose. It is whether the rule averages over more
than one token.** Rule ③ averages over the whole request, rule ② accumulates slack from the
first token onward, and the SLOs-Serve block rule averages over ten tokens. All three absorb
a 150 ms gap sitting among short ones, and all three are broken outright by a 500 ms stall,
so all three prefer the preference on. A rule that compares a single gap percentile against
50 ms cannot absorb anything, and every gap the preference adds is above 50 ms, so it prefers
the preference off.

Rule ② shows the mechanism most directly. The minimum cumulative-deadline slack over a chat
request's tokens has its 1st percentile at +915 / +734 ms with the preference on and at
-2375 / -2186 ms with it off, and the share of completed chat that ever falls behind the
deadline line drops from 1.988 / 2.335 per cent to 0.084 / 0.216 per cent. A request survives
rule ② by never spending its accumulated slack in one stall, which is exactly what removing
the >200 ms gaps buys.

**Where the budget would have to sit for the percentile rules to agree.** Sweeping the
per-token budget B with the rule "the request's own p90 gap <= B", the OFF arm wins at
B = 50 ms (-12.02 points), 60 ms (-18.78) and 75 ms (-12.68), and the ON arm wins from
B = 100 ms (+0.39) upward through 500 ms (+0.05). Under "own p99 <= B" the crossover is
between 75 ms (-1.24) and 100 ms (+0.80), and by B = 250 ms the ON arm is ahead by 20.96
points. Under "own maximum <= B" the crossover is between 100 ms (-0.36) and 125 ms (+0.94),
and at B = 400 ms the ON arm is ahead by 23.79 points. Under "own mean <= B" the ON arm wins
at every B tested.

So Appendix A's central observation, that the request's own p99 falls from 320.9 ms to
264.5 ms, is a real distributional fact with no consequence at chat's 50 ms budget. Both
values are violations under every per-token rule in the table. The fall becomes an attainment
gain only for a budget at or above roughly 100 ms, which is deep research's budget, not
chat's.

**Two facts outside the tail question point the same way as rules ② and ③.** Chat first-token
latency is lower with the preference on at every quantile: median 466.6 / 449.2 ms against
637.4 / 651.6 ms, a difference of -186.6 ms against a 17.4 ms spread, and q99 3814.7 / 3912.8 ms
against 4055.7 / 4130.3 ms. Chat end-to-end latency is lower too: median 16.951 / 16.889 s
against 17.160 / 17.190 s, a difference of -0.254 s against a 0.063 s spread. The preference
is not paying for the tail with the head.

**Deep research pays, and it pays in the far tail as well.** Appendix A described deep
research as a uniform slowdown whose dispersion does not move. Pooled over all its gaps that
is not the whole picture: the share of deep-research gaps above 200 ms rises from
4.389 / 4.358 per cent to 4.941 / 4.867 per cent (+0.530, spread 0.074), the share above 500 ms
rises from 1.086 / 1.072 to 1.357 / 1.340 per cent (+0.270, spread 0.018), and the pooled mean
gap rises from 50.860 / 50.509 ms to 53.529 / 53.541 ms (+2.850, spread 0.352). The deep
research population is also 4.7 per cent larger with the preference on, so part of this is
extra admitted load rather than the preference itself, and that was not separated. For swe
nothing in the pooled bucket table moves outside its spread.

## B.6 Corrected statement after the second pass

The class preference moves gap mass inside a chat request out of the bands above 200 ms and
into the bands between 50 and 200 ms, and it also removes some gaps below 25 ms. Pooled over
23.9 to 24.1 million chat gaps per run, the share above 500 ms falls from 0.342 / 0.362 to
0.189 / 0.165 per cent and the share above 200 ms falls from 2.861 / 2.922 to 2.214 / 2.192
per cent, while the share between 50 and 200 ms rises from 7.827 / 7.643 to 10.338 / 10.303
per cent. The movement survives exact length matching, survives restricting to the 46,473
chat requests answered in all four runs, and holds in each of the four mix segments.

Whether that is a benefit depends on the scoring rule, and the answer is not the same for all
of them. Under this project's per-request mean rule the preference gains 2.870 points of chat
attainment on the admitted denominator. Under the cumulative-deadline rule used by PolyServe,
QoServe and JITServe it gains 1.962 points. Under SLOs-Serve's 10-token block rule it gains
3.205 points. Under a rule that scores the request's own p90 gap against the 50 ms chat budget
it loses 11.791 points, which is the rule under which this project already loses to llm-d.
Appendix A's dispersion measures are correct and its reading of them as an unqualified benefit
is not; the improvement they describe sits between 200 and 500 ms, where every per-token rule
in this literature already counts a violation.

## B.7 What exceeds the repeat spread, and what was not verified

Every difference quoted in B.2 through B.6 exceeds its repeat spread, with three exceptions,
all stated where they appear: the 25-50 ms pooled bucket (-0.551 against a 0.801 spread), the
whole-hour chat rejection rate (+0.117 against a 0.819 spread), and the completed chat count
(-74.5 against a 523 spread). The three exceptions are all quantities the claim needs to be
flat, so failing to exceed is the outcome the claim wants there.

**Not verified in this pass.**
- No per-engine attribution. Nothing here connects a chat request's gaps to the instance,
  the running batch size, or the prefill chunks it shared a step with. The mechanism in
  section 8 remains inference.
- No engine-side timing. The 200-500 ms band is not shown to be prefill chunks, and the
  50-100 ms band is not shown to be longer decode steps on a larger batch.
- No llm-d comparison. The reason this dimension matters is that FluidServe loses to llm-d
  on within-request smoothness, and the llm-d runs of this trace were not read here. Whether
  the preference narrows that gap is unknown.
- The deep-research far-tail regression was not separated from the 4.7 per cent larger
  population the preference admits.
- Rule ② was scored for chat only. Deep research's 100 ms budget and swe's end-to-end rule
  were not scored under the alternative rules.
- Rule ② indexes tokens by chunk index. Chunks are 0.9932 of tokens in both arms, so the
  deadline line is about 0.7 per cent steeper than a strict per-token line. The bias is
  identical in the two arms and cannot produce the difference between them.
- Client-side buffering was not ruled out. Chunks per token and the fallback rate are
  identical across arms, which rules out a difference in how many chunks arrive, but not a
  difference in when a fixed number of chunks is released.
- Segment boundaries were taken from the plan file and applied to each run's own first
  arrival. Wall-clock drift between runs was not checked.
- Two repeats per arm. Every "exceeds" verdict rests on a spread estimated from two points.

**Reproduce.**

```bash
cd /home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
# per-request bucketed gap histogram, from diff(arrival_offset_ms); ~40 s per 5.2 GB file
python3 /home/nxclab/tools/a3_pass2/extract_buckets.py results/<run>/tbt_events.jsonl /home/nxclab/tools/a3_pass2/<run>.csv
python3 /home/nxclab/tools/a3_pass2/extract_block.py   results/<run>/tbt_events.jsonl /home/nxclab/tools/a3_pass2/blk_<run>.csv
python3 /home/nxclab/tools/a3_pass2/an.py    # merge with metrics.csv -> D.pkl
python3 /home/nxclab/tools/a3_pass2/q1.py    # B.2 pooled buckets
python3 /home/nxclab/tools/a3_pass2/q2.py    # B.3 population
python3 /home/nxclab/tools/a3_pass2/q2b.py   # B.3 length matching
python3 /home/nxclab/tools/a3_pass2/q2c.py   # B.3 pairing
python3 /home/nxclab/tools/a3_pass2/q3.py    # B.4 per segment
python3 /home/nxclab/tools/a3_pass2/q4.py    # B.5 rule table
python3 /home/nxclab/tools/a3_pass2/q4b.py   # B.5 SLOs-Serve 10-token block rule
python3 /home/nxclab/tools/a3_pass2/q4c.py   # B.5 budget sweep
python3 /home/nxclab/tools/a3_pass2/q5.py    # B.5 coalescing, TTFT, other classes
python3 /home/nxclab/tools/a3_pass2/q6.py    # B.3 across-request distribution
```
