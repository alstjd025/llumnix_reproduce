#!/usr/bin/env python3
"""EXP-09: ITL CDFs — same rate, different admission theta, overlaid.

ITL sample = client-observed inter-chunk arrival gap (`inter_arrival_ms` in
tbt_events.jsonl; vLLM streams ~per token, so this is per-token ITL up to
client-side chunk coalescing). For each rate condition (rpm), the CDF pools
all ITL samples of steady-window requests (request start in [60s, dur-20s]);
rejected/errored calls contribute no samples. One panel per rate, one line
per theta; theta=off comes from the EXP-06 no-admission sweep.

Outputs: <out>/exp09_itl_cdf_grid.png, exp09_itl_percentiles.csv
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

STEADY_LO, DRAIN_S = 60.0, 20.0
MAX_SAMPLES = 400_000                     # per (rate, theta), stride-subsampled
COLORS = {0.0: "0.25", 0.3: "#9467bd", 0.4: "#2ca02c", 0.5: "#ff7f0e", 0.6: "#d62728"}
PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 8, "legend.frameon": False,
         "lines.linewidth": 1.5}


def itl_samples(run_dir):
    path = os.path.join(run_dir, "tbt_events.jsonl")
    if not os.path.isfile(path):
        return np.array([])
    rows = []          # (start_time, [itl...])
    t0, tmax = None, 0.0
    for line in open(path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        st = rec.get("start_time")
        if st is None:
            continue
        t0 = st if t0 is None else min(t0, st)
        tmax = max(tmax, rec.get("end_time") or st)
        ev = rec.get("chunk_events") or []
        if ev:
            rows.append((st, [e["inter_arrival_ms"] for e in ev
                              if e.get("inter_arrival_ms") is not None]))
    if not rows:
        return np.array([])
    hi = tmax - t0 - DRAIN_S
    out = []
    for st, itls in rows:
        rel = st - t0
        if STEADY_LO <= rel < hi:
            out.extend(itls)
    a = np.asarray(out, dtype=np.float32)
    if len(a) > MAX_SAMPLES:
        a = a[:: len(a) // MAX_SAMPLES + 1]
    return a


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp09")
    ap.add_argument("--min-conds", type=int, default=11,
                    help="thetas with fewer conditions are skipped (in-progress sweeps)")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    runs = {}                                  # (theta, rpm) -> dir
    for d in glob.glob("results/*exp06_swe_sweep_rpm_*"):
        runs[(0.0, int(d.split("rpm_")[1]))] = d
    counts = {}
    for d in glob.glob("results/*exp09_swe_kvadm_th*_rpm_*"):
        th = int(re.search(r"th(\d{4})_rpm", d).group(1)) / 1000.0
        counts[th] = counts.get(th, 0) + 1
        runs[(th, int(d.split("rpm_")[1]))] = d
    thetas = [0.0] + sorted(th for th, n in counts.items() if n >= args.min_conds)
    rpms = sorted({rpm for th, rpm in runs if th in thetas})
    print("thetas:", thetas, "rates:", [r / 60 for r in rpms])

    cache_dir = os.path.join(args.out_dir, "itl_cache")
    os.makedirs(cache_dir, exist_ok=True)
    data, pct_rows = {}, []
    for th in thetas:
        for rpm in rpms:
            d = runs.get((th, rpm))
            if not d:
                continue
            cpath = os.path.join(cache_dir, f"th{int(th*1000):04d}_rpm{rpm}.npy")
            if os.path.isfile(cpath):
                a = np.load(cpath)
            else:
                a = itl_samples(d)
                np.save(cpath, a)
            data[(th, rpm)] = a
            if len(a):
                pct_rows.append(dict(theta=th, rate=rpm / 60.0, n=len(a),
                                     p50=np.percentile(a, 50), p90=np.percentile(a, 90),
                                     p95=np.percentile(a, 95), p99=np.percentile(a, 99)))
                r = pct_rows[-1]
                print(f"th={th if th else 'off':>4} {r['rate']:>5.2f} j/s: n={r['n']:>7} "
                      f"p50={r['p50']:6.1f} p95={r['p95']:7.1f} p99={r['p99']:8.1f} ms")
    pd.DataFrame(pct_rows).to_csv(os.path.join(args.out_dir, "exp09_itl_percentiles.csv"),
                                  index=False)

    ncol = 4
    nrow = (len(rpms) + ncol - 1) // ncol
    with plt.rc_context(PAPER):
        fig, axes = plt.subplots(nrow, ncol, figsize=(4.0 * ncol, 3.1 * nrow), squeeze=False)
        for i, rpm in enumerate(rpms):
            ax = axes[i // ncol][i % ncol]
            for th in thetas:
                a = data.get((th, rpm))
                if a is None or not len(a):
                    continue
                xs = np.sort(a)
                ax.plot(xs, np.arange(1, len(xs) + 1) / len(xs),
                        color=COLORS.get(th, "0.5"),
                        label=("no admission" if th == 0 else f"θ={th:g}"))
            ax.axvline(50, color="k", ls="--", lw=1.3, alpha=0.85)
            ax.set_xscale("log"); ax.set_xlim(5, 3000); ax.set_ylim(0, 1.02)
            ax.set_xticks([10, 20, 50, 100, 200, 500, 1000],
                          ["10", "20", "50", "100", "200", "500", "1000"], fontsize=7.5)
            ax.set_xticks([], minor=True)
            ax.text(50, 0.03, " 50ms", fontsize=7, color="k", ha="left")
            ax.set_title(f"{rpm / 60:g} jobs/s offered")
            ax.grid(axis="both", ls=":", lw=0.5, alpha=0.5)
            if i % ncol == 0:
                ax.set_ylabel("CDF")
            if i // ncol == nrow - 1:
                ax.set_xlabel("ITL (ms, log)")
        for j in range(len(rpms), nrow * ncol):
            axes[j // ncol][j % ncol].axis("off")
        h, l = axes[0][0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 1.00), ncol=len(thetas))
        fig.suptitle("EXP-09 — ITL CDF per offered rate, admission θ overlaid\n"
                     "(inter-chunk arrival gaps, steady-window requests; dotted line = 50 ms)",
                     y=1.06)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp09_itl_cdf_grid.png")
        fig.savefig(out, dpi=140, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)


if __name__ == "__main__":
    main()
