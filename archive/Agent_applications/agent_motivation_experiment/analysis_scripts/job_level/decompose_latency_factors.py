#!/usr/bin/env python3
"""Decompose request-level latency (TTFT, TBT) into server-side factors
to explain why goodput collapses, and compare an admission-control run
against a no-admission run at the SAME lambda and seed.

Request-level view: every chain call is treated as one independent
request. (Caveat: arrivals are chain-driven and input_tokens accumulate
along the chain, so this is NOT a pure Poisson request-level load -- it
explains "why was this call slow", not pure request-level dynamics.)

call latency = TTFT + TBT * output_tokens, so goodput loss is driven by:

  TTFT  ~= queueing delay (server busy -> request waits to enter a batch)
           + prefill compute (input tokens)
  TBT   ~= decode step time, driven by
           decode batch size (running_req) and KV-cache pressure (token_usage)

Server signals (server_metrics.csv):
  decode events    -> running_req, token_usage, gen_throughput
  req_stats events -> queue_duration_ms, forward_duration_ms, input_len

Outputs (all into --out-dir):
  * factor_timeseries.png  -- 6-panel time series, admission vs no-admission
  * regression.csv         -- call-level OLS: TBT ~ running_req + token_usage
  * cliff_decomposition.csv-- pre- vs post-cliff factor change

Usage:
  python analysis_scripts/decompose_latency_factors.py \
    --adm-run   results/260510_1625_admission_ratio_tp_tau5_lambda_0p075 \
    --noadm-run results/260511_2314_no_admission_tp_tau5_lambda_0p075 \
    --pre-min 5 20 --post-min 50 80 \
    --out-dir results/aggregate_analysis/latency_decomposition_lambda_0p075
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
    "axes.labelsize": 8.5,
    "axes.linewidth": 0.75,
    "legend.fontsize": 7,
    "legend.frameon": False,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "xtick.direction": "in",
    "ytick.direction": "in",
    "lines.linewidth": 1.2,
    "lines.markersize": 2.5,
}
COLOR_ADM = "#1f77b4"     # W/ admission
COLOR_NOADM = "#d62728"   # W/O admission


def load_run(run_dir: str) -> dict:
    """Load calls + server signals for one run, anchored to the run's
    first call (minute 0). Attaches the server decode state
    (running_req, token_usage) at each call's start_time."""
    calls = pd.read_csv(os.path.join(run_dir, "analysis", "application_calls.csv"))
    t0 = float(calls["start_time"].min())
    calls = calls.copy()
    calls["minute"] = (calls["start_time"] - t0) / 60.0
    for c in ("first_token_latency", "tbt_mean_ms", "input_tokens",
              "output_tokens", "latency"):
        calls[c] = pd.to_numeric(calls[c], errors="coerce")

    sm = pd.read_csv(os.path.join(run_dir, "analysis", "server_metrics.csv"))
    dec = sm[sm["event_type"].astype(str) == "decode"].copy()
    for c in ("epoch", "running_req", "queue_req", "token_usage", "gen_throughput"):
        if c in dec.columns:
            dec[c] = pd.to_numeric(dec[c], errors="coerce")
    dec = dec.dropna(subset=["epoch"]).sort_values("epoch")
    dec["epoch"] = dec["epoch"].astype(float)
    dec["minute"] = (dec["epoch"] - t0) / 60.0

    pf = sm[sm["event_type"].astype(str) == "prefill"].copy()
    for c in ("epoch", "new_token", "cached_token", "running_req",
              "queue_req", "token_usage", "prefill_throughput"):
        if c in pf.columns:
            pf[c] = pd.to_numeric(pf[c], errors="coerce")
    pf = pf.dropna(subset=["epoch"]).sort_values("epoch")
    pf["epoch"] = pf["epoch"].astype(float)
    pf["minute"] = (pf["epoch"] - t0) / 60.0
    denom = pf["new_token"].fillna(0) + pf["cached_token"].fillna(0)
    pf["cache_hit_ratio"] = pf["cached_token"].fillna(0) / denom.where(denom > 0)

    rs = sm[sm["event_type"].astype(str) == "req_stats"].copy()
    for c in ("epoch", "queue_duration_ms", "forward_duration_ms", "input_len"):
        rs[c] = pd.to_numeric(rs[c], errors="coerce")
    rs = rs.dropna(subset=["epoch"]).sort_values("epoch")
    rs["epoch"] = rs["epoch"].astype(float)
    rs["minute"] = (rs["epoch"] - t0) / 60.0

    # attach server state at each call's start_time (nearest event)
    calls = calls.sort_values("start_time")
    calls = pd.merge_asof(
        calls, dec[["epoch", "running_req", "queue_req", "token_usage"]].rename(
            columns={"epoch": "start_time"}),
        on="start_time", direction="nearest", tolerance=15.0)
    calls = pd.merge_asof(
        calls, pf[["epoch", "cache_hit_ratio"]].rename(
            columns={"epoch": "start_time"}),
        on="start_time", direction="nearest", tolerance=15.0)
    calls = pd.merge_asof(
        calls, rs[["epoch", "queue_duration_ms"]].rename(
            columns={"epoch": "start_time"}),
        on="start_time", direction="nearest", tolerance=5.0)
    return {"calls": calls, "decode": dec, "prefill": pf, "reqstats": rs, "t0": t0}


