#!/usr/bin/env python3
"""Paper figure: the two ends of one axis at 35 req/s, and what each costs.

  pace_and_outcome_35.pdf   3.335 x 1.75 in, ONE COLUMN, width=\\columnwidth
  pace_and_outcome_35.csv   the values drawn, same basename

  (a) decode iteration time on the engine carrying the tightest per-token
      budget, as a CDF, against that budget and against each arm's own mean
  (b) what happened to every request that arrived, as a share of arrivals

WHAT THE FIGURE CLAIMS, AND WHY THE TWO PANELS HAVE TO BE SIDE BY SIDE. A
control plane decides whether one more request fits, and it can be wrong in two
directions. (a) is the decision seen at the engine and (b) is what that decision
cost, at one arrival rate, on the same runs.

  llm-d        decides conservatively. Its engine spends 70.3% of its iterations
               inside the 50 ms chat budget and its median iteration is 37.3 ms,
               so the fleet is not being filled -- and (b) shows what that buys
               and what it costs: almost nothing it accepts misses (0.7% of
               arrivals) because it refuses 54.6% of them.
  Llumnix SLO  decides aggressively. Its median iteration is 74.4 ms, half again
               the budget, and only 32.2% of iterations are inside it. In (b) it
               refuses 39.2% AND still misses 36.7%, so its refusals are not
               cutting the work it cannot keep.
  PolyServe    does not decide per request at all; it partitions by class. Its
               chat-tier engine runs at a median of 63.3 ms and it refuses
               10.5%, so 41.5% of arrivals are accepted and missed.

⚠ THE MEAN LINES ARE IN (a) BECAUSE THE MEAN CONTRADICTS THE CLAIM AND THE
FIGURE SHOULD SHOW THAT. Every arm has a long right tail, so every arm's MEAN
iteration time is above the budget -- 58.5, 124.3 and 74.1 ms -- including the
arm whose iterations are mostly inside it. A sentence of the form "llm-d's
average decode time is well under 50 ms" is false on this data. What is true is
about where the mass is, which is what a CDF shows and what the vertical mean
lines are drawn against.

⚠ (b) HAS A FOURTH BAND. A request that was still in flight when the load window
closed has no outcome; `attain()` drops it from both denominators everywhere else
in the paper, so it cannot be folded into either "met" or "missed" here. It is
1.1 to 3.9% for these three arms. Every bar is 100% of arrivals.

SCOPE, AND WHERE THE TWO PANELS DIFFER. (a) is one engine of one run -- the
chat-carrying engine, repeat 1 -- because an iteration-time distribution is a
property of an engine. (b) is every request of both repeats. The band in (a) is
the envelope of that run's four engines and is NOT a repeat spread; the repeat
spread for (b) is in the CSV.

WHICH ENGINE IS DRAWN IN (a). The engine's admissible pace is set by the
tightest per-token budget among the requests on it, so the engine to look at is
one holding chat. Joining the client's request ids with the scheduler's dispatch
log: PolyServe engine 8003 is 98.8% chat (its chat tier), Llumnix SLO 8003 is
73.4% chat, the highest of its four. llm-d routes through its own inference
gateway and cannot be attributed this way; its four engines have medians within
3.8 ms of each other (37.3, 37.4, 38.9, 41.1 ms), which is what the band says
and why the choice does not carry the claim.

DATA. EXP-108 (2026-08-31) at 35 req/s, two repeats. Scoring for (b) is the
token-level cumulative deadline with a 90% tolerance: a_1 <= T and at least 90%
of the request's tokens satisfy a_i <= T + (i-1) * P, with chat (5 s, 50 ms),
deepresearch (10 s, 100 ms) and swe (7 s, 75 ms). FluidServe is deliberately
absent: this figure is about how the two failure modes bound one axis, and it is
the axis the paper's own system is placed on afterwards.

⚠ IT IS DRAWN AT ONE COLUMN AND NOT SCALED TO IT. 3.335 in is
`(\\textwidth - \\columnsep) / 2` for USENIX and ACM sigconf alike, the same
canvas as `intro_reject_throughput.pdf`, so `\\includegraphics` applies a factor
of 1.0 and 8 pt in the script is 8 pt on the page. What that width costs, in the
order the space was taken from: the y axis names are cut to "CDF" and
"Arrivals (%)"; the panel letters move inside the axes; the arm key moves inside
panel (a), into the bottom-right corner no CDF passes through; and only the four
outcome bands keep a legend above the figure, because panel (b) has no other way
to name them. `--full` redraws the same figure at 7.0 in for a two-column slot,
where those four things are affordable again. THE TWO SIZES ARE DIFFERENT
DRAWINGS OF ONE FIGURE, not one scaled: at 7.0 in the labels that were cut here
are written out, so do not scale either file to the other width.

    python3 paper_figures/fig_pace_and_outcome_35.py [--with-ours] [--full]
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paper_style import COL_W, TEXT_W, STYLE, GRID, ARM_COLOR, save  # noqa: E402

CHAT_BUDGET_MS = 50.0
PORTS = [8000, 8001, 8002, 8003]
GRID_MS = np.logspace(np.log10(12.0), np.log10(600.0), 100)
FIG_H = 1.75          # two-column판
FIG_H_COL = 2.20      # 한 칼럼판: 패널마다 두 줄짜리 키가 위에 붙어 그만큼 높다

# arm label, colour, the run whose engines panel (a) reads, the engine drawn,
# and the arm key panel (b) is keyed by in the outcome table.
ARMS = [
    ("PolyServe", ARM_COLOR["polyserve"],
     "260831_0805_exp108r1_polyservept75_t75fair_rpm_2100", 8003, "PolyServe"),
    ("Llumnix SLO", ARM_COLOR["slo"],
     "260831_0956_exp108r1_slot75_t75fair_rpm_2100", 8003, "Llumnix SLO"),
    ("llm-d", ARM_COLOR["llmd"],
     "260831_0548_exp108r1_llmdslot75_t75fair_rpm_2100", 8000, "llm-d"),
]
OURS = ("FluidServe", ARM_COLOR["fluidserve"],
        "260831_0257_exp108r1_fsv3capgnofrct75_t75_rpm_2100", 8000, "FluidServe")

def desat(hex_colour, f):
    """The same hue at lower saturation: blend the colour with its own grey.

    Blending toward WHITE would lighten it as well, and four lightened bands
    stacked in one bar stop separating from the white hatch band between them.
    Blending toward the colour's own luminance keeps the value and takes only
    the chroma out, which is what a filled area needs and a line does not.
    """
    r, g, b = (int(hex_colour[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    grey = 0.299 * r + 0.587 * g + 0.114 * b
    m = [c * f + grey * (1 - f) for c in (r, g, b)]
    return "#%02x%02x%02x" % tuple(int(round(255 * c)) for c in m)


# The project's own arm colours at 55% saturation. They are the colours this
# paper already uses for these systems, so a reader carries one vocabulary
# across the figures; a filled band at full chroma next to three others is
# louder than a line of the same colour, which is why only the saturation
# moves. White under a hatch for the requests that have no outcome at all --
# the hatch says "not a result" rather than naming a fifth one.
SEG = [("met_pct", "Met", desat("#2ca02c", 0.55), None),
       ("missed_pct", "Admitted, missed", desat("#ff7f0e", 0.55), None),
       ("unfinished_pct", "Unfinished", "#ffffff", "////"),
       ("rejected_pct", "Rejected", "#c7c7c7", None)]
OUTCOME_CSV = os.path.join(HERE, "outcome_split_sweep_t75.csv")


def windows(run, port):
    """Mean inter-token latency of each 1 s scrape window of one engine, in ms.

    `delta(inter_token_latency_seconds_sum) / delta(..._count)`, the engine's own
    measurement. A window in which the counter did not advance produced no output
    token and has no iteration time; it is dropped rather than counted as zero."""
    path = os.path.join(ROOT, "results", run, "server_metrics",
                        f"engine_{port}.jsonl")
    if not os.path.exists(path):
        return np.array([])
    s, c = [], []
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            gs = next((d[k] for k in d
                       if k.startswith("vllm:inter_token_latency_seconds_sum")
                       and d[k] is not None), None)
            gc = next((d[k] for k in d
                       if k.startswith("vllm:inter_token_latency_seconds_count")
                       and d[k] is not None), None)
            if gs is None or gc is None:
                continue
            s.append(float(gs))
            c.append(float(gc))
    if len(s) < 2:
        return np.array([])
    ds, dc = np.diff(np.array(s)), np.diff(np.array(c))
    m = dc > 0
    return 1000.0 * ds[m] / dc[m]


def cdf_on_grid(v):
    return np.searchsorted(np.sort(v), GRID_MS, side="right") / len(v)


def build(arms, out_pdf, full=False):
    out_rows = []
    oc = pd.read_csv(OUTCOME_CSV)
    oc = oc[oc["req_per_s"] == 35.0]
    with plt.rc_context(STYLE):
        width = TEXT_W if full else COL_W
        fig, ax = plt.subplots(1, 2, figsize=(width,
                                              FIG_H if full else FIG_H_COL),
                               gridspec_kw=dict(
                                   width_ratios=[1.45, 1.0] if full
                                   else [1.0, 1.02]))
        handles, labels = [], []
        for label, colour, run, drawn, key in arms:
            per = {p: windows(run, p) for p in PORTS}
            per = {p: v for p, v in per.items() if v.size}
            if not per:
                sys.exit(f"no engine windows for {label} in {run}: the run "
                         f"directory name is wrong or its engine metrics are "
                         f"missing. A silently empty arm would leave the panel "
                         f"looking complete with one curve absent.")
            v = per[drawn]
            h, = ax[0].plot(GRID_MS, cdf_on_grid(v), color=colour, lw=1.3)
            handles.append(h)
            labels.append(label)
            # The arm's own mean, as a line, because the mean is the statistic
            # the claim would be false under and the figure has to show that.
            ax[0].axvline(v.mean(), color=colour, ls=(0, (1, 1.4)), lw=1.0)
            out_rows += [dict(panel="a", arm=label, run=run, engine=drawn,
                              role="drawn", n_windows=len(v), itl_ms=x, cdf=y)
                         for x, y in zip(GRID_MS, cdf_on_grid(v))]
            # The other three engines are no longer drawn -- the shading that
            # carried them is gone -- so their spread is kept as one row each
            # rather than silently lost: it is the only thing standing in for
            # the class attribution llm-d cannot be given.
            for p, w in per.items():
                if p == drawn:
                    continue
                out_rows.append(dict(panel="a_spread", arm=label, run=run,
                                     engine=p, role="not drawn",
                                     n_windows=len(w),
                                     itl_ms=float(np.median(w)), cdf=np.nan))
            out_rows.append(dict(panel="a_mean", arm=label, run=run,
                                 engine=drawn, role="drawn", n_windows=len(v),
                                 itl_ms=float(v.mean()), cdf=np.nan))
        ax[0].axvline(CHAT_BUDGET_MS, color="#333333", ls="--", lw=0.9)
        # Horizontal and low, to the right of the line: rotated up the middle
        # of the panel it crossed the two curves the panel is read for.
        # Bottom right, the only region of this panel no curve passes through.
        # Beside the line it was crossed by the nearest arm's mean line.
        if full:
            ax[0].text(560, 0.03, "dashed: 50 ms\nchat budget", fontsize=5.8,
                       color="#333333", va="bottom", ha="right",
                       linespacing=1.25)
        ax[0].set_xscale("log")
        ax[0].set_xlim(12, 200)
        ticks = [20, 50, 100, 200]
        ax[0].set_xticks(ticks)
        ax[0].set_xticklabels([str(t) for t in ticks])
        ax[0].minorticks_off()
        ax[0].set_ylim(0, 1.0)
        ax[0].set_yticks([0, 0.25, 0.5, 0.75, 1.0])
        ax[0].grid(axis="both", **GRID)
        ax[0].set_axisbelow(True)
        ax[0].set_ylabel("CDF over 1 s windows" if full else "CDF")
        # The axis names and every numeric tick label are left at the project
        # style's 8 pt, the size they take in the four-panel figure this one is
        # read beside; the text is shortened to fit instead of the type. Only
        # panel (b)'s arm names, which have no counterpart there, are smaller.
        ax[0].set_xlabel(
            ("Decode iteration time (ms)\n(a) Decode time") if full
            else "Iteration time (ms)", labelpad=1.5, linespacing=1.7)


        names = [a[4] for a in arms]
        xs = np.arange(len(names))
        bottom = np.zeros(len(names))
        bar_h = []
        for col, _, colour, hatch in SEG:
            v = np.array([oc.loc[oc["arm"] == n, col].iloc[0] for n in names])
            b = ax[1].bar(xs, v, bottom=bottom, width=0.62, color=colour,
                          edgecolor="#555555", linewidth=0.35, hatch=hatch)
            bottom += v
            bar_h.append(b)
        for n in names:
            r = oc[oc["arm"] == n].iloc[0]
            out_rows.append(dict(panel="b", arm=n, run="EXP-108 35 req/s x2",
                                 engine=np.nan, role="drawn",
                                 n_windows=int(r["n_repeats"]), itl_ms=np.nan,
                                 cdf=np.nan,
                                 **{c: float(r[c]) for c, _, _, _ in SEG}))
        ax[1].set_xticks(xs)
        # Rotated at one column: three policy names side by side under a panel
        # 1.15 in wide collide horizontally at any size big enough to read.
        ax[1].set_xticklabels(
            [n.replace(" ", "\n") for n in names] if full else names,
            fontsize=6.5 if full else 6.0,
            rotation=0 if full else 30,
            ha="center" if full else "right")
        ax[1].set_xlim(-0.62, len(names) - 0.38)
        ax[1].set_ylim(0, 100)
        ax[1].set_yticks([0, 25, 50, 75, 100])
        ax[1].grid(axis="y", **GRID)
        ax[1].set_axisbelow(True)
        ax[1].set_ylabel("Share of arrivals (%)" if full else "Arrivals (%)")
        ax[1].set_xlabel("\n(b) Arrival outcome" if full else "",
                         labelpad=1.5, linespacing=1.7)


        if full:
            fig.legend(handles, labels,
                       loc="lower center", ncol=len(handles),
                       bbox_to_anchor=(0.30, 0.875), frameon=False,
                       fontsize=6.8, columnspacing=0.9, handlelength=1.4,
                       handletextpad=0.35)
            fig.legend([b[0] for b in bar_h], [s[1] for s in SEG],
                       loc="lower center", ncol=2,
                       bbox_to_anchor=(0.845, 0.845), frameon=False,
                       fontsize=6.2, columnspacing=0.8, handlelength=1.1,
                       handletextpad=0.35, labelspacing=0.25)
            fig.tight_layout(rect=(0, 0, 1, 0.862), w_pad=2.2, pad=0.25)
        else:
            # Each panel's key sits ABOVE that panel, so a reader never has to
            # carry a key across the figure and no key sits on a curve. The two
            # keys name different things -- (a)'s the policies, (b)'s the four
            # outcomes -- and putting them in one row over the whole canvas
            # would suggest one applies to both.
            #
            # ⚠ THE KEYS ARE THE ONE THING NOT AT THE PROJECT'S 8 pt. Three
            # policy names at 8 pt measure about 1.85 in against a panel 1.35 in
            # wide, so the row either wraps off the canvas or the panel loses a
            # third of its height to a three-row key. The type is dropped here
            # and nowhere else; every axis name and numeric tick label stays at
            # 8 pt, which is what makes this figure sit beside the four-panel
            # one without the text changing size between them.
            # TWO ROWS EACH, not one. A single row forced the type down to
            # 5 pt, which is smaller than anything else on the page; two rows
            # buy back 1.5 pt and cost 0.12 in of canvas height.
            #
            # The dotted vertical lines get a key of their own, in neutral
            # grey. There are three of them, one per arm in that arm's colour,
            # and without a key a reader has no way to learn what they are --
            # which is exactly what happened when this figure was first shown.
            mean_key = plt.Line2D([], [], color="#777777", ls=(0, (1, 1.4)),
                                  lw=1.0)
            # Anchored to the CANVAS corners, not to the panels: the two keys
            # together need about 2.7 in and the two panels span 2.8 in, so
            # anchoring each over its own panel makes them meet in the middle.
            # Left key over the left panel, right key over the right one, each
            # using the full margin, is the only arrangement that fits both at
            # a readable size.
            fig.legend(handles + [mean_key], labels + ["arm's mean"],
                       loc="upper left", bbox_to_anchor=(0.005, 1.0),
                       ncol=2, fontsize=6.2, frameon=False, handlelength=1.1,
                       handletextpad=0.3, columnspacing=0.6,
                       labelspacing=0.2, borderaxespad=0.0, borderpad=0.0)
            # Reordered so the long label starts its own row: with the four in
            # source order the first row is "Met | Admitted, missed" and runs
            # past the panel.
            order = [0, 2, 1, 3]
            fig.legend([bar_h[i][0] for i in order],
                       [SEG[i][1] for i in order],
                       loc="upper right", bbox_to_anchor=(0.995, 1.0),
                       ncol=2, fontsize=6.2, frameon=False, handlelength=1.0,
                       handletextpad=0.3, columnspacing=0.5,
                       labelspacing=0.2, borderaxespad=0.0, borderpad=0.0)
            # The budget line stays an annotation: a fifth key entry would add
            # a third legend row. Left of the line, not right of it -- to the
            # right it sits among the mean lines and reads as struck through.
            ax[0].text(CHAT_BUDGET_MS * 0.93, 0.98, "50 ms", fontsize=6.0,
                       color="#333333", va="top", ha="right")
            fig.tight_layout(rect=(0, 0.085, 1, 0.865), w_pad=0.7, pad=0.3)
            # The panel names are placed at one fixed height rather than as the
            # second line of each x label: panel (b)'s tick labels are rotated
            # and therefore taller than panel (a)'s, so labels attached to the
            # axes come out at two different heights and read as a misprint.
            for a, name in zip(ax, ["(a) Decode time", "(b) Arrival outcome"]):
                box = a.get_position()
                fig.text(0.5 * (box.x0 + box.x1), 0.012, name, ha="center",
                         va="bottom", fontsize=8)
        save(fig, out_pdf)
    return pd.DataFrame(out_rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-ours", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="redraw at 7.0 in for a two-column slot")
    args = ap.parse_args()
    arms = ARMS + ([OURS] if args.with_ours else [])
    base = os.path.join(HERE, "pace_and_outcome_35"
                        + ("_withfs" if args.with_ours else "")
                        + ("_full" if args.full else ""))
    df = build(arms, base + ".pdf", full=args.full)
    df.to_csv(base + ".csv", index=False, float_format="%.5f")
    print(f"wrote {base}.csv  ({len(df)} rows)")
    m = df[df["panel"] == "a_mean"]
    print("\n(a) 그린 엔진의 평균 iteration 시간:")
    for _, r in m.iterrows():
        print(f"    {r['arm']:12s} engine {int(r['engine'])}  "
              f"mean {r['itl_ms']:6.1f} ms")
    b = df[df["panel"] == "b"]
    print("\n(b) 도착 전체의 갈래 (%):")
    print(b[["arm", "met_pct", "missed_pct", "unfinished_pct",
             "rejected_pct"]].round(1).to_string(index=False))


if __name__ == "__main__":
    main()
