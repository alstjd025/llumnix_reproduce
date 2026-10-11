# EXP-38 — three policies in one session on the longer-output workload

Started 2026-07-29 14:38 UTC, rep1 finished 17:23 UTC, rep2 expected 20:15 UTC.

This file was written after rep1 completed. The design and the judgement rule
below were fixed before the run and before the analysis respectively, and the
places where that matters are marked; nothing here was chosen after seeing the
number it judges.

## 1. Why

Two things were open.

**The comparison itself had never been made.** FluidServe against Llumnix's own
SLO policy has been open since implementation.md §25.2 because no session ever
contained both. The differences at issue were the same size as the movement
between sessions, so every earlier statement about that pair was a comparison of
two numbers that were not measured against each other.

**The workload changed underneath every earlier result.** The deep-research
class's median output went from 249 to 942 tokens when its system prompt was
given a seven-section report structure and its retrieved notes were made the
question's own (implementation.md §31.3). The fleet now holds about 668k KV
tokens where it held 352k, and the mean output per request went from about 400
to 515. No result measured before that carries over.

The reason for lengthening deep research was a hypothesis: a policy that judges
from the current batch snapshot should get worse as the requests in that batch
live longer, because the state it is judging diverges further from the state the
request will actually meet. §5 below records what happened to that hypothesis.

## 2. Configuration

Three arms in ONE session, arm inside and repeat outside, two repeats.

| | |
|---|---|
| scheduler binary | `scheduler-exp07-v29fluxcount`, md5 `cbbefe34` — v23 plus the instrumentation and the flux-flip shadow counter. No decision reads the counter; the policy logic is identical to EXP-27 pass 3 |
| rates | 900 / 1800 / 2700 / 3600 rpm = 15 / 30 / 45 / 60 req/s, 8 min each |
| mix | m1 (10:2:1 by input-token share) for PolyServe and FluidServe; m1f for the SLO arm |
| engines | 4 × Llama-3.1-70B on B200 TP2, stock FIFO, migration off, engine admission off |
| gateway | FluidServe 500 ms retry / 35,000 ms hold; the other two the upstream default 1,000 ms / 5,000 ms |

**Rates.** The decode bound moves with the mean output per request, so the old
20/40/60/80 no longer sit where they used to. 64 × 400/515 ≈ 50, so 15/30/45/60
reproduces the same positions against capacity — 30%, 60%, 90%, 120% — as the
old list did against 64. Absolute rates are not comparable across the workload
change; position against capacity is.

**Why the SLO arm runs m1f.** It has no end-to-end mode and reads `tbt_ms`
literally. On m1 it judges the agent class at 25 ms per token against
FluidServe's 57.7 and refuses nearly every instance, rejecting 98% of that
class. m1f restates that class as (2,500, 52), the decomposition closest to
FluidServe's nominal pace that still fits the 30 s budget at the measured output
length. Scoring is unaffected: the analysis scores that class end to end
whatever the config says. This makes the baseline as strong as a static (ttft,
tbt) pair can be, so what remains under test is the limitation and not the
configuration.

**Why the gateway hold differs by arm.** 5,000 ms is the gateway's own default
(`cmd/gateway/app/options/config.go`); FluidServe overrides it because `canWait`
derives waits up to about 20 s from the agent class's 30 s budget and a 5 s
ceiling truncates them. Making the window uniform was considered and reverted:
it would have made the baseline a configuration nobody deploys, and EXP-27
through EXP-36 all used the per-policy setting, so it would also have made this
sweep incomparable with all of them.

## 3. Judgement rule, fixed before the analysis

Pre-registered in conversation after rep1's PolyServe and SLO arms were on disk
and before any FluidServe condition was read:

1. FluidServe's per-request offered attainment at 60 req/s must exceed the
   Llumnix SLO arm's 43.7. **Passed** — 53.6.
2. FluidServe's agent-class offered attainment at 60 req/s must exceed the SLO
   arm's 72.3, because the end-to-end mode exists for exactly that class.
   **Refuted** — 29.2. §5.2 records what this costs.

