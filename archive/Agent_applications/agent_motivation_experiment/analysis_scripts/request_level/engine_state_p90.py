#!/usr/bin/env python3
"""Queue depth, KV occupancy and running batch at the 90th percentile, five control planes.

  engine_state_p90.png     six panels: three quantities x (fleet mean | busiest engine)

WHY THE 90TH PERCENTILE AND NOT THE MEAN. The reason differs per quantity and it
is worth keeping straight.

  queue    the distribution is strongly skewed and the mean is pulled down by the
           idle stretches between bursts. What harms a request is the depth at the
           moment it arrives, which is the upper tail.
  KV       this is the one that matters most. A preemption fires when KV runs
           out, so what predicts preemption is the top of the distribution, not
           its centre. An engine averaging 40% with a p90 of 95% and one
           averaging 40% with a p90 of 45% are in completely different states,
           and the mean cannot tell them apart. preemption_vs_rate.py used the
           mean and section 7.8 of EXP-78 drew a conclusion from it; this figure
           exists partly to check that conclusion.
  batch    whether the concurrent-sequence cap is binding shows at the ceiling.
           The mean is diluted by the steps where the batch is refilling after an
           eviction, which is exactly the behaviour under study.

The mean is not useless: for KV it is a fair summary of how much memory was in
use over the run. Where the two disagree the disagreement is the finding, so both
are printed in the table even though only p90 is drawn.

WHY TWO COLUMNS. The engines are not symmetric and for one arm that is the whole
point: PolyServe puts a class that is 77% of the requests on one instance, so its
fleet mean describes an engine that does not exist. The left column averages the
per-engine p90 over the four engines; the right takes the largest of them. Read
the right column for "is any engine in trouble" and the left for "what does the
fleet look like".

Percentiles are computed per engine over that condition's samples, then combined
across engines and across repeats. Error bars are min..max over repeats; a point
with no bar is one run.

    python3 analysis_scripts/request_level/engine_state_p90.py [--out-dir DIR] [--pct 90]
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

SERIES = {"queue": "vllm:num_requests_waiting",
          "kv": "vllm:kv_cache_usage_perc",
          "batch": "vllm:num_requests_running"}

ARMS = [
    ("FluidServe v0.2", "#17becf", "o", [
        "results/*exp68s*_fspfx_m1_rpm_*", "results/*exp68r*_fspfx_m1_rpm_*",
        "results/*exp70*_fspfx_m1_rpm_*"]),
    ("vLLM router (cache-aware)", "#7b3294", "h", ["results/*exp77r*_vllmcache_m1_rpm_*"]),
    ("PolyServe", "#d62728", "s", ["results/*exp72r1_polyserve_m1_rpm_*"]),
    ("Llumnix SLO", "#2ca02c", "^", ["results/*exp72r1_slo_m1f_rpm_*"]),
    ("llm-d", "#8c564b", "D", [
        "results/*exp68s*_llmdslo_m1f_rpm_*", "results/*exp68r*_llmdslo_m1f_rpm_*",
        "results/*exp70*_llmdslo_m1f_rpm_*"]),
]
GRID = [10, 15, 20, 25, 35, 45, 55, 70]


def run_stats(run, pct):
    """Per-engine percentile and mean of each series, then fleet mean and max."""
    per = collections.defaultdict(list)
    for f in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        vals = collections.defaultdict(list)
        with open(f) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                for k, v in d.items():
                    if v is None:
                        continue
                    for name, prefix in SERIES.items():
                        if k.startswith(prefix):
                            vals[name].append(v)
        for name, xs in vals.items():
            if xs:
                scale = 100.0 if name == "kv" else 1.0
                per[name].append((scale * float(np.percentile(xs, pct)),
                                  scale * float(np.mean(xs))))
    if not per:
        return None
    out = {}
    for name, xs in per.items():
        out[name] = dict(p_mean=float(np.mean([a for a, _ in xs])),
                         p_max=float(np.max([a for a, _ in xs])),
                         m_mean=float(np.mean([b for _, b in xs])))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default="results/aggregate_analysis/preemption")
    ap.add_argument("--pct", type=float, default=90)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    data = collections.defaultdict(lambda: collections.defaultdict(list))
    for name, _c, _m, globs in ARMS:
        for g in globs:
            for d in sorted(glob.glob(g)):
                if "PRERUN" in d:
                    continue
                s = run_stats(d, a.pct)
                if s:
                    data[name][int(d.rsplit("_", 1)[-1]) // 60].append(s)

    P = int(a.pct)
    for name in ("queue", "kv", "batch"):
        print(f"\n=== {name}: p{P} averaged over engines | p{P} of the busiest engine | "
              f"mean over engines (for comparison)")
        print(f"{'req/s':>6}" + "".join(f"{n.split(' (')[0]:>26}" for n, _, _, _ in ARMS))
        for r in GRID:
            row = f"{r:>6}"
            for n, _, _, _ in ARMS:
                v = data[n].get(r)
                if not v:
                    row += f"{'-':>26}"
                    continue
                pm = np.mean([x[name]["p_mean"] for x in v])
                px = np.mean([x[name]["p_max"] for x in v])
                mm = np.mean([x[name]["m_mean"] for x in v])
                row += f"{f'{pm:,.0f} | {px:,.0f} | {mm:,.0f}':>26}"
            print(row)

    LAB = {"queue": f"waiting queue, p{P}",
           "kv": f"KV occupancy (%), p{P}",
           "batch": f"running batch, p{P}"}
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(3, 2, figsize=(9.6, 8.4), sharex=True)
        for row, name in enumerate(("queue", "kv", "batch")):
            for col, key in enumerate(("p_mean", "p_max")):
                for arm, colour, marker, _g in ARMS:
                    if arm not in data:
                        continue
                    xs = [r for r in GRID if r in data[arm]]
                    ys = [np.mean([x[name][key] for x in data[arm][r]]) for r in xs]
                    lo = [ys[j] - min(x[name][key] for x in data[arm][r]) for j, r in enumerate(xs)]
                    hi = [max(x[name][key] for x in data[arm][r]) - ys[j] for j, r in enumerate(xs)]
                    ax[row][col].errorbar(
                        xs, ys, yerr=[lo, hi], color=colour, marker=marker,
                        label=arm if (row == 0 and col == 0) else None,
                        capsize=2, lw=1.3, markersize=4.5,
                        markeredgecolor="white", markeredgewidth=0.5)
                ax[row][col].grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
                ax[row][col].set_axisbelow(True)
                ax[row][col].set_xticks(GRID)
            ax[row][0].set_ylabel(LAB[name])
        ax[1][0].set_ylim(0, 100)
        ax[1][1].set_ylim(0, 100)
        # The cap that turns out to matter, drawn where the batch panels can show it.
        for c in (0, 1):
            ax[2][c].axhline(1024, color="#666666", lw=0.8, ls="--")
            ax[2][c].annotate("1,024 = concurrent-sequence cap", xy=(11, 1024),
                              fontsize=7, color="#666666", va="bottom")
        ax[0][0].set_title("averaged over the four engines", fontsize=9)
        ax[0][1].set_title("the busiest engine of the four", fontsize=9)
        ax[2][0].set_xlabel("offered rate (requests/s)")
        ax[2][1].set_xlabel("offered rate (requests/s)")
        ax[0][0].legend(loc="upper left", frameon=False, fontsize=7.5)
        fig.suptitle(
            f"Engine state at the {P}th percentile, five control planes (post-fix workload)\n"
            "left: the per-engine percentile averaged over engines   |   right: the largest of them\n"
            "a point with no error bar is one run; PolyServe and Llumnix SLO are one run throughout",
            fontsize=9)
        fig.tight_layout(rect=(0, 0, 1, 0.93))
        p = os.path.join(a.out_dir, f"engine_state_p{P}.png")
        fig.savefig(p, dpi=200)
        print("\nwrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
