#!/usr/bin/env python3
"""EXP-48 figures: the same policy and the same binary on two length profiles.

The two series are not two policies. They are the identical scheduler binary
(`809b823b`) running the identical arm, differing only in `classes[]` of
`deploy/profiling/llama31-70b-b200-tp2/fluidserve.json` -- the deep research
class's output-length distribution, which said mean 282 for a class producing
985. That is why the label is the profile date rather than an arm name.

Three figures, the standard set for a rate sweep:

  attainment   solid = admitted denominator, dotted = offered. The gap between
               the two is the rejection cost, which has to be visible because
               the admitted view alone rewards refusing the requests that were
               going to miss.
  goodput      output tokens per second from requests that met their SLO, drawn
               separately rather than on a second axis so both can be read.
  latency      chat's median inter-token latency against its 50 ms budget, which
               is the quantity the 60 req/s collapse was always about.

Error bars are min..max over repeats. A point with one repeat gets no bar and
the caption says so.

  python3 exp48_figures.py --old 'results/*exp47r?_fluidserve_m1_rpm_*' \\
                           --new 'results/*exp48r?_fluidserve_m1_rpm_*' \\
                           --out results/aggregate_analysis/exp48
"""
import argparse
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
from exp22_fluidserve import load_run, attain, PAPER_STYLE  # noqa: E402

SERIES = {
    "old": dict(label="07-26 profile (dr mean 282)",
                color="#7f7f7f", marker="s"),
    "new": dict(label="corrected (dr mean 985)",
                color="#1f77b4", marker="o"),
}


def collect(pattern, key):
    rows = []
    for d in sorted(glob.glob(pattern)):
        m = re.search(r"_rpm_(\d+)", os.path.basename(d))
        if not m:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        span = r["rel"].max() - r["rel"].min()
        ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
        adm = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])]
        ch = adm[adm["class"] == "chat"]
        tok = pd.to_numeric(ok.get("output_tokens"), errors="coerce").fillna(0).sum()
        rows.append(dict(
            series=key, rate=int(m.group(1)) / 60.0,
            off=attain(r, "violate_offered"), adm=attain(r, "violate_served"),
            rej=100.0 * r["rejected"].mean(), goodput=tok / span,
            chat_itl=pd.to_numeric(ch["itl_ms"], errors="coerce").median(),
            run=os.path.basename(d)))
    return rows


def band(ax, df, key, col, style, dotted=False):
    s = df[df.series == key].groupby("rate")[col]
    x = np.array(sorted(s.groups))
    mean = np.array([s.get_group(v).mean() for v in x])
    lo = np.array([s.get_group(v).min() for v in x])
    hi = np.array([s.get_group(v).max() for v in x])
    ax.errorbar(x, mean, yerr=[mean - lo, hi - mean],
                color=style["color"], marker=style["marker"],
                linestyle=":" if dotted else "-",
                markerfacecolor="none" if dotted else style["color"],
                markeredgewidth=0.9, capsize=2, elinewidth=0.8,
                label=None if dotted else style["label"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    df = pd.DataFrame(collect(a.old, "old") + collect(a.new, "new"))
    if df.empty:
        sys.exit("no runs matched")
    os.makedirs(a.out, exist_ok=True)
    print(df[["series", "rate", "off", "adm", "rej", "goodput", "chat_itl", "run"]]
          .sort_values(["rate", "series"]).to_string(index=False))
    n_per = df.groupby(["series", "rate"]).size()
    single = [f"{k[0]} at {k[1]:.0f} req/s" for k, v in n_per.items() if v < 2]

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(3.3, 2.5))
        for k, st in SERIES.items():
            if (df.series == k).any():
                band(ax, df, k, "adm", st)
                band(ax, df, k, "off", st, dotted=True)
        ax.set_xlabel("request rate (req/s)")
        ax.set_ylabel("SLO attainment (%), per request")
        ax.set_ylim(0, 105)
        ax.set_xticks(sorted(df.rate.unique()))
        ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
                  columnspacing=1.0, handletextpad=0.4)
        fig.savefig(os.path.join(a.out, "attainment.png"), dpi=300,
                    bbox_inches="tight")
        plt.close(fig)

        for col, ylab, fname in (
                ("goodput", "goodput (output tokens/s)", "goodput.png"),
                ("chat_itl", "chat median inter-token latency (ms)", "chat_itl.png")):
            fig, ax = plt.subplots(figsize=(3.3, 2.2))
            for k, st in SERIES.items():
                if (df.series == k).any():
                    band(ax, df, k, col, st)
            if col == "chat_itl":
                ax.axhline(50, color="#d62728", linestyle="--", linewidth=0.9)
                ax.text(df.rate.min(), 50.6, "chat budget 50 ms",
                        color="#d62728", fontsize=7)
            ax.set_xlabel("request rate (req/s)")
            ax.set_ylabel(ylab)
            ax.set_xticks(sorted(df.rate.unique()))
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
                      columnspacing=1.0, handletextpad=0.4)
            fig.savefig(os.path.join(a.out, fname), dpi=300, bbox_inches="tight")
            plt.close(fig)

    print(f"\nwrote 3 figures to {a.out}")
    print("solid = admitted denominator, dotted = offered; bars are min..max "
          "over repeats")
    if single:
        print("ONE REPEAT, no bar drawn: " + ", ".join(single))
    return 0


if __name__ == "__main__":
    sys.exit(main())
