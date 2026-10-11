#!/usr/bin/env python3
"""Plot a Llumnix rate-sweep: per-condition time series + cross-rate throughput.

Inputs: the per-condition run dirs (each has metrics.csv + server_metrics/*.jsonl).
Outputs (PNG, 300 dpi):
  timeseries.png   4 panels (throughput / KV% / queue / migration), each overlaying
                   all rate conditions on a common time axis (color = req/s).
  throughput_vs_rate.png   x = offered req/s, y = avg completed throughput
                   (steady-state window), with the offered=achieved diagonal and
                   the completion-rate on a twin axis.

Definitions (see EXP-02):
  - "completed throughput" = successful requests / sec. Success = metrics.csv
    success==True (full response, no error/timeout/termination). This is a
    COMPLETION rate, not a latency-SLO goodput.
  - KV% = max over the 4 engines of vllm:kv_cache_usage_perc (GPU KV occupancy).
  - queue = gateway_pending + Σ engine num_requests_waiting.
  - migration = scheduler_rescheduling_total (rescheduling decisions).
Steady-state window: [WARMUP_S, dur - DRAIN_S] to drop fill-up and end-drain.
"""

import argparse
import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

WARMUP_S = 60.0
DRAIN_S = 20.0
BIN_S = 10.0

PAPER_STYLE = {
    "font.family": "serif", "font.size": 9, "axes.labelsize": 10,
    "axes.titlesize": 10, "axes.linewidth": 0.8, "legend.fontsize": 8,
    "legend.frameon": False, "xtick.direction": "in", "ytick.direction": "in",
    "lines.linewidth": 1.5, "lines.markersize": 5,
}


