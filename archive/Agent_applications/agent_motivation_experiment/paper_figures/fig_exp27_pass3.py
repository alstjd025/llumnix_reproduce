#!/usr/bin/env python3
"""Paper figure: EXP-27 pass 3 (v22) rate sweep — attainment and token goodput.

Three arms — PolyServe, the Llumnix SLO baseline, FluidServe — in two PDFs, each
two side-by-side panels, sized for ONE COLUMN of a two-column USENIX paper. The
two differ only in whether the offered-denominator curves are drawn; see
`build()` and the "Content" note below.

  exp27_pass3_attainment_goodput_offered.pdf    with the offered curves
  exp27_pass3_attainment_goodput_admitted.pdf   admitted denominator only

They are also different heights (2.22 in vs 1.95 in), because the offered
version carries a second legend row.

Sizing. `usenix2019_v3.sty` sets `\\textwidth=7in` and `\\columnsep=0.33in`, so a
single column is 3.335 in wide. The figure is drawn at exactly that width, which
makes `\\includegraphics[width=\\columnwidth]` inside a plain `figure` a scale
factor of 1.0, so the 8 pt type set here is 8 pt on the page. Any re-scaling in
LaTeX (`scale=`, `width=` something other than `\\columnwidth`, `\\resizebox`)
multiplies the effective font size by the same factor and breaks that; the
earlier 7 in version of this figure rendered at roughly 3.8 pt when placed in one
column for exactly that reason. If the figure is ever moved into a `figure*`
spanning both columns, redraw it at COL_W = 7.0 rather than stretching it.

Side by side inside 3.335 in leaves about 1.3 in of plotting area per panel,
which the default labelling does not fit into. Three things buy that room back
and are the reason this figure is not just the wide version rescaled:

  - one x axis label centred under both panels rather than one per panel;
  - goodput tick labels carry a k suffix (6k, 12k, 18k) instead of being
    written out (6000, 12000, 18000), which is three characters less of axis
    width and lets both y axis labels stay on a single line;
  - the dotted style is explained once by a neutral key on a second legend row
    rather than by naming each arm's offered curve. Two arms reject, so naming
    them would need five legend entries and about 3.6 in of the 3.335 in
    available.

Everything an axis is read by — tick labels, axis labels, legend — stays at 8 pt.

Content. The two panels have to stay together: a routing/admission policy can
raise SLO attainment by refusing work, so the attainment panel alone is not a
claim about capacity, and the goodput panel is the quantity that rejections
cannot inflate. For the same reason the attainment panel carries both
denominators for the arm that rejects:

  solid   admitted — the denominator is the requests the system accepted, so a
          rejected request leaves the population entirely.
  dotted  offered  — every request that arrived is in the denominator and a
          rejection counts as a violation.

PolyServe rejected zero requests at every rate in this pass, so its offered
curve is numerically identical to its admitted curve and is not drawn in either
version: two coincident lines would suggest a distinction that the data does
not contain. The other two arms both reject, and their two curves separate at
60 and 80 req/s:

              rejected at 60 req/s   at 80 req/s
  Llumnix SLO          5%                36%
  FluidServe           9%                30%

The per-point rejection annotations that earlier versions of this figure
carried are gone. With two rejecting arms the labels for the 60 req/s points
would sit 1.1 attainment points apart, which is under a tenth of an inch at
this panel height and unreadable. The gap between each arm's solid and dotted
curve still shows what its rejections cost.

The admitted-only version drops the dotted curves too, so nothing in that
figure records that either arm refused work. Attainment over an admitted
denominator is not interpretable without those numbers — a policy that refuses
everything scores 100% — so IN BOTH VERSIONS, AND ESPECIALLY THAT ONE, THE
CAPTION HAS TO STATE THE REJECTION RATES ABOVE. The goodput panel is a partial
guard, since rejected work produces no tokens, but it reports a rate rather
than the fraction of arrivals that were turned away.

Data, metric definitions and run selection are unchanged from the exploratory
figures (`analysis_scripts/request_level/exp27_figures.py`, `fig_split`), which
this script imports rather than reimplements — only the styling differs.

    python3 paper_figures/fig_exp27_pass3.py
"""
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import COL_W, STYLE, GRID, kfmt, save  # noqa: E402

from exp27_figures import collect, rps, ARM_C, ARM_L  # noqa: E402

