#!/usr/bin/env python3
"""Paper figure: what each instance held, and how fast it ran, on the EIGHT
instance fleet at the halved budgets.

  class_mix_hour_abs_exp126_tbt.pdf   7.0 x 5.0 in, `figure*`, width=\\textwidth
  class_mix_hour_abs_exp126_tbt.csv   the values drawn

This is `class_mix_hour_abs_tbt.pdf` rebuilt on EXP-126: same two quantities per
panel -- the classes an instance held, and the per-token time it delivered --
with two differences that follow from the experiment.

  EIGHT ROWS, NOT FOUR. The fleet is 8 x Llama-3.1-8B at TP=1, so a column is
  eight panels and the figure is a page-width block rather than a strip.
  HALVED BUDGETS. chat 25 ms, agent 38 ms, deep research 50 ms per token, and
  2.5 / 3.5 / 5 s to the first token. Nothing here may sit beside a
  standard-budget number.

⚠ THE ROW IS AN ENGINE, FIXED FOR THE WHOLE RUN, ranked by how much chat it
held over the hour -- so row 1 is one engine throughout and a reassignment shows
as that row changing colour partway through, not as rows swapping.

⚠ THE ROWS OF ONE COLUMN ARE NOT THE ROWS OF ANOTHER. Each arm is ranked within
itself, so "instance 3" in two columns is two different engines that happen to
hold the third-most chat under their own policy.

⚠ THE LEFT AXIS IS SHARED BY EVERY PANEL so the columns can be compared; the
right one is too. A quiet instance is therefore drawn as a low band rather than
rescaled to fill its panel.

⚠ THE ATTRIBUTION IS NOT COMPLETE FOR EVERY ARM. A request reaches this figure
only if it can be tied to the engine that served it -- through the scheduler's
dispatch log for the Llumnix-scheduled arms, through Envoy's access log for
llm-d -- and the share that could be tied is printed per arm and belongs in the
caption. CLAUDE.md records that the loss concentrates at high load, which is
where the panels are read.

DATA. EXP-126 (2026-09-10), repeat 1 of each arm, the one-hour mixture-shift
trace at x6.20 (67-279 req/s). Residency comes from
`results/aggregate_analysis/class_mix/hour_engine_mix_exp126.csv`, written by a
wrapper that keeps the EXP-109 table untouched; the per-token time is each
engine's own histogram counters, differenced per second and smoothed.

    python3 paper_figures/fig_class_mix_hour_exp126.py
"""
import argparse
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402

RL = os.path.join(ROOT, "analysis_scripts", "request_level")


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DKG = _load("dkg", os.path.join(RL, "decode_kv_growth.py"))
EV = _load("exp41ev", os.path.join(RL, "exp41_engine_view.py"))

MIX = os.path.join(ROOT, "results", "aggregate_analysis", "class_mix",
                   "hour_engine_mix_exp126.csv")
SUMM = os.path.join(ROOT, "results", "aggregate_analysis", "class_mix",
                    "hour_summary_exp126.csv")
# Column order and labels, the same as `exp126_two_models_hour_reqgoodput.pdf`.
# ⚠ THREE OF THE FIVE ARMS ARE NOT DRAWN, AND NOT BECAUSE OF ANYTHING THEY DID.
# Residency can only be reconstructed from the scheduler's dispatch log, and the
# capture of that log on this fleet lost lines under load -- the failure
# CLAUDE.md group D records for this exact log. Measured on 2026-09-11, as the
# share of ADMITTED requests that could be tied to the engine that served them
# (`engine_class_occupancy.occupancy`, repeat 1 of each arm):
#
#     FluidServe   100.0%      llm-d 97.7%
#     PolyServe     54.8%  (54.5% in repeat 2, so it is not one bad run)
#     vLLM router   31.8%
#     Llumnix       18.6%, and only 38 of the hour's 61 windows have any line
#
# THE LOSS IS NOT UNIFORM IN TIME, which is what rules the three out rather than
# merely scaling them down. Counting every line of the captured log per five
# minutes, Llumnix goes 31.9k, 39.5k, 36.3k, 31.1k and then 1.4k, 174, 142, 167
# -- the capture stops around minute 20 and the rest of the hour is empty. The
# vLLM router keeps 51-100% through minute 20 and 3-27% after it. It is the
# WHOLE FILE that thins, not the dispatch lines within it, so this is the
# collector losing output and not a property of either policy.
#
# The trace shifts its mixture at minutes 16, 31 and 46, so an arm whose log
# ends at minute 20 cannot show a single shift, which is what this figure is
# for. Their per-token lines would be fine -- those come from the engines' own
# counters -- but a column with a correct line over bands that are wrong in
# shape is worse than no column.
#
# `hour_engine_mix_exp126.csv` CARRIES ALL FIVE ARMS so the coverage can be
# re-measured without rebuilding; `hour_summary_exp126.csv` holds the
# `attributed_pct` above. Drawing one of the three is one line here, and the
# figure would then be stating something the data does not support.
ARMS = [("llmdslot", "llm-d"), ("fsv3capgnofrc", "FluidServe")]
CLASSES = ("chat", "deepresearch", "swe")
CLASS_LABEL = {"chat": "Chat", "deepresearch": "Deep Research", "swe": "Agent"}
# ColorBrewer PuBuGn, as in the other class-mix figures of this directory.
CLASS_COLOR = {"chat": "#ece2f0", "deepresearch": "#a6bddb", "swe": "#1c9099"}
TBT_COLOR = "#d62728"
TBT_STYLE = (0, (3, 1.5))
TBT_SMOOTH_S = 120
# The halved budgets, for the caption and for the default axis top.
BUDGETS = {"chat": 25.0, "swe": 38.0, "deepresearch": 50.0}
XMAX = 60.0
FIG_W, FIG_H = ps.TEXT_W, 5.0
LABEL_SIZE = 5.5


