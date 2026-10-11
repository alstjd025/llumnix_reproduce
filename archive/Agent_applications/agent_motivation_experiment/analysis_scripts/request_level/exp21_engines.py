"""EXP-21 — per-engine view of what tier partitioning actually does.

Fleet attainment hides the whole mechanism. PolyServe deliberately makes the
engines UNEQUAL: it confines each workload class to the servers assigned to its
SLO tier, so one engine ends up carrying the heavy class and another sits nearly
idle. Stock load-balance does the opposite and spreads everything evenly. These
figures show that difference directly, per engine.

Two figures:

  exp21_engine_composition.png  which classes landed on which engine, per arm.
                                This is the partition made visible.
  exp21_engine_load.png         what the partition costs each engine: batch
                                depth, KV occupancy, token throughput and
                                inter-token latency.

Usage:
  python analysis_scripts/request_level/exp21_engines.py \
      --runs results/*exp21_loadbalance_mixA_rpm_600 results/*exp21_polyserve_mixA_rpm_600 \
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
# Named workload classes keep one colour across every figure in the project.
CLASS_COLOR = {"chat": "#1f77b4", "deepresearch": "#2ca02c", "swe": "#d62728"}
CLASSES = ("chat", "deepresearch", "swe")
# Arms are distinguished by hatch as well as shade, so the bars stay readable
# in greyscale and to colour-blind readers.
ARM_HATCH = {"loadbalance": "", "polyserve": "//"}

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import class_of  # noqa: E402


def arm_of(run_dir):
    m = re.search(r"exp21_([a-z]+)_mixA", os.path.basename(run_dir))
    return m.group(1) if m else os.path.basename(run_dir)


def composition(run_dir):
    """Requests per (engine, class), from the per-request engine map."""
    csv_path = os.path.join(run_dir, "analysis", "request_engine.csv")
    if not os.path.exists(csv_path):
        return None
    df = pd.read_csv(csv_path)
    if df.empty:
        return None
    # A migrated request has two homes; attribute only unambiguous ones so the
    # partition is not blurred by requests that moved after dispatch.
    if "migrated" in df:
        df = df[~df["migrated"].astype(bool)]
    df["class"] = df["task_id"].map(class_of)
    df["engine_port"] = df["engine_port"].astype("Int64")
    return (df.groupby(["engine_port", "class"]).size()
              .unstack(fill_value=0).reindex(columns=list(CLASSES), fill_value=0))


def engine_stats(run_dir):
    """Per-engine load and latency over the steady part of the run.

    vLLM's counters are cumulative, so rates come from first/last differences;
    gauges are summarised by percentile. The first 20% of samples are dropped so
    warm-up does not drag the averages.
    """
    rows = {}
    for f in sorted(glob.glob(os.path.join(run_dir, "server_metrics", "engine_*.jsonl"))):
        port = int(re.search(r"engine_(\d+)", f).group(1))
        recs = []
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if isinstance(r, dict) and r.get("t") is not None:
                    recs.append(r)
        if len(recs) < 5:
            continue
        recs = recs[int(len(recs) * 0.2):]

        def series(sub):
            out = []
            for r in recs:
                for k, v in r.items():
                    if sub in k and isinstance(v, (int, float)):
                        out.append(float(v))
                        break
            return np.array(out, dtype=float)

        def rate(sub):
            s = series(sub)
            span = recs[-1]["t"] - recs[0]["t"]
            return (s[-1] - s[0]) / span if len(s) >= 2 and span > 0 else np.nan

        def mean_ratio(sum_sub, cnt_sub):
            s, c = series(sum_sub), series(cnt_sub)
            if len(s) < 2 or len(c) < 2 or (c[-1] - c[0]) <= 0:
                return np.nan
            return (s[-1] - s[0]) / (c[-1] - c[0])

        running, kv = series("num_requests_running"), series("kv_cache_usage_perc")
        rows[port] = {
            "running_p90": float(np.percentile(running, 90)) if len(running) else np.nan,
            "waiting_p90": float(np.percentile(series("num_requests_waiting"), 90))
                           if len(series("num_requests_waiting")) else np.nan,
            "kv_p90": float(np.percentile(kv, 90)) if len(kv) else np.nan,
            "gen_tok_per_s": rate("generation_tokens_total"),
            "prompt_tok_per_s": rate("prompt_tokens_total"),
            "mean_itl_ms": 1000.0 * mean_ratio("inter_token_latency_seconds_sum",
                                               "inter_token_latency_seconds_count"),
            "mean_ttft_s": mean_ratio("time_to_first_token_seconds_sum",
                                      "time_to_first_token_seconds_count"),
        }
    return pd.DataFrame(rows).T.sort_index() if rows else None


def fig_composition(data, out_dir):
    arms = list(data)
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(1, len(arms), figsize=(3.6 * len(arms), 3.3),
                                 sharey=True)
        axes = np.atleast_1d(axes)
        for ax, arm in zip(axes, arms):
            comp = data[arm]["composition"]
            if comp is None:
                ax.set_title(f"{arm} (no engine map)")
                continue
            ports = list(comp.index)
            x = np.arange(len(ports))
            bottom = np.zeros(len(ports))
            for c in CLASSES:
                vals = comp[c].to_numpy(dtype=float)
                ax.bar(x, vals, bottom=bottom, color=CLASS_COLOR[c], label=c,
                       edgecolor="white", linewidth=0.5)
                bottom += vals
            ax.set_xticks(x)
            ax.set_xticklabels([str(p) for p in ports])
            ax.set_xlabel("Engine (api port)")
            ax.set_title(arm, pad=4)
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            ax.set_axisbelow(True)
        axes[0].set_ylabel("Requests dispatched")
        # Shared y-axis: give the tallest stack headroom so it is not clipped.
        tallest = max((d["composition"].sum(axis=1).max()
                       for d in data.values() if d["composition"] is not None),
                      default=1)
        axes[0].set_ylim(0, tallest * 1.12)
        axes[0].legend(loc="lower center", bbox_to_anchor=(len(arms) / 2.0, 1.08),
                       ncol=3)
        p = os.path.join(out_dir, "exp21_engine_composition.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig)
        print("wrote:", p)


def fig_load(data, out_dir):
    # Input tokens and TTFT come first: the tier partition's whole effect is to
    # concentrate the heavy class's prefill on some engines and spare the others,
    # and those two panels show it most directly.
    panels = [("prompt_tok_per_s", "Input tokens / s"),
              ("mean_ttft_s", "Mean TTFT (s)"),
              ("kv_p90", "KV cache usage, p90 (fraction)"),
              ("running_p90", "Batch size, p90 (requests)"),
              ("gen_tok_per_s", "Output tokens / s"),
              ("mean_itl_ms", "Mean inter-token latency (ms)")]
    arms = list(data)
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(2, 3, figsize=(9.6, 5.4), constrained_layout=True)
        axes = axes.ravel()
        ports = sorted({p for a in arms if data[a]["stats"] is not None
                        for p in data[a]["stats"].index})
        x = np.arange(len(ports))
        width = 0.8 / max(len(arms), 1)
        for ax, (col, label) in zip(axes, panels):
            for i, arm in enumerate(arms):
                st = data[arm]["stats"]
                if st is None or col not in st:
                    continue
                vals = [st[col].get(p, np.nan) for p in ports]
                ax.bar(x + i * width - 0.4 + width / 2, vals, width * 0.92,
                       label=arm, hatch=ARM_HATCH.get(arm, ""),
                       color="#8c8c8c" if arm == "loadbalance" else "#1f77b4",
                       edgecolor="white", linewidth=0.6)
            ax.set_xticks(x)
            ax.set_xticklabels([str(p) for p in ports], fontsize=7)
            ax.set_xlabel("Engine")
            ax.set_ylabel(label)
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            ax.set_axisbelow(True)
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, 1.04), ncol=len(arms))
        p = os.path.join(out_dir, "exp21_engine_load.png")
        fig.savefig(p, dpi=300); plt.close(fig)
        print("wrote:", p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    runs = []
    for pattern in a.runs:
        runs.extend(sorted(glob.glob(pattern)) or ([pattern] if os.path.isdir(pattern) else []))
    if not runs:
        sys.exit("no run directories matched")

    data = {}
    for run in runs:
        arm = arm_of(run)
        data[arm] = {"composition": composition(run), "stats": engine_stats(run),
                     "run": run}
        print(f"\n=== {arm}  ({os.path.basename(run)}) ===")
        comp = data[arm]["composition"]
        if comp is not None:
            total = comp.sum(axis=1)
            print("  requests per engine, by class")
            for port, row in comp.iterrows():
                share = " ".join(f"{c}={int(row[c]):>4}" for c in CLASSES)
                print(f"    {port}: {share}   total={int(total[port]):>5}")
        st = data[arm]["stats"]
        if st is not None:
            print("  per-engine load")
            print(st.round(3).to_string().replace("\n", "\n    "))

    fig_composition(data, a.out_dir)
    fig_load(data, a.out_dir)

    combined = []
    for arm, d in data.items():
        if d["stats"] is not None:
            s = d["stats"].copy()
            s.insert(0, "arm", arm)
            combined.append(s)
    if combined:
        csv = os.path.join(a.out_dir, "exp21_engine_stats.csv")
        pd.concat(combined).to_csv(csv, index_label="engine_port")
        print("\nwrote:", csv)


if __name__ == "__main__":
    main()
