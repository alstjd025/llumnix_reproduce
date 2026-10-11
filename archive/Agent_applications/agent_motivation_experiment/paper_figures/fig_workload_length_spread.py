#!/usr/bin/env python3
"""Paper figure: how much of the length spread the request's class accounts for.

  workload_length_spread.pdf   3.335 x 1.95 in, `figure`, width=\\columnwidth
  workload_length_spread.csv   exactly the values drawn

  (a) the distribution of output length, pooled over the mixture and then split
      by class, as distribution functions so a median is where a curve crosses
      50 and a p90 where it crosses 90
  (b) the same four populations as their 10th-to-90th percentile span, with the
      median marked and the ratio of the two ends printed beside each bar

⚠ WHAT THIS FIGURE SHOWS IS NOT "SPLITTING BY CLASS MAKES THE LENGTHS
PREDICTABLE". It shows that it does so for TWO of the three classes and not for
the third, and the third is 76.9% of the requests:

    pooled          p10 82   p50 458   p90 983    p90/p10 = 12.0
    chat            p10 61   p50 384   p90 757    p90/p10 = 12.4
    deep research   p10 700  p50 969   p90 1241   p90/p10 =  1.8
    agent           p10 301  p50 489   p90 655    p90/p10 =  2.2

Conditioning on the class removes 24.4% of the variance of the length and 15.3%
of the variance of its logarithm; predicting each request with its own class's
median rather than the global median takes the median relative error from 43.8%
to 34.0%. Both are real and neither is large, and the reason is in the table:
chat is as wide as the mixture. The two tails of the pooled distribution have
different owners -- 98.9% of the requests below the pooled 10th percentile are
chat, and 70.5% of those above its 90th are deep research -- so the pooled
spread is mostly two populations side by side rather than one wide one.

WHY THAT IS THE ARGUMENT AND NOT A PROBLEM. The policy does not use a point
estimate of a request's length; it uses the class's distribution, through the
expected remaining tokens and the probability that a request completes within a
horizon. EXP-64 gave it each request's exact length instead and the difference
was within 3 points at every arrival rate and did not keep a sign (-0.2 points
at 35 req/s), and EXP-90 showed a profile built from 12 seconds of traffic, and
a profile 3.4x out of date, both cost less than the repeat-to-repeat spread. A
figure that claimed class conditioning makes lengths predictable would be
claiming something this workload does not support and the design does not need.

POPULATION. EXP-108 at 10 req/s, the four arms and both repeats pooled,
completed requests only (not rejected, errored, or cut off at run end), 34,598
requests. 10 req/s is the lowest rate of that sweep and no arm rejects there, so
the lengths are the workload's rather than what saturation left behind.

    python3 paper_figures/fig_workload_length_spread.py
"""
import glob
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

from paper_style import COL_W, STYLE, GRID, save  # noqa: E402
from exp22_fluidserve import load_run, CLASSES, CLASS_COLORS  # noqa: E402

RUNS = "results/*exp108r?_*_rpm_600"
LABEL = {"chat": "Chat", "deepresearch": "Deep Research", "swe": "Agents"}
ALL_C = "#444444"
XLIM = (30.0, 4.0e3)
FIG_H = 1.95


def population():
    frames = []
    for d in sorted(glob.glob(os.path.join(ROOT, RUNS))):
        if "PRERUN" in d:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        frames.append(r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])])
    a = pd.concat(frames)
    a["out"] = pd.to_numeric(a["output_tokens"], errors="coerce")
    a = a.dropna(subset=["out"])
    return a[a["out"] > 0]


def ecdf(v):
    x = np.sort(np.asarray(v, dtype=float))
    return x, 100.0 * np.arange(1, len(x) + 1) / len(x)