def per_minute(df: pd.DataFrame, col: str, how: str, max_min: float) -> pd.Series:
    d = df.dropna(subset=[col, "minute"]).copy()
    d["mbin"] = d["minute"].astype(int)
    g = d.groupby("mbin")[col]
    s = g.median() if how == "median" else g.mean()
    return s.reindex(range(0, int(max_min) + 1))


def ols_standardized(y: np.ndarray, X: pd.DataFrame) -> dict:
    """OLS with z-scored predictors so coefficients are comparable as
    relative influence. Returns per-predictor standardized beta plus R^2."""
    mask = np.isfinite(y) & np.isfinite(X.to_numpy()).all(axis=1)
    y = y[mask]
    Xv = X.to_numpy()[mask]
    if len(y) < 10:
        return {"n": int(len(y)), "r2": float("nan")}
    Xz = (Xv - Xv.mean(axis=0)) / (Xv.std(axis=0) + 1e-12)
    yz = (y - y.mean()) / (y.std() + 1e-12)
    design = np.column_stack([np.ones(len(yz)), Xz])
    beta, *_ = np.linalg.lstsq(design, yz, rcond=None)
    yhat = design @ beta
    r2 = 1.0 - ((yz - yhat) ** 2).sum() / ((yz - yz.mean()) ** 2).sum()
    out = {"n": int(len(y)), "r2": float(r2)}
    for name, b in zip(X.columns, beta[1:]):
        out[f"std_beta_{name}"] = float(b)
    return out


def plot_timeseries(adm: dict, noadm: dict, pre: tuple, post: tuple,
                    png: str) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    max_min = max(adm["calls"]["minute"].max(), noadm["calls"]["minute"].max())
    # (df-key, column, agg, y-label)
    panels = [
        ("calls", "first_token_latency", "median", "TTFT (s)"),
        ("calls", "tbt_mean_ms", "median", "TBT (ms)"),
        ("decode", "token_usage", "mean", "KV usage"),
        ("decode", "running_req", "mean", "running_req"),
        ("decode", "queue_req", "mean", "waiting_req"),
        ("calls", "queue_duration_ms", "median", "waiting time (ms)"),
        ("prefill", "cache_hit_ratio", "mean", "Prefill cache hit"),
        ("calls", "__goodput__", "count", "Goodput calls / min"),
    ]
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(len(panels), 1, figsize=(3.8, 11.5), sharex=True)
        for ax, (key, col, how, ylab) in zip(axes, panels):
            for run, color, label in ((adm, COLOR_ADM, "W/ admission"),
                                      (noadm, COLOR_NOADM, "W/O admission")):
                df = run[key]
                if col == "__goodput__":
                    d = df.dropna(subset=["minute"]).copy()
                    d["mbin"] = d["minute"].astype(int)
                    s = d[d["call_goodput_bool"] == True].groupby(  # noqa: E712
                        "mbin").size().reindex(range(0, int(max_min) + 1),
                                               fill_value=0)
                else:
                    s = per_minute(df, col, how, max_min)
                ax.plot(s.index, s.values, color=color, label=label)
            ax.set_ylabel(ylab)
            ax.set_ylim(bottom=0)
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            for sp in ax.spines.values():
                sp.set_visible(True)
            ax.axvspan(pre[0], pre[1], color="#88cc88", alpha=0.18, zorder=0)
            ax.axvspan(post[0], post[1], color="#cc8888", alpha=0.18, zorder=0)
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, 1.04), ncol=2)
        axes[-1].set_xlabel("Time since first call (min)  "
                            "[green=pre-cliff, red=post-cliff]")
        os.makedirs(os.path.dirname(os.path.abspath(png)), exist_ok=True)
        fig.savefig(png, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"Saved: {png}")


