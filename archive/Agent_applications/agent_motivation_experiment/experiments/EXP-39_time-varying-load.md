# EXP-39 — time-varying load from a real trace

Designed 2026-07-30, before EXP-38 rep2 finished. Not yet run.

## 1. What this replaces, and why

The plan carried since implementation.md §31.7 was a "burstiness sweep": hold
the mean arrival rate fixed and vary its short-timescale variability, on the
argument that the flux projection only earns its keep when the state moves.
**That design is withdrawn. The traces do not support it.**

Measured directly from the raw per-request arrival timestamps of three public
traces, comparing the observed coefficient of variation of the arrival count in
a window against the Poisson value for the same mean:

| trace | mean | 1 s | 5 s | 10 s | 60 s |
|---|---|---|---|---|---|
| Azure conv 2024 (13.3 h) | 25.1 req/s | **1.24×** | 1.90× | 2.50× | 5.33× |
| Azure code 2024 (16.5 h) | 20.2 req/s | 3.57× | 7.71× | 10.83× | 26.15× |
| Azure code 2024, busiest 30 min | 68.8 req/s | **1.32×** | 2.06× | 2.53× | — |
| BurstGPT_1 (61 d) | 0.3 req/s | **1.96×** | 3.62× | 4.98× | 11.76× |

The ratio rises with the window, which means the excess variance lives at long
timescales, not short ones. Azure code's 3.57× at one second over 16 hours is
the diurnal cycle seen through a one-second window: within its own busiest
30-minute stretch the same trace is 1.32× Poisson at one second, and its 99th
percentile second carries 1.38× the mean. **At the seconds timescale real LLM
serving traces are close to Poisson.** A synthetic second-scale burst could be
built, but it could not be defended as realistic, and "we invented an arrival
process under which our policy wins" is the objection it would attract.

What the same traces do show is large variation at the **minute** timescale.
Azure code's per-minute rate over 90 hours: p50 13.4, p90 50.7, p99 67.7, max
79.8 req/s — a five-fold swing between the median minute and the 99th
percentile minute, and a busiest 30-minute window whose mean, 68.8 req/s, is
above this fleet's decode bound of about 50.

## 2. What a minute-scale rate change actually stresses

Not the projection horizon. `horizonSteps` is 100 and the measured pace at
saturation is 50.5 ms, so the projection looks 5.05 s ahead, and the arrival
rate's autocorrelation at that lag is 0.77 in the trace already built. A
snapshot policy re-observes every few hundred milliseconds; a rate that moves
over minutes is fully visible to it.

What a rate change does stress is the **occupancy ramp**. By Little's law the
number of live requests settles at arrival rate times mean lifetime, and the
lifetime here is 10–60 s. So when the rate rises, admissions exceed completions
for as long as occupancy takes to reach its new equilibrium — a duration set by
the request lifetime, not by how fast the rate changed. That interval is exactly
the condition under which the projection differs from the snapshot, because
`proj = kvLogical + inflow − outflow` and the two flows only cancel when the age
distribution of live requests is stationary.

Measured at a fixed 60 req/s, the projection already sits 18.6% below the
snapshot (`obs_kv` 721,215 against `projected_kv` 587,069 per instance), worth
1.89 ms of a 50 ms step. It changes 0.41% of decisions, because at saturation
most instances are far from the feasibility boundary and a 1.89 ms shift rarely
crosses it. **Whether a ramp raises that share is a measurement this experiment
makes, not an assumption it rests on.**

## 3. Where the advantage actually comes from, on the evidence so far

EXP-38 rep1 with the corrected TBT metric (§32), per request, offered
denominator:

| rate | PolyServe | Llumnix SLO | FluidServe |
|---|---|---|---|
| 30 req/s | 59.1 | 99.9 | 100.0 |
| 45 req/s | 27.7 | 50.8 | **88.5** |
| 60 req/s | 16.3 | 27.5 | **34.6** |

The margin is concentrated at 45 req/s and it is concentrated in chat: 89.4%
against 38.6%. Both policies see the same fleet; the difference is what they do
with a request they cannot place now. Llumnix SLO returns
`ErrorNoAvailableEndpoint`, the gateway retries on its own cadence and gives up
at its default 5,000 ms, and that timeout is the same for a chat request whose
budget is 5 s and an agent request whose budget is 30 s. FluidServe holds each
request until `ttftSlo − prefillMs − placementDelayBound − recheckMs`, derived
per class.

**So the mechanism under test is deadline-bounded holding, and the experiment
should be built to test that.** The flux projection is measured alongside, and
if its share does not move, that is recorded as a negative result rather than
argued around.

## 4. Design

**Arms, in one session, arm inside and repeat outside, two repeats.**

| arm | what it is |
|---|---|
| `polyserve` | tier partition, no admission control |
| `slo` | Llumnix SLO filter, gateway default 5,000 ms hold, m1f |
| `slo-hold35` | **the same filter with `--wait-scheduling-timeout=35000`** |
| `fluidserve` | v23 + instrumentation + flux counter |

