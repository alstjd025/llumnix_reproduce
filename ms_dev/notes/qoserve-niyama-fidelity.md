# QoServe (Niyama) port — fidelity against the original, and what it changes here

Started 2026-07-30. Working document: the port's state, where it departs from the
paper implementation, which departures matter on this workload, and the
experiment the corrected port is for.

Source of truth for the original: `sarathi-serve-niyama/` at
`microsoft/sarathi-serve @ niyama_asplos2026`, principally
`sarathi/core/scheduler/deadline_scheduler.py` (353 lines) and
`sarathi/core/datatypes/sequence.py`. Port: `patches/vllm-sched/deadline_sched.py`
(engine side) and the `priority_mode="deadline"` branch of
`workloads/swe_bench_coding/agent.py` (client side, mirroring
`patches/vllm-sched/slo_tier.py`).

## 1. Why this is being revisited

EXP-38 established that FluidServe's advantage at 45 req/s is 35.6 points offered
over the Llumnix SLO arm, on a fleet whose engines run stock FIFO. The obvious
objection is that the advantage is an artifact of a weak engine: give the engine
a deadline-aware scheduler and the control plane's contribution might vanish.

Section 34 also identified where the fleet actually fails at 60 req/s — not in
the decode batch, which the policy holds constant at 246 per instance across
rates, but in a 4.5 ms rise in per-token time of which 72% is the residual
between the measurement and the decode-only law, booked as prefill duty. **The
one Niyama unit that acts on that term is dynamic prefill chunk sizing (unit 4).**
So the port is worth making faithful before it is used as an arm.

## 2. What can act at all, given how our control plane behaves

Measured over EXP-38 rep1, engine-side waiting queue, median and maximum per
engine:

| condition | running per engine | waiting median / max |
|---|---|---|
| PolyServe @ 45 | 16 / 624 / 1022 / 16 | 0/3, 1/359, **1840/4318**, 0/5 |
| Llumnix SLO @ 45 | 255 / 262 / 245 / 280 | 0/19, 0/11, 0/7, 0/19 |
| FluidServe @ 45 | 233 / 220 / 286 / 236 | 0/8, 0/8, 0/6, 0/9 |
| PolyServe @ 60 | 22 / 1022 / 22 / 683 | 0/1, **3679/5653**, 0/2, **457/1493** |
| Llumnix SLO @ 60 | 315 / 191 / 181 / 189 | 0/44, 0/12, 0/8, 0/26 |
| FluidServe @ 60 | 250 / 249 / 236 / 248 | 0/13, 0/8, 0/9, 0/8 |

Preemptions are zero in every FluidServe and SLO condition; KV occupancy is
40–45%.

| Niyama unit | acts on | live under FluidServe / Llumnix SLO? |
|---|---|---|
| 2 — deadline order of the waiting queue | waiting queue | **No.** Both hold at the gateway, so the engine queue is empty |
| 5 — eager relegation of doomed requests | waiting queue | **No.** Same reason |
| 3 — slack reorder of `running` | order within a step; preemption order | Only when the step token budget binds — which unit 4 makes binding |
| **4 — dynamic prefill chunk sizing** | per-step total token budget | **Yes, on both** |

**Both policies that hold requests outside the engine reduce Niyama to unit 4.**
That is itself a result worth stating: an engine-side scheduler orders what is
waiting inside the engine, and an admission layer that keeps that queue empty
leaves it nothing to order. It also means this experiment is a test of unit 4
and not of Niyama as a whole, and must be described that way.

## 3. Unit-by-unit fidelity

### unit 1 — SLA tier and deadline model. **Faithful, with one stale constant.**

Original: three tiers with `TIER_DEADLINES = [6.0, 600.0, 1800.0]` seconds
assigned at random; for TTLT tiers (tier ≥ 1) the first-token deadline is derived
as `tier_deadline − num_decode_tokens × execution_threshold_batched`, falling
back to `output_len_pred` when the per-request count is unknown.

