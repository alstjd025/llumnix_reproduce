# EXP-59 — pinning classes to instances, inside our own system

Written before the run. 2026-08-06.

## 1. The hole this fills

The motivation argues that there are two ways to get the class-to-instance
assignment wrong, and that they are wrong in opposite directions
(`motivation.md` §8):

- **below**: a class must run on at least as many instances as its share of the
  work needs. The static-partition baseline gives chat, whose output tokens
  amount to 2.50 instances' worth, an effective **1.02** instances, and one
  instance then accumulates 1,180 waiting requests while two others sit at 25.6%
  KV.
- **above**: the tightest-budget class must not be resident on every instance,
  or every instance is held to its budget. Under all three fully mixed
  configurations an instance is free of chat **1.0%** of the time.

The upper condition is supported by an ablation of our own system: EXP-56 turns
the class preference off, the chat-free instance-time falls from 30.9/23.7/4.4%
to 1.0/1.0/1.0%, and offered attainment falls 7.5 points at 45 req/s.

**The lower condition has no such ablation.** Our code has no setting that pins
a class to an instance, so that end of the axis is supplied by PolyServe, which
also differs from us in how it estimates per-tier demand, in allowing a limited
spill between tiers, and in its admission rule. So the sentence that can be
written today is "a configuration that pins classes to servers scores 35.6 on
this workload", not "pinning is what costs the points".

`--fluidserve-class-pin` closes that. The candidate list is filtered to the
instances the arriving request's class is assigned to and **nothing else
changes** — same capacity model, same budgets, same four-way ladder, same
ordering among whatever survives the filter, same binary.

## 2. And it separates two things that are currently confounded

PolyServe fails in two ways at once and the evidence cannot tell them apart:

1. **the allocation is fixed** (integer instances, changed at most every few
   minutes), and
2. **the allocation it chose is the wrong one** — it hands chat one instance
   where chat's share of the produced output tokens implies 2.50, because its
   repartitioner estimates demand from per-tier decode tokens and a class that
   is numerous but short reads small.

Two pinned arms separate them, because the second arm is given the allocation
that (2) got wrong:

| arm | chat (tier 50) | deepresearch (100) | swe (25) | where it comes from |
|---|---|---|---|---|
| `fspin_poly` | 1 | 2 | 1 | the allocation PolyServe's own repartitioner settled on (EXP-57 §6.3) |
| `fspin_demand` | **2** | 1 | 1 | proportional to each class's share of the produced output tokens (2.50 / 1.11 / 0.20), rounded to integers |

**If `fspin_demand` also scores far below the unpinned control, then fixing the
assignment is what costs, not the choice of allocation.** That is the sentence
the motivation needs and cannot write today.

## 3. What changed in the code

`--fluidserve-class-pin`, default empty (no pinning). Format
`50:0;100:1,2;25:3`: the class whose per-token budget tier is 50 may only be
placed on the first instance in the sorted list of instance ids, the tier-100
class on the second and third, the tier-25 class on the fourth.

- **Positions, not names.** The configuration means the same thing after a pod
  is recreated under a new name.
- **No spill.** A request whose instances cannot take it is held or rejected
  like any other request with nowhere to go; it is never placed outside its set.
  **The baseline being compared against does allow a limited spill, so this arm
  is the stricter version of a static partition and that has to be said wherever
  the two are read together.**
- A malformed value panics at start-up rather than being read as "no pinning",
  because "no pinning" is this experiment's control arm.
- The start-up line prints `classpin=50:0;100:1,2;25:3` or `classpin=off`, and
  the deployment script compares it against what the arm asked for.

Binary `c2d970cab36f7a31c8b7b6c91d2bab1f`. It also contains EXP-58's
`--fluidserve-affinity-weight`, whose default of 1.0 reproduces the previous
ordering exactly, so the control arm here is the same policy EXP-56 measured.

## 4. Design

| | |
|---|---|
| rates | 45 and 55 req/s (2700 and 3300 rpm) |
| arms | `fluidserve` (control, unpinned), `fspin_poly`, `fspin_demand` |
| repeats | 2 |
| conditions | 12, eight minutes each, engines cold-restarted per condition |
| engine scheduler | stock FIFO, migration off |
| mix | m1 |
| pinned ablations | `FS_CLASS_HARM=false` in all three arms, as in every other FluidServe arm, so the control is the arm EXP-56 measured |

Loop order is repeat → rate → arm, so the three arms sit adjacent in time.

## 5. Hypotheses and judgement rules

### H1 — pinning costs, whichever allocation is used

**Prediction**: both pinned arms score at least **10 points** of offered
attainment below the unpinned control at 45 req/s.

The threshold is 10 rather than 5 because the control's own repeat spread at
45 req/s is 8.5 points (90.4 / 93.4 / 98.9 in EXP-56). Pairing within a session
removes the session offset but not this, since the spread comes from how much
separation happens to form.

