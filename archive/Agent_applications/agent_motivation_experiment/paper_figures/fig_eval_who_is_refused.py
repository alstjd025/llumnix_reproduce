#!/usr/bin/env python3
"""Paper figure (evaluation): the two policies refuse different classes.

  eval_who_is_refused.pdf   7.0 x 2.05 in, FULL TEXT WIDTH, two panels

**The claim.** "Are you just sacrificing a class?" is the first question the
per-class result invites, and the honest answer is yes -- both policies do, and
they choose differently. The rejection rate of each class, side by side:

  FluidServe   refuses the agent class hardest: 64% at 35 req/s, 73% at 45, 81%
               at 70, against chat's 14 / 30 / 52.
  llm-d        refuses CHAT hardest: 59% at 35, 90% at 45, 89% at 70, against
               the agent class's 29 / 32 / 65.

Chat is 76.9% of arrivals, so the two choices do not cost the same. That is the
whole of the aggregate difference stated as a design decision rather than as a
score.

**Neither policy has a rule that names a class.** The selection is a consequence
of the budgets: the agent class is scored on 30 s end to end, which under load is
the first budget that cannot be met from any instance, so our feasibility test
refuses it. llm-d filters on predicted headroom against a per-token budget, and
chat's 50 ms is the tightest of the three, so its headroom goes negative first.
The figure is therefore a statement about what each admission rule implies, not
about a preference either system was given.

**Where the loss goes, and why rejection is the right axis.** Splitting each
class's arrivals three ways -- met, rejected, unfinished when the window closed --
puts almost all of the loss in rejection for both arms. FluidServe's agent class
at 45 req/s is 73% rejected and 0% unfinished; llm-d's chat at 45 is 90% rejected
and 0% unfinished. So this figure and the per-class attainment figure carry the
same information, and this one carries it as a decision.

**Data.** `paper_experiment/static_sweep_clean_2026-08/`: eight rates, two
repeats, `GOMAXPROCS=16`. FluidServe and llm-d from EXP-82.

**CAVEATS for the caption.**

1. llm-d's rejections are counted as its runner labels them. 96-100% of them
   arrive as an HTTP 500 on the KV-threshold path rather than a 429, and whether
   those are admission decisions or failures changes that arm's ADMITTED
   attainment a great deal. It does not change this figure, which counts the
   requests that produced nothing either way.
2. Two repeats; bands are min..max. llm-d at 10 req/s is the unstable cell.
3. The agent class is scored on 30 s end to end for every arm, whatever the
   workload config states.

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
CLASSES = [("chat", "chat  (76.9%)", "#1f77b4", "s"),
           ("deepresearch", "deep research  (15.4%)", "#ff7f0e", "o"),
           ("swe", "agent  (7.7%)", "#d62728", "^")]
PANELS = [("FluidServe", "FluidServe refuses the agent class"),
          ("llm-d", "llm-d refuses chat")]


def rejected_by_class(run_dir):
    r = load_run(run_dir)
    if r is None or r.empty:
        return None
    out = {}
    for c, _, _, _ in CLASSES:
        s = r[r["class"] == c]
        out[c] = 100.0 * s["rejected"].mean() if len(s) else np.nan
    return out


def main():
    mpath = os.path.join(EXPDIR, "paper_experiment",
                         "static_sweep_clean_2026-08", "manifest.tsv")
    data = collections.defaultdict(lambda: collections.defaultdict(list))
    for row in csv.DictReader(open(mpath), delimiter="\t"):
        if row["arm_label"] not in dict(PANELS):
            continue
        v = rejected_by_class(os.path.join(EXPDIR, "results", row["run"]))
        if v:
            data[row["arm_label"]][float(row["req_per_s"])].append(v)

    rates = sorted(data["FluidServe"])
    x = np.arange(len(rates))
    plt.rcParams.update(ps.STYLE)
    fig, axes = plt.subplots(1, 2, figsize=(ps.TEXT_W, 2.05), sharey=True)

    for ax, (arm, title) in zip(axes, PANELS):
        for c, label, colour, marker in CLASSES:
            m = [np.mean([v[c] for v in data[arm][r]]) for r in rates]
            lo = [min(v[c] for v in data[arm][r]) for r in rates]
            hi = [max(v[c] for v in data[arm][r]) for r in rates]
            ax.plot(x, m, color=colour, marker=marker, ms=3.2, lw=1.4, label=label)
            ax.fill_between(x, lo, hi, color=colour, alpha=0.16, lw=0)
        ax.set_xticks(x)
        ax.set_xticklabels(["%g" % r for r in rates])
        ax.set_xlabel("offered load (req/s)")
        ax.set_ylim(0, 105)
        ax.grid(axis="y", **ps.GRID)
        ax.set_title(title, pad=3)
    axes[0].set_ylabel("rejected (%)\nof that class's arrivals")
    axes[0].legend(loc="upper left", handlelength=1.4, borderpad=0.25,
                   labelspacing=0.22, handletextpad=0.5, fontsize=7)
    fig.tight_layout(rect=(0, 0, 1, 1), w_pad=1.2)
    ps.save(fig, os.path.join(HERE, "eval_who_is_refused.pdf"))

    for arm, _ in PANELS:
        print("\n%s -- rejection rate of each class (min..max, 2 repeats)" % arm)
        print("req/s".rjust(6) + "".join(c[:14].rjust(18) for c, _, _, _ in CLASSES))
        for r in rates:
            line = ("%g" % r).rjust(6)
            for c, _, _, _ in CLASSES:
                v = [q[c] for q in data[arm][r]]
                line += ("%.1f-%.1f" % (min(v), max(v))).rjust(18)
            print(line)


if __name__ == "__main__":
    main()
