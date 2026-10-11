#!/usr/bin/env python3
"""Paper figure: what the engines were doing underneath the scores, on the run
set the token-level-deadline motivation figure is drawn from.

  engine_state_2x2_t75.pdf         the four baselines
  engine_state_2x2_t75_withfs.pdf  the same plus FluidServe
  3.335 x 3.05 in, one column, width=\\columnwidth

  Each PDF is written with a CSV of the same basename holding exactly the values
  drawn in it: one row per arm and arrival rate, the mean that is the marker,
  the min and max that are the error bar, and the number of repeats behind the
  cell.

  (a) preemptions accumulated over one 8-minute condition, summed over the four
      engines
  (b) KV occupancy, 90th percentile
  (c) waiting queue, 90th percentile
  (d) running batch, 90th percentile

⚠ THIS IS NOT `engine_state_2x2_fs.pdf` AND MUST NOT OVERWRITE IT.
`fig_preemption_kv.py` writes that one from `fig_intro_capacity.py`'s globs,
which are the pinned EARLIER static sweep (`paper_experiment/static_sweep_2026-08/`,
80 runs, 2026-08-13). This file draws THE SAME FOUR PANELS from the runs that
`motivation_throughput_vs_goodput_4panel_t75_withfs.pdf` is drawn from, so that
the engine-layer panels and the score panels describe one set of runs. The two
figures therefore hold different numbers for the same arm at the same arrival
rate, and `t75` in the name is what records which sweep a number came from.

DATA, AND WHY IT IS TAKEN FROM THE SCORER'S TABLE RATHER THAN FROM A GLOB.
The run set is read out of the `run` column of the two tables the score figure
reads -- `results/aggregate_analysis/ladder95/exp108_paper_ladder95.csv` and
`vllm77_ladder95.csv` -- so the two figures cannot drift apart, and so that
EXP-108's llm-d cell at 10 req/s carries the same EXP-110 substitution here as
it does there (`build_paper_ladder_table.py` explains the substitution). 79 runs:
EXP-108's four arms at eight arrival rates with two repeats (62 runs), the two
EXP-110 replacements, and EXP-77's vLLM router (15 runs).

NOTHING ON THIS FIGURE IS SCORED. Every quantity is a counter or a gauge from
the engine's own Prometheus series, so the scoring rule that the name `t75`
refers to does not enter any number here; it identifies the run set only.

⚠ THE vLLM ROUTER IS A DIFFERENT SESSION, three weeks earlier and across the
2026-08-28 machine restore, and it is thinner: 45 and 70 req/s carry ONE repeat
rather than two, so those two points have no error bar and are the least precise
points on the figure rather than the most. The 45 req/s repeat 2 directory
exists but holds no `server_metrics/engine_*.jsonl` at all, which is why it
drops out here while it still appears in the score figure. The script prints the
n behind every cell and the CSV carries it.

⚠ THE TWO TOP PANELS CANNOT BE READ SEPARATELY, because zero preemptions happens
for two opposite reasons: an engine that is comfortable and an engine that is
starved of work because its control plane refused the arrivals both evict
nothing. (b) is what separates them, and (c) separates a fleet that is keeping
up from one whose arrivals are waiting outside the batch.

WHY (b), (c) AND (d) ARE 90TH PERCENTILES AND NOT MEANS. A preemption fires when
the KV pool runs out, so what predicts one is the top of the distribution and
not its centre; a pool that is full half the time reads near 50 on a mean. The
queue is skewed enough that a mean is pulled to the quiet stretches, and the
running batch only shows whether it reaches its cap at the top. Every percentile
is taken PER ENGINE and then averaged over the four, never pooled: pooling would
let one saturated engine beside three idle ones read as a fleet at the middle,
which is exactly the state a static class partition produces.

THE QUEUE PANEL IS SYMLOG, because its values run from 0 -- which llm-d holds at
every arrival rate -- into the thousands, and a plain log axis drops the zeros
while a linear one flattens everything below a few hundred onto the axis.

    python3 paper_figures/fig_engine_state_t75.py
"""
import collections
import importlib.util
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
import paper_style as ps  # noqa: E402


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# `series()` -- the Prometheus reading -- is imported rather than copied, so a
# correction to how a counter or a percentile is read reaches both figures.
PK = _load_module(os.path.join(HERE, "fig_preemption_kv.py"), "preemptkv")
FOUR = _load_module(os.path.join(HERE, "fig_motivation_tg_4panel.py"), "tg4")

