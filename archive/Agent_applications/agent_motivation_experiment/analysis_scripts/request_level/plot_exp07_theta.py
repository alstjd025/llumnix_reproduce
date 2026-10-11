#!/usr/bin/env python3
"""EXP-07: KV-occupancy-threshold admission — per-theta condition analysis.

X-axis is the admission threshold theta (categories, permissive -> strict:
off, 0.8, 0.6, 0.45, 0.3), one line per offered rate. SLO and steady-window
definitions match slo_sliding_window.py / plot_slo_vs_throughput.py:
TTFT <= 5 s (arrival-anchored) AND meanTBT <= 50 ms; steady window
[60 s, dur-20 s] by arrival; errors/timeouts/run-end-cut excluded and
reported; REJECTED requests are their own category and count as violations
in the offered view.

Outputs (in --out-dir):
  exp07_theta_summary.csv
  exp07_attain_vs_theta.png     attain_offered / attain_admitted / rejection%
  exp07_kv_tput_vs_theta.png    KV usage (mean + p10-p90) + token throughput
  exp07_tbt_vs_theta.png        per-request meanTBT p50 / p99 (+ TTFT p50)
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

TTFT_SLO_S = 5.0
TBT_SLO_MS = 50.0
STEADY_LO = 60.0
DRAIN_S = 20.0
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

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 8, "legend.frameon": False,
         "lines.linewidth": 1.4}
RATE_COLORS = {50: "#2ca02c", 60: "#1f77b4", 90: "#d62728"}


def kv_series(run_dir):
    per = []
    for p in ENGINE_PORTS:
        f = os.path.join(run_dir, "server_metrics", f"engine_{p}.jsonl")
        if not os.path.isfile(f):
            continue
        ts, vs, t0 = [], [], None
        for line in open(f):
            rec = json.loads(line)
            if not rec.get("ok"):
                continue
            vals = [v for k, v in rec.items()
                    if k.split("|")[0] == "vllm:kv_cache_usage_perc"
                    and isinstance(v, (int, float))]
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
    return per[0][0][:n], np.mean([v[:n] for _, v in per], axis=0) * 100.0


def condition_stats(run_dir):
    df = pd.read_csv(os.path.join(run_dir, "metrics.csv"), low_memory=False)
    r = df[df.agent != "job_summary"].copy()
    r = r[pd.to_numeric(r.get("start_time"), errors="coerce").notna()]
    t0 = r["start_time"].min()
    r["rel"] = r["start_time"] - t0
    dur = r["end_time"].max() - t0
    hi = dur - DRAIN_S

    bl = lambda c: r[c].fillna(False).astype(bool) if c in r.columns else pd.Series(False, index=r.index)
    rejected = bl("is_rejected")
    excl = (bl("is_error") | bl("is_timeout") | bl("is_server_terminated")) & ~rejected
    cls = r[~excl & ~rejected].copy()
    rej = r[rejected]

    tbt = pd.to_numeric(cls["tbt_mean_ms"], errors="coerce")
    ttft = pd.to_numeric(cls["first_token_latency"], errors="coerce")
    cls["violate"] = (ttft > TTFT_SLO_S) | (tbt > TBT_SLO_MS)

    sw = cls[(cls["rel"] >= STEADY_LO) & (cls["rel"] < hi)]
    rj = rej[(rej["rel"] >= STEADY_LO) & (rej["rel"] < hi)]
    n_att = int((~sw["violate"]).sum())
    n_off = len(sw) + len(rj)

    ok = r[r["success"].astype(bool)].copy()
    ok["rel_end"] = ok["end_time"] - t0
    win = ok[(ok["rel_end"] >= STEADY_LO) & (ok["rel_end"] <= hi)]
    tokps = pd.to_numeric(win["output_tokens"], errors="coerce").sum() / max(1e-9, hi - STEADY_LO)

    kt, kv = kv_series(run_dir)
    m = (kt >= STEADY_LO) & (kt <= (kt.max() if len(kt) else 0) - DRAIN_S)
    kv_stats = ((float(kv[m].mean()), float(np.percentile(kv[m], 10)),
                 float(np.percentile(kv[m], 90))) if m.any() else (np.nan,) * 3)

    tbt_sw = pd.to_numeric(sw["tbt_mean_ms"], errors="coerce").dropna()
    ttft_sw = pd.to_numeric(sw["first_token_latency"], errors="coerce").dropna()
    return dict(
        attain_admitted=100.0 * n_att / max(1, len(sw)),
        attain_offered=100.0 * n_att / max(1, n_off),
        reject_pct=100.0 * len(rj) / max(1, n_off),
        classified=len(sw), rejected=len(rj), excluded=int(excl.sum()),
        tokps=tokps, kv_mean=kv_stats[0], kv_p10=kv_stats[1], kv_p90=kv_stats[2],
        tbt_p50=tbt_sw.quantile(0.5) if len(tbt_sw) else np.nan,
        tbt_p99=tbt_sw.quantile(0.99) if len(tbt_sw) else np.nan,
        ttft_p50=ttft_sw.quantile(0.5) if len(ttft_sw) else np.nan,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp07_kvadm_th*_rpm_*")
    ap.add_argument("--min-rpm", type=int, default=3000,
                    help="skip smoke dirs below this rpm")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp07")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    rows = []
    for d in sorted(glob.glob(args.glob)):
        m = re.search(r"th(\d{4})_rpm_(\d+)$", d)
        if not m:
            continue
        theta, rpm = int(m.group(1)) / 1000.0, int(m.group(2))
        if rpm < args.min_rpm:
            continue
        s = condition_stats(d)
        s.update(theta=theta, rate=rpm // 60, run=os.path.basename(d))
        rows.append(s)
        print(f"rate={s['rate']:>2} theta={'off' if theta == 0 else theta:>4}: "
              f"offered={s['attain_offered']:5.1f}% admitted={s['attain_admitted']:5.1f}% "
              f"rej={s['reject_pct']:5.1f}% tok/s={s['tokps']:6.0f} KVμ={s['kv_mean']:5.1f}% "
              f"TBTp50={s['tbt_p50']:5.1f} p99={s['tbt_p99']:6.1f}")
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(args.out_dir, "exp07_theta_summary.csv"), index=False)

    cats = [0.0, 0.8, 0.6, 0.45, 0.3]          # permissive -> strict
    labels = ["off", "0.8", "0.6", "0.45", "0.3"]
    xpos = {th: i for i, th in enumerate(cats)}

    def per_rate(rate, col):
        sub = t[t["rate"] == rate]
        xs = [xpos[th] for th in sub["theta"] if th in xpos]
        order = np.argsort(xs)
        vals = sub[col].to_numpy()
        return np.array(xs)[order], vals[np.argsort([xpos[th] for th in sub["theta"]])]

    rates = sorted(t["rate"].unique())
    with plt.rc_context(PAPER):
        # 1) attainment + rejection
        fig, axes = plt.subplots(1, len(rates), figsize=(4.2 * len(rates), 4.0),
                                 sharey=True, squeeze=False)
        for ax, rate in zip(axes[0], rates):
            x, off = per_rate(rate, "attain_offered")
            _, adm = per_rate(rate, "attain_admitted")
            _, rj = per_rate(rate, "reject_pct")
            c = RATE_COLORS.get(rate, "0.3")
            ax.plot(x, off, "D-", color=c, label="SLO attainment (offered)")
            ax.plot(x, adm, "o--", color=c, alpha=0.45, label="SLO attainment (admitted only)")
            ax.plot(x, rj, "x:", color="#ff7f0e", label="Rejection rate")
            ax.set_xticks(range(len(cats))); ax.set_xticklabels(labels)
            ax.set_xlabel("admission threshold θ (hot KV usage)")
            ax.set_title(f"{rate} req/s offered")
            ax.set_ylim(-3, 105); ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
        axes[0][0].set_ylabel("% of steady-window requests")
        h, l = axes[0][0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.99), ncol=3)
        fig.suptitle("EXP-07 — SLO attainment vs KV-occupancy admission threshold "
                     "(offered view: rejected requests count as violations)", y=1.10)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp07_attain_vs_theta.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)

        # 2) KV usage + throughput
        fig, axes = plt.subplots(1, len(rates), figsize=(4.2 * len(rates), 4.0), squeeze=False)
        for ax, rate in zip(axes[0], rates):
            x, kvm = per_rate(rate, "kv_mean")
            _, k10 = per_rate(rate, "kv_p10")
            _, k90 = per_rate(rate, "kv_p90")
            _, tok = per_rate(rate, "tokps")
            ax.plot(x, kvm, "o-", color="#2ca02c", label="KV usage (steady mean)")
            ax.fill_between(x, k10, k90, color="#2ca02c", alpha=0.18, label="KV p10–p90")
            ax.set_ylim(0, 105); ax.set_xticks(range(len(cats))); ax.set_xticklabels(labels)
            ax.set_xlabel("admission threshold θ"); ax.set_title(f"{rate} req/s offered")
            ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
            ax2 = ax.twinx()
            ax2.plot(x, tok, "^-", color="#d62728", label="Output tokens/s")
            ax2.set_ylim(0, max(t["tokps"]) * 1.15)
            if rate == rates[-1]:
                ax2.set_ylabel("Output tokens/s", color="#d62728")
            ax2.tick_params(axis="y", colors="#d62728")
            if rate == rates[0]:
                shared_lines = ax.get_lines() + ax2.get_lines()
                shared_lines.append(ax.collections[0])  # p10-p90 band patch
        axes[0][0].set_ylabel("KV cache usage (%)", color="#2ca02c")
        fig.legend(shared_lines, [l.get_label() for l in shared_lines],
                   loc="lower center", bbox_to_anchor=(0.5, 0.99), ncol=3)
        fig.suptitle("EXP-07 — KV usage & throughput vs admission threshold", y=1.10)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp07_kv_tput_vs_theta.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)

        # 3) TBT / TTFT
        fig, axes = plt.subplots(1, len(rates), figsize=(4.2 * len(rates), 4.0),
                                 sharey=True, squeeze=False)
        for ax, rate in zip(axes[0], rates):
            x, p50 = per_rate(rate, "tbt_p50")
            _, p99 = per_rate(rate, "tbt_p99")
            ax.plot(x, p50, "o-", color="#1f77b4", label="meanTBT p50")
            ax.plot(x, p99, "s--", color="#1f77b4", alpha=0.5, label="meanTBT p99")
            ax.axhline(TBT_SLO_MS, color="0.4", ls=":", label=f"TBT SLO {TBT_SLO_MS:.0f}ms")
            ax.set_xticks(range(len(cats))); ax.set_xticklabels(labels)
            ax.set_xlabel("admission threshold θ"); ax.set_title(f"{rate} req/s offered")
            ax.set_yscale("log"); ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)
        axes[0][0].set_ylabel("per-request mean TBT (ms, log)")
        h, l = axes[0][0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.99), ncol=3)
        fig.suptitle("EXP-07 — decode TBT vs admission threshold (admitted requests)", y=1.10)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp07_tbt_vs_theta.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)


if __name__ == "__main__":
    main()
