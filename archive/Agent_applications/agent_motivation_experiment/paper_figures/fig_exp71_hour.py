#!/usr/bin/env python3
"""Paper figure: five control planes on the hour-long dynamic trace.

  exp71_hour.pdf         7.0 x 1.70 in, `figure*`, width=\\textwidth
  exp71_hour_4panel.pdf  7.0 x 1.75 in, the same with throughput added in front

Panels against time, in one file, so that the two denominators sit beside each
other and can be read against the tokens in the same glance:

  Throughput          every output token the engines emitted, whether or not the
                      request it belonged to met its rule or even finished
                      (four-panel file only)
  Token goodput       output tokens per second belonging to a request that
                      finished inside its rule
  SLO attainment, admitted   only the requests the policy accepted
  SLO attainment, offered    every arrival is in the denominator, so a rejected
                      request counts as a violation

THE FOUR-PANEL FILE IS THE ONE THAT SHOWS THE ENGINES WERE BUSY. Throughput and
goodput are drawn on ONE shared y axis, so the vertical gap between a policy's
two curves is the work it did that nobody could use. The vLLM router is the
extreme: its throughput peaks above 20,000 tokens/s -- the highest of the five at
that moment -- while its goodput at the same minute is near zero.

THE TWO ATTAINMENT PANELS ARE THE SAME MEASUREMENT ON TWO POPULATIONS, AND THE
DISTANCE BETWEEN THEM IS THE REJECTION RATE. Over this hour the five arms reject
16.4% (FluidServe), 37.9% (llm-d), 31.2% (Llumnix SLO), and 0.0% (the vLLM router
and PolyServe, neither of which has admission control), so the admitted panel
flatters three of them by three different amounts and leaves two unchanged: for
those two the two panels are identical lines. Neither panel alone supports a
ranking, which is why both are drawn rather than one being chosen. The goodput
panel is the guard on both — a rejected request produces no tokens, so no choice
of denominator can inflate it.

WHOLE-HOUR NUMBERS FOR THIS RUN (pass 2 alone, the run drawn here):

                 rejected   offered   admitted   goodput tok/s
  FluidServe       16.4%      80.2       96.0          10,756
  llm-d            37.9%      57.6       92.8           8,438
  Llumnix SLO      31.2%      37.7       54.8           5,536
  vLLM router       0.0%      30.6       30.6           3,563
  PolyServe         0.0%      11.2       11.2           1,561

WHAT THE HOUR IS. `dyn60_short_m123_b1045.csv`: an Azure production arrival
trace rebanded to 10.8-45.0 req/s (median 24.8) with the class mix stepping
m1 -> m2 -> m3 -> m1 at 15-minute boundaries, marked by the grey guides. About
98,200 arrivals reached each arm. The guides are NOT labelled on the figure, so
the caption has to say what they are.

⚠ SWE IS SCORED THE SAME WAY IN EVERY ARM BUT CONFIGURED TWO WAYS. llm-d and
Llumnix SLO cannot express an end-to-end budget, so they receive the `m1f`
workload configuration, which splits the agent class's 30 s end-to-end budget
into a (first-token, per-token) pair; FluidServe and PolyServe receive `m1`.
Scoring is the 30 s end-to-end rule for all four. This does not affect the
panels here, which are whole-mix, but it means no per-class agent comparison
should be drawn from these runs.

⚠ NOT COMPARABLE WITH THE EXP-54 HOUR FIGURE, on two counts, either of which
alone is disqualifying. The load generator was fixed on 2026-08-08: a per-worker
dataset split never ran on mixed workloads, so twelve workers sent the same
prompt sequence and engine prefix cache hit rate was inflated from 28.9% to
83-86%. And the arrival band changed on 2026-08-09 from 24.9-74.7 to
10.8-45.0 req/s. `exp54_hour_*.pdf` predates both.

RUN-BOUNDARY CUTOFFS, AND WHY THE X AXIS STOPS AT 53.8 MINUTES. A request still
in flight when the run ends has an unknown outcome, so `attain()` drops it from
both denominators rather than scoring it a violation. That is right in general
and wrong at the end of a backlogged run, where the requests still in flight are
precisely the slow ones. The two arms with no admission control are backlogged
and the effect is severe. PolyServe's in-flight-at-end share is 0.0% until
minute 54, then 14.1 / 19.7 / 32.0 / 55.3 / 78.7 / 90.7% over the next four
minutes, and its offered attainment rises with it from 1.0% at minute 53 to
97.9% in the final window; nothing recovered, the population changed. The vLLM
router crosses earlier -- 0.9 / 5.0 / 11.9 / 24.4 / 41.5 / 62.2% from minute 53
-- and reaches 100%. FluidServe, llm-d and Llumnix SLO never exceed 4.4 / 0.7 /
1.8%, because they reject and do not build a backlog.

Windows above MAX_CUTOFF are therefore dropped and ALL FIVE arms are cut at the
same time, so the panels stay comparable; the trim is reported when the script
runs and lands at 53.8 of 59.8 minutes, set by the vLLM router. Over the whole
hour PolyServe loses 7.2% of its arrivals this way and the vLLM router 11.3%,
against 0.1-1.3% for the other three, and those requests sat behind a deep
queue, so the exclusion is in those two arms' favour: counting PolyServe's all
as violations gives 10.4% instead of 11.2% for the hour.

ONE RUN PER ARM, AND IT IS PASS 2. `exp71br1` for four arms and `exp77br1` for
the vLLM router, the second of two passes in each case. Pass 1 exists for all
five and the two passes agree to 0.0 / 0.2 / 1.5 / 1.8 points of offered
attainment for PolyServe / Llumnix SLO / FluidServe / llm-d and 0.5 for the vLLM
router (31.1 against 30.6), so nothing on these lines is noise-bounded but the
whole-hour level is reproducible. The two-pass averages for the first four are
in `experiments/EXP-71_hour-trace-v02.md` section 3.5.

⚠ THE VLLM ROUTER COMES FROM A DIFFERENT EXPERIMENT AND A DIFFERENT DAY (EXP-77,
2026-08-10) than the other four (EXP-71, 2026-08-08). It replays the same trace
file on the same four engines and the same workload, so the join is legitimate,
but session-to-session movement on this workload has been measured at up to 4.6
points and the caption should say the arms are not all from one session.

⚠ llm-d received no warmup run. Its predictor learns online and the three-minute
warmup the other arms get would have to hold an arrival rate this trace never
holds. This may be unfavourable to llm-d.

COLOURS. The repository's policy colours as the other paper figures use them:
FluidServe blue, PolyServe red, Llumnix SLO green, llm-d brown. The EXP-71
analysis script draws FluidServe v0.2 in cyan instead; that is the same policy
these figures already call FluidServe, so the blue is kept.

    python3 paper_figures/fig_exp71_hour.py
"""
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import TEXT_W, STYLE, GRID, ARM_COLOR, kfmt, save  # noqa: E402
from exp22_fluidserve import load_run, attain  # noqa: E402
from exp41_dynamic_timeline import windows, WIN  # noqa: E402

