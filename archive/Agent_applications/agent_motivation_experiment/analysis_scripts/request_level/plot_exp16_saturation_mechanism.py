#!/usr/bin/env python3
"""EXP-16: what happens at the saturation knee — from vLLM's OWN counters.

Uses the scraped Prometheus counters (server_metrics/engine_*.jsonl), which are
independent of the per-step schedule() instrumentation and free of async
attribution issues:
  vllm:kv_cache_usage_perc          — real KV occupancy
  vllm:prefix_cache_hits/queries    — prefix-cache hit RATE
  vllm:num_preemptions_total        — decode-request preemptions
  vllm:request_prefill_time_seconds — vLLM's own mean prefill time / request

Window-differences the counters over the arrival-aligned window and plots them
vs offered rate to show that at the knee (~KV 55-80%) the cache-hit rate drops
and preemptions switch on together — the two saturation mechanisms.
"""
import glob
import json
import os
import re

import numpy as np
import matplotlib.pyplot as plt

PAPER_STYLE = {
    "font.family": "serif", "font.serif": ["DejaVu Serif", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 7.5, "legend.frameon": False,
    "xtick.direction": "in", "ytick.direction": "in", "lines.markersize": 4.5,
}


def keyfind(row, sub):
    for k in row:
        if k.startswith("vllm:") and sub in k:
            return k
    return None


def series(rows, sub):
    k = next((keyfind(r, sub) for r in rows if keyfind(r, sub)), None)
    if not k:
        return None
    return [(r["t"], r[k]) for r in rows if r.get(k) is not None]


def wdelta(rows, sub, warm=60, tail=30):
    s = series(rows, sub)
    if not s or len(s) < 2:
        return None
    t0, t1 = s[0][0], s[-1][0]
    w = [v for t, v in s if t0 + warm <= t <= t1 - tail]
    return (w[-1] - w[0]) if len(w) >= 2 else None


def wmean(rows, sub, warm=60, tail=30):
    s = series(rows, sub)
    if not s:
        return None
    t0, t1 = s[0][0], s[-1][0]
    w = [v for t, v in s if t0 + warm <= t <= t1 - tail]
    return float(np.mean(w)) if w else None


def collect(results_dir):
    out = {}
    for d in sorted(glob.glob(f"{results_dir}/*exp16_instr_rpm_*"),
                    key=lambda x: int(re.search(r"rpm_(\d+)", x).group(1))):
        rps = int(re.search(r"rpm_(\d+)", os.path.basename(d)).group(1)) // 60
        dh = dq = dpre = pf_s = pf_c = 0.0
        kv = []
        span = 210
        for f in sorted(glob.glob(os.path.join(d, "server_metrics",
                                               "engine_80*.jsonl"))):
            rows = [json.loads(l) for l in open(f) if l.strip()]
            if len(rows) < 3:
                continue
            s = series(rows, "prefix_cache_hits_total")
            if s:
                span = s[-1][0] - s[0][0]
            dh += wdelta(rows, "prefix_cache_hits_total") or 0
            dq += wdelta(rows, "prefix_cache_queries_total") or 0
            dpre += wdelta(rows, "num_preemptions_total") or 0
            pf_s += wdelta(rows, "request_prefill_time_seconds_sum") or 0
            pf_c += wdelta(rows, "request_prefill_time_seconds_count") or 0
            m = wmean(rows, "kv_cache_usage_perc")
            if m is not None:
                kv.append(m)
        out[rps] = {
            "kv": 100 * np.mean(kv) if kv else np.nan,
            "hit": 100 * dh / dq if dq else np.nan,
            "preempt": dpre / max(span - 90, 1),
            "prefill_ms": 1000 * pf_s / pf_c if pf_c else np.nan,
        }
    return out


def main():
    out_dir = "figs/exp16_sweep"
    os.makedirs(out_dir, exist_ok=True)
    m = collect("results")
    rates = sorted(m)
    kv = [m[r]["kv"] for r in rates]
    hit = [m[r]["hit"] for r in rates]
    pre = [m[r]["preempt"] for r in rates]
    pf = [m[r]["prefill_ms"] for r in rates]

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
        # panel 1: KV% + cache hit% (both %, same axis) + preemptions (twin)
        ax[0].plot(rates, kv, "o-", color="#9467bd", label="KV occupancy %")
        ax[0].plot(rates, hit, "s-", color="#1f77b4", label="prefix-cache hit %")
        ax[0].set_ylabel("percent"); ax[0].set_ylim(0, 100)
        ax[0].set_xlabel("offered rate (req/s)")
        ax[0].set_title("As KV fills, prefix-cache hit rate collapses")
        axb = ax[0].twinx()
        axb.plot(rates, pre, "^--", color="#d62728", label="preemptions/s")
        axb.set_ylabel("preemptions / s", color="#d62728")
        axb.tick_params(axis="y", colors="#d62728")
        h1, l1 = ax[0].get_legend_handles_labels()
        h2, l2 = axb.get_legend_handles_labels()
        ax[0].legend(h1 + h2, l1 + l2, loc="center left")
        ax[0].grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        # panel 2: vLLM's own mean prefill time per request
        ax[1].plot(rates, pf, "o-", color="#d62728")
        ax[1].set_xlabel("offered rate (req/s)")
        ax[1].set_ylabel("mean prefill time / request (ms)")
        ax[1].set_title("Prefill cost per request (vLLM counter)")
        ax[1].grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        for r, y in zip(rates, pf):
            ax[1].annotate(f"{y:.0f}", (r, y), fontsize=6.5,
                           textcoords="offset points", xytext=(0, 5))
        fig.tight_layout()
        p = os.path.join(out_dir, "exp16_saturation_mechanism.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print("wrote:", p)

    print(f"{'rate':>4} {'kv%':>5} {'hit%':>6} {'preempt/s':>9} {'prefill_ms':>10}")
    for r in rates:
        print(f"{r:>4} {m[r]['kv']:>5.0f} {m[r]['hit']:>6.1f} "
              f"{m[r]['preempt']:>9.2f} {m[r]['prefill_ms']:>10.0f}")


if __name__ == "__main__":
    main()
