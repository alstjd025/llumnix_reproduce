# EXP-41 — the two control planes on an hour of moving load

Started 2026-07-31 12:12 KST. `full` finished 14:31, `azcode` running, expected
16:45 KST. Written while running.

## 1. Why, and why two traces

Every comparison up to EXP-40 held the arrival rate fixed for eight minutes at a
time. A rate that never moves cannot show an adaptation cost, and the whole
claim about admission is about what happens when the load changes. Two traces,
deliberately different in what each can answer:

| variant | trace | what it can show | what it cannot |
|---|---|---|---|
| `full` | `dyn60_short_m123` | four peaks, so the load crosses the knee four times **and comes back down four times** — recovery is visible | rate and mix move together, so a segment difference cannot be attributed to one of them |
| `azcode` | `azcode_w60_m1` | a **real** 60-minute window replayed verbatim, no compression or rescaling, **mix fixed at m1** — so only the rate moves | one crossing, no return; recovery is not visible |

Their shapes are in `results/aggregate_analysis/trace_shapes.png`. `full` is
Azure conv+code minute counts, four days compressed 96x and rank-mapped to
25–75 req/s, mix stepping m1→m2→m3→m1 every 15 minutes, mean 50.1 req/s.
`azcode` is minute 5,087 of the Azure code 2024 trace (2024-05-13 12:47 UTC),
125,864 arrivals, mean 35.0 req/s, per-minute rate rising from the mid-20s to
the high 40s.

Both arms are **stock FIFO**; the engine scheduler is not a variable here.

## 2. What had to be built

`mix_dyn60_short_m123.json` declares the agent class as `tbt_ms 25`, which the
Llumnix SLO filter reads literally and which made it reject 98% of that class in
EXP-28. The static sweep solves this with `m1f`; the dynamic configs had no
equivalent. Added, for both traces:

- `workload_configs/mix_dyn60_short_m123_slofair.json`
- `workload_configs/mix_azcode_w60_m1.json` (verbatim trace, mix fixed m1)
- `workload_configs/mix_azcode_w60_m1_slofair.json`

Agent class restated as (2500, 52), exactly as `mix_short_m1_slofair.json` does.
Scoring is unaffected: the analysis judges that class end to end whatever the
config says. `run_exp30_dynamic.sh` now picks the fair config **by arm**, the
same way `run_exp27_mixsweep.sh` picks m1 or m1f, and gained the `azcode`
variant, an `ARMS` override and an overridable session prefix.

**The pre-flight check earned its keep again.** The first launch aborted with
`engine has SCHED_EXTRA_ARGS='--scheduling-policy priority --scheduler-cls
deadline_sched.DeadlineScheduler'` — EXP-40 had left the engine on QoServe. Run
as it was, three conditions labelled FIFO would have been QoServe. The engine
was reset and **the reset is now part of the driver** rather than something
remembered between runs.

## 3. Result — `full`, one run each, both healthy

| | offered | admitted | rej% | goodput | total tok/s | chat / dr / agent |
|---|---|---|---|---|---|---|
| Llumnix SLO | 38.5 | 66.7 | 42.1 | 11,384 | 16,143 | 26.7 / **100.0** / 58.5 |
| **FluidServe** | **59.7** | **84.3** | **29.1** | **15,643** | **18,383** | **59.3** / 83.7 / 35.1 |

**+21.2 points offered, 37% more token goodput, 13 points less rejected, and 14%
more total throughput.**

By 15-minute segment — the advantage holds through every mix:

| segment | mix | rate | SLO | FluidServe | diff |
|---|---|---|---|---|---|
| 0–15 | m1 | 43.3 | 43.8 | **71.2** | **+27.4** |
| 15–30 | m2 | 47.2 | 67.5 | **89.9** | +22.3 |
| 30–45 | m3 | 49.1 | 29.3 | **52.8** | +23.5 |
| 45–60 | m1 | 58.3 | 19.2 | **32.1** | +12.9 |

By rate band, pooling the hour by each request's 30-second arrival rate:

| band | n | SLO | FluidServe | diff |
|---|---|---|---|---|
| 0–40 req/s | 35,439 | 75.2 | 93.2 | +18.0 |
| **40–50** | 30,694 | 49.4 | **75.9** | **+26.5** |
| **50–60** | 46,518 | 31.7 | **60.1** | **+28.3** |
| 60+ | 66,767 | 19.0 | 34.4 | +15.4 |

