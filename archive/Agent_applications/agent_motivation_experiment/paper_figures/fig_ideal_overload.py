#!/usr/bin/env python3
"""Schematic (NO MEASURED DATA): what a control plane should do past capacity.

  ideal_overload.pdf   3.335 x 3.30 in, one column, width=\\columnwidth

⚠ EVERY CURVE IN THIS FIGURE IS DRAWN FROM A FORMULA. Nothing here is measured,
the axes carry no units, and the capacity is the symbol C rather than a number.
THE CAPTION MUST SAY SO in its first clause; a schematic that looks like a
result is the worst thing this figure could be. It exists to define what the
paper is aiming at, so that the measured figures can be read against it.

THE SETUP. Offered load is lambda, on a shared x axis in units of C. C is the
highest offered load at which the fleet can still serve every arrival inside its
latency rule -- the same quantity `fig_intro_capacity.py` measures for each real
control plane. Three rows, all functions of lambda:

  Throughput       requests per second the system completes
  SLO attainment   share of ARRIVALS that met their rule; a dropped request is
                   a miss, so this is the offered denominator
  Drop rate        share of arrivals refused at admission

PHASE I, lambda < C. There is nothing to decide: throughput follows the demand
line lambda, attainment sits at the target, the drop rate is zero. Every policy
that is not broken looks the same here, which is why the measured figures also
show the arms on top of each other below about 20 req/s.

PHASE II, lambda > C. This is the whole design problem, and the ideal is a flat
answer in all three rows at once:

  throughput   pinned at C -- the fleet stays busy, and no more
  attainment   pinned at the target -- what was admitted still meets its rule
  drop rate    exactly the excess, 1 - C/lambda, and not a request more

The third curve is the arithmetic consequence of the first two: to hold
throughput at C while lambda arrives, C/lambda of the arrivals can be admitted
and the rest must be refused. It is drawn so that "the drop rate should rise"
does not read as "any drop rate will do" -- there is one correct curve.

THE THREE WAYS TO LEAVE IT, drawn dotted, one colour each:

  no rejection              Everything is accepted. Throughput stays at about C,
                            because the engines are the constraint and they are
                            full either way -- so the top row CANNOT tell this
                            case from the ideal. What gives it away is the
                            middle row: the excess is absorbed as queueing and
                            eviction, so attainment falls away. The drop row is
                            flat at zero.
  mistimed rejection        Requests are refused, but on a signal unrelated to
                            what the engines can currently sustain -- a fixed
                            concurrency cap, a queue-length trigger, a rate
                            limit. BOTH rows fall: work is refused that could
                            have been served, so throughput sits below C, and
                            work is admitted that cannot be served, so
                            attainment drops as well.
  over-rejection            Refusing more than the excess. Attainment holds at
                            the target, because what little is admitted is
                            served well -- so the MIDDLE row cannot tell this
                            case from the ideal. What gives it away is the top
                            row, where throughput falls below C, and the bottom
                            row, where the curve sits above 1 - C/lambda.

Read the three together and each failure is visible in exactly two rows and
invisible in the third. That is the argument for reporting all three in the
measured figures, and it is why attainment on the admitted denominator is not
enough on its own.

    python3 paper_figures/fig_ideal_overload.py
"""
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402

FIG_W, FIG_H = ps.COL_W, 3.30
XMAX = 2.6          # in units of C
TARGET = 95.0       # the SLO target, in percent; a symbol, not a measurement

# The ideal is drawn in FluidServe's blue, the colour our system carries in
# every measured figure: this schematic states the target the paper aims at, and
# the reader meets the same colour again as the thing that comes closest to it.
IDEAL = ps.ARM_COLOR["fluidserve"]
# Brown and purple were too close to separate at 1.1 pt, so the middle case
# moves to orange, and each deviation also gets its OWN dash pattern -- short,
# long, dotted. Two channels rather than one: at this line width a reader
# following a curve through a crossing needs the rhythm as well as the hue, and
# the figure has to survive being printed in grey.
DEV = {"none": "#d62728",
       "blind": "#ff7f0e",
       "over": "#2ca02c"}
