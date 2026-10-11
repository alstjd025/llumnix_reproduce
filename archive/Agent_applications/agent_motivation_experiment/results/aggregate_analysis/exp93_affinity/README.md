# Where the class preference in FluidServe shows a benefit, and where it does not

**Written 2026-08-24.** Source runs: EXP-93, the one-hour mix-shift trace
`dyn60_shift_m2Am1B_b1045` (chat 93.0 → 33.3 → 76.9 → 60.0 % of requests across four
15-minute segments, 99,242 arrivals, identical arrival times in every run).
Two arms, two repeats each, four runs in total:

| arm | run directory | class preference |
|---|---|---|
| `fspfx` r1 | `results/260822_2141_exp93r1_fspfx_shift` | on |
| `fspfx` r2 | `results/260823_0007_exp93br1_fspfx_shift` | on |
| `fsnoaff` r1 | `results/260823_0721_exp93nr1_fsnoaff_shift` | off |
| `fsnoaff` r2 | `results/260823_0834_exp93nbr1_fsnoaff_shift` | off |

The two arms differ in the class preference only. The scheduler start-up line for the
preference-off arm was read at run time and is recorded in
`experiments/EXP-93_mix-shift-stress.md` as `affinity=false, affweight=0.00` with
`prefix=true` and the other FluidServe flags identical to the preference-on arm.

Five dimensions were analysed, each written to an appendix in this directory and each
re-derived by a second reader who tried to refute it: `a1_separation.md` (fleet structure),
`a2_ttft_vs_pace.md` (miss decomposition), `a3_tail.md` (token-stream tail),
`a4_prefix_preemption.md` (engine-side counters), `a5_fairness.md` (distribution across
classes). Every number below carries its denominator and its repeat spread, and a repeat
spread is the range of two values, not a standard error.

## 1. The answer

The class preference builds the fleet structure the theory requires, and the structure is
the only dimension on which the benefit is unambiguous: with the preference on, 25.9 % and
28.2 % of engine-seconds run at a pace looser than the 50 ms chat budget, against 6.3 % and
6.8 % with it off, a 20.5-point difference against a largest repeat spread of 2.23 points
(two repeats per arm, one hour, four engines). That reading is weakened in two ways by its
own verification. Weighted by resident requests instead of by time the difference is 12.7
points (19.3 % and 19.9 % of resident work against 6.4 % and 7.4 %), because the engine the
preference frees is the least loaded one. In the segment where chat is 93.0 % of requests
the time-based difference is largest, 28.0 points, while the work-based difference is 3.0
points, because there the freed engine holds a median of 8 to 9 resident requests against a
per-engine fleet mean of 105. The one place where the structure converts into a resolved
performance gain is the segment where chat is 76.9 % of requests: chat requests answered
above their 50 ms per-token budget fall from 7.06 % and 7.84 % to 0.80 % and 0.89 % of
answered chat, offered attainment rises 2.14 points against a repeat spread of 0.90, and the
gain survives trimming 180 s from each segment boundary. Two dimensions resolve against the
preference. Preemption rises from 10.31 and 10.20 per 1,000 admitted requests to 15.99 and
17.46, about 63 %, against repeat spreads of 1.47 and 0.11. Jain's fairness index over the
three classes falls from 0.9474 to 0.9373 on per-request attainment, with the four runs
completely separated, although 63.4 % of that movement is chat's own gain rather than a
measured loss to another class. No dimension produces an aggregate gain that reaches the
3-point threshold set before the run, and the largest per-segment gain is 2.14 points. The
defensible claim is therefore narrow and it is about structure, not about totals: the class
preference is the only mechanism measured here that frees an engine of chat when chat is the
majority class. In the 93 %-chat segment the preference reaches 27.7 % and 28.3 % of
engine-seconds while the feasibility test alone reaches exactly 0.00 % in both repeats;
where deep research is abundant the feasibility test frees an engine without the preference
(10.4 % to 14.1 % of engine-seconds) and the preference adds 13 to 15 points on top.

