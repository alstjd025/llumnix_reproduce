#!/usr/bin/env python3
"""Per-engine SLO *signals* vs offered rate.

True per-engine SLO attainment-% is NOT recoverable from our captures:
the client's metrics.csv has no request->engine attribution (it only sees
the gateway), and the server-side engine metrics expose TTFT/ITL only as
sum+count (means), not histogram buckets — so "fraction of requests with
TTFT<=5s" cannot be computed per engine. What IS available per engine is
the **mean TTFT** and **mean ITL** over the analysis window, derived from
the Prometheus counter deltas:

    mean TTFT = Δ(vllm:time_to_first_token_seconds_sum) / Δ(_count)
    mean ITL  = Δ(vllm:inter_token_latency_seconds_sum)  / Δ(_count)

This script plots those two signals per engine (8000-8003) against the
offered rate, with the fleet SLO thresholds (TTFT 5s, meanTBT 50ms) drawn
as reference lines. It answers "at what rate does each engine cross into
SLO violation, and are the four engines balanced?" — the closest honest
proxy for per-engine attainment. Overlapping curves confirm the gateway
load-balances symmetrically.

Analysis window is arrival-anchored [60s, steady_max] into each run's
metrics.csv first arrival, mapped onto the engine time series by wall
clock, matching plot_slo_vs_throughput.py's standard [60,340] window.
"""
import argparse
import glob
import json
import os
import re

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "lines.linewidth": 1.4, "lines.markersize": 4.5,
}
# The fleet's size is a property of the run, not of this file. A literal
# 8000..8003 here drops half of an eight-instance fleet from every panel with
# nothing saying so, so the ports are read off the run's own server_metrics
# directory; $ENGINE_PORTS, then the historical four, are the fallbacks for a
# caller that has no run directory in hand.
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


ENGINE_PORTS = engine_ports_of()
ENGINE_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]


def _first_arrival(run_dir):
    c = pd.read_csv(f"{run_dir}/metrics.csv", low_memory=False)
    c = c[c["agent"] == "request"]
    t = pd.to_numeric(c["start_time"], errors="coerce").dropna()
    return t.min() if len(t) else None


def _series(engine_file, sub):
    """Return (wall_ts[], sum[], count[]) for a TTFT/ITL metric family."""
    rows = [json.loads(l) for l in open(engine_file) if l.strip()]
    if not rows:
        return None
    ks = rows[-1]
    # Metric keys carry a "|engine=..,model_name=.." suffix; match the base.
    def base(k):
        return k.split("|", 1)[0]
    ksum = next((k for k in ks if sub in k and base(k).endswith("_sum")), None)
    kcnt = next((k for k in ks if sub in k and base(k).endswith("_count")), None)
    kts = next((k for k in ("ts", "timestamp", "time", "t") if k in ks), None)
    if not (ksum and kcnt and kts):
        return None
    ts, s, c = [], [], []
    for r in rows:
        if r.get(ksum) is None or r.get(kcnt) is None or r.get(kts) is None:
            continue
        ts.append(float(r[kts])); s.append(float(r[ksum])); c.append(float(r[kcnt]))
    return np.array(ts), np.array(s), np.array(c)


def _window_mean(triple, t0, t1):
    """Mean = Δsum/Δcount between the first sample >=t0 and last <=t1."""
    if triple is None:
        return np.nan
    ts, s, c = triple
    m = (ts >= t0) & (ts <= t1)
    if m.sum() < 2:
        return np.nan
    s_w, c_w = s[m], c[m]
    dc = c_w[-1] - c_w[0]
    return (s_w[-1] - s_w[0]) / dc if dc > 0 else np.nan


def collect(run_dirs, rate_key, rate_div, win_lo, win_hi):
    out = []  # (rate, {port: (ttft_s, itl_ms)})
    for d in run_dirs:
        m = re.search(rf"{rate_key}(\d+)", os.path.basename(d))
        if not m:
            continue
        rate = int(m.group(1)) / rate_div
        fa = _first_arrival(d)
        if fa is None:
            continue
        t0, t1 = fa + win_lo, fa + win_hi
        per = {}
        for port in ENGINE_PORTS:
            f = f"{d}/server_metrics/engine_{port}.jsonl"
            if not os.path.isfile(f):
                per[port] = (np.nan, np.nan); continue
            ttft = _window_mean(_series(f, "time_to_first_token_seconds"), t0, t1)
            itl = _window_mean(_series(f, "inter_token_latency_seconds"), t0, t1)
            per[port] = (ttft, itl * 1000 if itl == itl else np.nan)
        out.append((rate, per))
    out.sort(key=lambda x: x[0])
    return out


def plot(data, out_path, rate_unit):
    rates = [r for r, _ in data]
    with plt.rc_context(PAPER_STYLE):
        fig, (axT, axI) = plt.subplots(1, 2, figsize=(9, 3.6))
        for port, col in zip(ENGINE_PORTS, ENGINE_COLORS):
            ttft = [per[port][0] for _, per in data]
            itl = [per[port][1] for _, per in data]
            axT.plot(rates, ttft, "-o", color=col, label=f"engine {port}",
                     mec="white", mew=0.5)
            axI.plot(rates, itl, "-o", color=col, label=f"engine {port}",
                     mec="white", mew=0.5)
        axT.axhline(5.0, ls="--", lw=1.0, color="0.4")
        axT.text(rates[0], 5.0, " SLO 5s", va="bottom", ha="left", color="0.4", fontsize=7)
        axI.axhline(50.0, ls="--", lw=1.0, color="0.4")
        axI.text(rates[0], 50.0, " SLO 50ms", va="bottom", ha="left", color="0.4", fontsize=7)
        for ax, ttl, ylab in [(axT, "Mean TTFT per engine", "mean TTFT (s)"),
                              (axI, "Mean ITL per engine", "mean ITL (ms)")]:
            ax.set_title(ttl); ax.set_xlabel(f"Offered rate ({rate_unit})")
            ax.set_ylabel(ylab)
            ax.set_yscale("log")
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        axT.legend(loc="upper left")
        fig.suptitle("Per-engine SLO signals (proxy for per-engine attainment; "
                     "true attainment-% not recoverable — see script header)",
                     fontsize=8)
        fig.tight_layout(rect=[0, 0, 1, 0.96])
        fig.savefig(out_path, dpi=300)
        print("wrote:", out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glob", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--rate-key", default="rpm_")
    ap.add_argument("--rate-div", type=float, default=60.0)
    ap.add_argument("--rate-unit", default="req/s")
    ap.add_argument("--win-lo", type=float, default=60.0)
    ap.add_argument("--win-hi", type=float, default=340.0)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    run_dirs = sorted(glob.glob(a.glob))
    data = collect(run_dirs, a.rate_key, a.rate_div, a.win_lo, a.win_hi)
    if not data:
        raise SystemExit("no runs matched")
    print(f"{'rate':>6} | " + " | ".join(f"e{p} TTFT/ITL" for p in ENGINE_PORTS))
    for rate, per in data:
        cells = " | ".join(f"{per[p][0]:.2f}s/{per[p][1]:.0f}ms" for p in ENGINE_PORTS)
        print(f"{rate:6.0f} | {cells}")
    plot(data, os.path.join(a.out_dir, "per_engine_slo_signals.png"), a.rate_unit)


if __name__ == "__main__":
    main()
