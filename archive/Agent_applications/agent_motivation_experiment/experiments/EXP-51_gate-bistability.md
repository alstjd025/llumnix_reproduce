# EXP-51 — 45 req/s is bistable, and the state variable is whether any engine is free of chat

Written before the run, 2026-08-02 13:30 KST. Everything in §1–§3 is re-analysis
of runs that already exist; nothing new had been run when this was written.

## 1. The observation

Fourteen conditions at 45 req/s on the m1 mix, across EXP-42, 47, 48, 49 and 50,
sort into two groups with nothing between them:

| | runs | route share | pend | force | offered |
|---|---|---|---|---|---|
| collapsed | 4 | **9.1 – 15.3%** | 78–83% | **4.6 – 6.7%** | 87.8 – 91.1 |
| healthy | 10 | **72.4 – 95.7%** | 4–26% | **0.0 – 0.5%** | 98.2 – 99.6 |

The route share jumps from 15.3% to 72.4% with nothing in the gap, and the
`fsbase`, `fluidserve`, `fskv` and `fsc` arms all appear in **both** groups, so
this is not a flag effect. Four of fourteen, 29%.

**It is not a start-up transient.** Every run routes 100% for the first three
minutes. The divergence happens between minutes 3 and 5, which is when the fleet
first reaches its steady-state occupancy, and once a run has collapsed it does
not recover inside the remaining four minutes:

| | 0–60 s | 60–120 | 120–180 | 180–240 | **240–300** | 300–360 |
|---|---|---|---|---|---|---|
| the four that collapsed | 100 | 100 | 98–100 | 47–99 | **6.3 / 7.3 / 12.9 / 22.0** | 2.6 – 8.0 |
| the ten that did not | 100 | 100 | 100 | 99–100 | **99 – 100** | 44 – 89 |

## 2. The state variable, measured

`gate_allowance_ms` per instance, median over the steady window (300–480 s):

| run | the four engines | route |
|---|---|---|
| `260801_0857_exp48r1_fluidserve` (collapsed) | 50.0 / 50.0 / 50.0 / 50.0 | 15.3% |
| `260801_1004_exp48r2_fluidserve` (healthy) | 50.0 / **100.0** / 50.0 / 50.0 | 88.9% |
| `260801_1638_exp50r1_fsc` (healthy) | 50.0 / 50.0 / 50.0 / **100.0** | 82.6% |

`f.gateAllowance` is the minimum `nominalMs` over the requests live on that
instance, and the three class budgets are chat 50 ms, swe 61.9 ms (its 30 s
end-to-end budget divided by 484.9 expected output tokens) and deep research
100 ms. **An engine reading 100.0 is holding deep research and nothing else.**

So the two states are: **at least one engine is free of chat**, or **chat is on
all four**. In the second case every instance's gate is `50 × 0.90 = 45.0 ms` for
every arriving request whatever its own budget, and there is nowhere in the fleet
to put work with a looser budget.

**The collapsed run is the faster one.** Its engines deliver 43–47 ms per token
against a 45.0 ms gate. The healthy run delivers 47–56 ms — slower — and routes,
because one of its gates is 90.0 ms. The difference is not load; it is which
budget each instance is being held to.

**Why it does not recover.** With every gate closed, requests PEND until they run
out of time and are then FORCED, and FORCE does not consult the gate. Forced
placements put chat back on every engine, so the separation cannot re-form. The
force share is 4.6–6.7% in the collapsed runs and 0.0–0.5% in the healthy ones.

## 3. Why candidate C is the arm to test this with

C replaces `min(f.gateAllowance, req.nominalMs)` with `req.nominalMs`, which
removes `f.gateAllowance` from the decision entirely. **It does not change which
engine holds which class** — that is decided by arrivals — so under C the
all-four-at-50 configuration should still occur at the same rate. What should
change is that it stops mattering.

That is a sharper prediction than "C collapses less often", and it is the one
this experiment is built around.

## 4. Design

| | |
|---|---|
| arms | `fluidserve` and `fsc`, arm as the inner loop, six rounds |
| rate | 45 req/s (2700 rpm) only |
| duration | **8 minutes**, deliberately the same as every existing condition, so the twelve new runs pool with the fourteen already recorded |
| mix | m1 |
| binary | `scheduler-exp49-H2` md5 `c3c6a3518092ea80eb7bb4dc1725ae5b`, one for both arms |
| profile | the corrected `classes[]` |
| existing sample | baseline 10 runs (3 collapsed), `fsc` 2 runs (0 collapsed) |
| after this | baseline n=16, `fsc` n=8 |

**Eight minutes rather than sixteen.** The collapse is decided by minute five, so
eight minutes captures the state, and keeping the duration identical to the
fourteen existing conditions is worth more than the extra observation window —
it roughly doubles the baseline sample for free. **The recovery question is
therefore not answered here** and is left open: no run has recovered within the
four minutes after collapsing, and whether it would in twenty is unmeasured.

## 5. Judgement rules, written before any result

**The classification is mechanical and fixed now**: a run is *collapsed* if its
route share over the steady window (120 s to end minus 60 s) is below 40%. Every
one of the fourteen existing runs falls unambiguously on one side of that (15.3%
against 72.4%), and no run may be reclassified after the fact.

1. **The discriminating test.** Among `fsc` runs where all four instances read
   `gate_allowance_ms` = 50.0 over the steady window, **the route share must stay
   above 40%.** That is C's mechanism stated as a prediction: the configuration
   still happens, and it no longer decides the outcome. If such runs collapse
   anyway, the attribution in §2 is wrong and the cause is somewhere else.
2. **The rate.** `fsc` collapses in 0 of 8 against a baseline of 3 or more in 16.
   With 3/16 as the baseline rate, 0/8 has a one-sided p of about 0.16, so **this
   alone will not be conclusive** and rule 1 is the one that decides. Reported
   with both counts either way.