Port: the client classifies by which SLO the class declares — a TTFT SLO gives
the interactive tier, an end-to-end-only SLO gives the TTLT tier and the same
subtraction `e2e − out_len × tbt`, and no SLO gives best-effort. This is a
non-random realisation of the same rule, which is the right adaptation: our
classes have real SLOs where Niyama's benchmark assigned tiers by lottery.

The arithmetic already matches by construction — m1 declares the agent class as
`ttft_ms 11800`, which is exactly `30000 − 728 × 25`.

**Departure:** `out_len` for the agent class is 728 in the config while the
measured mean output is 479. Niyama uses the per-request `num_decode_tokens`
when it is known. Using 728 makes the derived first-token deadline 11,800 ms
where the measured output implies 18,025 — the class is given a deadline 6.2 s
tighter than its own budget requires. Conservative in direction, wrong in value.

### unit 2 — waiting-queue order. **Departs: pure EDF instead of hybrid prioritization.**

Original `Sequence.__lt__` for `scheduler_type='deadline'` is **not** EDF. It is

```
key = ttft_deadline + arrival_time + hybrid_prioritization_param * remaining_prefill_tokens
```

with `hybrid_prioritization_param = 0.008` (s per token) and `drop` as a strict
primary key. That is a deliberate blend of EDF and shortest-remaining-prefill:
a long prompt is pushed back by 8 ms per thousand tokens. `edf` and `srpf` exist
as separate `scheduler_type` values; `deadline` is the hybrid.

**The units are seconds**, because `ttft_deadline` and `arrival_time` are
`time.monotonic()` values, so the parameter is 8 ms *per token*. On our mix the
penalty is **5.2 s** for a 649-token chat prompt and **44.5 s** for a
5,557-token agent prompt, against deadlines of 5 s and 11.8 s. The term does not
adjust the order, it **dominates** it: Niyama's `deadline` policy is
shortest-prompt-first with the deadline as a tiebreak, not EDF with a nudge.

That is faithful to the paper implementation and it is also true there — their
tightest tier has a 6 s deadline against prompts of one to two thousand tokens,
so the same domination holds. It is worth stating plainly because "deadline
scheduler" suggests otherwise, and because on our SLO scale the effect is if
anything stronger.

Port: pure EDF on the absolute deadline, with relegation expressed as a
`10^15` priority bump rather than a separate key.

**Inert here** (empty queue) but it is the defining feature of the `deadline`
policy and should be implemented so the port is what it claims to be.

### unit 3 — running-queue sort. **Departs, and the original looks wrong.**

Original `_sorting_key`: `(0, -slack)` for decodes, `(1, arrival + ttft_deadline)`
for prefills. The primary key puts every decode ahead of every prefill, which is
the stated intent. The secondary key `-slack` sorts decodes by **descending**
slack — the most urgent decode goes last.

Port: `(0, tbt_deadline − now)` — least slack first.

In Sarathi-Serve the secondary key barely matters: the first pass admits decodes
until the chunk budget is reached and each decode costs one token, so with a
2048-token budget every decode is admitted regardless of order. In vLLM V1 the
order does matter, because preemption sheds from the tail of `running`. Sorting
most-urgent-last would preempt the most urgent decode first.

**The port's inversion is kept deliberately.** Recording it as a departure with
the reason, rather than reproducing what appears to be a sign error in a place
where the original is insensitive to it and V1 is not.

### unit 4 — dynamic prefill chunk sizing. **Two departures that matter here.**

Original `_get_prefill_size_by_slack`: binary search over a fixed grid

```
TOKEN_SEARCH_SPACE          = [128] + range(256, 2049, 256) + [2552]
LOW_MEMORY_TOKEN_SEARCH_SPACE = range(128, 513, 128)
```

for the largest **total** token count whose predicted batch time times
`pred_thres = 1.2` fits the tightest running decode's slack, returning
`total − batch_num_decode_tokens` as the prefill budget. The low-memory space is
selected when `free_blocks * block_size // 32 < 1000`.

Batch time is predicted by **two stratified linear models**, split at 512 total
tokens, each over five terms:

```
t = intercept + a*total_tokens + b*prefill_tokens + c*decode_tokens
              + d*decode_context + e*prefill_context
```

Port `_budget_by_slack`: a closed-form inversion of a **single** model with three
terms and no prefill-context term,

```
decode_only = C0 + C_KV*kv + C_ND*ndec
max_prefill = (min_slack − decode_only*1.2) / C_PT
budget      = clamp(ndec*2 + max_prefill, 128, max_num_batched_tokens)
```

**Departure A — the ceiling.** Niyama's grid tops out at 2,552 total tokens, so
it never schedules a step larger than that. The port clamps to the engine's
`max_num_batched_tokens`, which is 8,192 here. Our prompts are 649 / 4,639 /
5,557 tokens, so an 8,192 budget prefills an entire agent prompt in one step —
about 436 ms, the tail that section 29 identified as making the observed
iteration time bimodal (p50 38.7, p90 98.2, p99 395). Niyama would split the
same prompt across at least three steps of ≤2,552. **This is the largest
behavioural difference between the two and it acts directly on the term section
34 identified.**

**Departure B — the model form.** The original prices prefill *context* as well
as prefill *tokens*; the port prices only tokens. Attention over an already
computed prefix is not free, and our prefill-heavy classes carry 4.6k–5.6k of it.
The port's coefficients should stay — they are fitted on B200 EXP-16 data and
Niyama's are for different hardware — but the functional form should match.

**Departure C — the memory branch** is absent. KV sits at 40–45% here so it is
probably inert, but it is three lines.

Incidentally the port's own fitted `C_KV = 3.058e-5` is 2.2× FluidServe's
deployed `c_kv = 1.41151e-5` and close to the 2.266e-5 that section 34.4 obtained
by refitting on EXP-38. Two models fitted independently, on different runs, by
different means, both say the deployed KV coefficient is low. That is worth
recording whatever happens to this experiment.

### unit 5 — eager relegation. **Departs in the throughput estimate.**

Original: inside the prefill loop, a request whose remaining prefill cannot
finish before its first-token deadline gets `drop = 1`, is pushed back onto the
heap and skipped — once only, guarded by `drop == 0`. The estimate uses
`expected_prefill_throughput`, an EMA of `last_prefill_size / elapsed` where
`last_prefill_size` is the **prefill tokens actually scheduled** in the previous
step.

Port `_relegate_waiting`: scans the first 32 heap entries and bumps the priority
of any whose prefill cannot finish in time. Its `_prefill_tps` is an EMA of
`sum(r.num_computed_tokens)` over all running requests, which **includes decode
tokens**. At our operating point that is roughly 250 decode against 800 prefill
tokens per step, so the estimate runs about 30% high and the port relegates less
than the original would.

The 32-entry scan cap is a deliberate addition: `PriorityRequestQueue.__iter__`
copies and drains the heap, which is unaffordable per step once a backlog forms.
Inert here.

### What cannot be ported

Sarathi-Serve's scheduler owns block allocation and **has no preemption** — it
asserts `can_append_slot` and returns an always-empty `preempted_seq_ids`. vLLM
V1 preempts by recompute. The original also keeps `_prefill_queue` separate from
`waiting`, where V1 has one queue. These are substrate differences, not
omissions, and they are why unit 3's ordering carries a consequence here that it
does not carry there.

## 4. Changes applied

All in `patches/vllm-sched/deadline_sched.py`, each behind an environment
variable whose default is the faithful value, so the previous behaviour is
reproducible by setting one knob.

