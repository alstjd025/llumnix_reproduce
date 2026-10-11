#!/usr/bin/env python3
"""Paper figure (evaluation): what FluidServe refused, and how it kept what it took.

  eval_admission_quality.pdf   7.0 x 2.15 in, FULL TEXT WIDTH, two panels

Drawn at the full text width rather than one column. At 3.335 in the two panels
have about 1.3 in of plotting area each, and the y-axis label of the left panel
and the title of the right one collide with their own axes -- checked by
rendering, not assumed. If the paper needs a one-column version, the two panels
have to be split into two files and redrawn at COL_W, not scaled.

**The claim.** An admission controller is only worth the name if the requests it
takes are the requests it can serve. Two panels, one for each side of the same
decision:

  (a) OF THE REQUESTS IT PLACED, the share that missed their own budget. It is
      0.0-0.1% at 10-15 req/s and 8.7-9.2% at 70 req/s, so between 91% and 100%
      of what is admitted is delivered inside its rule. Split by class, because
      the residual is not spread evenly: deep research carries almost all of it
      (22-34% above 35 req/s against chat's 1-4%).

  (b) WHEN AN INSTANCE WAS REJECTED AS A DESTINATION, which condition rejected
      it. The pace gate accounts for 71-98% at every rate, which is the measured
      form of the paper's motivation -- an instance holding several classes is
      held to the tightest per-token budget among them. Memory is 0% only while
      the fleet has slack and settles at 17-20% above 35 req/s.

**Why they belong on one figure.** Panel (b) is about refusals and panel (a) is
about admissions, and a reader who sees only one of them can conclude the
opposite of what the data supports: (a) alone rewards a policy for refusing
everything, and (b) alone says nothing about whether the refusals were right.

**Denominators, which differ between the panels and must be stated.**

  (a) placed requests. Rejections are excluded BY CONSTRUCTION -- this is not
      attainment and a policy that refuses everything scores 100% here. The
      rejection rate is printed on the panel for that reason: 0% up to 20 req/s,
      1.9% at 25, 53.7% at 70.
  (b) (request, instance) evaluation events, not requests. One arriving request
      is tested against every instance, so a request refused everywhere
      contributes four events.

**Data.** `paper_experiment/static_sweep_clean_2026-08/`, the FluidServe arm:
16 runs, eight arrival rates, two repeats each, `GOMAXPROCS=16` (EXP-82). Panel
(a) joins `metrics.csv` to the scheduler's dispatch lines through
`request_ids.jsonl`; the join succeeds for 100.0% of placed requests in all 16
runs, which is printed by the analysis script and is not always true -- one
hour-long PolyServe condition joins at 86.4%.

**CAVEATS for the caption.**

1. Panel (a)'s population is "placed", not "called feasible": the dispatch log
   does not distinguish a route from a forced placement. The scheduler's own
   decision counters put forced placements at 0.0-2.2% of decisions, which
   bounds the difference.
2. Panel (a) says nothing about the requests that were REFUSED. The nearest
   measurement of that is EXP-78's rejection-off arm, where the share of
   admitted requests kept falls from 89.8% to 56.2% at 45 req/s.
3. The residual in panel (a) has a diagnosed cause and no remedy yet: the
   feasibility test prices per-token pace and KV but not the prefill queue
   already in front of the request, so first-token deadlines break while the
   pace stays inside the gate (3.5-5.7% of placed requests above 35 req/s
   against 0.8-3.5% for pace). EXP-87 added exactly that test and the result sat
   inside the control's repeat spread.
4. Error bars are min..max over the two repeats, not an interval estimate.

Sources: analysis_scripts/request_level/eval_b2_feasible_but_missed.py
         analysis_scripts/request_level/eval_b1_binding_predicate.py
"""
import collections
import csv
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402

ROOT = os.path.abspath(os.path.join(HERE, ".."))
AGG = os.path.join(ROOT, "results", "aggregate_analysis", "paper_eval_2026-08")

CLASS_COLOR = {"chat": "#1f77b4", "deepresearch": "#ff7f0e", "swe": "#d62728"}
CLASS_LABEL = {"chat": "chat", "deepresearch": "deep research", "swe": "agent"}
REASON = [("gate", "pace gate", "#1f77b4"),
          ("memory", "KV memory", "#d62728"),
          ("incumbents", "harm to residents", "#ff7f0e"),
          ("unpredictable", "unpredictable", "#999999")]

# Printed on panel (a) so the denominator cannot be read as attainment. From the
# pinned set's own table.csv, two-repeat means.
REJECTED = {10: 0.0, 15: 0.0, 20: 0.0, 25: 1.9, 35: 18.2, 45: 32.6, 55: 43.7, 70: 53.7}


def load(name):
    with open(os.path.join(AGG, name)) as f:
        return list(csv.DictReader(f))


