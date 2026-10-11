#!/usr/bin/env python3
"""Plot PPT-ready figures v3 — Throughput vs Goodput with call-level τ goodput."""

import glob
import os
import re
import argparse
from datetime import datetime

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np


def load_metrics(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["start_time"] = pd.to_numeric(df["start_time"], errors="coerce")
    df["end_time"] = pd.to_numeric(df["end_time"], errors="coerce")
    df["job_submit_time"] = pd.to_numeric(df["job_submit_time"], errors="coerce")
    df["job_end_time"] = pd.to_numeric(df["job_end_time"], errors="coerce")
    df["latency"] = pd.to_numeric(df["latency"], errors="coerce")
    return df


def load_server_throughput(result_dir: str) -> pd.DataFrame:
    log_files = sorted(glob.glob(os.path.join(result_dir, "server.stderr*")))
    if not log_files:
        return pd.DataFrame()
    pattern = re.compile(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \w+\] "
        r".*Decode batch.*gen throughput \(token/s\): ([\d.]+)"
    )
    rows = []
    with open(log_files[0]) as f:
        for line in f:
            m = pattern.search(line)
            if m:
                ts = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                tp = float(m.group(2))
                epoch = int(ts.timestamp())
                rows.append({"epoch": epoch, "server_throughput": tp})
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def compute_baseline_call_latency(baseline_dir: str) -> dict:
    """Compute per-call-index mean latency from baseline experiment."""
    csv_path = os.path.join(baseline_dir, "metrics.csv")
    if not os.path.exists(csv_path):
        return {}
    df = pd.read_csv(csv_path)
    calls = df[df["agent"].str.startswith("chain_call_")].copy()
    calls["latency"] = pd.to_numeric(calls["latency"], errors="coerce")
    calls["call_index"] = pd.to_numeric(calls["call_index"], errors="coerce")
    baseline = calls.groupby("call_index")["latency"].mean().to_dict()
    return baseline


