#!/usr/bin/env python3
"""Diagnose the goodput cliff between an admission-control run and a
no-admission run at the SAME lambda and seed.

Both runs must share the Poisson arrival pattern (same seed) so the only
difference is admission control. The script produces a 4-panel
time-series figure (shared x = minutes from each run's first call) and
prints latency-distribution and job-kill statistics.

Panels (each: W/ admission blue, W/O admission red):
  (1) goodput calls per minute      -- where goodput collapses in time
  (2) median call latency per minute (non-rejected calls) -- the cliff
  (3) server running_req per minute  -- decode-batch / queue saturation
  (4) rejected calls per minute      -- admission run only

Job-kill statistic: an admission reject ends the whole agentic job, so
the job's remaining downstream calls are never sent. The script reports
rejected calls, killed jobs, and estimated un-sent downstream calls
(``total_calls_expected - call_index`` summed over killed jobs) -- this
quantifies how much load one reject actually removes.

Usage:
  python analysis_scripts/diagnose_saturation_cliff.py \
    --adm-run   results/260510_1625_admission_ratio_tp_tau5_lambda_0p075 \
    --noadm-run results/260511_2314_no_admission_tp_tau5_lambda_0p075 \
    --window-min 20 80 \
    --out-dir results/aggregate_analysis/cliff_diagnosis_lambda_0p075
"""
from __future__ import annotations

import argparse
import os

import numpy as np
import pandas as pd

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
    "lines.linewidth": 1.3,
    "lines.markersize": 3.0,
}
COLOR_ADM = "#1f77b4"     # W/ admission
COLOR_NOADM = "#d62728"   # W/O admission


def load_calls(run_dir: str) -> tuple:
    """Return (calls_df, t0) where t0 is the run's first call start_time."""
    path = os.path.join(run_dir, "analysis", "application_calls.csv")
    calls = pd.read_csv(path)
    t0 = float(calls["start_time"].min())
    calls = calls.copy()
    calls["minute"] = ((calls["start_time"] - t0) / 60.0).astype(float)
    return calls, t0


def load_server(run_dir: str, t0: float) -> pd.DataFrame:
    """Server decode events with a client-t0-anchored 'minute' column."""
    path = os.path.join(run_dir, "analysis", "server_metrics.csv")
    sm = pd.read_csv(path)
    dec = sm[sm["event_type"].astype(str) == "decode"].copy()
    for c in ("epoch", "running_req", "token_usage", "queue_req"):
        if c in dec.columns:
            dec[c] = pd.to_numeric(dec[c], errors="coerce")
    dec["minute"] = (dec["epoch"] - t0) / 60.0
    return dec


def per_minute_count(df: pd.DataFrame, mask: pd.Series, max_min: float) -> pd.Series:
    """Count of rows (where mask True) per integer minute bin."""
    sel = df[mask]
    bins = np.arange(0, int(max_min) + 2)
    cnt, _ = np.histogram(sel["minute"], bins=bins)
    return pd.Series(cnt, index=bins[:-1])


def per_minute_agg(df: pd.DataFrame, col: str, max_min: float, how: str = "median") -> pd.Series:
    """Aggregate `col` per integer minute bin."""
    d = df.dropna(subset=[col, "minute"]).copy()
    d["mbin"] = d["minute"].astype(int)
    g = d.groupby("mbin")[col]
    s = g.median() if how == "median" else g.mean()
    return s.reindex(range(0, int(max_min) + 1))


