# A6 — The within-request tail against llm-d

EXP-93, mix-shift trace `dyn60_shift_m2Am1B_b1045`, six one-hour runs, two repeats per arm.
All six replay the same 99,242 arrivals from the same trace file with the same seed, so the
arrival times are byte-identical across arms.

| arm | what it is | runs |
|---|---|---|
| ON | FluidServe, class preference on (`fspfx`) | `results/260822_2141_exp93r1_fspfx_shift`, `results/260823_0007_exp93br1_fspfx_shift` |
| OFF | FluidServe, class preference off (`fsnoaff`) | `results/260823_0721_exp93nr1_fsnoaff_shift`, `results/260823_0834_exp93nbr1_fsnoaff_shift` |
| LLMD | llm-d (`llmdslo`) | `results/260822_2303_exp93r1_llmdslo_shift`, `results/260823_0129_exp93br1_llmdslo_shift` |

No directory used here contains `PRERUN`.

This report closes the item both `a3_tail.md` section 9 and its Appendix B section B.7 list as
not verified: "No llm-d comparison. The reason this dimension matters is that FluidServe loses
to llm-d on within-request smoothness, and the llm-d runs of this trace were not read here."

Throughout, "diff" is the mean over the two repeats of one arm minus the mean over the two
repeats of the other, "spread" is the larger of the two arms' |repeat 1 − repeat 2|, and a
difference is only called a difference when |diff| > spread. Cells that pass carry `*`.

## 0. Answer

**llm-d is not better everywhere, and the single rule on which this project was said to lose
to llm-d is the only rule on which it loses at all once the classes and the load are held
fixed.** On the whole hour, admitted denominator, chat, FluidServe with the preference on beats
llm-d by 7.048 points under the per-request mean rule and by 2.146 points under the cumulative
deadline rule, and loses by 31.607 points under the rule that scores the request's own p90 gap
against the 50 ms chat budget. The dividing line is the one Appendix B section B.5 already
identified between FluidServe's two arms: rules that average or accumulate over more than one
token prefer FluidServe, and rules that threshold a single gap percentile against 50 ms prefer
llm-d. Adding llm-d does not move that line, it puts a third arm on the same two sides of it.

**The comparison is confounded and the confound is large enough to reverse one of the two
verdicts.** llm-d rejects 45.61 per cent of arrivals (45.92 / 45.30) against FluidServe-ON's
18.91 per cent (19.07 / 18.74), and for chat specifically it rejects 47.27 per cent (47.60 /
46.94) against 10.64 per cent (10.84 / 10.44). Its fleet therefore delivers 8,581 / 8,723
output tokens per second against FluidServe-ON's 11,962 / 11,992, which is 27.8 per cent less
work. Restricting to the 13 of 60 one-minute windows in which the two arms' admitted request
rates agree within 10 per cent, FluidServe with the preference **off** beats llm-d on the own-p90
rule by 14.777 points where over the whole hour it lost by 19.816. Binning each arm by the token
throughput its own fleet was sustaining, FluidServe-ON's own-p90 attainment is 97.259 / 97.594
per cent below 7,000 tokens per second against llm-d's 92.303 / 79.135, so llm-d does not lead
at matched work either. **llm-d's own-p90 advantage over FluidServe-ON survives the correction,
but it shrinks from 31.6 points to 17.1 points and it is no longer an advantage over FluidServe
as such — it is an advantage over the class preference.**

**There is a band where FluidServe with the preference on wins on the within-request tail, and
it is the busy end of the trace, which is the useful direction.** In the 24 of 60 windows where
the trace offers 30 to 45 req/s, FluidServe-ON beats llm-d on the request's own p99 gap against
the 50 ms budget by 0.473 points against a 0.049 spread, on the share of chat gaps above 500 ms
by 0.152 points against a 0.020 spread, and its median request's own p90 gap has converged to
llm-d's within 1.45 ms (66.98 against 65.53). Over the whole hour FluidServe-ON also has the
lowest share of chat gaps above 500 ms of any of the three arms, 0.177 per cent against llm-d's
0.338 and FluidServe-OFF's 0.352.

## 1. Method, and which source produced which number

Appendix B's route is reused unchanged so that llm-d is scored by the same definition and not by
a second definition of the same name. The two llm-d event logs were passed through the same two
extractors Appendix B used, `extract_buckets.py` and `extract_block.py`, and merged onto
`metrics.csv` by the same key.

**From the raw token-event files** (`tbt_events.jsonl`, 3.77 and 3.83 GB for the two llm-d runs;
5.17 to 5.24 GB for the four FluidServe runs): every gap statistic. A gap is
`diff(arrival_offset_ms)` over one request's `chunk_events`. The bucket counts, the per-request
p50/p90/p95/p99/max/mean/standard deviation, the minimum cumulative-deadline slack, and the worst
10-gap block mean all come from there.

**From the client's per-request summary** (`metrics.csv`): class, rejection, error and cutoff
flags, first-token latency, end-to-end latency, output tokens, arrival time. The per-request mean
inter-token latency `itl_ms` is `(e2e − ttft) / (output_tokens − 1)` as computed by
`exp22_fluidserve.load_run`.

The client's recorded per-request gap quantiles were used only as a cross-check, never as a
reported number. The recomputed own-p90 and the recorded `tbt_p90_ms` differ by at most 0.0001 ms
over every completed chat request in all six runs, and `tbt_sample_count` equals the recomputed
gap count for every one of them, with zero mismatches out of 56,205 / 56,431 / 56,131 / 56,654 /
33,031 / 33,472.

**Arrivals are counted as `load_run` counts them, `agent != "job_summary"`**, after that
function's 60 s warm-up and 20 s drain filter. That leaves 98,231 to 98,255 arrivals per run out
of the trace's 99,242.

**How a rejection was classified, per arm.** Both arms set `is_rejected` on the request row, and
both also set `is_error` on the same row, so `load_run`'s `rejected` flag catches both. The
reason strings differ. FluidServe records one reason, `KV_THRESHOLD`, on all 18,429 to 19,113
rejections per run. llm-d records two, `KV_THRESHOLD` on 44,215 / 44,899 and `ADMISSION_REJECTED`
on 397 / 398; the second is the path through which the Envoy external processor refuses a request
that the gateway-side check let through. Both were counted as rejections. llm-d repeat 2 also
carries 2 rows that are `is_error` without `is_rejected`, a genuine HTTP 400, and those 2 rows are
counted as errors, not rejections. Run-boundary cutoffs are 119 to 236 rows per run and are
excluded from every population here, because a request cut off at the boundary has a lower-bound
output length rather than a length.