**Added after a second adversarial pass on the token-stream tail (2026-08-24).** That dimension
is no longer excluded, and it carries the largest resolved gain in this document, but only
under a named scoring rule. The distribution claim survives every check: pooled over completed
chat requests, the share of token gaps above 200 ms falls from 21.98 / 22.47 % of stream time
to 16.29 / 16.00 %, gaps above 500 ms fall from 81,801 / 87,177 to 45,202 / 39,583, and the
50-200 ms band rises correspondingly; it holds in all four mix segments, and it strengthens
rather than weakens on a length-matched population and on the 44,199 requests answered in all
four runs. What does NOT survive is the unqualified word benefit. Scoring chat on the admitted
denominator against its own 5 s first-token and 50 ms per-token budget:

| rule | preference on | off | difference | repeat spread |
|---|---|---|---|---|
| per-request mean, this project's headline | 96.70 / 97.29 | 94.28 / 93.96 | **+2.87** | 0.59 |
| cumulative deadline, as PolyServe, QoServe and JITServe score | 98.05 / 97.88 | 96.17 / 95.83 | **+1.96** | 0.34 |
| 10-token block mean, as SLOs-Serve implements it | 11.46 / 11.59 | 8.48 / 8.16 | **+3.21** | 0.32 |
| the request's own p90 gap within 50 ms, AdaGen style | 26.55 / 24.60 | 36.33 / 38.41 | **-11.79** | 2.08 |
| the request's own p99 gap within 50 ms | 2.82 / 2.69 | 3.96 / 3.86 | -1.15 | 0.13 |

The dividing line is not strict against loose. It is whether the rule averages over more than
one token: every gap the preference adds is above 50 ms, so a rule that tests a single gap
percentile cannot absorb one, while a rule that averages absorbs it and keeps the far-tail
reduction. Two consequences follow. The p99 improvement quoted in section 2 (320.9 to 264.5 ms)
is real and has no effect at chat's budget, because both values violate every per-token rule at
50 ms; sweeping the budget, the own-p90 rule crosses over between 75 and 100 ms. And chat's
first token improves at the same time, median 637.4 / 651.6 ms down to 466.6 / 449.2 ms, so
this is not a first-token-for-per-token trade in chat's case. Deep research pays: the share of
its gaps above 500 ms rises from 1.086 / 1.072 % to 1.357 / 1.340 %, though its answered
population is 4.7 % larger in the preference-on arm and the two were not separated.

**The gap that matters most here was not closed: no llm-d comparison was run.** This dimension
was chosen because the within-request tail is where this project loses to that baseline, and
nothing in this pass measures the baseline.


## 2. The five dimensions

Reference row first: total offered per-request attainment is 75.29 / 75.66 % with the
preference on and 73.93 / 74.19 % with it off, +1.41 points against a repeat spread of 0.37,
on 99,242 arrivals per run. The threshold set before the run was 3 points.

