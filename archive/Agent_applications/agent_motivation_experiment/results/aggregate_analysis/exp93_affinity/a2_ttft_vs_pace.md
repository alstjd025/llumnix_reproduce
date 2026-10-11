# EXP-93 A2: does the class preference trade a per-token gain for a first-token loss?

Written 2026-08-24. Read-only analysis of four one-hour runs that were already on disk;
no cluster job was launched and nothing outside this file was written.

**Question.** FluidServe's class preference concentrates a class onto fewer instances.
Concentrating deep research also concentrates its prefill, because deep research carries
3,814-token prompts. The hypothesis under test is that the preference buys a per-token gain
and pays for it in time to first token.

**Answer.** The trade is real, it is reproducible across both repeats, and it is larger than
the repeat spread. In the segment where the two arms actually differ in whether an engine
becomes dedicated (`s2_m1`, minutes 31-46, mix 76.9% chat / 15.4% deep research / 7.7% agent),
the share of answered chat requests exceeding the 50 ms per-token budget falls from 7.06% and
7.84% with the preference off to 0.80% and 0.89% with it on, and the share of answered deep
research requests exceeding the 10 s first-token budget rises from 0.8% and 0.7% off to 11.8%
and 12.9% on. The chat movement is 6.6 points against a repeat spread of at most 0.78 points,
and the deep research movement is 11.6 points against a repeat spread of at most 1.1 points, so
each is at least eight times its own spread.
Counted in requests the trade is favourable: over the 24,140 arrivals of that segment the
preference avoids about 1,148 chat per-token misses and adds about 397 deep research
first-token misses.

**But the preference is not what creates the concentration in the other two segments.** In
`s1_A` (even mix) and `s3_B` (60/30/10) the arm with the preference OFF also produces one
engine holding 67.6-85.9% deep research, and on that engine deep research misses its
first-token budget at 72.9-76.4%, indistinguishable from the 66.6-87.1% measured on the
preference-on arm's dedicated engine. The feasibility test concentrates deep research by
itself once the class is a large enough share of arrivals. The preference changes the
concentration only where the mix alone does not force it.

---

## Method, and one deviation from the script named in the task

Requests come from `exp22_fluidserve.load_run`, one row per arrival, with the 60 s warm-up
and 20 s drain removed and run-boundary cutoffs dropped. Segments come from
`exp93_mix_shift.read_plan` and `tag_segments`, keyed on arrival time. The request-to-engine
join is `exp41_engine_view.attribute_engines`; it matched 100.0% of admitted requests on all
four runs.

The miss rule is `slo_rule_breakdown.RULES`: chat 5 s first token and 50 ms per token, deep
research 10 s and 100 ms, agent 30 s end to end. **The per-token quantity is not the one that
script reads.** `slo_rule_breakdown.py` uses the client's `tbt_mean_ms` column, which this
repository established is 1/1.92 of the true per-token time because the client tokenises each
streamed chunk out of context. This analysis therefore keeps the thresholds and substitutes
`load_run`'s corrected `itl_ms`, computed as `(end-to-end - first token) / (output tokens - 1)`.
Numbers below will not match what running `slo_rule_breakdown.py` directly prints.

Every miss is placed in exactly one category. Rejection and client error precede any rule, so
a rejected request is counted as rejected rather than as a first-token miss. Among requests
that produced an answer, a miss is `TTFT only`, `pace only`, or `both`.

### 0. Runs, window and arrival counts

| arm | repeat | directory | arrivals in window | s0_m2 | s1_A | s2_m1 | s3_B |
|---|---|---|---|---|---|---|---|
| preference on | 1 | `260822_2141_exp93r1_fspfx_shift` | 98,019 | 21,982 | 22,505 | 24,140 | 29,392 |
| preference on | 2 | `260823_0007_exp93br1_fspfx_shift` | 98,007 | 21,986 | 22,503 | 24,142 | 29,376 |
| preference off | 1 | `260823_0721_exp93nr1_fsnoaff_shift` | 98,022 | 21,982 | 22,505 | 24,141 | 29,394 |
| preference off | 2 | `260823_0834_exp93nbr1_fsnoaff_shift` | 98,020 | 21,982 | 22,502 | 24,147 | 29,389 |

The four runs see the same arrivals to within 20 requests out of about 98,000, so no result
below is a difference in offered load.

---

## (a) What breaks: first token or pace?

The two classes break opposite rules, and neither arm changes which rule they break.

### (a) miss composition per class, per arm

| class | arm | rep | arrivals | misses | miss% | rejected | errored | no first token | TTFT only | pace only | both | e2e |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| chat | on | 1 | 64,246 | 8,615 | 13.4 | 6,969 | 0 | 0 | 11 | 1,635 | 0 | 0 |
| chat | on | 2 | 64,238 | 8,025 | 12.5 | 6,710 | 0 | 0 | 14 | 1,300 | 1 | 0 |
| chat | off | 1 | 64,232 | 10,053 | 15.7 | 7,027 | 0 | 0 | 17 | 2,994 | 15 | 0 |
| chat | off | 2 | 64,239 | 9,743 | 15.2 | 6,501 | 0 | 0 | 9 | 3,227 | 6 | 0 |
| deepresearch | on | 1 | 20,951 | 8,976 | 42.8 | 6,213 | 0 | 0 | 2,760 | 2 | 1 | 0 |
| deepresearch | on | 2 | 20,949 | 9,331 | 44.5 | 6,323 | 0 | 0 | 3,007 | 1 | 0 | 0 |
| deepresearch | off | 1 | 20,966 | 9,179 | 43.8 | 6,896 | 0 | 0 | 2,282 | 0 | 1 | 0 |
| deepresearch | off | 2 | 20,959 | 9,059 | 43.2 | 6,845 | 0 | 0 | 2,213 | 1 | 0 | 0 |
| swe | on | 1 | 12,822 | 6,631 | 51.7 | 5,555 | 0 | 0 | 0 | 0 | 0 | 1,076 |
| swe | on | 2 | 12,820 | 6,501 | 50.7 | 5,376 | 0 | 0 | 0 | 0 | 0 | 1,125 |
| swe | off | 1 | 12,824 | 6,318 | 49.3 | 5,155 | 0 | 0 | 0 | 0 | 0 | 1,163 |
| swe | off | 2 | 12,822 | 6,499 | 50.7 | 5,378 | 0 | 0 | 0 | 0 | 0 | 1,121 |

**TTFT vs pace among misses that DID get an answer** (denominator = TTFT-only + pace-only + both)

| class | arm | rep | answered misses | TTFT-involved % | pace-involved % | TTFT-only % | pace-only % |
|---|---|---|---|---|---|---|---|
| chat | on | 1 | 1,646 | 0.7 | 99.3 | 0.7 | 99.3 |
| chat | on | 2 | 1,315 | 1.1 | 98.9 | 1.1 | 98.9 |
| chat | off | 1 | 3,026 | 1.1 | 99.4 | 0.6 | 98.9 |
| chat | off | 2 | 3,242 | 0.5 | 99.7 | 0.3 | 99.5 |
| deepresearch | on | 1 | 2,763 | 99.9 | 0.1 | 99.9 | 0.1 |
| deepresearch | on | 2 | 3,008 | 100.0 | 0.0 | 100.0 | 0.0 |
| deepresearch | off | 1 | 2,283 | 100.0 | 0.0 | 100.0 | 0.0 |
| deepresearch | off | 2 | 2,214 | 100.0 | 0.0 | 100.0 | 0.0 |

**same, per segment** (deep research and chat only)

| class | segment | arm | rep | answered misses | TTFT-involved % | pace-involved % |
|---|---|---|---|---|---|---|
| chat | s0_m2 | on | 1 | 200 | 4.5 | 95.5 |
| chat | s0_m2 | on | 2 | 183 | 6.0 | 94.0 |
| chat | s0_m2 | off | 1 | 202 | 5.4 | 94.6 |
| chat | s0_m2 | off | 2 | 255 | 2.4 | 98.4 |
| chat | s1_A | on | 1 | 387 | 0.5 | 99.5 |
| chat | s1_A | on | 2 | 448 | 0.9 | 99.3 |
| chat | s1_A | off | 1 | 604 | 0.2 | 99.8 |
| chat | s1_A | off | 2 | 648 | 0.3 | 99.7 |
| chat | s2_m1 | on | 1 | 134 | 0.0 | 100.0 |
| chat | s2_m1 | on | 2 | 149 | 0.0 | 100.0 |
| chat | s2_m1 | off | 1 | 1,227 | 1.5 | 99.7 |
| chat | s2_m1 | off | 2 | 1,355 | 0.4 | 99.9 |
| chat | s3_B | on | 1 | 925 | 0.0 | 100.0 |
| chat | s3_B | on | 2 | 535 | 0.0 | 100.0 |
| chat | s3_B | off | 1 | 993 | 0.2 | 99.9 |
| chat | s3_B | off | 2 | 984 | 0.2 | 99.9 |
| deepresearch | s0_m2 | on | 1 | 2 | - | - |
| deepresearch | s0_m2 | on | 2 | 1 | - | - |
| deepresearch | s0_m2 | off | 1 | 0 | - | - |
| deepresearch | s0_m2 | off | 2 | 0 | - | - |
| deepresearch | s1_A | on | 1 | 1,097 | 99.9 | 0.1 |
| deepresearch | s1_A | on | 2 | 1,314 | 100.0 | 0.0 |
| deepresearch | s1_A | off | 1 | 1,184 | 100.0 | 0.0 |
| deepresearch | s1_A | off | 2 | 1,147 | 100.0 | 0.0 |
| deepresearch | s2_m1 | on | 1 | 401 | 99.8 | 0.2 |
| deepresearch | s2_m1 | on | 2 | 438 | 99.8 | 0.2 |
| deepresearch | s2_m1 | off | 1 | 24 | 100.0 | 4.2 |
| deepresearch | s2_m1 | off | 2 | 22 | 95.5 | 4.5 |
| deepresearch | s3_B | on | 1 | 1,263 | 100.0 | 0.1 |
| deepresearch | s3_B | on | 2 | 1,255 | 100.0 | 0.0 |
| deepresearch | s3_B | off | 1 | 1,075 | 100.0 | 0.0 |
| deepresearch | s3_B | off | 2 | 1,045 | 100.0 | 0.0 |

