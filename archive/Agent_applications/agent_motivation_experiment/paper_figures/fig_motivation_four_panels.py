#!/usr/bin/env python3
"""Paper figure: four control planes, comparable tokens produced, 8.9x apart in useful ones.

  motivation_four_panels.pdf   7.0 x 1.62 in, `figure*`, width=\\textwidth

This is the post-2026-08-08 replacement for `motivation_throughput_vs_goodput.pdf`.
That figure is three policies on the workload whose load generator sent every
prompt exactly twelve times, inflating the engines' prefix hit rate from 28.9%
to 83-86%. Nothing from it may be drawn beside anything here.

  (a) output tokens per second the engines produced
  (b) how much of that belonged to a request that finished inside its own rule
  (c) the same question counted in REQUESTS, on the offered denominator

(a) AND (b) SHARE A Y AXIS ON PURPOSE. The claim is that the first spans much
less than the second, and rescaling either would hide it. At 45 req/s the four
control planes make the engines produce 6,847 to 13,077 output tokens per second
-- a 1.9x spread -- and deliver 1,325 to 11,836 of them inside their own latency
rule, an 8.9x spread.

WHAT (a) DOES NOT SAY, and the earlier figure got wrong for three policies: that
throughput is flat regardless of policy. It is not. PolyServe climbs from 4,610
at 10 req/s to 10,628 at 35 and then declines to 8,443 at 70; Llumnix SLO peaks
at 11,098 at 25 req/s; llm-d peaks at 8,985 at 25 and falls to 6,847 at 45,
because it is by then rejecting 71.5% of arrivals; only FluidServe is monotone,
reaching 13,952 at 70. The honest reading of (a) is that the engines keep
producing tokens at every arrival rate under every policy, not that they produce
the same number.

(c) IS NOT A RESTATEMENT OF (b). (b) weights each request by the tokens it
produced, so a policy that keeps long requests and refuses short ones scores well
on it; (c) counts each request once and counts a rejection as a violation.

FluidServe IS PRESENT HERE, unlike in the older motivation figure. This one is
not a motivation-only figure: the 8.9x spread in (b) is the spread across the
four, and the paper's own system is one of the four. If a motivation-section
figure is wanted, pass `--no-ours` and it drops that arm; the spread among the
remaining three at 45 req/s is 4.8x, which is still the point.

DATA. Post-fix workload, static sweep, eight rates. FluidServe and llm-d have two
repeats at 35-70 req/s and one at 10-25 (EXP-68/69/70); PolyServe and Llumnix SLO
have one repeat everywhere (EXP-72). The shaded band is min..max where there are
two and absent where there is one, so a cell with no band is the LESS precise
one, not the more precise one -- the caption has to say so.

Goodput here is output tokens of rule-meeting requests divided by the run's whole
span, 0 to the last arrival. `exp23_rate_sweep.py` divides by first-arrival to
last instead, which reads about 1.65% higher on this trace.

    python3 paper_figures/fig_motivation_four_panels.py [--no-ours]
"""
import argparse
import glob
import os
import re
import sys

import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import TEXT_W, STYLE, GRID, kfmt, save  # noqa: E402
from exp22_fluidserve import load_run, attain  # noqa: E402

# One entry per arm, and the globs are written out one arm at a time rather than
# as a single `results/*exp7*` pattern. An arm that is re-measured in a later
# session leaves its old directories on disk, and a pattern wide enough to catch
# the new ones catches the old ones too and averages them in silence.
ARMS = [
    ("fspfx", "FluidServe", "#1f77b4", [
        "results/*exp68s*_fspfx_m1_rpm_*", "results/*exp68r*_fspfx_m1_rpm_*",
        "results/*exp69*_fspfx_m1_rpm_*", "results/*exp70*_fspfx_m1_rpm_*"]),
    ("llmdslo", "llm-d", "#8c564b", [
        "results/*exp68s*_llmdslo_m1f_rpm_*", "results/*exp68r*_llmdslo_m1f_rpm_*",
        "results/*exp70*_llmdslo_m1f_rpm_*"]),
    ("slo", "Llumnix SLO", "#2ca02c", ["results/*exp72r1_slo_m1f_rpm_*"]),
    ("polyserve", "PolyServe", "#d62728", ["results/*exp72r1_polyserve_m1_rpm_*"]),
]

FIG_H = 1.62
TITLES = [r"$\mathbf{(a)\ Throughput}$",
          r"$\mathbf{(b)\ Goodput\ tokens}$",
          r"$\mathbf{(c)\ Request\ SLO\ attainment}$"]