# m1 balanced mix, four engines, 8 min per condition.
#
#   pass 3 (exp27p3)  PolyServe and FluidServe, both repeats r1 and r2, so those
#                     points are n=2 and their bars are min..max.
#   pass 5 (exp27p5)  the Llumnix SLO baseline at the fair setting, ONE run per
#                     rate, in a SEPARATE SESSION.
#
# Pass 4, the same baseline at the 25 ms/token setting, is deliberately excluded:
# it judged the agent class 2.3x tighter than FluidServe judged it and rejected
# 98% of it, so its curve belongs to a policy answering a different question.
# This selection matches `exp27_figures.py`, which is the authority on it.
#
# Two caveats that the CAPTION HAS TO CARRY, because the drawing cannot:
#   - the baseline is one run, so its markers have no bars. A point with no bar
#     beside points with bars reads as the more precise measurement, which is
#     the opposite of the truth.
#   - it is a cross-session comparison, and the size of that movement is not
#     currently known under the corrected scoring below. The figure it was
#     measured on (0.1 points at 20 req/s, 4.6 at 80, from five PolyServe runs
#     whose code never changed) was computed before the inter-token correction
#     and has not been recomputed.
#
# SCORING. Attainment here uses the CORRECTED inter-token latency, derived as
# (e2e - ttft) / (output_tokens - 1) by `exp22_fluidserve.mean_inter_token_ms`.
# The recorded `tbt_mean_ms` column is about half the true value on every run
# collected before 2026-07-30, so these same runs scored very differently before
# that correction landed: FluidServe at 80 req/s read 87.0% admitted then and
# 48.1% now, and the Llumnix baseline at 60 req/s read 93.7% then and 28.6% now.
# Any figure or table of these runs quoted from before 2026-07-30 is on the
# uncorrected basis. FS_LEGACY_TBT=1 reproduces it.
#
# The n=2 spread is NOT uniformly small. It is under 0.5 points at 20-60 req/s
# on every arm, and 16.1 points on FluidServe at 80 req/s (40.1 vs 56.2), which
# is why that point carries a visible bar and no claim should rest on it.
RUNS = ["results/*exp27p3*", "results/*exp27p5*"]

# One marker per arm, kept identical in both panels and reused (dotted, faded)
# for that arm's offered curve, so the two denominators of one policy are
# visibly the same policy and no marker has to be spent on the distinction.
MARK = {"polyserve": "o", "slo": "^", "fluidserve": "s", "fluidserveflat": "D"}
# Legend text, overriding ARM_L where the paper uses a different name. The
# baseline arm is Llumnix's SLO-aware dispatch policy and `exp27_figures.py`
# labels it "Llumnix SLO" to separate it from the load-balancing policy that
# EXP-21 measured; the paper calls it "Llumnix", so the caption or the text has
# to say which Llumnix policy it is.
LABEL = dict(ARM_L, slo="Llumnix")

XLABEL = "Offered rate (request/s)"


def agg(df, arm):
    """Per-rate mean and min/max over the repeats of one arm.

    Two repeats per condition for PolyServe and FluidServe, one for the
    baseline, so the bars span the observations rather than estimating a
    confidence interval; they are the observed spread and should be described
    that way in the caption.
    """
    return df[df.arm == arm].groupby("rpm").agg(
        sloA=("attain", "mean"), sloALo=("attain", "min"), sloAHi=("attain", "max"),
        sloO=("attain_off", "mean"), sloOLo=("attain_off", "min"),
        sloOHi=("attain_off", "max"),
        gp=("goodput", "mean"), gpLo=("goodput", "min"), gpHi=("goodput", "max"),
        rej=("rejected", "mean"),
    ).reset_index().sort_values("rpm")



