#!/usr/bin/env python3
"""Paper figure: a deadline-aware ENGINE scheduler under the same router.

  qoserve_engine.pdf       3.335 x 1.75 in, one column, width=\\columnwidth
  qoserve_engine_wide.pdf  7.0 x 1.62 in, `figure*`, width=\\textwidth

Two panels against offered rate, two arms that differ ONLY in the engine:

  (a) Throughput      every output token the engines emitted, divided by the
                      condition's length, whether or not the request it
                      belonged to met its rule or even finished
  (b) SLO attainment  every arrival is in the denominator and BOTH a rejection
                      and an unfinished request count as violations

WHAT THE FIGURE CLAIMS, and it is a narrow claim: the engine scheduler moves a
great deal of throughput and moves the capacity NOT AT ALL. Past the knee it
multiplies useful work by 3 to 5 -- token goodput 1,436 to 5,424 at 35 req/s --
and the rate at which the system stops keeping its rule moves by 0.1 req/s.
FluidServe's is 28.0 on the same grid and the same workload. Reordering work
inside an instance is worth a lot and is not what sets capacity.

⚠ THE CAPACITY NUMBER HERE IS 21.2 -> 21.1 AND THE ONE IN THE INTRO FIGURE IS
21.4 -> 21.3. They are the same construction on different denominators:
`fig_intro_capacity.py` interpolates 90% of the OFFERED attainment, which drops
requests still running at the end, and this figure interpolates 90% of the
all-arrivals attainment, which counts them as violations. Both move by 0.1
req/s. Quote whichever pair the surrounding text is using and say which; do not
mix 21.4 with the curves drawn here.

WHY THE ATTAINMENT DENOMINATOR IS ALL ARRIVALS, and it is the decision that
makes this figure honest. Neither arm rejects anything (0.0% both, at every
rate), so offered and admitted attainment are identical here and the only
question is what to do with requests still running when the condition ended.
Dropping them -- which `attain()` does by default, because in general their
outcome is unknown -- would delete exactly the requests that were slowest, and
the two arms leave very different numbers of them behind:

  unfinished at 35 / 45 / 55 / 70 req/s
    FIFO       21.7 / 39.3 / 51.1 / 62.6 %
    +QoServe    8.6 / 14.1 / 19.5 / 32.7 %

At 70 req/s the FIFO arm would be scored on the 37% of its arrivals that
finished. So an unfinished request is counted as a violation here, which is
the metric EXP-81 registered before the run. ⚠ THE CAPTION MUST SAY THIS: on
the completed-only denominator the gap would be much smaller and would be an
artifact of which requests survived to be measured.

WHY THE THROUGHPUT PANEL CANNOT BE READ ALONE, and this figure is the sharpest
case of it in the paper. Throughput is maximised by generating tokens nobody
can use, and past the knee the treatment arm produces FAR MORE tokens and
almost none of them count:

  req/s   throughput FIFO / +QoServe   goodput FIFO / +QoServe   useful share
     25        12,682 /       12,608       8,419 /       8,013    66% /  64%
     35        14,374 /       16,379       1,436 /       5,424    10% /  33%
     45        14,299 /       19,253         766 /       4,110     5% /  21%
     55        14,105 /       21,513         674 /       2,301     5% /  11%
     70        13,404 /       22,107         597 /         681     4% /   3%

At 70 req/s +QoServe emits 65% more tokens than FIFO and 3.1% of them belong to
a request that met its rule. THE HIGHEST THROUGHPUT IN THE FIGURE IS THE WORST
POINT IN THE FIGURE. Token goodput is the quantity that closes the two panels
and it is printed by this script but NOT drawn, so the caption has to carry it
or the reader will take panel (a) at face value.

⚠ WHY THE TREATMENT'S THROUGHPUT RISES rather than staying flat: FIFO's engines
are preempting by recompute 3,422-4,029 times per condition, and a recompute
re-runs prefill, which produces no output token. Removing the preemptions (the
count is zero at every rate under QoServe) turns that engine time into emitted
tokens. The tokens are real; what they are worth is panel (b).

WHERE THE GAIN COMES FROM, and it is not what the port's name suggests. The
treatment arm's preemption count is ZERO at every rate against 3,422-4,029 for
FIFO, and the cause is unit 4 of the Niyama port, the dynamic prefill chunk
size, which stops the engine from evicting and recomputing. Relegation (unit 5)
pushes a request back once and never rejects it, so it cannot produce 18 points
on its own. The difference is the BUNDLE of units 2, 3, 4 and 5, because
`--scheduling-policy priority` changes the engine's waiting-queue structure as
well. Details and the source check are in `qoserve-niyama-fidelity.md`.

DATA. `paper_experiment/qoserve_engine_2026-08/`, 34 pinned runs: 16 treatment
and 16 control over eight rates with two repeats, plus two session checks at
35 req/s that are read for validation and NOT drawn. The control arm is the
main sweep's vLLM router arm (EXP-77), re-used rather than re-run, and it is 3
to 6 days older than the treatment; the session checks exist because of that
and both land inside the sweep's own repeat spread. Error bars are min..max
over the two repeats.

⚠ ONE CONDITION IS NOT TWO INDEPENDENT DRAWS OF THE SAME THING AT 25 req/s.
That is where the collapse begins and the control's repeat spread is 6.0 points
against the treatment's 0.3. The -2.7 point difference there is inside the
control's own spread and must not be read as a regression.

⚠ NOT THE WHOLE OF NIYAMA. Unit 6, the linear batch-time predictor, is not
ported, and `_relegate_waiting` scans only the first 32 entries of the waiting
heap where the original scans all of it; at 35 req/s the per-engine queue holds
74-218 requests, so 15-43% of it was examined. Read as "a port that scans the
first 32 does this much".

    python3 paper_figures/fig_qoserve_engine.py
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

from paper_style import COL_W, TEXT_W, STYLE, GRID, ARM_COLOR, kfmt, save  # noqa: E402
from exp22_fluidserve import load_run  # noqa: E402

DATASET = os.path.join(ROOT, "paper_experiment", "qoserve_engine_2026-08")

# (label in table.csv, label drawn, colour, marker, line style).
#
# THE CONTROL KEEPS THE PALETTE'S vLLM PURPLE because it is literally that arm:
# the same runs the main sweep draws. The treatment is drawn in neutral dark
# grey rather than a second policy colour, because every colour in
# `paper_style.ARM_COLOR` is bound to a ROUTING policy and this arm is not a
# new routing policy -- it is the same router over a different engine. ⚠ Grey
# is used for reference rules in `fig_ideal_overload.py`; there is no rule in
# this figure, but if the two ever appear on one page that would need changing.
ARMS = [
    ("vLLM router + FIFO", "vLLM (FIFO)", ARM_COLOR["vllmrouter"], "h", "--"),
    ("vLLM router + QoServe", "vLLM + QoServe", "#404040", "P", "-"),
]
PANELS = [("thru", r"Throughput", "Tokens/s"),
          ("att", r"SLO\ attainment", "Attainment (%)")]
MATH_SERIF = {"mathtext.fontset": "dejavuserif"}
XTICKS = [10, 30, 50, 70]
TARGET = 90.0


def condition(run_dir):
    """Throughput and all-arrivals attainment for one condition.

    Both are computed from the same `load_run` pass so they cannot come to
    describe different populations, which is the failure mode recorded in
    implementation.md section 32 (two layers calling different quantities by
    one name). The attainment is then cross-checked against the dataset's own
    `table.csv` in `main`.
    """
    r = load_run(run_dir)
    if r is None or r.empty:
        return None
    span = r["rel"].max() - r["rel"].min()
    tok = pd.to_numeric(r.get("output_tokens"), errors="coerce").fillna(0)
    met = (~r["violate_offered"]) & (~r["cutoff"])
    return {"thru": tok.sum() / span,
            "gp": tok[met].sum() / span,
            "att": 100.0 * met.sum() / len(r),
            "cut": 100.0 * r["cutoff"].mean(),
            "n": len(r)}


def knee(rates, vals, target=TARGET):
    """The highest rate whose attainment is still at or above the target.

    Linear interpolation between the two grid points that straddle the target,
    the same construction `fig_intro_capacity.py` uses, so the number here and
    the one in the intro figure mean the same thing. Returns NaN if the arm
    never crosses, which cannot be read as "no capacity".
    """
    rates, vals = np.asarray(rates, float), np.asarray(vals, float)
    for i in range(len(rates) - 1):
        if vals[i] >= target > vals[i + 1]:
            f = (vals[i] - target) / (vals[i] - vals[i + 1])
            return rates[i] + f * (rates[i + 1] - rates[i])
    return np.nan


def collect():
    tab = pd.read_csv(os.path.join(DATASET, "table.csv"))
    out, checked = {}, []
    for key, _, _, _, _ in ARMS:
        rows = tab[tab.arm_label == key]
        acc = {}
        for _, row in rows.iterrows():
            c = condition(os.path.join(DATASET, "data", row["run"]))
            if c is None:
                sys.exit(f"no rows for {row['run']}")
            acc.setdefault(float(row["req_per_s"]), []).append(c)
            checked.append(abs(c["att"] - float(row["all_arrivals"])))
        out[key] = acc
    # The dataset ships the scored table; recomputing it here and finding the
    # same numbers is what says this script reads the runs the same way the
    # verifier does. A silent divergence here would be invisible in the figure.
    print(f"attainment vs the pinned table.csv: max |difference| "
          f"{max(checked):.3f} points over {len(checked)} runs")
    if max(checked) > 0.05:
        sys.exit("recomputed attainment disagrees with the pinned table")
    return out


def build(data, out, width, height):
    with plt.rc_context({**STYLE, **MATH_SERIF}):
        fig, ax = plt.subplots(1, len(PANELS), figsize=(width, height))
        handles, labels = [], []

        for key, lab, col, mk, ls in ARMS:
            x = sorted(data[key])
            for i, (k, _, _) in enumerate(PANELS):
                y = np.array([np.mean([v[k] for v in data[key][r]]) for r in x])
                lo = y - np.array([min(v[k] for v in data[key][r]) for r in x])
                hi = np.array([max(v[k] for v in data[key][r]) for r in x]) - y
                h = ax[i].errorbar(x, y, yerr=[lo, hi], color=col, marker=mk,
                                   ls=ls, ms=2.8, lw=1.2, capsize=1.5,
                                   mec="white", mew=0.4)
                if i == 0:
                    handles.append(h)
                    labels.append(lab)

        for i, (k, title, ylab) in enumerate(PANELS):
            ax[i].set_xlabel(f"Offered rate (req/s)\n"
                             f"$\\mathbf{{({'ab'[i]})\\ {title}}}$",
                             labelpad=1.5, linespacing=1.6)
            ax[i].set_ylabel(ylab, labelpad=1.5)
            ax[i].set_xlim(7, 73)
            ax[i].set_xticks(XTICKS)
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
            if k == "att":
                ax[i].set_ylim(0, 105)
                ax[i].set_yticks([0, 25, 50, 75, 100])
                # The rule the capacity number is read off. Drawn because the
                # figure's claim is about where the curves cross it, and a
                # reader cannot locate 90 on a 0-105 axis by eye.
                ax[i].axhline(TARGET, color="#777777", lw=0.6, ls=(0, (3, 2)),
                              zorder=1)
            else:
                ax[i].set_ylim(0, None)
                ax[i].yaxis.set_major_formatter(kfmt())

        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 1 - 0.215 / height), frameon=False,
                   fontsize=7, columnspacing=1.2, handlelength=2.0,
                   handletextpad=0.4, borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 1 - 0.195 / height),
                         w_pad=1.2, pad=0.3)
        save(fig, out)


def main():
    data = collect()
    print(f"{'arm':16s} {'rate':>5s} {'thru':>8s} {'goodput':>8s} "
          f"{'attain':>7s} {'unfin':>6s} {'n':>2s}")
    for key, lab, _, _, _ in ARMS:
        rates = sorted(data[key])
        att = []
        for r in rates:
            v = data[key][r]
            m = {k: float(np.mean([x[k] for x in v]))
                 for k in ("thru", "gp", "att", "cut")}
            att.append(m["att"])
            print(f"{lab:16s} {r:5.0f} {m['thru']:8,.0f} {m['gp']:8,.0f} "
                  f"{m['att']:7.1f} {m['cut']:6.1f} {len(v):2d}")
        print(f"  -> {lab}: {TARGET:.0f}% is sustained up to "
              f"{knee(rates, att):.1f} req/s")

    build(data, os.path.join(HERE, "qoserve_engine.pdf"), COL_W, 1.75)
    build(data, os.path.join(HERE, "qoserve_engine_wide.pdf"), TEXT_W, 1.62)
    return 0


if __name__ == "__main__":
    sys.exit(main())
