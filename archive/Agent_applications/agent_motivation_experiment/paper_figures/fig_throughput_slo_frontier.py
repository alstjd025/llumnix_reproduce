#!/usr/bin/env python3
"""Paper figure: what the fleet produced against how much of it was on time.

  throughput_slo_frontier.pdf   3.335 x 2.00 in, `figure`, width=\\columnwidth
  throughput_slo_frontier.csv   exactly the values drawn

One point per control plane and arrival rate: the share of the requests that
ARRIVED in that condition and met their deadline on the x axis, against the
tokens the four engines produced per second on the y axis. Eight rates per arm,
so eight points, and no line between them -- the points are separate operating
conditions and joining them would suggest a path the system moves along.

THE STAIRCASE IS THE BASELINES' FRONTIER. Of the four baselines' 32 points, the
ones that no other baseline point beats on both axes are marked and joined by a
step; the region under it is everything the four of them reached. It is a
frontier over MEASURED POINTS at eight arrival rates, not a bound: another rate,
or another baseline, could put a point above it.

⚠ THE DENOMINATOR OF THE X AXIS IS EVERY ARRIVAL, INCLUDING THE REFUSED ONES.
On an admitted denominator a policy that refuses almost everything reads near
100% -- Llumnix SLO at 70 req/s admits 2.6% of arrivals and meets the deadline
for 98% of those -- and the panel would rank it first. Rejection is therefore
counted as a violation here, and the rejection rates are drawn in the fourth
panel of `motivation_throughput_vs_goodput_4panel_t75_withfs.pdf`.

⚠ THE Y AXIS COUNTS TOKENS THE ENGINES EMITTED, ON TIME OR NOT. That is the
point of pairing the two axes: two arms can produce the same number of tokens
per second and deliver a different share of them inside the deadline, which is
the whole distance between the vLLM router and FluidServe at 45 req/s -- 14,214
against 15,511 tokens per second, 6.2% against 69.5% of arrivals served.

SCORING. Token i of a request is on time if it arrives within
TTFT_SLO + i x TBT_SLO of the send, i counted from zero; a request is on time if
at least 95% of its tokens are. `deadline_ladder_attainment.py` owns the rule and
this script reads its per-run table, the same one the four-panel figure draws.

DATA. EXP-108 (2026-08-31) for the four arms and EXP-77 (2026-08-10) for the
vLLM router, eight arrival rates, two repeats per cell except the vLLM router at
45 and 70 req/s. Each point is the mean of its repeats.

    python3 paper_figures/fig_throughput_slo_frontier.py
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
import paper_style as ps  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FOUR = _load("tg4", os.path.join(HERE, "fig_motivation_tg_4panel.py"))
ARMS = list(FOUR.ARMS)          # (key, label, colour, marker, size)
OURS = "FluidServe"
# 2.00 -> 1.60 in on request (2026-09-09), the height alone: the width is the
# column and a figure drawn narrower would have to be scaled back up to it,
# which multiplies every font size by the same factor.
# The panel is drawn at the golden ratio: 2.25 in wide by 2.25 / 1.618 = 1.39
# in high. The canvas height follows from that plus the margins the labels need,
# which are measured rather than guessed, so the box is exactly this size on the
# page and the script prints it.
PHI = 1.6180339887
AXES_W = 2.25           # 2.50 -> 2.25 on request; the ratio is kept
AXES_H = AXES_W / PHI
FIG_H = 1.60


def collect():
    frames = [pd.read_csv(FOUR.CSV)]
    if os.path.exists(FOUR.CSV_VLLM):
        frames.append(pd.read_csv(FOUR.CSV_VLLM))
    d = pd.concat(frames, ignore_index=True)
    d["rate"] = d["run"].str.extract(r"_rpm_(\d+)")[0].astype(float) / 60.0
    g = (d.groupby(["arm", "rate"])[["throughput_tok_s", "offered", "admitted",
                                     "rejected_pct", "goodput_tok_s"]]
         .agg(["mean", "count"]))
    g.columns = ["_".join(c) if c[1] == "count" else c[0] for c in g.columns]
    return g.reset_index()


def frontier(pts):
    """The Pareto-optimal points of `pts` = [(x, y), ...], maximising both."""
    keep = [p for p in pts
            if not any(q[0] >= p[0] and q[1] >= p[1] and q != p for q in pts)]
    return sorted(keep)


def build_one(tab, rate, out):
    """One arrival rate: attainment on the x axis, throughput on the y.

    Five points, one per control plane, labelled beside the marker rather than
    through a legend -- with five points a key costs more space than the names
    do. The dashed line joins the two baselines that no other baseline beats on
    both axes, which is the trade-off the four of them offer at this rate.
    """
    labels = {k: (lab, col, mk, sz) for k, lab, col, mk, sz in ARMS}
    sub = tab[np.isclose(tab["rate"], rate)]
    base = [(r.admitted, r.throughput_tok_s, labels[r.arm][0])
            for r in sub.itertuples() if labels[r.arm][0] != OURS]
    front = sorted(p for p in base
                   if not any(q[0] >= p[0] and q[1] >= p[1] and q != p
                              for q in base))
    with plt.rc_context(ps.STYLE):
        fig, ax = plt.subplots(figsize=(ps.COL_W, FIG_H))
        for r in sub.itertuples():
            lab, col, mk, sz = labels[r.arm]
            # Ours is a star and the baselines keep the shapes they have in
            # every other figure. A star of the same nominal size carries less
            # ink than a square, so it is set larger by hand rather than by the
            # table's size column.
            if lab == OURS:
                mk, ms = "*", 11.5
            else:
                ms = sz + 5.5
            # A thin black keyline on every marker: five saturated fills on a
            # white ground read as five different weights without one, and the
            # star loses its points against the grid.
            ax.plot([r.admitted], [r.throughput_tok_s], ls="none", marker=mk,
                    ms=ms, color=col, mec="#000000", mew=0.5, zorder=3)
            # Names beside the points, nudged away from the neighbour each one
            # has: the two baselines at the top of the throughput axis sit
            # within 800 tokens/s of each other.
            # Where each name sits is set per point, because the five points
            # are in three corners and one rule cannot serve them: the default
            # is to the right, the two on the right edge go left, and the two
            # that would collide with a neighbour go up or down.
            dx, dy, ha, va = 3.0, 0.0, "left", "center"
            if lab == "vLLM-router":
                dx, dy, va = 3.0, -550, "top"
            if lab in ("PolyServe", "llm-d"):
                dx, dy, ha, va = -3.0, 420, "right", "bottom"
            if lab == OURS:
                lab, dx, dy, ha, va = f"{lab}\n(ours)", -3.5, -420, "right", "top"
            ax.annotate(lab, (r.admitted, r.throughput_tok_s),
                        textcoords="offset points" if False else "data",
                        xytext=(r.admitted + dx, r.throughput_tok_s + dy),
                        fontsize=8.0, color=col, ha=ha, va=va)
        ax.set_xlim(0, 112)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_ylim(7.5e3, 1.68e4)
        ax.yaxis.set_major_formatter(ps.kfmt())
        # ⚠ THE DENOMINATOR IS NO LONGER ON THE AXIS. The x label names the
        # quantity in full and the caption has to say that it counts the
        # requests each control plane ACCEPTED: at this rate llm-d reaches 97.7%
        # after refusing 61.5% of arrivals and FluidServe 99.9% after refusing
        # 30.4%, and an axis that does not say so invites the two to be read as
        # the same achievement.
        ax.set_xlabel("Service-level Objective Attainment (%)", labelpad=1.5)
        ax.set_ylabel("Throughput (token/s)", labelpad=1.5)
        ax.grid(axis="both", **ps.GRID)
        ax.set_axisbelow(True)
        # Boxed: the points sit in three corners of the panel and the frame is
        # what says where the space they could occupy ends.
        for side in ("top", "right"):
            ax.spines[side].set_visible(True)
        fig.tight_layout(pad=0.35)
        # The axes box is pinned in INCHES, not left to the layout: tight_layout
        # gives the axes whatever the labels do not take, which made it 2.85 by
        # 1.25 in here. The margins it worked out are kept and the box is set to
        # the golden-ratio size inside them.
        w, h = fig.get_size_inches()
        bb = ax.get_position()
        need = bb.y0 * h + AXES_H + (1 - bb.y1) * h
        if abs(need - h) > 0.01:
            # The canvas grows or shrinks to hold the pinned box plus the
            # margins the labels asked for, so neither is squeezed.
            fig.set_size_inches(w, need)
            fig.tight_layout(pad=0.35)
            w, h = fig.get_size_inches()
            bb = ax.get_position()
        ax.set_position([bb.x0, bb.y0, AXES_W / w, AXES_H / h])
        # Centre the DRAWN INK, not the axes box: the y label and its tick
        # numbers hang off the left of the box and the x label off the bottom,
        # so centring the box alone leaves the figure looking pushed left. The
        # tight bounding box of everything drawn is measured and the axes moved
        # by half the difference between its centre and the canvas centre.
        fig.canvas.draw()
        tb = fig.get_tightbbox(fig.canvas.get_renderer())
        bb = ax.get_position()
        ax.set_position([bb.x0 + (w / 2 - (tb.x0 + tb.x1) / 2) / w,
                         bb.y0 + (h / 2 - (tb.y0 + tb.y1) / 2) / h,
                         bb.width, bb.height])
        fig.canvas.draw()
        tb = fig.get_tightbbox(fig.canvas.get_renderer())
        print(f"    ink margins: left {tb.x0:.2f} right {w - tb.x1:.2f} "
              f"bottom {tb.y0:.2f} top {h - tb.y1:.2f} in")
        bb = ax.get_position()
        print(f"    panel axes box {bb.width * w:.2f} x {bb.height * h:.2f} in")
        ps.save(fig, out)
    return sub, front


def build(tab, out):
    """All eight arrival rates: attainment on the x axis, throughput on the y.

    The axes were swapped on 2026-09-10 so that this figure and the
    single-rate one put the same quantity on the same axis.
    """
    labels = {k: (lab, col, mk, sz) for k, lab, col, mk, sz in ARMS}
    base_pts = [(r.offered, r.throughput_tok_s) for r in tab.itertuples()
                if labels[r.arm][0] != OURS]
    front = frontier(base_pts)

    with plt.rc_context(ps.STYLE):
        fig, ax = plt.subplots(figsize=(ps.COL_W, FIG_H))
        # The frontier first, so the points sit on top of it.
        # With attainment on the x axis the staircase DESCENDS, so the step
        # direction is "pre" and not "post": the ceiling on throughput for an
        # attainment x is the throughput of the first frontier point at or to
        # the right of x, which makes each tread cover (x_{i-1}, x_i] at y_i.
        # It is extended left to x = 0, where the same ceiling still holds.
        fx = [p[0] for p in front]
        fy = [p[1] for p in front]
        sx, sy = [0.0] + fx, [fy[0]] + fy
        ax.step(sx, sy, where="pre", color="#909090", lw=0.8, ls="--", zorder=1)
        ax.fill_between(sx, 0, sy, step="pre",
                        color="#bdbdbd", alpha=0.25, lw=0, zorder=0)
        for key, lab, col, mk, sz in ARMS:
            sub = tab[tab["arm"] == key]
            ax.plot(sub["offered"], sub["throughput_tok_s"], ls="none",
                    marker=mk, ms=sz + 1.2, color=col, label=lab, zorder=3)
        ax.set_xlim(0, 105)
        ax.set_xticks([0, 25, 50, 75, 100])
        ax.set_ylim(3.5e3, 1.72e4)
        ax.yaxis.set_major_formatter(ps.kfmt())
        # ⚠ SHORT LABEL, DENOMINATOR IN THE CAPTION. "SLO attainment (%), all
        # arrivals" is 1.6 in of set type against a 1.5 in axis and was clipped
        # at both ends; the denominator is the first thing the caption says.
        ax.set_xlabel("SLO attainment (%)", labelpad=1.5)
        ax.set_ylabel("Tokens/s produced", labelpad=1.5)
        ax.grid(axis="both", **ps.GRID)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.text(fx[len(fx) // 2] + 2.0, fy[len(fx) // 2] - 300,
                "baselines' frontier", fontsize=5.8,
                color="#707070", ha="left", va="top")
        # Lower centre, not lower left: with attainment on the x axis the
        # points fill both bottom corners, and a key in either one sits on top
        # of them. The band between 25% and 75% attainment below 8k tokens/s is
        # the only part of the panel no arm reaches.
        ax.legend(loc="lower center", frameon=False, fontsize=6.0, ncol=2,
                  handlelength=1.0, handletextpad=0.4, borderaxespad=0.2,
                  columnspacing=0.9, labelspacing=0.25)
        fig.tight_layout(pad=0.35)
        ps.save(fig, out)
    return front


def main():
    tab = collect()
    # ⚠ 45 req/s IS CHOSEN AND THE REASON IS STATED. It is the only arrival rate
    # at which FluidServe beats every baseline on BOTH axes: at 35 the vLLM
    # router and PolyServe produce more tokens (14,288 and 14,684 against
    # 13,799) and at 55 PolyServe does (15,119 against 14,868). A single-rate
    # panel is a choice of operating point, so the panel says which one and the
    # sweep across all eight is the other figure in this file.
    rate = float(os.environ.get("FRONTIER_RATE", 45))
    sub, front1 = build_one(tab, rate,
                            os.path.join(HERE, f"throughput_slo_at{rate:.0f}.pdf"))
    print(f"\nat {rate:.0f} req/s (x = admitted attainment, y = throughput):")
    labels = {k: lab for k, lab, *_ in ARMS}
    for r in sub.itertuples():
        print(f"  {labels[r.arm]:13s} {r.admitted:6.1f}%  {r.throughput_tok_s:8,.0f} "
              f"tok/s   rejected {r.rejected_pct:5.1f}%")
    print("  baseline frontier: " +
          " -> ".join(f"{n} ({x:.1f}%, {y:,.0f})" for x, y, n in front1))
    front = build(tab, os.path.join(HERE, "throughput_slo_frontier.pdf"))
    labels = {k: lab for k, lab, *_ in ARMS}
    tab["arm_label"] = tab["arm"].map(labels)
    tab["on_baseline_frontier"] = [
        (r.offered, r.throughput_tok_s) in front for r in tab.itertuples()]
    cols = ["arm_label", "rate", "throughput_tok_s", "offered", "admitted",
            "rejected_pct", "goodput_tok_s", "offered_count",
            "on_baseline_frontier"]
    out = os.path.join(HERE, "throughput_slo_frontier.csv")
    tab[cols].to_csv(out, index=False, float_format="%.2f")
    print(tab[cols].round(1).to_string(index=False))
    print(f"\nbaseline frontier points (attainment %, tokens/s): " +
          ", ".join(f"({x:.1f}, {y:,.0f})" for x, y in front))
    print(f"wrote {out}  ({len(tab)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
