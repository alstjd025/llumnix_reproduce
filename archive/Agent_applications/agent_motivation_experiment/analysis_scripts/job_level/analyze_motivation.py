#!/usr/bin/env python3
"""
Post-hoc analysis script for the motivation experiment.

Reads experiment results from CSV files and generates motivation figures
illustrating the gap between throughput and goodput under load.

Usage:
    python analysis_scripts/analyze_motivation.py \
        --results-dir ./results \
        --baseline-dir ./results/baseline \
        --output-dir ./figures
"""

import argparse
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def discover_runs(results_dir: str) -> list[dict]:
    """Scan *results_dir* for subdirectories that contain metrics.csv.

    Returns a sorted list of dicts with keys:
        path, concurrency_level, rpm, metrics_csv, config_json
    """
    results_dir = Path(results_dir)
    if not results_dir.is_dir():
        print(f"[ERROR] results-dir does not exist: {results_dir}", file=sys.stderr)
        sys.exit(1)

    runs: list[dict] = []
    for subdir in sorted(results_dir.iterdir()):
        metrics_csv = subdir / "metrics.csv"
        config_json = subdir / "run_config.json"
        if not metrics_csv.is_file():
            continue

        concurrency_level = None
        rpm = None
        lam = None
        mode = None
        if config_json.is_file():
            with open(config_json) as f:
                cfg = json.load(f)
            mode = cfg.get("mode")
            concurrency_level = cfg.get("concurrency", cfg.get("concurrency_level"))
            rpm = cfg.get("request_rate_per_min")
            lam = cfg.get("lambda")

        runs.append({
            "path": str(subdir),
            "mode": mode,
            "concurrency_level": concurrency_level,
            "rpm": rpm,
            "lambda": lam,
            "metrics_csv": str(metrics_csv),
            "config_json": str(config_json) if config_json.is_file() else None,
        })

    # Sort by concurrency_level (fallback: rpm, then path)
    def _sort_key(r):
        cl = r["concurrency_level"]
        rpm = r["rpm"]
        lam = r["lambda"]
        if cl is not None:
            return (0, cl, 0, r["path"])
        if rpm is not None:
            return (1, 0, rpm, r["path"])
        if lam is not None:
            return (2, 0, lam, r["path"])
        return (3, 0, 0, r["path"])

    runs.sort(key=_sort_key)
    return runs


def load_metrics(csv_path: str) -> pd.DataFrame:
    """Load a metrics.csv and return a DataFrame (empty DataFrame on error)."""
    try:
        df = pd.read_csv(csv_path, skipinitialspace=True)
        # Strip whitespace from column names
        df.columns = df.columns.str.strip()
        for col in [
            "start_time", "end_time", "latency", "input_tokens", "output_tokens",
            "first_token_latency", "tbt_p90_ms", "tbt_p95_ms", "call_index",
            "total_calls_expected", "job_submit_time", "job_end_time",
        ]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        for col in ["job_completed", "success", "is_timeout", "is_error",
                    "is_job_timeout", "is_server_terminated"]:
            if col in df.columns:
                df[col] = df[col].astype(str).str.lower().isin({"true", "1", "yes"})
        return df
    except pd.errors.EmptyDataError:
        return pd.DataFrame()
    except Exception as exc:
        print(f"[WARN] Failed to read {csv_path}: {exc}", file=sys.stderr)
        return pd.DataFrame()


def load_baseline(baseline_dir: str) -> pd.DataFrame:
    """Load baseline metrics from *baseline_dir*/metrics.csv."""
    baseline_csv = Path(baseline_dir) / "metrics.csv"
    if not baseline_csv.is_file():
        print(f"[ERROR] baseline metrics.csv not found: {baseline_csv}", file=sys.stderr)
        sys.exit(1)
    return load_metrics(str(baseline_csv))


# ---------------------------------------------------------------------------
# SLO computation
# ---------------------------------------------------------------------------

