#!/usr/bin/env python3
"""Paper figure: four ways to estimate how long a running request will be.

  length_predictors_<population>.pdf   3.335 x 1.75 in, width=\\columnwidth
  length_predictors_<population>.csv   exactly the values drawn

THE QUESTION. A control plane that re-evaluates a running request needs its
FINAL output length. Four estimators are available to it, each conditioning on
strictly more than the one above:

  global    the workload's typical length, one number for every request
  recent    the typical length of the last N requests that finished before this
            one arrived -- one number per request, but not per request's state
  pooled    j + the typical remaining length of requests still running at j,
            over the whole mixture          <- a survival function, no classes
  class     the same, over the request's own class   <- a survival function,
                                                        conditioned on class

SCORING IS IDENTICAL FOR ALL FOUR, and it is the one `fig_length_refinement.py`
uses: at a point where a request has produced j tokens and has not finished,
score |estimate - actual| / actual, and take the median over the requests still
running at j.

⚠ THE POPULATION BEING SCORED CHANGES WITH j, WHICH IS WHY A CONSTANT ESTIMATOR
DOES NOT DRAW A FLAT LINE. `global` never looks at j, but the requests alive at
j get longer as j grows, so one fixed number is wrong by more and more. At j = 0
`global` and `pooled` nearly coincide -- both are the centre of the whole
distribution -- and the figure is the story of how they come apart.

⚠ ONE CENTRAL STATISTIC FOR ALL FOUR: the median. Mixing a mean estimator with
median ones would vary two things at once, and the axis this figure is about is
WHAT THE ESTIMATOR CONDITIONS ON. The mean-based variants are in the CSV
(`*_mean` rows) for the reader who wants them; on this workload they are worse
for every estimator, because the length distributions are right-skewed.

⚠ `global`, `pooled` AND `class` ARE POOLED OVER RUNS; `recent` CANNOT BE.
The first three are properties of the workload's length distribution, so pooling
runs only sharpens them. `recent` is defined by an ordering in time, and two
runs have unrelated clocks, so it is computed WITHIN each run and the scored
errors are pooled afterwards. The first `--recent-min` requests of a run have no
history and are dropped from that curve only; the count is printed.

⚠ WHETHER A CONSTANT ESTIMATE IS CLAMPED TO j CHANGES `global` AND `recent` A
LOT, and nothing else. A request that has produced 800 tokens cannot have a final
length of 420, and any implementation would say max(estimate, j+1). Both are
computed: the drawn curve is the CLAMPED one, because it is the charitable
reading of the baseline, and the raw one is in the CSV. The gap between them is
the cost of the thing the survival function exists to avoid -- telling you a
request that has outlived the average is about to stop.

POPULATIONS.
  static  EXP-108 at 10 req/s, four arms and both repeats, completed requests
          only. No arm rejects at that rate, so the lengths are the workload's.
          ⚠ THE MIXTURE IS STATIONARY HERE, so `recent` and `global` sample the
          same distribution and differ only by noise. That is a result, not a
          defect of the figure: recency buys nothing in steady state.
  hour    EXP-109's hour trace, whose mixture shifts every 15 minutes, which is
          the only population on which `recent` can differ from `global`.
          ⚠ It is contaminated in a way `static` is not: that run rejects, so
          the lengths are of the requests the policy admitted.

    python3 paper_figures/fig_length_predictors.py [--population static|hour]
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import COL_W, STYLE, GRID, save  # noqa: E402
from exp22_fluidserve import load_run  # noqa: E402

POPULATIONS = {
    "static": "results/*exp108r?_*_rpm_600",
    # The failed repeat is named explicitly rather than filtered by a pattern:
    # 260901_0602 has engine metrics but no metrics.csv and no scheduler.jsonl,
    # so a glob over exp109 picks up a run that produced no requests at all.
    "hour": "results/*exp109r*_fsv3capgnofrct75_shift",
}
EXCLUDE = ("260901_0602_exp109r2_fsv3capgnofrct75_shift",)

GRID_J = [0, 50, 100, 150, 200, 300, 400, 500, 600, 700, 800, 1000]
MIN_ALIVE = 200
RECENT_N = 100
# The four estimators form a 2x2: whether the remaining length is a CONSTANT or
# a function of how far the request has run, crossed with whether the class is
# used. `memoryless` is the constant that a system holding one number would use.
COLORS = {"memoryless": "#b2182b", "memoryless_class": "#ef8a62",
          "pooled": "#67a9cf", "class": "#2166ac"}
NAMES = {"memoryless": "Constant, pooled", "memoryless_class": "Constant, per class",
         "pooled": "Survival, pooled", "class": "Survival, per class"}
EXTRA = {"global": "Workload mean (clamped)", "recent": f"Recent {RECENT_N}"}
FIG_H = 1.75


def population(pattern):
    """One frame per run, each carrying its own `recent` estimate."""
    frames = []
    for d in sorted(glob.glob(os.path.join(ROOT, pattern))):
        if "PRERUN" in d or os.path.basename(d) in EXCLUDE:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        r = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])].copy()
        r["out"] = pd.to_numeric(r["output_tokens"], errors="coerce")
        r = r.dropna(subset=["out", "start_time", "end_time"])
        r = r[r["out"] > 0]
        if r.empty:
            continue
        r["recent"] = recent_estimate(r)
        r["run"] = os.path.basename(d)
        frames.append(r)
    if not frames:
        sys.exit(f"no usable runs under {pattern}")
    return pd.concat(frames, ignore_index=True)


def recent_estimate(r):
    """Median final length of the last RECENT_N requests that FINISHED before
    this one started.

    Finished, not started: a length is only known once the request is done, so
    an estimator that used requests still running would be using information
    nobody has. The lookup is a searchsorted into the completion order, so the
    window is the last N completions at that instant rather than a fixed span of
    time -- at a rate that varies over an hour those are different things, and
    a count is what a running implementation would keep.
    """
    comp = r.sort_values("end_time")
    ends = comp["end_time"].to_numpy(dtype=float)
    lens = comp["out"].to_numpy(dtype=float)
    roll = pd.Series(lens).rolling(RECENT_N, min_periods=20).median().to_numpy()
    pos = np.searchsorted(ends, r["start_time"].to_numpy(dtype=float), "left") - 1
    out = np.full(len(r), np.nan)
    ok = pos >= 0
    out[ok] = roll[pos[ok]]
    return out


def score(est, actual, j, target):
    """Median relative error, against one of two quantities.

    `final`     |est - L| / L                 how long will this request be
    `remaining` |(est - j) - (L - j)| / (L-j) how much MORE will it produce

    ⚠ THESE ARE NOT THE SAME COMPARISON AND THEY RANK THE ESTIMATORS
    DIFFERENTLY. The policy consumes `remaining` -- it is what the allowance
    divides by and what the outflow term is built from -- while `final` is the
    quantity a reader asks about. The difference bites exactly where a constant
    estimate has been clamped up to j: saying "this request ends now" is a small
    error in the final length once j is large, and a total error in what is
    left, which is the term that decides whether the KV comes back.
    """
    if target == "final":
        return float(np.median(np.abs(est - actual) / actual))
    rem = actual - j
    return float(np.median(np.abs((est - j) - rem) / rem))


def curves(a, stat="median", target="final"):
    """-> rows of (estimator, j, error%, n_alive) for the four estimators."""
    cen = np.median if stat == "median" else np.mean
    allL = a["out"].to_numpy(dtype=float)
    # The constant each memoryless estimator carries: the typical length of the
    # whole population, and of each class, measured once at admission.
    REM0 = {"pooled": float(cen(allL))}
    for c, sub in a.groupby("class"):
        if len(sub) >= MIN_ALIVE:
            REM0[c] = float(cen(sub["out"].to_numpy(dtype=float)))
    rows = []
    for j in GRID_J:
        alive = a[a["out"] > j]
        L = alive["out"].to_numpy(dtype=float)
        if len(L) < MIN_ALIVE:
            continue
        ref_pool = allL[allL > j]

        # global / recent: one number, not a function of j. Clamped to j+1
        # because a request that has produced j tokens cannot end shorter.
        g = float(cen(allL))
        rows.append(("global", j, 100 * score(np.maximum(g, j + 1), L, j, target), len(L)))
        rows.append(("global_raw", j, 100 * score(np.full(len(L), g), L, j, target), len(L)))

        rec = alive["recent"].to_numpy(dtype=float)
        m = np.isfinite(rec)
        if m.sum() >= MIN_ALIVE:
            rows.append(("recent", j, 100 * score(np.maximum(rec[m], j + 1), L[m], j, target),
                         int(m.sum())))
            rows.append(("recent_raw", j, 100 * score(rec[m], L[m], j, target), int(m.sum())))

        # MEMORYLESS: one number for the remaining length, the same at every j.
        #
        # This is what a system that holds a single statistic and no distribution
        # can actually say, and unlike a clamped final-length estimate it is
        # coherent everywhere: an exponential length has E[L-j | L>j] = E[L], so
        # "expect the workload's typical length more, however long it has already
        # run" needs no survival curve at all.
        #
        # ⚠ IT IS EXACTLY `pooled` FROZEN AT j = 0. The pair therefore isolates
        # the value of RE-READING the curve as the request runs from the value of
        # having a distribution at all, which a clamped constant cannot: past the
        # mean, a clamp is not a distribution-free estimator but the degenerate
        # conditional one that puts all the mass on the next token.
        rows.append(("memoryless", j, 100 * score(j + REM0["pooled"], L, j, target),
                     len(L)))
        parts_m, ok_m = [], True
        for c, sub in alive.groupby("class"):
            if c not in REM0:
                ok_m = False
                break
            Lc = sub["out"].to_numpy(dtype=float)
            e_c = j + REM0[c]
            parts_m.append(np.abs(e_c - Lc) / Lc if target == "final"
                           else np.abs((e_c - j) - (Lc - j)) / (Lc - j))
        if ok_m and parts_m:
            rows.append(("memoryless_class", j,
                         100 * float(np.median(np.concatenate(parts_m))), len(L)))

        # survival: j + the typical remaining among survivors at j
        if len(ref_pool) >= MIN_ALIVE:
            rows.append(("pooled", j, 100 * score(j + float(cen(ref_pool - j)), L, j, target),
                         len(L)))
        parts, ok = [], True
        for c, sub in alive.groupby("class"):
            ref_c = allL[(a["class"].to_numpy() == c) & (allL > j)]
            if len(ref_c) < MIN_ALIVE:
                ok = False
                break
            e_c = j + float(cen(ref_c - j))
            Lc = sub["out"].to_numpy(dtype=float)
            if target == "final":
                parts.append(np.abs(e_c - Lc) / Lc)
            else:
                parts.append(np.abs((e_c - j) - (Lc - j)) / (Lc - j))
        if ok and parts:
            rows.append(("class", j, 100 * float(np.median(np.concatenate(parts))),
                         len(L)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--population", choices=sorted(POPULATIONS), default="static")
    ap.add_argument("--target", choices=("final", "remaining"), default="final",
                    help="error against the final length, or against what is left")
    a_ = ap.parse_args()

    a = population(POPULATIONS[a_.population])
    nrec = int(a["recent"].notna().sum())
    print(f"population {a_.population}: {len(a):,} completed requests over "
          f"{a['run'].nunique()} run(s); {len(a)-nrec:,} have no completion "
          f"history and are dropped from the `recent` curve only")
    for c, sub in a.groupby("class"):
        print(f"    {c:14s} {len(sub):7,}  median length {sub['out'].median():6.0f}")

    rows = (curves(a, "median", a_.target)
        + [(k + "_mean", j, e, n) for k, j, e, n in curves(a, "mean", a_.target)])
    t = pd.DataFrame(rows, columns=["estimator", "j", "error_pct", "n_alive"])
    out = os.path.join(HERE, f"length_predictors_{a_.population}_{a_.target}.csv")
    t.to_csv(out, index=False, float_format="%.3f")

    print("\n  median relative error (%), clamped, median statistic")
    piv = t[t["estimator"].isin(list(NAMES) + list(EXTRA))].pivot(index="j", columns="estimator",
                                              values="error_pct")
    print(piv[[k for k in list(NAMES) + list(EXTRA) if k in piv]].to_string(float_format=lambda v: f"{v:6.1f}"))

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(figsize=(COL_W, FIG_H))
        for k in NAMES:
            s = t[t["estimator"] == k]
            if s.empty:
                continue
            ax.plot(s["j"], s["error_pct"], color=COLORS[k], lw=1.0,
                    marker="o", ms=2.6, mec="white", mew=0.4, label=NAMES[k])
        ax.set_xlabel("Tokens already produced", labelpad=1.5)
        ax.set_ylabel(("Median length\nerror (%)" if a_.target == "final"
               else "Median remaining\nerror (%)"), labelpad=1.5)
        ax.set_ylim(0, None)
        ax.grid(axis="both", **GRID)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.01), ncol=2,
                  frameon=False, handlelength=1.3, columnspacing=0.8,
                  borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 0.80))
        save(fig, os.path.join(HERE, f"length_predictors_{a_.population}_{a_.target}.pdf"))
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
