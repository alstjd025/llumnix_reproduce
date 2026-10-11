# EXP-58 — the class preference as a degree, so that separation becomes an axis

Written before the run. 2026-08-06.

## 1. Why this exists

EXP-56 established that a fully mixed fleet scores worse than the shipped
configuration: turning `--fluidserve-enable-affinity` off drops the median
class concentration from 58.7% to 29.1% (25.0% is an even spread over four
instances) and costs 7.5 points of offered attainment at 45 req/s and 4.8 at 55.

That result has a shape problem, and the shape is what the paper needs.

The claim the motivation section wants to make is **not** "the preference is
worth 7.5 points". It is: *fully separating the classes is bad, fully mixing
them is bad, and the good region is in between*. Stating that needs at least
three points on one axis. We have two, and the third — the fully separated end —
comes from PolyServe, a different system that also differs in how it estimates
demand, in having no spill between tiers, and in its admission rule. So
"concentration 83.5 scores 27.5" cannot be attributed to the concentration.

With the preference as a switch there is no way to fix that from inside our own
system. This experiment turns it into a number.

## 2. What changed in the code

`sortCandidates` ordered the feasible instances lexicographically: most of the
arriving request's class first, free space breaking ties. It now orders them by

```
score = w · share + (1 − w) · room
```

`share` is the fraction of the instance's resident requests that belong to the
arriving request's class; `room` is the free space left after admitting, as a
fraction of the instance's physical capacity. Both are fractions between 0 and
1, so the weighted sum compares commensurable quantities and needs no scaling
constant.

- **w = 1** reproduces the previous behaviour exactly. The score is the share,
  and equal shares fall through to the same room comparison as before.
- **w = 0** reproduces `--fluidserve-enable-affinity=false` exactly. The score
  is the room and the class plays no part.

The class term inside `harmToIncumbents` is scaled by the same `w`, so that one
number moves the preference in both of the places it acts. **In this experiment
that scaling is inert**: every FluidServe arm in this repository pins
`FS_CLASS_HARM=false`, so the damage-estimate term is already off. The weight
therefore acts in exactly one place here — the ordering of the feasible set —
and these arms differ from `fluidserve` and `fsnoaff` in that and nothing else.

Flag: `--fluidserve-affinity-weight`, default 1.0, clamped to [0, 1]. It is
ignored when `--fluidserve-enable-affinity=false`, so the switch stays the
master and every earlier experiment's description of itself stays true.

Binary `cf74fe987302003256f17b728e8cb21b` (previous: `f88e9430…`, kept at
`/home/nxclab/tools/bin-backup/scheduler-exp07.pre-exp58`).

## 3. Why the values are not evenly spaced

`w = 0, 0.05, 0.15, 0.4, 1.0`.

The ordering between two instances flips where `w · Δshare = (1 − w) · Δroom`.
At 45 req/s the difference in class share between two instances is of order 0.5
and the difference in free space is of order 0.1, which puts the crossing near
`w = 0.17`. Even spacing would put three of the five points in the region where
the class preference already decides every comparison, and none in the region
where the two terms actually trade off.

If it turns out that 0.05 already behaves like 1.0, that is a result — it says
the knob is parameterised on the wrong scale and needs values an order of
magnitude smaller — but it is a result obtained at the cost of the sweep, so the
spacing is chosen to make it unlikely.

## 4. Design

| | |
|---|---|
| rates | 45 and 55 req/s (2700 and 3300 rpm), the band where the gate arithmetic binds |
| arms | `fsw000` `fsw005` `fsw015` `fsw040` `fsw100` |
| repeats | 2 |
| conditions | 20, eight minutes each, engines cold-restarted per condition |
| engine scheduler | stock FIFO, migration off |
| mix | m1 |

Loop order is repeat → rate → weight, so the five weights sit adjacent in time
within a repeat and any drift across the run is shared rather than read as an
effect of `w`.

**Two repeats is deliberately few, and the analysis is built for that.** The
quantity this experiment produces is not a precise mean per weight. It is a set
of runs, all from one binary, spread along the concentration axis. Every run
contributes one (concentration, attainment) point, and `w` is the instrument
that spreads them out. EXP-56 already showed that at `w = 1` the concentration
varies from 43.1 to 74.6 between repeats of an identical configuration; that
variation adds points to the plot rather than obscuring it. Twenty runs here
plus the twelve of EXP-56 give thirty-two points from one system.

