# EXP-54 — the four control planes on the hour-long dynamic trace

The dynamic counterpart of EXP-53. EXP-53 held the arrival rate fixed and swept
it; this replays a one-hour trace whose rate follows an Azure production trace
and whose class mix steps m1 → m2 → m3 → m1 at 15-minute boundaries, so the
question is not only "which policy is better at rate r" but "what does each
policy do when the composition of the load changes underneath it".

Trace `dyn60_short_m123`, mean offered 50.1 req/s, about 179,000 requests.
Migration on for the two Llumnix baselines and off for FluidServe and PolyServe,
engine stock vLLM FIFO, no engine-side admission, no KV admission threshold.
Scheduler binary `f88e9430f21dc74a410a7091ebdea218`, FluidServe at its v0.1.1
defaults (gate slack 1.0, candidates A, C and H2 all off).

## 1. What ran

Repeat 1, 2026-08-04 09:51 → 16:24 KST, four arms in two passes so migration is
switched once per pass rather than once per arm.

| arm | directory | health |
|---|---|---|
| FluidServe | `260803_1751_exp54r1_fluidserve_full` | ok, 4/4 engines, 50.1 req/s delivered |
| PolyServe | `260803_1905_exp54r1_polyserve_full` | ok, 4/4, 50.1 |
| Llumnix SLO | `260803_2117_exp54r1_slo_full` | ok, 4/4, 50.1 |
| Llumnix (load-balance) | `260803_2229_exp54r1_loadbalance_full` | **stopped at 87%, see §4** |

Repeat 2 launched 16:30 KST with three arms; the load-balance arm is excluded
for the reason in §4 and needs a client fix before it can be measured.

## 2. Result

Whole run, repeat 1. Offered counts a rejection as a violation; admitted removes
rejected requests from the population entirely. Goodput is output tokens per
second from requests that met their rule.

| | rejection | offered | admitted | throughput tok/s | goodput tok/s |
|---|---|---|---|---|---|
| **FluidServe** | 27.0% | **70.0** | **95.9** | 18,944 | **18,073** |
| Llumnix SLO | 42.1% | 38.8 | 67.0 | 16,163 | 11,490 |
| PolyServe | 0.0% | 17.6 | 17.6 | 18,150 | 3,198 |

**The gap is not in how many tokens the fleet produces.** PolyServe produces
18,150 output tokens per second against FluidServe's 18,944, a difference of 4%.
Of PolyServe's tokens, 3,198 per second belong to requests that finished inside
their latency rule; of FluidServe's, 18,073 do. **Same fleet, 4% difference in
total production, 5.7x difference in production that satisfies the SLO.** This
is the same shape EXP-53 measured statically at 70 req/s (37% and 24x) and it
now holds on a trace whose rate and mix both move.

Per segment, admitted denominator with the first 60 s of each segment dropped,
and the three 60-second transition windows pooled separately:

| segment | FluidServe | Llumnix SLO | PolyServe |
|---|---|---|---|
| s0_m1 0–16 min | 97.3 (rej 24.5) | 73.7 (40.8) | 18.9 (0) |
| s1_m2 16–31 | 96.9 (2.8) | 68.0 (1.6) | 2.4 (0) |
| s2_m3 31–46 | 96.2 (30.9) | 59.0 (54.7) | 20.4 (0) |
| s3_m1 46–61 | 91.8 (49.7) | 62.7 (70.3) | 9.6 (0) |
| transitions | 96.6 (5.2) | 57.8 (18.3) | 15.0 (0) |

**FluidServe loses least at the boundaries.** Its transition attainment, 96.6,
is within a point of its best segment, while Llumnix SLO drops to 57.8 from
68.0 and PolyServe to 15.0. The mix change is exactly the event a static
partition cannot follow.

Per-class attainment, admitted denominator, whole run:

| | chat | deepresearch | swe |
|---|---|---|---|
| FluidServe | 97.8 | 90.2 | 82.6 |
| Llumnix SLO | 56.9 | 100.0 | 66.0 |
| PolyServe | 3.7 | 8.8 | 95.3 |

Llumnix SLO reaches 100.0 on deep research by rejecting the classes that
compete with it; PolyServe reaches 95.3 on swe for the same reason, having given
up chat almost entirely.

Per-class goodput, output tokens per second over the whole hour, which is the
number the fairness argument turns on:

