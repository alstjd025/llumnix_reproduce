#!/usr/bin/env python3
"""Paper figure: what each control plane delivered per class, and what became of
the arrivals, on the hour trace.

  class_latency_outcome_<model>.pdf   7.0 x 1.95 in, `figure*`, width=\\textwidth
  class_latency_outcome_<model>.csv   exactly the values drawn

  (a) mean time between tokens, per class, with each class's own nominal budget
      drawn over its bar
  (b) mean time to first token, per class, same treatment, on a LOG axis
  (c) every arrival, split into met / missed / rejected / unfinished

⚠ (a) AND (b) ARE MEASURED ON THE REQUESTS THAT FINISHED, so their denominator
is different for every arm and (c) is what says so. An arm that refuses 42% of
its arrivals is timed on the 58% it kept, and it should look fast; the panel
pair is only readable together. The completed count per bar is in the CSV and
printed.

⚠ THE BUDGET MARK IS THE NOMINAL PROMISE, NOT THE SCORING RULE. The figures in
this directory score a request with the cumulative ladder -- token i is on time
if it arrives by TTFT + i x TBT, and the request passes if 95% of its tokens do
-- which lets a request bank early tokens against late ones. A bar above its
mark is therefore not the same as a failed request, and a bar below it does not
mean every request passed. The mark says what the class was promised.

⚠ (b) IS LOGARITHMIC because the arm without admission control sits three orders
of magnitude above the rest (up to 629 s against 0.4-14 s), and a linear axis
holding both draws every other bar as a line on the floor.

⚠ FOUR BANDS IN (c) AND NOT THREE. met + missed + rejected does not sum to the
arrivals, and the gap is largest exactly where it matters: the arm without
admission control leaves about half of its arrivals still running when the span
closes, and those have no outcome at all. Dropping that band would rescale its
bar and flatter it.

SCORING for (c) is the ladder rule at the standard budgets; the latencies in (a)
and (b) are the client's own record, with the per-token time derived as
(end-to-end - time to first token) / (output tokens - 1) rather than read from
the `tbt_mean_ms` column, which is half the true value on runs collected before
2026-07-30.

DATA. The runs `two_models_hour_reqgoodput.pdf` draws, repeat 2, cut at the same
minute: EXP-109 (Llama-3.1-70B) or EXP-113 (Qwen2.5-72B), chosen with --model.

    python3 paper_figures/fig_class_latency_and_outcome.py [--model qwen]
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
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import TEXT_W, STYLE, GRID, kfmt, save  # noqa: E402

os.environ.setdefault("FS_SWE_TBT_MS", "75")
os.environ.setdefault("FS_SWE_TTFT_S", "7")
from exp22_fluidserve import load_run, class_of  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TM = _load("tm", os.path.join(HERE, "fig_two_models_hour.py"))

ORDER = ["vLLM", "PolyServe", "Llumnix SLO", "llm-d", "FluidServe"]
# Chat, Agent, Deep Research -- the order the author reads them in, which is
# also the order of their per-token budgets (50, 75, 100 ms), so the budget
# marks in (a) step up from left to right inside every group.
CLASSES = [("chat", "Chat"), ("swe", "Agent"), ("deepresearch", "Deep Research")]
# (first token ms, per token ms) -- the promise each class carries in these runs.
BUDGETS = {"chat": (5000.0, 50.0), "deepresearch": (10000.0, 100.0),
           "swe": (7000.0, 75.0)}
# The class palette of the class-mix figures, so a colour means the same class
# across the directory.
C_CLASS = {"chat": "#ece2f0", "deepresearch": "#a6bddb", "swe": "#1c9099"}
# The outcome palette of the stacked hour figures, for panel (c).
SEG = [("met", "SLO Attained", "#80cdc1", None),
       ("missed", "SLO Missed", "#f4a582", None),
       ("rejected", "Rejected", "#cfcfcf", None),
       ("unfinished", "Unfinished", "#ffffff", "////")]
MODELS = {"llama": "Llama-3.1-70B", "qwen": "Qwen2.5-72B"}
# The two latency panels are cut and the bars that cross the cut carry their
# value in type above the axis. ⚠ WHAT THIS COSTS: a clipped bar no longer shows
# how far past the budget it went, and the reader has to read the number. It is
# the lesser cost -- one arm sits three orders of magnitude above the rest, and
# an axis that reaches it draws every other bar as a line on the floor.
TBT_TOP = 100.0
TTFT_TOP = 1.0e4
FIG_H = 1.95
LABEL_SIZE = 6


def latencies(run_dir):
    """(class -> mean TTFT ms, mean per-token ms, n) over the finished requests."""
    r = load_run(run_dir)
    if r is None or r.empty:
        return {}
    r = r.copy()
    r["class"] = r["task_id"].map(class_of)
    done = r[~r["rejected"] & ~r["cutoff"] & ~r["errored"]]
    out = {}
    for c, _lab in CLASSES:
        g = done[(done["class"] == c) & (done["output_tokens"] > 1)]
        if g.empty:
            continue
        ttft = pd.to_numeric(g["first_token_latency"], errors="coerce") * 1000.0
        itl = pd.to_numeric(g["itl_ms"], errors="coerce")
        out[c] = dict(ttft_mean_ms=float(ttft.mean()),
                      tbt_mean_ms=float(itl.mean()), n=len(g))
    return out


def outcomes(v, minute):
    """The four counts, as requests per second over the drawn span."""
    w = v[v["rel"] <= minute * 60.0]
    span = minute * 60.0
    met = int((w["ladder_ok"] & ~w["rejected"] & ~w["errored"]
               & ~w["cutoff"]).sum())
    rej = int(w["rejected"].sum())
    cut = int(w["cutoff"].sum())
    return dict(met=met / span, missed=(len(w) - met - rej - cut) / span,
                rejected=rej / span, unfinished=cut / span, arrivals=len(w),
                met_n=met, missed_n=len(w) - met - rej - cut, rejected_n=rej,
                unfinished_n=cut)


def collect(model):
    arms = TM.RUNS[model]
    data = TM.collect()
    dur = TM.cut_minute(data[model])
    per = {}
    for arm in ORDER:
        run = arms[arm][0]
        v = TM.verdicts(run)
        if v is None:
            print(f"!! {arm}: no verdicts", file=sys.stderr)
            continue
        per[arm] = dict(run=run, lat=latencies(os.path.join(ROOT, "results", run)),
                        out=outcomes(v, dur))
    return per, dur


def _fmt(v):
    if v >= 1e6:
        return f"{v / 1e6:.2f}M"
    if v >= 1e3:
        return f"{v / 1e3:.0f}k"
    return f"{v:.0f}"


def bars(ax, per, key, log=False, cap=None):
    """Three bars per arm, one per class, with each class's budget over its bar.

    A bar taller than `cap` is drawn to the cap by the axes and its value is
    written above it, rotated because a bar here is about 0.1 in wide.
    """
    n = len(CLASSES)
    w = 0.8 / n
    over = []
    for i, (c, _lab) in enumerate(CLASSES):
        xs, vs = [], []
        for j, arm in enumerate(ORDER):
            if arm not in per or c not in per[arm]["lat"]:
                continue
            xs.append(j - 0.4 + w * (i + 0.5))
            vs.append(per[arm]["lat"][c][key])
        ax.bar(xs, vs, width=w * 0.92, color=C_CLASS[c], edgecolor="#000000",
               linewidth=0.4, zorder=3)
        # The promise, drawn over that class's bars only: three full-width rules
        # would have to be told apart by colour, and the question the mark
        # answers is about one bar at a time.
        b = BUDGETS[c][0 if key == "ttft_mean_ms" else 1]
        for x in xs:
            ax.plot([x - w * 0.5, x + w * 0.5], [b, b], color="#000000",
                    lw=0.9, solid_capstyle="butt", zorder=4)
        if cap:
            for x, v in zip(xs, vs):
                if v > cap:
                    ax.text(x, cap * 1.02 if log else cap + 1.5, _fmt(v),
                            ha="center", va="bottom", fontsize=4.6,
                            rotation=90, zorder=5, clip_on=False)
                    over.append((c, v))
    if log:
        ax.set_yscale("log")
    ax.set_xticks(range(len(ORDER)))
    ax.set_xticklabels(ORDER, fontsize=5.2, rotation=40, ha="right",
                       rotation_mode="anchor")
    ax.set_xlim(-0.7, len(ORDER) - 0.3)
    return over


def build(per, dur, model, out):
    style = {**STYLE, "xtick.labelsize": LABEL_SIZE,
             "ytick.labelsize": LABEL_SIZE, "axes.labelsize": LABEL_SIZE}
    rows = []
    with plt.rc_context(style):
        fig, ax = plt.subplots(
            1, 4, figsize=(TEXT_W, FIG_H),
            gridspec_kw=dict(width_ratios=[1.15, 1.15, 0.85, 0.85]))
        o1 = bars(ax[0], per, "tbt_mean_ms", cap=TBT_TOP)
        ax[0].set_ylabel("Mean TBT (ms)", labelpad=1.5)
        ax[0].set_ylim(0, TBT_TOP)
        ax[0].set_yticks([0, 25, 50, 75, 100])
        ax[0].set_xlabel("(a) Time between Tokens", labelpad=1.5)

        o2 = bars(ax[1], per, "ttft_mean_ms", log=True, cap=TTFT_TOP)
        ax[1].set_ylabel("Mean TTFT (ms)", labelpad=1.5)
        ax[1].set_ylim(100, TTFT_TOP)
        ax[1].set_yticks([1e2, 1e3, 1e4])
        ax[1].set_yticklabels(["100", "1k", "10k"])
        ax[1].minorticks_off()
        ax[1].set_xlabel("(b) Time to First Token", labelpad=1.5)

        xs = list(range(len(ORDER)))
        bottom = np.zeros(len(xs))
        for key, _lab, colour, hatch in SEG:
            v = np.array([per[a]["out"][key] if a in per else 0.0
                          for a in ORDER])
            ax[2].bar(xs, v, bottom=bottom, width=0.72, color=colour,
                      edgecolor="#000000", linewidth=0.5, hatch=hatch)
            bottom = bottom + v
        ax[2].set_xticks(xs)
        ax[2].set_xticklabels(ORDER, fontsize=5.2, rotation=40, ha="right",
                              rotation_mode="anchor")
        ax[2].set_xlim(-0.7, len(ORDER) - 0.3)
        ax[2].set_ylim(0, bottom.max() * 1.08)
        ax[2].set_ylabel("Arrivals (req/s)", labelpad=1.5)
        ax[2].set_xlabel("(c) Outcome of Every Arrival", labelpad=1.5)

        # (d) THE SAME OUTCOMES IN REQUESTS, AND ONLY THE DECIDED ONES.
        # (c) is a rate over every arrival; this is a count over the arrivals
        # whose outcome is known, so the unfinished band is absent and the bar
        # height is no longer the same for every arm -- it is how many requests
        # that control plane actually settled in the hour.
        bottom = np.zeros(len(xs))
        for key, _lab, colour, hatch in SEG[:3]:
            v = np.array([per[a]["out"][f"{key}_n"] if a in per else 0.0
                          for a in ORDER])
            ax[3].bar(xs, v, bottom=bottom, width=0.72, color=colour,
                      edgecolor="#000000", linewidth=0.5, hatch=hatch)
            bottom = bottom + v
        ax[3].set_xticks(xs)
        ax[3].set_xticklabels(ORDER, fontsize=5.2, rotation=40, ha="right",
                              rotation_mode="anchor")
        ax[3].set_xlim(-0.7, len(ORDER) - 0.3)
        ax[3].set_ylim(0, bottom.max() * 1.08)
        ax[3].yaxis.set_major_formatter(kfmt())
        ax[3].set_ylabel("Requests", labelpad=1.5)
        ax[3].set_xlabel("(d) Decided Requests", labelpad=1.5)

        for a in ax:
            a.grid(axis="y", **GRID)
            a.set_axisbelow(True)
            for side in ("top", "right"):
                a.spines[side].set_visible(False)

        for arm in ORDER:
            if arm not in per:
                continue
            d = per[arm]
            for c, _lab in CLASSES:
                if c in d["lat"]:
                    rows.append(dict(model=model, arm=arm, cls=c, panel="a,b",
                                     cut_minute=dur, **d["lat"][c],
                                     ttft_budget_ms=BUDGETS[c][0],
                                     tbt_budget_ms=BUDGETS[c][1], run=d["run"]))
            rows.append(dict(model=model, arm=arm, cls="all", panel="c",
                             cut_minute=dur, run=d["run"],
                             **{f"{k}_r_s": v for k, v in d["out"].items()}))
            tot = sum(d["lat"][c]["n"] for c, _ in CLASSES if c in d["lat"])
            rows.append(dict(model=model, arm=arm, cls="all", panel="total",
                             cut_minute=dur, run=d["run"], n=tot,
                             completed_per_s=tot / (dur * 60.0)))
            print(f"    {arm:12s} completed {tot:7,d} "
                  f"({tot / (dur * 60.0):5.2f} /s)  TBT " + " ".join(
                f"{d['lat'][c]['tbt_mean_ms']:5.1f}" for c, _ in CLASSES
                if c in d["lat"]) + "  TTFT " + " ".join(
                f"{d['lat'][c]['ttft_mean_ms']:9,.0f}" for c, _ in CLASSES
                if c in d["lat"]) + f"  n " + " ".join(
                f"{d['lat'][c]['n']:6,d}" for c, _ in CLASSES if c in d["lat"]))

        print(f"    cut at {TBT_TOP:.0f} ms / {TTFT_TOP:,.0f} ms: "
              f"{len(o1)} TBT and {len(o2)} TTFT bars are written above the "
              f"axis instead of drawn")
        keys = [Patch(facecolor=C_CLASS[c], edgecolor="#000000", linewidth=0.4,
                      label=lab) for c, lab in CLASSES]
        keys.append(Line2D([], [], color="#000000", lw=0.9,
                           label="Nominal SLO"))
        keys += [Patch(facecolor=col, edgecolor="#000000", linewidth=0.5,
                       hatch=h, label=lab) for _k, lab, col, h in SEG]
        band = 0.20
        rect_top = 1 - band / FIG_H
        fig.legend(keys, [k.get_label() for k in keys], loc="lower center",
                   bbox_to_anchor=(0.5, rect_top + 0.004), ncol=len(keys),
                   frameon=False, fontsize=5.6, columnspacing=0.8,
                   handlelength=1.2, handleheight=1.2, handletextpad=0.3,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0.115 / FIG_H, 1, rect_top), w_pad=1.2,
                         pad=0.3)
        fig.text(0.5, 0.012, f"{model}, one hour, repeat 2", ha="center",
                 va="bottom", fontsize=8)
        save(fig, out)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=sorted(MODELS), default="llama")
    a = ap.parse_args()
    model = MODELS[a.model]
    per, dur = collect(model)
    print(f"{model}, cut at {dur:.2f} min")
    pdf = os.path.join(HERE, f"class_latency_outcome_{a.model}.pdf")
    df = build(per, dur, model, pdf)
    out = pdf[:-4] + ".csv"
    df.to_csv(out, index=False, float_format="%.3f")
    print(f"wrote {out}  ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