def build(df, out, show_offered):
    """One figure. `show_offered` adds the second attainment denominator.

    It also controls the rejection annotations, which belong to the same
    statement: with `show_offered=False` the figure reads as "FluidServe holds
    87% attainment at 80 req/s" and says nothing about the 30% of arrivals it
    refused to admit. That is a deliberate choice for readability, not an
    oversight, and it moves the obligation to report the rejection rate into
    the caption. See the module docstring.
    """
    arms = [a for a in ARM_C if a in set(df["arm"])]
    xticks = rps(sorted(df.rpm.unique()))
    # The offered version needs a second legend row for the style key, so it is
    # taller and its rows sit lower. Figure-fraction anchors, not points, so
    # they have to be restated per height rather than derived from one another.
    fig_h = 2.22 if show_offered else 1.95
    arms_y = 0.905 if show_offered else 0.845
    key_y, rect_top = 0.830, (0.820 if show_offered else 0.835)

    with plt.rc_context(STYLE):
        fig, (ax_a, ax_g) = plt.subplots(1, 2, figsize=(COL_W, fig_h))

        handles, labels = [], []
        any_offered = False
        for arm in arms:
            d, c, mk = agg(df, arm), ARM_C[arm], MARK[arm]

            h = ax_a.errorbar(rps(d.rpm), d.sloA,
                              yerr=[d.sloA - d.sloALo, d.sloAHi - d.sloA],
                              color=c, ls="-", marker=mk, capsize=2,
                              mec="white", mew=0.5)
            handles.append(h)
            labels.append(LABEL[arm])

            # Only for an arm that actually rejects: with zero rejections the
            # offered curve lies exactly on the admitted one, and drawing it
            # would suggest a distinction the data does not contain.
            if show_offered and bool((d.rej > 0).any()):
                ax_a.errorbar(rps(d.rpm), d.sloO,
                              yerr=[d.sloO - d.sloOLo, d.sloOHi - d.sloO],
                              color=c, ls=":", marker=mk, ms=3.0, alpha=0.55,
                              capsize=2, mec="white", mew=0.5)
                any_offered = True

            ax_g.errorbar(rps(d.rpm), d.gp,
                          yerr=[d.gp - d.gpLo, d.gpHi - d.gp],
                          color=c, ls="-", marker=mk, capsize=2,
                          mec="white", mew=0.5)

        # Single-line y labels. The unit lives in the tick labels on the right
        # panel (6k, 12k, ...) rather than in the axis label, which keeps both
        # labels to one line and gives the panels the width back.
        ax_a.set_ylabel("SLO attainment (%)")
        ax_a.set_ylim(0, 108)
        ax_a.set_yticks([0, 25, 50, 75, 100])
        ax_g.set_ylabel("Goodput token (t/s)")
        # Ticks every 5k up to 20k, with the top of the axis above FluidServe's
        # 60 req/s peak (21.5k). The automatic locator stopped at 18k and left
        # that peak between the last gridline and the frame, where its value
        # cannot be read off the axis; with a 20k tick it reads as "just over
        # 20k". Five ticks here also match the five on the left panel.
        ax_g.set_ylim(0, 23000)
        ax_g.set_yticks([0, 5000, 10000, 15000, 20000])
        ax_g.yaxis.set_major_formatter(kfmt())

        for ax in (ax_a, ax_g):
            ax.set_xticks(xticks)
            ax.set_xlim(xticks.min() - 4, xticks.max() + 4)
            ax.grid(axis="both", **GRID)
            ax.set_axisbelow(True)

        # One arm per legend entry, one row. Naming each offered curve instead
        # would need five entries and 3.6 in of the 3.335 in available, so the
        # dotted style is explained once on a second row by a neutral key
        # rather than repeated per arm.
        legend_kw = dict(loc="lower center", frameon=False, columnspacing=0.8,
                         handlelength=1.5, handletextpad=0.4, borderaxespad=0.0)
        fig.legend(handles, labels, ncol=len(labels),
                   bbox_to_anchor=(0.5, arms_y), **legend_kw)
        if any_offered:
            key = plt.Line2D([], [], color="#555555", ls=":", lw=1.2, alpha=0.8)
            fig.legend([key], ["dotted: offered denominator, rejected = violation"],
                       ncol=1, bbox_to_anchor=(0.5, key_y), **legend_kw)
        fig.tight_layout(rect=(0, 0.10, 1, rect_top), w_pad=0.8, pad=0.35)
        # One x label centred under both panels: repeating it costs a line of
        # figure height and reads as two different quantities.
        fig.text(0.5, 0.005, XLABEL, ha="center", va="bottom", fontsize=8)
        save(fig, out)


def main():
    df = collect([os.path.join(ROOT, p) for p in RUNS])
    if df.empty:
        print("no runs matched; run from the experiment root", file=sys.stderr)
        return 1
    # Two versions of the same figure. Which one belongs in the paper depends on
    # whether the surrounding text has already established that FluidServe
    # rejects and PolyServe does not; the offered curve is the honest version
    # and the admitted-only one is the readable version.
    build(df, os.path.join(HERE, "exp27_pass3_attainment_goodput_offered.pdf"),
          show_offered=True)
    build(df, os.path.join(HERE, "exp27_pass3_attainment_goodput_admitted.pdf"),
          show_offered=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