| | chat | deepresearch | swe | total |
|---|---|---|---|---|
| **FluidServe** | **12,396** | 4,622 | 700 | **17,718** |
| Llumnix SLO | 4,429 | **5,630** | **1,203** | 11,262 |
| PolyServe | 565 | 367 | 2,214 | 3,145 |

PolyServe's per-class numbers are the most even of the three, and that evenness
is produced by destroying the two large classes rather than by serving all three.

## 3. What the four engines were doing

Per-engine, from the engines' own Prometheus series and from
`request_engine.csv`, which attributes 100% of admitted requests for FluidServe
and Llumnix SLO and 90.9% for PolyServe.

| arm | port | batch | KV% | queue | preempt | chat share | attain (adm) | chat ITL ms |
|---|---|---|---|---|---|---|---|---|
| FluidServe | 8000 | 218 | 30.5 | 0.6 | 0 | 95.8% | 98.8 | 40.2 |
| | 8001 | 228 | 67.4 | 5.7 | **2,965** | 15.4% | 81.0 | 45.2 |
| | 8002 | 174 | 36.3 | 0.4 | 0 | 84.5% | 97.2 | 42.1 |
| | 8003 | 182 | 36.0 | 0.5 | 0 | 85.8% | 97.9 | 42.2 |
| Llumnix SLO | 8000–8003 | 190–204 | 54–56 | 0.3–0.4 | 0 | 62–66% | 64.8–70.7 | 44.0–47.9 |
| PolyServe | 8000 | 42 | 14.3 | 0.1 | 0 | 0% (all swe) | 97.4 | — |
| | 8001 | 243 | 21.7 | **444.9** | 4,883 | 88.8% | 20.2 | **96.4** |
| | 8002 | 374 | 23.2 | **1,120.8** | 599 | 98.4% | 2.2 | **101.0** |
| | 8003 | 248 | 46.3 | **1,056.6** | **19,594** | 0% (all dr) | 10.0 | — |

> ⚠ **Read section 7.2 and 7.3 before using this table.** The separation is real
> and repeat 1 held it on the same engine for the whole hour, so these numbers
> stand. The *causal* sentence below — that the separation is what keeps chat
> inside budget — is neither confirmed nor refuted, because both repeats
> separate and there is no unseparated run to compare against.

**This is the clearest direct evidence of class packing recorded so far.**
FluidServe puts deep research on engine 8001 — 67.5% of that engine's requests
are deep research against 3–11% on the other three — and the three engines left
holding chat run it at 40–42 ms per token, inside chat's 50 ms budget, and
return 97–99% attainment. Engine 8001 pays for it with 2,965 preemptions and 81%
attainment. **The separation is what keeps chat's pace inside budget**: an engine
that holds only chat has nothing on it whose longer decode would stretch the
per-token time of the requests already there.

PolyServe's static partition does the opposite. Its two chat engines run chat at
96.4 and 101.0 ms per token, twice the budget, with 445 and 1,121 requests
queued at the engine, while engine 8000 sits at batch 42 with a queue of 0.1 and
serves 8,748 requests at 97.4%. Busiest-to-least imbalance averages **48x**
against FluidServe's 4.5x and Llumnix SLO's 1.25x. Engine 8003, the deep
research partition, preempts 19,594 times.

Llumnix SLO spreads everything evenly, which is visible as all four engines
carrying 62–66% chat and running it at 44–48 ms, just inside budget, for 65–71%
attainment.

**Migration produced no observable request movement in the Llumnix SLO arm.**
`build_request_engine_map.py` reports `migration-flagged uuids: 0` for it, the
same as for the two arms that ran with migration off. Before any claim that the
baselines were given their own mechanism, this needs explaining — either the
mechanism did not fire on this trace, or the engine-side flag is not what
controls it.

## 4. The load-balance arm, and why it was stopped

The arm ran 116 minutes against the other three arms' 72 and was stopped at 87%
of its tasks. Its last twenty minutes measure the load generator, not the policy.

The call-level record, by five-minute window:

| window (min) | ok | stream cut | Errno 99 | calls started/s |
|---|---|---|---|---|
| 0–40 | 122,430 | 0 | 0 | 26–62 |
| 45–50 | 3,146 | 9,814 | 2,204 | 50.5 |
| 50–55 | 965 | 15,558 | **34,488** | **170.0** |
| 55–60 | 108 | 16,088 | 19,480 | 118.9 |

