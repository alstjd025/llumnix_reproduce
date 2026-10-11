#!/usr/bin/env python3
"""Re-aggregate per-run application summary over a [start_min, end_min] window.

Reads the parsed `analysis/application_calls.csv` and `analysis/application_jobs.csv`
of one or more runs, slices each run to a shared time window measured in minutes
from that run's first call, and rebuilds the same columns as
`application_summary.csv` over the slice. Useful for excluding warmup/saturation
tails when comparing runs.

Filter rules (steady-state goodput definition):
- jobs:  `job_submit_time` in [t0 + start_min*60, t0 + end_min*60) — the
  release-time filter. Models "jobs that entered the system during
  steady-state load".
- calls: every call whose parent job is in the windowed job set
  (tied to parent submit time). Do NOT filter calls independently by
  `start_time`; that mixes populations and can invert call/job goodput
  ordering at high load.

Run-boundary cutoffs (jobs submitted near `end_min` whose chain doesn't
finish before the run terminates) are surfaced as `job_goodput_bool = NaN`
by `parse_application_metrics.py` and drop out of the denominator
automatically. See CLAUDE.md `Goodput > Window filtering` / `Goodput >
Run-boundary cutoffs` for the full rationale.

Outputs one CSV row per run with the existing summary columns plus
`run`, `lambda`, `tau`, `window_start_min`, `window_end_min`,
`window_t0_unix`, `window_calls_kept`, `window_jobs_kept`.

Example:
    python analysis_scripts/summarize_sweep_window.py \\
        --run-dirs results/260510_*tau5_lambda_* \\
        --window-min 20 80 \\
        --output-csv results/aggregate_analysis/sweep_window_summary_tau5_20to80min.csv \\
        --print-markdown
"""

import argparse
import json
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

# Reuse build_summary and bool helpers from the per-run parser so the schema
# and aggregation arithmetic stay in lockstep.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from parse_application_metrics import build_summary, _as_bool, _as_nullable_bool  # noqa: E402


CALL_BOOL_COLS = ["success_bool", "is_rejected_bool"]
CALL_NULLABLE_BOOL_COLS = ["call_goodput_bool"]
JOB_BOOL_COLS = ["job_completed_bool", "is_rejected_bool"]
JOB_NULLABLE_BOOL_COLS = ["job_goodput_bool"]


def _coerce_bools(df: pd.DataFrame, plain_cols, nullable_cols) -> pd.DataFrame:
    for c in plain_cols:
        if c in df.columns:
            df[c] = _as_bool(df[c])
    for c in nullable_cols:
        if c in df.columns:
            df[c] = _as_nullable_bool(df[c])
    return df


