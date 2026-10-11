#!/usr/bin/env python3
"""Paper figure: the load variation in the source Azure trace.

  azure_trace_shape.pdf     3.335 x 1.60 in, `figure`, width=\\columnwidth

Arrival rate over the four-day Azure window our dynamic trace is built from,
normalised to its own peak. The point of the figure is the vertical extent: the
trough is 17% of the peak, so a fleet provisioned for the peak is running at
under a fifth of its offered load for part of the day, and one provisioned for
the mean is over-subscribed for another part.

NORMALISING BY THE PEAK IS WHAT MAKES THIS FIGURE HONEST, and it is why the
source series is drawn rather than the trace we replay.

Our replayed trace is NOT a rescaled copy of this. `build_dynamic_mix_trace.py`
applies a **quantile (rank) transform** onto a chosen band, 10 to 50 req/s, not
a linear scale. A rank transform keeps the temporal ORDER and autocorrelation of
the source — when Azure is busy we are busy — but replaces the distribution of
rates with a uniform one over the band. Two consequences:

  1. The peak-to-trough ratio of the REPLAYED trace is 50/10 = 5.0x BY
     CONSTRUCTION. It is a parameter we picked so the sweep covers the band our
     fleet resolves. Plotting it as evidence that real load varies would be
     circular, which is why this figure plots the source.
  2. Time spent at each rate is uniform in the replay and is not uniform here.
     Do not read this figure's horizontal extents as the replay's.

The generator's own docstring states the same thing: "say Azure-shaped,
rescaled to our cluster, not an Azure trace".

SOURCE WINDOW. `traces/azure/plots/_minute_{conv,code}2024.csv`, the per-minute
request counts of the Azure LLM Inference 2024 conversation and code traces,
summed index-wise, then the window `day_window=[0,4]` starting at
`start_hour=6` — minutes 360 to 6120, 5,760 minutes, four days. Those are the
parameters recorded in `traces/dynamic/canonical/dyn60_azure4d.plan.json`, so
this figure and the trace read the same window.

The two source traces cover DIFFERENT CALENDAR WEEKS and are summed by index,
which aligns hour-of-day phase but not calendar date. That is the generator's
documented caveat, not something introduced here; it does not affect the
diurnal shape, which is what this figure is about.

    python3 paper_figures/fig_azure_trace_shape.py
"""
import json
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

from paper_style import COL_W, STYLE, GRID, save  # noqa: E402

PLAN = "traces/dynamic/canonical/dyn60_azure4d.plan.json"
CONV = "traces/azure/plots/_minute_conv2024.csv"
CODE = "traces/azure/plots/_minute_code2024.csv"
FIG_H = 1.60
COLOR = "#1f77b4"


def source_window():
    """The per-minute counts of the window the trace is built from.

    The window is read from the trace's own plan file rather than hardcoded, so
    that regenerating the trace with a different window cannot leave this figure
    describing the old one.
    """
    plan = json.load(open(os.path.join(ROOT, PLAN)))
    src = plan["source"]
    day0, day1 = src["day_window"]
    start_hour = src["start_hour"]

    conv = pd.read_csv(os.path.join(ROOT, CONV))["count"].to_numpy(float)
    code = pd.read_csv(os.path.join(ROOT, CODE))["count"].to_numpy(float)
    n = min(len(conv), len(code))
    total = conv[:n] + code[:n]

    lo = day0 * 1440 + start_hour * 60
    hi = lo + (day1 - day0) * 1440
    if hi > len(total):
        sys.exit(f"window {lo}..{hi} exceeds {len(total)} minutes of source")
    return total[lo:hi], plan


def main():
    w, plan = source_window()
    peak = w.max()
    y = w / peak
    hours = np.arange(len(w)) / 60.0

    p5, p95 = np.percentile(w, 5), np.percentile(w, 95)
    print(f"{len(w)} minutes ({len(w)/1440:.1f} days). requests/min: "
          f"min {w.min():.0f}, p5 {p5:.0f}, median {np.median(w):.0f}, "
          f"p95 {p95:.0f}, max {peak:.0f}")
    print(f"  max/min {peak/w.min():.2f}x   p95/p5 {p95/p5:.2f}x   "
          f"trough {w.min()/peak:.3f} of peak")
    print(f"  replayed band {plan['rate']['rate_min']}-{plan['rate']['rate_max']}"
          f" req/s = {plan['rate']['rate_max']/plan['rate']['rate_min']:.1f}x, "
          f"by construction ({plan['rate']['mapping']})")

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(COL_W, FIG_H))

        ax.plot(hours, y, color=COLOR, lw=0.5)
        ax.fill_between(hours, y, color=COLOR, alpha=0.15, lw=0)

        # The two levels the claim rests on. Drawn as rules across the panel
        # rather than annotated per point, because the claim is about the band
        # the load moves through, not about when the extremes happen to occur.
        ax.axhline(1.0, color="#555555", lw=0.6, ls="--")
        ax.axhline(w.min() / peak, color="#555555", lw=0.6, ls="--")
        ax.annotate(f"peak / trough = {peak / w.min():.1f}$\\times$",
                    (0.5, 0.60), xycoords="axes fraction", ha="center",
                    fontsize=7, color="#333333")

        ax.set_xlabel("Time (hours)")
        ax.set_ylabel("Arrival rate\n(fraction of peak)")
        ax.set_xlim(0, hours[-1])
        ax.set_xticks(np.arange(0, hours[-1] + 1, 24))
        ax.set_ylim(0, 1.12)
        ax.set_yticks([0, 0.5, 1.0])
        ax.grid(axis="both", **GRID)
        ax.set_axisbelow(True)

        fig.tight_layout(pad=0.35)
        save(fig, os.path.join(HERE, "azure_trace_shape.pdf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
