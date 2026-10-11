# EXP-126 — the one-hour trace with both SLO halves, arrivals term on and off, against PolyServe

*Written 2026-09-11 02:35 KST, before any condition finished. Sections 1 to 5 are
the pre-registration; section 6 onwards is the result.*

## 1. What is being asked

Two separate findings from 2026-09-10 have not been put together.

**Budget normalisation (EXP-121, two repeats; EXP-123, one repeat) was measured on
the STATIC sweep only.** Halving the per-token budgets moved the pace gate's share
of sole infeasibility from 31.4% to 90.1% at 160 req/s and from 61.1% to 88.3% at
210, and with that the ordering against PolyServe reversed: at 160 req/s FluidServe
went from −0.2 behind to +14.5 ahead (repeat range +5.8 to +23.3), at 210 from
−23.1 behind to +6.2 ahead (repeat range 0.8 points).

**The KV projection's arrivals term (EXP-124 static, EXP-125 hour) was measured at
the STANDARD budgets only.** On the hour trace it raised admitted attainment from
88.6 to 93.0 and goodput 2.3%, but left FluidServe at 68.2 offered against
PolyServe's 76.0 — the gap narrowed from −9.5 to −7.8 and did not close.

This run is the cell neither of them covers: **the hour trace, the halved budgets,
and the arrivals term on and off.**

## 2. Settings

| | |
|---|---|
| trace | `dyn60_shift_m2Am1B_b1045_x620.csv` — one hour, mixture shifts every 15 minutes, 67 to 279 req/s |
| fleet | 8 × Llama-3.1-8B-Instruct, TP=1, `--api-server-count 4`, gateway `-v 0` |
| scheduler binary | `903889ac0e52bd18408644a71b797484` (verified in the pod with `md5sum /proc/1/exe`) |
| profile | `deploy/profiling/llama31-8b-b200-tp1` |
| load generator | 36 worker processes (`runner-exp114dyn-p36.template.yaml`) |
| repeats | 2, repeat as the outer loop, treatment first |

**The six budgets, each half of the standard:**

| class | first token | per token |
|---|---|---|
| chat | 5,000 → **2,500 ms** | 50 → **25 ms** |
| deepresearch | 10,000 → **5,000 ms** | 100 → **50 ms** |
| swe | 7,000 → **3,500 ms** | 75 → **38 ms** |

38 rather than 37.5 because `--fluidserve-class-budgets` is parsed with
`strconv.Atoi` and a fraction makes the scheduler panic at start-up.

**Three arms:**

| arm | policy | differs from the one above by |
|---|---|---|
| `fsv3ah1c25d50s38ftc2500d5000s3500` | fluidserve | — |
| `fsv3capgnofrcc25d50s38ftc2500d5000s3500` | fluidserve | `--fluidserve-arrival-horizons` absent. **One named flag, nothing else.** |
| `polyservepc25d50s38ftc2500d5000s3500` | polyserve | the paper mechanisms, as `polyservept75` |

## 3. Two things that change the meaning and must travel with any number from here

**PolyServe's tier boundaries move as well.** It derives them from the same
workload file, so the halved budgets put them at chat 25 / swe 38 /
deepresearch 50 (verified in the pre-flight print). That is the correct
comparison — both policies must be given the same promise — but this arm differs
from `polyservept75` in **how it partitions**, not only in how it is scored, and
a drop against `polyservept75` cannot be attributed to scoring alone.

**No column here can sit beside a standard-budget column.** The scoring rule
differs, so these numbers are not comparable with EXP-125's 66.5 / 68.2 / 76.0.
The scorer prints six warnings to that effect on every invocation, and the arm
names carry the budgets so a result directory says how it must be scored.

## 4. What is expected, and what would refute it

The read floor is EXP-125's control repeat spread: **offered 1.9 points**
(68.41 / 67.51 / 66.5 across three runs of one binary), **admitted 1.1 points**.
Differences below that are not read.

**H1 — at the halved budgets FluidServe leads PolyServe on the hour trace.**
Grounds: the static sweep reversed the ordering at both 160 and 210 req/s, and
this trace spends most of its hour in that band. *Refuted if the FluidServe
control is at or below PolyServe on offered attainment.*

**H2 — the arrivals term still adds on top.** Grounds: +4.4 admitted and +1.7
offered at the standard budgets on this same trace. *Refuted if the arrivals arm
is more than 1.9 points below the control on offered attainment.*

