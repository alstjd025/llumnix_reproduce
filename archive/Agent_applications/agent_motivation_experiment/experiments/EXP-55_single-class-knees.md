# EXP-55 — where each class saturates, and what runs out first

Run 2026-08-04 02:16 → 07:27 KST. 24 conditions, 8 minutes each, all healthy.

## 1. Design

One class at a time. With a single-class workload **no routing decision can
differentiate anything**, so the four engines under `load-balance` are four
replicas of the same single-class experiment and the per-engine series are read
directly. Migration off, stock vLLM FIFO, no admission control.

Workload configs `single_{chat,deepresearch,swe}.json` are
`mix_short_m1_balanced.json` with the mix set to one class; every other field,
including the SLO table and the transcript, is unchanged.

Rates were set by two calibration passes because the classes differ by an order
of magnitude: chat 40–90, deep research 8–30, swe 12–48 req/s.

**Caveat**: bursts are spread over four engines, so the knee sits very slightly
higher than a true single engine would show.

## 2. Result — the knee, and the state of the engine at it

| | knee (req/s) | offered across it | ITL / budget | KV p90 | queue p90 |
|---|---|---|---|---|---|
| **chat** | 65 → **70** | 94.9 → **53.3** | 35.4 → **49.2 / 50** | 22 → **27%** | 2 |
| **swe** | 24 → **28** | 87.7 → **40.4** | 44.8 → **65.4 / 62** | 36 → **64%** | 1 |
| **deepresearch** | 16 → **18** | 99.9 → **58.6** | 67.0 → **95.2 / 100** | 83 → **100%** | 1 → **140** |

**Three different walls.**

- **chat is pace-bound with memory to spare.** It crosses its 50 ms budget while
  KV is at 27% and the queue is 2. Nothing is full; the tokens are just too slow.
- **swe is also pace-bound, at a different threshold** — 62 ms/token, its 30 s
  end-to-end budget divided by 494 expected output tokens — and it gets there
  with KV at 64%.
- **deep research is the only one that hits memory.** Its delivered pace never
  exceeds its own 100 ms budget at any rate in the sweep (max 102.6 at 26 req/s).
  KV reaches 100% at 18 req/s and the queue goes from 1 to 140, so what breaks it
  is waiting, not speed.

## 3. It is not just prompt size

Normalised by input tokens per second the knees are **45k (chat), 79k (deep
research), 174k (swe)** — a factor of 3.8, and **the order changes**: by request
rate chat survives longest, by input tokens it fails first. So neither "requests
per second" nor "tokens per second" explains it.

## 4. And the batch sizes are similar while the memory is not

At their knees the three run at batch 327 / 317 / 418 — within 30% — while KV
reads 27 / 64 / 100%. One request holds 1,077 logical tokens in chat and 5,361 in
deep research, a factor of 5. **Deep research never reaches the batch its own
latency budget would allow, because memory runs out first.**

## 5. What this is for

The three quantities that decide each class are measured in **ms/token, tokens,
and seconds**. Any single scalar threshold — KV occupancy, queue depth, one
global TTFT/TPOT pair — is a proxy for one of them and merely correlated with
the other two. That is the motivation for deciding per request whether a given
instance can serve it inside its own budget, and it maps onto the conditions in
`feasible` one for one.

Figure: `results/aggregate_analysis/exp55/knees.png`, built by
`analysis_scripts/request_level/exp55_knees.py`. Third panel is the claim: KV on
x, delivered pace as a percentage of the class budget on y, with both limits
drawn.