# Label, run directory, line style. The directories are named rather than
# globbed: `results/*exp71*_fspfx_fullb` matches BOTH passes, and this figure is
# one pass. The arm names and the paper names disagree on purpose -- `fspfx` is
# FluidServe v0.2, which is the deployed default, while a directory named
# `fluidserve` is the ablation with prefix accounting turned off. llm-d ran from
# a different driver, hence the `full` variant where the others carry `fullb`;
# all four replay the same trace file and see the same 98,200 arrivals.
# The order is the paper-wide convention: vLLM, PolyServe, Llumnix SLO, llm-d,
# FluidServe, ours last. It sets the legend order.
SERIES = [
    # EXP-77's second pass, which replays the same trace file as EXP-71's. The
    # `b` in `exp77br1` marks the second pass exactly as `exp71br1` does, so
    # these five runs are the second pass of every arm. What this arm is: the
    # PyPI `vllm-router` package's default `cache_aware` policy, a fork of the
    # SGLang model gateway -- NOT vllm-project/production-stack. Its dash
    # pattern is dash-dot-dot, a different rhythm from PolyServe's dash-dot.
    ("vLLM", "results/260810_0741_exp77br1_vllmcache_fullb",
     ARM_COLOR["vllmrouter"], (0, (4, 1, 1, 1, 1, 1))),
    ("PolyServe", "results/260809_0210_exp71br1_polyserve_fullb",
     ARM_COLOR["polyserve"], "-."),
    ("Llumnix SLO", "results/260809_0057_exp71br1_slo_fullb",
     ARM_COLOR["slo"], "--"),
    # A long dash rather than a dotted line. At 1.1 pt over a 110-point series
    # a dot pattern breaks up into something closer to a shaded band than a
    # line, and this arm crosses the others often enough that it has to stay
    # traceable through the crossings. The period is twice Llumnix SLO's, which
    # is what separates the two dashed arms at this width.
    ("llm-d", "results/260808_2007_exp71br1_llmdslo_full",
     ARM_COLOR["llmd"], (0, (6, 1.5))),
    ("FluidServe", "results/260808_1845_exp71br1_fspfx_fullb",
     ARM_COLOR["fluidserve"], "-"),
]
MIN_IN_WINDOW = 30              # same floor as the source timeline figure
MIX_BOUNDARIES = [15, 30, 45]   # the trace steps its mix every 15 minutes
FIG_H = 1.70
MAX_CUTOFF = 0.20               # see the RUN-BOUNDARY note in the docstring

