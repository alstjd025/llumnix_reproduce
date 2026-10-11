#!/usr/bin/env python3
"""Paper figure: EXP-54 — three control planes on the hour-long dynamic trace.

Two PDFs, each two side-by-side panels against time — (left) SLO attainment,
(right) output token goodput — drawn at 7.0 in, the USENIX full text width, so
they go in a `figure*` at `width=\\textwidth` with no rescaling.

  exp54_hour_offered.pdf     attainment on the offered denominator
  exp54_hour_admitted.pdf    attainment on the admitted denominator

USE THE OFFERED VERSION UNLESS THE TEXT ALREADY GIVES THE REJECTION RATES. The
three arms reject at completely different rates over this hour — FluidServe
27.0%, Llumnix SLO 42.1%, PolyServe 0.0% — so the admitted denominator flatters
them by completely different amounts and reorders them. Whole-run figures:

              rejected   offered   admitted   goodput tok/s
  FluidServe     27.0%      70.0       95.9          18,014
  Llumnix SLO    42.1%      38.7       67.0          11,451
  PolyServe       0.0%      18.9       18.9           5,763

PolyServe's two attainment numbers are the same because it never rejects; the
other two gain 26 and 28 points from having refused work.

POLYSERVE IS DRAWN FROM EXP-57 AND THE OTHER TWO FROM EXP-54. That arm's tier
length table understated deep research by 3.58x and fed both its admission limit
and its repartitioner; it was corrected on 2026-08-05 and the hour trace re-run
for that arm alone. The correction is worth 5.0 points of attainment and 2,565
tokens/s of goodput over the hour (13.9 -> 18.9 and 3,198 -> 5,763), so figures
drawn before it, and the EXP-54 write-up's numbers for this arm, are superseded
rather than merely imprecise. The superseded runs are still on disk under
`exp54r1_polyserve_full` and `exp54r2_polyserve_full`.

The goodput panel is the guard against all of this: rejected work produces no
tokens, so no denominator choice can inflate it.

WHAT THE HOUR IS. `dyn60_short_m123`, mean offered 50.1 req/s, about 179,000
requests. Arrival rate follows an Azure production trace; the class mix steps
m1 -> m2 -> m3 -> m1 at 15-minute boundaries, marked with grey guides. The
guides are NOT labelled on the figure, so the caption has to say what they are.

THE LOAD-BALANCE ARM IS ABSENT, AND NOT BECAUSE IT LOST. `exp54r1_loadbalance`
was stopped at 87% of its tasks: it rejects nothing, so from minute 40 all four
engines saturated, queued requests had their streams cut, and the client began
failing to obtain source ports — 62,114 `[Errno 99] Cannot assign requested
address`, against zero in every other arm. Its last twenty minutes measure the
load generator, not the policy. Reporting it as a fourth curve would be
reporting a client defect as a result. Say it is excluded and why.

RUN-BOUNDARY CUTOFFS, AND WHY THE X AXIS STOPS SHORT OF 60 MINUTES. A request
still in flight when the run ends has an unknown outcome, so `attain()` removes
it from both denominators rather than counting it as a violation. That rule is
right in general and wrong at the end of a backlogged run: the requests still in
flight there are precisely the slow ones, so removing them removes the failures.
PolyServe's final window has 3,681 arrivals of which 2,441 — 66.3% — never
finished, and its attainment reads 22.6% there against 8.9% ninety seconds
earlier. Nothing recovered; the population changed.

Windows in which more than MAX_CUTOFF of arrivals were still in flight are
therefore dropped, and all arms are cut at the same time so the panels stay
comparable. FluidServe never exceeds 4.5% and Llumnix SLO 5.8%, because both
reject and neither builds a backlog; PolyServe reaches 66.3%. The trim is
reported when the script runs and lands at 58.2 of 59.8 minutes.

ONE RUN PER ARM, AND A REPEAT NOW EXISTS FOR ALL THREE. When this was first
drawn only FluidServe had a second run; `exp54r2_fluidserve_full`,
`exp54r2_slo_full` and `exp57r2_polyserve_full` now complete the set. The figure
still draws repeat 1 of each arm, so no feature on any line is noise-bounded —
adding the second repeat as a band per window is a change to what the figure
shows, not a redraw, and has not been made. Say in the caption that these are
single runs.

    python3 paper_figures/fig_exp54_hour.py
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

# label, run directory, line style. Colours come from paper_style so a colour
# means the same policy here as in the static-sweep figures.
SERIES = [
    ("FluidServe", "results/260803_1751_exp54r1_fluidserve_full",
     ARM_COLOR["fluidserve"], "-"),
    # EXP-57, not EXP-54. This arm's tier length table understated deep research
    # by 3.58x; it was corrected on 2026-08-05 and the hour trace re-run for
    # this arm alone. `results/260803_1905_exp54r1_polyserve_full` and
    # `260804_0140_exp54r2_polyserve_full` are the superseded runs and are still
    # on disk. The other two arms do not read that flag and keep EXP-54.
    ("PolyServe", "results/260805_0006_exp57r1_polyserve_full",
     ARM_COLOR["polyserve"], "-."),
    ("Llumnix SLO", "results/260803_2117_exp54r1_slo_full",
     ARM_COLOR["slo"], "--"),
]
MIN_IN_WINDOW = 30              # same floor as the source timeline figure
MIX_BOUNDARIES = [15, 30, 45]   # the trace steps its mix every 15 minutes
FIG_H = 1.60
# Drop trailing windows in which more than this share of the arrivals were still
# in flight when the run ended. See the RUN-BOUNDARY note in the docstring: those
# requests leave the denominator, and in a backlogged arm they are exactly the
# slow ones, so attainment reads high for a reason that is not the policy.
MAX_CUTOFF = 0.20


def series(run):
    """Per-window attainment on both denominators, and token goodput."""
    r = load_run(run)
    if r is None or r.empty:
        return None
    dur = r["rel"].max()
    x, adm, off, gp, cut = [], [], [], [], []
    for t, g in windows(r, dur):
        if len(g) < MIN_IN_WINDOW:
            continue
        x.append(t)
        cut.append(float(g["cutoff"].mean()))
        adm.append(attain(g, "violate_served"))
        off.append(attain(g, "violate_offered"))
        # Goodput is judged on the offered column in both versions: a rejected
        # request produced no tokens, so it contributes none. That is a fact
        # about tokens, not a choice of denominator.
        ok = g[(~g["violate_offered"]) & (~g["cutoff"])]
        gp.append(pd.to_numeric(ok.get("output_tokens"), errors="coerce")
                  .fillna(0).sum() / WIN)
    return (np.array(x), np.array(adm), np.array(off), np.array(gp),
            np.array(cut), r)


def build(data, dur, out, show_offered):
    with plt.rc_context(STYLE):
        fig, (ax_a, ax_g) = plt.subplots(1, 2, figsize=(TEXT_W, FIG_H))

        handles, labels = [], []
        for lab, x, adm, off, gp, c, ls in data:
            y = off if show_offered else adm
            # No markers: ~119 points per line at 30 s steps would draw as a
            # solid band and hide the shape the figure exists to show.
            h, = ax_a.plot(x, y, color=c, ls=ls, lw=1.1)
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
            for m in MIX_BOUNDARIES:
                ax.axvline(m, color="#999999", lw=0.5, ls=":", zorder=0)

        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 0.885), frameon=False,
                   columnspacing=1.4, handlelength=2.0, handletextpad=0.5,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 0.875), w_pad=1.6, pad=0.35)
        save(fig, out)


def main():
    raw = []
    for lab, run, c, ls in SERIES:
        s = series(os.path.join(ROOT, run))
        if s is None:
            print(f"no rows for {run}", file=sys.stderr)
            return 1
        x, adm, off, gp, cut, r = s
        print(f"{lab:12s} {len(x):3d} windows, "
              f"whole-hour admitted {attain(r, 'violate_served'):5.1f}%, "
              f"offered {attain(r, 'violate_offered'):5.1f}%, "
              f"rejected {100.0 * r['rejected'].mean():4.1f}%, "
              f"max cutoff share {100.0 * cut.max():4.1f}%")
        raw.append((lab, x, adm, off, gp, cut, c, ls))

    # One cut point for every arm, so the panels stay directly comparable: the
    # earliest time at which ANY arm's window crosses MAX_CUTOFF. Giving the
    # arms different x extents would itself invite a wrong reading.
    ends = [x[cut <= MAX_CUTOFF].max() if (cut <= MAX_CUTOFF).any() else x.min()
            for _, x, _, _, _, cut, _, _ in raw]
    dur = min(ends)
    full = max(x.max() for _, x, _, _, _, _, _, _ in raw)
    if dur < full:
        who = [lab for lab, x, _, _, _, cut, _, _ in raw if (cut > MAX_CUTOFF).any()]
        print(f"trimmed to {dur:.1f} min (from {full:.1f}); "
              f"{', '.join(who)} exceeded {MAX_CUTOFF:.0%} in-flight-at-end "
              f"beyond that")

    data = [(lab, x[x <= dur], adm[x <= dur], off[x <= dur], gp[x <= dur], c, ls)
            for lab, x, adm, off, gp, cut, c, ls in raw]

    build(data, dur, os.path.join(HERE, "exp54_hour_offered.pdf"),
          show_offered=True)
    build(data, dur, os.path.join(HERE, "exp54_hour_admitted.pdf"),
          show_offered=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
