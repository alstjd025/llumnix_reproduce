#!/usr/bin/env python3
"""KV "tank" control-view graphs: is flow a LEADING indicator of saturation?

G1 (per overloaded condition)  kvtank_pred_rpm_<rpm>.png:
    top:    active KV level + actual pin time (first tick >=99% of pool)
    middle: net inflow (inflow - outflow, smoothed)
    bottom: predicted time-to-full(t) = free_space / net_inflow  vs the ACTUAL
            remaining time to pin — if the curves track, flow imbalance predicts
            saturation ahead of time (admission-control lead time).

G2 (all conditions pooled)     kvtank_drain_curve.png:
    outflow rate vs active-KV level scatter (pre-pin ticks only, colored by
    offered rate) — the tank's intrinsic drain (capacity) curve. Its plateau is
    the decode-bound drain ceiling; the level where it flattens is the natural
    admission setpoint.

Flows are as in plot_kv_flows.py (conservation accounting). Ticks after the pool
pins at ~100% are EXCLUDED from flow fits: the prefix-cache counters are then
dominated by scheduler re-query thrash and no longer measure physical writes.
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
POOL_PER_ENGINE = 36570 * 16
SMOOTH_S = 15  # net-flow smoothing window (ticks ~ seconds); prediction horizon scale

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 8, "legend.frameon": False,
         "lines.linewidth": 1.4}


def fleet(run, key):
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
            vals = [v for k, v in rec.items()
                    if k.split("|")[0] == key and isinstance(v, (int, float))]
            if not vals:
                continue
            if t0 is None:
                t0 = rec["t"]
            ts.append(rec["t"] - t0)
            vs.append(sum(vals))
        if ts:
            per.append((np.array(ts), np.array(vs)))
    if not per:
        return np.array([]), np.array([])
    n = min(len(v) for _, v in per)
    return per[0][0][:n], np.sum([v[:n] for _, v in per], axis=0)


def movavg(x, w):
    if w > 1 and len(x) >= w:
        return np.convolve(x, np.ones(w) / w, mode="same")
    return x


def flows(run):
    t, kvfrac = fleet(run, "vllm:kv_cache_usage_perc")
    _, q = fleet(run, "vllm:prefix_cache_queries_total")
    _, h = fleet(run, "vllm:prefix_cache_hits_total")
    _, g = fleet(run, "vllm:generation_tokens_total")
    n = min(map(len, (t, q, h, g)))
    if n < 10:
        return None
    t = t[:n]
    level = kvfrac[:n] * POOL_PER_ENGINE            # fleet active KV tokens
    cap = 4 * POOL_PER_ENGINE
    dt = np.diff(t); dt[dt <= 0] = 1e-9
    inflow = (np.clip(np.diff(q[:n]) - np.diff(h[:n]), 0, None)
              + np.clip(np.diff(g[:n]), 0, None) + np.clip(np.diff(h[:n]), 0, None)) / dt
    dU = np.diff(level) / dt
    outflow = np.clip(inflow - dU, 0, None)
    net = inflow - outflow                           # == dU/dt by construction
    tm = t[:-1] + dt / 2
    # pin time: first tick where level >= 99% capacity
    pin_idx = np.argmax(level >= 0.99 * cap) if (level >= 0.99 * cap).any() else None
    pin_t = t[pin_idx] if pin_idx is not None and pin_idx > 0 else None
    return dict(t=t, level=level, cap=cap, tm=tm, inflow=inflow, outflow=outflow,
                net=net, pin_t=pin_t)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp05_warmup_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp05_kvflow")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    dirs = sorted(glob.glob(args.glob), key=lambda x: int(x.split("rpm_")[1]))

    drain_pts = []  # (level_frac, outflow, rate) pre-pin only
    for run in dirs:
        rpm = int(run.split("rpm_")[1])
        F = flows(run)
        if F is None:
            continue
        # collect drain-curve points (pre-pin, post-warmup)
        end = F["pin_t"] if F["pin_t"] else F["t"][-1] - 20
        # drop counter-thrash ticks: outflow beyond any physically plausible
        # rate (whole fleet pool turning over in <1s) means the prefix-cache
        # counters are re-query noise, not writes (can appear pre-pin too when
        # tool-delay workloads hold KV locked at mid levels)
        m = (F["tm"] >= 60) & (F["tm"] <= end) & (F["outflow"] < 2e6)
        for lv, of in zip(F["level"][:-1][m] / F["cap"], F["outflow"][m]):
            drain_pts.append((lv, of, rpm / 60.0))

        # G1 only for conditions that actually pin
        if not F["pin_t"]:
            continue
        net_s = movavg(F["net"], SMOOTH_S)
        free = F["cap"] - F["level"][:-1]
        with np.errstate(divide="ignore", invalid="ignore"):
            ttf = np.where(net_s > 500, free / net_s, np.nan)   # predicted sec to full
        actual = F["pin_t"] - F["tm"]                            # actual sec to pin
        pre = F["tm"] < F["pin_t"]

        with plt.rc_context(PAPER):
            fig, ax = plt.subplots(3, 1, figsize=(10, 8.5), sharex=True)
            a = ax[0]
            a.plot(F["t"], F["level"] / 1e3, color="#2ca02c")
            a.axhline(F["cap"] / 1e3, color="0.4", ls=":")
            a.axvline(F["pin_t"], color="#d62728", ls="--", label=f"actual pin t={F['pin_t']:.0f}s")
            a.set_ylabel("active KV (k tok)"); a.legend()
            a.set_title(f"G1 — flow as leading indicator (offered {rpm/60:.0f} req/s)")

            b = ax[1]
            b.plot(F["tm"], net_s / 1e3, color="#9467bd")
            b.axhline(0, color="0.6", lw=0.8)
            b.axvline(F["pin_t"], color="#d62728", ls="--")
            b.set_ylabel("net inflow (k tok/s)")

            c = ax[2]
            c.plot(F["tm"][pre], ttf[pre], color="#1f77b4", label="predicted time-to-full (free/net)")
            c.plot(F["tm"][pre], actual[pre], color="0.3", ls="--", label="actual time to pin")
            c.axvline(F["pin_t"], color="#d62728", ls="--")
            c.set_ylim(0, 200); c.set_ylabel("seconds"); c.set_xlabel("time (s)")
            c.legend()
            for p in ax:
                p.grid(axis="y", ls=":", lw=0.5, alpha=0.5)
            fig.tight_layout()
            out = os.path.join(args.out_dir, f"kvtank_pred_rpm_{rpm}.png")
            fig.savefig(out, dpi=140, bbox_inches="tight"); plt.close(fig)
            print("wrote:", out)

    # G2 drain curve
    if drain_pts:
        lv, of, rate = map(np.array, zip(*drain_pts))
        with plt.rc_context(PAPER):
            fig, axp = plt.subplots(figsize=(7.5, 5))
            sc = axp.scatter(lv * 100, of / 1e3, c=rate, s=8, alpha=0.5, cmap="viridis")
            # binned median drain
            bins = np.linspace(0, 1, 21)
            mids, med = [], []
            for i in range(len(bins) - 1):
                m = (lv >= bins[i]) & (lv < bins[i + 1])
                if m.sum() >= 5:
                    mids.append((bins[i] + bins[i + 1]) / 2 * 100)
                    med.append(np.median(of[m]) / 1e3)
            axp.plot(mids, med, "k-", lw=2, label="median drain (binned)")
            plt.colorbar(sc, label="offered rate (req/s)")
            axp.set_xlabel("active KV level (% of pool)")
            axp.set_ylabel("outflow / drain rate (k tok/s)")
            axp.set_title("G2 — tank drain capacity vs level (pre-pin ticks only)\n"
                          "plateau = decode-bound drain ceiling; its knee = natural admission setpoint")
            axp.legend(); axp.grid(ls=":", lw=0.5, alpha=0.5)
            fig.tight_layout()
            out = os.path.join(args.out_dir, "kvtank_drain_curve.png")
            fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
            print("wrote:", out)


if __name__ == "__main__":
    main()
