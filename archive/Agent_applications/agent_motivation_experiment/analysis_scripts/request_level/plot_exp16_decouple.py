#!/usr/bin/env python3
"""EXP-16 Phase 3 (option A): separate KV-read term from batch-count term.

Pools per-step decode-only data from short-context (chat) and long-context (swe)
single-workload runs. Because the two workloads sit on different context-length
lines, the (KV, count) plane is populated off-diagonal, so the regression
  interval ~ a*kv_tokens + b*n_decode
can identify a vs b (unlike the mix sweep where KV ∝ count).

Figures:
  exp16_decouple_plane.png   : (n_decode, kv_tokens) scatter colored by workload
        — shows the off-diagonal coverage that makes identification possible.
  exp16_decouple_terms.png   : interval vs KV (both workloads overlaid) and
        interval vs count (both overlaid). If interval tracks KV regardless of
        workload/count, KV is the determinant.
Prints per-workload and pooled regressions with VIF.

Usage:
  plot_exp16_decouple.py --results-dir results --out-dir figs/exp16_decouple
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
from plot_exp16_perstep import load_steps, busy_window  # noqa: E402

PAPER_STYLE = {
    "font.family": "serif", "font.serif": ["DejaVu Serif", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 7.5, "legend.frameon": False,
    "xtick.direction": "in", "ytick.direction": "in",
}
WL_COLOR = {"chat": "#1f77b4", "swe": "#d62728"}


def collect(results_dir):
    """{(workload, rps): decode-only windowed df}."""
    out = {}
    for d in sorted(glob.glob(f"{results_dir}/*exp16dec_*")):
        f = os.path.join(d, "server_metrics", "sched_steps.jsonl")
        if not os.path.exists(f):
            continue
        m = re.search(r"exp16dec_([a-z]+)_rpm_(\d+)", os.path.basename(d))
        if not m:
            continue
        wl, rpm = m.group(1), int(m.group(2))
        w = busy_window(load_steps(f))
        w = w[w["prefill_tokens_step"] == 0]          # decode-only
        if not w.empty:
            out[(wl, rpm // 60)] = w
    return out


def regress(df):
    cols = ["kv_tokens", "n_decode"]
    d = df[["interval_ms"] + cols].dropna()
    d = d[(d[cols] >= 0).all(axis=1)]
    if len(d) < 50:
        return None
    X = d[cols].values.astype(float)
    y = d["interval_ms"].values
    Xd = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(Xd, y, rcond=None)
    yhat = Xd @ beta
    r2 = 1 - np.sum((y - yhat) ** 2) / np.sum((y - y.mean()) ** 2)
    vif = {}
    for i, c in enumerate(cols):
        oth = np.column_stack([np.ones(len(X))] +
                              [X[:, j] for j in range(len(cols)) if j != i])
        b2, *_ = np.linalg.lstsq(oth, X[:, i], rcond=None)
        r = X[:, i] - oth @ b2
        sst = np.sum((X[:, i] - X[:, i].mean()) ** 2)
        r2i = 1 - np.sum(r ** 2) / sst if sst > 0 else 0.0
        vif[c] = 1 / (1 - r2i) if r2i < 1 else float("inf")
    return {"a_ms_per_Mtok": beta[1] * 1e6, "b_ms_per_req": beta[2],
            "c0": beta[0], "r2": r2, "vif": vif, "n": len(d)}


def _binned(ax, x, y, color, label, nb=12):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 10:
        return
    ax.scatter(d["x"], d["y"], s=3, alpha=0.10, color=color)
    q = pd.qcut(d["x"], min(nb, d["x"].nunique()), duplicates="drop")
    g = d.groupby(q, observed=True).agg(x=("x", "mean"), y=("y", "median"))
    ax.plot(g["x"], g["y"], "o-", color=color, label=label, zorder=5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    runs = collect(a.results_dir)
    if not runs:
        raise SystemExit("no exp16dec runs found")
    print("conditions:", list(runs))
    by_wl = {}
    for (wl, rps), w in runs.items():
        by_wl.setdefault(wl, []).append(w)
    pooled = {wl: pd.concat(v, ignore_index=True) for wl, v in by_wl.items()}
    allp = pd.concat(list(pooled.values()), ignore_index=True)

    # regressions
    print("\n== regression interval ~ a*kv + b*count ==")
    for name, df in list(pooled.items()) + [("POOLED", allp)]:
        r = regress(df)
        if r:
            print(f"{name:>7}: n={r['n']:>6} R2={r['r2']:.3f}  "
                  f"a={r['a_ms_per_Mtok']:.1f} ms/Mtok  b={r['b_ms_per_req']:.4f} ms/req  "
                  f"VIF kv={r['vif']['kv_tokens']:.1f} cnt={r['vif']['n_decode']:.1f}")

    # figure 1: coverage plane
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(5, 3.8))
        for wl, df in pooled.items():
            ax.scatter(df["n_decode"], df["kv_tokens"] / 1e6, s=4, alpha=0.15,
                       color=WL_COLOR.get(wl, "#555"), label=wl)
        ax.set_xlabel("decode batch size (count)")
        ax.set_ylabel("batch KV (Mtok)")
        ax.set_title("(count, KV) coverage — off-diagonal => identifiable")
        ax.legend()
        ax.grid(ls=":", lw=0.7, alpha=0.6)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp16_decouple_plane.png")
        fig.savefig(p, dpi=300); print("wrote:", p)

    # figure 2: interval vs KV and vs count, both workloads
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 2, figsize=(9.5, 3.6))
        for wl, df in pooled.items():
            _binned(ax[0], df["kv_tokens"] / 1e6, df["interval_ms"],
                    WL_COLOR.get(wl, "#555"), wl)
            _binned(ax[1], df["n_decode"], df["interval_ms"],
                    WL_COLOR.get(wl, "#555"), wl)
        ax[0].set_xlabel("batch KV (Mtok)"); ax[0].set_ylabel("interval (ms)")
        ax[0].set_title("vs KV — overlap => KV determines")
        ax[1].set_xlabel("decode batch size"); ax[1].set_ylabel("interval (ms)")
        ax[1].set_title("vs count — offset at matched count => not count")
        for x in ax:
            x.grid(ls=":", lw=0.7, alpha=0.6); x.legend()
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp16_decouple_terms.png")
        fig.savefig(p, dpi=300); print("wrote:", p)


if __name__ == "__main__":
    main()
