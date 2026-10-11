#!/usr/bin/env python3
"""Why is <100% KV the SLO-optimum? — decompose what actually drives TBT.

Pools 20-s steady-state windows from many runs and, for each window, computes
  running   fleet Σ vllm:num_requests_running          (decode batch width)
  prefill   fleet prefill-compute rate, Δ(queries−hits)/Δt  [tok/s]
  kv        fleet hot KV usage (%)
  tbt_p50   median per-request meanTBT of requests ENDING in the window
then plots TBT against the two candidate drivers, separately for the chat
workload (sharegpt; exp07+exp08 runs) and the SWE chain workload (exp06).

Reading: if TBT is a function of decode batch width, chat/SWE windows fall on
one curve in the running-axis plot. If prefill interference (chunked-prefill
step-time stealing) dominates, SWE departs in the running plot but aligns in
the prefill plot. The KV level where the 50 ms SLO binds is then
   KV* = N*(50ms) x avg-tokens-per-running-request / pool
which is why the SLO-optimal occupancy sits far below 100% for short-context
chat, and lower still for prefill-heavy SWE.

Outputs: <out>/tbt_driver_windows.csv, tbt_vs_running.png,
         tbt_vs_prefill.png, kv_tokens_per_request.png
"""

import argparse
import glob
import json
import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

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
POOL_TOTAL = 36570 * 16 * 4        # fleet hot-KV pool, tokens (70B TP2 x4)
WIN_S = 20.0
STEADY_LO, DRAIN_S = 60.0, 20.0
TBT_SLO_MS = 50.0
THRASH_TOKPS = 2e6                 # prefix-counter re-query noise cutoff

CHAT_COLOR, SWE_COLOR = "#1f77b4", "#d62728"


def fleet_series(run, key):
    per = []
    for p in ENGINE_PORTS:
        f = os.path.join(run, "server_metrics", f"engine_{p}.jsonl")
        if not os.path.isfile(f):
            continue
        ts, vs, t0 = [], [], None
        for line in open(f):
            rec = json.loads(line)
            if not rec.get("ok"):
                continue
            v = [x for k, x in rec.items()
                 if k.split("|")[0] == key and isinstance(x, (int, float))]
            if not v:
                continue
            if t0 is None:
                t0 = rec["t"]
            ts.append(rec["t"] - t0)
            vs.append(sum(v))
        if ts:
            per.append((np.array(ts), np.array(vs)))
    if not per:
        return np.array([]), np.array([])
    n = min(len(v) for _, v in per)
    return per[0][0][:n], np.sum([v[:n] for _, v in per], axis=0)


def windows_for_run(run, workload):
    t_r, running = fleet_series(run, "vllm:num_requests_running")
    t_k, kv = fleet_series(run, "vllm:kv_cache_usage_perc")
    _, q = fleet_series(run, "vllm:prefix_cache_queries_total")
    _, h = fleet_series(run, "vllm:prefix_cache_hits_total")
    if not len(t_r) or not len(q):
        return []

    df = pd.read_csv(os.path.join(run, "metrics.csv"), low_memory=False)
    r = df[df.agent != "job_summary"].copy()
    bl = lambda c: r[c].fillna(False).astype(bool) if c in r.columns else pd.Series(False, index=r.index)
    r = r[~(bl("is_error") | bl("is_timeout") | bl("is_server_terminated") | bl("is_rejected"))]
    t0 = r["start_time"].min()
    r["rel_end"] = pd.to_numeric(r["end_time"], errors="coerce") - t0
    r["tbt"] = pd.to_numeric(r["tbt_mean_ms"], errors="coerce")
    r = r[r["tbt"].notna()]
    dur = r["rel_end"].max()

    out = []
    lo = STEADY_LO
    while lo + WIN_S <= dur - DRAIN_S:
        hi = lo + WIN_S
        m_r = (t_r >= lo) & (t_r < hi)
        m_k = (t_k >= lo) & (t_k < hi)
        if m_r.sum() >= 5:
            # prefill compute rate over the window from counter deltas
            iq = np.searchsorted(t_r, [lo, hi])
            i0, i1 = max(iq[0], 1), min(iq[1], len(q) - 1)
            pre = np.nan
            if i1 > i0:
                dtt = t_r[i1] - t_r[i0]
                if dtt > 0:
                    pre = ((q[i1] - q[i0]) - (h[i1] - h[i0])) / dtt
            # thrash-polluted counters invalidate the prefill reading only;
            # the window still carries a valid (running, tbt) sample
            if np.isnan(pre) or pre < 0 or pre > THRASH_TOKPS:
                pre = np.nan
            w = r[(r["rel_end"] >= lo) & (r["rel_end"] < hi)]
            if len(w) >= 5:
                out.append(dict(
                    workload=workload, run=os.path.basename(run), t=lo,
                    running=float(running[m_r].mean()),
                    kv_pct=float(kv[m_k].mean() / 4 * 100) if m_k.sum() else np.nan,
                    prefill_tokps=float(pre),
                    tbt_p50=float(w["tbt"].median()),
                    n_req=len(w)))
        lo += WIN_S
    return out