| # | Gap | Change | Knob | Bites here? |
|---|---|---|---|---|
| G1 | step ceiling 8192 vs Niyama's 2552 | budget chosen by binary search over Niyama's grid `[128] + range(256,2049,256) + [2552]` instead of a continuous clamp to `max_num_batched_tokens` | `DEADLINE_USE_GRID=0` | **Yes, the largest** |
| G2a | prefill *context* not priced | added a `_C_PCTX * prefix_tokens` term; the prefix is the largest computed prefix among requests currently prefilling, plus the waiting head, mirroring Niyama's "top of the prefill queue, conservative" estimate | `DEADLINE_BT_CPCTX=0` | Yes |
| G3 | relegation throughput counted decode tokens | EMA now over the prefill tokens actually scheduled, read from `SchedulerOutput.num_scheduled_tokens` restricted to requests that were still prefilling before the step | — | No (queue empty) |
| G4 | pure EDF instead of hybrid prioritization | `priority = deadline_ms + 8.0 * prompt_tokens`, folded in at `add_request` so the key stays static | `DEADLINE_HYBRID_PARAM_MS=0` | No (queue empty) |
| G5 | memory branch absent | grid restricted to `range(128,513,128)` when free KV tokens / 32 falls below 1000; the free-KV accessor returns infinity when the V1 API is not where expected, so an unknown API leaves the branch off rather than throttling every step | `DEADLINE_LOW_MEM_TOKENS` | No (KV 40–45%) |
| G6 | decode secondary sort inverted vs the original | **not changed**, see unit 3 | — | — |
| G7 | agent-class `out_len` 728 vs measured 479 | **not changed**, see below | — | Small |

The start-up line now reports the new knobs, so the deployment check has
something to read back:

```
[deadline] relegation=... dynamic_chunk=... grid=... grid_top=2552
           hybrid_ms_per_tok=8.000 c_pctx=1.226e-03 tbt_s=... max_batched=8192
```

### G2 is only half done, and the missing half cannot be fitted from what we have

Niyama's batch-time model is **stratified** at 512 total tokens, with all five
coefficients differing between the regimes. Only the functional term for prefill
context was added here; the stratification was not, because fitting a second
regime needs per-step prefill-token counts and the captures do not carry them.
That is the same gap that stopped the decode law being corrected online
(`fluidserve_capacity.go`, the note above `newCapacityModel`) and the same
signal that section 34 says would separate "prefill stole time" from "the decode
law is miscalibrated". One missing measurement blocks three things.

The prefill-context coefficient is **transferred, not fitted**: Niyama's
>512-token model prices prefill context at 2.727223e-6 s/token against
6.863043e-5 s/token for prefill tokens, and that ratio, 0.0397, is applied to
our B200-fitted `_C_PT`. Only the ratio of two attention costs is assumed to
carry across hardware. It is a stated estimate.

### G7 is left alone deliberately

The agent class declares `ttft_ms 11800`, which is `30000 - 728 * 25`, the TTLT
conversion with a stale output length; the measured mean of 479 implies 18,025.
Correcting it means editing a workload config that EXP-38 also used, and a
config edited mid-study is how two sweeps stop being comparable. It is recorded
with its size and left for a run that is not being compared against EXP-38.

### What the change actually does, checked offline

Same arithmetic as `_budget_by_slack`, evaluated at the operating points EXP-38
measured (KV and decode batch per instance, agent-class prefix):

| slack of the tightest decode | old ceiling 8192 | new, Niyama grid |
|---|---|---|
| 20 ms | 620 | 620 |
| 50 ms | 620 | 620 |
| 100 ms | 1,699 | 1,280 |
| 400 ms | **8,192** | **2,552** |

The floor dominates when slack is tight, so the two agree there. The change
bites where a decode has room and the engine would otherwise have taken a large
chunk — which is exactly the 436 ms prefill iteration that makes the observed
step-time distribution bimodal. **The intervention is on the tail, not the
median**, and the tail is what section 34 could not account for.

## 5. The experiment this is for

**Claim to test: FluidServe's advantage over the Llumnix SLO arm does not depend
on the engine being FIFO.** Stated that way it is falsifiable and it is the
objection a reviewer raises. Stated as "FluidServe plus QoServe beats Llumnix
plus QoServe" it is the same measurement.

Four arms, one session, arm inside and repeat outside:

| arm | control plane | engine |
|---|---|---|
| `slo-fifo` | Llumnix SLO | stock |
| `slo-qoserve` | Llumnix SLO | DeadlineScheduler |
| `fluidserve-fifo` | FluidServe | stock |
| `fluidserve-qoserve` | FluidServe | DeadlineScheduler |

