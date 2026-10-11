# EXP-93 A5: does any distributional criterion prefer FluidServe's class preference?

**Question.** FluidServe prefers, among the instances that pass its feasibility test, the one
already holding the most requests of the arriving request's class. Over the whole hour the
preference moves total SLO attainment by 1.4 points, which is below the pre-registered 3-point
threshold. Per class it moves chat up and the other two classes down. This file asks whether the
preference is defensible on a distributional criterion instead of on the total, and reports the
criteria that favour it next to the criteria that oppose it.

**Answer in one line.** No fairness criterion prefers the class preference. Every criterion that
prefers it is a criterion that rewards helping the majority class: the request-weighted total, the
token-weighted total, and chat's own attainment. Both inequality statistics oppose it over the
hour, and the worst-class floor cannot separate the arms at all. The single distributional gain
that is not a chat gain is that the preference admits 3.1 points more deep research input tokens,
and that extra admitted work does not turn into SLO-met deep research output.

---

## 1. Runs, data path, and what "resolved" means here

Four one-hour replays of the same trace with the same arrival times, differing only in the class
preference flag. No directory name contains `PRERUN`.

| arm | repeat | directory | arrivals scored |
|---|---|---|---|
| preference ON (`fspfx`) | r1 | `results/260822_2141_exp93r1_fspfx_shift` | 98,255 |
| preference ON (`fspfx`) | r2 | `results/260823_0007_exp93br1_fspfx_shift` | 98,231 |
| preference OFF (`fsnoaff`) | r1 | `results/260823_0721_exp93nr1_fsnoaff_shift` | 98,240 |
| preference OFF (`fsnoaff`) | r2 | `results/260823_0834_exp93nbr1_fsnoaff_shift` | 98,247 |

Every run replays the same 99,242 planned arrivals; the four scored counts differ by at most 24
requests (0.02%), which is the warm-up and drain trim that `load_run` applies. Rows are produced
by `exp22_fluidserve.load_run` and segmented by `exp93_mix_shift.read_plan` and
`tag_segments` against `traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.plan.json`. The
denominator throughout is `violate_offered`: every arrival counts, and a rejection is a violation.
Requests whose outcome the end of the run cut off are excluded from every numerator and
denominator (218 to 236 per run, 0.22 to 0.24% of arrivals).

**Resolution rule.** Each arm has two repeats. For every quantity I report both repeats, the
arm mean, and the repeat spread `|r1 - r2|` for each arm. I call a difference *resolved* only when
`|mean(ON) - mean(OFF)|` exceeds the larger of the two repeat spreads. A difference that does not
clear that bar is reported as "not a difference" and is not used in any conclusion.

**Segments.** The trace runs four 15-minute segments whose chat share by request count is
93.0%, 33.3%, 76.9% and 60.0%. Arrival counts per segment are 21,983 / 22,504 / 24,143 / 29,614
(mean over the four runs), so the offered rate rises from 24.4 to 32.9 requests per second across
the hour as well as the mix changing.

---

## 2. (a) Worst-class attainment: the floor does not separate the arms over the hour

Per-class SLO attainment on the offered denominator, in percent of that class's arrivals.

| quantity | ON r1 | ON r2 | ON mean | OFF r1 | OFF r2 | OFF mean | ON − OFF | larger spread | resolved |
|---|---|---|---|---|---|---|---|---|---|
| all requests | 75.29 | 75.66 | **75.47** | 73.93 | 74.19 | **74.06** | **+1.41** | 0.37 | yes |
| class-equal mean | 64.01 | 64.09 | **64.05** | 63.77 | 63.64 | **63.70** | **+0.34** | 0.13 | yes |
| chat | 86.59 | 87.51 | **87.05** | 84.35 | 84.83 | **84.59** | **+2.46** | 0.92 | yes |
| deep research | 57.16 | 55.46 | **56.31** | 56.22 | 56.78 | **56.50** | −0.19 | 1.70 | no |
| swe (agent) | 48.28 | 49.29 | **48.79** | 50.73 | 49.31 | **50.02** | −1.24 | 1.42 | no |
| **worst class (min)** | 48.28 | 49.29 | **48.79** | 50.73 | 49.31 | **50.02** | −1.24 | 1.42 | **no** |

