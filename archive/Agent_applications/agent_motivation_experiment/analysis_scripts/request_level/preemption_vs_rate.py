#!/usr/bin/env python3
"""Preemptions against arrival rate for the five control planes, with the panel
that stops the top one from being misread.

  preemption_vs_rate.png

WHY TWO PANELS. A preemption count on its own cannot be ranked, because zero
happens for two opposite reasons and the figure would invite the wrong one:

  llm-d          zero at every rate because it refuses 72-80% of arrivals and
                 leaves KV at 24-36%. The engines are short of work; there is
                 nothing to evict. Its token throughput is the lowest of the five.
  FluidServe     zero up to 25 req/s because what it placed fits. Its throughput
                 is the highest of the five.

So the lower panel carries KV occupancy, which separates starved from healthy
from thrashing, and the two panels have to be read together.

WHAT A PREEMPTION COSTS. vLLM V1 preempts by recompute: the prefill already done
for that request is discarded and paid for again when it is rescheduled. That
engine time produces no tokens, and no request-level metric attributes it to
anything, which is why it belongs on a figure of its own.

PolyServe FALLS from 3,175 at 35 req/s to 1,902 at 70, and that is not an
improvement. What was measured on its bottleneck engine, which is the chat engine
at every rate (100% chat at 25, 99% at 70 -- only the engine index moves, the
class does not):

  req/s   batch mean   batch max   KV mean   preemptions   waiting queue mean
     25          551         879       68%         2,137                  456
     35          554         941       68%         2,570                1,966
     45          552       1,024       59%         1,843                2,918
     55          508       1,024       52%         1,563                3,685
     70          464       1,024       40%           940                4,492

From 45 req/s the batch is pinned at exactly 1,024, which is the engine's cap on
concurrent sequences; at 25 and 35 it peaks below that at 879 and 941. Over the
same range the engine's KV occupancy FALLS, 68% down to 40%, while its queue
grows to 4,492. A preemption in vLLM V1 fires when KV runs out, so an engine
whose KV is no longer the binding constraint has less reason to evict anything,
and the count drops even as the run gets worse.

WHAT IS NOT ESTABLISHED: why the mean KV falls. Two explanations fit these
gauges and they cannot be separated with them -- the sequence cap binding before
the KV limit, so the engine stops filling KV; or step time going into prefill of
the queue, leaving fewer sequences resident per step. Telling them apart needs
the per-step decomposition of EXP-16, not these counters.

Counts are per 8-minute condition, summed over the four engines, taken as the
difference between the last and first sample of `vllm:num_preemptions_total` in
each engine's own metrics file. Error bars are min..max over repeats; a point
with no bar is one run.

    python3 analysis_scripts/request_level/preemption_vs_rate.py [--out-dir DIR]
"""
import argparse
import collections
import glob
import json
import os
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from exp22_fluidserve import PAPER_STYLE  # noqa: E402

PRE = "vllm:num_preemptions_total"
KV = "vllm:kv_cache_usage_perc"

# The post-fix workload only. Each arm's globs are written out one at a time for
# the reason recorded in redraw_static_sweep_workload2026-08-08.sh: the pre-fix
# sweeps are still on disk and a looser pattern averages them in silently.
ARMS = [
    ("FluidServe v0.2", "#17becf", "o", [
        "results/*exp68s*_fspfx_m1_rpm_*", "results/*exp68r*_fspfx_m1_rpm_*",
        "results/*exp70*_fspfx_m1_rpm_*"]),
    ("vLLM router (cache-aware)", "#7b3294", "h", [
        "results/*exp77r*_vllmcache_m1_rpm_*"]),
    ("PolyServe", "#d62728", "s", ["results/*exp72r1_polyserve_m1_rpm_*"]),
    ("Llumnix SLO", "#2ca02c", "^", ["results/*exp72r1_slo_m1f_rpm_*"]),
    ("llm-d", "#8c564b", "D", [
        "results/*exp68s*_llmdslo_m1f_rpm_*", "results/*exp68r*_llmdslo_m1f_rpm_*",
        "results/*exp70*_llmdslo_m1f_rpm_*"]),
]
GRID = [10, 15, 20, 25, 35, 45, 55, 70]