def summarize_run(run_dir: Path, window_min) -> dict:
    analysis = run_dir / "analysis"
    calls_csv = analysis / "application_calls.csv"
    jobs_csv = analysis / "application_jobs.csv"
    config_json = run_dir / "run_config.json"
    if not calls_csv.is_file():
        raise FileNotFoundError(
            f"Missing {calls_csv}. Run parse_application_metrics.py first."
        )
    if not jobs_csv.is_file():
        raise FileNotFoundError(
            f"Missing {jobs_csv}. Run parse_application_metrics.py first."
        )

    calls = pd.read_csv(calls_csv)
    jobs = pd.read_csv(jobs_csv)
    calls = _coerce_bools(calls, CALL_BOOL_COLS, CALL_NULLABLE_BOOL_COLS)
    jobs = _coerce_bools(jobs, JOB_BOOL_COLS, JOB_NULLABLE_BOOL_COLS)

    if calls.empty or "start_time" not in calls.columns:
        raise ValueError(f"{calls_csv} has no rows or missing start_time column")

    t0 = float(calls["start_time"].min())
    start_s = t0 + float(window_min[0]) * 60.0
    end_s = t0 + float(window_min[1]) * 60.0

    # Window the jobs first, then tie calls to those jobs' task_ids. This keeps
    # call-rate and job-rate metrics on the same population (the chain whose
    # parent job was submitted in the window), so chain-amplification orderings
    # such as call_goodput < job_goodput don't get inverted by independent
    # call/job filters.
    if "job_submit_time" in jobs.columns:
        jobs_w = jobs[
            (jobs["job_submit_time"] >= start_s)
            & (jobs["job_submit_time"] < end_s)
        ].copy()
    else:
        jobs_w = jobs.iloc[0:0].copy()
    if "task_id" in jobs_w.columns and "task_id" in calls.columns:
        window_task_ids = set(jobs_w["task_id"])
        calls_w = calls[calls["task_id"].isin(window_task_ids)].copy()
    else:
        calls_w = calls.iloc[0:0].copy()

    summary = build_summary(calls_w, jobs_w).iloc[0].to_dict()

    # Output-token split by parent-job classification, scaled to per-second.
    # Three buckets:
    #   - goodput:      job_goodput_bool == True
    #   - wasted:       job_goodput_bool == False  (completed-but-slow / rejected / tau timeout)
    #   - unclassified: job_goodput_bool is NA     (run-boundary cutoff, unknown outcome)
    # Classification IDs come from the *full* jobs DataFrame (not jobs_w) so a
    # call whose start_time is in the window but whose parent job's
    # submit_time is just before the window still gets attributed correctly.
    # Sum of the three equals output_tokens_per_s (from build_summary).
    duration_s = summary.get("duration_s")

    # Job classification IDs (shared by the 3-bucket and 4-bucket token splits).
    # Uses the *full* jobs DataFrame so a call whose start_time is in the
    # window but whose parent job's submit_time is just before the window
    # still gets attributed correctly.
    good_ids: set = set()
    bad_ids: set = set()
    unclassified_ids: set = set()
    if not jobs.empty and "task_id" in jobs.columns and "job_goodput_bool" in jobs.columns:
        gp_bool_all = jobs["job_goodput_bool"]
        good_ids = set(jobs.loc[gp_bool_all.fillna(False).astype(bool), "task_id"])
        bad_ids = set(
            jobs.loc[
                gp_bool_all.notna() & ~gp_bool_all.fillna(False).astype(bool),
                "task_id",
            ]
        )
        unclassified_ids = set(jobs.loc[gp_bool_all.isna(), "task_id"])

    # Three-bucket output-token split by parent-job classification.
    # Sum equals output_tokens_per_s (from build_summary).
    output_goodput_tps = float("nan")
    output_wasted_tps = float("nan")
    output_unclassified_tps = float("nan")
    if (
        duration_s is not None
        and not pd.isna(duration_s)
        and float(duration_s) > 0
        and "task_id" in calls_w.columns
        and "output_tokens" in calls_w.columns
    ):
        d = float(duration_s)
        good_calls = calls_w[calls_w["task_id"].isin(good_ids)]
        bad_calls = calls_w[calls_w["task_id"].isin(bad_ids)]
        unclassified_calls = calls_w[calls_w["task_id"].isin(unclassified_ids)]
        output_goodput_tps = float(good_calls["output_tokens"].sum()) / d
        output_wasted_tps = float(bad_calls["output_tokens"].sum()) / d
        output_unclassified_tps = (
            float(unclassified_calls["output_tokens"].sum()) / d
        )
    summary["output_goodput_tokens_per_s"] = output_goodput_tps
    summary["output_wasted_tokens_per_s"] = output_wasted_tps
    summary["output_unclassified_tokens_per_s"] = output_unclassified_tps

    # Four-way breakdown by (call_goodput, job_goodput). Calls whose
    # call_goodput_bool is NA (no per-call baseline match — the workload's
    # chain length can differ between baseline and replay for the same task)
    # are absorbed into the *default* segment of their parent job's status
    # (segment 1 if job goodput, segment 4 if wasted), so the four buckets
    # always sum to goodput_tokens + wasted_tokens.
    out_s1 = float("nan")  # call ∈ {T, NA} & job T -> pure goodput
    out_s2 = float("nan")  # call F & job T          -> slow call, goodput job
    out_s3 = float("nan")  # call T & job F          -> good call, wasted job
    out_s4 = float("nan")  # call ∈ {F, NA} & job F  -> pure wasted
    if (
        duration_s is not None
        and not pd.isna(duration_s)
        and float(duration_s) > 0
        and not calls_w.empty
        and "call_goodput_bool" in calls_w.columns
        and "task_id" in calls_w.columns
        and "output_tokens" in calls_w.columns
    ):
        d = float(duration_s)
        cgp = calls_w["call_goodput_bool"]
        is_T = (cgp == True).fillna(False).astype(bool)
        is_F = (cgp == False).fillna(False).astype(bool)
        in_good = calls_w["task_id"].isin(good_ids)
        in_bad = calls_w["task_id"].isin(bad_ids)
        out_s1 = float(calls_w.loc[in_good & ~is_F, "output_tokens"].sum()) / d
        out_s2 = float(calls_w.loc[in_good & is_F, "output_tokens"].sum()) / d
        out_s3 = float(calls_w.loc[in_bad & is_T, "output_tokens"].sum()) / d
        out_s4 = float(calls_w.loc[in_bad & ~is_T, "output_tokens"].sum()) / d
    summary["output_pure_goodput_tokens_per_s"] = out_s1
    summary["output_slow_call_in_goodput_job_tokens_per_s"] = out_s2
    summary["output_good_call_in_wasted_job_tokens_per_s"] = out_s3
    summary["output_pure_wasted_tokens_per_s"] = out_s4

    lam = None
    tau = None
    if config_json.is_file():
        with open(config_json) as f:
            cfg = json.load(f)
        lam = cfg.get("lambda")
        tau = cfg.get("tau")

    summary["run"] = run_dir.name
    summary["lambda"] = lam
    summary["tau"] = tau
    summary["window_start_min"] = float(window_min[0])
    summary["window_end_min"] = float(window_min[1])
    summary["window_t0_unix"] = t0
    summary["window_calls_kept"] = len(calls_w)
    summary["window_jobs_kept"] = len(jobs_w)
    return summary


