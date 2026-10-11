#!/usr/bin/env python3
"""Paper figure: the hour — FluidServe against Llumnix SLO on a moving load.

One PDF, two side-by-side panels against time: (left) SLO attainment on the
ADMITTED denominator, (right) output token goodput. Drawn at 7.0 in, the USENIX
full text width, so it goes in a `figure*` at `width=\\textwidth` with no
rescaling. See `paper_style.py` and `README.md` in this directory.

  exp50_hour_attainment_goodput.pdf

This is panels D and G of `results/aggregate_analysis/exp50/hour_timeline.png`
reduced to the two arms that are being compared, drawn at paper size. The
windowing and both metrics are taken from
`analysis_scripts/request_level/exp41_dynamic_timeline.py` rather than
reimplemented: a 90 s window stepped every 30 s, anchored on ARRIVAL, so a point
at minute t reads "of the requests that arrived around t, this is what happened
to them". A request arriving at t and finishing at t+30 s is scored at t. A
window holding fewer than 30 requests is dropped rather than plotted as a noisy
point.

THE ADMITTED DENOMINATOR IS THE ONE ASKED FOR, AND IT IS THE FLATTERING ONE FOR
BOTH ARMS. Rejections leave the population entirely, so this panel is "how well
was the work the policy chose to do actually done", not "what fraction of the
offered load was served". Both arms reject heavily on this trace and neither
rejection rate is on the figure, so THE CAPTION HAS TO GIVE THEM; the whole-hour
figures are in README.md. The goodput panel is the partial guard, because
rejected work produces no tokens and so cannot inflate it.

TWO ARMS, ONE RUN EACH, FROM DIFFERENT SESSIONS. Nothing here has a repeat, so
no band on either line is a confidence interval — every wiggle is one
realisation. The two runs are also from different experiments a day apart. Both
saw the same trace, which panel A of the source figure verifies by drawing the
offered rate per arm and finding one curve.

    python3 paper_figures/fig_exp50_hour.py
"""
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

from paper_style import TEXT_W, STYLE, GRID, ARM_COLOR, kfmt, save  # noqa: E402
from exp22_fluidserve import load_run, attain  # noqa: E402
from exp41_dynamic_timeline import windows, WIN  # noqa: E402

# label, run directory, colour, line style. The FluidServe arm is the corrected
# length profile (EXP-50 part 2); the baseline is the Llumnix SLO arm of EXP-45,
# which is the most recent hour of that policy on this trace.
SERIES = [
    ("FluidServe", "results/260801_1808_exp50p2r1_fluidserve_full",
     ARM_COLOR["fluidserve"], "-"),
    ("Llumnix SLO", "results/260731_2203_exp45r1_slo_full",
     ARM_COLOR["slo"], "--"),
]
MIN_IN_WINDOW = 30      # same floor as the source figure
MIX_BOUNDARIES = [15, 30, 45]   # the trace steps mix every 15 minutes
FIG_H = 1.60


def series(run):
    """Per-window admitted attainment and token goodput for one run."""
    r = load_run(run)
    if r is None or r.empty:
        return None
    dur = r["rel"].max()
    x, adm, gp = [], [], []
    for t, g in windows(r, dur):
        if len(g) < MIN_IN_WINDOW:
            continue
        x.append(t)
        adm.append(attain(g, "violate_served"))
        # Goodput is judged on the OFFERED column even in this figure: a
        # rejected request produced no tokens, so it contributes none. That is
        # a statement about tokens, not a choice of denominator.
        ok = g[(~g["violate_offered"]) & (~g["cutoff"])]
        gp.append(pd.to_numeric(ok.get("output_tokens"), errors="coerce")
                  .fillna(0).sum() / WIN)
    return np.array(x), np.array(adm), np.array(gp), dur / 60.0, r


def main():
    data, dur = [], 0.0
    for lab, run, c, ls in SERIES:
        s = series(os.path.join(ROOT, run))
        if s is None:
            print(f"no rows for {run}", file=sys.stderr)
            return 1
        x, adm, gp, d, r = s
        rej = 100.0 * r["rejected"].mean()
        print(f"{lab:12s} {len(x):3d} windows, {d:.1f} min, "
              f"whole-hour admitted {attain(r, 'violate_served'):5.1f}%, "
              f"offered {attain(r, 'violate_offered'):5.1f}%, rejected {rej:4.1f}%")
        data.append((lab, x, adm, gp, c, ls))
        dur = max(dur, d)

    with plt.rc_context(STYLE):
        fig, (ax_a, ax_g) = plt.subplots(1, 2, figsize=(TEXT_W, FIG_H))

        handles, labels = [], []
        for lab, x, adm, gp, c, ls in data:
            # No markers: 118 points per line at 30 s steps would draw as a
            # solid band and hide the shape the figure exists to show.
            h, = ax_a.plot(x, adm, color=c, ls=ls, lw=1.1)
            ax_g.plot(x, gp, color=c, ls=ls, lw=1.1)
            handles.append(h)
            labels.append(lab)

        ax_a.set_ylabel("SLO attainment (%)")
        ax_a.set_ylim(0, 105)
        ax_a.set_yticks([0, 25, 50, 75, 100])
        ax_g.set_ylabel("Goodput token (t/s)")
        ax_g.set_ylim(0, None)
        ax_g.yaxis.set_major_formatter(kfmt())

        for ax in (ax_a, ax_g):
            ax.set_xlabel("Time (minutes)")
            ax.set_xlim(0, dur)
            ax.set_xticks(range(0, int(dur) + 1, 10))
            ax.grid(axis="both", **GRID)
            ax.set_axisbelow(True)
            # The trace steps its class mix every 15 minutes, and both curves
            # move at those instants, so the guides are what makes the shape
            # attributable to the workload rather than to the policy. They are
            # NOT labelled on the figure any more, so the caption has to say
            # what they mark: m1, m2, m3, m1, fifteen minutes each.
            for m in MIX_BOUNDARIES:
                ax.axvline(m, color="#999999", lw=0.5, ls=":", zorder=0)
        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 0.885), frameon=False,
                   columnspacing=1.4, handlelength=2.0, handletextpad=0.5,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 0.875), w_pad=1.6, pad=0.35)
        save(fig, os.path.join(HERE, "exp50_hour_attainment_goodput.pdf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