The worst class is swe in all four runs. The floor is 1.24 points lower with the preference on,
and the repeat spread of the preference-off arm is 1.42 points. **The max-min criterion cannot
separate the two arms over the hour.** It does not favour the preference and it does not oppose
it; it is silent.

The per-class deltas here are chat +2.46, deep research −0.19, swe −1.24. The brief quotes
+2.7 / −0.1 / −1.9. The chat and deep research figures agree within the repeat spread and the swe
figure differs by 0.7 points, which is also inside the 1.42-point spread of the preference-off arm.
Nothing in this file depends on which of the two swe figures is used, because neither is a
resolved difference.

**Decomposition of the total.** The total is the arrival-weighted sum of the per-class attainments,
and chat is 65.4% of the hour's arrivals. Chat contributes 0.654 x 2.458 = +1.61 points; deep
research contributes 0.215 x (−0.191) = −0.04; swe contributes 0.131 x (−1.236) = −0.16. The three
terms sum to +1.41, so **the chat term alone is 114% of the net gain and the other two classes
subtract from it.** A criterion built on this sum rewards helping the class that carries two thirds
of the requests. That is a utilitarian criterion, not a fairness criterion.

The class-equal mean also favours the preference, by 0.34 points against a spread of 0.13. It is
worth saying plainly that this too is not a fairness criterion: it is a utilitarian criterion with
different weights. An arm can raise the equal-weight mean while making the classes less equal, and
section 4 shows that this is what happens.

---

## 3. (b) Token measures, and which baseline a goodput share is scored against

### 3.1 Definitions

Three token quantities per class, all computed on the same rows as section 2.

- **Input acceptance** = admitted input tokens / offered input tokens. Input tokens are recorded on
  rejected rows, so this denominator is complete and no estimate enters it.
- **Token attainment** = SLO-met output tokens / baseline output tokens, where a request that met
  its class rule contributes all of its output tokens and a request that missed contributes none.
- **Goodput share** = the class's share of the fleet's SLO-met output tokens, scored against a
  baseline share.

### 3.2 The baseline, and why it is not the input mix

The baseline is **the output-token split the fleet would produce if nothing were rejected**:

    baseline_c = n_c x Obar_c ,   baseline share_c = baseline_c / sum_k baseline_k

`n_c` is the class's arrivals and `Obar_c` is the mean output length of its requests that ran to
completion, pooled over all four runs so that both arms are scored against the same baseline.
Pooled values are chat 421.5, deep research 969.7 and swe 503.9 output tokens, from 229,748 /
57,548 / 29,824 completed requests. The per-run means differ from the pooled value by at most 0.8% (swe ranges from 500.4 to 507.7
across the four runs against the pooled 503.9), so the pooled baseline does not favour either arm.

Over the hour this baseline is chat 50.28% / deep research 37.72% / swe 12.00%.

The input-token mix is a different split: chat 19.31% / deep research 41.91% / swe 38.77%. **The
two baselines disagree by a factor of 2.6 on chat and 3.2 on swe**, because a chat request carries
677 input tokens and produces 421 output tokens while an swe request carries 6,805 input tokens and
produces 504. Scoring output goodput against the input mix would report chat at 3.2x its share and
swe at 0.20x, and that ratio would mostly be measuring the prompt-to-completion ratio of the
workload rather than anything the scheduler did. The output baseline asks the question the
scheduler is answerable for: of the output the fleet was asked to produce, whose output did it
produce on time.

The baseline estimates the output that rejected requests would have produced, so it rests on one
assumption, and the assumption is checkable. Rejection is strongly length-selective on the input
side: rejected requests carry 1.3 to 1.8 times the input tokens of admitted ones in every class and
both arms (chat 1,095 against 627 tokens with the preference on, deep research 5,885 against 3,911,
swe 7,832 against 6,041). If output length tracked input length, using the class mean would
understate what the rejected requests would have produced. It does not track it: within a class the
Pearson correlation between input and output length is −0.038 for chat, +0.080 for deep research
and −0.058 for swe, and the above-median-input subgroup's mean output length differs from the class
mean by at most 2.8%. The class-mean baseline is therefore accurate to a few percent, and the same
bias applies to both arms.