Read the two right-hand groups. Chat almost never misses on first token: 9 to 17 TTFT-only
misses per run against 1,300 to 3,227 pace-only misses. Deep research almost never misses on
pace: 0 to 2 pace misses per run against 2,213 to 3,007 TTFT misses. Agent misses end to end
or not at all, by construction of its rule.

Answering the question as posed: among misses that produced an answer, chat's TTFT-involved
share is 0.5-1.1% in all four runs, and deep research's TTFT-involved share is 99.9-100.0% in
all four runs. **The preference does not change which rule a class breaks. It changes how many
requests break it, and it moves the two classes in opposite directions.** Chat's answered
misses fall from 3,026 and 3,242 (off) to 1,646 and 1,315 (on). Deep research's answered
misses rise from 2,283 and 2,214 (off) to 2,763 and 3,008 (on).

The per-segment table shows the movement is concentrated in `s2_m1`. Chat's answered misses
there are 134 and 149 with the preference on against 1,227 and 1,355 with it off, a factor of
8.7 on the repeat means. In `s0_m2`, `s1_A` and `s3_B` the two arms are within or close to
their repeat spread.

Part of the class-level movement is a change in what was admitted rather than a change in how
admitted requests fared, so the rejection rates are needed to read the counts:

### per-segment rejection rate, per class (denominator = arrivals in that segment)

| class | segment | on r1 | on r2 | off r1 | off r2 |
|---|---|---|---|---|---|
| chat | s0_m2 | 1.0 | 1.5 | 2.0 | 2.0 |
| chat | s1_A | 8.5 | 11.8 | 12.2 | 11.4 |
| chat | s2_m1 | 9.3 | 8.9 | 6.3 | 6.6 |
| chat | s3_B | 24.8 | 21.8 | 25.6 | 22.6 |
| deepresearch | s0_m2 | 5.2 | 4.7 | 4.8 | 5.5 |
| deepresearch | s1_A | 34.7 | 36.4 | 37.7 | 37.1 |
| deepresearch | s2_m1 | 8.5 | 8.8 | 17.0 | 17.4 |
| deepresearch | s3_B | 37.2 | 36.9 | 38.8 | 38.6 |
| swe | s0_m2 | 15.0 | 16.2 | 14.8 | 14.6 |
| swe | s1_A | 39.9 | 37.4 | 36.4 | 38.3 |
| swe | s2_m1 | 39.5 | 39.1 | 32.6 | 32.8 |
| swe | s3_B | 59.3 | 59.7 | 59.1 | 61.7 |

In `s2_m1` the preference rejects more chat (9.3% and 8.9% against 6.3% and 6.6%) and less
deep research (8.5% and 8.8% against 17.0% and 17.4%). It therefore admits roughly 320 more
deep research requests per run in that segment, and those extra admissions land on the
concentrated engine. The first-token cost is thus partly a cost of admitting more of the class
and partly a cost of where the class is put; section (b) separates the two by measuring the
miss rate per engine rather than the miss count per class.

---

## (b) Deep research, split by the engine it was dispatched to

### (b) deep research per dispatch engine

attribution coverage (admitted requests joined to an engine):

- preference on repeat 1: 79,282 of 79,282 = 100.0%
- preference on repeat 2: 79,598 of 79,598 = 100.0%
- preference off repeat 1: 78,944 of 78,944 = 100.0%
- preference off repeat 2: 79,296 of 79,296 = 100.0%

| arm | rep | segment | engine | reqs | chat% | dr% | swe% | dr n | dr TTFT>10s % | dr TTFT p50 | dr TTFT p90 | dr ITL p50 | dr ITL p90 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| on | 1 | s0_m2 | 8000 | 8,468 | 99.4 | 0.6 | 0.0 | 52 | 0.0 | 0.62 | 3.83 | 44.8 | 45.8 |
| on | 1 | s0_m2 | 8001 **(least chat)** | 2,580 | 70.2 | 25.8 | 4.1 | 665 | 0.3 | 0.42 | 7.19 | 29.0 | 46.6 |
| on | 1 | s0_m2 | 8002 | 5,325 | 94.1 | 2.7 | 3.1 | 146 | 0.0 | 1.86 | 9.05 | 45.6 | 47.1 |
| on | 1 | s0_m2 | 8003 | 5,265 | 94.9 | 2.0 | 3.1 | 107 | 0.0 | 1.33 | 8.62 | 45.0 | 46.0 |
| on | 2 | s0_m2 | 8000 | 7,827 | 98.8 | 0.7 | 0.5 | 58 | 0.0 | 1.59 | 8.90 | 45.0 | 45.8 |
| on | 2 | s0_m2 | 8001 | 2,599 | 77.0 | 15.5 | 7.5 | 403 | 0.0 | 0.56 | 8.80 | 40.8 | 46.9 |
| on | 2 | s0_m2 | 8002 **(least chat)** | 2,533 | 73.8 | 18.4 | 7.7 | 467 | 0.2 | 0.60 | 8.77 | 30.3 | 47.2 |
| on | 2 | s0_m2 | 8003 | 8,586 | 99.5 | 0.5 | 0.0 | 47 | 0.0 | 1.42 | 9.10 | 44.8 | 45.3 |
| off | 1 | s0_m2 | 8000 | 5,372 | 93.8 | 4.2 | 1.9 | 228 | 0.0 | 0.85 | 8.97 | 44.1 | 46.7 |
| off | 1 | s0_m2 | 8001 **(least chat)** | 5,274 | 93.1 | 4.7 | 2.2 | 246 | 0.0 | 0.83 | 8.99 | 37.5 | 46.4 |
| off | 1 | s0_m2 | 8002 | 5,406 | 93.4 | 4.5 | 2.1 | 244 | 0.0 | 0.80 | 9.10 | 39.1 | 46.5 |
| off | 1 | s0_m2 | 8003 | 5,392 | 93.4 | 4.7 | 1.9 | 255 | 0.0 | 0.94 | 9.16 | 42.7 | 46.5 |
| off | 2 | s0_m2 | 8000 | 5,372 | 93.1 | 4.6 | 2.3 | 249 | 0.0 | 0.72 | 9.07 | 40.4 | 46.1 |
| off | 2 | s0_m2 | 8001 **(least chat)** | 4,867 | 91.8 | 5.8 | 2.5 | 280 | 0.0 | 1.21 | 9.03 | 45.2 | 47.8 |
| off | 2 | s0_m2 | 8002 | 5,441 | 94.2 | 4.0 | 1.8 | 219 | 0.0 | 0.82 | 8.90 | 38.5 | 46.0 |
| off | 2 | s0_m2 | 8003 | 5,756 | 94.6 | 3.8 | 1.6 | 219 | 0.0 | 0.71 | 8.84 | 37.4 | 45.5 |
| on | 1 | s1_A | 8000 | 5,088 | 49.8 | 20.4 | 29.8 | 1,037 | 0.0 | 1.04 | 3.25 | 47.4 | 49.5 |
| on | 1 | s1_A | 8001 | 5,069 | 56.5 | 22.6 | 20.9 | 1,144 | 0.0 | 1.25 | 3.64 | 46.8 | 50.0 |
| on | 1 | s1_A | 8002 | 4,150 | 35.5 | 25.7 | 38.7 | 1,068 | 0.0 | 1.12 | 4.03 | 46.9 | 49.4 |
| on | 1 | s1_A | 8003 **(least chat)** | 1,966 | 0.0 | 83.7 | 16.3 | 1,646 | 66.6 | 14.90 | 18.73 | 69.7 | 73.6 |
| on | 2 | s1_A | 8000 | 5,015 | 51.0 | 20.9 | 28.1 | 1,048 | 0.0 | 1.23 | 3.91 | 47.5 | 49.2 |
| on | 2 | s1_A | 8001 | 3,815 | 23.9 | 21.7 | 54.4 | 828 | 0.0 | 1.29 | 7.23 | 48.4 | 51.9 |
| on | 2 | s1_A | 8002 **(least chat)** | 1,736 | 0.0 | 99.7 | 0.3 | 1,730 | 76.0 | 15.69 | 19.12 | 70.6 | 73.7 |
| on | 2 | s1_A | 8003 | 5,514 | 57.1 | 21.1 | 21.8 | 1,163 | 0.0 | 1.22 | 3.58 | 46.9 | 48.4 |
| off | 1 | s1_A | 8000 | 4,842 | 46.6 | 21.8 | 31.7 | 1,055 | 0.0 | 1.07 | 3.69 | 47.5 | 49.1 |
| off | 1 | s1_A | 8001 **(least chat)** | 1,822 | 6.9 | 85.9 | 7.2 | 1,565 | 75.7 | 15.69 | 19.34 | 69.6 | 72.3 |
| off | 1 | s1_A | 8002 | 4,630 | 45.5 | 22.4 | 32.1 | 1,037 | 0.0 | 1.15 | 3.53 | 47.2 | 49.2 |
| off | 1 | s1_A | 8003 | 4,735 | 44.4 | 21.4 | 34.2 | 1,012 | 0.0 | 1.06 | 3.33 | 46.9 | 49.4 |
| off | 2 | s1_A | 8000 | 4,546 | 45.8 | 24.2 | 30.0 | 1,100 | 0.9 | 1.18 | 7.70 | 47.8 | 52.4 |
| off | 2 | s1_A | 8001 **(least chat)** | 2,146 | 15.9 | 72.7 | 11.4 | 1,560 | 72.9 | 15.42 | 18.71 | 69.3 | 72.7 |
| off | 2 | s1_A | 8002 | 4,569 | 45.6 | 21.4 | 33.0 | 977 | 0.0 | 1.17 | 4.09 | 47.1 | 49.5 |
| off | 2 | s1_A | 8003 | 4,734 | 45.2 | 22.9 | 31.9 | 1,083 | 0.0 | 1.15 | 4.35 | 47.3 | 49.0 |
| on | 1 | s2_m1 | 8000 | 3,684 | 55.5 | 31.5 | 13.0 | 1,161 | 0.9 | 0.97 | 3.12 | 47.3 | 68.4 |
| on | 1 | s2_m1 | 8001 | 10,915 | 97.5 | 1.7 | 0.8 | 185 | 0.0 | 0.59 | 1.81 | 45.1 | 46.1 |
| on | 1 | s2_m1 | 8002 | 4,548 | 74.1 | 15.3 | 10.6 | 697 | 0.0 | 1.14 | 3.29 | 45.1 | 47.3 |
| on | 1 | s2_m1 | 8003 **(least chat)** | 2,218 | 35.1 | 61.3 | 3.6 | 1,359 | 28.7 | 2.99 | 14.83 | 68.1 | 71.1 |
| on | 2 | s2_m1 | 8000 | 5,007 | 79.0 | 16.2 | 4.8 | 810 | 0.0 | 0.86 | 2.81 | 45.1 | 47.1 |
| on | 2 | s2_m1 | 8001 | 3,781 | 57.4 | 21.8 | 20.8 | 823 | 0.0 | 1.10 | 3.50 | 45.4 | 47.1 |
| on | 2 | s2_m1 | 8002 **(least chat)** | 1,604 | 0.0 | 97.3 | 2.7 | 1,561 | 28.0 | 1.92 | 15.28 | 68.3 | 72.4 |
| on | 2 | s2_m1 | 8003 | 11,039 | 97.6 | 1.8 | 0.6 | 195 | 0.0 | 0.75 | 2.30 | 45.5 | 46.3 |
| off | 1 | s2_m1 | 8000 | 5,721 | 81.9 | 12.5 | 5.7 | 713 | 0.1 | 1.07 | 8.32 | 44.7 | 51.6 |
| off | 1 | s2_m1 | 8001 **(least chat)** | 4,320 | 71.9 | 21.4 | 6.7 | 925 | 2.5 | 2.10 | 8.98 | 47.6 | 61.6 |
| off | 1 | s2_m1 | 8002 | 5,723 | 81.6 | 13.0 | 5.4 | 744 | 0.0 | 1.07 | 7.20 | 44.7 | 46.8 |
| off | 1 | s2_m1 | 8003 | 5,979 | 82.7 | 11.7 | 5.5 | 702 | 0.0 | 0.96 | 7.10 | 44.6 | 47.3 |
| off | 2 | s2_m1 | 8000 **(least chat)** | 4,537 | 75.7 | 18.2 | 6.0 | 828 | 1.3 | 2.47 | 8.92 | 47.8 | 61.3 |
| off | 2 | s2_m1 | 8001 | 5,863 | 83.0 | 12.0 | 5.0 | 702 | 0.0 | 1.01 | 7.23 | 44.7 | 45.8 |
| off | 2 | s2_m1 | 8002 | 5,760 | 81.4 | 13.0 | 5.7 | 746 | 0.0 | 1.09 | 7.94 | 44.8 | 46.8 |
| off | 2 | s2_m1 | 8003 | 5,502 | 79.1 | 14.4 | 6.5 | 793 | 1.3 | 1.13 | 8.72 | 45.0 | 49.7 |
| on | 1 | s3_B | 8000 **(least chat)** | 1,450 | 0.0 | 100.0 | 0.0 | 1,450 | 87.1 | 16.61 | 19.56 | 70.1 | 73.0 |
| on | 1 | s3_B | 8001 | 8,453 | 86.5 | 11.0 | 2.5 | 930 | 0.0 | 0.98 | 2.61 | 47.9 | 48.7 |
| on | 1 | s3_B | 8002 | 4,968 | 59.9 | 28.8 | 11.2 | 1,432 | 0.0 | 1.28 | 3.06 | 48.8 | 50.0 |
| on | 1 | s3_B | 8003 | 5,135 | 59.3 | 32.3 | 8.4 | 1,659 | 0.0 | 1.17 | 2.89 | 48.3 | 49.5 |
| on | 2 | s3_B | 8000 | 6,829 | 78.1 | 17.6 | 4.2 | 1,205 | 0.0 | 1.17 | 3.04 | 47.5 | 48.6 |
| on | 2 | s3_B | 8001 | 5,024 | 58.3 | 29.0 | 12.7 | 1,459 | 0.0 | 1.11 | 3.13 | 48.0 | 49.3 |
| on | 2 | s3_B | 8002 **(least chat)** | 1,516 | 0.0 | 100.0 | 0.0 | 1,516 | 82.8 | 16.34 | 19.36 | 71.4 | 74.8 |
| on | 2 | s3_B | 8003 | 7,173 | 78.0 | 18.3 | 3.7 | 1,313 | 0.0 | 1.02 | 2.91 | 47.9 | 48.7 |
| off | 1 | s3_B | 8000 **(least chat)** | 1,764 | 17.6 | 79.6 | 2.8 | 1,404 | 76.4 | 16.06 | 19.65 | 69.6 | 72.7 |
| off | 1 | s3_B | 8001 | 6,088 | 71.9 | 21.3 | 6.8 | 1,299 | 0.0 | 1.11 | 3.02 | 48.1 | 49.2 |
| off | 1 | s3_B | 8002 | 5,914 | 71.4 | 22.8 | 5.8 | 1,349 | 0.2 | 1.17 | 2.90 | 48.1 | 49.4 |
| off | 1 | s3_B | 8003 | 5,962 | 71.5 | 21.7 | 6.8 | 1,292 | 0.0 | 1.13 | 2.99 | 48.0 | 49.2 |
| off | 2 | s3_B | 8000 | 5,916 | 71.7 | 22.2 | 6.1 | 1,316 | 0.1 | 1.17 | 3.59 | 47.9 | 49.2 |
| off | 2 | s3_B | 8001 | 5,957 | 71.9 | 22.1 | 6.0 | 1,314 | 0.2 | 1.14 | 3.52 | 47.9 | 49.0 |
| off | 2 | s3_B | 8002 **(least chat)** | 2,074 | 29.3 | 67.6 | 3.1 | 1,401 | 74.3 | 15.92 | 18.93 | 70.6 | 73.3 |
| off | 2 | s3_B | 8003 | 6,256 | 73.3 | 21.2 | 5.5 | 1,327 | 0.0 | 1.12 | 3.14 | 47.8 | 48.8 |

