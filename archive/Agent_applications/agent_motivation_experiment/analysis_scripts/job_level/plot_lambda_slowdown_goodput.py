#!/usr/bin/env python3
"""Plot lambda-wise call slowdown and application goodput summaries."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd

from parse_application_metrics import parse_application_metrics


def as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().isin({"true", "1", "yes"})


def read_config(run_dir: Path) -> dict:
    config_path = run_dir / "run_config.json"
    if not config_path.is_file():
        return {}
    with config_path.open() as f:
        return json.load(f)


def discover_poisson_runs(results_dir: Path) -> list[Path]:
    runs: list[tuple[float, Path]] = []
    for run_dir in sorted(results_dir.iterdir()):
        if not run_dir.is_dir() or not (run_dir / "metrics.csv").is_file():
            continue
        config = read_config(run_dir)
        lam = config.get("lambda")
        if lam is None:
            continue
        runs.append((float(lam), run_dir))
    return [run_dir for _, run_dir in sorted(runs, key=lambda item: item[0])]


def load_or_parse_analysis(run_dir: Path, baseline_dir: str | None, tau: float | None) -> Path:
    analysis_dir = run_dir / "analysis"
    required = [
        analysis_dir / "application_calls.csv",
        analysis_dir / "application_jobs.csv",
        analysis_dir / "application_summary.csv",
    ]
    if not all(path.is_file() for path in required):
        parse_application_metrics(
            str(run_dir),
            str(analysis_dir),
            baseline_dir=baseline_dir,
            tau=tau,
        )
    return analysis_dir


def summarize_slowdown(run: str, lam: float, calls: pd.DataFrame, tau: float) -> dict[str, object]:
    slowdown = pd.to_numeric(calls["call_slowdown"], errors="coerce")
    slowdown = slowdown[np.isfinite(slowdown)].dropna()
    if slowdown.empty:
        return {"run": run, "lambda": lam, "n_calls": 0, "tau": tau}
    return {
        "run": run,
        "lambda": lam,
        "n_calls": int(slowdown.size),
        "tau": tau,
        "latency_under_tau_rate": float((slowdown < tau).mean()),
        "p50_slowdown": float(slowdown.quantile(0.50)),
        "p75_slowdown": float(slowdown.quantile(0.75)),
        "p90_slowdown": float(slowdown.quantile(0.90)),
        "p95_slowdown": float(slowdown.quantile(0.95)),
        "p99_slowdown": float(slowdown.quantile(0.99)),
        "max_slowdown": float(slowdown.max()),
    }


def cdf_xy(values: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    vals = pd.to_numeric(values, errors="coerce")
    vals = np.sort(vals[np.isfinite(vals)].dropna().to_numpy())
    if len(vals) == 0:
        return vals, vals
    return vals, np.arange(1, len(vals) + 1) / len(vals)


def plot_slowdown_cdf(call_sets: list[tuple[float, str, pd.DataFrame]], tau: float, output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    cmap = plt.cm.viridis
    colors = [cmap(i / max(len(call_sets) - 1, 1)) for i in range(len(call_sets))]
    for color, (lam, label, calls) in zip(colors, call_sets):
        x, y = cdf_xy(calls["call_slowdown"])
        if len(x) == 0:
            continue
        ax.step(x, y, where="post", linewidth=2.0, color=color, label=f"lambda={lam:g} ({label})")

    ax.axvline(tau, color="tab:red", linestyle="--", linewidth=1.5, label=f"tau={tau:g}")
    ax.set_xscale("log")
    ax.set_xlabel("Call latency slowdown vs baseline")
    ax.set_ylabel("Calls <= slowdown (%)")
    ax.set_title("Call Latency Slowdown CDF by Lambda")
    ax.grid(True, which="both", alpha=0.25)
    ax.set_ylim(0, 1.02)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax.legend(fontsize=8)
    fig.tight_layout()
    path = output_dir / "call_slowdown_cdf_by_lambda.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_slowdown_quantiles(summary: pd.DataFrame, tau: float, output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    summary = summary.sort_values("lambda")
    x = summary["lambda"]
    for col, label, color in [
        ("p50_slowdown", "p50", "tab:blue"),
        ("p90_slowdown", "p90", "tab:orange"),
        ("p95_slowdown", "p95", "tab:red"),
    ]:
        ax.plot(
            x,
            summary[col] * 100.0,
            marker="o",
            linestyle="--",
            linewidth=2.0,
            color=color,
            label=label,
        )

    ax.axhline(tau * 100.0, color="black", linestyle=":", linewidth=1.4, label=f"tau={tau:g}")
    ax.set_xlabel("Poisson lambda")
    ax.set_ylabel("Observed latency / baseline latency (%)")
    ax.set_title("Call Slowdown Quantiles by Lambda")
    ax.grid(True, which="both", alpha=0.25)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=100.0))
    ax.legend()
    fig.tight_layout()
    path = output_dir / "call_slowdown_quantiles_by_lambda.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"Saved: {path}")


def plot_goodput(summary: pd.DataFrame, output_dir: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 5))
    summary = summary.sort_values("lambda")
    x = summary["lambda"]
    ax.plot(
        x,
        summary["call_goodput_rate"],
        marker="o",
        linestyle="--",
        linewidth=2.2,
        color="tab:green",
        label="Call-level goodput",
    )
    ax.plot(
        x,
        summary["job_goodput_rate"],
        marker="s",
        linestyle="--",
        linewidth=2.2,
        color="tab:red",
        label="Job-level goodput",
    )
    ax.set_xlabel("Poisson lambda")
    ax.set_ylabel("Goodput (%)")
    ax.set_title("Goodput Ratio by Lambda")
    ax.set_ylim(-0.03, 1.03)
    ax.grid(True, which="both", alpha=0.25)
    ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0))
    ax.legend()
    fig.tight_layout()
    path = output_dir / "goodput_ratio_by_lambda.png"
    fig.savefig(path, dpi=180)
    plt.close(fig)
    print(f"Saved: {path}")


def build_outputs(run_dirs: list[Path], baseline_dir: str | None, tau_arg: float | None, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    call_sets: list[tuple[float, str, pd.DataFrame]] = []
    slowdown_rows: list[dict[str, object]] = []
    goodput_rows: list[dict[str, object]] = []
    per_call_rows: list[pd.DataFrame] = []

    for run_dir in run_dirs:
        config = read_config(run_dir)
        lam = config.get("lambda")
        if lam is None:
            print(f"Skipping non-lambda run: {run_dir}")
            continue
        lam = float(lam)
        tau = float(tau_arg if tau_arg is not None else config.get("tau", 3.0))
        analysis_dir = load_or_parse_analysis(run_dir, baseline_dir, tau)

        calls = pd.read_csv(analysis_dir / "application_calls.csv")
        jobs = pd.read_csv(analysis_dir / "application_jobs.csv")
        run_summary = pd.read_csv(analysis_dir / "application_summary.csv")
        calls["call_slowdown"] = pd.to_numeric(calls["call_slowdown"], errors="coerce")
        calls["lambda"] = lam
        calls["run"] = run_dir.name
        calls["call_goodput_bool"] = as_bool(calls["call_goodput_bool"])
        jobs["job_goodput_bool"] = as_bool(jobs["job_goodput_bool"])

        row = run_summary.iloc[0].to_dict()
        row.update(
            {
                "run": run_dir.name,
                "lambda": lam,
                "tau": tau,
                "computed_call_goodput_rate": float(calls["call_goodput_bool"].mean()),
                "computed_job_goodput_rate": float(jobs["job_goodput_bool"].mean()),
            }
        )
        goodput_rows.append(row)
        slowdown_rows.append(summarize_slowdown(run_dir.name, lam, calls, tau))
        call_sets.append((lam, run_dir.name, calls))
        per_call_rows.append(
            calls[
                [
                    "run",
                    "lambda",
                    "task_id",
                    "base_task_id",
                    "call_index",
                    "total_calls_expected",
                    "latency",
                    "baseline_call_latency",
                    "call_slowdown",
                    "call_latency_threshold",
                    "call_goodput_bool",
                    "success_bool",
                    "input_tokens",
                    "output_tokens",
                ]
            ].copy()
        )

    if not goodput_rows:
        raise RuntimeError("No lambda runs found.")

    goodput = pd.DataFrame(goodput_rows).sort_values("lambda")
    slowdown = pd.DataFrame(slowdown_rows).sort_values("lambda")
    per_call = pd.concat(per_call_rows, ignore_index=True)

    goodput.to_csv(output_dir / "goodput_by_lambda.csv", index=False)
    slowdown.to_csv(output_dir / "call_slowdown_by_lambda_summary.csv", index=False)
    per_call.to_csv(output_dir / "call_slowdown_by_lambda_long.csv", index=False)
    print(f"Saved: {output_dir / 'goodput_by_lambda.csv'}")
    print(f"Saved: {output_dir / 'call_slowdown_by_lambda_summary.csv'}")
    print(f"Saved: {output_dir / 'call_slowdown_by_lambda_long.csv'}")

    tau = float(goodput["tau"].iloc[0])
    plot_slowdown_cdf(call_sets, tau, output_dir)
    plot_slowdown_quantiles(slowdown, tau, output_dir)
    plot_goodput(goodput, output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--results-dir",
        default="results",
        help="Directory containing experiment run directories.",
    )
    parser.add_argument(
        "--run-dirs",
        nargs="*",
        default=None,
        help="Explicit run directories. Defaults to all runs under --results-dir with lambda in run_config.json.",
    )
    parser.add_argument(
        "--baseline-dir",
        default=None,
        help="Baseline run directory. Used only if application analysis CSVs need to be regenerated.",
    )
    parser.add_argument("--tau", type=float, default=None, help="Override slowdown threshold.")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory. Defaults to <results-dir>/aggregate_analysis/lambda_slowdown_goodput.",
    )
    args = parser.parse_args()

    results_dir = Path(args.results_dir)
    run_dirs = [Path(p) for p in args.run_dirs] if args.run_dirs else discover_poisson_runs(results_dir)
    output_dir = (
        Path(args.output_dir)
        if args.output_dir
        else results_dir / "aggregate_analysis" / "lambda_slowdown_goodput"
    )
    build_outputs(run_dirs, args.baseline_dir, args.tau, output_dir)


if __name__ == "__main__":
    main()