**The same shape the static sweep found** — largest in the 40–60 band, smaller
at both ends — reproduced under load that never sits still. EXP-38 measured
+35.6 at a static 45 req/s and +8.3 at 60; here the 40–50 band reads +26.5 and
60+ reads +15.4.

### The timeline, and one thing only a timeline shows

`results/aggregate_analysis/exp41/exp41_full_timeline.png`, six panels on one
time axis, 90-second sliding windows anchored on arrival.

**Llumnix SLO stops recovering.** Through the first half both policies fall at
each peak and climb back between them. After minute 45 FluidServe returns to
about 60% in the troughs and **the SLO arm stays pinned near 20%** even when the
offered rate drops. Its KV occupancy (panel F) climbs over the same stretch
while its decode batch does not, which is the candidate explanation and is not
yet established — separating it needs the transition-only scoring in
`exp30_dynamic.py`.

**It rejects more and scores less.** At the peaks the SLO arm rejects up to 80%
against FluidServe's 50%, and its attainment is lower anyway.

**Deep research sits at 100% for both policies for the whole hour** (panel E)
while chat and the agent class collapse at every peak. That is §34.6's finding
— the class using half its budget is making the batch heavy for the class on its
budget line — holding for sixty minutes of moving load rather than for one
eight-minute condition.

## 4. Caveats

- **One run per condition.** The differences, 12.9 to 28.3 points, are well
  above the repeat spread measured elsewhere (1.2–4.2 points, up to 11.4 at the
  knee), but they are not repeats.
- **`full` moves rate and mix together.** Both arms see the identical trace, so
  the arm difference is valid; "why is the 15–30 segment better" is not
  answerable from this run. The flat-rate ablation
  (`dyn09_short_mixcycle_flat`, rate fixed at 40, mix cycling only) is what
  separates them, and `azcode` avoids the confound entirely by holding the mix.
- Recovery is described from the timeline, not yet measured. The per-segment and
  transition-only breakdown is the measurement.

## 5. Result — `azcode`, one run each, both healthy

Finished 17:05 KST. `run_health.py`: both arms delivered 34.8 req/s against the
trace's 35.0 mean, 4/4 engines, no flags. The mix is fixed at m1 and the window
is replayed verbatim, so **the only thing that moves is the rate**, which climbs
from the mid-20s to the low 50s over the hour.

| | offered | admitted | rej% | goodput | total tok/s | chat / dr / swe (offered) |
|---|---|---|---|---|---|---|
| Llumnix SLO | 55.7 | 67.4 | 17.3 | 11,151 | 15,387 | 45.3 / **100.0** / 72.9 |
| **FluidServe** | **82.1** | **91.5** | **10.2** | **14,525** | **16,396** | **85.4** / 72.1 / 69.5 |

**+26.4 points offered, +24.1 admitted, 30% more token goodput, 7 points less
rejected.** The same direction and roughly the same size as `full` (+21.2), on a
trace where the mix cannot be the explanation.

By 15-minute segment. The load ramps monotonically, so the segments are a
load ladder rather than four repetitions:

| segment | mean rate | SLO | FluidServe | diff |
|---|---|---|---|---|
| 0–15 | 27.5 | 99.9 | 100.0 | +0.1 |
| 15–30 | 32.6 | 98.0 | 100.0 | +2.0 |
| 30–45 | 38.5 | 41.9 | **84.6** | **+42.7** |
| 45–60 | 45.9 | 21.1 | **61.2** | **+40.1** |

By 30-second offered-rate band, pooled over the hour:

| band | n | SLO | FluidServe | diff |
|---|---|---|---|---|
| under 30 req/s | 39,530 | 98.0 | 99.4 | +1.5 |
| 30–35 | 17,557 | 86.5 | 97.3 | +10.8 |
| **35–40** | 20,642 | **31.4** | **83.0** | **+51.7** |
| 40–50 | 42,777 | 21.4 | 63.5 | +42.1 |
| 50+ | 6,339 | 17.7 | 54.7 | +36.9 |

**Every band favours FluidServe, and nowhere in this hour do the two cross.**
That is the answer to the question `azcode` was run for: the `full` crossing at
minutes 50–56 is not something the rate alone produces at any rate this trace
reaches. It is **not** a refutation either — `azcode` peaks at about 55 req/s in
a 30-second bin, which is 1.25x the measured SLO capacity, while the `full`
crossing needed 68–77 req/s held for six minutes, about 1.6x. The two traces
address different parts of the load range.

