# A1: does the class preference build the structure the theory needs, and how completely?

EXP-93 mix-shift trace, four one-hour runs, two repeats per arm. Written 2026-08-24.

The theory under test: an instance's admissible per-token pace is the minimum nominal
budget among the requests resident on it, so a single chat request resident on an
instance holds that instance to 50 ms. Grouping classes should leave some instances
holding no chat, and those instances should be admissible at a looser pace. This
document measures whether that structure exists, how much of the fleet and how much of
the hour it covers, and what the resulting distribution of admissible paces is. It does
not measure whether the looser pace was converted into anything.

**Answer in one line.** The preference does build the structure, reproducibly, and it
builds it to the size the fleet allows: it converts 25.9% and 28.2% of engine-seconds
(two repeats, preference on) from the 50 ms chat pace to a looser pace, against 6.3% and
6.8% with the preference off. The difference of 20.5 percentage points is about nine
times the larger repeat spread of 2.25 points. The structure is capped near one engine in
four, its identity migrates two or three times per hour, and the freed engine carries only
about a fifth of the resident requests, so the fleet-mean admissible pace rises only from
52.8-53.3 ms to 60.4-61.4 ms.

---

## 0. Runs, validity, and how the quantities were built

| arm | repeat | directory | arrivals in window | admitted requests attributed to an engine |
|---|---|---|---|---|
| preference ON (`fspfx`) | r1 | `results/260822_2141_exp93r1_fspfx_shift` | 98,255 | 79,518 of 79,518 (100.0%) |
| preference ON (`fspfx`) | r2 | `results/260823_0007_exp93br1_fspfx_shift` | 98,231 | 79,822 of 79,822 (100.0%) |
| preference OFF (`fsnoaff`) | r1 | `results/260823_0721_exp93nr1_fsnoaff_shift` | 98,240 | 79,162 of 79,162 (100.0%) |
| preference OFF (`fsnoaff`) | r2 | `results/260823_0834_exp93nbr1_fsnoaff_shift` | 98,247 | 79,523 of 79,523 (100.0%) |

No directory whose name contains `PRERUN` was read.

The four runs saw the same arrivals to within 0.02% (98,231 to 98,255), and the realised
chat share per segment matches the plan in every run: 93.0% in `s0_m2` (plan 93.0%),
33.3-33.4% in `s1_A` (plan 33.3%), 76.9% in `s2_m1` (plan 76.9%), 60.0% in `s3_B` (plan
60.0%). Offered-denominator attainment reproduces the figure this task was given as
already measured: 75.3 and 75.7 with the preference on, 73.9 and 74.2 with it off, so
75.5 against 74.1 as stated.

Data path. `load_run` from `exp22_fluidserve` for one row per arrival,
`attribute_engines` from `exp41_engine_view` for the request-to-engine join, `read_plan`
and `tag_segments` from `exp93_mix_shift` for the segment column. Nothing was
reimplemented. The join matched 100.0% of admitted requests in all four runs, so no
segment or engine is under-represented.

**Stale instance ids.** `server_metrics/scheduler.jsonl` in each run carries eight
instance ids, of which four have `instance_cms_running_requests` equal to zero in all
3,726 scrapes and four are non-zero in 3,659-3,662 scrapes with maxima of 239-288 running
requests. The four all-zero ids are registrations left over from an engine restart. This
analysis keys on `engine_port` from the dispatch map, which contains only the four live
instances, so the stale ids cannot enter any number here.

**Residency.** The pace in section 3 needs to know which classes were resident on an
engine at a given moment, and the client's `metrics.csv` records arrival and completion
rather than dispatch and completion. Residency was therefore taken as the interval from
the client's `start_time` to its `end_time`, which begins earlier than true engine
residency by the gateway queueing delay. Two checks bound the error.

1. Against the scheduler's own counter. Comparing the client-derived resident count per
   engine with `instance_cms_running_requests` for the matching live instance id gives a
   Pearson correlation of 0.975 to 0.997 across all sixteen engine-run pairs, with the
   client count 1.7% to 13.6% higher, as expected from the queueing delay being included.
2. Against a lower bound. Recomputing every pace number with residency starting at first
   token instead of at arrival changes the whole-hour fraction of engine-seconds looser
   than 50 ms by at most 0.05 points (ON 25.90 to 25.93 and 28.15 to 28.20; OFF 6.30 to
   6.30 and 6.77 to 6.77). The conclusion is insensitive to the definition, because at
   this load another request of the same class is almost always resident anyway.

Idle engine-seconds are 0.05% of the 57,600 samples and idle engine-windows are 0.0% of
the 1,920 pairs, so nothing below is chat-free by virtue of being empty.

---

## 1. Effective number of instances each class is spread over

N_eff = 1 / sum of squared dispatch shares over the four engines, computed per 30 s
window and reported as the median over the 30 windows in each segment. Windows in which
a class received fewer than 8 requests are excluded; that removes 2 or 3 windows of 30
for swe in `s0_m2` and none anywhere else.

| arm | rep | segment | chat | deepresearch | swe |
|---|---|---|---|---|---|
| ON | r1 | s0_m2 | 2.00 | 1.00 | 1.00 |
| ON | r1 | s1_A | 2.30 | 3.69 | 2.85 |
| ON | r1 | s2_m1 | 2.08 | 2.56 | 1.91 |
| ON | r1 | s3_B | 2.49 | 3.77 | 2.51 |
| ON | r2 | s0_m2 | 2.00 | 1.00 | 1.00 |
| ON | r2 | s1_A | 2.24 | 3.54 | 2.78 |
| ON | r2 | s2_m1 | 2.06 | 3.02 | 1.97 |
| ON | r2 | s3_B | 2.46 | 3.71 | 2.44 |
| OFF | r1 | s0_m2 | 3.91 | 3.70 | 3.44 |
| OFF | r1 | s1_A | 2.96 | 3.82 | 3.00 |
| OFF | r1 | s2_m1 | 3.70 | 3.89 | 3.71 |
| OFF | r1 | s3_B | 2.98 | 3.95 | 2.96 |
| OFF | r2 | s0_m2 | 3.86 | 3.72 | 3.32 |
| OFF | r2 | s1_A | 2.95 | 3.89 | 2.99 |
| OFF | r2 | s2_m1 | 3.83 | 3.88 | 3.61 |
| OFF | r2 | s3_B | 2.99 | 3.95 | 2.93 |

