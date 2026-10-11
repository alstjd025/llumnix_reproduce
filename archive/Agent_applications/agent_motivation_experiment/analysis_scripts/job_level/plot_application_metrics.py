#!/usr/bin/env python3
"""Plot application-side metrics from CSVs produced by parse_application_metrics.py."""

import argparse
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import pandas as pd

from parse_application_metrics import build_call_job_goodput_tables, parse_application_metrics
from parse_server_logs import parse_server_logs


CALL_BUCKET_LABELS = {
    "call_goodput__job_not_goodput": "Call goodput / Job not goodput",
    "call_not_goodput__job_not_goodput": "Call not goodput / Job not goodput",
    "call_goodput__job_goodput": "Call goodput / Job goodput",
    "call_not_goodput__job_goodput": "Call not goodput / Job goodput",
    "unclassified": "Unclassified",
}

CALL_BUCKET_COLORS = {
    "call_goodput__job_not_goodput": "tab:orange",
    "call_not_goodput__job_not_goodput": "tab:red",
    "call_goodput__job_goodput": "tab:green",
    "call_not_goodput__job_goodput": "tab:purple",
    "unclassified": "0.7",
}

JOB_BUCKET_LABELS = {
    "all_observed_calls_goodput__job_not_goodput": "All observed calls goodput / Job not goodput",
    "some_observed_call_not_goodput__job_not_goodput": "Some call not goodput / Job not goodput",
    "all_observed_calls_goodput__job_goodput": "All observed calls goodput / Job goodput",
    "some_observed_call_not_goodput__job_goodput": "Some call not goodput / Job goodput",
    "no_classifiable_observed_calls__job_not_goodput": "No classifiable calls / Job not goodput",
    "no_classifiable_observed_calls__job_goodput": "No classifiable calls / Job goodput",
    "unclassified": "Unclassified",
}

JOB_BUCKET_COLORS = {
    "all_observed_calls_goodput__job_not_goodput": "tab:orange",
    "some_observed_call_not_goodput__job_not_goodput": "tab:red",
    "all_observed_calls_goodput__job_goodput": "tab:green",
    "some_observed_call_not_goodput__job_goodput": "tab:purple",
    "no_classifiable_observed_calls__job_not_goodput": "tab:brown",
    "no_classifiable_observed_calls__job_goodput": "tab:blue",
    "unclassified": "0.7",
}


def _load_or_parse(
    result_dir: str,
    input_dir: str | None,
    baseline_dir: str | None = None,
    tau: float | None = None,
) -> str:
    if input_dir:
        return input_dir
    output_dir = os.path.join(result_dir, "analysis")
    expected = Path(output_dir) / "application_timeseries.csv"
    if not expected.is_file():
        parse_application_metrics(result_dir, output_dir, baseline_dir=baseline_dir, tau=tau)
    return output_dir


def _load_or_parse_server_metrics(result_dir: str, server_metrics_csv: str | None) -> pd.DataFrame:
    if server_metrics_csv:
        path = Path(server_metrics_csv)
        return pd.read_csv(path) if path.is_file() else pd.DataFrame()

    output_dir = Path(result_dir) / "analysis"
    output_csv = output_dir / "server_metrics.csv"
    if output_csv.is_file():
        return pd.read_csv(output_csv)

    if any(Path(result_dir).glob("server.stderr*")):
        output_dir.mkdir(parents=True, exist_ok=True)
        df = parse_server_logs([result_dir])
        df.to_csv(output_csv, index=False)
        return df

    return pd.DataFrame()