def compute_slo_thresholds(baseline_df: pd.DataFrame,
                           alpha_call: float,
                           alpha_job: float) -> dict:
    """Compute SLO thresholds from baseline (concurrency=1) data.

    Returns dict with:
        ttft_slo, tbt_slo, baseline_jct: {chain_length: mean_jct},
        job_slo: {chain_length: T_SLO(n)}
    """
    call_df = baseline_df[baseline_df["agent"].astype(str).str.startswith("chain_call")].copy()
    job_df = baseline_df[baseline_df["agent"] == "job_summary"].copy()

    # TTFT SLO: p95 of first_token_latency * alpha_call
    ttft_slo = float(call_df["first_token_latency"].quantile(0.95)) * alpha_call

    # TBT SLO: p95 of tbt_p95_ms * alpha_call
    tbt_slo = float(call_df["tbt_p95_ms"].quantile(0.95)) * alpha_call

    # Per-chain-length baseline JCT
    baseline_jct: dict[int, float] = {}
    job_slo: dict[int, float] = {}

    if not job_df.empty and "total_calls_expected" in job_df.columns:
        for n, group in job_df.groupby("total_calls_expected"):
            jct_values = (group["job_end_time"] - group["job_submit_time"]).dropna()
            if len(jct_values) > 0:
                mean_jct = float(jct_values.mean())
                baseline_jct[int(n)] = mean_jct
                job_slo[int(n)] = mean_jct * (1 + alpha_job)

    return {
        "ttft_slo": ttft_slo,
        "tbt_slo": tbt_slo,
        "baseline_jct": baseline_jct,
        "job_slo": job_slo,
    }


# ---------------------------------------------------------------------------
# Per-run metrics
# ---------------------------------------------------------------------------

def compute_run_metrics(df: pd.DataFrame, slo: dict) -> dict | None:
    """Compute all metrics for one concurrency level / RPM rate.

    Returns a dict of metric values or None if data is insufficient.
    """
    call_df = df[df["agent"].astype(str).str.startswith("chain_call")].copy()
    job_df = df[df["agent"] == "job_summary"].copy()

    if call_df.empty and job_df.empty:
        return None

    # ---- Call-level goodput ----
    total_calls = len(call_df)
    if total_calls > 0:
        ttft_ok = call_df["first_token_latency"] <= slo["ttft_slo"]
        tbt_ok = call_df["tbt_p95_ms"] <= slo["tbt_slo"]
        call_goodput = float((ttft_ok & tbt_ok).sum()) / total_calls * 100
    else:
        call_goodput = float("nan")

    # ---- JCR (Job Completion Rate) ----
    total_jobs = len(job_df)
    if total_jobs > 0:
        completed_jobs = job_df[job_df["job_completed"] == True]  # noqa: E712
        jcr = float(len(completed_jobs)) / total_jobs * 100
    else:
        jcr = float("nan")
        completed_jobs = pd.DataFrame()

    # ---- JSA (Job SLO Attainment) ----
    if len(completed_jobs) > 0:
        slo_attained_count = 0
        for _, row in completed_jobs.iterrows():
            jct = row["job_end_time"] - row["job_submit_time"]
            chain_len = int(row["total_calls_expected"]) if pd.notna(row["total_calls_expected"]) else None
            if chain_len is not None and chain_len in slo["job_slo"]:
                if jct <= slo["job_slo"][chain_len]:
                    slo_attained_count += 1
            elif chain_len is not None and slo["job_slo"]:
                # No SLO for this chain length; skip or use nearest
                # Use the maximum available SLO as a conservative fallback
                max_slo = max(slo["job_slo"].values())
                if jct <= max_slo:
                    slo_attained_count += 1
        jsa = float(slo_attained_count) / len(completed_jobs) * 100
    else:
        jsa = float("nan")

    # ---- Job Goodput ----
    if not np.isnan(jcr) and not np.isnan(jsa):
        job_goodput = jcr * jsa / 100
    else:
        job_goodput = float("nan")

    # ---- WCR (Wasted Compute Ratio) ----
    total_in = float(call_df["input_tokens"].sum()) if "input_tokens" in call_df.columns else 0
    total_out = float(call_df["output_tokens"].sum()) if "output_tokens" in call_df.columns else 0
    total_tokens = total_in + total_out

    if total_jobs > 0 and not job_df.empty:
        incomplete_jobs = job_df[job_df["job_completed"] != True]  # noqa: E712
        if len(incomplete_jobs) > 0:
            # Gather call data belonging to incomplete jobs
            incomplete_ids = set(incomplete_jobs["task_id"].unique())
            incomplete_calls = call_df[call_df["task_id"].isin(incomplete_ids)]
            wasted_in = float(incomplete_calls["input_tokens"].sum()) if "input_tokens" in incomplete_calls.columns else 0
            wasted_out = float(incomplete_calls["output_tokens"].sum()) if "output_tokens" in incomplete_calls.columns else 0
            wasted_tokens = wasted_in + wasted_out
            wcr = wasted_tokens / total_tokens * 100 if total_tokens > 0 else 0
        else:
            wcr = 0.0
    else:
        wcr = float("nan")

    # ---- Throughput ----
    if not call_df.empty and "start_time" in call_df.columns and "end_time" in call_df.columns:
        t_min = call_df["start_time"].min()
        t_max = call_df["end_time"].max()
        duration = t_max - t_min
        if duration > 0:
            throughput_out = total_out / duration
            throughput_total = total_tokens / duration
        else:
            throughput_out = float("nan")
            throughput_total = float("nan")
    else:
        throughput_out = float("nan")
        throughput_total = float("nan")

    # ---- Call index vs SLO violation ----
    call_violation_by_index: dict[int, dict] = {}
    if total_calls > 0 and "call_index" in call_df.columns:
        for idx, group in call_df.groupby("call_index"):
            idx = int(idx)
            n = len(group)
            ttft_viol = (group["first_token_latency"] > slo["ttft_slo"]).sum()
            tbt_viol = (group["tbt_p95_ms"] > slo["tbt_slo"]).sum()
            any_viol = ((group["first_token_latency"] > slo["ttft_slo"]) |
                        (group["tbt_p95_ms"] > slo["tbt_slo"])).sum()
            call_violation_by_index[idx] = {
                "total": n,
                "ttft_violations": int(ttft_viol),
                "tbt_violations": int(tbt_viol),
                "any_violations": int(any_viol),
                "violation_rate": float(any_viol) / n * 100 if n > 0 else 0,
            }

    return {
        "call_goodput": call_goodput,
        "jcr": jcr,
        "jsa": jsa,
        "job_goodput": job_goodput,
        "wcr": wcr,
        "throughput_out": throughput_out,
        "throughput_total": throughput_total,
        "total_calls": total_calls,
        "total_jobs": total_jobs,
        "completed_jobs": len(completed_jobs),
        "call_violation_by_index": call_violation_by_index,
    }


