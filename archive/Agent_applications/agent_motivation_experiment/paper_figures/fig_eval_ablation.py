#!/usr/bin/env python3
"""Paper figure (evaluation): what each part of the decision is worth, and when.

  eval_ablation.pdf   3.335 x 2.05 in, one column

**The claim.** FluidServe makes one decision with four ingredients. Turning each
off in turn, on the same workload and the same three arrival rates, shows that
NO SINGLE INGREDIENT DOMINATES and that two of them trade places as load rises:
rejection is worth 18 points at 25 req/s and under a point at 45, while holding
is worth 2 points at 25 and 22 at 45. A figure that reported an average over
rates would say both are worth about ten and explain nothing.

**And the sign changes.** Class preference is worth +2.8 and +3.8 points at 25
and 35 req/s and **-7.1 at 45**, where turning it off is better: the preference
concentrates a class onto fewer instances and those instances reach the KV limit
first (the share of refusals attributed to memory goes 3.4% -> 18.8%). It is
drawn below the axis rather than omitted.

**What the bars are NOT.** They are not additive contributions. Measured at
25 req/s: turning rejection off costs 18.1 points, and turning holding off AS
WELL costs 8.7 -- less, not more. At 35 the same pair is 14.6 alone and 2.9
together. So the only sentence the figure supports is "with the other parts on,
removing this one costs X", and the caption has to say so. Holding without
rejection is actively harmful (-9.4 points at 25 req/s): held requests with no
exit end up placed together, and the deepest engine queue in the experiment,
104 against the control's 12, is that arm's.

**Scoring.** Every arrival is the denominator, and a rejected request and a
request unfinished when the load window closed both count as misses. This is the
metric EXP-78 fixed before running, for the reason that the two standard
denominators drop unfinished requests -- which is exactly what an arm with
rejection turned off produces instead of rejections. Computed here by calling
`all_arrivals_attainment.one_run`, not copied from any table.

**Data.** Two repeats per cell. Bars are the mean over repeats and the whisker is
min..max of the DIFFERENCE, propagated as the worst case over the four pairings
of the two arms' repeats.

  control, rejection, holding, both      EXP-73 + EXP-78, one session each
  per-instance prefill (prefix)          EXP-69 at 35/45, EXP-78 at 25

**CAVEAT for the caption.** The prefix row's control is a different session from
the other rows' control -- EXP-69's ablation runs pair with EXP-68s, and the
subtraction is done within each session. Every arm is on the post-2026-08-08
workload.

Source runs are printed by the script and listed in
paper_evaluation_2026-08/10_figures.md.
"""
import collections
import glob
import itertools
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
RATES = [25, 35, 45]
RPM = {25: 1500, 35: 2100, 45: 2700}

# (label, colour, {rate: (treatment glob, control glob)}). The control is named
# per row because the prefix row's ablation was run in a different session and
# must be subtracted against that session's own control.
CTRL73 = "results/*exp73r[12]_fspfx_m1_rpm_%d"
ROWS = [
    ("rejection", "#d62728",
     {r: ("results/*exp78r[12]_fsnoshed_m1_rpm_%d" % RPM[r], CTRL73 % RPM[r])
      for r in RATES}),
    ("holding", "#1f77b4",
     {r: ("results/*exp73r[12]_fspnopend_m1_rpm_%d" % RPM[r], CTRL73 % RPM[r])
      for r in RATES}),
    ("class preference", "#2ca02c",
     {r: ("results/*exp73r[12]_fspnoaff_m1_rpm_%d" % RPM[r], CTRL73 % RPM[r])
      for r in RATES}),
    ("per-instance prefill", "#ff7f0e",
     {25: ("results/*exp78r[12]_fluidserve_m1_rpm_1500", CTRL73 % 1500),
      35: ("results/*exp69r[12]_fluidserve_m1_rpm_2100",
           "results/*exp68sr[12]_fspfx_m1_rpm_2100"),
      45: ("results/*exp69r[12]_fluidserve_m1_rpm_2700",
           "results/*exp68sr[12]_fspfx_m1_rpm_2700")}),
]


def score(pattern):
    """all-arrivals attainment for every run matching pattern."""
    vals = []
    for d in sorted(glob.glob(os.path.join(ROOT, pattern))):
        if "PRERUN" in d:
            continue
        r = one_run(d)
        if r is not None:
            vals.append((os.path.basename(d), r["all_arrivals"]))
    return vals


def main():
    lost = {}          # (row, rate) -> (mean loss, min loss, max loss, n_t, n_c)
    print("all-arrivals attainment per run\n")
    cache = {}
    for label, _, spec in ROWS:
        for rate in RATES:
            tpat, cpat = spec[rate]
            for pat in (tpat, cpat):
                if pat not in cache:
                    cache[pat] = score(pat)
            t, c = cache[tpat], cache[cpat]
            if not t or not c:
                print("  ! %s @ %d: treatment %d runs, control %d runs -- skipped"
                      % (label, rate, len(t), len(c)))
                continue
            diffs = [cv - tv for (_, cv), (_, tv) in itertools.product(c, t)]
            lost[(label, rate)] = (
                np.mean([cv for _, cv in c]) - np.mean([tv for _, tv in t]),
                min(diffs), max(diffs), len(t), len(c))
            print("  %-22s @%2d  control %s  treated %s   loss %+5.1f"
                  % (label, rate,
                     "/".join("%.1f" % v for _, v in c),
                     "/".join("%.1f" % v for _, v in t),
                     lost[(label, rate)][0]))

    plt.rcParams.update(ps.STYLE)
    fig, ax = plt.subplots(figsize=(ps.COL_W, 2.05))
    w = 0.2
    xs = np.arange(len(RATES))
    for i, (label, colour, _) in enumerate(ROWS):
        off = (i - (len(ROWS) - 1) / 2.0) * w
        m = [lost[(label, r)][0] if (label, r) in lost else np.nan for r in RATES]
        lo = [m[j] - lost[(label, r)][1] if (label, r) in lost else 0
              for j, r in enumerate(RATES)]
        hi = [lost[(label, r)][2] - m[j] if (label, r) in lost else 0
              for j, r in enumerate(RATES)]
        ax.bar(xs + off, m, width=w, color=colour, label=label,
               edgecolor="white", linewidth=0.3)
        ax.errorbar(xs + off, m, yerr=[lo, hi], fmt="none", ecolor="#333333",
                    elinewidth=0.6, capsize=1.4)
    ax.axhline(0, color="black", lw=0.6)
    ax.set_xticks(xs)
    ax.set_xticklabels(["%d" % r for r in RATES])
    ax.set_xlabel("offered load (req/s)")
    ax.set_ylabel("attainment lost when\nturned off (points)")
    ax.grid(axis="y", **ps.GRID)
    ax.legend(loc="upper left", ncol=2, handlelength=1.1, columnspacing=0.6,
              borderpad=0.25, labelspacing=0.25, handletextpad=0.4, fontsize=7)
    ax.set_ylim(-12, 32)
    fig.tight_layout(rect=(0, 0, 1, 1))
    ps.save(fig, os.path.join(HERE, "eval_ablation.pdf"))

    print("\npoints lost when turned off (mean over repeats; whisker = min..max "
          "of the pairwise difference)")
    print("element".ljust(24) + "".join(("%d req/s" % r).rjust(12) for r in RATES))
    for label, _, _ in ROWS:
        print(label.ljust(24) + "".join(
            ("%+.1f" % lost[(label, r)][0]).rjust(12)
            if (label, r) in lost else "-".rjust(12) for r in RATES))


if __name__ == "__main__":
    main()
