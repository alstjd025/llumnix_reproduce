#!/usr/bin/env python3
"""FluidServe (current) against llm-d, per class, on the mix-shift hour trace.

Four quantities, because no one of them can be read alone:

  rejection      what the policy refused. On its own it rewards accepting
                 everything.
  offered        every arrival is in the denominator and a rejection is a
                 violation. This is the quantity the repository treats as
                 headline, and it is the one a rejection cannot flatter.
  admitted       only accepted requests are in the denominator, so it says how
                 well a policy keeps the promises it made. On its own it rewards
                 refusing everything, which is why it never appears without the
                 rejection rate beside it.
  goodput        output tokens per second from requests that met their rule --
                 the only one of the four that is not a ratio, so it is the one
                 that notices a policy scoring well on a small population.

Bars are the mean of two repeats, dots the repeats themselves. The two arms come
from different sessions; the differences drawn here are far outside either arm's
repeat spread, and the README says which ones are not.
"""
import os
import sys

import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXP_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(EXP_ROOT, "analysis_scripts", "request_level"))
RESULTS = os.path.join(EXP_ROOT, "results")

from exp22_fluidserve import CLASSES, attain, goodput_tokens, load_run  # noqa: E402

ARMS = [
    ("FluidServe (current)", "#1f77b4",
     ["260825_0133_exp98r1_fsboth_shift", "260825_0246_exp98r2_fsboth_shift"]),
    ("llm-d", "#8c564b",
     ["260822_2303_exp93r1_llmdslo_shift", "260823_0129_exp93br1_llmdslo_shift"]),
]
GROUPS = CLASSES + ["all"]
PANELS = [
    ("rej", "rejected (%)", "lower is better"),
    ("off", "SLO attainment (%)\nevery arrival in the denominator", "higher is better"),
    ("adm", "SLO attainment (%)\naccepted requests only", "higher is better"),
    ("gp",  "goodput\n(output tokens/s meeting the rule)", "higher is better"),
]


def measure():
    out = {}
    for arm, _, runs in ARMS:
        for rep, run in enumerate(runs, 1):
            d = load_run(os.path.join(RESULTS, run))
            d = d.assign(rel2=d["start_time"] - d["start_time"].min())
            for g in GROUPS:
                s = d if g == "all" else d[d["class"] == g]
                dur = float(s["rel2"].max() - s["rel2"].min()) or 1.0
                out[(arm, rep, g)] = dict(
                    rej=100.0 * s["rejected"].mean(),
                    off=attain(s, "violate_offered"),
                    adm=attain(s, "violate_served"),
                    gp=goodput_tokens(s, dur),
                    n=len(s))
    return out


def draw(m, out_base):
    fig, axes = plt.subplots(1, 4, figsize=(11.4, 3.0))
    x = np.arange(len(GROUPS))
    w = 0.36
    for pi, (key, label, better) in enumerate(PANELS):
        ax = axes[pi]
        for ai, (arm, colour, runs) in enumerate(ARMS):
            vals = [[m[(arm, r, g)][key] for r in (1, 2)] for g in GROUPS]
            mean = [np.mean(v) for v in vals]
            ax.bar(x + (ai - 0.5) * w, mean, width=w, color=colour,
                   label=arm if pi == 0 else None, zorder=2)
            for gi, v in enumerate(vals):          # the repeats themselves
                ax.plot([x[gi] + (ai - 0.5) * w] * 2, v, "o", ms=2.6,
                        color="k", zorder=4)
        ax.set_xticks(x)
        ax.set_xticklabels(["chat", "deep\nresearch", "swe", "all"], fontsize=8)
        ax.set_ylabel(label, fontsize=8)
        ax.set_title(better, fontsize=7.5, color="#555555")
        ax.tick_params(labelsize=8)
        ax.grid(axis="y", ls=":", lw=0.5, alpha=0.6, zorder=0)
        ax.set_axisbelow(True)
        # the difference, written where it is read
        for gi, g in enumerate(GROUPS):
            a = np.mean([m[(ARMS[0][0], r, g)][key] for r in (1, 2)])
            b = np.mean([m[(ARMS[1][0], r, g)][key] for r in (1, 2)])
            top = max(a, b)
            ax.text(x[gi], top * 1.04 + (0.02 * ax.get_ylim()[1]),
                    f"{a - b:+,.0f}" if key == "gp" else f"{a - b:+.1f}",
                    ha="center", va="bottom", fontsize=7,
                    color="#1a7f37" if (a - b > 0) != (key == "rej") else "#b3261e")
        ax.set_ylim(0, ax.get_ylim()[1] * 1.16)
    fig.legend(fontsize=8.5, ncol=2, frameon=False, loc="upper center",
               bbox_to_anchor=(0.5, 1.0))
    fig.suptitle(
        "Mix-shift hour trace, two repeats each. Bars are the repeat mean, dots the repeats. "
        "Numbers above each pair are FluidServe minus llm-d, green where that favours FluidServe. "
        "Rejection and the two attainments are read together: neither ratio alone can tell a policy "
        "that serves everything well from one that refuses most of it.",
        fontsize=7.6, y=0.885, wrap=True)
    fig.tight_layout(rect=(0, 0, 1, 0.80))
    for ext in ("pdf", "png"):
        fig.savefig(f"{out_base}.{ext}", dpi=200)
    print(f"wrote {out_base}.pdf / .png")


def main():
    m = measure()
    rows = [dict(arm=a, repeat=r, group=g, **{k: round(v, 2) for k, v in d.items()})
            for (a, r, g), d in m.items()]
    df = pd.DataFrame(rows).sort_values(["arm", "group", "repeat"])
    csv = os.path.join(HERE, "class_compare.csv")
    df.to_csv(csv, index=False)
    print(f"wrote {csv}")
    draw(m, os.path.join(HERE, "class_compare"))


if __name__ == "__main__":
    main()
