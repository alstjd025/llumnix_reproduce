#!/usr/bin/env python3
"""Per-run request-level summary for direct/no-baseline workloads.

For workloads like ``sharegpt_request_level_poisson`` that send each
request directly and have **no per-request solo baseline** (and no
``tau``-based goodput): this script reads a run's ``metrics.csv`` and
writes raw-load statistics — request counts, throughput, and
latency/TTFT/TBT percentile distributions.

It intentionally **does not** compute SLO goodput. Absolute-SLO goodput
will be added in a follow-up once thresholds are decided.

Inputs:
  <run_dir>/metrics.csv     produced by run_experiment.py
  <run_dir>/run_config.json optional, used to label the summary row

Outputs (under <run_dir>/analysis/):
  request_metrics.csv  one row per ``agent=="request"`` row with a few
                       cleaned/derived columns
  request_summary.csv  one-row aggregate for this run

For the transcript-based codingagent_request_level_poisson workload,
use ``parse_request_metrics.py`` (sister script in this folder) instead.
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd


# Percentiles reported in the summary. Mean is always included.
PERCENTILES = (50, 90, 99)

# Columns whose percentiles are reported (over success=True only).
PERCENTILE_COLUMNS = ("latency", "first_token_latency", "tbt_mean_ms")


def load_run_config(run_dir: Path) -> dict:
    path = run_dir / "run_config.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def load_request_rows(run_dir: Path) -> pd.DataFrame:
    """Read ``metrics.csv`` and keep only ``agent == "request"`` rows."""
    csv_path = run_dir / "metrics.csv"
    if not csv_path.exists():
        raise FileNotFoundError(f"metrics.csv not found: {csv_path}")
    df = pd.read_csv(csv_path, skipinitialspace=True, low_memory=False)
    df = df[df["agent"].astype(str) == "request"].copy()
    if df.empty:
        raise ValueError(
            f"no agent=='request' rows in {csv_path} — is this a request-"
            f"level workload?"
        )
    # Coerce types. CSV booleans come back as strings.
    for col in (
        "success", "is_error", "is_rejected", "is_server_terminated",
        "is_job_timeout", "is_timeout", "stream_fallback_used",
    ):
        if col in df.columns:
            df[col] = df[col].astype(str).str.lower().eq("true")
    for col in (
        "latency", "first_token_latency",
        "tbt_mean_ms", "tbt_p50_ms", "tbt_p75_ms", "tbt_p80_ms",
        "tbt_p85_ms", "tbt_p90_ms", "tbt_p95_ms", "tbt_max_ms",
        "input_tokens", "output_tokens",
        "start_time", "end_time",
    ):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def derive_request_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Per-request rows with a few derived columns kept for inspection."""
    keep = [
        "task_id",
        "start_time", "end_time",
        "latency", "first_token_latency",
        "tbt_mean_ms", "tbt_p90_ms", "tbt_p95_ms",
        "input_tokens", "output_tokens",
        "success", "is_error", "is_rejected",
        "is_server_terminated", "is_job_timeout",
        "rejection_reason", "error_msg",
    ]
    keep = [c for c in keep if c in df.columns]
    out = df[keep].copy()
    if "start_time" in out.columns:
        first = out["start_time"].min()
        out["t_minutes"] = (out["start_time"] - first) / 60.0
    return out.sort_values("start_time").reset_index(drop=True)


def percentile_stats(series: pd.Series, prefix: str) -> dict:
    """Mean + percentiles, NaN-safe."""
    s = series.dropna()
    if s.empty:
        out = {f"{prefix}_mean": np.nan}
        for p in PERCENTILES:
            out[f"{prefix}_p{p}"] = np.nan
        return out
    out = {f"{prefix}_mean": float(s.mean())}
    for p in PERCENTILES:
        out[f"{prefix}_p{p}"] = float(np.percentile(s, p))
    return out


