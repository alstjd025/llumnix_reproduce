# EXP-60 — the pinned allocation on the trace whose mix moves

**Written after the run, from the chain script's header, which carried the
design before the run.** `exp-record` asks for the judgement rule in this file
before the run and that did not happen; the rationale and the arms were in
`/home/nxclab/tools/exp60_pin_hour.sh` instead. Recording that here so the
omission is visible rather than silent, and so the next experiment puts the file
first.

## 1. Why

EXP-59 measured a demand-proportional fixed allocation at fixed arrival rate and
fixed mix and found it ties the unpinned control at 45 req/s and beats it by
16.4 points at 55. That design cannot show what fixing the assignment costs: the
allocation was derived from this workload's measured output-token mix, and in an
eight-minute static condition that mix never moves, so the allocation is right
for the whole condition by construction.

`dyn60_short_m123` steps the mix three times in an hour (m1 → m2 → m3 → m1) while
the arrival rate ranges over 13.8–73.8 req/s. That is the condition
`motivation.md` §7.3 is about.

## 2. Design

Two arms, `fluidserve` (control) and `fspin-demand` (chat 2 / dr 1 / swe 1), two
repeats, one hour each, engines cold-restarted per condition, stock FIFO,
migration off. Binary `c2d970ca`.

**Prediction, from motivation.md §7.3**: the pinned arm loses on the hour trace
even though it did not lose on the static conditions, because the mix moves and
a fixed allocation cannot follow it. **Refutation**: the pinned arm does not
lose, which would mean fact 5 ("the right assignment moves") does not translate
into a measurable cost, and the motivation's account of why we win has to be
rebuilt.

## 3. Result

**The refutation branch fired.** Repeat 1: control 68.77 offered, pinned
**75.87**. The pinned arm also rejects less (15.80% against 27.28%).

Per class, and the two arms sacrifice different ones:

| | chat | deepresearch | swe |
|---|---|---|---|
| control | 71.6 / rejected 25.5% | 81.9 / 9.3% | **31.3 / 62.1%** |
| pinned | 80.6 / 13.1% | **42.8 / 32.8%** | 77.0 / 16.7% |

**The mechanism the prediction named does appear, in one of the four segments.**
Chat's demand in instances (its share of produced output tokens × 4) against
what each arm gave it:

| segment | chat needs | control gave | pin gave | pin's chat attainment − control |
|---|---|---|---|---|
| m1 | 2.46 | 2.42 | 2.00 | +9.3 |
| **m2, chat-heavy** | **3.46** | **2.84** | 2.00 | **−9.4** |
| m3, swe-heavy | 2.51 | 2.59 | 2.00 | +20.7 |
| m1′ | 2.22 | 2.56 | 2.00 | +17.9 |

The only segment the pin loses is the only segment where no integer allocation
can give chat what it needs: with three classes and a floor of one instance
each, chat cannot have three of four.

## 4. And this trace cannot answer the question either

Rounding each segment's demand to an integer allocation gives **(2,1,1) in all
four**. The mix never crosses a rounding boundary, so the fixed allocation is
the best available throughout and the cost of fixing it cannot show up in the
aggregate. Details and the two ways out (fleet size, mix range) are in
`fluidserve-implementation.md` §63.4.

## 5. Repeat 2's control condition is contaminated

Its per-class signature is the pinned one and the log shows three pin
applications for two pinned arms. `run_exp30_dynamic.sh` loops over arms inside
one shell and `set_arm` exported `FS_CLASS_PIN` without clearing it for the next
arm. Fixed by clearing every ablation variable at the top of `set_arm`; the
missing control repeat is **EXP-60b**. Full account in §63.5.

So at the time this was written the experiment had **three pinned hour runs**
(74.48 / 75.87 / 77.65) and **one control** (68.77, which matches EXP-54's 69.95
and 69.25 across sessions). EXP-60b replaced the missing control.

## 6. Judgement with the replacement control (2026-08-06 22:30 KST)

EXP-60b's start-up line reads `classpin=off` and the log applies the pin zero
times, so the contamination did not recur.

| arm | repeats | offered (mean) | spread | rejected | token goodput |
|---|---|---|---|---|---|
| control | 2 | **69.02** | 68.77 ~ 69.27 | 27.3 / 27.6% | 17,545 ~ 17,752 |
| pinned | 2 | **76.76** | 75.87 ~ 77.65 | 15.8 / 15.7% | 17,799 ~ 18,156 |

**+7.74 points, against repeat spreads of 0.50 and 1.78.** The refutation branch
stands.

The contaminated control's per-class signature matches the pinned arm to within
a point on every class and differs from the two clean controls by more than 30
points on every class, so the diagnosis made from the log is confirmed by the
data itself.

**The gain is in requests, not in tokens.** Requests completed within SLO rise
11.9% (124,428 → 139,179) while their output tokens rise 1.9% (17,649 → 17,978
tok/s, against spreads of 207 and 357). The pin halves deepresearch, whose
outputs are long, and fills the space with chat and swe, whose outputs are
short. Details in `fluidserve-implementation.md` §63.3.1.