`[Errno 99] Cannot assign requested address` is the client failing to obtain a
source port for a new TCP connection. Linux offers about 28,000 ephemeral ports
and a closed connection holds its port in `TIME_WAIT` for 60 seconds. No other
arm produced a single one of these; this arm produced 62,114.

The sequence is: load-balance rejects nothing, so at minute 40 all four engines
saturate and requests that have been queued a long time have their streams cut.
A cut stream is an exception, and **the client's exception handler does not
recognise a connection-level failure as a server termination** — the keyword
list at `workloads/swe_bench_coding/agent.py:936` covers `connectionreset`,
`brokenpipe`, `connectionaborted`, `connection refused`, `eof occurred` and
`server disconnected`, and `cannot assign requested address` is in none of them.
So the call falls into the generic branch, which **opens one more connection**
for a non-streaming retry (`llm.invoke`, 31,383 of them in this arm). That
retry needs a port too. Once the port range is exhausted every attempt fails
immediately, and because a failure returns in microseconds the attempt rate
climbs to 170/s — which is not the trace's arrival rate but the client spinning.

**Rejections are not retried.** The handler branches on the rejection first and
records `is_rejected=True` without touching the fallback, which is what the
comment at line 229 means by "no double submission". `is_rejected` is False for
all 363,226 rows in this arm, so none of these failures were rejections; the
policy never rejects anything. The HTTPAdapter is also mounted with
`max_retries=0`, so urllib3 itself retries nothing — the "Max retries exceeded"
text in the message is requests' standard wording for a connection failure, not
evidence of a retry.

Before this arm can be measured the client needs two changes: add
`cannot assign requested address` and `max retries exceeded` to the
server-termination keyword list so a connection failure is recorded rather than
retried, and widen `net.ipv4.ip_local_port_range` on the runner. Neither is on
the measurement path for the other three arms.

Minutes 0–40 of this arm contain no errors at all and remain usable.

## 5. Figures

`results/aggregate_analysis/exp54/`

| file | what |
|---|---|
| `exp54_full_timeline.png` | eight panels on one time axis: offered rate, rejection rate, attainment on both denominators, per-class attainment on both, goodput, fleet batch and KV |
| `exp54_full_engine_timeline.png` | per-engine batch, KV, queue, preemption, prefix hit rate |
| `exp54_full_engine_requests.png` | per-engine request counts, class mix and chat ITL |
| `compare_rpm_full.png` | three policies side by side, one column each, rows sharing a y axis |
| `engine_*.png`, `tokens_*.png`, `llumnix_*.png` | the per-condition engine-layer set, one of each per arm |
| `class_goodput_hour.png` | per-class goodput over time |

Three plotting scripts were generalised to accept a dynamic trace, since all of
them assumed a rate in the directory name and so had silently never been run on
an hour-long trace: `plot_ratesweep_split.py` (falls back to the directory name
as its tag), `exp38_policy_compare.py` (keys the cell on the variant when there
is no `_rpm_`), and `exp53_class_goodput.py` (takes `--hour label|colour|dir`).
`exp41_dynamic_timeline.py` now draws every arm-coloured panel solid and keeps
the per-arm line style only in the two panels where colour already encodes the
class.

## 6. What repeat 2 is for

Every number above is one measurement per condition. On this trace the
whole-run score moves 0.5 points or less between repeats — EXP-41 and EXP-44
measured the same FluidServe configuration a day apart at offered 59.7 and 59.2
— so the 31.2-point gap between FluidServe and Llumnix SLO is far outside it.
**Preemption is not**: the same two runs moved 1,471 to 1,852, 26%. The
per-engine preemption counts in §3 should not be quoted until repeat 2 is in.

---

## 7. Repeat 2, and a correction to section 3

Repeat 2 ran three arms on 2026-08-04 16:31 → 21:03 KST, all healthy, 4/4
engines, 50.1 req/s delivered. Directories `260804_0031_exp54r2_fluidserve_full`,
`260804_0140_exp54r2_polyserve_full`, `260804_0352_exp54r2_slo_full`.

### 7.1 The score reproduces closely

Mean over two repeats, with (max − min)/2 beside it.

