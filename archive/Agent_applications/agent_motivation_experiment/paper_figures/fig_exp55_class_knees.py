#!/usr/bin/env python3
"""Paper figure: EXP-55 — each class saturates at a different rate, in a
different engine state.

  exp55_class_knees.pdf     3.335 x 3.20 in, `figure`, width=\\columnwidth

A 3x3 grid. **Columns are the three classes, rows are three quantities**: SLO
attainment, KV occupancy, engine queue depth. Every panel is drawn against
ABSOLUTE offered rate over that class's own measured range, so nothing is
normalised and nothing is squeezed: chat spans 40-90 req/s, the agent class
12-48, deep research 8-30, and each column gets an axis that fits its own class.
The dotted vertical rule in every panel is that class's knee, so a column reads
straight down -- at the rate where attainment falls, this is what the engine
looked like.

That is the whole figure: **the three knees are 65, 24 and 16 req/s, and the
engine state at those three moments is nothing alike.**

              knee     attainment   KV mean   queue mean
  chat        65 req/s    94.9%       17.0%       0.6
  agent       24 req/s    87.7%       25.8%       0.3
  deep res.   16 req/s    99.9%       59.1%       0.2

The knee drawn is the ONSET -- the last measured rate at which the class is
still above 85% -- and the numbers above are the engine state at that rate. The
state one rate LATER, after the class has fallen, is 20.8 / 43.0 / 77.1% of the
pool and 0.6 / 0.4 / 45.0 queued, at attainments of 53.3 / 40.4 / 58.6%. Which
end is quoted changes the lower two rows a great deal for deep research and
hardly at all for chat, so the caption has to say which one the rule marks.

**Chat's collapse is invisible in both lower rows.** At its knee the KV pool is
17.0% used with 0.6 requests queued on average, and at the next rate up, where
attainment has fallen from 94.9% to 53.3%, it is 20.8% used with the same 0.6
queued. A policy watching memory, or queue depth, sees nothing change across the
rate at which chat stops meeting its rule. That is the argument for modelling
per-token pace per class rather than governing by a single occupancy threshold.

Deep research is the opposite: it holds 59.1% of the pool at a rate where it is
still meeting its rule 99.9% of the time, and it is the only class where
queueing enters the rule at all -- its queue is 0.2 at the knee and 45.0 one
rate later, where attainment drops to 58.6%. The agent class sits between the
two, and is scored end to end rather than on either of those axes.

WHY THE MEAN FOR THE TWO ENGINE ROWS. The queue is zero for most of the run at
every rate below saturation, so its MEDIAN is exactly 0 across the whole of
chat's sweep bar one point and the curve disappears; and its p90 never falls
below 1 for chat, because chat's sweep starts at 40 req/s where the ninetieth
percentile always finds someone waiting, so that curve cannot start where the
other two do. The mean is the only one of the three that is both continuous and
zero at low load, which is what lets the three classes share a baseline. Chat's
mean queue at its knee is 0.6 against deep research's 45.0 -- a factor of 75 in
one number. Same statistic for the KV row, so the two are read the same way.

The ordering does not depend on the choice of statistic: one rate past the knee
the three classes sit at KV mean 20.8 / 43.0 / 77.1, median 21.6 / 39.8 / 98.7
and p90 27.0 / 64.7 / 99.9. Both gauges are trimmed to the same analysis window
`load_run` scores attainment on.

The queue row is symlog: the values run from 0, which chat holds most of the
run, to 2,666, and a plain log axis would drop the zeros.

ONE RUN PER CONDITION, 24 conditions, no repeats. And the experiment's own
caveat: bursts are spread over four engines, so each knee sits very slightly
higher than a single engine would show.

    python3 paper_figures/fig_exp55_class_knees.py
"""
import glob
import json
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
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import COL_W, STYLE, GRID, save  # noqa: E402
from exp22_fluidserve import (  # noqa: E402
    load_run, per_request, CLASS_COLORS, WARMUP_S, DRAIN_S,
)

