# EXP-127 — does PolyServe's memory test refuse while the pool is half empty?

*Written 2026-09-11 11:20 KST, before the run. Sections 1 to 5 are the
pre-registration; section 6 is the result.*

## 1. The question, and why it cannot be answered from what is already on disk

On the four-instance 70B hour trace (EXP-109), PolyServe refused **454,553**
placements and **90.5% of them cited memory**; the paper's time-based checks
(first token, second token) fired in **0.08%**. Over the same hour **no engine's
observed KV occupancy ever passed 64%** (p99 63.7 / 64.4 / 50.9 / 63.3%), and the
engine dedicated to the agent class sat at a p50 of **36.7%**.

That looks like an accounting that refuses work the hardware could have taken,
but it cannot be concluded from those two facts, because **the quantity the test
compares against is not recorded anywhere**:

- `instance_cms_kv_cache_usage_ratio_projected` is named "projected" but is **not
  the admission projection** — its values equal the engines' observed KV ratios
  (0.559 / 0.538 / 0.368 / 0.586 against 55.7 / 54.1 / 36.7 / 58.5%) and it never
  passes 1.0. Another case of one name over two quantities.
- `NumTotalGpuTokens`, the pool size the test compares to, is in no series.
- Deriving the pool as `instance_cms_all_decodes_tokens_num ÷
  instance_cms_kv_cache_usage_ratio_projected` gives **712k / 791k / 1,329k /
  793k across four identical instances**, so that derivation is wrong. The gauge's
  numerator is not the quantity being divided, and the error is largest on the
  instance holding the class with 6,812-token prompts — where most of the KV is
  prompt rather than decode tokens.

So the arithmetic has never been checked. This run records both sides of it.

## 2. What the test actually computes

From `polyserve.go:126-139` and `:570-573`, the paper's section 4.5 forward
simulation:

```
projected = allDecodesTokensNum + promptTokens        // what the instance holds, plus the new prompt
          + (decodeBatchSize + 1) x decodeTokens.forTier(tier)   // every resident request's full expected output
refuse if projected > NumTotalGpuTokens
```

**There is no term for what each resident request has already produced.** A
deepresearch request that has already emitted 900 of its expected 985 tokens has
those 900 inside `allDecodesTokensNum` and is then charged a further 985. In a
steady-state population the average request is roughly half done, so the growth
term is overstated by something near a factor of two. On the 70B profile the tier
expected outputs are **494 / 428 / 985 tokens** (swe / chat / deepresearch), so at
the observed batch of 103 the growth term is about 102,000 tokens and roughly half
of that is demand that does not exist.

That the term is structurally conservative is certain from the code. **Whether it
is large enough to be what refuses is the measurement.**

## 3. Settings

Run on the **current eight-instance Llama-3.1-8B fleet**, not the 70B one, because
that fleet is deployed and because EXP-126 measured this exact PolyServe arm on it
two hours ago, so the new numbers sit beside an existing two-repeat column.
⚠ **The conclusion therefore applies to this fleet.** Carrying it to the 70B hour
is an extrapolation until the same two series are recorded there.

| | |
|---|---|
| arm | `polyservepc25d50s38ftc2500d5000s3500` — the paper mechanisms, budgets halved |
| rates | **9,600 and 12,600 rpm (160 and 210 req/s)**, 8 minutes each, one repeat |
| binary | `c549de9ed1e1dd7227a63f246cae2f64` |
| backup | `bin-backup/scheduler-exp07.pre-exp127` = `903889ac0e52bd18408644a71b797484` |

**Why those two rates.** At 160 req/s this arm rejected **1.7%** and at 210 req/s
**24.1%** (EXP-123, same budgets, same fleet). One rate where the test barely
fires and one where it does, so the occupancy at refusal can be contrasted rather
than read from a single point.

**What changed in the binary.** Two per-instance gauges in `publishLocked`, and
nothing else. Verified by building the same tree with the change removed: that
binary is `903889ac…`, byte-identical to what is deployed now, so the two builds
differ only by the gauges.

- `scheduler_polyserve_kv_capacity_tokens` — `NumTotalGpuTokens`, constant per instance
- `scheduler_polyserve_projected_kv_tokens` — the scheduler's own last projection

The capacity alone would let the projection be reconstructed offline from series
already collected; the scheduler's own value is published beside it so the
reconstruction can be checked rather than trusted.

⚠ **The projection sample is specific to one candidate.** It is the last value
computed for that instance, carrying that request's prompt and, more importantly,
**that request's tier** in `decodeTokens.forTier`. A single sample is therefore
not "the instance's projection"; only the distribution over a run is meaningful.

## 4. What is expected, and what would refute it

**H1 — the projection reaches the pool while the pool is not reached.** At
12,600 rpm, `projected / capacity` should approach or exceed 1.0 in the windows
where memory refusals are counted, while `observed / capacity` stays well below —
under about 0.7, matching what every engine showed on the 70B hour.

*Refuted if `observed / capacity` is also near 1.0 when refusals happen.* In that
case the pool genuinely is reached, the accounting is not what refuses, and the
sentence "PolyServe's memory provisioning is too conservative" must not be
written.

**H2 — the gap is larger at the higher rate.** The growth term scales with the
resident batch, so the overstatement should grow with load and the two rates
should separate.

*Refuted if the two rates show the same gap*, which would mean the term is not
what moves with load.

