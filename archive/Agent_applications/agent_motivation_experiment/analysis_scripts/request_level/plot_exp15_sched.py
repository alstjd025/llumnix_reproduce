#!/usr/bin/env python3
"""EXP-15 figures: intra-engine scheduling policy (FIFO/EDF/SJF/SRPF).

Tells the central result in one place: the policies change **queueing** by an
order of magnitude but barely move **SLO attainment**, because the binding
constraint in this workload is decode-side (TBT / E2E) rather than admission
order — and draining the queue faster can even hurt, by enlarging the batch.

Figures (into --out-dir):
  exp15_queue_vs_attainment.png : 3 panels x 3 rates, grouped by policy —
        mean chat TTFT, mean waiting queue, fleet SLO attainment.
  exp15_per_class_<rate>.png    : per-class attainment by policy at one rate.

Scoring is the class-differentiated SLO from exp14_per_class_slo (chat
TTFT<=5s & TBT<=50ms, deepresearch <=10s & <=100ms, swe E2E<=SWE_E2E_SLO_S),
over the standard arrival-anchored [60s, 340s] window.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import served_rows, class_of  # noqa: E402
from exp14_per_class_slo import per_class_violate, SLO_RULES  # noqa: E402

PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "lines.linewidth": 1.4, "lines.markersize": 4.5,
}
POLICIES = ["fifo", "edf", "sjf", "srpf"]
POLICY_COLORS = {"fifo": "#7f7f7f", "edf": "#1f77b4",
                 "sjf": "#2ca02c", "srpf": "#d62728"}
CLASS_COLORS = {"chat": "#1f77b4", "deepresearch": "#ff7f0e", "swe": "#d62728"}
RATES = [(1320, 22), (1800, 30), (2280, 38)]


def collect(results_dir):
    rows = []
    for rpm, rate in RATES:
        for pol in POLICIES:
            ds = sorted(glob.glob(f"{results_dir}/*exp15_{pol}_rpm_{rpm}"))
            if not ds:
                continue
            d = ds[-1]
            sr = served_rows(d)
            if sr is None or sr.empty:
                continue
            sr["class"] = sr["task_id"].map(class_of)
            sr["violate_pc"] = per_class_violate(sr)
            att = lambda s: (100.0 * (~s["violate_pc"]).mean()
                             if len(s) else np.nan)
            wait = []
            for f in glob.glob(f"{d}/server_metrics/engine_*.jsonl"):
                rr = [json.loads(l) for l in open(f) if l.strip()]
                k = [x for x in (rr[-1] if rr else {})
                     if "num_requests_waiting" in x]
                if k:
                    v = [r[k[0]] for r in rr if r.get(k[0]) is not None]
                    if v:
                        wait.append(np.mean(v))
            rec = {
                "rate": rate, "policy": pol, "fleet": att(sr),
                "queue": float(np.sum(wait)) if wait else np.nan,
                "ttft_chat": pd.to_numeric(
                    sr[sr["class"] == "chat"]["first_token_latency"],
                    errors="coerce").mean(),
                "tok_s": pd.to_numeric(sr["output_tokens"],
                                       errors="coerce").sum() / 280.0,
            }
            for c in ("chat", "deepresearch", "swe"):
                rec[f"attain_{c}"] = att(sr[sr["class"] == c])
            rows.append(rec)
    return pd.DataFrame(rows)


def _grouped(ax, df, col, ylab, title, log=False):
    rates = sorted(df["rate"].unique())
    x = np.arange(len(rates))
    w = 0.2
    for i, pol in enumerate(POLICIES):
        vals = [df[(df.rate == r) & (df.policy == pol)][col].mean()
                for r in rates]
        ax.bar(x + (i - 1.5) * w, vals, w, label=pol.upper(),
               color=POLICY_COLORS[pol], edgecolor="white", linewidth=0.5)
    ax.set_xticks(x)
    ax.set_xticklabels([f"{r}" for r in rates])
    ax.set_xlabel("Offered rate (req/s)")
    ax.set_ylabel(ylab)
    ax.set_title(title)
    if log:
        ax.set_yscale("log")
    ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    df = collect(a.results_dir)
    if df.empty:
        raise SystemExit("no exp15 runs found")
    df.to_csv(os.path.join(a.out_dir, "exp15_summary.csv"), index=False)
    print(df.to_string(index=False))

    swe_slo = SLO_RULES["swe"]["e2e"]
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(1, 3, figsize=(10.5, 3.4))
        _grouped(axes[0], df, "ttft_chat", "mean TTFT (s)",
                 "chat TTFT — policies work", log=True)
        _grouped(axes[1], df, "queue", "mean waiting requests",
                 "Waiting queue — policies work", log=True)
        _grouped(axes[2], df, "fleet", "SLO attainment (%)",
                 "Fleet goodput — barely moves")
        axes[2].set_ylim(0, 60)
        h, l = axes[0].get_legend_handles_labels()
        fig.legend(h, l, loc="upper center", ncol=4, bbox_to_anchor=(0.5, 1.02))
        fig.suptitle(
            "Intra-engine scheduling changes queueing by 10-19x but not goodput "
            f"(mix A, per-class SLO; swe E2E<={swe_slo:.0f}s)",
            y=1.10, fontsize=8)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp15_queue_vs_attainment.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print("wrote:", p)

    # per-class attainment at each rate
    for rate in sorted(df["rate"].unique()):
        sub = df[df.rate == rate]
        with plt.rc_context(PAPER_STYLE):
            fig, ax = plt.subplots(figsize=(4.6, 3.3))
            x = np.arange(len(POLICIES))
            w = 0.26
            for i, c in enumerate(("chat", "deepresearch", "swe")):
                vals = [sub[sub.policy == p][f"attain_{c}"].mean()
                        for p in POLICIES]
                ax.bar(x + (i - 1) * w, vals, w, label=c,
                       color=CLASS_COLORS[c], edgecolor="white", linewidth=0.5)
            ax.plot(x, [sub[sub.policy == p]["fleet"].mean() for p in POLICIES],
                    "k--o", lw=1.2, ms=4, label="fleet")
            ax.set_xticks(x)
            ax.set_xticklabels([p.upper() for p in POLICIES])
            ax.set_ylabel("SLO attainment (%)")
            ax.set_xlabel("Scheduling policy")
            ax.set_ylim(0, 105)
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=4)
            ax.set_title(f"Per-class attainment @ {rate} req/s", pad=26)
            fig.tight_layout()
            p = os.path.join(a.out_dir, f"exp15_per_class_{rate}.png")
            fig.savefig(p, dpi=300)
            print("wrote:", p)


if __name__ == "__main__":
    main()
