# EXP-42 — candidate A: the same allowance margin on the forced-placement test

Written before the run, 2026-08-01 02:10 KST. This is the first of the four
candidates in `fluidserve-implementation.md` §37.6. **One change at a time**, at
the user's instruction, so that a result can be attributed.

## 1. What is being changed and why

Two places in the policy compare the same predicted quantity — the mean time to
produce one token on the instance after this request is placed there
(`c.meanAfter`) — against the same request's budget, and until now they used
different thresholds.

| path | condition | threshold for chat, whose budget is 50 ms |
|---|---|---|
| ROUTE (`evaluate`, fluidserve.go:1259) | `meanAfter ≤ min(gateAllowance, nominalMs) × fsAllowanceUtilisation` | **45.0 ms** |
| FORCE (`missesOwnBudget`, fluidserve.go:1322) | reject if `meanAfter > nominalMs`, otherwise place | **50.0 ms** |

A request whose predicted pace falls between 45.0 and 50.0 ms is therefore
refused a routed placement, held at the gateway until it can wait no longer, and
then placed anyway.

That band is where the failures are. Measured at 60 req/s (EXP-38, two
repeats pooled): chat's realised mean inter-token latency has a median of
**49.9 ms** against its 50 ms budget, and **62% of admitted chat requests miss on
it**. The prediction is not what is wrong — over minutes 50–56 of EXP-41 the
scheduler's predicted step and the engine's observed step agree to within 0.1 ms
on all four instances. What differs between a placement that succeeds and one
that does not is which of the two thresholds it was tested against.

The change makes `missesOwnBudget` use the same margin the routing test already
uses:

```go
budget := req.nominalMs
if p.cfg.forceMargin {
    budget *= fsAllowanceUtilisation      // 0.90, the constant already in use
}
return req.nominalMs > 0 && c.meanAfter > budget
```

**No new constant.** `fsAllowanceUtilisation` is already 0.90 and already applied
on the routing path; this applies it consistently. Only the branch for classes
judged on time-between-tokens is touched. The end-to-end branch (the swe class)
and the time-to-first-token branch are left alone, because changing them is a
different change and would make this result unattributable.

**Why it should not cost anything directly.** Under the offered denominator a
rejection and a miss both score zero, so a request that is going to miss scores
the same whether it is rejected or placed. Placing it is not free: it occupies a
decode slot and its KV for its whole life. Rejecting it instead returns that
capacity to the requests around it.

## 2. What is expected, on what grounds, and what would refute it

**Expected.** At 60 req/s, requests predicted to land between 45 and 50 ms are
rejected instead of forced. Estimating with the refit decode law
(`t = 14.792 + 2.266e-5·M + 0.06759·n`, per engine, from 14,428 EXP-38 per-tick
samples):

- about 62% of admitted chat stops being placed, which is roughly 341 requests
  across the fleet, 85 per engine → `0.06759 × 85` = **−5.75 ms**
- those requests were holding roughly 850 logical tokens each, 72,250 per engine
  → `2.266e-5 × 72,250` = **−1.64 ms**
- total **about −7.4 ms**, taking the realised pace from 49.9 to about 42.5 ms

42.5 ms is below both thresholds, so the requests that remain should meet the
rule and the policy should return to routing rather than forcing.

**These are calculations, not measurements.** They assume the decode law holds
at the new operating point and that the requests removed are the ones the
estimate names.

**Refutation conditions, in the order they will be checked.**

1. **The mechanism did not fire.** If the share of decisions that are `shed` does
   not rise at 60 req/s, the flag did not take effect and the run is void, not
   negative. Check the scheduler's start-up line for `forcemargin=true` first.
2. **It fired and did not help.** If chat's median inter-token latency at 60
   req/s does not fall below **47 ms**, the estimate above is wrong and the
   change is rejected. 47 rather than 42.5 because the estimate is coarse; the
   direction is what is being tested.
3. **It helped the pace and not the score.** If offered attainment at 60 req/s
   does not rise above **45** from the current 35.2, the freed capacity did not
   convert into requests meeting their rule. §33.3 already records one case where
   rejecting earlier moved the numerator and the denominator together and the
   offered score did not move, so this is a real possibility and not a formality.
