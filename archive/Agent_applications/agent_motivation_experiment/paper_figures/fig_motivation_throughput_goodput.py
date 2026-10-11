#!/usr/bin/env python3
"""Paper figure: four deployed routers, comparable tokens produced, 15x apart in
the ones that count.

  motivation_throughput_vs_goodput.pdf   7.0 x 1.62 in, `figure*`, width=\\textwidth

  (a) output tokens per second the engines produced
  (b) how much of that belonged to a request that finished inside its rule
  (c) the same question counted in REQUESTS, on both denominators

FLUIDSERVE IS DELIBERATELY ABSENT. A motivation figure that needs the paper's
own system to make its point is not a motivation figure. The four arms here are
all deployed or published systems: the static class partition (PolyServe), the
two SLO-aware control planes (Llumnix SLO, llm-d) and the vLLM router's default
cache-aware policy.

(a) AND (b) SHARE A Y AXIS ON PURPOSE. The claim is that the first is nearly the
same across policies and the second is not; rescaling either would hide it. At
70 req/s the four produce 9,534 / 8,025 / 9,968 / 13,436 output tokens per second
-- a spread of 1.7x -- and turn 479 / 7,347 / 843 / 600 of them into requests
that met their rule, a spread of 15.3x. The vLLM router is the sharpest case on
its own: it produces the MOST tokens of the four and converts 4.5% of them.

(c) IS NOT A RESTATEMENT OF (b). (b) weights each request by the tokens it
produced, so a policy that keeps long requests and refuses short ones scores well
on it; (c) counts each request once.

(c) therefore carries both denominators: solid counts every request that arrived,
so a rejection is a violation; dotted counts only the requests the policy
accepted. Only Llumnix SLO and llm-d reject, so only their accepted lines are
drawn -- PolyServe and the vLLM router have no admission control at all and their
two lines would coincide exactly, which would assert a distinction the data does
not contain. llm-d's dotted line stays between 88.8 and 92.1% from 35 to 70 req/s
while its solid line falls from 42.9 to 17.7; the whole distance between them is
its rejection rate, which reaches 80.4%.

⚠ THE TWO ARMS THAT NEVER REJECT PRODUCE A DIFFERENT KIND OF MISSING REQUEST,
AND IT IS NOT ON THE FIGURE. A policy with no admission control does not refuse a
request; it leaves it unfinished when the measurement window closes. Those
requests have no outcome, so `attain()` drops them from BOTH denominators, and in
a backlogged arm they are the slow ones. At 35 / 45 / 70 req/s that share is
21.8 / 39.6 / 61.6% for the vLLM router and 37.9 / 35.9 / 35.7% for PolyServe,
against 1.0 / 0.5 / 0.3% for llm-d and 3.8 / 2.5 / 2.2% for Llumnix SLO. The
exclusion therefore favours the two arms that reject nothing. Counting every
unfinished request as a violation puts the vLLM router at 6.1 / 2.3 / 1.0 instead
of 7.8 / 3.7 / 2.5; the ordering does not change, and the caption has to say so.

DATA. The static rate sweep on the workload as fixed on 2026-08-08, eight rates
(10, 15, 20, 25, 35, 45, 55, 70 req/s), 8 minutes per condition, four engines.
The run selection is `analysis_scripts/redraw_static_sweep_workload2026-08-08.sh`
and the provenance is
`results/aggregate_analysis/static_sweep_workload2026-08-08/README.md`.

  PolyServe, Llumnix SLO   EXP-72, one repeat at every rate
  llm-d                    EXP-68/70, two repeats at 35-70 and one at 10-25
  vLLM router              EXP-77, one repeat at every rate; repeat 2 had not
                           finished when this was drawn

No error bars are drawn and no point here is noise-bounded. Session-to-session
movement on this workload has been measured at up to 4.6 points, which is far
below the differences the figure is read for but not below every comparison in
it; the README names the ones to be careful with.

⚠ NOT COMPARABLE WITH ANY EARLIER VERSION OF THIS FIGURE. Until 2026-08-10 it was
drawn from EXP-53/57, which predate the load-generator fix of 2026-08-08: a
per-worker dataset split never ran on mixed workloads, so twelve workers sent the
same prompt sequence and engine prefix cache hit rate was inflated from 28.9% to
83-86%. The arms changed too -- that version had Llumnix's load-balancing policy,
which this sweep does not include, and did not have the vLLM router.

swe is configured two ways and scored one way: llm-d and Llumnix SLO cannot
express an end-to-end budget and take the `m1f` workload configuration, PolyServe
and the vLLM router take `m1`, and all four are judged against the same 30 s
end-to-end rule.

    python3 paper_figures/fig_motivation_throughput_goodput.py
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from matplotlib.ticker import FuncFormatter  # noqa: E402
from paper_style import TEXT_W, STYLE, GRID, kfmt, save  # noqa: E402


def _load_module(path, name="m"):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EXP22 = _load_module(os.path.join(
    ROOT, "analysis_scripts/request_level/exp22_fluidserve.py"))
# Arms, colours, markers and run globs come from the intro figure so that the
# two are drawn from one selection. FluidServe is dropped here and nowhere else.
CAP = _load_module(os.path.join(HERE, "fig_intro_capacity.py"), "capfig")
# Every arm, so that both versions of the figure come from one collection pass
# and cannot disagree; the motivation version filters FluidServe out at draw
# time.
ARMS = CAP.ARMS
OURS = "FluidServe"

FIG_H = 1.62
# Plain text at the ordinary weight. These were set bold through mathtext, which
# was the only way to bold one line of a two-line x label; with the bold gone the
# mathtext goes too, and with it the layout allowance underneath -- mathtext
# reports a box taller than the glyphs it draws, which is why `rect`'s bottom
# used to sit below the canvas at -0.03.
TITLES = ["(a) Throughput",
          "(b) Goodput Tokens",
          "(c) Request SLO"]


# The pinned copy of the static sweep. `paper_experiment/static_sweep_clean_2026-08/
# manifest.tsv` names 16 runs per arm -- eight arrival rates, two repeats -- with
# a sha256 of every metrics.csv, and STATUS.md calls that directory the canonical
# static sweep.
#
# ⚠ WHY A SECOND SELECTION EXISTS. The disk cleanup of 2026-08-26 kept only the
# runs the manifest references and emptied the rest, and the EXP-86 conditions
# under `results/` were emptied while their pinned copies were kept: 24 of those
# directories still exist and contain nothing. A glob over `results/` therefore
# finds one repeat instead of two at most rates and NOTHING at three cells --
# vLLM at 45 req/s, PolyServe at 70, Llumnix SLO at 55 -- and drops them from the
# figure in silence. llm-d is unaffected because EXP-82 was not pruned.
#
# `pinned_arms()` keeps the arm names, colours, markers and order and replaces
# only the run globs. NOTHING ABOUT THE SCORING CHANGES; the same `load_run`
# reads the same files, from the directory that still has them.
PINNED = "paper_experiment/static_sweep_clean_2026-08/data"
PINNED_PATS = {
    "vLLM": [f"{PINNED}/*exp86r[12]_vllmcache_m1_rpm_*"],
    "PolyServe": [f"{PINNED}/*exp86r[12]_polyserve_m1_rpm_*"],
    "Llumnix SLO": [f"{PINNED}/*exp86r[12]_slo_m1f_rpm_*"],
    "llm-d": [f"{PINNED}/*exp82r[12]_llmdslo_m1f_rpm_*"],
}


def pinned_arms():
    """ARMS with the run globs repointed at the pinned copy, arms without one
    dropped. FluidServe has no entry, so this returns the four baselines."""
    return [(n, c, m, PINNED_PATS[n]) for (n, c, m, _) in ARMS
            if n in PINNED_PATS]


def collect(arms=None):
    """arm -> rate -> (throughput, goodput, offered, admitted, rejected)."""
    out = {}
    for name, _, _, pats in (ARMS if arms is None else arms):
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
                ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
                acc[rate].append((
                    served["output_tokens"].sum() / span,
                    ok["output_tokens"].sum() / span,
                    EXP22.per_request(r, "violate_offered"),
                    EXP22.per_request(served, "violate_served"),
                    100.0 * r["rejected"].mean(),
                ))
        out[name] = {k: tuple(float(np.mean(c)) for c in zip(*v))
                     for k, v in acc.items()}
    return out


def report(data, arms):
    print(f"{'arm':13s} {'rate':>5s} {'thru':>8s} {'goodput':>8s} "
          f"{'offered':>8s} {'admitted':>9s} {'rejected':>9s}")
    for name, _, _, _ in arms:
        for rate in sorted(data[name]):
            t, g, off, adm, rej = data[name][rate]
            print(f"{name:13s} {rate:5.0f} {t:8.0f} {g:8.0f} {off:8.1f} "
                  f"{adm:9.1f} {rej:9.1f}")


def build(data, arms, out, legend=True):
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(TEXT_W, FIG_H))
        handles, labels = [], []
        adm_key = None

        for name, col, _, _ in arms:
            x = sorted(data[name])
            thru = [data[name][k][0] for k in x]
            good = [data[name][k][1] for k in x]
            off = [data[name][k][2] for k in x]
            adm = [data[name][k][3] for k in x]
            rej = [data[name][k][4] for k in x]
            mk = dict(marker="o", ms=2.8, mec="white", mew=0.4)
            h, = ax[0].plot(x, thru, color=col, lw=1.2, **mk)
            ax[1].plot(x, good, color=col, lw=1.2, **mk)
            ax[2].plot(x, off, color=col, lw=1.2, **mk)
            # The accepted-set denominator, drawn ONLY for an arm that actually
            # rejects. PolyServe and the vLLM router have no admission control,
            # so their accepted line is their offered line; drawing it would put
            # a second curve exactly on top of the first.
            if max(rej) > 0:
                adm_h, = ax[2].plot(x, adm, color=col, ls=":", lw=1.1,
                                    marker="x", ms=3.2, mew=0.9)
                adm_key = adm_h
            handles.append(h)
            labels.append(name)

        # (b) is read against (a), so they share a y axis. Both keep their tick
        # numbers and their name: side by side as separate rectangles, a panel
        # with no y axis of its own reads as a continuation of the one left of
        # it rather than as a second measurement.
        ax[1].sharey(ax[0])

        for i in (0, 1, 2):
            # The panel title is the SECOND LINE of the x label, not a text box
            # placed in axes coordinates: `tight_layout` reserves room for an
            # axis label and knows nothing about a hand-placed artist, so the
            # hand-placed version left the panels crushed into the top.
            ax[i].set_xlabel(f"Offered rate (req/s)\n{TITLES[i]}",
                             labelpad=1.5, linespacing=1.6)
            ax[i].set_xlim(7, 73)
            ax[i].set_xticks([10, 20, 30, 40, 50, 60, 70])
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
        ax[0].set_ylim(0, 16000)
        ax[0].set_yticks([0, 5000, 10000, 15000])
        ax[0].yaxis.set_major_formatter(kfmt())
        ax[0].set_ylabel("Tokens/s")
        ax[1].set_ylabel("Tokens/s")
        ax[2].set_ylim(0, 105)
        ax[2].set_yticks([0, 25, 50, 75, 100])
        ax[2].set_ylabel("SLO attainment (%)")

        # The second denominator is kept out of the figure legend, which names
        # the four POLICIES; this is not a fifth policy but two of those four
        # counted a second way, and it exists in (c) only. The key is drawn in
        # grey rather than in one arm's colour, because two arms have such a
        # line and colouring the sample would name one of them.
        if adm_key is not None:
            grey = plt.Line2D([], [], color="#555555", ls=":", lw=1.1,
                              marker="x", ms=3.2, mew=0.9)
            ax[2].legend([grey], ["Admitted"], loc="lower right",
                         bbox_to_anchor=(1.0, 1.0), fontsize=6.5,
                         frameon=False, handlelength=1.6, handletextpad=0.35,
                         borderaxespad=0.0, borderpad=0.0)

        # With a single arm there is nothing for a policy legend to
        # distinguish, so the caller turns it off -- but `rect` does NOT change.
        # The legend row stays reserved and empty so that every three-panel
        # motivation figure has axes of exactly the same size, whichever arms it
        # carries. Reclaiming the row would make the single-arm version's panels
        # taller than the ones it is meant to be compared against.
        if legend:
            fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                       bbox_to_anchor=(0.5, 0.866), frameon=False,
                       columnspacing=1.4, handlelength=1.8, handletextpad=0.4)
        fig.tight_layout(rect=(0, 0, 1, 0.872), w_pad=3.0, pad=0.25)
        save(fig, out)


# (index into the tuple `collect` returns, panel title) for the two denominators
# the throughput/attainment/rejection layout can be drawn on.
DENOM = {"admitted": (3, "(b) Request SLO (admitted)"),
         "offered": (2, "(b) Request SLO (offered)")}


def build_admit_reject(data, arms, out, denom="admitted", legend=True):
    """Throughput, attainment among ADMITTED requests, and rejection rate.

    The same first panel as the goodput version and a different pair after it.
    Where that figure asks "how much of the output was worth anything", this one
    asks "how well did each policy serve what it chose to take, and how much did
    it choose to take" -- and answers the second question directly instead of
    leaving it to be inferred from the gap between two attainment curves.

    (b) IS THE DENOMINATOR THAT REWARDS REFUSING WORK, WHICH IS EXACTLY WHY (c)
    IS BESIDE IT. Read alone, (b) would hand the best score to a policy that
    accepted almost nothing. The two panels are only interpretable together, and
    the caption has to say so.

    THE TWO ARMS WITHOUT ADMISSION CONTROL ARE DRAWN AS THEY ARE. PolyServe and
    the vLLM router never reject, so in (b) their admitted curve IS their offered
    curve -- no second line, nothing hidden -- and in (c) they lie flat on zero.
    ⚠ AND THEY ARE ABSENT FROM (c) ENTIRELY. Their two rejection curves are
    0.0% at every rate, so they lie on the x axis and on each other and only the
    one drawn last would show its colour. A single flat line that stands for two
    policies is worse than no line, so neither is drawn. THE CAPTION MUST THEN
    SAY that PolyServe and the vLLM router reject nothing at any rate -- absence
    from that panel is a property of the policy, not missing data. Panel (c)
    therefore has two curves where the legend has four.
    """
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(TEXT_W, FIG_H))
        handles, labels = [], []

        for name, col, _, _ in arms:
            x = sorted(data[name])
            mk = dict(marker="o", ms=2.8, mec="white", mew=0.4)
            h, = ax[0].plot(x, [data[name][k][0] for k in x], color=col,
                            lw=1.2, **mk)
            ax[1].plot(x, [data[name][k][DENOM[denom][0]] for k in x],
                       color=col, lw=1.2, **mk)
            rej = [data[name][k][4] for k in x]
            # An arm that never rejects is NOT drawn in (c). PolyServe and the
            # vLLM router are at 0.0% at every rate, so their two lines lie on
            # the axis and on each other, and only the one drawn last shows its
            # colour -- a reader sees one flat line and cannot tell whether the
            # other arm is at zero or missing. Leaving them out makes the panel
            # say only what it can say, and THE CAPTION HAS TO SUPPLY THE REST:
            # the two arms absent from (c) reject nothing at any rate, which is
            # a property of having no admission control, not missing data.
            if max(rej) > 0:
                ax[2].plot(x, rej, color=col, lw=1.2, **mk)
            handles.append(h)
            labels.append(name)

        titles = ["(a) Throughput", DENOM[denom][1], "(c) Rejection rate"]
        for i in (0, 1, 2):
            ax[i].set_xlabel(f"Offered rate (req/s)\n{titles[i]}",
                             labelpad=1.5, linespacing=1.6)
            ax[i].set_xlim(7, 73)
            ax[i].set_xticks([10, 20, 30, 40, 50, 60, 70])
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
        ax[0].set_ylim(0, 16000)
        ax[0].set_yticks([0, 5000, 10000, 15000])
        ax[0].yaxis.set_major_formatter(kfmt())
        ax[0].set_ylabel("Tokens/s")
        # (b) and (c) are both percentages of the arrivals and are read against
        # each other, so they get one scale: at any rate the two readings for a
        # policy are "of what it took, this fraction was on time" and "it took
        # this much less than everything".
        for i in (1, 2):
            ax[i].set_ylim(0, 105)
            ax[i].set_yticks([0, 25, 50, 75, 100])
        ax[1].set_ylabel("SLO attainment (%)")
        ax[2].set_ylabel("Rejection rate (%)")

        # `rect` is the same with and without the legend, so this figure's
        # axes are identical in size to the four-arm version above.
        if legend:
            fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                       bbox_to_anchor=(0.5, 0.866), frameon=False,
                       columnspacing=1.4, handlelength=1.8, handletextpad=0.4)
        fig.tight_layout(rect=(0, 0, 1, 0.872), w_pad=3.0, pad=0.25)
        save(fig, out)


def build_four(data, arms, out, legend=True):
    """Goodput, throughput, attainment among ADMITTED requests, rejection rate.

      motivation_throughput_vs_goodput_4panel.pdf  7.0 x 1.62 in, width=\\textwidth

    THE SCORING IS UNCHANGED. Every value comes from the same `collect()` pass,
    and therefore the same `EXP22.load_run` and `EXP22.per_request`, as the
    three-panel versions above; this function only chooses which four of the
    five collected quantities to draw and in what order. A number read off this
    figure and a number read off `motivation_throughput_vs_goodput.pdf` for the
    same arm and rate are the same number.

    WHY THIS ORDER. (a) leads with the quantity the figure is an argument about
    -- output that belonged to a request that met its rule -- and (b) puts the
    raw token rate immediately beside it, sharing one y axis, so the reader sees
    that the two differ by an order of magnitude while the engines were producing
    comparable amounts. (c) and (d) then answer the same question counted in
    requests rather than tokens, and they also share one y axis because both are
    percentages of the same arrivals.

    (c) IS THE ADMITTED DENOMINATOR, WHICH REWARDS REFUSING WORK. That is why (d)
    is beside it and why the two must be read as a pair: (c) alone hands the best
    score to a policy that accepted almost nothing, and (d) says how much each
    policy accepted. This is the pairing that the three-panel `build()` cannot
    make, because there the second denominator is carried as a dotted overlay and
    the amount refused has to be inferred from the gap between two curves.

    ⚠ AN ARM THAT NEVER REJECTS IS ABSENT FROM (d), NOT MISSING. PolyServe and
    the vLLM router have no admission control, so their rejection curves are
    0.0% at every rate: drawn, they would lie on the x axis and on each other,
    and only the one drawn last would show its colour, leaving a reader unable to
    tell whether the other arm is at zero or absent. THE CAPTION MUST SAY that
    the two arms missing from (d) reject nothing at any rate, which is a property
    of the policy. Panel (d) therefore carries two curves where the legend names
    four. This is the same decision, for the same reason, as in
    `build_admit_reject`.

    X TICKS ARE MEASURED RATES. Four panels across 7.0 in leave about 1.4 in of
    plotting area each, which will not carry seven labels at 8 pt. The labelled
    subset is 10, 25, 45 and 70 req/s and the remaining measured rates -- 15, 20,
    35, 55 -- get unlabelled minor ticks. The three-panel figures label 10 to 70
    in steps of ten, four of which (30, 40, 50, 60) were never measured; this one
    does not introduce round numbers that no condition ran at.
    """
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, 4, figsize=(TEXT_W, FIG_H))
        handles, labels = [], []

        for name, col, _, _ in arms:
            x = sorted(data[name])
            mk = dict(marker="o", ms=2.8, mec="white", mew=0.4)
            # (a) goodput, (b) throughput. `h` is taken from the panel the
            # legend should point at, which is the one the figure argues from.
            h, = ax[0].plot(x, [data[name][k][1] for k in x], color=col,
                            lw=1.2, **mk)
            ax[1].plot(x, [data[name][k][0] for k in x], color=col,
                       lw=1.2, **mk)
            # (c) attainment on the ADMITTED denominator, index 3.
            ax[2].plot(x, [data[name][k][3] for k in x], color=col,
                       lw=1.2, **mk)
            rej = [data[name][k][4] for k in x]
            if max(rej) > 0:
                ax[3].plot(x, rej, color=col, lw=1.2, **mk)
            handles.append(h)
            labels.append(name)

        titles = ["(a) Goodput Tokens", "(b) Throughput",
                  "(c) Request SLO (admitted)", "(d) Rejection rate"]
        for i in range(4):
            # The panel title is the SECOND LINE of the x label rather than a
            # hand-placed text artist: `tight_layout` reserves room for an axis
            # label and knows nothing about an artist placed in axes
            # coordinates, which left the panels crushed into the top.
            ax[i].set_xlabel(f"Offered rate (req/s)\n{titles[i]}",
                             labelpad=1.5, linespacing=1.6)
            ax[i].set_xlim(7, 73)
            ax[i].set_xticks([10, 25, 45, 70])
            ax[i].set_xticks([15, 20, 35, 55], minor=True)
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)

        # (a) and (b) share one y axis on purpose: the claim is that the token
        # rate the engines produced is nearly the same across policies and the
        # part of it that counted is not, and rescaling either panel would hide
        # exactly that. Both keep their tick numbers and their name -- side by
        # side as separate rectangles, a panel with no y axis of its own reads
        # as a continuation of the one left of it rather than as a second
        # measurement.
        ax[1].sharey(ax[0])
        ax[0].set_ylim(0, 16000)
        ax[0].set_yticks([0, 5000, 10000, 15000])
        ax[0].yaxis.set_major_formatter(kfmt())
        ax[1].yaxis.set_major_formatter(kfmt())
        ax[0].set_ylabel("Tokens/s")
        ax[1].set_ylabel("Tokens/s")
        # (c) and (d) are both percentages of the same arrivals and are read
        # against each other, so they get one scale: at any rate the pair reads
        # "of what this policy took, this fraction was on time" beside "it took
        # this much less than everything that arrived".
        for i in (2, 3):
            ax[i].set_ylim(0, 105)
            ax[i].set_yticks([0, 25, 50, 75, 100])
        ax[2].set_ylabel("SLO attainment (%)")
        ax[3].set_ylabel("Rejection rate (%)")

        # `rect` is identical to the three-panel builders, so the axes of this
        # figure are the same height as theirs and the two can be read as the
        # same measurement at different widths.
        if legend:
            fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                       bbox_to_anchor=(0.5, 0.866), frameon=False,
                       columnspacing=1.4, handlelength=1.8, handletextpad=0.4)
        fig.tight_layout(rect=(0, 0, 1, 0.872), w_pad=2.2, pad=0.25)
        save(fig, out)


def build_one_arm_norm(data, arms, out, stacked=True, phases=False):
    """Two panels -- normalised throughput and attainment -- for one arm or two.

    THE THROUGHPUT AXIS IS DIVIDED BY A CONSTANT, so it runs 0 to 1 and the two
    panels can be read against each other as fractions of their own ceilings.
    That is the whole reason to normalise: the claim is about the SHAPES --
    output rises and then holds while the share of requests meeting their rule
    falls away -- and with one axis in tokens per second and the other in
    percent the eye has to do a conversion to see it.

    ⚠ WITH MORE THAN ONE ARM THE DIVISOR IS SHARED: the largest throughput
    reached by ANY arm at ANY rate, not each arm's own peak. Per-arm
    normalisation would put every curve's maximum at 1.0 and make a panel whose
    only purpose is comparison say nothing about which arm produced more. The
    cost is that no curve except the highest one touches 1.0, which is correct.

    ⚠ NORMALISING THROWS AWAY THE UNIT, so the divisor has to travel with the
    figure. It is printed when the script runs and belongs in the caption; a
    reader cannot recover tokens per second from this panel otherwise.
    """
    peak = max(float(max(v[0] for v in data[name].values()))
               for name, _, _, _ in arms)
    who = max(arms, key=lambda a: max(v[0] for v in data[a[0]].values()))[0]
    print(f"  normalised by {peak:,.0f} tokens/s, the highest any arm reached "
          f"({who})")

    with plt.rc_context(STYLE):
        # 1.42 in for one arm, 1.58 for two: the extra 0.16 is the legend row,
        # and the axes boxes are the same size either way so the two versions
        # can be compared. 1.42 is itself down from 1.72, about 20% off the
        # panel height, which the y label of (a) broken over two lines allowed
        # -- rotated, "Normalized throughput" on one line is about 1.15 in and
        # was taller than the panel, so it was the floor on the whole figure.
        # The width stays at the column: the canvas cannot narrow without
        # leaving white space beside a figure meant to be set at \columnwidth.
        #
        # Panel titles are dropped for the same reason (the x label is one
        # line), so the caption refers to these panels as left and right. 1.40
        # is the floor: at that height the topmost ink lands 4 px from the
        # canvas edge at 400 dpi.
        # STACKED, not side by side. The two panels share the x axis, so the
        # rate is drawn and labelled once, and the reader compares the two
        # quantities by looking straight down a load rather than across a gap.
        # It costs height. Rotated, the two y labels are about 0.95 in
        # ("Throughput (norm.)") and 1.05 in ("SLO attainment (%)"), and a label
        # longer than its own panel spills past it -- at 2.55 in the two ran
        # into each other at the boundary between the panels. Each panel has to
        # be at least as tall as its label, which sets the canvas.
        legend = len(arms) > 1
        if stacked:
            fig, ax = plt.subplots(2, 1, sharex=True,
                                   figsize=(3.335, 2.90 if legend else 2.75))
        else:
            # Side by side. The two panels no longer share an x axis, so the
            # rate is drawn and named twice, and the canvas is short instead of
            # tall: 1.55 in, which leaves each panel just over the 0.95 in the
            # longer y label needs when rotated.
            fig, ax = plt.subplots(1, 2,
                                   figsize=(3.335, 1.70 if legend else 1.55))
        mk = dict(marker="o", ms=2.8, mec="white", mew=0.4)
        handles, labels = [], []
        for name, col, _, _ in arms:
            x = sorted(data[name])
            h, = ax[0].plot(x, [data[name][k][0] / peak for k in x],
                            color=col, lw=1.2, **mk)
            ax[1].plot(x, [data[name][k][3] for k in x], color=col, lw=1.2,
                       **mk)
            handles.append(h)
            labels.append(name)

        # THE TWO PHASE BOUNDARIES, computed from this arm's own curves rather
        # than chosen: the load at which throughput is highest, and the load at
        # which attainment falls through 90% of arrivals (linear interpolation
        # between the two measured rates that bracket it, the same definition
        # `fig_intro_capacity.py` uses for capacity). They are only drawn for a
        # single arm -- with two arms on the axes there would be two of each and
        # the shading would say nothing.
        # `phases` is either False, True (the only arm defines them) or the
        # name of the arm that does. With two arms each has its own pair of
        # boundaries, and shading both would leave four bands that belong to
        # nothing; one arm's bands, NAMED IN THE CAPTION, say more.
        pa = arms[0][0] if phases is True else phases
        if pa:
            xs = sorted(data[pa])
            th = [data[pa][k][0] for k in xs]
            at = [data[pa][k][DENOM["admitted"][0]] for k in xs]
            peak = xs[int(np.argmax(th))]
            knee = xs[-1]
            for j in range(len(xs)):
                if at[j] < 90.0:
                    knee = xs[j] if j == 0 else (
                        xs[j - 1] + (at[j - 1] - 90.0) * (xs[j] - xs[j - 1])
                        / (at[j - 1] - at[j]))
                    break
            b = sorted([knee, peak])
            print(f"  phases from {pa}: SLO knee {knee:.1f}, throughput peak "
                  f"{peak:.0f} req/s")
            # Three bands, lightest first, so the eye reads left to right as
            # "nothing wrong / one thing wrong / both wrong". The shading is the
            # phase and the line is its edge; drawing only the line leaves the
            # reader to decide which side of it each phase is on.
            edges = [7.0] + b + [73.0]
            for i in (0, 1):
                for k, (lo, hi) in enumerate(zip(edges[:-1], edges[1:])):
                    ax[i].axvspan(lo, hi, color="#000000",
                                  alpha=(0.0, 0.05, 0.10)[k], lw=0, zorder=0)
                for v in b:
                    ax[i].axvline(v, color="#777777", lw=0.8, ls="--",
                                  zorder=1)
            # The numerals go in BOTH panels. The bands are the same x in each,
            # but a reader looking at the right panel should not have to carry
            # "the middle band is II" across the gap from the left one.
            for k, name in enumerate(("I", "II", "III")):
                mid = 0.5 * (edges[k] + edges[k + 1])
                for i in (0, 1):
                    ax[i].annotate(name, (mid, 1.0),
                                   xycoords=("data", "axes fraction"),
                                   xytext=(0, -7), textcoords="offset points",
                                   color="#555555", fontsize=6.5, ha="center",
                                   va="top")

        # One x label under the bottom panel when the axis is shared; one under
        # each panel when it is not.
        for i in ([1] if stacked else [0, 1]):
            ax[i].set_xlabel("Offered rate (req/s)", labelpad=1.5)
        for i in (0, 1):
            ax[i].set_xlim(7, 73)
            ax[i].set_xticks([10, 30, 50, 70])
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
        # Both panels run 0 to their own ceiling on the SAME gridlines, so a
        # fall in one is directly comparable with a fall in the other. Fifths
        # rather than quarters: the ticks are labelled to one decimal, and
        # 0.25 printed that way would read "0.2", which is a different number.
        # (b) moves to twentieths for the same reason -- to keep the two panels
        # on matching gridlines.
        ax[0].set_ylim(0, 1.05)
        ax[0].set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
        ax[0].yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.1f}"))
        ax[0].set_ylabel("Throughput (norm.)")
        ax[1].set_ylim(0, 105)
        ax[1].set_yticks([0, 20, 40, 60, 80, 100])
        ax[1].set_ylabel("SLO attainment (%)")

        # With one arm there is no legend AND no row reserved for one: unlike
        # the single-arm versions of the three-panel figures, the canvas shrinks
        # instead, because this shape has no multi-arm counterpart of the same
        # canvas to line up with.
        top = 1.0
        if legend:
            # The legend needs a fixed physical height, so the fraction is
            # computed from the canvas rather than written as a constant.
            top = 1.0 - 0.20 / fig.get_size_inches()[1]
            fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                       bbox_to_anchor=(0.5, top - 0.006), frameon=False,
                       fontsize=7, columnspacing=1.0, handlelength=1.4,
                       handletextpad=0.35, borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, top), h_pad=0.7, w_pad=1.0, pad=0.3)
        save(fig, out)


def main():
    data = collect()
    present = [a for a in ARMS if data[a[0]]]
    if len(present) < 2:
        sys.exit("fewer than two arms have conditions")
    report(data, present)

    # The motivation version, without our own system. A motivation figure that
    # needs the paper's system to make its point is not a motivation figure.
    build(data, [a for a in present if a[0] != OURS],
          os.path.join(HERE, "motivation_throughput_vs_goodput.pdf"))
    # The same three panels with FluidServe added. This one is NOT a motivation
    # figure -- it is the result read on the same axes -- so it belongs wherever
    # the paper has already introduced the system, and its caption has to say
    # which curve is ours.
    build(data, present,
          os.path.join(HERE, "motivation_throughput_vs_goodput_withfs.pdf"))
    # FluidServe alone, no policy legend: one arm names itself in the caption.
    # The panels are the same three, so this reads as the same measurement with
    # the baselines lifted off rather than as a different figure.
    build(data, [a for a in present if a[0] == OURS],
          os.path.join(HERE, "motivation_throughput_vs_goodput_fsonly.pdf"),
          legend=False)
    # The four-panel version of the same measurement, baselines only: goodput,
    # throughput, attainment on the admitted denominator, rejection rate. Same
    # `collect()` pass and therefore the same scoring as every figure above; the
    # difference is that the amount refused is a panel of its own rather than
    # the gap between a solid and a dotted curve.
    # ⚠ THIS ONE IS DRAWN FROM THE PINNED COPY, not from `results/`. See
    # `pinned_arms()` for why: the EXP-86 directories under `results/` were
    # emptied by the 2026-08-26 cleanup, so a `results/` glob silently loses
    # three rate cells and half the repeats at the rest. The figures above still
    # use the `results/` selection, so THIS FIGURE AND ITS THREE-PANEL SIBLINGS
    # ARE NOT DRAWN FROM THE SAME SET until that selection is repointed too --
    # do not read a number off one and quote it beside a number off the other.
    pinned = pinned_arms()
    pdata = collect(pinned)
    ppresent = [a for a in pinned if pdata[a[0]]]
    print("\n--- four-panel, pinned selection ---")
    report(pdata, ppresent)
    build_four(pdata, ppresent,
               os.path.join(HERE,
                            "motivation_throughput_vs_goodput_4panel.pdf"))
    # Throughput / admitted attainment / rejection rate, baselines only. Our
    # system is absent from all three panels for the same reason as above.
    build_admit_reject(data, [a for a in present if a[0] != OURS],
                       os.path.join(HERE, "motivation_admitted_reject.pdf"))
    # The same three panels for our system alone, same denominator, same axes
    # size. ⚠ (b) is the ADMITTED denominator, which rewards refusing work, and
    # with a single arm there is no baseline beside it to check that. What
    # checks it is (c) in the same figure: at 70 req/s the 88.4% in (b) is
    # scored on the 43.9% of arrivals that were accepted. The caption has to
    # carry that pair; the offered number for the same point is 38.2%.
    build_admit_reject(data, [a for a in present if a[0] == OURS],
                       os.path.join(HERE,
                                    "motivation_admitted_reject_fsonly.pdf"),
                       denom="admitted", legend=False)
    # The vLLM router alone, two panels, throughput normalised. It never
    # rejects, so its admitted curve and its offered curve are the same line
    # and the choice of denominator in (b) does not arise.
    vllm = [a for a in present if a[0] == "vLLM"]
    if vllm:
        build_one_arm_norm(data, vllm,
                           os.path.join(HERE, "motivation_vllm_2panel.pdf"))
        # The same two panels side by side rather than stacked.
        build_one_arm_norm(data, vllm,
                           os.path.join(HERE, "motivation_vllm_2panel_lr.pdf"),
                           stacked=False, phases=True)
        # The same two panels with our system beside it. Both throughput curves
        # are divided by the SAME number, so the panel still says which arm
        # produced more.
        both = vllm + [a for a in present if a[0] == OURS]
        if len(both) > 1:
            build_one_arm_norm(data, both,
                               os.path.join(HERE,
                                            "motivation_vllm_fs_2panel.pdf"))
        # The two arms with no admission control between them: the vLLM router
        # refuses nothing, llm-d refuses most of the load. Both throughput
        # curves are divided by the SAME number, so the left panel still says
        # which of the two produced more.
        vl = vllm + [a for a in present if a[0] == "llm-d"]
        if len(vl) > 1:
            build_one_arm_norm(data, vl,
                               os.path.join(HERE,
                                            "motivation_vllm_llmd_2panel.pdf"))
            # Side by side with llm-d beside it, banded by the VLLM ROUTER's
            # two boundaries. llm-d has its own and they are not these; the
            # caption has to say whose the shading is.
            build_one_arm_norm(data, vl,
                               os.path.join(
                                   HERE,
                                   "motivation_vllm_llmd_2panel_lr.pdf"),
                               stacked=False, phases="vLLM")
    return 0


if __name__ == "__main__":
    sys.exit(main())
