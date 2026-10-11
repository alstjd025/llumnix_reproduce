# EXP-62 — a deadline-aware engine scheduler under a static class partition, and the three-way comparison

Written before the run. 2026-08-06 23:55 KST.

## 1. The two things this run is for

### 1.1 The last cell of the "best engine scheduler under a naive router" objection

EXP-40 crossed two control planes with two engine schedulers and found the gap
between the control planes unchanged; its weakness, measured later, is that both
control planes it used keep the engine queues empty, so three of the Niyama
port's five units had nothing to act on. EXP-61 closed one hole by putting
QoServe under Llumnix load balance, whose engines do carry a queue (per-engine
medians 373 / 325 / 367 / 404 at 60 req/s), and the answer was that the engine
scheduler does not recover the loss and costs a little: −1.14 / −0.93 / −0.45
offered attainment at 45 / 50 / 60 req/s with token goodput −3 / −10 / −14%.

**PolyServe is the sharper version of the same question and it has not been
run.** Load balance spreads the queue over all four engines, so an engine
scheduler at least sees every waiting request somewhere. PolyServe rejects
nothing and puts **3,679 waiting requests on one engine while the other three
sit at zero**, because its static partition gives chat, which is 76.9% of
arrivals, a single instance. That is the cleanest available statement of the
limit an engine-layer scheduler has: **reordering chooses the sequence within an
instance and cannot move a request to one of the three idle instances.** If a
deadline-aware scheduler is going to recover a misplacement, this is the
configuration where it has the most to recover and the clearest reason it
cannot.

### 1.2 The three-way comparison at one arrival rate axis, in one session

The comparison the paper needs — the best engine scheduler under each control
plane, against ours with the stock engine — is currently assembled from three
sessions (PolyServe from EXP-57, load balance × QoServe from EXP-61, FluidServe
from EXP-53 and EXP-59). Cross-session movement on this workload reaches 4.6
points, which is smaller than the differences being read but not negligible, and
FluidServe has **no measurement at 50 req/s on the current binary at all**. This
run puts PolyServe with the stock engine, PolyServe with QoServe, and FluidServe
with the stock engine in **one session at 45, 50 and 60 req/s**, so the three
columns of that table share a session offset that cancels in the differences.

## 2. Design

| | |
|---|---|
| rates | 45, 50, 60 req/s (2700, 3000, 3600 rpm) |
| arms | `polyserve` (stock engine), `polyserve` + QoServe, `fluidserve` (stock engine) |
| repeats | 2 |
| conditions | 18, eight minutes each, engines cold-restarted per condition |
| mix | m1 |
| engine scheduler | stock vLLM ordering, except the one arm that loads `deadline_sched.DeadlineScheduler` via `SCHED_EXTRA_ARGS` |
| migration | off |
| binary | `bin/scheduler-exp07`, the one EXP-59 and EXP-60 used |
| PolyServe tier demand | derived from `fluidserve.json` by `set_scheduler_profiling.py`, i.e. the EXP-57 correction. **Anything measured before that correction is not comparable** |

Loop order is repeat → arm → rate, and the engine-side scheduler is switched per
arm because it lives on the LeaderWorkerSet rather than in the scheduler's
flags.

## 3. Hypotheses and judgement rules

### H1 — QoServe does not recover PolyServe's loss

**Prediction**: at every rate, PolyServe with QoServe is within **3 points** of
PolyServe with the stock engine, and in particular does not reach even a quarter
of the distance to FluidServe. At 45 req/s that distance is about 59 points
(35.6 against 94.5 measured in different sessions), so a quarter of it is 15.

**Refutation**: QoServe gains **5 points or more** at any rate, with a repeat
spread smaller than the gain. That would say the engine layer can partly undo a
placement error after all, and the claim in `motivation.md` §5 — that an
engine-side SLO scheduler does not substitute for getting the placement right —
would have to be narrowed to the control planes already tested.

### H2 — the mechanism: ordering cannot move work between instances

**Prediction**: the per-engine waiting-queue medians stay as asymmetric under
QoServe as under the stock engine — one engine carrying two orders of magnitude
more than the others — and the identity of the loaded engine does not change.

This is the point of the experiment stated as something observable rather than
inferred. The engine-layer figures come from the engine's own Prometheus series,
so they do not depend on anything the load generator computes.

**Refutation**: the asymmetry falls materially under QoServe. That would mean
the engine scheduler is somehow affecting what the control plane places, which
would need explaining before any score is read.

### H3 — the three-way ordering, in one session

**Prediction**: FluidServe with the stock engine is above PolyServe with QoServe
at all three rates by more than the sum of the two repeat spreads.

**Refutation**: the ordering reverses or the gap falls inside the spreads at any
rate. FluidServe's repeat spread at 45 req/s has reached 8.5 points, so this is
a real possibility at that rate and the run is designed with two repeats
knowing it; if it happens, the finding is about the bimodality already recorded
in §5.9 of the implementation notes rather than about PolyServe.

### H4 — where QoServe's cost shows up

**Prediction**: token goodput falls under QoServe at every rate, as it did in
EXP-61 (−3 / −10 / −14%), because dynamic chunking's throughput cost (§35.3
measured −3.6% of total throughput) is paid whether or not the ordering helps.

## 4. Checks that must pass before any number is read

