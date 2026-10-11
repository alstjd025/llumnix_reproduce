# EXP-44 — candidate A on an hour of moving load

Written before the run, 2026-08-01 10:40 KST. EXP-42 accepted candidate A on
static conditions; this asks whether the gain transfers to load that moves, and
what it does to the two failure modes EXP-41 recorded.

## 1. Design

| | |
|---|---|
| arms | `fluidserve` (A off) and `fsa` (A on) |
| trace | `full` = `dyn60_short_m123`, the same hour EXP-41 ran |
| repeats | 1 (about 2.3 hours); a second is a separate decision |
| engine | stock FIFO, four engines, migration off, engine admission off |
| binary | `scheduler-exp42-A` md5 `d24861df81d89257f5f16dbbb70bb295`, one for both arms |
| `--fluidserve-class-harm` | **false in both arms, set explicitly** |

**The Llumnix SLO arm is dropped** at the user's direction. The question here is
what A did, and both arms answer it inside one session. The FluidServe-versus-
Llumnix comparison already exists from EXP-41 and can be re-run once A is
settled; **no claim about that gap may be made from this run**, because its
baseline would be in another session.

**Why class-harm is pinned false in both arms.** EXP-41's FluidServe ran that way
(§38), and `set_scheduler_profiling.py` now returns unset ablations to their
compiled default, which for this flag is `true`. Without pinning, the
`fluidserve` arm would differ from EXP-41 in two ways instead of none.

## 2. Predictions, written before the run

**Whole-hour offered attainment for `fsa`: about 68**, against EXP-41's 59.7 for
the same trace and policy without A. Derived by carrying the static gains of
EXP-42 onto EXP-41's rate bands:

| band | requests | EXP-41 FluidServe | predicted with A |
|---|---|---|---|
| under 40 req/s | 35,439 | 93.2 | ~95 |
| 40–50 | 30,694 | 75.9 | ~85 |
| 50–60 | 46,518 | 60.1 | ~70 |
| 60+ | 66,767 | 34.4 | ~45 |

Weighted by request count that is **68.2**. The same arithmetic reproduces
EXP-41's measured 59.7 from its own bands, which is the check that the weighting
is right.

**This is an extrapolation.** The static conditions went to 60 req/s; this trace
reaches 77 in a 30-second bin and holds above 65 for six minutes. Nothing was
measured there.

### What should be fixed

**EXP-41 §6.1, the loss at minutes 50–56.** FluidServe scored 14.9 against
Llumnix SLO's 17.4 in the only stretch above 65 req/s for more than 2.5 minutes.
The mechanism was that it admitted 3,965 chat requests of which **91.2% missed**,
3,546 of them on time-between-tokens. A rejects exactly those placements: at a
static 60 req/s it took admitted attainment from 55.5 to 93.6. **Prediction:
`fsa` scores higher than `fluidserve` across minutes 50–56.**

### What may get worse, and this is the prediction worth writing down

**EXP-41 §6.2, the 1,471 preemptions on engine 8003.** A sheds chat harder and
deep research less — measured at a static 60 req/s, chat 37.1% → 56.1% and deep
research 13.0% → **7.3%** — and deep research is what filled that engine. Its
share of resident KV went 70.8% → **73.5%** with A.

**Prediction: preemptions do not fall, and may rise.** Static conditions recorded
zero on both arms, so this trace is the only place the question can be asked.

