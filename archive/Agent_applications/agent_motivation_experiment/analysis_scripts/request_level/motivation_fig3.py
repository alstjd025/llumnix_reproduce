#!/usr/bin/env python3
"""Motivation figure 3: the fleet's capacity is set by the routing policy.

The question this answers is "how much load can these four engines take?", and
the answer is that the question is not well posed. The same four engines, the
same request mix, the same rule for when they are counted as saturated, give
answers between 26.7 and 45.1 requests per second depending only on which policy
decides where each request goes.

How the prediction on the right-hand panel is built. EXP-55 ran each class ALONE
on the same four engines, so no routing decision could differentiate anything
and what came out is a property of the class and the hardware rather than of any
policy. Each class alone saturates at its own rate:

    chat            65.6 req/s
    swe             23.1
    deep research   16.5

If the fleet had a single resource and each class consumed a fixed share of it,
a mix in which class c is a fraction f_c of the requests would saturate at

    R = 1 / sum_c (f_c / K_c)

which for this mix (chat 10 : deep research 2 : swe 1 by request count) is 41.0
req/s. That is not our model of the system and it is not what FluidServe
computes. It is the simplest thing anyone would assume before measuring, and the
point of the figure is that measurement does not land on it: three existing
policies come in at 0.65, 0.89 and 0.90 of it.

Panel A says why the prediction is not a formality. The three classes divide the
fleet's capacity in proportions that match neither their share of the requests
nor their share of the input tokens. Deep research is 15.4% of the requests,
43.5% of the input tokens and 38.3% of the capacity; chat is 76.9%, 30.4% and
48.1%. There is no unit in which this workload is three quantities of the same
thing, so a policy that counts requests, or bytes, or queue entries, is counting
the wrong thing whichever it picks.

Panel C is the robustness check. The saturation point depends on where the line
for "saturated" is drawn, so the whole calculation is repeated at four
thresholds. Every ratio moves by less than the gaps between the policies and the
ordering never changes.

FluidServe is drawn set apart and is not part of the motivating claim, which is
made by the three existing policies alone. It is on the figure because the
distance from 0.90 to 1.10 is what the rest of the paper is about.

CAVEAT that has to be stated wherever these numbers are: the single-class
measurement is ONE repeat per rate (EXP-55, 8-minute conditions). The mixed
measurements are two to three repeats (EXP-53). Two earlier EXP-55 passes exist
at 4 minutes per condition and are excluded, not averaged in: at rates past
saturation a 4-minute condition is measured largely while the queue is still
filling and reads far higher than the same rate held for 8 minutes -- deep
research at 30 req/s reads 61.3% attainment over 4 minutes and 27.5% over 8.

  python3 motivation_fig3.py <out-dir>
"""
import argparse
import glob
import os
import re
import sys
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run, attain  # noqa: E402

# Request counts and measured mean input tokens, both from
# workload_configs/mix_short_m1_balanced.json, which is the file the load
# generator reads. The token means are measured at the gateway rather than
# estimated from the transcripts.
MIX_COUNT = {"chat": 10, "deepresearch": 2, "swe": 1}
MIX_INTOK = {"chat": 649, "deepresearch": 4639, "swe": 5557}

CLS_KEY = {"schat": "chat", "sdr": "deepresearch", "sswe": "swe"}
CLS_C = {"chat": "#1f77b4", "deepresearch": "#ff7f0e", "swe": "#d62728"}
CLS_LABEL = {"chat": "chat", "deepresearch": "deep research", "swe": "swe"}

# Three existing policies plus ours, in the order they are drawn. The fourth is
# separated in the figure and excluded from the motivating claim.
ARMS = [("polyserve", "PolyServe\n(static partition)", "#d62728"),
        ("loadbalance", "Llumnix\n(load balance)", "#9467bd"),
        ("slo", "Llumnix SLO\n(latency aware)", "#2ca02c"),
        # llm-d is not in the default selection. Its sweep is EXP-66, a
        # different session from EXP-53's, and it is scored from the m1f
        # workload config because it cannot express an end-to-end budget. Both
        # are stated on the figure when --arms asks for it.
        ("llmdslo", "llm-d\n(predicted latency)", "#8c564b"),
        ("fluidserve", "FluidServe\n(this paper)", "#1f77b4")]
BASELINES = {"polyserve", "loadbalance", "slo", "llmdslo"}
DEFAULT_ARMS = ["polyserve", "loadbalance", "slo", "fluidserve"]

CRITERIA = [95.0, 90.0, 80.0, 70.0]
MAIN = 90.0


