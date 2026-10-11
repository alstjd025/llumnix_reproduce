#!/usr/bin/env python3
"""Four candidate x axes for the EXP-55 single-class sweeps, side by side.

The three classes were swept over different rate ranges -- chat 20-90, deep
research 4-30, swe 4-80 req/s -- because their knees are a factor of four apart
and a range that resolves one wastes conditions on the others. On a linear
request-rate axis the three curves then occupy different thirds of the plot and
look like three separate experiments rather than one.

The ranges are not the problem; the axis is. This draws the same data on four
axes so the choice can be made by looking:

  A  request rate, linear      what the figure does now
  B  request rate, log         the same numbers, spaced by ratio rather than by
                               difference. A four-fold spread in the knees is a
                               constant offset here instead of three disjoint
                               regions
  C  input tokens per second   a physical axis rather than a counting one. It
                               compresses the spread from 9x to 3.8x AND changes
                               the order, which is itself a result
  D  rate / that class's knee  every class crosses 1.0 by construction, so this
                               shows the SHAPE of the collapse rather than where
                               it happens

The knee in D is derived, not taken from the record: the rate at which offered
attainment crosses 90%, linearly interpolated between the two bracketing points.

  python3 exp55_xaxis_options.py 'results/*exp55r1*' <out-dir>
"""
import glob
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run, attain  # noqa: E402
from exp55_knees import CLS  # noqa: E402

KNEE_AT = 90.0   # offered attainment considered the knee


def knee_rate(g):
    """The rate at which offered attainment crosses KNEE_AT, interpolated."""
    g = g.sort_values("rate")
    x, y = g["rate"].to_numpy(), g["off"].to_numpy()
    below = np.where(y < KNEE_AT)[0]
    if len(below) == 0 or below[0] == 0:
        return np.nan
    i = below[0]
    x0, x1, y0, y1 = x[i - 1], x[i], y[i - 1], y[i]
    if y0 == y1:
        return x1
    return x0 + (y0 - KNEE_AT) * (x1 - x0) / (y0 - y1)


def main(pattern, out):
    rows = []
    for d in sorted(glob.glob(pattern)):
        m = re.search(r"_(schat|sdr|sswe)_rpm_(\d+)", os.path.basename(d))
        if not m:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        name, _, _, intok = CLS[m.group(1)]
        rate = int(m.group(2)) / 60.0
        rows.append(dict(cls=name, key=m.group(1), rate=rate,
                         intok=intok * rate / 1000.0,
                         off=attain(r, "violate_offered")))
    df = pd.DataFrame(rows)
    if df.empty:
        sys.exit(f"no runs matched {pattern}")

    knees = {k: knee_rate(df[df.key == k]) for k in CLS}
    print("derived knee (rate where offered crosses 90%)")
    for k, v in knees.items():
        print(f"  {CLS[k][0]:<14} {v:6.1f} req/s   "
              f"측정된 rate: " + " ".join(f"{x:.0f}" for x in
                                          sorted(df[df.key == k]["rate"])))

    common = set.intersection(*[set(df[df.key == k]["rate"].round(1)) for k in CLS])
    print(f"\n세 클래스가 공통으로 가진 rate: {sorted(common) or '없음'}")
    for a, b in (("schat", "sswe"), ("sdr", "sswe"), ("schat", "sdr")):
        s = sorted(set(df[df.key == a]["rate"].round(1))
                   & set(df[df.key == b]["rate"].round(1)))
        print(f"  {CLS[a][0]:>12} ∩ {CLS[b][0]:<14} {s}")

    os.makedirs(out, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 4, figsize=(11.0, 2.6))
        for k, (name, c, _, _) in CLS.items():
            g = df[df.key == k].sort_values("rate")
            ax[0].plot(g.rate, g.off, color=c, marker="o", ms=3, label=name)
            ax[1].plot(g.rate, g.off, color=c, marker="o", ms=3, label=name)
            ax[2].plot(g.intok, g.off, color=c, marker="o", ms=3, label=name)
            if knees[k] == knees[k]:
                ax[3].plot(g.rate / knees[k], g.off, color=c, marker="o", ms=3,
                           label=name)
        ax[1].set_xscale("log")
        ax[1].set_xticks([4, 8, 16, 32, 64])
        ax[1].get_xaxis().set_major_formatter(matplotlib.ticker.ScalarFormatter())
        ax[3].axvline(1.0, color="#888888", ls="--", lw=0.8)
        ax[3].axhline(KNEE_AT, color="#888888", ls="--", lw=0.8)
        ax[3].set_xlim(0, 3)

        for a_, t, xl in zip(
                ax,
                ["A. request rate, linear (what we have)", "B. request rate, log",
                 "C. input tokens per second", "D. rate / that class's own knee"],
                ["request rate (req/s)", "request rate (req/s)",
                 "input tokens/s (thousands)", "rate / that class's knee"]):
            a_.set_title(t, fontsize=8)
            a_.set_xlabel(xl)
            a_.set_ylim(0, 105)
            a_.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax[0].set_ylabel("SLO attainment (%)\noffered denominator")
        ax[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.42), ncol=3,
                     fontsize=7, columnspacing=1.2)
        fig.suptitle(
            "EXP-55, the same three single-class sweeps on four x axes. The rate "
            "ranges differ because the knees are a factor of four apart;\nwhat "
            "changes between these panels is only how that spread is displayed.",
            fontsize=8, y=1.10)
        p = os.path.join(out, "xaxis_options.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/*exp55r1*",
         sys.argv[2] if len(sys.argv) > 2 else "results/aggregate_analysis/exp55")
