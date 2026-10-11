#!/usr/bin/env python3
"""Paper figure: the hour-long dynamic trace as one bar per control plane.

  exp71_hour_bars.pdf       7.0 x 1.75 in, `figure*`, width=\\textwidth
  exp71_hour_bars_1col.pdf  3.335 x 1.75 in, one column, width=\\columnwidth

Three panels, five bars each, the bars identified by the legend rather than by
x tick labels so the same five names are not repeated three times:

  (a) Throughput          every output token the engines emitted over the
                          window, divided by the window's length, whether or
                          not the request it belonged to met its rule
  (b) SLO attainment, admitted   of the requests the policy ACCEPTED, the
                          fraction that finished inside their class rule
  (c) Rejection rate      the fraction of arrivals the policy refused

THE THREE PANELS ARE ONE READING AND NONE OF THEM RANKS THE POLICIES ALONE, and
the caption has to say so, because each of the three has a degenerate optimum:

  (a) is maximised by generating tokens nobody can use. The vLLM router has the
      HIGHEST throughput here, 13,191 tokens/s against FluidServe's 11,274, and
      the lowest useful fraction: its token goodput over the same window is
      4,035 tokens/s, so 69% of what its engines produced belonged to a request
      that missed its rule. FluidServe's goodput is 10,682, which is 95%.
  (b) is maximised by refusing everything -- a policy that rejected 99% of
      arrivals and served the rest perfectly would score 100 here.
  (c) is maximised by refusing nothing, which is what the two arms at 0.0% do,
      and it costs them (b): they score 31.7% and 11.3%.

So (b) is only meaningful beside (c), and both are only meaningful beside the
useful part of (a). Token goodput is the quantity that closes the loop and it is
NOT drawn here -- it is panel (b) of `exp71_hour.pdf` -- so either the caption
carries the four goodput numbers or the two figures appear together.

WHAT IS DRAWN (trimmed window, one run per arm):

                 throughput   goodput   admitted   rejected
  vLLM              13,191      4,035     31.7%       0.0%
  PolyServe         11,841      1,665     11.3%       0.0%
  Llumnix SLO       10,092      5,890     57.6%      27.4%
  llm-d              9,189      8,684     92.7%      32.9%
  FluidServe        11,274     10,682     96.4%      14.7%

WHY THE WINDOW STOPS AT 53.8 MINUTES, AND WHAT IT CHANGES. A request still in
flight when the run ends has an unknown outcome and `attain()` drops it from
both denominators; at the end of a backlogged run those are precisely the slow
requests, so the last minutes flatter the two arms that never reject. The trim
rule and its evidence are in `fig_exp71_hour.py`; this figure imports the same
runs and applies the same cut so the two figures describe the same window.
Over the full 59.8 minutes the numbers would read:

                 throughput   admitted   rejected
  vLLM              12,136      30.6%       0.0%
  PolyServe         11,165      11.2%       0.0%
  Llumnix SLO       10,016      54.8%      31.2%
  llm-d              8,933      92.8%      37.9%
  FluidServe        11,439      96.0%      16.4%

The ORDER of the five arms is the same in all three panels either way. What
moves is the rejection rate, by 1.7 to 5.0 points, because the trace's arrival
rate is higher in the minutes that get cut and rejections rise with load; if a
rejection rate is quoted in prose, say which window it came from.

ONE RUN PER ARM AND IT IS PASS 2, the same runs as `fig_exp71_hour.py`, which
this imports rather than copying. Pass 1 exists for all five and agrees to
0.0-1.8 points of offered attainment, so these bars are reproducible at the
whole-hour level but carry NO error bar and none should be inferred.

⚠ THE VLLM ROUTER COMES FROM A DIFFERENT EXPERIMENT AND DAY (EXP-77) than the
other four (EXP-71); ⚠ llm-d and Llumnix SLO receive the `m1f` workload
configuration and the other three `m1`; ⚠ llm-d received no warmup run. All
three carry over from `fig_exp71_hour.py`, where they are written out in full.

    python3 paper_figures/fig_exp71_bars.py
"""
import importlib.util
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

from paper_style import COL_W, TEXT_W, STYLE, GRID, kfmt, save  # noqa: E402
from exp22_fluidserve import attain  # noqa: E402


