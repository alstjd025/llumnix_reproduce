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

## The engine-layer set, when the question is what the fleet did

The rate sweep above answers "which policy scored better". These answer "what
were the four engines actually doing", which is where a partition failure, a
queue and a stalled engine live. All three are glob-driven and take any sweep.

| Figure | Panels | Script |
|---|---|---|
| **engine layer, one condition** | running batch, engine-side queue, KV occupancy, fleet prefill and decode throughput, prefix hit rate, queueing time — four engines per panel | `plot_ratesweep_split.py` → `engine_rpm_*.png` |
| **per-engine throughput and latency** | one column per engine, rows prefill tok/s, decode tok/s, mean TTFT, mean ITL | same → `tokens_rpm_*.png` |
| **control plane** | gateway current / pending / being-inferred, and completed / rejected / errored per second | same → `llumnix_rpm_*.png` |
| **policies side by side, one rate** | one policy per column, rows running batch, engine queue, KV, fleet decode | `exp38_policy_compare.py` |

**Produce the per-condition figure and the side-by-side one, not one of them.**
Six panels of one run is the right shape for reading that run and the wrong
shape for comparing policies; three columns of four rows is the reverse. They
cost the same to generate.

**Rows in a side-by-side figure share a y axis across columns.** Without that, a
column that looks calm is calm only relative to itself, and the reader draws the
opposite conclusion from the one the data supports.

These panels come from the engine's own Prometheus series and the gateway's, so
they are unaffected by defects in what the load generator computes — which is
worth knowing when a client-side metric is under suspicion.

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

## A corrected metric has to reach the figure scripts too

When a measurement defect is fixed in analysis, the fix lands in one loader and
every other script keeps reading the raw column. `tbt_mean_ms` was found to be
half the true inter-token latency and corrected inside
`exp22_fluidserve.load_run`; three older scripts —
`plot_slo_vs_throughput.py`, `plot_per_engine_attainment.py`,
`exp14_per_class_slo.py` — read the column directly, so running them on the same
sweep would have drawn the pre-correction numbers under the same titles as the
corrected tables in the same experiment file.

`load_run` now publishes the corrected value as an **`itl_ms`** column. Use it.
Before drawing latency or attainment with a script you did not just write,
`grep -n "tbt_mean_ms"` it.

After any such refactor, re-run one condition through the table script and check
the numbers are unchanged before regenerating figures — the 60 req/s FluidServe
condition should still read 55.3 admitted, 34.6 offered, 12,171 goodput.

## Line style carries the arm; the legend is built from what was drawn

When colour already encodes one dimension, style encodes the other, and there
must be **one style per arm, not one for the arm of interest and one for the
rest**. `fig_latency` drew FluidServe solid and everything else dashed while its
legend named only PolyServe, so the Llumnix SLO arm was invisible as a distinct
line and mislabelled where it was visible. Build the legend from the arms
present in the data, never from a hardcoded list.

A legend of seven entries does not fit above the axes beside a two-line title;
it wraps and overlaps. Put it below.

## A script that parses the condition out of the directory name skips a whole class of run

`plot_ratesweep_split.py` and `exp38_policy_compare.py` located the condition
with `_rpm_(\d+)` on the directory name. A dynamic trace has no single rate, so
its directories carry no such token: the first raised `ValueError` and the
second **produced an empty result and said nothing**. Every hour-long trace from
EXP-41 to EXP-54 was therefore recorded with no engine-layer figures at all, and
nobody noticed because the request-level figures were there.

Both now fall back to the directory name as the tag. When writing or reusing a
script, assume the run set will one day include a shape the naming convention
did not anticipate, and **make the no-match case loud** — a script that plots
nothing must say which paths it rejected and why.

The same defect in a third form: `exp53_class_goodput.py` had its three
comparison runs written into `__main__`, so it could only ever draw EXP-52. It
takes `--hour label|colour|dir` now.

## A figure script takes a glob

`exp27_figures.main()` hardcoded EXP-27's pass patterns, so every later sweep
either edited that file or went without figures. It takes `--runs` now. A new
plotting script should take the run glob and the output directory and nothing
about which experiment it is.

## Verify what got drawn

`collect()` filters by a filename pattern, and a variant suffix silently drops
every run of one arm — `mix_of` required `_m1_rpm_` and dropped every `_m1f_`
run, so a figure carried a note naming an arm that was not on it. After
generating, confirm the arms and the point count are what was intended before
reporting the figure as done.

## Figures that go in the paper

