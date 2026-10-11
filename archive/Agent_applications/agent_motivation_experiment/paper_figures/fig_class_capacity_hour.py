#!/usr/bin/env python3
"""Paper figure: how much of the fleet each class got, against the load that
arrived, over the hour-long trace.

  class_capacity_hour.pdf   7.0 x 2.75 in, `figure*`, width=\\textwidth
  class_capacity_hour.csv   exactly the values drawn

  top     what arrived: requests per second, stacked by class, over the same
          60 s windows. This is the same trace for every control plane, so it
          is drawn once and every panel below it is read against it.
  bottom  what each control plane gave each class, in INSTANCES: for class c
          and window t,

              a_c(t) = sum over instances i of  r_{c,i}(t) / r_i(t)

          where r_{c,i} is the mean number of requests of class c resident on
          instance i during the window and r_i is that instance's total. An
          instance whose requests are all of one class contributes 1 to that
          class; an instance holding a third of each contributes a third to
          each. The three bands therefore sum to THE NUMBER OF INSTANCES THAT
          HELD ANYTHING, and the gap between the top of the stack and 4 is
          fleet capacity that carried nothing in that window.

WHAT THIS FIGURE ADDS TO `class_mix_hour.pdf`. That one normalises every panel
to its own instance, so it shows how the classes were separated and says
nothing about how much work there was: an instance holding four requests and an
instance holding four hundred look the same. This one keeps the amount. Read
together: the first says whether the classes were kept apart, the second says
whether the capacity given to each class followed the load that arrived.

⚠ THE TOP PANEL IS OFFERED LOAD AND THE BOTTOM PANELS ARE ADMITTED WORK. Every
arrival appears above; only the requests a policy accepted can be resident on an
instance below. An arm that rejects a class shows a thin band for it below a
thick one above, and that is the rejection, not a placement decision. The
rejection rates over this run are FluidServe 19.9%, PolyServe 22.5%, Llumnix SLO
39.4%, llm-d 49.0%, vLLM router 0.0%, and they belong beside the figure.

⚠ THE COMPARISON IS BY REQUEST COUNT, NOT BY WORK. A deep research request
occupies an instance for far longer than a chat request, so a class that is 20%
of the arrivals is not entitled to 20% of the fleet. The top panel is therefore
the shape of the demand and not a target the bands below should match; what is
readable is whether a policy's allocation MOVES when the arriving mixture moves,
and by how much.

⚠ THE vLLM ROUTER PANEL IS DRAWN ON 74.9% OF ITS ADMITTED REQUESTS, because
engine attribution reads the scheduler's dispatch log and those lines are
dropped under load; the shortfall is concentrated after minute 40 (99.7% over
minutes 0-20, 43.6% after minute 40). The other four are at 99.9-100%.

DATA. EXP-109 (2026-08-31), repeat 1 of the five arms, through
`build_class_mix_tables.py`; the arrivals come from the same runs' `metrics.csv`
by way of `load_run`. Window 60 s, `load_run`'s analysis window (60 s of warmup
and 20 s of drain cut).

    python3 paper_figures/fig_class_capacity_hour.py
"""
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

sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))
_spec = importlib.util.spec_from_file_location(
    "eco", os.path.join(ROOT, "analysis_scripts", "request_level",
                        "engine_class_occupancy.py"))
ECO = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ECO)

MIX = os.path.join(ROOT, "results", "aggregate_analysis", "class_mix")
CSV_MIX = os.path.join(MIX, "hour_engine_mix.csv")
CSV_SUM = os.path.join(MIX, "hour_summary.csv")
REPEAT = "exp109r1"
# The run the arrival strip is taken from. Every arm replays the same trace, so
# any of them gives the same strip; naming one keeps the figure reproducible
# rather than depending on which arm happened to be read first.
ARRIVAL_RUN = "260831_2015_exp109r1_fsv3capgnofrct75_shift"

ARMS = [("vllmcachet75", "vLLM-router"), ("polyservept75", "PolyServe"),
        ("slot75", "Llumnix SLO"), ("llmdslot75", "llm-d"),
        ("fsv3capgnofrct75", "FluidServe")]
