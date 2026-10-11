#!/usr/bin/env python3
"""Plot SGLang server metrics from parse_server_logs.py CSV output."""

import argparse
import json
import os
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from parse_server_logs import parse_server_logs


def _infer_max_min(result_dirs: list[str], explicit_max_min: float | None) -> float | None:
    if explicit_max_min is not None:
        return explicit_max_min
    durations = []
    for result_dir in result_dirs:
        config_path = Path(result_dir) / "run_config.json"
        if not config_path.is_file():
            continue
        with open(config_path) as f:
            config = json.load(f)
        if config.get("duration_min") is not None:
            durations.append(float(config["duration_min"]))
    return max(durations) if durations else None


def _load_or_parse(result_dirs: list[str], input_csv: str | None) -> tuple[pd.DataFrame, str]:
    if input_csv:
        return pd.read_csv(input_csv), str(Path(input_csv).parent)

    output_dir = Path(result_dirs[0]) / "analysis"
    output_csv = output_dir / "server_metrics.csv"
    if not output_csv.is_file():
        output_dir.mkdir(parents=True, exist_ok=True)
        df = parse_server_logs(result_dirs)
        df.to_csv(output_csv, index=False)
    return pd.read_csv(output_csv), str(output_dir)


def plot_one_experiment(
    df: pd.DataFrame,
    experiment: str,
    output_dir: str,
    max_min: float | None = None,
):
    sub_df = df[df["experiment"].astype(str) == str(experiment)].copy()
    if sub_df.empty:
        return

    sub_df["time_min"] = pd.to_numeric(sub_df["rel_time"], errors="coerce") / 60.0
    sub_df = sub_df[sub_df["time_min"] >= 0]
    if max_min is not None:
        sub_df = sub_df[sub_df["time_min"] <= max_min]

    fig, axes = plt.subplots(3, 2, figsize=(16, 14), sharex=True)
    decode = sub_df[sub_df["event_type"] == "decode"].sort_values("time_min").copy()
    prefill = sub_df[sub_df["event_type"] == "prefill"].sort_values("time_min").copy()
    req = sub_df[sub_df["event_type"] == "req_stats"].sort_values("time_min").copy()

    ax = axes[0, 0]
    if not decode.empty:
        decode["gen_smooth"] = decode["gen_throughput"].rolling(10, min_periods=1).mean()
        ax.plot(decode["time_min"], decode["gen_smooth"], color="tab:blue", linewidth=1.5)
    ax.set_ylabel("Decode tok/s")
    ax.set_title("Decode throughput")

    ax = axes[0, 1]
    if not prefill.empty:
        prefill["prefill_smooth"] = prefill["prefill_throughput"].rolling(10, min_periods=1).mean()
        ax.plot(prefill["time_min"], prefill["prefill_smooth"], color="tab:green", linewidth=1.5)
    ax.set_ylabel("Prefill tok/s")
    ax.set_title("Prefill throughput")

    ax = axes[1, 0]
    if not decode.empty:
        decode["token_usage_smooth"] = decode["token_usage"].rolling(10, min_periods=1).mean()
        ax.plot(decode["time_min"], decode["token_usage_smooth"], color="tab:blue", linewidth=1.5)
        ax.axhline(y=0.9, color="red", linestyle="--", alpha=0.5, linewidth=0.8)
    ax.set_ylabel("Token usage")
    ax.set_ylim(-0.02, 1.02)
    ax.set_title("KV/token usage")

    ax = axes[1, 1]
    if not decode.empty:
        decode["running_smooth"] = decode["running_req"].rolling(10, min_periods=1).mean()
        decode["queue_smooth"] = decode["queue_req"].rolling(10, min_periods=1).mean()
        ax.plot(decode["time_min"], decode["running_smooth"], label="running", linewidth=1.5)
        ax.plot(decode["time_min"], decode["queue_smooth"], label="queued", linewidth=1.5)
        ax.legend()
    ax.set_ylabel("Requests")
    ax.set_title("Running and queued requests")

    ax = axes[2, 0]
    if not req.empty:
        req["time_bin"] = req["time_min"].astype(int)
        binned = req.groupby("time_bin")["queue_duration_ms"].median().reset_index()
        ax.plot(binned["time_bin"], binned["queue_duration_ms"], color="tab:orange", linewidth=1.5)
    ax.set_ylabel("Queue ms")
    ax.set_xlabel("Time (min)")
    ax.set_title("Median queue duration")

    ax = axes[2, 1]
    if not req.empty:
        binned = req.groupby("time_bin")["forward_duration_ms"].median().reset_index()
        ax.plot(binned["time_bin"], binned["forward_duration_ms"], color="tab:purple", linewidth=1.5)
    ax.set_ylabel("Forward ms")
    ax.set_xlabel("Time (min)")
    ax.set_title("Median forward duration")

    fig.suptitle(f"Server metrics - {experiment}", fontsize=16, fontweight="bold")
    fig.tight_layout()
    safe_name = str(experiment).replace("=", "_").replace("/", "_").replace(" ", "_")
    path = Path(output_dir) / f"server_metrics_{safe_name}.png"
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    print(f"Saved: {path}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dirs", nargs="*", help="Experiment result directories")
    parser.add_argument("--input-csv", default=None, help="CSV from parse_server_logs.py")
    parser.add_argument("--output-dir", default=None, help="Figure output directory")
    parser.add_argument(
        "--max-min",
        type=float,
        default=None,
        help="Maximum time in minutes to plot. Defaults to run_config duration_min when available.",
    )
    args = parser.parse_args()

    if not args.input_csv and not args.result_dirs:
        parser.error("provide --input-csv or at least one result_dir")

    df, csv_dir = _load_or_parse(args.result_dirs, args.input_csv)
    max_min = _infer_max_min(args.result_dirs, args.max_min)
    output_dir = args.output_dir or (
        os.path.join(args.result_dirs[0], "figures") if args.result_dirs else csv_dir
    )
    os.makedirs(output_dir, exist_ok=True)

    if df.empty:
        print("No server metrics to plot.")
        return

    for experiment in sorted(df["experiment"].dropna().unique()):
        plot_one_experiment(df, experiment, output_dir, max_min=max_min)


if __name__ == "__main__":
    main()