**Refutation**: either pinned arm within 5 points of the control. That would say
the loss attributed to pinning in the motivation belongs to something else
PolyServe does, and §8.1 of `motivation.md` would have to be rewritten.

### H2 — the allocation matters, and it is not the whole story

**Prediction**: `fspin_demand` scores **above** `fspin_poly` (the allocation is
part of the loss) and **still at least 10 points below the control** (the
allocation is not the whole loss).

**Refutation, and the more interesting outcome**: `fspin_demand` within 5 points
of the control. That result says a *well chosen* static allocation is as good as
a continuously re-formed one at this fleet size, and the motivation's claim
narrows from "pinning classes to servers is wrong" to "PolyServe's way of
choosing the allocation is wrong". **Record it as a finding, not a failure**;
it would also mean that the four-instance fleet is too small for the argument
and that §12 item 4 (vary the engine count) becomes the priority.

### H3 — the pinned arms satisfy the upper condition better and still lose

**Prediction**: the pinned arms have a **higher** fraction of instance-time with
no resident chat request than the control, and a **lower** score.

This is the point of §8.3 of `motivation.md` restated inside one system: class
separation is not itself the objective. Today it rests on a comparison against
PolyServe (67.9% chat-free instance-time against our 4.4–30.9%, and 35.6 points
against our 90.4–98.9).

**Refutation**: the pinned arms show *less* chat-free instance-time than the
control. That would mean the filter is not producing the separation it is
supposed to produce and the arm is measuring something else.

### H4 — the lower condition is visible as a queue on one instance

**Prediction**: in `fspin_poly`, the single chat instance carries a
time-averaged waiting queue at least ten times the mean of the other three, and
its KV occupancy is not the highest in the fleet. This is the shape of §4's
table reproduced inside our own system.

**Refutation**: queues within a factor of two across instances. That would say
the filter is not binding, which at 45 req/s with chat at 76.9% of arrivals
would be surprising and would need explaining before anything else is read.

## 6. What this still cannot answer

- **Fleet size.** Four instances allocate in units of 25%. At sixteen the same
  integer rounding is a few percent, and both the failure being demonstrated
  here and the mechanism our own policy relies on would weaken. Unchanged from
  `motivation.md` §12 item 4.
- **Spill.** This arm forbids it and the baseline allows a limited form of it,
  so the two are not the same configuration. A third pinned arm with spill would
  separate that, and is not in this run.

## 7. Result (2026-08-06)

12 conditions, no contamination: the log applied the pin exactly as many times
as there were pinned conditions. The chain invokes the driver once per condition
in a fresh process, so the environment variable an arm exports cannot reach the
next one -- unlike EXP-60, where it did.

| | 45 req/s | 55 req/s |
|---|---|---|
| control, no pin | 94.50 (90.26 / 98.74) | 68.42 (67.68 / 69.17) |
| **`fspin-demand`** (chat 2 / dr 1 / swe 1) | **93.20** (93.19 / 93.21) | **84.79** (84.36 / 85.23) |
| `fspin-poly` (chat 1 / dr 2 / swe 1) | **72.29** | **61.96** |

**H1 is refuted for one of the two allocations.** `fspin-poly` loses 22.2 points
at 45 req/s and 6.5 at 55, well past the 10-point threshold. `fspin-demand`
loses 1.3 at 45 and **gains 16.4** at 55, which is inside the 5-point band the
rules named as the narrowing outcome.

**H2's second branch fired**, the one the rules called the more interesting
outcome: a well-chosen static allocation is not worse than continuous
placement at this fleet size, and at 55 req/s it is much better. It also rejects
far less (7.7% against 24.1%) and reproduces to 0.02-0.9 points where the
control spans 8.5.

**H3 holds.** The pinned arms have more instance-time free of the tightest class
and a lower score in the `fspin-poly` case, which is the point restated inside
one system: separation is not the objective.

**H4 holds.** `fspin-poly` puts chat, 76.9% of arrivals, on one instance and its
rejection rate goes to 27-37%.

### 7.1 What the design could not see

The allocation `fspin-demand` uses was derived from this workload's measured
output-token mix, and an eight-minute static condition holds that mix fixed, so
the allocation is right for the whole condition by construction. **The claim
being tested -- that fixing the assignment costs when the mix moves -- cannot
appear here.** The pre-registration listed two outcomes and missed this third
one: the workload cannot exhibit the failure mode. EXP-60 was written for that,
and section 63.4 of the implementation notes records that the one-hour trace
cannot show it either, for a different reason: with four instances, three
classes and a floor of one instance each, the demand-implied integer allocation
is (2,1,1) in every one of the trace's four mix segments.

**What survives from this experiment**: `fspin-poly` is 22.2 points worse than
the control, which makes "PolyServe's way of choosing the allocation is wrong" an
ablation of our own policy rather than a comparison against a different system.
That was one of the two things this experiment was built for.
