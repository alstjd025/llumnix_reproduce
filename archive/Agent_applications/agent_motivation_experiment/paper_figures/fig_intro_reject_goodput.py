#!/usr/bin/env python3
"""Paper figure (intro): matched attainment among admitted work, at very
different rejection rates and very different useful output.

  intro_reject_goodput.pdf   3.335 x 1.75 in, one column, width=\\columnwidth

  (a) Token goodput          output tokens per second belonging to a request
                             that arrived, was served, and met its rule
  (b) SLO attainment among ADMITTED requests, left axis, solid
      Rejection rate,                          right axis, dashed

WHAT THIS FIGURE ARGUES, AND HOW IT DIFFERS FROM `fig_intro_capacity.py`. The
capacity figure scores every arrival, so a policy cannot buy its number by
refusing work, and the claim is a single rate. This one splits that into the two
things a policy actually chooses between: how well it serves what it takes, and
how much it takes. Read alone, panel (b)'s left axis would reward refusing
everything, which is why the rejection rate is on the same panel and the useful
output is on the panel beside it. All three are needed and none of them ranks
the five arms on its own.

THE ADMITTED PANEL IS WHERE WE TIE, NOT WHERE WE WIN. At 35-70 req/s FluidServe
holds 88.4-92.9% of admitted requests inside their rule and llm-d holds
88.8-92.1%; at 70 req/s llm-d is the higher of the two. The distance between the
two policies is entirely in the other two quantities:

    70 req/s      rejected   admitted attainment   goodput tok/s
    FluidServe      56.1%          88.4                13,053
    llm-d           80.4%          92.1                 7,347
    Llumnix SLO     81.7%           7.8                   843
    vLLM router      0.0%           2.5                   600
    PolyServe        0.0%           1.2                   479

FluidServe and llm-d reach the same quality of service on what they accept, and
FluidServe accepts 2.2x more of the arrivals (43.9% against 19.6%) and turns
that into 1.8x the useful tokens.

THE REJECTION AXIS DOES NOT RANK THE ARMS EITHER, AND THE FIGURE HAS TO ADMIT
IT. PolyServe and the vLLM router have no admission control, so they sit at zero
on that axis at every rate and are the best two on it and the worst two on both
others: at 70 req/s they hold 1.2% and 2.5% of arrivals inside their rule and
produce 479 and 600 useful tokens per second against FluidServe's 13,053.
"Rejects less" is only a virtue at equal attainment, which is why the two arms it
is claimed against are llm-d and Llumnix SLO, both of which reject about 80%.

Llumnix SLO rejects as much as llm-d and gets 7.8% attainment on what is left.
It is the case that shows rejecting is not itself the mechanism.

⚠ AND AN ARM THAT NEVER REJECTS STILL LOSES REQUESTS -- IT JUST LOSES THEM WHERE
THIS FIGURE CANNOT SHOW THEM. A policy with no admission control leaves requests
unfinished when the measurement window closes, and those have no outcome, so they
leave BOTH denominators. At 35 / 45 / 70 req/s that share is 21.8 / 39.6 / 61.6%
for the vLLM router and 37.9 / 35.9 / 35.7% for PolyServe, against 1.9 / 1.7 /
1.3% for FluidServe. Reading the rejection axis as "how much work was turned
away" therefore understates those two arms, and the goodput panel is where that
shows up.

WHY THE REJECTION LINES ARE DASHED AND CARRY NO MARKERS. Ten lines share one
panel. Colour identifies the arm and is the same in both panels; within panel
(b) the line style says which axis a line belongs to. Markers are left to the
attainment lines so that the two families separate at a glance where they cross.

DATA. The post-2026-08-08 static sweep, eight rates (10, 15, 20, 25, 35, 45, 55,
70 req/s), the same runs as `fig_intro_capacity.py`, whose `ARMS` table this
imports rather than copying: FluidServe and llm-d from EXP-68/69/70, PolyServe
and Llumnix SLO from EXP-72, the vLLM router from EXP-77. FluidServe and llm-d
have two repeats at 35-70 and one at 10-25; the other three have one repeat at
every rate, and EXP-77's second repeat had not finished when this was drawn. No
error bars are drawn and none of these points is noise-bounded; say so in the
caption.

swe is configured two ways and scored one way: llm-d and Llumnix SLO cannot
express an end-to-end budget and take `m1f`, FluidServe, PolyServe and the vLLM
router take `m1`, and all five are judged against the same 30 s end-to-end rule.

    python3 paper_figures/fig_intro_reject_goodput.py
"""
import collections
import glob
import importlib.util
import os
import re
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.ticker as mticker  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402


