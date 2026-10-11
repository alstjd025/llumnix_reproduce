#!/usr/bin/env python3
"""Compare request-level goodput and server decode throughput between the
request-level admission-control runs and the no-admission baseline runs.

Two run families (matched by lambda):
  * ``admission_ratio_tp_tau5``  -> "W/ admission"  (request-level admission control)
  * ``no_admission_tp_tau5``     -> "W/O admission" (no admission control)

GOODPUT (NEW definition -- intentionally differs from
``summarize_sweep_window.py`` / ``parse_application_metrics.py``):

  Over a ``[start, end]`` minute window, filter calls by their OWN
  ``start_time``. ``start_time`` is the client-side request-issue time
  (recorded in ``agent.py`` right before ``llm.stream()``, *before* the
  admission-rejection check), so rejected calls carry a valid timestamp
  and are included.

    denominator = every call whose start_time falls in the window
                  -- rejected + unclassified + normal, ALL of them
    numerator   = calls with call_goodput_bool == True

    goodput_rate = numerator / denominator

  This is NOT the ``call_goodput_rate`` of the standard parser, whose
  denominator is ``classifiable_calls`` (rejected & unclassified
  excluded). Here the denominator is every arrival.

THROUGHPUT (server-side metric):

  The SGLang server's own decode generation throughput, the same source
  ``plot_application_metrics.py`` uses for the "Server decode throughput"
  curve of ``application_throughput_goodput.png``: rows of
  ``analysis/server_metrics.csv`` with ``event_type == "decode"``, column
  ``gen_throughput`` (tok/s).

  ``server_metrics.csv`` carries an absolute ``epoch`` per row, so the
  throughput window is the SAME absolute time interval as the goodput
  window (anchored to the client's first call). The reported value is
  the mean ``gen_throughput`` over decode events in that interval.

Usage:
  python analysis_scripts/compare_admission_goodput_throughput.py \
    --results-dir results \
    --window-min 20 80 \
    --output-csv results/aggregate_analysis/admission_vs_noadmission_goodput_throughput.csv \
    --plot-png  results/aggregate_analysis/admission_vs_noadmission_goodput_throughput.png
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Optional

import pandas as pd

# --- run families ----------------------------------------------------------
FAMILIES = {
    "admission": {"glob": "*admission_ratio_tp_tau5*", "label": "W/ admission"},
    "no_admission": {"glob": "*no_admission_tp_tau5*", "label": "W/O admission"},
}

# --- paper figure style (project-wide; see CLAUDE.md "Figure Styling") -----
PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8,
    "axes.labelsize": 9,
    "axes.titlesize": 9,
    "axes.linewidth": 0.75,
    "legend.fontsize": 7,
    "legend.frameon": False,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "xtick.major.size": 3.0,
    "ytick.major.size": 3.0,
    "xtick.major.width": 0.7,
    "ytick.major.width": 0.7,
    "lines.linewidth": 1.4,
    "lines.markersize": 4.5,
}
COLOR_ADM = "#1f77b4"   # W/ admission   (blue)
COLOR_NOADM = "#d62728"  # W/O admission  (red)


def _lambda_of(run_dir: str) -> Optional[float]:
    """Read the Poisson lambda for a run from run_config.json."""
    cfg_path = os.path.join(run_dir, "run_config.json")
    if not os.path.isfile(cfg_path):
        return None
    with open(cfg_path) as fh:
        cfg = json.load(fh)
    val = cfg.get("lambda", cfg.get("poisson_lambda"))
    return float(val) if val is not None else None


def _server_decode_throughput(run_dir: str, abs_w0: float, abs_w1: float) -> tuple:
    """Mean server decode ``gen_throughput`` over an absolute-epoch window.

    Reads ``analysis/server_metrics.csv`` (produced by parse_server_logs.py),
    keeps ``event_type == "decode"`` rows, and averages ``gen_throughput``
    over decode events whose ``epoch`` falls in ``[abs_w0, abs_w1)``.

    Returns ``(mean_tok_s, n_decode_events)``; ``(nan, 0)`` if unavailable.
    """
    sm_path = os.path.join(run_dir, "analysis", "server_metrics.csv")
    if not os.path.isfile(sm_path):
        print(f"  [warn] no server_metrics.csv (run parse_server_logs.py): {run_dir}")
        return float("nan"), 0
    sm = pd.read_csv(sm_path)
    if sm.empty or "event_type" not in sm.columns:
        return float("nan"), 0
    dec = sm[sm["event_type"].astype(str) == "decode"].copy()
    dec["epoch"] = pd.to_numeric(dec["epoch"], errors="coerce")
    dec["gen_throughput"] = pd.to_numeric(dec["gen_throughput"], errors="coerce")
    dec = dec.dropna(subset=["epoch", "gen_throughput"])
    win = dec[(dec["epoch"] >= abs_w0) & (dec["epoch"] < abs_w1)]
    if win.empty:
        return float("nan"), 0
    return float(win["gen_throughput"].mean()), int(len(win))


def windowed_stats(run_dir: str, start_min: float, end_min: float) -> Optional[dict]:
    """New goodput rate (client) + server decode throughput for one run.

    Calls are filtered by their own ``start_time`` relative to the run's
    first call. Denominator = all calls in the window (rejected included);
    numerator = calls with ``call_goodput_bool == True``. The throughput
    window is the SAME absolute time interval (anchored to ``t0``).
    """
    calls_path = os.path.join(run_dir, "analysis", "application_calls.csv")
    if not os.path.isfile(calls_path):
        print(f"  [skip] no application_calls.csv: {run_dir}")
        return None
    calls = pd.read_csv(calls_path)
    if calls.empty or "start_time" not in calls.columns:
        print(f"  [skip] empty/invalid calls table: {run_dir}")
        return None

    t0 = float(calls["start_time"].min())
    abs_w0 = t0 + start_min * 60.0
    abs_w1 = t0 + end_min * 60.0

    cw = calls[(calls["start_time"] >= abs_w0) & (calls["start_time"] < abs_w1)]
    n_calls = int(len(cw))
    if n_calls == 0:
        print(f"  [skip] no calls in window: {run_dir}")
        return None

    n_goodput = int((cw["call_goodput_bool"] == True).sum())  # noqa: E712
    n_rejected = int((cw.get("is_rejected_bool") == True).sum())  # noqa: E712

    thr, n_decode = _server_decode_throughput(run_dir, abs_w0, abs_w1)

    return {
        "run": os.path.basename(run_dir.rstrip("/")),
        "n_calls": n_calls,
        "n_goodput": n_goodput,
        "n_rejected": n_rejected,
        "goodput_rate": n_goodput / n_calls,
        "server_decode_tok_s": thr,
        "n_decode_events": n_decode,
        "window_start_min": start_min,
        "window_end_min": end_min,
    }


def collect(results_dir: str, start_min: float, end_min: float) -> pd.DataFrame:
    rows = []
    for family, spec in FAMILIES.items():
        run_dirs = sorted(glob.glob(os.path.join(results_dir, spec["glob"])))
        run_dirs = [d for d in run_dirs if os.path.isdir(d)]
        print(f"[{family}] {len(run_dirs)} runs matched '{spec['glob']}'")
        for run_dir in run_dirs:
            lam = _lambda_of(run_dir)
            if lam is None:
                print(f"  [skip] no lambda in run_config.json: {run_dir}")
                continue
            stats = windowed_stats(run_dir, start_min, end_min)
            if stats is None:
                continue
            stats["family"] = family
            stats["policy"] = spec["label"]
            stats["lambda"] = lam
            rows.append(stats)
    if not rows:
        raise SystemExit("No runs collected -- check --results-dir and globs.")
    df = pd.DataFrame(rows).sort_values(["family", "lambda"]).reset_index(drop=True)
    return df


def plot(df: pd.DataFrame, png_path: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.ticker import FixedLocator, FixedFormatter

    with plt.rc_context(PAPER_STYLE):
        fig, ax1 = plt.subplots(figsize=(3.6, 2.7))
        ax2 = ax1.twinx()

        handles = []
        for family, color in (("admission", COLOR_ADM), ("no_admission", COLOR_NOADM)):
            sub = df[df["family"] == family].sort_values("lambda")
            if sub.empty:
                continue
            label = FAMILIES[family]["label"]
            lam = sub["lambda"].to_numpy()
            # goodput rate (%) on the left axis -- solid line
            ax1.plot(lam, sub["goodput_rate"] * 100.0, color=color,
                     linestyle="-", marker="o", markeredgecolor="white",
                     markeredgewidth=0.5, zorder=3)
            # server decode throughput on the right axis -- dashed line
            ax2.plot(lam, sub["server_decode_tok_s"], color=color,
                     linestyle="--", marker="s", markeredgecolor="white",
                     markeredgewidth=0.5, zorder=3)
            handles.append(Line2D([], [], color=color, linestyle="-", marker="o",
                                   label=f"{label} — goodput"))
            handles.append(Line2D([], [], color=color, linestyle="--", marker="s",
                                   label=f"{label} — throughput"))

        ax1.set_xscale("log")
        # higher load on the left
        lam_all = sorted(df["lambda"].unique())
        ax1.set_xlim(max(lam_all) * 1.25, min(lam_all) * 0.8)
        ax1.xaxis.set_major_locator(FixedLocator(lam_all))
        ax1.xaxis.set_minor_locator(FixedLocator([]))
        ax1.xaxis.set_major_formatter(FixedFormatter([f"{v:g}" for v in lam_all]))
        for lbl in ax1.get_xticklabels():
            lbl.set_rotation(45)
            lbl.set_horizontalalignment("right")
            lbl.set_rotation_mode("anchor")

        ax1.set_xlabel(r"Arrival rate $\lambda$ (jobs/sec)")
        ax1.set_ylabel("Request-level goodput (%)")
        ax2.set_ylabel("Server decode throughput (tok/s)")
        ax1.set_ylim(0, 100)
        ax2.set_ylim(bottom=0)

        ax1.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
        for spine in ax1.spines.values():
            spine.set_visible(True)

        ax1.legend(handles=handles, loc="lower center",
                   bbox_to_anchor=(0.5, 1.02), ncol=2)

        os.makedirs(os.path.dirname(os.path.abspath(png_path)), exist_ok=True)
        fig.savefig(png_path, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"Saved: {png_path}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--results-dir", default="results",
                    help="Directory holding the run folders (default: results)")
    ap.add_argument("--window-min", nargs=2, type=float, metavar=("START", "END"),
                    default=[20.0, 80.0],
                    help="Steady-state window in minutes (default: 20 80)")
    ap.add_argument("--output-csv",
                    default="results/aggregate_analysis/admission_vs_noadmission_goodput_throughput.csv")
    ap.add_argument("--plot-png",
                    default="results/aggregate_analysis/admission_vs_noadmission_goodput_throughput.png")
    args = ap.parse_args()

    start_min, end_min = args.window_min
    df = collect(args.results_dir, start_min, end_min)

    cols = ["family", "policy", "lambda", "n_calls", "n_goodput", "n_rejected",
            "goodput_rate", "server_decode_tok_s", "n_decode_events",
            "window_start_min", "window_end_min", "run"]
    df = df[cols]

    os.makedirs(os.path.dirname(os.path.abspath(args.output_csv)), exist_ok=True)
    df.to_csv(args.output_csv, index=False)
    print(f"Saved: {args.output_csv}\n")

    # markdown preview
    print(f"Window: [{start_min:g}, {end_min:g}] min  |  "
          "goodput rate = goodput calls / ALL calls in window (rejected included)  |  "
          "throughput = mean server decode gen_throughput over the same window\n")
    for family in ("admission", "no_admission"):
        sub = df[df["family"] == family]
        if sub.empty:
            continue
        print(f"### {FAMILIES[family]['label']}")
        print("| lambda | calls | goodput | rejected | goodput rate | decode throughput (tok/s) |")
        print("|---:|---:|---:|---:|---:|---:|")
        for _, r in sub.iterrows():
            print(f"| {r['lambda']:g} | {r['n_calls']} | {r['n_goodput']} | "
                  f"{r['n_rejected']} | {r['goodput_rate']*100:.1f}% | "
                  f"{r['server_decode_tok_s']:.1f} |")
        print()

    plot(df, args.plot_png)


if __name__ == "__main__":
    main()