| | rejection | offered | admitted | throughput tok/s | goodput tok/s |
|---|---|---|---|---|---|
| **FluidServe** | 27.1 ±0.1 | **69.6 ±0.3** | **95.5 ±0.4** | 18,911 ±33 | **17,967 ±106** |
| Llumnix SLO | 42.1 ±0.0 | 39.0 ±0.2 | 67.2 ±0.3 | 16,176 ±13 | 11,545 ±55 |
| PolyServe | 0.0 ±0.0 | 17.1 ±0.5 | 17.1 ±0.5 | 18,212 ±62 | 3,192 ±7 |

Per-class goodput, output tokens/s: FluidServe **12,437 ±87 / 4,717 ±20 /
713 ±1**, Llumnix SLO 4,552 ±48 / **5,762 ±1** / **1,231 ±6**, PolyServe
578 ±3 / 371 ±2 / 2,243 ±8.

Every spread is under 0.5 points and the FluidServe–Llumnix SLO gap is 30.6, so
the comparison is settled. The section 2 observation reproduces: total
production differs by 3.8% and goodput by 5.6x.

### 7.2 The separation reproduces; which engine holds it does not

**This section was first written with the opposite conclusion and the conclusion
was wrong.** The statistic used was the share of each class held by the engine
holding the most of it, computed over the whole hour. It read 58.7% for deep
research in repeat 1 and 32.6% in repeat 2 against 25% for an even spread, and
that was recorded as "the separation does not reproduce".

Windowed at three minutes, both repeats are concentrated at 99–100% for the
first third of the hour. What differs is which engine, and whether it lasts:

```
repeat 1:  8001 for all 58 windows
repeat 2:  8002 (0–21 min) → 8003 (21–33 min) → 8001 (33–60 min)
```

**Pooling an hour over a target whose identity moves spreads the distribution
and reads as no concentration.** The median over windows is 53.5% (repeat 1) and
51.5% (repeat 2) — the same number.

Both repeats also lose concentration after minute 33, falling to 41–56%. That is
the highest-load stretch of the trace, and it matches section 37.3: at
saturation nothing is feasible, the affinity ordering runs only within the
feasible set, and a separation cannot be rebuilt.

The figure `separation_vs_score.png` carries three panels for this reason —
score, windowed share, and which engine — because any two of them mislead. It is
also what caught the error: the windowed panel showed the two repeats tracking
each other while the table said they diverged, and the table was believed first.

### 7.3 What this leaves of section 3's causal claim

Section 3 said the separation is the mechanism that keeps chat's per-token time
inside budget. **That claim is neither confirmed nor refuted here.** Both repeats
separate, so there is no unseparated run to compare against.

Measuring it needs the ablation that already exists,
`--fluidserve-enable-affinity=false`, with the judgement rule written first.

One observation survives unchanged: FluidServe runs chat at 38–45 ms per token
in both repeats while Llumnix SLO runs it at 44–48 ms in both, against a 50 ms
budget. Those are pooled per-engine figures and carry the same caveat as above.

### 7.4 Preemption reproduces in the fleet total and not per engine

| arm | repeat | 8000 | 8001 | 8002 | 8003 | total |
|---|---|---|---|---|---|---|
| FluidServe | 1 | 0 | 2,965 | 0 | 0 | **2,965** |
| | 2 | 0 | 2,064 | 858 | 0 | **2,922** |
| PolyServe | 1 | 0 | 4,883 | 599 | 19,594 | **25,076** |
| | 2 | 0 | 20,058 | 5,510 | 0 | **25,568** |
| Llumnix SLO | 1, 2 | 0 | 0 | 0 | 0 | **0** |

Totals reproduce within 1.5% and 2.0%; which engine pays does not. Section 6's
instruction not to quote the preemption numbers before repeat 2 was right about
the per-engine figures and unnecessary for the totals.

### 7.5 Still unexplained

**`classharm=false`.** The scheduler's start-up line reports it for all 50
FluidServe conditions across EXP-51, 52, 53 and 54. The source default is `true`,
the deployed binary's own `--help` says `(default true)`, the deploy script
reports leaving the flag unset, and the running pod's args do not contain it.
Four sources say true and the start-up line says false. The decisive test is to
pass `FS_CLASS_HARM=true` explicitly and see whether the line changes.

**Migration.** `build_request_engine_map.py` reports `migration-flagged uuids: 0`
for all six conditions including the two Llumnix SLO arms that ran with it on.