**This brackets the Llumnix SLO knee at between 35 and 40 req/s**: 86.5 in the
30–35 band, 31.4 in the 35–40 band. FluidServe still reads 83.0 at 35–40 and
does not fall below 55 anywhere. Under a ramping trace rather than static
conditions, so it is not the same measurement as the planned static knee runs,
but it bounds them.

### 5.1 What the separation bought and what it cost

`azcode` produces a much cleaner class partition than `full` did, and with the
mix held fixed the trade is legible.

| arm | port | requests | attainment (admitted) | chat% | dr% | swe% | chat ITL |
|---|---|---|---|---|---|---|---|
| SLO | 8000 | 21,908 | 80.7 | 64.7 | 22.5 | 12.8 | 38.7 |
| SLO | 8001 | 25,443 | 69.9 | 70.8 | 18.8 | 10.3 | 42.2 |
| SLO | 8002 | 29,073 | 60.4 | 76.3 | 16.6 | 7.1 | 49.3 |
| SLO | 8003 | 28,480 | 62.2 | 74.7 | 17.4 | 7.9 | 48.0 |
| FS | 8000 | 14,898 | 86.1 | 42.9 | 15.4 | **41.8** | 48.7 |
| FS | 8001 | 44,731 | **98.2** | **97.7** | 1.5 | 0.8 | **32.6** |
| FS | 8002 | 39,548 | **95.1** | **94.8** | 3.7 | 1.5 | **26.6** |
| FS | **8003** | 14,609 | **66.4** | **0.0** | **99.4** | 0.6 | — |

Two engines carrying 95–98% chat, one carrying 99.4% deep research and no chat
at all, one carrying the swe class. **The gain is chat**: 45.3 → 85.4 offered,
and among admitted requests the TBT failure rate falls from 41.3% (31,224 of
75,585) to 4.9% (4,273 of 87,357), because chat's inter-token latency on its own
engines is 26.6–32.6 ms against a 50 ms budget rather than 44.3 ms fleet-wide.
Chat is 76.9% of arrivals, so this is most of the +26.4.

**The cost is the deep research engine, and it is the same failure as 6.2, worse.**

| | preemptions | imbalance (busiest/least) | peak queue | KV max |
|---|---|---|---|---|
| Llumnix SLO | 0 | 1.6x | 40 | 91–95% |
| FluidServe | **5,513**, all on 8003 | **7.3x** | **128** | **100% on 8003** |

Deep research falls 100.0 → 72.1 offered, and unlike in `full` the loss is not
mainly shedding: 535 of 19,488 are shed (2.7%) and **4,807 of the 18,643 admitted
miss on TTFT**, with p90 at **20.64 s** against a 10 s budget while only 13 miss
on TBT. A TTFT of 20 s is past the hold deadline, so this is not the gateway
holding the request — it is queueing behind an engine whose KV is full and which
is preempting, exactly the state 6.2.1 describes. The Llumnix SLO arm's deep
research sits at 100.0 with zero rejections all hour.

Arithmetically, bringing deep research back to the SLO arm's 100.0 without giving
up any of the chat gain would move FluidServe from 82.1 to about **86.4**. That
is the size of the prize for fixing 6.2.1, on this trace.

## 6. Two failure modes an eight-minute condition cannot produce

Both were found on `full` and both reappear on `azcode`; 6.2 in particular is
larger there (5,513 preemptions against 1,471, imbalance 7.3x against 2.70x).

Both were found by asking a question about the timeline rather than the whole-run
mean, and neither appears anywhere in EXP-38 or EXP-40. They are at opposite
ends of the hour and have nothing to do with each other.

### 6.1 Minutes 50–56: the hold turns into a loss, and the arm difference goes negative

This is the only stretch of the hour in which Llumnix SLO scores higher than
FluidServe.

| min | rate (req/s) | SLO | FluidServe | diff |
|---|---|---|---|---|
| 50 | 68.3 | 17.1 | 15.0 | −2.2 |
| 51 | 72.7 | 17.3 | 14.8 | −2.5 |
| 52 | 73.0 | 17.3 | 13.7 | **−3.6** |
| 53 | 74.5 | 17.5 | 14.7 | −3.1 |
| 54 | 73.0 | 17.3 | 14.2 | −3.1 |
| 55 | 69.6 | 17.1 | 15.2 | −1.9 |
| 56 | 58.8 | 17.9 | 17.6 | −0.4 |
| 57 | 56.6 | 18.3 | **31.5** | +13.2 |

