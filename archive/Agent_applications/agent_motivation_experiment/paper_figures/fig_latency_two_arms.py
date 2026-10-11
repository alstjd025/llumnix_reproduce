#!/usr/bin/env python3
"""Paper figure: the same twelve minutes on one engine, under two control planes.

  latency_two_arms.pdf   3.335 x 1.95 in, one column, width=\\columnwidth
  latency_two_arms.csv   exactly the values drawn

  (a) Llumnix SLO: the per-token time its engine reported and the share of the
      requests it was given that met their deadline
  (b) llm-d: the same two quantities, same engine index, same minutes

ONE PANEL PER CONTROL PLANE, AND THE AXES ARE SHARED. Both panels use one scale
for the per-token time and one for the attainment, so a height on the left means
what it means on the right; that is the whole reason to split them rather than
draw four curves in one panel. Colour is the QUANTITY (per-token time, then
attainment) and the panel is the control plane.

⚠ WHAT THE FIGURE DOES NOT SHOW IS REJECTION, and it is the reason the two
panels differ. Over these twelve minutes llm-d refuses 39.3% of arrivals and
Llumnix SLO 28.3%; a control plane that admits less work runs its engine at a
lower per-token time and meets the deadline of more of what it kept. The bars in
`hour_slo_vs_llmd_m35_47.pdf` carry that, and the caption of this figure has to
name both numbers or the panels read as "one is better at everything".

⚠ THE ENGINE INDEX IS NOT THE SAME KIND OF THING IN THE TWO PANELS. llm-d mixes
the classes, so its four engines are interchangeable: over this window their
per-token times are 40.4, 40.7, 41.2 and 42.1 ms, a spread of 1.7 ms. Llumnix
SLO's are not, so its panel is one engine of four and not a fleet summary.

DATA. EXP-109 (2026-08-31) repeat 1 of each arm, engine 8002, minutes 35-47 from
each run's own first arrival. Gauges as scraped each second; the per-token time
is the engine's own histogram counters differenced per second, smoothed over
3 s before the division. Attainment is the ladder rule on the requests
dispatched to that engine, admitted denominator, 15 s windows.

    python3 paper_figures/fig_latency_two_arms.py
"""
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.ticker import AutoMinorLocator, FixedLocator  # noqa: E402

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
PORT = 8002
T_LO, T_HI = 35.0, 47.0
# One colour pair per control plane: the panel already separates them, and the
# colours make a curve identifiable when the two panels are quoted apart from
# each other. WITHIN a pair the warmer colour is always the per-token time and
# the cooler one the attainment, so the roles stay the same across panels.
# ⚠ THE HUE IS THE ARM, as everywhere else in this directory: Llumnix SLO is
# green (#2ca02c) and llm-d is brown (#8c564b). Within a panel the two curves
# are two tones of that hue -- the darker one is always the per-token time and
# the lighter one always the attainment -- so a colour never means one thing
# here and another in `hour_slo_vs_llmd.pdf` beside it.
PAIRS = {"slo": ("#1b7837", "#7fbf7b"),        # dark / light green
         "llmd": ("#8c564b", "#d8a37a")}       # dark / light brown
THRU_C = {"slo": "#1b7837", "llmd": "#8c564b"}
FIG_H = 3.10
LABEL_SIZE = 6


def token_rate(run_dir, t0, smooth_s=3):
    """This engine's own tokens per second, from its cumulative counter.

    The bottom row is the SAME ENGINE as the top row, not the fleet: a panel
    that paired one engine's latency with four engines' output would invite the
    reader to divide one by the other.
    """
    path = os.path.join(run_dir, "server_metrics", f"engine_{PORT}.jsonl")
    ts, cum = [], []
    with open(path) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            v = next((x for k, x in r.items()
                      if k.startswith("vllm:generation_tokens_total")
                      and x is not None), None)
            if v is None:
                continue
            ts.append(r["t"]); cum.append(v)
    t = np.array(ts)
    grid = np.arange(t[0], t[-1], 1.0)
    c = np.interp(grid, t, np.array(cum, float))
    d = np.diff(c, prepend=c[0])
    d[d < 0] = 0.0
    d = np.convolve(d, np.ones(smooth_s) / smooth_s, mode="same")
    m = (grid - t0) / 60.0
    keep = (m >= T_LO) & (m <= T_HI)
    return m[keep], d[keep]


