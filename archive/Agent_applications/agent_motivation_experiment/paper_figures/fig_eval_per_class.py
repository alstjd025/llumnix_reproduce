#!/usr/bin/env python3
"""Paper figure (evaluation): the result split by class, including where we lose.

  eval_per_class.pdf   7.0 x 2.05 in, FULL TEXT WIDTH, three panels

**The claim.** The aggregate hides two opposite facts, and the paper is stronger
for showing both. Split by class, on all five control planes:

  chat (76.9% of requests)   FluidServe leads at every rate and the margin is
                             large -- this is where the aggregate advantage comes
                             from.
  deep research (15.4%)      FluidServe leads up to 25 req/s and llm-d leads
                             above 35. We lose this class under load.
  agent (7.7%)               scored on a 30 s end-to-end budget rather than a
                             per-token one, so it behaves differently from both.

**Why the deep-research panel is in the paper rather than in an appendix.** Its
cause is measured and it is the same defect three other measurements point at.
The fleet folds to two admissible paces above 35 req/s and the loose one is a
single engine (eval_pace_diversity), so every deep-research request has to go
there; the feasibility test prices per-token pace and KV but not the prefill
queue already in front of the request, so it keeps routing to it; and 22-34% of
the deep-research requests placed there miss their FIRST-TOKEN budget while their
pace stays inside the gate (eval_admission_quality). Six attempts to remove that
defect have been measured and refuted, the most recent adding exactly the missing
test to the feasibility conjunction (EXP-87).

**Scoring.** Every arrival is the denominator; a rejection and a request
unfinished when the load window closed both count as misses. Per class, so the
denominator of each panel is the arrivals of that class.

**Data.** `paper_experiment/static_sweep_clean_2026-08/`: five arms, eight rates,
two repeats, `GOMAXPROCS=16`. FluidServe and llm-d from EXP-82, the other three
from EXP-86.

**CAVEATS for the caption.**

1. The three baselines other than llm-d collapse above 25 req/s -- their token
   goodput falls with load -- so their curves in the right half of each panel
   describe a system that has stopped working, not a policy preference.
2. Llumnix SLO and llm-d run on the m1f workload config, which restates the agent
   class's 30 s end-to-end budget as the (TTFT, per-token) pair closest to it,
   because neither policy can express an end-to-end budget. Scoring is unaffected:
   the agent class is scored on 30 s end to end for every arm.
3. llm-d's repeats at 10 req/s differ by 11.5 points. EXP-89 adds a third.
4. Two repeats; bands are min..max.

Source: paper_experiment/static_sweep_clean_2026-08, scored with exp22_fluidserve.load_run.
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
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "analysis_scripts",
                                                "request_level")))
import paper_style as ps               # noqa: E402
from exp22_fluidserve import load_run  # noqa: E402

EXPDIR = os.path.abspath(os.path.join(HERE, ".."))
CLASSES = [("chat", "chat  (76.9% of requests)"),
           ("deepresearch", "deep research  (15.4%)"),
           ("swe", "agent  (7.7%)")]
ARMS = [("FluidServe", ps.ARM_COLOR["fluidserve"], "s", "-", 4),
        ("llm-d", ps.ARM_COLOR["llmd"], "D", "-", 3),
        ("vLLM router", ps.ARM_COLOR["vllmrouter"], "h", ":", 2),
        ("Llumnix SLO", ps.ARM_COLOR["slo"], "^", ":", 2),
        ("PolyServe", ps.ARM_COLOR["polyserve"], "o", ":", 2)]


def per_class(run_dir):
    r = load_run(run_dir)
    if r is None or r.empty:
        return None
    met = (~r["violate_offered"]) & (~r["cutoff"])
    out = {}
    for c, _ in CLASSES:
        sel = r["class"] == c
        n = int(sel.sum())
        out[c] = 100.0 * float((met & sel).sum()) / n if n else np.nan
    return out


def main():
    mpath = os.path.join(EXPDIR, "paper_experiment",
                         "static_sweep_clean_2026-08", "manifest.tsv")
    data = collections.defaultdict(lambda: collections.defaultdict(list))
    for row in csv.DictReader(open(mpath), delimiter="\t"):
        v = per_class(os.path.join(EXPDIR, "results", row["run"]))
        if v:
            data[row["arm_label"]][float(row["req_per_s"])].append(v)

    rates = sorted(data["FluidServe"])
    x = np.arange(len(rates))
    plt.rcParams.update(ps.STYLE)
    fig, axes = plt.subplots(1, 3, figsize=(ps.TEXT_W, 2.05), sharey=True)

    for ax, (c, title) in zip(axes, CLASSES):
        for arm, colour, marker, ls, z in ARMS:
            m = [np.mean([v[c] for v in data[arm][r]]) for r in rates]
            lo = [min(v[c] for v in data[arm][r]) for r in rates]
            hi = [max(v[c] for v in data[arm][r]) for r in rates]
            ax.plot(x, m, color=colour, marker=marker, ls=ls, ms=3.0,
                    lw=1.4 if z >= 3 else 0.9, label=arm, zorder=z)
            ax.fill_between(x, lo, hi, color=colour, alpha=0.15, lw=0, zorder=z - 1)
        ax.set_xticks(x)
        ax.set_xticklabels(["%g" % r for r in rates])
        ax.set_xlabel("offered load (req/s)")
        ax.set_title(title, pad=3)
        ax.set_ylim(0, 108)
        ax.grid(axis="y", **ps.GRID)
    axes[0].set_ylabel("attainment (%)\nall arrivals of that class")
    axes[0].legend(loc="lower left", handlelength=1.4, borderpad=0.25,
                   labelspacing=0.22, handletextpad=0.5, fontsize=6.5)
    fig.tight_layout(rect=(0, 0, 1, 1), w_pad=1.1)
    ps.save(fig, os.path.join(HERE, "eval_per_class.pdf"))

    for c, _ in CLASSES:
        print("\n%s -- all-arrivals attainment of that class (min..max, 2 repeats)" % c)
        print("req/s".rjust(6) + "".join(a[:12].rjust(15) for a, _, _, _, _ in ARMS))
        for r in rates:
            line = ("%g" % r).rjust(6)
            for arm, _, _, _, _ in ARMS:
                v = [q[c] for q in data[arm][r]]
                line += ("%.1f-%.1f" % (min(v), max(v))).rjust(15)
            print(line)


if __name__ == "__main__":
    main()
