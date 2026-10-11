#!/usr/bin/env python3
"""EXP-14 cross-ratio summary: three mixes on one set of axes.

Reads the three mixed-workload sweeps (mixA/mixB/mixC) and produces:
  - exp14_fleet_attainment.png : fleet SLO attainment vs offered rate, one
    line per mix ratio — the "knee shifts with token/cache composition" view.
  - exp14_per_class_<M>.png    : per-class attainment within each mix (do the
    light classes collapse together with the heavy ones? = interference).
  - exp14_summary.csv          : per (mix,rate) attainment/throughput/KV/queue.

SLO rule + window imported from plot_slo_vs_throughput (identical to every
other figure in the arc). Per-class split uses the task_id prefix; no engine
map needed for these (that's plot_per_engine_attainment.py's job).

Usage:
  python exp14_summary.py --results-dir results \
      --out-dir results/aggregate_analysis/exp14_mix
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import served_rows, class_of, attain  # noqa: E402

PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "lines.linewidth": 1.6, "lines.markersize": 5,
}
MIXES = {  # session tag -> (label, color)
    "mixA": ("A  1:1:1", "#1f77b4"),
    "mixB": ("B  6:3:1 (light)", "#2ca02c"),
    "mixC": ("C  1:1:3 (heavy)", "#d62728"),
}
CLASS_COLORS = {"chat": "#1f77b4", "deepresearch": "#ff7f0e", "swe": "#d62728"}


def engine_kv_queue(run_dir):
    kv, wait = [], []
    for f in glob.glob(f"{run_dir}/server_metrics/engine_*.jsonl"):
        rr = [json.loads(l) for l in open(f) if l.strip()]
        last = rr[-1] if rr else {}
        for sub, acc, agg in [("kv_cache_usage_perc", kv, max),
                              ("num_requests_waiting", wait, np.mean)]:
            k = [x for x in last if sub in x]
            if k:
                vals = [r[k[0]] for r in rr if r.get(k[0]) is not None]
                if vals:
                    acc.append(agg(vals))
    return (max(kv) if kv else np.nan), (float(np.sum(wait)) if wait else np.nan)


def collect_mix(results_dir, tag):
    rows = []
    for d in sorted(glob.glob(f"{results_dir}/*exp14_{tag}_rpm_*"),
                    key=lambda x: int(re.search(r"rpm_(\d+)", x).group(1))):
        rate = int(re.search(r"rpm_(\d+)", d).group(1)) / 60
        try:
            sr = served_rows(d)
        except Exception:
            continue
        if sr is None or sr.empty:
            continue
        sr["class"] = sr["task_id"].map(class_of)
        kv, wait = engine_kv_queue(d)
        rec = {"mix": tag, "rate": rate, "n": len(sr),
               "fleet": attain(sr), "kv": kv, "wait": wait}
        for c in ("chat", "deepresearch", "swe"):
            sub = sr[sr["class"] == c]
            rec[f"attain_{c}"] = attain(sub) if len(sub) else np.nan
            rec[f"ttft_{c}"] = (pd.to_numeric(sub["first_token_latency"],
                                              errors="coerce").mean()
                                if len(sub) else np.nan)
        rows.append(rec)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    allrows = []
    for tag in MIXES:
        allrows += collect_mix(a.results_dir, tag)
    if not allrows:
        raise SystemExit("no exp14 mix runs found")
    df = pd.DataFrame(allrows)
    df.to_csv(os.path.join(a.out_dir, "exp14_summary.csv"), index=False)
    print(df[["mix", "rate", "n", "fleet", "attain_chat", "attain_deepresearch",
              "attain_swe", "kv", "wait"]].to_string(index=False))

    # Fleet attainment vs rate, one line per mix
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(5.4, 3.6))
        for tag, (label, color) in MIXES.items():
            sub = df[df["mix"] == tag].sort_values("rate")
            if sub.empty:
                continue
            ax.plot(sub["rate"], sub["fleet"], "-o", color=color, label=label,
                    mec="white", mew=0.5)
        ax.set_xlabel("Offered rate (req/s)")
        ax.set_ylabel("Fleet SLO attainment (%)")
        ax.set_ylim(-3, 105)
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=3)
        ax.set_title("Mix ratio shifts the SLO knee", pad=26)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp14_fleet_attainment.png")
        fig.savefig(p, dpi=300); print("wrote:", p)

    # Per-class attainment within each mix
    for tag, (label, _) in MIXES.items():
        sub = df[df["mix"] == tag].sort_values("rate")
        if sub.empty:
            continue
        with plt.rc_context(PAPER_STYLE):
            fig, ax = plt.subplots(figsize=(5.2, 3.5))
            for c in ("chat", "deepresearch", "swe"):
                ax.plot(sub["rate"], sub[f"attain_{c}"], "-o",
                        color=CLASS_COLORS[c], label=c, mec="white", mew=0.5)
            ax.plot(sub["rate"], sub["fleet"], "--", color="0.4", label="fleet")
            ax.set_xlabel("Offered rate (req/s)")
            ax.set_ylabel("SLO attainment (%)"); ax.set_ylim(-3, 105)
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=4)
            ax.set_title(f"Per-class attainment — mix {label}", pad=26)
            fig.tight_layout()
            p = os.path.join(a.out_dir, f"exp14_per_class_{tag}.png")
            fig.savefig(p, dpi=300); print("wrote:", p)


if __name__ == "__main__":
    main()