4. **It cost something elsewhere.** If offered attainment at 45 req/s falls below
   **88.5 − 4.2 = 84.3** (the current value less the largest repeat spread ever
   measured on this arm), the change is rejected regardless of what it does at
   60. Same for 15 and 30 req/s, which are at 100.0 and must stay there.
5. **It only rejects more.** If the rejection rate rises and token goodput at 60
   req/s does not rise above the current 12,359 tokens/s, the change is rejected.

A result that passes 1–2 but fails 3 is still worth recording: it would mean the
pace is not the binding constraint at that load, which contradicts §34.

## 3. Design

| | |
|---|---|
| arms | `fsA` (`--fluidserve-force-margin=true`) and `fsBase` (flag absent, shipped behaviour) |
| rates | 15, 30, 45, 60 req/s (900/1800/2700/3600 rpm) |
| repeats | 2, as the outer loop |
| mix | m1, the same workload as EXP-38 |
| engine | stock FIFO, four engines, migration off, engine admission off |
| binary | one binary for both arms, `scheduler-exp42-A` md5 `d24861df81d89257f5f16dbbb70bb295` |
| session | one session for all conditions |
| `--fluidserve-class-harm` | **false in both arms, set explicitly** (see below) |

**Why class-harm is pinned to false.** While verifying that the new flag reached
the scheduler, its start-up line reported `classharm=false`, which is not the
compiled default and which this run had not asked for. Reading the 120 archived
deployment specs in time order shows the flag was written by one ablation arm on
2026-07-28 10:28 and stayed in the deployment for the 61 conditions that
followed, because `set_scheduler_profiling.py` kept ablation flags it was not
asked to set. EXP-27 pass 2 onward, EXP-28 to EXP-38, EXP-40 and EXP-41 all ran
that way. The script now removes ablations it does not set, and EXP-42 pins this
one to false **in both arms** — not because false is right, but because a
baseline that silently differed from EXP-38 would make this result incomparable
with the experiments it is being read against. Turning it on is EXP-43.
Full account: `fluidserve-implementation.md` §38.

**Both arms run from the same binary**, differing only in the flag, so the
comparison cannot pick up a build difference. The flag is off in the shipped
default, so `fsBase` needs no environment variable and is the same code path
that produced EXP-38's numbers.

15 and 30 req/s are included even though nothing is expected to change there,
because refutation condition 4 needs them and because at those rates the policy
routes 99.7% of the time and never reaches the changed line at all — so a
difference there would mean something is wrong with the harness rather than with
the change.

**Go boolean flags must be written `--flag=value` as one token.** Written
separated, pflag sets the flag true and treats `"false"` as a positional
argument, which is how EXP-25's ablation arm ran four hours with the setting it
was supposed to have turned off. `set_scheduler_profiling.py` writes the joined
form and reads the scheduler's start-up line back to confirm; the run is void
without that confirmation.

## 4. Result — accepted on all five conditions

Finished 2026-08-01 06:08 KST. Sixteen conditions, two repeats of each arm at
each rate, one session, one binary. `run_health.py`: all sixteen at 4/4 engines,
delivered rate within 0.1% of target, no flags. The archived deployment spec for
every arm invocation carries the flag it was supposed to.

**The baseline reproduces EXP-38.** At 60 req/s `fsbase` reads offered 36.1,
SLO-meeting completions 21.3/s, goodput 12,638 tokens/s and chat median
inter-token latency 49.7 ms, against EXP-38's 35.2, 20.69, 12,359 and 49.9. That
is the check that the harness and the binary are the ones those numbers came
from, and it is what makes the difference below readable.

### Means over two repeats

