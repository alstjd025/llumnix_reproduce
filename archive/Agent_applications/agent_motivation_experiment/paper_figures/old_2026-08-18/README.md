# old_2026-08-18 — the static-sweep figures as they stood before EXP-82/86

**These twenty-four PDFs are superseded. Do not cite a number off them.** They are
kept because the drafts written before 2026-08-18 quote them, and a reader who
finds a value in an old draft needs somewhere to see where it came from.

Every figure here draws the same five control planes on the same eight arrival
rates and the same workload. What changed is **which runs**, and the reason is
below.

## What replaced them

| | superseded (here) | current (`paper_figures/`) |
|---|---|---|
| runs | EXP-68 / 69 / 70 / 72 / 77 / 80, six experiments over six days | **EXP-82** (FluidServe, llm-d) and **EXP-86** (PolyServe, Llumnix SLO, vLLM router) |
| pinned set | `paper_experiment/static_sweep_2026-08/` | `paper_experiment/static_sweep_clean_2026-08/` |
| gateway | Go thread pool sized from the host's 72 CPUs | `GOMAXPROCS=16` |
| repeats | two everywhere, after EXP-80 filled 23 cells | two everywhere |

## Why they were replaced

The Llumnix Go gateway sized its thread pool from the host's 72 CPUs while its
container quota was 8 cores per 100 ms period, so the kernel descheduled it for
**18.7% of each run**. A stopped gateway freezes every stream it is carrying at
the same instant and, when it is scheduled again, writes several already-generated
tokens to the client at once. The share of token intervals under 5 ms was
**14.11%** and is **0.29%** after `GOMAXPROCS=16`.

**Four of the five arms pass through that gateway and llm-d does not**, so the
distortion fell on one side of the comparison. EXP-82 re-measured FluidServe and
llm-d under the fix and EXP-86 re-measured the other three; the two together are
the first eight-rate, two-repeat set in which all five arms were instrumented
the same way.

## ⚠ It was not only the instrumentation, and this is not settled

EXP-82 wrote down in advance (its section 5.2) that a gateway stopped for 18.7%
of the run might be holding FluidServe back through its own hold-and-retry path,
and that **if attainment moved, the experiment had fixed the system rather than
the measurement, in which case the pinned sweep would have to be re-scored.**

Measured on the figure's own metric (per-request offered attainment; the bracket
is the two repeats):

| req/s | FluidServe attainment | rejected | token goodput |
|---|---|---|---|
| 45 | 59.4 [59.4, 59.5] → **62.8 [61.4, 64.2]** | 35.4% → 32.6% | 13,003 → 13,451 |
| 55 | 48.3 [47.4, 49.1] → **51.5 [50.8, 52.1]** | 46.3% → 43.7% | 13,184 → 13,557 |
| 70 | 38.2 [36.9, 39.4] → **41.9 [41.5, 42.2]** | 56.1% → 53.7% | 13,053 → 13,911 |

The old and new intervals do not overlap at any of the three, the three move in
the same direction, and the arm rejects **less** while attaining **more**. That
is the shape of a system that behaved differently, not of a metric recomputed.

`ms_dev/notes/STATUS.md` currently says attainment, rejection and goodput stayed
inside the repeat spread. **The two statements disagree and the disagreement is
open.** EXP-82's own results section is still empty. Until it is settled:

- the figures in `paper_figures/` are correct either way — they draw the runs
  that exist;
- **the prose is not**. "We re-measured with corrected instrumentation and the
  numbers did not change" cannot be written yet, and the change is in our
  favour, so a reviewer will ask.

llm-d moves the other way at 45 req/s (24.9 → 20.6, intervals also disjoint).
llm-d does not pass through the Llumnix gateway, so the gateway fix cannot
explain it; that shift is inside the session-to-session movement this repository
has measured at up to 4.6 points.

## What barely moved

The capacity number, because it is set at 25-35 req/s where the shift is +0.9
and +0.3 points:

| | superseded | current |
|---|---|---|
| FluidServe | 28.0 | **28.4** |
| vLLM router | 21.4 | **21.6** |
| Llumnix SLO | 20.4 | **20.4** |
| llm-d | 19.9 | **20.3** |
| PolyServe | 15.8 | **15.9** |

FluidServe / llm-d = 1.41 → **1.40**; against PolyServe 1.78 → **1.79**.

## What is NOT here, and why

- **`exp71_hour*.pdf` and `qoserve_engine*.pdf` stayed in `paper_figures/`.**
  They are on the earlier gateway as well, but nobody has re-measured the hour
  trace or the QoServe arm, so they are not superseded — they are unrepeated.
  ⚠ That leaves the paper mixing two instrumentations, which is defensible
  because attainment and goodput are the quantities those figures show and the
  contamination moved per-token quantiles rather than those, **but it has to be
  said once somewhere.**
  ⚠ The QoServe figure's control arm is EXP-77's vLLM router. After this swap
  the same "vLLM" has two curves in the paper, and at 25 req/s they read 66.6
  and 70.9 — which is exactly where that figure's most delicate claim sits.
- **`ideal_*`, `azure_*`, `workload_lengths`, `three_way_split`,
  `motivation_four_panels` stayed.** None of them reads the static sweep: the
  first family is a schematic with no measured data and the rest are properties
  of the source trace and the workload.
- **`exp53_*`, `exp55_*`, `exp27_*`, `exp50_*` stayed**, and they are stale for
  a different reason: they predate the 2026-08-08 load-generator fix, when
  twelve workers sent the same prompt sequence and engine prefix cache hit rate
  read 83-86% instead of 28.9%. Mixing two kinds of staleness in one folder
  would make this one unreadable.

## Rebuilding either version

The current figures come from the four scripts in `paper_figures/`, unchanged in
location — only the `ARMS` table in `fig_intro_capacity.py` was repointed, and
the other three import it:

```bash
python3 paper_figures/fig_intro_capacity.py
python3 paper_figures/fig_motivation_throughput_goodput.py
python3 paper_figures/fig_intro_reject_goodput.py
python3 paper_figures/fig_preemption_kv.py
```

The superseded versions are reproducible from git: the `ARMS` table before this
change is in the commit that precedes it, and the runs it names are still on
disk and still pinned in `paper_experiment/static_sweep_2026-08/`.