**It is the only sustained extreme overload in the trace.** Counting stretches
whose 30-second rate stays above 65 req/s: minute 6.5 for 1.0 min, minute 8.0
for 2.5 min, minute 21.0 for 1.0 min, minute 36.0 for 1.0 min, and **minute 50.0
for 6.0 min** (peak 77.1 req/s at 53.5). Measured SLO capacity is about 44
req/s, so this is 1.6–1.7x capacity held for six minutes. The difference returns
to +13.2 in the first minute the rate drops below 60.

Decomposed over minutes 50–57 by what each class contributes to the offered
score (attainment x that class's share of arrivals):

| class | share | SLO att | FS att | SLO contrib | FS contrib | diff |
|---|---|---|---|---|---|---|
| chat | 76.9% | 0.0 | 1.5 | 0.0 | 1.2 | +1.2 |
| deepresearch | 15.4% | 100.0 | 80.2 | 15.4 | 12.3 | **−3.1** |
| swe | 7.7% | 25.7 | 18.5 | 2.0 | 1.4 | −0.6 |

**The Llumnix SLO arm's 17.4 is arithmetic, not adaptation.** It rejects
**100%** of chat (22,604 of 22,604) and **0%** of deep research, so it collects
that class's 15.4 points in full and nothing else. Because the offered
denominator counts a rejection as a violation, refusing a class costs exactly
what that class could have contributed — which at 1.6x capacity is nothing that
either policy can collect anyway.

**Deep research is not failing on latency for FluidServe; it is being shed by
FluidServe.** Of 4,517 arrivals it sheds 829 (18.4%), and of the 3,688 it admits
only 64 (1.7%) miss a rule. The whole 3.1-point loss is its own admission
decision. The mechanism is the hold:

| | dr TTFT p50 | dr TTFT p90 | budget |
|---|---|---|---|
| Llumnix SLO | 0.45 s | 0.97 s | 10 s |
| FluidServe | **8.91 s** | **9.59 s** | 10 s |

`canWait` sets the hold deadline at `ttftSlo − prefillMs − placementDelayBound −
recheckMs`, so FluidServe spends about nine of deep research's ten TTFT seconds
waiting for an instance that can meet the deadline. At 1.6x capacity sustained
for six minutes that instance never appears, so 18.4% reach the deadline with no
feasible target and are shed, and the rest are dispatched with under a second of
margin left. **This is the same hold that produces the advantage at 40–60 req/s**
and that section 35 credited for the result being insensitive to the engine
scheduler; past the capacity point it converts a class that would have passed on
immediate dispatch into an 18.4% loss.

Where the capacity went: FluidServe admits 3,965 chat requests (17.5% of chat
arrivals) of which **91.2% fail, 3,546 of them on TBT** at an ITL p50 of 55.1 ms
against a 50 ms budget. Those requests produce output that scores nothing.

| | total output | goodput | share that scored |
|---|---|---|---|
| Llumnix SLO | 12,115 tok/s | **11,132** | 92% |
| FluidServe | **13,747** | 9,035 | 66% |

The engine is not the constraint and FluidServe is not producing a slower fleet:
its decode batch is 797.6 against 772.5, its mean KV is 68.7% against 78.1%, and
its ITL is 55 ms against 60 ms. It is spending a faster fleet on a class that
cannot meet 50 ms at this load.

**Confirmed from the scheduler's own gauges.** The per-instance FluidServe
gauges are in `server_metrics/scheduler.jsonl` for this run, so the section 33
explanation could be checked directly rather than inferred. Over minutes 50–56:

| engine | `gate_allowance_ms` | `tightest_allowance_ms` | predicted step | observed step | headroom |
|---|---|---|---|---|---|
| 8000 | **50.0** | 72.7 | 56.4 | 56.5 | −697k |
| 8001 | **50.0** | 68.6 | 55.7 | 55.7 | −659k |
| 8002 | **50.0** | 69.1 | 55.7 | 55.8 | −666k |
| 8003 | **50.0** | 68.3 | 55.6 | 55.7 | −653k |

An instance's gate is the minimum `nominalMs` over the requests live on it, and
by this point in the hour every instance holds chat, so every gate is chat's 50
ms. The routing test is `meanAfter <= min(gateAllowance, req.nominalMs) * 0.90`,
which for a deep research request is `55.6 <= 45.0` — false on all four
instances even though the request's own budget is 100 ms and the fleet is
running at 55.6. **Nothing can ROUTE, so every arrival falls to PEND, and deep
research spends nine seconds of its ten-second budget there before being shed.**
The prediction is also not the error: predicted and observed step agree to
within 0.1 ms on all four.

Two fixes follow without needing class priorities, which remain future work:
judge the gate against **the arriving request's own budget** rather than the
instance minimum, so a class with large slack is not held behind a class with
none; and **shorten the hold deadline when recent holds have been ending in
sheds**, so the nine seconds are not spent before the request is discarded
anyway.

Two fixes follow without needing class priorities, which remain future work:
judge the gate against **the arriving request's own budget** rather than the
instance minimum, so a class with large slack is not held behind a class with
none; and **shorten the hold deadline when recent holds have been ending in
sheds**, so the nine seconds are not spent before the request is discarded
anyway.

Note what 6.2 below adds to this: during minutes 5–20 engine 8003's gate **was**
100 ms, because the class packing had given it only deep research to hold. The
policy can produce the state that 6.1 says is missing. What it cannot do is hold
that state at 1.6x capacity, because packing only happens on the ROUTE path and
at that load nothing routes.

### 6.2 Minutes 6–16: FluidServe overloads one engine and pays 1,471 recomputes

`exp41_engine_view.py`, two figures in `results/aggregate_analysis/exp41/`.
EXP-38 and EXP-40 recorded **zero preemptions in every condition**; an hour of
moving load is where they appear, and they appear on one engine of one arm.

| arm | port | batch | KV mean | KV max | queue | preemptions | prefix hit |
|---|---|---|---|---|---|---|---|
| SLO | 8000 | 196 | 54.2 | 92 | 0.4 | 0 | 78.8% |
| SLO | 8001 | 191 | 54.7 | 95 | 0.4 | 0 | 78.4% |
| SLO | 8002 | 203 | 54.9 | 92 | 0.4 | 0 | 78.3% |
| SLO | 8003 | 199 | 54.8 | 95 | 0.4 | 0 | 78.7% |
| FS | 8000 | 183 | 41.4 | 82 | 0.4 | 0 | 79.8% |
| FS | 8001 | 177 | 46.1 | 83 | 0.4 | 0 | 79.3% |
| FS | 8002 | 231 | 35.7 | 87 | 0.6 | 0 | 81.1% |
| **FS** | **8003** | **220** | **55.9** | **100** | **2.6** | **1,471** | 80.0% |

Busiest-over-least-busy decode batch, averaged per window: **1.25x for the SLO
arm, 2.70x for FluidServe.** The preemptions are confined to minutes 7–15 (149 /
158 / 174 / 177 / 113 / 417 / — / 254 / 29), during which engine 8003's batch
reaches 540, its KV touches 100%, and its engine queue reaches 27 while the
other three stay at 0. vLLM V1 preempts by recompute, so each one discards a
completed prefill and pays for it again, and no request-level metric attributes
that cost to anything.

Minutes 7–15 are the first peak, and they are where FluidServe's per-minute
advantage is at its most erratic in the whole first half: +11.6 at minute 7 and
**+1.4 at minute 9**, against +34 to +61 in the surrounding minutes. The two
observations are consistent in time; attributing one to the other needs the
repeat, since a single run cannot separate them from the peak itself.

**The dispatch side** (`analysis/request_engine.csv`, every admitted request
matched to the engine the scheduler dispatched it to, 100% of 103,801 and
127,266 respectively):

| arm | port | requests | attainment (admitted) | chat% | dr% | swe% | chat ITL |
|---|---|---|---|---|---|---|---|
| SLO | 8000 | 25,867 | 65.7 | 63.6 | 20.6 | 15.7 | 47.6 |
| SLO | 8001 | 25,406 | 69.2 | 63.2 | 20.7 | 16.1 | 44.3 |
| SLO | 8002 | 26,241 | 64.2 | 65.8 | 19.7 | 14.5 | 47.7 |
| SLO | 8003 | 26,287 | 67.7 | 64.3 | 20.5 | 15.2 | 44.7 |
| FS | 8000 | 31,149 | 84.1 | 79.9 | 12.3 | 7.8 | 43.6 |
| FS | 8001 | 26,908 | 77.8 | 70.9 | 17.3 | 11.9 | 45.0 |
| FS | 8002 | **45,118** | **91.7** | **93.0** | 5.1 | 1.9 | **41.2** |
| FS | 8003 | 24,091 | 77.7 | 60.2 | **32.9** | 6.9 | 44.8 |

The Llumnix SLO arm gives all four engines the same request count to within 3%
and the same class mix to within 3 points, which is what a policy with no class
concept produces. FluidServe does separate the classes without being asked to —
engine 8002 receives 93.0% chat and 45,118 requests, engine 8003 receives 32.9%
deep research — and its per-engine attainment tracks that separation, with the
chat-concentrated engine scoring highest (91.7%) at the lowest chat ITL (41.2 ms).

**The prefix cache hit rate does not read out this separation here**, contrary to
what it did for PolyServe (94.5% on the chat engine against 66.7% on the agent
engines). All eight engines report 78–81%. The separation is partial rather than
a partition, and the three classes do not have disjoint enough prompt prefixes on
this workload for a 93/60 split in class share to move the counter. **The
engine-reported hit rate is therefore not a reliable read-out of routing on this
workload; the dispatch log is.**

### 6.2.1 Why 8003: the class packing is deliberate, and its brake is too short-sighted

The whole-hour mix hides what happened. Broken out by five-minute block, the
share of each block's deep research arrivals that went to each engine:

| block | 8000 | 8001 | 8002 | 8003 | n |
|---|---|---|---|---|---|
| 0–5 | 0.3 | 0.8 | 0.0 | **98.9** | 989 |
| 5–10 | 13.1 | 15.8 | 7.3 | **63.8** | 2,292 |
| 10–15 | 15.3 | 9.8 | 3.6 | **71.3** | 2,037 |
| 15–20 | 0.0 | 0.0 | 0.0 | **100.0** | 759 |
| 20–25 | 22.8 | 26.9 | 2.7 | 47.6 | 824 |
| 25–30 | 3.9 | **93.9** | 0.5 | 1.7 | 593 |
| 30–35 | 20.6 | 45.8 | 13.1 | 20.5 | 1,170 |
| 35–60 | 24–30 | 24–30 | 12–24 | 25–30 | — |

**Engine 8003 was receiving essentially every deep research request.** Read
minute by minute over minutes 1–16 its class mix is `0/100/0`: it held nothing
but deep research. It received the *fewest* requests of the four (250–420 per
minute against 8000's 626) and the *most* input tokens (1.0–1.9M per minute),
because deep research averages 4,639 input tokens against chat's 649.

**This is the policy working as designed.** `sortCandidates`
(`pkg/scheduler/policy/fluidserve.go:1156`) orders the feasible candidates by
`share` descending — the instance already holding the most of the arriving
request's class is chosen first. The comment states the intent: an instance's
admissible occupancy is set by the tightest pace on it, so mixing classes wastes
capacity in both directions, and "filling one instance with one class until it
can take no more is what produces a separation without any instance being
assigned to a class, and the feasibility test is what stops the filling."

It works. `scheduler_fluidserve_gate_allowance_ms` per engine, by block:

| block | 8000 | 8001 | 8002 | **8003** |
|---|---|---|---|---|
| 0–5 | 50.0 | 58.6 | 50.0 | 78.4 |
| 5–10 | 50.0 | 51.4 | 50.0 | **100.0** |
| 10–15 | 50.3 | 50.0 | 50.0 | **100.0** |
| 15–20 | 52.4 | 50.0 | 50.0 | **90.7** |
| 20–25 | 50.0 | 50.0 | 50.0 | 56.2 |
| 25–60 | 50.0 | 50.0–52.3 | 50.0 | 50.0 |

Engine 8003's gate reaches **100 ms** — deep research's own budget — precisely
because nothing else is on it. That is the state section 6.1 says is missing at
the end of the hour, and it is worth twice the occupancy a 50 ms gate allows.

**What fails is the memory limit, and it fails because it is computed from a
prefix-sharing ratio measured at the instant of admission with no forward model
at all.**

First, what the projection does and does not know. Residency measured over
minutes 5–16:

| class | e2e p50 | e2e p90 | mean |
|---|---|---|---|
| chat | 17.7 s | 35.4 s | 20.2 s |
| **deepresearch** | **78.7 s** | **119.1 s** | **80.0 s** |
| swe | 17.2 s | 28.6 s | 18.4 s |

The projection is `proj = kvLogical + nDecode·horizonSteps − outflow` (line 911)
with `horizonSteps = 100`, which at the 40–100 ms paces here is **4 to 10
seconds** against a residency of **80**. The full output-length distribution is
used, but only in `expectedOutflow` (line 959), as `completionProb(j, horizon) x
kvTokens` — the probability a request finishes inside the horizon, times its
whole footprint. It decides **who leaves**, never **how much those who stay will
grow**: every resident request is credited with exactly 100 more tokens. For a
class whose completion probability over ten seconds is near zero the outflow
term vanishes and the projection reduces to `kvLogical + (live count) x 100`,
with the length distribution contributing nothing.

Two things that are **not** the defect, checked before concluding:

- **Stale metrics are not the problem.** `decodeBatchOf` (line 448) adds
  `NumTokensInflightDispatchDecodeRequests` to `kvLogical`, so a request's prompt
  is charged the moment it is dispatched rather than when the next status pull
  reports it. Successive admissions inside one poll interval see each other.
- **The per-request charge is not far wrong.** `costOf` (line 1206) charges
  `promptTokens + min(100, expectedToks)`. For deep research that is 4,639 + 100
  against an eventual 4,639 + ~840, so the charge is about **14% low** — the
  decode term alone is eight times low, but it is only 15% of the footprint. It
  also affects only the one-request-ahead test; the growth of the resident set is
  observed directly through `kvLogical`.

What actually moved is the limit. Engine 8003, the scheduler's logical KV beside
the engine's own physical occupancy:

| min | live | logical KV | physical % | phys max | logical held at 100% physical | limit | capKv | proj |
|---|---|---|---|---|---|---|---|---|
| 5 | 162 | 1,074k | 39.3 | 42 | **2,730k** | 2,099k | 4,685k | 826k |
| 6 | 404 | 2,587k | 50.6 | 68 | **5,111k** | 2,220k | 2,362k | 1,791k |
| 7 | 209 | 2,549k | **89.3** | **100** | 2,854k | 1,565k | 1,565k | 2,069k |
| 8 | 285 | 2,222k | **99.5** | **100** | **2,234k** | 1,716k | 1,746k | 1,746k |
| 12 | 253 | 1,993k | 97.2 | 100 | 2,050k | 2,074k | 2,681k | 1,563k |

**Minutes 6 and 8 hold a comparable logical count — 2,587k and 2,222k — at 50.6%
and 99.5% of the physical pool.** The logical tokens the pool was holding per
100% of physical fell from 5,111k to 2,234k and stayed near 2,000–2,400k for the
rest of the burst. The chat engine 8002 sits at 1,378k throughout for contrast.

`capMem = kvCapacity * fsMemorySafety / ratio` (line 941) with `ratio =
kvPhysical / kvLogical`, an **instantaneous** measurement of the current resident
set. Deep research requests share a system prompt of roughly 910 tokens, but
generated tokens are never shared, so **a cohort's sharing ratio is highest the
moment it arrives and decays as it decodes**. Packing one class onto one engine
makes that engine report the most favourable ratio it will ever report, at
exactly the moment the admission decisions are being made against it. The
admissions of minutes 5–6 were booked against a capacity that then halved.

The limit moves more than the projection does: between minutes 6 and 7 `proj`
rises 1,791k → 2,069k (+278k) while the limit falls 2,220k → 1,565k (**−655k**).

By minute 7 both brakes are engaged — predicted step 101.7 ms against a gate of
90 (100 x 0.90), headroom −505k — and the policy correctly stops admitting. But a
dispatched request cannot be recalled, so the resident cohort keeps decoding into
a pool that is already full, and the engine preempts.

Three candidate fixes, none tried:

1. **Give `capMem`'s ratio a forward model.** It is the only quantity in the
   policy with no projection whatsoever. Discounting it by the resident set's
   expected remaining output — which will not be shared — would have put minute
   6's figure near 2,300k rather than 5,111k.
2. **Compute `inflow` from the length distribution** rather than `nDecode x 100`.
   This makes the projection right but does not touch the limit, which is where
   the larger error was.
3. **Bound the per-instance concentration of any one class.** The crude version,
   and the only one that does not depend on an estimate being right.

The Llumnix SLO arm never reaches this state because it has no affinity term:
deep research is spread roughly evenly, so no engine's resident set is dominated
by one long-lived, high-sharing class.

### 6.3 The two denominators, drawn apart

`exp41_dynamic_timeline.py` now draws attainment twice, panel C on the offered
denominator and panel D on the admitted one, with the rejection rate that
separates them in panel B and the per-class version of each in E and F. They
were one panel before, which hid the only place in the hour where the two
denominators disagree about which policy is ahead.

| window | | offered | admitted | rej% | chat off/adm | dr off/adm | swe off/adm |
|---|---|---|---|---|---|---|---|
| whole hour | SLO | 38.5 | 66.7 | 42.1 | 26.7 / 56.4 | 100.0 / 100.0 | 58.5 / 65.9 |
| | **FS** | **59.7** | **84.3** | **29.1** | 59.3 / 82.9 | 83.7 / 94.9 | 35.1 / 77.4 |
| 0–15 | SLO | 43.8 | 74.5 | 41.2 | 29.2 / 62.6 | 100.0 / 100.0 | 77.4 / 78.7 |
| | **FS** | **71.2** | **90.8** | **21.6** | 72.6 / 91.9 | 75.3 / 84.9 | 49.4 / 94.3 |
| 15–30 | SLO | 67.5 | 68.6 | 1.5 | 65.3 / 66.4 | 100.0 / 100.0 | 82.4 / 82.4 |
| | **FS** | **89.9** | **92.0** | 2.4 | 89.6 / 91.6 | 98.9 / 99.5 | 82.6 / 91.2 |
| 30–45 | SLO | 29.3 | 63.6 | 53.9 | 8.4 / 35.6 | 100.0 / 100.0 | 59.8 / 66.1 |
| | **FS** | **52.8** | **78.3** | **32.6** | 54.3 / 75.0 | 90.6 / 99.4 | 29.1 / 73.1 |
| 45–60 | SLO | 19.2 | 58.0 | 66.9 | 1.3 / 8.6 | 100.0 / 100.0 | 36.4 / 48.3 |
| | **FS** | **32.1** | **69.6** | **53.9** | 22.8 / 58.2 | 81.8 / 98.3 | 26.1 / 64.1 |
| **50–57** | **SLO** | **17.4** | **87.6** | 80.2 | 0.0 / *no chat admitted* | 100.0 / 100.0 | 25.7 / 44.5 |
| | FS | 14.9 | 52.5 | 71.5 | 1.5 / 8.8 | 80.2 / **98.3** | 18.5 / 58.7 |

**On the admitted denominator FluidServe leads by more, not less** — 84.3
against 66.7 over the hour, and in every fifteen-minute segment. That is the
expected direction: the offered denominator charges FluidServe for the 29.1% it
rejects, and the admitted one asks only how well the accepted work was done.

**Minutes 50–57 are where the two denominators point opposite ways, and the
admitted view there is the reason it is never reported alone.** The Llumnix SLO
arm reads **87.6** on the admitted denominator — its best number anywhere in the
hour — while rejecting **80.2%** of arrivals and admitting **zero chat requests**
(the chat column is undefined, not zero). A denominator that removes rejections
scores a policy highest exactly where it refused the most. The offered view puts
the same window at 17.4.

The per-class admitted column also settles what 6.1 argued: FluidServe's deep
research reads **98.3% admitted** in that window against 80.2% offered. Its 3.1
point loss is entirely the 18.4% it shed, and the requests it did admit were
served correctly.

### 6.4 What is measured and what is not

- Both subsections are one run per arm. In 6.1 the −2.5-point crossing is inside
  the repeat spread seen elsewhere (up to 4.2 points, 11.4 at the knee), so **the
  crossing as a number is not established**; the mechanism is — an 18.4% versus
  0% shed rate on deep research, and 91.2% failure among admitted chat, are far
  outside any measured spread.
- In 6.2 the preemption count, the imbalance, the class shares, the gate
  allowances and the headroom are direct counter readings, not estimates. The
  link from the preemption burst to the narrowed advantage at minute 9 is
  temporal co-occurrence only.
- In 6.2.1 the logical KV, the limit, `capKv` and `proj` are the scheduler's own
  published gauges and the physical occupancy is the engine's own counter, so
  the sharing-ratio collapse is two direct readings placed side by side rather
  than an inference. What is inferred is the *cause* of that collapse — that a
  cohort's sharing decays as it generates unshared tokens — which follows from
  how prefix caching works but was not measured directly; the measurement would
  be per-cohort sharing against cohort age.
- An earlier version of 6.2.1 attributed the failure to the projection horizon
  and to an "eight times" undercharge in `costOf`. Both statements were wrong in
  emphasis: the decode term alone is eight times low but is 15% of a deep
  research footprint, making the per-request charge 14% low; and the projection
  already counts in-flight dispatches, so successive admissions inside one poll
  interval do see each other. The measured error is on the limit side.
- The engine attainment column uses the **admitted** denominator and is not
  comparable with the offered numbers in sections 3 and 6.1. A rejected request
  is never dispatched, so it has no engine to be attributed to.