**Validity gate: a chunk gap is a token gap in llm-d too.** llm-d's data path is Envoy plus the
endpoint-picker, not the Llumnix gateway, so more or less chunk coalescing on that path would make
the gap distributions incomparable before any policy effect. Chunks per output token over
completed chat is 0.9935 / 0.9934 for llm-d against 0.9932 / 0.9931 for FluidServe-ON and
0.9932 / 0.9931 for FluidServe-OFF, a difference of 0.0003, and gaps per (output tokens − 1) is
the same figure. Neither path coalesces more than the other. No chat request in any of the six
runs used the non-streaming fallback.

## 2. Question 1 — the bucketed gap distribution, with llm-d as a third column pair

Pooled over all gaps of all completed chat requests, total over total: each bucket's gap count
divided by the run's total gap count. Completed means rejections, errors and run-boundary cutoffs
removed. Whole hour. Percentages are shares of all gaps.

### 2.1 Chat

Requests: ON 56,205 / 56,431; OFF 56,131 / 56,654; LLMD 33,031 / 33,472.
Gaps: ON 23.93 M / 24.02 M; OFF 23.90 M / 24.09 M; LLMD 14.18 M / 14.48 M.

| band (ms) | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | OFF−LLMD | spread |
|---|---|---|---|---|---|---|---|---|---|---|
| 0-25 | 16.169 | 15.426 | 17.212 | 17.075 | 25.435 | 17.473 | −5.656 | 7.962 | −3.977 | 7.962 |
| 25-50 | 71.278 | 72.079 | 72.100 | 72.360 | 65.566 | 72.952 | +2.420 | 7.386 | +2.971 | 7.386 |
| 50-100 | 6.388 | 6.408 | 4.590 | 4.457 | 3.695 | 3.848 | **+2.627*** | 0.154 | **+0.752*** | 0.154 |
| 100-200 | 3.950 | 3.895 | 3.236 | 3.185 | 3.103 | 3.505 | **+0.618*** | 0.402 | +0.007 | 0.402 |
| 200-500 | 2.025 | 2.028 | 2.519 | 2.560 | 1.852 | 1.896 | **+0.152*** | 0.045 | **+0.666*** | 0.045 |
| >500 | 0.189 | 0.165 | 0.342 | 0.362 | 0.350 | 0.326 | **−0.161*** | 0.024 | +0.014 | 0.024 |

Cumulative, share of all chat gaps above a threshold:

| threshold | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | OFF−LLMD | spread |
|---|---|---|---|---|---|---|---|---|---|---|
| >50 ms | 12.552 | 12.495 | 10.688 | 10.565 | 8.999 | 9.575 | **+3.237*** | 0.576 | **+1.340*** | 0.576 |
| >100 ms | 6.164 | 6.087 | 6.097 | 6.107 | 5.304 | 5.727 | **+0.610*** | 0.423 | +0.587 | 0.423 |
| >200 ms | 2.214 | 2.192 | 2.861 | 2.922 | 2.202 | 2.222 | −0.009 | 0.022 | **+0.680*** | 0.061 |
| >500 ms | 0.189 | 0.165 | 0.342 | 0.362 | 0.350 | 0.326 | **−0.161*** | 0.024 | +0.014 | 0.024 |

Pooled mean gap: ON 42.901 / 42.822 ms, OFF 43.339 / 43.441, LLMD 39.490 / 40.390.

**Where llm-d sits for chat.** It is the smoothest of the three in the 50-200 ms band and the
roughest of the three above 500 ms. Its share of gaps in 50-100 ms is 3.77 per cent against
FluidServe-OFF's 4.52 and FluidServe-ON's 6.40, so on the band that decides the own-p90 rule it
leads both FluidServe arms. Its share above 500 ms is 0.338 per cent, statistically the same as
FluidServe-OFF's 0.352 (difference +0.014 against a 0.024 spread) and 0.161 points worse than
FluidServe-ON's 0.177.

**Two of llm-d's six buckets cannot be read at all.** Its 0-25 ms share is 25.435 per cent in
repeat 1 and 17.473 in repeat 2, a repeat spread of 7.96 points, and its 25-50 ms share moves
7.39 points the other way. The two FluidServe arms agree to 0.74 and 0.80 points on the same two
buckets. No statement about llm-d's fastest gaps is supported by these two repeats. The four
buckets above 50 ms are stable in llm-d, with spreads of 0.024 to 0.402 points.

**Does the preference move FluidServe toward llm-d or away from it, for chat?** It depends on the
band, and the two directions are the two halves of the movement Appendix B measured.

- 50-100 ms and 100-200 ms: **away**. FluidServe-OFF sits 0.752 and 0.007 points from llm-d;
  FluidServe-ON sits 2.627 and 0.618 points from it, on the far side. The preference adds gaps
  exactly in the band where llm-d is strongest.
- 200-500 ms: **toward**. FluidServe-OFF is 0.666 points above llm-d, FluidServe-ON 0.152 points
  above it, so the preference closes 77 per cent of that gap without quite reaching llm-d.
- Above 500 ms: **through and past**. FluidServe-OFF ties llm-d, FluidServe-ON is 0.161 points
  better. This is the one band in which FluidServe-ON is the best of the three arms.
- Cumulatively above 200 ms: **toward, to a tie**. FluidServe-OFF is 0.680 points worse than
  llm-d, FluidServe-ON is level with it (−0.009 against a 0.022 spread).

### 2.2 Deep research

Requests: ON 14,737 / 14,626; OFF 14,070 / 14,114; LLMD 12,956 / 13,192.
Gaps: ON 14.24 M / 14.13 M; OFF 13.59 M / 13.61 M; LLMD 12.69 M / 12.89 M.

| band (ms) | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | OFF−LLMD | spread |
|---|---|---|---|---|---|---|---|---|---|---|
| 0-25 | 8.114 | 8.279 | 7.619 | 7.786 | 12.154 | 11.328 | **−3.544*** | 0.826 | **−4.038*** | 0.826 |
| 25-50 | 80.242 | 80.044 | 81.095 | 81.072 | 79.973 | 80.806 | −0.247 | 0.833 | +0.694 | 0.833 |
| 50-100 | 3.724 | 3.806 | 3.892 | 3.778 | 1.745 | 1.780 | **+2.002*** | 0.082 | **+2.072*** | 0.114 |
| 100-200 | 2.980 | 3.004 | 3.005 | 3.007 | 2.672 | 2.831 | **+0.240*** | 0.159 | **+0.254*** | 0.159 |
| 200-500 | 3.583 | 3.527 | 3.303 | 3.286 | 2.757 | 2.645 | **+0.854*** | 0.112 | **+0.593*** | 0.112 |
| >500 | 1.357 | 1.340 | 1.086 | 1.072 | 0.697 | 0.609 | **+0.695*** | 0.088 | **+0.425*** | 0.088 |