# Short dash, long dash, dash-dot. The third was a fine dot pattern, which at
# 1.2 pt reads as a smudge rather than a line and was weakest exactly where it
# matters most -- lying along the ideal in the attainment row, where it has to
# be legible as a curve to show the two coincide.
DASH = {"none": (0, (2.2, 1.2)),
        "blind": (0, (5.5, 1.6)),
        "over": (0, (3.5, 1.2, 1.0, 1.2))}
# The three names are a parallel set on one axis, how much is refused against
# the excess: none, too little, too much. Earlier drafts called the middle one
# "admission blind to batch" and then "indiscriminate rejection", both of which
# named a mechanism while its neighbours named an amount, so the three did not
# line up.
# A marker per case as well as a colour and a dash. The curves carry a few of
# them (`markevery`), so the legend key shows the same shape the reader is
# following; at 1.2 pt on a schematic printed small, the shape is the channel
# that survives when the dashes blur together. The legend's own line sample is
# drawn thinner than the curve so the shape, not the stroke, is what reads.
MARK = {"ideal": "o", "none": "s", "blind": "^", "over": "D"}
LEG_LW = 0.9
LABEL = {"none": "No rejection",
         "blind": "Under-rejection",
         "over": "Over-rejection"}


def proxy(color, ls, marker):
    """A legend key drawn thinner than the curve it stands for."""
    return plt.Line2D([], [], color=color, ls=ls, lw=LEG_LW, marker=marker,
                      ms=3.0, mec="white", mew=0.4)


