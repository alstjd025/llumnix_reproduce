#!/usr/bin/env python3
"""Paper figure: the same twelve minutes on one engine, under three control planes.

  latency_three_arms.pdf   7.0 x 2.60 in, `figure*`, width=\\textwidth
  latency_three_arms.csv   exactly the values drawn

  top row     what the engine delivered per token, what it was REQUIRED to
              deliver, and the share of the requests it was given that met
              their deadline
  bottom row  the tokens that engine produced per second

  (a) Llumnix SLO   (b) llm-d   (c) PolyServe

This is `latency_two_arms.pdf` with a third control plane and two lines added to
the top row. Everything else -- window, engine index, scoring, smoothing -- is
unchanged, so a value read off one figure sits at the same height on the other.

THREE LINES IN THE TOP ROW, AND THEY ANSWER ONE QUESTION EACH.

  TBT            the engine's own inter-token latency, per second: what every
                 request decoding there is actually getting, since one step
                 advances every resident request by one token
  required TBT   the TIGHTEST per-token budget among the classes resident on
                 that engine in that minute -- 50 ms if any chat is there, else
                 75 if any agent, else 100. It is a step function of the class
                 mixture and not a property of the policy's settings, which is
                 why it can differ between panels and move inside one
  mean TBT       the engine's own inter-token latency averaged over the whole
                 window, drawn flat so the eye can put it against the required
                 line without integrating the noisy curve

⚠ THE REQUIRED LINE IS NOT A THRESHOLD THE SCORE IS COMPUTED FROM. Attainment
here is the ladder rule on whole requests, which lets a request bank early
tokens against late ones; an engine can sit above its required line for a while
and still meet the deadline of everything on it. The two are drawn together
because the question "was this engine fast enough for what was on it" has no
answer without both, not because one implies the other.

⚠ A CLASS COUNTS AS RESIDENT AT ANY OCCUPANCY. `resident` is a time average over
the minute, so a single chat request present for three seconds reads 0.05 and
still pulls the required line to 50 ms. That is the honest reading -- the engine
did have that request to serve -- but it means the line reports the tightest
thing present rather than the typical thing present, and a minute at 50 ms can
be a minute that was almost entirely deep research.

⚠ WHAT THE FIGURE DOES NOT SHOW IS REJECTION, and over these twelve minutes the
three arms refuse very different shares of what arrives. A control plane that
admits less work runs its engine faster and meets the deadline of more of what
it kept. The caption has to carry the three rejection rates or the panels read
as "one is better at everything".

⚠ THE ENGINE INDEX IS NOT THE SAME KIND OF THING IN THE THREE PANELS. llm-d
mixes the classes, so its four engines are interchangeable. Llumnix SLO's and
PolyServe's are not: PolyServe's engine 8002 is its agent tier's server for this
whole window, which is why its required line sits at 75 ms while the other two
sit at 50.

COLOUR. The attainment curve is ONE colour in every panel (#54278f, dark
purple), because it is one quantity measured the same way three times; red is
reserved for the per-token time in the class-mix figures of this directory and
is not used here. The per-token time keeps each arm's own hue, as everywhere
else in this directory.

DATA. EXP-109 (2026-08-31) repeat 1 of each arm, engine 8002, minutes 35-47 from
each run's own first arrival. Gauges as scraped each second; the per-token time
is the engine's histogram counters differenced per second and smoothed over 3 s
before the division. Attainment is the ladder rule on the requests dispatched to
that engine, admitted denominator, 15 s windows. The class mixture behind the
required line is the 60 s residency table
`results/aggregate_analysis/class_mix/hour_engine_mix.csv`.

    python3 paper_figures/fig_latency_three_arms.py
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
L2 = _load("lat2", os.path.join(HERE, "fig_latency_two_arms.py"))
# The budgets come from the scorer, not from a copy: a figure that drew a
# required line from its own table could disagree with the attainment curve
# beside it about what the deadline was.
DLA = _load("dla", os.path.join(ROOT, "analysis_scripts", "request_level",
                                "deadline_ladder_attainment.py"))

ARMS = ["slo", "llmd", "polyserve"]
PORT = 8002
T_LO, T_HI = 35.0, 47.0
MIX = os.path.join(ROOT, "results", "aggregate_analysis", "class_mix",
                   "hour_engine_mix.csv")
REPEAT = "exp109r1"
# The per-token time keeps the arm's hue; the attainment is one colour for all
# three panels and the required line is a neutral grey-black, because it is not
# a measurement of this engine but the promise it was under.
ITL_C = {"slo": "#1b7837", "llmd": "#8c564b", "polyserve": "#d62728"}
ATT_C = "#54278f"
REQ_C = "#333333"
MEAN_C = "#777777"
# ⚠ THE PER-TOKEN AXIS IS CUT AT 150 ms AND THE CUT IS COUNTED. Reaching the
# largest sample, which is what `latency_two_arms.pdf` does, puts the axis at
# 560 ms here because one engine has a handful of seconds in the hundreds; the
# band every arm actually lives in, 40-90 ms, then occupies a seventh of the
# panel and the two lines this figure was extended for -- the 50 and 75 ms
# promises -- become one hairline above the axis. The seconds above the cut are
# printed per arm so the caption can state them.
TBT_TOP = 150.0
FIG_H = 2.60
LABEL_SIZE = 6


def required_tbt(arm_key):
    """minutes, the tightest per-token budget among the classes on this engine.

    One step per 60 s window of the residency table. Returns the step edges so
    the line is drawn as a step and not as a slope between two minutes: the
    quantity changes when a class arrives or leaves, not gradually.
    """
    label = EW.RUNS[arm_key][0]
    d = pd.read_csv(MIX)
    d = d[d["run"].str.contains(REPEAT) & (d["engine_port"] == PORT)]
    d = d[d["run"] == EW.RUNS[arm_key][1]]
    if d.empty:
        sys.exit(f"no residency rows for {label} on engine {PORT}")
    x, y = [], []
    for w, g in d.groupby("win_start_s"):
        present = [c for c in ("chat", "deepresearch", "swe")
                   if g[g["class"] == c]["resident"].sum() > 0]
        if not present:
            continue
        x.append(w / 60.0)
        y.append(min(DLA.BUDGETS[c][1] for c in present))
    o = np.argsort(x)
    x, y = np.asarray(x)[o], np.asarray(y)[o]
    keep = (x >= T_LO - 1) & (x <= T_HI + 1)
    return x[keep], y[keep]


def series(arm_key):
    label, run = EW.RUNS[arm_key]
    EW.PORT, EW.T_LO, EW.T_HI, EW.SMOOTH_S = PORT, T_LO, T_HI, 1
    EW.WIN, EW.STEP = 15.0, 15.0
    L2.PORT, L2.T_LO, L2.T_HI = PORT, T_LO, T_HI
    d = os.path.join(ROOT, "results", run)
    t0 = float(pd.read_csv(os.path.join(d, "metrics.csv"),
                           usecols=["start_time"])["start_time"].min())
    m, _kv, _bat, _wait, itl = EW.gauges(d, t0)
    att = EW.attainment(d, t0)
    thr = L2.token_rate(d, t0)
    req = required_tbt(arm_key)
    return label, run, m, itl, att, thr, req


def write_csv(data, path):
    rows = []
    for label, run, m, itl, att, thr, req in data:
        rows += [{"arm": label, "run": run, "series": "inter_token_latency_ms",
                  "minute": float(a), "value": float(b)}
                 for a, b in zip(m, itl)]
        rows += [{"arm": label, "run": run, "series": "required_tbt_ms",
                  "minute": float(a), "value": float(b)}
                 for a, b in zip(*req)]
        ok = ~np.isnan(itl)
        rows.append({"arm": label, "run": run, "series": "mean_tbt_ms_window",
                     "minute": np.nan, "value": float(itl[ok].mean())})
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
    df["window_minutes"] = f"{T_LO:.0f}-{T_HI:.0f}"
    df.to_csv(path, index=False, float_format="%.4f")
    print(f"wrote {path}  ({len(df)} rows)")


def build(data, out, width=ps.TEXT_W, height=FIG_H):
    top = TBT_TOP
    style = {**ps.STYLE, "xtick.labelsize": LABEL_SIZE,
             "ytick.labelsize": LABEL_SIZE, "axes.labelsize": LABEL_SIZE}
    with plt.rc_context(style):
        fig, axes = plt.subplots(2, len(data), figsize=(width, height))
        ax, axb = axes[0], axes[1]
        top_thru = max(float(np.nanmax(d[5][1])) for d in data)
        for i, (label, _run, m, itl, att, thr, req) in enumerate(data):
            itl_c = ITL_C[ARMS[i]]
            a, b = ax[i], ax[i].twinx()
            ok = ~np.isnan(itl)
            mean_itl = float(itl[ok].mean())
            a.plot(m, itl, color=itl_c, lw=0.9, zorder=3)
            # The promise, as a step: it changes when a class arrives or leaves.
            a.step(req[0], req[1], where="post", color=REQ_C, lw=1.0,
                   ls=(0, (4, 1.5)), zorder=4)
            a.axhline(mean_itl, color=MEAN_C, lw=0.8, ls=":", zorder=2)
            b.plot(att[0], att[1], color=ATT_C, lw=1.0, zorder=3)
            a.set_ylim(0, top)
            a.set_yticks([0, 50, 100, 150])
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
            a.set_ylabel("TBT (ms)", labelpad=1.5)
            b.set_ylabel("Request SLO (%)", labelpad=2.0)
            a.set_xlabel("Time (min.)", labelpad=1.5)

            axb[i].plot(thr[0], thr[1], color=itl_c, lw=0.8)
            axb[i].set_ylim(0, top_thru * 1.05)
            axb[i].set_xlim(T_LO, T_HI)
            axb[i].set_xticks(np.arange(T_LO, T_HI + 1e-9, 3))
            axb[i].xaxis.set_minor_locator(AutoMinorLocator(3))
            axb[i].yaxis.set_major_formatter(ps.kfmt())
            axb[i].grid(axis="both", **ps.GRID)
            axb[i].set_axisbelow(True)
            axb[i].tick_params(which="minor", length=1.2)
            axb[i].set_xlabel(f"Time (min.)\n({'abc'[i]}) {label}",
                              labelpad=1.5, linespacing=1.5)
            axb[i].set_ylabel("Tokens/s", labelpad=1.5)
            over = int((itl[ok] > top).sum())
            print(f"    {label}: TBT p50 {np.percentile(itl[ok], 50):.1f} ms, "
                  f"mean {mean_itl:.1f}, required "
                  f"{np.unique(req[1])} ms, SLO mean {att[1].mean():.1f}%, "
                  f"tokens/s p50 {np.percentile(thr[1], 50):,.0f}, "
                  f"{over} of {int(ok.sum())} s above the {top:.0f} ms axis "
                  f"(max {itl[ok].max():.0f})")

        # One key for the figure: four line styles that mean the same thing in
        # every panel. Per-panel keys were what the two-arm figure needed
        # because its colours were the panel's own; here only the TBT curve
        # changes hue, and it is named by the panel underneath it.
        hs = [Line2D([], [], color="#555555", lw=0.9, label="TBT (this engine)"),
              Line2D([], [], color=REQ_C, lw=1.0, ls=(0, (4, 1.5)),
                     label="Required TBT (tightest class present)"),
              Line2D([], [], color=MEAN_C, lw=0.8, ls=":",
                     label="Mean TBT over the window"),
              Line2D([], [], color=ATT_C, lw=1.0, label="SLO attainment")]
        band = 0.20
        rect_top = 1 - band / height
        fig.legend(hs, [h.get_label() for h in hs], loc="lower center",
                   bbox_to_anchor=(0.5, rect_top + 0.004), ncol=4,
                   frameon=False, fontsize=6.0, columnspacing=1.0,
                   handlelength=1.6, handletextpad=0.35, borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, rect_top), pad=0.3, w_pad=1.0,
                         h_pad=0.8)
        w, h = fig.get_size_inches()
        bb = ax[0].get_position()
        print(f"    panel axes box {bb.width * w:.2f} x {bb.height * h:.2f} in")
        ps.save(fig, out)


def main():
    data = [series(k) for k in ARMS]
    pdf = os.path.join(HERE, "latency_three_arms.pdf")
    build(data, pdf)
    write_csv(data, pdf[:-4] + ".csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
