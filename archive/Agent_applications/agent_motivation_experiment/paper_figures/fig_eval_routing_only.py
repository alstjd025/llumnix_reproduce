#!/usr/bin/env python3
"""Paper figure (evaluation): three policies that refuse nothing, and only the
destination rule differs.

  eval_routing_only.pdf   3.335 x 2.00 in, one column

**The claim.** The first thing a reader asks about a policy that rejects is
whether its advantage is the rejection. This removes rejection from the
comparison entirely. Three arms, all of which place every request that arrives:

  FluidServe, routing only   rejection AND holding turned off, so what is left
                             is choosing a destination by the time and memory
                             model. `set_scheduler_profiling.py`'s own header
                             calls this combination pure routing.
  vLLM router                the PyPI `vllm-router` package's default
                             `cache_aware` policy, a fork of the SGLang model
                             gateway. NOT vllm-project/production-stack.
  PolyServe                  a static partition of the fleet by class.

Their measured rejection rate is 0.0% at every one of the eight rates, so the
only thing that differs is where a request is sent.

**And what admission adds on top**, drawn as a reference line rather than a
fourth competitor: the deployed FluidServe, which does reject. The gap between
it and the routing-only arm is what holding and rejection are worth, and it is
not constant -- the same measurement in EXP-78 puts it at 18.1 points at
25 req/s and 0.8 at 45.

**Scoring.** Every arrival is the denominator; a rejection and a request still
unfinished when the load window closed both count as misses. That choice is
forced here: an arm with rejection off produces unfinished requests instead of
rejections, and the two standard denominators drop unfinished requests from
both sides, which would flatter exactly the arms under test.

**Data.**

  FluidServe routing-only   EXP-88, eight rates x two repeats, one session
  vLLM router, PolyServe    EXP-86, from paper_experiment/static_sweep_clean_2026-08
  FluidServe deployed       EXP-82, same pinned set

All of them ran with the gateway at `GOMAXPROCS=16`.

**CAVEATS for the caption.**

1. Say the rejection rates. "Nobody rejects" is the whole basis of the
   comparison and it is a measured fact, not a configuration claim.
2. This does NOT say routing matters more than admission control. The same
   experiment family measured that removing rejection costs 18.1 points at
   25 req/s, and that holding without rejection is actively harmful.
3. EXP-88 re-measured the three rates EXP-78 already held so that the session
   and binary difference could be checked rather than assumed; the check is
   printed by this script.
4. Two repeats. Bands are min..max.

Sources: EXP-88, EXP-86, EXP-82.
"""
import collections
import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "analysis_scripts",
                                                "request_level")))
import paper_style as ps                       # noqa: E402
from all_arrivals_attainment import one_run    # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, ".."))
RATES = [10, 15, 20, 25, 35, 45, 55, 70]
RPM = {r: r * 60 for r in RATES}

ARMS = [
    ("FluidServe, routing only", "#1f77b4", "s", "-",
     "results/*exp88r[12]_fsroute_m1_rpm_%d"),
    ("vLLM router", ps.ARM_COLOR["vllmrouter"], "h", "-",
     "results/*exp86r[12]_vllmcache_m1_rpm_%d"),
    ("PolyServe", ps.ARM_COLOR["polyserve"], "o", "-",
     "results/*exp86r[12]_polyserve_m1_rpm_%d"),
]
REFERENCE = ("FluidServe, deployed", "#9ecae1", "s", "--",
             "results/*exp82r[12]_fspfx_m1_rpm_%d")
EXP78 = {25: "results/*exp78r[12]_fsroute_m1_rpm_1500",
         35: "results/*exp78r[12]_fsroute_m1_rpm_2100",
         45: "results/*exp78r[12]_fsroute_m1_rpm_2700"}


def score(pattern):
    out = []
    for d in sorted(glob.glob(os.path.join(ROOT, pattern))):
        if "PRERUN" in d:
            continue
        r = one_run(d)
        if r is not None:
            out.append((r["all_arrivals"], r["rejected_pct"]))
    return out


def main():
    series = collections.OrderedDict()
    for label, colour, marker, ls, pat in ARMS + [REFERENCE]:
        pts = {r: score(pat % RPM[r]) for r in RATES}
        series[label] = (colour, marker, ls, pts)
        got = sum(len(v) for v in pts.values())
        print("%-26s %2d runs   %s" % (
            label, got,
            "  ".join("%g:%s" % (r, "/".join("%.1f" % a for a, _ in pts[r]) or "-")
                      for r in RATES)))
        rej = [j for v in pts.values() for _, j in v]
        if rej:
            print("%-26s rejection %.1f-%.1f%%" % ("", min(rej), max(rej)))

    missing = [r for r in RATES if not series["FluidServe, routing only"][3][r]]
    if missing:
        sys.exit("EXP-88 has no runs at %s -- not drawing a partial figure"
                 % ", ".join("%g" % r for r in missing))

    print("\nsession check: EXP-88 against EXP-78 at the three shared rates")
    for r in sorted(EXP78):
        old = [a for a, _ in score(EXP78[r])]
        new = [a for a, _ in series["FluidServe, routing only"][3][r]]
        if old and new:
            print("  %2d req/s  EXP-78 %s   EXP-88 %s   difference of means %+.1f"
                  % (r, "/".join("%.1f" % v for v in old),
                     "/".join("%.1f" % v for v in new),
                     np.mean(new) - np.mean(old)))

    plt.rcParams.update(ps.STYLE)
    fig, ax = plt.subplots(figsize=(ps.COL_W, 2.00))
    x = np.arange(len(RATES))
    for label, (colour, marker, ls, pts) in series.items():
        m = [np.mean([a for a, _ in pts[r]]) if pts[r] else np.nan for r in RATES]
        lo = [min([a for a, _ in pts[r]]) if pts[r] else np.nan for r in RATES]
        hi = [max([a for a, _ in pts[r]]) if pts[r] else np.nan for r in RATES]
        z = 2 if label.endswith("deployed") else 4
        ax.plot(x, m, color=colour, marker=marker, ls=ls, label=label,
                lw=1.4 if z == 4 else 1.0, ms=3.2, zorder=z)
        ax.fill_between(x, lo, hi, color=colour, alpha=0.16, lw=0, zorder=z - 1)
    ax.set_xticks(x)
    ax.set_xticklabels(["%g" % r for r in RATES])
    ax.set_xlabel("offered load (req/s)")
    ax.set_ylabel("attainment (%)\nall arrivals")
    ax.set_ylim(0, 108)
    ax.grid(axis="y", **ps.GRID)
    ax.legend(loc="lower left", handlelength=1.4, borderpad=0.25,
              labelspacing=0.25, handletextpad=0.5, fontsize=7)
    fig.tight_layout(rect=(0, 0, 1, 1))
    ps.save(fig, os.path.join(HERE, "eval_routing_only.pdf"))

    print("\nall-arrivals attainment (mean of two repeats)")
    print("req/s".rjust(6) + "".join(l[:14].rjust(16) for l in series))
    for j, r in enumerate(RATES):
        print(("%g" % r).rjust(6) + "".join(
            ("%.1f" % np.mean([a for a, _ in pts[r]])).rjust(16)
            if pts[r] else "-".rjust(16) for _, _, _, pts in series.values()))


if __name__ == "__main__":
    main()
