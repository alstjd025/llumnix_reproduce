#!/usr/bin/env python3
"""Paper figure (motivation): how many different paces the fleet can offer.

  eval_pace_diversity.pdf   3.335 x 1.95 in, one column

**The claim.** An instance cannot be held to a pace looser than the tightest
per-token budget among the requests already on it, so the set of paces the fleet
can offer is decided by where the classes ended up -- not by how loaded the
instances are. Measuring that set directly, from the allowance the scheduler
publishes per instance about once a second:

  10-20 req/s   two and a half to three distinct paces on four engines. The
                fleet can accept a slow-paced request somewhere and a
                tight-paced one somewhere else.
  25 req/s      it collapses. 72-85% of scrapes have EVERY instance on the same
                pace, and 86% of instance-samples are at chat's 50 ms. This is
                the arrival rate at which this policy's rejection rate first
                leaves zero.
  35-70 req/s   it settles at two: about three quarters of instance-samples at
                50 ms and about a fifth at 100 ms, which on four engines is one
                engine carrying deep research alone.

**Why the last row matters more than it looks.** That one engine is where every
deep-research request has to go, because the other three are held to chat's
50 ms. It is also where 22-34% of placed deep-research requests miss their
first-token budget: the prefill queue in front of them reaches 76,813-87,845
tokens while the per-token pace stays inside the gate, so the feasibility test --
which prices pace and KV and not that queue -- keeps routing to it.

**What a load-based router would do instead.** The companion statistic in the
same script: at 10-20 req/s the instance with the smallest decode batch is NOT
the instance with the loosest pace 85-97% of the time, so load and admissible
pace are different orderings. Above 35 req/s they agree 80-95% of the time --
because there is nothing left to disagree about once three engines are on the
same value.

**Data.** `paper_experiment/static_sweep_clean_2026-08/`, the FluidServe arm:
16 runs, eight rates, two repeats, `GOMAXPROCS=16`. Every scrape that published
an allowance for at least two instances; 485-552 scrapes per run.

**CAVEATS for the caption.**

1. This is FluidServe's own view of the fleet. The allowance series exists only
   in this policy, so the figure describes the state our policy runs in and is
   not a comparison between policies.
2. `-1` is published when an instance holds no requests. It is drawn as "idle"
   and excluded from the count of distinct paces rather than counted as a fourth
   one.
3. The bars are shares of instance-samples, so a bar is a time-average over the
   run and four engines, not a snapshot.
4. Two repeats; the whisker on the line is min..max.

Source: analysis_scripts/request_level/eval_load_is_not_capacity.py
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

AGG = os.path.abspath(os.path.join(
    HERE, "..", "results", "aggregate_analysis", "paper_eval_2026-08"))

BANDS = [("share_p50", "chat, 50 ms", "#1f77b4"),
         ("share_p62", "agent, 62.5 ms", "#d62728"),
         ("share_p100", "deep research, 100 ms", "#ff7f0e"),
         ("share_pidle", "no residents", "#cccccc")]


def main():
    rows = list(csv.DictReader(open(os.path.join(AGG, "load_is_not_capacity.csv"))))
    by = collections.defaultdict(list)
    for r in rows:
        by[float(r["req_per_s"])].append(r)
    rates = sorted(by)
    x = np.arange(len(rates))

    def mean(rate, key):
        return float(np.mean([float(r[key]) for r in by[rate]]))

    plt.rcParams.update(ps.STYLE)
    fig, ax = plt.subplots(figsize=(ps.COL_W, 1.95))
    bottom = np.zeros(len(rates))
    for key, label, colour in BANDS:
        v = np.array([mean(r, key) for r in rates])
        ax.bar(x, v, bottom=bottom, width=0.68, color=colour, label=label,
               edgecolor="white", linewidth=0.4)
        bottom += v
    ax.set_ylim(0, 128)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_ylabel("instance-samples (%)")
    ax.set_xticks(x)
    ax.set_xticklabels(["%g" % r for r in rates])
    ax.set_xlabel("offered load (req/s)")
    ax.legend(loc="upper center", ncol=2, handlelength=1.0, columnspacing=0.7,
              borderpad=0.2, labelspacing=0.2, handletextpad=0.4, fontsize=6.5)

    dx = ax.twinx()
    m = [mean(r, "mean_distinct_paces") for r in rates]
    lo = [m[j] - min(float(q["mean_distinct_paces"]) for q in by[r])
          for j, r in enumerate(rates)]
    hi = [max(float(q["mean_distinct_paces"]) for q in by[r]) - m[j]
          for j, r in enumerate(rates)]
    dx.errorbar(x, m, yerr=[lo, hi], color="black", lw=1.3, marker="s", ms=3.2,
                capsize=1.6, elinewidth=0.6, zorder=6, label="distinct paces")
    dx.set_ylim(0, 4.3)
    dx.set_yticks([0, 1, 2, 3, 4])
    dx.set_ylabel("distinct paces on the fleet")
    # No second legend: the right axis label already names the line, and at this
    # width every corner of the plot is occupied by a bar or by the line itself.

    fig.tight_layout(rect=(0, 0, 1, 1))
    ps.save(fig, os.path.join(HERE, "eval_pace_diversity.pdf"))

    print("req/s".rjust(6) + "distinct".rjust(11)
          + "".join(l.rjust(24) for _, l, _ in BANDS))
    for r in rates:
        print(("%g" % r).rjust(6) + ("%.2f" % mean(r, "mean_distinct_paces")).rjust(11)
              + "".join(("%.1f%%" % mean(r, k)).rjust(24) for k, _, _ in BANDS))


if __name__ == "__main__":
    main()