def job_kill_stats(calls: pd.DataFrame, w0: float, w1: float) -> dict:
    """Quantify how much load admission rejects actually remove.

    The parallel workload runs in execution rounds: when a call in a
    round is rejected, the round's ``if failed: break`` ends the job, so
    every later round of that job is never launched. A round can launch
    several Locate calls in parallel, so one killed job may contribute
    several rejected-call rows.

    Computed PER JOB (task_id), not per rejected call:
      * killed_jobs            -- jobs with >=1 rejected call in window
      * for each killed job, over its FULL chain (window-independent):
          launched   = call rows present (rejected + server-processed)
          unsent     = total_calls_expected - launched (never sent)
    """
    inwin = calls[(calls["minute"] >= w0) & (calls["minute"] < w1)]
    n_rej_calls = int((inwin["is_rejected_bool"] == True).sum())  # noqa: E712
    killed_ids = set(inwin.loc[inwin["is_rejected_bool"] == True, "task_id"])  # noqa: E712
    killed = len(killed_ids)
    if killed == 0:
        return {"rejected_calls_in_window": 0, "killed_jobs": 0,
                "calls_launched_by_killed_jobs": 0, "killed_jobs_rejected_calls": 0,
                "killed_jobs_server_processed_calls": 0,
                "unsent_downstream_calls": 0, "load_removed_vs_noadm": 0,
                "removed_per_killed_job": float("nan")}

    kc = calls[calls["task_id"].isin(killed_ids)]
    launched = int(len(kc))
    rej_calls_total = int((kc["is_rejected_bool"] == True).sum())  # noqa: E712
    server_processed = launched - rej_calls_total
    per_job = kc.groupby("task_id").agg(
        launched=("call_index", "size"),
        total_expected=("total_calls_expected", "first"),
    )
    per_job["total_expected"] = pd.to_numeric(per_job["total_expected"], errors="coerce")
    unsent = int((per_job["total_expected"] - per_job["launched"]).clip(lower=0).fillna(0).sum())
    return {
        "rejected_calls_in_window": n_rej_calls,
        "killed_jobs": killed,
        "calls_launched_by_killed_jobs": launched,
        "killed_jobs_rejected_calls": rej_calls_total,
        "killed_jobs_server_processed_calls": server_processed,
        "unsent_downstream_calls": unsent,
        "load_removed_vs_noadm": rej_calls_total + unsent,
        "removed_per_killed_job": (rej_calls_total + unsent) / killed,
    }


def counterfactual_pure_request_reject(adm_calls: pd.DataFrame,
                                       noadm_calls: pd.DataFrame,
                                       w0: float, w1: float) -> dict:
    """Estimate how many rejects a PURE request-level policy (no job-kill
    side effect) would need to match the admission run's server load.

    The naive estimate (full chain length of killed jobs) over-counts:
    a surviving job cannot launch its whole chain inside the window --
    chains have serial segments and each call has real latency, so an
    80-min window holds only part of a long chain.

    The no-admission run IS the measured counterfactual: there every job
    survives, so the in-window call count of the killed-job set there is
    the real load a pure request-level policy would have to shave down.
    Requires both runs to share seed/task pool so task_id matches.
    """
    am = adm_calls[(adm_calls["minute"] >= w0) & (adm_calls["minute"] < w1)]
    killed = set(am.loc[am["is_rejected_bool"] == True, "task_id"])  # noqa: E712
    aj = am[am["task_id"].isin(killed)]
    a_rejected = int((aj["is_rejected_bool"] == True).sum())  # noqa: E712
    a_processed = int((aj["is_rejected_bool"] != True).sum())  # noqa: E712

    nm = noadm_calls[(noadm_calls["minute"] >= w0) & (noadm_calls["minute"] < w1)]
    nj = nm[nm["task_id"].isin(killed)]
    n_launched = int(len(nj))
    n_matched = int(nj["task_id"].nunique())

    # pure request-level: jobs survive -> all n_launched in-window calls
    # arrive; to hold server-processed load at the admission level
    # (a_processed) the policy must reject (n_launched - a_processed).
    need = max(0, n_launched - a_processed)
    return {
        "killed_jobs": len(killed),
        "killed_jobs_matched_in_noadm": n_matched,
        "adm_inwindow_calls_of_killed": a_rejected + a_processed,
        "adm_rejected": a_rejected,
        "adm_server_processed": a_processed,
        "noadm_inwindow_calls_of_killed": n_launched,
        "corrected_pure_reject_estimate": need,
    }


def latency_dist(calls: pd.DataFrame, w0: float, w1: float) -> pd.DataFrame:
    """TTFT / TBT / latency / slowdown median & p90 over the window
    (non-rejected calls only -- rejected calls have ~0 latency)."""
    inwin = calls[(calls["minute"] >= w0) & (calls["minute"] < w1)]
    served = inwin[inwin["is_rejected_bool"] != True]  # noqa: E712
    rows = []
    for col, name in [("first_token_latency", "TTFT (s)"),
                       ("tbt_mean_ms", "TBT mean (ms)"),
                       ("latency", "call latency (s)"),
                       ("call_slowdown", "call slowdown (x)")]:
        v = pd.to_numeric(served[col], errors="coerce").dropna()
        rows.append({"metric": name, "n": len(v),
                     "median": v.median(), "p90": v.quantile(0.90)})
    return pd.DataFrame(rows)