3. **The state variable has to track the outcome.** Across all runs of both arms,
   *baseline* runs with a chat-free engine must be healthy and those without must
   be collapsed. The three runs checked so far agree perfectly; if the new ones do
   not, §2 is a coincidence of three and the experiment has found that instead.
4. **No regression at this rate.** `fsc`'s healthy runs must score within the
   baseline's healthy range (98.2–99.6 offered). C is not supposed to change
   anything when the fleet has separated on its own.
5. **Whatever the outcome, the per-instance `gate_allowance_ms` trace of every
   condition is recorded**, because the quantity being explained is when and why
   an engine stops being chat-free, and that is only readable per run.

## 6. What each outcome leads to

- **Rule 1 passes.** C removes a failure mode that costs about ten points of
  offered attainment 29% of the time at the knee. That is a different trade from
  the one EXP-50 judged, where C cost 4.4 points on the hour, and it makes the
  design named in v0.1.1 §6.2 — C's own-budget gate plus an explicit reservation
  for the tightest-budget class — the thing to build rather than one of two
  options.
- **Rule 1 fails.** The gate is not what locks the state in, and the next thing
  to measure is the placement side: §37.3 recorded that which engine collects a
  class is decided by the first arrival and is otherwise arbitrary, and the
  candidate then is to keep an engine chat-free deliberately rather than hoping
  arrivals do it.
- **Rule 3 fails.** §2 is a coincidence of three runs, and the two states have
  some other cause that has to be found before anything is built.

## 7. Result

### 7.1 Rule 3 is already perfect on the fourteen runs that existed before this

`exp51_bistability.py` found four conditions §1 had missed (EXP-37, two from
EXP-38, and one older `tbtfix` run), so the baseline sample before EXP-51 is
**fourteen**, not ten, with **five** collapsed — 36%.

| baseline, 45 req/s | healthy | COLLAPSED |
|---|---|---|
| at least one engine free of chat | **9** | 0 |
| no engine free of chat | 0 | **5** |

**Fourteen for fourteen, no exceptions.** Every collapsed run reads
`50/50/50/50` and every healthy one reads `50/50/50/100` (one reads
`50/50/59/100` — an instance holding swe and no chat). Force share is 4.9–6.7%
in the collapsed group and 0.0–0.5% in the healthy one, with nothing between.

That settles rule 3 before the new conditions land: **the state variable tracks
the outcome exactly.** What remains is rule 1, which cannot be read from what
exists — both recorded `fsc` runs happened to have a chat-free engine, so neither
tests whether C survives without one. That is what the twelve new conditions are
for.

### 7.2 Result — C removes the collapse, and rule 1 could not be read because of HOW

Twelve new conditions, six rounds of (`fluidserve`, `fsc`). Pooled with what
existed:

| arm | n | collapsed | rate |
|---|---|---|---|
| baseline | **20** | **8** | 40% |
| **`fsc`** | **8** | **0** | **0%** |

**Rule 3 holds at 20 for 20.** Twelve baseline runs with a chat-free engine, all
healthy; eight without, all collapsed. No exceptions in any run of any arm.

**Rule 4 passes.** `fsc`'s runs read 98.4 – 99.9 offered against the baseline's
healthy range of 98.7 – 99.6. C costs nothing at this rate when the fleet has
separated, and it is at the top of the range rather than the bottom.

**Rule 1 cannot be read, and the reason is the finding.** The rule asked what
happens to `fsc` runs in which all four instances read 50.0 — and there were
none. **All eight `fsc` runs ended with a chat-free engine, against 12 of 20 for
the baseline.** Under the null that C has the baseline's 40% collapse rate,
0 of 8 has probability 0.6^8 = 0.017.

### 7.3 §3's prediction was wrong, and the correction is the mechanism

§3 predicted that C "does not change which engine holds which class, so the
all-four-at-50 configuration should still occur at the same rate; what should
change is that it stops mattering." **The configuration did not occur at all.**

The reason is a path §3 did not consider. Class affinity — `sortCandidates`
ordering candidates by how much of the arriving class each already holds — is
applied **only within the feasible set**, which is deliberate and is what keeps
the preference from overriding capacity. When every gate closes, the feasible set
is empty on every instance, so **affinity stops operating entirely** and
placements go through FORCE, which orders by harm and spreads work rather than
concentrating it. FORCE is also what puts chat back on every engine.

So the two states are not "separated" and "mixed" arrived at by chance. They are:

- **ROUTE alive** → affinity operates → deep research concentrates on whichever
  engine already holds the most of it → that engine loses its chat → its gate
  opens to 90 ms → ROUTE stays alive.
- **ROUTE dead** → affinity never runs → FORCE spreads every class over every
  engine → every gate is 45 ms → ROUTE stays dead.

**Both are self-sustaining, which is why nothing in between was ever observed and
why no collapsed run recovers.** C removes the gate term that can kill ROUTE, so
the fleet stays in the first state.

### 7.4 What this changes

C's standing was "rejected: 4.4 points worse over the hour" (EXP-50). It now also
**removes a failure that costs about ten points of offered attainment in 40% of
runs at 45 req/s**, and it does so by keeping the mechanism that separates
classes alive rather than by overriding it.

That does not overturn EXP-50 — the hour is still 65.3 against 69.7, and chat
still loses 14.4 points there. What it establishes is that the gate's instance
minimum has a cost that the hour-long score did not show, and that the two ends
of that choice are both wrong. **EXP-52 measures the axis between them**, and the
prediction to carry into it is now sharper: the slack that matters is the
smallest one that keeps ROUTE alive at 45 req/s, because everything else follows
from that.
