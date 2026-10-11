#!/usr/bin/env python3
"""Per-lambda TBT-p90 threshold attainment curve.

For each run, computes the fraction of **successful** requests whose
per-request ``tbt_p90_ms`` is at or below each fixed threshold T, then
overlays one line per lambda.

  X-axis = TBT threshold (ms), default {50, 75, 100, 125, 150, 175, 200}
  Y-axis = fraction of successful requests with ``tbt_p90_ms <= T`` (%)
  Lines  = one per run (labelled by lambda from each run's run_config.json)

Reads ``analysis/request_metrics.csv`` (output of
``parse_request_summary.py``); falls back to ``metrics.csv`` if needed.

Usage:
  python plot_tbt_p90_threshold.py \
    --run-dirs results/*sharegpt_sweep_lambda_* \
    --output-path results/aggregate_analysis/<dir>/tbt_p90_threshold.png \
    [--thresholds 50 75 100 125 150 175 200] \
    [--csv-output <path>]
"""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_THRESHOLDS_MS = (50, 75, 100, 125, 150, 175, 200)


def load_request_tbt_p90(run_dir: Path) -> pd.Series:
    """Return ``tbt_p90_ms`` for success rows of one run."""
    p_summary = run_dir / "analysis" / "request_metrics.csv"
    if p_summary.exists():
        df = pd.read_csv(p_summary, low_memory=False)
    else:
        # Fall back to raw metrics.csv (in case parse_request_summary.py
        # hasn't been re-run after adding tbt_p90 to its keep list).
        p_raw = run_dir / "metrics.csv"
        if not p_raw.exists():
            raise FileNotFoundError(
                f"neither {p_summary} nor {p_raw} exists"
            )
        df = pd.read_csv(p_raw, low_memory=False, skipinitialspace=True)
        df = df[df["agent"].astype(str) == "request"]
    if "tbt_p90_ms" not in df.columns:
        raise ValueError(
            f"{run_dir}: tbt_p90_ms column missing; re-run parse_request_summary.py"
        )
    succ = df["success"].astype(str).str.lower().eq("true")
    s = pd.to_numeric(df.loc[succ, "tbt_p90_ms"], errors="coerce").dropna()
    if s.empty:
        raise ValueError(f"{run_dir}: no success rows with tbt_p90_ms")
    return s


def run_label(run_dir: Path) -> tuple[float | None, str]:
    """(lambda_value, legend_label) — lambda from run_config.json."""
    cfg = {}
    p = run_dir / "run_config.json"
    if p.exists():
        with open(p) as f:
            cfg = json.load(f)
    lam = cfg.get("lambda")
    if lam is not None:
        return float(lam), rf"$\lambda$={lam}"
    return None, cfg.get("session_name") or run_dir.name


def attainment_pct(values: pd.Series, thresholds: list[int]) -> list[float]:
    """For each T, return % of values <= T."""
    arr = values.to_numpy()
    n = arr.size
    return [100.0 * float((arr <= T).sum()) / n if n else float("nan")
            for T in thresholds]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dirs", nargs="+", required=True,
                    help="run directories (each with analysis/request_metrics.csv)")
    ap.add_argument("--output-path", required=True,
                    help="output PNG path")
    ap.add_argument("--thresholds", nargs="+", type=int,
                    default=list(DEFAULT_THRESHOLDS_MS),
                    help="TBT thresholds in ms (default: 50 75 100 125 150 175 200)")
    ap.add_argument("--csv-output", default=None,
                    help="optional CSV path to dump the attainment table")
    args = ap.parse_args()

    run_dirs = [Path(d).resolve() for d in args.run_dirs]
    # Compute per-run attainment, sorted by lambda for stable ordering.
    rows = []
    for d in run_dirs:
        lam, label = run_label(d)
        s = load_request_tbt_p90(d)
        rows.append({
            "run_dir": str(d),
            "lambda": lam,
            "label": label,
            "values": s,
            "n": int(s.size),
        })
    rows.sort(key=lambda r: (r["lambda"] is None, r["lambda"] or 0.0))

    table = {"threshold_ms": args.thresholds}
    fig, ax = plt.subplots(figsize=(5.5, 3.4))
    for r in rows:
        pct = attainment_pct(r["values"], args.thresholds)
        ax.plot(args.thresholds, pct, marker="o", linewidth=1.4,
                label=f"{r['label']}  (n={r['n']})")
        table[r["label"]] = pct

    ax.set_xlabel("TBT threshold (ms)")
    ax.set_ylabel(r"Successful requests with $\mathrm{TBT}_{p90} \leq T$ (%)")
    ax.set_xticks(args.thresholds)
    ax.set_ylim(0, 105)
    ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
    ax.legend(loc="best", frameon=False)
    fig.tight_layout()

    out = Path(args.output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300)
    plt.close(fig)
    print(f"wrote: {out}")

    if args.csv_output:
        df = pd.DataFrame(table)
        csv_out = Path(args.csv_output).resolve()
        csv_out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(csv_out, index=False)
        print(f"wrote: {csv_out}")
        # Also print the table to stdout for quick inspection.
        print("\n" + df.to_markdown(index=False, floatfmt=".2f"))


if __name__ == "__main__":
    main()
