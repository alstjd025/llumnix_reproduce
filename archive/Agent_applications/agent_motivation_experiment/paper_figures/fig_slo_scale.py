#!/usr/bin/env python3
"""Paper figure: offered SLO attainment against the SLO scale, three fleets.

  slo_scale.pdf   7.0 x 2.05 in, `figure*`, width=\\textwidth
  slo_scale.csv   the plotted series, one row per (fleet, arm, k)

WHAT IT CLAIMS. Whether one control plane leads another is, on one fleet, a
statement about the scoring rule and, on another, not. The four-instance
Qwen2.5-72B cell holds its ordering across the whole multiplier range; the
four-instance Llama-3.1-70B cell does not, and PolyServe passes FluidServe at
k = 1.5; the eight-instance Llama-3.1-8B cell is lost at every multiplier. That
the three panels behave differently is the result, not a defect in one of them.

HOW THE CURVES ARE MADE. They are RE-SCORES of runs already on disk, not new
runs. `exp22_fluidserve.load_run` derives every scored quantity from columns of
`metrics.csv` and reads the rule from the module constant `SLO_RULES`, so
multiplying that constant and calling it again is a pure re-score;
`analysis_scripts/request_level/slo_scale_rescore.py` does exactly that and this
figure imports it rather than repeating it.

⚠ WHAT A RE-SCORE CANNOT ANSWER, AND THIS BELONGS IN THE CAPTION, NOT ONLY HERE.
It answers "is the ranking robust to the scoring rule". It does NOT answer "how
would the policy have behaved under a different budget". Every policy here takes
the budget as an INPUT -- FluidServe through `--fluidserve-class-budgets`, the
other three through `slo.<class>.tbt_ms` -- so the admission decisions, and
therefore the rejections, are frozen at the value each run was deployed with.
Each arm's offered attainment is bounded above by `100 - rejection - errors`,
which is why `offered_ceiling_pct` is a column of the CSV and why several curves
flatten well below 100: they are at that ceiling, not at a scoring limit.
k = 1.0 is the deployed budget and the only multiplier at which the policies
actually ran; it is drawn as a vertical rule.

WHICH BUDGETS THE MULTIPLIER MOVES. The per-token budgets only -- chat 50,
deep research 100, swe 75 ms per token. The first-token budgets (5 / 10 / 7 s)
are held fixed, because a first-token budget is a property of the interaction
rather than of the token rate, and because the companion crossover figure is a
statement about the per-token ceiling alone.

⚠ THIS DIFFERS FROM THE TABLE IN EXP-117 SECTION 8.2, which scaled the
first-token budgets by k as well. The two agree to the last decimal at k = 1.0
and diverge away from it: +3.3 / -2.8 points over k <= 1.5, and up to +16.6
points at k = 3 (Llumnix SLO on the 70B fleet, 43.5 -> 60.2), because loosening
the first-token budget rescues requests whose per-token time was never the
problem. `--scale-ttft` reproduces that table into
`slo_scale_allbudgets.pdf` and `.csv`; the README records the comparison.

THE AGENT CLASS. swe is scored in per-token form (`FS_SWE_TBT_MS=75`,
`FS_SWE_TTFT_S=7`). Scored end to end at 30 s it has no per-token term for the
multiplier to act on and its column could not sit beside the other two.

THE ARM THAT IS NOT DRAWN. The vLLM router refuses nothing and backlogs: 52-59%
of its arrivals are still in flight when the hour ends, and a request in flight
at the end leaves BOTH denominators because its outcome is unknown. Its curve
would therefore describe the 41-48% of arrivals that finished -- the fast ones --
beside curves that describe 99.4-99.8% of theirs. It is excluded for that reason
and not for where it lands; its re-scored values are in the README.

    python3 paper_figures/fig_slo_scale.py [--scale-ttft]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# Fixed before the scorer is imported: it reads them at import time.
os.environ.setdefault("FS_SWE_TBT_MS", "75")
os.environ.setdefault("FS_SWE_TTFT_S", "7")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import TEXT_W, STYLE, GRID, ARM_COLOR, save  # noqa: E402
import slo_scale_rescore as rs  # noqa: E402

# The multipliers. Chosen, not measured: every one of them is a re-score of the
# same run, so there is no reason to prefer round numbers and no cost to a
# denser grid. It spans the deployed budget by a factor of about 1.7 either way,
# which covers the ranges the SLO-scale figures in the literature use (JITServe
# 0.8-1.4, AdaGen 0.5-1.5) and then some.
KS = [0.6, 0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 2.0, 3.0]
KTICKS = [0.6, 0.8, 1.0, 1.5, 2.0, 3.0]

# Arm table. A run whose arm is not in here is refused rather than drawn, and a
# fleet's arms are named rather than globbed: `results/*exp109r1_vllmcachet75_*`
# matches five directories, of which one is a 293-minute run that never
# terminated and three are header-only remains of failed attempts.
ARMS = {
    "fsv3capgnofrct75": ("FluidServe", ARM_COLOR["fluidserve"], "-"),
    "polyservept75":    ("PolyServe", ARM_COLOR["polyserve"], "-."),
    "llmdslot75":       ("llm-d", ARM_COLOR["llmd"], (0, (6, 1.5))),
    "slot75":           ("Llumnix SLO", ARM_COLOR["slo"], "--"),
}
# Legend order, paper-wide: baselines first, ours last.
ARM_ORDER = ["PolyServe", "Llumnix SLO", "llm-d", "FluidServe"]

# One entry per fleet panel: (title, [(run directory, repeat label)]).
# Repeat labels are carried so the CSV can say how many independent runs are
# behind each cell; a cell with one run gets no band and would otherwise read as
# the most precise point on the figure.
FLEETS = [
    ("(a) 4 x Llama-3.1-70B, TP=2", [
        "results/260831_2015_exp109r1_fsv3capgnofrct75_shift",
        "results/260901_1514_exp109r2_fsv3capgnofrct75_shift",
        "results/260831_2232_exp109r1_polyservept75_shift",
        "results/260901_1742_exp109r2_polyservept75_shift",
        "results/260831_2128_exp109r1_llmdslot75_shift",
        "results/260901_1637_exp109r2_llmdslot75_shift",
        "results/260831_2346_exp109r1_slot75_shift",
        "results/260901_1856_exp109r2_slot75_shift",
    ]),
    ("(b) 4 x Qwen2.5-72B, TP=2", [
        "results/260903_0302_exp113r1_fsv3capgnofrct75_shiftq",
        "results/260903_1031_exp113r2_fsv3capgnofrct75_shiftq",
        "results/260903_0436_exp113r1_polyservept75_shiftq",
        "results/260903_1139_exp113r2_polyservept75_shiftq",
        "results/260903_0834_exp113r1_llmdslot75_shiftq",
        "results/260903_1537_exp113r2_llmdslot75_shiftq",
        "results/260903_0548_exp113r1_slot75_shiftq",
        "results/260903_1252_exp113r2_slot75_shiftq",
    ]),
    ("(c) 8 x Llama-3.1-8B, TP=1", [
        "results/260908_2055_exp114h62r1_fsv3capgnofrct75_shift62",
        "results/260909_0900_exp114mlr1_fsv3capgnofrct75_shift62",
        "results/260908_2317_exp114h62r1_polyservept75_shift62",
    ]),
]

FIG_H = 2.05


def collect(scale_ttft):
    """Re-score every run at every multiplier and aggregate over repeats."""
    if scale_ttft:
        # Reproduces the EXP-117 section 8.2 table: the multiplier moves the
        # first-token budgets too. Kept as a variant rather than the default
        # because the companion crossover figure is a statement about the
        # per-token ceiling alone, and two figures that scale different things
        # cannot be read against each other.
        rs.scaled_rules = lambda k: {
            c: {kk: v * k for kk, v in r.items()}
            for c, r in rs.BASE_RULES.items()}
    rows = []
    for title, runs in FLEETS:
        dirs = [os.path.join(ROOT, r) for r in runs]
        missing = [d for d in dirs if not os.path.isdir(d)]
        if missing:
            sys.exit("missing run directories:\n  " + "\n  ".join(missing))
        df = rs.rescore(dirs, KS)
        bad = sorted(set(df["arm"]) - set(ARMS))
        if bad:
            sys.exit(f"unregistered arm(s) {bad} in {title}: add them to ARMS "
                     f"before drawing, or the figure drops them in silence")
        df["fleet"] = title
        rows.append(df)
    return pd.concat(rows, ignore_index=True)


def aggregate(df):
    """One row per (fleet, arm, k): the line, the band, and the repeat count."""
    out = []
    for (fleet, arm, k), g in df.groupby(["fleet", "arm", "k"], sort=False):
        label, _, _ = ARMS[arm]
        out.append(dict(
            fleet=fleet, arm=label, arm_dir=arm, slo_scale_k=k,
            attainment_offered_pct=float(g["offered_pct"].mean()),
            attainment_offered_pct_min=float(g["offered_pct"].min()),
            attainment_offered_pct_max=float(g["offered_pct"].max()),
            attainment_admitted_pct=float(g["admitted_pct"].mean()),
            attainment_admitted_pct_min=float(g["admitted_pct"].min()),
            attainment_admitted_pct_max=float(g["admitted_pct"].max()),
            goodput_tok_s=float(g["goodput_tok_s"].mean()),
            goodput_tok_s_min=float(g["goodput_tok_s"].min()),
            goodput_tok_s_max=float(g["goodput_tok_s"].max()),
            rejection_pct=float(g["rejection_pct"].mean()),
            offered_ceiling_pct=float(g["offered_ceiling_pct"].mean()),
            in_flight_at_end_pct=float(g["cutoff_pct"].mean()),
            n_repeats=int(len(g)),
            n_arrivals=int(g["n_arrivals"].mean()),
            scoring_rule=g["rule"].iloc[0]))
    return pd.DataFrame(out)


def build(agg, out_pdf, scale_ttft):
    order = [t for t, _ in FLEETS]
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, len(order), figsize=(TEXT_W, FIG_H),
                               sharey=True)
        handles, labels = {}, []
        for i, fleet in enumerate(order):
            sub = agg[agg["fleet"] == fleet]
            for name in ARM_ORDER:
                s = sub[sub["arm"] == name].sort_values("slo_scale_k")
                if s.empty:
                    continue
                _, colour, ls = next(v for v in ARMS.values() if v[0] == name)
                x = s["slo_scale_k"].to_numpy()
                y = s["attainment_offered_pct"].to_numpy()
                lo = s["attainment_offered_pct_min"].to_numpy()
                hi = s["attainment_offered_pct_max"].to_numpy()
                # The band is min..max over repeats. Where a cell has one run
                # there is no band, and `n_repeats` in the CSV is the only thing
                # that says so -- hence the hatchless, faint fill, which
                # disappears rather than collapsing to a visible line.
                ax[i].fill_between(x, lo, hi, color=colour, alpha=0.20, lw=0)
                line, = ax[i].plot(x, y, color=colour, ls=ls, lw=1.0)
                handles.setdefault(name, line)
                # The arm's own ceiling, 100 - rejection - errors, as a short
                # segment at the right margin. Attainment on the offered
                # denominator cannot rise past it at ANY multiplier, because the
                # rejections are frozen at the budget the run was deployed with,
                # and without it a curve that flattens at 80 reads as a scoring
                # limit rather than as an arm sitting on its own refusals. Where
                # the segment lies on the curve the arm is at its ceiling; where
                # it lies above, the arm still has room the multiplier has not
                # bought it.
                ceil = float(s["offered_ceiling_pct"].iloc[0])
                ax[i].plot([2.30, 3.05], [ceil, ceil], color=colour,
                           ls=(0, (1, 1)), lw=0.7, zorder=4)
            # The deployed budget. Every other column on this axis is a
            # re-score; this is the only one at which the policies ran.
            ax[i].axvline(1.0, color="#333333", lw=0.7, ls=(0, (2.5, 1.5)),
                          zorder=3)
            ax[i].set_xscale("log")
            ax[i].set_xticks(KTICKS)
            ax[i].set_xticklabels([f"{v:g}" for v in KTICKS])
            ax[i].set_xticks([k for k in KS if k not in KTICKS], minor=True)
            ax[i].set_xticklabels([], minor=True)
            ax[i].set_xlim(min(KS) * 0.95, max(KS) * 1.05)
            ax[i].set_ylim(0, 105)
            ax[i].set_yticks([0, 25, 50, 75, 100])
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
            # The panel label is the second line of the x label rather than a
            # title, because `tight_layout` reserves room for an axis label and
            # knows nothing about a hand-placed artist, and the space above the
            # axes is taken by the legend.
            ax[i].set_xlabel(f"SLO scale $k$\n{fleet}", labelpad=1.5,
                             linespacing=1.6)
        ax[0].set_ylabel("SLO attainment (%),\nper request, offered")
        # Two neutral keys rather than one entry per arm per style: the vertical
        # rule and the ceiling segment mean the same thing on every curve.
        from matplotlib.lines import Line2D
        keys = [Line2D([], [], color="#333333", lw=0.7, ls=(0, (2.5, 1.5))),
                Line2D([], [], color="#777777", lw=0.7, ls=(0, (1, 1)))]
        names = [n for n in ARM_ORDER if n in handles]
        fig.legend([handles[n] for n in names] + keys,
                   names + ["deployed $k{=}1$", "$100-$rejection"],
                   loc="lower center",
                   ncol=len(names) + 2, bbox_to_anchor=(0.5, 0.895), frameon=False,
                   columnspacing=1.4, handlelength=2.0, handletextpad=0.5,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0.0, 1, 0.90), w_pad=1.2, pad=0.25)
        save(fig, out_pdf)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scale-ttft", action="store_true",
                    help="scale the first-token budgets by k as well, which "
                         "reproduces the EXP-117 section 8.2 table")
    a = ap.parse_args()
    df = collect(a.scale_ttft)
    agg = aggregate(df)
    stem = "slo_scale" + ("_allbudgets" if a.scale_ttft else "")
    build(agg, os.path.join(HERE, stem + ".pdf"), a.scale_ttft)
    cols = ["fleet", "arm", "slo_scale_k",
            "attainment_offered_pct", "attainment_offered_pct_min",
            "attainment_offered_pct_max", "attainment_admitted_pct",
            "attainment_admitted_pct_min", "attainment_admitted_pct_max",
            "goodput_tok_s", "goodput_tok_s_min", "goodput_tok_s_max",
            "rejection_pct", "offered_ceiling_pct", "in_flight_at_end_pct",
            "n_repeats", "n_arrivals", "arm_dir", "scoring_rule"]
    out_csv = os.path.join(HERE, stem + ".csv")
    agg[cols].sort_values(["fleet", "arm", "slo_scale_k"]).to_csv(
        out_csv, index=False, float_format="%.4f")
    print(f"wrote {out_csv}  ({len(agg)} rows = "
          f"{agg['fleet'].nunique()} fleets x arms x {agg['slo_scale_k'].nunique()} k)")
    # What got drawn, checked against what was intended.
    for fleet in [t for t, _ in FLEETS]:
        s = agg[agg["fleet"] == fleet]
        who = ", ".join(f"{a}(n={int(s[s.arm == a]['n_repeats'].max())})"
                        for a in ARM_ORDER if a in set(s["arm"]))
        print(f"  {fleet}: {who}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
