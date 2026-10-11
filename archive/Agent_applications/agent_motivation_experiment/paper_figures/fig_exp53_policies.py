#!/usr/bin/env python3
"""Paper figure: EXP-53 — four control planes on one static rate sweep.

  FluidServe   our policy, gate slack 1.0, the configuration tagged v0.1.1
  PolyServe    static per-class partition, ported
  Llumnix SLO  the shipped SLO-aware policy
  Llumnix      the shipped load-balancing policy, no SLO input

Two PDFs, each two side-by-side panels — (left) SLO attainment, (right) output
token goodput — against offered request rate, sized for ONE COLUMN of a
two-column USENIX paper. They differ only in whether the offered-denominator
curves are drawn:

  exp53_attainment_goodput_offered.pdf    with the offered curves
  exp53_attainment_goodput_admitted.pdf   admitted denominator only

Sizing, and why the labelling is trimmed, are in `paper_style.py` and in
`README.md` in this directory. Include at width=\\columnwidth and do not rescale.

WHY THE OFFERED CURVES MATTER MORE HERE THAN IN ANY OTHER FIGURE WE HAVE. Two
of the four arms reject and two cannot. Read on the admitted denominator alone,
Llumnix SLO IMPROVES from 44.0% at 50 req/s to 69.8% at 70 req/s — while its
rejection rate goes from 20.7% to 68.9%. It is not getting better; it is
refusing more than two thirds of the arrivals and scoring itself on the third it
kept. On the offered denominator the same arm goes 34.7 -> 21.1. An
admitted-only version of this figure shows a rising curve for a collapsing
policy, so the admitted-only PDF should only be used where the surrounding text
gives the rejection rates.

TWO REPEATS, AND ONE POINT WHERE THAT MATTERS. The two agree within about a
point at every condition except FluidServe at 60 req/s, where admitted reads
97.7 and 81.8 and offered 66.7 and 55.0. That is the bistability recorded in
`fluidserve-implementation.md` §52, and it is the reason that point carries a
bar four times taller than any other on the figure. No claim should rest on it.

X AXIS. The eight measured rates (15, 25, 35, 45, 50, 55, 60, 70) cannot all
carry a label in 1.3 in of axis width without colliding. Five of them are
labelled and all eight get a tick, so every label still sits on a rate that was
actually measured and the unlabelled ticks show where the other three are. The
axis stays linear, which is why 45-60 looks crowded: those four rates really are
10 req/s apart and the knee really is that narrow.

DATA, AND THE ONE ARM THAT DOES NOT COME FROM EXP-53. Two repeats of every
condition. PolyServe is drawn from EXP-57, which re-measured that arm alone
after its tier length table was corrected; the correction moves it a long way at
the rates this figure is about — at 70 req/s attainment 14.4 -> 24.0% and
goodput 3,965 -> 10,220 tokens/s — so the EXP-53 PolyServe curve that earlier
versions of this figure carried should not be quoted. See the RUNS comment
below for why the two sessions can be joined.

    python3 paper_figures/fig_exp53_policies.py
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import (  # noqa: E402
    COL_W, STYLE, GRID, ARM_COLOR, ARM_MARKER, kfmt, save,
)
from exp53_compare import collect, ARMS  # noqa: E402

# ONE GLOB PER ARM, not one per session, because the arms no longer come from
# the same session. PolyServe's `--polyserve-tier-decode-tokens` understated
# deep research by 3.58x, was corrected on 2026-08-05, and that arm alone was
# re-measured as EXP-57; its EXP-53 runs are SUPERSEDED. They are still on disk
# and `results/*exp53*` still matches them, so a session-shaped glob would
# average a stale tier table into a corrected one and draw a policy that never
# ran. No other arm reads that flag, so the other three keep their EXP-53 runs.
#
# That the two sessions can be joined was measured rather than assumed: Llumnix
# SLO, unchanged in code and configuration, was re-run in the EXP-57 session at
# 45 and 70 req/s and read 52.3 and 21.7 offered against EXP-53's 52.4 and 21.4,
# inside EXP-53's own repeat spread. Those two conditions
# (`exp57unchanged_slo_*`) are the check and are not drawn, so that every rate
# keeps the same number of repeats.
#
# BOTH repeats of each arm. EXP-53's repeat 2 was relaunched under the prefix
# `exp53p2` rather than `exp53r2` (EXP-53 §5), so the obvious `results/*exp53r*`
# matches repeat 1 only and silently drops half the conditions; it did exactly
# that here until 2026-08-04. `exp53r1r1` is the top-up that refilled four
# repeat-1 cells lost to an engine that did not come up. The repeat prefixes are
# named rather than using `*exp53*` so that `exp53smoker1_loadbalance_m1_rpm_2700`
# -- a shakedown condition -- is not pooled with the two real repeats.
UNCHANGED = ["loadbalance_m1", "slo_m1f", "fluidserve_m1"]
RUNS = ([f"results/*exp53r1*_{a}_rpm_*" for a in UNCHANGED]
        + [f"results/*exp53p2*_{a}_rpm_*" for a in UNCHANGED]
        + ["results/*exp57r*_polyserve_m1_rpm_*"])
ORDER = ["fluidserve", "polyserve", "slo", "loadbalance"]
LABEL = {"fluidserve": "FluidServe", "polyserve": "PolyServe",
         "slo": "Llumnix SLO", "loadbalance": "Llumnix"}

XLABEL = "Offered rate (request/s)"
XTICKS = [15, 35, 45, 55, 70]      # labelled; a subset of the measured rates
YG_MAX, YG_TICKS = 23000, [0, 5000, 10000, 15000, 20000]


def build(df, out, show_offered):
    """One figure. `show_offered` adds the offered-denominator curves.

    An arm that never rejects has an offered curve identical to its admitted
    one, so it is not drawn: two coincident lines would suggest a distinction
    the data does not contain. Here that is PolyServe and Llumnix; FluidServe
    and Llumnix SLO both reject.
    """
    arms = [a for a in ORDER if a in set(df["arm"])]
    rates = np.array(sorted(df.rate.unique()), dtype=float)
    fig_h = 2.22 if show_offered else 1.95
    arms_y = 0.905 if show_offered else 0.845
    key_y, rect_top = 0.830, (0.820 if show_offered else 0.835)

    with plt.rc_context(STYLE):
        fig, (ax_a, ax_g) = plt.subplots(1, 2, figsize=(COL_W, fig_h))

        handles, labels, any_offered = [], [], False
        for arm in arms:
            d = df[df.arm == arm].groupby("rate").agg(
                adm=("adm", "mean"), admLo=("adm", "min"), admHi=("adm", "max"),
                off=("off", "mean"), offLo=("off", "min"), offHi=("off", "max"),
                gp=("goodput", "mean"), gpLo=("goodput", "min"),
                gpHi=("goodput", "max"), rej=("rej", "mean"),
            ).reset_index().sort_values("rate")
            c, mk = ARM_COLOR[arm], ARM_MARKER[arm]

            h = ax_a.errorbar(d.rate, d.adm,
                              yerr=[d.adm - d.admLo, d.admHi - d.adm],
                              color=c, ls="-", marker=mk, capsize=2,
                              mec="white", mew=0.5)
            handles.append(h)
            labels.append(LABEL[arm])

            if show_offered and bool((d.rej > 0).any()):
                ax_a.errorbar(d.rate, d.off,
                              yerr=[d.off - d.offLo, d.offHi - d.off],
                              color=c, ls=":", marker=mk, ms=3.0, alpha=0.55,
                              capsize=2, mec="white", mew=0.5)
                any_offered = True

            ax_g.errorbar(d.rate, d.gp, yerr=[d.gp - d.gpLo, d.gpHi - d.gp],
                          color=c, ls="-", marker=mk, capsize=2,
                          mec="white", mew=0.5)

        ax_a.set_ylabel("SLO attainment (%)")
        ax_a.set_ylim(0, 108)
        ax_a.set_yticks([0, 25, 50, 75, 100])
        ax_g.set_ylabel("Goodput token (t/s)")
        ax_g.set_ylim(0, YG_MAX)
        ax_g.set_yticks(YG_TICKS)
        ax_g.yaxis.set_major_formatter(kfmt())

        for ax in (ax_a, ax_g):
            ax.set_xticks(XTICKS)
            # Every measured rate as an unlabelled minor tick, so the figure
            # still says where the measurements are.
            ax.set_xticks(rates, minor=True)
            ax.set_xlim(rates.min() - 4, rates.max() + 4)
            ax.grid(axis="both", **GRID)
            ax.set_axisbelow(True)

        legend_kw = dict(loc="lower center", frameon=False, columnspacing=0.6,
                         handlelength=1.3, handletextpad=0.3, borderaxespad=0.0)
        fig.legend(handles, labels, ncol=len(labels),
                   bbox_to_anchor=(0.5, arms_y), **legend_kw)
        if any_offered:
            key = plt.Line2D([], [], color="#555555", ls=":", lw=1.2, alpha=0.8)
            fig.legend([key], ["dotted: offered denominator, rejected = violation"],
                       ncol=1, bbox_to_anchor=(0.5, key_y), **legend_kw)
        fig.tight_layout(rect=(0, 0.10, 1, rect_top), w_pad=0.8, pad=0.35)
        # One x label centred under both panels: repeating it costs a line of
        # figure height and reads as two different quantities.
        fig.text(0.5, 0.005, XLABEL, ha="center", va="bottom", fontsize=8)
        save(fig, out)


def main():
    df = collect([os.path.join(ROOT, p) for p in RUNS])
    if df.empty:
        print("no EXP-53 conditions matched", file=sys.stderr)
        return 1
    n = df.groupby(["arm", "rate"]).size()
    print(f"{len(df)} conditions, {df.arm.nunique()} arms, "
          f"repeats per cell: {sorted(set(n))}")
    # Per arm, so that a glob which matched nothing is visible here rather than
    # as a missing line on the figure.
    for arm, k in df.groupby("arm").size().items():
        print(f"  {arm:12s} {k:2d} conditions, "
              f"{df[df.arm == arm].rate.nunique()} rates")
    build(df, os.path.join(HERE, "exp53_attainment_goodput_offered.pdf"),
          show_offered=True)
    build(df, os.path.join(HERE, "exp53_attainment_goodput_admitted.pdf"),
          show_offered=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