Whole hour, median over all 120 windows: chat N_eff 2.24 and 2.19 with the preference on,
3.34 and 3.51 with it off. This reproduces the value this task was given as already
measured (2.0-2.5 on, 2.95-3.91 off) and confirms the pipeline.

The repeat spread is 0.02-0.46 for chat, 0.00-1.13 for deepresearch (the one large value
is ON `s2_m1`, 2.56 against 3.02), and 0.00-0.10 for swe. Two readings deserve emphasis.

- In `s0_m2`, the chat-dominated segment where 93.0% of arrivals are chat, the preference
  puts deepresearch and swe on **exactly one engine** in both repeats (N_eff 1.00 and
  1.00) while the preference off spreads them over 3.70/3.72 and 3.44/3.32 engines. This
  is the largest separation anywhere in the hour and it occurs in the segment where the
  minority classes are rarest.
- In `s1_A`, the even-mix segment, the preference concentrates chat onto 2.30/2.24 engines
  but leaves deepresearch on 3.69/3.54. Deepresearch is not being gathered; chat is being
  kept away from one engine, and deepresearch fills the rest of the fleet.

---

## 2. Per-engine class composition and heavy-token share

Composition is the class mix of the requests each engine received in the segment; the
heavy-token share is the fraction of that engine's input tokens contributed by
deepresearch plus swe. Rows sum to 100% across the three classes.

### s0_m2 (93.0% chat)

| arm | rep | port | requests | % of segment | chat % | dr % | swe % | heavy input tokens % |
|---|---|---|---|---|---|---|---|---|
| ON | r1 | 8000 | 8,468 | 39.1 | 99.4 | 0.6 | 0.0 | 2.8 |
| ON | r1 | 8001 | 2,580 | 11.9 | 70.2 | 25.8 | 4.1 | 73.0 |
| ON | r1 | 8002 | 5,325 | 24.6 | 94.1 | 2.7 | 3.1 | 32.9 |
| ON | r1 | 8003 | 5,265 | 24.3 | 94.9 | 2.0 | 3.1 | 30.9 |
| ON | r2 | 8000 | 7,827 | 36.3 | 98.8 | 0.7 | 0.5 | 8.0 |
| ON | r2 | 8001 | 2,599 | 12.1 | 77.0 | 15.5 | 7.5 | 66.0 |
| ON | r2 | 8002 | 2,533 | 11.8 | 73.8 | 18.4 | 7.7 | 73.0 |
| ON | r2 | 8003 | 8,586 | 39.9 | 99.5 | 0.5 | 0.0 | 3.0 |
| OFF | r1 | 8000-8003 | 5,274-5,406 | 24.6-25.2 | 93.1-93.8 | 4.2-4.7 | 1.9-2.2 | 33.2-36.0 |
| OFF | r2 | 8000-8003 | 4,867-5,756 | 22.7-26.9 | 91.8-94.6 | 3.8-5.8 | 1.6-2.5 | 29.6-42.8 |

### s1_A (33.3% chat, even mix)

| arm | rep | port | requests | % of segment | chat % | dr % | swe % | heavy input tokens % |
|---|---|---|---|---|---|---|---|---|
| ON | r1 | 8000 | 5,088 | 31.3 | 49.8 | 20.4 | 29.8 | 88.9 |
| ON | r1 | 8001 | 5,069 | 31.1 | 56.5 | 22.6 | 20.9 | 86.2 |
| ON | r1 | 8002 | 4,150 | 25.5 | 35.5 | 25.7 | 38.7 | 93.5 |
| **ON** | **r1** | **8003** | **1,966** | **12.1** | **0.0** | **83.7** | **16.3** | **100.0** |
| ON | r2 | 8000 | 5,015 | 31.2 | 51.0 | 20.9 | 28.1 | 88.6 |
| ON | r2 | 8001 | 3,815 | 23.7 | 23.9 | 21.7 | 54.4 | 96.2 |
| **ON** | **r2** | **8002** | **1,736** | **10.8** | **0.0** | **99.7** | **0.3** | **100.0** |
| ON | r2 | 8003 | 5,514 | 34.3 | 57.1 | 21.1 | 21.8 | 85.7 |
| OFF | r1 | 8000 | 4,842 | 30.2 | 46.6 | 21.8 | 31.7 | 90.3 |
| OFF | r1 | 8001 | 1,822 | 11.4 | **6.9** | 85.9 | 7.2 | 99.2 |
| OFF | r1 | 8002 | 4,630 | 28.9 | 45.5 | 22.4 | 32.1 | 91.5 |
| OFF | r1 | 8003 | 4,735 | 29.5 | 44.4 | 21.4 | 34.2 | 91.4 |
| OFF | r2 | 8000 | 4,546 | 28.4 | 45.8 | 24.2 | 30.0 | 90.8 |
| OFF | r2 | 8001 | 2,146 | 13.4 | **15.9** | 72.7 | 11.4 | 97.8 |
| OFF | r2 | 8002 | 4,569 | 28.6 | 45.6 | 21.4 | 33.0 | 91.2 |
| OFF | r2 | 8003 | 4,734 | 29.6 | 45.2 | 22.9 | 31.9 | 91.1 |

The 0.0% chat cells in the ON rows and the 6.9% and 15.9% cells in the OFF rows are the
whole result of this section. The preference-off arm also builds a lopsided engine in this
segment, but that engine still receives chat, and any chat at all pins the pace at 50 ms.