**Validity check, and this one gates the rest.** The run must reproduce EXP-123's
columns for this arm at these two rates — offered **74.3** and **45.9**, rejection
**1.7%** and **24.1%** — within the repeat spread. If it does not, the binary or
the environment changed something beyond the gauges and no reading from it is
usable. ⚠ EXP-123 is one repeat at each rate, so the spread is not known from it;
a difference of a few points is not by itself a failure, but a different rejection
regime is.

## 5. What this run cannot answer

- **Whether removing the overstatement would help.** That needs a second arm with
  a progress term subtracted, which is a change to a baseline's algorithm and has
  to be argued against the paper's text before it is written.
- **Anything about the 70B fleet**, as stated in section 3.
- **Which class's requests the refusals belong to.** `scheduler_polyserve_refused_total`
  carries `stage` and `reason` but no class label, so "chat was the one being
  turned away" remains unsupported, as it was in EXP-109.

## 6. Result — 2026-09-11 12:32 to 13:15 KST

**Validity check passed.** The build with the gauges made the same decisions as
the one without:

| rate | | offered | admitted | rejection | arrivals |
|---|---|---|---|---|---|
| 160 req/s | EXP-123 | 74.3 | 75.6 | 1.7% | 72,572 |
| | **EXP-127** | **74.6** | **75.9** | **1.7%** | 72,565 |
| 210 req/s | EXP-123 | 45.9 | 60.6 | 24.1% | 95,254 |
| | **EXP-127** | **47.4** | **62.4** | **23.9%** | 95,216 |

Offered moves 0.3 and 1.5 points and rejection 0.0 and 0.2 points, so nothing
beyond the gauges changed.

### 6.1 The measurement

**The pool is 1,037,648 tokens per instance**, identical on all eight — a number
that had never been recorded in any run before this one.

| | 160 req/s | 210 req/s |
|---|---|---|
| refusal samples | 2,046 | 2,380 |
| **projected / capacity** at the refusal | p10 **1.00**, p50 **1.01**, p90 **1.06** | p10 1.00, p50 **1.01**, p90 **1.08** |
| fleet's **fullest** engine at that moment | **60.4%** | **60.1%** |
| fleet's median engine | 55.9% | 55.8% |
| fleet's emptiest engine | 37.9% | 35.5% |
| refusals with the fullest engine **below 70%** | **100.0%** | **100.0%** |

**H1 is confirmed and its refutation did not occur.** The projection sits at
1.00 to 1.08 — where a test that refuses above 1.0 must sit — while the fullest
engine in the fleet is at 60%. Not one refusal in either condition happened with
any engine above 70%.

**H2 is not supported.** The two rates are indistinguishable on both quantities
(1.01 / 60.4% against 1.01 / 60.1%) even though rejection goes from 1.7% to
23.9%. **The accounting stops at the same place at both loads; what changes with
load is how often that place is reached**, not where it is.

### 6.2 Two causes, and the second was found by the arithmetic failing

**⑴ No progress term**, certain from the code (section 2). Every resident request
is charged its tier's full expected output regardless of how much of it has
already been produced, so in a steady-state population the growth term is roughly
doubled.

**⑵ A logical token count compared against a physical pool**, found while trying
to compute the projection offline. Dividing `instance_cms_all_decodes_tokens_num`
by this capacity gives **p50 88.7% and p99 141%** on the EXP-126 PolyServe hour
runs — above the pool, which is impossible — so the scheduler's decode-token sum
is the count with prefix sharing undone while `NumTotalGpuTokens` is physical
blocks. FluidServe divides by exactly this ratio (`capMem = kvCapacity x safety /
ratio`); **the PolyServe port has no such term.**

⑴ alone is about a factor of two on one term and cannot take 60% to 100%; ⑵ is
the rest. ⚠ **⑵ is not yet confirmed in code** — that `allDecodesTokensNum` is
the unshared sum is inferred from the impossible ratio, not read.

### 6.3 Scope

- **This fleet.** On the four-instance 70B hour the engines' observed occupancy
  was 37-59% (p99 51-64%) and 90.5% of refusals cited memory, so the same
  direction is likely, but the same series were not recorded there.
- ⚠ **The eight-instance fleet does reach its pool at the top of the
  distribution** — engine p99 is 96-99.7% on the EXP-126 PolyServe hour. The
  refusals measured here are not at those moments: the fullest engine was below
  70% every time.
- **One repeat per rate.** The two rates agree to two decimal places on the
  projection and to 0.3 points on occupancy, which is the only reason a single
  repeat is quoted.
- **Instance-to-port join was not possible** — these runs have no
  `analysis/request_engine.csv` — so the comparison is against the fleet's
  distribution rather than the refused instance's own occupancy. It does not
  change the conclusion: the fullest engine in the fleet was below 70%.

## 7. Decision — the port is not changed (2026-09-11, user)

**PolyServe stays as it is.** Asked whether to repair the accounting and
re-measure, or to leave the port faithful and report the decomposition, the user
chose the second: *"굳이 더 잘하게 할 필요 없어. 이게 논문에 충실한거거든."*

So the sentence that goes with every PolyServe column is the measurement, not a
repair: **its section 4.5 admission test treats the fleet as full at an observed
occupancy of about 60%, and every refusal measured here happened with no engine
above 70%.**

⚠ **This must be stated wherever PolyServe's rejection rate is quoted**, because
part of what separates it from FluidServe in EXP-126 is this accounting rather
than the partition. The two are not separated by this run.

The instrumentation stays in the code. The binary carrying it is
`bf9e01b6743a4354bf825852faf9d299`; **the deployed binary was restored to
`903889ac…` afterwards** so that EXP-126b's two repeats run identical code.