def _paper_style() -> dict:
    return {
        "font.family": "serif",
        "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
        "font.size": 8,
        "axes.labelsize": 9,
        "axes.titlesize": 9,
        "axes.linewidth": 0.75,
        "legend.fontsize": 8,
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


def _apply_lambda_x_axis(ax, x):
    """Log x-axis with plain-number ticks at the actual lambda values, rotated 45°.
    Inverts the axis so higher load is on the left.
    """
    if len(x) > 1 and x.min() > 0 and x.max() / x.min() >= 10:
        ax.set_xscale("log")
        from matplotlib.ticker import FixedLocator, FuncFormatter, NullLocator
        ax.xaxis.set_major_locator(FixedLocator(list(x)))
        ax.xaxis.set_minor_locator(NullLocator())
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(45)
            lbl.set_horizontalalignment("right")
            lbl.set_rotation_mode("anchor")
    ax.invert_xaxis()
    ax.set_xlabel(r"Arrival rate $\lambda$ (jobs/sec)")


def plot_token_throughput_vs_goodput(df: pd.DataFrame, output_png: Path) -> None:
    plot_df = df.dropna(
        subset=[
            "lambda",
            "output_tokens_per_s",
            "output_goodput_tokens_per_s",
            "output_wasted_tokens_per_s",
            "duration_s",
        ]
    ).sort_values("lambda")
    if plot_df.empty:
        print(f"[skip plot] no rows with required columns; nothing for {output_png}")
        return
    # Highest load on the left, matching the goodput plot.
    plot_df = plot_df.iloc[::-1].reset_index(drop=True)
    lams = plot_df["lambda"].astype(float).to_numpy()
    duration_s = plot_df["duration_s"].astype(float).to_numpy()
    # Bar denominator excludes unclassified tokens (jobs cut off by run end,
    # outcome unknown) so that "goodput %" and "wasted %" are comparable to
    # SLO attainment / wasted_compute_ratio in the table. Each bar's height
    # equals goodput_tokens + wasted_tokens (the classified portion only).
    gp = (
        plot_df["output_goodput_tokens_per_s"].astype(float).clip(lower=0).to_numpy()
        * duration_s
    )
    wasted = (
        plot_df["output_wasted_tokens_per_s"].astype(float).clip(lower=0).to_numpy()
        * duration_s
    )
    classified = gp + wasted
    # In millions for readable tick labels.
    gp_m = gp / 1e6
    wasted_m = wasted / 1e6
    classified_m = classified / 1e6

    AX_W, AX_H = 3.0, 1.85
    LEFT_IN, BOTTOM_IN = 0.55, 0.7
    RIGHT_PAD_IN, TOP_PAD_IN = 0.15, 0.55
    fig_w = LEFT_IN + AX_W + RIGHT_PAD_IN
    fig_h = BOTTOM_IN + AX_H + TOP_PAD_IN

    with plt.rc_context(_paper_style()):
        fig = plt.figure(figsize=(fig_w, fig_h))
        ax = fig.add_axes((
            LEFT_IN / fig_w,
            BOTTOM_IN / fig_h,
            AX_W / fig_w,
            AX_H / fig_h,
        ))

        # Stacked bars per lambda (categorical x): goodput at the bottom,
        # wasted on top. Unclassified (run-cutoff) tokens are excluded from
        # the bar entirely so the two segments sum to 100% within each bar.
        idx = np.arange(len(lams))
        bar_w = 0.72
        ax.bar(
            idx, gp_m, width=bar_w,
            color="#1f77b4", alpha=0.55, edgecolor="black", linewidth=0.5,
            label="Goodput tokens",
        )
        ax.bar(
            idx, wasted_m, width=bar_w, bottom=gp_m,
            color="#d62728", alpha=0.55, edgecolor="black", linewidth=0.5,
            label="Wasted tokens",
        )

        # In-bar percentage labels (out of classified total). Skip thin segments.
        ymax = max(classified_m) if len(classified_m) else 1.0
        min_segment_for_label = 0.05 * ymax  # ~5% of the tallest bar
        for i in range(len(idx)):
            denom = classified[i]
            if denom <= 0:
                continue
            gp_pct = 100.0 * gp[i] / denom
            wasted_pct = 100.0 * wasted[i] / denom
            if gp_m[i] >= min_segment_for_label:
                ax.text(
                    idx[i], gp_m[i] / 2.0, f"{gp_pct:.0f}%",
                    ha="center", va="center", fontsize=7, color="black",
                )
            if wasted_m[i] >= min_segment_for_label:
                ax.text(
                    idx[i], gp_m[i] + wasted_m[i] / 2.0, f"{wasted_pct:.0f}%",
                    ha="center", va="center", fontsize=7, color="black",
                )

        # Categorical x-axis: one tick per lambda, plain numbers, rotated 45°.
        ax.set_xticks(idx)
        ax.set_xticklabels([f"{v:g}" for v in lams])
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(45)
            lbl.set_horizontalalignment("right")
            lbl.set_rotation_mode("anchor")
        ax.set_xlim(-0.5, len(lams) - 0.5)
        ax.set_xlabel(r"Arrival rate $\lambda$ (jobs/sec)")
        ax.set_ylabel("Output tokens (M)")
        ax.set_ylim(0, ymax * 1.08)

        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(True)
        ax.yaxis.grid(True, linestyle=":", linewidth=0.7, alpha=0.6)
        ax.xaxis.grid(False)
        ax.set_axisbelow(True)

        ax.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=2,
            handlelength=2.2,
            borderpad=0.4,
            columnspacing=1.4,
            labelspacing=0.3,
        )

        output_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_png, dpi=300)
        plt.close(fig)
    print(f"Saved: {output_png}")


