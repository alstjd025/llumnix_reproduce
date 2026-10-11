#!/usr/bin/env python3
"""Parse request-level experiment metrics into goodput CSVs.

For the `codingagent_request_level_poisson` workload: every request is an
independent unit (no job/chain). Each request carries the per-request
Halo SLO fields and is admitted/rejected on its own. This script reads
the run's `metrics.csv` (`agent == "request"` rows), joins each request
with its solo (concurrency-1) baseline from the transcript file, and
writes per-request and aggregate goodput CSVs.

Goodput is computed for three dimensions independently, each vs its own
recorded baseline x tau:
    e2e  goodput : latency            < baseline_e2e_s        * tau
    ttft goodput : first_token_latency < baseline_ttft_s      * tau
    tbt  goodput : tbt_mean_ms        < baseline_tbt_mean_ms  * tau

Classification (mirrors CLAUDE.md "unclassified" rules):
  - rejected by admission control          -> unclassified (declined before work)
  - cut off by run end (server-terminated) -> unclassified (run-boundary cutoff)
  - missing baseline                       -> unclassified
  - otherwise                              -> classified; goodput per the bool
Goodput rates use classified requests as the denominator.

Outputs (under <run>/analysis/):
  request_metrics.csv   one row per request, with slowdowns + goodput bools
  request_summary.csv   one-row aggregate (rates, percentiles, throughput)
"""

import argparse
import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd


_REPLAY_SUFFIX_RE = re.compile(r"__r\d+$")


def strip_replay_suffix(task_id: str) -> str:
    """`<request_id>__rNN` -> `<request_id>`."""
    return _REPLAY_SUFFIX_RE.sub("", str(task_id))


def load_run_config(run_dir: Path) -> dict:
    path = run_dir / "run_config.json"
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def resolve_tau(cfg: dict, cli_tau) -> float:
    """Goodput threshold. CLI override > run_config tau > halo.e2e_slo > 3.0."""
    if cli_tau is not None:
        return float(cli_tau)
    if cfg.get("tau") is not None:
        return float(cfg["tau"])
    halo = cfg.get("halo") or {}
    if halo.get("e2e_slo") is not None:
        return float(halo["e2e_slo"])
    return 3.0


def resolve_transcript_path(run_dir: Path, cfg: dict, cli_path) -> Path:
    """Locate the transcript file: CLI override > run_config transcript_file."""
    candidate = cli_path or cfg.get("transcript_file")
    if not candidate:
        raise SystemExit(
            "ERROR: transcript file unknown — pass --transcript-file or "
            "ensure run_config.json records 'transcript_file'."
        )
    path = Path(candidate)
    if not path.exists():
        raise SystemExit(f"ERROR: transcript file not found: {path}")
    return path