| dimension | verdict as filed | headline number, with repeat spread | verification |
|---|---|---|---|
| Fleet structure: distribution of per-instance admissible pace over engine-seconds | benefit | engine-seconds looser than the 50 ms chat pace: 25.90 / 28.15 % on, 6.30 / 6.77 % off; 20.5 points against a largest spread of 2.23 | **weakened.** Headline reproduced by a route sharing no join and no discretisation step (25.91 / 28.14 against 6.29 / 6.76). Two supporting numbers corrected: the work-weighted difference is 12.7 points, not 13.9, and the work-weighted share in the 93 %-chat segment is 2.85 / 3.15 %, not 6.14 / 6.63 % |
| Miss decomposition: first-token against per-token, per class, per engine | benefit | segment with chat at 76.9 %: chat answered above its 50 ms per-token budget 0.80 / 0.89 % on against 7.06 / 7.84 % off (spread 0.78); deep research answered above its 10 s first-token budget 11.76 / 12.89 % against 0.78 / 0.68 % (spread 1.13); net −516.5 misses on 24,140 arrivals, +2.14 points of offered attainment against a spread of 0.90 | **yes.** Reproduced to the digit by a reader that imports none of the project's analysis functions. One correction, confined to the engine-side prefill series: those counters were segmented on a time origin 60 s earlier than the request tables, which changes three quoted backlog numbers and no conclusion |
| Token-stream tail, across requests and within a request | harm | median chat request's own p90 token gap 64.75 / 65.04 ms on against 57.12 / 55.36 ms off, spread 1.76 | **no.** The arithmetic reproduces exactly from the raw token-gap log, but the verdict does not follow from it. At an unchanged median own mean gap of 45.0 against 45.1 ms, the same requests' own p99 falls from 320.9 to 264.5 ms (spread 2.81), own maximum from 506.6 to 412.6 ms (spread 3.82), own standard deviation from 55.4 to 46.8 ms (spread 0.43) and the Gini coefficient of their own gaps from 0.347 to 0.326 (spread 0.003). Gap mass moves out of the far tail into the 50–200 ms band, which raises p90 and lowers p99 at the same time. The corrected direction is a benefit for chat's far tail; it is not carried into section 1 because it has had one adversarial pass, not two |
| Engine-side counters: preemption and prefix cache hit rate, normalised by work | harm | preemption per 1,000 admitted requests 15.99 / 17.46 on against 10.31 / 10.20 off, +6.47 against spreads of 1.47 and 0.11, about +63 %; prefix cache hit rate +1.8 to +3.0 points on a base near 48 %, the lower figure inside the preference-on arm's own 1.6-point spread | **yes.** Preemption counts reproduce exactly by an independent differencing route. One defect found and shown not to matter: numerator and denominator used different windows, which moves the rates by 1.2 % uniformly. The two arms ran in different session blocks and this project has measured 26 % session-to-session movement in preemption count for an identical configuration, but a 26 % term still leaves about +30 %, and the two sessions are indistinguishable in the segment where neither arm preempts |
| Distribution across the three classes | harm | Jain's index on per-class offered attainment 0.9386 / 0.9360 on against 0.9494 / 0.9454 off, −0.0101 against spreads of 0.0040 and 0.0026 | **weakened.** Every number reproduced to the last digit, and the four runs are completely separated on this measure. Three weakenings: the claim holds over the hour but not in the 93 %-chat segment, where the worst-class token floor rises 2.69 points and Jain on goodput share rises 0.0032; deep research input-token acceptance rises 3.12 points against a 0.62 spread and deep research is not the majority class; and 63.4 % of the Jain movement comes from chat's own gain, while over the hour no class declines resolvably (deep research −0.19 against a 1.70 spread, swe −1.24 against 1.42) |

## 3. Why the total does not move even where a dimension does

Both candidate mechanisms operate, they act at different layers, and only the first is
measured at the layer where the total is decided. At the placement layer, the preference
moves refusals from the pace test to the memory test almost exactly: over the hour and two
repeats, refusals attributed to the per-token pace gate fall from 71.1 % to 67.4 % of all
refusals and refusals attributed to KV capacity rise from 9.0 % to 15.1 %, a −6.1 against
+6.1 point trade with a repeat spread of 0.4 to 0.8 points, while the number of placements
changes by 1.9 % (76,058 against 74,602 routes) and the number of shed requests by 1.7 %
(18,598 against 18,928). The proposed reason is that both tests read the same quantity, the
tokens an instance currently holds, and read it in opposite directions: an instance holding
more tokens takes longer per token and has less KV space left. The fleet must hold the same
total tokens whichever way requests are grouped, so relieving one test tightens the other.
That reasoning is inference from the equality of the two movements and from token
conservation; no per-decision trace was built linking a pace refusal that disappeared on the
freed instance to a memory refusal that appeared elsewhere, so the causal direction is not
established. At the outcome layer, concentration does convert a per-token gain into a
first-token loss, and this half is measured rather than inferred: grouping deep research onto
one engine builds a prefill queue there, 82,472 to 88,511 queued prefill tokens at the median across the four runs
against 0 on every other engine, and that engine's own counters attribute the cost to waiting
rather than to prefill itself, with mean queue time 1.94 to 11.44 s against 0.04 to 0.11 s on
mixed engines and mean prefill time rising only from 0.14–0.34 s to 0.45–0.70 s. This
conversion is partial, not complete: in the segment where it was measured the chat gain of
6.6 points of per-token misses and the deep research loss of 11.6 points of first-token
misses still leave a net gain of 2.14 points of offered attainment, so it explains why the
segment gain is 2 points rather than 6, and it does not explain a flat total. A third
limiter is measured and its causal link is not: the freed engine carries 19.3 % and 19.9 %
of resident work over the hour and 2.85 % and 3.15 % in the 93 %-chat segment, so the
loosened pace applies where little work sits, and nothing in these five analyses shows that
the looser pace was used — no batch size, admission decision or queue depth was read on the
freed engine at the moment it was free.

