# EXP-52 — the gate as one axis with a stated default, not two options

Written before the run, 2026-08-02 16:15 KST, at the user's suggestion that the
chat-versus-loose-budget trade is a parameter rather than a yes/no choice.

## 1. The axis

```go
gate := req.nominalMs
if inst := f.gateAllowance * gateSlack; inst < gate { gate = inst }
c.gateAfter = gate * fsAllowanceUtilisation      // 0.90
```

`f.gateAllowance` is the tightest per-token budget PROMISED to anything live on
that instance; with chat at 76.9% of arrivals it is chat's 50 ms almost
everywhere. `gateSlack` is how far past that promise the instance may be driven
in order to serve a class promised more.

| slack | the gate a deep research request (100 ms) meets on a chat-carrying instance | what it means |
|---|---|---|
| **1.0** | 45.0 ms | the shipped policy: hold the instance to chat's promise, with the 10% margin |
| **1.111** | 50.0 ms | use the whole of what chat was promised and none of the margin |
| **1.4** | 63.0 ms | let the instance run 26% past chat's promise for foreign work |
| **≥ 2.0** | 90.0 ms | identical to candidate C — the instance minimum never binds, because 2.0 is this workload's largest class-budget ratio (100/50) |

**Both ends are measured failures and they fail in opposite directions.** At 1.0,
a fleet delivering 43–47 ms per token refuses a request promised 100, and at
45 req/s that produces the bistability of §52: 5 of 14 runs end with chat on all
four engines, every gate at 45.0 ms, and 15% of decisions routing. At the far
end, EXP-50 measured the whole hour losing 4.4 points of offered attainment
because the capacity freed for deep research and swe comes out of chat.

Incumbents are protected on the next condition regardless, by `tightestAllowance`
— what each of them has LEFT, not what its class was promised.

## 2. Why this is a design statement and not a knob to tune away

The two aggregations disagree along this axis and that is the point. At slack 1
the per-request score is highest, because chat is 76.9% of arrivals. At slack ≥ 2
the class-equal score is highest (EXP-50: 79.1 against 62.3), because deep
research reaches 99.9 and swe 79.2. **The parameter selects a point on that
frontier, and the default is a statement about which objective the fleet is
scored on** — which is exactly the open item v0.1.1 §6.7 records as unstated.

## 2a. Is this the same knob as the 0.90 utilisation factor? Partly, and the part that is not is the point

Raised by the user, 2026-08-02 21:00 KST, and it changes what this experiment has
to measure.

`c.gateAfter = min(gateAllowance * slack, nominalMs) * 0.90`. Where the instance
minimum wins, `slack` and `0.90` multiply and are one coefficient — which is why
`slack = 1/0.90 = 1.111` lands the gate on exactly chat's 50 ms. So in that
regime the two are inverses of each other.

**They are not the same knob because they act on different populations.** 0.90
applies to every request against whatever gate it faces. `slack` only moves the
gate for a request being judged against SOMEONE ELSE's budget: for chat arriving
at a chat-carrying instance `gateAllowance == nominalMs`, so `min(50*slack, 50)`
is 50 for every slack ≥ 1 and **slack does nothing for chat at all.** Lowering
0.90 tightens everyone including chat; raising slack loosens only the classes
whose own budget is looser than the instance minimum.

### What the sweep is actually finding

`feasible` also contains `meanAfter <= f.tightestAllowance`, the least REMAINING
budget among incumbents that can still meet theirs. Measured over the steady
window of EXP-52's own conditions:

| condition | nominal gate | **tightestAllowance** | delivered |
|---|---|---|---|
| slack 1.0, healthy | 62.5 | **63.8** | 50.2 |
| slack 1.0, **collapsed** | **50.0** | **50.2** | 44.8 |
| slack 1.4 | 62.5 | **65.8** | 48.4 |
| candidate C | 62.5 | **66.5** | 46.1 |

Chat requests running below their budget bank slack, so the remaining-budget
check sits at 61–66 ms. **The gate that slack 1.4 produces, 63 ms, is
approximately where that check already is.** So the axis is not "how much may
chat be hurt" but **"at what slack does the nominal gate stop being the tighter
of the two conditions"**, and candidate C is the end where it is removed rather
than merely made loose.

### Why static and dynamic should differ, and what slack is FOR

`tightestAllowance` excludes incumbents that are already past their budgets —
deliberately, since refusing work does not rescue them, and recorded as a risk in
`evaluate`'s comment since EXP-46.

- **Static 45 and 60 req/s**: chat rarely goes past budget, the remaining-budget
  check does its job, and **slack 1.4 and candidate C should be
  indistinguishable.**
- **The hour, in the stretches above 65 req/s**: chat does go past budget, those
  requests drop out of `tightestAllowance`, and **the only floor left is the
  nominal gate**, which is built from budgets that never move.

