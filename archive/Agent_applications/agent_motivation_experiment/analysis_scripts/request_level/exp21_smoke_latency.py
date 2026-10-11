"""EXP-21 — the concrete latency metrics behind the tier partition, per class.

A rate sweep gives one attainment number per condition, which is exactly what
hides PolyServe's mechanism: at a rate where nothing violates its SLO, both arms
read 100% while the engines underneath are running completely different loads.
These figures show the raw distributions and the time course instead, against
the same per-class SLO thresholds the attainment rules use.

  exp21_smoke_latency_cdf.png    per-class TTFT and mean-TBT CDFs, both arms,
                                 with each class's SLO drawn in
  exp21_smoke_timeseries.png     per-engine KV occupancy and batch depth over
                                 the run: the partition forming and holding

Class is colour, arm is linestyle, so a class can be followed across arms.

Usage:
  SWE_E2E_SLO_S=30 python analysis_scripts/request_level/exp21_smoke_latency.py \
      --runs 'results/*exp21_loadbalance_mixA_rpm_600' 'results/*exp21_polyserve_mixA_rpm_600' \
      --out-dir results/aggregate_analysis/exp21_polyserve
"""

import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "xtick.major.size": 3.0, "ytick.major.size": 3.0,
    "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "lines.linewidth": 1.4, "lines.markersize": 4.5,
}
CLASS_COLOR = {"chat": "#1f77b4", "deepresearch": "#2ca02c", "swe": "#d62728"}
CLASSES = ("chat", "deepresearch", "swe")
ARM_LS = {"loadbalance": "--", "polyserve": "-"}
ENGINE_COLOR = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import served_rows, class_of  # noqa: E402
from exp14_per_class_slo import SLO_RULES, per_class_violate  # noqa: E402


def arm_of(run_dir):
    m = re.search(r"exp21_([a-z]+)_mixA", os.path.basename(run_dir))
    return m.group(1) if m else os.path.basename(run_dir)


def cdf(ax, values, **kw):
    v = np.sort(pd.to_numeric(values, errors="coerce").dropna().to_numpy())
    if len(v) == 0:
        return
    ax.plot(v, np.arange(1, len(v) + 1) / len(v) * 100.0, **kw)


def fig_cdf(rows_by_arm, out_dir):
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(1, 2, figsize=(8.0, 3.4), constrained_layout=True)
        for arm, sr in rows_by_arm.items():
            for c in CLASSES:
                sub = sr[sr["class"] == c]
                if sub.empty:
                    continue
                style = dict(color=CLASS_COLOR[c], ls=ARM_LS.get(arm, "-"))
                cdf(axes[0], sub["first_token_latency"],
                    label=f"{c} — {arm}", **style)
                cdf(axes[1], sub["tbt_mean_ms"], **style)
        # Each class carries its own SLO, so draw all of them rather than one
        # global line; swe is scored on end-to-end latency and has no TTFT/TBT
        # threshold to draw.
        for c in CLASSES:
            rule = SLO_RULES[c]
            if "ttft" in rule:
                axes[0].axvline(rule["ttft"], color=CLASS_COLOR[c], lw=0.8,
                                ls=":", alpha=0.8)
                axes[1].axvline(rule["tbt"], color=CLASS_COLOR[c], lw=0.8,
                                ls=":", alpha=0.8)
        axes[0].set_xlabel("TTFT (s)")
        axes[1].set_xlabel("Mean time-between-tokens (ms)")
        for ax in axes:
            ax.set_ylabel("Requests (%)")
            ax.set_xscale("log")
            ax.set_ylim(0, 101)
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            ax.set_axisbelow(True)
        axes[0].legend(loc="lower center", bbox_to_anchor=(1.05, 1.02), ncol=3,
                       fontsize=7)
        p = os.path.join(out_dir, "exp21_smoke_latency_cdf.png")
        fig.savefig(p, dpi=300); plt.close(fig); print("wrote:", p)