- The scheduler start-up line reports the intended policy for every condition,
  and `set_scheduler_profiling.py` prints its `verified:` line.
- The engine start-up line carries `[deadline] relegation=` on the QoServe arm
  and does **not** carry it on the two stock arms. The driver aborts otherwise.
- **Rejection rate by arm**: PolyServe's admission control does reject, so a
  non-zero value is expected here, unlike the load-balance arms. What must be
  checked instead is the error breakdown: any condition with 400 or 503 responses
  from the gateway is measuring the stack failing rather than the policy, which
  is what happened to one condition of EXP-61 (§63.6.1).
- `metrics.csv` has data rather than only a header; if it has only a header,
  open `shards/` before recording the condition as failed.

## 5. What this cannot answer

- **One engine scheduler.** The Niyama port is one implementation of one design;
  a negative result about it is not a negative result about every engine-side
  deadline mechanism. The fidelity limits are in `qoserve-niyama-fidelity.md`.
- **Fleet size.** Four instances, and PolyServe's failure here is partly an
  integer-rounding failure that a larger fleet would soften. Unchanged from
  `motivation.md` §12 item 4.
- **Whether PolyServe with a better repartitioner would do better.** EXP-59
  already showed that the allocation PolyServe chooses is 22.2 points worse than
  a demand-proportional one inside our own policy, so this run measures QoServe
  on top of the allocation PolyServe actually chooses, not on top of the best
  static allocation.

## 6. Result (2026-08-07)

18 conditions, no gateway 400 or 503 responses in any of them.

| req/s | arm | offered (min~max) | rejected | goodput | chat / dr / swe |
|---|---|---|---|---|---|
| 45 | PolyServe + FIFO | 34.10 (33.95~34.24) | 0.0% | 8,399 | 8.3 / 100 / 100 |
| 45 | PolyServe + QoServe | **35.08** (34.99~35.17) | 0.0% | 8,301 | 9.5 / 100 / 100 |
| 45 | **FluidServe + FIFO** | **94.76** (90.22~99.30) | 3.8% | **19,360** | 96.0 / 97.7 / 76.9 |
| 50 | PolyServe + FIFO | 32.65 (31.94~33.36) | 0.0% | 9,150 | 6.8 / 100 / 99.9 |
| 50 | PolyServe + QoServe | **35.30** (35.11~35.50) | 0.0% | 9,132 | 9.3 / 100 / 99.9 |
| 50 | **FluidServe + FIFO** | **78.42** (77.80~79.03) | 16.2% | **18,230** | 79.7 / 90.8 / 42.3 |
| 60 | PolyServe + FIFO | 30.47 (29.67~31.27) | 0.0% | 10,546 | 5.2 / 100 / 99.1 |
| 60 | PolyServe + QoServe | **33.26** (32.70~33.82) | 0.0% | 10,560 | 8.1 / 100 / 98.8 |
| 60 | **FluidServe + FIFO** | **59.99** (55.72~64.25) | 31.4% | **17,606** | 59.0 / 85.8 / 23.1 |

**H1 holds, but with the opposite sign from EXP-61.** The paired differences are
+0.98 / +2.65 / +2.79, all inside the 3-point band the rule named, and far
inside the quarter-of-the-gap clause: at 45 req/s the distance to FluidServe is
60.66 points, a quarter of which is 15.2, and the engine scheduler moves 0.98.
Under Llumnix load balance the same scheduler moved −1.14 / −0.93 / −0.45. **The
sign reversal was not predicted and is the most useful part of the run.**

**H2 holds exactly.** In all four PolyServe conditions one engine carries a
median of 2,293 to 2,746 waiting requests and the other three carry zero
(maximum 12), and QoServe does not change that at all. Reordering chooses a
sequence inside an instance; it cannot move a request to one of the three idle
instances.

**H3's prediction is confirmed by where the gain sits: entirely in chat.**
deepresearch and swe are already at 99 to 100 and have no room; chat moves
8.3→9.5, 6.8→9.3, 5.2→8.1. The class piled onto the single instance is the class
the reordering helps, and it is the only one.

**H4 is refuted.** Goodput does not fall under QoServe here (8,399→8,301,
9,150→9,132, 10,546→10,560, i.e. −1.2% / −0.2% / +0.1%), where under load
balance it fell 3 to 14%. The dynamic-chunking cost is presumably still paid,
but with three engines idle the fleet has the throughput to absorb it.

### 6.1 The three-way comparison, and what it added

FluidServe now has all three static rates measured on the current binary in one
session: 94.76 at 45, **78.42 at 50 (a point that did not exist)**, 59.99 at 60.
The 45 req/s value agrees with EXP-59's 94.50.

**The swe defect recorded in implementation.md §63.8 appears in the static
conditions too, rising monotonically with arrival rate**: swe attainment
76.9 → 42.3 → 23.1 and swe rejection 3.8 → 16.2 → 31.4%. The one-hour trace's
62% is the continuation of that curve rather than something specific to a moving
mix, which means the cause can be investigated in an eight-minute condition.

### 6.2 Figure

`results/aggregate_analysis/motivation/qoserve_cross.png`
(`analysis_scripts/request_level/qoserve_cross.py`), three panels: what the
engine scheduler changes, the levels it would have to close, and where a queue
exists for it to reorder. Details and the two defects found while building it
are in `fluidserve-implementation.md` §64.5.