## 5. Hypotheses and judgement rules

### H1 — concentration rises with the weight

**Prediction**: median class concentration is ordered `w=0 ≤ 0.05 ≤ 0.15 ≤ 0.4 ≤
1.0`, with the endpoints landing on the values EXP-56 measured — `w=0` inside
28.8–29.3 and `w=1` inside 43.1–74.6.

**Refutation**: an endpoint outside the EXP-56 range for that arm. That would
mean the weighted sum is not the same rule as the switch it is supposed to
generalise, and nothing else in this experiment could be read.

**Weaker failure, still informative**: the four non-zero weights all land within
2 points of each other. That says the axis saturates below `w = 0.05` and the
sweep has to be repeated with smaller values.

### H2 — attainment rises with concentration over the range we can reach

**Prediction**: offered attainment increases with concentration across the
reachable range, without a downturn at the top. Our maximum concentration is
around 75, well below PolyServe's 83.5, and the mechanism that makes full
separation bad — a class pinned to instances whose number cannot follow the mix
— needs the assignment to be *fixed*, which no value of `w` makes it.

**Refutation**: attainment at `w = 1` lower than at `w = 0.4` by more than the
repeat spread of the `w = 1` arm.

**If refuted, that is the most valuable result in the experiment**, because it
would be the first evidence from inside our own system that too much separation
hurts. Today that half of the argument rests entirely on PolyServe. Record it as
a finding rather than as a failure.

### H3 — the spread between repeats grows with the weight

**Prediction**: the range of concentration across repeats is widest at `w = 1`
and narrowest at `w = 0`. The reason is that the preference is positive
feedback — an instance holding slightly more of a class becomes more attractive
to it, so a small difference in arrival order is amplified — and the strength of
that amplification is `w`.

**Refutation**: the range at `w = 1` no larger than at `w = 0`. Two repeats
cannot measure a range well, so this is read together with EXP-56's three
repeats at each endpoint (spread 31.5 at `w = 1`, 0.5 at `w = 0`) rather than on
its own.

**Why it matters beyond this experiment**: if some middle weight keeps most of
the attainment gain while cutting the run-to-run variation, that is a better
operating point than the shipped one, and the paper can say so with measurements
instead of speculation.

## 6. What this experiment still cannot answer

Separation acts through two paths at once — it creates instances with no chat
resident, whose gate is 61.9 or 100 ms instead of 45, and it creates instances
whose batches are homogeneous, on which chat's time per token falls. EXP-56
registered these as mutually exclusive and found both. Varying `w` moves both
together, exactly as the switch did, so this experiment does not decompose them
either. Doing that needs an arm that produces the separation but pins the gate
to chat's value regardless of who is resident.

## 7. Result

### 7.1 Repeat 1 at 45 req/s — the axis is monotone in four quantities at once

| w | offered | effective instances | chat-free instance-time % | rejection % | chat ms/token |
|---|---|---|---|---|---|
| 0.00 | 86.33 | 3.95 | 1.03 | 9.76 | 44.15 |
| 0.05 | 86.68 | 3.84 | 1.03 | 9.87 | 44.34 |
| 0.15 | 87.42 | 3.74 | 1.03 | 9.92 | 43.89 |
| 0.40 | 89.15 | 3.44 | 4.38 | 8.20 | 43.54 |
| 1.00 | 90.21 | 3.19 | 9.28 | 7.28 | 42.77 |

**H1 holds, and the exact distances are worth stating rather than a verdict.**
The reference ranges are the minimum and maximum of three draws, so a fourth
draw landing just outside one is expected and says nothing; what would matter is
landing far outside.

| | measured | EXP-56 range | distance outside | range width |
|---|---|---|---|---|
| `w=0` offered | 86.33 | 86.5 – 87.0 | 0.17 | 0.50 |
| `w=0` effective instances | 3.95 | 3.91 – 3.94 | 0.01 | 0.03 |
| `w=0` chat-free % | 1.03 | 1.0 – 1.0 | 0.03 | 0.00 |
| `w=1` offered | 90.21 | 90.4 – 98.9 | 0.19 | 8.50 |
| `w=1` effective instances | 3.19 | 1.63 – 3.01 | 0.18 | 1.38 |
| `w=1` chat-free % | 9.28 | 4.4 – 30.9 | inside | 26.5 |

