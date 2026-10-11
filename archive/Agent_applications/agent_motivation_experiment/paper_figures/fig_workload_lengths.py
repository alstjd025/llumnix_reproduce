#!/usr/bin/env python3
"""Paper figure: how long the requests of this workload are.

  workload_lengths.pdf     3.335 x 1.95 in, `figure`, width=\\columnwidth

Two panels, both in OUTPUT tokens: (left) every request of the mixture pooled
into one distribution function, (right) the same requests split by class. The y
axis is the share of requests at or below that length, so a median is where a
curve crosses 50 and a p90 is where it crosses 90. The left panel is
what an engine sees arriving -- the classes in the proportions they actually
come in -- and the right says which class each part of that shape comes from.
Reading them together is the point: the aggregate is not one mode with a tail,
it is three modes that the mixture happens to overlay.

⚠ THE LEFT PANEL IS WEIGHTED BY THE CLASS SHARES AND THE RIGHT IS NOT. Pooling
the requests weights each class by how often it arrives (chat 76.9%), while each
curve on the right is the distribution within its own class. The left curve is
the share-weighted sum of the three on the right, which is a statement about the
CURVES and not something the eye adds up from the picture.

POPULATION. EXP-108 at 10 req/s, the four arms and both repeats pooled,
completed requests only (not rejected, errored, or cut off at run end). 10 req/s
is the lowest rate of that sweep and every arm rejects nothing there, so the
lengths are the workload's rather than what saturation left behind. This is the
workload the paper's experiments run on; the figure was drawn from EXP-53 until
2026-09-09, which is a run of the workload as it was BEFORE the 2026-08-08
correction to per-worker dataset slicing, and its lengths must not be quoted
beside anything measured after that date.

DENSITY IN LOG SPACE, AND WHAT THAT MEANS FOR THE Y AXIS. Input spans 2 to
15,334 tokens and output 2 to 7,855, so the kernel density is estimated over
log10(tokens) and drawn against a log x axis. The curve is therefore a density
per decade, not per token: the AREA under a curve between two x values is the
share of that class's requests in that range, but the HEIGHT cannot be compared
against a linear-axis density. The y axis carries no numbers for that reason —
heights are comparable between the curves on this figure and meaningless off it.

EACH CLASS IS NORMALISED TO ITS OWN AREA, not to its share of the traffic. The
three curves therefore answer "given a request of this class, how long is it",
which is the question the table answers too. They do NOT show that chat is 77%
of the requests; the class shares are in the legend.

    python3 paper_figures/fig_workload_lengths.py
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde
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
FIG_H = 1.95
# One range for both panels, so "this class outputs about a tenth of what
# it takes in" is readable by moving the eye across rather than by
# reading two differently-scaled axes. Covers both maxima (input 15,334,
# output 7,855).
XLIM = (1.5, 2.0e4)
# The pooled curve is not a class and is drawn in grey so it cannot be read as
# a fourth one.
ALL_C = "#444444"


def population():
    frames = []
    for d in sorted(glob.glob(os.path.join(ROOT, RUNS))):
        if "PRERUN" in d:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        frames.append(r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])])
    if not frames:
        return None
    a = pd.concat(frames)
    a["inp"] = pd.to_numeric(a["input_tokens"], errors="coerce")
    a["out"] = pd.to_numeric(a["output_tokens"], errors="coerce")
    a = a.dropna(subset=["inp", "out"])
    return a[(a["inp"] > 0) & (a["out"] > 0)]


def density(v, lo, hi):
    """Kernel density over log10(v), evaluated on a grid in the same space.

    Estimated in log space rather than by transforming a linear-space estimate:
    the lengths run from 2 to about 8,000 tokens, and a Gaussian kernel wide
    enough to smooth the top end erases the bottom one, while a kernel narrow
    enough for the bottom leaves the top as a comb of spikes. The bandwidth is
    scipy's Scott rule, n^(-1/5) times the standard deviation of log10(tokens).

    ⚠ THE HEIGHT IS THEREFORE A DENSITY PER DECADE, not per token: the estimate
    integrates to 1 against d(log10 x), so the AREA under the curve between two
    x values -- read on the log axis as drawn -- is the share of requests in
    that range, and the height cannot be compared with a linear-axis density.
    That is why the y axis carries no numbers.
    """
    x = np.log10(np.asarray(v, dtype=float))
    grid = np.linspace(np.log10(lo), np.log10(hi), 512)
    kde = gaussian_kde(x)
    print(f"      n={len(x)}  bandwidth={kde.factor * x.std(ddof=1):.4f} "
          f"decades (Scott)")
    return 10.0 ** grid, kde(grid)


def ecdf(v):
    """The empirical distribution: sorted values and the share at or below each.

    ⚠ THIS REPLACED A KERNEL DENSITY ON 2026-09-09. The density had to be
    estimated over log10(tokens) -- the lengths span 2 to about 8,000 -- so its
    height was a density PER DECADE, a quantity no reader can take off an axis,
    and the y axis carried no numbers at all. The distribution function needs no
    kernel, no bandwidth and no log transform: the y value is the share of
    requests at or below that length, and it can be read directly. What is lost
    is the shape of the modes, which is why the three classes are still drawn
    apart in panel (b).
    """
    x = np.sort(np.asarray(v, dtype=float))
    y = 100.0 * np.arange(1, len(x) + 1) / len(x)
    return x, y


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdf", action="store_true",
                    help="empirical distribution functions instead of the "
                         "kernel densities")
    cdf = ap.parse_args().cdf
    a = population()
    if a is None:
        print(f"no runs matched {RUNS}", file=sys.stderr)
        return 1
    share = a["class"].value_counts(normalize=True) * 100

    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(1, 2, figsize=(COL_W, FIG_H))
        handles, labels = [], []

        lo, hi = XLIM
        # Left: the mixture as one population. Drawn in grey because it is not a
        # class and must not be read as a fourth one.
        x, y = density(a["out"], lo, hi) if not cdf else ecdf(a["out"])
        axes[0].plot(x, y, color=ALL_C, lw=1.1)
        if not cdf:
            axes[0].fill_between(x, y, color=ALL_C, alpha=0.13, lw=0)
        # The pooled curve has NO legend entry: it is the only curve in its
        # panel and the panel's caption names it, so a key would spend a slot
        # on something nothing else can be confused with.
        for c in CLASSES:
            v = a.loc[a["class"] == c, "out"]
            if len(v) < 10:
                continue
            x, y = density(v, lo, hi) if not cdf else ecdf(v)
            axes[1].plot(x, y, color=CLASS_COLORS[c], lw=1.1)
            if not cdf:
                # A light fill separates the modes where two curves cross; at
                # 0.13 it does not hide the curve underneath.
                axes[1].fill_between(x, y, color=CLASS_COLORS[c], alpha=0.13,
                                     lw=0)
            handles.append(plt.Line2D([], [], color=CLASS_COLORS[c], lw=1.4))
            # No share in the label: a percentage beside a curve is read as a
            # property of that curve, and the class shares are a property of the
            # mixture, which is what the left panel draws.
            labels.append(LABEL[c])
        for ax in axes:
            ax.set_xscale("log")
            ax.set_xlim(lo, hi)
            if cdf:
                ax.set_ylim(0, 100)
                ax.set_yticks([0, 25, 50, 75, 100])
            else:
                ax.set_ylim(0, None)
                # A density per decade is not a number to read off an axis; the
                # shape and the area carry the information.
                ax.set_yticks([])
            ax.grid(axis="both" if cdf else "x", **GRID)
            ax.set_axisbelow(True)

        axes[0].set_xlabel("Output tokens\n(a) All requests", linespacing=1.5)
        axes[1].set_xlabel("Output tokens\n(b) By class", linespacing=1.5)
        axes[0].set_ylabel("Requests $\\leq$ x (%)" if cdf else "Density")
        if cdf:
            axes[1].set_yticklabels([])

        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 0.845), frameon=False,
                   columnspacing=0.8, handlelength=1.2, handletextpad=0.4,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 0.835), w_pad=1.0, pad=0.35)
        save(fig, os.path.join(HERE, "workload_lengths"
                               + ("_cdf" if cdf else "") + ".pdf"))

    print(f"{len(a)} requests: " + ", ".join(
        f"{LABEL[c]} {len(a[a['class'] == c])} ({share[c]:.1f}%)"
        for c in CLASSES))
    print("output tokens, median / p90: all " +
          f"{a['out'].median():.0f} / {a['out'].quantile(0.9):.0f}; " +
          ", ".join(f"{LABEL[c]} {a.loc[a['class'] == c, 'out'].median():.0f}"
                    f" / {a.loc[a['class'] == c, 'out'].quantile(0.9):.0f}"
                    for c in CLASSES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