def _load_module(path, name="m"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Run selection, colours, the per-window series and the trim threshold all come
# from the timeline figure, so the two files cannot drift apart on which runs
# they draw or where they cut.
TL = _load_module(os.path.join(HERE, "fig_exp71_hour.py"), "exp71tl")

# (key, panel title, y label). The keys are computed in `whole()`.
# (key, (title at text width, title at column width), (y label, y label)).
# TWO SETS OF NAMES, BECAUSE A PANEL TITLE WIDER THAN ITS PANEL IS NOT CLIPPED:
# `tight_layout` centres it on its own axes and it runs over the neighbours,
# which is what "(b) SLO attainment (admitted)" did at 2.33 in per panel.
#
# AT ONE COLUMN THE TITLE IS THE LETTER ALONE. Each panel is then 1.0 in wide,
# where even "(a) Throughput" (0.85 in) touches its neighbour and "(c) Rejected"
# runs off the canvas, so the quantity is named by the y label instead and the
# letter only identifies the panel for the caption. That makes the caption
# responsible for saying what (a), (b) and (c) are in the one-column version.
PANELS = [
    ("thru", (r"Throughput", ""), ("Tokens/s", "Tokens/s")),
    ("adm", (r"SLO\ (admitted)", ""),
     ("SLO attainment (%)", "Attainment (%)")),
    ("rej", (r"Rejection\ rate", ""),
     ("Rejection rate (%)", "Rejected (%)")),
]
MATH_SERIF = {"mathtext.fontset": "dejavuserif"}
FIG_H = 1.75
BAR_W = 0.72


def whole(r, limit_s):
    """The three bar quantities over one arm's retained window.

    Throughput is the window's tokens divided by the window's LENGTH IN
    SECONDS, not the mean of the per-window rates the timeline figure plots.
    The two agree here because the windows are equal length, but the division
    is written out so the quantity does not depend on the windowing.

    Attainment is recomputed from the request rows rather than averaged over
    the timeline's windows: windows hold different numbers of requests, and a
    mean of per-window percentages is not the percentage over the population.
    """
    r = r[r["rel"] <= limit_s]
    span = r["rel"].max() - r["rel"].min()
    tok = pd.to_numeric(r.get("output_tokens"), errors="coerce").fillna(0)
    ok = (~r["violate_offered"]) & (~r["cutoff"])
    return {"thru": tok.sum() / span,
            "gp": tok[ok].sum() / span,
            "adm": attain(r, "violate_served"),
            "off": attain(r, "violate_offered"),
            "rej": 100.0 * r["rejected"].mean(),
            "n": len(r)}


def build(data, out, width):
    """One bar per arm in each panel, arms named by the legend.

    NO X TICK LABELS. Three panels of the same five names is the same
    information written three times, and at one column width the names do not
    fit under 0.85 in of axes anyway. The cost is that the bars carry no
    ordering cue of their own, which is why the arm order is fixed and the
    legend is drawn in that same order.
    """
    n = len(PANELS)
    x = np.arange(len(data))
    narrow = 1 if width < 5 else 0
    with plt.rc_context({**STYLE, **MATH_SERIF}):
        fig, ax = plt.subplots(1, n, figsize=(width, FIG_H))
        handles, labels = [], []

        for j, (lab, vals, col) in enumerate(data):
            for i, (key, _, _) in enumerate(PANELS):
                b = ax[i].bar(x[j], vals[key], BAR_W, color=col,
                              edgecolor="white", lw=0.4, zorder=3)
                if i == 0:
                    handles.append(b)
                    labels.append(lab)

        for i, (key, titles, ylabs) in enumerate(PANELS):
            stem = titles[narrow]
            ax[i].set_xlabel(
                "$\\mathbf{(%s)%s}$" % ("abc"[i], f"\\ {stem}" if stem else ""),
                labelpad=2.0)
            ax[i].set_ylabel(ylabs[narrow], labelpad=1.5)
            ax[i].set_xlim(-0.7, len(data) - 0.3)
            ax[i].set_xticks([])
            ax[i].grid(axis="y", **GRID)
            ax[i].set_axisbelow(True)
            if key == "adm":
                ax[i].set_ylim(0, 105)
                ax[i].set_yticks([0, 25, 50, 75, 100])
            else:
                top = max(v[key] for _, v, _ in data)
                # Headroom on the rejection panel for the "0" labels below,
                # which sit above a bar of zero height.
                ax[i].set_ylim(0, top * (1.18 if key == "rej" else 1.06))
                if key == "thru":
                    ax[i].yaxis.set_major_formatter(kfmt())

            # A bar of height zero draws nothing, and nothing on a chart reads
            # as "not measured" rather than as "measured, and it was zero".
            # The two arms with no admission control reject 0.0%, which is the
            # panel's whole point for them, so the value is written in.
            if key == "rej":
                for j, (_, vals, _) in enumerate(data):
                    if vals[key] < 0.05:
                        ax[i].text(x[j], top * 0.02, "0", ha="center",
                                   va="bottom", fontsize=6.5)

        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 1 - 0.235 / FIG_H), frameon=False,
                   fontsize=6.5 if width < 5 else 8,
                   columnspacing=0.8 if width < 5 else 1.6,
                   handlelength=1.1 if width < 5 else 1.6,
                   handletextpad=0.35, borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 1 - 0.215 / FIG_H),
                         w_pad=1.4, pad=0.3)
        save(fig, out)


def main():
    raw = []
    for lab, run, col, _ in TL.SERIES:
        d = os.path.join(ROOT, run)
        s = TL.series(d)
        if s is None:
            print(f"no rows for {run}", file=sys.stderr)
            return 1
        raw.append((lab, s, col))

    # The same cut rule as the timeline figure: the earliest time at which ANY
    # arm's window crosses MAX_CUTOFF, applied to all five so the bars describe
    # one window rather than five.
    ends = [s["x"][s["cut"] <= TL.MAX_CUTOFF].max()
            if (s["cut"] <= TL.MAX_CUTOFF).any() else s["x"].min()
            for _, s, _ in raw]
    dur = min(ends)
    full = max(s["x"].max() for _, s, _ in raw)
    print(f"window {dur:.1f} of {full:.1f} min")

    data = []
    print(f"{'arm':12s} {'thru':>8s} {'goodput':>8s} {'adm':>6s} "
          f"{'off':>6s} {'rej':>6s} {'n':>8s}")
    for lab, s, col in raw:
        v = whole(s["run"], dur * 60.0)
        print(f"{lab:12s} {v['thru']:8,.0f} {v['gp']:8,.0f} {v['adm']:6.1f} "
              f"{v['off']:6.1f} {v['rej']:6.1f} {v['n']:8,d}")
        data.append((lab, v, col))

    build(data, os.path.join(HERE, "exp71_hour_bars.pdf"), TEXT_W)
    build(data, os.path.join(HERE, "exp71_hour_bars_1col.pdf"), COL_W)
    return 0


if __name__ == "__main__":
    sys.exit(main())