### 3.3 Results over the hour

| quantity | ON r1 | ON r2 | ON mean | OFF r1 | OFF r2 | OFF mean | ON − OFF | larger spread | resolved |
|---|---|---|---|---|---|---|---|---|---|
| **input acceptance, chat** | 82.68 | 82.87 | 82.78 | 82.73 | 83.78 | 83.25 | −0.48 | 1.05 | no |
| **input acceptance, deep research** | 61.20 | 60.58 | **60.89** | 57.66 | 57.88 | **57.77** | **+3.12** | 0.62 | yes |
| **input acceptance, swe** | 50.21 | 51.65 | **50.93** | 53.50 | 51.93 | **52.72** | **−1.78** | 1.58 | yes (marginal) |
| token attainment, fleet | 70.85 | 70.71 | **70.78** | 69.47 | 69.65 | **69.56** | **+1.21** | 0.18 | yes |
| token attainment, chat | 87.64 | 88.42 | **88.03** | 85.20 | 85.41 | **85.30** | **+2.73** | 0.78 | yes |
| token attainment, deep research | 57.00 | 55.33 | 56.16 | 56.04 | 56.59 | 56.31 | −0.15 | 1.67 | no |
| token attainment, swe | 44.00 | 44.84 | 44.42 | 45.87 | 44.74 | 45.31 | −0.89 | 1.13 | no |
| **worst-class token attainment** | 44.00 | 44.84 | 44.42 | 45.87 | 44.74 | 45.31 | −0.89 | 1.13 | **no** |
| goodput share, chat (baseline 50.28) | 62.20 | 62.87 | **62.53** | 61.63 | 61.64 | **61.64** | **+0.90** | 0.68 | yes |
| goodput share, deep research (37.72) | 30.35 | 29.52 | 29.94 | 30.44 | 30.66 | 30.55 | −0.61 | 0.83 | no |
| goodput share, swe (12.00) | 7.45 | 7.61 | **7.53** | 7.92 | 7.71 | **7.81** | **−0.28** | 0.22 | yes |

Write `rho_c` for the class's goodput share divided by its baseline share. `rho_c = 1` means the
class received exactly its proportional part of the fleet's on-time output; `rho_c` is also the
class's token attainment divided by the fleet's token attainment.

| rho | ON r1 | ON r2 | ON mean | OFF r1 | OFF r2 | OFF mean | ON − OFF | larger spread | resolved |
|---|---|---|---|---|---|---|---|---|---|
| chat | 1.2370 | 1.2505 | **1.2438** | 1.2264 | 1.2262 | **1.2263** | **+0.0175** | 0.0134 | yes |
| deep research | 0.8046 | 0.7825 | 0.7935 | 0.8066 | 0.8124 | 0.8095 | −0.0160 | 0.0221 | no |
| swe | 0.6211 | 0.6342 | **0.6276** | 0.6603 | 0.6424 | **0.6513** | **−0.0237** | 0.0179 | yes |

Three things follow.

**The token view does not rescue the preference; it repeats the request view.** Both arms
over-serve chat and under-serve swe by a wide margin, and the preference widens both gaps. Chat's
rho rises from 1.226 to 1.244 and swe's falls from 0.651 to 0.628. Moving from requests to tokens
changes the absolute numbers a great deal, because chat is 65.4% of arrivals but 50.3% of the
baseline output tokens, and it does not change the sign of any resolved arm difference.

**The worst class in token terms is still swe, and the floor is still silent.** Token attainment
for swe is 44.42 with the preference and 45.31 without it, against a repeat spread of 1.13. This is
the second criterion of the max-min family, and it also fails to separate the arms.

**The one non-chat gain is an admission gain that does not become goodput.** With the preference on
the fleet admits 3.12 points more deep research input tokens (60.89% against 57.77%, spreads 0.62
and 0.22), yet deep research token attainment is unchanged at 56.16 against 56.31 with a spread of
1.67. The request-level counters show the mechanism directly: deep research rejection falls from
32.78% to 29.92% of its arrivals (spreads 0.53 and 0.23, resolved), while attainment among the deep
research requests that were admitted falls from 84.04% to 80.34% (spreads 1.82 and 0.54, resolved).
The preference lets more deep research in and misses more of what it lets in, and the two effects
cancel to zero on the offered denominator.