If preemptions rise, that is not a reason to reject A — its score gain is
measured and large — but it promotes candidate B (putting the request's KV
occupancy into the shed decision, the user's proposal) from "well motivated" to
"the next thing to fix", because A moved the class balance in the direction B
corrects.

## 3. Judgement rules

1. **`fsa` whole-hour offered at least 5 points above `fluidserve`.** Below that,
   the static gain did not transfer and the reason has to be found before
   anything else is built on A.
2. **`fsa` above `fluidserve` across minutes 50–56.** This is the specific
   failure A was expected to fix.
3. **Preemptions recorded whatever the score does**, per engine, against
   EXP-41's 1,471 on 8003.
4. **No loss in the low band.** `fsa` under 40 req/s must not fall below
   EXP-41's 93.2 by more than the repeat spread; the closest available figure
   for that spread is the 0.4 points EXP-42 measured between candidate A's two
   repeats at 45 req/s, so a fall of more than about 2 points is a real loss.
5. **One run per arm.** A difference smaller than EXP-38's measured repeat
   spread on this workload (up to 4.2 points, and 10.2 at 45 req/s on the
   shipped policy) is not established by this run, whatever its sign.

## 4. Result — no gain over the hour, and the prediction was wrong in the other direction

Finished 2026-08-01 13:30 KST. Both arms healthy: 4/4 engines, 50.1 req/s
delivered against the trace mean, no flags.

**The baseline reproduces EXP-41 on the same trace and policy** — offered 59.2
against 59.7, admitted 83.7 against 84.3, rejection 29.3% against 29.1%, goodput
15,472 against 15,643. That is what makes the difference below attributable to
the flag rather than to the session.

| whole hour | offered | admitted | rej% | goodput | total tok/s | chat / dr / swe |
|---|---|---|---|---|---|---|
| FluidServe | 59.2 | 83.7 | 29.3 | 15,472 | 18,305 | 58.7 / 83.3 / 34.3 |
| **+ margin** | **59.7** | **94.0** | 36.5 | **15,973** | 16,901 | 57.2 / 91.9 / 41.5 |

**Judgement rule 1 fails.** The whole-hour offered difference is **+0.5**, against
a threshold of +5. The static gain of +13.1 points at 60 req/s did not transfer
to the hour.

### Where it went: a loss at the first peak cancels the gains

| segment | mix | FluidServe | + margin | diff |
|---|---|---|---|---|
| 0–15 | m1 | **70.7** | **57.0** | **−13.7** |
| 15–30 | m2 | 90.2 | 95.8 | +5.6 |
| 30–45 | m3 | 51.8 | 60.6 | +8.8 |
| 45–60 | m1 | 31.2 | 31.2 | 0.0 |

By 30-second rate band the differences are all small — +0.3, +1.5, +2.0, −0.8 —
so this is not a rate effect. It is specific to the first fifteen minutes, which
is the only segment that contains a cold start and the first crossing of the
knee, from 27 to 72 req/s in about two minutes.

### What A did do: the engine overload is gone

| | preemptions | where | peak decode batch on one engine | peak KV | peak engine queue |
|---|---|---|---|---|---|
| FluidServe | **1,852** | all on engine 8002 | ~500 | 100% | 27 |
| **+ margin** | **0** | — | ~330 | <80% | 0 |

**The prediction was that preemptions would not fall and might rise**, because A
sheds chat harder and deep research less and deep research is what fills the
engine that collects it. They went to zero instead. The reason is visible in the
totals: A produces 16,901 output tokens/s against 18,305, so the fleet carries
about 8% less work and no engine reaches its memory bound.

Note also that the baseline's overload landed on **engine 8002** here and on
**8003** in EXP-41. §37.3 measured that which engine collects a class is decided
by the first arrival and is otherwise arbitrary; this is that, observed again.
The count also moved 1,471 → 1,852 between two runs of the same configuration,
so **the run-to-run variation of this quantity is at least 26%** and only a
change well outside that range is readable. Zero is outside it.

### Why the first peak got worse

Minutes 5–15, which is where the loss is:

| arm | offered | admitted | chat shed | dr shed | swe shed | chat ITL | route | force |
|---|---|---|---|---|---|---|---|---|
| FluidServe | 64.9 | 87.3 | 25.0% | 14.0% | **56.1%** | 41.8 | 13.9% | 3.6% |
| + margin | 48.6 | 90.0 | **55.1%** | 8.1% | **30.7%** | 44.1 | 7.2% | 1.5% |

**A only tightened the branch for classes judged on time between tokens.** The
end-to-end branch, which is how the swe class is judged, was deliberately left
alone so that EXP-42's result would be attributable to one change. The
consequence shows here: A sheds chat much harder (25.0% → 55.1%) and swe much
less (56.1% → 30.7%), so the admitted mix shifts toward the class with
5,557-token prompts, and chat's inter-token latency gets **worse** rather than
better (41.8 → 44.1 ms) despite far more chat being refused.

Per class over the segment: FluidServe reads chat 72.1 / dr 73.7 / swe 50.4;
with the margin, chat 49.7 / dr 92.6 / swe 58.8. **Chat is 76.9% of the requests,
so the segment goes to whichever arm serves chat better**, and that is the
baseline.

### The uncomfortable part: the baseline's failure was also a crude isolation

In the baseline arm, engine 8002 became the deep research sink and was destroyed
by it — but that left the other three engines carrying chat at a pace chat could
meet. With the margin the load is spread, no engine breaks, deep research
improves from 73.7 to 92.6, and chat falls from 72.1 to 49.7.

**One engine being sacrificed to the heavy class is, in this segment, worth more
under a per-request score than four healthy engines sharing the load**, because
the class that benefits is 76.9% of the requests. This is the same trade EXP-41
§5.1 measured on `azcode` from the other side, where the separation was worth
+40 points of chat and cost 5,513 preemptions.

**One run per arm, and the first peak is a transient**, so this is a mechanism
worth chasing rather than a settled result.

### Against the pre-registered rules

1. **FAIL** — whole-hour offered +0.5, threshold +5.
2. **PASS, but small** — minutes 50–57: 14.9 → 16.7. EXP-41 recorded 14.9 there
   against Llumnix SLO's 17.4, so the margin closes most of that gap but this
   run has no Llumnix arm to confirm it against.
3. **Recorded**: 1,852 → 0, the opposite of what was predicted.
4. **PASS** — under 40 req/s, 93.5 → 93.8.
5. **One run per arm.** +0.5 over the hour is far below any measured spread and
   establishes nothing. The −13.7 in the first segment and the 1,852 → 0 in
   preemptions are the two differences large enough to be worth pursuing.

### What this changes

**A is not withdrawn.** Its static result stands, it removed the engine overload
entirely, and it took admitted attainment from 83.7 to 94.0 over the hour. What
it does not do is raise the offered score on this trace, because the margin it
applies to one class's budget shifts the admitted mix toward another class that
the same change did not touch.

That points at **applying the margin to the end-to-end branch as well**, which
was deliberately excluded from EXP-42 and is now the obvious next single change.
It is the same constant and the same one-line shape.
