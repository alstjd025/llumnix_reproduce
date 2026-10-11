#!/usr/bin/env python3
"""EXP-08 overlay: full rate sweep at theta=0.6 vs no-admission baselines.

Same-model (Llama-3.1-70B) comparison:
  - EXP-08 curve: 11 rates, theta=0.6 — offered attainment (rejects =
    violations), admitted attainment, rejection rate (steady window).
  - EXP-07 theta=0 points (50/60/90 req/s): no-admission baseline.
  - EXP-07 theta=0.6 points: cross-run reproducibility check.

Steady window [60s, dur-20s]; SLO TTFT<=5s & meanTBT<=50ms.
Output: <out>/exp08_overlay_vs_rate.png + exp08_summary.csv
"""

import argparse
import glob
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import importlib.util as _ilu
_spec = _ilu.spec_from_file_location(
    "exp07mod", os.path.join(os.path.dirname(__file__), "plot_exp07_theta.py"))
_exp07 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_exp07)
condition_stats = _exp07.condition_stats

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 8, "legend.frameon": False,
         "lines.linewidth": 1.4}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp08_kvadm_th0600_rpm_*")
    ap.add_argument("--exp07-summary",
                    default="results/aggregate_analysis/exp07/exp07_theta_summary.csv")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp08")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    rows = []
    for d in sorted(glob.glob(args.glob), key=lambda x: int(x.split("rpm_")[1])):
        rpm = int(d.split("rpm_")[1])
        s = condition_stats(d)
        s.update(rate=rpm / 60.0)
        rows.append(s)
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(args.out_dir, "exp08_summary.csv"), index=False)

    b = pd.read_csv(args.exp07_summary)
    off = b[b["theta"] == 0.0].sort_values("rate")       # no admission
    th6 = b[b["theta"] == 0.6].sort_values("rate")       # exp07 repro points

    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(figsize=(7.6, 4.8))
        x = t["rate"]
        ax.plot(x, t["attain_offered"], "D-", color="#1f77b4",
                label="θ=0.6 — SLO attainment (offered; rejects = violations)")
        ax.plot(x, t["attain_admitted"], "o--", color="#1f77b4", alpha=0.45,
                label="θ=0.6 — SLO attainment (admitted only)")
        ax.plot(x, t["reject_pct"], "x:", color="#ff7f0e", label="θ=0.6 — rejection rate")
        ax.plot(off["rate"], off["attain_offered"], "s", color="#d62728", ms=8,
                label="no admission (EXP-07 θ=off)")
        ax.plot(th6["rate"], th6["attain_offered"], "P", color="#2ca02c", ms=8,
                label="EXP-07 θ=0.6 (repro check)")
        # capacity-clamp reference: min(1, C/rate) with C from high-load goods
        hi = t[t["rate"] >= 70]
        C = float((hi["attain_offered"] / 100.0 * hi["rate"]).mean())
        xs = np.linspace(x.min(), x.max(), 200)
        ax.plot(xs, 100 * np.minimum(1, C / xs), color="0.6", ls=":",
                label=f"capacity clamp min(1, {C:.0f}/rate)")
        ax.set_xticks(x)
        ax.set_xlabel("offered rate (req/s)")
        ax.set_ylabel("% of steady-window requests")
        ax.set_ylim(-3, 105)
        ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
        h, l = ax.get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.99), ncol=2)
        fig.suptitle("EXP-08 — full rate sweep at KV-admission θ=0.6 vs no admission\n"
                     "(chat, Llama-3.1-70B, steady window)", y=1.16)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp08_overlay_vs_rate.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)
        print(f"capacity clamp C = {C:.1f} req/s (mean good-throughput at 70-100)")


if __name__ == "__main__":
    main()
