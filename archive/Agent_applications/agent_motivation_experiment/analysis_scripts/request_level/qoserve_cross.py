#!/usr/bin/env python3
"""What a deadline-aware engine scheduler changes, under four control planes.

THE QUESTION. If the routing layer places requests badly, can an engine-side
SLO scheduler recover the loss? Four control planes have now been crossed with
stock vLLM ordering and with the Niyama port (`deadline_sched.DeadlineScheduler`).
The answer is the same in all four: it moves the score by at most about three
points, in either direction, while the gap between control planes is sixty.

THREE PANELS, because the claim needs all three to be honest.

  A  the change the engine scheduler makes. This is the quantity under test.
  B  the levels it would have to close. Without B a reader sees "+2.8" and
     cannot tell whether that is most of the gap or a twentieth of it.
  C  why. The engine scheduler orders the requests waiting AT an instance, so it
     can only act where a queue exists and can never move work to an idle one.
     This is the mechanism behind both signs: load balancing spreads a queue
     over all four engines and the reordering does not pay for its throughput
     cost, while the static partition piles 2,300 requests on one engine and
     leaves three empty, so reordering inside that one engine recovers a little.

PAIRING, AND WHERE IT DOES NOT HELP. The two arms of every pair ran adjacently
in one session, so A reports PAIRED differences: the k-th run of one arm against
the k-th run of the other. For three of the four planes that gives tight
intervals, at most 1.4 points wide.

It does not work for FluidServe at 45 req/s, and the reason is worth stating
rather than hiding. That arm's score depends on whether class separation happens
to form in the run, which is a coin flip at this rate: the two FIFO runs scored
87.68 and 99.08 and the two QoServe runs scored 99.48 and 88.46, so the paired
differences are +11.80 and -10.62 around a mean of +0.59. Pairing removes a
session offset; it cannot remove a per-run bimodality. **The honest reading of
that bar is that this experiment says nothing about QoServe's effect on
FluidServe at 45 req/s**, and the panel is annotated to that effect rather than
showing a small bar with a plausible-looking interval.

  python3 analysis_scripts/request_level/qoserve_cross.py
"""
import glob
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt            # noqa: E402
import numpy as np                          # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run, attain   # noqa: E402

OUT = "results/aggregate_analysis/exp62"

# plane -> (experiment tag, arm stem). The arm directory is <stem>fifo or
# <stem>qoserve, and the repeat is the r<N> in the session tag.
PLANES = [
    ("Llumnix", "exp61", "loadbalance"),
    ("Llumnix SLO",          "exp40", "slo"),
    ("PolyServe",            "exp62", "polyserve"),
    ("FluidServe",           "exp40", "fluidserve"),
]
COLOR = {"Llumnix": "#7f7f7f", "Llumnix SLO": "#2ca02c",
         "PolyServe": "#d62728", "FluidServe": "#1f77b4"}
RATES = [45, 50, 60]
# Excluded with its reason in ms_dev/notes/excluded_runs.tsv: load balance has no
# admission control yet that condition reported 22.6% rejected, because the
# gateway returned 400 from minute 3 and 503 from minute 7 (implementation §63.6.1).
EXCLUDE = ["260806_0328_exp61r1_loadbalanceqoserve_m1_rpm_3600"]

# One FIFO run per plane at 60 req/s for panel C. FluidServe's comes from EXP-62
# rather than EXP-40 so that panel C shows the current policy; the queue is zero
# in both, which is the point being made.
QUEUE_RUNS = {
    "Llumnix": "results/260806_0241_exp61r1_loadbalancefifo_m1_rpm_3600",
    "Llumnix SLO": "results/260803_0128_exp53p2r1_slo_m1f_rpm_3600",
    "PolyServe": "results/260806_0801_exp62r1_polyservefifo_m1_rpm_3600",
    "FluidServe": "results/260806_0843_exp62r1_fluidservefifo_m1_rpm_3600",
}


