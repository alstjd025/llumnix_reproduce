#!/usr/bin/env python3
"""Plot call/job latency slowdown CDFs against a no-load baseline."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


NUMERIC_COLUMNS = [
    "iteration",
    "call_index",
    "total_calls_expected",
    "start_time",
    "end_time",
    "latency",
    "input_tokens",
    "output_tokens",
    "first_token_latency",
    "decode_speed_tps",
    "job_submit_time",
    "job_end_time",
    "job_timeout_sec",
]


def base_task_id(task_id: object) -> str:
    text = str(task_id)
    return text.split("__replay", 1)[0]


def as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def load_metrics(run_dir: Path) -> pd.DataFrame:
    csv_path = run_dir / "metrics.csv"
    if not csv_path.is_file():
        raise FileNotFoundError(f"metrics.csv not found: {csv_path}")

    df = pd.read_csv(csv_path, skipinitialspace=True)
    df.columns = df.columns.str.strip()
    df = df[df["task_id"].astype(str) != "task_id"].copy()

    for col in NUMERIC_COLUMNS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df["base_task_id"] = df["task_id"].map(base_task_id)
    return df


def split_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    agent = df["agent"].astype(str)
    calls = df[agent.str.startswith("chain_call")].copy()
    jobs = df[agent == "job_summary"].copy()
    if "success" in calls.columns:
        calls["success_bool"] = as_bool(calls["success"])
    if "job_completed" in jobs.columns:
        jobs["job_completed_bool"] = as_bool(jobs["job_completed"])
    return calls, jobs


def build_call_baseline(baseline_calls: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "base_task_id",
        "call_index",
        "latency",
        "input_tokens",
        "output_tokens",
        "first_token_latency",
        "decode_speed_tps",
    ]
    existing = [c for c in cols if c in baseline_calls.columns]
    grouped = (
        baseline_calls[existing]
        .dropna(subset=["base_task_id", "call_index", "latency"])
        .groupby(["base_task_id", "call_index"], as_index=False)
        .median(numeric_only=True)
    )
    return grouped.rename(
        columns={
            "latency": "baseline_latency",
            "input_tokens": "baseline_input_tokens",
            "output_tokens": "baseline_output_tokens",
            "first_token_latency": "baseline_first_token_latency",
            "decode_speed_tps": "baseline_decode_speed_tps",
        }
    )


def build_job_baseline(baseline_jobs: pd.DataFrame) -> pd.DataFrame:
    jobs = baseline_jobs.copy()
    if "job_completed" in jobs.columns:
        jobs = jobs[as_bool(jobs["job_completed"])]
    return (
        jobs[["base_task_id", "latency", "total_calls_expected"]]
        .dropna(subset=["base_task_id", "latency"])
        .groupby("base_task_id", as_index=False)
        .median(numeric_only=True)
        .rename(
            columns={
                "latency": "baseline_latency",
                "total_calls_expected": "baseline_total_calls_expected",
            }
        )
    )


def add_call_slowdown(calls: pd.DataFrame, call_baseline: pd.DataFrame) -> pd.DataFrame:
    merged = calls.merge(
        call_baseline,
        on=["base_task_id", "call_index"],
        how="left",
        validate="many_to_one",
    )
    merged = merged.dropna(subset=["latency", "baseline_latency"]).copy()
    merged = merged[merged["baseline_latency"] > 0]
    merged["slowdown"] = merged["latency"] / merged["baseline_latency"]

    decode_speed = merged["baseline_decode_speed_tps"].replace(0, np.nan)
    output_tokens = merged["output_tokens"].fillna(merged["baseline_output_tokens"])
    input_scale = (
        merged["input_tokens"].fillna(merged["baseline_input_tokens"])
        / merged["baseline_input_tokens"].replace(0, np.nan)
    )
    input_scale = input_scale.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    adjusted_baseline = (
        merged["baseline_first_token_latency"].fillna(0) * input_scale
        + output_tokens / decode_speed
    )
    merged["baseline_latency_token_adjusted"] = adjusted_baseline.where(
        adjusted_baseline.notna() & (adjusted_baseline > 0),
        merged["baseline_latency"],
    )
    merged["slowdown_token_adjusted"] = (
        merged["latency"] / merged["baseline_latency_token_adjusted"]
    )
    return merged


def add_job_slowdown(jobs: pd.DataFrame, job_baseline: pd.DataFrame) -> pd.DataFrame:
    merged = jobs.merge(job_baseline, on="base_task_id", how="left", validate="many_to_one")
    merged = merged.dropna(subset=["latency", "baseline_latency"]).copy()
    merged = merged[merged["baseline_latency"] > 0]
    merged["slowdown"] = merged["latency"] / merged["baseline_latency"]
    if "job_completed" in merged.columns:
        completed = as_bool(merged["job_completed"])
    else:
        completed = pd.Series(False, index=merged.index)
    merged["strict_slowdown"] = merged["slowdown"].where(completed, np.inf)
    return merged


def cdf_xy(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    vals = pd.to_numeric(values, errors="coerce").dropna().to_numpy()
    denom = len(vals)
    finite_vals = np.sort(vals[np.isfinite(vals)])
    if denom == 0 or len(finite_vals) == 0:
        return finite_vals, finite_vals
    return finite_vals, np.arange(1, len(finite_vals) + 1) / denom


def plot_cdf(
    datasets: list[tuple[str, pd.DataFrame]],
    value_col: str,
    tau: float,
    title: str,
    output_path: Path,
) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 4.8))
    for label, df in datasets:
        x, y = cdf_xy(df[value_col])
        if len(x) == 0:
            continue
        ax.step(x, y, where="post", linewidth=2.0, label=f"{label} (n={len(x)})")

    ax.axvline(tau, color="tab:red", linestyle="--", linewidth=1.5, label=f"tau={tau:g}")
    ax.set_xscale("log")
    ax.set_xlabel("Latency slowdown vs baseline (observed / baseline)")
    ax.set_ylabel("CDF: fraction <= slowdown")
    ax.set_title(title)
    ax.grid(True, which="both", alpha=0.25)
    ax.set_ylim(0, 1.02)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(output_path, dpi=180)
    plt.close(fig)


def summarize(label: str, df: pd.DataFrame, value_col: str, tau: float) -> dict[str, object]:
    values = pd.to_numeric(df[value_col], errors="coerce").dropna()
    if values.empty:
        return {"run": label, "n": 0}
    finite = values[np.isfinite(values)]
    return {
        "run": label,
        "n": int(values.size),
        "finite_n": int(finite.size),
        "tau": tau,
        "tau_goodput": float((values <= tau).mean()),
        "p50": float(finite.quantile(0.50)) if not finite.empty else np.nan,
        "p90": float(finite.quantile(0.90)) if not finite.empty else np.nan,
        "p95": float(finite.quantile(0.95)) if not finite.empty else np.nan,
        "p99": float(finite.quantile(0.99)) if not finite.empty else np.nan,
        "max": float(finite.max()) if not finite.empty else np.nan,
        "finite_p50": float(finite.quantile(0.50)) if not finite.empty else np.nan,
        "finite_p90": float(finite.quantile(0.90)) if not finite.empty else np.nan,
        "finite_max": float(finite.max()) if not finite.empty else np.nan,
    }


def read_tau(result_dir: Path, fallback: float) -> float:
    config_path = result_dir / "run_config.json"
    if not config_path.is_file():
        return fallback
    try:
        with config_path.open() as f:
            return float(json.load(f).get("tau", fallback))
    except Exception:
        return fallback


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dirs", nargs="+", help="Experiment result directories")
    parser.add_argument(
        "--baseline-dir",
        required=True,
        help="Baseline result directory containing metrics.csv",
    )
    parser.add_argument("--tau", type=float, default=None, help="Slowdown threshold")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: <first_result_dir>/analysis)",
    )
    args = parser.parse_args()

    result_dirs = [Path(p) for p in args.result_dirs]
    baseline_df = load_metrics(Path(args.baseline_dir))
    baseline_calls, baseline_jobs = split_rows(baseline_df)
    call_baseline = build_call_baseline(baseline_calls)
    job_baseline = build_job_baseline(baseline_jobs)

    output_dir = Path(args.output_dir) if args.output_dir else result_dirs[0] / "analysis"
    output_dir.mkdir(parents=True, exist_ok=True)

    tau = args.tau if args.tau is not None else read_tau(result_dirs[0], 3.0)
    call_sets: list[tuple[str, pd.DataFrame]] = []
    job_sets: list[tuple[str, pd.DataFrame]] = []
    summary_rows: list[dict[str, object]] = []

    for result_dir in result_dirs:
        df = load_metrics(result_dir)
        calls, jobs = split_rows(df)
        call_slowdown = add_call_slowdown(calls, call_baseline)
        job_slowdown = add_job_slowdown(jobs, job_baseline)
        label = result_dir.name

        call_slowdown.to_csv(output_dir / f"{label}_call_slowdown.csv", index=False)
        job_slowdown.to_csv(output_dir / f"{label}_job_slowdown.csv", index=False)
        call_sets.append((label, call_slowdown))
        job_sets.append((label, job_slowdown))
        summary_rows.append(summarize(f"{label}:call_raw", call_slowdown, "slowdown", tau))
        summary_rows.append(
            summarize(
                f"{label}:call_token_adjusted",
                call_slowdown,
                "slowdown_token_adjusted",
                tau,
            )
        )
        summary_rows.append(summarize(f"{label}:job_latency_only", job_slowdown, "slowdown", tau))
        summary_rows.append(summarize(f"{label}:job_strict_completed", job_slowdown, "strict_slowdown", tau))

    plot_cdf(
        call_sets,
        "slowdown",
        tau,
        "Call Latency Slowdown CDF",
        output_dir / "call_latency_slowdown_cdf.png",
    )
    plot_cdf(
        call_sets,
        "slowdown_token_adjusted",
        tau,
        "Call Latency Slowdown CDF (Token Adjusted)",
        output_dir / "call_latency_slowdown_cdf_token_adjusted.png",
    )
    plot_cdf(
        job_sets,
        "slowdown",
        tau,
        "Job Latency Slowdown CDF (Latency Only)",
        output_dir / "job_latency_slowdown_cdf.png",
    )
    plot_cdf(
        job_sets,
        "strict_slowdown",
        tau,
        "Job Latency Slowdown CDF (Completed Jobs Only)",
        output_dir / "job_latency_slowdown_cdf_strict_completed.png",
    )

    summary = pd.DataFrame(summary_rows)
    summary.to_csv(output_dir / "latency_slowdown_summary.csv", index=False)
    print(summary.to_string(index=False))
    print(f"Saved outputs under: {output_dir}")


if __name__ == "__main__":
    main()