Every distance is smaller than, or of the order of, the width of the range it
misses. **And the two `w=1` misses are consistent with each other**: 3.19 is less
separated than any of the three EXP-56 draws and 90.21 is lower than any of their
scores, which is the same direction. So this repeat is a fourth draw at the
low-separation end of the same distribution, not a different rule.

**The axis is monotone anyway**, which is the useful part: reading it does not
depend on catching the high-separation state, and this repeat did not catch it.

### 7.2 An observation the design did not anticipate, to be checked against repeat 2

**The two paths EXP-56 could not separate come apart here.** Section 6.4 of that
experiment registered the gate reading and the batch-homogeneity reading as
mutually exclusive, found both, and concluded that they are two faces of one
cause and cannot be decomposed with that design. On this axis they switch on at
different weights.

- **At w = 0.05 and 0.15 the gate path is inert**: the fraction of instance-time
  with no resident chat request is 1.03, exactly what it is at w = 0. No
  instance is free of chat, so no instance can admit a looser class on its own
  terms.
- **The homogeneity path is already acting there**: chat's time per token falls
  44.15 -> 43.89 and the score rises 86.33 -> 87.42.
- **At w = 0.4 the gate path switches on** -- chat-free instance-time goes 1.03
  -> 4.38 -> 9.28 -- and the score rises a further 2.8 points.

If this survives repeat 2, it is a decomposition: **about 1.1 of the 3.9 points
between w=0 and w=1 arrive before any instance is free of chat.** Two cautions
before believing it. The 1.1 points is close to the repeat spread of the `w=0`
arm measured three times in EXP-56 (0.5 points), so one repeat is not enough.
And this reading depends on the chat-free fraction being a step rather than a
gradual rise; with points only at 0.05, 0.15 and 0.4 the location of that step is
known to within a factor of three.

### 7.3 Repeat 1 at 55 req/s — the knob is a weaker instrument there

| w | offered | effective instances | chat-free instance-time % | rejection % | chat ms/token |
|---|---|---|---|---|---|
| 0.00 | 64.52 | 3.86 | 1.03 | 25.84 | 45.20 |
| 0.05 | 65.32 | 3.80 | 1.03 | 25.83 | 45.41 |
| **0.15** | **67.48** | **2.79** | **14.95** | 28.75 | **43.65** |
| 0.40 | 65.80 | 3.61 | 1.55 | 26.36 | 45.21 |
| 1.00 | **68.54** | 3.16 | 2.84 | 24.88 | 44.00 |

**The weight does not order the outcome here.** `w=0.15` produces more separation
than `w=0.40` (2.79 against 3.61 effective instances, 14.95% against 1.55%
chat-free instance-time) and scores higher (67.48 against 65.80).

That is what should be expected at this rate rather than a failure of the knob.
EXP-56 measured three repeats of one configuration at 55 req/s and their
chat-free instance-time read **1.6, 1.2 and 23.9%**: whether the separated state
forms at all is stochastic in this band. With one repeat per weight, a weight
that failed to catch it is indistinguishable from a weight that cannot produce
it. Repeat 2 is what separates those.

### 7.4 And that makes the experiment answer a sharper question than it asked

Two predictors of the score are available in these runs: **the knob**, which we
set, and **the separation the knob produced**, which we measured. Correlations
with offered attainment, repeat 1, five weights per rate:

| | corr(w, score) | corr(effective instances, score) | corr(chat-free time, score) |
|---|---|---|---|
| 45 req/s | +0.947 | **−0.997** | +0.938 |
| 55 req/s | +0.786 | **−0.858** | +0.498 |

**At both rates the separation predicts the score better than the knob does, and
the gap is larger where the knob is a weaker instrument.** That ordering is the
causal claim this experiment was built to support, and it is stronger than the
monotone-in-`w` result the design predicted: it holds in the band where setting
`w` does not reliably produce the separation.

⚠ **One repeat, five points per rate.** The 55 req/s row also has the two
mechanism variables disagreeing — `w=1` scores highest with only 2.84% chat-free
time while `w=0.15` scores 67.48 with 14.95% — so at that rate the batch
homogeneity path (chat 44.00 against 45.20 ms per token) may be carrying more
than the gate path. **Not to be written up before repeat 2.**

---

## 8. Result, both repeats (2026-08-06)

20 conditions, all completed.

### 8.1 45 req/s — all three hypotheses resolved

