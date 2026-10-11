# EXP-50 — candidate C again, on the corrected length profile and with the binding term now known

Written before the run, 2026-08-02 08:10 KST.

## 1. Why this is being run a second time

EXP-46 measured candidate C as **the largest static gain on record** — offered
48.7 → 72.4 at 60 req/s, all three classes up, token goodput 22,334 — and
**rejected it on the hour** for 6,583 preemptions against a threshold of 500.

Two things have changed since, and both bear on that rejection.

**The profile it was measured on was wrong.** EXP-48 found the deep research
class's output-length distribution stated a mean of 282 for a class producing
985, which over-predicted the release term of the KV projection by a factor of
3.4 on the class holding roughly 77% of resident KV. C's mechanism is to let deep
research route where the instance minimum had been refusing it, so **C is the
candidate whose behaviour depends most on that class's model being right**, and
the 6,583 was produced with it wrong.

**The binding constraint has now been measured rather than inferred.** The
counter added for EXP-49 counts which of the four conditions in `feasible`
refused each placement. Over the hour:

| | gate | memory | incumbents | unpredictable |
|---|---|---|---|---|
| `fluidserve` | **90.8%** | 7.2% | 2.0% | 0.0% |
| `fskv` (H2) | 82.3% | 7.8% | 9.9% | 0.0% |

**The pace gate is what refuses placements, and candidate C is the only proposal
that changes it.** EXP-49 rejected H2, which changes the projection; the
projection enters `feasible` through the memory condition, which accounts for
7.2%.

## 2. What is changed

One line in `evaluate`, behind `--fluidserve-own-budget-gate`:

```go
gate := math.Min(f.gateAllowance, req.nominalMs)
if p.cfg.ownBudgetGate {
    gate = req.nominalMs        // the arriving request's own class budget
}
c.gateAfter = gate * fsAllowanceUtilisation
```

`f.gateAllowance` is the minimum nominal budget over every request live on the
instance, so within seconds of a run starting it is chat's 50 ms everywhere —
chat is 76.9% of arrivals. A deep research request with a 100 ms budget is
therefore tested against 45.0 ms and fails on every instance, while the fleet it
cannot enter is running at 55.6 ms, inside its own budget. The incumbents are
still protected on the next line by `f.tightestAllowance`, which uses what each
of them has left rather than what its class was promised.

**Candidate A is off in both arms**, unlike EXP-46 where both arms carried it.
This makes the difference from the `fluidserve` arm one change.

## 3. Design

| | |
|---|---|
| arms | `fluidserve` and `fsc` (`--fluidserve-own-budget-gate=true`), A off and `FS_CLASS_HARM=false` in both |
| part 1 | static m1, 45 and 60 req/s, 2 repeats |
| part 2 | `full` hour, 1 repeat per arm |
| binary | `scheduler-exp49-H2` md5 `c3c6a3518092ea80eb7bb4dc1725ae5b`, one for both arms — it carries C behind its flag, H2 behind a flag that stays off, and the refusal counter |
| profile | the corrected `classes[]` from EXP-48 |

The baseline arm is re-run rather than read from EXP-48 or EXP-49, even though
those two agree to within 0.3 points on the hour (70.2 and 69.9 offered, 2,776
and 2,724 preemptions). It costs one condition per rate and it is what makes the
difference attributable.

## 4. What is expected

**Static, at 60 req/s.** EXP-46 measured C at 72.4 against its baseline's 48.7 on
the old profile. The corrected profile has already moved the baseline from 36.6
to 60.2, so most of that headroom is spent: **the expectation is a gain of a few
points, not twenty**, and a result near the baseline would not be surprising.
What would be surprising is a loss.

**On the hour, the preemption count is the whole question.** Baselines on the
corrected profile: 2,776 (EXP-48) and 2,724 (EXP-49), both on one or two engines
reaching 100% KV. C's mechanism sends more deep research to engines that were
refusing it, which is what produced 6,583 on the old profile.

## 5. Judgement rules, written before any result

1. **Static offered at 60 req/s at least 4.2 points above the `fluidserve` arm of
   the same session**, 4.2 being EXP-38's largest measured repeat spread on this
   workload. Below that, C no longer buys anything now that the profile is
   correct, and the honest conclusion is that EXP-46's +23.7 was mostly the
   profile defect and not the gate.
2. **Preemptions on the hour no worse than the baseline's 2,724–2,776 plus its
   own spread.** The two baseline runs differ by 52, so anything above about
   3,000 is a real rise and C is rejected again — this time on a correct input,
   which settles it.
3. **Rejection rising while offered does not rise is a failure** (§33.3),
   reported with token goodput beside both denominators.
4. **The refusal counter is read whatever the score does.** If C works, the gate
   share should fall from 90.8% and something else should become binding; if the
   gate share does not move, C did not do what it is supposed to do and the run
   is void rather than negative.
5. **Deep research is watched separately.** C exists to let that class route. If
   its offered attainment does not rise, the mechanism did not fire.

## 6. Result

### 6.1 Static: +10.6 points, and the mechanism fired exactly as described

| 60 req/s | offered | admitted | rej% | goodput | chat ITL | route% | chat / dr / swe |
|---|---|---|---|---|---|---|---|
| `fluidserve` rep1/rep2 | 60.3 / 56.8 | 92.5 / 85.1 | 34.3 / 32.8 | 19,345 / 18,346 | 43.6 / 44.3 | 7.9 / 6.3 | 57.3 / 84.7 / 22.0 |
| **`fsc`** rep1/rep2 | **69.2 / 69.1** | **97.1 / 97.4** | **28.2 / 28.4** | **21,687 / 21,613** | 42.0 / 41.4 | 17.7 / 18.3 | **62.2 / 100.0 / 82.9** |