def plot_token_throughput_call_job_breakdown(df: pd.DataFrame, output_png: Path) -> None:
    """Variant of `plot_token_throughput_vs_goodput` with 4 sub-segments per
    bar, splitting the goodput and wasted regions by individual call_goodput
    status:
      - Pure goodput      (call T or NA, job T): solid blue
      - Slow call, good job (call F, job T):     blue with diagonal hatch
      - Good call, wasted job (call T, job F):   red with diagonal hatch
      - Pure wasted       (call F or NA, job F): solid red
    The 1+2 height equals the simple "goodput tokens" segment of the
    2-segment plot; 3+4 equals the simple "wasted tokens" segment.
    """
    cols = [
        "lambda",
        "output_pure_goodput_tokens_per_s",
        "output_slow_call_in_goodput_job_tokens_per_s",
        "output_good_call_in_wasted_job_tokens_per_s",
        "output_pure_wasted_tokens_per_s",
        "duration_s",
    ]
    plot_df = df.dropna(subset=cols).sort_values("lambda")
    if plot_df.empty:
        print(f"[skip plot] no rows with required columns; nothing for {output_png}")
        return
    plot_df = plot_df.iloc[::-1].reset_index(drop=True)
    lams = plot_df["lambda"].astype(float).to_numpy()
    duration_s = plot_df["duration_s"].astype(float).to_numpy()
    s1 = (
        plot_df["output_pure_goodput_tokens_per_s"]
        .astype(float).clip(lower=0).to_numpy() * duration_s
    )
    s2 = (
        plot_df["output_slow_call_in_goodput_job_tokens_per_s"]
        .astype(float).clip(lower=0).to_numpy() * duration_s
    )
    s3 = (
        plot_df["output_good_call_in_wasted_job_tokens_per_s"]
        .astype(float).clip(lower=0).to_numpy() * duration_s
    )
    s4 = (
        plot_df["output_pure_wasted_tokens_per_s"]
        .astype(float).clip(lower=0).to_numpy() * duration_s
    )
    total = s1 + s2 + s3 + s4
    s1_m, s2_m, s3_m, s4_m = s1/1e6, s2/1e6, s3/1e6, s4/1e6
    total_m = total / 1e6

    AX_W, AX_H = 3.0, 1.95
    LEFT_IN, BOTTOM_IN = 0.55, 0.7
    # Slightly taller top pad because the legend now has 4 items (2 rows).
    RIGHT_PAD_IN, TOP_PAD_IN = 0.15, 0.65
    fig_w = LEFT_IN + AX_W + RIGHT_PAD_IN
    fig_h = BOTTOM_IN + AX_H + TOP_PAD_IN

    with plt.rc_context(_paper_style()):
        fig = plt.figure(figsize=(fig_w, fig_h))
        ax = fig.add_axes((
            LEFT_IN / fig_w,
            BOTTOM_IN / fig_h,
            AX_W / fig_w,
            AX_H / fig_h,
        ))

        idx = np.arange(len(lams))
        bar_w = 0.72
        blue, red = "#1f77b4", "#d62728"
        # Bottom to top: pure goodput -> slow-call-in-good-job ->
        # good-call-in-wasted-job -> pure wasted.
        ax.bar(idx, s1_m, width=bar_w, color=blue, alpha=0.55,
               edgecolor="black", linewidth=0.5,
               label="Pure goodput")
        ax.bar(idx, s2_m, width=bar_w, bottom=s1_m, color=blue, alpha=0.55,
               edgecolor="black", linewidth=0.5, hatch="///",
               label="Slow call, goodput job")
        ax.bar(idx, s3_m, width=bar_w, bottom=s1_m + s2_m, color=red, alpha=0.55,
               edgecolor="black", linewidth=0.5, hatch="\\\\\\",
               label="Good call, wasted job")
        ax.bar(idx, s4_m, width=bar_w, bottom=s1_m + s2_m + s3_m, color=red, alpha=0.55,
               edgecolor="black", linewidth=0.5,
               label="Pure wasted")

        ax.set_xticks(idx)
        ax.set_xticklabels([f"{v:g}" for v in lams])
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(45)
            lbl.set_horizontalalignment("right")
            lbl.set_rotation_mode("anchor")
        ax.set_xlim(-0.5, len(lams) - 0.5)
        ax.set_xlabel(r"Arrival rate $\lambda$ (jobs/sec)")
        ax.set_ylabel("Output tokens (M)")
        ymax = float(max(total_m)) if len(total_m) else 1.0
        ax.set_ylim(0, ymax * 1.08)

        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(True)
        ax.yaxis.grid(True, linestyle=":", linewidth=0.7, alpha=0.6)
        ax.xaxis.grid(False)
        ax.set_axisbelow(True)

        ax.legend(
            loc="lower center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=2,
            handlelength=2.2,
            borderpad=0.4,
            columnspacing=1.4,
            labelspacing=0.3,
        )

        output_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_png, dpi=300)
        plt.close(fig)
    print(f"Saved: {output_png}")