def band(rows, key):
    """rate -> (min, max, mean) over repeats for one column."""
    by = collections.defaultdict(list)
    for r in rows:
        v = r[key]
        if v == "" or v != v:
            continue
        by[float(r["req_per_s"])].append(float(v))
    return {k: (min(v), max(v), sum(v) / len(v)) for k, v in by.items() if v}


def main():
    b2 = load("b2_feasible_but_missed.csv")
    b1 = load("b1_binding_predicate.csv")
    rates = sorted({float(r["req_per_s"]) for r in b2})
    x = np.arange(len(rates))

    plt.rcParams.update(ps.STYLE)
    fig, (ax, bx) = plt.subplots(1, 2, figsize=(ps.TEXT_W, 2.15))

    # (a) of placed requests, share that missed -- total plus the three classes.
    # The rejection rate rides on the right axis in grey: the left axis excludes
    # rejections by construction, so without it the panel rewards refusing.
    tot = band(b2, "missed_pct")
    ax.plot(x, [tot[r][2] for r in rates], color="black", lw=1.4, marker="s",
            ms=3.2, zorder=6, label="all placed")
    ax.fill_between(x, [tot[r][0] for r in rates], [tot[r][1] for r in rates],
                    color="black", alpha=0.18, lw=0, zorder=5)
    for c in ("chat", "deepresearch", "swe"):
        b = band(b2, "miss_" + c)
        ax.plot(x, [b[r][2] for r in rates], color=CLASS_COLOR[c], lw=1.0,
                ls="--", marker="o", ms=2.6, label=CLASS_LABEL[c], zorder=4)
        ax.fill_between(x, [b[r][0] for r in rates], [b[r][1] for r in rates],
                        color=CLASS_COLOR[c], alpha=0.15, lw=0, zorder=3)
    ax.set_ylabel("missed, of placed (%)")
    ax.set_ylim(0, 38)
    ax.set_xticks(x)
    ax.set_xticklabels(["%g" % r for r in rates])
    ax.set_xlabel("offered load (req/s)")
    ax.grid(axis="y", **ps.GRID)

    rx = ax.twinx()
    rx.plot(x, [REJECTED[r] for r in rates], color="#909090", lw=1.0, ls=":",
            marker="v", ms=2.6, zorder=2, label="rejected")
    rx.set_ylim(0, 100)
    rx.set_ylabel("rejected (%)", color="#606060")
    rx.tick_params(axis="y", colors="#606060")
    rx.spines["right"].set_color("#909090")

    h1, l1 = ax.get_legend_handles_labels()
    h2, l2 = rx.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", ncol=2, handlelength=1.4,
              columnspacing=0.7, borderpad=0.25, labelspacing=0.25,
              handletextpad=0.5)
    ax.set_title("(a) of the requests it placed, how many missed", pad=3)

    # (b) which condition refused an instance -- stacked, shares sum to 100
    means = {k: band(b1, "r_" + k) for k, _, _ in REASON}
    bottom = np.zeros(len(rates))
    for key, lab, col in REASON:
        v = np.array([means[key][r][2] if r in means[key] else 0.0 for r in rates])
        bx.bar(x, v, bottom=bottom, width=0.68, color=col, label=lab,
               edgecolor="white", linewidth=0.4)
        bottom += v
    bx.set_ylim(0, 118)
    bx.set_yticks([0, 20, 40, 60, 80, 100])
    bx.set_ylabel("refusal events (%)")
    bx.set_xticks(x)
    bx.set_xticklabels(["%g" % r for r in rates])
    bx.set_xlabel("offered load (req/s)")
    bx.legend(loc="upper center", ncol=4, handlelength=1.0, columnspacing=0.8,
              borderpad=0.2, labelspacing=0.2, handletextpad=0.4, fontsize=7)
    bx.set_title("(b) why an instance was refused as a destination", pad=3)

    fig.tight_layout(rect=(0, 0, 1, 1), w_pad=1.6)
    ps.save(fig, os.path.join(HERE, "eval_admission_quality.pdf"))

    print("\npanel (a) of placed requests, % missed  (min..max over 2 repeats)")
    for r in rates:
        print("  %5g req/s  all %4.1f-%4.1f   chat %4.1f   dr %5.1f   swe %5.1f"
              "   [rejected %4.1f]"
              % (r, tot[r][0], tot[r][1],
                 band(b2, "miss_chat")[r][2], band(b2, "miss_deepresearch")[r][2],
                 band(b2, "miss_swe")[r][2], REJECTED[r]))
    print("\npanel (b) refusal-event shares (%, mean of 2 repeats)")
    for r in rates:
        print("  %5g req/s  " % r + "  ".join(
            "%s %5.1f" % (k, means[k][r][2] if r in means[k] else 0.0)
            for k, _, _ in REASON))


if __name__ == "__main__":
    main()