def plot_throughput_vs_goodput(calls: pd.DataFrame, jobs: pd.DataFrame,
                                result_dir: str, output_dir: str,
                                server_tp: pd.DataFrame = None,
                                baseline_call_latency: dict = None,
                                tau: float = 3.0,
                                label_suffix: str = ""):
    """Throughput vs Call/Job-level Goodput — PPT-ready v3."""
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = ["Liberation Serif", "Times New Roman", "DejaVu Serif"]
    plt.rcParams["mathtext.fontset"] = "stix"

    t0 = calls["start_time"].min()
    calls["rel_start"] = calls["start_time"] - t0
    calls["rel_end"] = calls["end_time"] - t0
    jobs["rel_submit"] = jobs["job_submit_time"] - t0
    jobs["rel_end"] = jobs["job_end_time"] - t0

    # Cut at 60 min
    calls = calls[calls["rel_start"] / 60 <= 60].copy()
    jobs = jobs[jobs["rel_submit"] / 60 <= 60].copy()

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # --- Left y-axis: Server Throughput ---
    if server_tp is not None and len(server_tp) > 0:
        server_tp = server_tp.copy()
        server_tp["rel_time"] = server_tp["epoch"] - t0
        server_tp = server_tp[(server_tp["rel_time"] >= 0) & (server_tp["rel_time"] / 60 <= 60)]
        if len(server_tp) > 0:
            server_tp["smooth"] = server_tp["server_throughput"].rolling(10, min_periods=1).mean()
            ax1.fill_between(server_tp["rel_time"] / 60, server_tp["smooth"],
                             alpha=0.15, color="tab:blue")
            ax1.plot(server_tp["rel_time"] / 60, server_tp["smooth"],
                     color="tab:blue", linewidth=2.0, label="Throughput (tok/s)")

    ax1.set_xlabel("Time (min)", fontsize=13)
    ax1.set_ylabel("Throughput (tok/s)", color="tab:blue", fontsize=13)
    ax1.tick_params(axis="y", labelcolor="tab:blue", labelsize=11)
    ax1.tick_params(axis="x", labelsize=11)
    ax1.set_ylim(bottom=0)

    # --- Right y-axis: Call-level & Job-level Goodput ---
    ax2 = ax1.twinx()

    # Call-level goodput (success): only decided calls
    calls["success_bool"] = calls["success"].astype(bool)
    calls_decided = calls[
        calls["success_bool"] | (calls["is_job_timeout"] == True) | (calls["is_timeout"] == True) | (calls["is_error"] == True)
    ].dropna(subset=["rel_end"]).sort_values("rel_end").copy()
    calls_decided["cum_ok"] = calls_decided["success_bool"].cumsum()
    calls_decided["cum_ended"] = range(1, len(calls_decided) + 1)
    calls_decided["running_call_goodput"] = calls_decided["cum_ok"] / calls_decided["cum_ended"]

    # Call-level τ goodput: latency ≤ baseline_latency(call_index) × τ
    if baseline_call_latency:
        calls_decided["call_index_num"] = pd.to_numeric(calls_decided["call_index"], errors="coerce")
        calls_decided["baseline_latency"] = calls_decided["call_index_num"].map(baseline_call_latency)
        calls_decided["tau_threshold"] = calls_decided["baseline_latency"] * tau
        calls_decided["tau_ok"] = (calls_decided["latency"] <= calls_decided["tau_threshold"]).astype(float)
        calls_decided["cum_tau_ok"] = calls_decided["tau_ok"].cumsum()
        calls_decided["running_tau_goodput"] = calls_decided["cum_tau_ok"] / calls_decided["cum_ended"]

    # Job-level goodput (JCR): only decided jobs
    jobs["job_completed_bool"] = jobs["job_completed"].astype(bool)
    jobs_decided = jobs[
        jobs["job_completed_bool"] | (jobs["is_job_timeout"] == True)
    ].dropna(subset=["rel_end"]).sort_values("rel_end").copy()
    jobs_decided["cum_ended"] = range(1, len(jobs_decided) + 1)
    jobs_decided["cum_completed"] = jobs_decided["job_completed_bool"].cumsum()
    jobs_decided["running_jcr"] = jobs_decided["cum_completed"] / jobs_decided["cum_ended"]

    ax2.plot(calls_decided["rel_end"] / 60, calls_decided["running_call_goodput"],
             color="tab:green", linewidth=2.5, label="Call-level Goodput (success)")
    if baseline_call_latency and "running_tau_goodput" in calls_decided.columns:
        ax2.plot(calls_decided["rel_end"] / 60, calls_decided["running_tau_goodput"],
                 color="#2ca02c", linewidth=2.5, linestyle="-.",
                 label=f"Call-level Goodput ($\\tau$={tau:.0f})")
    ax2.plot(jobs_decided["rel_end"] / 60, jobs_decided["running_jcr"],
             color="tab:red", linewidth=2.5, linestyle="--", label="Job-level Goodput")
    ax2.set_ylabel("Goodput Rate", fontsize=13)
    ax2.set_ylim(-0.05, 1.05)
    ax2.tick_params(axis="y", labelsize=11)

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, loc="center right", fontsize=11,
               framealpha=0.9)

    import json
    config_path = os.path.join(result_dir, "run_config.json")
    with open(config_path) as f:
        config = json.load(f)
    lam = config.get("lambda", "")
    ax1.set_title(f"Poisson Arrival ($\\lambda$={lam}, $\\tau$={tau:.0f}){label_suffix} — "
                  f"Throughput vs Goodput Over Time",
                  fontsize=14, fontweight="bold")

    fig.tight_layout()
    path = os.path.join(output_dir, f"throughput_vs_goodput_v3{label_suffix.replace(' ', '_')}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")

    # --- Export CSV data ---
    csv_data = []
    if server_tp is not None and len(server_tp) > 0:
        for _, row in server_tp.iterrows():
            csv_data.append({
                "time_min": row["rel_time"] / 60,
                "throughput": row.get("smooth", row["server_throughput"]),
            })
    for _, row in calls_decided.iterrows():
        entry = {
            "time_min": row["rel_end"] / 60,
            "call_goodput_success": row["running_call_goodput"],
        }
        if "running_tau_goodput" in row.index:
            entry["call_goodput_tau"] = row["running_tau_goodput"]
        csv_data.append(entry)
    for _, row in jobs_decided.iterrows():
        csv_data.append({
            "time_min": row["rel_end"] / 60,
            "job_goodput": row["running_jcr"],
        })
    csv_df = pd.DataFrame(csv_data).sort_values("time_min")
    csv_path = os.path.join(output_dir, "data_throughput_vs_goodput_v3.csv")
    csv_df.to_csv(csv_path, index=False)
    print(f"Saved: {csv_path}")

    plt.rcParams.update(plt.rcParamsDefault)