Exploratory figures live in `results/aggregate_analysis/<exp>/` as PNG. Paper
figures live in `Agent_applications/agent_motivation_experiment/paper_figures/`
as PDF, one script per figure, with `paper_style.py` holding the shared
constants and `README.md` recording what data each figure is drawn from. A paper
figure is not an exploratory figure exported — the constraints below only apply
to the paper one, and they change the drawing.

**Import the metrics, never reimplement them.** A paper script imports
`collect`/`load_run` from the experiment's analysis script so that a correction
to the loader reaches the paper. It also means the paper figure changes silently
when the loader changes, so re-run it and check against the recorded table after
any change to `exp22_fluidserve.load_run`.

### Physical size is the whole problem

Draw the figure at the size it will occupy on the page, so `\includegraphics`
applies a scale factor of 1.0 and 8 pt in the script is 8 pt on paper. Any
rescaling in LaTeX multiplies every font size by the same factor.

- USENIX `usenix2019_v3.sty`: `\textwidth=7in`, `\columnsep=0.33in`, so one
  column is **3.335 in** and the full width is **7.0 in**. ACM `sigconf` is
  3.33 / 7.0, close enough that one figure serves both.
- Single column is one of the two columns. A 7 in figure dropped into one column
  is scaled by 0.48 and its 8 pt type renders at **3.8 pt**. That happened.
- To move a figure between one column and two, **redraw it at the other width**.
  Do not stretch it: the label trimming below is only correct at one width.

**Never `bbox_inches="tight"` on a paper figure.** It crops the canvas to the
ink, so the PDF comes out narrower than the width it was designed for, and
`width=\columnwidth` then scales it back UP — 3.12 in cropped from 3.335 became
a 1.07x enlargement of every glyph. Pin the canvas and fit the layout inside it
with `tight_layout(rect=...)`. Check with `pdfinfo`: the page size must be the
width you designed at.

Set `pdf.fonttype: 42`. The matplotlib default is Type 3, which several
camera-ready checkers reject.

### What has to give at 3.335 in

Two panels side by side leave about 1.3 in of plotting area each, which the
default labelling does not fit into. In order of what to cut:

- one x axis label centred under both panels, not one per panel;
- thousands as a `k` suffix on the tick labels (`6k`, not `6000`) — three
  characters less of axis width, and it lets the y axis labels stay one line;
- explain a line style once with a neutral key on a second legend row instead of
  naming it per arm. Five entries need about 3.6 in; four fit in one row only
  with `handlelength=1.3, columnspacing=0.6`;
- with more than about five x values, label a **subset of the measured rates**
  and give the rest an unlabelled minor tick. Do not switch to round numbers
  that were never measured.
- panel labels `(a)`/`(b)` go inside the axes or are dropped. Above the axes is
  where the legend is.

Two-line y axis labels are usually wrong here: a rotated label longer than the
panel is tall gets clipped, so shorten the text instead.

### Rules that only bite in a paper figure

- **An arm whose two denominators coincide gets one line, not two.** Drawing a
  dotted curve exactly on top of a solid one asserts a distinction the data does
  not contain. Draw the offered curve only for arms that actually reject.
- **Dropping the rejection annotations moves an obligation into the caption, it
  does not remove it.** Attainment on an admitted denominator is uninterpretable
  without the rejection rate — in EXP-53 the Llumnix SLO arm's admitted
  attainment *rises* 44.0 → 69.8% between 50 and 70 req/s while its rejection
  rate goes 20.7 → 68.9%. Produce both an `_offered` and an `_admitted` version,
  and say in the README which one is safe to use where.
- **A point with n=1 has no error bar and therefore reads as the most precise
  point on the figure.** State it in the caption. Every EXP-53 cell is n=1.
- **Do not reproduce a standing note from an analysis script without checking
  it.** `exp53_compare.py` prints "FluidServe and PolyServe run without
  migration" on every figure while its own counter reports 225 rescheduling
  pairs for PolyServe and 0 for Llumnix SLO. Unresolved notes stay out.

### Record what the figure is made of

`paper_figures/README.md` carries, per figure: the run glob and directories, n
per cell, which sessions, what was excluded and why, the metric definitions, the
full table of plotted values, and the list of things the caption must state.
This is what makes a figure re-derivable a month later, and it is where the
uncorrected-vs-corrected comparison for a re-scored run belongs.

### Refactoring these scripts

Regenerate and compare against the previous PDF **rendered to PNG** before
reporting done. A regex edit that moved figures onto the shared style module
also deleted the `tight_layout` and x-label lines; the script still ran and
still wrote a plausible-looking PDF. `pdftoppm -r 200` on both and `cmp` is the
check that caught it.
