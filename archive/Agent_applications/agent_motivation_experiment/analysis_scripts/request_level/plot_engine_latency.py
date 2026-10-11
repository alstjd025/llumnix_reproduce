#!/usr/bin/env python3
"""Per-engine TTFT / ITL(TBT) over time, correlated with KV / running imbalance.

For each rate condition, one figure `latency_rpm_<rpm>.png` with 4 panels:
  A. per-engine windowed mean TTFT(t)   = Δ(ttft_sum)/Δ(ttft_count)
  B. per-engine windowed mean ITL(t)    = Δ(itl_sum)/Δ(itl_count)   (≈ TBT)
  C. per-engine KV cache usage (context — is the fleet imbalanced?)
  D. per-engine running requests (context)

Method notes:
  - Engine Prometheus histograms are cumulative; the collector scrapes every ~1s,
    so finite differences give "mean latency of the requests/tokens observed in
    that window", attributed to the engine that served them. This is the only
    per-engine attribution available (client metrics.csv has no engine id).
  - Engine TTFT is measured from the engine receiving the request to its first
    token — gateway queue time is EXCLUDED, which makes cross-engine comparison
    clean (pure engine-side slowdown).
  - Windows with Δcount == 0 yield NaN (no data, not zero); series are median-
    smoothed over SMOOTH_S seconds to tame small-count noise.
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


ENGINE_PORTS = engine_ports_of()
SMOOTH_S = 5  # smoothing window in ticks (~seconds)

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 7, "legend.frameon": False,
         "lines.linewidth": 1.2}


def _load(path):
    if not os.path.isfile(path):
        return []
    return [json.loads(l) for l in open(path) if l.strip()]


def series(recs, key):
    ts, vs, t0 = [], [], None
    for r in recs:
        if not r.get("ok"):
            continue
        vals = [v for k, v in r.items()
                if k.split("|")[0] == key and isinstance(v, (int, float))]
        if not vals:
            continue
        if t0 is None:
            t0 = r["t"]
        ts.append(r["t"] - t0)
        vs.append(sum(vals))
    return np.array(ts), np.array(vs)


def windowed_mean(recs, sum_key, count_key):
    """(t_mid, Δsum/Δcount) per scrape window; NaN where Δcount==0."""
    ts, sv = series(recs, sum_key)
    tc, cv = series(recs, count_key)
    n = min(len(sv), len(cv))
    if n < 2:
        return np.array([]), np.array([])
    ds, dc = np.diff(sv[:n]), np.diff(cv[:n])
    mean = np.where(dc > 0, ds / np.where(dc > 0, dc, 1), np.nan)
    tm = ts[:n - 1] + np.diff(ts[:n]) / 2
    # rolling median (NaN-aware) to tame small-sample noise
    if SMOOTH_S > 1 and len(mean) >= SMOOTH_S:
        sm = np.full_like(mean, np.nan)
        half = SMOOTH_S // 2
        for i in range(len(mean)):
            w = mean[max(0, i - half):i + half + 1]
            w = w[~np.isnan(w)]
            if len(w):
                sm[i] = np.median(w)
        mean = sm
    return tm, mean


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp03_70b_ratesweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp03_plots")
    ap.add_argument("--rate-key", default="rpm_",
                    help="dirname token preceding the rate value (e.g. 'lambda_')")
    ap.add_argument("--rate-div", type=float, default=60.0,
                    help="divide the parsed value by this to get req/s")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    dirs = sorted(glob.glob(args.glob),
                  key=lambda x: float(x.split(args.rate_key)[1]))
    colors = plt.cm.tab10(np.arange(4))

    written = []
    for run in dirs:
        rpm = float(run.split(args.rate_key)[1]); reqps = rpm / args.rate_div
        recs = {p: _load(os.path.join(run, "server_metrics", f"engine_{p}.jsonl"))
                for p in ENGINE_PORTS}
        with plt.rc_context(PAPER):
            fig, ax = plt.subplots(2, 2, figsize=(13, 8))

            a = ax[0, 0]
            for c, p in zip(colors, ENGINE_PORTS):
                t, m = windowed_mean(recs[p], "vllm:time_to_first_token_seconds_sum",
                                     "vllm:time_to_first_token_seconds_count")
                if len(t):
                    a.plot(t, m, color=c, label=f"eng {p}")
            a.set_title("A. Mean TTFT per engine (engine-side; excludes gateway queue)")
            a.set_ylabel("TTFT (s)"); a.legend(ncol=2)

            b = ax[0, 1]
            for c, p in zip(colors, ENGINE_PORTS):
                t, m = windowed_mean(recs[p], "vllm:inter_token_latency_seconds_sum",
                                     "vllm:inter_token_latency_seconds_count")
                if len(t):
                    b.plot(t, m * 1000, color=c, label=f"eng {p}")
            b.set_title("B. Mean ITL per engine (≈ TBT)")
            b.set_ylabel("ITL (ms/token)"); b.legend(ncol=2)

            cax = ax[1, 0]
            for c, p in zip(colors, ENGINE_PORTS):
                t, v = series(recs[p], "vllm:kv_cache_usage_perc")
                if len(t):
                    cax.plot(t, v * 100, color=c, label=f"eng {p}")
            cax.set_title("C. KV cache usage (context)")
            cax.set_ylabel("KV (%)"); cax.set_xlabel("time (s)"); cax.legend(ncol=2)

            d = ax[1, 1]
            for c, p in zip(colors, ENGINE_PORTS):
                t, v = series(recs[p], "vllm:num_requests_running")
                if len(t):
                    d.plot(t, v, color=c, label=f"eng {p}")
            d.set_title("D. Running requests (context)")
            d.set_ylabel("requests"); d.set_xlabel("time (s)"); d.legend(ncol=2)

            for row in ax:
                for pnl in row:
                    pnl.grid(axis="y", ls=":", lw=0.5, alpha=0.5)
            fig.suptitle(f"Per-engine latency vs load imbalance — offered {reqps:g} req/s",
                         y=1.0)
            fig.tight_layout()
            out = os.path.join(args.out_dir, f"latency_{args.rate_key}{rpm:g}.png")
            fig.savefig(out, dpi=140, bbox_inches="tight")
            plt.close(fig)
            written.append(out)
    print("wrote:")
    for w in written:
        print(" ", w)


if __name__ == "__main__":
    main()