### s2_m1 (76.9% chat)

| arm | rep | port | requests | % of segment | chat % | dr % | swe % | heavy input tokens % |
|---|---|---|---|---|---|---|---|---|
| ON | r1 | 8000 | 3,684 | 17.2 | 55.5 | 31.5 | 13.0 | 84.6 |
| ON | r1 | 8001 | 10,915 | 51.1 | 97.5 | 1.7 | 0.8 | 14.1 |
| ON | r1 | 8002 | 4,548 | 21.3 | 74.1 | 15.3 | 10.6 | 69.8 |
| ON | r1 | 8003 | 2,218 | 10.4 | 35.1 | 61.3 | 3.6 | 92.7 |
| ON | r2 | 8000 | 5,007 | 23.4 | 79.0 | 16.2 | 4.8 | 62.1 |
| ON | r2 | 8001 | 3,781 | 17.6 | 57.4 | 21.8 | 20.8 | 84.6 |
| **ON** | **r2** | **8002** | **1,604** | **7.5** | **0.0** | **97.3** | **2.7** | **100.0** |
| ON | r2 | 8003 | 11,039 | 51.5 | 97.6 | 1.8 | 0.6 | 13.4 |
| OFF | r1 | 8000-8003 | 4,320-5,979 | 19.9-27.5 | 71.9-82.7 | 11.7-21.4 | 5.4-6.7 | 60.4-74.3 |
| OFF | r2 | 8000-8003 | 4,537-5,863 | 20.9-27.1 | 75.7-83.0 | 12.0-18.2 | 5.0-6.5 | 59.5-71.3 |

`s2_m1` is where the two ON repeats differ most: r2 produces a fully chat-free engine and
r1 does not (its most separated engine, 8003, still takes 35.1% chat over the segment,
although section 4 shows it is chat-free in 76.7% of the individual 30 s windows).

### s3_B (60.0% chat)

| arm | rep | port | requests | % of segment | chat % | dr % | swe % | heavy input tokens % |
|---|---|---|---|---|---|---|---|---|
| **ON** | **r1** | **8000** | **1,551** | **7.7** | **0.0** | **100.0** | **0.0** | **100.0** |
| ON | r1 | 8001 | 8,504 | 42.0 | 86.5 | 11.1 | 2.5 | 50.3 |
| ON | r1 | 8002 | 5,007 | 24.7 | 59.6 | 29.2 | 11.2 | 82.3 |
| ON | r1 | 8003 | 5,180 | 25.6 | 58.9 | 32.6 | 8.4 | 82.1 |
| ON | r2 | 8000 | 6,877 | 33.1 | 78.2 | 17.6 | 4.2 | 65.6 |
| ON | r2 | 8001 | 5,052 | 24.3 | 58.1 | 29.2 | 12.7 | 83.2 |
| **ON** | **r2** | **8002** | **1,629** | **7.8** | **0.0** | **100.0** | **0.0** | **100.0** |
| ON | r2 | 8003 | 7,208 | 34.7 | 77.7 | 18.7 | 3.6 | 63.1 |
| OFF | r1 | 8000 | 1,834 | 9.2 | **16.9** | 80.4 | 2.7 | 97.5 |
| OFF | r1 | 8001-8003 | 5,963-6,137 | 29.9-30.8 | 71.1-71.7 | 21.6-23.1 | 5.8-6.8 | 72.1-73.4 |
| OFF | r2 | 8002 | 2,149 | 10.5 | **28.3** | 68.7 | 3.0 | 95.6 |
| OFF | r2 | 8000,8001,8003 | 5,956-6,314 | 29.2-30.9 | 71.4-72.9 | 21.5-22.5 | 5.5-6.1 | 69.9-73.4 |

The spread of the heavy input-token share across the four engines, which is the simplest
one-number summary of separation by work rather than by request count, is 70.2 and 70.0
points in `s0_m2` with the preference on against 2.8 and 13.2 points with it off; 13.8 and
14.3 against 8.9 and 7.0 in `s1_A`; 78.6 and 86.6 against 13.9 and 11.8 in `s2_m1`; and
49.7 and 36.9 against 25.4 and 25.7 in `s3_B`.

---

## 3. (c) The distribution of admissible paces — the core measurement

For each engine and each one-second instant, the admissible pace is the minimum nominal
budget among the classes resident at that instant: 50 ms if any chat is resident,
otherwise 57.7 ms if any swe is resident (its 30 s end-to-end budget over a mean output of
about 520 tokens), otherwise 100 ms if only deepresearch is resident. There are 3,600
samples per engine per run and 14,391-14,394 non-idle engine-seconds per run.

### Whole hour

| arm | rep | 50 ms % | 57.7 ms % | 100 ms % | looser than 50 ms % | mean pace ms | p50 | p75 | p90 |
|---|---|---|---|---|---|---|---|---|---|
| ON | r1 | 74.10 | 6.09 | 19.81 | **25.90** | 60.37 | 50.0 | 57.7 | 100.0 |
| ON | r2 | 71.85 | 6.33 | 21.82 | **28.15** | 61.40 | 50.0 | 57.7 | 100.0 |
| OFF | r1 | 93.70 | 0.72 | 5.57 | **6.30** | 52.84 | 50.0 | 50.0 | 50.0 |
| OFF | r2 | 93.23 | 0.26 | 6.50 | **6.77** | 53.27 | 50.0 | 50.0 | 50.0 |

The variable takes three values, so the full distribution is given above and the
quantiles are reported only because the mean of a floor-bounded quantity should not stand
alone. The median is 50 ms in all four runs: with or without the preference, most of the
fleet at most instants is held to the chat pace. What moves is the upper part of the
distribution — p90 goes from 50 ms with the preference off to 100 ms with it on.