# Arm key, paper name, colour, marker, marker size -- taken from the score
# figure unchanged, so an arm is the same colour AND the same shape in both.
ARMS = list(FOUR.ARMS)
OURS = "FluidServe"
LADDER = os.path.join(ROOT, "results", "aggregate_analysis", "ladder95")
TABLES = [os.path.join(LADDER, "exp108_paper_ladder95.csv"),
          os.path.join(LADDER, "vllm77_ladder95.csv")]

RATES = [10, 15, 20, 25, 35, 45, 55, 70]        # what was measured
LABEL_TICKS = [10, 20, 30, 40, 50, 60, 70]      # what the axis is labelled with
GRID_H = 3.05

# (index into the tuple `series` returns, panel title, y label, CSV name).
# "Counts", not "requests", in (a): `vllm:num_preemptions_total` counts
# preemption OCCURRENCES and vLLM V1 returns an evicted request to the waiting
# queue, so a request evicted twice counts twice. (c) and (d) are gauges of
# requests, where "requests" is the unit.
PANELS = [
    (0, "(a) Preemptions", "Counts", "preemptions"),
    (2, "(b) KV occupancy", "p90 (%)", "kv_p90_pct"),
    (3, "(c) Waiting queue", "p90 (requests)", "queue_p90_reqs"),
    (4, "(d) Running batch", "p90 (requests)", "batch_p90_reqs"),
]
# The fifth quantity `series` returns. Not drawn -- (b) is the p90 -- but
# written to the CSV because the two-panel figure `preemption_kv.pdf` draws the
# mean and a reader comparing the two needs both from one table.
EXTRA = [(1, "kv_mean_pct")]


def run_list():
    """The runs the score figure draws, arm and directory, from its own tables."""
    frames = []
    for t in TABLES:
        if not os.path.exists(t):
            sys.exit(f"missing {t}; run build_paper_ladder_table.py first")
        frames.append(pd.read_csv(t)[["run", "arm"]])
    d = pd.concat(frames, ignore_index=True)
    rate = d["run"].str.extract(r"_rpm_(\d+)")[0]
    if rate.isna().any():
        for r in d.loc[rate.isna(), "run"]:
            print(f"!! no _rpm_ in {r}; dropped", file=sys.stderr)
    d = d[rate.notna()].copy()
    d["rate"] = rate[rate.notna()].astype(float) / 60.0
    return d


def collect():
    """label -> rate -> list of the five quantities, one entry per repeat.

    A run whose directory holds no engine metrics is dropped LOUDLY. That is not
    hypothetical -- one EXP-77 directory is in that state -- and a silent drop
    would leave a one-repeat cell looking like a two-repeat one.
    """
    d = run_list()
    by_arm = {a: lbl for a, lbl, _, _, _ in ARMS}
    out, dropped = {}, []
    for arm, label, _, _, _ in ARMS:
        acc = collections.defaultdict(list)
        for _, row in d[d["arm"] == arm].iterrows():
            path = os.path.join(ROOT, "results", row["run"])
            s = PK.series(path)
            if s is None:
                dropped.append(row["run"])
                continue
            acc[float(row["rate"])].append(s)
        if not acc:
            print(f"!! no engine metrics for any run of arm {arm}",
                  file=sys.stderr)
            continue
        out[label] = dict(acc)
    unknown = sorted(set(d["arm"]) - set(by_arm))
    if unknown:
        sys.exit(f"arm(s) in the tables with no entry in ARMS: {unknown}. "
                 "Register them before drawing, or the figure omits them "
                 "silently.")
    if dropped:
        print(f"!! {len(dropped)} run(s) have no server_metrics/engine_*.jsonl "
              f"and are NOT on the figure:")
        for r in dropped:
            print(f"     {r}")
    return out


def report(data):
    print(f"{'arm':13s} {'req/s':>5s} {'n':>2s} {'preempt':>9s} {'KVmean':>7s} "
          f"{'KVp90':>6s} {'Qp90':>8s} {'Bp90':>6s}")
    for _, label, _, _, _ in ARMS:
        if label not in data:
            continue
        for r in sorted(data[label]):
            v = data[label][r]
            m = [np.mean([x[i] for x in v]) for i in range(5)]
            print(f"{label:13s} {r:5.0f} {len(v):2d} {m[0]:9,.0f} {m[1]:7.1f} "
                  f"{m[2]:6.1f} {m[3]:8,.0f} {m[4]:6,.0f}")