**deep research on the least-chat engine vs the rest**

| arm | rep | segment | least-chat engine | its chat% | dr TTFT>10s % there | dr TTFT>10s % elsewhere | dr n there | dr n elsewhere |
|---|---|---|---|---|---|---|---|---|
| on | 1 | s0_m2 | 8001 | 70.2 | 0.3 | 0.0 | 665 | 305 |
| on | 2 | s0_m2 | 8002 | 73.8 | 0.2 | 0.0 | 467 | 508 |
| off | 1 | s0_m2 | 8001 | 93.1 | 0.0 | 0.0 | 246 | 727 |
| off | 2 | s0_m2 | 8001 | 91.8 | 0.0 | 0.0 | 280 | 687 |
| on | 1 | s1_A | 8003 | 0.0 | 66.6 | 0.0 | 1,646 | 3,249 |
| on | 2 | s1_A | 8002 | 0.0 | 76.0 | 0.0 | 1,730 | 3,039 |
| off | 1 | s1_A | 8001 | 6.9 | 75.7 | 0.0 | 1,565 | 3,104 |
| off | 2 | s1_A | 8001 | 15.9 | 72.9 | 0.3 | 1,560 | 3,160 |
| on | 1 | s2_m1 | 8003 | 35.1 | 28.7 | 0.5 | 1,359 | 2,043 |
| on | 2 | s2_m1 | 8002 | 0.0 | 28.0 | 0.0 | 1,561 | 1,828 |
| off | 1 | s2_m1 | 8001 | 71.9 | 2.5 | 0.0 | 925 | 2,159 |
| off | 2 | s2_m1 | 8000 | 75.7 | 1.3 | 0.4 | 828 | 2,241 |
| on | 1 | s3_B | 8000 | 0.0 | 87.1 | 0.0 | 1,450 | 4,021 |
| on | 2 | s3_B | 8002 | 0.0 | 82.8 | 0.0 | 1,516 | 3,977 |
| off | 1 | s3_B | 8000 | 17.6 | 76.4 | 0.1 | 1,404 | 3,940 |
| off | 2 | s3_B | 8002 | 29.3 | 74.3 | 0.1 | 1,401 | 3,957 |

The engine marked `(least chat)` is the candidate dedicated engine in that segment and arm.
The answer to the question is unambiguous, and it holds in both arms:

| segment | arm | deep research TTFT>10 s on the least-chat engine | on the other three engines |
|---|---|---|---|
| s1_A | on | 66.6%, 76.0% | 0.0%, 0.0% |
| s1_A | off | 75.7%, 72.9% | 0.0%, 0.3% |
| s2_m1 | on | 28.7%, 28.0% | 0.5%, 0.0% |
| s2_m1 | off | 2.5%, 1.3% | 0.0%, 0.4% |
| s3_B | on | 87.1%, 82.8% | 0.0%, 0.0% |
| s3_B | off | 76.4%, 74.3% | 0.1%, 0.1% |

Deep research misses its first-token budget on the engine that holds it and essentially never
misses it anywhere else. In the six run-segments that have a dedicated engine, meaning one
engine holding 61% or more of the deep research dispatched in that segment, the miss rate on it
is 57 to 764 times the rate on the other three, and in four of the six the other three record
zero. The comparison is within a single arm and segment, so it does not rest on any between-run
difference. The two `s2_m1` rows with the preference off are the exception, and they are the
case with no dedicated engine: deep research is spread at 11.7-21.4% per engine there and the
least-chat engine's 2.5% and 1.3% are the only elevated values in the whole off arm.

The same table carries the other half of the mechanism. On the concentrated engine deep
research's per-token time is 68.1-71.4 ms at p50, against 44.6-48.8 ms on the mixed engines of
the same run and segment. **The dedicated engine does run at the looser pace the theory
predicts.** Deep research's budget is 100 ms, so 70 ms costs nothing, whereas a mixed engine
that also holds chat is held near 47 ms. The concentration converts pace headroom that deep
research cannot use into queueing that it pays for.