def tbt_series(run, port, t0, smooth_s=TBT_SMOOTH_S):
    """minutes, mean inter-token latency in ms, for one engine of one run."""
    d = DKG.itl_per_engine(os.path.join(ROOT, "results", run))
    key = str(port)
    if key not in d:
        return None
    grid, ds, dc = d[key]
    k = max(1, int(smooth_s))
    sd, cd = DKG.smooth(ds, k), DKG.smooth(dc, k)
    v = np.divide(sd, cd, out=np.full_like(sd, np.nan), where=cd > 0) * 1000.0
    return (grid - t0) / 60.0, v


def collect():
    d = pd.read_csv(MIX)
    s = pd.read_csv(SUMM) if os.path.exists(SUMM) else None
    out, cover, ranks = {}, {}, set()
    for key, label in ARMS:
        sub = d[d["arm"] == key]
        if sub.empty:
            print(f"!! {label}: no rows in {os.path.basename(MIX)}",
                  file=sys.stderr)
            continue
        run = sub["run"].iloc[0]
        if s is not None and (s["arm"] == key).any():
            cover[label] = float(s[s["arm"] == key]["attributed_pct"].iloc[0])
        t0 = float(pd.read_csv(os.path.join(ROOT, "results", run),
                               usecols=["start_time"])["start_time"].min()) \
            if False else float(pd.read_csv(
                os.path.join(ROOT, "results", run, "metrics.csv"),
                usecols=["start_time"])["start_time"].min())
        per = {}
        for rank, g in sub.groupby("engine_rank"):
            ranks.add(int(rank))
            piv = g.pivot_table(index="win_start_s", columns="class",
                                values="resident", aggfunc="sum",
                                fill_value=0.0)
            for c in CLASSES:
                if c not in piv:
                    piv[c] = 0.0
            grid = np.arange(0.0, sub["win_start_s"].max() + 60.0, 60.0)
            piv = piv.reindex(grid, fill_value=0.0)
            port = int(g["engine_port"].iloc[0])
            per[int(rank)] = dict(piv=piv[list(CLASSES)], port=port,
                                  tbt=tbt_series(run, port, t0))
        out[label] = dict(run=run, per=per)
    return out, cover, sorted(ranks)