# The fleet's size is a property of the run, not of this file. A literal
# 8000..8003 drops half of an eight-instance fleet from every panel with nothing
# saying so, so the ports come from the run's own server_metrics directory;
# $ENGINE_PORTS, then the historical four, are the fallbacks.
def engine_ports_of(run=None):
    if run:
        found = sorted(
            int(os.path.basename(f)[len("engine_"):-len(".jsonl")])
            for f in glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl")))
        if found:
            return tuple(found)
    raw = os.environ.get("ENGINE_PORTS", "").strip()
    if raw:
        return tuple(int(p) for p in raw.split(",") if p.strip())
    return (8000, 8001, 8002, 8003)


def _series(run_dir, fname, key, agg="sum"):
    """Return (t_rel[], value[]) for a metric key across a server_metrics file.

    agg controls how multiple labelled series in one tick are combined.
    """
    p = os.path.join(run_dir, "server_metrics", fname)
    ts, vs = [], []
    if not os.path.isfile(p):
        return np.array([]), np.array([])
    t0 = None
    for line in open(p):
        rec = json.loads(line)
        t = rec.get("t")
        if t0 is None:
            t0 = t
        vals = [v for k, v in rec.items() if key in k and isinstance(v, (int, float))]
        if not vals:
            continue
        ts.append(t - t0)
        vs.append(sum(vals) if agg == "sum" else max(vals))
    return np.array(ts), np.array(vs)


def _engine_agg(run_dir, key, agg):
    """Aggregate a vllm:* key across the engine files, aligned by tick index."""
    per = []
    for port in engine_ports_of(run_dir):
        t, v = _series(run_dir, f"engine_{port}.jsonl", key, agg="max")
        per.append((t, v))
    base_t = max((p[0] for p in per if len(p[0])), key=len, default=np.array([]))
    if not len(base_t):
        return np.array([]), np.array([])
    n = min(len(v) for _, v in per if len(v))
    stack = np.vstack([v[:n] for _, v in per if len(v)])
    combined = stack.sum(axis=0) if agg == "sum" else stack.max(axis=0)
    return base_t[:n], combined


def load_condition(run_dir):
    df = pd.read_csv(os.path.join(run_dir, "metrics.csv"))
    req = df[df.agent == "request"].copy()
    t0 = req["start_time"].min()
    ok = req[req["success"].astype(bool)].copy()
    ok["rel_end"] = ok["end_time"] - t0
    dur = (req["end_time"].max() - t0)
    rpm = int(run_dir.split("rpm_")[1])
    reqps = rpm / 60.0

    # throughput(t): completed successful req/s in BIN_S bins
    bins = np.arange(0, dur + BIN_S, BIN_S)
    tput = ok.groupby(pd.cut(ok["rel_end"], bins), observed=False).size() / BIN_S
    tput_v = tput.values
    tput_t = (bins[:-1] + BIN_S / 2)[:len(tput_v)]
    n = min(len(tput_t), len(tput_v))
    tput_t, tput_v = tput_t[:n], tput_v[:n]

    # steady-state completion throughput
    win = ok[(ok["rel_end"] >= WARMUP_S) & (ok["rel_end"] <= dur - DRAIN_S)]
    ss_span = max(1e-9, (dur - DRAIN_S) - WARMUP_S)
    ss_tput = len(win) / ss_span
    completion = len(ok) / max(1, len(req))

    kv_t, kv_v = _engine_agg(run_dir, "kv_cache_usage_perc", "max")
    ewait_t, ewait_v = _engine_agg(run_dir, "num_requests_waiting", "sum")
    gwp_t, gwp_v = _series(run_dir, "gateway.jsonl", "gateway_pending_requests", "sum")
    resched_t, resched_v = _series(run_dir, "scheduler.jsonl", "scheduler_rescheduling_total", "sum")

    # queue(t) = gateway_pending + engine waiting (align by nearest index)
    q_t, q_v = gwp_t, gwp_v.copy() if len(gwp_v) else np.array([])
    if len(q_v) and len(ewait_v):
        n = min(len(q_v), len(ewait_v))
        q_t, q_v = q_t[:n], q_v[:n] + ewait_v[:n]

    return dict(rpm=rpm, reqps=reqps, dur=dur, ss_tput=ss_tput, completion=completion,
                tput_t=tput_t, tput_v=tput_v, kv_t=kv_t, kv_v=kv_v * 100.0,
                q_t=q_t, q_v=q_v, resched_t=resched_t, resched_v=resched_v)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp02_ratesweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp02_plots")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    dirs = sorted(glob.glob(args.glob), key=lambda x: int(x.split("rpm_")[1]))
    conds = [load_condition(d) for d in dirs]
    cmap = plt.cm.viridis(np.linspace(0, 0.9, len(conds)))

    with plt.rc_context(PAPER_STYLE):
        # ---- Figure 1: per-condition time series (4 panels) ----
        fig, axes = plt.subplots(2, 2, figsize=(11, 7))
        panels = [
            ("tput_t", "tput_v", "Completed throughput (req/s)", axes[0, 0]),
            ("kv_t", "kv_v", "GPU KV cache usage (%)", axes[0, 1]),
            ("q_t", "q_v", "Queue (gateway_pending + engine waiting)", axes[1, 0]),
            ("resched_t", "resched_v", "Migration (scheduler_rescheduling_total)", axes[1, 1]),
        ]
        for tk, vk, title, ax in panels:
            for c, col in zip(conds, cmap):
                if len(c[tk]) and len(c[vk]):
                    ax.plot(c[tk], c[vk], color=col, label=f"{c['reqps']:.0f}")
            ax.axvspan(0, WARMUP_S, color="0.9", zorder=0)  # warmup shading
            ax.set_title(title)
            ax.set_xlabel("time (s)")
            ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
        axes[0, 0].legend(title="offered req/s", ncol=2, loc="upper right")
        fig.suptitle("EXP-02 rate-sweep — per-condition time series "
                     "(shaded = warmup; each condition cold-restarted)", y=1.0)
        fig.tight_layout()
        f1 = os.path.join(args.out_dir, "timeseries.png")
        fig.savefig(f1, dpi=300, bbox_inches="tight")
        plt.close(fig)

        # ---- Figure 2: cross-rate avg throughput ----
        x = [c["reqps"] for c in conds]
        y = [c["ss_tput"] for c in conds]
        comp = [c["completion"] * 100 for c in conds]
        fig, ax = plt.subplots(figsize=(6, 4.2))
        ax.plot(x, y, "o-", color="#1f77b4", label="Completed throughput (steady-state)")
        ax.plot(x, x, "--", color="0.6", lw=1.0, label="Offered = achieved (ideal)")
        ax.set_xlabel("Offered rate (req/s)")
        ax.set_ylabel("Completed throughput (req/s)")
        ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
        ax2 = ax.twinx()
        ax2.plot(x, comp, "s:", color="#d62728", label="Completion rate (%)")
        ax2.set_ylabel("Completion rate (%)", color="#d62728")
        ax2.set_ylim(0, 105)
        ax2.tick_params(axis="y", colors="#d62728")
        lines = ax.get_lines() + ax2.get_lines()
        ax.legend(lines, [ln.get_label() for ln in lines], loc="upper left")
        ax.set_title("EXP-02 — throughput & completion vs offered rate")
        fig.tight_layout()
        f2 = os.path.join(args.out_dir, "throughput_vs_rate.png")
        fig.savefig(f2, dpi=300, bbox_inches="tight")
        plt.close(fig)

    print("wrote:")
    print(" ", f1)
    print(" ", f2)
    print("\nsteady-state (window [%.0fs, dur-%.0fs]):" % (WARMUP_S, DRAIN_S))
    for c in conds:
        print(f"  {c['reqps']:5.0f} req/s: ss_tput={c['ss_tput']:.1f}/s  "
              f"completion={c['completion']*100:.0f}%")


if __name__ == "__main__":
    main()
