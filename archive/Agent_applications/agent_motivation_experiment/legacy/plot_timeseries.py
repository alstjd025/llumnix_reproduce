#!/usr/bin/env python3
"""Plot time-series graphs from Poisson/Rate sweep experiment results.

Usage:
    python plot_timeseries.py <result_dir> [--output-dir figures/]

Example:
    python plot_timeseries.py results/poisson_0.01_20260426-050845
"""

import argparse
import glob
import os
import re
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import pandas as pd
import numpy as np


def load_server_throughput(result_dir: str) -> pd.DataFrame:
    """Parse SGLang server stderr log for decode gen throughput over time."""
    log_files = sorted(glob.glob(os.path.join(result_dir, "server.stderr*")))
    if not log_files:
        return pd.DataFrame()
    log_path = log_files[0]

    pattern = re.compile(
        r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \w+\] "
        r".*Decode batch.*gen throughput \(token/s\): ([\d.]+)"
    )
    rows = []
    with open(log_path) as f:
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


def load_metrics(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df["start_time"] = pd.to_numeric(df["start_time"], errors="coerce")
    df["end_time"] = pd.to_numeric(df["end_time"], errors="coerce")
    df["job_submit_time"] = pd.to_numeric(df["job_submit_time"], errors="coerce")
    df["job_end_time"] = pd.to_numeric(df["job_end_time"], errors="coerce")
    return df


def split_calls_jobs(df: pd.DataFrame):
    calls = df[df["agent"].str.startswith("chain_call_")].copy()
    jobs = df[df["agent"] == "job_summary"].copy()
    return calls, jobs


def plot_job_lifecycle(calls: pd.DataFrame, jobs: pd.DataFrame, result_dir: str, output_dir: str):
    """Plot 1: Job lifecycle — submitted, running, completed over time."""
    t0 = calls["start_time"].min()
    calls["rel_start"] = calls["start_time"] - t0
    calls["rel_end"] = calls["end_time"] - t0
    jobs["rel_submit"] = jobs["job_submit_time"] - t0
    jobs["rel_end"] = jobs["job_end_time"] - t0

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    # 1a: Cumulative submitted vs completed
    ax = axes[0]
    if len(jobs) > 0:
        submit_times = sorted(jobs["rel_submit"].dropna())
        completed_times = sorted(jobs.loc[jobs["job_completed"] == True, "rel_end"].dropna())
        ax.step([s / 60 for s in submit_times], range(1, len(submit_times) + 1),
                where="post", label="Submitted", color="tab:blue")
        ax.step([s / 60 for s in completed_times], range(1, len(completed_times) + 1),
                where="post", label="Completed", color="tab:green")
    ax.set_ylabel("Cumulative Jobs")
    ax.legend(loc="upper left")
    ax.set_title("Job Submission & Completion Over Time")

    # 1b: Concurrency (number of active calls at each moment)
    ax = axes[1]
    if len(calls) > 0:
        events = []
        for _, row in calls.iterrows():
            if pd.notna(row["rel_start"]):
                events.append((row["rel_start"], +1))
            if pd.notna(row["rel_end"]):
                events.append((row["rel_end"], -1))
        events.sort()
        times, counts = [], []
        count = 0
        for t, delta in events:
            count += delta
            times.append(t / 60)
            counts.append(count)
        ax.fill_between(times, counts, alpha=0.4, color="tab:orange")
        ax.plot(times, counts, color="tab:orange", linewidth=0.8)
    ax.set_ylabel("Active Calls")
    ax.set_title("Server Concurrency Over Time")

    # 1c: Per-call TTFT and latency
    ax = axes[2]
    if len(calls) > 0 and "first_token_latency" in calls.columns:
        ttft_data = calls.dropna(subset=["first_token_latency", "rel_start"])
        ax.scatter(ttft_data["rel_start"] / 60, ttft_data["first_token_latency"],
                   s=4, alpha=0.5, color="tab:red", label="TTFT (s)")
        lat_data = calls.dropna(subset=["latency", "rel_start"])
        ax.scatter(lat_data["rel_start"] / 60, lat_data["latency"],
                   s=4, alpha=0.3, color="tab:purple", label="Call Latency (s)")
    ax.set_ylabel("Seconds")
    ax.set_xlabel("Time (min)")
    ax.legend(loc="upper left", markerscale=4)
    ax.set_title("Per-Call TTFT & Latency Over Time")

    fig.suptitle(f"Job Lifecycle — {Path(result_dir).name}", fontsize=14, fontweight="bold")
    fig.tight_layout()
    path = os.path.join(output_dir, "job_lifecycle.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_goodput_wcr(calls: pd.DataFrame, jobs: pd.DataFrame, result_dir: str, output_dir: str):
    """Plot 2: Running JCR, call success rate, WCR over time (1-min bins)."""
    t0 = calls["start_time"].min()
    calls["rel_start"] = calls["start_time"] - t0
    jobs["rel_submit"] = jobs["job_submit_time"] - t0
    jobs["rel_end"] = jobs["job_end_time"] - t0

    duration_min = calls["rel_start"].max() / 60
    bins = np.arange(0, duration_min + 1, 1)

    fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

    # 2a: Running JCR (cumulative completed / cumulative submitted)
    ax = axes[0]
    if len(jobs) > 0:
        submit_min = jobs["rel_submit"].dropna() / 60
        end_min = jobs.loc[jobs["job_completed"] == True, "rel_end"].dropna() / 60
        cum_submitted, _ = np.histogram(submit_min, bins=bins)
        cum_completed, _ = np.histogram(end_min, bins=bins)
        cum_submitted = np.cumsum(cum_submitted).astype(float)
        cum_completed = np.cumsum(cum_completed).astype(float)
        jcr = np.where(cum_submitted > 0, cum_completed / cum_submitted, np.nan)
        ax.plot(bins[:-1], jcr, color="tab:blue", linewidth=2, label="Running JCR")
        ax.set_ylim(-0.05, 1.05)
    ax.set_ylabel("JCR")
    ax.legend(loc="upper right")
    ax.set_title("Running Job Completion Rate")

    # 2b: Per-bin call success rate
    ax = axes[1]
    if len(calls) > 0:
        calls["bin"] = pd.cut(calls["rel_start"] / 60, bins=bins)
        bin_stats = calls.groupby("bin", observed=False).agg(
            total=("success", "count"),
            ok=("success", "sum"),
        )
        bin_stats["rate"] = bin_stats["ok"] / bin_stats["total"].replace(0, np.nan)
        ax.bar(bins[:-1], bin_stats["rate"].values, width=1, align="edge",
               color="tab:green", alpha=0.6, label="Call Success Rate")
        ax.set_ylim(0, 1.05)
    ax.set_ylabel("Rate")
    ax.legend(loc="lower right")
    ax.set_title("Per-Minute Call Success Rate")

    # 2c: Running WCR
    ax = axes[2]
    if len(jobs) > 0:
        completed_jobs = jobs[jobs["job_completed"] == True]
        incomplete_jobs = jobs[jobs["job_completed"] != True]
        all_job_ends = []
        for _, row in completed_jobs.iterrows():
            if pd.notna(row["rel_end"]):
                all_job_ends.append((row["rel_end"] / 60, row["input_tokens"] + row["output_tokens"], "useful"))
        for _, row in incomplete_jobs.iterrows():
            end_t = row["rel_end"] if pd.notna(row["rel_end"]) else row.get("rel_submit", 0)
            if pd.notna(end_t):
                all_job_ends.append((end_t / 60, row["input_tokens"] + row["output_tokens"], "wasted"))

        if all_job_ends:
            df_ends = pd.DataFrame(all_job_ends, columns=["time_min", "tokens", "type"])
            useful_cum = df_ends[df_ends["type"] == "useful"].sort_values("time_min")
            wasted_cum = df_ends[df_ends["type"] == "wasted"].sort_values("time_min")
            useful_cum["cum"] = useful_cum["tokens"].cumsum()
            wasted_cum["cum"] = wasted_cum["tokens"].cumsum()

            ax.plot(useful_cum["time_min"], useful_cum["cum"] / 1e6,
                    color="tab:green", linewidth=2, label="Useful Tokens (cum)")
            ax.plot(wasted_cum["time_min"], wasted_cum["cum"] / 1e6,
                    color="tab:red", linewidth=2, label="Wasted Tokens (cum)")
    ax.set_ylabel("Tokens (M)")
    ax.set_xlabel("Time (min)")
    ax.legend(loc="upper left")
    ax.set_title("Cumulative Useful vs Wasted Tokens")

    fig.suptitle(f"Goodput & Wasted Compute — {Path(result_dir).name}", fontsize=14, fontweight="bold")
    fig.tight_layout()
    path = os.path.join(output_dir, "goodput_wcr.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_throughput(calls: pd.DataFrame, result_dir: str, output_dir: str):
    """Plot 3: Instantaneous throughput (1-min bins)."""
    t0 = calls["start_time"].min()
    calls["rel_end"] = calls["end_time"] - t0

    duration_min = calls["rel_end"].max() / 60
    bins = np.arange(0, duration_min + 1, 1)

    fig, axes = plt.subplots(2, 1, figsize=(14, 7), sharex=True)

    # 3a: Output throughput (tokens/s)
    ax = axes[0]
    calls["bin"] = pd.cut(calls["rel_end"] / 60, bins=bins)
    bin_tp = calls.groupby("bin", observed=False).agg(
        output_tokens=("output_tokens", "sum"),
        count=("output_tokens", "count"),
    )
    throughput = bin_tp["output_tokens"] / 60  # tokens per second
    ax.bar(bins[:-1], throughput.values, width=1, align="edge",
           color="tab:purple", alpha=0.6, label="Output Throughput (tok/s)")
    ax.set_ylabel("tok/s")
    ax.legend(loc="upper right")
    ax.set_title("Per-Minute Output Throughput")

    # 3b: TTFT distribution over time
    ax = axes[1]
    if "first_token_latency" in calls.columns:
        ttft_data = calls.dropna(subset=["first_token_latency", "rel_end"])
        if len(ttft_data) > 0:
            ttft_data["bin"] = pd.cut(ttft_data["rel_end"] / 60, bins=bins)
            ttft_stats = ttft_data.groupby("bin", observed=False).agg(
                p50=("first_token_latency", lambda x: x.quantile(0.5)),
                p90=("first_token_latency", lambda x: x.quantile(0.9)),
                p99=("first_token_latency", lambda x: x.quantile(0.99)),
            )
            ax.plot(bins[:-1], ttft_stats["p50"].values, color="tab:blue",
                    linewidth=2, label="TTFT p50")
            ax.plot(bins[:-1], ttft_stats["p90"].values, color="tab:orange",
                    linewidth=2, label="TTFT p90")
            ax.plot(bins[:-1], ttft_stats["p99"].values, color="tab:red",
                    linewidth=2, label="TTFT p99")
    ax.set_ylabel("Seconds")
    ax.set_xlabel("Time (min)")
    ax.legend(loc="upper right")
    ax.set_title("TTFT Percentiles Over Time")

    fig.suptitle(f"Throughput & Latency — {Path(result_dir).name}", fontsize=14, fontweight="bold")
    fig.tight_layout()
    path = os.path.join(output_dir, "throughput_latency.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_illusion(calls: pd.DataFrame, jobs: pd.DataFrame, result_dir: str, output_dir: str,
                  server_tp: pd.DataFrame = None):
    """Plot: The Illusion of Efficiency — throughput vs goodput over time.

    Left y-axis: Throughput (tok/s), 1-min bins (client) + server gen throughput (line)
    Right y-axis: Running call-level goodput, running job-level goodput (JCR)
    """
    plt.rcParams["font.family"] = "serif"
    plt.rcParams["font.serif"] = ["Liberation Serif", "Times New Roman", "DejaVu Serif"]
    plt.rcParams["mathtext.fontset"] = "stix"

    t0 = calls["start_time"].min()
    calls["rel_start"] = calls["start_time"] - t0
    calls["rel_end"] = calls["end_time"] - t0
    jobs["rel_submit"] = jobs["job_submit_time"] - t0
    jobs["rel_end"] = jobs["job_end_time"] - t0

    duration_min = calls["rel_start"].max() / 60
    bins = np.arange(0, duration_min + 1, 1)
    bin_centers = bins[:-1] + 0.5

    fig, ax1 = plt.subplots(figsize=(10, 5))

    # --- Left y-axis: Client-side throughput (1-min bins) ---
    calls["bin"] = pd.cut(calls["rel_end"] / 60, bins=bins)
    bin_tp = calls.groupby("bin", observed=False).agg(
        output_tokens=("output_tokens", "sum"),
    )
    throughput = bin_tp["output_tokens"] / 60  # tokens per second
    ax1.bar(bin_centers, throughput.values, width=0.9, align="center",
            color="tab:blue", alpha=0.3, label="Client Throughput (tok/s)")

    # --- Left y-axis: Server-side gen throughput (continuous line) ---
    if server_tp is not None and len(server_tp) > 0:
        # Server epoch is from datetime, client t0 is epoch seconds from start_time
        # Both are in epoch seconds, so relative time = server_epoch - t0
        server_tp["rel_time"] = server_tp["epoch"] - t0
        # Drop samples before client start
        server_tp = server_tp[server_tp["rel_time"] >= 0]
        if len(server_tp) > 0:
            server_tp["smooth"] = server_tp["server_throughput"].rolling(10, min_periods=1).mean()
            ax1.plot(server_tp["rel_time"] / 60, server_tp["smooth"],
                     color="tab:cyan", linewidth=2.0, alpha=0.8,
                     label="Server Gen Throughput (tok/s)")

    ax1.set_xlabel("Time (min)", fontsize=13)
    ax1.set_ylabel("Throughput (tok/s)", color="tab:blue", fontsize=13)
    ax1.tick_params(axis="y", labelcolor="tab:blue")
    ax1.set_ylim(bottom=0)

    # --- Right y-axis: Goodput rates ---
    ax2 = ax1.twinx()

    # Running call-level goodput (cumulative success rate)
    calls_sorted = calls.dropna(subset=["rel_start"]).sort_values("rel_start")
    calls_sorted["cum_ok"] = calls_sorted["success"].cumsum()
    calls_sorted["cum_total"] = range(1, len(calls_sorted) + 1)
    calls_sorted["running_call_goodput"] = calls_sorted["cum_ok"] / calls_sorted["cum_total"]

    # Running TBT-based call-level goodput: fraction of calls where tbt_p90_ms <= threshold
    tbt_thresholds_ms = [100, 200, 300]
    tbt_colors = ["#2ca02c", "#98df8a", "#d4e157"]
    tbt_styles = ["-", "-.", ":"]
    for thr, col, sty in zip(tbt_thresholds_ms, tbt_colors, tbt_styles):
        tbt_ok = (calls_sorted["tbt_p90_ms"] <= thr).astype(float)
        cum_tbt_ok = tbt_ok.cumsum()
        running_tbt = cum_tbt_ok / calls_sorted["cum_total"]
        ax2.plot(calls_sorted["rel_start"] / 60, running_tbt,
                 color=col, linewidth=1.8, linestyle=sty,
                 label=f"Call Goodput (TBT$_{{p90}}$≤{thr}ms)")

    # Running job-level goodput (JCR)
    jobs_sorted = jobs.dropna(subset=["rel_submit"]).sort_values("rel_submit")
    jobs_sorted["cum_submitted"] = range(1, len(jobs_sorted) + 1)
    jobs_completed_cum = jobs_sorted["job_completed"].eq(True).cumsum()
    jobs_sorted["running_jcr"] = jobs_completed_cum / jobs_sorted["cum_submitted"]

    ax2.plot(calls_sorted["rel_start"] / 60, calls_sorted["running_call_goodput"],
             color="tab:green", linewidth=2.5, label="Call-level Goodput (success)")
    ax2.plot(jobs_sorted["rel_submit"] / 60, jobs_sorted["running_jcr"],
             color="tab:red", linewidth=2.5, linestyle="--", label="Job-level Goodput (JCR)")
    ax2.set_ylabel("Goodput Rate", fontsize=13)
    ax2.set_ylim(-0.05, 1.05)
    ax2.tick_params(axis="y")

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, loc="center right", fontsize=9,
               framealpha=0.9)

    tag = Path(result_dir).name
    ax1.set_title(f"The Illusion of Efficiency — {tag}", fontsize=14, fontweight="bold")
    fig.tight_layout()

    path = os.path.join(output_dir, "illusion_of_efficiency.png")
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"Saved: {path}")

    # Reset rcParams
    plt.rcParams.update(plt.rcParamsDefault)


def main():
    parser = argparse.ArgumentParser(description="Plot time-series graphs from experiment results")
    parser.add_argument("result_dir", help="Path to experiment result directory (e.g., results/poisson_0.01_...)")
    parser.add_argument("--output-dir", default=None, help="Output directory for figures (default: <result_dir>/figures)")
    args = parser.parse_args()

    csv_path = os.path.join(args.result_dir, "metrics.csv")
    if not os.path.exists(csv_path):
        print(f"ERROR: {csv_path} not found")
        return

    output_dir = args.output_dir or os.path.join(args.result_dir, "figures")
    os.makedirs(output_dir, exist_ok=True)

    print(f"Loading {csv_path}...")
    df = load_metrics(csv_path)
    calls, jobs = split_calls_jobs(df)
    print(f"  {len(calls)} chain_call rows, {len(jobs)} job_summary rows")

    server_tp = load_server_throughput(args.result_dir)
    if len(server_tp) > 0:
        print(f"  {len(server_tp)} server throughput samples")
    else:
        print("  No server throughput log found")

    plot_job_lifecycle(calls, jobs, args.result_dir, output_dir)
    plot_goodput_wcr(calls, jobs, args.result_dir, output_dir)
    plot_throughput(calls, args.result_dir, output_dir)
    plot_illusion(calls, jobs, args.result_dir, output_dir, server_tp=server_tp)

    print(f"\nDone. Figures saved to {output_dir}/")


if __name__ == "__main__":
    main()
