#!/usr/bin/env python3
"""Motivation figure 6: the two conditions on one quantity.

WHAT IT ARGUES. "Fully separating the classes is bad, fully mixing them is bad,
and the good region is in between" is vague until the axis is named and the two
ends are given reasons. The axis is the effective number of instances a class
runs on, 1/sum(share^2) over a 60-second window, median over windows: 4.0 for an
even spread over four instances, 1.0 for one instance, 2.0 for two. On that axis
there are two conditions, and each established method violates one.

  BELOW  a class must run on at least as many instances as its share of the work
         needs. Panel A: the static partition gives chat, whose output tokens
         amount to 2.50 instances' worth, an effective 1.02.

  ABOVE  the tightest-budget class must not be resident on every instance, or
         every instance is held to its budget. Panel B: under every fully mixed
         configuration an instance is free of chat about 1% of the time.

  AND    separation is not itself the objective. Panel B also shows the static
         partition satisfying the upper condition far better than we do and
         still scoring less than half. Panel C puts the score against the axis
         directly and shows the turn.

WHAT IT DOES NOT SAY. Panel C is not a curve. The three fully mixed
configurations land on top of each other on the axis, at an effective 3.9 to 4.0
instances, and score 87, 52 and 32; so the axis is where the two conditions live
and not a predictor of the score by itself. Panel C mixes systems. Three of its clusters are different
control planes that differ in more than this axis -- demand estimation, spill
between tiers, admission rule -- so the shape of the curve across clusters is
not a causal statement about the axis. The only within-system comparison is
FluidServe against FluidServe with the class preference off (and, once EXP-58
and EXP-59 land, the weight sweep and the pinned arms, which are drawn in the
same colour family for that reason).

  python3 motivation_fig6.py <measures.csv> [--out DIR]

The CSV is what separation_measures.py writes; it carries the score in the same
row as the measures, so no join is needed.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, CLASSES  # noqa: E402

# Arm colours are fixed across the paper. The FluidServe family shares a hue so
# that a reader can see at a glance which points are one system.
FAMILY = [
    ("polyserve", "PolyServe\n(static partition)", "#d62728"),
    ("fspin", "FluidServe\nclasses pinned", "#ff7f0e"),
    ("fluidserve", "FluidServe", "#1f77b4"),
    ("fsw", "FluidServe\npreference strength", "#7fb1d6"),
    ("fsnoaff", "FluidServe\npreference off", "#9ecae1"),
    ("slo", "Llumnix SLO", "#2ca02c"),
    ("loadbalance", "Llumnix\nload balance", "#7f7f7f"),
]


def family_of(label):
    """First matching key wins, so `fsnoaff` is not caught by `fluidserve`."""
    lab = label.split(" ")[0]
    for key, name, colour in FAMILY:
        if lab.startswith(key):
            return key, name, colour
    return lab, lab, "#333333"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--out", default="results/aggregate_analysis/motivation")
    ap.add_argument("--rate", default="45 req/s",
                    help="printed in the title; the CSV is not filtered by it")
    # A fixed output name overwrote the static figure with the hour one the
    # first time this was run on a second CSV. Two inputs, two names.
    ap.add_argument("--name", default="motivation_two_conditions.png")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    df = pd.read_csv(a.csv)
    df["family"], df["fname"], df["colour"] = zip(*[family_of(l) for l in df["label"]])
    order = [k for k, _, _ in FAMILY if (df["family"] == k).any()]
    if not order:
        sys.exit("no known arms in the CSV")
    print(f"arms drawn: {order}  ({len(df)} runs)")

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(11.0, 3.2))

        # ---- A: instances per class, against what the work would imply -------
        w, gap = 0.26, 0.02
        for j, cls in enumerate(CLASSES):
            xs, ys, ds, cs = [], [], [], []
            for i, key in enumerate(order):
                g = df[df["family"] == key]
                xs.append(i + (j - 1) * (w + gap))
                ys.append(g[f"effinst_{cls}"].mean())
                ds.append(g[f"demand_{cls}"].mean())
                cs.append(g["colour"].iloc[0])
            ax[0].bar(xs, ys, width=w, color=cs,
                      alpha=[1.0, 0.7, 0.45][j], edgecolor="white", linewidth=0.4,
                      label=cls if len(order) else None)
            # The reference is drawn as a tick on the bar rather than as a line
            # across the panel, because it differs per arm: each policy completes
            # a different set of requests and so produces a different token mix.
            for x, d in zip(xs, ds):
                ax[0].plot([x - w / 2, x + w / 2], [d, d], color="black", lw=1.1)
        ax[0].set_xticks(range(len(order)))
        ax[0].set_xticklabels([dict((k, n) for k, n, _ in FAMILY)[k] for k in order],
                              fontsize=6.5)
        ax[0].set_ylabel("effective instances (of 4)")
        ax[0].set_ylim(0, 4.3)
        ax[0].axhline(4.0, color="#999999", lw=0.6, ls=":")
        ax[0].set_title("A. how many instances each class runs on\n"
                        "bars: chat, deepresearch, swe (left to right). "
                        "black tick: what its output tokens imply", fontsize=7)

        # ---- B: the upper condition, against the score -----------------------
        for key in order:
            g = df[df["family"] == key]
            ax[1].scatter(g["nochat_pct"], g["offered"], s=26,
                          color=g["colour"].iloc[0], edgecolor="white", linewidth=0.5,
                          label=g["fname"].iloc[0].replace("\n", " "), zorder=3)
        ax[1].set_xlabel("instance-time with no resident chat request (%)")
        ax[1].set_ylabel("SLO attainment (%), offered")
        ax[1].set_title("B. separation is not the objective:\nthe static partition has the "
                        "most of it and the lowest score", fontsize=7)

        # ---- C: the score against the axis -----------------------------------
        for key in order:
            g = df[df["family"] == key]
            ax[2].scatter(g["effinst_chat"], g["offered"], s=26,
                          color=g["colour"].iloc[0], edgecolor="white", linewidth=0.5,
                          zorder=3)
        ax[2].set_xlabel("effective instances holding chat (of 4)")
        ax[2].set_ylabel("SLO attainment (%), offered")
        ax[2].set_xlim(0.5, 4.2)
        # The three fully mixed configurations sit on top of each other on this
        # axis and score 87, 52 and 32. Saying so in the panel is the point: the
        # axis is where the two conditions live, it is not a predictor of the
        # score on its own, and a reader who takes the shape as a curve through
        # the clusters has been misled by the figure.
        mixed = df[df["effinst_chat"] > 3.5]
        spread = (f"{mixed['offered'].max():.0f} to {mixed['offered'].min():.0f}"
                  if len(mixed) else "n/a")
        ax[2].set_title("C. both ends are worse than the middle -- but the axis\n"
                        f"does not decide the score: at 4.0 it spans {spread}",
                        fontsize=7)

        for k in (0, 1, 2):
            ax[k].grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax[1].legend(fontsize=6, loc="lower left", frameon=True, framealpha=0.85,
                     edgecolor="none")

        n = len(df)
        fig.suptitle(
            f"Two conditions on one quantity, static {a.rate}, {n} runs. "
            f"Below: a class needs as many instances as its work implies. "
            f"Above: the tightest class must leave some instance free.",
            fontsize=8, y=1.06)
        fig.tight_layout()
        p = os.path.join(a.out, a.name)
        fig.savefig(p, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {p}")

    # The numbers behind the panels, so the caption can be checked against them.
    keep = ["fname", "effinst_chat", "demand_chat", "nochat_pct", "offered"]
    print("\nper arm (mean over runs):")
    print(df.groupby("fname")[keep[1:]].mean().to_string(float_format="%.2f"))


if __name__ == "__main__":
    main()
