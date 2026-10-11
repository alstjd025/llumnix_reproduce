"""EXP-17 — throughput vs goodput over time, and vs offered rate. FIFO vs QoServe.

Goodput definition (as agreed): a request is judged as a WHOLE — if it met its
class SLO (chat TTFT<=5s & TBT<=50ms | deepresearch TTFT<=10s & TBT<=100ms |
swe E2E<=SWE_E2E_SLO_S) then ALL of its OUTPUT tokens are goodput, otherwise all
of them are wasted. Input tokens are never counted.

Time placement (fixed 2026-07-25): the classification is per request, but the
tokens are placed over the interval in which they were actually produced —
uniformly across the decode window [start+TTFT, end] — NOT dumped into the bin
of the completion instant. The earlier completion-instant version made a long
request appear as a spike at its end and zero before, so the curve fell to 0
whenever completions paused even though the engine was still generating.

Two throughput curves are drawn so the reconstruction is verifiable:
  * server  — sum over the 4 engines of d(vllm:generation_tokens_total)/dt from
              the 1 s Prometheus scrapes. Ground truth, SLO-blind.
  * client  — the same spreading applied to every completed request. If it
              tracks the server curve, the spreading assumption is sound and the
              goodput curve (its SLO-meeting subset) can be trusted.

Usage:
  SWE_E2E_SLO_S=30 python analysis_scripts/request_level/exp17_goodput_tokens.py \
      --out-dir results/aggregate_analysis/exp17_qoserve
"""

import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import served_rows, class_of  # noqa: E402
from exp14_per_class_slo import SLO_RULES, per_class_violate  # noqa: E402

ARMS = {
    "fifo":    "*exp14_mixA_rpm_*",
    "edf":     "*exp18_edf_mixA_rpm_*",
    "sjf":     "*exp19_sjf_mixA_rpm_*",
    "srpf":    "*exp20_srpf_mixA_rpm_*",
    "qoserve": "*exp17b_qoservefix_mixA_rpm_*",
    # Routing axis (engine=FIFO). Different rpm grid, but goodput is normalised
    # by the FIXED 60-340s window below, not by a run's own span, so the arms
    # stay comparable regardless of which rates each one sampled.
    "polyserve": "*exp21_polyserve_mixA_rpm_*",
}
PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "xtick.major.size": 3.0, "ytick.major.size": 3.0,
    "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "lines.linewidth": 1.4, "lines.markersize": 4.5,
}
ARM_STYLE = {
    "fifo":    dict(color="#d62728", ls="--", marker="o", label="FIFO (baseline)"),
    "edf":     dict(color="#2ca02c", ls="-.", marker="^", label="EDF (deadline order only)"),
    "sjf":     dict(color="#9467bd", ls=":",  marker="D", label="SJF (shortest job)"),
    "srpf":    dict(color="#8c564b", ls=(0,(3,1,1,1)), marker="v", label="SRPF (shortest remaining)"),
    "qoserve": dict(color="#1f77b4", ls="-",  marker="s", label="QoServe (deadline)"),
    "polyserve": dict(color="#000000", ls="-", marker="*", label="PolyServe (ROUTING, engine=FIFO)"),
}
BIN_S = 20.0
# served_rows' nominal steady window: [STEADY_LO=60, min(last,360)-DRAIN=340].
# Rates are normalised by this FIXED span for both arms — never by a run's own
# observed span, which collapses for FIFO under overload and would flatter it.
WIN_LO, WIN_HI = 60.0, 340.0
WIN_S = WIN_HI - WIN_LO
EDGES = np.arange(WIN_LO, WIN_HI + BIN_S, BIN_S)
CENTERS_MIN = (EDGES[:-1] + BIN_S / 2) / 60.0