Standing rules: both denominators reported beside the rejection rate and token
goodput; the per-class breakdown reported with them, so a win bought by
abandoning a class is visible; no judgement from one measurement per condition.

## 4. Result — both repeats

All twenty-four conditions passed the health check: four engine metric files all
advancing, offered rate within 1% of target.

**These figures are on the corrected time-between-tokens metric** (implementation.md
§32). Every attainment number recorded before 2026-07-30 judged the per-token
half of the rule against roughly twice its intended budget, because the client
divided each inter-chunk gap by a per-chunk token estimate that is 1.92x the
true token count. The correction is applied in analysis and therefore applies to
these runs retroactively; `FS_LEGACY_TBT=1` reproduces the earlier numbers, and
the pair is tabulated in §32.4. The ranking is unchanged and the margins widen.

Mean over two repeats, with the observed range, per request:

| rate | arm | admitted | **offered** | rej% | goodput | chat / dr / agent (offered) |
|---|---|---|---|---|---|---|
| 15 | all three | 100.0 | 100.0 | 0 | 7,885–8,043 | 100 / 100 / 100 |
| 30 | PolyServe | 58.3 [57.6, 59.1] | 58.3 | 0 | 9,957 | 45.8 / 100 / 100 |
| 30 | Llumnix SLO | 99.9 | 99.9 | 0 | 15,561 | 99.9 / 100 / 100 |
| 30 | **FluidServe** | **100.0** | **100.0** | 0 | **15,687** | 100 / 100 / 100 |
| 45 | PolyServe | 27.7 [27.7, 27.8] | 27.7 | 0 | 5,607 | 12.3 / 54.2 / **99.8** |
| 45 | Llumnix SLO | 58.2 [56.6, 59.9] | 52.9 [50.8, 55.0] | 9.0 | 12,661 | 41.3 / **100** / 80.0 |
| 45 | **FluidServe** | **94.1 [93.9, 94.2]** | **88.5 [88.5, 88.6]** | 5.8 | **20,252** | **89.5** / 97.1 / 63.2 |
| 60 | PolyServe | 16.6 [16.3, 17.0] | 16.6 | 0 | 4,109 | 4.9 / 26.6 / **99.3** |
| 60 | Llumnix SLO | 55.0 [54.1, 55.8] | 26.9 [26.3, 27.5] | 50.3 | 11,267 | 8.8 / **100** / 72.8 |
| 60 | **FluidServe** | **56.3 [55.3, 57.4]** | **35.2 [34.6, 35.8]** | 36.9 | **12,359** | **27.1** / 81.1 / 29.1 |

FluidServe over Llumnix SLO on the offered denominator, against the repeat-to-repeat
range that decides whether a difference is one:

| rate | difference | goodput | spread, FluidServe / SLO |
|---|---|---|---|
| 15 | +0.0 | +2% | 0.0 / 0.0 |
| 30 | +0.1 | +1% | 0.0 / 0.0 |
| **45** | **+35.6** | **+60%** | 0.1 / 4.2 |
| **60** | **+8.3** | +10% | 1.2 / 1.2 |

Both loaded rates clear their spread by a wide margin — the 45 req/s difference
is eight times the larger of the two ranges and the 60 req/s one is seven times.
The two repeats of FluidServe at 45 req/s read 88.5 and 88.6.

Figures and both tables (corrected and, under `legacy/`, the pre-correction
metric) are in `results/aggregate_analysis/exp38/`.

## 4b. rep1 against rep2

rep1 was read and written up before rep2 finished, and its numbers are the left
end of every range in §4 — the per-run rows are in
`results/aggregate_analysis/exp38/exp23_rate_sweep.csv`. The two repeats agree
closely enough that nothing in §5 rests on which one is read: the largest
disagreement in any condition is the Llumnix SLO arm at 45 req/s, 50.8 against
55.0 offered, and the smallest is FluidServe at the same rate, 88.5 against 88.6.