**H3 — and its gain is SMALLER here than the +4.4 admitted it gave at the standard
budgets.** This is the prediction that can most easily be wrong, and it comes from
the crossover footprint rather than from any measurement. `T*` rises when the
per-token budget `B` falls, because `B` sits in the denominator
`((B − c0)·s − C·c_kv)`; a higher `T*` puts the workload's footprint further below
it, which is the memory-bound side turning into the pace-bound side. The arrivals
term corrects a **memory** accounting error — it charges an instance for prompt
mass recently routed to it — so where memory is not what binds, correcting it
should buy less. *Refuted if the arrivals term's admitted gain here is as large as
or larger than +4.4.*

**The mechanism check that decides H3 independently of the score:** the sole
infeasibility counters split by cause. If H3 holds, the pace gate's share here
should be near the 88–90% the static halved-budget arms showed, not the 21%
measured on this fleet at the standard budgets.
⚠ `scheduler_fluidserve_infeasible_sole_total` gained a `tier` label on
2026-09-10, so it must be parsed by label NAME. Splitting the series key at the
first `=` reads `gate,tier` in one repeat and `gate` in another, and produced a
0.0% pace share once already.

## 5. What this run cannot answer

- **Whether the halved budgets are the right promise.** They are a normalisation
  derived from the measured per-token time when the pool is full (ratio 1.96) and
  the loaded p50 (1.97), two independent estimates that agree. Whether a reviewer
  accepts a budget chosen that way is a separate argument, and CLAUDE.md group B
  records that re-choosing a deadline for a new fleet is not what either tradition
  in the literature does.
- **Whether N=1 is the right window.** It was chosen after the fact from the
  static sweep, and every arrivals-term result so far is one repeat per window.
- **Anything about the 4-instance 70B fleet**, where the binding axis is the other
  one and none of these flags has been regression-tested.

## 6. Result — six of six, 2026-09-11 02:30 to 09:41 KST

All six conditions delivered the trace's 615,228 arrivals exactly, so no column
here is the load generator rather than the policy. No `ABORT`, no `FAILED`, no
unmerged `shards/`.

| arm | offered | admitted | rejection | goodput (tok/s) |
|---|---|---|---|---|
| FluidServe + arrivals term (N=1) | 72.9–73.6 | **99.8** | 26.2–26.9% | 72,951–73,439 |
| FluidServe control | **73.5** | 99.5–99.6 | 26.1–26.2% | **73,634–73,673** |
| PolyServe | **44.6–45.0** | **70.6–71.6** | 36.8–37.2% | **50,569–51,029** |

Two repeats each; the ranges are the two runs. **The repeat spread is 0.0 to 0.7
points on every arm, PolyServe included** — the arm whose static-sweep spread
reached 16.5 points at 160 req/s is stable here, so the 28.9-point gap is more
than forty times the spread.

### 6.1 H1 confirmed, and by more than the static sweep predicted

FluidServe leads PolyServe by **+28.5 to +29.0 offered**, **+28.2 to +29.2
admitted**, and **44% on token goodput**. At the standard budgets on this same
trace and fleet the ordering was the other way: PolyServe 76.0 against 68.2.

Per mixture segment (two-repeat bands, offered / admitted / rejection):

| segment | FluidServe + arrivals | FluidServe control | PolyServe |
|---|---|---|---|
| s0 chat 93% | 97.1–97.3 / 99.6 / 2.3–2.6 | 97.3–97.4 / 99.4–99.5 / 2.1 | 88.1–88.3 / 96.4–96.5 / 8.4–8.7 |
| s1 even | 63.1–65.5 / 99.9 / 34.4–36.9 | 64.1 / 99.6–99.7 / 35.6–35.7 | 34.0–34.7 / 68.6–71.0 / 50.5–51.1 |
| s2 chat 77% | 81.1–81.2 / 99.8 / 18.6–18.8 | 80.6–81.0 / 99.6 / 18.7–19.1 | 39.2–40.1 / 54.5–55.8 / 28.1 |
| s3 chat 60% | 51.2–51.8 / 99.9 / 47.9–48.5 | 52.4–52.5 / 99.6 / 47.1 | 19.3–19.5 / 47.4–48.2 / 59.1–59.4 |

**Every segment goes the same way**, unlike EXP-125 where the whole-run average
was the mean of a gain in one segment and a loss in another.

### 6.2 H2 is not supported: the arrivals term is indistinguishable here

offered 73.5 against 72.9–73.6, admitted 99.5–99.6 against 99.8. Both are inside
the read floor and the offered sign flips between repeats. The pre-registered
refutation was "more than 1.9 points below the control", which did not happen, so
the honest statement is **not distinguishable**, not harmful.