# ---------------------------------------------------------------------------
# Sensitivity analysis
# ---------------------------------------------------------------------------

def compute_sensitivity(baseline_df: pd.DataFrame,
                        all_run_dfs: list[tuple[dict, pd.DataFrame]],
                        alphas: list[float]) -> dict:
    """Compute job goodput for each alpha x concurrency combination.

    Returns:
        {concurrency_label: {alpha: job_goodput}}
    """
    sensitivity: dict[str, dict[float, float]] = {}

    for alpha in alphas:
        slo = compute_slo_thresholds(baseline_df, alpha_call=alpha, alpha_job=alpha)
        for run_info, df in all_run_dfs:
            label = _run_label(run_info)
            metrics = compute_run_metrics(df, slo)
            if metrics is None:
                continue
            if label not in sensitivity:
                sensitivity[label] = {}
            sensitivity[label][alpha] = metrics["job_goodput"]

    return sensitivity


# ---------------------------------------------------------------------------
# Label helpers
# ---------------------------------------------------------------------------

def _run_label(run_info: dict) -> str:
    """Human-readable label for a run."""
    cl = run_info.get("concurrency_level")
    rpm = run_info.get("rpm")
    lam = run_info.get("lambda")
    if cl is not None:
        return str(cl)
    if rpm is not None:
        return f"{rpm} RPM"
    if lam is not None:
        return f"lambda={lam}"
    return Path(run_info["path"]).name


def _run_xvalue(run_info: dict) -> float:
    """Numeric x-axis value for a run (concurrency level or RPM)."""
    cl = run_info.get("concurrency_level")
    rpm = run_info.get("rpm")
    lam = run_info.get("lambda")
    if cl is not None:
        return float(cl)
    if rpm is not None:
        return float(rpm)
    if lam is not None:
        return float(lam)
    return 0.0


# ---------------------------------------------------------------------------
# Plotting
# ---------------------------------------------------------------------------

def _setup_style():
    """Configure matplotlib for clean academic figures."""
    try:
        plt.style.use("seaborn-v0_8-whitegrid")
    except OSError:
        try:
            plt.style.use("seaborn-whitegrid")
        except OSError:
            pass

    plt.rcParams.update({
        "font.size": 10,
        "axes.labelsize": 12,
        "axes.titlesize": 13,
        "xtick.labelsize": 10,
        "ytick.labelsize": 10,
        "legend.fontsize": 9,
        "figure.dpi": 150,
        "savefig.dpi": 150,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.1,
    })


