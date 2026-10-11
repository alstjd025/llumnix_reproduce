#!/usr/bin/env python3
"""Paper figure: what happened to every arrival, across the rate sweep.

  outcome_split_sweep_t75.pdf   7.0 x 1.90 in, `figure*`, width=\\textwidth
  outcome_split_sweep_t75.csv   the values drawn, same basename

One panel per control plane, one stacked bar per arrival rate. Each bar is 100%
of the requests that ARRIVED at that rate, divided into the four things that can
happen to one:

  met                 accepted and finished inside its own latency rule.
                      This segment IS offered attainment, so the figure
                      reconciles with every attainment number drawn from the
                      same scoring.
  admitted, missed    accepted, finished, and outside its rule. The fleet spent
                      engine time and delivered a violation. This is the segment
                      the figure is about, and the only saturated colour.
  unfinished          still in flight when the load window closed. Its outcome
                      is unknown, so `attain()` drops it from BOTH denominators
                      everywhere else in the paper.
  rejected            refused by admission control before any engine time.

⚠ UNFINISHED IS A BAND HERE AND IT IS NOT ONE IN `three_way_split.pdf`. That
figure states three shares over "arrivals whose outcome is known" and carries
the dropped share in its caption. On a rate sweep that choice stops working: the
share still in flight at the end is 0.7 to 2.4% for the four control planes that
reject or hold, and 19.7% on average -- 61.6% at the worst rate -- for the vLLM
router, which refuses nothing and builds a queue, so the requests left
unfinished are precisely the slow ones. Dropping them would rescale that arm's
bar by a factor of two and flatter it exactly where it is worst. Every bar here
therefore sums to 100% of arrivals and nothing is removed.

CLIENT ERRORS ARE FOLDED INTO `missed`. A request that errored without being
rejected is a violation that consumed engine time, which is what that segment
means. The share is at most 0.01% in every run drawn here, so it is folded
rather than given a band nobody could see; the number is in the CSV's
`errored_pct` column.

SCORING. The per-token half of each class rule is the token-level cumulative
deadline with a 90% tolerance, the same rule as
`motivation_throughput_vs_goodput_4panel_t75.pdf`: writing a_i for the arrival of
the i-th output token from submission, T for the class time-to-first-token
budget and P for its per-token budget, a request passes when a_1 <= T and at
least 90% of its tokens satisfy a_i <= T + (i-1) * P. Class budgets are chat
(5 s, 50 ms), deepresearch (10 s, 100 ms), swe (7 s, 75 ms). NOTHING HERE MAY
SIT BESIDE A NUMBER SCORED AGAINST THE 30 s END-TO-END RULE FOR swe.

DATA. EXP-108 (2026-08-31), four arms, eight arrival rates, two repeats, plus the
vLLM router from EXP-77 (2026-08-10), which is the one arm that reads no latency
promise and can therefore be scored under one it did not run with. Bars are the
mean over repeats; the per-repeat spread is in the CSV. The vLLM router's cell at
70 req/s has one repeat.

    python3 paper_figures/fig_outcome_split_sweep.py [--rule q90]
"""
import argparse
import os
import re
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

from paper_style import TEXT_W, STYLE, GRID, save  # noqa: E402

# The rule is fixed and owned by deadline_ladder_attainment.py; this script
# reads its PER-REQUEST verdicts and only counts them. The run set is the
# paper's, which differs from EXP-108's in one cell -- see
# build_paper_ladder_table.py.
LADDER = os.path.join(ROOT, "results", "aggregate_analysis", "ladder95")
VERDICTS = os.path.join(LADDER, "verdicts")
SOURCES = [os.path.join(LADDER, "exp108_paper_ladder95.csv"),
           os.path.join(LADDER, "vllm77_ladder95.csv")]

ARMS = [("vllmcache", "vLLM-router"), ("polyservept75", "PolyServe"),
        ("slot75", "Llumnix SLO"), ("llmdslot75", "llm-d"),
        ("fsv3capgnofrct75", "FluidServe")]
# Green for what worked, amber for work the fleet did and threw away, grey for
# work it declined to do, and hatched white for the requests that have no
# outcome at all -- the hatch says "not a result" rather than naming a fifth
# result.
def desat(hex_colour, f):
    """The same hue at lower saturation: blend the colour with its own grey.

    Blending toward WHITE would lighten it as well, and four lightened bands
    stacked in one bar stop separating from the white hatch band between them.
    """
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    grey = 0.299 * r + 0.587 * g + 0.114 * b
    m = [c * f + grey * (1 - f) for c in (r, g, b)]
    return "#%02x%02x%02x" % tuple(int(round(255 * c)) for c in m)


# The project's own colours at 55% saturation, the same four as
# `pace_and_outcome_35.pdf`, which draws the same four bands: one colour means
# one outcome across the paper. Only the saturation moves, because a filled
# band at full chroma beside three others is louder than a line of that colour.
SEG = [("met_pct", "Met its rule", desat("#2ca02c", 0.55), None),
       ("missed_pct", "Admitted, missed", desat("#ff7f0e", 0.55), None),
       ("unfinished_pct", "Unfinished at window close", "#ffffff", "////"),
       ("rejected_pct", "Rejected", "#c7c7c7", None)]
RATES = [10, 15, 20, 25, 35, 45, 55, 70]
FIG_H = 1.90
RULE = "ladder95"