def _load_or_build_call_job_goodput(csv_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    csv_path = Path(csv_dir)
    call_summary_path = csv_path / "application_call_job_goodput_summary.csv"
    job_summary_path = csv_path / "application_job_call_goodput_summary.csv"
    long_path = csv_path / "application_call_job_goodput.csv"
    if call_summary_path.is_file() and job_summary_path.is_file():
        return pd.read_csv(call_summary_path), pd.read_csv(job_summary_path)

    calls_path = csv_path / "application_calls.csv"
    jobs_path = csv_path / "application_jobs.csv"
    if not calls_path.is_file() or not jobs_path.is_file():
        return pd.DataFrame(), pd.DataFrame()

    calls = pd.read_csv(calls_path)
    jobs = pd.read_csv(jobs_path)
    call_table, call_summary, job_summary = build_call_job_goodput_tables(calls, jobs)
    call_table.to_csv(long_path, index=False)
    call_summary.to_csv(call_summary_path, index=False)
    job_summary.to_csv(job_summary_path, index=False)
    print(f"Saved: {long_path}")
    print(f"Saved: {call_summary_path}")
    print(f"Saved: {job_summary_path}")
    return call_summary, job_summary


def _server_decode_throughput_by_minute(server_df: pd.DataFrame) -> pd.DataFrame:
    if server_df.empty or "event_type" not in server_df.columns:
        return pd.DataFrame()

    decode = server_df[server_df["event_type"].astype(str) == "decode"].copy()
    if decode.empty:
        return pd.DataFrame()

    decode["time_min"] = pd.to_numeric(decode["rel_time"], errors="coerce") / 60.0
    decode["gen_throughput"] = pd.to_numeric(decode["gen_throughput"], errors="coerce")
    decode = decode.dropna(subset=["time_min", "gen_throughput"])
    decode = decode[decode["time_min"] >= 0]
    if decode.empty:
        return pd.DataFrame()

    decode["minute"] = decode["time_min"].astype(int)
    return (
        decode.groupby("minute", as_index=False)["gen_throughput"]
        .mean()
        .rename(columns={"gen_throughput": "server_decode_tokens_per_s"})
    )


def plot_throughput_goodput(csv_dir: str, output_dir: str, server_df: pd.DataFrame | None = None):
    ts = pd.read_csv(Path(csv_dir) / "application_timeseries.csv")
    fig, ax1 = plt.subplots(figsize=(10, 5))
    server_tp = _server_decode_throughput_by_minute(server_df if server_df is not None else pd.DataFrame())
    if not server_tp.empty:
        server_tp = server_tp[server_tp["minute"] <= ts["minute"].max()]
        ax1.plot(
            server_tp["minute"],
            server_tp["server_decode_tokens_per_s"],
            color="tab:blue",
            linewidth=1.8,
            label="Server decode throughput",
        )
        ax1.fill_between(
            server_tp["minute"],
            0,
            server_tp["server_decode_tokens_per_s"],
            color="tab:blue",
            alpha=0.15,
        )
        ax1.set_ylabel("Server decode tokens/s", color="tab:blue")
    else:
        ax1.bar(ts["minute"], ts["output_tokens_per_s"], color="tab:blue", alpha=0.35,
                label="Application output throughput")
        ax1.set_ylabel("Application output tokens/s", color="tab:blue")
    ax1.set_xlabel("Time (min)")
    ax1.tick_params(axis="y", labelcolor="tab:blue")

    ax2 = ax1.twinx()
    ax2.plot(ts["minute"], ts["call_goodput_rate"], color="tab:green", linewidth=2,
             label="Call goodput")
    ax2.plot(ts["minute"], ts["job_goodput_rate"], color="tab:red", linewidth=2,
             linestyle="--", label="Job goodput")
    if "call_rejection_rate" in ts.columns:
        ax2.plot(
            ts["minute"],
            ts["call_rejection_rate"],
            color="tab:orange",
            linewidth=1.8,
            linestyle=":",
            label="Admission rejection",
        )
    ax2.set_ylabel("Goodput / rejection rate")
    ax2.set_ylim(-0.05, 1.05)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, loc="center right")
    fig.tight_layout()
    path = Path(output_dir) / "application_throughput_goodput.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")


def _plot_stacked_breakdown(
    summary: pd.DataFrame,
    bucket_col: str,
    count_col: str,
    rate_col: str,
    labels: dict[str, str],
    colors: dict[str, str],
    title: str,
    output_path: Path,
) -> None:
    if summary.empty:
        return
    plot_df = summary.copy()
    plot_df[count_col] = pd.to_numeric(plot_df[count_col], errors="coerce").fillna(0)
    plot_df[rate_col] = pd.to_numeric(plot_df[rate_col], errors="coerce")
    plot_df = plot_df[(plot_df[count_col] > 0) & plot_df[rate_col].notna()]
    if plot_df.empty:
        return

    fig, ax = plt.subplots(figsize=(10, 2.8))
    left = 0.0
    for _, row in plot_df.iterrows():
        bucket = row[bucket_col]
        rate = float(row[rate_col])
        count = int(row[count_col])
        ax.barh(
            ["Share"],
            [rate],
            left=left,
            color=colors.get(bucket, "0.7"),
            label=labels.get(bucket, bucket),
        )
        if rate >= 0.055:
            ax.text(
                left + rate / 2,
                0,
                f"{rate:.1%}\n{count:,}",
                ha="center",
                va="center",
                color="white",
                fontsize=9,
                fontweight="bold",
            )
        left += rate

    ax.set_xlim(0, 1)
    ax.xaxis.set_major_formatter(PercentFormatter(1.0))
    ax.set_xlabel("Share")
    ax.set_title(title)
    ax.grid(axis="x", alpha=0.25)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.32), ncol=2, fontsize=8)
    fig.tight_layout()
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {output_path}")