def _save_fig(fig, output_dir: Path, name: str):
    """Save figure as PNG and PDF."""
    output_dir.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(output_dir / f"{name}.png"))
    fig.savefig(str(output_dir / f"{name}.pdf"))
    plt.close(fig)
    print(f"  Saved {name}.png / {name}.pdf")


def plot_figure1(x_vals: list[float],
                 x_labels: list[str],
                 throughput_out: list[float],
                 call_goodput: list[float],
                 job_goodput: list[float],
                 output_dir: Path):
    """Figure 1: The Illusion -- throughput vs. goodput gap."""
    fig, ax1 = plt.subplots(figsize=(6, 4))

    # Left y-axis: throughput
    color_tp = "#2ca02c"
    ax1.set_xlabel("Concurrency Level" if not any("RPM" in l for l in x_labels) else "Requests per Minute")
    ax1.set_ylabel("Throughput (tokens/s)", color=color_tp)
    line_tp = ax1.plot(x_vals, throughput_out, "-o", color=color_tp, linewidth=2,
                       markersize=6, label="Throughput", zorder=3)
    ax1.tick_params(axis="y", labelcolor=color_tp)

    # Annotate throughput peak
    valid_tp = [(i, v) for i, v in enumerate(throughput_out) if not np.isnan(v)]
    if valid_tp:
        peak_idx, peak_val = max(valid_tp, key=lambda iv: iv[1])
        ax1.annotate(f"Peak: {peak_val:.1f}",
                     xy=(x_vals[peak_idx], peak_val),
                     xytext=(10, 10), textcoords="offset points",
                     fontsize=9, color=color_tp, fontweight="bold",
                     arrowprops=dict(arrowstyle="->", color=color_tp, lw=1.2))

    # Right y-axis: goodput
    ax2 = ax1.twinx()
    ax2.set_ylabel("Goodput (%)", color="black")

    color_cg = "#1f77b4"
    color_jg = "#d62728"
    line_cg = ax2.plot(x_vals, call_goodput, "--s", color=color_cg, linewidth=1.8,
                       markersize=5, label="Call-level Goodput", zorder=2)
    line_jg = ax2.plot(x_vals, job_goodput, "-^", color=color_jg, linewidth=2,
                       markersize=5, label="Job-level Goodput", zorder=2)
    ax2.set_ylim(-5, 105)
    ax2.tick_params(axis="y")

    # Combined legend
    lines = line_tp + line_cg + line_jg
    labels = [l.get_label() for l in lines]
    ax1.legend(lines, labels, loc="center right", frameon=True, framealpha=0.9)

    # Shade the gap between call goodput and job goodput
    for i in range(len(x_vals) - 1):
        x_span = [x_vals[i], x_vals[i + 1]]
        cg_span = [call_goodput[i], call_goodput[i + 1]]
        jg_span = [job_goodput[i], job_goodput[i + 1]]
        ax2.fill_between(x_span, jg_span, cg_span, alpha=0.10, color=color_jg, zorder=1)

    ax1.set_xticks(x_vals)
    ax1.set_xticklabels(x_labels)
    fig.tight_layout()
    _save_fig(fig, output_dir, "fig1_illusion")


def plot_figure2(all_violation_data: dict[str, dict[int, float]],
                 output_dir: Path):
    """Figure 2: Call index vs SLO violation rate."""
    if not all_violation_data:
        print("  [SKIP] fig2: no violation data")
        return

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.set_xlabel("Call Index within Chain")
    ax.set_ylabel("SLO Violation Rate (%)")

    cmap = plt.cm.viridis
    conc_levels = sorted(all_violation_data.keys())
    n_lines = len(conc_levels)
    colors = [cmap(i / max(n_lines - 1, 1)) for i in range(n_lines)]

    for idx, conc in enumerate(conc_levels):
        viol = all_violation_data[conc]
        # Only include concurrency levels with meaningful violations
        max_viol = max(viol.values()) if viol else 0
        if max_viol < 1.0:
            continue
        indices = sorted(viol.keys())
        rates = [viol[i] for i in indices]
        ax.plot(indices, rates, "-o", color=colors[idx], linewidth=1.5,
                markersize=4, label=f"Conc={conc}")

    ax.legend(loc="upper left", fontsize=8)
    ax.set_ylim(bottom=0)
    fig.tight_layout()
    _save_fig(fig, output_dir, "fig2_abandonment")


