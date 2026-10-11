# EXP-43 — candidate E: turn the class term in the damage estimate back on

Written before the run, 2026-08-01 04:20 KST. Second change, one at a time.
Background: `fluidserve-implementation.md` §38 (why this was never measured) and
§37.4 (why it is the mechanism the saturated regime is missing).

## 1. What is being changed and why

No code changes. One flag: `--fluidserve-class-harm`, whose compiled default is
`true` and which has been `false` in every deployment since 2026-07-28 10:28.

The term is the last line of `harmToIncumbents` (fluidserve.go:1400):

```go
if p.cfg.enableAffinity && p.cfg.classHarm && len(f.live) > 0 {
    harm += (1 - classShare(f, tier)) * fsHarmCap
}
```

When no instance can take a request at its promised pace, the candidates are
ordered by `harm` ascending. Without this term `harm` is the sum over the
requests already on the instance that can still meet their budgets, and requests
already past their budgets contribute nothing — so an instance whose own class
has begun to miss reads as costing nothing and keeps attracting work from other
classes. The term charges a candidate for the share of it that belongs to other
classes, which is what makes an instance already full of one class the place
that class keeps going even when nothing is feasible.

**Why this matters here.** §37.1 measured how often the policy is in that
regime: the ROUTE path, which is where the class-affinity ordering in
`sortCandidates` operates, takes 99.7% of decisions at 15 req/s, 11.2% at 45 and
**1.5% at 60**. §37.4 concluded that at saturation there is no path that can
build class separation, and §38 established why: the one path designed for that
regime was switched off. This turns it on and measures it.

## 2. What is expected, on what grounds, and what would refute it

**Expected.** At 60 req/s the class mix per engine becomes less uniform, chat
concentrates on fewer engines, and chat's inter-token latency on those engines
falls below its 50 ms budget the way it did on `azcode` in EXP-41 §5.1, where a
chat-only engine ran at 26.6–32.6 ms against a fleet average of 44.3.

**Grounds, and the reason for doubt.** EXP-41 showed the separation is worth a
great deal when it forms — chat went from 45.3 to 85.4 offered — but it also
showed what it costs: the engine that collected deep research reached 100% KV
occupancy and preempted 5,513 times, because `capMem` is computed from a
prompt-sharing ratio measured on the current residents and no allowance is made
for the tokens they have yet to generate, which are never shared (§36.3). This
term makes the concentration stronger. **It could therefore improve the score
and produce the failure mode that candidate D exists to fix, at the same time.**

**Refutation conditions, in the order they will be checked.**

1. **The mechanism did not fire.** If the Herfindahl index of the class mix per
   engine at 60 req/s does not rise, the term did not change any ordering and the
   result is a null, not a negative. Check the start-up line for
   `classharm=true` first. A null here would itself be informative: it would mean
   the incumbent sum dominates the ordering and the class term is too small to
   decide anything, which is a statement about `fsHarmCap`.
2. **It fired and did not help.** Offered attainment at 60 req/s must rise above
   the within-session baseline. The baseline for this experiment is candidate A
   with class-harm off, not the shipped policy.
3. **It cost something at the lower rates.** Offered attainment at 15, 30 and 45
   req/s must not fall by more than the within-session repeat spread, or by 4.2
   points where only one repeat is available. At 15 and 30 the ROUTE path takes
   99.9% of decisions, so this term should never be reached; a difference there
   means something other than the flag changed.
4. **It made the memory problem worse.** Preemptions were **zero in every static
   condition of EXP-38 and EXP-40**. If any condition here records a non-zero
   preemption count, that is a result in itself and is recorded whatever happens
   to the score: it would mean the concentration this term produces is already
   past what `capMem` can account for at a static rate, which EXP-41 only saw
   under a moving load.
5. **It only concentrates.** If the separation index rises and neither the score
   nor chat's inter-token latency improves, the term is producing concentration
   without the capacity gain it is supposed to buy.

## 3. Design

| | |
|---|---|
| arms | `fsah` (`--fluidserve-class-harm=true`) and `fsa` (`=false`) |
| **both arms** | `--fluidserve-force-margin=true` — candidate A is in the baseline now |
| rates | 15, 30, 45, 60 req/s |
| repeats | 2, outer loop |
| mix | m1 |
| engine | stock FIFO, four engines, migration off, engine admission off |
| binary | same as EXP-42, `scheduler-exp42-A` md5 `d24861df81d89257f5f16dbbb70bb295` |
| session | one session for all conditions |