def crossing(rate, off, level):
    """The rate at which offered attainment falls through `level`.

    Linear interpolation between the two bracketing conditions. Returns the
    lowest measured rate when the sweep is already below the level at its first
    point, which would mean the sweep started past saturation.
    """
    o = np.argsort(rate)
    x, y = np.asarray(rate)[o], np.asarray(off)[o]
    below = np.where(y < level)[0]
    if len(below) == 0:
        return np.nan
    i = below[0]
    if i == 0:
        return x[0]
    x0, x1, y0, y1 = x[i - 1], x[i], y[i - 1], y[i]
    return x1 if y0 == y1 else x0 + (y0 - level) * (x1 - x0) / (y0 - y1)


def single_class(pattern="results/*exp55r1*"):
    """EXP-55: one class at a time. 8-minute conditions only -- see the caveat."""
    rows = []
    for d in sorted(glob.glob(pattern)):
        m = re.search(r"_(schat|sdr|sswe)_rpm_(\d+)$", os.path.basename(d))
        if not m:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        rows.append(dict(cls=CLS_KEY[m.group(1)], rate=int(m.group(2)) / 60.0,
                         off=attain(r, "violate_offered")))
    return pd.DataFrame(rows)


def _supersede(df):
    """Drop the EXP-53 PolyServe rows once EXP-57 has re-measured that arm.

    Averaging the two would mix a stale tier length table with a corrected one.
    Prints what it dropped, because a silent supersede is how a figure ends up
    describing runs nobody chose.
    """
    if df.empty or "src" not in df:
        return df
    have_new = ((df["arm"] == "polyserve") & (df["src"] == "exp57")).any()
    if not have_new:
        print("  PolyServe: EXP-53 runs only (stale tier table -- see EXP-57)")
        return df
    old = (df["arm"] == "polyserve") & (df["src"] == "exp53")
    print(f"  PolyServe: using {int((~old & (df.arm=='polyserve')).sum())} EXP-57 "
          f"conditions, superseding {int(old.sum())} from EXP-53")
    return df[~old]


# PolyServe's tier length table was corrected on 2026-08-05 and its arm
# re-measured as EXP-57; its EXP-53 runs used the stale table, which understated
# deep research by 3.58x and fed the repartitioner, so they are SUPERSEDED rather
# than averaged in. The other arms never read that flag and stay on EXP-53.
#
# Joining the two sessions was checked rather than assumed: Llumnix SLO, whose
# code and configuration did not change, was re-run in the EXP-57 session at 45
# and 70 req/s and read 52.3 and 21.7 against EXP-53's 52.4 and 21.4, inside
# EXP-53's own repeat spread. Those two conditions are excluded from the sweep
# below so the repeat count stays even across rates.
def mixed(patterns=("results/*exp5[37]*_rpm_*",)):
    """The mix, one row per (arm, rate). Repeats averaged before interpolating.

    Takes a list of globs so an arm measured in its own experiment can be added
    without loosening the default one. llm-d's sweep is EXP-66 and lives under a
    different session tag; passing its glob here is what puts it on the figure.
    """
    rows = []
    seen = set()
    for pattern in patterns:
      for d in sorted(glob.glob(pattern)):
        base = os.path.basename(d)
        if "unchanged" in base or "PRERUN" in base or d in seen:
            continue
        seen.add(d)
        m = re.search(r"_(polyserve|slo|loadbalance|fluidserve|llmdslo)_m1f?_rpm_(\d+)$", base)
        if not m:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        rows.append(dict(arm=m.group(1), rate=int(m.group(2)) / 60.0,
                         src="exp57" if "exp57" in base else "exp53",
                         off=attain(r, "violate_offered")))
      # end for d
    df = _supersede(pd.DataFrame(rows))
    return df.groupby(["arm", "rate"], as_index=False).agg(
        off=("off", "mean"), n=("off", "size"))


def knees(sc, mx, level):
    """Single-class saturation, the additive prediction, and each policy's."""
    k = {c: crossing(g.rate, g.off, level) for c, g in sc.groupby("cls")}
    tot = sum(MIX_COUNT.values())
    share = {c: MIX_COUNT[c] / tot for c in MIX_COUNT}
    inv = sum(share[c] / k[c] for c in share)
    pred = 1.0 / inv
    got = {a: crossing(g.rate, g.off, level) for a, g in mx.groupby("arm")}
    # Each class's share of the predicted capacity: the term it contributes to
    # the sum that the prediction inverts.
    cap_share = {c: (share[c] / k[c]) / inv for c in share}
    return k, pred, got, share, cap_share