def load_baselines(transcript_path: Path) -> pd.DataFrame:
    """request_id -> baseline timings, from the transcript JSONL."""
    rows = []
    with open(transcript_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rows.append(
                {
                    "request_id": rec.get("request_id"),
                    "stage": rec.get("stage", ""),
                    "baseline_ttft_s": rec.get("baseline_ttft_s"),
                    "baseline_tbt_mean_ms": rec.get("baseline_tbt_mean_ms"),
                    "baseline_e2e_s": rec.get("baseline_e2e_s"),
                    "recorded_output_tokens": rec.get("recorded_output_tokens"),
                }
            )
    df = pd.DataFrame(rows).drop_duplicates(subset="request_id", keep="first")
    return df


def _to_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.strip().str.lower().isin({"true", "1"})


def build_request_metrics(run_dir: Path, tau: float, transcript_path: Path) -> pd.DataFrame:
    metrics_path = run_dir / "metrics.csv"
    if not metrics_path.exists():
        raise SystemExit(f"ERROR: metrics.csv not found: {metrics_path}")

    df = pd.read_csv(metrics_path)
    df = df[df["agent"].astype(str) == "request"].copy()
    if df.empty:
        raise SystemExit(
            "ERROR: no `agent == request` rows in metrics.csv — this run "
            "is not a codingagent_request_level_poisson run."
        )

    for col in ("latency", "first_token_latency", "tbt_mean_ms",
                "start_time", "end_time", "output_tokens", "input_tokens"):
        df[col] = pd.to_numeric(df.get(col), errors="coerce")
    for col in ("is_rejected", "is_timeout", "is_error",
                "is_job_timeout", "is_server_terminated", "success"):
        df[col] = _to_bool(df.get(col, pd.Series(dtype=str)))

    df["request_id"] = df["task_id"].map(strip_replay_suffix)

    baselines = load_baselines(transcript_path)
    df = df.merge(baselines, on="request_id", how="left", suffixes=("", "_t"))

    # Slowdowns vs solo baseline.
    df["e2e_slowdown"] = df["latency"] / df["baseline_e2e_s"]
    df["ttft_slowdown"] = df["first_token_latency"] / df["baseline_ttft_s"]
    df["tbt_slowdown"] = df["tbt_mean_ms"] / df["baseline_tbt_mean_ms"]

    # Unclassified: declined before doing work, or cut off by run end, or
    # no baseline to compare against.
    df["has_baseline"] = df["baseline_e2e_s"].notna()
    df["unclassified"] = (
        df["is_rejected"] | df["is_server_terminated"] | ~df["has_baseline"]
    )

    def _goodput(slowdown_col: str) -> pd.Series:
        good = df[slowdown_col] < tau
        good = good.where(df[slowdown_col].notna(), other=np.nan)
        good = good.mask(df["unclassified"], other=np.nan)
        return good

    df["e2e_goodput"] = _goodput("e2e_slowdown")
    df["ttft_goodput"] = _goodput("ttft_slowdown")
    df["tbt_goodput"] = _goodput("tbt_slowdown")
    df["tau"] = tau

    cols = [
        "task_id", "request_id", "stage", "start_time", "end_time",
        "latency", "first_token_latency", "tbt_mean_ms",
        "input_tokens", "output_tokens",
        "baseline_e2e_s", "baseline_ttft_s", "baseline_tbt_mean_ms",
        "e2e_slowdown", "ttft_slowdown", "tbt_slowdown",
        "e2e_goodput", "ttft_goodput", "tbt_goodput",
        "is_rejected", "rejection_reason", "is_timeout", "is_error",
        "is_job_timeout", "is_server_terminated", "unclassified", "tau",
    ]
    return df[[c for c in cols if c in df.columns]].sort_values("start_time")


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    total = len(df)
    classified = df[~df["unclassified"]]

    def _rate(series: pd.Series) -> float:
        s = series.dropna()
        return float(s.mean()) if len(s) else float("nan")

    def _pct(series: pd.Series, q: float) -> float:
        s = series.dropna()
        return float(np.percentile(s, q)) if len(s) else float("nan")

    duration_s = float("nan")
    if df["start_time"].notna().any():
        duration_s = float(df["start_time"].max() - df["start_time"].min())

    row = {
        "total_requests": total,
        "classified_requests": int(len(classified)),
        "unclassified_requests": int(df["unclassified"].sum()),
        "rejected_requests": int(df["is_rejected"].sum()),
        "rejection_rate": df["is_rejected"].mean() if total else float("nan"),
        "server_terminated_requests": int(df["is_server_terminated"].sum()),
        "timeout_requests": int(df["is_timeout"].sum()),
        "error_requests": int(df["is_error"].sum()),
        "e2e_goodput_rate": _rate(df["e2e_goodput"]),
        "ttft_goodput_rate": _rate(df["ttft_goodput"]),
        "tbt_goodput_rate": _rate(df["tbt_goodput"]),
        "e2e_slowdown_p50": _pct(df["e2e_slowdown"], 50),
        "e2e_slowdown_p95": _pct(df["e2e_slowdown"], 95),
        "ttft_slowdown_p50": _pct(df["ttft_slowdown"], 50),
        "ttft_slowdown_p95": _pct(df["ttft_slowdown"], 95),
        "tbt_slowdown_p50": _pct(df["tbt_slowdown"], 50),
        "tbt_slowdown_p95": _pct(df["tbt_slowdown"], 95),
        "run_duration_s": duration_s,
        "request_throughput_per_s": (total / duration_s) if duration_s and duration_s > 0 else float("nan"),
        "total_output_tokens": int(df["output_tokens"].fillna(0).sum()),
        "tau": float(df["tau"].iloc[0]) if total else float("nan"),
    }
    return pd.DataFrame([row])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", help="Path to a request-level run directory")
    parser.add_argument("--tau", type=float, default=None,
                        help="Goodput threshold (default: from run_config.json)")
    parser.add_argument("--transcript-file", type=str, default=None,
                        help="Override transcript path (default: from run_config.json)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir)
    cfg = load_run_config(run_dir)
    tau = resolve_tau(cfg, args.tau)
    transcript_path = resolve_transcript_path(run_dir, cfg, args.transcript_file)

    df = build_request_metrics(run_dir, tau, transcript_path)
    summary = build_summary(df)

    out_dir = run_dir / "analysis"
    out_dir.mkdir(exist_ok=True)
    df.to_csv(out_dir / "request_metrics.csv", index=False)
    summary.to_csv(out_dir / "request_summary.csv", index=False)

    print(f"tau={tau}  transcript={transcript_path}")
    print(f"wrote {out_dir/'request_metrics.csv'}  ({len(df)} requests)")
    print(f"wrote {out_dir/'request_summary.csv'}")
    with pd.option_context("display.max_columns", None, "display.width", 160):
        print(summary.to_string(index=False))


if __name__ == "__main__":
    main()
