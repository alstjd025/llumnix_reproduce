#!/usr/bin/env python3
"""Motivation figure 5: the right answer moves, and it moves for two reasons.

Figures 3 and 4 are about a fleet at one operating point. This one is about the
operating point not staying still, which is what makes an assignment decided
once wrong later even if it was right when it was decided.

Two things move independently over the hour:

  the arrival rate       taken from four days of Azure production minutes,
                         rescaled to 25-75 req/s
  the class mix          cycled on fifteen-minute segments across three mixes

Panel C puts them together. Using the three single-class saturation rates
measured in EXP-55 -- chat 65.6, swe 23.1, deep research 16.5 req/s -- the rate
this fleet could sustain if the classes competed for one resource in fixed
proportions is

    R(t) = 1 / sum_c ( f_c(t) / K_c )

where f_c(t) is the share of class c among the requests arriving in that window.
R(t) is not a property of the fleet: measured, it runs from 37.6 to 55.6 req/s
over the hour, a factor of 1.48, purely because the mix moves and with the
hardware untouched. So "how loaded are we" cannot be answered from the arrival
rate, and a partition computed from one window's demand is answering a question
about a different window.

Measured on the hour trace: arrivals 13.8 to 73.8 req/s, chat 66.6% to 93.1% of
them, deep research 4.6% to 15.5%, swe 2.3% to 22.3%. The offered rate is above
R(t) for 63% of the hour.

The shaded stretches are where the offered rate is above R(t). They are where
every policy has to reject something, and they are the reason admission cannot
be a separate concern bolted on after routing: what should be rejected depends
on what has been placed where, and what can be placed depends on what has been
rejected.

The mix is read from the arrivals of one run, not from the trace file, so it is
the mix the fleet actually saw. All arrivals count, including the ones the
policy went on to reject -- this panel is about what was offered.

  python3 motivation_fig5.py <out-dir> [run-dir]
"""
import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run  # noqa: E402
from motivation_fig3 import single_class, crossing, CLS_C, CLS_LABEL  # noqa: E402

WIN = 120.0   # seconds per point; a mix share needs counts behind it


def main(out, run):
    r = load_run(run)
    if r is None or r.empty:
        sys.exit(f"no request rows in {run}")

    sc = single_class()
    K = {c: crossing(g.rate, g.off, 90.0) for c, g in sc.groupby("cls")}
    print("single-class saturation used for the capacity line (EXP-55):")
    for c in sorted(K):
        print(f"  {c:<14}{K[c]:6.2f} req/s")

    d = r.dropna(subset=["rel"]).copy()
    d["w"] = (d["rel"] // WIN).astype(int)
    g = d.groupby("w")
    rate = g.size() / WIN
    share = (d.groupby(["w", "class"]).size().unstack(fill_value=0)
             .reindex(columns=list(K), fill_value=0))
    share = share.div(share.sum(axis=1).replace(0, np.nan), axis=0)
    cap = 1.0 / sum(share[c] / K[c] for c in K)

    # The last window is usually a partial one, so its request count is an
    # artefact of the window being cut short rather than of the trace.
    t = np.asarray(share.index, dtype=float) * WIN / 60.0
    keep = np.asarray(t) < (d["rel"].max() / 60.0) - WIN / 60.0
    t = t[keep]
    rate = np.asarray(rate, dtype=float)[keep]
    cap = np.asarray(cap, dtype=float)[keep]
    share = {c: np.asarray(share[c], dtype=float)[keep] for c in K}

    over = rate > cap
    print(f"\noffered rate     {rate.min():5.1f} to {rate.max():5.1f} req/s")
    print(f"implied capacity {cap.min():5.1f} to {cap.max():5.1f} req/s   "
          f"(a factor of {cap.max()/cap.min():.2f} from the mix alone)")
    print(f"windows offered above capacity: {over.sum()} of {len(over)} "
          f"({100.0*over.mean():.0f}% of the hour)")
    print(f"\nclass share over the hour, min to max")
    for c in K:
        print(f"  {CLS_LABEL[c]:<14}{100*share[c].min():5.1f}% to "
              f"{100*share[c].max():5.1f}%")

    os.makedirs(out, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(3, 1, figsize=(7.2, 5.2), sharex=True)
        fig.subplots_adjust(left=0.115, right=0.985, top=0.885, bottom=0.135,
                            hspace=0.30)

        ax[0].plot(t, rate, color="#333333", lw=1.0)
        ax[0].fill_between(t, 0, rate, color="#333333", alpha=0.10, lw=0)
        ax[0].set_ylabel("A. requests arriving\n(req/s)")
        ax[0].set_ylim(0, max(rate.max(), cap.max()) * 1.12)

        bot = np.zeros(len(t))
        for c in ("chat", "deepresearch", "swe"):
            v = 100 * share[c]
            ax[1].fill_between(t, bot, bot + v, color=CLS_C[c], lw=0,
                               label=CLS_LABEL[c])
            bot = bot + v
        ax[1].set_ylabel("B. what they were\n(% of arrivals)")
        ax[1].set_ylim(0, 100)
        ax[1].legend(loc="lower center", bbox_to_anchor=(0.5, -0.02), ncol=3,
                     fontsize=6.5, columnspacing=1.4, framealpha=0.85)

        ax[2].plot(t, cap, color="#1f77b4", lw=1.3,
                   label="what this mix could sustain")
        ax[2].plot(t, rate, color="#333333", lw=1.0,
                   label="what arrived")
        ax[2].fill_between(t, cap, rate, where=over,
                           color="#d62728", alpha=0.22, lw=0,
                           label="offered above capacity")
        ax[2].set_ylabel("C. the two together\n(req/s)")
        ax[2].set_ylim(0, max(rate.max(), cap.max()) * 1.12)
        ax[2].set_xlabel("time (minutes)")
        ax[2].legend(loc="upper right", fontsize=6.5, ncol=3,
                     columnspacing=1.2, framealpha=0.85)
        ax[2].annotate(
            f"the capacity line moves {cap.max()/cap.min():.2f}x over the hour "
            f"with the hardware untouched;\nit is a property of the mix, not of "
            f"the fleet",
            (0.015, 0.06), xycoords="axes fraction", fontsize=6.8,
            color="#1f77b4", va="bottom")

        for a in ax:
            a.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            a.set_xlim(0, t.max())
            for m in (15, 30, 45):
                a.axvline(m, color="#999999", lw=0.5, ls=":")

        fig.suptitle(
            "The workload does not hold still: arrival rate from Azure "
            "production minutes, class mix cycled every fifteen minutes.\n"
            "The classes saturate this fleet at rates a factor of four apart, "
            "so a mix that moves moves the capacity with it.",
            fontsize=8.5, y=0.995)
        p = os.path.join(out, "motivation_the_target_moves.png")
        fig.savefig(p, dpi=300)
        print(f"\nwrote {p}")


if __name__ == "__main__":
    o = sys.argv[1] if len(sys.argv) > 1 else "results/aggregate_analysis/motivation"
    if len(sys.argv) > 2:
        run = sys.argv[2]
    else:
        c = sorted(glob.glob("results/*exp54r1_slo_full"))
        if not c:
            sys.exit("no EXP-54 run found; pass one explicitly")
        run = c[0]
    print(f"reading arrivals from {run}\n")
    main(o, run)