The comparison also shows what the preference does and does not cause. In `s1_A` and `s3_B`
both arms have a concentrated engine, and the miss rates on it overlap between arms. The
difference between arms is the sharpness of the dedication, not its existence: the
preference-on engine takes 0.0% chat in three of four run-segments, while the preference-off
engine takes 6.9%, 15.9%, 17.6% and 29.3%. In `s2_m1` the arms differ in kind. With the
preference on, one engine takes 61.3% and 97.3% deep research; with it off, deep research is
spread at 11.7-21.4% across all four engines and no engine has a raised miss rate.

---

## (c) Deep research first-token and per-token distributions

### (c) deep research first-token and per-token distributions

| arm | rep | segment | answered dr | TTFT p50 s | TTFT p90 s | ITL p50 ms | ITL p90 ms | TTFT>10s % | ITL>100ms % |
|---|---|---|---|---|---|---|---|---|---|
| on | 1 | ALL | 14,738 | 1.47 | 16.22 | 48.5 | 70.5 | 18.7 | 0.0 |
| on | 2 | ALL | 14,626 | 1.56 | 16.59 | 48.1 | 71.7 | 20.6 | 0.0 |
| off | 1 | ALL | 14,070 | 1.49 | 16.05 | 47.9 | 69.8 | 16.2 | 0.0 |
| off | 2 | ALL | 14,114 | 1.49 | 15.87 | 47.6 | 70.2 | 15.7 | 0.0 |
| on | 1 | s0_m2 | 970 | 0.59 | 8.53 | 39.9 | 46.5 | 0.2 | 0.0 |
| on | 2 | s0_m2 | 975 | 0.64 | 8.80 | 40.5 | 47.0 | 0.1 | 0.0 |
| off | 1 | s0_m2 | 973 | 0.84 | 9.07 | 41.3 | 46.5 | 0.0 | 0.0 |
| off | 2 | s0_m2 | 967 | 0.82 | 8.98 | 41.2 | 46.5 | 0.0 | 0.0 |
| on | 1 | s1_A | 4,895 | 1.83 | 17.07 | 48.4 | 71.3 | 22.4 | 0.0 |
| on | 2 | s1_A | 4,769 | 2.30 | 17.33 | 48.8 | 72.0 | 27.6 | 0.0 |
| off | 1 | s1_A | 4,669 | 1.97 | 17.15 | 48.5 | 70.9 | 25.4 | 0.0 |
| off | 2 | s1_A | 4,720 | 1.95 | 16.91 | 48.2 | 71.1 | 24.3 | 0.0 |
| on | 1 | s2_m1 | 3,402 | 1.21 | 11.56 | 51.4 | 70.0 | 11.8 | 0.0 |
| on | 2 | s2_m1 | 3,389 | 1.14 | 12.46 | 46.8 | 71.0 | 12.9 | 0.0 |
| off | 1 | s2_m1 | 3,084 | 1.18 | 8.44 | 44.8 | 56.4 | 0.8 | 0.0 |
| off | 2 | s2_m1 | 3,069 | 1.20 | 8.65 | 45.0 | 51.4 | 0.7 | 0.0 |
| on | 1 | s3_B | 5,471 | 1.62 | 17.36 | 48.8 | 70.6 | 23.1 | 0.0 |
| on | 2 | s3_B | 5,493 | 1.57 | 17.28 | 48.2 | 72.4 | 22.8 | 0.0 |
| off | 1 | s3_B | 5,344 | 1.52 | 16.91 | 48.7 | 70.4 | 20.1 | 0.0 |
| off | 2 | s3_B | 5,358 | 1.52 | 16.76 | 48.3 | 71.5 | 19.5 | 0.0 |

**chat, for contrast** (budget TTFT 5 s, pace 50 ms)

| arm | rep | segment | answered chat | TTFT p50 s | TTFT p90 s | ITL p50 ms | ITL p90 ms |
|---|---|---|---|---|---|---|---|
| on | 1 | ALL | 57,277 | 0.47 | 1.36 | 44.4 | 48.4 |
| on | 2 | ALL | 57,528 | 0.45 | 1.34 | 44.5 | 48.2 |
| off | 1 | ALL | 57,205 | 0.64 | 1.60 | 44.6 | 48.9 |
| off | 2 | ALL | 57,738 | 0.65 | 1.68 | 44.7 | 48.8 |
| on | 1 | s0_m2 | 20,233 | 0.39 | 1.40 | 41.5 | 45.8 |
| on | 2 | s0_m2 | 20,142 | 0.39 | 1.66 | 41.1 | 46.1 |
| off | 1 | s0_m2 | 20,035 | 0.54 | 2.08 | 39.9 | 46.4 |
| off | 2 | s0_m2 | 20,032 | 0.55 | 2.16 | 39.9 | 46.4 |
| on | 1 | s1_A | 6,873 | 0.69 | 2.20 | 46.5 | 49.4 |
| on | 2 | s1_A | 6,617 | 0.69 | 2.29 | 47.1 | 49.5 |
| off | 1 | s1_A | 6,588 | 0.77 | 2.23 | 46.9 | 49.9 |
| off | 2 | s1_A | 6,650 | 0.76 | 2.28 | 46.9 | 50.0 |
| on | 1 | s2_m1 | 16,837 | 0.40 | 1.06 | 43.9 | 46.4 |
| on | 2 | s2_m1 | 16,909 | 0.38 | 0.98 | 43.9 | 46.5 |
| off | 1 | s2_m1 | 17,406 | 0.63 | 1.37 | 44.2 | 47.4 |
| off | 2 | s2_m1 | 17,341 | 0.66 | 1.46 | 44.3 | 48.4 |
| on | 1 | s3_B | 13,334 | 0.62 | 1.39 | 47.4 | 49.6 |
| on | 2 | s3_B | 13,860 | 0.55 | 1.28 | 46.9 | 48.9 |
| off | 1 | s3_B | 13,176 | 0.70 | 1.37 | 46.9 | 49.7 |
| off | 2 | s3_B | 13,715 | 0.69 | 1.41 | 46.7 | 49.4 |

Over the whole hour the hypothesis is only weakly supported. Deep research TTFT p90 is 16.22 s
and 16.59 s with the preference on against 16.05 s and 15.87 s with it off; the difference of
means is 0.45 s and the larger repeat spread is 0.37 s, so the movement is barely outside the
noise. The share above 10 s moves from 15.7% and 16.2% off to 18.7% and 20.6% on, a difference
of 3.7 points against a repeat spread of 1.9 points. Per-token time does not move at all
over the hour: p50 47.6-48.5 ms and p90 69.8-72.4 ms in every run, with no arm separation.

The hour-level average hides the effect because two of the four segments concentrate deep
research in both arms. In `s2_m1` the separation is clean: TTFT p90 is 11.56 s and 12.46 s on
against 8.44 s and 8.65 s off, and the share above 10 s is 11.8% and 12.9% on against 0.8% and
0.7% off. Deep research's per-token time in that segment is also *worse* with the preference on
(p50 51.4 and 46.8 ms against 44.8 and 45.0 ms; p90 70.0 and 71.0 ms against 56.4 and 51.4 ms).
**The prediction that the preference improves deep research's per-token number is refuted.**
Deep research gains nothing on pace, because it was never near its 100 ms budget in either arm.
The per-token gain accrues to chat, which shares the fleet with a class that has been moved off
it.

### chat per-token latency against its 50 ms budget (answered chat only)

| arm | rep | segment | n | ITL p50 | p90 | p95 | p99 | share >50 ms |
|---|---|---|---|---|---|---|---|---|
| on | 1 | ALL | 57,031 | 44.4 | 48.4 | 49.3 | 52.0 | 2.87 |
| on | 2 | ALL | 57,284 | 44.5 | 48.2 | 49.0 | 52.0 | 2.27 |
| off | 1 | ALL | 56,961 | 44.6 | 48.9 | 50.1 | 63.5 | 5.28 |
| off | 2 | ALL | 57,495 | 44.7 | 48.8 | 50.3 | 63.8 | 5.62 |
| on | 1 | s0_m2 | 20,158 | 41.5 | 45.8 | 46.7 | 49.9 | 0.95 |
| on | 2 | s0_m2 | 20,067 | 41.1 | 46.1 | 46.9 | 49.5 | 0.86 |
| off | 1 | s0_m2 | 19,960 | 39.9 | 46.4 | 47.1 | 49.7 | 0.96 |
| off | 2 | s0_m2 | 19,957 | 39.9 | 46.4 | 47.2 | 51.0 | 1.26 |
| on | 1 | s1_A | 6,838 | 46.5 | 49.4 | 50.1 | 54.8 | 5.63 |
| on | 2 | s1_A | 6,584 | 47.1 | 49.5 | 50.7 | 57.7 | 6.76 |
| off | 1 | s1_A | 6,553 | 46.9 | 49.9 | 51.1 | 64.8 | 9.20 |
| off | 2 | s1_A | 6,616 | 46.9 | 50.0 | 51.3 | 66.8 | 9.76 |
| on | 1 | s2_m1 | 16,757 | 43.9 | 46.4 | 47.1 | 49.4 | 0.80 |
| on | 2 | s2_m1 | 16,830 | 43.9 | 46.5 | 47.2 | 49.7 | 0.89 |
| off | 1 | s2_m1 | 17,323 | 44.2 | 47.4 | 53.5 | 68.0 | 7.06 |
| off | 2 | s2_m1 | 17,263 | 44.3 | 48.4 | 53.5 | 67.7 | 7.84 |
| on | 1 | s3_B | 13,278 | 47.4 | 49.6 | 50.4 | 54.2 | 6.97 |
| on | 2 | s3_B | 13,803 | 46.9 | 48.9 | 49.6 | 53.4 | 3.88 |
| off | 1 | s3_B | 13,125 | 46.9 | 49.7 | 50.8 | 68.3 | 7.56 |
| off | 2 | s3_B | 13,659 | 46.7 | 49.4 | 51.2 | 67.1 | 7.20 |

