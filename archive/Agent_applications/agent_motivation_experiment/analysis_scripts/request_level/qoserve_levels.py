#!/usr/bin/env python3
"""Every control plane crossed with both engine schedulers, on all three metrics.

The companion figure (`qoserve_cross.py`) draws the DIFFERENCE the engine
scheduler makes and the per-engine queue that explains it. This one draws the
LEVELS, so that the difference can be read against what it would have to close,
and it draws all three metrics because they do not agree and the disagreement is
part of the argument:

  A  offered SLO attainment  -- every arriving request counts, a rejection is a
                                violation. This is the headline.
  B  admitted SLO attainment -- only requests the system accepted. The gap
                                between A and B is what rejection costs, and a
                                policy that refuses most arrivals scores well
                                here while scoring badly in A.
  C  token goodput           -- output tokens produced by requests that met
                                their rule. Counts work rather than requests, so
                                a policy that keeps long requests and refuses
                                short ones sits higher here than in A.

ARMS AND WHERE EACH COMES FROM. Every FIFO arm is paired with its QoServe arm
inside one session, which is the point of using these three experiments rather
than the older EXP-53 sweep: EXP-53's PolyServe ran with the stale tier length
table and is superseded, and its FluidServe predates several policy changes.

  PolyServe   + FIFO / QoServe   EXP-62, 2 repeats, 45 / 50 / 60 req/s
  Llumnix     + FIFO / QoServe   EXP-61, 2 repeats, 45 / 50 / 60 (one QoServe
                                 repeat at 60 excluded, see EXCLUDE below)
  Llumnix SLO + FIFO / QoServe   EXP-40, 2 repeats, 45 and 60 only
  FluidServe  + FIFO             EXP-62, 2 repeats, 45 / 50 / 60

Llumnix SLO is included even though EXP-40 is a different session and has no
50 req/s point, because it is the strongest of the existing control planes and
a comparison that showed only the weaker one would be picking its opponent. The
join is defensible for this arm specifically: Llumnix SLO's code and settings did
not change between the sessions, and when EXP-57 re-ran it at 45 and 70 req/s it
read 52.3 and 21.7 against EXP-53's 52.4 and 21.4, inside EXP-53's own repeat
spread.

NAMING. The load-balancing Llumnix policy is called "Llumnix" and the SLO-aware
variant "Llumnix SLO", per the convention recorded in CLAUDE.md.

  python3 analysis_scripts/request_level/qoserve_levels.py
"""
import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt            # noqa: E402
import numpy as np                          # noqa: E402
import pandas as pd                         # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run, attain   # noqa: E402

OUT = "results/aggregate_analysis/exp62"
RATES = [45, 50, 60]

# label -> (experiment tag, directory arm stem, colour, line style)
# Colour carries the control plane, style carries the engine scheduler, because
# colour is already spoken for and one arm must not get a style of its own.
ARMS = [
    ("FluidServe",           "exp62", "fluidservefifo",    "#1f77b4", "-"),
    ("PolyServe + FIFO",     "exp62", "polyservefifo",     "#d62728", "-"),
    ("PolyServe + QoServe",  "exp62", "polyserveqoserve",  "#d62728", "--"),
    ("Llumnix SLO + FIFO",   "exp40", "slofifo",           "#2ca02c", "-"),
    ("Llumnix SLO + QoServe", "exp40", "sloqoserve",       "#2ca02c", "--"),
    ("Llumnix + FIFO",       "exp61", "loadbalancefifo",   "#7f7f7f", "-"),
    ("Llumnix + QoServe",    "exp61", "loadbalanceqoserve", "#7f7f7f", "--"),
]
# Listed with its reason in ms_dev/notes/excluded_runs.tsv: Llumnix performs no
# admission control yet that condition reported 22.6% rejected, because the
# gateway returned 400 from minute 3 and 503 from minute 7 (implementation §63.6.1).
EXCLUDE = ["260806_0328_exp61r1_loadbalanceqoserve_m1_rpm_3600"]

METRICS = [
    ("offered", "offered SLO attainment (%)", "A. offered — a rejection is a violation"),
    ("admitted", "admitted SLO attainment (%)", "B. admitted — only what the system took"),
    ("goodput", "goodput (output tokens/s)", "C. goodput — tokens that met their rule"),
]