def series(arm_key):
    label, run = EW.RUNS[arm_key]
    EW.PORT, EW.T_LO, EW.T_HI, EW.SMOOTH_S = PORT, T_LO, T_HI, 1
    EW.WIN, EW.STEP = 15.0, 15.0
    d = os.path.join(ROOT, "results", run)
    t0 = float(pd.read_csv(os.path.join(d, "metrics.csv"),
                           usecols=["start_time"])["start_time"].min())
    m, _kv, _bat, _wait, itl = EW.gauges(d, t0)
    att = EW.attainment(d, t0)
    tm, tr = token_rate(d, t0)
    return label, run, m, itl, att, (tm, tr)


def write_csv(data, path):
    rows = []
    for label, run, m, itl, att, thr in data:
        rows += [{"arm": label, "run": run, "series": "inter_token_latency_ms",
                  "minute": float(a), "value": float(b)}
                 for a, b in zip(m, itl)]
        rows += [{"arm": label, "run": run,
                  "series": "slo_attainment_admitted_pct", "minute": float(a),
                  "value": float(b), "n_requests": int(c)}
                 for a, b, c in zip(*att)]
        rows += [{"arm": label, "run": run, "series": "tokens_per_s_engine",
                  "minute": float(a), "value": float(b)}
                 for a, b in zip(*thr)]
    df = pd.DataFrame(rows)
    df["engine_port"] = PORT
    df["attainment_window_s"] = 15.0
    df.to_csv(path, index=False, float_format="%.4f")
    print(f"wrote {path}  ({len(df)} rows)")