Chat's per-token distribution sits directly against its budget, which is why the preference
changes so many chat verdicts while barely moving the median. In `s2_m1` chat p50 is 43.9 ms
with the preference on and 44.2-44.3 ms with it off, a difference of 0.35 ms. At p95 the same
comparison is 47.1-47.2 ms against 53.5 ms, and the share above 50 ms is 0.80% and 0.89%
against 7.06% and 7.84%. A distribution packed within 3 ms of its threshold turns a small shift
in the tail into thousands of verdicts, so the per-token benefit must be read at p95 and at the
threshold crossing rate, not at the median.

---

## (d) Queued prefill per engine, and where it lands

### (d) queued prefill tokens per engine (`instance_cms_all_prefills_tokens_num`)

stale instance ids (counter identically zero for the whole run) are dropped:

- preference on repeat 1: 4 live ids -> ports [8000, 8001, 8002, 8003], 4 stale ids dropped, 3,726 scrapes
- preference on repeat 2: 4 live ids -> ports [8000, 8001, 8002, 8003], 4 stale ids dropped, 3,729 scrapes
- preference off repeat 1: 4 live ids -> ports [8000, 8001, 8002, 8003], 4 stale ids dropped, 3,725 scrapes
- preference off repeat 2: 4 live ids -> ports [8000, 8001, 8002, 8003], 4 stale ids dropped, 3,727 scrapes

| arm | rep | segment | engine | prefill p50 tok | p90 tok | p99 tok | max tok | share of segment scrapes >8192 tok |
|---|---|---|---|---|---|---|---|---|
| on | 1 | s0_m2 | 8000 | 0 | 777 | 2,750 | 5,001 | 0.0 |
| on | 1 | s0_m2 | 8001 | 0 | 511 | 7,191 | 16,612 | 0.7 |
| on | 1 | s0_m2 | 8002 | 0 | 1,168 | 7,034 | 11,201 | 0.6 |
| on | 1 | s0_m2 | 8003 | 0 | 554 | 7,412 | 10,439 | 0.4 |
| on | 2 | s0_m2 | 8000 | 0 | 959 | 3,487 | 11,960 | 0.1 |
| on | 2 | s0_m2 | 8001 | 0 | 367 | 9,211 | 13,075 | 1.7 |
| on | 2 | s0_m2 | 8002 | 0 | 480 | 8,341 | 15,407 | 1.1 |
| on | 2 | s0_m2 | 8003 | 0 | 1,146 | 3,108 | 7,673 | 0.0 |
| off | 1 | s0_m2 | 8000 | 0 | 908 | 5,712 | 11,530 | 0.4 |
| off | 1 | s0_m2 | 8001 | 0 | 1,008 | 5,150 | 11,559 | 0.3 |
| off | 1 | s0_m2 | 8002 | 0 | 629 | 6,806 | 10,859 | 0.3 |
| off | 1 | s0_m2 | 8003 | 0 | 1,257 | 6,754 | 14,819 | 0.4 |
| off | 2 | s0_m2 | 8000 | 0 | 1,195 | 5,441 | 12,091 | 0.6 |
| off | 2 | s0_m2 | 8001 | 0 | 1,577 | 7,129 | 10,576 | 0.6 |
| off | 2 | s0_m2 | 8002 | 0 | 915 | 4,437 | 8,883 | 0.2 |
| off | 2 | s0_m2 | 8003 | 0 | 1,011 | 4,784 | 10,772 | 0.3 |
| on | 1 | s1_A | 8000 | 0 | 2,090 | 8,431 | 12,932 | 1.1 |
| on | 1 | s1_A | 8001 | 0 | 3,095 | 16,704 | 34,571 | 3.1 |
| on | 1 | s1_A | 8002 | 0 | 2,710 | 10,201 | 27,514 | 1.9 |
| on | 1 | s1_A | 8003 | 82,472 | 99,597 | 105,840 | 109,778 | 74.1 |
| on | 2 | s1_A | 8000 | 0 | 2,200 | 9,363 | 21,091 | 1.7 |
| on | 2 | s1_A | 8001 | 0 | 2,491 | 10,421 | 18,831 | 1.8 |
| on | 2 | s1_A | 8002 | 88,511 | 101,634 | 105,739 | 108,188 | 91.4 |
| on | 2 | s1_A | 8003 | 0 | 1,789 | 7,309 | 21,294 | 0.8 |
| off | 1 | s1_A | 8000 | 0 | 2,180 | 7,919 | 18,687 | 1.0 |
| off | 1 | s1_A | 8001 | 85,873 | 100,273 | 107,561 | 111,837 | 75.9 |
| off | 1 | s1_A | 8002 | 0 | 2,542 | 9,375 | 19,504 | 1.7 |
| off | 1 | s1_A | 8003 | 0 | 2,209 | 9,277 | 18,859 | 1.3 |
| off | 2 | s1_A | 8000 | 0 | 3,509 | 20,023 | 55,822 | 3.6 |
| off | 2 | s1_A | 8001 | 83,912 | 99,276 | 105,214 | 109,457 | 71.4 |
| off | 2 | s1_A | 8002 | 0 | 2,607 | 7,873 | 12,292 | 0.9 |
| off | 2 | s1_A | 8003 | 0 | 2,575 | 8,603 | 14,662 | 1.2 |
| on | 1 | s2_m1 | 8000 | 0 | 3,749 | 22,412 | 34,833 | 4.0 |
| on | 1 | s2_m1 | 8001 | 0 | 769 | 2,602 | 9,625 | 0.1 |
| on | 1 | s2_m1 | 8002 | 0 | 1,897 | 9,599 | 14,813 | 1.6 |
| on | 1 | s2_m1 | 8003 | 5,000 | 74,018 | 98,691 | 105,711 | 46.7 |
| on | 2 | s2_m1 | 8000 | 0 | 1,953 | 7,981 | 14,026 | 1.0 |
| on | 2 | s2_m1 | 8001 | 0 | 3,426 | 11,491 | 19,277 | 3.4 |
| on | 2 | s2_m1 | 8002 | 7,356 | 85,678 | 102,385 | 107,007 | 48.8 |
| on | 2 | s2_m1 | 8003 | 0 | 804 | 2,394 | 8,146 | 0.0 |
| off | 1 | s2_m1 | 8000 | 0 | 1,980 | 10,481 | 23,771 | 2.4 |
| off | 1 | s2_m1 | 8001 | 0 | 6,603 | 37,464 | 61,601 | 8.1 |
| off | 1 | s2_m1 | 8002 | 0 | 1,986 | 9,027 | 14,320 | 1.2 |
| off | 1 | s2_m1 | 8003 | 0 | 1,675 | 10,255 | 16,293 | 1.7 |
| off | 2 | s2_m1 | 8000 | 0 | 4,945 | 20,461 | 42,105 | 5.9 |
| off | 2 | s2_m1 | 8001 | 0 | 1,518 | 9,471 | 15,207 | 1.6 |
| off | 2 | s2_m1 | 8002 | 0 | 1,876 | 8,618 | 20,275 | 1.4 |
| off | 2 | s2_m1 | 8003 | 0 | 2,673 | 11,700 | 38,444 | 2.0 |
| on | 1 | s3_B | 8000 | 98,340 | 108,082 | 113,256 | 118,479 | 93.6 |
| on | 1 | s3_B | 8001 | 0 | 872 | 4,140 | 12,112 | 0.2 |
| on | 1 | s3_B | 8002 | 0 | 2,035 | 9,440 | 13,328 | 1.8 |
| on | 1 | s3_B | 8003 | 0 | 1,347 | 8,595 | 15,455 | 1.3 |
| on | 2 | s3_B | 8000 | 0 | 1,290 | 11,674 | 39,466 | 2.1 |
| on | 2 | s3_B | 8001 | 0 | 1,304 | 8,513 | 14,524 | 1.1 |
| on | 2 | s3_B | 8002 | 94,482 | 105,688 | 109,976 | 113,294 | 91.6 |
| on | 2 | s3_B | 8003 | 0 | 990 | 5,676 | 15,160 | 0.4 |
| off | 1 | s3_B | 8000 | 92,598 | 106,798 | 111,650 | 113,099 | 75.4 |
| off | 1 | s3_B | 8001 | 0 | 1,074 | 6,849 | 15,330 | 0.6 |
| off | 1 | s3_B | 8002 | 0 | 1,416 | 7,000 | 13,717 | 0.7 |
| off | 1 | s3_B | 8003 | 0 | 1,463 | 7,974 | 12,708 | 0.9 |
| off | 2 | s3_B | 8000 | 0 | 1,528 | 8,289 | 17,743 | 1.1 |
| off | 2 | s3_B | 8001 | 0 | 1,143 | 7,953 | 17,936 | 1.0 |
| off | 2 | s3_B | 8002 | 90,536 | 105,375 | 110,118 | 114,427 | 72.8 |
| off | 2 | s3_B | 8003 | 0 | 1,391 | 6,026 | 13,680 | 0.4 |

**does the queued prefill concentrate on the same engine the deep-research class concentrates on**

| arm | rep | segment | engine with most queued prefill | its share of fleet prefill-token-seconds | engine with least chat | engine with most dr | dr share on the top-prefill engine |
|---|---|---|---|---|---|---|---|
| on | 1 | s0_m2 | 8002 | 32.1 | 8001 | 8001 | 2.7 |
| on | 2 | s0_m2 | 8001 | 32.2 | 8002 | 8002 | 15.5 |
| off | 1 | s0_m2 | 8003 | 28.8 | 8001 | 8003 | 4.7 |
| off | 2 | s0_m2 | 8001 | 30.9 | 8001 | 8001 | 5.8 |
| on | 1 | s1_A | 8003 | 96.1 | 8003 | 8003 | 83.7 |
| on | 2 | s1_A | 8002 | 97.4 | 8002 | 8002 | 99.7 |
| off | 1 | s1_A | 8001 | 96.9 | 8001 | 8001 | 85.9 |
| off | 2 | s1_A | 8001 | 95.9 | 8001 | 8001 | 72.7 |
| on | 1 | s2_m1 | 8003 | 92.0 | 8003 | 8003 | 61.3 |
| on | 2 | s2_m1 | 8002 | 94.4 | 8002 | 8002 | 97.3 |
| off | 1 | s2_m1 | 8001 | 52.7 | 8001 | 8001 | 21.4 |
| off | 2 | s2_m1 | 8000 | 41.0 | 8000 | 8000 | 18.2 |
| on | 1 | s3_B | 8000 | 98.4 | 8000 | 8003 | 100.0 |
| on | 2 | s3_B | 8002 | 98.1 | 8002 | 8002 | 100.0 |
| off | 1 | s3_B | 8000 | 98.0 | 8000 | 8000 | 79.6 |
| off | 2 | s3_B | 8002 | 97.8 | 8002 | 8002 | 67.6 |

