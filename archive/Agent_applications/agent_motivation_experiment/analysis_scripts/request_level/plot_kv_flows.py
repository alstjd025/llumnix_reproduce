#!/usr/bin/env python3
"""KV-cache flow analysis over time: creation vs release, per rate condition.

For each condition, one figure `kvflow_rpm_<rpm>.png` with 2 panels:
  A. Fleet ACTIVE KV usage in tokens (Σ engines; usage% × pool) + preemption marks.
  B. Flow rates (tokens/s, fleet Σ, smoothed):
       inflow_new   = Δ(prefix_cache_queries − hits) + Δ(generation_tokens)
                      -> KV newly WRITTEN (prefill misses + decode appends)
       inflow_reuse = Δ(prefix_cache_hits)
                      -> KV re-activated from the prefix cache (no compute)
       outflow      = (inflow_new + inflow_reuse) − d(active KV)/dt
                      -> KV de-activated (request finished / preempted blocks freed)

Accounting notes (this vLLM build exposes no evict/swap counters):
  - kv_cache_usage_perc counts ACTIVE (referenced) blocks only; blocks kept by the
    prefix cache but unreferenced are "free-cached" and invisible here.
  - `outflow` is a conservation residual: release of blocks to the free/cached
    pool. TRUE eviction (cached data destroyed to make room) is not directly
    observable; when usage ~= 100%, allocations necessarily evict — use the
    preemption marks + hit-rate drops as pressure signals.
  - V1 has no swap-out; CPU offload is disabled in this deploy.
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
POOL_TOKENS_PER_ENGINE = 36570 * 16   # num_gpu_blocks x block_size (70B TP2 util0.9)
SMOOTH = 5

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 8, "legend.frameon": False,
         "lines.linewidth": 1.4}


def series(run, port, key):
    p = os.path.join(run, "server_metrics", f"engine_{port}.jsonl")
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


def fleet(run, key):
    """Align per-engine series on shortest tick index and sum."""
    per = [series(run, p, key) for p in ENGINE_PORTS]
    per = [(t, v) for t, v in per if len(v)]
    if not per:
        return np.array([]), np.array([])
    n = min(len(v) for _, v in per)
    return per[0][0][:n], np.sum([v[:n] for _, v in per], axis=0)


def smooth(x):
    if SMOOTH > 1 and len(x) >= SMOOTH:
        return np.convolve(x, np.ones(SMOOTH) / SMOOTH, mode="same")
    return x


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp05_warmup_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp05_kvflow")
    ap.add_argument("--pool-tokens", type=int, default=POOL_TOKENS_PER_ENGINE,
                    help="KV pool tokens per engine (blocks x block_size)")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    dirs = sorted(glob.glob(args.glob), key=lambda x: int(x.split("rpm_")[1]))

    for run in dirs:
        rpm = int(run.split("rpm_")[1])
        t_kv, kv_frac = fleet(run, "vllm:kv_cache_usage_perc")   # sum of fractions
        t_q, q = fleet(run, "vllm:prefix_cache_queries_total")
        t_h, h = fleet(run, "vllm:prefix_cache_hits_total")
        t_g, g = fleet(run, "vllm:generation_tokens_total")
        t_p, pre = fleet(run, "vllm:num_preemptions_total")
        if not len(t_kv) or not len(t_q):
            print(f"rpm{rpm}: missing series, skipped"); continue

        n = min(map(len, (t_kv, q, h, g)))
        t = t_kv[:n]
        active_tok = kv_frac[:n] * args.pool_tokens          # Σ engines (fraction sum x per-engine pool)
        dq, dh, dg = np.diff(q[:n]), np.diff(h[:n]), np.diff(g[:n])
        dt = np.diff(t); dt[dt <= 0] = 1e-9
        dU = np.diff(active_tok)
        inflow_new = np.clip((dq - dh) + dg, 0, None) / dt
        inflow_reuse = np.clip(dh, 0, None) / dt
        outflow = np.clip(inflow_new + inflow_reuse - dU / dt, 0, None)
        tm = t[:-1] + dt / 2

        with plt.rc_context(PAPER):
            fig, ax = plt.subplots(2, 1, figsize=(11, 7), sharex=True)
            a = ax[0]
            a.plot(t, active_tok / 1e3, color="#2ca02c", lw=1.6,
                   label="active KV (fleet, k tokens)")
            a.axhline(4 * args.pool_tokens / 1e3, color="0.4", ls=":",
                      label=f"pool capacity ({4*args.pool_tokens/1e3:.0f}k)")
            if len(pre) and pre.max() > 0:
                dpre = np.diff(pre[:n]) if len(pre) >= n else np.array([])
                marks = tm[dpre > 0] if len(dpre) else []
                for i, x in enumerate(marks):
                    a.axvline(x, color="#d62728", alpha=0.25, lw=1,
                              label="preemption tick" if i == 0 else None)
            a.set_ylabel("active KV (k tokens)")
            a.set_title(f"A. Fleet active KV — offered {rpm/60:.2g}/s")
            a.legend(loc="upper left")

            b = ax[1]
            b.plot(tm, smooth(inflow_new) / 1e3, color="#9467bd", lw=1.5,
                   label="inflow: newly written (prefill-miss + decode)")
            b.plot(tm, smooth(inflow_reuse) / 1e3, color="#1f77b4", lw=1.3,
                   label="inflow: reactivated from prefix cache")
            b.plot(tm, smooth(outflow) / 1e3, color="#ff7f0e", lw=1.5,
                   label="outflow: released (finish/preempt; residual)")
            b.set_ylabel("k tokens/s"); b.set_xlabel("time (s)")
            b.set_title("B. KV flows (fleet Σ, smoothed)")
            b.legend(loc="upper right")
            for p in ax:
                p.grid(axis="y", ls=":", lw=0.5, alpha=0.5)
            fig.tight_layout()
            out = os.path.join(args.out_dir, f"kvflow_rpm_{rpm}.png")
            fig.savefig(out, dpi=140, bbox_inches="tight")
            plt.close(fig)
            print("wrote:", out)


if __name__ == "__main__":
    main()