def build(data, out, width=ps.COL_W, height=FIG_H):
    pool = np.concatenate([d[3][~np.isnan(d[3])] for d in data])
    # ⚠ THE AXIS REACHES THE LARGEST SAMPLE (2026-09-08). It was cut at the 99th
    # percentile so that the 40-110 ms band both arms live in filled the panel;
    # now the few seconds three to five times the rest are on the axis, which
    # costs that band about half its height. Nothing is off the axis and no
    # note is needed; the price is resolution where the data is dense.
    top = float(np.ceil(pool.max() / 20.0) * 20)
    style = {**ps.STYLE, "xtick.labelsize": LABEL_SIZE,
             "ytick.labelsize": LABEL_SIZE, "axes.labelsize": LABEL_SIZE}
    with plt.rc_context(style):
        # Equal rows and no shared x: every panel carries its own axis labels
        # and its own tick numbers, which is what makes each panel readable when
        # one of them is pointed at in the text on its own.
        fig, axes = plt.subplots(2, 2, figsize=(width, height))
        ax, axb = axes[0], axes[1]
        top_thru = max(float(np.nanmax(d[5][1])) for d in data)
        for i, (label, _run, m, itl, att, thr) in enumerate(data):
            itl_c, att_c = PAIRS[ARMS[i]]
            a, b = ax[i], ax[i].twinx()
            ok = ~np.isnan(itl)
            over = ok & (itl > top)
            a.plot(m, itl, color=itl_c, lw=0.9)
            b.plot(att[0], att[1], color=att_c, lw=0.9)
            a.set_ylim(0, top)
            a.set_yticks(np.linspace(0, top, 5))
            a.set_yticklabels([f"{t:.0f}" for t in np.linspace(0, top, 5)])
            b.set_ylim(0, 100)
            b.set_yticks([0, 25, 50, 75, 100])
            a.set_xlim(T_LO, T_HI)
            a.set_xticks(np.arange(T_LO, T_HI + 1e-9, 3))
            a.xaxis.set_minor_locator(AutoMinorLocator(3))
            for ax_ in (a, b):
                ax_.yaxis.set_minor_locator(AutoMinorLocator(2))
                ax_.tick_params(which="minor", length=1.2)
            a.grid(axis="both", **ps.GRID)
            a.set_axisbelow(True)
            axb[i].plot(thr[0], thr[1], color=THRU_C[ARMS[i]], lw=0.8)
            axb[i].set_ylim(0, top_thru * 1.05)
            axb[i].set_xlim(T_LO, T_HI)
            axb[i].set_xticks(np.arange(T_LO, T_HI + 1e-9, 3))
            axb[i].xaxis.set_minor_locator(AutoMinorLocator(3))
            axb[i].yaxis.set_major_formatter(ps.kfmt())
            axb[i].grid(axis="both", **ps.GRID)
            axb[i].set_axisbelow(True)
            axb[i].tick_params(which="minor", length=1.2)
            axb[i].set_xlabel(f"Time (min.)\n({'ab'[i]}) {label}", labelpad=1.5,
                              linespacing=1.5)
            axb[i].set_ylabel("Tokens/s", labelpad=1.5)
            print(f"    {label}: engine tokens/s p50 "
                  f"{np.percentile(thr[1], 50):,.0f}")
            # The units go on the outer edges only: the left panel keeps the
            # per-token time on its left and the right panel keeps the
            # attainment on its right, because the two panels share both scales
            # and repeating either in the gutter spends 0.3 in to say it twice.
            a.set_ylabel("TBT (ms)", labelpad=1.5)
            b.set_ylabel("Request SLO (%)", labelpad=2.0)
            a.set_xlabel("Time (min.)", labelpad=1.5)
            # A key per panel, because the colours are now the panel's own.
            hs = [Line2D([], [], color=itl_c, lw=0.9, label="TBT"),
                  Line2D([], [], color=att_c, lw=0.9, label="SLO attainment")]
            a.legend(hs, [h.get_label() for h in hs], loc="lower center",
                     bbox_to_anchor=(0.5, 1.01), ncol=2, frameon=False,
                     fontsize=5.4, columnspacing=0.7, handlelength=1.1,
                     handletextpad=0.3, borderaxespad=0.0)
            print(f"    {label}: TBT p50 {np.percentile(itl[ok], 50):.1f} ms, "
                  f"max {itl[ok].max():.0f} ms, SLO mean {att[1].mean():.1f}%, "
                  f"{int(over.sum())} s above the {top:.0f} ms axis")

        band = 0.16
        rect_top = 1 - (band - 0.01) / height
        fig.tight_layout(rect=(0, 0, 1, rect_top), pad=0.3, w_pad=1.0,
                         h_pad=0.8)
        for _ in range(4):
            fig.canvas.draw()
            gap = height - max(a.get_legend().get_window_extent().ymax
                               for a in ax if a.get_legend()) / fig.dpi
            if abs(gap) <= 0.01:
                break
            rect_top = min(0.999, rect_top + gap / height)
            fig.tight_layout(rect=(0, 0, 1, rect_top), pad=0.3, w_pad=1.0,
                             h_pad=0.8)
        for a_ in list(ax) + list(axb):
            for sp in ("top", "right"):
                a_.spines[sp].set_visible(True)
        w, h = fig.get_size_inches()
        bb = ax[0].get_position()
        print(f"    panel axes box {bb.width * w:.2f} x {bb.height * h:.2f} in, "
              f"top gap {gap:.3f} in")
        ps.save(fig, out)
        return bb.width * w, bb.height * h


def main():
    data = [series(k) for k in ARMS]
    pdf = os.path.join(HERE, "latency_two_arms.pdf")
    h = FIG_H
    for _ in range(5):
        box = build(data, pdf, ps.COL_W, h)
        if abs(box[0] - box[1]) < 0.015:
            break
        h += box[0] - box[1]
    write_csv(data, pdf[:-4] + ".csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
