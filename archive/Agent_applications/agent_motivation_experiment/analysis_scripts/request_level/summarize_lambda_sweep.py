#!/usr/bin/env python3
"""Cross-run request-level summary for a lambda sweep.

Given a list of run directories (each one produced by
``parse_request_summary.py``), build a single CSV table indexed by
``lambda``, one row per run. Useful for ShareGPT-style request-level
Poisson sweeps where each lambda condition is one run.

Inputs:
  --run-dirs <dir> [<dir> ...]   one or more run directories. Each must
                                  contain analysis/request_summary.csv
                                  (run parse_request_summary.py first).

Outputs:
  --output-csv <path>             cross-lambda summary CSV (one row per run)
  --output-dir <dir>              if set, writes summary CSV plus optional
                                  PNG plots into this directory
  --plot-png                       enable matplotlib plots (lambda ->
                                  throughput, p90 latency, server-terminated%)

This script intentionally does not compute SLO goodput — see
``parse_request_summary.py`` for the rationale.
"""

import argparse
import os
from pathlib import Path

import pandas as pd


# Column order in the cross-run CSV — keep stable so downstream consumers
# can index by name.
SUMMARY_COLUMNS = [
    "lambda",
    "run_dir",
    "workload",
    "duration_min_cfg",
    "duration_min_observed",
    "requests_submitted",
    "requests_ok",
    "requests_terminated",
    "requests_error",
    "requests_rejected",
    "requests_job_timeout",
    "server_terminated_pct",
    "error_pct",
    "rejected_pct",
    "throughput_rps_ok",
    "output_tokens_per_s",
    "input_tokens_per_s",
    "output_tokens_total",
    "latency_s_mean", "latency_s_p50", "latency_s_p90", "latency_s_p99",
    "ttft_s_mean", "ttft_s_p50", "ttft_s_p90", "ttft_s_p99",
    "tbt_mean_ms_mean", "tbt_mean_ms_p50", "tbt_mean_ms_p90", "tbt_mean_ms_p99",
]


def load_summary(run_dir: Path) -> dict:
    """Read one run's request_summary.csv into a flat dict row."""
    csv = run_dir / "analysis" / "request_summary.csv"
    if not csv.exists():
        raise FileNotFoundError(
            f"missing {csv}: run parse_request_summary.py first"
        )
    df = pd.read_csv(csv)
    if len(df) != 1:
        raise ValueError(f"{csv} should have exactly 1 row, found {len(df)}")
    row = df.iloc[0].to_dict()
    row["run_dir"] = str(run_dir)
    return row


def collect_table(run_dirs: list[Path]) -> pd.DataFrame:
    rows = [load_summary(d) for d in run_dirs]
    df = pd.DataFrame(rows)
    # Order columns: keep declared ones first, then anything new at the end.
    declared = [c for c in SUMMARY_COLUMNS if c in df.columns]
    extra = [c for c in df.columns if c not in declared]
    df = df[declared + extra]
    if "lambda" in df.columns:
        df = df.sort_values("lambda").reset_index(drop=True)
    return df


def print_markdown(df: pd.DataFrame) -> None:
    """Compact stdout table — the key columns only."""
    cols = [
        "lambda",
        "requests_submitted", "requests_ok", "requests_terminated", "requests_error",
        "throughput_rps_ok",
        "latency_s_p50", "latency_s_p90", "latency_s_p99",
        "ttft_s_p50", "ttft_s_p90",
        "tbt_mean_ms_p50", "tbt_mean_ms_p90",
        "server_terminated_pct",
    ]
    cols = [c for c in cols if c in df.columns]
    print("\n" + df[cols].to_markdown(index=False))


def make_plots(df: pd.DataFrame, output_dir: Path) -> None:
    """Three small matplotlib figures: lambda vs throughput / latency / sat%."""
    import matplotlib.pyplot as plt

    output_dir.mkdir(parents=True, exist_ok=True)
    lam = df["lambda"]

    def _plot(y_col: str, ylabel: str, fname: str) -> None:
        if y_col not in df.columns:
            return
        fig, ax = plt.subplots(figsize=(4.5, 3.0))
        ax.plot(lam, df[y_col], marker="o", linewidth=1.4)
        ax.set_xlabel(r"Arrival rate $\lambda$ (req/sec)")
        ax.set_ylabel(ylabel)
        ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
        fig.tight_layout()
        fig.savefig(output_dir / fname, dpi=300)
        plt.close(fig)

    _plot("throughput_rps_ok", "Throughput (successful req/s)", "lambda_throughput.png")
    _plot("latency_s_p90", "Latency p90 (s)", "lambda_latency_p90.png")
    _plot("server_terminated_pct", "Server-terminated (%)", "lambda_server_terminated_pct.png")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dirs", nargs="+", required=True,
                    help="run directories (each must have analysis/request_summary.csv)")
    ap.add_argument("--output-csv", default=None,
                    help="explicit CSV path; defaults to <output-dir>/lambda_summary.csv")
    ap.add_argument("--output-dir", default=None,
                    help="directory to write the summary CSV and optional plots")
    ap.add_argument("--plot-png", action="store_true",
                    help="also write PNG plots into --output-dir")
    ap.add_argument("--print-markdown", action="store_true",
                    help="print a compact markdown table to stdout")
    args = ap.parse_args()

    if not args.output_csv and not args.output_dir:
        raise SystemExit("provide --output-csv or --output-dir")

    run_dirs = [Path(d).resolve() for d in args.run_dirs]
    df = collect_table(run_dirs)

    if args.output_dir:
        out_dir = Path(args.output_dir).resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        if not args.output_csv:
            args.output_csv = str(out_dir / "lambda_summary.csv")
        if args.plot_png:
            make_plots(df, out_dir)
            print(f"wrote plots into: {out_dir}")

    df.to_csv(args.output_csv, index=False)
    print(f"wrote: {args.output_csv}  ({len(df)} rows)")

    if args.print_markdown:
        print_markdown(df)


if __name__ == "__main__":
    main()