# ---------------------------------------------------------------- loading ---
def load(results_dir):
    """{arm: {rate_rps: dict(df=..., run_dir=..., t0=...)}}"""
    out = {}
    for arm, pattern in ARMS.items():
        by_rpm = {}
        for d in sorted(glob.glob(os.path.join(results_dir, pattern))):
            by_rpm[int(re.search(r"rpm_(\d+)", d).group(1))] = d
        per_rate = {}
        for rpm, d in sorted(by_rpm.items()):
            sr = served_rows(d)
            if sr is None or sr.empty:
                continue
            sr["class"] = sr["task_id"].map(class_of)
            good = ~per_class_violate(sr)
            rel = pd.to_numeric(sr["rel"], errors="coerce")
            start = pd.to_numeric(sr["start_time"], errors="coerce")
            df = pd.DataFrame({
                "rel": rel,
                "ttft": pd.to_numeric(sr["first_token_latency"], errors="coerce").fillna(0),
                "lat": pd.to_numeric(sr["latency"], errors="coerce").fillna(0),
                "out": pd.to_numeric(sr["output_tokens"], errors="coerce").fillna(0),
                "good": good.values,
            }).dropna(subset=["rel"])
            per_rate[rpm / 60.0] = dict(df=df, run_dir=d,
                                        t0=float((start - rel).median()))
        out[arm] = per_rate
    return out


# ------------------------------------------------------------- token math ---
def _spread(t0s, t1s, weights, edges=EDGES):
    """Distribute each weight uniformly over [t0, t1) across the bins."""
    acc = np.zeros(len(edges) - 1)
    n = len(acc)
    for a, b, w in zip(t0s, t1s, weights):
        if w <= 0 or not np.isfinite(a):
            continue
        if not np.isfinite(b) or b <= a:          # no decode span: one instant
            i = int(np.searchsorted(edges, a, side="right")) - 1
            if 0 <= i < n:
                acc[i] += w
            continue
        lo = min(max(int(np.searchsorted(edges, a, side="right")) - 1, 0), n - 1)
        hi = min(max(int(np.searchsorted(edges, b, side="right")) - 1, 0), n - 1)
        dur = b - a
        for i in range(lo, hi + 1):
            ov = min(b, edges[i + 1]) - max(a, edges[i])
            if ov > 0:
                acc[i] += w * ov / dur
    return acc


def client_series(df):
    """(throughput, goodput) tokens/s, tokens spread over each decode window."""
    if df.empty:
        z = np.zeros(len(EDGES) - 1)
        return z, z
    gen_start = df["rel"] + df["ttft"]        # first token emitted
    gen_end = df["rel"] + df["lat"]           # last token emitted
    thr = _spread(gen_start.values, gen_end.values, df["out"].values)
    g = df["good"].values
    gp = _spread(gen_start.values[g], gen_end.values[g], df["out"].values[g])
    return thr / BIN_S, gp / BIN_S


def server_series(run_dir, t0):
    """Ground-truth output tokens/s from vllm:generation_tokens_total counters."""
    acc = np.zeros(len(EDGES) - 1)
    for f in sorted(glob.glob(os.path.join(run_dir, "server_metrics", "engine_*.jsonl"))):
        ts, cum = [], []
        with open(f) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or "t" not in rec:
                    continue
                k = next((x for x in rec if "generation_tokens_total" in x), None)
                if k is None:
                    continue
                try:
                    ts.append(float(rec["t"]) - t0)
                    cum.append(float(rec[k]))
                except (TypeError, ValueError):
                    continue
        if len(ts) < 2:
            continue
        o = np.argsort(ts)
        ts, cum = np.asarray(ts)[o], np.asarray(cum)[o]
        d = np.diff(cum)
        d[d < 0] = 0.0                       # counter reset
        acc += _spread(ts[:-1], ts[1:], d)   # credit each scrape interval
    return acc / BIN_S