Arm difference in the fraction of engine-seconds looser than 50 ms: 20.5 points
(ON mean 27.03, OFF mean 6.54). Repeat spread is 2.25 points on the ON arm and 0.47 on
the OFF arm, so the difference is roughly nine times the larger spread and is real.
Arm difference in mean pace: 7.8 ms (ON 60.37 and 61.40, spread 1.03; OFF 52.84 and 53.27,
spread 0.43).

### Per segment

| arm | rep | segment | 50 ms % | 57.7 ms % | 100 ms % | looser than 50 % | mean pace ms |
|---|---|---|---|---|---|---|---|
| ON | r1 | s0_m2 | 72.34 | 13.72 | 13.94 | 27.66 | 58.03 |
| ON | r2 | s0_m2 | 71.71 | 13.20 | 15.09 | 28.29 | 58.56 |
| OFF | r1 | s0_m2 | 100.00 | 0.00 | 0.00 | **0.00** | 50.00 |
| OFF | r2 | s0_m2 | 100.00 | 0.00 | 0.00 | **0.00** | 50.00 |
| ON | r1 | s1_A | 73.39 | 9.28 | 17.33 | 26.61 | 59.38 |
| ON | r2 | s1_A | 73.75 | 4.08 | 22.17 | 26.25 | 61.40 |
| OFF | r1 | s1_A | 88.28 | 0.00 | 11.72 | 11.72 | 55.86 |
| OFF | r2 | s1_A | 85.94 | 0.53 | 13.53 | 14.06 | 56.80 |
| ON | r1 | s2_m1 | 76.06 | 1.00 | 22.94 | 23.94 | 61.55 |
| ON | r2 | s2_m1 | 69.36 | 7.08 | 23.56 | 30.64 | 62.32 |
| OFF | r1 | s2_m1 | 96.97 | 2.50 | 0.53 | 3.03 | 50.46 |
| OFF | r2 | s2_m1 | 99.47 | 0.53 | 0.00 | 0.53 | 50.04 |
| ON | r1 | s3_B | 74.61 | 0.39 | 25.00 | 25.39 | 62.53 |
| ON | r2 | s3_B | 72.58 | 0.97 | 26.44 | 27.42 | 63.30 |
| OFF | r1 | s3_B | 89.58 | 0.39 | 10.03 | 10.42 | 55.04 |
| OFF | r2 | s3_B | 87.53 | 0.00 | 12.47 | 12.47 | 56.24 |

Three readings.

1. **The chat-dominated segment is where the preference is the whole story.** In `s0_m2`
   the preference-off arm has 0.00% of engine-seconds at any pace looser than 50 ms in
   both repeats: every one of the 3,591 and 3,594 non-idle engine-seconds has chat
   resident on every engine. The preference-on arm reaches 27.66% and 28.29%. This is the
   cleanest positive in the whole analysis, and it is exactly the case the theory
   describes: chat is 93.0% of arrivals, so without a deliberate preference it lands
   everywhere.
2. **Where the preference is off, the feasibility test alone still frees an engine when
   deepresearch is plentiful.** `s1_A` and `s3_B` reach 11.7-14.1% and 10.4-12.5% with the
   preference off. The preference roughly doubles those, to 26.3-26.6% and 25.4-27.4%.
   The preference is therefore not the only source of separation; it is the source that
   works when the tight-budget class is the majority.
3. **Half the freed time in the chat-heavy segment is only worth 7.7 ms.** In `s0_m2` the
   preference-on arm spends 13.72% and 13.20% of engine-seconds at 57.7 ms and only 13.94%
   and 15.09% at 100 ms, because swe co-resides with deepresearch on the freed engine and
   swe's implied per-token budget is 15% looser than chat's, not 100% looser. Over the
   whole hour the split is 6.1-6.3% at 57.7 ms against 19.8-21.8% at 100 ms. The headline
   fraction of freed engine-time overstates the headroom actually created.

### How many engines are loose at the same instant

Percentage of seconds, by the number of engines simultaneously admissible at a pace
looser than 50 ms.

| arm | rep | segment | 0 loose | 1 loose | 2 loose | 3 loose |
|---|---|---|---|---|---|---|
| ON | r1 | s0_m2 | 39.1 | 11.3 | 49.6 | 0.0 |
| ON | r2 | s0_m2 | 39.4 | 8.2 | 52.3 | 0.0 |
| ON | r1 | s1_A | 0.0 | 93.6 | 6.4 | 0.0 |
| ON | r2 | s1_A | 0.0 | 95.0 | 5.0 | 0.0 |
| ON | r1 | s2_m1 | 8.0 | 88.2 | 3.8 | 0.0 |
| ON | r2 | s2_m1 | 0.0 | 83.0 | 11.4 | 5.6 |
| ON | r1 | s3_B | 0.0 | 98.4 | 1.6 | 0.0 |
| ON | r2 | s3_B | 0.0 | 93.9 | 2.6 | 3.6 |
| OFF | r1 | s0_m2 | 100.0 | 0.0 | 0.0 | 0.0 |
| OFF | r2 | s0_m2 | 100.0 | 0.0 | 0.0 | 0.0 |
| OFF | r1 | s1_A | 53.1 | 46.9 | 0.0 | 0.0 |
| OFF | r2 | s1_A | 43.8 | 56.2 | 0.0 | 0.0 |
| OFF | r1 | s2_m1 | 87.9 | 12.1 | 0.0 | 0.0 |
| OFF | r2 | s2_m1 | 97.9 | 2.1 | 0.0 | 0.0 |
| OFF | r1 | s3_B | 58.3 | 41.7 | 0.0 | 0.0 |
| OFF | r2 | s3_B | 50.1 | 49.9 | 0.0 | 0.0 |

With the preference off, two engines are never loose at the same instant in any second of
any run. With it on, the common state in three of four segments is exactly one loose
engine, held 83.0-98.4% of the time. `s0_m2` is different: two engines are loose 49.6% and
52.3% of the time, but no engine is loose 39.1% and 39.4% of the time, so the freed
capacity there is intermittent rather than a standing allocation.