def main():
    a = population()
    groups = [("All requests", ALL_C, a["out"].to_numpy())]
    groups += [(LABEL[c], CLASS_COLORS[c], a.loc[a["class"] == c, "out"].to_numpy())
               for c in CLASSES]

    rows = []
    for name, _c, v in groups:
        p10, p50, p90 = np.percentile(v, [10, 50, 90])
        rows.append(dict(group=name, n=len(v), share_pct=100.0 * len(v) / len(a),
                         p10=p10, p50=p50, p90=p90, p90_over_p10=p90 / p10,
                         mean=v.mean(), cv=v.std(ddof=1) / v.mean()))
    tab = pd.DataFrame(rows)

    with plt.rc_context({**STYLE, "xtick.labelsize": 6.5,
                         "ytick.labelsize": 6.5, "axes.labelsize": 7}):
        fig, ax = plt.subplots(1, 2, figsize=(COL_W, FIG_H))
        handles, labels = [], []
        for name, c, v in groups:
            x, y = ecdf(v)
            ax[0].plot(x, y, color=c, lw=1.0)
            handles.append(plt.Line2D([], [], color=c, lw=1.3))
            labels.append(name)
        ax[0].set_xscale("log")
        ax[0].set_xlim(*XLIM)
        ax[0].set_ylim(0, 100)
        ax[0].set_yticks([0, 25, 50, 75, 100])
        ax[0].set_ylabel(r"Requests $\leq$ x (%)", labelpad=1.5)
        ax[0].set_xlabel("Output tokens\n(a) Distribution", labelpad=1.5,
                         linespacing=1.5)

        # (b) One bar per population: the middle 80% of it, with the median
        # marked. The number beside the bar is p90/p10 -- the factor between the
        # two ends -- which is the quantity the panel exists to compare.
        ys = np.arange(len(groups))[::-1]
        for yy, (name, c, v) in zip(ys, groups):
            p10, p50, p90 = np.percentile(v, [10, 50, 90])
            ax[1].plot([p10, p90], [yy, yy], color=c, lw=3.2,
                       solid_capstyle="butt", alpha=0.85)
            ax[1].plot([p50], [yy], marker="|", color="#ffffff", ms=5, mew=1.0)
            ax[1].text(p90 * 1.25, yy, f"{p90 / p10:.1f}$\\times$",
                       va="center", ha="left", fontsize=6.0, color=c)
        ax[1].set_xscale("log")
        ax[1].set_xlim(*XLIM)
        ax[1].set_ylim(-0.8, len(groups) - 0.2)
        ax[1].set_yticks(ys)
        ax[1].set_yticklabels([g[0] for g in groups], fontsize=6.0)
        ax[1].set_xlabel("Output tokens\n(b) p10 - p90 span", labelpad=1.5,
                         linespacing=1.5)

        for a_ in ax:
            a_.grid(axis="x", **GRID)
            a_.set_axisbelow(True)

        fig.legend(handles, labels, loc="lower center", ncol=4,
                   bbox_to_anchor=(0.5, 0.86), frameon=False, fontsize=6.0,
                   columnspacing=0.8, handlelength=1.2, handletextpad=0.35,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 0.855), w_pad=1.0, pad=0.35)
        save(fig, os.path.join(HERE, "workload_length_spread.pdf"))

    # The two numbers the caption quotes, computed here rather than by hand.
    x = a["out"].to_numpy()
    lin = 1 - a.groupby("class")["out"].transform(
        lambda s: s.var(ddof=0)).mean() / x.var(ddof=0)
    lg = np.log10(x)
    within = pd.Series(lg).groupby(a["class"].to_numpy()).transform(
        lambda s: s.var(ddof=0)).mean()
    err_all = np.abs(x - np.median(x)) / x
    err_cls = np.abs(x - a.groupby("class")["out"].transform("median")) / x
    print(tab.round(2).to_string(index=False))
    print(f"class explains {100 * lin:.1f}% of the variance of the length and "
          f"{100 * (1 - within / lg.var(ddof=0)):.1f}% of that of log10(length)")
    print(f"median |relative error| of predicting with the median: "
          f"pooled {100 * np.median(err_all):.1f}%, per class "
          f"{100 * np.median(err_cls):.1f}%")
    out = os.path.join(HERE, "workload_length_spread.csv")
    tab.to_csv(out, index=False, float_format="%.3f")
    print(f"wrote {out}  ({len(tab)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