def plot_wcr(jobs: pd.DataFrame, result_dir: str, output_dir: str,
             label_suffix: str = ""):
    """Cumulative Useful vs Wasted Tokens + Running WCR — PPT-ready v3."""
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = ["Liberation Serif", "Times New Roman", "DejaVu Serif"]
    plt.rcParams["mathtext.fontset"] = "stix"

    t0 = jobs["job_submit_time"].min()
    jobs = jobs.copy()
    jobs["rel_end"] = jobs["job_end_time"] - t0

    jobs["job_completed_bool"] = jobs["job_completed"].astype(bool)
    jobs_decided = jobs[
        jobs["job_completed_bool"] | (jobs["is_job_timeout"] == True)
    ].dropna(subset=["rel_end"]).sort_values("rel_end").copy()
    jobs_valid = jobs_decided[jobs_decided["rel_end"] / 60 <= 60]

    if len(jobs_valid) == 0:
        print("No valid job end data for WCR plot")
        plt.rcParams.update(plt.rcParamsDefault)
        return

    jobs_valid["tokens"] = jobs_valid["input_tokens"] + jobs_valid["output_tokens"]
    jobs_valid["is_useful"] = jobs_valid["job_completed_bool"] == True
    jobs_valid["time_min"] = jobs_valid["rel_end"] / 60

    jobs_valid["cum_total"] = jobs_valid["tokens"].cumsum()
    jobs_valid["cum_useful"] = (jobs_valid["tokens"] * jobs_valid["is_useful"]).cumsum()
    jobs_valid["cum_wasted"] = (jobs_valid["tokens"] * ~jobs_valid["is_useful"]).cumsum()
    jobs_valid["running_wcr"] = 1 - jobs_valid["cum_useful"] / jobs_valid["cum_total"]

    fig, ax1 = plt.subplots(figsize=(10, 5))

    ax1.fill_between(jobs_valid["time_min"], 0,
                     jobs_valid["cum_useful"] / 1e6,
                     color="tab:green", alpha=0.4, label="Useful Tokens")
    ax1.fill_between(jobs_valid["time_min"],
                     jobs_valid["cum_useful"] / 1e6,
                     jobs_valid["cum_total"] / 1e6,
                     color="tab:red", alpha=0.4, label="Wasted Tokens")
    ax1.plot(jobs_valid["time_min"], jobs_valid["cum_useful"] / 1e6,
             color="tab:green", linewidth=2)
    ax1.plot(jobs_valid["time_min"], jobs_valid["cum_total"] / 1e6,
             color="tab:red", linewidth=2)
    ax1.set_xlabel("Time (min)", fontsize=13)
    ax1.set_ylabel("Tokens (M)", fontsize=13)
    ax1.tick_params(axis="both", labelsize=11)
    ax1.set_ylim(bottom=0)

    ax2 = ax1.twinx()
    ax2.plot(jobs_valid["time_min"], jobs_valid["running_wcr"],
             color="tab:orange", linewidth=2.5, linestyle="--", label="WCR")
    ax2.set_ylabel("Wasted Compute Ratio", fontsize=13, color="tab:orange")
    ax2.tick_params(axis="y", labelcolor="tab:orange", labelsize=11)
    ax2.set_ylim(-0.05, 1.05)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=11,
               framealpha=0.9)

    import json
    config_path = os.path.join(result_dir, "run_config.json")
    with open(config_path) as f:
        config = json.load(f)
    lam = config.get("lambda", "")
    tau = config.get("tau", "")
    ax1.set_title(f"Poisson Arrival ($\\lambda$={lam}, $\\tau$={tau}){label_suffix} — "
                  f"Useful vs Wasted Compute Over Time",
                  fontsize=14, fontweight="bold")

    fig.tight_layout()
    path = os.path.join(output_dir, f"wcr_tokens_v3{label_suffix.replace(' ', '_')}.png")
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")

    # --- Export CSV data ---
    csv_df = jobs_valid[["time_min", "cum_useful", "cum_total", "cum_wasted", "running_wcr"]].copy()
    csv_df["cum_useful_M"] = csv_df["cum_useful"] / 1e6
    csv_df["cum_total_M"] = csv_df["cum_total"] / 1e6
    csv_df["cum_wasted_M"] = csv_df["cum_wasted"] / 1e6
    csv_path = os.path.join(output_dir, "data_wcr_v3.csv")
    csv_df.to_csv(csv_path, index=False)
    print(f"Saved: {csv_path}")

    plt.rcParams.update(plt.rcParamsDefault)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir")
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--baseline-dir", default="results/baseline_20260424-180204",
                        help="Baseline experiment directory for per-call latency")
    args = parser.parse_args()

    csv_path = os.path.join(args.result_dir, "metrics.csv")
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} not found")
        return

    output_dir = args.output_dir or os.path.join(args.result_dir, "figures")
    os.makedirs(output_dir, exist_ok=True)

    df = load_metrics(csv_path)
    calls = df[df["agent"].str.startswith("chain_call_")].copy()
    jobs = df[df["agent"] == "job_summary"].copy()
    print(f"  {len(calls)} chain_call rows, {len(jobs)} job_summary rows")

    server_tp = load_server_throughput(args.result_dir)
    if len(server_tp) > 0:
        print(f"  {len(server_tp)} server throughput samples")

    baseline_call_latency = compute_baseline_call_latency(args.baseline_dir)
    if baseline_call_latency:
        print(f"  {len(baseline_call_latency)} baseline call latency entries")

    import json
    with open(os.path.join(args.result_dir, "run_config.json")) as f:
        config = json.load(f)
    tau = config.get("tau", 3.0)

    plot_throughput_vs_goodput(calls, jobs, args.result_dir, output_dir,
                               server_tp=server_tp,
                               baseline_call_latency=baseline_call_latency,
                               tau=tau)
    plot_wcr(jobs, args.result_dir, output_dir)


if __name__ == "__main__":
    main()