def cliff_decomposition(run: dict, pre: tuple, post: tuple) -> dict:
    """Median of each factor in the pre-cliff and post-cliff windows."""
    calls, dec = run["calls"], run["decode"]

    def med(df, col, w):
        sel = df[(df["minute"] >= w[0]) & (df["minute"] < w[1])]
        return float(pd.to_numeric(sel[col], errors="coerce").median())

    row = {}
    for src, col, name in [(calls, "first_token_latency", "TTFT_s"),
                           (calls, "queue_duration_ms", "queue_delay_ms"),
                           (calls, "tbt_mean_ms", "TBT_ms"),
                           (calls, "input_tokens", "input_tokens"),
                           (calls, "latency", "call_latency_s"),
                           (dec, "running_req", "running_req"),
                           (dec, "token_usage", "kv_usage")]:
        pre_v, post_v = med(src, col, pre), med(src, col, post)
        row[f"{name}_pre"] = pre_v
        row[f"{name}_post"] = post_v
        row[f"{name}_delta"] = post_v - pre_v
        row[f"{name}_ratio"] = post_v / pre_v if pre_v else float("nan")
    return row


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adm-run", required=True)
    ap.add_argument("--noadm-run", required=True)
    ap.add_argument("--pre-min", nargs=2, type=float, default=[5.0, 20.0],
                    metavar=("START", "END"), help="pre-cliff window (min)")
    ap.add_argument("--post-min", nargs=2, type=float, default=[50.0, 80.0],
                    metavar=("START", "END"), help="post-cliff window (min)")
    ap.add_argument("--out-dir",
                    default="results/aggregate_analysis/latency_decomposition")
    args = ap.parse_args()
    pre, post = tuple(args.pre_min), tuple(args.post_min)

    adm = load_run(args.adm_run)
    noadm = load_run(args.noadm_run)
    os.makedirs(args.out_dir, exist_ok=True)

    plot_timeseries(adm, noadm, pre, post,
                    os.path.join(args.out_dir, "factor_timeseries.png"))

    # --- call-level regression (standardized betas = relative influence) ---
    reg_rows = []
    for label, run in (("W/ admission", adm), ("W/O admission", noadm)):
        c = run["calls"]
        # TBT: decode-step time
        tbt = ols_standardized(c["tbt_mean_ms"].to_numpy(),
                               c[["running_req", "token_usage"]])
        tbt["run"] = label; tbt["target"] = "TBT_ms"
        # TTFT: time-to-first-token
        ttft = ols_standardized(c["first_token_latency"].to_numpy(),
                                c[["queue_duration_ms", "input_tokens"]])
        ttft["run"] = label; ttft["target"] = "TTFT_s"
        # Waiting time decomposition: queue_duration ~ server-state factors
        wait = ols_standardized(c["queue_duration_ms"].to_numpy(),
                                c[["running_req", "queue_req",
                                   "token_usage", "cache_hit_ratio"]])
        wait["run"] = label; wait["target"] = "queue_duration_ms"
        reg_rows += [tbt, ttft, wait]
    reg = pd.DataFrame(reg_rows)
    reg_csv = os.path.join(args.out_dir, "regression.csv")
    reg.to_csv(reg_csv, index=False)
    print(f"Saved: {reg_csv}\n")
    print("--- call-level OLS (standardized betas = relative influence) ---")
    print(reg.to_string(index=False))

    # --- cliff pre/post decomposition ---
    dec_rows = []
    for label, run in (("W/ admission", adm), ("W/O admission", noadm)):
        r = cliff_decomposition(run, pre, post)
        r["run"] = label
        dec_rows.append(r)
    dec_df = pd.DataFrame(dec_rows)
    dec_csv = os.path.join(args.out_dir, "cliff_decomposition.csv")
    dec_df.to_csv(dec_csv, index=False)
    print(f"\nSaved: {dec_csv}\n")
    print(f"--- pre-cliff {pre} vs post-cliff {post}: factor medians ---")
    show = ["run"] + [c for c in dec_df.columns
                      if c.endswith(("_pre", "_post", "_ratio"))]
    print(dec_df[show].to_string(index=False))


if __name__ == "__main__":
    main()