def plot_call_job_goodput_breakdowns(csv_dir: str, output_dir: str) -> None:
    call_summary, job_summary = _load_or_build_call_job_goodput(csv_dir)
    output = Path(output_dir)
    _plot_stacked_breakdown(
        call_summary,
        bucket_col="call_job_goodput_bucket",
        count_col="call_count",
        rate_col="call_rate",
        labels=CALL_BUCKET_LABELS,
        colors=CALL_BUCKET_COLORS,
        title="Call-Level Goodput Split by Parent Job Goodput",
        output_path=output / "call_job_goodput_breakdown.png",
    )
    _plot_stacked_breakdown(
        job_summary,
        bucket_col="job_call_goodput_bucket",
        count_col="job_count",
        rate_col="job_rate",
        labels=JOB_BUCKET_LABELS,
        colors=JOB_BUCKET_COLORS,
        title="Job-Level Split by Observed Call Goodput",
        output_path=output / "job_call_goodput_breakdown.png",
    )


def plot_wcr(csv_dir: str, output_dir: str):
    ts = pd.read_csv(Path(csv_dir) / "application_timeseries.csv")
    fig, ax1 = plt.subplots(figsize=(10, 5))
    minute = ts["minute"]
    goodput_m = ts["goodput_tokens"] / 1e6
    total_m = ts["classified_total_tokens"] / 1e6
    wasted_m = ts["wasted_tokens"] / 1e6

    ax1.fill_between(minute, 0, goodput_m, color="tab:green", alpha=0.45,
                     label="Goodput tokens")
    ax1.fill_between(minute, goodput_m, total_m, color="tab:red", alpha=0.35,
                     label="Wasted tokens")
    ax1.plot(minute, total_m, color="black", linewidth=1.4, label="Total tokens")
    ax1.plot(minute, wasted_m, color="tab:red", linewidth=1.0, alpha=0.8)
    ax1.set_xlabel("Time (min)")
    ax1.set_ylabel("Tokens (M)")
    ax1.set_ylim(bottom=0)

    ax2 = ax1.twinx()
    ax2.plot(minute, ts["running_wcr"], color="tab:orange", linewidth=2.2,
             linestyle="--", label="WCR")
    ax2.set_ylabel("WCR")
    ax2.set_ylim(-0.05, 1.05)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax2.legend(lines1 + lines2, labels1 + labels2, loc="upper left")
    fig.tight_layout()
    path = Path(output_dir) / "wcr.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", help="Experiment result directory")
    parser.add_argument(
        "--input-dir",
        default=None,
        help="Directory containing parsed application CSVs. Defaults to <result_dir>/analysis.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Figure output directory. Defaults to <result_dir>/figures.",
    )
    parser.add_argument(
        "--baseline-dir",
        default=None,
        help="Baseline run directory. Used only if parsing is needed.",
    )
    parser.add_argument(
        "--tau",
        type=float,
        default=None,
        help="Latency slowdown threshold. Used only if parsing is needed.",
    )
    parser.add_argument(
        "--server-metrics-csv",
        default=None,
        help="CSV from parse_server_logs.py. Defaults to <result_dir>/analysis/server_metrics.csv.",
    )
    args = parser.parse_args()

    csv_dir = _load_or_parse(
        args.result_dir,
        args.input_dir,
        baseline_dir=args.baseline_dir,
        tau=args.tau,
    )
    server_df = _load_or_parse_server_metrics(args.result_dir, args.server_metrics_csv)
    output_dir = args.output_dir or os.path.join(args.result_dir, "figures")
    os.makedirs(output_dir, exist_ok=True)
    plot_throughput_goodput(csv_dir, output_dir, server_df=server_df)
    plot_wcr(csv_dir, output_dir)
    plot_call_job_goodput_breakdowns(csv_dir, output_dir)


if __name__ == "__main__":
    main()