**The baseline moves to candidate A.** EXP-42 accepted A on all five of its
pre-registered conditions, so leaving A off here would measure class-harm on top
of a policy that is no longer the one being developed, and turning both on in a
single arm would make the result unattributable. Both arms carry A; the single
difference is class-harm.

**Why 15 and 30 req/s are still run.** They cost an hour and they are the check
that the harness did not change: at those rates the changed line is never
reached, so any difference is a fault rather than an effect.

**45 req/s is bistable and this must be read with that in mind.** EXP-38's
condition routed 11.2% of decisions and scored 88.5; EXP-42's baseline routed
90.6% and scored 99.1. The same code and the same rate produce two operating
regimes, and which one a run lands in persists for the whole eight minutes. A
difference at 45 req/s between two arms of this experiment is therefore only
readable if both arms landed in the same regime, which the route share reports.

## 4. Result — null where it was supposed to act

Finished 2026-08-01 09:46 KST. Sixteen conditions, two repeats per arm per rate,
one session, one binary (the same one EXP-42 ran, asserted by md5 before launch).
All sixteen at 4/4 engines, delivered rate within 0.2% of target, no flags.

### Means over two repeats

| rate | offered | separation index | chat ITL | goodput | preemptions |
|---|---|---|---|---|---|
| 15 | 100.0 → 100.0 | 0.711 → 0.849 | 20.6 → 20.1 | 7,968 → 8,000 | 0 → 0 |
| 30 | 100.0 → 100.0 | 0.888 → 0.891 | 23.5 → 25.4 | 15,657 → 15,635 | 0 → 0 |
| 45 | 99.0 → 96.1 | 0.841 → 0.788 | 35.2 → 36.1 | 21,840 → 21,501 | 0 → 0 |
| **60** | **48.2 → 48.3** | **0.501 → 0.509** | **45.3 → 45.4** | 16,693 → 16,641 | 0 → 0 |

**At 60 req/s nothing moved.** Offered attainment +0.1, separation +0.008, chat's
inter-token latency +0.1 ms, goodput −52 tokens/s. Every one of those is far
inside the repeat spread of the arm itself.

### Why: candidate A removed the path this term acts on

The class term enters `harm`, which orders candidates **only when none of them is
feasible**. On that path the sole decision that actually places a request is
FORCE. Candidate A cut FORCE from 6.5% of decisions to 1.4% by rejecting the
placements it used to make, so the ordering this term changes now decides where
1.4% of requests go.

| | force share at 60 req/s |
|---|---|
| shipped policy (EXP-42 `fsbase`) | 6.5% |
| candidate A (this experiment's baseline) | **1.4%** |

**The two candidates interact, and the design of this experiment is what exposed
it.** Moving the baseline to A was the right call for attributing a result — but
the result it attributes is "on top of A, at these rates, this term does
nothing", not "this term does nothing". Whether it would have mattered on the
shipped policy, where its reach was 6.5% rather than 1.4%, is not measured here
and is now of limited interest: the policy has moved.

### The 45 req/s difference is the bistability, not the term

The −3.0 points at 45 come from one condition:

| arm | repeat | route share | offered |
|---|---|---|---|
| fsa | 1 | 87.3% | 98.9 |
| fsa | 2 | 90.2% | 99.1 |
| fsah | 1 | **36.7%** | **92.5** |
| fsah | 2 | 95.9% | 99.6 |

`fsah` fell into the low-routing regime once in two runs and `fsa` zero times in
two. That is one event, and EXP-42 §4 recorded the same condition producing both
regimes on the shipped policy within one session. **Two repeats cannot separate
"this term makes the bad regime more likely" from "the bad regime happens".**
It is a reason to include 45 req/s in the knee measurement with four or more
repeats, not a finding.

### The separation index at 15 and 30 req/s is not readable

`fsa` reads 0.772 and 0.651 at 15 req/s; `fsah` reads 0.834 and 0.863. The
difference between the arms (0.14) is the same size as the spread inside one arm
(0.12). At those rates routing takes 99.7–99.9% of decisions, so the changed
term is essentially never reached and there is nothing for it to do — the
variation is which engine happened to collect which class, which §37.3 measured
as arbitrary and decided by the first arrival.

### Verdict

**Null on condition 1**, which the pre-registered rules say to record as a
statement about the term's reach rather than as a negative result about the
mechanism. Conditions 2 to 5 pass trivially, because nothing changed.

The flag stays at its compiled default of `true` in the code and **`false` in
the experiments**, because that is what every measurement from EXP-27 to EXP-42
used and there is now a measured reason to believe it does not matter at the
rates tested. If a later change raises the FORCE share again, this becomes worth
re-measuring.