| rate | offered base → A | chat ITL base → A | goodput base → A | rejected base → A |
|---|---|---|---|---|
| 15 | 100.0 → 100.0 (+0.0) | 21.1 → 21.0 | 8,019 → 7,932 | 0.0 → 0.0 |
| 30 | 100.0 → 100.0 (−0.0) | 25.1 → 26.5 | 15,707 → 15,588 | 0.0 → 0.0 |
| **45** | 94.0 → **99.0** (**+5.0**) | 40.1 → **36.1** | 21,060 → **21,835** | 3.6 → **0.7** |
| **60** | 36.1 → **49.3** (**+13.1**) | 49.7 → **44.9** | 12,638 → **16,846** | 35.0 → 46.7 |

Per class on the offered denominator, and all three classes improve at both
loaded rates:

| rate | arm | chat | deepresearch | swe |
|---|---|---|---|---|
| 45 | base | 95.2 | 97.9 | 74.9 |
| 45 | **A** | **99.7** | **100.0** | **90.9** |
| 60 | base | 28.3 | 82.4 | 27.9 |
| 60 | **A** | **41.7** | **92.0** | **44.2** |

### The five conditions

1. **The mechanism fired.** Shed share at 60 req/s 4.3% → 6.8%. The decision mix
   moved the way the change predicts: **force fell 6.5% → 1.4%** as the
   placements it was making became rejections, and **route rose 1.7% → 6.4%**,
   because a fleet running at 44.9 ms rather than 49.7 has instances that pass
   the routing test again.
2. **Chat's inter-token latency fell below 47 ms**: 49.7 → 44.9, target < 47.0.
3. **Offered attainment at 60 req/s rose above 45**: 36.1 → 49.3.
4. **No regression at the lower rates.** 15 and 30 are unchanged to within 0.05
   points, which is the harness check; 45 improved by 5.0.
5. **Token goodput rose**: 12,638 → 16,846 tokens/s, +33%. The rejection rate
   also rose, 35.0 → 46.7%, which is the point — the requests it now rejects are
   the ones it used to place and then miss, and under the offered denominator
   those scored zero either way.

### The estimate was directional and too large

Predicted −7.4 ms on the pace, measured **−4.8**. The sign and the mechanism are
right; the magnitude is not. The estimate assumed every chat request predicted
between 45 and 50 ms would stop being placed and that nothing would take its
place, whereas route rose from 1.7% to 6.4%, so some of the freed capacity was
immediately spent on requests that now pass the routing test. That is the
intended behaviour and it damps the pace change.

### Second result, not pre-registered: 45 req/s stopped being bistable

The baseline reproduced the two regimes §35.6 recorded, **within one session**:

| arm | repeat | route share | offered | rejected |
|---|---|---|---|---|
| base | 1 | 90.6% | 99.1 | 0.6% |
| base | 2 | **9.1%** | **88.9** | **6.6%** |
| **A** | 1 | 90.2% | 99.2 | 0.5% |
| **A** | 2 | 90.7% | 98.8 | 0.9% |

The baseline's two repeats differ by 10.2 points and land in different operating
regimes; candidate A's differ by 0.4 and both land in the routing regime. **Two
repeats each is not enough to establish this** — it is four runs — but the
mechanism is the one the change was made for: forcing placements that will miss
is what pushes the fleet past the gate, and once past it nothing is feasible, so
every subsequent arrival is held and then forced as well. Removing the forced
placements removes the push. If it holds, it matters more than the 60 req/s
number, because a policy whose result at its own knee depends on which regime a
run falls into is not one whose numbers can be quoted.

**Follow-up worth running**: 36, 40, 45 req/s with four or more repeats per arm,
which is the knee measurement already on the list, now with a specific question
attached rather than only "where is the knee".

### What is not established

- Two repeats per condition. The 60 req/s difference (+13.1) is far outside the
  baseline's own spread there (36.1 ± 1.2), and the 45 req/s difference (+5.0) is
  not, because the baseline's spread at 45 is 10.2.
- One mix (m1) and one engine scheduler (stock FIFO). EXP-40 showed the
  control-plane advantage does not depend on the engine, but that was measured
  without this change.
- The change was tested only where it is reachable. At 15 and 30 req/s the
  routing path takes 99.7% of decisions and the changed line is never evaluated,
  so those conditions test the harness rather than the change.