def plot_token_throughput_with_rate_line(df: pd.DataFrame, output_png: Path) -> None:
    """Stacked-bar (goodput / wasted M tokens) with output throughput
    (tokens/sec) overlaid as a single line on the right twin axis. Bars use
    the same goodput/wasted split as `plot_token_throughput_vs_goodput`;
    the line shows raw output throughput so the reader sees that the server
    is producing at a high rate even when most of it is wasted."""
    plot_df = df.dropna(
        subset=[
            "lambda",
            "output_goodput_tokens_per_s",
            "output_wasted_tokens_per_s",
            "duration_s",
            "output_tokens_per_s",
        ]
    ).sort_values("lambda")
    if plot_df.empty:
        print(f"[skip plot] no rows with required columns; nothing for {output_png}")
        return
    # Highest load on the left.
    plot_df = plot_df.iloc[::-1].reset_index(drop=True)
    lams = plot_df["lambda"].astype(float).to_numpy()
    duration_s = plot_df["duration_s"].astype(float).to_numpy()
    gp = (
        plot_df["output_goodput_tokens_per_s"].astype(float).clip(lower=0).to_numpy()
        * duration_s
    )
    wasted = (
        plot_df["output_wasted_tokens_per_s"].astype(float).clip(lower=0).to_numpy()
        * duration_s
    )
    classified = gp + wasted
    gp_m = gp / 1e6
    wasted_m = wasted / 1e6
    classified_m = classified / 1e6
    thp_rate = plot_df["output_tokens_per_s"].astype(float).to_numpy()

    AX_W, AX_H = 3.0, 1.85
    LEFT_IN, BOTTOM_IN = 0.55, 0.7
    RIGHT_PAD_IN, TOP_PAD_IN = 0.55, 0.55
    fig_w = LEFT_IN + AX_W + RIGHT_PAD_IN
    fig_h = BOTTOM_IN + AX_H + TOP_PAD_IN

    with plt.rc_context(_paper_style()):
        fig = plt.figure(figsize=(fig_w, fig_h))
        ax = fig.add_axes((
            LEFT_IN / fig_w,
            BOTTOM_IN / fig_h,
            AX_W / fig_w,
            AX_H / fig_h,
        ))

        idx = np.arange(len(lams))
        bar_w = 0.72
        ax.bar(
            idx, gp_m, width=bar_w,
            color="#1f77b4", alpha=0.55, edgecolor="black", linewidth=0.5,
            label="Goodput tokens",
        )
        ax.bar(
            idx, wasted_m, width=bar_w, bottom=gp_m,
            color="#d62728", alpha=0.55, edgecolor="black", linewidth=0.5,
            label="Wasted tokens",
        )

        # Categorical x-axis (matches the bar plot).
        ax.set_xticks(idx)
        ax.set_xticklabels([f"{v:g}" for v in lams])
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(45)
            lbl.set_horizontalalignment("right")
            lbl.set_rotation_mode("anchor")
        ax.set_xlim(-0.5, len(lams) - 0.5)
        ax.set_xlabel(r"Arrival rate $\lambda$ (jobs/sec)")
        ax.set_ylabel("Output tokens (M)")
        ymax_left = float(max(classified_m)) if len(classified_m) else 1.0
        ax.set_ylim(0, ymax_left * 1.08)

        # Right twin axis: throughput as a single line.
        ax2 = ax.twinx()
        ax2.plot(
            idx, thp_rate,
            marker="D", color="#2ca02c",
            markeredgecolor="white", markeredgewidth=0.5,
            label="Output throughput",
        )
        ax2.set_ylabel("Throughput (tokens/sec)")
        thp_max = float(max(thp_rate)) if len(thp_rate) else 1.0
        ax2.set_ylim(0, thp_max * 1.08)
        ax2.tick_params(axis="y", direction="in", width=0.7, size=3.0)
        for side in ("top", "left", "bottom"):
            ax2.spines[side].set_visible(False)

        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(True)
        ax.yaxis.grid(True, linestyle=":", linewidth=0.7, alpha=0.6)
        ax.xaxis.grid(False)
        ax.set_axisbelow(True)

        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(
            h1 + h2, l1 + l2,
            loc="lower center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=3,
            handlelength=2.0,
            borderpad=0.4,
            columnspacing=1.2,
            labelspacing=0.3,
        )

        output_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_png, dpi=300)
        plt.close(fig)
    print(f"Saved: {output_png}")