def main(out, arms=None, extra_patterns=(), out_name="motivation_capacity_is_a_policy.png",
         extra_note=""):
    arms = list(arms or DEFAULT_ARMS)
    sc = single_class()
    mx = mixed(("results/*exp5[37]*_rpm_*",) + tuple(extra_patterns))
    mx = mx[mx.arm.isin(arms)]
    if sc.empty or mx.empty:
        sys.exit("no EXP-55 or EXP-53 runs matched")
    missing = [a for a in arms if a not in set(mx.arm)]
    if missing:
        sys.exit(f"asked for arms {missing} but no runs matched them; "
                 f"pass their glob with --extra")

    k, pred, got, share, cap = knees(sc, mx, MAIN)
    tok_tot = sum(MIX_COUNT[c] * MIX_INTOK[c] for c in MIX_COUNT)
    tok = {c: MIX_COUNT[c] * MIX_INTOK[c] / tok_tot for c in MIX_COUNT}

    print(f"single-class saturation at {MAIN:.0f}% offered attainment "
          f"(EXP-55, 1 repeat)")
    for c in ("chat", "deepresearch", "swe"):
        print(f"  {c:<14}{k[c]:6.2f} req/s")
    print(f"\nadditive prediction for the mix: {pred:.2f} req/s")
    print(f"{'class':<14}{'of requests':>13}{'of input tokens':>17}"
          f"{'of capacity':>13}")
    for c in ("chat", "deepresearch", "swe"):
        print(f"  {c:<12}{100*share[c]:>12.1f}%{100*tok[c]:>16.1f}%"
              f"{100*cap[c]:>12.1f}%")

    print(f"\nmeasured saturation of the mix (EXP-53, repeats averaged)")
    for a, lab, _ in ARMS:
        if a in got:
            n = int(mx[mx.arm == a]["n"].min())
            print(f"  {a:<12}{got[a]:6.2f} req/s   "
                  f"{got[a]/pred:5.2f} x prediction   (>= {n} repeats/point)")

    sens = {}
    for lv in CRITERIA:
        _, p_, g_, _, _ = knees(sc, mx, lv)
        sens[lv] = {a: g_[a] / p_ for a in g_}
    print("\nsensitivity to where 'saturated' is drawn (ratio to prediction)")
    print("  " + "".join(f"{lv:>8.0f}%" for lv in CRITERIA))
    for a, _, _ in ARMS:
        if a in got:
            print(f"  {a:<12}" + "".join(f"{sens[lv][a]:>8.2f}" for lv in CRITERIA))

    os.makedirs(out, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(11.2, 3.1),
                               gridspec_kw={"width_ratios": [1.0, 1.25, 0.85]})

        # ---- A: three accountings of the same workload, and they disagree
        bars = [("by request\ncount", share), ("by input\ntokens", tok),
                ("by fleet\ncapacity", cap)]
        for i, (lab, d) in enumerate(bars):
            bot = 0.0
            for c in ("chat", "deepresearch", "swe"):
                v = 100 * d[c]
                ax[0].bar(i, v, bottom=bot, color=CLS_C[c], width=0.66,
                          label=CLS_LABEL[c] if i == 0 else None)
                if v > 7:
                    ax[0].annotate(f"{v:.0f}%", (i, bot + v / 2), ha="center",
                                   va="center", fontsize=7, color="white",
                                   weight="bold")
                bot += v
        ax[0].set_xticks(range(len(bars)))
        ax[0].set_xticklabels([b[0] for b in bars], fontsize=7)
        ax[0].set_ylim(0, 100)
        ax[0].set_ylabel("share of the workload (%)")
        ax[0].set_title("A. the same mix, counted three ways", fontsize=8)
        ax[0].legend(loc="upper center", bbox_to_anchor=(0.5, -0.14), ncol=3,
                     fontsize=6.5, columnspacing=0.9)

        # ---- B: the prediction against what each policy delivers
        keys = [a for a, _, _ in ARMS if a in got]
        xs = np.arange(len(keys))
        for i, a in enumerate(keys):
            lab, col = next((l, c) for k_, l, c in ARMS if k_ == a)
            base = a in BASELINES
            ax[1].bar(i, got[a], color=col, width=0.6,
                      alpha=1.0 if base else 0.30,
                      hatch=None if base else "////",
                      edgecolor=col, lw=0 if base else 1.0)
            ax[1].annotate(f"{got[a]:.1f}\n{got[a]/pred:.2f}x",
                           (i, got[a] + 1.2), ha="center", va="bottom",
                           fontsize=7, color=col, weight="bold")
        ax[1].axhline(pred, color="#333333", ls="--", lw=1.1)
        # Clear of every bar and every value label, with a leader to the line.
        # Placing it on the line collided with the second bar's label, and below
        # it collided with the same label from the other side.
        ax[1].annotate(f"predicted {pred:.1f} req/s, from the three\n"
                       f"classes measured one at a time",
                       xy=(0.62, pred), xytext=(-0.44, pred + 8.5),
                       ha="left", va="bottom", fontsize=6.8, color="#333333",
                       arrowprops=dict(arrowstyle="-", color="#333333", lw=0.7))
        ax[1].set_xticks(xs)
        ax[1].set_xticklabels([next(l for k_, l, _ in ARMS if k_ == a)
                               for a in keys], fontsize=6.5)
        ax[1].set_ylim(0, max(list(got.values()) + [pred]) * 1.30)
        ax[1].set_ylabel("offered rate the fleet sustains (req/s)")
        ax[1].set_title("B. the same four engines, the same mix", fontsize=8)
        ax[1].grid(axis="y", ls=":", lw=0.7, alpha=0.6)

        # ---- C: and the ordering does not depend on where the line is drawn
        for a, lab, col in ARMS:
            if a not in got:
                continue
            ax[2].plot(CRITERIA, [sens[lv][a] for lv in CRITERIA], color=col,
                       marker="o", ms=3.5, mec="white", mew=0.5,
                       ls="-" if a in BASELINES else "--",
                       label=lab.split("\n")[0])
        ax[2].axhline(1.0, color="#333333", ls="--", lw=1.0)
        ax[2].set_xticks(CRITERIA)
        ax[2].set_xticklabels([f"{c:.0f}%" for c in CRITERIA], fontsize=7)
        ax[2].invert_xaxis()
        ax[2].set_xlabel("attainment counted as saturated", fontsize=7)
        ax[2].set_ylabel("measured / predicted")
        lo = min(v for lv in CRITERIA for v in sens[lv].values())
        hi = max(v for lv in CRITERIA for v in sens[lv].values())
        # Never clip: an arm whose ratio swings far more than the others is
        # telling you its curve has a different shape, which is exactly what
        # this panel is for. Fixed limits hid llm-d running to 1.43.
        ax[2].set_ylim(min(0.5, lo - 0.05), max(1.35, hi + 0.05))
        ax[2].set_title("C. and it is not the threshold", fontsize=8)
        ax[2].grid(ls=":", lw=0.7, alpha=0.6)
        ax[2].legend(loc="lower right", fontsize=6, handlelength=1.6,
                     borderpad=0.3, labelspacing=0.25)

        base_lo = min(got[a] for a in keys if a in BASELINES)
        base_hi = max(got[a] for a in keys if a in BASELINES)
        nbase = sum(1 for a in keys if a in BASELINES)
        # Every number and every count in this sentence comes from the table the
        # panels are drawn from. It used to say "one of them exceeds it", which
        # stopped being true the moment a fourth arm was added: with llm-d on the
        # figure two exceed the prediction. A caption that states a count has to
        # compute it.
        over = [next(l for k_, l, _ in ARMS if k_ == a).split("\n")[0]
                for a in keys if got[a] > pred]
        if not over:
            verdict = "None of them lands on it, and none exceeds it."
        elif len(over) == 1:
            verdict = f"None of them lands on it, and one exceeds it ({over[0]})."
        else:
            verdict = (f"None of them lands on it, and {len(over)} exceed it "
                       f"({', '.join(over)}).")
        fig.suptitle(
            f"How much load do four engines take? The question has no single "
            f"answer. With the mix and the hardware held fixed, the choice of "
            f"routing policy alone moves it by {base_hi/base_lo:.1f}x across "
            f"the {nbase} existing policies\nand {max(got.values())/base_lo:.1f}x "
            f"including ours. The dashed line is what the three classes, "
            f"measured one at a time on the same engines, predict if they "
            f"competed for a single resource\nin fixed proportions. " + verdict,
            fontsize=8, y=1.14)
        if extra_note:
            # tight_layout has already packed the axes against the bottom, so a
            # note placed at a negative y lands on the tick labels. Reserve the
            # space first, then draw into it.
            n = textwrap.fill(extra_note, 150)
            fig.subplots_adjust(bottom=0.26 + 0.030 * n.count("\n"))
            fig.text(0.5, 0.015, n, ha="center", va="bottom", fontsize=7,
                     color="#444444")
        p = os.path.join(out, out_name)
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print(f"\nwrote {p}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("out", nargs="?", default="results/aggregate_analysis/motivation")
    ap.add_argument("--arms", nargs="+", default=None,
                    help=f"which arms to draw; default {DEFAULT_ARMS}")
    ap.add_argument("--extra", nargs="*", default=[],
                    help="additional run globs, for an arm measured in its own "
                         "experiment (e.g. llm-d's EXP-66 sweep)")
    ap.add_argument("--out-name", default="motivation_capacity_is_a_policy.png")
    ap.add_argument("--note", default="",
                    help="a line under the figure, for what the selection mixes")
    a = ap.parse_args()
    main(a.out, a.arms, a.extra, a.out_name, a.note)
