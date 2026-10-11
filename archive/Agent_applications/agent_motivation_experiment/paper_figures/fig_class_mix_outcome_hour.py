#!/usr/bin/env python3
"""Paper figure: how each control plane placed the classes, and what the same
fleet returned while it did.

  class_mix_outcome_hour.pdf   7.0 x 3.60 in, `figure*`, width=\\textwidth
  class_mix_outcome_hour.csv   exactly the values drawn in the bottom row

  rows 1-4  the four instances, as in `class_mix_hour.pdf`: a band is one
            class's share of the requests resident on that instance in a 60 s
            window, and the instances are sorted once per column by the chat
            each held over the hour
  row 5     what came out of that placement: tokens per second the engines
            produced (grey) and the tokens among them that met their own
            deadline (the arm's colour). THE GAP IS WORK THAT WAS DONE AND
            COUNTED FOR NOTHING.

WHY THE TWO HALVES BELONG IN ONE FIGURE. The placement rows show that the
control planes divide the fleet differently, but a division is not better for
being different. The bottom row is the same hour scored: the fleet, the trace
and the engines are identical across the five columns, so the differences in the
coloured area are differences in what the control plane did with them. Reading
down a column gives the mechanism and then its result.

⚠ GOODPUT IS NOT THROUGHPUT, AND THE FIGURE IS ABOUT THE DIFFERENCE. The vLLM
router's engines produce tokens at a rate close to FluidServe's, and almost none
of them arrive inside their deadline. A figure that showed only tokens produced
would rank the two the same way; one that showed only goodput would leave a
reader wondering whether the engines were idle. Both lines are drawn on one
scale so the gap can be read directly.

THE SCORING RULE. Token i of a request is on time if it arrives within
TTFT_SLO + i x TBT_SLO of the send, with i counted from zero. Goodput counts
tokens, not requests, so a request that missed the 95% bar still contributes the
tokens of its own that were on time. `deadline_ladder_attainment.py` owns the
rule and this script reads its per-request verdicts.

⚠ WHAT THE BOTTOM ROW DOES NOT SEPARATE. Goodput falls for two different
reasons: a policy can reject a request, or it can accept one and deliver it
late. Both leave the coloured area smaller, and the rejection rates over this
run are FluidServe 19.9%, PolyServe 22.5%, Llumnix SLO 39.4%, llm-d 49.0%, vLLM
router 0.0%. The request-level split is in `class_mix_hour.pdf`'s companion
timeline (`exp109_hour_five_admitted.pdf`) and the caption must carry the
rejection rates.

⚠ THE vLLM ROUTER'S PLACEMENT ROWS ARE DRAWN ON 74.9% OF ITS ADMITTED REQUESTS
(engine attribution reads the scheduler's dispatch log and those lines are
dropped under load; the shortfall is concentrated after minute 40). Its bottom
row is unaffected: goodput and throughput come from the client's own records and
the engines' counters, neither of which needs an engine name.

DATA. EXP-109 (2026-08-31), repeat 1 of the five arms -- the same runs in both
halves, which is why this figure exists rather than two figures side by side.
Placement from `build_class_mix_tables.py` in 60 s windows; goodput from the
ladder verdicts in 90 s windows stepped every 30 s; throughput from
`vllm:generation_tokens_total` on the four engines.

    python3 paper_figures/fig_class_mix_outcome_hour.py
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))
import paper_style as ps  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MIXFIG = _load("cmh", os.path.join(HERE, "fig_class_mix_hour.py"))
HOURFIG = _load("e109", os.path.join(HERE, "fig_exp109_hour.py"))

CLASS_COLOR = MIXFIG.CLASS_COLOR
CLASS_LABEL = MIXFIG.CLASS_LABEL
CLASSES = MIXFIG.CLASSES
RANKS = MIXFIG.RANKS
# The same five runs in both halves. Repeat 1, to match the placement tables.
RUNS = {
    "vLLM-router": "260901_2137_exp109r1_vllmcachet75_shift",
    "PolyServe": "260831_2232_exp109r1_polyservept75_shift",
    "Llumnix SLO": "260831_2346_exp109r1_slot75_shift",
    "llm-d": "260831_2128_exp109r1_llmdslot75_shift",
    "FluidServe": "260831_2015_exp109r1_fsv3capgnofrct75_shift",
}
ARM_KEY = {"vLLM-router": "vllmrouter", "PolyServe": "polyserve",
           "Llumnix SLO": "slo", "llm-d": "llmd", "FluidServe": "fluidserve"}
ORDER = ["vLLM-router", "PolyServe", "Llumnix SLO", "llm-d", "FluidServe"]
XMAX = MIXFIG.XMAX
FIG_H = 3.60
THRU_COLOR = "#9e9e9e"


def outcome():
    """label -> (minutes, tokens produced/s, tokens on time/s)."""
    out = {}
    for label in ORDER:
        s = HOURFIG.series(os.path.join(ROOT, "results", RUNS[label]))
        if s is None:
            print(f"!! no verdicts for {label}", file=sys.stderr)
            continue
        out[label] = (s["x"], s["thru"], s["gp"])
    return out


def write_csv(res, cover, path):
    rows = []
    for label, (x, thru, gp) in res.items():
        for xi, t, g in zip(x, thru, gp):
            rows.append({"arm": label, "minute": float(xi),
                         "throughput_tok_s": float(t),
                         "goodput_tok_s": float(g),
                         "on_time_share_pct": (100.0 * g / t) if t > 0 else np.nan,
                         "attributed_pct": round(cover.get(label, np.nan), 2),
                         "rule": "ladder95"})
    df = pd.DataFrame(rows).sort_values(["arm", "minute"])
    df.to_csv(path, index=False, float_format="%.3f")
    print(f"wrote {path}  ({len(df)} rows)")


def build(mix, res, cover, out):
    labels = [l for l in ORDER if l in res]
    with plt.rc_context(ps.STYLE):
        fig = plt.figure(figsize=(ps.TEXT_W, FIG_H))
        gs = fig.add_gridspec(5, len(labels),
                              height_ratios=[1, 1, 1, 1, 1.7],
                              hspace=0.30, wspace=0.26,
                              left=0.068, right=0.984, top=0.905, bottom=0.085)
        top = max(np.nanmax(t) for _, t, _ in res.values())

        for j, label in enumerate(labels):
            for i, rank in enumerate(RANKS):
                ax = fig.add_subplot(gs[i, j])
                for side in ("top", "right"):
                    ax.spines[side].set_visible(False)
                ax.set_xlim(0, XMAX)
                ax.set_xticks([0, 15, 30, 45, 60])
                ax.set_xticklabels([])
                ax.set_ylim(0, 100)
                ax.set_yticks([0, 100] if i == 0 else [])
                if (label, rank) in mix:
                    piv = mix[(label, rank)]
                    x = piv.index.to_numpy() / 60.0
                    vals = [piv[c].to_numpy(dtype=float) for c in CLASSES]
                    tot = np.sum(vals, axis=0)
                    vals = [np.where(tot > 0, 100.0 * v / np.where(tot > 0, tot, 1.0),
                                     0.0) for v in vals]
                    ax.stackplot(x, *vals,
                                 colors=[CLASS_COLOR[c] for c in CLASSES],
                                 linewidth=0.0)
                if i == 0:
                    ax.set_title(label, fontsize=8, pad=3)
                if j == 0:
                    ax.set_ylabel(f"I{rank}", labelpad=1.5, fontsize=7)

            ax = fig.add_subplot(gs[4, j])
            x, thru, gp = res[label]
            col = ps.ARM_COLOR[ARM_KEY[label]]
            # The wasted part is drawn as the space between the two lines, not
            # as a third series: it is a difference, and shading it keeps the
            # eye on the size of the gap rather than on another edge to follow.
            ax.fill_between(x, gp, thru, color=THRU_COLOR, alpha=0.28, lw=0)
            ax.plot(x, thru, color=THRU_COLOR, lw=0.7)
            ax.plot(x, gp, color=col, lw=0.9)
            ax.set_xlim(0, XMAX)
            ax.set_xticks([0, 15, 30, 45, 60])
            ax.set_ylim(0, 1.05 * top)
            ax.yaxis.set_major_formatter(ps.kfmt())
            if j > 0:
                ax.set_yticklabels([])
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)
            ax.set_xlabel("minute", labelpad=1.5)
            if j == 0:
                ax.set_ylabel("Tokens/s", labelpad=1.5)
            # The hour's own summary, in the panel: the mean rate of on-time
            # tokens and the share of produced tokens they are.
            m_gp, m_th = float(np.nanmean(gp)), float(np.nanmean(thru))
            ax.text(0.04, 0.93, f"{m_gp / 1000:.1f}k  ({100 * m_gp / m_th:.0f}%)",
                    transform=ax.transAxes, fontsize=6.4, va="top", color=col)

        keys = [(CLASS_COLOR[c], CLASS_LABEL[c]) for c in CLASSES]
        handles = [Patch(facecolor=c, label=l, edgecolor="#666666", linewidth=0.4)
                   for c, l in keys]
        handles.append(Line2D([], [], color=THRU_COLOR, lw=0.9,
                              label="Tokens produced"))
        # The on-time line takes the arm's colour in each column, so the key
        # shows the line and names it; the colour is the column's, not a fifth
        # series.
        handles.append(Line2D([], [], color="#333333", lw=0.9,
                              label="Tokens on time (arm colour)"))
        fig.legend(handles, [l for _, l in keys] + ["Tokens produced",
                                                    "Tokens on time (arm colour)"],
                   loc="lower center", ncol=5,
                   bbox_to_anchor=(0.5, 1 - 0.150 / FIG_H), frameon=False,
                   fontsize=7, columnspacing=1.1, handlelength=1.0,
                   handleheight=1.0, handletextpad=0.4, borderaxespad=0.0)
        ps.save(fig, out)


def report(res):
    print(f"{'arm':13s} {'produced':>10s} {'on time':>9s} {'on time %':>10s}")
    for label, (_, thru, gp) in res.items():
        m_gp, m_th = float(np.nanmean(gp)), float(np.nanmean(thru))
        print(f"{label:13s} {m_th:10,.0f} {m_gp:9,.0f} {100 * m_gp / m_th:10.1f}")
    print("\nmean over the hour's windows, tokens per second")


def main():
    mix, cover = MIXFIG.collect()
    res = outcome()
    report(res)
    pdf = os.path.join(HERE, "class_mix_outcome_hour.pdf")
    build(mix, res, cover, pdf)
    write_csv(res, cover, pdf[:-4] + ".csv")
    print("  the placement rows draw the values in class_mix_hour.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
