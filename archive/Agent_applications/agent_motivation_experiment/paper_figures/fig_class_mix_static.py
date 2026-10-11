#!/usr/bin/env python3
"""Paper figure: how each control plane mixed the three classes across the four
engines, as the arrival rate rises.

  class_mix_static.pdf   7.0 x 2.75 in, `figure*`, width=\\textwidth
  class_mix_static.csv   exactly the values drawn

  rows    three arrival rates: 20 req/s (below every arm's knee), 35 (above it)
          and 45 (deep in the region where four of the five reject)
  columns the five control planes, in the paper's order
  bars    the four engines, and the height is the MEAN NUMBER OF REQUESTS OF
          THAT CLASS CONCURRENTLY RESIDENT on that engine, stacked by class

WHY RESIDENCY AND NOT A DISPATCH COUNT. The claim the figure supports is about
what shared an engine at the same time, because an instance holding one request
of the tightest-budget class is held to that budget for everything else it might
take. A count of dispatches answers a different question and weights a class of
short requests equal to a class of long ones that occupied the engine for
minutes. `engine_class_occupancy.py` computes both; the dispatch count is in the
CSV as well.

⚠ THE BARS ARE ORDERED BY WHAT THE ENGINE HELD, NOT BY PORT NUMBER. Bar 1 is
the engine holding the most chat IN THAT WINDOW, bar 2 the next, and so on, with
the ordering redone every 60 s and the bars then averaged over windows. Two
reasons, and the second is the one that matters:

  a port number is not an identity. Which engine ends up with the tight class is
      an outcome; it is a different port in each repeat and in each arm, so
      averaging by port would average two different things together.
  the assignment moves inside a static condition. Llumnix SLO at 55 req/s holds
      the agent class on an effective 1.88 instances measured per window and on
      3.98 measured by pooling the whole condition, because the pair of engines
      holding it changed six times. Pooling by port would draw that arm as
      spreading the class evenly over four engines, which is the opposite of
      what it did at every instant.

  The same check run on every condition is in `static_summary.csv`: for each
  class, the effective number of instances per window, the same pooled, and the
  number of holder changes. Where the two agree the ordering changes nothing;
  where they disagree it is what keeps the figure honest.

⚠ THE DENOMINATOR IS ADMITTED WORK. A rejected request has no engine, so an arm
that rejects more carries less and its bars are shorter for that reason as well
as any other. At 45 req/s the rejection rates are FluidServe 30.4%, PolyServe
28.3%, llm-d 61.5%, Llumnix SLO 73.0%, vLLM router 0.0%; the caption has to
carry them, and the rejection panel of
`motivation_throughput_vs_goodput_4panel_t75_withfs.pdf` is where they are drawn.

⚠ ONE CELL IS DRAWN ON A SUBSET. Attribution goes through the scheduler's own
dispatch log, whose lines are dropped under high load. Coverage is 99.8-100% in
every cell of this figure except the vLLM router at 45 req/s, which is 85.2% of
admitted requests, and it is n=1 there because the other repeat kept no
scheduler metrics at all. Coverage falls further outside the drawn rates -- vLLM
router 69.0% at 55 and 52.3% at 70, Llumnix SLO 54.8% at 70 -- which is why the
figure stops at 45 rather than at the top of the sweep. Every cell's coverage is
in `static_summary.csv`.

DATA. EXP-108 (2026-08-31) for four arms and EXP-77 (2026-08-10) for the vLLM
router, the same 79 runs as the score figures, through
`build_class_mix_tables.py`. Two repeats per cell except vLLM router at 45 req/s.
The window is 60 s and the analysis window is `load_run`'s, so 60 s of warmup
and 20 s of drain are cut.

    python3 paper_figures/fig_class_mix_static.py
"""
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402

MIX = os.path.join(ROOT, "results", "aggregate_analysis", "class_mix")
CSV_MIX = os.path.join(MIX, "static_rank_mix.csv")
CSV_SUM = os.path.join(MIX, "static_summary.csv")

# Arm key -> paper name, in the paper's order. Same keys and order as
# `fig_motivation_tg_4panel.py`.
ARMS = [("vllmcache", "vLLM-router"), ("polyservept75", "PolyServe"),
        ("slot75", "Llumnix SLO"), ("llmdslot75", "llm-d"),
        ("fsv3capgnofrct75", "FluidServe")]