# Two panel sets from one collection pass. `PANELS3` is the figure as it was;
# `PANELS4` puts throughput first, so the reader sees what the engines produced
# before what any of it was worth. Each entry is (key, y label, title stem); the
# (a)/(b)/... letters are attached at draw time so the same stem can be second
# in one figure and third in the other.
PANELS3 = [("gp", "Goodput token (t/s)", r"Token\ goodput"),
           ("adm", "SLO attainment (%)", r"Request\ SLO\ (admitted)"),
           ("off", "SLO attainment (%)", r"Request\ SLO\ (offered)")]
# The two attainment titles are shortened for this one. At four panels each is
# 1.75 in wide and "(d) Request SLO (offered)" is wider than that, so the label,
# which `tight_layout` centres on its axes, ran off the right edge of the
# canvas. Three panels give 2.33 in each and the long form fits.
PANELS4 = [("thru", "Tokens/s", r"Throughput"),
           ("gp", "Goodput token (t/s)", r"Token\ goodput"),
           ("adm", "SLO attainment (%)", r"SLO\ (admitted)"),
           ("off", "SLO attainment (%)", r"SLO\ (offered)")]
# Mathtext so the panel title, which is the second line of the x label, can be
# bold while the x label above it is not; dejavuserif so it is the same
# typeface as the rest of the figure.
MATH_SERIF = {"mathtext.fontset": "dejavuserif"}


def series(run):
    """Per-window attainment on both denominators, and token goodput."""
    r = load_run(run)
    if r is None or r.empty:
        return None
    dur = r["rel"].max()
    col = {k: [] for k in ("x", "adm", "off", "gp", "thru", "cut")}
    for t, g in windows(r, dur):
        if len(g) < MIN_IN_WINDOW:
            continue
        col["x"].append(t)
        col["cut"].append(float(g["cutoff"].mean()))
        col["adm"].append(attain(g, "violate_served"))
        col["off"].append(attain(g, "violate_offered"))
        tok = pd.to_numeric(g.get("output_tokens"), errors="coerce").fillna(0)
        # Throughput counts every token the engines emitted in the window,
        # whether or not the request it belonged to met its rule or even
        # finished. Goodput counts only the tokens of a request that met its
        # rule, judged on the offered column in both attainment panels: a
        # rejected request produced no tokens, so it contributes none. That is
        # a fact about tokens, not a choice of denominator.
        col["thru"].append(tok.sum() / WIN)
        ok = (~g["violate_offered"]) & (~g["cutoff"])
        col["gp"].append(tok[ok].sum() / WIN)
    return {k: np.array(v) for k, v in col.items()} | {"run": r}