**Rule 1 passes: +10.6 points against a threshold of +4.2**, with a repeat spread
of 0.1 on the treatment arm. The rejection rate falls rather than rises and token
goodput is 15% higher, so this is not §33.3's failure mode. At 45 req/s both arms
are at the ceiling (98.5–99.6).

**Rule 5 passes**: deep research 84.7 → 100.0. And swe, which was not the target,
goes 22.0 → 82.9 — that class is judged end to end, so the time it spends held at
the gateway comes straight out of its budget, and the held share falls (pend
83.2/84.5% → 74.3/73.8%).

**Rule 4 as written was wrong and is corrected here.** It asked for the gate's
*share* of refusals to fall; the share rose, 97.3/96.1% → 99.1/99.3%, because the
other terms fell faster. Read as counts, which is what the rule should have said:
gate refusals 571,970 / 621,316 → **353,105 / 345,479** (−42%) and incumbents
refusals 15,961 / 25,326 → **3,308 / 2,305** (−86%). The mechanism fired.

### 6.2 The hour: preemptions are fixed, and the score is lost to chat

| whole hour | offered | admitted | rej% | goodput | chat / dr / swe | preemptions |
|---|---|---|---|---|---|---|
| `fluidserve` | **69.7** | **95.5** | **27.0** | **17,972** | **72.7** / 83.0 / 31.2 | 2,794 |
| **`fsc`** | 65.3 | 94.2 | 30.7 | 17,473 | 58.3 / **99.9** / **79.2** | **1,873** |
| Llumnix SLO (EXP-45) | 38.3 | 66.3 | 42.1 | 11,353 | 26.4 / 100.0 / 59.4 | 0 |

**Rule 2 passes, and by a wide margin.** Preemptions 2,794 → 1,873, a fall of
33%, against a threshold of "no worse than about 3,000". EXP-46's rejection of C
was 6,583 preemptions on the old profile; **on the corrected profile C does not
break engines, it repairs them.** That question is settled.

**Rule 3 fails.** The rejection rate rises 27.0 → 30.7 while offered attainment
falls 69.7 → 65.3, which is exactly the failure §33.3 defines. Token goodput
falls 2.8%.

Where it goes is entirely one class:

| class | share of requests | baseline | C | change |
|---|---|---|---|---|
| chat | 76.9% | 72.7 | 58.3 | **−14.4** |
| deepresearch | 15.4% | 83.0 | 99.9 | +16.9 |
| swe | 7.7% | 31.2 | 79.2 | +48.0 |

By segment: 74.7 → 71.0, 92.5 → **95.9**, 68.1 → 60.5, 48.4 → 39.5. And minutes
50–56, the window the corrected profile had just won for the first time, goes
back: 35.2 → 21.0, with chat inside it collapsing from 34.3 to 1.9.

### 6.3 The two scoring rules disagree, and both are reported

| aggregation | baseline | C |
|---|---|---|
| **per request** (the headline) | **69.7** | 65.3 |
| equal weight across classes | 62.3 | **79.1** |

**C wins the class-equal average by 16.8 points and loses the per-request one by
4.4**, because chat is 76.9% of the requests and is the class it takes from. This
is the same disagreement EXP-27 recorded from the other side, where PolyServe
held two small classes at 100 while collapsing the one carrying 93% of the
traffic. The pre-registered rule is per request, so C is judged on that; the
class-equal figure is reported beside it rather than used to overturn it.

### 6.4 What made the difference from the static conditions

Decision shares over the hour: route 19.2% → 21.7%, **shed 7.9% → 10.0%**, force
2.1% → 0.6%, pend 70.8% → 67.7%. Refusal counts: gate 2,113,842 → 1,865,341
(−12%), memory 169,864 → 105,359 (−38%), incumbents 45,337 → 17,513 (−61%).

The gate does open, as it did statically. What differs is what fills the space.
At a fixed 60 req/s the freed capacity went to all three classes and chat rose
too (57.3 → 62.2). Over the hour the rate reaches 77 req/s and holds above 65 for
six minutes, and in those stretches the requests that can now pass the gate are
the ones with loose budgets — deep research at 100 ms and swe judged end to end —
so they take the capacity and chat, whose 50 ms budget is the tightest, is shed
instead. **The gate was doing two jobs at once: refusing deep research that could
have been served, and reserving capacity for chat. C removes both.**

## 7. Verdict

**Rejected on the pre-registered rule, and the rejection is worth more than the
previous one because it is a different failure.**

EXP-46 rejected C for 6,583 preemptions. That is now answered: on a correct deep
research length model C *reduces* preemptions by a third, from 2,794 to 1,873.
**The engine-overload objection to C is withdrawn.**

What rejects it now is that the pace gate, besides refusing work that could have
been served, was also the only thing reserving capacity for the tightest-budget
class. Removing it entirely gives that capacity to the two loose-budget classes.
Deep research reaches 99.9 and swe 79.2 while chat falls 14.4 points, and chat is
77% of the traffic.

**That is a statement about what to build next rather than a dead end.** What is
wanted is a gate that judges an arriving request against its own budget — C's
correct half — while still holding some capacity for the class that cannot
absorb any delay. Two shapes are available and neither has been measured:

- **Candidate B** (the user's proposal): put the request's KV footprint into the
  SHED decision, so the classes that would crowd chat are the ones shed first.
  Its premise needs re-measuring on the corrected profile before it is built.
- **A per-class floor on the gate**: apply C, but keep an instance from taking so
  much loose-budget work that its delivered pace passes chat's budget. This is
  close to what `f.gateAllowance` was doing by accident, stated deliberately and
  as a reservation rather than as a refusal.
