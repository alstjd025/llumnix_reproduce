#!/usr/bin/env python3
"""EXP-40's headline: is the control-plane advantage an artifact of the engine?

The rate sweep draws four curves and leaves the reader to subtract two pairs of
them by eye. The question the experiment asks is about that difference, so it is
drawn directly.

  left    the four arms, hue = control plane, lighter shade = deadline-aware
          engine, on the offered denominator. Reading the two shades of one hue
          is reading what the engine changed for that control plane.
  middle  the difference that is the claim: FluidServe minus the Llumnix SLO
          arm, once under each engine, with the repeat spread as a band. If the
          two lines lie inside each other's band, the advantage does not depend
          on the engine.
  right   the mechanism, chat's mean inter-token latency against its 50 ms
          budget. Section 34 measures roughly ten points of chat attainment per
          millisecond here, so a millisecond on this panel is the unit in which
          the middle panel is paid for.

  python3 exp40_engine_cross.py --runs 'results/*exp40*' --out-dir <dir>
"""
import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    PAPER_STYLE, load_run, per_request,
)

ARMS = {
    "slofifo":            ("Llumnix SLO + FIFO",    "#2ca02c", "-",  "o"),
    "sloqoserve":         ("Llumnix SLO + QoServe", "#98df8a", "--", "o"),
    "fluidservefifo":     ("FluidServe + FIFO",     "#1f77b4", "-",  "s"),
    "fluidserveqoserve":  ("FluidServe + QoServe",  "#aec7e8", "--", "s"),
}
PAIRS = [("FIFO", "fluidservefifo", "slofifo", "#1f77b4", "-"),
         ("QoServe", "fluidserveqoserve", "sloqoserve", "#ff7f0e", "--")]


def collect(patterns):
    rows = []
    for p in patterns:
        for d in sorted(glob.glob(p)):
            m = re.search(r"exp40r\d_([a-z]+)_m1f?_rpm_(\d+)", os.path.basename(d))
            if not m or m.group(1) not in ARMS:
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            ch = r[(r["class"] == "chat") & ~r["rejected"] & ~r["errored"] & ~r["cutoff"]]
            rows.append({
                "arm": m.group(1), "rate": int(m.group(2)) / 60.0,
                "off": per_request(r, "violate_offered"),
                "itl": pd.to_numeric(ch["itl_ms"], errors="coerce").mean(),
            })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    df = collect(a.runs)
    if df.empty:
        sys.exit("no runs matched")
    rates = sorted(df["rate"].unique())
    print(f"arms {sorted(set(df.arm))} at {rates} req/s from {len(df)} runs")

    def band(arm, col):
        g = df[df.arm == arm].groupby("rate")[col]
        return g.mean(), g.min(), g.max()

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(10.2, 3.2))

        for arm, (lab, c, ls, mk) in ARMS.items():
            mu, lo, hi = band(arm, "off")
            ax[0].errorbar(mu.index, mu.values,
                           yerr=[mu.values - lo.values, hi.values - mu.values],
                           color=c, ls=ls, marker=mk, capsize=2, label=lab)
        ax[0].set_ylabel("SLO attainment (%), offered, per request")
        ax[0].set_ylim(0, 105)
        ax[0].legend(fontsize=6.5, loc="lower left")
        ax[0].set_title("the four arms")

        # The difference IS the claim, so it is drawn rather than left to be
        # subtracted by eye. The band is the worst case the repeats allow: the
        # two arms' extremes combined, so a difference whose bands overlap is a
        # difference the repeats do not establish.
        for lab, fa, sa, c, ls in PAIRS:
            fm, flo, fhi = band(fa, "off")
            sm, slo_, shi = band(sa, "off")
            mid = fm.values - sm.values
            worst_lo = flo.values - shi.values
            worst_hi = fhi.values - slo_.values
            ax[1].plot(fm.index, mid, color=c, ls=ls, marker="D", label=f"under {lab}")
            ax[1].fill_between(fm.index, worst_lo, worst_hi, color=c, alpha=0.15, lw=0)
        ax[1].axhline(0, color="#666666", lw=0.6)
        ax[1].set_ylabel("FluidServe − Llumnix SLO (points, offered)")
        ax[1].legend(fontsize=7, loc="upper right")
        ax[1].set_title("the claim: does the gap depend on the engine?")

        for arm, (lab, c, ls, mk) in ARMS.items():
            mu, lo, hi = band(arm, "itl")
            ax[2].errorbar(mu.index, mu.values,
                           yerr=[mu.values - lo.values, hi.values - mu.values],
                           color=c, ls=ls, marker=mk, capsize=2)
        ax[2].axhline(50.0, color="#d62728", lw=0.8, ls=":")
        ax[2].annotate("chat budget 50 ms", (rates[0], 50.0), textcoords="offset points",
                       xytext=(2, 3), fontsize=6.5, color="#d62728")
        ax[2].set_ylabel("chat mean inter-token latency (ms)")
        ax[2].set_title("the mechanism, and what it is paid in")

        for x in ax:
            x.set_xlabel("offered rate (requests/s)")
            x.set_xticks(rates)
            x.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        fig.suptitle("EXP-40 — two control planes x two engine schedulers, one session, "
                     "two repeats\nbars and bands are min..max over repeats",
                     fontsize=9, y=1.06)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp40_engine_cross.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