### 6.3 H3 confirmed in direction, but a ceiling is confounded with it

The term's admitted gain went from **+4.4 at the standard budgets to +0.2 to
+0.3 here**, which is the predicted direction. ⚠ **The control is already at
99.5, so only 0.5 points remain to be won.** This run cannot separate "the
binding axis moved so a memory correction buys less" from "there was nothing left
to gain", and it should not be cited as if it could.

**The mechanism check passes independently of the score.** The pace gate's share
of sole infeasibility is **83.6% and 85.1%**, against 88–90% measured on the
static halved-budget arms and **21% on this fleet at the standard budgets**. The
binding axis did move, and the scheduler's own counters say so.

### 6.4 The result that was not predicted: tightening the promise raised attainment

| | offered | admitted | rejection |
|---|---|---|---|
| standard budgets (EXP-125, one repeat) | 66.5 | 88.6 | 25.0% |
| **halved budgets (EXP-126, two repeats)** | **73.5** | **99.5** | 26.1% |

At chat 50 ms the measured per-token time under load is p50 40.7 and p90 52–58 ms,
so the policy was admitting into a region it could not then serve and missed 11%
of what it took. At 25 ms the same gate holds the instances far enough below that
point that **99.5% of what is admitted is met**, and it costs 1.1 percentage
points more rejection to gain 7 points.

**So the factor of two is not a harder test; it is a correction of a budget that
was mis-set for this fleet**, and the derived 1.96 / 1.97 was the size of the
correction. ⚠ This comparison crosses a scoring rule, so it is stated as a
before-and-after of the budget choice and never merged into one table.

### 6.5 Where the two policies differ: chat

| class | share | FluidServe control | PolyServe |
|---|---|---|---|
| chat | 65.5% | 75.8–75.9 / 99.5–99.7 / 23.8% | **38.0–38.4 / 59.3–60.3 / 35.9–36.4%** |
| deepresearch | 21.5% | 67.3–67.5 / 99.7–99.8 / 32.1–32.3% | **69.1–69.3** / 94.2–94.5 / 26.6% |
| swe | 13.1% | 71.3–71.7 / 99.1–99.3 / 27.7–28.0% | 37.4–38.3 / 90.0–91.9 / **58.3–58.4%** |

**PolyServe is ahead of us on deepresearch by 1.6 to 2.0 points.** What collapses
is chat, where it misses 40% of what it admits, and chat is 65.5% of the arrivals.
Its swe survives by refusing 58% of it. FluidServe is at or above 99.1 admitted in
all three classes.

Part of the chat collapse is the tier compression named in section 3: the halved
budgets put PolyServe's boundaries at 25 / 38 / 50 ms, **13 and 12 ms apart where
they had been 25 ms apart**, so its classification axis separates these three
classes less well than before. That is a consequence of giving it the same
promise, and it is not separable from the rest within this run.

## 7. What is still missing, and the order it will be filled

**One baseline is not "the baselines".** Sections 6.1 to 6.5 compare against
PolyServe only.

**2026-09-11, user's instruction on ordering:** llm-d and Llumnix SLO next,
**vLLM router last**. Both of the first two take their promise entirely from the
workload file, so they read the halved budgets from the same file PolyServe read
and need no new flag.

- **EXP-126b** (started 09:48 KST, four conditions, about 4.7 hours): `llmdslotc25d50s38ftc2500d5000s3500` and `slotc25d50s38ftc2500d5000s3500`, two repeats each, repeat as the outer loop, llm-d first. Chain `/home/nxclab/tools/exp126b_baselines.sh`, drivers `run_exp126_llmd36.sh` and `run_exp126_halfslo_hour.sh`.
- **vLLM router: last.** Not yet scheduled.

**The llm-d condition carries a check the others do not.** On 2026-09-04 llm-d ran
a whole hour on four of eight instances, because the EPP takes its endpoints from
the InferencePool's `targetPorts` and not from the ConfigMap the driver refreshes,
and routing to half a fleet looks like a bad policy rather than a routing failure.
The pool was verified to list eight ports before starting, and **after each llm-d
condition the chain counts `vllm:request_success_total` per engine port from that
run's own scrapes** and fails the condition if any engine did essentially nothing.
The file existing is not evidence — the collector scrapes all eight ports whatever
the router does — which is why the check reads the counter.

Also still open, unchanged by this run: EXP-124 repeat 2 and the choice of N; the
4-instance 70B regression before any of these flags moves to a compiled default;
and whether a budget chosen by normalisation is defensible in the paper, which is
an argument rather than a measurement.
