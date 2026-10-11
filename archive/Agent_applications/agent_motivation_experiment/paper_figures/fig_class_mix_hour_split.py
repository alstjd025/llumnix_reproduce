#!/usr/bin/env python3
"""Paper figure: the same instance-by-instance class mixture as
`class_mix_hour.pdf`, with each class split into the work that met its deadline
and the work that did not.

  class_mix_hour_split.pdf   7.0 x 3.10 in, `figure*`, width=\\textwidth
  class_mix_hour_split.csv   exactly the values drawn

  rows      the four instances, sorted once per column by the chat each held
            over the hour, so a row is one instance for the whole panel
  columns   the five control planes
  bands     one class's share of the requests resident on that instance in a
            60 s window. The solid part produced tokens that met their own
            deadline; the HATCHED part is the same class's work on the same
            instance that did not.

WHAT THE HATCHING ADDS. `class_mix_hour.pdf` shows how the classes were divided
across the fleet, and a division is not better for being different. Here the
same bands carry whether the work was worth anything: the hatched area is
instance time spent on requests that were served late. Reading a column now
answers both questions at once -- which classes shared an instance, and which of
them the instance failed.

HOW A BAND IS SPLIT. Each request contributes its residency in proportion to
its own token verdicts: a request whose tokens all arrived inside
TTFT_SLO + i x TBT_SLO counts entirely as solid, one that missed a third of its
deadlines puts a third of its residency in the hatched part, and one that
finished with no tokens at all is wholly hatched. The rule belongs to
`deadline_ladder_attainment.py` and is read from its per-request verdicts.

⚠ THE SPLIT IS A SHARE, NOT A TIMELINE. A request's late tokens are not
necessarily the ones it produced late in its life, so the hatched part of a
window is how much of that instance's occupancy belonged to work that missed
its deadline. It is not the minutes during which the misses happened.

⚠ REJECTION IS NOT IN THIS FIGURE AT ALL. A rejected request never reaches an
instance, so refusing work and serving it late look completely different here:
the second is hatched and the first is simply absent. Over this run the
rejection rates are FluidServe 19.9%, PolyServe 22.5%, Llumnix SLO 39.4%, llm-d
49.0%, vLLM router 0.0%, and a caption that omits them lets an arm that refuses
half its arrivals read as an arm that wastes nothing.

WHAT IT SHOWS. The share of instance time spent on late work is FluidServe
0.04%, llm-d 1.55%, PolyServe 24.4%, Llumnix SLO 37.8%, vLLM router 99.1%.
PolyServe's is almost entirely in one class: 37% of the chat residency on its
chat servers was late, against 0% of deep research and 0% of agent, while the
instance its partition reserves for the agent class stays nearly idle.

⚠ THE vLLM ROUTER COLUMN IS DRAWN ON 74.9% OF ITS ADMITTED REQUESTS, because
engine attribution reads the scheduler's dispatch log and those lines are
dropped under load; the shortfall is concentrated after minute 40. The other
four columns are at 99.9-100%.

DATA. EXP-109 (2026-08-31), repeat 1 of the five arms, through
`build_class_mix_tables.py --split`. Window 60 s, `load_run`'s analysis window.

    python3 paper_figures/fig_class_mix_hour_split.py
"""
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

MIX = os.path.join(ROOT, "results", "aggregate_analysis", "class_mix")
CSV_MIX = os.path.join(MIX, "hour_engine_mix_split.csv")
CSV_SUM = os.path.join(MIX, "hour_split_summary.csv")

ARMS = [("vllmcachet75", "vLLM-router"), ("polyservept75", "PolyServe"),
        ("slot75", "Llumnix SLO"), ("llmdslot75", "llm-d"),
        ("fsv3capgnofrct75", "FluidServe")]
CLASS_COLOR = {"chat": "#fc8d59", "deepresearch": "#ffffbf", "swe": "#91bfdb"}
CLASS_LABEL = {"chat": "Chat", "deepresearch": "Deep Research", "swe": "Agent"}
CLASSES = ("chat", "deepresearch", "swe")
RANKS = [1, 2, 3, 4]
XMAX = 60.0
FIG_H = 3.10
# The hatch is drawn in the collection's edge colour. Dark grey rather than a
# darker shade of each class, so that "late" is one thing across three fills of
# very different lightness.
HATCH = "////"
HATCH_EDGE = "#4d4d4d"


def collect():
    d = pd.read_csv(CSV_MIX)
    s = pd.read_csv(CSV_SUM)
    out, cover, late = {}, {}, {}
    for arm, label in ARMS:
        sub = d[d["arm"] == arm]
        if sub.empty:
            print(f"!! no rows for {label}", file=sys.stderr)
            continue
        row = s[s["arm"] == arm]
        cover[label] = float(row["attributed_pct"].mean())
        late[label] = float(row["late_share_pct"].mean())
        grid = np.arange(0.0, sub["win_start_s"].max() + 60.0, 60.0)
        for rank in RANKS:
            r = sub[sub["engine_rank"] == rank]
            piv = r.pivot_table(index="win_start_s", columns="class",
                                values=["resident_ontime", "resident_late"],
                                aggfunc="sum", fill_value=0.0)
            cols = {}
            for c in CLASSES:
                for part in ("resident_ontime", "resident_late"):
                    key = (part, c)
                    cols[f"{c}_{part.split('_')[1]}"] = (
                        piv[key] if key in piv else pd.Series(0.0, index=piv.index))
            out[(label, rank)] = pd.DataFrame(cols).reindex(grid, fill_value=0.0)
    return out, cover, late