## 4. The next experiment

Both proposals are static conditions run by `k8s/exp07/run_exp27_mixsweep.sh`, which costs
roughly 10 minutes per condition. Two pre-flight items apply to both. New arm names must be
added to the arm tables of `exp22_fluidserve.py`, `exp27_figures.py` and
`exp38_policy_compare.py` before launch, because those scripts drop unregistered arms and
still produce a plausible figure. And the scheduler start-up line should be captured per
condition, because it is absent from all four retained `scheduler_dispatch.log` files of
EXP-93 and the arm identity there rests on the driver and on the experiment note rather than
on the runs' own data.

### Rank 1 — Does the benefit grow when more than one engine is free of chat?

**Question.** The preference frees about one engine of four and never more: with it on, the
modal state is exactly one loose engine and three engines stay chat-bound in 71.9 % to
74.1 % of all engine-seconds. If that cap is what limits the gain, then forcing two chat-free
engines should raise attainment above the preference; if it is not, the limiter is elsewhere,
most plausibly the pace-for-memory trade of section 3.

**Arms.** Five, all FluidServe with one setting changed, so the pin acts only by filtering
the candidate list. `fsnoaff` (preference off, no engine deliberately freed). `fspfx`
(preference on, about one engine freed and freed emergently). Three new arms using the
existing `FS_CLASS_PIN` environment variable, which hard-assigns a per-token budget tier to
a fixed set of instance positions and leaves unlisted tiers unrestricted: `fspin-c3` with
`50:0,1,2` (chat on three engines, one engine chat-free by construction), `fspin-c2` with
`50:0,1` (two chat-free), `fspin-c1` with `50:0` (three chat-free). Tier 50 is chat, and
deep research and swe stay unrestricted in all three, so the only thing the ladder varies is
how many engines chat may occupy. `fspin-c1` is the known-bad anchor: chat's share of
produced output tokens is 2.50 engines' worth, so one engine should starve it, and the pin
allows no spill.

**Workload and rates.** Mix m1 (chat 76.9 %), whose knee is measured at 28.0 req/s (EXP-80,
8 rates, 2 repeats). Rates 35 and 45 req/s, that is 1.25 and 1.6 times the knee, because the
largest preference gain on record is +7.5 points at static 45 req/s in m1 (EXP-56, 3
repeats), falling to +4.8 at 55 req/s (3 repeats).

**Cost.** 5 arms × 2 rates × 2 repeats = 20 conditions, about 3.4 hours.

**What refutes it.** If `fspin-c2` does not exceed `fspfx` by at least 2.0 points of offered
per-request attainment at 45 req/s in both repeats, while holding two engines chat-free in
at least 90 % of engine-seconds, then the one-engine cap is not the limiter and the
structural reading of section 1 cannot be turned into performance by enlarging the
structure. A second, independent refutation applies to the instrument rather than the
hypothesis: if `fspin-c3` and `fspfx` differ by more than 2.0 points at either rate, the
pinned structure and the emergent structure are not the same object and the ladder cannot be
read as "number of chat-free engines". Both thresholds are set at 2.0 points because the
within-session repeat spread at 45 req/s in m1 is up to 2 points (EXP-56, 3 repeats per arm).

### Rank 2 — Is the hour-trace result flat because the trace runs below the band where the preference pays?

**Question.** The preference gained +7.5 points at static 45 req/s and +4.8 at 55 req/s in
m1 (EXP-56, 3 repeats each) but only +1.41 points over this hour. The chat-heavy segments of
the hour trace have median arrival rates of 23.6 and 27.4 req/s, at or below m1's measured
knee of 28.0 req/s. The proposal is that the gain is confined to a band above the knee, and
that the hour trace spends most of its chat-heavy time below it.

**Arms.** `fspfx` and `fsnoaff`, unchanged.

**Workload and rates.** Mix m1 at 20, 25, 30, 35 and 40 req/s, which brackets the knee from
0.7 to 1.4 times. Mix m5 (chat 33.3 %) at 12 and 16 req/s, whose knee is estimated at 12
req/s from two measured points, as a check on the separate finding that the feasibility test
already frees an engine when deep research is abundant, so the preference should add little
there.

