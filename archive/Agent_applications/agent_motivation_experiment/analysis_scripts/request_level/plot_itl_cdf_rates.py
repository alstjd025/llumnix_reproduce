#!/usr/bin/env python3
"""Per-rate ITL CDFs for one sweep (no theta dimension) on a single axis.

ITL sample = client-observed inter-chunk arrival gap (tbt_events.jsonl),
steady-window requests only (request start in [60s, dur-20s]). One CDF line
per rate condition, colored by offered rate (viridis). Reuses the sample
extractor + cache layout of plot_exp09_itl_cdf.py.

Default target: EXP-05 (chat, no admission).
"""

import argparse
import glob
import os
import importlib.util as _ilu

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.cm as cm
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

_spec = _ilu.spec_from_file_location(
    "itlmod", os.path.join(os.path.dirname(__file__), "plot_exp09_itl_cdf.py"))
_itl = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_itl)

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "legend.fontsize": 7.5, "legend.frameon": False, "lines.linewidth": 1.5}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp05_warmup_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp05_slo")
    ap.add_argument("--rate-div", type=float, default=60.0,
                    help="rpm divisor for the rate label (60 -> req/s)")
    ap.add_argument("--rate-unit", default="req/s")
    ap.add_argument("--rate-key", default="rpm_",
                    help="dirname token preceding the rate value (e.g. 'lambda_')")
    ap.add_argument("--tag", default="exp05")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    cache_dir = os.path.join(args.out_dir, "itl_cache")
    os.makedirs(cache_dir, exist_ok=True)

    dirs = sorted(glob.glob(args.glob),
                  key=lambda x: float(x.split(args.rate_key)[1]))
    rates, pct_rows, data = [], [], {}
    for d in dirs:
        rpm = float(d.split(args.rate_key)[1])
        cpath = os.path.join(cache_dir, f"{args.rate_key}{rpm:g}.npy")
        if os.path.isfile(cpath):
            a = np.load(cpath)
        else:
            a = _itl.itl_samples(d)
            np.save(cpath, a)
        if not len(a):
            continue
        rate = rpm / args.rate_div
        rates.append(rate); data[rate] = a
        pct_rows.append(dict(rate=rate, n=len(a),
                             p50=np.percentile(a, 50), p90=np.percentile(a, 90),
                             p95=np.percentile(a, 95), p99=np.percentile(a, 99)))
        r = pct_rows[-1]
        print(f"{rate:6.2f} {args.rate_unit}: n={r['n']:>7} p50={r['p50']:6.1f} "
              f"p90={r['p90']:7.1f} p95={r['p95']:7.1f} p99={r['p99']:8.1f} ms")
    pd.DataFrame(pct_rows).to_csv(
        os.path.join(args.out_dir, f"{args.tag}_itl_percentiles.csv"), index=False)

    norm = mcolors.Normalize(vmin=min(rates), vmax=max(rates))
    cmap = cm.viridis
    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(figsize=(8.2, 5.2))
        for rate in rates:
            xs = np.sort(data[rate])
            ax.plot(xs, np.arange(1, len(xs) + 1) / len(xs),
                    color=cmap(norm(rate)), label=f"{rate:g}")
        for v, lab in ((50, "50ms"), (100, "100ms")):
            ax.axvline(v, color="k", ls="--", lw=1.1, alpha=0.8)
            ax.text(v, 0.02, f" {lab}", fontsize=7.5)
        ax.set_xscale("log"); ax.set_xlim(5, 3000); ax.set_ylim(0, 1.02)
        ax.set_xticks([10, 20, 50, 100, 200, 500, 1000],
                      ["10", "20", "50", "100", "200", "500", "1000"])
        ax.set_xticks([], minor=True)
        ax.set_xlabel("ITL (ms, log)"); ax.set_ylabel("CDF")
        ax.grid(axis="both", ls=":", lw=0.5, alpha=0.5)
        ax.legend(title=f"offered ({args.rate_unit})", ncol=2, loc="lower right")
        ax.set_title(f"{args.tag} — ITL CDF per offered rate "
                     "(inter-chunk gaps, steady-window requests)")
        fig.tight_layout()
        out = os.path.join(args.out_dir, f"{args.tag}_itl_cdf_rates.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)


if __name__ == "__main__":
    main()