def plot_figure3(x_vals: list[float],
                 x_labels: list[str],
                 wcr: list[float],
                 throughput_out: list[float],
                 output_dir: Path):
    """Figure 3: Wasted Compute Ratio vs throughput."""
    fig, ax1 = plt.subplots(figsize=(6, 4))

    # Left y-axis: WCR bar chart
    color_wcr = "#ff7f0e"
    ax1.set_xlabel("Concurrency Level" if not any("RPM" in l for l in x_labels) else "Requests per Minute")
    ax1.set_ylabel("WCR (%)", color=color_wcr)
    width = (x_vals[-1] - x_vals[0]) / (len(x_vals) * 3) if len(x_vals) > 1 else 0.5
    bars = ax1.bar(x_vals, wcr, width=width, color=color_wcr, alpha=0.7, label="WCR", zorder=2)
    ax1.tick_params(axis="y", labelcolor=color_wcr)

    # Right y-axis: throughput overlay
    ax2 = ax1.twinx()
    color_tp = "#2ca02c"
    ax2.set_ylabel("Throughput (tokens/s)", color=color_tp)
    ax2.plot(x_vals, throughput_out, "-o", color=color_tp, linewidth=2,
             markersize=5, label="Throughput", zorder=3)
    ax2.tick_params(axis="y", labelcolor=color_tp)

    # Combined legend
    from matplotlib.patches import Patch
    from matplotlib.lines import Line2D
    handles = [Patch(facecolor=color_wcr, alpha=0.7, label="WCR"),
               Line2D([0], [0], color=color_tp, marker="o", label="Throughput")]
    ax1.legend(handles=handles, loc="upper left", frameon=True, framealpha=0.9)

    ax1.set_xticks(x_vals)
    ax1.set_xticklabels(x_labels)
    fig.tight_layout()
    _save_fig(fig, output_dir, "fig3_wasted_compute")


def plot_figure4(sensitivity: dict[str, dict[float, float]],
                 alphas: list[float],
                 output_dir: Path):
    """Figure 4: Alpha sensitivity of job goodput."""
    if not sensitivity:
        print("  [SKIP] fig4: no sensitivity data")
        return

    fig, ax = plt.subplots(figsize=(6, 4))
    ax.set_xlabel("Slack Factor (alpha)")
    ax.set_ylabel("Job Goodput (%)")

    cmap = plt.cm.tab10
    conc_labels = sorted(sensitivity.keys())
    n_lines = len(conc_labels)
    colors = [cmap(i / max(n_lines, 1)) for i in range(n_lines)]

    for idx, label in enumerate(conc_labels):
        alpha_data = sensitivity[label]
        y_vals = [alpha_data.get(a, float("nan")) for a in alphas]
        ax.plot(alphas, y_vals, "-o", color=colors[idx], linewidth=1.5,
                markersize=4, label=f"Conc={label}")

    ax.legend(loc="lower right", fontsize=8)
    ax.set_ylim(-5, 105)
    ax.axhline(y=100, color="gray", linestyle=":", linewidth=0.8)
    fig.tight_layout()
    _save_fig(fig, output_dir, "fig4_alpha_sensitivity")


# ---------------------------------------------------------------------------
# Summary table
# ---------------------------------------------------------------------------