def _fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"{float(v) * 100:.1f}%"


def plot_goodput_vs_lambda(df: pd.DataFrame, output_png: Path) -> None:
    plot_df = df.dropna(subset=["lambda"]).sort_values("lambda")
    if plot_df.empty:
        print(f"[skip plot] no rows with lambda; nothing to plot for {output_png}")
        return
    x = plot_df["lambda"].astype(float).to_numpy()

    # ACM sigconf single-column figure: 3.3 in wide, 8-9pt fonts.
    style = {
        "font.family": "serif",
        "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
        "font.size": 8,
        "axes.labelsize": 9,
        "axes.titlesize": 9,
        "axes.linewidth": 0.75,
        "legend.fontsize": 8,
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
    # Outer figure size and exact inner axes box size (in inches).
    # The inner axes box (the framed plot area) is locked to AX_W x AX_H
    # via ax.set_position() so the rendered plot area matches the intended
    # paper-figure dimension regardless of label sizes.
    AX_W, AX_H = 3.0, 1.85
    # Right side carries the twin y-axis (rejection rate) plus its tick labels
    # and rotated axis label, so RIGHT_PAD_IN matches LEFT_IN.
    LEFT_IN, BOTTOM_IN = 0.45, 0.7
    RIGHT_PAD_IN, TOP_PAD_IN = 0.5, 0.55
    fig_w = LEFT_IN + AX_W + RIGHT_PAD_IN
    fig_h = BOTTOM_IN + AX_H + TOP_PAD_IN
    with plt.rc_context(style):
        fig = plt.figure(figsize=(fig_w, fig_h))
        ax = fig.add_axes((
            LEFT_IN / fig_w,
            BOTTOM_IN / fig_h,
            AX_W / fig_w,
            AX_H / fig_h,
        ))
        ax.plot(
            x, plot_df["call_goodput_rate"].astype(float) * 100.0,
            marker="o", color="#1f77b4", markeredgecolor="white", markeredgewidth=0.5,
            label="Request-level SLO attainment",
        )
        ax.plot(
            x, plot_df["job_goodput_rate"].astype(float) * 100.0,
            marker="s", color="#d62728", markeredgecolor="white", markeredgewidth=0.5,
            label="Job-level SLO attainment",
        )

        # Rejection rate is not an SLO attainment metric; put it on a twin
        # y-axis with its own label so the meaning of each axis is clean.
        rej_color = "#ff7f0e"
        ax2 = ax.twinx()
        ax2.plot(
            x, plot_df["call_rejection_rate"].astype(float) * 100.0,
            marker="^", color=rej_color, linestyle="--",
            markeredgecolor="white", markeredgewidth=0.5,
            label="Request rejection rate",
        )
        ax2.set_ylabel("Rejection rate (%)")
        ax2.set_ylim(0, 100)
        ax2.set_yticks([0, 20, 40, 60, 80, 100])
        ax2.tick_params(axis="y", direction="in", width=0.7, size=3.0)
        # Hide the other three spines on ax2 so they don't double-draw with ax.
        for side in ("top", "left", "bottom"):
            ax2.spines[side].set_visible(False)

        _apply_lambda_x_axis(ax, x)
        ax.set_ylabel("SLO attainment (%)")
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 20, 40, 60, 80, 100])

        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(True)
        ax.yaxis.grid(True, linestyle=":", linewidth=0.7, alpha=0.6)
        ax.xaxis.grid(False)
        ax.set_axisbelow(True)

        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(
            h1 + h2, l1 + l2,
            loc="lower center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=2,
            handlelength=2.2,
            borderpad=0.4,
            columnspacing=1.4,
            labelspacing=0.3,
        )

        output_png.parent.mkdir(parents=True, exist_ok=True)
        # No bbox_inches="tight": keep the figure size exactly equal to figsize
        # so the inner axes box stays at the intended physical dimensions.
        fig.savefig(output_png, dpi=300)
        plt.close(fig)
    print(f"Saved: {output_png}")