def _load_module(path, name="m"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXP22 = _load_module(os.path.join(
    ROOT, "analysis_scripts/request_level/exp22_fluidserve.py"))
# The arm list, colours, markers and run globs come from the capacity figure
# rather than being copied: the two figures are drawn from the same conditions
# and a second copy of the globs is how one of them silently keeps a superseded
# run after the other is fixed.
CAP = _load_module(os.path.join(HERE, "fig_intro_capacity.py"), "capfig")
ARMS = CAP.ARMS
# The arm the ratio variant divides by.
REF = "FluidServe"

# 1.75, down from 2.05. At 1.60 the rotated y labels are as tall as the
# canvas and the topmost ink lands 2 px from the edge at 300 dpi, so this is
# about as short as the figure goes while the three axis names stay written
# out.
FIG_H = 1.75
XTICKS = [10, 30, 50, 70]


def collect():
    """arm -> rate -> dict of the three quantities, averaged over repeats."""
    out = {}
    for name, _, _, pats in ARMS:
        acc = collections.defaultdict(list)
        seen = set()
        for pat in pats:
            for d in sorted(glob.glob(os.path.join(ROOT, pat))):
                if "PRERUN" in d or d in seen:
                    continue
                seen.add(d)
                r = EXP22.load_run(d)
                if r is None or r.empty:
                    continue
                rate = int(re.search(r"rpm_(\d+)", d).group(1)) / 60.0
                span = r["rel"].max() - r["rel"].min()
                served = r[~r["rejected"]]
                # Goodput is scored on the offered column: a rejected request
                # produced no tokens, so it contributes none. It is the one
                # quantity here that no choice of denominator can inflate.
                ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
                acc[rate].append((
                    EXP22.per_request(served, "violate_served"),
                    100.0 * r["rejected"].mean(),
                    ok["output_tokens"].sum() / span,
                    # Every output token the engines emitted, whether or not the
                    # request it belonged to met its rule. The left panel can be
                    # drawn on either this or the line above it.
                    served["output_tokens"].sum() / span,
                ))
        out[name] = {k: tuple(float(np.mean(c)) for c in zip(*v))
                     for k, v in acc.items()}
    return out


def main():
    data = collect()
    missing = [n for n, _, _, _ in ARMS if not data[n]]
    if missing:
        print(f"  NOT DRAWN: {', '.join(missing)} has no post-fix conditions")
    arms = [a for a in ARMS if data[a[0]]]

    print(f"{'arm':13s} {'rate':>5s} {'admitted':>9s} {'rejected':>9s} "
          f"{'goodput':>9s} {'thru':>9s}")
    for name, _, _, _ in arms:
        for rate in sorted(data[name]):
            adm, rej, gp, th = data[name][rate]
            print(f"{name:13s} {rate:5.0f} {adm:9.1f} {rej:9.1f} {gp:9.0f} "
                  f"{th:9.0f}")

    build(data, arms, 2, "Goodput (tokens/s)",
          os.path.join(HERE, "intro_reject_goodput.pdf"))
    # The same figure with the left panel showing every token the engines
    # emitted rather than only the ones that counted. ⚠ IT ASKS A DIFFERENT
    # QUESTION AND ANSWERS IT DIFFERENTLY: on goodput the arms span 27x at
    # 70 req/s, on throughput 1.7x. Throughput beside attainment says "the
    # engines were equally busy and the outcomes were not"; goodput beside
    # attainment says "and here is what the busyness was worth". Both are
    # honest; the caption has to say which is on the axis.
    build(data, arms, 3, "Throughput (tokens/s)",
          os.path.join(HERE, "intro_reject_throughput.pdf"))
    # The same panel with the y axis cut at 4k, and with every arm divided by
    # FluidServe at the same rate. Both make the throughput differences easier
    # to see and both need a sentence in the caption to stay honest -- one
    # because the axis is truncated, the other because the unit is gone.
    build(data, arms, 3, "Throughput (tokens/s)",
          os.path.join(HERE, "intro_reject_throughput_zoom.pdf"), mode="zoom")
    if REF in data:
        build(data, arms, 3, f"Throughput / {REF}",
              os.path.join(HERE, "intro_reject_throughput_ratio.pdf"),
              mode="ratio")
        # Both panels against ours. The rejection rate on the right axis stays
        # in per cent: a ratio of rejection rates is undefined for the two arms
        # that never reject, and 0/56.1 says nothing a reader can use.
        build(data, arms, 3, "Throughput (norm.)",
              os.path.join(HERE, "intro_reject_ratio_both.pdf"), mode="ratio2")
    return 0


def build(data, arms, left, left_label, out, mode="abs"):
    with plt.rc_context(ps.STYLE):
        fig, (ax_g, ax_a) = plt.subplots(1, 2, figsize=(ps.COL_W, FIG_H))
        ax_r = ax_a.twinx()
        handles = []

        for name, col, mk, _ in arms:
            x = sorted(data[name])
            adm = [data[name][k][0] for k in x]
            rej = [data[name][k][1] for k in x]
            gp = [data[name][k][left] for k in x]
            if mode in ("ratio", "ratio2"):
                # Each arm's throughput DIVIDED BY FLUIDSERVE'S AT THE SAME
                # OFFERED RATE, so FluidServe is a flat 1.0 and every other
                # curve reads directly as "this fraction of what ours produced
                # at the same load". Per rate, not against a single number: the
                # question is how the arms compare at each point on the sweep,
                # and one divisor would fold the shape of our own curve into
                # everyone else's.
                gp = [data[name][k][left] / data[REF][k][left] for k in x]
            h, = ax_g.plot(x, gp, color=col, marker=mk, ms=2.8, mec="white",
                           mew=0.4)
            if mode == "ratio2":
                # Attainment divided by FluidServe's at the same rate, like the
                # left panel. ⚠ THIS AXIS COMPRESSES THE INTERESTING END: three
                # arms land between 0.01 and 0.09 at 70 req/s and are then
                # indistinguishable from each other and from zero. That IS the
                # statement -- next to ours they are all a rounding error -- but
                # if the ordering among them matters, the unnormalised version
                # is the one to use.
                adm = [adm[j] / data[REF][k][0] for j, k in enumerate(x)]
            ax_a.plot(x, adm, color=col, marker=mk, ms=2.8, mec="white",
                      mew=0.4)
            # Short dashes rather than dots, and 1.2 pt rather than 0.9. A
            # 0.9 pt dot pattern over eight points reads as a faint smudge at
            # this panel size; a dash long enough to show its colour does not.
            # It is still distinguishable from the attainment line of the same
            # colour, which is solid AND carries markers.
            ax_r.plot(x, rej, color=col, ls=(0, (2.2, 1.2)), lw=1.2)
            handles.append(h)

        ax_g.set_ylabel(left_label)
        if mode in ("ratio", "ratio2"):
            # The reference is a line on the figure, not just the arm that
            # happens to sit at 1.0: without it a reader has to find FluidServe
            # in the legend to know what the axis is against.
            ax_g.axhline(1.0, color="#999999", lw=0.7, ls=":", zorder=0)
        # Five ticks, matching the right panel's 0/25/50/75/100 so the two
        # panels are read at the same rhythm. The step is 4k rather than the
        # 3.5k that would put the top tick at the data maximum, because every
        # label then stays three characters: "10.5k" is two characters wider
        # than "12k" and at 3.335 in for two panels plus a twin axis that width
        # comes out of the y label, which it pushed off the canvas once already.
        if mode in ("ratio", "ratio2"):
            ax_g.set_ylim(0.4, 1.12)
            ax_g.set_yticks([0.4, 0.6, 0.8, 1.0])
        elif mode == "zoom":
            # ⚠ THE AXIS DOES NOT START AT ZERO. Every arm produces at least
            # 4,900 tokens/s at the rates on this sweep, so a zero-based axis
            # spends its lower quarter on nothing; but a truncated axis makes
            # ratios look larger than they are, and THE CAPTION MUST SAY THAT
            # THE AXIS IS CUT. The 1.7x spread at 70 req/s looks like about 3x
            # here.
            ax_g.set_ylim(4000, 16400)
            ax_g.set_yticks([4000, 8000, 12000, 16000])
            ax_g.yaxis.set_major_formatter(ps.kfmt())
        else:
            ax_g.set_ylim(0, 16000)
            ax_g.set_yticks([0, 4000, 8000, 12000, 16000])
            ax_g.yaxis.set_major_formatter(ps.kfmt())
        if mode == "ratio2":
            # "(norm.)", not "/ FluidServe": the divisor is the same on both
            # panels and naming it twice on two rotated labels costs width that
            # a 3.335 in figure with a twin axis does not have. ⚠ THE AXIS THEN
            # DOES NOT SAY WHAT IT WAS NORMALISED BY, so the caption must:
            # every curve is divided by FluidServe's value at the same offered
            # rate, which is why FluidServe is the flat line at 1.0.
            ax_a.set_ylabel("SLO attain. (norm.)")
            # Fifths, not quarters: the labels are written to one decimal and
            # 0.25 printed that way reads "0.2", which is a different number.
            ax_a.set_ylim(0, 1.12)
            ax_a.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
            ax_a.yaxis.set_major_formatter(
                mticker.FuncFormatter(lambda v, _: f"{v:.1f}"))
            ax_a.axhline(1.0, color="#999999", lw=0.7, ls=":", zorder=0)
        else:
            ax_a.set_ylabel("SLO attainment (%)")
            ax_a.set_ylim(0, 105)
            ax_a.set_yticks([0, 25, 50, 75, 100])
        # The style is named on the axis rather than in a key inside the panel:
        # the panel has eight lines and every place a key would go is on top
        # of one of them. Naming the dotted family is enough -- the other is
        # then the solid one.
        ax_r.set_ylabel("Rejection rate (%, dashed)", labelpad=1.5)
        ax_r.set_ylim(0, 105)
        ax_r.set_yticks([0, 25, 50, 75, 100])
        # The right axis keeps black ticks and a black label, as the project
        # style requires: it is shared by four coloured lines, so colouring the
        # axis would name one of them.
        ax_r.tick_params(axis="y", direction="in", length=2.5, width=0.6)

        for ax in (ax_g, ax_a):
            ax.set_xlabel("Offered rate (req/s)")
            ax.set_xlim(5, 75)
            ax.set_xticks(XTICKS)
            ax.grid(axis="y", **ps.GRID)
            ax.set_axisbelow(True)

        # ncol = however many arms there are, always one row. At a fixed
        # ncol=4 the fifth arm wrapped to a second row and that row was drawn
        # off the top of the canvas; a legend does not shrink the axes, so
        # nothing in the output reported the loss.
        fig.legend(handles, [n for n, _, _, _ in arms], loc="lower center",
                   bbox_to_anchor=(0.5, 0.895), ncol=len(arms), fontsize=6.5,
                   frameon=False, handlelength=1.0, columnspacing=0.4,
                   handletextpad=0.22, borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 0.895), w_pad=0.6, pad=0.3)
        ps.save(fig, out)


if __name__ == "__main__":
    sys.exit(main())