# ---------------------------------------------------------------- figures ---
def fig_over_time(data, out_dir):
    rates = sorted(set(r for arm in data.values() for r in arm))
    arms = list(ARMS)
    fig, axes = plt.subplots(len(arms), len(rates),
                             figsize=(2.15 * len(rates), 2.5 * len(arms)),
                             sharex=True, sharey=True)
    axes = np.atleast_2d(axes)
    ymax = 1.0
    for i, arm in enumerate(arms):
        col = ARM_STYLE[arm]["color"]
        for j, rate in enumerate(rates):
            ax = axes[i, j]
            e = data[arm].get(rate)
            if e is not None:
                thr, gp = client_series(e["df"])
                srv = server_series(e["run_dir"], e["t0"])
                ymax = max(ymax, float(thr.max()), float(srv.max()))
                ax.fill_between(CENTERS_MIN, gp, thr, color=col, alpha=0.15,
                                linewidth=0)
                ax.plot(CENTERS_MIN, srv, color="0.45", ls=":", linewidth=1.0)
                ax.plot(CENTERS_MIN, thr, color=col, ls="--", linewidth=1.0)
                ax.plot(CENTERS_MIN, gp, color=col, ls="-", linewidth=1.5)
            if i == 0:
                ax.set_title(f"{rate:.0f} req/s", pad=3)
            if j == 0:
                ax.set_ylabel(f"{ARM_STYLE[arm]['label']}\noutput tokens/s")
            if i == len(arms) - 1:
                ax.set_xlabel("Time in run (min)")
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            ax.set_axisbelow(True)
    axes[0, 0].set_ylim(0, ymax * 1.05)
    h = [plt.Line2D([], [], color="0.45", ls=":", label="throughput (server counter)"),
         plt.Line2D([], [], color="0.35", ls="--", label="throughput (client, reconstructed)"),
         plt.Line2D([], [], color="0.35", ls="-", label="goodput (SLO-meeting requests)"),
         plt.Rectangle((0, 0), 1, 1, color="0.35", alpha=0.15, label="wasted (gap)")]
    axes[0, 0].legend(handles=h, loc="lower center",
                      bbox_to_anchor=(len(rates) / 2.0, 1.16), ncol=4)
    p = os.path.join(out_dir, "exp17_throughput_vs_goodput_over_time.png")
    fig.tight_layout(rect=(0, 0, 1, 0.93))
    fig.savefig(p, dpi=300); plt.close(fig)
    print("wrote:", p)


def fig_vs_rate(summ, out_dir):
    """Same shape as the fleet-attainment figure, but in absolute tokens/s.

    Goodput curves only — throughput / wasted-gap overlays were dropped
    (they made the 5-arm figure unreadable); the raw numbers stay in the CSV.
    """
    fig, ax = plt.subplots(figsize=(5.4, 3.6))
    for arm, st in ARM_STYLE.items():
        s = summ[summ["arm"] == arm].sort_values("rate_rps")
        if s.empty:
            continue
        ax.plot(s["rate_rps"], s["goodput_tok_per_s"], color=st["color"],
                ls=st["ls"], marker=st["marker"], label=st["label"],
                markeredgecolor="white", markeredgewidth=0.5)
    ax.set_xlabel("Offered rate (req/s)")
    ax.set_ylabel("Goodput output tokens/s")
    ax.set_ylim(bottom=0)
    ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
    ax.set_axisbelow(True)
    ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2)
    p = os.path.join(out_dir, "exp17_goodput_vs_rate.png")
    fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig)
    print("wrote:", p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    print(f"goodput = OUTPUT tokens of SLO-meeting requests (whole-request), "
          f"spread over [start+TTFT, end] | swe E2E<={SLO_RULES['swe']['e2e']:.0f}s")

    data = load(a.results_dir)
    rows = []
    for arm, per_rate in data.items():
        for rate, e in sorted(per_rate.items()):
            df = e["df"]
            good = df["good"].values
            rows.append({
                "arm": arm, "rate_rps": rate, "n": len(df),
                "goodput_out_tok": df.loc[good, "out"].sum(),
                "wasted_out_tok": df.loc[~good, "out"].sum(),
                "last_completed_arrival_s": df["rel"].max(),
                "goodput_tok_per_s": df.loc[good, "out"].sum() / WIN_S,
                "throughput_tok_per_s": df["out"].sum() / WIN_S,
                "server_tok_per_s": float(server_series(e["run_dir"], e["t0"]).mean()),
            })
    summ = pd.DataFrame(rows)
    csv = os.path.join(a.out_dir, "exp17_goodput_tokens.csv")
    summ.to_csv(csv, index=False)
    print("wrote", csv)
    piv = summ.pivot(index="rate_rps", columns="arm",
                     values=["goodput_tok_per_s", "throughput_tok_per_s",
                             "server_tok_per_s"])
    print("\n(client goodput | client throughput | server throughput) tokens/s")
    print(piv.round(0).to_string())

    with plt.rc_context(PAPER_STYLE):
        fig_over_time(data, a.out_dir)
        fig_vs_rate(summ, a.out_dir)


if __name__ == "__main__":
    main()