def plot_goodput_with_throughput_vs_lambda(df: pd.DataFrame, output_png: Path) -> None:
    """v2 of the SLO attainment plot.

    Differences from `plot_goodput_vs_lambda`:
    - Rejection rate stays in the figure but moves to the **left** y-axis
      (it is also a percentage and now shares the SLO-attainment scale).
    - The right twin y-axis is now **Throughput (output tokens/sec)** drawn
      as a line plot (not bars), so the motivation gap "throughput is high
      but goodput is low" reads in a single figure.
    """
    plot_df = df.dropna(
        subset=["lambda", "output_tokens_per_s"]
    ).sort_values("lambda")
    if plot_df.empty:
        print(f"[skip plot] no rows with lambda; nothing to plot for {output_png}")
        return
    x = plot_df["lambda"].astype(float).to_numpy()

    AX_W, AX_H = 3.0, 1.85
    LEFT_IN, BOTTOM_IN = 0.5, 0.7
    RIGHT_PAD_IN, TOP_PAD_IN = 0.55, 0.65
    fig_w = LEFT_IN + AX_W + RIGHT_PAD_IN
    fig_h = BOTTOM_IN + AX_H + TOP_PAD_IN

    with plt.rc_context(_paper_style()):
        fig = plt.figure(figsize=(fig_w, fig_h))
        ax = fig.add_axes((
            LEFT_IN / fig_w,
            BOTTOM_IN / fig_h,
            AX_W / fig_w,
            AX_H / fig_h,
        ))
        # Left axis: SLO attainment (%) lines + rejection rate line
        # (rejection is also a percentage, so it shares this scale).
        ax.plot(
            x, plot_df["call_goodput_rate"].astype(float) * 100.0,
            marker="o", color="#1f77b4", markeredgecolor="white", markeredgewidth=0.5,
            label="Request-level SLO attainment",
        )
        ax.plot(
            x, plot_df["job_goodput_rate"].astype(float) * 100.0,
            marker="s", color="#d62728", markeredgecolor="white", markeredgewidth=0.5,
            label="Job-level SLO attainment",
        )
        ax.plot(
            x, plot_df["call_rejection_rate"].astype(float) * 100.0,
            marker="^", color="#ff7f0e", linestyle="--",
            markeredgecolor="white", markeredgewidth=0.5,
            label="Request rejection rate",
        )

        # Right twin axis: throughput (output tokens/sec).
        thp_color = "#2ca02c"
        ax2 = ax.twinx()
        ax2.plot(
            x, plot_df["output_tokens_per_s"].astype(float),
            marker="D", color=thp_color, markeredgecolor="white", markeredgewidth=0.5,
            label="Output throughput",
        )
        ax2.set_ylabel("Throughput (tokens/sec)")
        thp_max = float(plot_df["output_tokens_per_s"].max())
        ax2.set_ylim(0, thp_max * 1.08)
        ax2.tick_params(axis="y", direction="in", width=0.7, size=3.0)
        for side in ("top", "left", "bottom"):
            ax2.spines[side].set_visible(False)

        _apply_lambda_x_axis(ax, x)
        ax.set_ylabel("SLO attainment / Rejection (%)")
        ax.set_ylim(0, 100)
        ax.set_yticks([0, 20, 40, 60, 80, 100])

        for side in ("top", "right", "bottom", "left"):
            ax.spines[side].set_visible(True)
        ax.yaxis.grid(True, linestyle=":", linewidth=0.7, alpha=0.6)
        ax.xaxis.grid(False)
        ax.set_axisbelow(True)

        h1, l1 = ax.get_legend_handles_labels()
        h2, l2 = ax2.get_legend_handles_labels()
        ax.legend(
            h1 + h2, l1 + l2,
            loc="lower center",
            bbox_to_anchor=(0.5, 1.02),
            ncol=2,
            handlelength=2.2,
            borderpad=0.4,
            columnspacing=1.4,
            labelspacing=0.3,
        )

        output_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_png, dpi=300)
        plt.close(fig)
    print(f"Saved: {output_png}")