MATH_SERIF = {"mathtext.fontset": "dejavuserif"}
# Ticks at the measured rates. Labelling all eight collides at this width, so
# four are labelled and the rest get an unlabelled minor tick -- and the four are
# measured rates, never round numbers that were not run.
RATES = [10, 15, 20, 25, 35, 45, 55, 70]
LABELLED = [10, 25, 45, 70]


def collect(arm, globs):
    rows = []
    seen = set()
    for g in globs:
        for d in sorted(glob.glob(g)):
            if d in seen:
                continue
            seen.add(d)
            m = re.search(r"_rpm_(\d+)$", d)
            if not m:
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            rej = (r["rejected"] if "rejected" in r
                   else pd.Series(False, index=r.index))
            served = r[~rej]
            dur = r["rel"].max()
            met = served[~served["violate_served"]]
            rows.append(dict(
                arm=arm, rate=int(m.group(1)) / 60.0,
                thru=served["output_tokens"].sum() / dur,
                good=met["output_tokens"].sum() / dur,
                off=attain(r, "violate_offered"),
                rej=100.0 * rej.mean()))
    return pd.DataFrame(rows)


def main(drop_ours):
    os.chdir(ROOT)
    arms = [a for a in ARMS if not (drop_ours and a[0] == "fspfx")]
    df = pd.concat([collect(k, gs) for k, _, _, gs in arms], ignore_index=True)
    if df.empty:
        print("no runs matched", file=sys.stderr)
        return 1
    g = df.groupby(["arm", "rate"]).agg(
        thru=("thru", "mean"), thruLo=("thru", "min"), thruHi=("thru", "max"),
        good=("good", "mean"), goodLo=("good", "min"), goodHi=("good", "max"),
        off=("off", "mean"), offLo=("off", "min"), offHi=("off", "max"),
        n=("off", "size")).reset_index()
    print(f"{len(df)} conditions, arms={[a[0] for a in arms]}, "
          f"repeats per cell: {sorted(set(g.n))}")

    with plt.rc_context({**STYLE, **MATH_SERIF}):
        fig, ax = plt.subplots(1, 3, figsize=(TEXT_W, FIG_H))
        handles, labels = [], []
        for key, lab, col, _ in arms:
            s = g[g.arm == key].sort_values("rate")
            mk = dict(marker="o", ms=2.8, mec="white", mew=0.4)
            h, = ax[0].plot(s.rate, s.thru, color=col, lw=1.2, **mk)
            ax[0].fill_between(s.rate, s.thruLo, s.thruHi, color=col,
                               alpha=0.18, lw=0)
            ax[1].plot(s.rate, s.good, color=col, lw=1.2, **mk)
            ax[1].fill_between(s.rate, s.goodLo, s.goodHi, color=col,
                               alpha=0.18, lw=0)
            ax[2].plot(s.rate, s.off, color=col, lw=1.2, **mk)
            ax[2].fill_between(s.rate, s.offLo, s.offHi, color=col,
                               alpha=0.18, lw=0)
            handles.append(h)
            labels.append(lab)

        ax[1].sharey(ax[0])
        for i in (0, 1, 2):
            ax[i].set_xlabel(f"Offered rate (req/s)\n{TITLES[i]}",
                             labelpad=1.5, linespacing=1.6)
            ax[i].set_xlim(8, 72)
            ax[i].set_xticks(LABELLED)
            ax[i].set_xticks([r for r in RATES if r not in LABELLED],
                             minor=True)
            ax[i].grid(axis="y", **GRID)
        ax[0].set_ylabel("Output tokens/s")
        ax[0].yaxis.set_major_formatter(kfmt())
        ax[1].set_ylabel("Goodput tokens/s")
        ax[2].set_ylabel("Attainment (%)")
        ax[2].set_ylim(0, 105)

        fig.legend(handles, labels, loc="upper center",
                   bbox_to_anchor=(0.5, 1.02), ncol=len(arms),
                   handlelength=1.3, columnspacing=1.0)
        fig.tight_layout(rect=(0, 0, 1, 0.88))
    name = "motivation_four_panels" + ("_noours" if drop_ours else "")
    save(fig, os.path.join(HERE, name + ".pdf"))
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ours", action="store_true",
                    help="drop the FluidServe arm, for a motivation-only figure")
    sys.exit(main(ap.parse_args().no_ours))