RUNS = "results/*exp55r1_loadbalance_*"
TAG_RE = re.compile(r"_loadbalance_(schat|sdr|sswe)_rpm_(\d+)$")
# tag -> (label, colour, line style, the rule it is scored on)
CLS = {
    "schat": ("chat", CLASS_COLORS["chat"], "-", dict(ttft=5.0, tbt=50.0)),
    "sdr": ("deep research", CLASS_COLORS["deepresearch"], "--",
            dict(ttft=10.0, tbt=100.0)),
    "sswe": ("agent", CLASS_COLORS["swe"], "-.", dict(e2e=30.0)),
}
ORDER = ["schat", "sswe", "sdr"]
# Panel letters as the user specified them.
# One line each. "Deep research" written out does not fit a 0.9 in column at
# 8 pt, so the third is abbreviated rather than wrapped -- the caption gives the
# class its full name.
PANEL = {"schat": "(a) Chat", "sswe": "(b) Agent",
         "sdr": "(c) Deep res."}
FIG_H = 3.20


def engine_series(run, prefix):
    """One engine gauge, pooled over the four engines, over the SAME window
    `load_run` scores attainment on.

    Without the trim the gauge covers the whole file including the arrival ramp
    while the attainment covers minute one onward, so the two panels would be
    measuring different stretches of the same run. The trim turns out to move
    the numbers very little here -- KV median 20.8 -> 21.6, 38.6 -> 39.8,
    97.2 -> 98.7 -- because the runs are eight minutes and the warmup is one.
    It is done anyway so that the panels are comparable by construction rather
    than by luck.
    """
    out = []
    for f in glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl")):
        rec = []
        for line in open(f):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if o.get("ok"):
                rec.append(o)
        if not rec:
            continue
        t0, t1 = rec[0]["t"], rec[-1]["t"]
        for o in rec:
            rel = o["t"] - t0
            if not (WARMUP_S <= rel < (t1 - t0) - DRAIN_S):
                continue
            for k, v in o.items():
                if k.startswith(prefix) and isinstance(v, (int, float)):
                    out.append(float(v))
    return np.array(out)


def rule_distance(r, rule):
    """Per request, how close it came to ITS OWN rule. 1.0 is the rule.

    Two classes are scored on a pair of rules and one on a total, so the only
    quantity that can share an axis across all three is each request's distance
    to whichever rule applies to it. For the pair, the binding half is whichever
    is closer, hence the max.
    """
    if "e2e" in rule:
        return pd.to_numeric(r["latency"], errors="coerce") / rule["e2e"]
    ttft = pd.to_numeric(r["first_token_latency"], errors="coerce") / rule["ttft"]
    itl = pd.to_numeric(r["itl_ms"], errors="coerce") / rule["tbt"]
    return pd.concat([ttft, itl], axis=1).max(axis=1)


def collect():
    rows = []
    for d in sorted(glob.glob(os.path.join(ROOT, RUNS))):
        m = TAG_RE.search(os.path.basename(d))
        if not m:
            continue
        tag, rpm = m.group(1), int(m.group(2))
        r = load_run(d)
        if r is None or r.empty:
            continue
        served = r[~r["cutoff"]]
        kv = engine_series(d, "vllm:kv_cache_usage_perc")
        q = engine_series(d, "vllm:num_requests_waiting")
        rows.append(dict(
            tag=tag, rate=rpm / 60.0,
            attain=per_request(r, "violate_offered"),
            dist=float(rule_distance(served, CLS[tag][3]).median()),
            kv=float(kv.mean() * 100) if len(kv) else np.nan,
            q=float(q.mean()) if len(q) else np.nan,
        ))
    return pd.DataFrame(rows).sort_values(["tag", "rate"])


