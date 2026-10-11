# EXP-49 — candidate H2: project from the rate the occupancy is observed to be moving at

Written before the run, 2026-08-02 01:20 KST, while EXP-48 part 1 is still
running. **One change at a time**: this is the only difference from EXP-48's
condition, and it runs on EXP-48's corrected length profile.

## 1. What is being changed

```go
f.inflow  = f.nDecode * float64(p.cfg.horizonSteps)
f.outflow = p.expectedOutflow(f.live, p.cfg.horizonSteps)
f.proj    = f.kvLogical + f.inflow - f.outflow
```

becomes, behind `--fluidserve-kv-slope-projection`,

```go
delta  = kvSlopeOf(instance) * horizonMs        // tokens/ms, EWMA over status intervals
f.proj = f.kvLogical + delta
```

`kvSlope` is maintained in `observeInstance`, which already holds both ends of a
status interval, with the same smoothing constant `fsPrefillDutyAlpha = 0.1` the
prefill duty cycle uses. `inflow` and `outflow` are still published as the
positive and negative part of the same movement so the existing series keep a
meaning. Before any interval has been measured the slope is zero and the
projection is the occupancy itself, which is the permissive direction.

## 2. Why — the modelled balance has three terms where the quantity has four

Occupancy one horizon ahead is

```
current occupancy
  + the growth of the requests already resident
  − what the completions release
  + the footprint of the requests the scheduler places during the horizon
```

and FluidServe carries the first three. The fourth is absent deliberately: the
projection is meant to describe the instance *before* this one request is added.
At saturation that is not a small omission — EXP-47 measured **5.24 placements
per 500 ms status interval per engine**, so a 4.6 s horizon carries about 48 of
them, and at a mix-weighted footprint of some 1,732 logical tokens that is
**about 83,000 tokens** the projection does not contain.

**Measured (§48.2).** Pairing each published `projected_kv_tokens` with the
occupancy the same engine reported one horizon later.

**These numbers are restated on the corrected length profile, 2026-08-02 01:50
KST.** The first version of this section quoted the scoring done on runs with
the 2026-07-26 profile, where the shipped projection read a mean error of
−101,633 and an absolute error 3.6 times that of making no projection at all.
**Most of that was the stale profile, not the missing arrival term**, and
quoting it here would have overstated what this change has left to fix.

| run | predictor | mean error | MAE | under |
|---|---|---|---|---|
| 45 req/s, **07-26 profile** | shipped | −84,826 | 91,809 | 84.3% |
| 45 req/s, **corrected** (`exp48r1`) | occupancy only | −433 | **38,181** | 54.5% |
| | shipped | **−24,350** | 44,660 | **69.9%** |
| | slope (this change) | +2,933 | 39,545 | 51.4% |
| 60 req/s, **07-26 profile** | shipped | −103,411 | 112,358 | 87.0% |
| 60 req/s, **corrected** (`exp48r1`) | occupancy only | +2,517 | **43,767** | 54.0% |
| | shipped | **−14,410** | 46,362 | **66.0%** |
| | slope (this change) | +6,251 | 44,500 | 51.3% |

What survives the profile correction, and what does not:

- **The bias is still one-sided and still there.** 69.9% and 66.0% against the
  50% an unbiased projection gives, with a mean error of −24,350 and −14,410.
  That is the arrival term, and the slope projection removes it: +2,933 and
  +6,251, 51.4% and 51.3%.