**So `slack` is the floor that survives when the remaining-budget check stops
protecting anything**, and candidate C removes that floor. This is the
"reservation for the tightest-budget class" named as future work in v0.1.1 §6.2 —
it is not something to build, it is the term already there, exposed as a
parameter.

**This experiment as designed cannot see any of it**, because both its rates are
static. An hour-long part is added: slack 1.0, 1.4 and C on `dyn60_short_m123`.

**Prediction, written before that part runs**: slack 1.4's chat attainment over
the hour lands **between** C's 58.3 and slack 1.0's 72.7, and its offered
attainment is **above** C's 65.3. If 1.4 and C are indistinguishable there, the
mechanism above is wrong — the hole in `tightestAllowance` does not open in
practice — and there is no reason to keep an intermediate value at all.

## 3. Design

| | |
|---|---|
| arms | `fluidserve` (slack 1.0), `fsg11` (1.111), `fsg14` (1.4), `fsc` (candidate C, the slack ≥ 2 end) |
| part 1 | **45 req/s**, four rounds, arm as the inner loop — this is the bistable rate and the outcome there is a coin flip, so it needs counts |
| part 2 | **60 req/s**, two rounds — the saturated rate, where the trade is a score rather than a state |
| binary | `scheduler-exp52-kappa` md5 `f88e9430f21dc74a410a7091ebdea218` |
| profile | the corrected `classes[]` |

## 4. Judgement rules, written before any result

**Rule 0 — the new binary must not have changed the shipped policy.** Slack 1.0
is defined to be exactly what shipped. Its collapse rate at 45 req/s must be
consistent with the 5-of-14 already recorded, and its 60 req/s offered within the
56.8–61.1 range of the last three sessions. If not, the parameterisation changed
something it was not supposed to and nothing else may be read.

1. **Where does the bistability stop?** For each slack, the number of collapsed
   runs out of four (route share below 40%, the threshold fixed in EXP-51).
   Prediction: **1.111 already removes it**, because the gate then lands on
   50.0 ms and the collapsed runs deliver 43–47.
2. **What does it cost chat?** Chat's offered attainment at both rates, per
   slack. Prediction: monotone decreasing in slack, and **the interesting
   question is whether it is flat between 1.0 and 1.111** — if the bistability
   goes away for nothing, that is the default.
3. **Both aggregations reported at every point**, per request and class-equal,
   because they are what the default has to be argued from.
4. **The point to propose is stated in advance**: the smallest slack whose
   collapse count is zero, unless chat's attainment at that slack is more than
   4.2 points below slack 1.0 at either rate — in which case there is no free
   point and the choice becomes an explicit trade to be argued rather than
   measured.

## 5. Result

### 5.1 Rule 0 passes — the parameterisation did not change the shipped policy

Slack 1.0 at 60 req/s reads 58.9 and 59.8 offered, against 56.8–61.1 across the
last three sessions on the previous binary. At 45 req/s it collapsed 2 of 4,
consistent with 10 of 24 recorded. Nothing else here rests on a changed baseline.

### 5.2 45 req/s — the collapse disappears at 1.4 and not before

| slack | n (pooled with all history) | collapsed | probability if it were no better than 1.0 |
|---|---|---|---|
| 1.0 | 24 | 10 (42%) | — |
| 1.111 | 3 | 1 | — |
| **1.4** | **8** | **0** | **1.3%** |
| ∞ (C) | 12 | 0 | 0.14% |

Four extra conditions of 1.4 were run for exactly this reason: 0 of 4 has an 11%
chance of happening to a value that is no better, and 0 of 8 has 1.3%.

### 5.3 60 req/s — the axis is not monotone, and 1.111 is the worst point on it

| slack | offered | admitted | rej% | goodput | chat | dr | swe | route% |
|---|---|---|---|---|---|---|---|---|
| 1.0 | 59.4 | 91.1 | 34.2 | 19,147 | 57.8 | 89.3 | 19.2 | 7.4 |
| **1.111** | **49.7** | 86.7 | **42.4** | 17,197 | **38.8** | 98.9 | 67.1 | 9.4 |
| 1.4 | 63.2 | 95.6 | 33.3 | 20,414 | 54.7 | 100.0 | 82.1 | 16.4 |
| **∞ (C)** | **69.9** | **96.9** | **27.3** | **21,792** | **63.4** | 100.0 | 80.9 | 18.5 |

**1.111 is worse than both ends and it is unstable**: its two repeats read 41.7
and 57.6, a spread of 15.9 points where every other value sits at 0.3–1.2. At
that slack the gate lands on exactly 50.0 ms and the engine delivers close to it,
so the test oscillates around its own threshold. **That `1/0.90 = 1.111` is the
principled point of the axis is not a reason to choose it**, and it is dropped.