def build(data, cover, ranks, out, req_max=None, tbt_max=100.0):
    labels = [l for _, l in ARMS if l in data]
    style = {**ps.STYLE, "xtick.labelsize": LABEL_SIZE,
             "ytick.labelsize": LABEL_SIZE, "axes.labelsize": LABEL_SIZE}
    rows = []
    if req_max is None:
        req_max = max(float(d["per"][r]["piv"].sum(axis=1).max())
                      for d in data.values() for r in d["per"])
        req_max = float(np.ceil(req_max / 50.0) * 50)
    with plt.rc_context(style):
        fig, axes = plt.subplots(len(ranks), len(labels),
                                 figsize=(FIG_W, FIG_H), sharex=True)
        for i, rank in enumerate(ranks):
            for j, label in enumerate(labels):
                ax = axes[i][j]
                ax.set_xlim(0, XMAX)
                ax.set_xticks([0, 15, 30, 45, 60])
                for side in ("top", "right"):
                    ax.spines[side].set_visible(False)
                d = data[label]["per"].get(rank)
                if d is None:
                    continue
                piv = d["piv"]
                x = piv.index.to_numpy() / 60.0
                vals = [piv[c].to_numpy(dtype=float) for c in CLASSES]
                ax.stackplot(x, *vals,
                             colors=[CLASS_COLOR[c] for c in CLASSES],
                             linewidth=0.0)
                ax.set_ylim(0, req_max)
                ax.set_yticks([0, req_max / 2, req_max])
                ax.set_yticklabels([f"{v:,.0f}" for v in
                                    (0, req_max / 2, req_max)])
                ax.grid(axis="both", **ps.GRID)
                ax.set_axisbelow(True)
                if d["tbt"] is not None:
                    m, v = d["tbt"]
                    ok = np.isfinite(v) & (m >= 0) & (m <= XMAX)
                    a2 = ax.twinx()
                    a2.plot(m[ok], v[ok], color=TBT_COLOR, lw=0.7,
                            ls=TBT_STYLE, zorder=5)
                    a2.set_ylim(0, tbt_max)
                    a2.set_yticks(list(np.arange(0, tbt_max + 1, tbt_max / 2)))
                    a2.spines["top"].set_visible(False)
                    a2.tick_params(length=2.0, pad=1.0)
                    if j != len(labels) - 1:
                        a2.set_yticklabels([])
                    for mm, vv in zip(m[ok], v[ok]):
                        rows.append(dict(arm=label, engine_rank=rank,
                                         engine_port=d["port"],
                                         series="tbt_ms", minute=round(mm, 4),
                                         value=round(float(vv), 3)))
                tot = piv.sum(axis=1)
                for w, r in piv.iterrows():
                    rows.append(dict(arm=label, engine_rank=rank,
                                     engine_port=d["port"], series="resident",
                                     minute=w / 60.0,
                                     value=float(tot.loc[w]),
                                     **{f"resident_{c}": float(r[c])
                                        for c in CLASSES}))
                if i == 0:
                    ax.set_title(label, fontsize=7, pad=3)
                if j == 0:
                    ax.set_ylabel(f"Inst. {rank}", labelpad=1.5)
                if i == len(ranks) - 1:
                    ax.set_xlabel("Time (min.)", labelpad=1.5)
        keys = [Patch(facecolor=CLASS_COLOR[c], label=CLASS_LABEL[c],
                      edgecolor="#000000", linewidth=0.5) for c in CLASSES]
        keys.append(plt.Line2D([], [], color=TBT_COLOR, lw=0.9, ls=TBT_STYLE,
                               label="TBT"))
        band = 0.24
        rect_top = 1 - band / FIG_H
        fig.legend(keys, [k.get_label() for k in keys], loc="lower center",
                   bbox_to_anchor=(0.5, rect_top + 0.002), ncol=len(keys),
                   frameon=False, fontsize=6.4, columnspacing=1.0,
                   handlelength=1.25, handleheight=1.25, handletextpad=0.3,
                   borderaxespad=0.0)
        fig.supylabel("Number of Requests per Instance", fontsize=7, x=0.006)
        fig.text(0.998, 0.5, "Mean time between tokens (ms)", fontsize=7,
                 rotation=270, ha="right", va="center")
        fig.tight_layout(rect=(0.020, 0, 0.972, rect_top), w_pad=0.9,
                         h_pad=0.35, pad=0.3)
        ps.save(fig, out)
    if cover:
        print("  attributed share per arm: " + ", ".join(
            f"{k} {v:.1f}%" for k, v in cover.items()))
    print(f"  requests axis 0..{req_max:,.0f}, latency axis 0..{tbt_max:.0f} ms")
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--req-max", type=float, default=None)
    ap.add_argument("--tbt-max", type=float, default=100.0)
    a = ap.parse_args()
    data, cover, ranks = collect()
    if not data:
        sys.exit(f"no arms in {MIX}")
    print(f"{len(data)} arms, {len(ranks)} instances")
    pdf = os.path.join(HERE, "class_mix_hour_abs_exp126_tbt.pdf")
    df = build(data, cover, ranks, pdf, req_max=a.req_max, tbt_max=a.tbt_max)
    df["budgets"] = "chat 25 ms, swe 38 ms, deepresearch 50 ms per token"
    out = pdf[:-4] + ".csv"
    df.to_csv(out, index=False)
    print(f"wrote {out}  ({len(df):,} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