# Class colours are the project-wide ones (`exp22_fluidserve.CLASS_COLORS`), so
# a colour means the same class in an exploratory figure and in the paper.
CLASS_COLOR = {"chat": "#1f77b4", "deepresearch": "#ff7f0e", "swe": "#d62728"}
CLASS_LABEL = {"chat": "chat", "deepresearch": "deep research", "swe": "agent"}
CLASSES = ("chat", "deepresearch", "swe")
RATES = [20.0, 35.0, 45.0]
FIG_H = 2.75


def collect():
    d = pd.read_csv(CSV_MIX)
    s = pd.read_csv(CSV_SUM)
    out, cover = {}, {}
    for arm, label in ARMS:
        for rate in RATES:
            sub = d[(d["arm"] == arm) & (np.isclose(d["rate_req_s"], rate))]
            if sub.empty:
                print(f"!! no rows for {label} at {rate:.0f} req/s",
                      file=sys.stderr)
                continue
            # Mean over windows first (so the stack is additive and equals the
            # time average), then mean over repeats; the spread over repeats is
            # kept as the error bar on the bar total.
            piv = sub.pivot_table(index=["run", "engine_rank"], columns="class",
                                  values="resident_mean", aggfunc="sum",
                                  fill_value=0.0)
            for c in CLASSES:
                if c not in piv:
                    piv[c] = 0.0
            per_run_tot = piv[list(CLASSES)].sum(axis=1).groupby(level="engine_rank")
            out[(label, rate)] = {
                "mean": piv.groupby(level="engine_rank")[list(CLASSES)].mean(),
                "tot_min": per_run_tot.min(), "tot_max": per_run_tot.max(),
                "n": sub["run"].nunique()}
            cov = s[(s["arm"] == arm) & (np.isclose(s["rate_req_s"], rate))]
            cover[(label, rate)] = float(cov["attributed_pct"].mean())
    return out, cover


def write_csv(data, cover, path):
    rows = []
    for (label, rate), v in data.items():
        m = v["mean"]
        for rank in m.index:
            row = {"arm": label, "rate_req_s": rate, "engine_rank": int(rank),
                   "n_repeats": v["n"], "attributed_pct": round(cover[(label, rate)], 2)}
            for c in CLASSES:
                row[f"resident_{c}"] = float(m.loc[rank, c])
            row["resident_total"] = float(m.loc[rank, list(CLASSES)].sum())
            row["resident_total_min"] = float(v["tot_min"].loc[rank])
            row["resident_total_max"] = float(v["tot_max"].loc[rank])
            row["unit"] = "mean concurrently resident requests"
            row["order"] = "engines ranked per 60 s window by chat residency"
            rows.append(row)
    df = pd.DataFrame(rows).sort_values(["rate_req_s", "arm", "engine_rank"])
    df.to_csv(path, index=False)
    print(f"wrote {path}  ({len(df)} rows)")


