# EXP-128 — the vLLM router at the halved budgets, and how an arm that never refuses is read

*Written 2026-09-11 17:50 KST, before the run. Sections 1 to 4 are the
pre-registration; section 5 is the result.*

## 1. What is being asked

The fourth and last baseline on the halved-budget hour trace, run last at the
user's instruction. `--scheduling-policy vllm-cache` is this repository's port of
the policy PyPI `vllm-router` ships as its default (`cache_aware`, itself a fork
of SGLang's model gateway). Like PolyServe, llm-d and Llumnix SLO it takes its
promise entirely from the workload file, so the halved budgets reach it by
handing it the same file the other three read; no new flag exists.

## 2. The one thing that makes this arm different, and what it does to the reading

**It has no admission control.** Its rejection rate is 0.0% by construction, so
its failures do not appear as rejections — they appear as requests that never
finish.

Measured on the four-instance 70B hour (EXP-109, `260901_2137_exp109r1_vllmcachet75_shift`):

| | |
|---|---|
| arrivals | 99,241 |
| rejected | **0 (0.0%)** |
| cut at the grace boundary (`is_server_terminated`) | **51,187 (51.6%)** |
| finished with output | 49,127 (49.5%) |
| window | **4,261 s** against the trace's 3,660 s — the whole 600 s grace consumed |
| engine queue depth | 2,942–4,381, against 0.2–21 for every other arm |

The four arms already measured on THIS trace and fleet are nothing like that:
cut 0.3% (Llumnix SLO 3.7%), windows 3,673–3,847 s.

### 2.1 The headline denominator is all-arrivals, and it is applied to all five arms

`exp22_fluidserve.attain` drops a request that was still in flight when the
window closed, from **both** its denominators. That is correct in general — a
long request that cannot finish inside a run is not evidence of a violation —
and wrong at the end of a backlogged run, where the requests still in flight are
exactly the slow ones. **An arm that refuses nothing converts its rejections into
unfinished requests, and both standard denominators then stop counting them.**

So the headline here is the third column of
`analysis_scripts/request_level/all_arrivals_attainment.py`:

```
all arrivals = met / (met + missed + rejected + unfinished)
```

**computed the same way for all five arms.** For the other four this changes
almost nothing — their unfinished share is 0.3% — so using the stricter reading
costs them nothing and removes the artifact for this one.

⚠ The `offered` and `admitted` columns are still reported beside it, because
dropping them would hide that this arm refuses nothing, which is the fact under
test.

### 2.2 The load generator is the other thing to watch

An arm that refuses nothing holds the most concurrent streams, and the
generator's ceiling is about 150 streams per CPython worker process; past it
every stream in that process freezes together and the arrival pacing loop freezes
with it. **The bias lands precisely on the axis under test** — the arm that
admits more is the one penalised. Llumnix load-balance once exhausted the
client's ephemeral ports at saturation, producing 62,114 `cannot assign requested
address` errors, and its attempted rate read as 170/s, which was the rate the
client was spinning at rather than the trace's.

The chain therefore reports, after each condition: arrivals delivered, rejected /
cut / finished shares, window length against the trace's, **started calls per
second against the trace's** (an attempted rate above the trace's means failures
were returning instantly and being retried), and the error kinds by count.

## 3. Settings

| | |
|---|---|
| arm | `vllmcachec25d50s38ftc2500d5000s3500` |
| trace | `dyn60_shift_m2Am1B_b1045_x620.csv`, one hour, 67–279 req/s |
| budgets | first token 2500 / 5000 / 3500 ms, per token chat 25 / deepresearch 50 / swe 38 ms |
| fleet | 8 × Llama-3.1-8B TP=1, gateway `-v 0` |
| binary | **`903889ac0e52bd18408644a71b797484`** — the same one all of EXP-126 and EXP-126b ran on |
| repeats | 2 |
| driver | `run_exp128_vllmrouter.sh` (snapshot of the EXP-126 hour driver plus this arm) |

## 4. What is expected, and what would refute it

**H1 — it rejects 0.0% and loses a large share of arrivals to the grace cut
instead.** *Refuted if its cut share is near the other arms' 0.3%*, which would
mean this fleet absorbs the load the 70B one could not and the comparison is
about something else.

**H2 — on the all-arrivals denominator it lands below every arm that refuses.**
Grounds: it is the only arm with no mechanism for shedding, and the trace spends
its peak at 279 req/s against a 173.5 req/s knee. *Refuted if it is within the
repeat spread of any refusing arm.*

**H3 — the standard `offered` column overstates it by more than ten points**
relative to all-arrivals, while moving the other four arms by under one point.
This is the quantitative form of the artifact §2.1 describes. *Refuted if the two
readings agree for this arm*, in which case §2.1's correction is unnecessary here
and should not be presented as if it mattered.

**Validity check.** Arrivals delivered must be ≥0.97 of the trace's 615,228, or
the run measures the generator. ⚠ **For this arm that check is weaker than usual**
— a backlogged client can deliver every arrival late, so delivery alone does not
prove the pacing held; the started-calls-per-second comparison is the one that
does.

## 5. Result — 2026-09-11 17:35 to 20:02 KST, two repeats

Both conditions delivered all 615,228 arrivals. The two repeats agree to within
0.8 points on every column, so what follows is reproducible — but part of it is a
reproducible measurement defect rather than a policy result, and §5.3 says which.

### 5.1 The five arms, all scored the same way

`all arrivals = met / (met + missed + rejected + unfinished)`, from
`all_arrivals_attainment.py`. Repeat bands, not means.

| arm | **all arrivals** | offered | admitted | rejected | unfinished | non-rejection errors | goodput (tok/s) |
|---|---|---|---|---|---|---|---|
| FluidServe + arrivals term | 72.8–73.5 | 72.9–73.6 | **99.8** | 26.2–26.9% | 0.1% | 0.0% | 73,192–73,663 |
| **FluidServe control** | **73.4** | **73.5** | 99.5–99.6 | 26.1–26.2% | 0.1% | 0.0% | **73,862–73,897** |
| llm-d | 58.5–61.3 | 58.6–61.4 | 91.6–91.9 | 33.2–36.0% | 0.1% | 0.0% | 63,499–65,530 |
| PolyServe | 44.5–44.9 | 44.6–45.0 | 70.6–71.6 | 36.8–37.2% | 0.1% | 0.0% | 50,735–51,198 |
| **vLLM router** | 20.3–21.1 | 21.3–22.0 | 21.3–22.0 | **0.0%** | 4.4% | **59.4%** | 17,162–17,755 |
| Llumnix SLO | 19.1–19.6 | 19.9–20.3 | 32.0–35.6 | 36.4–41.4% | 3.4–4.0% | 11.0% | 16,426–16,788 |

FluidServe leads every baseline: **+12.1 to +14.9 points of all-arrivals
attainment over llm-d, the nearest, and 13% more token goodput.** The repeat
spread is 0.0–0.7 for FluidServe, 0.4 for PolyServe, 0.8 for the vLLM router,
0.5 for Llumnix SLO and **2.8 for llm-d**, so the ordering is read against a
floor four times smaller than the smallest gap.

⚠ **The `admitted` column is meaningless for the vLLM router**: with no
admission control it equals `offered` by construction and does not ask whether
the system kept a promise it made.

### 5.2 Two of the three pre-registered hypotheses were wrong

**H1 half held.** It rejects 0.0% as predicted. But **the grace cut took only
4.4%**, not the 51.6% seen on the four-instance 70B hour. The failures went
somewhere else, and §5.3 is where.

**H2 partly refuted.** It is below FluidServe, llm-d and PolyServe, but **above
Llumnix SLO** (20.3–21.1 against 19.1–19.6, bands not overlapping), and Llumnix
SLO does refuse — 36–41%. So "below every arm that refuses" is not what was
measured; "below every arm except the one whose refusals do not buy it anything"
is.

**H3 refuted.** `offered` 22.0 against all-arrivals 21.1 — **0.9 points**, not
the ten the correction in §2.1 was built for. The reason is in the runner's own
code: a connection failure is recorded as `is_error`, which **keeps it in the
offered denominator as a violation**, deliberately, so that a broken client stays
visible. The unfinished-request artifact §2.1 describes is real in general and
did not bind here. **The correction should not be presented as if it mattered for
this arm.**

### 5.3 Where its failures actually went, and what cannot be concluded

**59.4% of arrivals ended in `client connection exhausted`** — 365,625 and
365,695 across the two repeats.

It is not the client running out of sockets on its own: **the arrival pacing
held**, 165.4 calls/s mean and 276.8 peak against the trace's 168.1 and 279.3. A
client spinning on instant failures reads *above* the trace's rate, and this one
does not.

What the control plane shows instead:

| arm | client arrivals | **requests the gateway saw** | gateway concurrent p90 | engine waiting queue |
|---|---|---|---|---|
| **vLLM router** | 615,228 | **221,282 (36%)** | **27,676** | **2,784–2,847** |
| FluidServe | 615,228 | 453,679 (74%) | 2,546 | 0–21 |
| PolyServe | 615,228 | 388,575 (63%) | 2,587 | — |

**The policy that refuses nothing fills the control plane, and the arrivals
behind it cannot get in.** The gateway holds eleven times what any other arm
holds and the engines queue hundreds of times deeper.

⚠ **Whether those 59.4% are the policy's property or the harness's ceiling is not
separated by this run.** The client's socket limit and the gateway's admission
capacity are both being reached, and `error_msg` does not record which of the
four keywords matched (`cannot assign requested address`, `max retries
exceeded`, `too many open files`, `address already in use`) — and CLAUDE.md
already notes that "Max retries exceeded" is the phrase `requests` attaches to
any connection failure, including one the server caused. **Llumnix SLO has the
same error in 11.0% of its arrivals**, and it refuses 41%, so the error is not
exclusive to arms without admission control.

**So: the vLLM router's and Llumnix SLO's absolute numbers here are lower bounds.
The ordering of the top four is not affected — those four have 0.0%
non-rejection errors.**

→ To close it: record which keyword matched in `error_msg`, widen the runner's
`net.ipv4.ip_local_port_range`, and re-run one condition. Until then the sentence
that travels with this row is the one above.

### 5.4 Scope

- Two repeats, one fleet (8 × Llama-3.1-8B TP=1), one trace, one budget set.
- **These columns cannot sit beside any standard-budget table.**
- The other three baselines take their promise from the same workload file, so
  the halved budgets also moved PolyServe's tier boundaries to 25 / 38 / 50 ms,
  and **PolyServe's §4.5 memory accounting treats the fleet as full at about 60%
  observed occupancy** (EXP-127). Both belong beside its row.