def write_csv(data, labels, out_path):
    rows = []
    for label in labels:
        for r in sorted(data[label]):
            v = data[label][r]
            row = {"arm": label, "rate_req_s": r, "n_repeats": len(v)}
            for key, _, _, name in PANELS:
                xs = [x[key] for x in v]
                row[name] = float(np.mean(xs))
                row[f"{name}_min"] = float(np.min(xs))
                row[f"{name}_max"] = float(np.max(xs))
            for key, name in EXTRA:
                xs = [x[key] for x in v]
                row[name] = float(np.mean(xs))
                row[f"{name}_min"] = float(np.min(xs))
                row[f"{name}_max"] = float(np.max(xs))
            row["source"] = "engine Prometheus series; no SLO scoring"
            row["run_set"] = "exp108_paper_ladder95 + vllm77_ladder95"
            rows.append(row)
    pd.DataFrame(rows).to_csv(out_path, index=False)
    print(f"wrote {out_path}  ({len(rows)} rows)")


def build(data, arms, out):
    with plt.rc_context(ps.STYLE):
        fig, axes = plt.subplots(2, 2, figsize=(ps.COL_W, GRID_H), sharex=True)
        ax = np.asarray(axes).ravel()
        handles, labels = [], []

        for _, label, col, mk, msz in arms:
            x = sorted(data[label])
            for i, (key, _, _, _) in enumerate(PANELS):
                y = np.array([np.mean([v[key] for v in data[label][r]])
                              for r in x])
                lo = y - np.array([min(v[key] for v in data[label][r])
                                   for r in x])
                hi = np.array([max(v[key] for v in data[label][r])
                               for r in x]) - y
                h = ax[i].errorbar(x, y, yerr=[lo, hi], color=col, marker=mk,
                                   ms=msz, lw=0.9, capsize=1.3, elinewidth=0.7)
                if i == 0:
                    handles.append(h)
                    labels.append(label)

        for i, (key, title, ylab, _) in enumerate(PANELS):
            a = ax[i]
            a.set_title(title, fontsize=8, pad=2)
            a.set_ylabel(ylab, labelpad=1.5)
            a.set_xlim(7, 73)
            # Labels every 10 req/s, an unlabelled minor tick on each rate that
            # was measured -- the axis of the score figure, so the two read the
            # same way.
            a.set_xticks(LABEL_TICKS)
            a.set_xticks(RATES, minor=True)
            a.grid(axis="both", **ps.GRID)
            a.set_axisbelow(True)
            a.spines["top"].set_visible(False)
            a.spines["right"].set_visible(False)
            # Tick NUMBERS on every panel because each has its own y scale; the
            # x axis NAME only on the bottom row, where the quantity is shared.
            a.tick_params(labelbottom=True)
            if i >= len(PANELS) - 2:
                a.set_xlabel("Offered rate (req/s)", labelpad=1.5)

            if key == 0:                                # preemptions
                a.set_ylim(0, None)
                a.yaxis.set_major_formatter(ps.kfmt())
            elif key == 2:                              # KV p90, per cent
                a.set_ylim(0, 105)
                a.set_yticks([0, 25, 50, 75, 100])
            elif key == 3:                              # waiting queue p90
                a.set_yscale("symlog", linthresh=1.0)
                a.set_ylim(0, 8000)
            else:                                       # running batch p90
                a.set_ylim(0, None)

        # Reserved in INCHES, not as a fraction of the canvas, so the gap above
        # the panels is the same physical size whatever the canvas height is.
        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 1 - 0.19825 / GRID_H), frameon=False,
                   fontsize=6.5, columnspacing=0.6, handlelength=1.2,
                   handletextpad=0.3, borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 1 - 0.183 / GRID_H),
                         w_pad=1.0, h_pad=0.9, pad=0.3)
        ps.save(fig, out)


def main():
    data = collect()
    arms_fs = [a for a in ARMS if a[1] in data]
    arms = [a for a in arms_fs if a[1] != OURS]
    report(data)

    for tag, sel in (("_t75", arms), ("_t75_withfs", arms_fs)):
        pdf = os.path.join(HERE, f"engine_state_2x2{tag}.pdf")
        build(data, sel, pdf)
        write_csv(data, [a[1] for a in sel], pdf[:-4] + ".csv")
        print(f"  arms drawn: {', '.join(a[1] for a in sel)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