def write_csv(data, cover, late, path):
    rows = []
    for (label, rank), piv in data.items():
        tot = piv.sum(axis=1)
        for w, row in piv.iterrows():
            t = float(tot.loc[w])
            rec = {"arm": label, "engine_rank": rank, "minute": w / 60.0,
                   "attributed_pct": round(cover[label], 2),
                   "late_share_run_pct": round(late[label], 2)}
            for c in CLASSES:
                for part in ("ontime", "late"):
                    v = float(row[f"{c}_{part}"])
                    rec[f"resident_{c}_{part}"] = v
                    rec[f"share_{c}_{part}_pct"] = (100.0 * v / t) if t > 0 else np.nan
            rec["resident_total"] = t
            rec["unit"] = "mean concurrently resident requests in a 60 s window"
            rows.append(rec)
    df = pd.DataFrame(rows).sort_values(["arm", "engine_rank", "minute"])
    df.to_csv(path, index=False, float_format="%.4f")
    print(f"wrote {path}  ({len(df)} rows)")


def build(data, cover, late, out):
    labels = [l for _, l in ARMS if any((l, r) in data for r in RANKS)]
    order = [f"{c}_{p}" for c in CLASSES for p in ("ontime", "late")]
    with plt.rc_context({**ps.STYLE, "hatch.linewidth": 0.35}):
        fig, axes = plt.subplots(len(RANKS), len(labels),
                                 figsize=(ps.TEXT_W, FIG_H), sharex=True)
        for i, rank in enumerate(RANKS):
            for j, label in enumerate(labels):
                ax = axes[i][j]
                for side in ("top", "right"):
                    ax.spines[side].set_visible(False)
                ax.set_xlim(0, XMAX)
                ax.set_xticks([0, 15, 30, 45, 60])
                ax.set_ylim(0, 100)
                ax.set_yticks([0, 50, 100])
                if (label, rank) not in data:
                    continue
                piv = data[(label, rank)]
                x = piv.index.to_numpy() / 60.0
                vals = [piv[k].to_numpy(dtype=float) for k in order]
                tot = np.sum(vals, axis=0)
                vals = [np.where(tot > 0, 100.0 * v / np.where(tot > 0, tot, 1.0),
                                 0.0) for v in vals]
                polys = ax.stackplot(
                    x, *vals,
                    colors=[CLASS_COLOR[k.split("_")[0]] for k in order],
                    linewidth=0.0)
                for k, poly in zip(order, polys):
                    if k.endswith("_late"):
                        poly.set_hatch(HATCH)
                        poly.set_edgecolor(HATCH_EDGE)
                if i == 0:
                    ax.set_title(label, fontsize=8, pad=3)
                if j == 0:
                    ax.set_ylabel(f"Instance {rank}", labelpad=1.5)
                if i == len(RANKS) - 1:
                    ax.set_xlabel("minute", labelpad=1.5)

        keys = [(CLASS_COLOR[c], CLASS_LABEL[c], None) for c in CLASSES]
        keys.append(("#ffffff", "Missed its deadline", HATCH))
        handles = [Patch(facecolor=col, label=lab, hatch=h,
                         edgecolor=HATCH_EDGE if h else "#666666", linewidth=0.4)
                   for col, lab, h in keys]
        fig.legend(handles, [lab for _, lab, _ in keys], loc="lower center",
                   ncol=len(keys), bbox_to_anchor=(0.5, 1 - 0.175 / FIG_H),
                   frameon=False, fontsize=7, columnspacing=1.2,
                   handlelength=1.0, handleheight=1.0, handletextpad=0.4,
                   borderaxespad=0.0)
        fig.supylabel("Class share rate per instances (%)", fontsize=8, x=0.006)
        fig.tight_layout(rect=(0.022, 0, 1, 1 - 0.155 / FIG_H),
                         w_pad=0.7, h_pad=0.5, pad=0.3)
        ps.save(fig, out)


def report(data, cover, late):
    print(f"{'arm':13s} {'attr%':>6s} {'late% (run)':>11s} {'rank':>4s} "
          f"{'chat late%':>10s} {'deep late%':>10s} {'agent late%':>11s}")
    for _, label in ARMS:
        for rank in RANKS:
            if (label, rank) not in data:
                continue
            piv = data[(label, rank)]
            cells = []
            for c in CLASSES:
                t = piv[f"{c}_ontime"].sum() + piv[f"{c}_late"].sum()
                cells.append(100.0 * piv[f"{c}_late"].sum() / t if t > 0 else np.nan)
            print(f"{label:13s} {cover[label]:6.1f} {late[label]:11.2f} "
                  f"{rank:4d} {cells[0]:10.1f} {cells[1]:10.1f} {cells[2]:11.1f}")


def main():
    data, cover, late = collect()
    report(data, cover, late)
    pdf = os.path.join(HERE, "class_mix_hour_split.pdf")
    build(data, cover, late, pdf)
    write_csv(data, cover, late, pdf[:-4] + ".csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