def collect(exp, stem, eng):
    """rate -> [offered attainment, in time order] for one arm.

    Keyed by rate and NOT by the r<N> in the session tag, because that tag is
    not unique: EXP-40 ran `slofifo` twice inside repeat 1 (07:43 and 08:31 on
    2026-07-30), so a dict keyed on (rate, repeat) silently kept only the later
    one. Runs are returned in time order and paired positionally below.
    """
    out = {}
    for d in sorted(glob.glob(f"results/*_{exp}*_{stem}{eng}_*_rpm_*")):
        if os.path.basename(d) in EXCLUDE:
            continue
        rate = int(os.path.basename(d).split("_rpm_")[1]) // 60
        r = load_run(d)
        if r is None or r.empty:
            continue
        out.setdefault(rate, []).append(attain(r, "violate_offered"))
    return out


def queues_at_60(d):
    """Median waiting requests per engine, descending."""
    out = []
    for f in sorted(glob.glob(os.path.join(d, "server_metrics/engine_*.jsonl"))):
        w = []
        for line in open(f):
            try:
                r = json.loads(line)
            except Exception:
                continue
            for k, v in r.items():
                if k.startswith("vllm:num_requests_waiting"):
                    w.append(float(v))
                    break
        if w:
            out.append(float(np.median(w)))
    return sorted(out, reverse=True)