### Weighted by work rather than by engine count

The freed engine is the least loaded one. The share of resident requests that sit on an
engine whose admissible pace is looser than 50 ms, computed per second and then averaged:

| arm | rep | s0_m2 | s1_A | s2_m1 | s3_B | whole hour (mean / median) |
|---|---|---|---|---|---|---|
| ON | r1 | 6.14 | 27.48 | 23.19 | 21.64 | 19.62 / 20.10 |
| ON | r2 | 6.63 | 26.94 | 24.64 | 23.00 | 20.31 / 20.76 |
| OFF | r1 | 0.00 | 12.10 | 2.16 | 8.91 | 5.79 / 0.00 |
| OFF | r2 | 0.00 | 14.53 | 0.38 | 10.65 | 6.39 / 0.00 |

Whole-hour arm difference 13.9 points (ON mean 19.97, spread 0.69; OFF mean 6.09, spread
0.60), well above the spread. Note the collapse in `s0_m2`: 27.7-28.3% of engine-seconds
are loose there, but only 6.1-6.6% of resident requests are on a loose engine, because the
engine that is free of chat in the chat-dominated segment is holding a small number of
long deepresearch and swe requests while the other three hold the chat flood.

---

## 4. (a) How many engines are free of chat, and for how much of each segment

Windows are 30 s, giving 4 engines x 30 windows = 120 (engine, window) pairs per segment
per run. Two definitions are reported because they answer different questions: *dispatch*
asks whether the router sent chat there, *residency* asks whether chat was present at any
instant during the window, and only the second bears on the pace. No pair is excluded for
being idle; the minimum dispatch count filter (at least 5 requests in the window) removes
nothing.

| arm | rep | segment | zero chat dispatched % | zero chat resident at any instant % |
|---|---|---|---|---|
| ON | r1 | s0_m2 | 30.0 | 25.0 |
| ON | r1 | s1_A | 25.8 | 25.8 |
| ON | r1 | s2_m1 | 30.0 | 22.5 |
| ON | r1 | s3_B | 25.0 | 25.0 |
| ON | r2 | s0_m2 | 30.0 | 25.0 |
| ON | r2 | s1_A | 25.8 | 25.8 |
| ON | r2 | s2_m1 | 33.3 | 28.3 |
| ON | r2 | s3_B | 27.5 | 26.7 |
| OFF | r1 | s0_m2 | 0.0 | 0.0 |
| OFF | r1 | s1_A | 17.5 | 11.7 |
| OFF | r1 | s2_m1 | 2.5 | 2.5 |
| OFF | r1 | s3_B | 17.5 | 10.0 |
| OFF | r2 | s0_m2 | 0.0 | 0.0 |
| OFF | r2 | s1_A | 16.7 | 13.3 |
| OFF | r2 | s2_m1 | 0.0 | 0.0 |
| OFF | r2 | s3_B | 16.7 | 11.7 |

With the preference on, 25.0-33.3% of (engine, window) pairs receive no chat and
22.5-28.3% hold no chat at any instant. One engine in four is 25.0%, so the preference is
operating at or slightly above the one-engine-in-four level in every segment, including
the segment where chat is 93.0% of arrivals. With it off the same figures are 0.0-17.5%
and 0.0-13.3%, and the two segments in which it reaches double digits are the two with a
large deepresearch share.

Repeat spread on the ON arm is 0.0-3.3 points by dispatch and 0.0-5.8 points by residency
(the largest is `s2_m1`, 22.5 against 28.3). Repeat spread on the OFF arm is 0.0-2.5 and
0.0-2.5. The arm difference of 10-28 points exceeds every one of these.

Per engine, expressed as the percentage of the 30 windows in each segment in which that
engine received zero chat:

| arm | rep | segment | 8000 | 8001 | 8002 | 8003 |
|---|---|---|---|---|---|---|
| ON | r1 | s0_m2 | 0.0 | 63.3 | 30.0 | 26.7 |
| ON | r1 | s1_A | 0.0 | 3.3 | 0.0 | **100.0** |
| ON | r1 | s2_m1 | 30.0 | 0.0 | 13.3 | 76.7 |
| ON | r1 | s3_B | **100.0** | 0.0 | 0.0 | 0.0 |
| ON | r2 | s0_m2 | 0.0 | 56.7 | 63.3 | 0.0 |
| ON | r2 | s1_A | 0.0 | 3.3 | **100.0** | 0.0 |
| ON | r2 | s2_m1 | 10.0 | 23.3 | **100.0** | 0.0 |
| ON | r2 | s3_B | 3.3 | 3.3 | **100.0** | 3.3 |
| OFF | r1 | s0_m2 | 0.0 | 0.0 | 0.0 | 0.0 |
| OFF | r1 | s1_A | 0.0 | 70.0 | 0.0 | 0.0 |
| OFF | r1 | s2_m1 | 0.0 | 10.0 | 0.0 | 0.0 |
| OFF | r1 | s3_B | 70.0 | 0.0 | 0.0 | 0.0 |
| OFF | r2 | s0_m2 | 0.0 | 0.0 | 0.0 | 0.0 |
| OFF | r2 | s1_A | 0.0 | 66.7 | 0.0 | 0.0 |
| OFF | r2 | s2_m1 | 0.0 | 0.0 | 0.0 | 0.0 |
| OFF | r2 | s3_B | 0.0 | 0.0 | **66.7** | 0.0 |

The qualitative difference is not that the preference-off arm never separates. It is that
when the preference is on the freed engine is freed in 100% of the windows of a segment
(five of the eight arm-segment cells), and when it is off the best it reaches is 66.7-70.0%
and only in the two deepresearch-rich segments.

---

## 5. (b) Is the structure the same in both repeats, and is the identity stable?

