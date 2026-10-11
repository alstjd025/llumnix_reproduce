#!/usr/bin/env python3
"""Per-condition layered concurrency view, with gateway vs engine cleanly separated.

For each rate condition, one figure with 4 panels:
  A. Gateway layer (Llumnix control plane), 3 series:
       - gateway_current  = numReqs: ALL in-flight in the gateway
                            (queue + scheduling + being-inferred)
       - gateway_pending  = bufferQueue.Length()+BusyWorkers():
                            queued + in scheduling round-trip (NOT yet at an engine)
       - being_inferred   = current - pending (gateway's view of requests at engines)
  B. Per-engine RUNNING (vllm:num_requests_running) — requests in the model
     execution batch only (prefill+decode combined; NOT the engine queue). 4 engines + Σ.
  C. Per-engine WAITING (vllm:num_requests_waiting) — the engine-side queue
     (admitted to the engine, waiting for a batch slot / KV). 4 engines + Σ.
  D. Prefill vs decode WORK (fleet tokens/s): prompt_tokens/s (prefill) vs
     generation_tokens/s (decode), from the cumulative counters. Instantaneous
     per-request prefill/decode counts are not exposed by vLLM; this token-rate
     split is the available proxy. Preemptions/s overlaid if any.

Note on B vs C: `num_requests_running` is running-only; `num_requests_waiting`
is the queue. They are separate gauges, so queueing and running are NOT summed.
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
SMOOTH = 3

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
    r = np.where(dt > 0, np.diff(v) / dt, 0.0)
    r = np.clip(r, 0, None)
    tm = t[:-1] + dt / 2
    if SMOOTH > 1 and len(r) >= SMOOTH:
        r = np.convolve(r, np.ones(SMOOTH) / SMOOTH, mode="same")
    return tm, r


def _align_sum(series):
    """Sum a list of (t, v) on the shortest common index; return (t, total, per)."""
    series = [(t, v) for t, v in series if len(v)]
    if not series:
        return np.array([]), np.array([]), []
    n = min(len(v) for _, v in series)
    base_t = series[0][0][:n]
    per = [v[:n].astype(float) for _, v in series]
    return base_t, np.sum(per, axis=0), per


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp02b_ratesweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp02b_plots")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    dirs = sorted(glob.glob(args.glob), key=lambda x: int(x.split("rpm_")[1]))
    eng_colors = plt.cm.tab10(np.arange(4))

    written = []
    for run in dirs:
        rpm = int(run.split("rpm_")[1]); reqps = rpm / 60.0
        with plt.rc_context(PAPER):
            fig, ax = plt.subplots(2, 2, figsize=(13, 8))

            # --- A. Gateway layer ---
            a = ax[0, 0]
            ct, cv = gauge(run, "gateway.jsonl", "gateway_current_requests")
            pt, pv = gauge(run, "gateway.jsonl", "gateway_pending_requests")
            a.plot(ct, cv, color="#d62728", lw=1.8, label="gateway_current (all in gateway)")
            a.plot(pt, pv, color="#ff7f0e", lw=1.4, ls="--",
                   label="gateway_pending (queue + scheduling)")
            n = min(len(cv), len(pv))
            if n:
                inf = np.clip(cv[:n] - pv[:n], 0, None)
                a.plot(ct[:n], inf, color="#1f77b4", lw=1.4, ls=":",
                       label="being inferred (current - pending)")
            a.set_title("A. Gateway layer (Llumnix control plane)")
            a.set_ylabel("requests"); a.legend(loc="upper right")

            # --- B. per-engine running ---
            b = ax[0, 1]
            series = []
            for c, p in zip(eng_colors, ENGINE_PORTS):
                t, v = gauge(run, f"engine_{p}.jsonl", "vllm:num_requests_running")
                if len(t):
                    b.plot(t, v, color=c, lw=0.9, alpha=0.8, label=f"eng {p}")
                    series.append((t, v))
            bt, btot, _ = _align_sum(series)
            if len(bt):
                b.plot(bt, btot, "k-", lw=2.0, label="Σ engines running")
            b.set_title("B. Per-engine RUNNING (in execution batch; prefill+decode, no queue)")
            b.set_ylabel("requests"); b.legend(ncol=2)

            # --- C. per-engine waiting ---
            cax = ax[1, 0]
            series = []
            for c, p in zip(eng_colors, ENGINE_PORTS):
                t, v = gauge(run, f"engine_{p}.jsonl", "vllm:num_requests_waiting")
                if len(t):
                    cax.plot(t, v, color=c, lw=0.9, alpha=0.8, label=f"eng {p}")
                    series.append((t, v))
            wt, wtot, _ = _align_sum(series)
            if len(wt):
                cax.plot(wt, wtot, "k-", lw=2.0, label="Σ engines waiting")
            cax.set_title("C. Per-engine WAITING (engine-side queue, separate from running)")
            cax.set_ylabel("requests"); cax.set_xlabel("time (s)"); cax.legend(ncol=2)

            # --- D. prefill vs decode token throughput ---
            d = ax[1, 1]
            pr = [rate(run, f"engine_{p}.jsonl", "vllm:prompt_tokens_total") for p in ENGINE_PORTS]
            gn = [rate(run, f"engine_{p}.jsonl", "vllm:generation_tokens_total") for p in ENGINE_PORTS]
            pt2, ptot, _ = _align_sum(pr)
            gt2, gtot, _ = _align_sum(gn)
            if len(pt2):
                d.plot(pt2, ptot, color="#9467bd", lw=1.6, label="prefill: Σ prompt tok/s")
            if len(gt2):
                d.plot(gt2, gtot, color="#2ca02c", lw=1.6, label="decode: Σ generation tok/s")
            # preemptions/s (if any)
            prt, prv = rate(run, "engine_8000.jsonl", "vllm:num_preemptions_total")
            d.set_title("D. Prefill vs decode WORK (fleet tokens/s)")
            d.set_ylabel("tokens/s"); d.set_xlabel("time (s)"); d.legend(loc="upper right")

            for row in ax:
                for p in row:
                    p.grid(axis="y", ls=":", lw=0.5, alpha=0.5)
            fig.suptitle(f"EXP-02b layered concurrency — offered {reqps:.0f} req/s "
                         f"(gateway vs engine, running vs waiting)", y=1.0)
            fig.tight_layout()
            out = os.path.join(args.out_dir, f"layers_rpm_{rpm}.png")
            fig.savefig(out, dpi=140, bbox_inches="tight")
            plt.close(fig)
            written.append(out)
    print("wrote:")
    for w in written:
        print(" ", w)


if __name__ == "__main__":
    main()