def collect(exp, stem):
    """rate -> {metric: [value per run]}."""
    out = {}
    for d in sorted(glob.glob(f"results/*_{exp}*_{stem}_*_rpm_*")):
        if os.path.basename(d) in EXCLUDE:
            continue
        rate = int(os.path.basename(d).split("_rpm_")[1]) // 60
        if rate not in RATES:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        served = r[~r["rejected"]]
        met = served[~served["violate_served"]]
        dur = max(r["rel"].max(), 1.0)
        tok = float(pd.to_numeric(met["output_tokens"], errors="coerce").fillna(0).sum())
        cell = out.setdefault(rate, {"offered": [], "admitted": [], "goodput": [],
                                     "reject": []})
        cell["offered"].append(attain(r, "violate_offered"))
        cell["admitted"].append(attain(served, "violate_served"))
        cell["goodput"].append(tok / dur)
        cell["reject"].append(100.0 * float(r["rejected"].mean()))
    return out


def main():
    data = {}
    for label, exp, stem, _, _ in ARMS:
        data[label] = collect(exp, stem)
        got = {r: len(v["offered"]) for r, v in sorted(data[label].items())}
        print(f"  {label:24s} runs per rate: {got}")
        for r in sorted(data[label]):
            v = data[label][r]
            print(f"      {r} req/s  offered {np.mean(v['offered']):6.2f}  "
                  f"admitted {np.mean(v['admitted']):6.2f}  "
                  f"goodput {np.mean(v['goodput']):8,.0f}  "
                  f"rejected {np.mean(v['reject']):5.1f}%")

    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.2))
        fig.subplots_adjust(wspace=0.34)
        singles = []
        for ax, (key, ylab, title) in zip(axes, METRICS):
            for label, _, _, color, ls in ARMS:
                rr = sorted(data[label])
                if not rr:
                    continue
                mu = [float(np.mean(data[label][r][key])) for r in rr]
                lo = [mu[k] - min(data[label][r][key]) for k, r in enumerate(rr)]
                hi = [max(data[label][r][key]) - mu[k] for k, r in enumerate(rr)]
                ax.errorbar(rr, mu, yerr=[lo, hi], color=color, ls=ls, marker="o",
                            ms=3, lw=1.2, capsize=2, label=label)
                for k, r in enumerate(rr):
                    if len(data[label][r][key]) == 1 and (label, r) not in singles:
                        singles.append((label, r))
            ax.set_xlabel("arrival rate (req/s)")
            ax.set_ylabel(ylab)
            ax.set_xticks(RATES)
            ax.set_title(title, loc="left")
            ax.grid(axis="y", ls=":", lw=0.5)
        axes[0].set_ylim(0, 105)
        axes[1].set_ylim(0, 105)

        h, l = axes[0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", ncol=4, frameon=False,
                   bbox_to_anchor=(0.5, -0.16))
        single_txt = ("; ".join(f"{a} at {r} req/s" for a, r in singles)
                      or "none")
        note = (
            "Colour is the control plane, line style the engine scheduler (solid FIFO, dashed QoServe). Every FIFO arm "
            "is paired with its QoServe arm inside one session: PolyServe and FluidServe from EXP-62, Llumnix from "
            "EXP-61, Llumnix SLO from EXP-40. EXP-53 is not used — its PolyServe ran with the stale tier length table "
            "and is superseded by EXP-57, and its FluidServe predates several policy changes. Llumnix SLO has no "
            "50 req/s condition and comes from an earlier session; that join is checked rather than assumed, since "
            "EXP-57 re-ran the unchanged Llumnix SLO arm and read 52.3 and 21.7 at 45 and 70 req/s against EXP-53's "
            "52.4 and 21.4. Bars are min..max over 2 repeats; points drawn without a bar have one run: "
            f"{single_txt}. FluidServe was not crossed with QoServe in these sessions — EXP-40 did so and found "
            "+0.59 and +0.68 at 45 and 60 req/s, both inside that arm's own bimodal spread.")
        fig.text(0.005, -0.27, note, fontsize=5.2, va="top", wrap=True)
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, "qoserve_levels.png")
        fig.savefig(path, bbox_inches="tight", dpi=300)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