CLASS_COLOR = {"chat": "#fc8d59", "deepresearch": "#ffffbf", "swe": "#91bfdb"}
CLASS_LABEL = {"chat": "Chat", "deepresearch": "Deep Research", "swe": "Agent"}
CLASSES = ("chat", "deepresearch", "swe")
XMAX = 60.0
NINST = 4
FIG_H = 2.75
WINDOW = 60.0


def arrivals():
    """Offered requests per second per class, per window, from the trace."""
    run = os.path.join(ROOT, "results", ARRIVAL_RUN)
    r = ECO.load_run(run)
    t = pd.to_numeric(r["start_time"], errors="coerce")
    rel = t - t.min()
    r = r.assign(win=(rel // WINDOW) * WINDOW)
    piv = (r.pivot_table(index="win", columns="class", values="task_id",
                         aggfunc="size", fill_value=0) / WINDOW)
    for c in CLASSES:
        if c not in piv:
            piv[c] = 0.0
    grid = np.arange(0.0, piv.index.max() + WINDOW, WINDOW)
    return piv[list(CLASSES)].reindex(grid, fill_value=0.0)


def instance_equivalents():
    """label -> DataFrame(window x class) of instances devoted to each class."""
    d = pd.read_csv(CSV_MIX)
    d = d[d["run"].str.contains(REPEAT)]
    s = pd.read_csv(CSV_SUM)
    s = s[s["run"].str.contains(REPEAT)]
    out, cover = {}, {}
    for arm, label in ARMS:
        sub = d[d["arm"] == arm]
        if sub.empty:
            print(f"!! no rows for {label}", file=sys.stderr)
            continue
        cover[label] = float(s[s["arm"] == arm]["attributed_pct"].mean())
        piv = sub.pivot_table(index=["win_start_s", "engine_port"],
                              columns="class", values="resident",
                              aggfunc="sum", fill_value=0.0)
        for c in CLASSES:
            if c not in piv:
                piv[c] = 0.0
        tot = piv[list(CLASSES)].sum(axis=1)
        # An instance holding nothing contributes nothing rather than dividing
        # by zero: the stack then falls below the fleet size and that gap is
        # the capacity that carried no work in the window.
        frac = piv[list(CLASSES)].div(tot.where(tot > 0, np.nan), axis=0).fillna(0.0)
        agg = frac.groupby(level="win_start_s").sum()
        grid = np.arange(0.0, sub["win_start_s"].max() + WINDOW, WINDOW)
        out[label] = agg.reindex(grid, fill_value=0.0)
    return out, cover


DOM = 0.5      # a class owns an instance in a window when it holds this share
MIXED_COLOR = "#bdbdbd"


def dominant_counts():
    """label -> DataFrame(window x [classes, mixed]) counting INSTANCES.

    An instance belongs to the class holding at least `DOM` of its resident
    requests in that window, and to `mixed` when no class does. The counts are
    integers, so this asks how many instances each class OWNED rather than what
    fraction of each instance it occupied. An idle instance is in no column, so
    the stack falls below the fleet size exactly when capacity carried nothing.

    Why a second measure at all: the fractional one divides an instance by the
    residency on it, and residency counts time in the system, so a class whose
    requests are being delayed occupies more of the fleet by that measure than
    a class being served promptly. Counting owners removes that: an instance is
    one instance whether its requests are moving or waiting.
    """
    d = pd.read_csv(CSV_MIX)
    d = d[d["run"].str.contains(REPEAT)]
    out = {}
    for arm, label in ARMS:
        sub = d[d["arm"] == arm]
        if sub.empty:
            continue
        piv = sub.pivot_table(index=["win_start_s", "engine_port"],
                              columns="class", values="resident",
                              aggfunc="sum", fill_value=0.0)
        for c in CLASSES:
            if c not in piv:
                piv[c] = 0.0
        tot = piv[list(CLASSES)].sum(axis=1)
        share = piv[list(CLASSES)].div(tot.where(tot > 0, np.nan), axis=0)
        owner = share.idxmax(axis=1).where(share.max(axis=1) >= DOM, "mixed")
        owner = owner.where(tot > 0)                     # idle: no owner
        cnt = (owner.rename("owner").reset_index()
               .pivot_table(index="win_start_s", columns="owner",
                            values="engine_port", aggfunc="count", fill_value=0))
        for c in list(CLASSES) + ["mixed"]:
            if c not in cnt:
                cnt[c] = 0
        grid = np.arange(0.0, sub["win_start_s"].max() + WINDOW, WINDOW)
        out[label] = cnt[list(CLASSES) + ["mixed"]].reindex(grid, fill_value=0)
    return out


def write_csv(arr, alloc, cover, path, dom=None):
    rows = []
    for w, row in arr.iterrows():
        rec = {"arm": "(offered)", "minute": w / 60.0, "attributed_pct": np.nan,
               "unit": "arrivals per second"}
        for c in CLASSES:
            rec[f"{c}"] = float(row[c])
        rec["total"] = float(row[list(CLASSES)].sum())
        rows.append(rec)
    for label, df in alloc.items():
        for w, row in df.iterrows():
            rec = {"arm": label, "minute": w / 60.0,
                   "attributed_pct": round(cover[label], 2),
                   "unit": "instances devoted to the class"}
            for c in CLASSES:
                rec[f"{c}"] = float(row[c])
            rec["total"] = float(row[list(CLASSES)].sum())
            rows.append(rec)
    if dom is not None:
        for label, df_d in dom.items():
            for w, row in df_d.iterrows():
                rec = {"arm": label, "minute": w / 60.0,
                       "attributed_pct": round(cover[label], 2),
                       "unit": f"instances whose majority class is this (>= {DOM:.0%})"}
                for c in CLASSES:
                    rec[c] = float(row[c])
                rec["mixed"] = float(row["mixed"])
                rec["total"] = float(row[list(CLASSES) + ["mixed"]].sum())
                rows.append(rec)
    df = pd.DataFrame(rows).sort_values(["arm", "minute"])
    df.to_csv(path, index=False, float_format="%.4f")
    print(f"wrote {path}  ({len(df)} rows)")


def build(arr, alloc, cover, out, mixed=False):
    labels = [l for _, l in ARMS if l in alloc]
    with plt.rc_context(ps.STYLE):
        fig = plt.figure(figsize=(ps.TEXT_W, FIG_H))
        gs = fig.add_gridspec(2, len(labels), height_ratios=[1.0, 1.45],
                              hspace=0.62, wspace=0.30,
                              left=0.062, right=0.982, top=0.86, bottom=0.115)
        # The arrival strip spans the row because it is one trace, not five.
        top = fig.add_subplot(gs[0, :])
        x = arr.index.to_numpy() / 60.0
        top.stackplot(x, *[arr[c].to_numpy(dtype=float) for c in CLASSES],
                      colors=[CLASS_COLOR[c] for c in CLASSES], linewidth=0.0)
        top.set_xlim(0, XMAX)
        top.set_xticks([0, 15, 30, 45, 60])
        top.set_ylim(0, 1.05 * float(arr.sum(axis=1).max()))
        top.set_ylabel("Arrivals\n(req/s)", labelpad=1.5, linespacing=1.1)
        for side in ("top", "right"):
            top.spines[side].set_visible(False)
        top.set_title("Offered load", fontsize=8, pad=2)

        cols = list(CLASSES) + (["mixed"] if mixed else [])
        colours = [CLASS_COLOR[c] for c in CLASSES] + ([MIXED_COLOR] if mixed else [])
        for j, label in enumerate(labels):
            ax = fig.add_subplot(gs[1, j])
            df = alloc[label]
            xa = df.index.to_numpy() / 60.0
            ax.stackplot(xa, *[df[c].to_numpy(dtype=float) for c in cols],
                         colors=colours, linewidth=0.0)
            # The fleet size, so the gap under it is readable as unused
            # capacity rather than as the top of the axis.
            ax.axhline(NINST, color="#606060", lw=0.5, ls=(0, (2, 2)))
            ax.set_xlim(0, XMAX)
            ax.set_xticks([0, 15, 30, 45, 60])
            ax.set_ylim(0, NINST * 1.12)
            ax.set_yticks(range(NINST + 1))
            ax.set_title(label, fontsize=8, pad=2)
            ax.set_xlabel("minute", labelpad=1.5)
            if j == 0:
                ax.set_ylabel("Instances\nper class", labelpad=1.5,
                              linespacing=1.1)
            if mixed:
                ax.set_ylim(0, NINST * 1.12)
            for side in ("top", "right"):
                ax.spines[side].set_visible(False)

        keys = [(CLASS_COLOR[c], CLASS_LABEL[c]) for c in CLASSES]
        if mixed:
            keys.append((MIXED_COLOR, f"Mixed (no class >= {DOM:.0%})"))
        handles = [Patch(facecolor=col, label=lab, edgecolor="#666666",
                         linewidth=0.4) for col, lab in keys]
        fig.legend(handles, [lab for _, lab in keys], loc="lower center",
                   ncol=len(keys), bbox_to_anchor=(0.5, 1 - 0.155 / FIG_H),
                   frameon=False,
                   fontsize=7, columnspacing=1.2, handlelength=1.0,
                   handleheight=1.0, handletextpad=0.4, borderaxespad=0.0)
        ps.save(fig, out)


def report(arr, alloc, cover):
    seg = [(0, 15), (15, 30), (30, 45), (45, 60)]
    print("offered req/s by 15-minute segment (chat / deep research / agent):")
    for lo, hi in seg:
        m = arr[(arr.index >= lo * 60) & (arr.index < hi * 60)].mean()
        print(f"  {lo:2d}-{hi:2d} min  {m['chat']:5.1f} / "
              f"{m['deepresearch']:5.1f} / {m['swe']:5.1f}")
    print("\ninstances devoted to each class, mean over each segment")
    print(f"{'arm':13s} {'attr%':>6s} " +
          " ".join(f"{f'{lo}-{hi}m':>17s}" for lo, hi in seg))
    for _, label in ARMS:
        if label not in alloc:
            continue
        cells = []
        for lo, hi in seg:
            df = alloc[label]
            m = df[(df.index >= lo * 60) & (df.index < hi * 60)].mean()
            cells.append(f"{m['chat']:4.1f}/{m['deepresearch']:4.1f}/{m['swe']:4.1f}")
        print(f"{label:13s} {cover[label]:6.1f} " +
              " ".join(f"{c:>17s}" for c in cells))
    print("\ncells are chat / deep research / agent, in instances out of 4")


def main():
    arr = arrivals()
    alloc, cover = instance_equivalents()
    report(arr, alloc, cover)
    dom = dominant_counts()
    pdf = os.path.join(HERE, "class_capacity_hour.pdf")
    build(arr, alloc, cover, pdf)
    build(arr, dom, cover, os.path.join(HERE, "class_capacity_hour_owner.pdf"),
          mixed=True)
    write_csv(arr, alloc, cover, pdf[:-4] + ".csv", dom=dom)
    print("\ninstances owned by a class (>= "
          f"{DOM:.0%} of that instance's residency), mean over each segment")
    for _, label in ARMS:
        if label not in dom:
            continue
        cells = []
        for lo, hi in [(0, 15), (15, 30), (30, 45), (45, 60)]:
            df = dom[label]
            m = df[(df.index >= lo * 60) & (df.index < hi * 60)].mean()
            cells.append(f"{m['chat']:3.1f}/{m['deepresearch']:3.1f}/"
                         f"{m['swe']:3.1f}/{m['mixed']:3.1f}")
        print(f"{label:13s} " + " ".join(f"{c:>19s}" for c in cells))
    print("cells are chat / deep research / agent / mixed, in instances out of 4")
    return 0


if __name__ == "__main__":
    sys.exit(main())