def collect(rule=RULE):
    """One row per run: the four shares of ALL arrivals, from the per-request
    verdicts the ladder scorer wrote.

    met       accepted, finished, and at least 95% of its tokens on time
    missed    accepted, finished, and not
    unfinished  still in flight when the load window closed
    rejected  refused by admission control

    The four are disjoint by construction here, and the check that they sum to
    100 is kept because it catches a verdict file that does not match its run."""
    runs = pd.concat([pd.read_csv(p) for p in SOURCES], ignore_index=True)
    rows = []
    for _, r in runs.iterrows():
        vpath = os.path.join(VERDICTS, r["run"] + ".csv")
        if not os.path.exists(vpath):
            print(f"!! no verdict file for {r['run']}; run "
                  f"deadline_ladder_attainment.py --dump-verdicts", file=sys.stderr)
            continue
        v = pd.read_csv(vpath)
        n = len(v)
        rej = int(v["rejected"].sum())
        cut = int(v["cutoff"].sum())
        err = int((v["errored"] & ~v["rejected"]).sum())
        met = int((v["ladder_ok"] & ~v["rejected"] & ~v["errored"]
                   & ~v["cutoff"]).sum())
        missed = n - met - rej - cut          # client errors fold in here
        m = re.search(r"_rpm_(\d+)", r["run"])
        if m is None:
            print(f"!! no _rpm_ in {r['run']}; dropped", file=sys.stderr)
            continue
        rows.append(dict(run=r["run"], arm=r["arm"],
                         req_per_s=int(m.group(1)) / 60.0,
                         n_arrivals=n, met_pct=100 * met / n,
                         missed_pct=100 * missed / n,
                         unfinished_pct=100 * cut / n,
                         rejected_pct=100 * rej / n,
                         errored_pct=100 * err / n, rule=rule))
    d = pd.DataFrame(rows)
    total = d[[s[0] for s in SEG]].sum(axis=1)
    bad = d[(total - 100).abs() > 1e-6]
    if len(bad):
        sys.exit(f"the four shares do not sum to 100 for {len(bad)} runs")
    if (d["missed_pct"] < -1e-9).any():
        sys.exit("negative admitted-but-missed share")
    return d


def build(d, out_pdf, rule):
    agg = d.groupby(["arm", "req_per_s"])[[s[0] for s in SEG]].mean()
    counts = d.groupby(["arm", "req_per_s"]).size()
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, len(ARMS), figsize=(TEXT_W, FIG_H),
                               sharey=True)
        handles = None
        for i, (key, label) in enumerate(ARMS):
            x = [r for r in RATES if (key, float(r)) in agg.index]
            bottom = np.zeros(len(x))
            hs = []
            for col, name, colour, hatch in SEG:
                v = np.array([agg.loc[(key, float(r)), col] for r in x])
                h = ax[i].bar(range(len(x)), v, bottom=bottom, width=0.78,
                              color=colour, edgecolor="#555555", linewidth=0.35,
                              hatch=hatch)
                bottom += v
                hs.append(h)
            handles = hs
            ax[i].set_xticks(range(len(x)))
            ax[i].set_xticklabels([str(r) for r in x], fontsize=5.8)
            ax[i].set_xlim(-0.62, len(x) - 0.38)
            ax[i].set_ylim(0, 100)
            ax[i].set_yticks([0, 25, 50, 75, 100])
            ax[i].grid(axis="y", **GRID)
            ax[i].set_axisbelow(True)
            ax[i].set_xlabel(f"({chr(97 + i)}) {label}", labelpad=1.5)
        ax[0].set_ylabel("Share of arrivals (%)")
        # One x-axis name for five panels: repeating it five times at this width
        # costs more space than the panels have, and the quantity is the same in
        # all of them.
        fig.supxlabel("Offered rate (req/s)", y=0.012, fontsize=8)
        fig.legend([h[0] for h in handles], [s[1] for s in SEG],
                   loc="lower center", ncol=len(SEG),
                   bbox_to_anchor=(0.5, 0.885), frameon=False,
                   columnspacing=1.1, handlelength=1.3, handletextpad=0.4)
        fig.tight_layout(rect=(0, 0.055, 1, 0.888), w_pad=1.4, pad=0.25)
        save(fig, out_pdf)


def write_csv(d, out_csv):
    """Exactly what the bars are: the mean that is drawn, the per-repeat min and
    max that are not, and the repeat count behind each cell."""
    cols = [s[0] for s in SEG]
    g = d.groupby(["arm", "req_per_s"])
    out = g[cols].mean()
    for c in cols:
        out[f"{c}_min"] = g[c].min()
        out[f"{c}_max"] = g[c].max()
    out["errored_pct"] = g["errored_pct"].mean()
    out["n_arrivals"] = g["n_arrivals"].mean().round().astype(int)
    out["n_repeats"] = g.size()
    out["rule"] = d["rule"].iloc[0]
    name = dict(ARMS)
    out = out.reset_index()
    out["arm"] = out["arm"].map(name)
    order = {n: i for i, (_, n) in enumerate(ARMS)}
    out = out.sort_values(["arm", "req_per_s"],
                          key=lambda s: s.map(order) if s.name == "arm" else s)
    out.to_csv(out_csv, index=False, float_format="%.4f")
    print(f"wrote {out_csv}  ({len(out)} rows = {out['arm'].nunique()} arms x "
          f"{out['req_per_s'].nunique()} rates)")


def main():
    ap = argparse.ArgumentParser()
    args = ap.parse_args()
    d = collect()
    base = os.path.join(HERE, "outcome_split_sweep_t75")
    build(d, base + ".pdf", RULE)
    write_csv(d, base + ".csv")

    name = dict(ARMS)
    d["label"] = d["arm"].map(name)
    print()
    print(d.groupby(["label", "req_per_s"])[[s[0] for s in SEG]]
          .mean().round(1).to_string())


if __name__ == "__main__":
    main()