def plot_panels(adm: dict, noadm: dict, w0: float, w1: float, png: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    max_min = max(adm["calls"]["minute"].max(), noadm["calls"]["minute"].max())

    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(4, 1, figsize=(3.5, 6.4), sharex=True)

        for run, color in ((adm, COLOR_ADM), (noadm, COLOR_NOADM)):
            calls = run["calls"]
            label = run["label"]
            # (1) goodput calls per minute
            gp = per_minute_count(calls, calls["call_goodput_bool"] == True, max_min)  # noqa: E712
            axes[0].plot(gp.index, gp.values, color=color, label=label)
            # (2) median latency per minute (non-rejected)
            served = calls[calls["is_rejected_bool"] != True]  # noqa: E712
            lat = per_minute_agg(served, "latency", max_min, "median")
            axes[1].plot(lat.index, lat.values, color=color, label=label)
            # (3) server running_req per minute
            sm = run["server"]
            rr = per_minute_agg(sm, "running_req", max_min, "mean")
            axes[2].plot(rr.index, rr.values, color=color, label=label)
            # (4) rejected calls per minute
            rj = per_minute_count(calls, calls["is_rejected_bool"] == True, max_min)  # noqa: E712
            axes[3].plot(rj.index, rj.values, color=color, label=label)

        axes[0].set_ylabel("Goodput calls / min")
        axes[1].set_ylabel("Median call\nlatency (s)")
        axes[2].set_ylabel("Server\nrunning_req")
        axes[3].set_ylabel("Rejected calls / min")
        axes[3].set_xlabel("Time since first call (min)")

        for ax in axes:
            for s in ax.spines.values():
                s.set_visible(True)
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            ax.axvspan(w0, w1, color="0.85", alpha=0.5, zorder=0)
            ax.set_ylim(bottom=0)
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, 1.03), ncol=2)

        os.makedirs(os.path.dirname(os.path.abspath(png)), exist_ok=True)
        fig.savefig(png, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"Saved: {png}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adm-run", required=True, help="admission-control run dir")
    ap.add_argument("--noadm-run", required=True, help="no-admission run dir")
    ap.add_argument("--window-min", nargs=2, type=float, metavar=("START", "END"),
                    default=[20.0, 80.0])
    ap.add_argument("--out-dir",
                    default="results/aggregate_analysis/cliff_diagnosis")
    args = ap.parse_args()
    w0, w1 = args.window_min

    adm_calls, adm_t0 = load_calls(args.adm_run)
    noadm_calls, noadm_t0 = load_calls(args.noadm_run)
    adm = {"calls": adm_calls, "server": load_server(args.adm_run, adm_t0),
           "label": "W/ admission"}
    noadm = {"calls": noadm_calls, "server": load_server(args.noadm_run, noadm_t0),
             "label": "W/O admission"}

    os.makedirs(args.out_dir, exist_ok=True)
    plot_panels(adm, noadm, w0, w1,
                os.path.join(args.out_dir, "cliff_panels.png"))

    print(f"\n=== Window [{w0:g}, {w1:g}] min ===\n")
    print("--- (2) latency distribution: W/ admission ---")
    print(latency_dist(adm_calls, w0, w1).to_string(index=False))
    print("\n--- (2) latency distribution: W/O admission ---")
    print(latency_dist(noadm_calls, w0, w1).to_string(index=False))

    print("\n--- (5) job-kill: how much load one reject removes (W/ admission) ---")
    jk = job_kill_stats(adm_calls, w0, w1)
    for k, v in jk.items():
        print(f"  {k:28s} {v:.2f}" if isinstance(v, float) else f"  {k:28s} {v}")
    jk_csv = os.path.join(args.out_dir, "job_kill_stats.csv")
    pd.DataFrame([jk]).to_csv(jk_csv, index=False)
    print(f"Saved: {jk_csv}")

    print("\n--- (6) pure request-level reject estimate "
          "(no job-kill; counterfactual = no-admission run) ---")
    cf = counterfactual_pure_request_reject(adm_calls, noadm_calls, w0, w1)
    for k, v in cf.items():
        print(f"  {k:34s} {v}")
    cf_csv = os.path.join(args.out_dir, "pure_request_reject_estimate.csv")
    pd.DataFrame([cf]).to_csv(cf_csv, index=False)
    print(f"Saved: {cf_csv}")


if __name__ == "__main__":
    main()