The judgement rule in §3 was pre-registered against the uncorrected metric, so it
is worth checking that the correction does not change either verdict. It does
not. Rule 1 asked whether FluidServe's per-request offered attainment at 60 req/s
exceeds the SLO arm's: 53.6 against 43.7 uncorrected, 35.2 against 26.9
corrected, passed either way. Rule 2 asked whether its agent-class offered
attainment exceeds the SLO arm's: 29.2 against 72.3 uncorrected, 29.1 against
72.8 corrected, refuted either way.

## 5. What the numbers mean

### 5.1 The workload change did not create the condition it was meant to create

Lengthening the deep-research output was expected to hurt a snapshot policy. It
did not: the Llumnix SLO arm holds that class at 100.0% offered attainment at
both loaded rates and beats PolyServe on every aggregate. Two reasons, both
checked against the data rather than assumed.

**The rule that judges deep research does not tighten with output length.** That
class is judged on time to first token and on *mean* time between tokens.
Lengthening its output from 249 to 942 tokens averages the slow steps over
nearly four times as many samples, so at a fixed engine state the class passes
its rule more easily, not less. The class whose rule does get harder with a
longer output is the one judged end to end, and that is the agent class, which
was not lengthened — its 30 s budget is arithmetically incompatible with a
longer output, since the decode floor of 16.4 ms times 2,000 tokens is already
33 s.

**Rejection returns a snapshot policy to the regime where a snapshot is valid.**
The added KV and decode steps raise predicted time to first token, which makes
instances infeasible first for the class with the tightest such budget — chat, at
5 s. The SLO policy then places no chat, the gateway holds each request 5 s and
answers 503. At 60 req/s that removes 64.9% of chat, which is 49.9% of all
arrivals, and the fleet returns to a load at which the snapshot is accurate.
Shedding is not a symptom of the snapshot policy's weakness; it is the mechanism
that keeps it inside its assumption.

So the change made the steady state heavier without making it less stationary.
This is implementation.md §31.2's fifth finding again — the projection machinery
has still only been tested where the state does not move — and it is the reason
the remaining direction is burstiness and the Azure trace rather than more
static rates.

### 5.2 The agent class is FluidServe's worst class, and isolation is why

The refuted prediction. FluidServe rejects 56.5% of the agent class at 60 req/s
and misses 32% of what it admits.

Delivered pace by class at 60 req/s on the corrected metric, mean time between
tokens over served requests, with the per-engine decode batch that produces it:

| arm | chat | deepresearch | agent | agent E2E p50 / p90 | decode batch per engine |
|---|---|---|---|---|---|
| PolyServe | 94.7 ms | 118.6 ms | **21.9 ms** | **10.5 s / 14.7 s** | **22 / 1022 / 22 / 683** |
| Llumnix SLO | **52.1** | 51.8 | 50.9 | 25.3 s / 34.6 s | 315 / 191 / 181 / 189 |
| FluidServe | **48.7** | 50.7 | 49.1 | 26.6 s / 35.2 s | 250 / 249 / 236 / 248 |

The chat column is the whole aggregate result in one number. Chat's budget is
50 ms per token; FluidServe delivers 48.7 and Llumnix SLO 52.1, and the two land
on opposite sides of it. A 3.4 ms difference in delivered pace produces
26.4% against 9.5% offered attainment on the class that is 76.9% of the
requests. Nothing about the two policies' feasibility tests explains a gap that
size; the pace does, and the pace follows from how much work each policy allows
into the batch at once.

PolyServe's partition is visible directly in its batch sizes: two engines carry
22 requests and two carry 1,022 and 683. The tier holding the agent class is
nearly empty, which is why that class gets 21.9 ms per token and lands at a
third of its 30 s budget with 99.3% attainment — the one class any policy here
serves well. The other two tiers are carrying forty-six times as many requests
as the agent tier and deliver 94.7 and 118.6 ms per token against budgets of 50
and 100. **This is what "the demand unit does not reflect the SLO" looks like
when measured: the partition is sized in server-seconds, so it can be exactly
right about how much work each class brings and still put that work where it
cannot meet its deadline.**