def main():
    fifo, qos, pairs, levels = {}, {}, {}, {}
    for name, exp, stem in PLANES:
        fifo[name] = collect(exp, stem, "fifo")
        qos[name] = collect(exp, stem, "qoserve")
        # Paired differences, keyed by rate. The two arms ran adjacently in one
        # session, so the k-th run of one is paired with the k-th run of the
        # other. Where the counts differ the surplus is dropped and said so,
        # rather than being averaged in silently.
        p, dropped = {}, 0
        for rate in sorted(set(fifo[name]) | set(qos[name])):
            f, q = fifo[name].get(rate, []), qos[name].get(rate, [])
            n = min(len(f), len(q))
            dropped += (len(f) - n) + (len(q) - n)
            if n:
                p[rate] = [q[k] - f[k] for k in range(n)]
        if dropped:
            print(f"  {name}: {dropped} run(s) with no partner in the other arm, dropped")
        pairs[name] = p
        levels[name] = dict(fifo[name])
        print(f"  {name:22s} " + "  ".join(
            f"{r}:{np.mean(pairs[name][r]):+.2f}(n={len(pairs[name][r])})"
            for r in sorted(pairs[name])))

    queues = {p: queues_at_60(d) for p, d in QUEUE_RUNS.items()}
    for p, q in queues.items():
        print(f"  {p:22s} queue at 60 req/s: {[f'{x:.0f}' for x in q]}")

    with plt.rc_context(PAPER_STYLE):
        fig, (axA, axB, axC) = plt.subplots(1, 3, figsize=(11.0, 3.2))
        fig.subplots_adjust(wspace=0.42)
        w = 0.2

        for i, (name, _, _) in enumerate(PLANES):
            xs, ys, lo, hi = [], [], [], []
            for j, r in enumerate(RATES):
                if r not in pairs[name]:
                    continue
                v = pairs[name][r]
                xs.append(j + (i - 1.5) * w)
                ys.append(float(np.mean(v)))
                lo.append(float(np.mean(v)) - min(v))
                hi.append(max(v) - float(np.mean(v)))
            axA.bar(xs, ys, width=w, color=COLOR[name], label=name)
            axA.errorbar(xs, ys, yerr=[lo, hi], fmt="none", ecolor="0.2",
                         elinewidth=0.8, capsize=2)
        axA.axhline(0, color="0.2", lw=0.8)
        # FluidServe at 45 req/s has paired differences of +11.8 and -10.62.
        # Letting that set the y limits would compress every other bar into the
        # axis line, so the limit is set from the rest and the outlier is named.
        axA.set_ylim(-5.0, 5.0)
        if 45 in pairs["FluidServe"]:
            v = pairs["FluidServe"][45]
            axA.annotate(f"paired differences\n{max(v):+.1f} and {min(v):+.1f}:\n"
                         "this arm is bimodal at\n45 req/s, so the comparison\nis uninformative here",
                         xy=(0.3, 0.6), xytext=(-0.48, 4.8), fontsize=5.0,
                         color=COLOR["FluidServe"], ha="left", va="top",
                         arrowprops=dict(arrowstyle="-", color=COLOR["FluidServe"], lw=0.6))
        axA.set_xticks(range(len(RATES)))
        axA.set_xticklabels([str(r) for r in RATES])
        axA.set_xlabel("arrival rate (req/s)")
        axA.set_ylabel("QoServe − FIFO, paired\n(offered SLO attainment, points)")
        axA.set_title("A. what the engine scheduler changes", loc="left")
        axA.grid(axis="y", ls=":", lw=0.5)

        for name, _, _ in PLANES:
            # Same rates as A. EXP-40's 30 req/s point is named in the note; drawn
            # here it would sit left of every tick and read as an axis error.
            rr = [r for r in sorted(levels[name]) if r in RATES]
            mu = [float(np.mean(levels[name][r])) for r in rr]
            el = [mu[k] - min(levels[name][r]) for k, r in enumerate(rr)]
            eh = [max(levels[name][r]) - mu[k] for k, r in enumerate(rr)]
            axB.errorbar(rr, mu, yerr=[el, eh], color=COLOR[name], marker="o",
                         ms=3, lw=1.2, capsize=2, label=name)
        axB.set_xlabel("arrival rate (req/s)")
        axB.set_ylabel("offered SLO attainment (%)")
        axB.set_ylim(0, 105)
        axB.set_xticks(RATES)
        axB.set_title("B. the levels, with stock ordering", loc="left")
        axB.grid(axis="y", ls=":", lw=0.5)

        for i, (name, _, _) in enumerate(PLANES):
            q = queues.get(name) or [0.0] * 4
            axC.bar([i + (k - 1.5) * 0.2 for k in range(len(q))], q,
                    width=0.2, color=COLOR[name])
            # A bar of height zero is invisible, and "no queue at all" is the
            # claim being made about two of the four planes, so it is written on.
            if max(q) < 1:
                axC.text(i, max(1, axC.get_ylim()[1] * 0.02), "0 on all four",
                         ha="center", va="bottom", fontsize=5.5, color=COLOR[name])
        axC.set_xticks(range(len(PLANES)))
        axC.set_xticklabels([n.replace("Llumnix ", "Llumnix\n") for n, _, _ in PLANES],
                            fontsize=6)
        axC.set_ylabel("median waiting requests\nper engine, 60 req/s")
        axC.set_title("C. where a queue exists to reorder", loc="left")
        axC.grid(axis="y", ls=":", lw=0.5)

        h, l = axA.get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", ncol=4, frameon=False,
                   bbox_to_anchor=(0.5, -0.10))
        note = (
            "A: differences are PAIRED within a session (repeat i against repeat i), 2 pairs each, except Llumnix "
            "load balance at 60 req/s where one QoServe repeat is excluded because the gateway stopped serving "
            "(implementation.md §63.6.1), leaving one pair there. Bars span the paired differences. "
            "Pairs come from EXP-40 (Llumnix SLO, FluidServe), EXP-61 (load balance) and EXP-62 (PolyServe); each "
            "pair is internally valid, but the LEVELS in B are not all mutually comparable — FluidServe's is EXP-40 "
            "and predates several policy changes, and the same arm now reads 94.8 at 45 and 60.0 at 60 req/s. "
            "C: the engines' own Prometheus series, one FIFO run per plane, engines sorted descending. "
            "EXP-40 also measured 30 req/s (+0.03 Llumnix SLO, -0.01 FluidServe, both arms above 99.8); it is omitted "
            "because the other two planes have no point there. Three EXP-40 slofifo runs had no partner in the QoServe "
            "arm and are dropped from A; they remain in B.")
        fig.text(0.005, -0.21, note, fontsize=5.2, va="top", wrap=True)
        os.makedirs(OUT, exist_ok=True)
        path = os.path.join(OUT, "qoserve_cross.png")
        fig.savefig(path, bbox_inches="tight", dpi=300)
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