**Cost.** m1: 2 arms × 5 rates × 2 repeats = 20 conditions. m5: 2 arms × 2 rates × 2 repeats
= 8 conditions. 28 conditions in total, about 4.7 hours.

**What refutes it.** If the `fspfx` minus `fsnoaff` difference at 25 req/s in m1 is 5.0
points or more in both repeats, the gain is not confined to a band above the knee and the
hour result is not explained by where the trace sits. Equally, if the difference at 40 req/s
in m1 is 2.0 points or less in both repeats, the band does not exist in this arm pair at all
and the +7.5 point figure from EXP-56 does not transfer to the current binary, which
enables prefix awareness in both arms. The secondary prediction, that the m5 difference stays
under 2.0 points at both rates, is refuted by any m5 difference of 4.0 points or more.

**Ranked second** because one of its two anchors is already measured. EXP-56 supplies 45 and
55 req/s with three repeats each, so this experiment adds resolution to a known curve, while
rank 1 tests a mechanism no run has yet exercised.

## 5. What remains unverified, merged across the five analyses

1. ~~**Whether the looser pace was used.**~~ **CLOSED 2026-08-24 — `a7_freed_engine_use.md`.**
   **The freed engine is underused: its modelled step time sits at 0.416 / 0.417 of the pace it
   is allowed, against 0.764 / 0.769 on a chat-bound engine** (p50 over 59 one-minute windows,
   two repeats, 35.0-point gap against repeat spreads of 0.1 and 0.5). It produces **1,693 /
   1,653 output tokens per second against 3,514 / 3,629** on a chat-carrying engine, 46-48% as
   much.

   **But there are two regimes and the hour average hides them, and only one of them names a fix.**
   In the segment where chat is 93% of requests the freed engine is nearly EMPTY — 10.5 / 10.0
   decoding requests against 140.5 / 147.5 on its peers, KV 10% full, no prefill queue, no
   preemption. **The work does not exist there**: deep research and swe together arrive at 1.71
   req/s against chat's 22.72, so freeing one engine of four from a class that is 93% of arrivals
   reserves capacity that at most 7% of the stream could use. In the other three segments the
   freed engine is at physical KV occupancy 0.99 or above for 42-83% of its seconds and preempts
   21-40 times a minute: **fully committed, and committed on memory rather than on pace.** The
   gate there would allow 4.2 to 5.1 more req/s and the KV cap allows 0.00 to 0.19.

   > **That is the outcome-layer measurement of the pace-for-memory trade that section 3 could
   > only infer.** The grouped class carries **5,667 / 5,541 KV tokens per decode slot against
   > 2,422 / 2,203** on a mixed engine, so grouping it fills the engine's memory long before it
   > fills the pace the grouping bought.

   ⚠ **The size of the underuse depends on which step time is used, and the two disagree.**
   Substituting the MEASURED inter-token latency for the modelled one gives 0.766 / 0.784 on
   chat-free windows against 1.021 / 1.026 on chat-carrying ones — still underused, but by 22%
   of headroom rather than 58%. The reason is that measured over modelled is 1.781 / 1.790 on
   chat-free windows against 1.326 / 1.314 on chat-carrying ones: **the decode-only step law
   under-reads exactly the engine whose headroom the structural argument depends on.**

   ⚠ **And the preference does not change what a freed engine does.** Preference-off chat-free
   windows sit at 0.426 / 0.434, indistinguishable from the preference-on ones outside the
   93%-chat segment. What the preference changes is how many freed engines there are, and it adds
   the near-empty kind.

   **The design change this names** is not "send more work to the loosened engine", which only
   addresses one regime. It is **a bound on how much of the fleet the preference may reserve for
   a class, tied to what the other classes actually arrive at** — because where the reservation
   is largest the minority classes cannot fill it, and where they can fill it the engine
   saturates on KV instead.
2. **The admissible pace is a model, not a read-back.** `scheduler.jsonl` exports 47 keys per
   scrape and none is a per-instance pace, gate, budget or step-time value, so the pace
   distribution combines measured per-engine class residency with the nominal class budgets
   (chat 50, deep research 100, swe 57.7 ms). If the policy's gate uses a per-request
   remaining budget instead of the nominal class budget, the three levels move.