def build(data, cover, out, share=False):
    """The grid. `share=True` divides each bar by its own total -- the two
    questions in the module docstring, drawn from one table."""
    labels = [l for _, l in ARMS]
    with plt.rc_context(ps.STYLE):
        fig, axes = plt.subplots(len(RATES), len(labels),
                                 figsize=(ps.TEXT_W, FIG_H), sharex=True)
        for i, rate in enumerate(RATES):
            # y is shared ALONG A ROW, not down a column: the comparison the row
            # makes is between arms at one arrival rate, and the fleet holds
            # several times more work at 45 req/s than at 20, which would flatten
            # the top row into the axis if all nine cells shared one scale.
            top = max((data[(l, rate)]["tot_max"].max() for l in labels
                       if (l, rate) in data), default=1.0)
            for j, label in enumerate(labels):
                ax = axes[i][j]
                ax.spines["top"].set_visible(False)
                ax.spines["right"].set_visible(False)
                ax.grid(axis="y", **ps.GRID)
                ax.set_axisbelow(True)
                ax.set_ylim(0, 106 if share else top * 1.14)
                if share:
                    ax.set_yticks([0, 50, 100])
                if (label, rate) not in data:
                    ax.text(0.5, 0.5, "no data", ha="center", va="center",
                            transform=ax.transAxes, fontsize=6.5, color="#808080")
                    continue
                v = data[(label, rate)]
                m = v["mean"].copy()
                tot = m[list(CLASSES)].sum(axis=1)
                if share:
                    m = m[list(CLASSES)].div(tot.where(tot > 0, np.nan), axis=0) * 100.0
                    m = m.fillna(0.0)
                x = np.arange(len(m.index))
                bottom = np.zeros(len(x))
                for c in CLASSES:
                    vals = m[c].to_numpy(dtype=float)
                    ax.bar(x, vals, 0.72, bottom=bottom, color=CLASS_COLOR[c],
                           edgecolor="white", linewidth=0.3)
                    bottom += vals
                if not share and v["n"] > 1:
                    lo = bottom - v["tot_min"].to_numpy()
                    hi = v["tot_max"].to_numpy() - bottom
                    ax.errorbar(x, bottom, yerr=[lo, hi], fmt="none",
                                ecolor="#404040", elinewidth=0.6, capsize=1.2)
                ax.set_xticks(x)
                ax.set_xticklabels([str(k) for k in m.index])
                if i == 0:
                    ax.set_title(label, fontsize=8, pad=3)
                if j == 0:
                    ax.set_ylabel("share (%)" if share else "requests",
                                  labelpad=1.5)
                # The arrival rate labels the ROW, on the right, so that it
                # cannot be read as a quantity on the y axis.
                if j == len(labels) - 1:
                    ax.text(1.07, 0.5, f"{rate:.0f} req/s", rotation=270,
                            ha="left", va="center", transform=ax.transAxes,
                            fontsize=8)
                # A cell drawn on a subset of the admitted requests says so in
                # the cell: a reader comparing bars across a row has to know that
                # one of them is 85% of the work rather than all of it.
                if cover.get((label, rate), 100.0) < 99.0:
                    ax.text(0.03, 0.06, f"{cover[(label, rate)]:.0f}% attr.",
                            ha="left", va="bottom", transform=ax.transAxes,
                            fontsize=5.6, color="#404040")

        handles = [Patch(facecolor=CLASS_COLOR[c], label=CLASS_LABEL[c])
                   for c in CLASSES]
        fig.legend(handles, [CLASS_LABEL[c] for c in CLASSES], loc="lower center",
                   ncol=3, bbox_to_anchor=(0.5, 1 - 0.175 / FIG_H), frameon=False,
                   fontsize=7, columnspacing=1.0, handlelength=1.1,
                   handletextpad=0.4, borderaxespad=0.0)
        fig.supxlabel("the four engines, ordered within each 60 s window by how "
                      "much chat they held", fontsize=8, y=0.012)
        fig.tight_layout(rect=(0, 0.045, 0.985, 1 - 0.16 / FIG_H),
                         w_pad=0.7, h_pad=0.6, pad=0.3)
        ps.save(fig, out)


def report(data, cover):
    print(f"{'rate':>5s} {'arm':13s} {'n':>2s} {'attr%':>6s} "
          + " ".join(f"{'rank' + str(k):>22s}" for k in (1, 2, 3, 4)))
    for rate in RATES:
        for _, label in ARMS:
            if (label, rate) not in data:
                continue
            v = data[(label, rate)]
            cells = []
            for rank in v["mean"].index:
                r = v["mean"].loc[rank]
                cells.append(f"{r['chat']:6.1f}/{r['deepresearch']:5.1f}/"
                             f"{r['swe']:5.1f}")
            print(f"{rate:5.0f} {label:13s} {v['n']:2d} "
                  f"{cover[(label, rate)]:6.1f} "
                  + " ".join(f"{c:>22s}" for c in cells))
    print("\ncells are  chat / deep research / agent, mean concurrent requests")


def main():
    data, cover = collect()
    report(data, cover)
    pdf = os.path.join(HERE, "class_mix_static.pdf")
    build(data, cover, pdf, share=False)
    write_csv(data, cover, pdf[:-4] + ".csv")
    shr = os.path.join(HERE, "class_mix_static_share.pdf")
    build(data, cover, shr, share=True)
    # The share figure is the same table with each bar divided by its own total,
    # so it gets no CSV of its own: deriving one number twice is how two files
    # that should agree stop agreeing.
    print(f"  class_mix_static_share.pdf is drawn from class_mix_static.csv "
          f"(each bar divided by its own total)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
