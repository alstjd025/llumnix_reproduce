#!/usr/bin/env python3
"""Cross-run overlay of server decode throughput vs time.

For each run dir, reads ``analysis/server_metrics.csv`` (produced by
``analysis_scripts/parse_server_logs.py``) and plots one line per run
showing ``gen_throughput`` (tokens/s) vs minutes-since-first-decode.

Usage:
  python plot_server_throughput_overlay.py \
    --run-dirs results/*sharegpt_sweep_lambda_* \
    --output-path results/aggregate_analysis/<dir>/server_throughput_overlay.png \
    [--smooth-window 10] [--label-from lambda]

The label per run defaults to ``λ={lambda}`` (read from each run's
``run_config.json``); pass ``--label-from session_name`` to label by the
run directory's session name instead.
"""

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


def load_decode_series(run_dir: Path) -> pd.DataFrame:
    """Load decode events from ``analysis/server_metrics.csv``."""
    csv = run_dir / "analysis" / "server_metrics.csv"
    if not csv.exists():
        raise FileNotFoundError(
            f"missing {csv}: run analysis_scripts/parse_server_logs.py first"
        )
    df = pd.read_csv(csv, low_memory=False)
    df = df[df["event_type"] == "decode"].copy()
    df["epoch"] = pd.to_numeric(df["epoch"], errors="coerce")
    df["gen_throughput"] = pd.to_numeric(df["gen_throughput"], errors="coerce")
    df = df.dropna(subset=["epoch", "gen_throughput"]).sort_values("epoch")
    if df.empty:
        raise ValueError(f"no decode rows in {csv}")
    df["t_min"] = (df["epoch"] - df["epoch"].iloc[0]) / 60.0
    return df.reset_index(drop=True)


def run_label(run_dir: Path, label_from: str) -> str:
    """Compose a legend label from run_config.json (or fallback to dir name)."""
    cfg_path = run_dir / "run_config.json"
    cfg = {}
    if cfg_path.exists():
        with open(cfg_path) as f:
            cfg = json.load(f)
    if label_from == "lambda" and cfg.get("lambda") is not None:
        return rf"$\lambda$={cfg['lambda']}"
    return cfg.get("session_name") or run_dir.name


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dirs", nargs="+", required=True,
                    help="run directories (each must have analysis/server_metrics.csv)")
    ap.add_argument("--output-path", required=True,
                    help="output PNG path")
    ap.add_argument("--smooth-window", type=int, default=0,
                    help="rolling mean window (number of decode samples); 0 disables")
    ap.add_argument("--label-from", default="lambda",
                    choices=["lambda", "session_name"],
                    help="legend label source")
    args = ap.parse_args()

    run_dirs = [Path(d).resolve() for d in args.run_dirs]
    fig, ax = plt.subplots(figsize=(6.5, 3.2))
    for d in run_dirs:
        df = load_decode_series(d)
        y = df["gen_throughput"]
        if args.smooth_window and args.smooth_window > 1:
            y = y.rolling(args.smooth_window, min_periods=1).mean()
        ax.plot(df["t_min"], y, linewidth=1.0, alpha=0.85,
                label=run_label(d, args.label_from))

    ax.set_xlabel("Time since first decode (min)")
    ax.set_ylabel("Server decode throughput (gen tokens/s)")
    ax.grid(True, axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
    ax.legend(loc="best", frameon=False)
    fig.tight_layout()

    out = Path(args.output_path).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=300)
    plt.close(fig)
    print(f"wrote: {out}")


if __name__ == "__main__":
    main()