**llm-d is better than both FluidServe arms in every deep-research band above 50 ms**, and the
margin grows with the band: 2.0 points at 50-100 ms, 0.85 at 200-500, 0.70 above 500. Its pooled
mean gap is 43.630 / 42.669 ms against FluidServe-ON's 53.529 / 53.541.

**The preference moves FluidServe away from llm-d for deep research**, in the far tail as well as
the near one: the distance to llm-d in the 200-500 band grows from 0.593 to 0.854 points and above
500 ms from 0.425 to 0.695 points, both exceeding their spreads. This is the class the preference
costs, and Appendix B section B.5 recorded the same direction between FluidServe's own two arms.
Part of it is population, not policy: FluidServe-ON completes 14,737 / 14,626 deep-research
requests against FluidServe-OFF's 14,070 / 14,114, 4.6 per cent more, and llm-d's 12,956 / 13,192,
12.7 per cent more. That separation was not attempted here.

### 2.3 swe

Requests: ON 7,267 / 7,444; OFF 7,669 / 7,444; LLMD 6,387 / 6,336.
Gaps: ON 3.63 M / 3.76 M; OFF 3.85 M / 3.70 M; LLMD 3.13 M / 3.14 M.

| band (ms) | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | OFF−LLMD | spread |
|---|---|---|---|---|---|---|---|---|---|---|
| 0-25 | 12.642 | 11.455 | 7.892 | 8.236 | 15.741 | 12.422 | −2.033 | 3.319 | −6.017 | 3.319 |
| 25-50 | 76.442 | 77.416 | 80.938 | 80.790 | 76.271 | 79.428 | −0.921 | 3.157 | +3.014 | 3.157 |
| 50-100 | 3.873 | 4.030 | 4.115 | 3.926 | 1.874 | 1.847 | **+2.091*** | 0.157 | **+2.160*** | 0.189 |
| 100-200 | 3.428 | 3.530 | 3.439 | 3.392 | 2.562 | 2.894 | **+0.751*** | 0.332 | **+0.688*** | 0.332 |
| 200-500 | 3.145 | 3.167 | 3.146 | 3.141 | 2.824 | 2.783 | **+0.353*** | 0.040 | **+0.340*** | 0.041 |
| >500 | 0.470 | 0.402 | 0.470 | 0.515 | 0.728 | 0.626 | **−0.240*** | 0.102 | **−0.185*** | 0.102 |

**llm-d is better in the 50-500 ms bands and worse above 500 ms.** Its share of swe gaps above
500 ms is 0.677 per cent against both FluidServe arms' 0.436 and 0.493.

**The preference does not move FluidServe relative to llm-d for swe in any band above 50 ms.**
Every ON−OFF difference above 50 ms is inside its repeat spread: 50-100 ms −0.069 against 0.189,
100-200 ms +0.063 against 0.102, 200-500 ms +0.013 against 0.022, above 500 ms −0.056 against
0.068. The two fast buckets do move, but llm-d's own spread there is 3.32 and 3.16 points, so no
three-way statement is supported. This is the same conclusion Appendix B reached for swe between
FluidServe's own two arms, now confirmed to hold with llm-d present.

## 3. Question 2 — the rule table

Chat only. Admitted denominator: the requests the system accepted, run-boundary cutoffs excluded.
Chat budget is a 5 s first token and 50 ms per token. Whole hour, two repeats per arm.

Denominators, admitted chat requests: ON 57,277 / 57,528; OFF 57,205 / 57,738; LLMD 33,651 /
34,085. Of those, the ones carrying a gap record are 56,205 / 56,431, 56,131 / 56,654 and
33,031 / 33,472; a rule stated over a gap percentile scores a request without a gap record as a
violation, which is Appendix B's convention and is kept.

| rule | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | OFF−LLMD | spread |
|---|---|---|---|---|---|---|---|---|---|---|
| per-request mean inter-token latency ≤ 50 ms and TTFT ≤ 5 s | 96.699 | 97.290 | 94.284 | 93.964 | 89.682 | 90.210 | **+7.048*** | 0.592 | **+4.178*** | 0.527 |
| cumulative deadline t_i ≤ t_0 + 5 s + i × 50 ms | 98.046 | 97.881 | 96.172 | 95.831 | 95.534 | 96.101 | **+2.146*** | 0.567 | +0.184 | 0.567 |
| the request's own p90 gap ≤ 50 ms and TTFT ≤ 5 s | 26.550 | 24.602 | 36.329 | 38.405 | 60.946 | 53.419 | **−31.607*** | 7.527 | **−19.816*** | 7.527 |
| the request's own p99 gap ≤ 50 ms and TTFT ≤ 5 s | 2.820 | 2.694 | 3.958 | 3.859 | 5.590 | 5.510 | **−2.793*** | 0.125 | **−1.641*** | 0.099 |
| the request's own p95 gap ≤ 50 ms and TTFT ≤ 5 s | 10.985 | 10.212 | 13.436 | 13.208 | 23.693 | 16.931 | **−9.713*** | 6.762 | **−6.990*** | 6.762 |
| worst 10-token block mean ≤ 50 ms and TTFT ≤ 5 s | 11.460 | 11.589 | 8.482 | 8.158 | 14.377 | 11.791 | −1.559 | 2.586 | **−4.764*** | 2.586 |
| every gap ≤ 50 ms and TTFT ≤ 5 s | 2.392 | 2.293 | 3.239 | 3.154 | 3.159 | 2.940 | **−0.707*** | 0.219 | +0.147 | 0.219 |

The first four rows are the four the task named. The last three are carried over from Appendix B's
table so the new arm is scored on the same seven rules and not on a subset chosen after the fact.

**Under which rules does llm-d still beat FluidServe.** Under the four rules that compare a single
gap statistic against 50 ms: own p90 by 31.607 points, own p95 by 9.713, own p99 by 2.793, and
every-gap by 0.707, all against FluidServe-ON. It does not beat FluidServe under the per-request
mean rule, under the cumulative deadline rule, or under the 10-token block rule. FluidServe-ON is
ahead by 7.048 and 2.146 points under the first two and level with llm-d under the third
(−1.559 against a 2.586 spread).

**Does the preference narrow or widen the gap to llm-d.** Rule by rule, using the ON−LLMD and
OFF−LLMD columns above:

| rule | FluidServe without the preference | FluidServe with the preference | effect of the preference |
|---|---|---|---|
| per-request mean | 4.178 points ahead of llm-d | 7.048 points ahead | extends a lead by 2.870 points |
| cumulative deadline | level with llm-d (+0.184 inside a 0.567 spread) | 2.146 points ahead | turns a tie into a lead |
| own p90 ≤ 50 ms | 19.816 points behind | 31.607 points behind | deepens a deficit by 11.791 points |
| own p95 ≤ 50 ms | 6.990 points behind | 9.713 points behind | deepens a deficit by 2.723 points |
| own p99 ≤ 50 ms | 1.641 points behind | 2.793 points behind | deepens a deficit by 1.151 points |
| 10-token block mean | 4.764 points behind | level with llm-d (−1.559 inside a 2.586 spread) | closes a deficit to a tie |
| every gap ≤ 50 ms | level with llm-d (+0.147 inside a 0.219 spread) | 0.707 points behind | turns a tie into a deficit |

The preference moves FluidServe further from llm-d under every percentile-threshold rule and
further ahead of it under both averaging rules. The 10-token block rule is the exception in the
other direction: averaging over ten tokens absorbs the 50-200 ms gaps the preference adds while
still breaking on the 500 ms stalls it removes, so it is the only percentile-free rule where the
preference closes a deficit rather than extending a lead.

**The llm-d repeat spread is the largest of the three arms on exactly the rules where it wins.**
Its own-p90 attainment is 60.946 in repeat 1 and 53.419 in repeat 2, a spread of 7.53 points, and
its own-p95 spread is 6.76 points. FluidServe's spreads on the same two rows are 1.95 and 0.77
points. The 31.607-point difference is nine times llm-d's own spread, so the verdict holds, but
llm-d's level on these rules is known to about ±4 points and no finer.

### 3.1 The offered denominator, for context

Every chat arrival in the analysis window is in the denominator and a rejection is a violation.
Denominators: 64,246 / 64,238, 64,232 / 64,239, 64,251 / 64,265 non-cutoff chat arrivals.

| rule | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread |
|---|---|---|---|---|---|---|---|---|
| per-request mean ≤ 50 ms and TTFT ≤ 5 s | 86.209 | 87.128 | 83.969 | 84.455 | 46.970 | 47.846 | **+39.260*** | 0.918 |
| cumulative deadline | 87.411 | 87.657 | 85.650 | 86.133 | 50.035 | 50.970 | **+37.031*** | 0.935 |
| own p90 ≤ 50 ms and TTFT ≤ 5 s | 23.670 | 22.032 | 32.355 | 34.518 | 31.920 | 28.333 | **−7.275*** | 3.587 |
| own p99 ≤ 50 ms and TTFT ≤ 5 s | 2.514 | 2.413 | 3.525 | 3.468 | 2.928 | 2.922 | **−0.462*** | 0.101 |

On the offered denominator llm-d's own-p90 lead falls from 31.607 points to 7.275, because 47 per
cent of the chat requests it would have to score are ones it refused. This is not the load-matched
correction, which section 4 does; it is the same admitted numbers read against the other
denominator.

## 4. Question 3 — quantifying the confound

### 4.1 The two arms do not answer the same population

Arrivals in the analysis window, counted as `load_run` counts them:

| arm | arrivals | rejected | rejected % | errored, not rejected | run-boundary cutoffs | completed |
|---|---|---|---|---|---|---|
| ON r1 | 98,255 | 18,737 | 19.07 | 0 | 236 | 79,282 |
| ON r2 | 98,231 | 18,409 | 18.74 | 0 | 224 | 79,598 |
| OFF r1 | 98,240 | 19,078 | 19.42 | 0 | 218 | 78,944 |
| OFF r2 | 98,247 | 18,724 | 19.06 | 0 | 227 | 79,296 |
| LLMD r1 | 98,242 | 45,112 | 45.92 | 0 | 135 | 52,995 |
| LLMD r2 | 98,244 | 44,509 | 45.30 | 2 | 119 | 53,614 |

Per class, rejection percentage and completed count:

| class | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 |
|---|---|---|---|---|---|---|
| chat, rejected % | 10.838 | 10.438 | 10.931 | 10.111 | 47.596 | 46.935 |
| chat, completed | 57,277 | 57,528 | 57,205 | 57,738 | 33,651 | 34,085 |
| deep research, rejected % | 29.413 | 29.940 | 32.650 | 32.407 | 38.251 | 37.153 |
| deep research, completed | 14,738 | 14,626 | 14,070 | 14,114 | 12,957 | 13,193 |
| swe, rejected % | 43.290 | 41.908 | 40.176 | 41.914 | 50.140 | 50.550 |
| swe, completed | 7,267 | 7,444 | 7,669 | 7,444 | 6,387 | 6,336 |

**Output-length distribution of the admitted population.** llm-d does not keep shorter chat
requests; it keeps marginally longer ones, which works against the flattery hypothesis for this
one dimension. Completed chat, cutoffs removed so no length is a lower bound:

| arm | n | mean | q05 | q25 | q50 | q75 | q90 | q99 |
|---|---|---|---|---|---|---|---|---|
| ON r1 / r2 | 57,277 / 57,528 | 421.7 / 421.4 | 24 / 24 | 183 / 182 | 389 / 388 | 577 / 577 | 769 / 771 | 1476 / 1479 |
| OFF r1 / r2 | 57,205 / 57,738 | 421.7 / 421.1 | 24 / 24 | 183 / 183 | 388 / 388 | 577 / 577 | 771 / 771 | 1487 / 1470 |
| LLMD r1 / r2 | 33,651 / 34,085 | 425.1 / 428.7 | 26 / 26 | 188 / 192 | 393 / 396 | 580 / 583 | 774 / 779 | 1459 / 1495 |

The median admitted chat request is 388.5 tokens under FluidServe-ON and 394.5 under llm-d, a
difference of 6.0 tokens against a repeat spread of 3. Deep research is 971 against 980 and swe
488.5 against 481.5. Length selection is not where the confound lives.

**Arrival-rate profile of what each arm accepted.** This is where it lives. Admitted requests per
second, all classes, mean of two repeats, in 300 s roll-ups; the offered column is the trace and
is the same for every arm.

| window (s) | offered /s | ON admitted /s | OFF admitted /s | LLMD admitted /s | ON delivered tok/s | LLMD delivered tok/s | ON/LLMD admitted |
|---|---|---|---|---|---|---|---|
| 0-300 | 13.95 | 13.95 | 13.95 | 13.83 | 5,552 | 5,511 | 1.008 |
| 300-600 | 36.90 | 35.88 | 35.43 | 31.31 | 15,098 | 13,532 | 1.146 |
| 600-900 | 24.47 | 24.18 | 24.14 | 23.05 | 11,520 | 10,804 | 1.049 |
| 900-1200 | 18.93 | 15.86 | 15.71 | 10.93 | 8,909 | 6,403 | 1.451 |
| 1200-1500 | 34.22 | 21.13 | 20.93 | 10.81 | 12,197 | 7,502 | 1.955 |
| 1500-1800 | 21.89 | 17.16 | 16.99 | 10.62 | 10,694 | 7,357 | 1.615 |
| 1800-2100 | 21.09 | 20.70 | 20.55 | 17.88 | 10,831 | 9,318 | 1.158 |
| 2100-2400 | 35.46 | 27.90 | 29.00 | 15.44 | 14,212 | 9,369 | 1.806 |
| 2400-2700 | 23.53 | 22.15 | 22.17 | 16.62 | 11,985 | 9,134 | 1.332 |
| 2700-3000 | 23.18 | 20.18 | 20.07 | 12.79 | 10,685 | 7,919 | 1.578 |
| 3000-3300 | 43.21 | 26.36 | 25.83 | 9.22 | 14,571 | 7,327 | 2.858 |
| 3300-3600 | 33.51 | 23.08 | 22.62 | 9.03 | 13,673 | 7,439 | 2.555 |

