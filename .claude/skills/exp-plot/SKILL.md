---
name: exp-plot
description: Build figures and result tables for llumnix_reproduce experiments. Use whenever asked to plot, chart, visualise or tabulate a rate sweep, an arm comparison or a dynamic-trace run. Covers the standard figure set, the paper style, and the reporting rules that keep a figure from claiming more than the runs support.
---

# Figures and tables

## The standard set

Whenever a rate sweep finishes, these are produced without being asked:

| Figure | Answers | Script |
|---|---|---|
| **attainment vs rate** | the headline. Solid = admitted denominator, dotted = offered. The gap between them **is** the rejection cost | `exp27_figures.py` `fig_split` |
| **goodput vs rate** | separate figure, not a second axis, when the two need to be read precisely | same |
| **per-engine state over time** | decode batch, queue depth, KV per engine. The only thing that shows a fleet idle in three places and saturated in the fourth | `fig_engines` |
| **TTFT and ITL per class against budget** | separates "the engine is slow" from "the request waited". If ITL is inside budget and TTFT is not, nothing was overloaded | `fig_latency` |

For dynamic traces add the per-segment and transition breakdown
(`exp30_dynamic.py`): a whole-run mean of a run whose workload changes cannot
answer what the run was for, because an adaptation cost lives in the seconds
after each boundary and is diluted by the steady stretches either side.

## Axes and units

- x axis in **req/s**, not rpm. Ticks at the measured rates.
- attainment y axis `SLO attainment (%), per request`, 0–105.
- goodput y axis `goodput (output tokens/s)`.
- error bars are min..max over repeats. **A point with one run gets no bar and
  the note must say so.**

## Paper style

`PAPER_STYLE` in `exp22_fluidserve.py`: serif, font 8, all four spines, y-axis
dotted grid only, frameless legend, `dpi=300`. Arm colours are fixed so they
mean the same thing across figures — PolyServe `#d62728`, Llumnix SLO `#2ca02c`,
FluidServe `#1f77b4`.

Images over 2000 px cannot be read back; downscale a copy to inspect one.

## Rules that keep a figure honest

- **Both denominators on the attainment figure.** Admitted alone rewards a
  policy for refusing the requests that were going to miss, which is the axis
  under test.
- **Report every scoring combination, not the flattering ones.** A v0.1 table
  carried four of the five and asserted from that there was no scoring rule
  under which FluidServe lost. The omitted one — equal weight across classes on
  the offered denominator — is one PolyServe wins at 80 req/s, because equal
  class weighting does not count that the class it collapses is 76.9% of the
  requests.
- **Say in the note when arms come from different sessions**, with the measured
  size of that movement: 0.1 points at 20 req/s, 2.0 at 40, 4.6 at 80, from five
  PolyServe runs whose code never changed.
- **Do not draw an arm measured under a different setting beside one that was
  not.** The Llumnix SLO arm at 25 ms/token rejected 98% of the agent class; its
  curve belongs to a policy answering a different question and is excluded, not
  footnoted.
- **A run that failed its health check does not go in a figure.** Check
  `run_health.py` first; a condition that ran on three engines plots normally.

## One aggregation, and it is per request

Attainment is aggregated **per request**: every request counts once, no weight
applied. Report it on both denominators, with the rejection rate and token
goodput, and with the three per-class numbers beside it.

Do not print the class-equal average as a headline. It is the unweighted mean of
the three per-class numbers, so it carries nothing they do not, while hiding
which class produced it and giving a class that is 7.7% of the requests one
third of the score. `equal_mix()` stays in the code because results recorded
before 2026-07-30 are stated in it.

Two scripts disagreed on this for three days: `exp23_rate_sweep.py` drew the
class-equal average under the title "attainment" while `exp27_figures.py` drew
the per-request one, so the same run produced two headline figures that did not
match and neither said which it was. When adding a panel, name the aggregation
in the title.

## Verify what got drawn

`collect()` filters by a filename pattern, and a variant suffix silently drops
every run of one arm — `mix_of` required `_m1_rpm_` and dropped every `_m1f_`
run, so a figure carried a note naming an arm that was not on it. After
generating, confirm the arms and the point count are what was intended before
reporting the figure as done.