def make_markdown(rows) -> str:
    headers = [
        ("lambda", "λ"),
        ("call_success_rate", "call success"),
        ("call_goodput_rate", "call goodput"),
        ("call_rejection_rate", "call reject"),
        ("job_completion_rate", "job complete"),
        ("job_goodput_rate", "job goodput"),
        ("wasted_compute_ratio", "wasted compute"),
    ]
    lines = ["| " + " | ".join(h[1] for h in headers) + " |"]
    lines.append("|" + "|".join(["---:" for _ in headers]) + "|")
    for r in rows:
        cells = []
        for key, _ in headers:
            v = r.get(key)
            if key == "lambda":
                cells.append("-" if v is None else f"{float(v):g}")
            else:
                cells.append(_fmt_pct(v))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(
        description="Re-aggregate application summary over a [start, end] minute window."
    )
    p.add_argument(
        "--run-dirs",
        nargs="+",
        required=True,
        help="One or more experiment run directories (each must contain analysis/).",
    )
    p.add_argument(
        "--window-min",
        nargs=2,
        type=float,
        metavar=("START", "END"),
        required=True,
        help="Window in minutes from each run's first call. Example: --window-min 20 80",
    )
    p.add_argument(
        "--output-csv",
        type=str,
        default=None,
        help="Output CSV path. If omitted, only the table is printed.",
    )
    p.add_argument(
        "--print-markdown",
        action="store_true",
        help="Print a markdown summary table to stdout.",
    )
    p.add_argument(
        "--plot-png",
        type=str,
        default=None,
        help="If set, save the SLO-attainment-vs-lambda PNG to this path.",
    )
    p.add_argument(
        "--plot-throughput-png",
        type=str,
        default=None,
        help="If set, save the token-throughput-vs-goodput stacked-area PNG to this path.",
    )
    p.add_argument(
        "--plot-png-v2",
        type=str,
        default=None,
        help="If set, save the v2 SLO-attainment plot (rejection on left axis, throughput on right twin axis) to this path.",
    )
    p.add_argument(
        "--plot-throughput-with-rate-png",
        type=str,
        default=None,
        help="If set, save the throughput stacked-bar plot with an output-throughput (tokens/sec) line overlaid on the right twin axis.",
    )
    p.add_argument(
        "--plot-throughput-call-job-png",
        type=str,
        default=None,
        help="If set, save the 4-sub-segment token plot splitting goodput/wasted by individual call_goodput status (pure / slow-call-in-good-job / good-call-in-wasted-job / pure-wasted).",
    )
    args = p.parse_args()

    if args.window_min[1] <= args.window_min[0]:
        p.error("END must be greater than START")

    window = (args.window_min[0], args.window_min[1])
    rows = []
    for d in args.run_dirs:
        run_dir = Path(d)
        try:
            row = summarize_run(run_dir, window)
        except (FileNotFoundError, ValueError) as e:
            print(f"[skip] {d}: {e}", file=sys.stderr)
            continue
        rows.append(row)

    if not rows:
        print("No runs summarized.", file=sys.stderr)
        sys.exit(1)

    df = pd.DataFrame(rows)
    if "lambda" in df.columns and df["lambda"].notna().any():
        df = df.sort_values(by=["lambda", "run"], na_position="last").reset_index(drop=True)
    else:
        df = df.sort_values(by=["run"]).reset_index(drop=True)

    if args.output_csv:
        out = Path(args.output_csv)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(out, index=False)
        print(f"Saved: {out}")

    if args.print_markdown:
        print()
        print(make_markdown(df.to_dict("records")))

    # Gap between request-level and job-level goodput, both as absolute
    # percentage-point difference and as a ratio (how many times higher
    # request-level is than job-level). The two metrics may peak at
    # different lambdas.
    if (
        "lambda" in df.columns
        and df["lambda"].notna().any()
        and df[["call_goodput_rate", "job_goodput_rate"]].notna().all().all()
    ):
        ranked = df.dropna(subset=["lambda"]).copy()
        req = ranked["call_goodput_rate"].astype(float)
        job = ranked["job_goodput_rate"].astype(float)
        ranked["gap_pp"] = (req - job) * 100.0
        ranked["ratio_req_over_job"] = req.where(job > 0).divide(job.where(job > 0))

        def _fmt(row, label):
            return (
                f"{label}: lambda={row['lambda']:g}  "
                f"request={row['call_goodput_rate'] * 100:.1f}%  "
                f"job={row['job_goodput_rate'] * 100:.1f}%  "
                f"gap={row['gap_pp']:.1f}pp  "
                f"ratio={row['ratio_req_over_job']:.2f}x"
            )

        i_pp = ranked["gap_pp"].idxmax()
        print()
        print(_fmt(ranked.loc[i_pp], "Largest absolute gap"))
        if ranked["ratio_req_over_job"].notna().any():
            i_ratio = ranked["ratio_req_over_job"].idxmax()
            print(_fmt(ranked.loc[i_ratio], "Largest ratio       "))

    if args.plot_png:
        plot_goodput_vs_lambda(df, Path(args.plot_png))
    if args.plot_throughput_png:
        plot_token_throughput_vs_goodput(df, Path(args.plot_throughput_png))
    if args.plot_png_v2:
        plot_goodput_with_throughput_vs_lambda(df, Path(args.plot_png_v2))
    if args.plot_throughput_with_rate_png:
        plot_token_throughput_with_rate_line(df, Path(args.plot_throughput_with_rate_png))
    if args.plot_throughput_call_job_png:
        plot_token_throughput_call_job_breakdown(df, Path(args.plot_throughput_call_job_png))


if __name__ == "__main__":
    main()
