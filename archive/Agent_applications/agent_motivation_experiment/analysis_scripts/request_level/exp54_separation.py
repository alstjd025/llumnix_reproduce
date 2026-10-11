#!/usr/bin/env python3
"""Does the class separation reproduce, and does the score depend on it?

EXP-54 recorded a clean separation in repeat 1 -- deep research collected on one
engine -- and section 58.3 read that as the mechanism producing the score.

The first version of this script answered the question with a whole-run
statistic and got it wrong. Pooled over the hour, the share of deep research on
the engine holding the most of it read 58.7% in repeat 1 and 32.6% in repeat 2
against 25% for an even spread, which was recorded as "the separation does not
reproduce". Windowed at three minutes both repeats sit at 99-100% for the first
third of the hour: the separation reproduces, and what differs is WHICH engine
holds it and whether that lasts. Repeat 1 keeps deep research on engine 8001 for
the whole hour; repeat 2 moves it from 8002 to 8003 to 8001. Summing an hour
over a target whose identity moves spreads the distribution and reads as no
concentration at all.

So the figure carries three panels, because any two of them mislead:

  A  attainment over time, both repeats of every arm -- the score reproduces
  B  the share of each class held by the engine holding the most, windowed --
     so does the separation
  C  which engine that is -- and that does not

A class spread evenly over four engines reads 25% in B. PolyServe reads 100% for
deep research because its partition assigns that class to one engine outright.

  python3 exp54_separation.py --out results/aggregate_analysis/exp54
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, CLASSES, load_run, attain  # noqa: E402
from exp41_engine_view import attribute_engines  # noqa: E402

ARMS = {"fluidserve": ("FluidServe", "#1f77b4"),
        "polyserve": ("PolyServe", "#d62728"),
        "slo": ("Llumnix SLO", "#2ca02c")}
REPS = {"r1": "-", "r2": "--"}
WIN, STEP = 180.0, 60.0   # wider than the attainment window: a share needs counts


def runs_for(pattern="results/*exp54r[12]_{arm}_full"):
    out = {}
    for arm in ARMS:
        for d in sorted(glob.glob(pattern.format(arm=arm))):
            rep = "r1" if "_exp54r1_" in os.path.basename(d) else "r2"
            out[(arm, rep)] = d
    return out


def top_share(df, cls, t0, t1):
    """Share of class `cls` arriving in [t0, t1) held by the busiest engine, and
    which engine that is.

    Uniform over four engines is 25%. Windows with fewer than 40 requests of the
    class are dropped rather than plotted, because a share computed over a
    handful of requests moves for reasons that have nothing to do with routing.

    Both values are returned because the share alone is the statistic that got
    this wrong once. Pooled over the whole hour it read 32.6% for a repeat that
    was in fact concentrated at 99-100% in every window -- the engine holding the
    concentration moved twice, and summing over the hour spreads a moving target
    into what looks like no concentration at all.
    """
    w = df[(df["rel"] >= t0) & (df["rel"] < t1) & (df["class"] == cls)]
    if len(w) < 40:
        return np.nan, np.nan
    n = w["engine_port"].value_counts()
    return 100.0 * n.iloc[0] / n.sum(), int(n.index[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/aggregate_analysis/exp54")
    ap.add_argument("--pattern", default="results/*exp54r[12]_{arm}_full")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    runs = runs_for(a.pattern)
    if not runs:
        sys.exit("no runs matched")

    att, sep, whole = {}, {}, []
    for (arm, rep), d in runs.items():
        r = load_run(d)
        if r is None or r.empty:
            continue
        dur = r["rel"].max()
        xs, ys = [], []
        t = WIN / 2
        while t + WIN / 2 <= dur:
            w = r[(r["rel"] >= t - WIN / 2) & (r["rel"] < t + WIN / 2)]
            xs.append(t / 60)
            ys.append(attain(w, "violate_offered"))
            t += STEP
        att[(arm, rep)] = (xs, ys)

        # Engine attribution is only defined for admitted requests; a rejected
        # one has no dispatch record and would otherwise be joined onto a
        # neighbour's id (see the CLAUDE.md note on the hour-long traces).
        if not os.path.exists(os.path.join(d, "analysis", "request_engine.csv")):
            print(f"  no request_engine.csv for {arm} {rep}; "
                  f"run build_request_engine_map.py")
            continue
        e, n = attribute_engines(d, r)
        print(f"  {arm} {rep}: {len(e)} of {n} admitted attributed "
              f"({100.0 * len(e) / max(n, 1):.1f}%)")
        xs2, ys2 = [], {c: ([], []) for c in CLASSES}
        t = WIN / 2
        while t + WIN / 2 <= dur:
            xs2.append(t / 60)
            for c in CLASSES:
                sh, who = top_share(e, c, t - WIN / 2, t + WIN / 2)
                ys2[c][0].append(sh)
                ys2[c][1].append(who)
            t += STEP
        sep[(arm, rep)] = (xs2, ys2)
        rec = dict(arm=ARMS[arm][0], rep=rep, offered=attain(r, "violate_offered"))
        for c in CLASSES:
            rec[c[:4] + "_pooled"] = top_share(e, c, 0, dur + 1)[0]
            v = [x for x in ys2[c][0] if x == x]
            rec[c[:4] + "_win"] = float(np.median(v)) if v else float("nan")
        whole.append(rec)

    tab = pd.DataFrame(whole).sort_values(["arm", "rep"])
    print("offered attainment, and the share of each class held by the engine "
          "holding the most of it (uniform = 25%).")
    print("`_pooled` sums the whole hour; `_win` is the median over 3-minute "
          "windows. Where they disagree the assignment moved.")
    print(tab.to_string(index=False, float_format=lambda v: f"{v:.1f}"))

    print("\nwhich engine held the most deep research, per window (FluidServe)")
    for rep in ("r1", "r2"):
        k = ("fluidserve", rep)
        if k in sep:
            who = sep[k][1]["deepresearch"][1]
            print(f"  {rep}: " + " ".join("." if w != w else f"{int(w) % 10}"
                                          for w in who))

    # Does the separation form when there is room and dissolve when there is
    # not? The concentration is already windowed above; this pairs each window
    # with the offered rate over the same window. The claim that the separation
    # comes and goes with load is stated in motivation.md and in
    # how-it-works section 6, and until this block existed it was a number
    # computed in a session and never written down anywhere reproducible.
    print("\ndeep-research concentration against the offered rate, "
          f"per {WIN:.0f} s window")
    pooled = []
    for rep in ("r1", "r2"):
        k = ("fluidserve", rep)
        if k not in sep or k not in runs:
            continue
        r = load_run(runs[k])
        xs2, ys2 = sep[k]
        rate, conc = [], []
        for x, c in zip(xs2, ys2["deepresearch"][0]):
            if c != c:
                continue
            t = x * 60.0
            w = r[(r["rel"] >= t - WIN / 2) & (r["rel"] < t + WIN / 2)]
            rate.append(len(w) / WIN)
            conc.append(c)
        if len(rate) < 3:
            continue
        pooled += list(zip(rate, conc))
        print(f"  {rep}: r = {np.corrcoef(rate, conc)[0, 1]:+.3f}  n={len(rate)}")
    if pooled:
        ra = np.array([p[0] for p in pooled])
        co = np.array([p[1] for p in pooled])
        print(f"  pooled: r = {np.corrcoef(ra, co)[0, 1]:+.3f}  n={len(ra)}")
        lo, hi = co[ra < 45], co[ra > 55]
        if len(lo) and len(hi):
            print(f"  mean concentration below 45 req/s: {lo.mean():.1f}% "
                  f"(n={len(lo)})")
            print(f"  mean concentration above 55 req/s: {hi.mean():.1f}% "
                  f"(n={len(hi)})")
            print(f"  an even spread over four engines would be 25.0%")

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(10.4, 3.4))
        for (arm, rep), (xs, ys) in sorted(att.items()):
            lab, col = ARMS[arm]
            ax[0].plot(xs, ys, color=col, ls=REPS[rep], lw=1.1,
                       label=f"{lab} {rep}")
        ax[0].set_ylim(0, 105)
        ax[0].set_ylabel("SLO attainment (%), per request\noffered denominator")
        ax[0].set_title("A. the score reproduces", fontsize=8)
        ax[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=3,
                     fontsize=6, columnspacing=0.8)

        for rep in ("r1", "r2"):
            k = ("fluidserve", rep)
            if k not in sep:
                continue
            xs, ys = sep[k]
            for c, cc in zip(CLASSES, ("#1f77b4", "#ff7f0e", "#2ca02c")):
                ax[1].plot(xs, ys[c][0], color=cc, ls=REPS[rep], lw=1.1,
                           label=f"{c} {rep}")
            # Which engine, as a step trace. This is the whole difference between
            # the repeats and it is invisible in the share above.
            ax[2].step(xs, ys["deepresearch"][1], where="mid", color="#ff7f0e",
                       ls=REPS[rep], lw=1.2, label=f"deepresearch {rep}")
        ax[1].axhline(25, color="#999999", lw=0.7, ls=":")
        ax[1].annotate("even over four engines", (0.02, 27), fontsize=6,
                       color="#666666", xycoords=("axes fraction", "data"))
        ax[1].set_ylim(0, 105)
        ax[1].set_ylabel("share of the class held by\nthe engine holding the most (%)")
        ax[1].set_title("B. so does the separation — FluidServe", fontsize=8)
        ax[1].legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=3,
                     fontsize=6, columnspacing=0.8)

        ports = sorted({int(w) for _, y in sep.items()
                        for w in y[1]["deepresearch"][1] if w == w})
        ax[2].set_yticks(ports)
        ax[2].set_ylabel("engine holding the most\ndeep research")
        ax[2].set_title("C. but not on which engine", fontsize=8)
        ax[2].legend(loc="upper center", bbox_to_anchor=(0.5, -0.22), ncol=2,
                     fontsize=6, columnspacing=0.8)

        for x in ax:
            x.set_xlabel("time (minutes)")
            x.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        fig.suptitle(
            "EXP-54, two repeats of one configuration on one hour-long trace. Both "
            "concentrate deep research on a single engine;\nrepeat 1 keeps it on the "
            "same one for the hour and repeat 2 moves it twice. Offered attainment "
            "differs by 0.8 points.", fontsize=8, y=1.04)
        fig.subplots_adjust(wspace=0.42)
        p = os.path.join(a.out, "separation_vs_score.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