Delivered tokens per second is each admitted request's chunk-gap count spread uniformly over its
own streaming interval, summed and divided by the window, so it credits the window in which the
tokens were actually produced rather than the one in which the request arrived.

Over the hour, llm-d's fleet delivers 8,581 / 8,723 output tokens per second against
FluidServe-ON's 11,962 / 11,992 and FluidServe-OFF's 11,831 / 11,848. Counting only the output of
requests that met their own class rule, llm-d produces 8,109 / 8,298 SLO-meeting tokens per second
against FluidServe-ON's 10,839 / 10,816. **llm-d's fleet is doing 27.8 per cent less work over the
hour and 24.2 per cent less useful work, and the shortfall is concentrated exactly in the busy
windows: at 3000-3300 s FluidServe-ON admits 2.86 times as many requests.**

### 4.2 The load-matched correction

Load-matched windows exist. Splitting the analysis window into 60 one-minute windows and
comparing the mean-over-repeats admitted request rate of FluidServe-ON against llm-d, **13 of the
60 windows have the two rates within 10 per cent of each other**, covering 21.7 per cent of the
hour. They fall in four contiguous spans: 60-360 s (5 windows), 660-960 s (5), 1980-2100 s (2) and
2700-2760 s (1). Matching on the admitted chat rate instead selects 12 windows and matching on
delivered token rate selects 14, so the choice of matching quantity does not change the picture.

Scoring the same rules on only the chat requests that arrived in those 13 windows. Admitted chat in
the matched subset: ON 12,290 / 12,317; OFF 12,297 / 12,313; LLMD 12,063 / 12,126 — the three arms
now answer populations within 2 per cent of each other.

| rule, load-matched | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | OFF−LLMD | spread |
|---|---|---|---|---|---|---|---|---|---|---|
| per-request mean ≤ 50 ms and TTFT ≤ 5 s | 99.333 | 99.424 | 98.309 | 98.571 | 94.554 | 94.087 | **+5.058*** | 0.467 | **+4.119*** | 0.467 |
| cumulative deadline | 97.917 | 97.913 | 97.357 | 97.425 | 97.289 | 96.083 | **+1.229*** | 1.206 | +0.705 | 1.206 |
| own p90 gap ≤ 50 ms and TTFT ≤ 5 s | 54.386 | 53.300 | 85.899 | 85.552 | 78.289 | 63.607 | **−17.105*** | 14.682 | **+14.777*** | 14.682 |
| own p99 gap ≤ 50 ms and TTFT ≤ 5 s | 3.987 | 4.035 | 8.140 | 7.715 | 11.730 | 10.770 | **−7.239*** | 0.960 | **−3.322*** | 0.960 |

For comparison, the same four rules over all 60 windows are +7.048, +2.146, −31.607 and −2.793.

**Does llm-d's advantage survive the correction? Partly, and one half of it inverts.**

- On the own-p90 rule llm-d still leads FluidServe-ON, but by 17.105 points instead of 31.607, so
  **46 per cent of the whole-hour advantage was load, not policy.** Both differences barely clear
  their spreads here: llm-d's own two repeats differ by 14.682 points on this rule in the matched
  windows (78.289 against 63.607), which is the largest repeat spread anywhere in this report.
  The verdict "llm-d leads on own p90" is retained, the magnitude is not trustworthy to better than
  about ±15 points on matched load, and it rests on two repeats.
- **Against FluidServe with the preference off the sign inverts.** Over the hour FluidServe-OFF is
  19.816 points behind llm-d on own p90; in the matched windows it is 14.777 points **ahead**
  (85.9 / 85.6 against 78.3 / 63.6). The difference exceeds its spread by 0.1 points, which is as
  marginal as a verdict gets, so the honest statement is that **at matched load FluidServe-OFF and
  llm-d are not separated on the own-p90 rule, whereas at unmatched load llm-d appeared 20 points
  better.** What llm-d beats on this rule is the class preference, not FluidServe.
- On the own-p99 rule llm-d's lead **grows** under the correction, from 2.793 to 7.239 points
  against a 0.960 spread. That one survives cleanly and is not a load artifact.
- On the two averaging rules FluidServe-ON's lead shrinks but survives: 5.058 points against a
  0.467 spread on the per-request mean, 1.229 against a 1.206 spread on the cumulative deadline,
  the latter now marginal.

The pooled bucket shares in the same matched windows show why. Chat gaps, total over total,
11,810 to 12,060 requests per run:

| band (ms) | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread |
|---|---|---|---|---|---|---|---|---|
| 0-25 | 49.713 | 48.200 | 55.988 | 54.907 | 50.613 | 32.342 | +7.479 | 18.272 |
| 25-50 | 40.651 | 42.368 | 38.043 | 39.071 | 43.226 | 59.640 | −9.923 | 16.414 |
| 50-100 | 5.984 | 5.908 | 2.646 | 2.598 | 2.901 | 3.675 | **+2.658*** | 0.774 |
| 100-200 | 3.147 | 3.124 | 1.892 | 1.972 | 1.957 | 2.822 | +0.746 | 0.864 |
| 200-500 | 0.457 | 0.372 | 1.220 | 1.234 | 1.105 | 1.308 | **−0.792*** | 0.203 |
| >500 | 0.047 | 0.028 | 0.211 | 0.217 | 0.198 | 0.214 | **−0.168*** | 0.018 |

At matched load FluidServe-ON has **one fifth** of llm-d's share of chat gaps above 500 ms
(0.038 against 0.206 per cent) and **one third** of its share in 200-500 ms, while carrying twice
llm-d's share in 50-100 ms. llm-d's two fastest buckets are again unreadable, with spreads of 18.3
and 16.4 points.

### 4.3 The tail against sustained token throughput

