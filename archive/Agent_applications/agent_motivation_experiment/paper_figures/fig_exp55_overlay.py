#!/usr/bin/env python3
"""Paper figure: EXP-55 — the three classes on one rate axis.

  exp55_overlay.pdf        7.0   x 1.90 in, `figure*`, width=\\textwidth
  exp55_overlay_1col.pdf   3.335 x 1.55 in, `figure`,  width=\\columnwidth

Two widths of the same figure. The one-column version drops to three x ticks
(10, 30, 90 -- still evenly spaced on the log axis, each a tripling) because
four will not fit in 0.85 in of panel.

Three panels side by side on ONE shared rate axis, one curve per class: SLO
attainment, KV occupancy, engine queue depth.

THE RATE AXIS IS LOG, AND THAT IS A CHOICE ABOUT WHICH COMPARISON THE FIGURE
MAKES. A log axis shows RATIOS faithfully and absolute differences unfaithfully:
10 to 20 req/s occupies the same width as 40 to 80. A linear axis does the
reverse. The ratio is the reading that belongs here — the knees are 16, 24 and
65 req/s, so deep research saturates at about a quarter of the rate chat does,
and a request of one class is nothing like a request of another in what it costs
to serve, which makes "four times the rate" the meaningful statement and "49 more
requests per second" not one.

It also keeps the three sweeps from compressing into the left of the axis: they
span 8 to 90 req/s, more than a decade, and drawn linearly deep research and the
agent class fall into the left third with their knees on top of one another. The
absolute knee rates are printed in the caption so nothing is lost. This is the same data as the 3x3
grid in `fig_exp55_class_knees.py`, laid out so the classes can be compared
directly instead of column by column.

WHY THIS SHAPE. The claim is that the three classes saturate at different rates
and in different engine states. On one axis that claim is the horizontal offset
between three curves, read in one eye movement; in a 3x3 grid it is a comparison
across columns whose x axes are three different ranges, which the reader has to
re-read at every panel. The overlap that only this layout gives is worth the
most: between 12 and 30 req/s all three classes are measured, and at the same
rate deep research is already at 100% KV while the agent class is near 20%.

THE CURVES DO NOT SPAN THE WHOLE AXIS, BECAUSE THE SWEEP DID NOT. Each class was
swept over a range chosen around its own knee — chat 40-90, agent 12-48, deep
research 8-30 req/s — so each curve stops where its measurements stop. Nothing
is extrapolated: a gap on this figure is a rate that was not run, and the
caption has to say so rather than let a reader read the end of a line as the
end of the behaviour.

Filling the axis would take about thirteen more conditions (chat at 8-30, the
other two above their present tops). Two of them are not free: this sweep uses
the load-balance policy with no admission control, so pushing a class far past
its knee reproduces the condition in which EXP-54's load-balance arm exhausted
the load generator's ephemeral ports (62,114 `Errno 99`, none in any other arm)
and its last twenty minutes measured the client rather than the policy. Past
about three times the knee, the error counts have to be checked before the
condition is believed.

The vertical rules are each class's knee, in that class's own colour, defined
as the last measured rate at which attainment is still 85% or better: chat 65,
agent 24, deep research 16 req/s.

KV and queue are the MEAN over the same analysis window `load_run` scores
attainment on. The median collapses chat's queue to a flat zero and the p90
cannot reach zero for chat at all, because its sweep starts at 40 req/s; the
mean is the only one of the three that is continuous and still zero at low load,
so it is the only one under which the three classes share a baseline. One run per condition, 24 conditions, no repeats. The experiment's own
caveat: bursts are spread over four engines, so each knee sits very slightly
higher than a single engine would show.

    python3 paper_figures/fig_exp55_overlay.py
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import COL_W, TEXT_W, STYLE, GRID, save  # noqa: E402
from fig_exp55_class_knees import collect, CLS, ORDER  # noqa: E402

ROWS = [("attain", "SLO attain. (%)"), ("kv", "KV used (%)"),
        ("q", "Queue length")]
# (output, width, height, x ticks, w_pad) per variant.
#
# The ticks are EVENLY SPACED on the log axis in both: doublings in the wide
# version (log10 gaps 0.301 each) and triplings in the narrow one (0.477 each).
# [10, 20, 30, 50, 70, 90] reads as a sensible sequence written down but lands
# at gaps of 0.301, 0.176, 0.222, 0.146, 0.109 -- the ticks crowd to the right
# and the axis looks as though it compresses there, which it does not.
VARIANTS = [("exp55_overlay.pdf", TEXT_W, 1.90, [10, 20, 40, 80], 1.2),
            ("exp55_overlay_1col.pdf", COL_W, 1.55, [10, 30, 90], 0.5)]


def main():
    df = collect()
    if df.empty:
        print("no EXP-55 runs matched", file=sys.stderr)
        return 1
    knees = {}
    for tag in ORDER:
        d = df[df.tag == tag]
        healthy = d[d.attain >= 85.0]
        knees[tag] = healthy.rate.max() if len(healthy) else float("nan")
        print(f"{CLS[tag][0]:14s} measured {d.rate.min():5.1f}-{d.rate.max():5.1f}"
              f" req/s, knee {knees[tag]:5.1f}")

    xlo, xhi = float(df.rate.min()), float(df.rate.max())
    for out, width, height, xticks, wpad in VARIANTS:
        draw(df, knees, xlo, xhi, out, width, height, xticks, wpad)
    return 0


def draw(df, knees, xlo, xhi, out, width, height, xticks, wpad):
    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(1, len(ROWS), figsize=(width, height),
                                 sharex=True)
        for r, (field, ylab) in enumerate(ROWS):
            for tag in ORDER:
                d = df[df.tag == tag].sort_values("rate")
                lab, c, _, _ = CLS[tag]
                # All three curves solid. Colour alone separates them, and the
                # dashes were being read as a second dimension that does not
                # exist. NOTE: this figure no longer survives being printed in
                # black and white.
                axes[r].plot(d.rate, d[field], color=c, ls="-", lw=1.3,
                             label=lab if r == 0 else None)
                # The knee rule takes the CLASS colour, not one shared red.
                # Side by side there are three rules in every panel and they no
                # longer line up into a column the eye can read down, so a
                # single colour leaves the reader unable to tell which rule
                # belongs to which curve.
                axes[r].axvline(knees[tag], color=c, lw=0.7, ls=":", zorder=0)
            axes[r].set_xscale("log")
            # The box spans exactly the measured rates, 8 to 90 req/s, with no
            # padding: the frame corners ARE the ends of both axes, so no part
            # of the panel is outside the range anything was measured over.
            axes[r].set_xlim(xlo, xhi)
            axes[r].set_xticks(xticks)
            axes[r].set_xticklabels([str(t) for t in xticks])
            axes[r].set_ylabel(ylab, labelpad=1.5)
            axes[r].grid(axis="both", **GRID)
            axes[r].set_axisbelow(True)
        # Percentages get their natural 0-100 and the curves reach both ends.
        axes[0].set_ylim(0, 100); axes[0].set_yticks([0, 50, 100])
        axes[1].set_ylim(0, 100); axes[1].set_yticks([0, 50, 100])
        # symlog keeps the zeros that chat and the agent class hold at low rate.
        axes[2].set_yscale("symlog", linthresh=1.0)
        # Queue has no natural ceiling, so the top of the box is the largest
        # value measured. The highest labelled tick is then 10^3, below it.
        axes[2].set_ylim(0, float(df.q.max()))
        h, l = axes[0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", ncol=3,
                   bbox_to_anchor=(0.5, 0.875), frameon=False,
                   columnspacing=0.9, handlelength=1.5, handletextpad=0.35)
        fig.tight_layout(rect=(0, 0.09, 1, 0.865), w_pad=wpad, pad=0.35)
        # One x label under all three: the axis is shared, and three copies of
        # it in 3.335 in leaves no width for the curves.
        fig.text(0.5, 0.005, "Offered rate (request/s)", ha="center",
                 va="bottom", fontsize=8)
        save(fig, os.path.join(HERE, out))


if __name__ == "__main__":
    sys.exit(main())
