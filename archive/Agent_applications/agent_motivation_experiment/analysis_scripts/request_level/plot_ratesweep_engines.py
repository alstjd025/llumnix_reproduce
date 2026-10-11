#!/usr/bin/env python3
"""Detailed per-condition time series: per-engine + total, engine-layer + Llumnix-layer.

For each rate condition, one figure with 4 panels:
  A. Concurrency: per-engine running (4 thin) + engine total (thick, engine-layer)
     vs gateway_current + gateway_pending (Llumnix-layer).
  B. Output-token throughput: per-engine gen-tok/s (4 thin) + engine total (thick,
     engine-layer) vs Llumnix request_output_tokens/s (Llumnix-layer).
  C. GPU KV cache usage % per engine.
  D. Rates: arrival (scheduler_scheduling_total/s) vs completion
     (gateway request_total{200}/s) vs the offered rate line.

Cumulative Prometheus counters (generation_tokens_total, request_output_tokens_total,
scheduling_total, request_total) are differenced to per-second rates. The engine
counters reset to 0 at each condition's cold restart, so within a condition they are
clean monotonic series.
"""

import argparse
import glob
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

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
SMOOTH = 3  # rate smoothing window (ticks)

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 10, "legend.fontsize": 7, "legend.frameon": False,
         "lines.linewidth": 1.3}


def gauge(run, fname, key):
    """(t_rel, value) for an instantaneous gauge; label-sets summed per tick."""
    p = os.path.join(run, "server_metrics", fname)
    ts, vs, t0 = [], [], None
    if not os.path.isfile(p):
        return np.array([]), np.array([])
    for line in open(p):
        rec = json.loads(line)
        if not rec.get("ok"):
            continue
        vals = [v for k, v in rec.items()
                if k.split("|")[0] == key and isinstance(v, (int, float))]
        if not vals:
            continue
        if t0 is None:
            t0 = rec["t"]
        ts.append(rec["t"] - t0)
        vs.append(sum(vals))
    return np.array(ts), np.array(vs)


def rate(run, fname, key):
    """(t_mid, per-second rate) from a cumulative counter, smoothed."""
    t, v = gauge(run, fname, key)
    if len(t) < 2:
        return np.array([]), np.array([])
    dt = np.diff(t)
    dv = np.diff(v)
    r = np.where(dt > 0, dv / dt, 0.0)
    r = np.clip(r, 0, None)  # ignore counter resets (negative)
    tm = t[:-1] + dt / 2
    if SMOOTH > 1 and len(r) >= SMOOTH:
        r = np.convolve(r, np.ones(SMOOTH) / SMOOTH, mode="same")
    return tm, r


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp02_ratesweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp02_plots")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    dirs = sorted(glob.glob(args.glob), key=lambda x: int(x.split("rpm_")[1]))
    eng_colors = plt.cm.tab10(np.arange(4))

    written = []
    for run in dirs:
        rpm = int(run.split("rpm_")[1])
        reqps = rpm / 60.0
        with plt.rc_context(PAPER):
            fig, ax = plt.subplots(2, 2, figsize=(12, 7.5))

            # --- A. concurrency ---
            a = ax[0, 0]
            tot_t = tot = None
            for c, p in zip(eng_colors, ENGINE_PORTS):
                t, v = gauge(run, f"engine_{p}.jsonl", "vllm:num_requests_running")
                if len(t):
                    a.plot(t, v, color=c, lw=0.8, alpha=0.7, label=f"eng {p} running")
                    n = len(v)
                    if tot is None:
                        tot_t, tot = t[:n], v[:n].astype(float)
                    else:
                        m = min(len(tot), n)
                        tot_t, tot = tot_t[:m], tot[:m] + v[:m]
            if tot is not None:
                a.plot(tot_t, tot, "k-", lw=2.0, label="engine total running")
            gt, gv = gauge(run, "gateway.jsonl", "gateway_current_requests")
            a.plot(gt, gv, color="#d62728", lw=1.6, label="gateway_current (Llumnix)")
            gpt, gpv = gauge(run, "gateway.jsonl", "gateway_pending_requests")
            a.plot(gpt, gpv, color="#ff7f0e", lw=1.2, ls="--", label="gateway_pending (Llumnix)")
            a.set_title(f"A. Concurrency — requests in system (engine vs gateway)")
            a.set_ylabel("requests"); a.legend(ncol=2, fontsize=6)

            # --- B. output token throughput ---
            b = ax[0, 1]
            tt = ttot = None
            for c, p in zip(eng_colors, ENGINE_PORTS):
                t, v = rate(run, f"engine_{p}.jsonl", "vllm:generation_tokens_total")
                if len(t):
                    b.plot(t, v, color=c, lw=0.8, alpha=0.7, label=f"eng {p}")
                    n = len(v)
                    if ttot is None:
                        tt, ttot = t[:n], v[:n].astype(float)
                    else:
                        m = min(len(ttot), n); tt, ttot = tt[:m], ttot[:m] + v[:m]
            if ttot is not None:
                b.plot(tt, ttot, "k-", lw=2.0, label="engine total gen tok/s")
            lt, lv = rate(run, "gateway.jsonl", "request_output_tokens_total")
            b.plot(lt, lv, color="#1f77b4", lw=1.6, label="Llumnix output tok/s")
            b.set_title("B. Output-token throughput (engine-gen vs Llumnix-delivered)")
            b.set_ylabel("tokens/s"); b.legend(ncol=2, fontsize=6)

            # --- C. KV per engine ---
            cax = ax[1, 0]
            for c, p in zip(eng_colors, ENGINE_PORTS):
                t, v = gauge(run, f"engine_{p}.jsonl", "vllm:kv_cache_usage_perc")
                if len(t):
                    cax.plot(t, v * 100, color=c, label=f"eng {p}")
            cax.set_title("C. GPU KV cache usage (per engine)")
            cax.set_ylabel("KV usage (%)"); cax.set_xlabel("time (s)"); cax.legend(fontsize=7)

            # --- D. arrival vs completion rate ---
            d = ax[1, 1]
            at, av = rate(run, "scheduler.jsonl", "scheduler_scheduling_total")
            d.plot(at, av, color="#2ca02c", label="arrival (scheduler sched/s)")
            ct, cv = rate(run, "gateway.jsonl", "request_total")
            d.plot(ct, cv, color="#9467bd", label="gateway request_total/s")
            d.axhline(reqps, color="0.5", ls=":", label=f"offered = {reqps:.0f}/s")
            d.set_title("D. Arrival vs completion rate")
            d.set_ylabel("req/s"); d.set_xlabel("time (s)"); d.legend(fontsize=7)

            for row in ax:
                for p in row:
                    p.grid(axis="y", ls=":", lw=0.5, alpha=0.5)
            fig.suptitle(f"EXP-02 detail — offered {reqps:.0f} req/s "
                         f"(per-engine + total; engine-layer + Llumnix-layer)", y=1.0)
            fig.tight_layout()
            out = os.path.join(args.out_dir, f"detail_rpm_{rpm}.png")
            fig.savefig(out, dpi=150, bbox_inches="tight")
            plt.close(fig)
            written.append(out)
    print("wrote:")
    for w in written:
        print(" ", w)


if __name__ == "__main__":
    main()
