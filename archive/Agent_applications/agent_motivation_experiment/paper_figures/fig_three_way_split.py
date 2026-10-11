#!/usr/bin/env python3
"""Paper figure: what happened to the arrivals, split three ways.

  three_way_split.pdf   3.335 x 1.75 in, single column, width=\\columnwidth

Each bar is 100% of the requests that arrived and whose outcome is known. It is
divided into the three things that can happen to one: it finished inside its own
latency rule, it was accepted and missed anyway, or it was refused. The first
segment is offered attainment, so this figure reconciles with every attainment
number in the paper.

WHY THIS FIGURE EXISTS. Attainment beside a rejection rate leaves the reader to
do the subtraction, and the subtraction is where the two failure modes separate.
PolyServe refuses nothing and leaves 88.8% of arrivals accepted-and-missed;
Llumnix SLO refuses 31.3% and still leaves 31.1% accepted-and-missed, so its
rejection is not cutting the work it cannot keep; FluidServe and llm-d both hold
that middle segment at 3 to 5% and differ in how much they refuse.

WHAT THE CAPTION HAS TO CARRY.

  1. The bars exclude requests still in flight when the run ended, because their
     outcome is unknown and `attain()` drops them from both denominators. That
     share is 0.1% for three arms on the hour trace and 7.4% for PolyServe, and
     35.9% for PolyServe at static 45 req/s. Those requests are the ones behind
     the deep queue, so excluding them flatters that arm.
  2. Hour trace, two repeats per arm; static 45 req/s, two repeats for FluidServe
     and llm-d and one for the other two.
  3. Llumnix SLO and llm-d are given the `m1f` workload configuration, because
     neither can express an end-to-end budget. Scoring is end-to-end 30 s for the
     agent class in every arm.

    python3 paper_figures/fig_three_way_split.py
"""
import glob
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

from paper_style import COL_W, STYLE, save  # noqa: E402
from three_way_split import split  # noqa: E402

# Order is worst-to-best on the middle segment so the figure reads left to right
# as "accepts everything and misses" to "refuses instead of missing".
ARMS = [
    ("polyserve", "PolyServe", ["results/*exp71*_polyserve_full*"],
     ["results/*exp72r1_polyserve_m1_rpm_2700"]),
    ("slo", "Llumnix SLO", ["results/*exp71*_slo_full*"],
     ["results/*exp72r1_slo_m1f_rpm_2700"]),
    ("llmdslo", "llm-d", ["results/*exp71*_llmdslo_full*"],
     ["results/*exp68r*_llmdslo_m1f_rpm_2700", "results/*exp68s*_llmdslo_m1f_rpm_2700"]),
    ("fspfx", "FluidServe", ["results/*exp71*_fspfx_full*"],
     ["results/*exp68r*_fspfx_m1_rpm_2700", "results/*exp68s*_fspfx_m1_rpm_2700",
      "results/*exp69*_fspfx_m1_rpm_2700"]),
]
# Green for what worked, amber for work the fleet did and threw away, grey for
# work it declined to do. The amber is the one the figure is about, so it is the
# only saturated colour of the three.
SEG = [("met", "Met its rule", "#4c9a5a"),
       ("missed", "Admitted, missed", "#e8912a"),
       ("rej", "Rejected", "#b8b8b8")]
FIG_H = 1.75


def mean_split(globs):
    vals, seen = [], set()
    for g in globs:
        for d in sorted(glob.glob(g)):
            if d in seen:
                continue
            seen.add(d)
            v = split(d)
            if v:
                vals.append(v)
    if not vals:
        return None
    return {k: float(np.mean([v[k] for v in vals])) for k in
            ("met", "missed", "rej", "cutoff")}, len(vals)


def main():
    os.chdir(ROOT)
    panels = []
    for idx, title in ((2, "Hour trace"), (3, "Static 45 req/s")):
        rows = []
        for key, lab, hour_g, static_g in ARMS:
            got = mean_split(hour_g if idx == 2 else static_g)
            if got is None:
                print(f"no runs for {key} in {title}", file=sys.stderr)
                continue
            v, n = got
            rows.append((lab, v, n))
        panels.append((title, rows))
        print(f"{title}: " + ", ".join(
            f"{lab} {v['met']:.1f}/{v['missed']:.1f}/{v['rej']:.1f} "
            f"(cutoff {v['cutoff']:.1f}%, n={n})" for lab, v, n in rows))

    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, 2, figsize=(COL_W, FIG_H), sharey=True)
        for a, (title, rows) in zip(ax, panels):
            y = np.arange(len(rows))
            left = np.zeros(len(rows))
            for k, _, col in SEG:
                w = np.array([v[k] for _, v, _ in rows])
                a.barh(y, w, left=left, color=col, height=0.62,
                       edgecolor="white", lw=0.4)
                left += w
            a.set_yticks(y)
            a.set_yticklabels([lab for lab, _, _ in rows])
            a.set_xlim(0, 100)
            a.set_xticks([0, 50, 100])
            a.set_xlabel(f"Arrivals (%)\n{title}", labelpad=1.5,
                         linespacing=1.6)
            a.invert_yaxis()
            for s in ("top", "right"):
                a.spines[s].set_visible(True)

        handles = [plt.Rectangle((0, 0), 1, 1, color=c) for _, _, c in SEG]
        fig.legend(handles, [lab for _, lab, _ in SEG], loc="upper center",
                   bbox_to_anchor=(0.5, 1.03), ncol=2, handlelength=1.0,
                   columnspacing=0.8, handletextpad=0.5)
        fig.tight_layout(rect=(0, 0, 1, 0.83))
    save(fig, os.path.join(HERE, "three_way_split.pdf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