Per minute of the hour, the engine with the largest fraction of seconds at a pace looser
than 50 ms, listed only when that fraction is at least 50%.

| arm | rep | sequence by minute | minutes with an engine at least 90% loose |
|---|---|---|---|
| ON | r1 | 8001: m1-5, none m6-11, 8003: m12, 8001: m13-15, **8003: m16-42**, none m43, **8000: m44-60** | 52 of 60 |
| ON | r2 | 8001: m1-5, none m6-11, 8001: m12, 8002: m13-14, 8001: m15, **8002: m16-44**, 8000: m45, **8002: m46-60** | 54 of 60 |
| OFF | r1 | none m1-23, **8001: m24-32**, none m33-54, **8000: m55-60** | 14 of 60 |
| OFF | r2 | none m1-22, **8001: m23-30**, none m31-53, **8002: m54-60** | 15 of 60 |

**Is the structure the same in both repeats of each arm?** Statistically yes, by identity
no.

- The *amount* of structure reproduces closely. The two ON repeats hold a nearly dedicated
  engine for 52 and 54 of 60 minutes; the two OFF repeats for 14 and 15 of 60. The two ON
  repeats agree to within 2.25 points on the fraction of loose engine-seconds and to
  within 0.69 points on the work-weighted version.
- The *identity* does not reproduce across repeats. In ON r1 the long-lived dedicated
  engine is 8003 for minutes 16-42 and then 8000 for minutes 44-60. In ON r2 it is 8002
  for minutes 16-60 with a one-minute interruption. The two repeats of the same
  configuration therefore choose different physical engines, which is expected: nothing in
  the policy names an engine, and the choice is settled by whichever instance happened to
  be holding the fewest chat requests when the mix shifted.
- Within a run the identity is stable for long stretches but not for the hour. ON r1
  changes the dedicated engine once at the `s2_m1`-to-`s3_B` boundary (minute 43-44,
  segment boundary at minute 46) and has an unsettled opening in `s0_m2` where 8001 leads
  for five minutes, then no engine leads for six, then 8003 takes over. ON r2 settles on
  8002 at minute 16, at the `s0_m2`-to-`s1_A` boundary, and holds it for 45 minutes with a
  single one-minute break. So the answer is: one identity change per hour in r1, none
  after minute 16 in r2, and an unsettled first 15 minutes in both.
- Both OFF runs put their transient dedicated engine on 8001 in `s1_A` and on a different
  engine in `s3_B` (8000 in r1, 8002 in r2), and produce nothing at all in `s0_m2` and
  essentially nothing in `s2_m1`.

The consistent naming of `s0_m2` as the unsettled segment in the ON arm matters for
interpretation: the segment where the preference produces the largest class separation
(deepresearch and swe both on N_eff 1.00 engines) is also the segment where the freed
engine has no stable identity and is loose only intermittently (39.1% and 39.4% of seconds
with zero loose engines). Grouping the minority classes onto one engine and keeping that
engine free of chat are not the same achievement, and in `s0_m2` the policy achieves the
first much more completely than the second.

---

## 6. Answer to the question this task asked

**Does the preference build the structure the theory needs?** Yes, and the effect is
large relative to the repeat spread on every measure taken here.

| measure | preference ON (r1, r2) | preference OFF (r1, r2) | arm difference | largest repeat spread |
|---|---|---|---|---|
| engine-seconds looser than 50 ms, whole hour | 25.90%, 28.15% | 6.30%, 6.77% | 20.5 pts | 2.25 pts |
| mean admissible pace, whole hour | 60.37, 61.40 ms | 52.84, 53.27 ms | 7.8 ms | 1.03 ms |
| p90 of admissible pace | 100 ms, 100 ms | 50 ms, 50 ms | 50 ms | 0 |
| resident requests on a loose engine | 19.62%, 20.31% | 5.79%, 6.39% | 13.9 pts | 0.69 pts |
| chat N_eff, whole hour | 2.24, 2.19 | 3.34, 3.51 | -1.21 | 0.17 |
| minutes with an engine at least 90% loose | 52, 54 of 60 | 14, 15 of 60 | 38.5 min | 2 min |
| engine-seconds looser than 50 ms in the 93%-chat segment | 27.66%, 28.29% | 0.00%, 0.00% | 28.0 pts | 0.63 pts |

**How completely?** To approximately one engine in four, which is the largest whole-engine
unit this fleet offers, and for about 90% of the hour. It does not go further:

- the modal state is exactly one loose engine out of four, held 83.0-98.4% of the time in
  three of the four segments;
- three engines out of four remain at the 50 ms chat pace in 71.9-74.1% of all
  engine-seconds, so the median admissible pace is 50 ms with the preference on exactly as
  it is with it off;
- 6.1-6.3% of the freed engine-time reaches only 57.7 ms rather than 100 ms, because swe
  shares the freed engine with deepresearch;
- the freed engine holds only 19.6-20.3% of the resident requests, so weighted by work the
  structure covers a fifth of the fleet, not a quarter;
- the identity of the freed engine differs between the two repeats and migrates within a
  run at segment boundaries, so the structure is a property of the fleet, not of any
  instance.

**What this does and does not settle.** It settles that the mechanism is present and
working as designed: the preference removes chat from one instance, and that instance's
minimum resident budget rises accordingly. It therefore rules out the explanation that the
1.4-point attainment difference is small because the structure was never built. The
structure was built, at 25.9-28.2% of fleet-time, and the attainment still did not move.
The remaining candidate explanations are that a quarter of the fleet at a looser pace is
too small a lever at this load, that the freed engine is the one carrying the fewest
requests so the loosening is applied where it is least needed, or that the policy does not
convert a looser admissible pace into more admitted work. Distinguishing those requires
measuring what the freed engine did with its pace, which this analysis does not do.

---

## 7. What was not verified