def build(data, dur, panels, out, fig_h=None):
    n = len(panels)
    with plt.rc_context({**STYLE, **MATH_SERIF}):
        fig, ax = plt.subplots(1, n, figsize=(TEXT_W, fig_h or FIG_H))

        handles, labels = [], []
        for lab, s, c, ls in data:
            # No markers: about 110 points per line at 90 s steps would draw as
            # a solid band and hide the shape the figure exists to show.
            h = None
            for i, (key, _, _) in enumerate(panels):
                line, = ax[i].plot(s["x"], s[key], color=c, ls=ls, lw=1.1)
                h = h or line
            handles.append(h)
            labels.append(lab)

        att = [i for i, (k, _, _) in enumerate(panels) if k in ("adm", "off")]
        tok = [i for i, (k, _, _) in enumerate(panels) if k in ("gp", "thru")]
        for i in att:
            ax[i].set_ylabel(panels[i][1])
            ax[i].set_ylim(0, 105)
            ax[i].set_yticks([0, 25, 50, 75, 100])
        # The two attainment panels share the axis explicitly rather than by
        # coincidence: the distance between a policy's two curves is the
        # figure's second claim, and it can only be read off on one scale.
        for i in att[1:]:
            ax[i].sharey(ax[att[0]])
        # Throughput and goodput share theirs for the same reason -- the whole
        # point of showing both is the gap between them, and two panels on
        # different scales cannot be subtracted by eye.
        top = max(s[k].max() for _, s, _, _ in data for k in ("thru", "gp")
                  if any(kk == k for kk, _, _ in panels))
        for i in tok:
            ax[i].set_ylabel(panels[i][1])
            ax[i].set_ylim(0, top * 1.08)
            ax[i].yaxis.set_major_formatter(kfmt())

        for i in range(n):
            # The panel title is the SECOND LINE of the x label rather than a
            # text box in axes coordinates, because `tight_layout` reserves room
            # for an axis label and knows nothing about a hand-placed artist.
            title = "$\\mathbf{(%s)\\ %s}$" % ("abcd"[i], panels[i][2])
            ax[i].set_xlabel(f"Time (minutes)\n{title}",
                             labelpad=1.5, linespacing=1.6)
            ax[i].set_xlim(0, dur)
            ax[i].set_xticks(range(0, int(dur) + 1, 15))
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
            for m in MIX_BOUNDARIES:
                ax[i].axvline(m, color="#999999", lw=0.5, ls=":", zorder=0)

        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 0.872), frameon=False,
                   columnspacing=1.4, handlelength=2.0, handletextpad=0.5,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, -0.03, 1, 0.878), w_pad=1.6, pad=0.25)
        save(fig, out)


def main():
    raw = []
    for lab, run, c, ls in SERIES:
        d = os.path.join(ROOT, run)
        if not os.path.isdir(d):
            print(f"missing run directory {run}", file=sys.stderr)
            return 1
        s = series(d)
        if s is None:
            print(f"no rows for {run}", file=sys.stderr)
            return 1
        r, cut = s["run"], s["cut"]
        print(f"{lab:12s} {len(r):6d} arrivals, {len(s['x']):3d} windows, "
              f"whole-hour offered {attain(r, 'violate_offered'):5.1f}%, "
              f"admitted {attain(r, 'violate_served'):5.1f}%, "
              f"rejected {100.0 * r['rejected'].mean():4.1f}%, "
              f"max cutoff share {100.0 * cut.max():5.1f}%")
        raw.append((lab, s, c, ls))

    # One cut point for every arm, so the panels stay directly comparable: the
    # earliest time at which ANY arm's window crosses MAX_CUTOFF. Giving the
    # arms different x extents would itself invite a wrong reading.
    ends = [s["x"][s["cut"] <= MAX_CUTOFF].max()
            if (s["cut"] <= MAX_CUTOFF).any() else s["x"].min()
            for _, s, _, _ in raw]
    dur = min(ends)
    full = max(s["x"].max() for _, s, _, _ in raw)
    if dur < full:
        who = [lab for lab, s, _, _ in raw if (s["cut"] > MAX_CUTOFF).any()]
        print(f"trimmed to {dur:.1f} min (from {full:.1f}); "
              f"{', '.join(who)} exceeded {MAX_CUTOFF:.0%} in-flight-at-end "
              f"beyond that")

    data = []
    for lab, s, c, ls in raw:
        keep = s["x"] <= dur
        data.append((lab, {k: v[keep] for k, v in s.items() if k != "run"},
                     c, ls))
    build(data, dur, PANELS3, os.path.join(HERE, "exp71_hour.pdf"))
    build(data, dur, PANELS4, os.path.join(HERE, "exp71_hour_4panel.pdf"),
          fig_h=1.75)
    return 0


if __name__ == "__main__":
    sys.exit(main())