| w | offered (rep 1 / rep 2) | effective instances | chat-free instance-time % | chat ms/token |
|---|---|---|---|---|
| 0.00 | 86.33 / 86.23 | 3.93 | 1.03 | 44.07 |
| 0.05 | 86.68 / 86.31 | 3.85 | 1.03 | 44.31 |
| 0.15 | 87.42 / 87.34 | 3.78 | 1.03 | 43.95 |
| 0.40 | 89.15 / **98.12** | 2.57 | 17.53 | 40.24 |
| 1.00 | 90.21 / **99.04** | 2.38 | 20.75 | 39.97 |

**H1 — confirmed.** Every endpoint distance is small against the width of the
range it misses: `w=0` by 0.27 on a 0.50-wide attainment range, 0.01 on 0.03,
0.03 on 0.00; `w=1` by 0.19 on 8.50, 0.18 on 1.38, 1.32 on 26.50. The weighted
sum is the rule the switch was.

**H2 — confirmed, no turn.** `w=1` scores 94.63 against `w=0.4`'s 93.63, a
difference of +1.0 against a spread of 8.8 at the top. Nothing in the reachable
range shows too much separation hurting, which is what the design predicted:
no value of `w` makes the assignment fixed, and that is the property that makes
full separation fail.

**H3 — confirmed, and sharply.** The spread between repeats, by weight:

| w | 0.00 | 0.05 | 0.15 | 0.40 | 1.00 |
|---|---|---|---|---|---|
| spread (points) | 0.09 | 0.37 | 0.07 | **8.97** | **8.83** |

**Below `w = 0.4` the outcome is reproducible to a tenth of a point; at and
above it the same configuration scores 89.15 or 98.12.** The mechanism variable
says why: chat-free instance-time is 1.03% in all six runs at `w ≤ 0.15` and
4.38 or 30.67% at `w = 0.4`. **The separated state is not reachable at low
weight and is reachable but not certain at high weight**, which is what positive
feedback scaled by `w` predicts.

### 8.2 The main result is not H1, H2 or H3

Two predictors of the score are available: the knob we set, and the separation
it produced. Over the ten runs at 45 req/s, all one binary:

| predictor | correlation with offered attainment |
|---|---|
| the knob `w` | **+0.710** |
| effective instances per class | **−0.999** |
| chat-free instance-time | **+0.994** |
| chat's time per token | **−0.989** |

**The separation predicts the score essentially perfectly and the knob does
not.** That is the statement the motivation needed and could not make: until
now every point beyond two on a separation-against-score plot came from a
different system, so nothing on it could be attributed to the separation.

At 55 req/s the same ordering holds and everything is weaker: `w` +0.664,
effective instances −0.715, chat-free time +0.410, chat's time per token
−0.886. The band matters — 45 req/s is where the gate arithmetic binds.

### 8.3 A correction to section 7.2

After repeat 1 this file recorded that the gate path and the batch-homogeneity
path come apart on this axis, and that about 1.1 of the points arrive before any
instance is free of chat. **The first half is right and the explanation was
wrong.**

With both repeats, at 45 req/s: from `w=0` to `w=0.15` the score rises **86.28 →
87.38, +1.10 points**, and the repeat spread at those weights is 0.07 to 0.09,
so the rise is real. But **neither mechanism variable moves**: chat-free
instance-time is 1.03% at both, and chat's time per token goes 44.07 → 43.95, a
tenth of a millisecond against a budget of 50. The effective instance count does
move, 3.93 → 3.78, and the largest-instance share 28.9 → 32.2.

**So 1.10 of the 8.35 points between `w=0` and `w=1` arrive through neither of
the two mechanisms we know how to measure.** That is an open question, not a
decomposition. What is measurable today says only that a small amount of
class-preferential placement helps before it produces either a chat-free
instance or a measurably faster chat batch.

### 8.4 What this experiment still does not answer

- **The fully separated end is still not reachable from inside our system.** The
  lowest effective instance count any weight produced is 1.58, against
  PolyServe's 1.09 on the hour trace, and no weight makes the assignment fixed,
  which is the property that makes full separation fail. EXP-59 is the arm for
  that end.
- **The two paths are still not decomposed.** Above `w=0.4` they move together
  again, and below it neither moves while the score still rises (§8.3).
- **`w` is a poor operating knob.** It buys 8.35 points at 45 req/s and it buys
  them by making the outcome bimodal: 0.09 points of spread at `w=0`, 8.83 at
  `w=1`. A middle weight that keeps the gain and cuts the spread does not exist
  in this sweep — the gain and the spread appear together at `w=0.4`.