def print_summary_table(run_labels: list[str],
                        all_metrics: list[dict]):
    """Print a formatted summary table of all metrics."""
    header = (f"{'Run':<12} {'CallGP':>8} {'JCR':>8} {'JSA':>8} "
              f"{'JobGP':>8} {'WCR':>8} {'TP_out':>10} {'TP_tot':>10} "
              f"{'#Calls':>7} {'#Jobs':>7} {'#Comp':>7}")
    sep = "-" * len(header)
    print("\n" + sep)
    print("SUMMARY TABLE")
    print(sep)
    print(header)
    print(sep)
    for label, m in zip(run_labels, all_metrics):
        print(f"{label:<12} "
              f"{m['call_goodput']:>7.1f}% "
              f"{m['jcr']:>7.1f}% "
              f"{m['jsa']:>7.1f}% "
              f"{m['job_goodput']:>7.1f}% "
              f"{m['wcr']:>7.1f}% "
              f"{m['throughput_out']:>9.1f} "
              f"{m['throughput_total']:>9.1f} "
              f"{m['total_calls']:>7d} "
              f"{m['total_jobs']:>7d} "
              f"{m['completed_jobs']:>7d}")
    print(sep + "\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Post-hoc analysis for the motivation experiment.")
    parser.add_argument("--results-dir", required=True,
                        help="Root directory containing all experiment results")
    parser.add_argument("--baseline-dir", required=True,
                        help="Directory containing baseline (concurrency=1) results")
    parser.add_argument("--output-dir", default="./figures",
                        help="Directory to save figures (default: ./figures)")
    parser.add_argument("--alpha-call", type=float, default=1.5,
                        help="Slack factor for call-level SLO (default: 1.5)")
    parser.add_argument("--alpha-job", type=float, default=1.0,
                        help="Slack factor for job-level SLO (default: 1.0)")
    parser.add_argument("--alpha-sensitivity", type=str, default="0.5,1.0,2.0,5.0",
                        help="Comma-separated alpha values for sensitivity (default: 0.5,1.0,2.0,5.0)")
    args = parser.parse_args()

    alphas = [float(a.strip()) for a in args.alpha_sensitivity.split(",")]
    output_dir = Path(args.output_dir)

    _setup_style()

    # ---- Step 1: Load all data ----
    print("=" * 60)
    print("MOTIVATION EXPERIMENT -- POST-HOC ANALYSIS")
    print("=" * 60)

    runs = discover_runs(args.results_dir)
    if not runs:
        print("[ERROR] No experiment runs found in", args.results_dir, file=sys.stderr)
        sys.exit(1)

    print(f"\nFound {len(runs)} experiment run(s):")
    for r in runs:
        print(f"  {_run_label(r):>12s}  ({r['path']})")

    baseline_df = load_baseline(args.baseline_dir)
    print(f"\nBaseline: {len(baseline_df)} rows loaded from {args.baseline_dir}")

    # Load all run DataFrames
    all_run_data: list[tuple[dict, pd.DataFrame]] = []
    for run_info in runs:
        df = load_metrics(run_info["metrics_csv"])
        if df.empty:
            print(f"  [WARN] Empty data for {_run_label(run_info)}")
            continue
        all_run_data.append((run_info, df))

    # ---- Step 2: Compute SLO thresholds ----
    slo = compute_slo_thresholds(baseline_df, args.alpha_call, args.alpha_job)
    print(f"\nSLO Thresholds (alpha_call={args.alpha_call}, alpha_job={args.alpha_job}):")
    print(f"  TTFT_SLO = {slo['ttft_slo']:.2f} s")
    print(f"  TBT_SLO  = {slo['tbt_slo']:.2f} ms")
    if slo["baseline_jct"]:
        print(f"  Baseline JCT by chain length:")
        for n in sorted(slo["baseline_jct"]):
            print(f"    n={n}: JCT={slo['baseline_jct'][n]:.2f}s, T_SLO={slo['job_slo'][n]:.2f}s")

    # ---- Step 3: Compute metrics per concurrency level ----
    x_vals: list[float] = []
    x_labels: list[str] = []
    all_metrics: list[dict] = []
    all_violation_data: dict[str, dict[int, float]] = {}

    for run_info, df in all_run_data:
        label = _run_label(run_info)
        metrics = compute_run_metrics(df, slo)
        if metrics is None:
            print(f"  [WARN] Could not compute metrics for {label}")
            continue

        x_vals.append(_run_xvalue(run_info))
        x_labels.append(label)
        all_metrics.append(metrics)

        # Collect violation-by-index data for Figure 2
        if metrics["call_violation_by_index"]:
            all_violation_data[label] = {
                idx: v["violation_rate"]
                for idx, v in metrics["call_violation_by_index"].items()
            }

    # Print summary
    print_summary_table(x_labels, all_metrics)

    # ---- Step 4: Generate figures ----
    print("Generating figures...")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Extract arrays for plotting
    throughput_out = [m["throughput_out"] for m in all_metrics]
    call_goodput = [m["call_goodput"] for m in all_metrics]
    job_goodput = [m["job_goodput"] for m in all_metrics]
    wcr_vals = [m["wcr"] for m in all_metrics]

    # Figure 1: The Illusion
    plot_figure1(x_vals, x_labels, throughput_out, call_goodput, job_goodput, output_dir)

    # Figure 2: Abandonment Cost
    plot_figure2(all_violation_data, output_dir)

    # Figure 3: Wasted Compute
    plot_figure3(x_vals, x_labels, wcr_vals, throughput_out, output_dir)

    # Figure 4: Alpha Sensitivity
    sensitivity = compute_sensitivity(baseline_df, all_run_data, alphas)
    plot_figure4(sensitivity, alphas, output_dir)

    print(f"\nAll figures saved to {output_dir}/")
    print("=" * 60)


if __name__ == "__main__":
    main()
