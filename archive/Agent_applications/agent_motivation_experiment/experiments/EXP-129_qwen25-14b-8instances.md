# EXP-129 — Qwen2.5-14B-Instruct on eight instances: the fourth and last model

*Written 2026-09-11 20:40 KST, before anything was switched. Sections 1 to 6 are
the plan and the judgement rules; section 7 onwards is the record.*

## 1. Why this model and why now

The requirement is **two 70B-class models and two smaller ones, with the family
matched**, so that "does this hold on another model" is answered within a family
rather than across an arbitrary pair. Three of the four are done:

| family | large (4 × TP=2) | small (8 × TP=1) |
|---|---|---|
| Llama | Llama-3.1-70B (EXP-113 and earlier) | Llama-3.1-8B (EXP-114 through EXP-128) |
| Qwen | Qwen2.5-72B (EXP-111 to EXP-113) | **Qwen2.5-14B — this experiment** |

**This is the only one of the four where a single variable moves.** Going from
Llama-70B to Llama-8B changed the model size *and* the fleet shape (4 × TP=2 to
8 × TP=1) at once, and EXP-114 §1 says so explicitly. Here the fleet shape is
already eight instances of TP=1, so **only the model changes.**

## 2. What is already in place, and the one thing that is not

| | |
|---|---|
| `switch_model.py` entry | ✅ `qwen25-14b-b200-tp1` — `tp=1, dp=8, max_model_len=32768` (the model's own `max_position_embeddings`, not a choice) |
| weights | ✅ `models--Qwen--Qwen2.5-14B-Instruct` in `/hf-cache/hub` |
| **profile tables** | ❌ **`deploy/profiling/qwen25-14b-b200-tp1/` does not exist** |
| cluster | idle, currently Llama-3.1-8B |

**The profile is measured, never borrowed.** Reading another model's tables is
the completely silent failure: the policy starts normally, every log line looks
ordinary, and every number it computes is wrong. `set_scheduler_profiling.py`
prints which directory it read for exactly this reason.

## 3. The procedure, which is the one EXP-111 and EXP-114 both used

1. **Switch** — `switch_model.py --to qwen25-14b-b200-tp1 --verify`. Eight places
   carry the model's identity and five of them fail silently; the script sets
   them from one table and asks the engine directly.
2. **Prove the fleet** — a two-minute condition that must produce
   `server_metrics/engine_8000` through `engine_8007`. ⚠ `containerStatuses[].ready`
   means nothing here: this container has no readiness probe and reports ready
   four seconds after creation, while the ports opened 1 min 47 s later on the
   8B fleet. **Ask the ports.**
3. **`ttft.json`** — `measure_ttft_sweep.py`, idle engine, one request at a time,
   20 lengths × 5 repeats, fresh random tokens each repeat so the prefix cache
   cannot participate.
4. **`tpot.json` and `fluidserve.json`** — `run_exp111_profile.sh` with a spread
   of arrival rates, engine on `llumnix_sched.InstrumentedScheduler` (ordering
   identical to stock vLLM, instrumentation added), routing on **`load-balance`**.
   FluidServe would need the very table being built.
   ⚠ **The spread matters**: the KV term and the batch term are collinear at
   saturation, and EXP-16's first stage measured at one saturated rate and got a
   VIF of about 11.
   ⚠ **`gen_profiling_from_stepdump.py` writes `ttft.json` as well as
   `tpot.json`.** Run in the wrong order it overwrites the idle-measured prefill
   table with a derived one that the measuring script itself calls superseded.
5. **Knee, and the budget factor** — §4.
6. Then the arms.

## 4. The decision that is new since EXP-114: what per-token budget this fleet gets

On eight 8B instances the standard budgets turned out to be mis-set: the policy
was admitting into a region it could not serve and missed 11% of what it took,
and halving the per-token budgets moved offered attainment from 66.5 to 73.5 and
admitted from 88.6 to **99.5** (EXP-126).

**The factor of two was derived, not chosen** — the ratio of the budget to the
measured per-token time when the pool is full was 1.96, and to the loaded p50
1.97, two independent estimates that agreed.

**So this fleet gets its own factor, derived the same way, and 2 is not assumed.**
The knee conditions in step 5 therefore also record the per-token time under load
and at a full pool, and the factor comes out of those.

⚠ **This is the part of the work most exposed to a reviewer**, and it is an
argument rather than a measurement: CLAUDE.md group B records that neither
tradition in the literature re-chooses a deadline when the fleet changes. The
defensible form is to keep the absolute budget and report normalised difficulty
beside it, or to sweep the SLO scale and show the curve. **Whatever this fleet's
factor turns out to be, the paper has to present it that way, and this experiment
does not settle it.**

## 5. What is expected

**H1 — the engine is faster than 8B per step and slower than 70B.** 14B against
8B is 1.75× the parameters at the same TP=1, so prefill step cost and the decode
law's `c0` should scale roughly with that. *Refuted if 14B is not between them on
either term*, which would mean something other than model size is moving.

**H2 — output lengths move, and that is what decides capacity.** Qwen2.5-72B
produced 1.46× the tokens Llama-70B did on the same prompts and that alone took
capacity to 0.66×; Llama-8B against Llama-70B was 0.99× and capacity moved only
with engine speed. **Qwen2.5-14B is a Qwen, so the long-output behaviour is
expected to return.** *Refuted if the mix-weighted mean output is within 10% of
Llama-8B's 514 tokens.*

**H3 — the knee lands below the 8B fleet's 173.5 req/s**, because the engine is
slower per step and, if H2 holds, each request is longer. *Refuted if it is at or
above 173.5.*

**The prediction that matters most is H2**, because it is the one that makes the
two small models a real pair rather than a repetition: if Qwen-14B is long-output
like Qwen-72B, then within each family the small model reproduces the large one's
character, and "the result holds across models" is a claim about two families
rather than four unrelated points.

## 6. What this experiment will not answer

- **Whether the budget normalisation is defensible** (§4).
- **Anything about the 4-instance shape for this model.** Qwen-14B at 4 × TP=2 is
  not planned, so "model" and "fleet shape" stay confounded across families even
  though they are separated within this one.
- **The vLLM router and Llumnix SLO connection errors** (EXP-128 §5.3) are still
  open and will reappear here if those arms are run at saturation.

## 7. Record

### 2026-09-11 23:43 KST — the switch

`switch_model.py --to qwen25-14b-b200-tp1` moved all eight places the model's
identity is written, and the ports were asked directly: 8/8 serving
`Qwen/Qwen2.5-14B-Instruct` at `max_model_len=32768`, reached 5 minutes 25
seconds after the patch. `containerStatuses[].ready` was not used, per §3 step 2.

### 2026-09-12 00:58 to 03:00 KST — the profile could not be fitted, and six hypotheses were wrong

Three step dumps were taken. **None of them produced a usable decode surface**,
and the reason took six wrong turns, all of which are recorded here because the
pattern is the one this repository has paid for before: a mechanism written from
the shape of a number rather than from the thing that makes it.

| rates | steps | R² | median relative error |
|---|---|---|---|
| 15 / 40 / 80 / 120 / 160 | 311,921 | 0.827 | **57.0%** |
| + 5 / 10 / 20 / 30 / 45 | 1,391,918 | 0.800 | **57.5%** |
| (control) Llama-3.1-8B, same generator | 1,189,565 | 0.980 | **8.0%** |

**What was claimed and then refuted, in order:**

1. *"Not enough data."* Quadrupling it moved the median error 57.0 → 57.5%.
2. *"A Qwen-family property."* Qwen2.5-72B fits at R² 0.978 with the same
   generator and the same law.
3. *"The saturated, preempting conditions are the problem."* Removing them makes
   the KV coefficient **negative** (−1.2e-4), which is impossible.
4. *"Rates 5-30 are clean because preemption is zero there."* At 20 req/s the
   share of decode steps over 50 ms goes from 0.0% in the first two minutes to
   70-93% after the third, and steps per second fall 422 → 76. **Preemption stays
   at zero only because the KV has not filled yet; it is not a test of whether
   the engine is keeping up.**
5. *"A CUDA-graph capture cliff."* The capture-size list includes 40, 48, 56 …
   512. ⚠ **This refutation is weaker than it looked** — see §7.1.
6. *"A batch cliff."* The same batch bucket (80-119) takes 73-82 ms at arrival
   rates 15-45 and **16.6 ms at rate 160**, so batch alone does not set it.
7. *"Prefill interference."* Slow decode steps are adjacent to a prefill step
   41.0% of the time against 2.9% for fast ones, which looked decisive — but
   restricting to steps with at least eight pure-decode steps on **both** sides
   leaves the plateau intact: **Qwen 92.0 ms (n=1,814) against Llama 16.8 ms** at
   batch 180-259. **Retracted.**

### 7.1 What the diagnosis did establish

Full account in `EXP-129_capacity-model-diagnosis.md`. Four things matter here.

**① `interval_ms` is a wall-clock enter-to-enter gap between `schedule()` calls
(`patches/vllm-sched/llumnix_sched.py:226`), and it is off by one step.** Row *k*
covers step *k−1*'s work. Measured: a decode row whose PREDECESSOR was prefill is
1.24-2.45× slower, while one whose SUCCESSOR is prefill is 0.98-1.20×, i.e.
unaffected. **`decode_cells` in the generator pairs the interval with the wrong
row.** This is not specific to Qwen — it is in every profile this repository has
built. ⚠ As a RATE the value is trustworthy: token-weighted `interval_ms` matches
vLLM's own `inter_token_latency` counter at ratio 1.00-1.01 across all fifteen
runs, which is why the existing tables are not thereby invalidated.

**② All eight engines write one interleaved step file**, and the production
linker compares `t_wall` directly, so it drops exactly the heavily loaded steps.
Reconstructing the entry time as `t_wall − t_schedule_us` raises linking from
80-100% to 98.6-100%.

**③ The cliff is a discontinuity near batch 48, not a slope.** In the transition
the distribution is bimodal (p25 10.9 ms, p75 41.6 ms), and above it the surface
is nearly flat: batch 96 → 384 moves the step only 74.8 → 90.6 ms. A saturating
term `+62.9·sigmoid((B−48)/8)` reaches 5.9% leave-one-rate-out error against
Llama's 3.2%, **but it copies the discontinuity rather than explaining it**, and
it implies a marginal request costs 0.077 ms, which is useless for admission.

**④ It is not cross-engine interference and not preemption.** Other engines'
prefill activity shows no trend (90.1 / 84.2 / 86.3 / 87.7 ms), and the 20 req/s
condition has zero preemptions with a 65.3 ms inter-token latency.

### 7.2 The open question, and why it blocks shipping the table

**A model with 1.75× the parameters is 5.5× slower than Llama-3.1-8B on the same
fleet at the same batch. Model size does not explain that.** The shape that is
left — a fixed cost that appears above a threshold batch and then barely depends
on batch or KV — is the shape a CUDA-graph/eager fallback has, and hypothesis 5
above was rejected on the **configured capture-size list**, which says what was
requested and not what ran. The engine's start-up log rotates in one to two
minutes at this verbosity and was not archived.

→ **A capture of the start-up log is armed** (`exp129_capture_startup.sh`) and
fires on the restart Stage A performs on its way to the knee. That is the next
piece of evidence, and it decides whether this fleet is measuring Qwen2.5-14B or
measuring a deployment defect.

⚠ **Until it is answered, a profile built from this data risks freezing a defect
into the tables**, after which the policy runs normally and every number it
computes is wrong — the silent failure §2 exists to prevent.

### 7.3 What is running as of 2026-09-12 03:20 KST

| | | |
|---|---|---|
| step dump 3 | 3 / 5 / 7 / 9 / 11 / 13 req/s × 8 min, rates DERIVED rather than guessed | ends about 04:00 |
| Stage A | waits for it, then: tpot.json (temp dir, only tpot moved) → engine back to stock FIFO → idle ttft.json → fluidserve.json → **knee with our own policy at 6/9/12/15/20/25 req/s** | 04:00-06:30 |
| start-up capture | fires on Stage A's engine restart | ~04:10 |
| sentinel | disk, pods, duplicate runner Jobs, every 10 min | continuous |

### 7.4 The fallbacks, so that the morning has a decision and not a diagnosis

The requirement for the day is **an hour-trace experiment**, and it must be
possible whatever the profile turns out to be.

| if | then | cost |
|---|---|---|
| the knee lands and the profile's measured cells cover batch 96-256 | run the hour trace on Qwen2.5-14B | trace scaling + the arms |
| the start-up log shows a graph/eager fallback | fix the engine configuration and re-measure the profile | about 2 h before the trace |
| Qwen2.5-14B cannot be made to fit | **Qwen2.5-7B** — faster prefill, and the family pairing is preserved | about 3 h to switch, profile and knee |
| nothing else is ready | **Llama-3.1-8B** — the fleet, profile and budgets are all validated and the hour trace can start immediately | none |