`slo-hold35` is the point of this run. It gives the baseline the same holding
budget FluidServe has, so the two differ only in **how the budget is spent**: a
single fixed timeout for every class, against a deadline computed from each
class's own SLO. If FluidServe still wins, the claim is no longer "we are
allowed to wait longer" but "the wait is bounded by what the request can
afford", which is a statement about the design rather than about a flag. If
`slo-hold35` closes the gap, that is the more valuable finding and it must be
reported as such.

This is deliberately a **stronger** baseline than the one EXP-38 used. Weakening
the baseline to enlarge a margin is not available: the m1f restatement already
exists to make the SLO arm as strong as a static (ttft, tbt) pair can be, and
anything further in that direction would be tuning the comparison rather than
measuring it.

**Load — a real window, replayed verbatim.** `traces/dynamic/canonical/azcode_w60_m1`,
built by `traces/dynamic/build_replay_window.py`:

| | |
|---|---|
| source | Azure LLM Inference 2024 `code`, per-request timestamps |
| window | 60 minutes starting 2024-05-13 12:47 UTC (minute 5,087 of the file) |
| processing | **none** — no time compression, no rate rescaling, no rank transform |
| arrivals | 125,864 measured (plus a 3,364-arrival lead-in drawn from the window's own first minute) |
| mean rate | 35.0 req/s |
| per-minute rate | 21 → 52 req/s, 19 turning points |

The per-minute profile is
`28 21 23 27 26 25 25 26 30 26 28 26 25 29 30 32 28 26 28 31 30 28 30 32 27 27
26 31 33 32 33 36 30 36 41 37 40 39 40 38 38 39 43 41 39 49 46 48 46 47 41 43
42 45 50 47 45 44 52 50` — it wanders in the mid-20s for the first half hour and
then climbs into the high 40s and low 50s, crossing the region where the
corrected static sweep puts the separation (all policies hold at 30, they
separate most at 45) several times.

Nothing about this window was tuned except **which** window: it was chosen from
90 hours of the source as the 60-minute stretch whose mean rate lands in this
fleet's band and whose internal variation is largest. That choice is stated
rather than hidden, and it is the only degree of freedom exercised. Every
timescale in the file is the timescale that was recorded, which is what the
compressed trace could not offer.

The trace supplies **arrival timing only**. Prompts and outputs come from the
three application workloads, as in every other experiment here. The phrasing is
"arrival timing from a 60-minute segment of the Azure LLM Inference 2024 code
trace, replayed at its recorded rate", never "we replay the Azure trace".

**Mix.** m1 held fixed. EXP-30 measured that mix motion costs nothing on
two-minute segments, which is the timing most favourable to the claim, so
varying it alongside the rate would only confound the rate effect.

**Cost.** `slo`, `slo-hold35` and `fluidserve` at 60 min × 2 repeats is 6 h;
`polyserve` gets one repeat, 1 h, because its behaviour is already characterised
at every static rate and it collapses at all of them. With restarts, about 7.5 h
— one overnight run.

**A second condition, if the first justifies it:** the busiest contiguous 30
minutes of the same trace, also verbatim, mean 68.8 req/s and above the fleet's
capacity throughout. It measures overload behaviour rather than adaptation and
is worth running only if the primary condition shows the policies separating.

## 5. Judgement rules, fixed before the run

1. **Whole run.** FluidServe's per-request offered attainment exceeds
   `slo-hold35`'s by more than the within-session spread (1.4–3.3 points,
   up to 6 at the highest rates). This is the headline and it is the one that
   can fail outright.
2. **By rate band.** The advantage must be concentrated in the 40–50 req/s
   segments and near zero below 30. **If it is uniform across bands, the dynamic
   run has shown nothing the static sweep did not**, and should be reported that
   way rather than as an adaptation result.
3. **Transitions.** Attainment in the 60 s following each rate increase, against
   the steady stretch at the same rate. The adaptation claim is that
   FluidServe's transition penalty is smaller. If both are the same, holding
   helps at a rate level and not at a change of level, which is a different and
   weaker claim than the one being made.
4. **Flux share.** `scheduler_fluidserve_flux_flips_total / flux_evaluations_total`
   at decision level, during rising segments against flat ones. Refutation: if
   it stays inside 0.3–0.5%, the projection is not doing the work under
   time-varying load either, and §31.2's fifth finding extends to this workload
   class. That outcome does not invalidate the run; it relocates the claim onto
   the holding mechanism, where the EXP-38 evidence already puts it.
5. **Recovery.** Seconds for attainment to return to its pre-episode level after
   the rate falls back below 40 req/s. A policy that queued instead of shedding
   carries a backlog and recovers slowly; PolyServe left 49.2% of the deep
   research class unfinished at a fixed 60 req/s, so it should be slowest.

## 6. What must be true before it starts

- EXP-38 rep2 finished and judged, so the static baseline this is read against
  is not one repeat.
- `agent.py`'s per-chunk token estimate fixed (§32.6) and the fix verified on a
  smoke run, so this experiment's records do not need the analysis-side
  correction. The correction is exact either way; carrying both is avoidable
  confusion.
- The trace built and its realised rate profile checked against the plan JSON
  before spending cluster time.
