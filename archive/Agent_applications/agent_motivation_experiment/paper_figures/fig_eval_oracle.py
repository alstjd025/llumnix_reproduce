#!/usr/bin/env python3
"""Paper figure (evaluation): exact per-request output lengths buy nothing here.

  eval_oracle.pdf   3.335 x 1.75 in, one column

**The claim.** FluidServe conditions its length prediction on the request's
class, not on the request. Systems it is compared against predict per request --
Scorpio classifies into 100 bins, AdaGen regresses with a fine-tuned DistilBERT
-- so the obvious objection is that the comparison gave us less information than
they use. This measures the ceiling on that objection by giving the policy the
TRUE output length of every request, taken from earlier runs of the same
workload, and finds no difference: -0.3, -0.2, +0.6, -2.0 points at 25, 35, 45
and 55 req/s, against a pre-registered confirmation band of 3 points and a
refutation band of 5. The sign flips twice.

**Why an oracle rather than a better predictor.** With a predictor, a null result
is ambiguous -- the predictor might simply be bad. Handing the policy the answer
removes that: no predictor can beat the oracle, so no predictor can win on this
axis in this setting.

**Scoring.** Every arrival is the denominator, rejections and unfinished requests
both counted as misses, fixed before the run.

**Data.** EXP-64, 16 conditions = 4 arrival rates x 2 arms x 2 repeats, on the
post-2026-08-08 workload. The oracle table covers 99.2% of requests at the
decision rate (chat 99.8, deep research 96.3, agent 98.5), identical for both
arms, and the workload aborts rather than falling back if the table cannot be
read -- so "the treatment silently became the control" is excluded by
construction.

**CAVEATS for the caption.**

1. The scope is this workload (chat is 77.0% of requests) and this policy's
   feasibility test. A workload with wider within-class length spread, or a
   policy whose decision uses the length more sharply, has to be re-measured.
2. The treated arm's repeat spread is LARGER than the control's at three of the
   four rates (3.7 vs 1.8, 3.1 vs 1.1, 4.7 vs 0.9). Exact lengths made the
   outcome noisier, which is an observation on two repeats and not a
   measurement.
3. Two repeats. The whisker is min..max, not an interval estimate.

Source: EXP-64, scored with all_arrivals_attainment.one_run.
"""
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
RATES = [25, 35, 45, 55]
RPM = {25: 1500, 35: 2100, 45: 2700, 55: 3300}
ARMS = [("class distribution", "fspfx", "#1f77b4"),
        ("exact length (oracle)", "fsoracle", "#ff7f0e")]


def score(arm, rpm):
    out = []
    for d in sorted(glob.glob(os.path.join(
            ROOT, "results/*exp64r[12]_%s_m1_rpm_%d" % (arm, rpm)))):
        if "PRERUN" in d:
            continue
        r = one_run(d)
        if r is not None:
            out.append(r["all_arrivals"])
    return out


def main():
    vals = {(a, r): score(a, RPM[r]) for _, a, _ in ARMS for r in RATES}
    for k, v in sorted(vals.items()):
        print("  %-10s @%2d  %s" % (k[0], k[1], "/".join("%.1f" % x for x in v)))

    plt.rcParams.update(ps.STYLE)
    fig, ax = plt.subplots(figsize=(ps.COL_W, 1.75))
    xs = np.arange(len(RATES))
    w = 0.34
    for i, (label, arm, colour) in enumerate(ARMS):
        m = [np.mean(vals[(arm, r)]) for r in RATES]
        lo = [m[j] - min(vals[(arm, r)]) for j, r in enumerate(RATES)]
        hi = [max(vals[(arm, r)]) - m[j] for j, r in enumerate(RATES)]
        ax.bar(xs + (i - 0.5) * w, m, width=w, color=colour, label=label,
               edgecolor="white", linewidth=0.3)
        ax.errorbar(xs + (i - 0.5) * w, m, yerr=[lo, hi], fmt="none",
                    ecolor="#333333", elinewidth=0.6, capsize=1.6)
    for j, r in enumerate(RATES):
        d = np.mean(vals[("fsoracle", r)]) - np.mean(vals[("fspfx", r)])
        ax.text(xs[j], max(np.mean(vals[("fspfx", r)]),
                           np.mean(vals[("fsoracle", r)])) + 2.5,
                "%+.1f" % d, ha="center", va="bottom", fontsize=7)
    ax.set_xticks(xs)
    ax.set_xticklabels(["%d" % r for r in RATES])
    ax.set_xlabel("offered load (req/s)")
    ax.set_ylabel("attainment (%)\nall arrivals")
    ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.grid(axis="y", **ps.GRID)
    ax.legend(loc="lower left", handlelength=1.1, borderpad=0.25,
              labelspacing=0.25, handletextpad=0.4, fontsize=7)
    fig.tight_layout(rect=(0, 0, 1, 1))
    ps.save(fig, os.path.join(HERE, "eval_oracle.pdf"))

    print("\ndifference, oracle - class distribution (points, all arrivals)")
    for r in RATES:
        print("  %2d req/s  %+.1f   (treated spread %.1f, control spread %.1f)"
              % (r, np.mean(vals[("fsoracle", r)]) - np.mean(vals[("fspfx", r)]),
                 max(vals[("fsoracle", r)]) - min(vals[("fsoracle", r)]),
                 max(vals[("fspfx", r)]) - min(vals[("fspfx", r)])))


if __name__ == "__main__":
    main()