The load-matched windows are all at the quiet end of the trace, so they answer the confound but
not the question of what happens under pressure. Binning each arm by the delivered token
throughput **its own fleet** was sustaining in the window each request arrived in puts the three
arms at equal work rather than equal wall-clock time. Chat, admitted denominator.

| throughput (tok/s) | arm | admitted chat | per-request mean rule | own p90 ≤ 50 | own p99 ≤ 50 | % gaps >200 ms | % gaps >500 ms |
|---|---|---|---|---|---|---|---|
| 0-7,000 | ON r1 / r2 | 3,320 / 3,325 | 99.518 / 99.549 | 97.259 / 97.594 | 7.892 / 8.632 | 0.118 / 0.130 | 0.034 / 0.029 |
| | OFF r1 / r2 | 3,319 / 3,320 | 99.488 / 99.428 | 97.198 / 96.506 | 15.758 / 15.542 | 0.452 / 0.453 | 0.062 / 0.055 |
| | LLMD r1 / r2 | 5,067 / 4,232 | 94.750 / 92.226 | 92.303 / 79.135 | 17.644 / 17.439 | 1.471 / 1.514 | 0.292 / 0.322 |
| 7,000-10,000 | ON r1 / r2 | 6,721 / 7,509 | 98.334 / 98.349 | 49.606 / 41.324 | 3.764 / 2.850 | 1.478 / 1.542 | 0.206 / 0.179 |
| | OFF r1 / r2 | 7,160 / 7,126 | 95.964 / 95.257 | 71.229 / 71.625 | 5.922 / 5.417 | 2.397 / 2.468 | 0.422 / 0.426 |
| | LLMD r1 / r2 | 14,941 / 14,859 | 89.465 / 88.815 | 75.015 / 66.007 | 4.217 / 4.872 | 2.642 / 2.549 | 0.449 / 0.415 |
| 10,000-13,000 | ON r1 / r2 | 19,023 / 17,379 | 96.420 / 96.853 | 22.709 / 21.204 | 1.987 / 1.841 | 2.137 / 1.876 | 0.189 / 0.147 |
| | OFF r1 / r2 | 19,728 / 18,966 | 94.125 / 92.819 | 45.286 / 51.044 | 3.097 / 2.947 | 3.116 / 3.158 | 0.407 / 0.490 |
| | LLMD r1 / r2 | 4,574 / 6,034 | 91.342 / 93.404 | 65.829 / 56.977 | 4.526 / 4.359 | 1.894 / 2.228 | 0.333 / 0.294 |
| >13,000 | ON r1 / r2 | 28,213 / 29,315 | 96.165 / 97.022 | 15.326 / 14.054 | 2.559 / 2.487 | 2.689 / 2.780 | 0.204 / 0.188 |
| | OFF r1 / r2 | 26,998 / 28,326 | 93.314 / 93.765 | 13.045 / 14.774 | 2.615 / 2.708 | 3.097 / 3.168 | 0.310 / 0.297 |
| | LLMD r1 / r2 | 9,069 / 8,960 | 86.371 / 89.420 | 17.786 / 18.002 | 1.654 / 1.708 | 2.039 / 2.008 | 0.228 / 0.201 |

The window counts show the confound directly. llm-d spends 16 and 12 of its 60 windows below
7,000 tokens per second; FluidServe-ON spends 5. FluidServe-ON spends 20 and 21 windows above
13,000; llm-d spends 5 and 5. **llm-d's whole-hour tail is an average dominated by windows in
which its fleet was half-loaded, and the loading was its own admission decision, not the trace.**

At equal throughput the own-p90 comparison stops favouring llm-d in three of the four bins.
Below 7,000 tokens per second FluidServe-ON attains 97.259 / 97.594 against llm-d's 92.303 /
79.135; the difference of +11.7 points does not exceed llm-d's 13.2-point repeat spread, so the
correct verdict is no separation, not a FluidServe win, but it is certainly not the 31-point llm-d
lead of the pooled table. In the 7,000-10,000 and 10,000-13,000 bins llm-d leads by 25.0 and 39.4
points. Above 13,000 tokens per second the two converge to 14.690 against 17.894.

**Plainly: llm-d's advantage on the percentile-threshold rules does not survive the correction in
the form in which it was stated.** It survives as a narrower advantage over FluidServe with the
class preference on, and it does not survive at all as an advantage over FluidServe with the
preference off, which is level with llm-d at matched load. It survives cleanly only on the own-p99
rule, where matching makes it larger.

## 5. Question 4 — is there a band where FluidServe with the preference on wins?

Yes, and there are two ways of slicing to it. Both use the arrival time of the request, so the
windows are identical across runs.

### 5.1 By trace segment

Admitted chat, whole segment. Segment mixes are the chat share of arrivals from the plan file.

| segment | quantity | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| s0_m2, 60-960 s, chat 93.0% | per-request mean rule | 98.646 | 98.719 | 98.617 | 98.353 | 91.701 | 92.494 | +6.585 | 0.793 | FluidServe-ON |
| | own p90 ≤ 50 | 36.554 | 38.318 | 49.044 | 49.636 | 49.924 | 42.280 | −8.666 | 7.645 | llm-d |
| | % gaps >200 ms | 1.545 | 1.589 | 1.908 | 1.974 | 1.454 | 1.579 | +0.050 | 0.125 | no difference |
| | % gaps >500 ms | 0.112 | 0.106 | 0.144 | 0.164 | 0.173 | 0.184 | −0.070 | 0.011 | **FluidServe-ON** |
| s1_A, 960-1860 s, chat 33.3% | per-request mean rule | 93.860 | 92.731 | 90.301 | 89.744 | 84.164 | 76.436 | +12.996 | 7.728 | FluidServe-ON |
| | own p90 ≤ 50 | 28.372 | 20.750 | 29.038 | 29.865 | 80.595 | 66.726 | −49.100 | 13.869 | llm-d |
| | % gaps >200 ms | 3.598 | 3.608 | 3.882 | 3.898 | 3.702 | 3.838 | −0.167 | 0.136 | **FluidServe-ON** |
| | % gaps >500 ms | 0.337 | 0.337 | 0.462 | 0.507 | 0.849 | 0.723 | −0.449 | 0.125 | **FluidServe-ON** |
| s2_m1, 1860-2760 s, chat 76.9% | per-request mean rule | 98.729 | 98.652 | 92.474 | 91.736 | 89.125 | 90.367 | +8.944 | 1.242 | FluidServe-ON |
| | cumulative deadline | 98.248 | 98.267 | 93.858 | 93.380 | 97.284 | 97.428 | +0.902 | 0.144 | FluidServe-ON |
| | own p90 ≤ 50 | 22.569 | 18.582 | 36.959 | 42.702 | 71.954 | 62.924 | −46.863 | 9.030 | llm-d |
| | % gaps >200 ms | 1.690 | 1.737 | 3.083 | 3.201 | 2.790 | 2.706 | −1.034 | 0.084 | **FluidServe-ON** |
| | % gaps >500 ms | 0.156 | 0.131 | 0.475 | 0.507 | 0.433 | 0.368 | −0.257 | 0.065 | **FluidServe-ON** |
| s3_B, 2760-3660 s, chat 60.0% | per-request mean rule | 92.643 | 95.729 | 92.077 | 92.417 | 82.079 | 82.573 | +11.860 | 3.086 | FluidServe-ON |
| | own p90 ≤ 50 | 15.457 | 13.853 | 19.809 | 20.707 | 77.884 | 81.461 | −65.018 | 3.577 | llm-d |
| | % gaps >200 ms | 3.180 | 2.950 | 3.503 | 3.475 | 3.867 | 3.442 | −0.590 | 0.425 | **FluidServe-ON** |
| | % gaps >500 ms | 0.272 | 0.209 | 0.406 | 0.394 | 0.830 | 0.831 | −0.590 | 0.062 | **FluidServe-ON** |