def engine_timeseries(run_dir):
    out = {}
    for f in sorted(glob.glob(os.path.join(run_dir, "server_metrics", "engine_*.jsonl"))):
        port = int(re.search(r"engine_(\d+)", f).group(1))
        t, kv, run = [], [], []
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(r, dict) or r.get("t") is None:
                    continue
                g = {"kv": None, "run": None}
                for k, v in r.items():
                    if not isinstance(v, (int, float)):
                        continue
                    if "kv_cache_usage_perc" in k:
                        g["kv"] = float(v)
                    elif "num_requests_running" in k:
                        g["run"] = float(v)
                if g["kv"] is not None and g["run"] is not None:
                    t.append(r["t"]); kv.append(g["kv"]); run.append(g["run"])
        if t:
            t0 = t[0]
            out[port] = (np.array(t) - t0, np.array(kv), np.array(run))
    return out


def fig_timeseries(series_by_arm, out_dir):
    arms = list(series_by_arm)
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(2, len(arms), figsize=(4.4 * len(arms), 5.0),
                                 constrained_layout=True, sharex=True)
        axes = np.atleast_2d(axes)
        if axes.shape[0] == 1:
            axes = axes.T
        for col, arm in enumerate(arms):
            for i, (port, (t, kv, run)) in enumerate(sorted(series_by_arm[arm].items())):
                colr = ENGINE_COLOR[i % len(ENGINE_COLOR)]
                axes[0, col].plot(t / 60.0, kv, color=colr, label=str(port))
                axes[1, col].plot(t / 60.0, run, color=colr)
            axes[0, col].set_title(arm, pad=4)
            axes[1, col].set_xlabel("Time in run (min)")
            for row in (0, 1):
                axes[row, col].grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
                axes[row, col].set_axisbelow(True)
        axes[0, 0].set_ylabel("KV cache usage (fraction)")
        axes[1, 0].set_ylabel("Running requests")
        axes[0, 0].legend(loc="lower center", bbox_to_anchor=(len(arms) / 2.0, 1.10),
                          ncol=4, title="engine", title_fontsize=7)
        p = os.path.join(out_dir, "exp21_smoke_timeseries.png")
        fig.savefig(p, dpi=300); plt.close(fig); print("wrote:", p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    runs = []
    for pattern in a.runs:
        runs.extend(sorted(glob.glob(pattern)) or
                    ([pattern] if os.path.isdir(pattern) else []))
    if not runs:
        sys.exit("no run directories matched")

    rows_by_arm, series_by_arm = {}, {}
    print(f"SLO: chat TTFT<={SLO_RULES['chat']['ttft']}s & TBT<={SLO_RULES['chat']['tbt']}ms"
          f" | deepresearch TTFT<={SLO_RULES['deepresearch']['ttft']}s"
          f" & TBT<={SLO_RULES['deepresearch']['tbt']}ms"
          f" | swe E2E<={SLO_RULES['swe']['e2e']}s")
    for run in runs:
        arm = arm_of(run)
        sr = served_rows(run)
        if sr is None or sr.empty:
            print(f"  {arm}: no served rows"); continue
        sr["class"] = sr["task_id"].map(class_of)
        sr["violate_pc"] = per_class_violate(sr)
        rows_by_arm[arm] = sr
        series_by_arm[arm] = engine_timeseries(run)

        print(f"\n=== {arm} ({os.path.basename(run)}) ===")
        print(f"  {'class':<14}{'n':>6}{'TTFT p50':>10}{'p95':>9}"
              f"{'TBT p50':>10}{'p95':>9}{'E2E p50':>10}{'p95':>9}")
        for c in CLASSES:
            s = sr[sr["class"] == c]
            if s.empty:
                continue
            def q(col, p):
                return pd.to_numeric(s[col], errors="coerce").quantile(p)
            print(f"  {c:<14}{len(s):>6}"
                  f"{q('first_token_latency',.50):>10.3f}{q('first_token_latency',.95):>9.3f}"
                  f"{q('tbt_mean_ms',.50):>10.1f}{q('tbt_mean_ms',.95):>9.1f}"
                  f"{q('latency',.50):>10.2f}{q('latency',.95):>9.2f}")

    if rows_by_arm:
        fig_cdf(rows_by_arm, a.out_dir)
    if any(series_by_arm.values()):
        fig_timeseries(series_by_arm, a.out_dir)


if __name__ == "__main__":
    main()