- **The size no longer justifies the change on its own.** On the corrected
  profile the three predictors are within about 15% of each other in absolute
  error, and at 60 req/s making no projection at all is the most accurate of
  them (43,767 against the slope's 44,500). A residual bias of 14,000-24,000
  tokens on a memory capacity near 2.3 M logical tokens is about 1%.

**So the case for this change now rests on where the bias is, not on its
average size** — §48.2's per-occupancy table shows it concentrated on the
engines closest to their limit, which is where preemption happens. **EXP-48
part 2 measures exactly that**, and if the corrected profile alone takes
preemptions on the `full` hour to near zero, this experiment has little left to
fix and the next change should be re-applying candidate C instead, which was the
largest static gain on record (+23.7) and was rejected only on preemptions.

**This experiment is therefore conditional on EXP-48 part 2** and is not
launched before it is read.

## 3. Why this is not the offered-rate projection that was removed in v19

§15.3 and §15.4 removed an arrivals term for three reasons, and each is checked
against this one.

| §15's objection to `offeredRate` | this change |
|---|---|
| the value is a fleet total divided by instance count, so it is identical on every instance and cannot order candidates — its only effect is to tighten every gate at once | measured per instance from that instance's own status pair. §48.2's per-occupancy table shows the error, and therefore the correction, is several times larger on a full engine than on an empty one |
| it charges a 674-token chat request the same background load as a 22k-token swe request, which is how 44% of chat came to be rejected at 3000 rpm | the projection is a property of the instance; what each request is charged is still `costOf`, which is per request and unchanged |
| positive feedback through the rejection rate: `offered = served/(1−s)`, so s rising raises the projection, tightens the gate, and raises s again, with a fixed point at s = 54.6% | the slope counts requests that were **placed**, not requests that **arrived**. Placing more raises the slope, which lowers the headroom, which places less. The loop is negative and settles, the same construction `prefillDutyOf` uses |

## 4. What is given up

The slope says how fast occupancy is moving without saying why, so
`completionProb` and the class length profile leave the projection entirely.
Two consequences worth stating before the run:

- **A burst is extrapolated.** The slope is measured over about 500 ms and
  applied over 4.6 s. The EWMA window is roughly ten intervals, close to the
  horizon itself, but a step change in arrivals is still projected forward for a
  few seconds after it stops. That is the conservative direction on a rise and
  the permissive one on a fall.
- **§13's argument for the release term is not answered, only bypassed.** That
  argument was that charging every resident request its class mean over-predicts
  release for a heavy-tailed length distribution. It remains correct; the slope
  simply does not use a length distribution. If H2 is accepted, the class
  profile still drives `costOf` and `missesOwnBudget`, so it does not become
  dead input.

## 5. Design

| | |
|---|---|
| arms | `fluidserve` (EXP-48's condition, shipped projection) and `fskv` (`--fluidserve-kv-slope-projection=true`), both with A and C off and `FS_CLASS_HARM=false` |
| part 1 | static m1, 45 and 60 req/s, 2 repeats |
| part 2 | `full` hour, 1 repeat per arm |
| binary | `scheduler-exp49-H2` md5 `c3c6a3518092ea80eb7bb4dc1725ae5b`, one binary for both arms |
| profile | EXP-48's corrected `classes[]` in both arms |

The baseline arm is re-run rather than read from EXP-48 for part 1, because the
binary changes between the two experiments and one condition of a baseline is
cheap next to the risk of attributing a binary difference to the flag. For part
2, EXP-48's `full` run is the baseline if it has been made by then.

## 6. Judgement rules, written before any result

**Rule 0 — the mechanism check, and it comes first.** Under this flag the
published `projected_kv_tokens` **is** the slope projection, so scoring the
`fskv` run with `exp48_projection_error.py` measures the deployed projection
directly rather than an offline reconstruction. Required: **mean error within
±20,000 tokens and the under-prediction fraction between 40% and 60%.** If the
deployed projection is still biased, the offline result did not transfer and the
reason has to be found before any score is read, whatever the score says.

1. **Preemptions on `full` below 500**, against a baseline of 1,471 / 1,852 /
   1,605 across three runs of the shipped projection with the old profile, and
   against EXP-48's number with the new one. This is the failure H2 is built to
   remove.
2. **Static offered at 60 req/s not more than 4.2 points below the `fluidserve`
   arm of the same session.** 4.2 is EXP-38's largest measured repeat spread on
   this workload.
3. **Rejection rising while offered does not rise is a failure** (§33.3),
   reported with token goodput beside both denominators.
4. **Refutation of the premise.** If rule 0 passes — the projection is now
   unbiased — and preemptions do not fall and the score does not move, then the
   projection was never what limited admission, and the next thing to measure is
   which term of `feasible` actually refuses placements at saturation.
   `c.feasible` is a conjunction of four conditions and no run has yet recorded
   which of them binds. **This binary answers that**: a counter
   `scheduler_fluidserve_infeasible_total{reason}` is incremented for each
   condition that failed, separately rather than only for the first, so two
   failing together is visible as such. It changes no decision, and it is in
   both arms.

## 7. Result

### 7.1 Part 1: no difference at 60 req/s, and one bad repeat at 45

| arm | rate | rep | offered | admitted | rej% | goodput | chat ITL | route% |
|---|---|---|---|---|---|---|---|---|
| fluidserve | 45 | 1 / 2 | 99.1 / 98.9 | 99.7 / 99.6 | 0.6 / 0.7 | 22,019 / 21,830 | 35.9 / 35.3 | 91.8 / 87.4 |
| **fskv** | 45 | 1 / 2 | 98.2 / **87.8** | 99.6 / 97.5 | 1.4 / **9.8** | 21,734 / 19,970 | 34.2 / 42.9 | 72.4 / **12.5** |
| fluidserve | 60 | 1 / 2 | 61.1 / 59.2 | 97.1 / 91.3 | 36.5 / 34.6 | 19,765 / 19,263 | 43.6 / 43.9 | 9.3 / 7.1 |
| **fskv** | 60 | 1 / 2 | 58.6 / **61.4** | 87.5 / 90.4 | 32.6 / 31.5 | 18,829 / 19,524 | 44.6 / 43.8 | 5.6 / 6.1 |

**At 60 req/s the two arms are the same**: 60.2 against 60.0 mean offered, with
repeat spreads of 1.9 and 2.8. Rule 2 passes — there is no regression — but
there is no gain either. At 45 req/s `fskv` has one repeat at 87.8 with a
rejection rate of 9.8% and a route share that collapses from 72.4% to 12.5%,
against a baseline that reads 99.1 and 98.9. One repeat, at the rate whose spread
has always been about ten points, so it is recorded rather than read.

### 7.2 Rule 0 is ambiguous on a static condition, and that is the rule's fault

| | mean error | MAE | under |
|---|---|---|---|
| `fskv` 45 req/s, **deployed** | **+12,924** | 51,444 | **38.3%** |
| `fskv` 60 req/s, **deployed** | **+15,903** | 37,157 | **31.8%** |
| `fluidserve` 45 req/s, deployed | −42,974 | 68,410 | 72.2% |
| `fluidserve` 60 req/s, deployed | −20,022 | 43,839 | 63.0% |

The magnitude clause passes — both are inside ±20,000 — and the sign flips from
under-predicting to over-predicting, which is the conservative direction. The
centring clause fails: 38.3% and 31.8% against the required 40–60%.

**The reason is that the rule cannot be read on a stationary load.** In a static
condition the occupancy does not trend, so the expected change over a horizon is
zero and the best unbiased predictor is the current occupancy — which is exactly
what the scoring shows: `kv` alone reads +663 and +751 at 49.3–51.8%. Any drift
term can then only add variance plus whatever short-term rise it happens to catch,
and both the deployed slope and an offline reconstruction of it come out slightly
positive. **Rule 0 has to be read on the hour, where the load does trend**, and
that is part 2.

**One candidate explanation was tested and refuted before being written down.**
The deployed filter advances once per status interval and the offline
reconstruction once per published sample, so the deployed one is about twice as
fast; re-running the offline scoring at α = 0.2, 0.35 and 0.5 moves the
under-prediction fraction by less than 2 points and moves the mean *down*, not
up. Filter speed is not the difference.

### 7.3 The counter answers a question four experiments had to infer

`scheduler_fluidserve_infeasible_total{reason}`, as a share of all refusals
(they can sum above 100 because two conditions can fail on the same candidate):

| arm | rate | gate | incumbents | unpredictable | **memory** |
|---|---|---|---|---|---|
| fluidserve | 45 | 97.7 / 97.5 | 1.2 / 1.3 | 1.1 / 1.2 | **never** |
| fskv | 45 | 97.8 / 89.6 | 1.5 / 10.4 | 0.7 / — | **never** |
| fluidserve | 60 | 99.2 / 97.1 | 0.8 / 2.9 | — | **never** |
| fskv | 60 | 89.3 / 90.4 | 10.7 / 9.6 | — | **never** |

**`newKv <= capMem` did not refuse a single placement in any of the eight
conditions.** The pace gate refuses 89–99% of them.

This is the measurement rule 4 asked for, and it bears directly on this
experiment: the projection enters `feasible` only through the memory condition
and through `meanAfter`, and the memory condition never binds. That is why
changing the projection changes the score by nothing at 60 req/s. Under `fskv`
the `incumbents` term does rise from 0.8–2.9% to about 10%, because a higher
projected occupancy raises the predicted pace, but the gate still dominates.

**The caveat that keeps this from being the whole answer**: these are static
conditions, where occupancy peaks near 1,088k against a memory capacity around
2.3 M logical tokens, and no static condition has ever preempted. On the hour the
same engines reach 100%. Whether `memory` binds there is measured in part 2, on a
run that carries the counter.

### 7.4 Part 2, the hour: H2 removes a third of the preemptions and loses on everything else

| whole hour | offered | admitted | rej% | goodput | total tok/s | chat / dr / swe | preemptions |
|---|---|---|---|---|---|---|---|
| EXP-48 `fluidserve` | 70.2 | 95.7 | 26.6 | 17,998 | 18,909 | 73.3 / 81.9 / 32.3 | 2,776 |
| **EXP-49 `fluidserve`** | 69.9 | 95.7 | 27.0 | 18,031 | 18,904 | 72.8 / 82.9 / 31.5 | **2,724** |
| **EXP-49 `fskv` (H2)** | **67.5** | **92.3** | 26.9 | **17,413** | 18,812 | 70.2 / 80.1 / 31.4 | **1,748** |

**The baseline reproduces EXP-48 across sessions to within 0.3 points offered,
0.0 admitted and 52 preemptions**, which is what makes the difference below
attributable to the flag.

H2 takes preemptions from 2,724 to 1,748, a fall of 36%, and pays 2.4 points of
offered attainment, 3.4 of admitted and 3.4% of token goodput. Every segment is
slightly worse: 74.1 → 71.8, 93.1 → 91.2, 68.1 → 64.4, 48.8 → 47.2.

### 7.5 Against the pre-registered rules

**Rule 0 — the projection is genuinely fixed, and that is the point.** On a
trending load, where the rule can be read:

| deployed projection, `full` hour | mean error | MAE | under |
|---|---|---|---|
| `fluidserve` | −26,841 | 38,451 | 70.9% |
| **`fskv`** | **+13,341** | **27,726** | 28.8% |

The mean error moves inside the ±20,000 bound and the absolute error falls 28%.
The centring clause still fails, at 28.8% against 40–60%, and in the conservative
direction: extrapolating a measured rise over a 4.4 s horizon over-shoots when
the rise is transient. **The change does to the projection roughly what it
claimed it would.**

1. **Preemptions below 500: FAIL.** 1,748.
2. **No static regression: PASS.** 60.0 against 60.2 at 60 req/s.
3. **Rejection rising without offered rising: not triggered** — rejection is flat
   at 26.9 against 27.0 and offered is lower, so this is a straight loss rather
   than a shedding artefact.
4. **The refutation clause fires.** Rule 4 said: if the projection is fixed and
   the outcome does not improve, the projection was never what limited admission.
   That is what happened, and the counter now says what does limit it.

### 7.6 The counter on the hour, and a correction to §7.3

§7.3 recorded that `newKv <= capMem` never refused a placement. **That was true of
the static conditions and does not generalise.** Over the hour, as a share of all
refusal-condition events:

| | gate | memory | incumbents | unpredictable |
|---|---|---|---|---|
| `fluidserve` | **90.8%** | **7.2%** | 2.0% | 0.0% |
| `fskv` | 82.3% | 7.8% | 9.9% | 0.0% |

Memory does bind on the trace where engines reach 100% KV — it simply never binds
where they peak near half of capacity. The correction does not change the
conclusion: **the pace gate refuses nine placements in ten**, and the projection
reaches the decision mainly through a condition responsible for 7%.

## 8. Verdict

**Rejected.** H2 is not withdrawn as a description — it fixes the projection, and
that fix is measured on the run itself rather than offline — but it costs 2.4
points of offered attainment and 3.4% of goodput to remove a third of the
preemptions, and rule 1 asked for the preemptions to be removed, not thinned.

**What it establishes is worth more than what it cost.** Two things are now
measured that four experiments had been inferring:

- **The projection is not what limits admission.** §47.3 diagnosed the failure as
  a stock condition (`newKv <= capMem`) being used to control a flow. That
  condition accounts for 7.2% of refusals. The diagnosis was aimed at a term that
  is not the binding one.
- **The pace gate is.** 90.8%. And the one proposal on the table that changes it
  is candidate C, which was measured as the largest static gain ever recorded and
  rejected for a preemption count produced on a length profile now known to be
  wrong about the class C exists to admit.

**EXP-50 re-tests candidate C on the corrected profile.** The flag stays off in
the code.

