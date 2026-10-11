# EXP-53 — four control planes on one static rate sweep

Run 2026-08-02 03:43 → 2026-08-03 19:44 KST. Two repeats, 64 conditions, no
failed condition in repeat 2.

## 1. Arms

| arm | policy | migration | workload config |
|---|---|---|---|
| `fluidserve` | **FluidServe v0.1.1 as tagged** — gate slack 1.0, candidates A, C and H2 all off | **off** | m1 |
| `polyserve` | PolyServe, the ported static per-class partition | **off** | m1 |
| `slo` | Llumnix's own SLO-aware policy | **on** | **m1f** |
| `loadbalance` | Llumnix's shipped load-balancing policy, no SLO input | **on** | m1 |

**The two Llumnix arms run with their own migration mechanism enabled and the two
policies under test do not, because neither uses it.** This is an asymmetry in
the baselines' favour and it is stated on every figure. `slo` takes the `m1f`
config for the reason EXP-28 recorded: its `--ttft-slo` and `--tpot-slo` are
single global values, the default decomposition hands it the agent class's 25 ms
as a literal per-token target, and it rejected 98% of that class. Scoring is
unaffected.

Rates 15 / 25 / 35 / 45 / 50 / 55 / 60 / 70 req/s, 8 minutes each, engine cold
restart per condition, stock vLLM FIFO on four engines.

## 2. Result — offered attainment, mean of two repeats (min–max)

| req/s | **FluidServe** | PolyServe | Llumnix SLO | Llumnix |
|---|---|---|---|---|
| 15 | 100.0 | 100.0 | 100.0 | 100.0 |
| 25 | 100.0 | 99.9 | 100.0 | 100.0 |
| 35 | **100.0** | **41.1** | 99.8 | 99.8 |
| 45 | **90.2** (90.0–90.5) | 27.5 | 52.4 | 32.1 |
| 50 | **77.5** (77.1–77.9) | 26.8 | 34.2 | 14.2 |
| 55 | **66.4** (66.3–66.4) | 19.0 | 31.0 | 10.0 |
| 60 | **60.9** (55.0–66.7) | 17.1 | 26.3 | 7.2 |
| 70 | **51.9** (51.5–52.2) | 14.4 | 21.4 | 3.9 |

Token goodput, same aggregation:

| req/s | **FluidServe** | PolyServe | Llumnix SLO | Llumnix |
|---|---|---|---|---|
| 45 | **20,463** | 5,517 | 12,605 | 9,072 |
| 55 | **19,090** | 4,205 | 11,069 | 2,479 |
| 70 | **19,435** | 3,965 | 11,737 | 802 |

**Repeat spread is 0.1–0.5 points almost everywhere.** The one exception is
FluidServe at 60 req/s, 55.0 against 66.7, which is the bistability of §52
appearing in this sweep: one repeat kept an engine free of chat and the other
did not. That cell needs more repeats and the figure carries its error bar.

## 3. Three things the sweep establishes

**Below 35 req/s the policy does not matter.** Four policies within 2% of each
other on goodput at 15 and 25 req/s. Where each one leaves that plateau differs:
PolyServe at 35, the others at 45.

**Total output is similar; goodput is not.** At 70 req/s the four produce
14,900–20,500 tokens/s, a spread of 37%, and their goodput spans 802–19,435, a
factor of **24**. The engines are busy in every arm. What differs is whether the
tokens belong to a request that met its rule. This is the project's opening
claim reproduced as a policy comparison rather than as a load sweep.

**SLO awareness and class awareness buy different amounts.** At 45 req/s,
Llumnix → Llumnix SLO is 32.1 → 52.4, which is what knowing about latency
budgets is worth. Llumnix SLO → FluidServe is 52.4 → 90.2, which is what class
awareness and one quantity deciding both routing and admission are worth. The
second step is larger than the first.

## 4. Two things to check before this becomes a paper figure

**The migration arm distinction may not be what it claims.** The scheduler's
rescheduling loop runs regardless of the engine's `LLUMNIX_ENABLE_MIGRATION`, so
non-zero rescheduling pairs appear in the arms where migration is nominally off
(PolyServe, 90 over the sweep) and not in one where it is on (Llumnix SLO, 0).
Whether any of those decisions became a KV transfer is not established. The
claim "the baselines were given their own mechanism" must not be written until
it is.

**Llumnix SLO's admitted curve rises after 50 req/s and that is not an
improvement.** Its rejections are almost entirely chat — 10.9% of chat at
45 req/s rising to 88.4% at 70 — while deep research is never rejected at any
rate. So the admitted population goes from 76.9% chat to 29.8% chat, and the
class that cannot meet its budget leaves the denominator. Offered attainment
falls monotonically over the same range, 51.9 → 21.1. This is the clearest case
in the project for reporting both denominators.

## 5. Excluded

`results/260802_1912_exp53r2_fluidserve_m1_rpm_900` — the first condition of the
original repeat 2, three minutes in when the chain was stopped to run the
top-up. It has no usable data and the analysis skips it. Repeat 2 was relaunched
under the prefix `exp53p2` rather than reusing `exp53r2`.

Repeat 1 lost `fluidserve` and `polyserve` at 3600 and 4200 rpm to an engine that
did not come up at the seventh of eight rates; those four cells were filled by a
top-up pass under `exp53r1r1`, one rate per job.

## 6. Figures

`results/aggregate_analysis/exp53/` — attainment on both denominators, admitted
only, offered only, class-equal, goodput, per class; `engine_<arm>/` for the
engine layer per condition; `side_by_side_{2700,3300,4200}/` for the four
policies at 45, 55 and 70 req/s.
