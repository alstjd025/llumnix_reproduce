# EXP-130 — a twenty-minute screen for whether an engine can be profiled at all

**2026-09-12.** Written after Qwen2.5-14B cost three full profiling stages before
its anomaly became visible, and Qwen2.5-7B was then screened in twenty minutes
and rejected. The screen, its result on both models, and what the pair of
results narrows the cause to.

## 1. What the screen asks, and why a floor exists

A decode step reads the model weights once and the resident KV once, so

    floor(ms) = (weights_per_gpu + kv_bytes_resident) / HBM bandwidth

is a bound no engine beats. Measured against it at batch 64 with about 2,048
tokens per request, on this hardware (B200, about 8 TB/s):

| model | per-GPU read | measured step | floor | ratio |
|---|---|---|---|---|
| Llama-3.1-70B (TP=2) | 90.5 GB | 19.7 ms | 11.3 ms | **1.7x** |
| Llama-3.1-8B (TP=1) | 31.0 GB | 7.8 ms | 3.9 ms | **2.0x** |
| Qwen2.5-72B (TP=2) | 92.5 GB | 24.8 ms | 11.6 ms | **2.1x** |
| **Qwen2.5-14B (TP=1)** | **52.0 GB** | **66.7 ms** | 6.5 ms | **10.3x** |

**1.7 to 2.1x is what this hardware does when it is working**, and the three
models in that band are exactly the three that produced usable profiles. So a
model is screened by running it at two arrival rates and asking where it lands.
Two rates rather than one because a single point cannot show whether the ratio is
flat or has a step in it — Qwen2.5-14B's is 1.6x at batch 32 and 7x at batch 64.

**The screen uses `load-balance` and a stock engine**, the same routing the four
profiling stages used. FluidServe cannot be used: it reads the very profile the
screen exists to decide whether to build, and for a new model that table does not
exist yet. The engine's own `inter_token_latency` counter supplies the step time,
so the instrumented scheduler is not needed either — which also avoids the
failure that killed the first backend comparison, where the driver aborted on
`scheduler_cls=<none>` because the start-up lines had rotated out of the log.

## 2. Result: Qwen2.5-7B fails, and fails worse

`switch_model.py --to qwen25-7b-b200-tp1`, 8 instances x TP=1, KV pool
**2,321,296 tokens**, FLASHINFER backend. Two conditions, 60 and 150 req/s, four
minutes each.

| batch | tokens/request | step | floor | ratio | n |
|---|---|---|---|---|---|
| 0-32 | 1,952 | 3.6 ms | 1.9 ms | **1.9x** | 347 |
| 32-64 | 1,832 | **66.7 ms** | 2.5 ms | **26.8x** | 67 |
| 64-96 | 1,547 | 84.3 ms | 2.7 ms | 30.9x | 61 |
| 96-144 | 1,591 | 86.6 ms | 3.2 ms | 27.2x | 100 |
| 144-224 | 1,621 | 90.0 ms | 3.9 ms | 23.1x | 198 |
| 224-400 | 2,244 | 86.1 ms | 7.1 ms | 12.2x | 1,917 |

**No profile was built.** The stage cost twenty minutes instead of the three
profiling runs and roughly nine hours Qwen2.5-14B cost before the same conclusion.

## 3. What the two failures together rule out

**The plateau is the same value on both models.** Qwen2.5-7B settles at 84-90 ms
and Qwen2.5-14B at 76-97 ms, and they differ by a factor of two in parameters.
**A quantity that does not move when the model's arithmetic doubles is not the
model's arithmetic.** Both are also healthy below batch 32 (1.9x and about 1.6x),
so it is not a property the model has at all sizes of work — it appears above a
threshold and then stops depending on anything.

Nine explanations have now been refuted, each by measurement:

| # | claim | how it died |
|---|---|---|
| 1 | not enough step data | 311,921 -> 1,391,918 steps moved the fit's median error 57.0% -> 57.5% |
| 2 | a Qwen-family property | Qwen2.5-72B fits at R² 0.978 with the same generator |
| 3 | the saturated, preempting conditions | removing them makes the KV coefficient negative |
| 4 | rates 5-30 are clean, preemption is zero | at 20 req/s the share of steps over 50 ms goes 0.0% to 70-93% within three minutes; preemption is zero only because the KV has not filled |
| 5 | CUDA-graph fallback | the start-up log shows "Capturing CUDA graphs (decode, FULL): 0% -> 100%" |
| 6 | a batch cliff | the same batch bucket is 73-82 ms at rates 15-45 and 16.6 ms at rate 160 |
| 7 | prefill interference | the plateau survives steps with eight pure-decode steps on both sides (92.0 ms, n=1,814, against Llama's 16.8) |
| 8 | the attention backend | FLASHINFER and FLASH_ATTN agree to within 2% above batch 40 (76.0 vs 77.2, 84.0 vs 82.9, 87.9 vs 86.7) |
| 9 | KV size or memory pressure | Qwen2.5-14B reads **56% of what Qwen2.5-72B reads per GPU** and is 2.7x slower |

## 4. What is left, and it is two switches that move together

The five models split cleanly on one config field and one deployment field:

| model | `sliding_window` | TP | verdict |
|---|---|---|---|
| Qwen2.5-7B | **131072** | **1** | ❌ 30.9x |
| Qwen2.5-14B | **131072** | **1** | ❌ 10.3x |
| Qwen2.5-72B | 131072 | 2 | ✅ 2.1x |
| Llama-3.1-8B | None | 1 | ✅ 2.0x |
| Llama-3.1-70B | None | 2 | ✅ 1.7x |

`use_sliding_window` is **False** in all three Qwen configs, but the field is
declared, and the engine start-up log says on every boot:

> `Turning off hybrid kv cache manager because --kv-transfer-config is set. This
> will reduce the performance of vLLM on LLMs with sliding window attention or
> Mamba attention.`

**`--kv-transfer-config` is on the engine's command line** — Llumnix's
`HybridConnector` for migration — **while `LLUMNIX_ENABLE_MIGRATION=0`.** So the
connector is initialised, and the hybrid KV cache manager it disables is exactly
the thing the warning says a sliding-window model needs.

⚠ **This does not yet explain Qwen2.5-72B**, which declares the same field and is
fine. The only other axis that separates it is TP=2. **Two models on each side is
not enough to attribute a cause**, and the pairing "sliding window AND TP=1" is
a description of five points rather than a mechanism.

## 5. The next test, and its judgement rule

**Remove `--kv-transfer-config` from the engine and re-screen Qwen2.5-7B.**
Migration is already off, so nothing in the measurement path should need the
connector. About twenty minutes.

- **If the ratio falls to about 2x**: the connector is the cause, and the fix is a
  deployment change rather than a model change. ⚠ Then **Llama-3.1-8B must be
  re-screened under the same change** before any result from it is laid beside an
  earlier one — every measurement in this repository was taken with the connector
  present, and a deployment that changes decode step time changes everything.
- **If it stays near 30x**: the connector is not the cause, the "sliding window"
  reading is wrong, and the next candidate is whatever else separates TP=1 from
  TP=2 on this build.

## 6. What this screen is not

- It does not measure a policy. It measures the engine, with `load-balance` and a
  stock scheduler, precisely so the answer does not depend on FluidServe.
- It does not say a rejected model is bad. It says **this deployment cannot
  profile it**, and a profile built anyway would let the policy run normally while
  every number it computes is wrong.
- The floor uses a nominal 8 TB/s and bf16 weight sizes from the parameter count,
  so the absolute ratios carry maybe 10% of slop. **The decision does not turn on
  that**: the gap between the healthy band and the rejected models is a factor of
  five to fifteen.