The two FIFO arms are re-measured rather than taken from EXP-38: cross-session
movement reaches 4.6 points on this workload and the differences at issue are of
that order.

Rates 30 / 45 / 60 req/s — 15 is omitted because all arms are at 100 there.
Two repeats. 24 conditions, about 5.6 hours.

### What is predicted, and what would refute it

**The honest prior is not that QoServe helps FluidServe more.** Unit 4 buys
time between tokens by deferring prefill, which is paid for in time to first
token, and FluidServe has already spent that budget at the gateway:

| chat TTFT, admitted | p50 | p90 | budget | headroom at p90 |
|---|---|---|---|---|
| Llumnix SLO @ 45 | 0.56 s | 3.53 s | 5 s | +1.47 s |
| FluidServe @ 45 | 1.80 s | 4.66 s | 5 s | +0.34 s |
| Llumnix SLO @ 60 | 1.71 s | 5.46 s | 5 s | −0.46 s |
| FluidServe @ 60 | 4.52 s | 5.13 s | 5 s | −0.13 s |

FluidServe holds each chat request until its computed deadline, so its
distribution is pressed against the ceiling and there is little left for the
engine to spend. The Llumnix arm holds a fixed 5 s and its median request has
3.4 s of slack. **On that reasoning unit 4 should help the Llumnix arm more.**

Against it: FluidServe's `canWait` subtracts a *measured* placement-delay bound,
so if the engine starts deferring prefill the control plane shortens its own
hold automatically. The fixed 5 s window cannot adapt. Which effect dominates is
the measurement.

1. **Primary.** FluidServe−SLO on the per-request offered denominator with
   QoServe, against the same difference with FIFO. **Refuted if the gap narrows
   by more than the repeat spread** — that would mean the advantage was partly an
   artifact of the engine.
2. **Mechanism.** Chat's mean inter-token latency at 60 req/s, currently 48.7 ms
   against a 50 ms budget, where section 34 measures roughly 10 points of chat
   attainment per millisecond. **If it moves less than 1 ms, unit 4 is not
   reaching the term section 34 identified** and the run says nothing about
   dynamic chunking, only about the two control planes.
3. **The cost side.** Chat's TTFT-only failure rate, 7.0% at 60 req/s. If it
   rises while the TBT failures fall, unit 4 has moved failures between the two
   halves of a conjunctive rule and bought nothing.
4. **Capacity guard.** Total output tokens per second, 20,244 at 60 req/s. A
   fall means attainment was bought with throughput.
5. **Inertness check.** Engine queue and preemption counts. If they stay at zero,
   units 2, 3 and 5 did nothing and the result belongs to unit 4 alone; the
   fidelity work on the other units is then insurance, not a contribution.

### Before it starts

- The engine log's `[deadline] relegation=... dynamic_chunk=... tbt_s=...
  max_batched=...` line is the only authority that the scheduler loaded and with
  which settings, exactly as the scheduler's `FluidServe dispatch policy created`
  line is. A condition without it is void.
- `DEADLINE_DEFAULT_SLO_MS` is 30,000: if `priority` fails to reach the engine
  every request is treated as a 30 s SLO and the arm silently measures something
  else. The per-request log line printed for the first eight requests shows the
  unpacked `slo_ms` and `tbt_ms` and must show the class budgets, not the
  default.
- The client must run `priority_mode="deadline"`. EXP-38 sent priority for
  PolyServe's tier key; the encoding is the same, but the mode must be set
  explicitly for the two QoServe arms.
- **FluidServe's capacity model reads `chunk` from the engine's static
  `MaxNumBatchedTokens`, which unit 4 overrides every step.** With G1 applied the
  engine's effective budget is at most 2,552 while FluidServe still prices an
  8,192-token chunk and divides pending work by 8,192 to count prefill-carrying
  steps. The two errors have opposite signs and the net is not calculable in
  advance. This is a genuine confound and it is also what deploying the two
  together would actually do; it is measured, not corrected, in this experiment,
  and a follow-up arm that tells FluidServe the effective chunk is the way to
  separate them.