def binned(x, y, nbins=18):
    edges = np.quantile(x, np.linspace(0, 1, nbins + 1))
    xs, ys = [], []
    for i in range(nbins):
        m = (x >= edges[i]) & (x < edges[i + 1])
        if m.sum() >= 8:
            xs.append(np.median(x[m])); ys.append(np.median(y[m]))
    return np.array(xs), np.array(ys)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--chat-glob", default="results/*exp0[78]_kvadm*rpm_*")
    ap.add_argument("--swe-glob", default="results/*exp06_swe_sweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/why_not_full_kv")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    rows = []
    for d in sorted(glob.glob(args.chat_glob)):
        if re.search(r"rpm_(300|990)$", d) and "th0990" in d or d.endswith("rpm_300"):
            continue                       # skip smoke runs
        rows += windows_for_run(d, "chat")
    for d in sorted(glob.glob(args.swe_glob)):
        rows += windows_for_run(d, "swe")
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(args.out_dir, "tbt_driver_windows.csv"), index=False)
    print(f"windows: chat={len(t[t.workload=='chat'])} swe={len(t[t.workload=='swe'])}")

    PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
             "legend.fontsize": 8, "legend.frameon": False, "lines.linewidth": 1.6}
    specs = [("running", "fleet running requests (decode batch width)", "tbt_vs_running.png"),
             ("prefill_tokps", "fleet prefill compute (tok/s)", "tbt_vs_prefill.png")]
    for col, xlabel, fname in specs:
        with plt.rc_context(PAPER):
            fig, ax = plt.subplots(figsize=(7.4, 4.8))
            for wl, c in (("chat", CHAT_COLOR), ("swe", SWE_COLOR)):
                g = t[(t.workload == wl) & t[col].notna()]
                ax.scatter(g[col], g["tbt_p50"], s=7, alpha=0.25, color=c)
                bx, by = binned(g[col].to_numpy(), g["tbt_p50"].to_numpy())
                ax.plot(bx, by, "-o", color=c, ms=4, label=f"{wl} (binned median)")
                over = np.where(by > TBT_SLO_MS)[0]
                cross = np.nan
                if len(over) and over[0] > 0:
                    i = over[0]
                    cross = bx[i - 1] + (bx[i] - bx[i - 1]) * (TBT_SLO_MS - by[i - 1]) / (by[i] - by[i - 1])
                print(f"{fname} {wl}: TBT=50ms crossing at {col} ~ {cross:,.0f}")
            ax.axhline(TBT_SLO_MS, color="0.4", ls=":", label="TBT SLO 50 ms")
            ax.set_xlabel(xlabel); ax.set_ylabel("windowed TBT p50 (ms)")
            ax.set_ylim(0, 130); ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
            ax.legend()
            ax.set_title("What drives TBT? — 20 s steady windows, chat vs SWE")
            fig.tight_layout()
            out = os.path.join(args.out_dir, fname)
            fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
            print("wrote:", out)

    # THE unifying view: TBT vs hot-KV tokens. On the running axis chat/SWE
    # differ ~7.5x; on this axis they collapse onto one curve -> decode step
    # time is bandwidth-bound on total KV read per step, i.e. the tank LEVEL
    # itself is (quasi-)causal for TBT.
    t["kv_mtok"] = t["kv_pct"] / 100 * POOL_TOTAL / 1e6
    fitset = t[(t["kv_pct"] < 88) & t["kv_mtok"].notna() & t["tbt_p50"].notna()]
    a, b = np.polyfit(fitset["kv_mtok"], fitset["tbt_p50"], 1)
    kv_budget = (TBT_SLO_MS - b) / a
    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(figsize=(7.4, 4.8))
        for wl, c in (("chat", CHAT_COLOR), ("swe", SWE_COLOR)):
            g = t[t.workload == wl]
            ax.scatter(g["kv_mtok"], g["tbt_p50"], s=7, alpha=0.25, color=c)
            bx, by = binned(g["kv_mtok"].to_numpy(), g["tbt_p50"].to_numpy())
            ax.plot(bx, by, "-o", color=c, ms=4, label=f"{wl} (binned median)")
        xs = np.linspace(0, POOL_TOTAL / 1e6, 50)
        ax.plot(xs, a * xs + b, color="0.5", ls="--", lw=1,
                label=f"fit: TBT ≈ {b:.0f} + {a:.1f}·KV[Mtok] ms")
        ax.axhline(TBT_SLO_MS, color="0.4", ls=":", label="TBT SLO 50 ms")
        ax.axvline(kv_budget, color="#9467bd", ls=":",
                   label=f"KV budget @50ms ≈ {kv_budget:.2f} Mtok ({100*kv_budget*1e6/POOL_TOTAL:.0f}%)")
        sec = ax.secondary_xaxis("top", functions=(
            lambda v: v * 1e6 / POOL_TOTAL * 100, lambda v: v / 100 * POOL_TOTAL / 1e6))
        sec.set_xlabel("hot KV usage (% of pool)")
        ax.set_xlabel("fleet hot KV (M tokens)")
        ax.set_ylabel("windowed TBT p50 (ms)")
        ax.set_ylim(0, 130); ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
        ax.legend(fontsize=7.5)
        ax.set_title("Decode step time is set by TOTAL hot KV read per step\n"
                     "(chat and SWE collapse onto one curve on this axis)")
        fig.tight_layout()
        out = os.path.join(args.out_dir, "tbt_vs_kv_tokens.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)
        print(f"fit: TBT = {b:.1f} + {a:.2f} * KV_Mtok; 50ms budget = {kv_budget:.2f} Mtok "
              f"= {100*kv_budget*1e6/POOL_TOTAL:.0f}% of pool")

    # tokens held per running request (why the same batch cap ≠ same KV level)
    t["tok_per_req"] = t["kv_pct"] / 100 * POOL_TOTAL / t["running"]
    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(figsize=(6.4, 4.2))
        for wl, c in (("chat", CHAT_COLOR), ("swe", SWE_COLOR)):
            g = t[(t.workload == wl) & (t.running > 20)]
            ax.scatter(g["running"], g["tok_per_req"], s=7, alpha=0.3, color=c, label=wl)
            print(f"tok/req {wl}: median {g['tok_per_req'].median():,.0f}")
        ax.set_xlabel("fleet running requests"); ax.set_ylabel("hot KV tokens per running request")
        ax.set_yscale("log"); ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6); ax.legend()
        ax.set_title("Per-request KV footprint (hot tokens / running)")
        fig.tight_layout()
        out = os.path.join(args.out_dir, "kv_tokens_per_request.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)


if __name__ == "__main__":
    main()
