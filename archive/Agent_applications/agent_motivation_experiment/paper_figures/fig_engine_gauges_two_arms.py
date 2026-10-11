#!/usr/bin/env python3
"""Paper figure: every engine's KV pool and running batch, under two control
planes, over the same twelve minutes.

  engine_gauges_two_arms.pdf        3.335 x 2.80 in, one column
  engine_gauges_two_arms_wide.pdf   7.0 x 2.20 in, `figure*`
  engine_gauges_two_arms.csv        exactly the values drawn

  rows     what the engine reports: KV occupancy, then the running batch
  columns  the control plane
  lines    the four engines of the fleet, as scraped each second

WHY ALL FOUR ENGINES AND NOT A FLEET AVERAGE. A mean over four engines
describes an engine that exists only when the four are alike, and whether they
are alike is exactly what separates these two control planes. Under llm-d the
four lines lie on top of each other; under Llumnix SLO they do not.

⚠ THE ENGINE NUMBER IS A RANK, NOT A PORT, and it is computed per column: the
engines are sorted by their mean running batch over the window, so "Instance 1"
is the busiest engine of that arm. A port number would not be comparable across
the two columns, because which port ends up loaded is an outcome.

⚠ THE TWO ROWS ARE NOT THE SAME KIND OF LIMIT. KV occupancy has a ceiling at
100% that the engine enforces; the running batch has no fixed ceiling here, so
its axis is set by the largest value either arm reached and the two columns
share it.

DATA. EXP-109 (2026-08-31) repeat 1 of each arm, minutes 35-47 measured from
each run's own first arrival, from each engine's `server_metrics/engine_*.jsonl`
(`vllm:kv_cache_usage_perc`, `vllm:num_requests_running`). Not smoothed.

    python3 paper_figures/fig_engine_gauges_two_arms.py
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import AutoMinorLocator  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EW = _load("engwin", os.path.join(HERE, "fig_engine_window.py"))

ARMS = ["slo", "llmd"]
PORTS = [8000, 8001, 8002, 8003]
T_LO, T_HI = 35.0, 47.0
# One colour per RANK, shared by the two columns, so "the busiest engine" is the
# same colour on both sides.
ENG_C = ["#1f78b4", "#33a02c", "#e31a1c", "#ff7f00"]
FIG_H = 2.80
LABEL_SIZE = 6


def series(arm_key):
    """label, run, minutes, and per-rank (kv, batch), busiest batch first."""
    label, run = EW.RUNS[arm_key]
    EW.T_LO, EW.T_HI, EW.SMOOTH_S = T_LO, T_HI, 1
    d = os.path.join(ROOT, "results", run)
    t0 = float(pd.read_csv(os.path.join(d, "metrics.csv"),
                           usecols=["start_time"])["start_time"].min())
    per = []
    m = None
    for port in PORTS:
        EW.PORT = port
        mm, kv, bat, _wait, _itl = EW.gauges(d, t0)
        m = mm
        per.append((port, kv, bat))
    per.sort(key=lambda x: -float(np.mean(x[2])))
    return label, run, m, per


def write_csv(data, path):
    rows = []
    for label, run, m, per in data:
        for rank, (port, kv, bat) in enumerate(per, start=1):
            for name, v in (("kv_cache_usage_pct", kv),
                            ("running_batch_requests", bat)):
                rows += [{"arm": label, "run": run, "engine_rank": rank,
                          "engine_port": port, "series": name,
                          "minute": float(a), "value": float(b)}
                         for a, b in zip(m, v)]
    pd.DataFrame(rows).to_csv(path, index=False, float_format="%.4f")
    print(f"wrote {path}  ({len(rows)} rows)")


def build(data, out, width=ps.COL_W, height=FIG_H):
    top_bat = max(float(np.max(b)) for _, _, _, per in data
                  for _, _, b in per)
    style = {**ps.STYLE, "xtick.labelsize": LABEL_SIZE,
             "ytick.labelsize": LABEL_SIZE, "axes.labelsize": LABEL_SIZE}
    with plt.rc_context(style):
        fig, axes = plt.subplots(2, 2, figsize=(width, height))
        for j, (label, _run, m, per) in enumerate(data):
            for i, (name, ylab, top) in enumerate(
                    (("kv", "KV cache (%)", 100.0),
                     ("bat", "Batch (reqs)", np.ceil(top_bat / 50) * 50))):
                a = axes[i][j]
                for rank, (_port, kv, bat) in enumerate(per):
                    a.plot(m, kv if name == "kv" else bat,
                           color=ENG_C[rank], lw=0.7)
                a.set_ylim(0, top)
                a.set_yticks(np.linspace(0, top, 5))
                a.set_yticklabels([f"{t:.0f}" for t in np.linspace(0, top, 5)])
                a.set_xlim(T_LO, T_HI)
                a.set_xticks(np.arange(T_LO, T_HI + 1e-9, 3))
                a.xaxis.set_minor_locator(AutoMinorLocator(3))
                a.yaxis.set_minor_locator(AutoMinorLocator(2))
                a.tick_params(which="minor", length=1.2)
                a.grid(axis="both", **ps.GRID)
                a.set_axisbelow(True)
                a.set_ylabel(ylab, labelpad=1.5)
                if i == 1:
                    a.set_xlabel(f"Time (min.)\n({'ab'[j]}) {label}",
                                 labelpad=1.5, linespacing=1.5)
                else:
                    a.set_xlabel("Time (min.)", labelpad=1.5)
            print(f"    {label}: batch mean per rank " +
                  ", ".join(f"{np.mean(b):.0f}" for _, _, b in per) +
                  "; KV p50 per rank " +
                  ", ".join(f"{np.percentile(k, 50):.0f}%" for _, k, _ in per))

        handles = [Line2D([], [], color=ENG_C[i], lw=0.9,
                          label=f"Instance {i + 1}") for i in range(len(PORTS))]
        band = 0.16
        fig.legend(handles, [h.get_label() for h in handles], loc="lower center",
                   ncol=4, bbox_to_anchor=(0.5, 1 - band / height),
                   frameon=False, fontsize=5.8, columnspacing=0.8,
                   handlelength=1.1, handletextpad=0.3, borderaxespad=0.0)
        rect_top = 1 - (band - 0.01) / height
        fig.tight_layout(rect=(0, 0, 1, rect_top), pad=0.3, w_pad=1.0, h_pad=0.8)
        for _ in range(4):
            fig.canvas.draw()
            gap = height - fig.legends[0].get_window_extent().ymax / fig.dpi
            if abs(gap) <= 0.01:
                break
            rect_top = min(0.999, rect_top + gap / height)
            fig.tight_layout(rect=(0, 0, 1, rect_top), pad=0.3, w_pad=1.0,
                             h_pad=0.8)
        w, h = fig.get_size_inches()
        bb = axes[0][0].get_position()
        print(f"    panel axes box {bb.width * w:.2f} x {bb.height * h:.2f} in, "
              f"top gap {gap:.3f} in")
        ps.save(fig, out)
        return bb.width * w, bb.height * h


def main():
    data = [series(k) for k in ARMS]
    pdf = os.path.join(HERE, "engine_gauges_two_arms.pdf")
    h = FIG_H
    for _ in range(5):
        box = build(data, pdf, ps.COL_W, h)
        if abs(box[0] - box[1]) < 0.02:
            break
        h += box[0] - box[1]
    build(data, os.path.join(HERE, "engine_gauges_two_arms_wide.pdf"),
          ps.TEXT_W, 2.20)
    write_csv(data, pdf[:-4] + ".csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