Both mixing policies keep their four engines within 10% of each other. For the
agent class that even split is the wrong answer: it puts the median end-to-end
time at 26.6 s against a 30 s budget, below the threshold but with no room, and
the measured output of 479 tokens at the median against 633 at the 90th
percentile carries the upper half of the distribution past 30 s on output-length
spread alone.

Two distinct causes, and they need separating because only one is a defect.

**A gap in `canWait`.** The end-to-end branch computes the deadline as
`budgetMs − after − expectedToks × pace − recheckMs`. The `after` term is a
measured one-sided upper bound with z = 1.65, added in v22 precisely because a
point estimate there produced a pile-up of chat requests at their deadline. But
`expectedToks` and `pace` are still point estimates, so a request is allowed to
wait until its budget is exactly consumed in expectation, with nothing reserved
for the variance of either. This is the same failure the time-to-first-token
branch already had and had fixed, left unfixed on the end-to-end branch. It is
not a constant fitted to this workload: it applies to a term the policy already
treats this way elsewhere.

**A limit of admission-time projection.** Correcting the deadline cannot turn
29.2 into 99.3. As long as the agent class shares a batch with the other two it
receives about 26 ms per token, and at 479 output tokens that consumes most of
its budget however carefully the wait is bounded. For a class whose budget is
largely consumed by its own decode, the only way to make the budget fit is to
make the per-token time small, which requires a small batch — that is a property
of how the fleet is partitioned, not of when a request is admitted. FluidServe
selects the best of the instances that exist and never declines to place a chat
request in order to keep an instance fast for an agent request.

### 5.3 PolyServe's failure is more severe than its attainment number

At 60 req/s, 49.2% of its deep-research requests had not finished when the run
ended (30.0% at 45 req/s). `is_server_terminated` marks requests still in flight
after the post-duration grace, so this is queue backlog and not a measurement
artifact. Those requests leave both denominators, so the reported 62.3% is
computed over the half that finished.

### 5.4 The result is better stated as a capacity, not as a point difference

The margin over Llumnix SLO is +0.1 points at 30 req/s, +37.7 at 45 and +7.1 at
60, which reads as an inconsistent result until the curves are read as curves.
All three policies hold near 100% until they reach a rate at which they cannot,
and what differs is where that rate is. Taking 90% offered attainment as the
line, and interpolating between the rates measured:

| policy | highest rate holding 90% offered |
|---|---|
| PolyServe | below 30 req/s |
| Llumnix SLO | about 33 req/s |
| FluidServe | about 44 req/s |

**FluidServe carries about a third more load at the same SLO.** That is one
number, it does not depend on which rate is chosen to quote, and it is the
statement the rate sweep is actually built to support. The point differences are
what that gap looks like when sampled at four rates, which is why they vary so
much between them: at 30 both policies are above the line and at 60 both are far
below it, so only the middle sample sees the separation.

The interpolation is over a 15 req/s gap, which is too coarse for the number to
be quoted as it stands. **Two more rates, 36 and 40 req/s, would locate both
knees to within 4 req/s** at a cost of three arms times two rates times two
repeats, about 2.8 hours. That is the cheapest remaining measurement with a
direct effect on the headline claim.

## 6. Status and what remains

Both repeats are in and the gaps carrying the claim are far outside the spread
(§4). Two things remain.

**The knee is interpolated over too coarse a grid.** The result is best stated
as an SLO capacity (§5.4) and the grid puts Llumnix SLO's 90% crossing at about
33 req/s and FluidServe's at about 44, from samples 15 req/s apart. Adding 36
and 40 req/s, three arms and two repeats, about 2.8 hours, would locate both to
within 4 req/s. This is the cheapest remaining measurement that moves the
headline number.

**The collection-side fix for §32 can now be applied**, since no sweep is
running. The instructions and the verification are in the scratchpad note
`apply_after_rep2_tbt_fix.md`. The analysis-side correction stays regardless —
it is what makes every run already on disk re-scorable — but leaving the
recorded column wrong invites the next reader to trust it.

The agent-class finding does not depend on rep2 in its direction: 29.2 against
99.3 is far outside any spread measured here.