**Candidate C wins on everything at this rate, chat included** (57.8 → 63.4).
Deep research gets somewhere to go, the gateway's held requests clear, and chat
shares the room that frees. **Both aggregations therefore pick C here** — per
request 69.9 and class-equal 81.4 are both the maximum. The disagreement that
rejected C in EXP-50 exists only on the hour.

### 5.4 The gap between the two denominators is the rejection rate

Gaps of 31.7 / 37.0 / 32.4 / 27.0 against rejection rates of 34.2 / 42.4 / 33.3 /
27.3%. **Larger slack narrows the gap**, because an open gate means fewer requests
run out of time while held. C is not scoring by refusing more; it refuses less.

### 5.5 Against rules 1–4

1. **Answered**: the bistability stops at 1.4. The prediction that 1.111 would
   already be enough was wrong, and §5.3 says why.
2. **Answered and it is not what was expected**: chat is not monotone decreasing
   in slack. It falls to 38.8 at 1.111, recovers to 54.7 at 1.4, and is *highest*
   at C with 63.4 — above the shipped policy's 57.8.
3. **Both aggregations reported** in §5.3.
4. **The rule selects candidate C, not 1.4.** The smallest slack with zero
   collapses is 1.4, but the rule's escape clause — chat more than 4.2 points
   below slack 1.0 — does not fire for C either, and C is better than 1.4 on every
   static measure. **The rule as written did not anticipate that the largest slack
   would also be the best static point.** What still separates them is the hour,
   which the rule did not cover and which §2a added a part for.

### 5.6 Part 4, the hour — the prediction failed and the intermediate value is dominated

| arm | offered | admitted | rej% | goodput | chat | dr | swe | class-equal | preemptions |
|---|---|---|---|---|---|---|---|---|---|
| **slack 1.0** | **68.3** | **94.6** | **27.7** | **17,650** | **71.3** | 80.8 | 30.8 | 61.0 | 2,609 |
| slack 1.4 | 63.0 | 93.0 | 32.2 | 16,978 | **55.7** | 100.0 | 76.7 | 77.4 | 1,357 |
| ∞ (C) | 64.8 | 94.1 | 31.1 | 17,377 | 57.8 | 99.9 | 78.4 | **78.7** | 1,591 |

§2a predicted slack 1.4's chat would land **between** C's 58.3 and slack 1.0's
72.7, and its offered **above** C's 65.3. **Both are wrong in the same
direction**: 1.4's chat is 55.7, *below* C's 57.8, and its offered is 63.0,
*below* C's 64.8. The intermediate value is not a compromise between the two
ends; it is worse than one of them on every column except preemptions.

**So §2a's mechanism is refuted.** It argued that `gateAllowance` is the floor
that survives when `tightestAllowance` empties out at high load, so an
intermediate slack should retain protection that C gives up. If that were the
mechanism, 1.4 would hold more chat than C. It holds less.

The pre-registered clause was: "if 1.4 and C cannot be told apart there, the
mechanism is wrong and no intermediate value is worth keeping." They can be told
apart, in the wrong direction, which reaches the same conclusion more firmly.
**The axis collapses back to its two ends.**

Preemptions reproduce across sessions where the score does: slack 1.0 reads 2,609
against 2,776 / 2,724 / 2,794 in EXP-48/49/50, and C reads 1,591 against EXP-50's
1,873, inside the 26% spread this quantity is known to have.

## 6. Verdict

**No intermediate value is kept, and no default is changed.**

What the axis actually looks like, over everything measured:

| | slack 1.0 (shipped) | slack 1.4 | ∞ = candidate C |
|---|---|---|---|
| 45 req/s, collapse rate | **42%** (10 of 24) | 0% (0 of 8) | 0% (0 of 12) |
| 60 req/s, offered | 59.4 | 63.2 | **69.9** |
| 60 req/s, chat | 57.8 | 54.7 | **63.4** |
| hour, offered | **68.3** | 63.0 | 64.8 |
| hour, chat | **71.3** | 55.7 | 57.8 |
| hour, class-equal | 61.0 | 77.4 | **78.7** |
| hour, preemptions | 2,609 | **1,357** | 1,591 |

1.4 is not best at anything except the preemption count. **The choice is between
1.0 and C, and it is a genuine trade rather than a measurement gap**: C wins both
static rates and the class-equal aggregation, 1.0 wins the hour on the
per-request aggregation and on chat, which is 76.9% of arrivals.

**EXP-53 runs with slack 1.0**, the configuration tagged as v0.1.1. Changing a
default on a result this mixed is a decision about which aggregation the system
is scored on, and that is stated as open in v0.1.1 §6.7 rather than settled here.
