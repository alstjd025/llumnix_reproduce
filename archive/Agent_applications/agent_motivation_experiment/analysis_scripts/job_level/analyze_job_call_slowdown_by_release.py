#!/usr/bin/env python3
"""Analyze job slowdown, internal call slowdown, and release-time effects."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LogNorm
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

from parse_application_metrics import load_metrics, parse_application_metrics, split_metrics


DEFAULT_RUNS = [
    ("0.01", "20260430-173659"),
    ("0.02", "20260430-193949"),
    ("0.05", "20260430-214234"),
    ("0.10", "20260430-235032"),
    ("0.20", "20260501-015420"),
]

OUTCOME_ORDER = [
    "Goodput job",
    "Completed but slow",
    "Incomplete or failed",
    "Server terminated",
]

OUTCOME_COLORS = {
    "Goodput job": "tab:green",
    "Completed but slow": "tab:orange",
    "Incomplete or failed": "tab:red",
    "Server terminated": "tab:purple",
}


def as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def safe_slug(text: str) -> str:
    return text.replace(".", "_").replace("-", "_").replace("/", "_")


def read_config(run_dir: Path) -> dict:
    config_path = run_dir / "run_config.json"
    if not config_path.is_file():
        return {}
    with config_path.open() as f:
        return json.load(f)


def ensure_application_analysis(run_dir: Path, baseline_dir: Path, tau: float) -> Path:
    analysis_dir = run_dir / "analysis"
    needed = [
        analysis_dir / "application_calls.csv",
        analysis_dir / "application_jobs.csv",
    ]
    if not all(path.is_file() for path in needed):
        parse_application_metrics(
            str(run_dir),
            str(analysis_dir),
            baseline_dir=str(baseline_dir),
            tau=tau,
        )
    return analysis_dir


def build_call_baseline_with_tokens(baseline_calls: pd.DataFrame) -> pd.DataFrame:
    cols = [
        "base_task_id",
        "call_index",
        "latency",
        "input_tokens",
        "output_tokens",
        "first_token_latency",
        "decode_speed_tps",
    ]
    numeric_cols = [
        "call_index",
        "latency",
        "input_tokens",
        "output_tokens",
        "first_token_latency",
        "decode_speed_tps",
    ]
    data = baseline_calls[cols].copy()
    for col in numeric_cols:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    baseline = (
        data
        .dropna(subset=["base_task_id", "call_index", "latency"])
        .groupby(["base_task_id", "call_index"], as_index=False)
        .median(numeric_only=True)
    )
    return baseline.rename(
        columns={
            "latency": "baseline_call_latency_from_baseline",
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
        jobs[["base_task_id", "latency"]]
        .dropna(subset=["base_task_id", "latency"])
        .groupby("base_task_id", as_index=False)
        .median(numeric_only=True)
        .rename(columns={"latency": "baseline_job_latency_from_baseline"})
    )


def load_baselines(baseline_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    baseline_df = load_metrics(str(baseline_dir))
    baseline_calls, baseline_jobs = split_metrics(baseline_df)
    return (
        build_call_baseline_with_tokens(baseline_calls),
        build_job_baseline(baseline_jobs),
    )


def add_token_adjusted_slowdown(calls: pd.DataFrame) -> pd.DataFrame:
    calls = calls.copy()
    calls["total_tokens"] = calls["input_tokens"].fillna(0) + calls["output_tokens"].fillna(0)
    calls["baseline_total_tokens"] = (
        calls["baseline_input_tokens"].fillna(0) + calls["baseline_output_tokens"].fillna(0)
    )
    calls["input_token_ratio_vs_baseline"] = (
        calls["input_tokens"] / calls["baseline_input_tokens"].replace(0, np.nan)
    )
    calls["output_token_ratio_vs_baseline"] = (
        calls["output_tokens"] / calls["baseline_output_tokens"].replace(0, np.nan)
    )
    calls["total_token_ratio_vs_baseline"] = (
        calls["total_tokens"] / calls["baseline_total_tokens"].replace(0, np.nan)
    )

    input_scale = calls["input_token_ratio_vs_baseline"].replace([np.inf, -np.inf], np.nan).fillna(1.0)
    decode_speed = calls["baseline_decode_speed_tps"].replace(0, np.nan)
    token_adjusted_baseline = (
        calls["baseline_first_token_latency"].fillna(0) * input_scale
        + calls["output_tokens"].fillna(calls["baseline_output_tokens"]) / decode_speed
    )
    calls["token_adjusted_baseline_call_latency"] = token_adjusted_baseline.where(
        token_adjusted_baseline.notna() & (token_adjusted_baseline > 0),
        calls["baseline_call_latency"],
    )
    calls["token_adjusted_call_slowdown"] = (
        calls["latency"] / calls["token_adjusted_baseline_call_latency"].replace(0, np.nan)
    )
    return calls


def slowdown_bucket(values: pd.Series, tau: float) -> pd.Series:
    bins = [-np.inf, tau, 10.0, 30.0, np.inf]
    labels = [
        f"Normal (< {tau:g}x)",
        f"Slow ({tau:g}-10x)",
        "Very slow (10-30x)",
        "Extreme (>= 30x)",
    ]
    return pd.cut(values, bins=bins, labels=labels, right=False)


def classify_job_outcomes(jobs: pd.DataFrame) -> pd.Series:
    completed = as_bool(jobs["job_completed_bool"] if "job_completed_bool" in jobs else jobs["job_completed"])
    goodput = as_bool(jobs["job_goodput_bool"])
    server_terminated = (
        as_bool(jobs["is_server_terminated"])
        if "is_server_terminated" in jobs.columns
        else pd.Series(False, index=jobs.index)
    )
    outcome = pd.Series("Incomplete or failed", index=jobs.index)
    outcome.loc[server_terminated] = "Server terminated"
    outcome.loc[completed & ~goodput] = "Completed but slow"
    outcome.loc[goodput] = "Goodput job"
    return outcome


def build_run_tables(
    run_dir: Path,
    lambda_label: str,
    baseline_dir: Path,
    call_baseline: pd.DataFrame,
    job_baseline: pd.DataFrame,
    tau: float,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    analysis_dir = ensure_application_analysis(run_dir, baseline_dir, tau)
    calls = pd.read_csv(analysis_dir / "application_calls.csv")
    jobs = pd.read_csv(analysis_dir / "application_jobs.csv")

    calls = calls.merge(call_baseline, on=["base_task_id", "call_index"], how="left", validate="many_to_one")
    calls["baseline_call_latency"] = calls["baseline_call_latency"].combine_first(
        calls["baseline_call_latency_from_baseline"]
    )
    calls = calls.drop(columns=["baseline_call_latency_from_baseline"])
    calls["call_slowdown"] = pd.to_numeric(calls["call_slowdown"], errors="coerce")
    calls["call_goodput_bool"] = as_bool(calls["call_goodput_bool"])
    calls["success_bool"] = as_bool(calls["success_bool"] if "success_bool" in calls else calls["success"])
    calls = add_token_adjusted_slowdown(calls)
    calls["call_slowdown_bucket"] = slowdown_bucket(calls["call_slowdown"], tau)

    jobs = jobs.merge(job_baseline, on="base_task_id", how="left", validate="many_to_one")
    jobs["baseline_job_latency"] = jobs["baseline_job_latency"].combine_first(
        jobs["baseline_job_latency_from_baseline"]
    )
    jobs = jobs.drop(columns=["baseline_job_latency_from_baseline"])
    jobs["job_slowdown"] = pd.to_numeric(jobs["job_slowdown"], errors="coerce")
    jobs["job_goodput_bool"] = as_bool(jobs["job_goodput_bool"])
    jobs["job_completed_bool"] = as_bool(
        jobs["job_completed_bool"] if "job_completed_bool" in jobs else jobs["job_completed"]
    )
    jobs["job_outcome"] = classify_job_outcomes(jobs)

    t0 = np.nanmin(
        [
            pd.to_numeric(jobs["job_submit_time"], errors="coerce").min(),
            pd.to_numeric(calls["start_time"], errors="coerce").min(),
        ]
    )
    calls["relative_call_start_min"] = (calls["start_time"] - t0) / 60.0
    calls["relative_call_end_min"] = (calls["end_time"] - t0) / 60.0
    jobs["relative_release_min"] = (jobs["job_submit_time"] - t0) / 60.0
    jobs["relative_job_end_min"] = (jobs["job_end_time"] - t0) / 60.0

    per_job_calls = (
        calls.groupby("task_id")
        .agg(
            observed_call_count=("call_index", "count"),
            successful_call_count=("success_bool", "sum"),
            call_goodput_count=("call_goodput_bool", "sum"),
            median_call_slowdown=("call_slowdown", "median"),
            p90_call_slowdown=("call_slowdown", lambda s: s.quantile(0.90)),
            max_call_slowdown=("call_slowdown", "max"),
            median_token_adjusted_call_slowdown=("token_adjusted_call_slowdown", "median"),
            p90_token_adjusted_call_slowdown=("token_adjusted_call_slowdown", lambda s: s.quantile(0.90)),
            median_input_tokens=("input_tokens", "median"),
            median_output_tokens=("output_tokens", "median"),
            median_total_tokens=("total_tokens", "median"),
            first_bad_call_index=("call_index", lambda s: np.nan),
            last_observed_call_index=("call_index", "max"),
        )
        .reset_index()
    )
    first_bad = (
        calls.loc[~calls["call_goodput_bool"], ["task_id", "call_index"]]
        .groupby("task_id")["call_index"]
        .min()
        .rename("first_bad_call_index")
        .reset_index()
    )
    per_job_calls = per_job_calls.drop(columns=["first_bad_call_index"]).merge(
        first_bad, on="task_id", how="left"
    )

    job_table = jobs.merge(per_job_calls, on="task_id", how="left")
    count_cols = ["observed_call_count", "successful_call_count", "call_goodput_count"]
    job_table[count_cols] = job_table[count_cols].fillna(0)
    job_table["call_goodput_fraction_within_job"] = (
        job_table["call_goodput_count"] / job_table["observed_call_count"].replace(0, np.nan)
    )
    job_table["observed_call_fraction_of_expected"] = (
        job_table["observed_call_count"] / job_table["total_calls_expected"].replace(0, np.nan)
    )
    job_table["lambda"] = float(lambda_label)
    job_table["run"] = run_dir.name
    calls["lambda"] = float(lambda_label)
    calls["run"] = run_dir.name

    cohort = build_release_cohort_summary(job_table, calls)
    token_summary = build_slow_call_token_summary(calls)
    return job_table, calls, cohort, token_summary


def build_release_cohort_summary(job_table: pd.DataFrame, calls: pd.DataFrame, bin_minutes: float = 5.0) -> pd.DataFrame:
    jobs = job_table.copy()
    jobs["release_time_bin_min"] = (jobs["relative_release_min"] // bin_minutes) * bin_minutes
    call_bins = jobs[["task_id", "release_time_bin_min"]].merge(calls, on="task_id", how="left")

    job_summary = (
        jobs.groupby("release_time_bin_min")
        .agg(
            submitted_jobs=("task_id", "count"),
            job_goodput_rate=("job_goodput_bool", "mean"),
            job_completion_rate=("job_completed_bool", "mean"),
            p50_job_slowdown=("job_slowdown", "median"),
            p90_job_slowdown=("job_slowdown", lambda s: s.quantile(0.90)),
            average_observed_call_fraction=("observed_call_fraction_of_expected", "mean"),
        )
        .reset_index()
    )
    call_summary = (
        call_bins.groupby("release_time_bin_min")
        .agg(
            observed_calls=("call_index", "count"),
            p50_call_slowdown=("call_slowdown", "median"),
            p90_call_slowdown=("call_slowdown", lambda s: s.quantile(0.90)),
            p50_token_adjusted_call_slowdown=("token_adjusted_call_slowdown", "median"),
            p90_token_adjusted_call_slowdown=("token_adjusted_call_slowdown", lambda s: s.quantile(0.90)),
        )
        .reset_index()
    )
    return job_summary.merge(call_summary, on="release_time_bin_min", how="left")


def build_slow_call_token_summary(calls: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        calls.groupby("call_slowdown_bucket", observed=True)
        .agg(
            calls=("task_id", "count"),
            p50_call_slowdown=("call_slowdown", "median"),
            p90_call_slowdown=("call_slowdown", lambda s: s.quantile(0.90)),
            p50_input_tokens=("input_tokens", "median"),
            p90_input_tokens=("input_tokens", lambda s: s.quantile(0.90)),
            p50_output_tokens=("output_tokens", "median"),
            p90_output_tokens=("output_tokens", lambda s: s.quantile(0.90)),
            p50_total_tokens=("total_tokens", "median"),
            p90_total_tokens=("total_tokens", lambda s: s.quantile(0.90)),
            p50_total_token_ratio_vs_baseline=("total_token_ratio_vs_baseline", "median"),
            p90_total_token_ratio_vs_baseline=("total_token_ratio_vs_baseline", lambda s: s.quantile(0.90)),
            p50_token_adjusted_call_slowdown=("token_adjusted_call_slowdown", "median"),
            p90_token_adjusted_call_slowdown=("token_adjusted_call_slowdown", lambda s: s.quantile(0.90)),
        )
        .reset_index()
    )
    return grouped


def finite_positive(series: pd.Series) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce")
    return values[np.isfinite(values) & (values > 0)]


def save_job_slowdown_by_release_time(job_table: pd.DataFrame, tau: float, figures_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(10, 5.5))
    for outcome in OUTCOME_ORDER:
        sub = job_table[job_table["job_outcome"] == outcome]
        if sub.empty:
            continue
        sizes = 25 + 85 * sub["observed_call_fraction_of_expected"].fillna(0).clip(0, 1)
        ax.scatter(
            sub["relative_release_min"],
            sub["job_slowdown"],
            s=sizes,
            alpha=0.70,
            color=OUTCOME_COLORS[outcome],
            label=outcome,
            edgecolors="none",
        )
    ax.axhline(tau, color="black", linestyle="--", linewidth=1.4, label=f"Goodput threshold ({tau:g}x)")
    ax.set_yscale("log")
    ax.set_xlabel("Job release time since run start (min)")
    ax.set_ylabel("Job latency slowdown vs baseline (observed / baseline)")
    ax.set_title("Job Slowdown by Release Time")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = figures_dir / "job_slowdown_by_release_time.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_call_slowdown_heatmap(job_table: pd.DataFrame, calls: pd.DataFrame, tau: float, figures_dir: Path) -> None:
    ordered_jobs = job_table.sort_values("relative_release_min")["task_id"].tolist()
    max_call_index = int(calls["call_index"].max()) if not calls.empty else 0
    matrix = np.full((len(ordered_jobs), max_call_index), np.nan)
    job_to_row = {task_id: idx for idx, task_id in enumerate(ordered_jobs)}
    for row in calls[["task_id", "call_index", "call_slowdown"]].itertuples(index=False):
        if row.task_id in job_to_row and pd.notna(row.call_index) and pd.notna(row.call_slowdown):
            matrix[job_to_row[row.task_id], int(row.call_index) - 1] = row.call_slowdown

    finite = matrix[np.isfinite(matrix) & (matrix > 0)]
    if finite.size == 0:
        return
    vmax = max(tau * 2.0, float(np.nanquantile(finite, 0.98)))
    vmin = max(0.25, min(1.0, float(np.nanquantile(finite, 0.02))))

    fig, ax = plt.subplots(figsize=(11, 7))
    cmap = plt.cm.magma.copy()
    cmap.set_bad("#eeeeee")
    im = ax.imshow(
        matrix,
        aspect="auto",
        interpolation="nearest",
        cmap=cmap,
        norm=LogNorm(vmin=vmin, vmax=vmax),
    )
    ax.set_xlabel("Call index within job")
    ax.set_ylabel("Jobs sorted by release time")
    ax.set_title("Internal Call Slowdown by Job Release Order")
    ax.set_xticks(np.arange(max_call_index))
    ax.set_xticklabels(np.arange(1, max_call_index + 1))
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Call latency slowdown vs baseline")
    fig.tight_layout()
    path = figures_dir / "internal_call_slowdown_heatmap_by_job_release_order.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_job_vs_internal_call_slowdown(job_table: pd.DataFrame, tau: float, figures_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(7.5, 6))
    for outcome in OUTCOME_ORDER:
        sub = job_table[job_table["job_outcome"] == outcome]
        sub = sub.dropna(subset=["job_slowdown", "p90_call_slowdown"])
        if sub.empty:
            continue
        sizes = 25 + 85 * sub["observed_call_fraction_of_expected"].fillna(0).clip(0, 1)
        ax.scatter(
            sub["job_slowdown"],
            sub["p90_call_slowdown"],
            s=sizes,
            alpha=0.68,
            color=OUTCOME_COLORS[outcome],
            label=outcome,
            edgecolors="none",
        )
    ax.axhline(tau, color="gray", linestyle="--", linewidth=1.2)
    ax.axvline(tau, color="gray", linestyle="--", linewidth=1.2)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Job latency slowdown vs baseline")
    ax.set_ylabel("P90 call slowdown inside the job")
    ax.set_title("Job Slowdown vs Internal Call Slowdown")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = figures_dir / "job_slowdown_vs_internal_p90_call_slowdown.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_release_cohort_summary(cohort: pd.DataFrame, tau: float, figures_dir: Path) -> None:
    fig, axes = plt.subplots(2, 1, figsize=(10, 7.5), sharex=True)
    ax = axes[0]
    ax.plot(cohort["release_time_bin_min"], cohort["p50_job_slowdown"], marker="o", label="P50 job slowdown")
    ax.plot(cohort["release_time_bin_min"], cohort["p90_job_slowdown"], marker="o", label="P90 job slowdown")
    ax.plot(cohort["release_time_bin_min"], cohort["p50_call_slowdown"], marker="s", linestyle="--", label="P50 call slowdown")
    ax.plot(cohort["release_time_bin_min"], cohort["p90_call_slowdown"], marker="s", linestyle="--", label="P90 call slowdown")
    ax.axhline(tau, color="black", linestyle=":", linewidth=1.3, label=f"{tau:g}x threshold")
    ax.set_yscale("log")
    ax.set_ylabel("Latency slowdown vs baseline")
    ax.set_title("Release Cohort Slowdown and Survival")
    ax.grid(True, which="both", alpha=0.25)
    ax.legend(fontsize=8, ncol=2)

    ax = axes[1]
    ax.plot(
        cohort["release_time_bin_min"],
        cohort["job_goodput_rate"],
        marker="o",
        color="tab:green",
        label="Job goodput rate",
    )
    ax.plot(
        cohort["release_time_bin_min"],
        cohort["job_completion_rate"],
        marker="o",
        color="tab:blue",
        label="Job completion rate",
    )
    ax.plot(
        cohort["release_time_bin_min"],
        cohort["average_observed_call_fraction"],
        marker="o",
        color="tab:orange",
        label="Observed calls / expected calls",
    )
    ax.set_xlabel("Job release time bin since run start (min)")
    ax.set_ylabel("Rate (%)")
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax.set_ylim(-0.03, 1.03)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = figures_dir / "release_time_cohort_slowdown_and_job_survival.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_slow_call_token_plots(calls: pd.DataFrame, tau: float, figures_dir: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(13, 5.5))
    ax = axes[0]
    scatter = ax.scatter(
        calls["output_tokens"],
        calls["call_slowdown"],
        c=calls["relative_call_start_min"],
        s=14,
        alpha=0.45,
        cmap="viridis",
        edgecolors="none",
    )
    ax.axhline(tau, color="black", linestyle="--", linewidth=1.2)
    ax.set_yscale("log")
    ax.set_xlabel("Output tokens generated by the call")
    ax.set_ylabel("Call latency slowdown vs baseline")
    ax.set_title("Slow Calls vs Output Size")
    ax.grid(True, which="both", alpha=0.25)
    cbar = fig.colorbar(scatter, ax=ax)
    cbar.set_label("Call start time since run start (min)")

    ax = axes[1]
    ax.scatter(
        calls["call_slowdown"],
        calls["token_adjusted_call_slowdown"],
        c=calls["total_token_ratio_vs_baseline"],
        s=14,
        alpha=0.45,
        cmap="plasma",
        edgecolors="none",
    )
    ax.axhline(tau, color="gray", linestyle="--", linewidth=1.2)
    ax.axvline(tau, color="gray", linestyle="--", linewidth=1.2)
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("Raw call slowdown")
    ax.set_ylabel("Token-adjusted call slowdown")
    ax.set_title("Raw vs Token-Adjusted Slowdown")
    ax.grid(True, which="both", alpha=0.25)
    fig.tight_layout()
    path = figures_dir / "slow_call_token_size_and_token_adjusted_slowdown.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_token_boxplot(calls: pd.DataFrame, figures_dir: Path) -> None:
    bucket_order = [str(v) for v in calls["call_slowdown_bucket"].cat.categories]
    data = [
        calls.loc[calls["call_slowdown_bucket"].astype(str) == bucket, "total_tokens"].dropna()
        for bucket in bucket_order
    ]
    fig, ax = plt.subplots(figsize=(9, 5))
    ax.boxplot(data, tick_labels=bucket_order, showfliers=False)
    ax.set_yscale("log")
    ax.set_xlabel("Call slowdown bucket")
    ax.set_ylabel("Total call tokens (input + output)")
    ax.set_title("Token Size Distribution by Call Slowdown Bucket")
    ax.grid(True, which="both", axis="y", alpha=0.25)
    fig.tight_layout()
    path = figures_dir / "total_tokens_by_call_slowdown_bucket.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_run_outputs(
    job_table: pd.DataFrame,
    calls: pd.DataFrame,
    cohort: pd.DataFrame,
    token_summary: pd.DataFrame,
    output_dir: Path,
    tau: float,
) -> None:
    analysis_dir = output_dir / "analysis"
    figures_dir = output_dir / "figures"
    analysis_dir.mkdir(parents=True, exist_ok=True)
    figures_dir.mkdir(parents=True, exist_ok=True)

    job_table.to_csv(analysis_dir / "job_level_slowdown_and_internal_calls.csv", index=False)
    calls.to_csv(analysis_dir / "call_level_slowdown_and_token_context.csv", index=False)
    cohort.to_csv(analysis_dir / "release_time_cohort_slowdown_and_survival.csv", index=False)
    token_summary.to_csv(analysis_dir / "call_slowdown_bucket_token_summary.csv", index=False)

    save_job_slowdown_by_release_time(job_table, tau, figures_dir)
    save_call_slowdown_heatmap(job_table, calls, tau, figures_dir)
    save_job_vs_internal_call_slowdown(job_table, tau, figures_dir)
    save_release_cohort_summary(cohort, tau, figures_dir)
    save_slow_call_token_plots(calls, tau, figures_dir)
    save_token_boxplot(calls, figures_dir)


def parse_run_specs(specs: list[str] | None, results_dir: Path) -> list[tuple[str, Path]]:
    if not specs:
        return [(lam, results_dir / run_name) for lam, run_name in DEFAULT_RUNS]
    parsed: list[tuple[str, Path]] = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"Run spec must look like lambda=path, got: {spec}")
        lam, path = spec.split("=", 1)
        parsed.append((lam, Path(path)))
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory containing experiment run directories.",
    )
    parser.add_argument(
        "--baseline-dir",
        required=True,
        help="Baseline run directory containing metrics.csv.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help=(
            "Output root. Defaults to "
            "<results-dir>/aggregate_analysis/job_call_slowdown_by_release_time."
        ),
    )
    parser.add_argument(
        "--run",
        action="append",
        default=None,
        help="Optional explicit run mapping, format <lambda>=<run_dir>. Repeatable.",
    )
    parser.add_argument("--tau", type=float, default=None, help="Override slowdown threshold.")
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    baseline_dir = Path(args.baseline_dir)
    output_root = (
        Path(args.output_dir)
        if args.output_dir
        else results_dir / "aggregate_analysis" / "job_call_slowdown_by_release_time"
    )
    call_baseline, job_baseline = load_baselines(baseline_dir)

    for lambda_label, run_dir in parse_run_specs(args.run, results_dir):
        if not run_dir.is_absolute() and not run_dir.is_dir():
            run_dir = results_dir / run_dir
        if not run_dir.is_dir():
            raise FileNotFoundError(f"Run directory not found: {run_dir}")
        config = read_config(run_dir)
        tau = float(args.tau if args.tau is not None else config.get("tau", 3.0))
        run_output_dir = output_root / f"lambda_{safe_slug(lambda_label)}__{run_dir.name}"
        print(f"Analyzing lambda={lambda_label} run={run_dir.name}")
        job_table, calls, cohort, token_summary = build_run_tables(
            run_dir=run_dir,
            lambda_label=lambda_label,
            baseline_dir=baseline_dir,
            call_baseline=call_baseline,
            job_baseline=job_baseline,
            tau=tau,
        )
        save_run_outputs(job_table, calls, cohort, token_summary, run_output_dir, tau)
        print(f"  analysis: {run_output_dir / 'analysis'}")
        print(f"  figures:  {run_output_dir / 'figures'}")


if __name__ == "__main__":
    main()