def main():
    df = collect()
    if df.empty:
        print(f"no runs matched {RUNS}", file=sys.stderr)
        return 1

    knees = {}
    for tag in ORDER:
        d = df[df.tag == tag]
        # The knee: the LAST rate at which attainment is still >= 85%, i.e.
        # the measured condition at which the curve begins to turn down.
        #
        # EXP-55 writes each knee as an interval -- 65 -> 70, 24 -> 28,
        # 16 -> 18 -- because it is a transition between two measured rates and
        # not a point. Either end can be drawn, and the choice changes what the
        # lower two rows read at the line: at the onset chat is at 22.7% KV and
        # deep research at 82.0%, while one rate later they are 27.0% and 99.9%
        # and deep research's queue has gone from 1 to 144. The onset is used
        # because it is where the eye puts the bend, and because taking the
        # later end would let the figure quote the most dramatic engine state
        # available rather than the state at the moment the class starts to
        # fail. The queue blow-up past the line is visible in the panel itself.
        healthy = d[d.attain >= 85.0]
        knee = healthy.rate.max() if len(healthy) else np.nan
        knees[tag] = knee
        at = d[d.rate == knee]
        if len(at):
            a = at.iloc[0]
            # mean, not p90 -- this line said p90 while `collect` has taken the
            # mean since the row statistic was changed, which is how the
            # docstring came to describe a state one rate away from the drawn one.
            print(f"{CLS[tag][0]:14s} knee {knee:5.1f} req/s  "
                  f"attainment {a.attain:5.1f}%  KV mean {a.kv:5.1f}%  "
                  f"queue mean {a.q:7.1f}")

    # Short labels: the row is one metric for all three columns, and the
    # statistic (p90) belongs in the caption rather than repeated on the axis
    # where it crowds the tick numbers.
    # Short labels because every panel carries both axes and a column is only
    # about 0.8 in of drawing area wide: "SLO attainment (%)" is taller rotated
    # than a row is, and "Offered rate (request/s)" is wider than a column.
    # The statistic (p90) and the full names go in the caption.
    rows = [("attain", "SLO attain. (%)"),
            ("kv", "KV used (%)"),
            ("q", "Queue length")]

    with plt.rc_context(STYLE):
        fig, axes = plt.subplots(len(rows), len(ORDER),
                                 figsize=(COL_W, FIG_H), sharex="col")

        for col, tag in enumerate(ORDER):
            d = df[df.tag == tag].sort_values("rate")
            lab, c, _, _ = CLS[tag]
            # A little air either side of the measured range so the first and
            # last point are not on the frame.
            span = d.rate.max() - d.rate.min()
            lo, hi = d.rate.min() - 0.06 * span, d.rate.max() + 0.06 * span
            for row, (field, ylab) in enumerate(rows):
                ax = axes[row][col]
                ax.plot(d.rate, d[field], color=c, lw=1.3)
                # The knee, in every panel of the column, so the column reads
                # straight down from "attainment falls here" to "and the engine
                # looked like this".
                ax.axvline(knees[tag], color="#d62728", lw=0.8, ls=":", zorder=0)
                ax.set_xlim(lo, hi)
                ax.grid(axis="both", **GRID)
                ax.set_axisbelow(True)
                # NUMBERS on every panel, NAMES only on the outer edge. Each
                # column has its own rate range and each row its own scale, so
                # a reader needs the tick values in every cell; the axis name
                # is the same down a column and across a row, and repeating it
                # nine times costs the drawing area the curves need.
                ax.tick_params(labelbottom=True, labelleft=True)
                if col == 0:
                    ax.set_ylabel(ylab, labelpad=1.5)
                if row == len(rows) - 1:
                    ax.set_xlabel("Rate (req/s)", labelpad=1.5)
            # Black, not the class colour: the curve already carries the
            # colour, and a coloured heading reads as decoration.
            # Panel letters, black. The knee rate is no longer written here;
            # the dotted rule still marks it in all three panels of the column,
            # so THE CAPTION HAS TO SAY WHAT THAT RULE IS -- nothing on the
            # figure names it any more.
            axes[0][col].set_title(PANEL[tag], fontsize=8, color="black", pad=2)

        for col in range(len(ORDER)):
            axes[0][col].set_ylim(0, 105)
            axes[0][col].set_yticks([0, 50, 100])
            axes[1][col].set_ylim(0, 105)
            axes[1][col].set_yticks([0, 50, 100])
            # symlog keeps the zeros that chat holds for most of its sweep.
            axes[2][col].set_yscale("symlog", linthresh=1.0)
            axes[2][col].set_ylim(0, 4000)


        fig.tight_layout(w_pad=0.3, h_pad=0.4, pad=0.3)
        save(fig, os.path.join(HERE, "exp55_class_knees.pdf"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