The four dropped ids listed above report this counter as identically zero for all ~3,725
scrapes of the run. They are the stale registrations left by the engine restart. The four live
ids map one-to-one onto ports 8000-8003 through `analysis/request_engine.csv`.

The distribution of queued prefill is bimodal by engine, not by arm. A concentrated engine sits at 82,000-98,000
queued prefill tokens at p50 and 99,000-108,000 at p90. Every other engine sits at 0 tokens at
p50 and 367-6,603 at p90. At an 8,192-token prefill chunk the concentrated engine is carrying
ten to thirteen chunks of queued prefill and the others are carrying less than one.



The prefill backlog lands on exactly the engine the class lands on. In every one of the twelve
run-segments outside `s0_m2`, the engine holding the most queued prefill is the engine holding
the least chat, and it holds 92.0-98.4% of the fleet's prefill-token-seconds. In `s0_m2`, where
deep research is 4.6% of arrivals and no engine is dedicated in either arm, the top engine holds
28.8-32.2%, which is what an even split of four would give.

The arms differ in `s2_m1` and in how much of the segment the backlog persists. With the
preference on, the top engine holds 92.0% and 94.4% of `s2_m1` prefill-token-seconds and exceeds
8,192 queued tokens in 46.7% and 48.8% of scrapes. With it off, the top engine holds 52.7% and
41.0% and exceeds 8,192 tokens in 8.1% and 5.9% of scrapes. The concentration in `s2_m1` is
therefore intermittent even with the preference on, present for roughly half the segment; in
`s1_A` and `s3_B` it is present in 71.4-93.6% of scrapes in both arms.

### The first-token loss is engine queueing, not prefill compute

The engine's own counters separate the two. `vllm:request_queue_time_seconds` is the wait before
the engine starts a request and `vllm:request_prefill_time_seconds` is the prefill itself.

### engine-reported first-token time, queue time and prefill time, per engine per segment

Deltas of the vLLM cumulative counters between the segment endpoints; the mean is over
every request that engine finished prefilling in the segment, all classes together.

| arm | rep | segment | engine | requests | engine TTFT mean s | engine queue-wait mean s | engine prefill mean s | waiting-queue p90 |
|---|---|---|---|---|---|---|---|---|
| on | 1 | s0_m2 | 8000 | 8,461 | 0.19 | 0.04 | 0.15 | 2 |
| on | 1 | s0_m2 | 8001 | 2,580 | 0.44 | 0.09 | 0.35 | 1 |
| on | 1 | s0_m2 | 8002 | 5,318 | 0.27 | 0.06 | 0.21 | 1 |
| on | 1 | s0_m2 | 8003 | 5,263 | 0.24 | 0.05 | 0.18 | 1 |
| on | 1 | s1_A | 8000 | 5,080 | 0.35 | 0.08 | 0.27 | 2 |
| on | 1 | s1_A | 8001 | 5,066 | 0.37 | 0.09 | 0.28 | 2 |
| on | 1 | s1_A | 8002 | 4,138 | 0.40 | 0.09 | 0.30 | 1 |
| on | 1 | s1_A | 8003 | 1,948 | 7.85 | 7.14 | 0.59 | 31 |
| on | 1 | s2_m1 | 8000 | 3,683 | 0.44 | 0.11 | 0.33 | 2 |
| on | 1 | s2_m1 | 8001 | 10,904 | 0.18 | 0.04 | 0.14 | 2 |
| on | 1 | s2_m1 | 8002 | 4,552 | 0.39 | 0.09 | 0.30 | 1 |
| on | 1 | s2_m1 | 8003 | 2,236 | 2.41 | 2.38 | 0.45 | 15 |
| on | 1 | s3_B | 8000 | 1,599 | 11.59 | 10.52 | 0.69 | 28 |
| on | 1 | s3_B | 8001 | 8,646 | 0.29 | 0.06 | 0.22 | 2 |
| on | 1 | s3_B | 8002 | 5,047 | 0.44 | 0.09 | 0.34 | 2 |
| on | 1 | s3_B | 8003 | 5,260 | 0.42 | 0.09 | 0.32 | 2 |
| on | 2 | s0_m2 | 8000 | 7,822 | 0.20 | 0.04 | 0.16 | 1 |
| on | 2 | s0_m2 | 8001 | 2,598 | 0.43 | 0.09 | 0.34 | 0 |
| on | 2 | s0_m2 | 8002 | 2,532 | 0.41 | 0.08 | 0.32 | 0 |
| on | 2 | s0_m2 | 8003 | 8,582 | 0.19 | 0.04 | 0.15 | 2 |
| on | 2 | s1_A | 8000 | 5,013 | 0.36 | 0.08 | 0.28 | 2 |
| on | 2 | s1_A | 8001 | 3,810 | 0.41 | 0.08 | 0.32 | 1 |
| on | 2 | s1_A | 8002 | 1,718 | 10.38 | 9.87 | 0.68 | 29 |
| on | 2 | s1_A | 8003 | 5,505 | 0.32 | 0.07 | 0.24 | 2 |
| on | 2 | s2_m1 | 8000 | 5,009 | 0.33 | 0.08 | 0.25 | 2 |
| on | 2 | s2_m1 | 8001 | 3,779 | 0.40 | 0.09 | 0.31 | 1 |
| on | 2 | s2_m1 | 8002 | 1,619 | 3.76 | 3.51 | 0.60 | 18 |
| on | 2 | s2_m1 | 8003 | 11,031 | 0.19 | 0.04 | 0.15 | 2 |
| on | 2 | s3_B | 8000 | 7,073 | 0.33 | 0.07 | 0.25 | 3 |
| on | 2 | s3_B | 8001 | 5,106 | 0.39 | 0.08 | 0.30 | 2 |
| on | 2 | s3_B | 8002 | 1,668 | 10.83 | 10.39 | 0.70 | 28 |
| on | 2 | s3_B | 8003 | 7,272 | 0.28 | 0.06 | 0.21 | 2 |
| off | 1 | s0_m2 | 8000 | 5,369 | 0.27 | 0.06 | 0.21 | 1 |
| off | 1 | s0_m2 | 8001 | 5,274 | 0.29 | 0.07 | 0.22 | 1 |
| off | 1 | s0_m2 | 8002 | 5,405 | 0.29 | 0.07 | 0.23 | 1 |
| off | 1 | s0_m2 | 8003 | 5,392 | 0.27 | 0.06 | 0.21 | 1 |
| off | 1 | s1_A | 8000 | 4,840 | 0.37 | 0.08 | 0.29 | 2 |
| off | 1 | s1_A | 8001 | 1,805 | 8.88 | 8.01 | 0.62 | 33 |
| off | 1 | s1_A | 8002 | 4,615 | 0.38 | 0.08 | 0.30 | 2 |
| off | 1 | s1_A | 8003 | 4,730 | 0.37 | 0.08 | 0.28 | 2 |
| off | 1 | s2_m1 | 8000 | 5,708 | 0.35 | 0.08 | 0.27 | 2 |
| off | 1 | s2_m1 | 8001 | 4,331 | 0.48 | 0.35 | 0.34 | 3 |
| off | 1 | s2_m1 | 8002 | 5,721 | 0.36 | 0.08 | 0.28 | 2 |
| off | 1 | s2_m1 | 8003 | 5,977 | 0.34 | 0.08 | 0.26 | 2 |
| off | 1 | s3_B | 8000 | 1,861 | 8.55 | 7.72 | 0.64 | 28 |
| off | 1 | s3_B | 8001 | 6,219 | 0.37 | 0.08 | 0.28 | 2 |
| off | 1 | s3_B | 8002 | 6,071 | 0.39 | 0.08 | 0.30 | 3 |
| off | 1 | s3_B | 8003 | 6,132 | 0.38 | 0.08 | 0.29 | 3 |
| off | 2 | s0_m2 | 8000 | 5,370 | 0.28 | 0.06 | 0.21 | 1 |
| off | 2 | s0_m2 | 8001 | 4,860 | 0.30 | 0.07 | 0.23 | 2 |
| off | 2 | s0_m2 | 8002 | 5,441 | 0.29 | 0.06 | 0.23 | 2 |
| off | 2 | s0_m2 | 8003 | 5,753 | 0.27 | 0.06 | 0.20 | 2 |
| off | 2 | s1_A | 8000 | 4,543 | 0.39 | 0.09 | 0.29 | 2 |
| off | 2 | s1_A | 8001 | 2,138 | 7.05 | 6.24 | 0.55 | 32 |
| off | 2 | s1_A | 8002 | 4,560 | 0.40 | 0.08 | 0.31 | 2 |
| off | 2 | s1_A | 8003 | 4,727 | 0.37 | 0.08 | 0.29 | 2 |
| off | 2 | s2_m1 | 8000 | 4,539 | 0.44 | 0.10 | 0.33 | 2 |
| off | 2 | s2_m1 | 8001 | 5,867 | 0.36 | 0.21 | 0.28 | 2 |
| off | 2 | s2_m1 | 8002 | 5,749 | 0.36 | 0.08 | 0.28 | 2 |
| off | 2 | s2_m1 | 8003 | 5,495 | 0.37 | 0.08 | 0.29 | 2 |
| off | 2 | s3_B | 8000 | 6,057 | 0.39 | 0.08 | 0.30 | 3 |
| off | 2 | s3_B | 8001 | 6,128 | 0.37 | 0.08 | 0.29 | 2 |
| off | 2 | s3_B | 8002 | 2,179 | 6.85 | 6.08 | 0.57 | 29 |
| off | 2 | s3_B | 8003 | 6,402 | 0.36 | 0.08 | 0.28 | 2 |

