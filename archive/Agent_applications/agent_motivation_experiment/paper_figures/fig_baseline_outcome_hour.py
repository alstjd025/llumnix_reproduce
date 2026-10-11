#!/usr/bin/env python3
"""Paper figure: what the baselines did with the requests they ACCEPTED, on the
hour trace, under two models.

  baseline_outcome_hour.pdf   7.0 x 1.85 in, `figure*`, width=\\textwidth
  baseline_outcome_hour.csv   exactly the values drawn

  (a) Llama-3.1-70B, per baseline: requests per second it accepted, split into
      the ones that met their rule and the ones that did not
  (b) Llama-3.1-70B, per baseline: the tokens per second that met their own
      deadline
  (c), (d) the same two questions on Qwen2.5-72B

FLUIDSERVE IS NOT DRAWN. The question this figure asks is what the baselines do
with what they keep, and our arm's bar would be a fifth column that answers a
different question -- it accepts nearly everything and misses almost none of it,
so its "missed" band is invisible and its presence would turn the panel into a
ranking. The same runs with our arm in them are
`two_models_hour_reqgoodput.pdf`.

⚠ THE BAR IS THE ACCEPTED SET AND NOT THE ARRIVALS. Its height is the rate at
which each control plane ADMITTED work, so a short bar can be a policy that
refused most of what arrived rather than one that was sent less: over these
spans the four refuse between 0.0% and 43.5% of arrivals. The rejection rates
are printed by this script and belong in the caption; `two_models_hour_stacked
_normfs_met.pdf` is the figure that carries them as a band.

⚠ "MISSED" HERE EXCLUDES THE UNFINISHED. A request still running when the span
closed has no outcome, and the arm without admission control leaves about half
of its arrivals in that state on the Llama hour, so its accepted set is much
smaller than what it was sent. The unfinished count is in the CSV and printed.

⚠ THE TWO MODELS ARE NOT A CONTROLLED COMPARISON. The Qwen hour replays a
thinned arrival trace -- 64% of the arrivals -- while every class budget stays
where it was, so model and offered load differ at once between (a,b) and (c,d).

SCORING is the ladder rule of this directory at the standard budgets: token i is
on time if it arrives within TTFT_SLO + i x TBT_SLO of the send, a request is on
time if at least 95% of its tokens are, and the token goodput counts the tokens
that met their own deadline -- including those of a request that missed the 95%
bar, which is why (b) and (d) are not simply (a) and (c) times an output length.

DATA. The runs `two_models_hour_reqgoodput.pdf` draws: EXP-109 (2026-09-01/02,
Llama) and EXP-113 (2026-09-03, Qwen), repeat 2 as the bar and repeat 1 as the
range mark, each pair cut at the same minute as that figure.

    python3 paper_figures/fig_baseline_outcome_hour.py
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

from paper_style import TEXT_W, STYLE, GRID, kfmt, save  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TM = _load("tm", os.path.join(HERE, "fig_two_models_hour.py"))

# The four baselines, in the paper's order. FluidServe is deliberately absent.
ORDER = ["vLLM", "PolyServe", "Llumnix SLO", "llm-d"]
# The full version adds our arm and the two outcomes the baselines-only figure
# leaves out. ⚠ WITH ALL FOUR BANDS THE BAR IS EVERY ARRIVAL, so its height is
# the offered rate and is the same for every arm of a model -- except the arm
# without admission control, which the load generator could not keep fed.
ORDER_FULL = ORDER + ["FluidServe"]
# The outcome colours of this directory: one colour means one outcome across
# every figure here. The arm is named under its bar, because colour is spent on
# the outcome.
C_MET, C_MISS = "#80cdc1", "#f4a582"
# The other two outcomes, in the colours `two_models_hour_stacked.pdf` gives
# them: refused, and still running when the span closed. ⚠ The grey is LIGHTER
# than the two above on purpose -- nothing was served in it, and the eye should
# not land there first -- and the unfinished band is white with a hatch because
# it is not a result at all.
C_REJ, C_CUT = "#cfcfcf", "#ffffff"
HATCH_CUT = "////"
# The goodput panel has one bar per arm and the arm is already named under it,
# so the bars are one neutral colour rather than four that would repeat the
# labels in ink.
C_GOOD = "#9ecae1"
FIG_H = 1.85
LABEL_SIZE = 6


def rates(v, minute):
    """Requests per second met and missed among the ACCEPTED, and the tokens
    per second that met their own deadline, over minutes 0..`minute`."""
    w = v[v["rel"] <= minute * 60.0]
    span = minute * 60.0
    met_m = (w["ladder_ok"] & ~w["rejected"] & ~w["errored"] & ~w["cutoff"])
    met = int(met_m.sum())
    rej = int(w["rejected"].sum())
    cut = int(w["cutoff"].sum())
    missed = len(w) - met - rej - cut
    served = w[~w["rejected"] & ~w["cutoff"]]
    good = float((served["n_tokens"] - served["n_late"]).sum()) / span
    return dict(met_r_s=met / span, missed_r_s=missed / span,
                goodput_tok_s=good, met=met, missed=missed, rejected=rej,
                unfinished=cut, arrivals=len(w),
                rejected_pct=100.0 * rej / max(len(w), 1))


def collect(order=ORDER):
    out = {}
    for model, arms in TM.RUNS.items():
        per = {}
        for arm in order:
            r2, r1 = arms[arm]
            v2, v1 = TM.verdicts(r2), TM.verdicts(r1)
            if v2 is None:
                print(f"!! {model}/{arm}: no verdicts", file=sys.stderr)
                continue
            per[arm] = dict(run=r2, v2=v2, v1=v1)
        out[model] = per
    return out


def cut_minutes():
    """The same cut this figure's source figure uses, model by model."""
    data = TM.collect()
    return {m: TM.cut_minute(data[m]) for m in TM.RUNS}