3. **Per-class engine counters do not exist.** vLLM reports prefix hits, preemption and
   queue time per engine over all classes. The claim that deep research has the least prefix
   reuse is an inference from a −0.85 correlation across 16 engine-runs plus one extreme
   case, not a per-class measurement.
4. **The clean-interval prefix hit rate is a selected sample.** Intervals free of queueing
   cover 18.7 % to 54.2 % of each engine's prefill work under the waiting-queue filter and
   31.3 % to 92.9 % under an alternative filter, and 1 s sampling cannot exclude sub-second
   queueing inside a clean interval. The bias exists in both arms and was not shown to be
   equal between them.
5. **The split between gateway holding and engine queueing.** Client-side deep research TTFT
   on the concentrated engine is 15.3 to 16.6 s at the median where the engine reports a 7.9
   to 11.6 s mean queue time. Per-request dispatch timestamps were not joined, so the split
   is bounded, not resolved.
6. **Whether the concentrated engine keeps its identity inside a segment.** Checked at
   3-minute resolution in the 76.9 %-chat segment only. The identity of the freed engine
   differs between repeats.
7. **Prefill against decode residency.** A request counts as resident for one continuous
   interval. Whether a request queued at an engine should pin that engine's minimum budget is
   a policy question the client log cannot answer.
8. **Migration attribution.** A migrated request is attributed to its dispatch engine for its
   whole lifetime. Migration is small here — 4 completed migrations in one run and 0 in the
   other three — but the `migrated` column was not used.
9. **No matched-subsample comparison between arms.** The preference-on arm admits about 3
   points more deep research input tokens and completes 4 % to 5 % more deep research
   requests, so part of the deep research changes may be the extra admitted load rather than
   the preference.
10. **The session confound.** Both preference-on runs share one session block and both
    preference-off runs share another, so no quoted spread contains a session term. The
    preemption gap survives a 26 % session term; the 1.8-point work-weighted prefix hit-rate
    gap does not.
11. **Arm identity from the runs' own data.** The scheduler start-up line printing the flag
    is absent from all four retained `scheduler_dispatch.log` files.
12. **Client-side chunk coalescing.** Chunks per token are 0.9935 in all four runs, so there
    is no asymmetry between arms, but how much of the within-request gap skew is buffering
    rather than engine scheduling is untested, which affects how the absolute shape of the
    gap distribution should be read.
13. **Engine-side and request-side time origins.** The engine-side prefill and queue series
    were segmented 60 s earlier than the request tables; that instance was corrected, but the
    two origins were not reconciled globally.
14. **Engine step timing was never decomposed.** `vllm:request_prefill_time_seconds_sum` and
    `_count` are present in the scrapes and unused. Identifying the 500–600 ms worst-gap band
    as a prefill chunk is inference from prompt lengths, and the 8,192-token chunk size was
    taken from the task statement rather than read from engine configuration.
15. **The 4.3-point prefix hit-rate gain in the 93 %-chat segment is unexplained.** That
    segment offers almost nothing to group, and no candidate mechanism was tested.
16. **The goodput-share baseline estimates what rejected requests would have produced.**
    Rejection is length-selective on the input side, the error was bounded by showing output
    length is nearly uncorrelated with input length within a class, but it was not measured.
    An arrival-count baseline leaves the goodput-share Jain difference unresolved.
17. **No llm-d comparison on this trace.** Two `llmdslo` runs of the same trace exist,
    `results/260822_2303_exp93r1_llmdslo_shift` and
    `results/260823_0129_exp93br1_llmdslo_shift`, and neither was read.
18. **Nothing isolates the preference from the other FluidServe mechanisms**, which are on in
    both arms.
19. **Two repeats per arm everywhere.** Every spread is the range of two values. No
    confidence interval, bootstrap or statistical test was computed anywhere in the five
    analyses.
20. **The corrected within-request tail result has had one adversarial pass, not two.** The
    filed verdict was refuted and the replacement direction — that the preference lowers
    chat's own p99 gap by 56.4 ms while raising its own p90 by 8.7 ms — has not itself been
    checked by a second reader.