Admitted chat per segment (ON r1/r2, OFF r1/r2, LLMD r1/r2): s0_m2 20,233/20,142, 20,035/20,032,
18,460/18,425; s1_A 6,873/6,617, 6,588/6,650, 2,690/1,689; s2_m1 16,837/16,909, 17,406/17,341,
10,308/11,274; s3_B 13,334/13,860, 13,176/13,715, 2,193/2,697.

**FluidServe-ON has a lower share of chat gaps above 500 ms than llm-d in all four segments**, by
0.070, 0.449, 0.257 and 0.590 points against spreads of 0.011, 0.125, 0.065 and 0.062. It also
has a lower share above 200 ms in three of the four. The own-p90 rule goes to llm-d in all four,
and by the largest margins in the two segments where llm-d admitted the least chat.

⚠ **In s1_A and s3_B llm-d's admitted chat count differs between repeats by a factor of 1.59 and
1.23** (2,690 against 1,689; 2,193 against 2,697). Those two segments are the ones where llm-d's
per-segment numbers should be treated as indicative only. The FluidServe arms differ by 3.9 and
3.9 per cent on the same segments.

### 5.2 By offered arrival rate

The trace's offered rate over 60 s windows ranges from 10.6 to 45.0 req/s. Binning the same
windows by offered rate, which is a property of the trace and therefore identical in all six runs:

| offered rate | quantity | ON r1 | ON r2 | OFF r1 | OFF r2 | LLMD r1 | LLMD r2 | ON−LLMD | spread | verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| 10.6-15 req/s, 7 windows | per-request mean rule | 99.054 | 99.159 | 99.310 | 99.005 | 97.711 | 94.766 | +2.869 | 2.945 | no difference |
| | own p90 ≤ 50 | 90.363 | 89.325 | 91.002 | 89.847 | 95.902 | 79.172 | +2.307 | 16.729 | no difference |
| | own p99 ≤ 50 | 7.285 | 7.592 | 13.880 | 13.699 | 23.940 | 19.348 | −14.205 | 4.592 | llm-d |
| | % gaps >500 ms | 0.130 | 0.071 | 0.124 | 0.123 | 0.127 | 0.235 | −0.081 | 0.108 | no difference |
| 15-22 req/s, 12 windows | per-request mean rule | 98.680 | 98.780 | 97.149 | 96.994 | 95.910 | 95.072 | +3.239 | 0.838 | FluidServe-ON |
| | own p90 ≤ 50 | 48.365 | 44.856 | 82.312 | 82.180 | 83.224 | 74.284 | −32.143 | 8.940 | llm-d |
| | % gaps >200 ms | 1.143 | 1.070 | 2.238 | 2.248 | 1.666 | 1.685 | −0.569 | 0.074 | **FluidServe-ON** |
| | % gaps >500 ms | 0.138 | 0.155 | 0.388 | 0.364 | 0.260 | 0.210 | −0.088 | 0.050 | **FluidServe-ON** |
| 22-30 req/s, 17 windows | per-request mean rule | 97.678 | 98.032 | 93.958 | 92.383 | 87.823 | 89.764 | +9.061 | 1.941 | FluidServe-ON |
| | own p90 ≤ 50 | 22.002 | 19.911 | 42.330 | 48.009 | 68.513 | 55.290 | −40.945 | 13.222 | llm-d |
| | % gaps >200 ms | 1.976 | 1.832 | 3.098 | 3.199 | 2.618 | 2.576 | −0.693 | 0.144 | **FluidServe-ON** |
| | % gaps >500 ms | 0.186 | 0.142 | 0.449 | 0.543 | 0.489 | 0.397 | −0.279 | 0.092 | **FluidServe-ON** |
| **30-45 req/s, 24 windows** | per-request mean rule | 95.308 | 96.229 | 92.965 | 93.277 | 85.633 | 86.817 | +9.543 | 1.183 | FluidServe-ON |
| | cumulative deadline | 97.966 | 97.648 | 95.899 | 95.579 | 94.896 | 96.943 | +1.887 | 2.047 | no difference |
| | own p90 ≤ 50 | 14.249 | 12.777 | 12.659 | 14.324 | 34.155 | 34.458 | −20.793 | 1.473 | llm-d |
| | **own p99 ≤ 50** | 2.468 | 2.419 | 2.459 | 2.614 | 1.958 | 1.984 | **+0.473** | 0.049 | **FluidServe-ON** |
| | % gaps >200 ms | 2.874 | 2.915 | 3.176 | 3.228 | 2.578 | 2.507 | +0.352 | 0.071 | llm-d |
| | **% gaps >500 ms** | 0.212 | 0.192 | 0.302 | 0.299 | 0.353 | 0.356 | **−0.152** | 0.020 | **FluidServe-ON** |
| | median own p90 gap (ms) | 66.891 | 67.069 | 62.564 | 62.207 | 65.900 | 65.159 | +1.450 | 0.741 | llm-d, by 1.45 ms |

Admitted chat per band (ON r1/r2, LLMD r1/r2): 10.6-15 req/s 3,912/3,925 and 3,538/3,649;
15-22 req/s 8,411/8,360 and 6,992/6,980; 22-30 req/s 15,458/15,650 and 9,896/9,848;
30-45 req/s 29,496/29,593 and 13,225/13,608.

**The band where FluidServe with the preference on wins on the within-request tail is the busiest
one, 30 to 45 offered req/s over 24 of the 60 windows.** Three things happen there at once.

1. FluidServe-ON beats llm-d on a percentile-threshold rule for the first time: the request's own
   p99 gap within the 50 ms budget, 2.444 against 1.971 per cent, a difference of 0.473 points
   against a 0.049 spread. This is the rule family on which the project was said to lose
   everywhere.