def build(data, cuts, out, order=ORDER, bands=("met", "missed")):
    style = {**STYLE, "xtick.labelsize": LABEL_SIZE,
             "ytick.labelsize": LABEL_SIZE, "axes.labelsize": LABEL_SIZE}
    rows = []
    with plt.rc_context(style):
        fig, ax = plt.subplots(
            1, 4, figsize=(TEXT_W, FIG_H),
            gridspec_kw=dict(width_ratios=[1.0, 0.72, 1.0, 0.72]))
        top_req = top_good = 0.0
        for mi, (model, per) in enumerate(data.items()):
            dur = cuts[model]
            a, b = ax[2 * mi], ax[2 * mi + 1]
            xs, met, miss, good, rej, cut = [], [], [], [], [], []
            glo, ghi, mlo, mhi = [], [], [], []
            names = []
            for j, arm in enumerate(order):
                if arm not in per:
                    continue
                d = per[arm]
                s2 = rates(d["v2"], dur)
                s1 = rates(d["v1"], dur) if d["v1"] is not None else s2
                xs.append(j); names.append(arm)
                met.append(s2["met_r_s"]); miss.append(s2["missed_r_s"])
                rej.append(s2["rejected"] / (dur * 60.0))
                cut.append(s2["unfinished"] / (dur * 60.0))
                good.append(s2["goodput_tok_s"])
                mlo.append(min(s2["met_r_s"], s1["met_r_s"]))
                mhi.append(max(s2["met_r_s"], s1["met_r_s"]))
                glo.append(min(s2["goodput_tok_s"], s1["goodput_tok_s"]))
                ghi.append(max(s2["goodput_tok_s"], s1["goodput_tok_s"]))
                for rep, s in ((2, s2), (1, s1)):
                    rows.append(dict(model=model, arm=arm, repeat=rep,
                                     cut_minute=dur, **s))
                print(f"    {model:14s} {arm:12s} met {s2['met_r_s']:6.2f} "
                      f"missed {s2['missed_r_s']:5.2f} r/s, goodput "
                      f"{s2['goodput_tok_s']:8,.0f} tok/s, rejected "
                      f"{s2['rejected_pct']:5.1f}%, unfinished "
                      f"{s2['unfinished']:,}")
            # The stack, bottom to top, in the order `bands` gives.
            stack = {"met": (met, C_MET, None), "missed": (miss, C_MISS, None),
                     "rejected": (rej, C_REJ, None),
                     "unfinished": (cut, C_CUT, HATCH_CUT)}
            bottom = np.zeros(len(xs))
            for key in bands:
                vals, colour, hatch = stack[key]
                a.bar(xs, vals, bottom=bottom, width=0.72, color=colour,
                      edgecolor="#000000", linewidth=0.5, hatch=hatch)
                bottom = bottom + np.array(vals)
            # The range mark is on the ATTAINED band only: it is the quantity
            # the panel is read for, and a second mark on the stack top would
            # be a range of a sum whose two parts move for different reasons.
            a.vlines(xs, mlo, mhi, color="#333333", lw=0.7)
            b.bar(xs, good, width=0.72, color=C_GOOD, edgecolor="#000000",
                  linewidth=0.5)
            b.vlines(xs, glo, ghi, color="#333333", lw=0.7)
            tot = np.zeros(len(xs))
            for key in bands:
                tot = tot + np.array({"met": met, "missed": miss,
                                      "rejected": rej,
                                      "unfinished": cut}[key])
            top_req = max(top_req, float(tot.max()))
            top_good = max(top_good, max(ghi))
            for axis in (a, b):
                axis.set_xticks(xs)
                axis.set_xticklabels(names, fontsize=5.2, rotation=40,
                                     ha="right", rotation_mode="anchor")
                axis.set_xlim(-0.7, len(order) - 0.3)
                axis.grid(axis="y", **GRID)
                axis.set_axisbelow(True)
                for side in ("top", "right"):
                    axis.spines[side].set_visible(False)
            a.set_ylabel("Arrivals (req/s)" if len(bands) > 2
                         else "Accepted (req/s)", labelpad=1.5)
            b.set_ylabel("Token Goodput (t/s)", labelpad=1.5)
        for i in (0, 2):
            ax[i].set_ylim(0, top_req * 1.12)
        for i in (1, 3):
            ax[i].set_ylim(0, top_good * 1.12)
            ax[i].yaxis.set_major_formatter(kfmt())
        face = {"met": (C_MET, None, "SLO Attained"),
                "missed": (C_MISS, None, "SLO Missed"),
                "rejected": (C_REJ, None, "Rejected"),
                "unfinished": (C_CUT, HATCH_CUT, "Unfinished")}
        keys = [Patch(facecolor=face[k][0], edgecolor="#000000", linewidth=0.5,
                      hatch=face[k][1], label=face[k][2]) for k in bands]
        keys.append(Patch(facecolor=C_GOOD, edgecolor="#000000", linewidth=0.5,
                          label="Token Goodput"))
        band = 0.20
        rect_top = 1 - band / FIG_H
        fig.legend(keys, [k.get_label() for k in keys], loc="lower center",
                   bbox_to_anchor=(0.5, rect_top + 0.004), ncol=len(keys),
                   frameon=False, fontsize=6.4, columnspacing=1.0,
                   handlelength=1.25, handleheight=1.25, handletextpad=0.3,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0.135 / FIG_H, 1, rect_top), w_pad=1.2,
                         pad=0.3)
        # ⚠ A PAIR NAME CENTRED ON ITS PAIR CAN RUN OFF THE CANVAS. The two
        # pairs sit at the edges of a 7 in figure and these names are about
        # 3 in of set type, so the right one lost its last characters --
        # silently, because `fig.text` is clipped by nothing. Each name is
        # measured after it is drawn and slid back inside if it crosses an edge,
        # which moves it off its pair's centre by the overflow and no more.
        texts = []
        for mi, model in enumerate(data):
            b0 = ax[2 * mi].get_position(); b1 = ax[2 * mi + 1].get_position()
            # ⚠ THE PAIR NAME IS THE MODEL AND NOTHING ELSE. It said "Accepted
            # Requests and Token Goodput - <model>", which is 3.0 in of type
            # against a 3.5 in half: the two names met in the middle of the
            # canvas with no gap between them. What they were repeating is
            # already on the two y axes of the pair, so the name carries what
            # the axes cannot -- which model the pair is.
            texts.append(fig.text(
                0.5 * (b0.x0 + b1.x1), 0.012, f"({'ac'[mi]}, {'bd'[mi]}) "
                f"{model}", ha="center", va="bottom", fontsize=8))
        fig.canvas.draw()
        w = fig.get_size_inches()[0] * fig.dpi
        for t in texts:
            e = t.get_window_extent(fig.canvas.get_renderer())
            shift = 0.0
            if e.x0 < 2:
                shift = (2 - e.x0) / w
            elif e.x1 > w - 2:
                shift = (w - 2 - e.x1) / w
            if shift:
                t.set_x(t.get_position()[0] + shift)
                print(f"    pair name moved {shift * 7.0:+.3f} in to fit")
        fig.canvas.draw()
        boxes = [t.get_window_extent(fig.canvas.get_renderer()) for t in texts]
        gap = (boxes[1].x0 - boxes[0].x1) / fig.dpi
        if gap < 0.1:
            print(f"  ⚠ the two pair names are {gap:.2f} in apart; shorten "
                  f"them or the reader cannot tell which belongs to which pair")
        else:
            print(f"    pair names {gap:.2f} in apart")
        save(fig, out)
    return pd.DataFrame(rows)


def main():
    cuts = cut_minutes()
    print("cut minute per model:", {k: round(v, 2) for k, v in cuts.items()})
    full = "--full" in sys.argv
    order = ORDER_FULL if full else ORDER
    bands = (("met", "missed", "rejected", "unfinished") if full
             else ("met", "missed"))
    data = collect(order)
    pdf = os.path.join(HERE, "baseline_outcome_hour"
                       + ("_full" if full else "") + ".pdf")
    df = build(data, cuts, pdf, order=order, bands=bands)
    df["rule"] = "ladder95, standard budgets"
    df["note"] = ("bar = accepted requests per second, split met/missed; "
                  "unfinished and rejected are excluded from the bar")
    out = pdf[:-4] + ".csv"
    df.to_csv(out, index=False, float_format="%.4f")
    print(f"wrote {out}  ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