On a concentrated engine the mean queue wait is 2.38 s to 10.52 s while the mean prefill time is
0.45 s to 0.70 s. On a mixed engine in the same run and segment the queue wait is 0.04 s to
0.11 s and prefill is 0.14 s to 0.34 s. Prefill compute per request rises by at most a factor of
four on the concentrated engine; the wait to start rises by a factor of 30 to 130. The waiting
queue depth at p90 tells the same story: 15 to 33 requests on the concentrated engine against 0
to 3 elsewhere. **The first-token loss is a queue in front of the engine, produced by ten or more
chunks of prefill already admitted, not by any single prompt being expensive to prefill.**

Client-observed and engine-observed first-token times do not fully reconcile on the concentrated
engine: client TTFT p50 is 15.3-16.6 s where the engine reports a mean of 7.9-11.6 s. Part of the
gap is a median-against-mean comparison and part is time before the scheduler dispatched the
request. I did not measure the pre-dispatch hold per request, so the split between gateway-side
holding and engine-side queueing is bounded rather than resolved: engine queueing accounts for at
least 2.38 s to 10.52 s of it.

---

## What the trade costs and buys, in requests

### the segment where the arms differ: s2_m1 miss counts by category

| class | arm | rep | arrivals | rejected | TTFT-only | pace-only | both | e2e | met |
|---|---|---|---|---|---|---|---|---|---|
| chat | on | 1 | 18,562 | 1,725 | 0 | 134 | 0 | - | 16,703 |
| chat | on | 2 | 18,569 | 1,660 | 0 | 149 | 0 | - | 16,760 |
| chat | off | 1 | 18,568 | 1,162 | 4 | 1,209 | 14 | - | 16,179 |
| chat | off | 2 | 18,567 | 1,226 | 2 | 1,350 | 3 | - | 15,986 |
| deepresearch | on | 1 | 3,718 | 316 | 400 | 1 | 0 | - | 3,001 |
| deepresearch | on | 2 | 3,714 | 325 | 437 | 1 | 0 | - | 2,951 |
| deepresearch | off | 1 | 3,715 | 631 | 23 | 0 | 1 | - | 3,060 |
| deepresearch | off | 2 | 3,717 | 648 | 21 | 1 | 0 | - | 3,047 |
| swe | on | 1 | 1,860 | 734 | - | - | - | 65 | 1,061 |
| swe | on | 2 | 1,859 | 726 | - | - | - | 91 | 1,042 |
| swe | off | 1 | 1,858 | 605 | - | - | - | 140 | 1,113 |
| swe | off | 2 | 1,863 | 611 | - | - | - | 146 | 1,106 |

Taking the mean of the two repeats in `s2_m1`, over 24,140 arrivals:

| movement | requests, preference on minus off |
|---|---|
| chat per-token misses | -1,148 |
| chat rejections | +499 |
| deep research first-token misses | +397 |
| deep research rejections | -319 |
| agent end-to-end misses | -65 |
| agent rejections | +122 |
| **total misses** | **-518** |

### per-request offered attainment, whole hour and per segment (denominator = every arrival)

| arm | rep | whole hour | s0_m2 | s1_A | s2_m1 | s3_B |
|---|---|---|---|---|---|---|
| on | 1 | 75.3 | 97.4 | 62.1 | 86.0 | 60.0 |
| on | 2 | 75.7 | 97.0 | 59.8 | 86.0 | 63.3 |
| off | 1 | 73.9 | 96.5 | 59.7 | 84.3 | 59.5 |
| off | 2 | 74.2 | 96.1 | 59.8 | 83.4 | 61.2 |

### per-class offered attainment per segment

| class | segment | on r1 | on r2 | off r1 | off r2 | mean on | mean off | diff | max repeat spread |
|---|---|---|---|---|---|---|---|---|---|
| chat | s0_m2 | 98.0 | 97.6 | 97.0 | 96.7 | 97.8 | 96.9 | +0.9 | 0.4 |
| chat | s1_A | 86.3 | 82.2 | 79.7 | 79.9 | 84.3 | 79.8 | +4.4 | 4.1 |
| chat | s2_m1 | 90.0 | 90.3 | 87.1 | 86.1 | 90.1 | 86.6 | +3.5 | 1.0 |
| chat | s3_B | 70.0 | 75.2 | 68.8 | 71.9 | 72.6 | 70.3 | +2.3 | 5.2 |
| deepresearch | s0_m2 | 94.6 | 95.2 | 95.2 | 94.5 | 94.9 | 94.9 | +0.1 | 0.7 |
| deepresearch | s1_A | 50.7 | 46.1 | 46.5 | 47.6 | 48.4 | 47.1 | +1.3 | 4.6 |
| deepresearch | s2_m1 | 80.7 | 79.5 | 82.4 | 82.0 | 80.1 | 82.2 | -2.1 | 1.3 |
| deepresearch | s3_B | 48.3 | 48.6 | 48.9 | 49.5 | 48.5 | 49.2 | -0.7 | 0.6 |
| swe | s0_m2 | 79.5 | 77.7 | 77.5 | 76.4 | 78.6 | 77.0 | +1.6 | 1.8 |
| swe | s1_A | 49.4 | 51.2 | 52.9 | 51.7 | 50.3 | 52.3 | -2.0 | 1.8 |
| swe | s2_m1 | 57.0 | 56.1 | 59.9 | 59.4 | 56.5 | 59.6 | -3.1 | 1.0 |
| swe | s3_B | 34.6 | 35.3 | 34.9 | 32.1 | 35.0 | 33.5 | +1.5 | 2.8 |

The whole-hour per-request offered attainment is 75.3 and 75.7 with the preference on against
73.9 and 74.2 with it off. That reproduces the 1.4-point figure already on record and remains
below the pre-registered 3-point threshold. The segment breakdown says where the 1.4 points come
from: `s2_m1` contributes +2.15 points on a repeat spread of at most 0.9, and the other three
segments contribute +0.4 to +1.15 on repeat spreads of 0.4 to 2.3. **Only `s2_m1` clears its own
repeat spread**, and `s2_m1` is the segment where the preference, and not the mix, creates the
dedicated engine.

Per class the cost falls on agent, not on deep research: agent loses 2.0 points in `s1_A` and 3.1
points in `s2_m1` against repeat spreads of 1.8 and 1.0. Deep research loses 2.1 points in
`s2_m1` (spread 1.3) and is unchanged elsewhere. Chat gains 3.5 points in `s2_m1` (spread 1.0)
and 4.4 points in `s1_A`, though the `s1_A` gain has a 4.1-point repeat spread and should not be
read as a difference.

---

## Verdict on the hypothesis

1. **The conversion happens, and it is measurable.** Concentrating deep research raises its
   first-token miss rate on the receiving engine from under 0.5% to 28-87%, and lowers chat's
   per-token threshold crossings on the rest of the fleet by a factor of 8.7 in the segment where
   only the preference produces the concentration.
2. **The direction predicted for deep research itself is wrong.** The preference does not improve
   deep research's per-token number. Deep research runs at 68-71 ms on the dedicated engine
   against 45-48 ms on mixed engines, which is slower, and it is inside its 100 ms budget either
   way. The pace budget that gets freed is used by nobody. What improves is chat's pace, because
   chat is what remains on the other engines.
3. **The preference is not the only thing that concentrates.** In the two segments where deep
   research is 30-33% of arrivals, the feasibility test concentrates it without the preference,
   and the first-token penalty appears in both arms at the same magnitude.
4. **The net is positive but small, and it is one segment.** 518 fewer misses out of 24,140
   arrivals in `s2_m1`; nothing outside repeat spread in the other three.

## What I did not verify

- **The flag itself.** The scheduler start-up line that prints the policy configuration is not in
  the retained `scheduler_dispatch.log` of any of the four runs, so arm identity rests on the
  directory name and the driver. The behaviour is consistent with the labels: the
  preference-on runs produce a 0.0%-chat engine in three of four run-segments and the
  preference-off runs never do.
- **Per-class engine counters.** `vllm:request_queue_time_seconds` and the other engine counters
  are fleet-wide per engine and cannot be split by class. On a dedicated engine that is 97-100%
  deep research the attribution is safe; on a mixed engine it is not, so no per-class claim rests
  on those columns.
- **The gateway-side hold.** See the reconciliation gap above. I did not join per-request dispatch
  timestamps, so I cannot state what fraction of a deep research request's 16 s first-token time
  was spent before dispatch.
- **Whether the concentrated engine keeps its identity within a segment.** Concentration is
  measured per 15-minute segment. The `s2_m1` scrape share above 8,192 tokens (46.7% and 48.8%)
  shows the backlog is intermittent within that segment, but I did not test whether it moves
  between engines while a segment runs. This repository has been caught before by pooling a
  concentration measure over a window in which the concentrated engine moved.
- **The 8,192-token chunk size.** Taken from the task statement, not read from the engine
  configuration.
- **Statistical significance.** Two repeats per arm. Every comparison above is stated against the
  observed repeat spread, which is the only dispersion estimate two repeats support.
- **KV capacity.** `instance_cms_kv_cache_usage_ratio_projected` was not used anywhere.


---

# Appendix: adversarial re-derivation (2026-08-24)

An independent check re-derived the headline from the four run directories without
importing `exp22_fluidserve.load_run`, `exp93_mix_shift.tag_segments` or
`exp41_engine_view.attribute_engines`. The checking script reads `metrics.csv` directly,
rebuilds the analysis window, the class labels, the corrected per-token time and the
segment tags from the plan JSON, and recomputes every quantity the claim rests on.
Only this appendix was written; nothing else on disk changed.

**Verdict: the headline survives.** The request-level numbers reproduce to the digit. One
defect was found, and it is confined to section (d): the engine-side time series are
segmented on a time origin 60 s earlier than the one the request-side tables use, so three
of the queued-prefill numbers quoted in the summary are wrong. The direction and the
conclusion of section (d) do not change.

## 1. The headline, re-derived

The independent route reproduces the four numbers the claim is built on, and the arrival
counts that carry them.