- **The scheduler's own admissible pace was not read.** `server_metrics/scheduler.jsonl`
  exports 47 keys per scrape and none of them is a per-instance pace, gate, budget, tier
  or step-time value; the per-instance keys are running/waiting request counts, batch
  size, prefill and decode token counts, and the projected KV ratio. Section 3 is
  therefore a model: measured class residency per engine combined with the nominal budgets
  supplied in the task statement. It is not a read-back of the number the policy computed,
  and if the policy's gate uses a different quantity (for example a per-request remaining
  budget rather than the nominal class budget) the levels 50/57.7/100 would move.
- **Whether the looser pace was used.** Nothing here measures batch size, admission
  decisions, queueing or preemption on the freed engine, so the link from structure to
  outcome is untested. This is the single largest gap.
- **The swe budget of 57.7 ms per token** was taken as the constant given in the task. It
  was not recomputed per request from each swe request's own output length, and swe output
  length varies run to run in this system, so the 57.7 ms level is a class-mean stand-in.
  Since 6.1-6.3% of ON engine-seconds sit at that level, an error there would change the
  mean pace by less than 1 ms but would not change the fraction of engine-seconds looser
  than 50 ms at all.
- **`instance_cms_kv_cache_usage_ratio_projected` was not used for anything**, in
  accordance with the standing rule that it is a projection and exceeds 1.0.
- **Prefill and decode residency were not separated.** A request is treated as resident on
  its engine for one continuous interval. Whether a request that is queued at the engine
  rather than running should count toward the minimum budget is a policy question this
  analysis cannot answer from the client log.
- **Migration was not accounted for.** `request_engine.csv` carries a `migrated` column
  that was not examined; a request that moved between engines is attributed here to the
  engine in the dispatch map for its whole lifetime.
- **The `llmdslo` runs from the same session were not read**, and no rate sweep, static
  condition, or run outside EXP-93 was used.
- **Only two repeats per arm exist**, so every spread quoted is a range of two values, not
  a standard error.

---

## Appendix A. Adversarial verification, 2026-08-24

This appendix re-derives the headline by a route that shares no join step and no
time-discretisation step with section 3, and then tests the specific failure modes this
repository has actually produced before. The headline survives. One secondary number is
corrected, and one interpretive claim about the chat-dominated segment is weakened.

### A.1 The independent route

Section 3 joins the client's `metrics.csv` to the engine map through
`attribute_engines`, which matches `request_ids.jsonl` on `(task_id, call_index)` and
disambiguates with a nearest `start_time` within 2 s, and then samples residency once per
second. This appendix does neither. It reads `request_ids.jsonl` directly, joins it to
`analysis/request_engine.csv` on `request_id`, which is unique in both files and matches
80,476 of 80,476 rows in every run with no tolerance and no nearest match, and then
computes the exact measure of the union of the residency intervals per engine and class,
clipped to the analysis window [60 s, 3660 s). The result is a continuous time measure
rather than a count of one-second samples, so discretisation cannot enter it.

Fraction of non-idle engine-seconds admissible at a pace looser than 50 ms, whole hour:

| arm | rep | section 3 | exact interval union | 1 s grid on the same join |
|---|---|---|---|---|
| ON | r1 | 25.90 | 25.91 | 25.91 |
| ON | r2 | 28.15 | 28.14 | 28.14 |
| OFF | r1 | 6.30 | 6.29 | 6.29 |
| OFF | r2 | 6.77 | 6.76 | 6.76 |

Per segment the two routes agree to 0.11 points or better everywhere: `s0_m2` 27.71 and
28.37 against 27.66 and 28.29 on the ON arm and 0.000 against 0.00 on both OFF repeats;
`s1_A` 26.60 and 26.22 against 26.61 and 26.25, and 11.73 and 14.06 against 11.72 and
14.06; `s2_m1` 23.96 and 30.66 against 23.94 and 30.64, and 3.01 and 0.50 against 3.03 and
0.53; `s3_B` 25.39 and 27.31 against 25.39 and 27.42, and 10.42 and 12.48 against 10.42 and
12.47. The three-level split also reproduces: 6.11% and 6.33% of ON engine-seconds at
57.7 ms and 19.80% and 21.81% at 100 ms.

Two further quantities were re-derived from the same independent join and reproduce
exactly. Chat N_eff over the 30 s windows of the hour is 2.24 and 2.19 with the preference
on against 3.34 and 3.51 with it off. The count of minutes in which some engine is loose
for at least 90% of its seconds is 52 and 54 against 14 and 15.

The headline stands: 20.5 points of arm difference against a largest repeat spread of
2.23 points.

### A.2 Failure modes checked

**Stale instance ids and PRERUN directories.** No result directory whose name contains
EXP-93 also contains `PRERUN`; the glob returns zero. `scheduler.jsonl` does carry eight
instance ids across its 3,725 to 3,729 scrapes in all four runs, four of them registrations
from an earlier engine, but this appendix never reads `scheduler.jsonl`. It keys on
`engine_port`, which takes only the four values 8000 to 8003, so a stale id cannot enter
any number here by construction.

**A filter that removes rejected requests from a comparison about rejection.** Rejected
requests are absent from the residency intervals, because a rejected request is never
dispatched to an engine and therefore never pins that engine's minimum budget. The risk is
that the two arms reject different amounts of chat, which would change chat residency for a
reason other than routing. They do not. Over the hour the chat rejection rate is 10.84% and
10.44% with the preference on against 10.93% and 10.11% with it off, over 64,284 to 64,300
chat arrivals per run. In `s0_m2`, the segment that produces the largest arm difference,
the preference-on arm rejects **less** chat than the preference-off arm, 1.05% and 1.52%
against 2.02% and 2.03%, and still frees an engine that the preference-off arm never frees.
The structure is therefore not an artefact of shedding chat.