def build_summary(df: pd.DataFrame, run_dir: Path, cfg: dict) -> dict:
    """One-row summary: counts, throughput, success-only percentiles."""
    n_total = len(df)
    n_ok = int(df["success"].sum()) if "success" in df.columns else 0
    n_term = int(df["is_server_terminated"].sum()) if "is_server_terminated" in df.columns else 0
    n_err = int(df["is_error"].sum()) if "is_error" in df.columns else 0
    n_rej = int(df["is_rejected"].sum()) if "is_rejected" in df.columns else 0
    n_to = int(df["is_job_timeout"].sum()) if "is_job_timeout" in df.columns else 0

    # Submitted/observed span. start_time is the canonical timestamp for
    # request submission to invoke_with_tracking.
    span_min = float("nan")
    if "start_time" in df.columns and n_total > 0:
        span_s = float(df["start_time"].max() - df["start_time"].min())
        span_min = span_s / 60.0 if span_s > 0 else float("nan")

    # Throughput denominator: run_config duration_min if available, else
    # the observed start_time span.
    duration_min_cfg = cfg.get("duration_min")
    denom_min = float(duration_min_cfg) if duration_min_cfg else span_min
    denom_s = denom_min * 60.0 if denom_min and denom_min > 0 else float("nan")

    ok_rows = df[df["success"]] if "success" in df.columns else df
    throughput_rps = (n_ok / denom_s) if denom_s and denom_s > 0 else float("nan")
    out_tokens_total = float(ok_rows["output_tokens"].sum()) if "output_tokens" in ok_rows.columns else 0.0
    in_tokens_total = float(ok_rows["input_tokens"].sum()) if "input_tokens" in ok_rows.columns else 0.0
    output_tps = (out_tokens_total / denom_s) if denom_s and denom_s > 0 else float("nan")
    input_tps = (in_tokens_total / denom_s) if denom_s and denom_s > 0 else float("nan")

    row: dict = {
        "run_dir": str(run_dir),
        "workload": cfg.get("workload"),
        "lambda": cfg.get("lambda"),
        "duration_min_cfg": duration_min_cfg,
        "duration_min_observed": round(span_min, 3) if not np.isnan(span_min) else np.nan,
        "requests_submitted": n_total,
        "requests_ok": n_ok,
        "requests_terminated": n_term,
        "requests_error": n_err,
        "requests_rejected": n_rej,
        "requests_job_timeout": n_to,
        "server_terminated_pct": round(100.0 * n_term / n_total, 3) if n_total else 0.0,
        "error_pct": round(100.0 * n_err / n_total, 3) if n_total else 0.0,
        "rejected_pct": round(100.0 * n_rej / n_total, 3) if n_total else 0.0,
        "throughput_rps_ok": round(throughput_rps, 4) if not np.isnan(throughput_rps) else np.nan,
        "output_tokens_per_s": round(output_tps, 2) if not np.isnan(output_tps) else np.nan,
        "input_tokens_per_s": round(input_tps, 2) if not np.isnan(input_tps) else np.nan,
        "output_tokens_total": int(out_tokens_total),
    }

    # Percentile distributions over success=True only — terminated/error
    # rows have partial timings and would pollute the distribution.
    for col, label in (
        ("latency", "latency_s"),
        ("first_token_latency", "ttft_s"),
        ("tbt_mean_ms", "tbt_mean_ms"),
    ):
        if col in ok_rows.columns:
            row.update(percentile_stats(ok_rows[col], label))

    # Round percentile values to keep CSV readable.
    for k, v in list(row.items()):
        if isinstance(v, float) and not np.isnan(v):
            row[k] = round(v, 4)
    return row


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("run_dir", help="Run directory containing metrics.csv")
    args = ap.parse_args()

    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        raise SystemExit(f"not a directory: {run_dir}")

    cfg = load_run_config(run_dir)
    df = load_request_rows(run_dir)
    metrics = derive_request_metrics(df)
    summary = build_summary(df, run_dir, cfg)

    out_dir = run_dir / "analysis"
    out_dir.mkdir(exist_ok=True)
    metrics.to_csv(out_dir / "request_metrics.csv", index=False)
    pd.DataFrame([summary]).to_csv(out_dir / "request_summary.csv", index=False)

    print(f"[parse_request_summary] {run_dir.name}")
    print(f"  rows kept (agent=request) : {len(df)}")
    print(f"  ok / term / err / rej     : "
          f"{summary['requests_ok']} / {summary['requests_terminated']} / "
          f"{summary['requests_error']} / {summary['requests_rejected']}")
    print(f"  throughput (req/s, ok)    : {summary['throughput_rps_ok']}")
    print(f"  latency  (s) p50/p90/p99  : "
          f"{summary.get('latency_s_p50')} / {summary.get('latency_s_p90')} / "
          f"{summary.get('latency_s_p99')}")
    print(f"  TTFT     (s) p50/p90/p99  : "
          f"{summary.get('ttft_s_p50')} / {summary.get('ttft_s_p90')} / "
          f"{summary.get('ttft_s_p99')}")
    print(f"  TBT mean(ms) p50/p90/p99  : "
          f"{summary.get('tbt_mean_ms_p50')} / {summary.get('tbt_mean_ms_p90')} / "
          f"{summary.get('tbt_mean_ms_p99')}")
    print(f"  wrote: {out_dir/'request_summary.csv'}")
    print(f"         {out_dir/'request_metrics.csv'}")


if __name__ == "__main__":
    main()