| quantity, `s2_m1` | on r1 | on r2 | off r1 | off r2 |
|---|---|---|---|---|
| arrivals (cutoffs excluded) | 24,140 | 24,142 | 24,141 | 24,147 |
| answered chat with a per-token time, n | 16,757 | 16,830 | 17,323 | 17,263 |
| share of those above the 50 ms budget, % | **0.80** | **0.89** | **7.06** | **7.84** |
| answered deep research, n | 3,402 | 3,389 | 3,084 | 3,069 |
| share of those above the 10 s budget, % | **11.76** | **12.89** | **0.78** | **0.68** |
| per-request offered attainment, % | **86.02** | **85.96** | **84.30** | **83.40** |
| total misses over all arrivals | 3,375 | 3,389 | 3,789 | 4,008 |

The net movement in misses is 3,382.0 − 3,898.5 = **−516.5**, against the −518 in the body;
the gap is rounding in the per-category means. Attainment moves +2.14 points, against +2.15
in the body. The on-arm repeat spread is 0.06 points and the off-arm spread is 0.90 points,
and the two arms do not overlap.

## 2. The failure modes this repository has actually hit, checked one at a time

**Two quantities with one name, built from different samples.** Found once, in section (d)
only. See part 3.

**A difference smaller than the repeat spread.** The chat movement is 6.6 points against an
off-arm spread of 0.78, and the deep research movement is 11.6 points against an on-arm
spread of 1.13. The attainment movement is 2.14 points against a spread of 0.90, which is a
factor of 2.4 and the weakest of the three. The claim states the ratios correctly.

**A mean where the quantity has a ceiling.** The headline quantities are threshold-crossing
shares and percentiles, not means. The engine-reported queue wait in section (d) is a mean,
but no headline number rests on it.

**A per-window ratio averaged instead of a total over a total.** The attainment figures are
total misses over total arrivals, recomputed that way here. The prefill share in section (d)
is a total over a total in the body; a mean of per-scrape ratios gives 60.7% instead of
85.0% for `on r1 s2_m1`, so the two aggregations differ by a large margin and the body used
the defensible one.

**PRERUN directories or stale zero instance ids.** No PRERUN directory exists for any of the
four runs. Each `scheduler.jsonl` carries eight instance ids for
`instance_cms_all_prefills_tokens_num`, and four of them are identically zero across all
~3,726 scrapes. Those four are the registrations from before the engine restart: their ids
begin `1787424423…`, while the four live ids begin `1787460117…` and map one-to-one onto
ports 8000-8003 through `analysis/request_engine.csv`. The body drops the right four.

**A filter that removes rejections from a comparison about rejection.** The threshold-crossing
shares exclude rejected requests, which is correct for a latency distribution but leaves the
denominator arm-dependent, because the preference-on arm rejects about 500 more chat requests
in `s2_m1`. Recomputing both quantities on the arrival denominator, where a rejection stays
in the denominator, changes nothing:

| `s2_m1`, arrival denominator | on r1 | on r2 | off r1 | off r2 |
|---|---|---|---|---|
| chat per-token misses / chat arrivals, % | 0.72 | 0.80 | 6.59 | 7.29 |
| deep research first-token misses / dr arrivals, % | 10.76 | 11.77 | 0.65 | 0.56 |

The related worry is that the preference-on arm wins by admitting less work. It does not.
In `s2_m1` it rejects more requests (11.50% and 11.23% against 9.93% and 10.29%) and still
carries more admitted prompt tokens (32.66M and 32.58M against 32.18M and 32.13M) and produces
more output tokens inside SLO (10.54M and 10.51M against 10.37M and 10.24M). The arm trades
chat rejections for deep research admissions, and the admitted load goes up rather than down.

**An hour-level aggregate of a quantity whose target moves.** The body already confines the
claim to `s2_m1` and states that the hour does not support it. Re-derivation agrees.

## 3. The defect: section (d) is segmented on a time origin 60 s early

The request-side tables tag a request by `start_time − t0`, where `t0` is the first arrival in
`metrics.csv`. Section (d) tags a scrape by a clock that runs 60 s behind that one. Sweeping
the offset recovers the body's numbers exactly at −60 s and only there. For `on r1`, engine
8003, the body reports `s1_A` p50 82,472 tokens with a 96.1% fleet share and 74.1% of scrapes
above 8,192 tokens, and `s2_m1` p50 5,000 tokens with a 92.0% share and 46.7% of scrapes above
8,192. At −60 s the check reproduces all six; at 0 s it produces 82,472 / 96.9 / 78.3 and
2,774 / 85.0 / 42.6. The same −60 s offset reproduces the engine-counter table: `s1_A` engine
8003 queue mean 7.14 s and `s2_m1` 2.38 s appear at −60 s, and 7.72 s and 1.94 s at 0 s.

`s1_A` and `s3_B` barely move, because the backlog there is present for 72-96% of the segment
and the median sits on a plateau. `s2_m1` moves, because the backlog there is intermittent and
the first minute of the segment still carries the tail of `s1_A`'s concentration.

Corrected values, on the same origin the request tables use:

| quantity, `s2_m1` | on r1 | on r2 | off r1 | off r2 |
|---|---|---|---|---|
| top-prefill engine's share of fleet prefill-token-seconds, % | 85.0 (was 92.0) | 93.5 (was 94.4) | 40.8 (was 52.7) | 40.6 (was 41.0) |
| queued prefill p50 on that engine, tok | 2,774 (was 5,000) | 4,844 (was 7,356) | 0 | 0 |
| scrapes above 8,192 queued tok, % | 42.6 (was 46.7) | 45.8 (was 48.8) | 6.2 (was 8.1) | 5.9 |
| engine-reported queue wait on that engine, s | 1.94 (was 2.38) | 3.15 (was 3.51) | 0.14 (was 0.35) | 0.10 |

| quantity, `s1_A` | on r1 | on r2 | off r1 | off r2 |
|---|---|---|---|---|
| queued prefill p50 on the concentrated engine, tok | 82,472 | 88,511 | 85,873 | 83,912 |
| its share of fleet prefill-token-seconds, % | 96.9 (was 96.1) | 97.6 (was 97.4) | 97.1 (was 96.9) | 96.3 (was 95.9) |
| engine-reported queue wait there, s | 7.72 (was 7.14) | 9.77 (was 9.87) | 8.53 (was 8.01) | 6.68 (was 6.24) |

The correction widens the `s2_m1` arm contrast in the prefill share (85.0/93.5 against
40.8/40.6, rather than 92.0/94.4 against 52.7/41.0) and narrows it in the median backlog. The
range quoted for the mechanism, "mean queue time 2.38-10.52 s on the concentrated engine",
becomes **1.94-11.44 s**. Every statement section (d) makes survives; three of its numbers do
not.

## 4. Two of the body's open items, closed

**Arm identity.** The body could not find the scheduler start-up line in any of the four
retained `scheduler_dispatch.log` files, and the check confirms it is absent: every one of
those files begins mid-run with `rescheduling_policy.go` lines. The line was read at run time
and recorded in `experiments/EXP-93_mix-shift-stress.md` §9.5, the section that reports the
preference-off repeats: it prints `affinity=false, affweight=0.00` with the other five flags,
including `prefix=true`, identical to the preference-on arm. That section also records the
whole-hour offered attainment as 75.3-75.7 on and 73.9-74.2 off, which matches this document's
table. Arm identity therefore rests on a start-up line that was read, not only on the directory
name.

**Whether the concentrated engine keeps its identity inside `s2_m1`.** Splitting the segment
into five three-minute windows and taking the top-prefill engine in each: preference-on repeat 1
gives 8003, 8003, 8003, 8003, 8000, and repeat 2 gives 8002 in all five. Preference-off repeat 1
alternates between 8001 and 8000 and never exceeds 69% in any window. The concentration in the
preference-on arm holds one engine for at least four of the five windows, so pooling it over the
segment does not create it. This is the failure the body flagged as untested, and it did not
occur.

## 5. Robustness of the headline to two window choices

`exp93_mix_shift.py` excludes 180 s on either side of a segment boundary; this document does
not. Both choices, and a split of the segment into halves, give the same answer.

| `s2_m1` window | attainment on (r1, r2) | attainment off (r1, r2) | chat >50 ms on / off | dr >10 s on / off |
|---|---|---|---|---|
| full, as in the body | 86.02, 85.96 | 84.30, 83.40 | 0.80, 0.89 / 7.06, 7.84 | 11.8, 12.9 / 0.8, 0.7 |
| 180 s trimmed each side | 80.86, 80.46 | 79.45, 78.48 | 0.98, 1.16 / 9.31, 9.91 | 17.3, 19.4 / 1.1, 0.8 |
| first half | 84.30, 84.51 | 82.94, 82.41 | 0.85, 0.84 / 6.88, 7.32 | 12.7, 14.4 / 1.2, 0.9 |
| second half | 88.03, 87.67 | 85.91, 84.56 | 0.74, 0.94 / 7.26, 8.43 | 10.7, 11.3 / 0.3, 0.4 |

## 6. Other checks that passed

- **The engine join.** `analysis/request_engine.csv` contains no duplicated
  `(task_id, call_index)` pair in these runs, because `task_id` carries the replay index, so
  the join ambiguity that affects other hour traces does not arise here. Its row counts,
  80,476 and 80,129, and the admitted non-cutoff request counts, 79,282 and 78,944, match the
  attribution denominators the body reports.
- **The `agent` column.** These four runs contain only `request` and `job_summary` rows; no
  `grace_cut` row exists, so the choice between the two denominators cannot move the counts.
- **Run-boundary cutoffs.** 236, 224, 218 and 227 requests are cut off at the end of the run,
  all of them in `s3_B`, and the body excludes them from every denominator. The counts differ
  by 18 requests across four runs, so the exclusion cannot favour an arm.

## 7. What the appendix does not settle

The gateway-side hold is still unmeasured, as the body says. The two-repeat design still
supports only a range, not a dispersion estimate. The 8,192-token chunk size is still taken
from the task statement rather than from engine configuration; nothing above depends on it,
since the `>8,192` column is reported alongside the raw p50 and p90 token counts.