**Residency definition.** The headline was recomputed under six alternative residency
definitions: arrival to completion, first token to completion, arrival shifted later by
0.5 s, 1 s and 2 s, and both endpoints extended outward by 0.5 s and 1 s. Across all seven
definitions the ON r2 value moves between 28.04 and 28.23 and the OFF r1 value between 6.27
and 6.31. The largest deviation from the reported number on any run is 0.09 points, an
order of magnitude below the repeat spread. First-token times were available for 94.9% of
the joined requests by exact key match on `(task_id, call_index, start_time)`.

**A difference smaller than the repeat spread.** Whole hour, 20.5 points against 2.23. By
segment the smallest arm difference is `s1_A` at 13.5 points against an ON spread of 0.38
and an OFF spread of 2.32, and the segment with the largest ON spread is `s2_m1`, 6.70
points against an arm difference of 25.5. No arm difference reported in section 3 or
section 6 is within its spread.

**Aggregating over the hour a quantity whose target moves between segments.** The hour
figure is a total over a total, not a mean of segment means, so it is arithmetically sound.
It is nevertheless not representative of any segment. The arm difference is 27.7 points in
`s0_m2`, 13.5 in `s1_A`, 25.5 in `s2_m1` and 14.9 in `s3_B`, and the preference-off arm's
hour figure of 6.3% and 6.8% is composed of exactly 0.00% in one segment and 10.4% to 14.1%
in two others. Quote the hour number only with the segment table beside it.

### A.3 Correction: a per-second ratio was averaged where a total over a total was needed

Section 3's work-weighted measure, "the share of resident requests that sit on an engine
whose admissible pace is looser than 50 ms", is computed per second and then averaged over
seconds. That is a mean of per-second ratios. The quantity the sentence describes is a
share of resident-request-seconds, which is a total over a total. The two differ whenever
the loose state is correlated with fleet load, and here it is.

| arm | rep | section 3, mean of per-second ratios | total over total |
|---|---|---|---|
| ON | r1 | 19.62 | 19.28 |
| ON | r2 | 20.31 | 19.94 |
| OFF | r1 | 5.79 | 6.40 |
| OFF | r2 | 6.39 | 7.39 |

The two errors point in opposite directions and both enlarge the arm difference. Corrected,
the whole-hour arm difference is **12.7 points** (ON 19.28 and 19.94, spread 0.66; OFF 6.40
and 7.39, spread 0.99), not the 13.9 points given in sections 3 and 6. The corrected
difference is still about thirteen times the larger spread, so the conclusion does not
change; the number does. Section 6's row "resident requests on a loose engine | 19.62%,
20.31% | 5.79%, 6.39% | 13.9 pts" should read 19.28%, 19.94% | 6.40%, 7.39% | 12.7 pts.

The mechanism behind the sign of each error is visible in the load conditioning. With the
preference on, a loose engine exists in seconds when the fleet is lighter: over the hour the
fleet holds 560 and 564 resident requests on average when at least one engine is loose
against 716 and 735 when none is, so the per-second average over-weights the light seconds
in which the loose engine's share is relatively large. With the preference off the sign
reverses, 634 and 663 against 544 and 532, so the same averaging under-weights the loose
seconds.

### A.4 Weakening: the `s0_m2` result is the largest by time and the smallest by work

Section 3 calls `s0_m2` "the cleanest positive in the whole analysis" and section 6 carries
it into the summary table as 27.66% and 28.29% against 0.00% and 0.00%, an arm difference of
28.0 points. That difference in engine-seconds is real and reproduces here. What it buys is
much smaller than the phrase invites, and by more than section 3's own paragraph on the
collapse states.

In `s0_m2` the engine that is free of chat holds a median of 8 and 9 resident requests and a
mean of 10.8 and 11.7, against a per-engine fleet mean of 105 resident requests in that
segment. Corrected to a total over a total, the share of resident-request-seconds on a loose
engine in `s0_m2` is **2.85% and 3.15%**, not the 6.14% and 6.63% section 3 reports; the
mean-of-ratios inflates this segment by a factor of 2.1, which is the largest instance of
the error in A.3. In 16.8% and 16.0% of the loose engine-seconds in that segment the loose
engine holds two requests or fewer. The loose state also arrives when the fleet is quiet:
the fleet holds 219 and 215 resident requests when at least one engine is loose against 736
and 735 when none is, a factor of 3.4.

The corrected reading of `s0_m2` is therefore: with 93.0% of arrivals in the tight class, the
preference converts about 28% of engine-seconds to a looser pace, but those engine-seconds
carry about 3% of the resident work, they belong to an engine running at roughly a tenth of
a normal load, and they occur mostly while the fleet is at a third of its busy occupancy. A
looser admissible pace on a near-empty engine during a quiet interval is the case in which
the extra headroom is worth least. This does not contradict section 6's conclusion that the
structure was built and the attainment did not move; it names one more reason why it might
not have moved, and it should be stated wherever the 28.0-point row is quoted.

### A.5 What this appendix did not settle

The model in section 3 remains a model. This appendix used the same three nominal budgets
(chat 50 ms, swe 57.7 ms, deepresearch 100 ms) and the same rule that the minimum over
resident classes sets the pace, so it cannot detect an error in that rule. It confirms only
that the measured residency, and every number derived from it, is what section 3 says it is
under two independent joins and two independent time measures. Whether the policy's own
gate computes the same quantity is still unread, for the reason section 7 gives: no
per-instance pace, gate, budget or tier value is exported in `scheduler.jsonl`.

**Verdict: the headline survives.** 25.9% and 28.2% against 6.3% and 6.8%, a 20.5-point arm
difference against a 2.23-point repeat spread, reproduces to 0.02 points by an independent
route and is insensitive to the residency definition to within 0.09 points. Two numbers
change: the work-weighted arm difference falls from 13.9 to 12.7 points over the hour, and
the work-weighted `s0_m2` figure falls from 6.14-6.63% to 2.85-3.15%.