For contrast, chat's gain has the opposite structure. Chat rejection is 10.65% with the preference
and 10.53% without it (spreads 0.40 and 0.82), which is not a resolved difference, while attainment
among admitted chat requests rises from 94.55% to 97.42% (spreads 0.58 and 0.33, resolved). **The
preference does not admit more chat; it makes the chat it already admitted meet its 50 ms per-token
budget.** That is the effect the theory predicts, and it appears only for the tight-budget class
that also happens to be the majority class in this trace.

---

## 4. (c) One spread statistic, stated exactly, on attainment and on goodput share

**Definition.** For a vector `x = (x_1, ..., x_n)` of non-negative per-class values, Jain's fairness
index is

    J(x) = ( sum_i x_i )^2 / ( n * sum_i x_i^2 )

`J` lies in `[1/n, 1]`, equals 1 exactly when all classes are equal, and is invariant under
multiplying every `x_i` by the same positive constant. With `n = 3` the minimum is 1/3. I report
`J` as the spread statistic and the plain range `max(x) − min(x)` beside it, because the range is
in the same units as the underlying quantity and section 2 already needs the minimum.

`J` is applied to two vectors: the three per-class attainments, and the three `rho_c`. Because
`rho_c` is the class's token attainment multiplied by one constant, `J(rho)` equals `J(token
attainment)` exactly; the two are one number reported once.

| statistic | ON r1 | ON r2 | ON mean | OFF r1 | OFF r2 | OFF mean | ON − OFF | larger spread | resolved | which arm it favours |
|---|---|---|---|---|---|---|---|---|---|---|
| range of attainment (pts) | 38.31 | 38.22 | **38.26** | 33.62 | 35.52 | **34.57** | **+3.69** | 1.90 | yes | OFF |
| **Jain on attainment** | 0.9386 | 0.9360 | **0.9373** | 0.9494 | 0.9454 | **0.9474** | **−0.0101** | 0.0040 | yes | **OFF** |
| range of rho | 0.6160 | 0.6163 | **0.6161** | 0.5661 | 0.5838 | **0.5749** | **+0.0412** | 0.0177 | yes | OFF |
| **Jain on goodput share** | 0.9220 | 0.9197 | **0.9209** | 0.9333 | 0.9300 | **0.9317** | **−0.0108** | 0.0033 | yes | **OFF** |

Both spread statistics oppose the preference, on both the request measure and the token measure,
and both differences clear the repeat spread by a factor of two or more. The effect is small in
absolute terms — 0.010 of a Jain index whose observed range across these four runs is 0.936 to
0.947 — but it is consistent across all four run pairs and across both units.

---

## 5. (d) The answer depends on the segment, and it depends on it in the direction that matters

Per segment, with the chat share of that segment's requests in the header.

| segment (chat share) | criterion | ON mean (r1, r2) | OFF mean (r1, r2) | ON − OFF | larger spread | resolved | favours |
|---|---|---|---|---|---|---|---|
| **s0_m2 (93.0%)** | total attainment | 97.20 (97.39, 97.02) | 96.30 (96.46, 96.15) | +0.90 | 0.37 | yes | ON |
| | worst-class attainment | 78.59 (79.49, 77.69) | 76.95 (77.54, 76.37) | +1.64 | 1.80 | no | — |
| | worst-class token attainment | 73.16 (74.01, 72.31) | 70.47 (71.63, 69.31) | +2.69 | 2.32 | yes (marginal) | ON |
| | Jain on attainment | 0.9913 | 0.9901 | +0.0012 | 0.0018 | no | — |
| | Jain on goodput share | 0.9841 | 0.9809 | +0.0032 | 0.0032 | borderline | ON |
| **s1_A (33.3%)** | total attainment | 60.98 (62.14, 59.83) | 59.74 (59.69, 59.79) | +1.24 | 2.31 | **no** | — |
| | worst-class attainment | 47.71 (49.35, 46.07) | 47.06 (46.47, 47.65) | +0.65 | 3.29 | no | — |
| | Jain on attainment | 0.9314 | 0.9452 | −0.0138 | 0.0042 | yes | OFF |
| | Jain on goodput share | 0.9066 | 0.9239 | −0.0173 | 0.0093 | yes | OFF |
| **s2_m1 (76.9%)** | total attainment | 85.99 (86.02, 85.96) | 83.85 (84.30, 83.40) | +2.14 | 0.90 | yes | ON |
| | class-equal mean | 75.58 | 76.14 | −0.56 | 0.66 | no | — |
| | worst-class attainment | 56.55 (57.04, 56.05) | 59.63 (59.90, 59.37) | **−3.09** | 0.99 | yes | **OFF** |
| | Jain on attainment | 0.9665 | 0.9765 | −0.0100 | 0.0024 | yes | OFF |
| | Jain on goodput share | 0.9552 | 0.9637 | −0.0085 | 0.0041 | yes | OFF |
| **s3_B (60.0%)** | total attainment | 61.67 (60.02, 63.33) | 60.35 (59.48, 61.22) | +1.32 | 3.31 | **no** | — |
| | worst-class attainment | 34.97 | 33.51 | +1.46 | 2.79 | no | — |
| | Jain on attainment | 0.9177 | 0.9193 | −0.0016 | 0.0224 | no | — |
| | Jain on goodput share | 0.9029 | 0.9049 | −0.0020 | 0.0198 | no | — |

Three readings.

**The preference's gain on the total is resolvable only in the two chat-majority segments.** It is
+0.90 at chat 93.0% and +2.14 at chat 76.9%, both above the repeat spread. At chat 33.3% and chat
60.0% the repeat spread of the preference-on arm is 2.31 and 3.31 points and the difference is 1.24
and 1.32 points, so the total does not separate the arms. This is the segment result that bears
most directly on the theory: the even-mix segment is where grouping classes should free the most
instances from the tight 50 ms chat pace, and it is the segment where the total shows nothing.

**The one place the preference improves the floor is the segment where the fleet is least
loaded.** At chat 93.0% the fleet reaches 96 to 97% total attainment, and there the preference
raises the worst class's token attainment by 2.69 points (73.16 against 70.47) and moves Jain on
goodput share by +0.0032 against a spread of 0.0032. Both are marginal, and both point the same
way. The mechanism is consistent with the theory: when chat is 93.0% of requests and the fleet has
headroom, the preference can give the remaining 7.0% of requests an instance of their own without
costing chat anything. That is the only place in the hour where the preference behaves the way the
argument for it says it should.

**At chat 76.9% the criteria split cleanly and completely.** The total favours the preference by
2.14 points; the floor opposes it by 3.09 points; the class-equal mean is 0.56 points lower with
the preference and does not resolve; Jain opposes it on both units. The token measures show what
was traded: the preference admits 10.51 points more deep research input tokens (87.60% against
77.08%, spreads 0.30 and 0.29) and 6.53 points fewer swe input tokens (56.31% against 62.85%,
spreads 0.12 and 0.01), and it converts neither into goodput — deep research attainment falls 2.09
points and swe attainment falls 3.09 points, both resolved. In this segment the preference took
capacity from swe, gave it to deep research, missed the deep research SLOs anyway, and came out
ahead only because chat gained 3.51 points on 76.9% of the requests.

---

## 6. What favours the preference, what opposes it, and what is silent

Over the whole hour, using the resolution rule of section 1.

**Favours the class preference**
- Total per-request attainment, +1.41 points. Chat's term is 114% of this sum.
- Fleet token attainment, +1.21 points. Chat's term is 113% of this sum.
- Class-equal mean of the three attainments, +0.34 points.
- Chat attainment, +2.46 points, and chat token attainment, +2.73 points, driven by compliance
  among admitted chat rather than by admitting more chat.
- Deep research input-token acceptance, +3.12 points. This is the only resolved gain that is not a
  chat gain, and it produces no change in deep research token attainment.
- In the chat-93.0% segment only: worst-class token attainment +2.69 points and Jain on goodput
  share +0.0032, both marginal.

**Opposes the class preference**
- Range of per-class attainment, 3.69 points wider.
- Jain's index on per-class attainment, 0.9373 against 0.9474.
- Range of the proportional-service ratio rho, 0.0412 wider.
- Jain's index on goodput share, 0.9209 against 0.9317.
- swe's share of fleet goodput, 7.53% against 7.81% against a baseline share of 12.00%.
- swe input-token acceptance, −1.78 points (marginal; the spread is 1.58).
- In the chat-76.9% segment: the worst-class floor, 3.09 points lower.

**Cannot separate the arms**
- Worst-class attainment over the hour, on requests (−1.24 against a spread of 1.42) and on tokens
  (−0.89 against a spread of 1.13).
- Deep research attainment and swe attainment over the hour.
- Everything in the chat-60.0% segment, and the total in the chat-33.3% segment.

**The criteria that favour the preference are all majority-weighted.** Chat is 65.4% of the hour's
arrivals and 50.3% of its baseline output tokens. A criterion that sums over requests or over
tokens gives chat that weight by construction, so an arm that helps chat and hurts the other two
classes can raise it. Raising a majority-weighted sum is a legitimate goal and it is what the
1.4-point total measures, but it is not evidence about distribution. On this trace the two
questions give opposite answers: **the preference raises the average and widens the dispersion.**

**The strongest honest statement in the preference's favour is not distributional at all.** It is
that the preference raises chat's compliance among admitted requests by 2.87 points (97.42% against
94.55%) without admitting more chat, which is the effect the pace argument predicts. Whether that
effect is worth the wider dispersion is a policy choice, and this file does not make it.

---

## 7. What I did not verify

- **No engine-side or scheduler-side counter was used.** Every number here comes from the client's
  `metrics.csv` through `load_run`. I did not open `server_metrics/*.jsonl`, did not attribute
  requests to engines with `exp41_engine_view.attribute_engines`, and therefore did not check the
  fleet-side claims quoted in the brief (chat dispatch effective-instance-count, the dedicated
  instances in the even-mix and last segments, the refusal-reason trade). The stale-instance-id
  hazard does not arise because no instance-keyed series was read.
- **No causal link from the fleet layout to the distributional result.** I show that the preference
  redistributes admitted work and SLO-met output between classes. I do not show which engine
  placements produced that redistribution.
- **Two repeats per arm.** Every resolution verdict rests on a two-point spread, which is an
  estimate of run-to-run variation with one degree of freedom. Differences reported as "marginal"
  (swe input acceptance, the chat-93.0% floor, Jain on goodput share in that segment) would change
  verdict under a third repeat. The resolved Jain differences over the hour clear their spreads by
  2.5x and 3.3x and are the most robust findings in this file.
- **The baseline for goodput share is an estimate for the rejected requests.** It assumes a rejected
  request would have produced its class's mean completed output length. Section 3.2 bounds the error
  at a few percent by showing output length is nearly uncorrelated with input length within a class,
  but it does not measure what those specific requests would have produced.
- **Truncated requests.** Requests cut off by the end of the run (0.22 to 0.24% of arrivals per run)
  are excluded from all numerators and denominators. I did not check whether their class mix differs
  between the arms.
- **No statistical test.** The repeat-spread rule is the project's convention; I computed no
  confidence intervals and no bootstrap over requests or over time windows.
- **Segment-level rates are not matched across segments.** The offered rate rises from 24.4 to 32.9
  requests per second across the four segments, so a segment comparison mixes a change in mix with a
  change in rate. The arm comparison within a segment is unaffected, because both arms see the same
  arrivals.

---

# Appendix V: adversarial verification (2026-08-24)

An independent check re-derived the headline from the four run directories by a route that shares
no code with the one above. The route is a standalone reader (`/tmp/a5v/indep.py`) that parses
`metrics.csv` with Python's `csv` module, without pandas and without importing
`exp22_fluidserve`, and that reimplements the window trim, the class map, the corrected
inter-token time, the miss rules and Jain's index from their definitions. Segments were tagged
from the `t_range_s` fields of `traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.plan.json`
rather than through `read_plan` and `tag_segments`.

## V.1 The headline reproduces exactly

Jain's index on the three per-class offered attainments:

| | ON r1 | ON r2 | ON mean | OFF r1 | OFF r2 | OFF mean | ON − OFF |
|---|---|---|---|---|---|---|---|
| section 4 | 0.9386 | 0.9360 | 0.9373 | 0.9494 | 0.9454 | 0.9474 | −0.0101 |
| independent route | 0.93860 | 0.93604 | 0.93732 | 0.94938 | 0.94542 | 0.94740 | −0.01008 |

Jain's index on goodput share reproduces at 0.9209 against 0.9317. Every per-class attainment,
every per-class token quantity, every input-acceptance figure, and all sixteen segment rows of
section 5 reproduce to the last reported digit. The arrival-weighted decomposition of the total
also holds: chat is 65.54% of the hour's scored arrivals, so its term is 0.6554 x 2.4585 = +1.61
points against a net gain of +1.41 points.

Two statistics the file does not report make the two Jain differences stronger than the
repeat-spread rule shows. The four runs separate completely on both units: the larger ON value
(0.9386 on attainment, 0.9220 on goodput share) is below the smaller OFF value (0.9454 and
0.9300). No pairing of one ON run against one OFF run reverses the sign.

## V.2 Failure modes checked, and not found

- **Rejections are not filtered out of the comparison.** All 18,766 rejected rows in run ON r1
  carry `is_error=True`, which is the condition that has removed rejections from other analyses in
  this repository. It does not remove them here, because `load_run` defines
  `cutoff = is_server_terminated & ~rejected & ~errored`. Rejected rows therefore stay in the
  offered denominator and count as violations, which is what the denominator is for.
- **No `grace_cut` rows exist in these four runs.** The `agent` column takes only the values
  `request` (99,242 rows) and `job_summary` (99,242 rows) in each run, so the denominator hazard
  that `grace_cut` rows create in other runs does not arise.
- **The excluded cut-off requests do not differ between the arms.** They number 236 / 224 / 218 /
  227 and their class composition is 172 / 170 deep research on the two ON runs against 155 / 163
  on the two OFF runs. The largest possible effect of this imbalance on deep research attainment
  is 0.08 points, against a repeat spread of 1.70 points. Section 7 lists this as unchecked; it is
  now checked.
- **No PRERUN directory and no dropped repeat.** `results/*exp93*` contains six directories: the
  four used here and two `llmdslo` runs that belong to a different arm. None of the four appears in
  `ms_dev/notes/excluded_runs.tsv`.
- **Attainment is a total over a total, not an average of per-window ratios.** The independent
  route computes each class's attainment as met requests divided by that class's arrivals over the
  whole interval, and reproduces the file's values, so no per-window averaging entered them.
- **The hour-level Jain result is not an artefact of pooling across the four segments.** The two
  segments where Jain on attainment resolves point the same way as the hour (s1_A −0.0138, s2_m1
  −0.0100) and the two that do not resolve are near zero (s0_m2 +0.0011, s3_B −0.0016). No
  reversal of sign hides inside the pooling.
- **Jain's result survives the complement transformation.** Jain applied to the three per-class
  violation rates instead of the attainments also favours the preference-off arm: 0.8249 against
  0.8540, a difference of −0.0291 against repeat spreads of 0.0072 and 0.0086. The conclusion does
  not depend on which of the two complementary quantities is fed to the index.
- **Jain on goodput share survives the estimate for rejected requests.** Assuming a rejected
  request would have produced k times its class's mean completed output length, and sweeping k
  from 0.6 to 2.0, the ON − OFF difference stays between −0.0091 and −0.0117 and stays resolved at
  every k. The correlation bound in section 3.2 also reproduces exactly: Pearson r between input
  and output length among completed requests is −0.038 for chat, +0.080 for deep research and
  −0.058 for swe, and the above-median-input subgroup's mean output differs from the class mean by
  −2.8%, +2.0% and −1.1%.
- **The two arms really are the two arms.** Section 7 states that no engine-side counter was read,
  so the check that the class-preference flag took effect was outstanding. `analysis/request_engine.csv`
  settles it. Each run reports exactly four instance ids, none of them a stale zero-valued id. The
  preference-off runs place a nearly uniform class mix on every engine (chat 74.1 / 71.6 / 69.2 /
  74.1 percent on the four engines of OFF r1), while the preference-on runs concentrate classes
  (ON r2 puts 71.9% deep research on one engine and 86.8% chat on another). The treatment is
  present in the data.

## V.3 Three ways the headline is weaker than its one-line statement

**(1) "No distributional criterion prefers the class preference" is true of the hour and false of
the trace.** Section 5 itself reports two distributional criteria that resolve in favour of the
preference inside the chat-93.0% segment: the worst-class token attainment floor, +2.69 points
against a repeat spread of 2.32, and Jain on goodput share, +0.0032 against a spread of 0.0032.
The independent route confirms both, including how narrowly the second one clears (+0.00320
against 0.00318). A floor and an inequality index are exactly the criteria the sentence claims are
unanimous, so the sentence needs its scope stated: over the whole hour, no distributional criterion
prefers the class preference.

**(2) "Every criterion that does favour the preference is majority-weighted" is contradicted by
the file's own table.** Deep research input-token acceptance rises 3.12 points, from 57.77% to
60.89%, against repeat spreads of 0.62 and 0.22, and deep research is 21.4% of arrivals and 37.7%
of baseline output tokens. Section 3.3 already calls this "the only resolved gain that is not a
chat gain". The summary sentence in section 6 overstates what the table supports. The defensible
form is narrower: every criterion that favours the preference is either majority-weighted or
measures admitted work rather than SLO-met output.

**(3) Jain on goodput share depends on the baseline in a way section 3.2 does not report.** The
file tests one alternative baseline, the input-token mix, and rejects it on grounds of what it
measures; the independent route confirms it gives the same sign (−0.0069, resolved). A third
baseline gives a different answer. Scoring each class's share of SLO-met output tokens against its
share of *arrivals* — a baseline that needs no estimate of what rejected requests would have
produced — leaves the hour unresolved at −0.0002 against a repeat spread of 0.0110, and resolves
in favour of the preference in two segments (s2_m1 +0.0095 against a spread of 0.0019, s3_B
+0.0145 against 0.0118). That baseline is the least defensible of the three, because it charges
deep research nothing for producing 970-token answers where chat produces 421, but the file's
scale-invariance argument means the baseline is the whole content of `rho`, so the choice deserves
a stated sensitivity. **Jain on per-class attainment carries no baseline and is untouched by this.**

## V.4 What generates the hour-level Jain gap

Holding two of the three per-class attainments at their preference-off means and moving the third
to its preference-on mean decomposes the −0.0100 gap:

| class moved to its ON value | Jain | share of the gap |
|---|---|---|
| chat, 84.59 -> 87.05 | 0.9411 | 63.4% |
| swe, 50.02 -> 48.79 | 0.9442 | 32.3% |
| deep research, 56.50 -> 56.31 | 0.9471 | 3.0% |

Two thirds of the dispersion widening comes from chat's attainment rising, not from another class
falling. Over the hour neither non-chat class shows a resolved decline: deep research moves −0.19
against a spread of 1.70 and swe moves −1.24 against a spread of 1.42. **Over the whole hour, no
class is resolvably worse off with the preference on, and the hour-level inequality statistic is
mostly recording an improvement in the best-served class.** Resolved harm to a class does exist,
but only inside one segment: at chat 76.9% the swe floor falls 3.09 points against a repeat spread
of 0.99, and section 5 reports it.

## V.5 Verdict

The headline survives in weakened form. Both Jain numbers are correct, robust to the complement
transformation, robust to the estimate for rejected requests, consistent with the segments where
they resolve, and separated completely across the four runs; the strongest single number in the
file is Jain on per-class attainment, 0.9373 with the preference against 0.9474 without it. The
one-line claim overstates them in three respects. Restated to what the runs support:

> Over the whole hour, both inequality statistics resolve against the class preference, and the
> worst-class floor cannot separate the arms. Two thirds of the attainment-inequality movement is
> chat's own gain rather than a resolved loss to another class, so the hour-level statement is
> "the preference raises the average and widens the dispersion", not "the preference harms a
> class". Resolved harm to a class appears in one segment only, at chat 76.9%, where the swe floor
> falls 3.09 points against a repeat spread of 0.99. Three criteria favour the preference without
> being majority-weighted: deep research input-token acceptance over the hour, and the worst-class
> token floor and Jain on goodput share inside the chat-93.0% segment. Jain on goodput share also
> depends on the output-length baseline, and an arrival-count baseline leaves it unresolved over
> the hour.