def series(run):
    """(preemptions summed over engines, mean KV % over engines and samples)."""
    total, kvs, seen = 0.0, [], 0
    for f in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        first = last = None
        vals = []
        with open(f) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                for k, v in d.items():
                    if v is None:
                        continue
                    if k.startswith(PRE):
                        if first is None:
                            first = v
                        last = v
                    elif k.startswith(KV):
                        vals.append(v)
        if first is not None and last is not None:
            total += max(0.0, last - first)
            seen += 1
        if vals:
            kvs.append(float(np.mean(vals)))
    if not seen:
        return None
    return total, (100.0 * float(np.mean(kvs)) if kvs else np.nan)


def collect():
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for name, _c, _m, globs in ARMS:
        for g in globs:
            for d in sorted(glob.glob(g)):
                if "PRERUN" in d:
                    continue
                s = series(d)
                if s is None:
                    continue
                out[name][int(d.rsplit("_", 1)[-1]) // 60].append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="results/aggregate_analysis/preemption")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    data = collect()

    print(f"{'req/s':>6}" + "".join(f"{n:>22}" for n, _, _, _ in ARMS))
    for r in GRID:
        row = f"{r:>6}"
        for n, _, _, _ in ARMS:
            v = data[n].get(r)
            row += f"{(f'{np.mean([x[0] for x in v]):,.0f} (n={len(v)})' if v else '-'):>22}"
        print(row)

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(2, 1, figsize=(7.0, 6.2), sharex=True)
        for name, colour, marker, _g in ARMS:
            if name not in data:
                continue
            xs = [r for r in GRID if r in data[name]]
            for i, key in enumerate((0, 1)):
                ys = [np.mean([x[key] for x in data[name][r]]) for r in xs]
                lo = [ys[j] - min(x[key] for x in data[name][r]) for j, r in enumerate(xs)]
                hi = [max(x[key] for x in data[name][r]) - ys[j] for j, r in enumerate(xs)]
                ax[i].errorbar(xs, ys, yerr=[lo, hi], color=colour, marker=marker,
                               label=name if i == 0 else None, capsize=2, lw=1.4,
                               markersize=5, markeredgecolor="white", markeredgewidth=0.5)
        ax[0].set_ylabel("preemptions per 8-minute condition\n(four engines, recompute each)")
        ax[1].set_ylabel("KV cache occupancy (%)\nmean over engines and samples")
        ax[1].set_xlabel("offered rate (requests/s)")
        ax[1].set_ylim(0, 100)
        for x in ax:
            x.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            x.set_axisbelow(True)
            x.set_xticks(GRID)
        ax[0].legend(loc="upper left", frameon=False, fontsize=8)
        # The two readings that would otherwise be got backwards. They go under
        # the axes rather than inside them: placed in the panel they sat on top
        # of the PolyServe and Llumnix SLO curves, which is the failure the
        # exp-plot skill records for long annotations.
        # The two footnotes are stacked full width, not placed side by side.
        # Side by side they collided: at this font size the canvas holds about
        # 140 characters on one line, and half of it does not hold 77.
        fig.text(0.012, 0.068,
                 "llm-d holds at zero because it refuses 72-80% of arrivals and its engines are short of work, not because they are healthy:\n"
                 "KV sits at 28-32% in the lower panel and its token throughput is the lowest of the five.",
                 fontsize=7.5, color="#8c564b", va="bottom")
        fig.text(0.012, 0.008,
                 "PolyServe FALLS after 35 req/s and the run is not improving. Its bottleneck engine is the chat engine throughout, and its KV p90\n"
                 "and p99 are 100% at EVERY rate -- it never stops hitting the ceiling. What changes is how long it stays there: the median goes\n"
                 "99% to 31%. Preemption counts evictions, so an engine sitting at the ceiling evicts constantly and one that only spikes to it\n"
                 "does not. The MEAN KV in the lower panel falls 68% to 40% and hides this; see engine_state_p90.png.",
                 fontsize=7.5, color="#d62728", va="bottom")
        fig.suptitle("Preemptions and KV occupancy against arrival rate, five control planes\n"
                     "post-fix workload; a point with no error bar is one run "
                     "(PolyServe and Llumnix SLO are one run throughout)",
                     fontsize=9)
        fig.tight_layout(rect=(0, 0.17, 1, 0.94))
        p = os.path.join(a.out_dir, "preemption_vs_rate.png")
        fig.savefig(p, dpi=200)
        print("\nwrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