def main():
    x = np.linspace(0.05, XMAX, 600)

    # ONE ADMITTED SHARE AND ONE EFFICIENCY PER CASE, and all three rows are
    # derived from those two, so the rows cannot state different things about
    # the same case -- the failure mode of a schematic drawn curve by curve.
    #
    #   admit(x)  the share of arrivals let in
    #   eta(x)    the share of the engines' work that reaches a completion;
    #             below 1 when admission is mistimed and the engines spend
    #             capacity on requests they later evict and recompute
    #
    #   throughput = min(admit * lambda, C) * eta      drop = 1 - admit
    #
    # `f` is 0 through phase I and rises towards 1 with the overload, so every
    # case is IDENTICAL below C by construction. That is a claim of the figure
    # and it should not depend on three formulas happening to agree.
    over = np.maximum(x, 1.0)
    f = 1.0 - 1.0 / over

    # Over-rejection STARTS REFUSING BEFORE C, at OVER_START, and then refuses
    # on a power of lambda steeper than the excess. That is what makes its
    # throughput turn over slightly to the left of C, at a peak below the
    # ideal's, and its drop rate leave zero while the other three are still at
    # zero -- the signature of a policy that is too eager rather than too late.
    OVER_START, OVER_P = 0.85, 1.8
    # Under-rejection starts refusing WELL AFTER C, which is the contrast with
    # over-rejection starting before it. Between C and here it behaves like the
    # no-rejection case: the queue is already building and nothing is being
    # turned away.
    UNDER_START = 1.25
    admit = {"ideal": 1.0 / over,               # exactly the excess refused
             "none": np.ones_like(x),           # nothing refused, ever
             "blind": 1.0 - 0.45 * np.maximum(0.0, 1.0 - UNDER_START / x),
             "over": np.minimum(1.0, (OVER_START / x) ** OVER_P)}
    # `eta` is what the engines complete out of what they were given. For the
    # two cases that admit more than the fleet can serve it falls with the
    # overload, and it falls fast enough that their throughput PEAKS AT ABOUT C
    # and then declines: past capacity the extra arrivals do not add
    # completions, they add evictions, and every eviction pays for its prefill
    # twice. That decline is what the measured sweeps show as well.
    eta = {"ideal": np.ones_like(x),
           "none": 1.0 - 0.36 * f,
           # UNDER-REJECTION COMPLETES LESS THAN REFUSING NOTHING AT ALL, which
           # is the point of drawing it. It refuses too little to stop the queue
           # building, so it pays nearly the whole eviction cost that the
           # no-rejection case pays; and the refusals it does make land on a
           # coarse signal, so some of them remove work the engines had room
           # for. It gives up completions and buys no relief with them.
           "blind": 1.0 - 0.58 * f,
           "over": np.ones_like(x)}    # what little is admitted is served well

    # A SOFT KNEE, not a corner. Real throughput does not follow the demand
    # line to C and then turn: arrivals are bursty, so queueing costs appear
    # before the mean load reaches capacity and the curve bends into the
    # ceiling. `ceiling` is that bend -- it follows lambda while lambda is well
    # under C, sits at 0.89 C exactly at C, and approaches C from below after
    # it; p sets how sharp the bend is.
    #
    # ⚠ THE BEND IS A PROPERTY OF THE ARRIVAL STREAM AND THE ENGINES, SO IT IS
    # A FUNCTION OF LAMBDA, NOT OF WHAT EACH CASE ADMITTED. Written the other
    # way -- bending `admit * lambda` -- the ideal was the one case whose
    # argument is capped at exactly C, so its own knee held it at 0.89 C while
    # the case that admits everything ran along the ceiling ABOVE it. The
    # figure then said that refusing nothing produces more than the ideal,
    # which is the opposite of its claim.
    #
    # A case completes the smaller of what it let in and what the engines can
    # finish, and then loses the share `1 - eta` to mistimed admission.
    def ceiling(lam, p=6.0):
        return (lam ** -p + 1.0) ** (-1.0 / p)

    thru = {k: np.minimum(admit[k] * x, ceiling(x)) * eta[k] for k in admit}
    drop = {k: 1.0 - a for k, a in admit.items()}
    # Attainment holds at the target for the two cases that never admit more
    # than the engines can serve, and decays for the two that do. The decay
    # constants are chosen only so the two curves are distinguishable; nothing
    # in this figure claims a rate.
    # No rejection decays to the floor rather than towards it: with nothing
    # held back, every arrival past C is queued behind work that is already
    # late, so the share meeting its rule goes to nearly zero rather than
    # settling somewhere. Indiscriminate rejection decays too, but it is holding
    # some load back, so it is still off the floor at the right edge.
    att = {"ideal": np.full_like(x, TARGET),
           "over": np.full_like(x, TARGET),
           "none": TARGET * np.exp(-2.6 * (over - 1.0)),
           "blind": TARGET / (1.0 + 2.2 * (over - 1.0))}

    rows = [("Throughput", thru), ("SLO attainment", att), ("Drop rate", drop)]

    # WHERE A DEVIATION IS DRAWN FROM. Below its own departure point every
    # case IS the ideal -- exactly, by construction -- and drawing four
    # curves along one line there produced a band of mixed dashes that
    # looked like disagreement where there is none. Each deviation is drawn
    # only from the load at which it leaves the ideal, so phase I carries a
    # single line and the caption says the four coincide there.
    #
    # The same rule removes over-rejection from the attainment row
    # entirely: it sits ON the target at every load, so it never leaves the
    # ideal there and drawing it produced a two-colour line that read as two
    # slightly different curves. ⚠ THE CAPTION MUST THEN SAY that
    # over-rejection holds the target -- absence from that row is the whole
    # reason the case is dangerous, not a gap in the figure, and a reader
    # who does not know that will read it as "not measured".
    start = {"none": 1.0, "blind": 1.0, "over": OVER_START}
    # Two more removals, both for the same reason: a curve lying on a
    # boundary of its own panel is not information, it is a second line
    # drawn over the axis or over another case.
    #   ("over", 1)   over-rejection IS the target at every load, so it
    #                 never leaves the ideal in the attainment row
    #   ("none", 2)   no rejection drops nothing at any load, so its curve
    #                 is the x axis of the drop row
    # And under-rejection starts refusing at UNDER_START, so in the drop row
    # it is drawn from there rather than from C with a flat stretch first --
    # that stretch was also lying on the axis.
    SKIP = {("over", 1), ("none", 2)}
    ROW_START = {("blind", 2): UNDER_START}

    def draw(wide, out, keep=(0, 1, 2)):
        """One layout. `wide` puts the panels side by side instead of stacked,
        and `keep` selects which of the three rows are drawn -- (0, 1) is the
        version without the drop rate. Everything else -- the curves, where each
        one starts, what is left out -- is shared, so the files cannot disagree.

        `keep` holds ORIGINAL row indices, not positions, because `SKIP` and
        `ROW_START` are keyed on them: dropping a row must not silently move
        "over-rejection is not drawn in the attainment row" onto another row.
        """
        n = len(keep)
        with plt.rc_context(ps.STYLE):
            if wide:
                fig, axes = plt.subplots(1, n, figsize=(ps.TEXT_W, 1.72))
            else:
                fig, axes = plt.subplots(n, 1, sharex=True,
                                         figsize=(FIG_W, FIG_H * n / 3.0))
            # `ax` is indexed by ORIGINAL row number throughout, so the code
            # below reads the same whichever rows are present.
            ax = {r: axes[j] for j, r in enumerate(keep)}
            handles, labels = [], []

            for i, (_, series) in enumerate(rows):
                if i not in ax:
                    continue
                # The ideal goes down FIRST and the deviations on top of it. In the
                # attainment row the over-rejection curve is exactly the ideal --
                # that is the point of the case, it holds the target by refusing
                # work -- and drawn underneath it was invisible, so the row looked
                # as though the case were missing. On top, its dashes sit along the
                # blue line and the coincidence is what the reader sees. The caption
                # still has to say the two are equal there rather than close.
                ax[i].plot(x, series["ideal"], color=IDEAL, lw=1.6, zorder=2,
                           marker=MARK["ideal"], ms=3.0, markevery=(10, 110),
                           mec="white", mew=0.4)
                if i == 0:
                    handles.append(proxy(IDEAL, "-", MARK["ideal"]))
                    labels.append("Ideal")
                for key in ("none", "blind", "over"):
                    if (key, i) in SKIP:
                        continue
                    m = x >= ROW_START.get((key, i), start[key])
                    off = {"none": 40, "blind": 70, "over": 100}[key]
                    ax[i].plot(x[m], series[key][m], color=DEV[key], lw=1.2,
                               ls=DASH[key], zorder=3, marker=MARK[key],
                               ms=3.0, markevery=(off, 110), mec="white",
                               mew=0.4)
                    # The legend is built from row 0, which draws all three.
                    if i == 0:
                        handles.append(proxy(DEV[key], DASH[key], MARK[key]))
                        labels.append(LABEL[key])

            for i in ax:
                # C divides the two phases and is the only x value that matters,
                # so it is the only one labelled.
                ax[i].axvline(1.0, color="#999999", lw=0.7, ls=":", zorder=0)
                ax[i].set_xlim(0, XMAX)
                ax[i].set_xticks([1.0])
                ax[i].set_xticklabels(["$C$"])
                ax[i].grid(axis="both", **ps.GRID)
                ax[i].set_axisbelow(True)

            ax[0].set_ylim(0, 1.25)
            ax[0].set_yticks([0, 1.0])
            ax[0].set_yticklabels(["0", "$C$"])
            ax[0].set_ylabel("Throughput", labelpad=1.5)

            ax[1].set_ylim(0, 112)
            # The tick stays so the gridline stays, but it is not labelled: the name
            # goes inside the panel, just above the line and at the left, where it
            # sits on the flat stretch every case shares and cannot be read as a
            # value on the axis.
            ax[1].set_yticks([0, TARGET])
            ax[1].set_yticklabels(["0", ""])
            ax[1].annotate("target", (0.06, TARGET + 3), color="#333333",
                           fontsize=7, ha="left", va="bottom")
            ax[1].set_ylabel("SLO attainment", labelpad=1.5)

            if 2 in ax:
                ax[2].set_ylim(0, 1.05)
                ax[2].set_yticks([0, 1.0])
                ax[2].set_yticklabels(["0", "1"])
                ax[2].set_ylabel("Drop rate", labelpad=1.5)
            # The ideal drop curve is 1 - C/lambda. It is NOT written on the figure
            # any more, so the caption has to carry it: "the drop rate should rise"
            # is not the claim, "it should be exactly the excess" is.

            # Stacked, only the bottom panel carries the x name; side by side,
            # every panel does, because each has its own axis under it.
            for i in (list(ax) if wide else [max(ax)]):
                ax[i].set_xlabel(r"Offered load $\lambda$", labelpad=1.5)
            # The two phases named once, over the throughput panel. Side by side
            # the panel is a third as wide, so the names go inside it at the top
            # corners rather than centred over each half.
            px = (0.30, 2.30) if wide else (0.5, 1.85)
            ax[0].annotate("Phase I", (px[0], 1.13), color="#777777", fontsize=7,
                           ha="center", va="center")
            ax[0].annotate("Phase II", (px[1], 1.13), color="#777777", fontsize=7,
                           ha="center", va="center")

            # One row of four when the figure is wide, two of two when it is
            # not. THE SPACE THE LEGEND NEEDS IS A PHYSICAL HEIGHT, NOT A
            # FRACTION: `rect` takes a fraction, so a constant fraction that
            # fitted the three-row canvas cut the top row of the legend off the
            # two-row one, which is 1.1 in shorter. Reserve the inches and
            # divide.
            reserve = 0.21 if wide else 0.30
            top = 1.0 - reserve / fig.get_size_inches()[1]
            fig.legend(handles, labels, loc="lower center",
                       ncol=4 if wide else 2, fontsize=6.5,
                       bbox_to_anchor=(0.5, top - 0.006),
                       frameon=False, columnspacing=0.8, handlelength=1.6,
                       handletextpad=0.35, borderaxespad=0.0)
            fig.tight_layout(rect=(0, 0, 1, top), h_pad=0.6, w_pad=1.2,
                             pad=0.3)
            ps.save(fig, out)

    def draw_twin(cases, out, combined):
        """Throughput and attainment on ONE axes, left and right.

        The drop row is gone, so this pair has to carry the argument alone. It
        can, for three of the four cases: what a policy does past C shows up as
        the two curves separating. It CANNOT for over-rejection, whose
        attainment holds the target while its throughput falls -- on this figure
        that looks like a mild loss rather than a policy refusing work it could
        have served. THE CAPTION MUST SUPPLY THE DROP RATE for that case, or the
        three-panel version should be used instead.

        `combined` puts every case on one axes: colour and marker then identify
        the CASE and the line style identifies the QUANTITY, solid for
        throughput and dashed for attainment. That is the opposite assignment
        from the three-panel figures, where style identifies the case -- with
        two quantities sharing one axes there is no other way to say which axis
        a line belongs to, and mixing the two conventions in one paper is worse
        than switching once and saying so.
        """
        with plt.rc_context(ps.STYLE):
            fig, axl = plt.subplots(figsize=(ps.COL_W, 1.95 if combined else 1.62))
            axr = axl.twinx()
            handles, labels = [], []

            for key in cases:
                col = IDEAL if key == "ideal" else DEV[key]
                s0 = 0.0 if not combined else (0.0 if key == "ideal"
                                               else start[key])
                m = x >= s0
                axl.plot(x[m], thru[key][m], color=col, ls="-", lw=1.3,
                         marker=MARK[key], ms=3.0, markevery=(10, 130),
                         mec="white", mew=0.4, zorder=3)
                # In the combined figure over-rejection's attainment is the
                # ideal's, so it is left out for the same reason as in the
                # three-panel version. On its own figure there is nothing for it
                # to coincide with, so it is drawn.
                if not (combined and (key, 1) in SKIP):
                    ma = x >= s0
                    axr.plot(x[ma], att[key][ma], color=col, ls=(0, (4.5, 1.6)),
                             lw=1.3, marker=MARK[key], ms=3.0,
                             markevery=(70, 130), mec="white", mew=0.4,
                             zorder=3)
                if combined:
                    handles.append(proxy(col, "-", MARK[key]))
                    labels.append("Ideal" if key == "ideal" else LABEL[key])

            if not combined:
                key = cases[0]
                col = IDEAL if key == "ideal" else DEV[key]
                handles = [proxy(col, "-", MARK[key]),
                           proxy(col, (0, (4.5, 1.6)), MARK[key])]
                labels = ["Throughput", "SLO attainment"]
            else:
                # Two more keys, in grey, for what the line style means. Grey
                # because the style belongs to the quantity and colouring the
                # sample would tie it to one of the cases.
                handles += [proxy("#555555", "-", ""),
                            proxy("#555555", (0, (4.5, 1.6)), "")]
                labels += ["Throughput", "SLO attainment"]

            axl.axvline(1.0, color="#999999", lw=0.7, ls=":", zorder=0)
            axl.set_xlim(0, XMAX)
            axl.set_xticks([1.0])
            axl.set_xticklabels(["$C$"])
            axl.set_xlabel(r"Offered load $\lambda$", labelpad=1.5)
            axl.grid(axis="both", **ps.GRID)
            axl.set_axisbelow(True)
            axl.set_ylim(0, 1.25)
            axl.set_yticks([0, 1.0])
            axl.set_yticklabels(["0", "$C$"])
            axl.set_ylabel("Throughput", labelpad=1.5)
            axr.set_ylim(0, 112)
            axr.set_yticks([0, TARGET])
            axr.set_yticklabels(["0", "target"])
            axr.set_ylabel("SLO attainment", labelpad=1.5)
            axr.tick_params(axis="y", direction="in", length=2.5, width=0.6)

            fig.legend(handles, labels, loc="lower center",
                       ncol=3 if combined else 2, fontsize=6.5,
                       bbox_to_anchor=(0.5, 0.86 if combined else 0.885),
                       frameon=False, columnspacing=0.8, handlelength=1.8,
                       handletextpad=0.35, borderaxespad=0.0)
            fig.tight_layout(rect=(0, 0, 1, 0.865 if combined else 0.89),
                             pad=0.3)
            ps.save(fig, out)

    def draw_twin_grid(out):
        """The four per-case twin-axis panels tiled 2x2.

        ONE COLUMN. Each panel carries a y axis on both sides, so at 3.335 in
        the four sets of tick labels across a row would take more width than the
        two panels between them. THE INTERIOR TICK LABELS ARE THEREFORE HIDDEN:
        the left column shows the throughput scale and the right column shows
        the attainment scale, and each row is read
        [0, C] [panel] [panel] [0, target]. This is only legitimate because all
        four panels share both scales exactly -- the left axes are `sharey` and
        the right ones are set from the same two numbers -- and the caption
        should say so, because a reader who assumes otherwise will read panel
        (b)'s throughput against the attainment ticks on its right.

        Colour still identifies the case, but here the panel title does too, so
        the shared legend only has to say what the two line styles mean.
        """
        order = ["ideal", "none", "blind", "over"]
        titles = ["(a) Ideal", "(b) No rejection",
                  "(c) Under-rejection", "(d) Over-rejection"]
        with plt.rc_context(ps.STYLE):
            fig, axes = plt.subplots(2, 2, figsize=(ps.COL_W, 3.05),
                                     sharex=True, sharey=True)
            axl = axes.ravel()
            for i, key in enumerate(order):
                col = IDEAL if key == "ideal" else DEV[key]
                a, r = axl[i], axl[i].twinx()
                a.plot(x, thru[key], color=col, ls="-", lw=1.3,
                       marker=MARK[key], ms=3.0, markevery=(10, 130),
                       mec="white", mew=0.4, zorder=3)
                r.plot(x, att[key], color=col, ls=(0, (4.5, 1.6)), lw=1.3,
                       marker=MARK[key], ms=3.0, markevery=(70, 130),
                       mec="white", mew=0.4, zorder=3)
                a.set_title(titles[i], fontsize=8, color="black", pad=2)
                a.axvline(1.0, color="#999999", lw=0.7, ls=":", zorder=0)
                a.set_xlim(0, XMAX)
                a.set_xticks([1.0])
                a.set_xticklabels(["$C$"])
                a.grid(axis="both", **ps.GRID)
                a.set_axisbelow(True)
                a.set_ylim(0, 1.25)
                a.set_yticks([0, 1.0])
                a.set_yticklabels(["0", "$C$"])
                r.set_ylim(0, 112)
                r.set_yticks([0, TARGET])
                r.set_yticklabels(["0", "target"])
                r.tick_params(axis="y", direction="in", length=2.5, width=0.6,
                              labelright=(i % 2 == 1))
                # Tick NUMBERS on every panel, axis NAMES only on the outer
                # edge: each panel has the same two scales, and repeating four
                # names costs the drawing area the curves need.
                a.tick_params(labelbottom=True)
                if i % 2 == 0:
                    a.set_ylabel("Throughput", labelpad=1.5)
                if i % 2 == 1:
                    r.set_ylabel("SLO attainment", labelpad=1.5)
                if i >= 2:
                    a.set_xlabel(r"Offered load $\lambda$", labelpad=1.5)

            handles = [proxy("#555555", "-", ""),
                       proxy("#555555", (0, (4.5, 1.6)), "")]
            fig.legend(handles, ["Throughput", "SLO attainment"],
                       loc="lower center", ncol=2, fontsize=6.5,
                       bbox_to_anchor=(0.5, 0.925), frameon=False,
                       columnspacing=1.0, handlelength=1.8,
                       handletextpad=0.35, borderaxespad=0.0)
            fig.tight_layout(rect=(0, 0, 1, 0.93), w_pad=0.4, h_pad=0.7,
                             pad=0.3)
            ps.save(fig, out)

    draw(False, os.path.join(HERE, "ideal_overload.pdf"))
    draw(True, os.path.join(HERE, "ideal_overload_wide.pdf"))
    # Without the drop row. ⚠ The two rows that remain cannot tell over-rejection
    # from the ideal on their own: its attainment IS the target, so all that is
    # left of it is a throughput curve below C, which reads as a mild loss
    # rather than as work refused that could have been served. The caption has
    # to give that case's drop rate, or the three-row version has to be used.
    draw(False, os.path.join(HERE, "ideal_overload_nodrop.pdf"), keep=(0, 1))
    draw(True, os.path.join(HERE, "ideal_overload_wide_nodrop.pdf"),
         keep=(0, 1))
    # Two quantities on one axes: every case together, then one file per case.
    draw_twin(["ideal", "none", "blind", "over"],
              os.path.join(HERE, "ideal_twin_all.pdf"), combined=True)
    for key in ("ideal", "none", "blind", "over"):
        draw_twin([key], os.path.join(HERE, f"ideal_twin_{key}.pdf"),
                  combined=False)
    draw_twin_grid(os.path.join(HERE, "ideal_twin_grid.pdf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
