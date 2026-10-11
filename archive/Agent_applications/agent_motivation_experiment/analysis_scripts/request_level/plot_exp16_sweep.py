#!/usr/bin/env python3
"""EXP-16 Phase 2: per-step decode-latency decomposition across a RATE SWEEP.

Phase 1 (@30 req/s) was saturated, so KV and batch-count were collinear and the
terms could not be separated. Here we sweep rate to spread steps across the KV
axis and test whether the decomposition holds:

  * KV COLLAPSE  — decode-only interval vs batch-KV, one curve per rate. If the
    curves fall on ONE line, step time is a function of KV (not of rate or
    batch-count independently): the cleanest evidence the KV term is THE decode
    determinant.
  * vs RATE      — saturation (KV occupancy), T_schedule (stays ~2ms?), median
    interval, and the full-prefill-chunk tail, each as a function of offered rate.
  * REGRESSION   — per-rate and pooled interval ~ kv + n_decode + prefill, with
    R^2 and VIF, to see whether spreading rates reduces collinearity enough to
    identify coefficients.

Usage:
  plot_exp16_sweep.py --results-dir results --out-dir figs/exp16_sweep
"""
import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_exp16_perstep import load_steps, busy_window, regression  # noqa: E402

PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 7.5, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "lines.linewidth": 1.4, "lines.markersize": 4.0,
}
KV_BINS = np.array([0, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0, 6.0])


def _pct(s, q):
    s = pd.to_numeric(s, errors="coerce").dropna().values
    return float(np.percentile(s, q)) if len(s) else float("nan")


def collect(results_dir):
    """Return {rate_rps: windowed_df} for every exp16 rate run found."""
    out = {}
    for d in sorted(glob.glob(f"{results_dir}/*exp16_instr_rpm_*")):
        f = os.path.join(d, "server_metrics", "sched_steps.jsonl")
        if not os.path.exists(f):
            continue
        m = re.search(r"exp16_instr_rpm_(\d+)", os.path.basename(d))
        if not m:
            continue
        rps = int(m.group(1)) // 60
        w = busy_window(load_steps(f))
        if not w.empty:
            out[rps] = w
    return dict(sorted(out.items()))


def fig_kv_collapse(runs, out):
    cmap = plt.cm.viridis(np.linspace(0, 0.9, len(runs)))
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(5.4, 3.8))
        for (rps, w), c in zip(runs.items(), cmap):
            dec = w[w["prefill_tokens_step"] == 0].copy()
            dec["kvM"] = dec["kv_tokens"] / 1e6
            g = dec.groupby(pd.cut(dec["kvM"], KV_BINS), observed=True)
            x = g["kvM"].mean(); y = g["interval_ms"].median()
            ax.plot(x, y, "o-", color=c, label=f"{rps} req/s")
        ax.set_xlabel("batch KV occupancy (Mtok)")
        ax.set_ylabel("decode-only step interval, p50 (ms)")
        ax.set_title("Decode step time collapses on the KV axis\n"
                     "(prefill-free steps; curves overlap => KV is the determinant)")
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(title="offered rate", ncol=2)
        fig.tight_layout()
        p = os.path.join(out, "exp16_kv_collapse.png")
        fig.savefig(p, dpi=300); print("wrote:", p)


def fig_vs_rate(runs, out):
    rates = list(runs)
    kv_p50 = [_pct(w["kv_tokens"] / 1e6, 50) for w in runs.values()]
    kv_p90 = [_pct(w["kv_tokens"] / 1e6, 90) for w in runs.values()]
    ts_p50 = [_pct(w["t_schedule_us"] / 1e3, 50) for w in runs.values()]
    ts_p99 = [_pct(w["t_schedule_us"] / 1e3, 99) for w in runs.values()]
    iv_p50 = [_pct(w["interval_ms"], 50) for w in runs.values()]
    iv_p99 = [_pct(w["interval_ms"], 99) for w in runs.values()]
    fc_p90 = [_pct(w[w["prefill_tokens_step"] >= 7500]["interval_ms"], 90)
              for w in runs.values()]
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 4, figsize=(13.5, 3.2))
        ax[0].plot(rates, kv_p50, "o-", label="p50")
        ax[0].plot(rates, kv_p90, "s--", color="#9467bd", label="p90")
        ax[0].set_ylabel("batch KV (Mtok)"); ax[0].set_title("Saturation")
        ax[0].legend()
        ax[1].plot(rates, ts_p50, "o-", color="#7f7f7f", label="p50")
        ax[1].plot(rates, ts_p99, "s--", color="#7f7f7f", label="p99")
        ax[1].set_ylabel("T_schedule (ms)")
        ax[1].set_title("Scheduler CPU time"); ax[1].set_ylim(bottom=0)
        ax[1].legend()
        ax[2].plot(rates, iv_p50, "o-", label="p50")
        ax[2].plot(rates, iv_p99, "s--", color="#d62728", label="p99")
        ax[2].set_ylabel("step interval (ms)")
        ax[2].set_title("Step ITL"); ax[2].legend()
        ax[3].plot(rates, fc_p90, "o-", color="#d62728")
        ax[3].set_ylabel("interval p90 (ms)")
        ax[3].set_title("Full-chunk prefill steps")
        for a in ax:
            a.set_xlabel("offered rate (req/s)")
            a.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        fig.tight_layout()
        p = os.path.join(out, "exp16_vs_rate.png")
        fig.savefig(p, dpi=300); print("wrote:", p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    runs = collect(a.results_dir)
    if not runs:
        raise SystemExit("no exp16 rate runs found")
    print("rates found:", list(runs))

    fig_kv_collapse(runs, a.out_dir)
    fig_vs_rate(runs, a.out_dir)

    # per-rate + pooled regression
    lines = ["EXP-16 rate-sweep decomposition", "=" * 60]
    rows = []
    for rps, w in runs.items():
        r = regression(w)
        if not r:
            continue
        c = r["coef"]
        rows.append((rps, len(w), r["r2"],
                     c["a_ms_per_Mtok_kv"], c["b_ms_per_decode_req"],
                     c["c_ms_per_Ktok_prefill"], max(r["vif"].values())))
    lines.append(f"{'rate':>5} {'steps':>7} {'R2':>6} {'a_KV':>8} "
                 f"{'b_batch':>9} {'c_prefill':>10} {'maxVIF':>7}")
    for rps, n, r2, aK, bB, cP, vif in rows:
        lines.append(f"{rps:>5} {n:>7} {r2:>6.3f} {aK:>8.1f} {bB:>9.4f} "
                     f"{cP:>10.2f} {vif:>7.1f}")
    pooled = regression(pd.concat(list(runs.values()), ignore_index=True))
    if pooled:
        c = pooled["coef"]
        lines += ["", "POOLED (all rates):",
                  f"  n={pooled['n']}  R^2={pooled['r2']:.3f}",
                  f"  a (KV)      = {c['a_ms_per_Mtok_kv']:.2f} ms/Mtok",
                  f"  b (batch)   = {c['b_ms_per_decode_req']:.4f} ms/req",
                  f"  c (prefill) = {c['c_ms_per_Ktok_prefill']:.2f} ms/Ktok",
                  f"  VIF         = " +
                  ", ".join(f"{k}={v:.1f}" for k, v in pooled["vif"].items())]
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(a.out_dir, "exp16_sweep_summary.txt"), "w") as f:
        f.write(txt + "\n")


if __name__ == "__main__":
    main()