2. FluidServe-ON has 0.202 per cent of chat gaps above 500 ms against llm-d's 0.355, a difference
   of 0.152 points against a 0.020 spread. FluidServe-OFF is at 0.301, between the two.
3. The median chat request's own p90 gap converges: 66.98 ms for FluidServe-ON against 65.53 ms
   for llm-d, a difference of 1.45 ms against a 0.741 ms spread. Over the whole hour the same
   comparison is 64.90 against 41.02 ms, a gap of 23.9 ms. **llm-d's within-request smoothness
   advantage is a property of the load it chose to carry, and it disappears when the offered rate
   is high enough that llm-d cannot reject its way out of it** — at 30-45 req/s llm-d admits
   13,417 chat requests against FluidServe-ON's 29,545, so it is holding its p90 by serving 55 per
   cent fewer of them.

The own-p90 rule still goes to llm-d in that band, by 20.793 points, and this is the one verdict
that holds in every slice of this report. The share of gaps above 200 ms also goes to llm-d there
(+0.352 against a 0.071 spread), the only band where it does.

**The most useful single output of this task**, stated with its denominators: in the 24 one-minute
windows where the trace offers 30 to 45 req/s, over two repeats per arm, on the admitted chat
denominator of 29,496 / 29,593 requests for FluidServe-ON and 13,225 / 13,608 for llm-d,
FluidServe with the class preference on has a strictly better far tail than llm-d — 0.473 points
more chat requests whose own p99 gap fits the 50 ms budget and 0.152 points fewer gaps above
500 ms — while admitting 2.2 times as many chat requests (27.63 against 15.68 admitted req/s,
mean of two repeats) and delivering 1.5 times the output tokens per second (14,358 against
9,355 tokens/s).

## 6. What was not verified

- **Two repeats per arm.** Every "exceeds" verdict compares a difference against a spread
  estimated from two points. This is weakest for llm-d, whose repeat spread reaches 7.53 points on
  the whole-hour own-p90 rule, 14.68 points on the same rule in the load-matched windows, and 18.3
  points on the 0-25 ms gap bucket there. Three llm-d cells in this report clear their spread by
  less than 10 per cent of the spread and should be read as unresolved rather than as results: the
  matched-window own-p90 comparisons against both FluidServe arms, and the 30-45 req/s cumulative
  deadline row.
- **Why llm-d's repeats disagree so much was not investigated.** Its admitted chat count differs
  between repeats by a factor of 1.59 in segment s1_A. The llm-d endpoint-picker trains a latency
  predictor online and that predictor starts from scratch in each condition, which is a known risk
  recorded in `ms_dev/notes/llmd-baseline.md`; whether it explains this instability was not tested,
  and `training_server.log` and `epp_metrics.txt` were not opened.
- **No per-engine attribution for any arm.** Nothing here connects a request's gaps to the
  instance, the running batch size, or the prefill chunks it shared a decode step with.
  `exp41_engine_view.attribute_engines` was not run and the mechanism behind every difference in
  this report is unexplained.
- **The load matching is on admitted request rate, not on fleet state.** Two arms admitting the
  same number of requests per second can still hold different amounts of KV, because they admitted
  a different class mix earlier. Per-instance KV occupancy and running batch size from
  `server_metrics/engine_800{0,1,2,3}.jsonl` were not compared across arms, so "matched load" here
  means matched arrival pressure, not matched engine state.
- **The 13 matched windows are all at the quiet end of the trace.** Their mean offered rate is
  below the hour's, so the matched-window table answers the confound but describes a lighter
  system than the hour does. Section 4.3 is the partial remedy and it bins each arm by its own
  throughput, which is a weaker control than matching windows.
- **Deep research and swe were scored only on the bucket distribution**, not under the rule
  families. Deep research's 100 ms per-token budget and swe's 30 s end-to-end rule were not
  applied to any of the three arms, so section 2.2 and 2.3 say where the distributions sit and not
  who attains more.
- **The deep-research population difference was not separated from the policy effect.**
  FluidServe-ON completes 12.7 per cent more deep research than llm-d and 4.6 per cent more than
  FluidServe-OFF, and part of its worse deep-research tail is that extra admitted load.
- **Client-side buffering was not ruled out.** Chunks per token is within 0.0003 across all three
  arms, which rules out a difference in how many chunks arrive, but not a difference in when a
  fixed number of chunks is released by the Envoy path against the Llumnix gateway path.
- **Segment boundaries** were taken from the plan file and applied to each run's own first
  arrival; wall-clock drift between runs was not checked.
- **The cumulative-deadline rule indexes tokens by chunk index**, which is 0.9932 to 0.9935 of the
  token index, so the deadline line is about 0.7 per cent steeper than a strict per-token line in
  every arm. The bias is the same in all three and cannot produce a difference between them.
- **No figure was produced.** Every number here is from a table; nothing was cross-checked against
  a plot, which is the check that caught two errors in the earlier passes of this report series.

## 7. Reproduce

```bash
cd /home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
# the two llm-d event logs, through Appendix B's own extractors (about 25 s per 3.8 GB file)
python3 /home/nxclab/tools/a3_pass2/extract_buckets.py \
  results/<llmd-run>/tbt_events.jsonl /home/nxclab/tools/a6_llmd/<llmd-run>.csv
python3 /home/nxclab/tools/a3_pass2/extract_block.py \
  results/<llmd-run>/tbt_events.jsonl /home/nxclab/tools/a6_llmd/blk_<llmd-run>.csv

python3 /home/nxclab/tools/a6_llmd/an6.py      # merge all six runs with metrics.csv -> D6.pkl
python3 /home/nxclab/tools/a6_llmd/v0.py       # section 1 validity gate
python3 /home/nxclab/tools/a6_llmd/pop.py      # section 4.1 populations
python3 /home/nxclab/tools/a6_llmd/q1_6.py     # section 2 bucketed tables, three classes
python3 /home/nxclab/tools/a6_llmd/q2_6.py     # section 3 rule table
python3 /home/nxclab/tools/a6_llmd/q3_load.py  # section 4.2 load-matched window selection
python3 /home/nxclab/tools/a6_llmd/q3_match.py # section 4.2 matched-window scoring
python3 /home/nxclab/tools/a6_llmd/q3_tput.py  # section 4.3 throughput bins
python3 /home/nxclab/tools/a6_llmd/q4_bands.py # section 5 segment and rate bands
```

The four FluidServe runs reuse the per-request gap CSVs Appendix B already wrote in
`/home/nxclab/tools/a3_pass2/`; they were not regenerated, so the FluidServe columns in this
report are the same bytes as the ones in Appendix B and the two reports cannot disagree by
extraction.
