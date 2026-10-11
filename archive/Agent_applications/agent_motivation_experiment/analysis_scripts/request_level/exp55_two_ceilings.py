#!/usr/bin/env python3
"""Which of the two limits does each class reach first?

An engine has exactly two hard limits, and they are measured in different units:

  the time limit    the average time to produce one token, against the per-token
                    budget that class was given. Crossing it is an SLO violation
                    whatever the memory looks like.
  the memory limit  KV cache occupancy. Reaching it stops the engine accepting
                    work and starts preemption, whatever the latency looks like.

As load rises both quantities rise. The question this figure answers is which
limit each class reaches first, and the answer is a different one for each of
the three -- which is why no single threshold can control all three.

Both axes are expressed as a percentage of that class's own limit, so the two
are directly comparable and the point (100, 100) is "both limits at once". A
class leaving through the TOP edge ran out of time with memory to spare; one
leaving through the RIGHT edge ran out of memory while still inside its latency
budget.

The data is EXP-55: one class at a time on the same four engines, so no routing
decision differentiates anything and what is drawn is a property of the class
and the engine rather than of any policy.

  python3 exp55_two_ceilings.py 'results/*exp55r1*' <out-dir>
"""
import glob
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run, attain  # noqa: E402
from exp55_knees import CLS, engine_p90  # noqa: E402

# What each class is judged on, spelled out for the annotation rather than left
# to the reader to recover from the budget table.
RULE = {"chat": "50 ms/token",
        "deepresearch": "100 ms/token",
        "swe": "30 s end to end\n= 62 ms/token"}


def main(pattern, out):
    rows = []
    for d in sorted(glob.glob(pattern)):
        m = re.search(r"_(schat|sdr|sswe)_rpm_(\d+)", os.path.basename(d))
        if not m:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        name, _, budget, _ = CLS[m.group(1)]
        a = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])]
        itl = pd.to_numeric(a["itl_ms"], errors="coerce").median()
        e = engine_p90(d)
        rows.append(dict(cls=name, key=m.group(1), rate=int(m.group(2)) / 60.0,
                         kv=e["kv"] * 100, pace=100.0 * itl / budget,
                         off=attain(r, "violate_offered")))
    df = pd.DataFrame(rows)
    if df.empty:
        sys.exit(f"no runs matched {pattern}")

    print(f"{'class':<14}{'rate':>7}{'KV %':>8}{'pace % of budget':>18}{'offered':>9}")
    for k in CLS:
        for _, r in df[df.key == k].sort_values("rate").iterrows():
            print(f"{r.cls:<14}{r.rate:>7.0f}{r.kv:>8.1f}{r.pace:>18.1f}{r.off:>9.1f}")

    os.makedirs(out, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(5.2, 4.0))

        # The two limits, drawn as regions rather than lines so that "past the
        # limit" is visible without reading the axis.
        ax.axhspan(100, 260, color="#d62728", alpha=0.06, lw=0)
        ax.axvspan(100, 118, color="#1f77b4", alpha=0.06, lw=0)
        ax.axhline(100, color="#d62728", lw=1.0, ls="--")
        ax.axvline(100, color="#1f77b4", lw=1.0, ls="--")
        ax.annotate("time limit", (1.5, 103), fontsize=7, color="#d62728",
                    va="bottom", weight="bold")
        ax.annotate("memory limit", (101.5, 6), fontsize=7, color="#1f77b4",
                    rotation=90, va="bottom", weight="bold")

        for k, (name, c, _, _) in CLS.items():
            g = df[df.key == k].sort_values("rate")
            ax.plot(g.kv, g.pace, color=c, lw=1.2, marker="o", ms=3.5,
                    mec="white", mew=0.5, label=f"{name}  ({RULE[name]})")
            # The direction of increasing load, so the path is not read backwards.
            if len(g) >= 2:
                x0, y0 = g.kv.iloc[-2], g.pace.iloc[-2]
                x1, y1 = g.kv.iloc[-1], g.pace.iloc[-1]
                ax.annotate("", xy=(x1, y1), xytext=(x0, y0),
                            arrowprops=dict(arrowstyle="-|>", color=c, lw=1.2))
            # Where the class breaks: the first point below 90% offered.
            b = g[g.off < 60]
            if not b.empty:
                p = b.iloc[0]
                ax.plot(p.kv, p.pace, marker="*", ms=11, color=c,
                        mec="white", mew=0.6, zorder=5)

        ax.annotate("chat: out of TIME\nwith 73% of memory free",
                    (26.9, 98.4), xytext=(2, 132), fontsize=7, color="#1f77b4",
                    arrowprops=dict(arrowstyle="->", color="#1f77b4", lw=0.8))
        ax.annotate("swe: out of TIME too,\nbut at 64% memory\nand a different threshold",
                    (63.8, 105.5), xytext=(24, 205), fontsize=7, color="#d62728",
                    arrowprops=dict(arrowstyle="->", color="#d62728", lw=0.8))
        ax.annotate("deep research:\nout of MEMORY,\nstill inside its\nown time budget",
                    (99.9, 95.2), xytext=(3, 12), fontsize=7, color="#ff7f0e",
                    arrowprops=dict(arrowstyle="->", color="#ff7f0e", lw=0.8,
                                    connectionstyle="arc3,rad=-0.15"))

        ax.set_xlabel("memory used, % of the engine's KV cache (p90)")
        ax.set_ylabel("time used, % of that class's own per-token budget")
        ax.set_xlim(0, 118)
        ax.set_ylim(0, 260)
        ax.grid(ls=":", lw=0.6, alpha=0.5)
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.16), ncol=3,
                  fontsize=6.5, columnspacing=1.0)
        ax.set_title("Three classes, the same four engines, one class at a time.\n"
                     "Each line runs from low load to high; the star is where the\n"
                     "class stops meeting its rule. Each runs out of a different thing.",
                     fontsize=8)
        p = os.path.join(out, "two_ceilings.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/*exp55r1*",
         sys.argv[2] if len(sys.argv) > 2 else "results/aggregate_analysis/exp55")
