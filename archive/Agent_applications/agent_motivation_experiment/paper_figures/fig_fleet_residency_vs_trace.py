#!/usr/bin/env python3
"""Paper figure: does the fleet's holding follow the offered load?

  fleet_residency_vs_trace.pdf   3.335 x 2.05 in, `figure`, width=\\columnwidth
  fleet_residency_vs_trace.csv   the values drawn

ONE PANEL, TWO AXES. The left axis is the arrival rate of the hour-long trace,
drawn as a filled area because it is the same series for every arm -- one trace,
replayed three times. The right axis is, for each of PolyServe, Llumnix and
FluidServe, THE NUMBER OF REQUESTS THE WHOLE FLEET WAS HOLDING: the mean
concurrent requests on an instance, summed over the four instances and over the
three classes. The question the panel answers is which policy's holding follows
the shape of the offered load.

⚠ RESIDENCY IS NOT THROUGHPUT, and Little's law is why the two axes can be read
against each other at all: mean concurrency = admitted arrival rate x mean time
in the system. A policy whose residency tracks the arrival curve is one whose
time-in-system stayed roughly constant as load moved. A policy whose residency
keeps climbing while arrivals fall is accumulating work it has not finished, and
a policy whose residency is flat across a rising arrival curve is either
rejecting the increase or holding a queue that is already at its ceiling. None
of those three is visible in an attainment number.

⚠ THE ARMS ADMIT DIFFERENT AMOUNTS, so the levels are not comparable as a
ranking of "how much work was done" -- a lower line can mean faster completion
or heavier rejection, and the rejection rate is printed per arm below and
belongs in the caption. What IS comparable is the SHAPE, which is what the
panel is for, and the correlation with the arrival series is printed for each.

⚠ REQUESTS WITH NO RECORDED END ARE HELD TO THE END OF THE RUN by
`engine_class_occupancy.occupancy`, which is right (they were on that engine)
but inflates the last minutes of exactly the arms that build a backlog. The
share is printed per arm; read the final two or three minutes with it in view.

DATA. EXP-109 (2026-08-31/09-01), the one-hour mixture-shift trace on four
instances of Llama-3.1-70B at the standard budgets (chat 5 s / 50 ms,
agent 7 s / 75 ms, deep research 10 s / 100 ms). The line is repeat 1 and the
band is the min..max of the two repeats. Residency comes from
`results/aggregate_analysis/class_mix/hour_engine_mix.csv`; all three arms
attribute 100% of their admitted requests to an engine, so no arm's line is
scaled down by a lost dispatch log. The arrival series is the trace file itself,
`traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv`, not any one run's
arrivals, because every arm replays it and the figure is about one shape.

    python3 paper_figures/fig_fleet_residency_vs_trace.py
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.patheffects as pe  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402

MIX = os.path.join(ROOT, "results", "aggregate_analysis", "class_mix",
                   "hour_engine_mix.csv")
TRACE = os.path.join(ROOT, "traces", "dynamic", "canonical",
                     "dyn60_shift_m2Am1B_b1045.csv")
# Colour and dash per arm are the ones `two_models_hour_reqgoodput.pdf` uses, so
# a reader moving between the figures never has to relearn either.
ARMS = [("polyservept75", "PolyServe", "#41b6c4", "-."),
        ("slot75", "Llumnix", "#a1dab4", "--"),
        ("fsv3capgnofrct75", "FluidServe", "#253494", "-")]
TRACE_COLOR = "#b0b0b0"
FIG_W, FIG_H = ps.COL_W, 1.88
TOP_BAND = 0.27          # inches reserved above the axes for the one-row key
XMAX = 60.0
# A 0.8 pt line in #a1dab4 is hard to see on white, so every arm line carries a
# thin grey stroke underneath: the fill colour stays exactly the one asked for.
STROKE = [pe.Stroke(linewidth=1.5, foreground="#8c8c8c"), pe.Normal()]


def arrivals(kind="rate", roll=5):
    """(minutes, series) of the offered load, as a rate or as its own CV.

    `rate` is the arrival rate per minute, in requests per second.

    `cv` is the COEFFICIENT OF VARIATION OF THAT RATE OVER TIME, in a centred
    window of `roll` minutes: the standard deviation of the per-minute rate
    inside the window divided by its mean. It says how much the offered load
    moves from minute to minute around its own local level, which is a property
    of the trace and identical for every arm.

    ⚠ THIS IS A CV OVER TIME AND THE ARMS' CV IS ACROSS INSTANCES. Both are
    dimensionless and that is what lets them share one axis, but they are
    variability of different things: the trace's is "how unsteady was the load
    this minute", each arm's is "how unevenly was that load spread over the four
    instances". A caption that calls both "variability" without saying of what
    invites the reader to treat the grey area as a baseline the arms should
    beat, and it is not one.
    """
    t = pd.read_csv(TRACE, usecols=["arrival_s"])["arrival_s"].to_numpy(float)
    edges = np.arange(0.0, XMAX * 60.0 + 60.0, 60.0)
    n, _ = np.histogram(t, bins=edges)
    rate = n / 60.0
    if kind == "rate":
        return edges[:-1] / 60.0, rate
    r = pd.Series(rate)
    w = r.rolling(roll, center=True, min_periods=roll)
    cv = (w.std(ddof=0) / w.mean()).to_numpy(float)
    return edges[:-1] / 60.0, cv


def residency(stat, roll_min):
    """{label: (minutes, repeat-1 series, lo, hi, run, n_repeats)}.

    The table is one row per (window, engine, class). Classes are summed first,
    so every statistic below is computed on ONE NUMBER PER INSTANCE PER MINUTE:
    how many requests that instance was holding.

      `sum`  the four instances added up -- what the whole fleet holds.
      `var`  the population variance ACROSS THE FOUR INSTANCES in that minute.
             This is a spread, not a level: it is large when one instance holds
             much more than the others and zero when all four hold the same,
             whatever the level. ⚠ IT GROWS WITH THE LEVEL BY CONSTRUCTION --
             doubling every instance's holding quadruples the variance -- so a
             policy that simply holds more will show more variance without
             being any less even. `cv` is the same spread with the level
             divided out, and is the one to read when the arms hold different
             amounts, which they do here.
      `std`  the square root of `var`, in requests rather than requests squared.
      `cv`   that standard deviation divided by the mean across instances.
      `rollvar` the variance OVER TIME of the fleet total, in a centred window
             of `roll_min` minutes. This answers a different question from the
             three above: not "are the instances uneven" but "how much does the
             fleet's holding move from minute to minute".
    """
    d = pd.read_csv(MIX)
    out = {}
    for arm, label, _c, _ls in ARMS:
        sub = d[d["arm"] == arm]
        if sub.empty:
            print(f"!! {label}: no rows in {os.path.basename(MIX)}",
                  file=sys.stderr)
            continue
        grid = np.arange(0.0, XMAX * 60.0, 60.0)
        per = {}
        for run, g in sub.groupby("run"):
            # one column per instance, one row per window
            piv = (g.groupby(["win_start_s", "engine_port"])["resident"].sum()
                    .unstack("engine_port").reindex(grid))
            v = piv.to_numpy(float)
            if stat == "sum":
                y = np.nansum(v, axis=1)
            elif stat == "rollvar":
                tot = np.nansum(v, axis=1)
                y = pd.Series(tot).rolling(roll_min, center=True,
                                           min_periods=roll_min).var(ddof=0)
                y = y.to_numpy(float)
            else:
                m = np.nanmean(v, axis=1)
                var = np.nanmean((v - m[:, None]) ** 2, axis=1)
                y = {"var": var, "std": np.sqrt(var),
                     "cv": np.sqrt(var) / np.where(m > 0, m, np.nan)}[stat]
            y = np.where(np.isfinite(y), y, np.nan)
            # A window in which no instance held anything is not a zero-variance
            # window, it is a window with no measurement.
            y[np.all(~np.isfinite(v), axis=1)] = np.nan
            per[run] = y
        runs = sorted(per)                       # r1 sorts before r2 by date
        mat = np.vstack([per[r] for r in runs])
        out[label] = (grid / 60.0, per[runs[0]], np.nanmin(mat, axis=0),
                      np.nanmax(mat, axis=0), runs[0], len(runs))
    return out


def rejection(run):
    """(rejected share, unfinished share) of that run's arrivals, in percent."""
    m = pd.read_csv(os.path.join(ROOT, "results", run, "metrics.csv"),
                    usecols=["agent", "is_rejected", "is_server_terminated"],
                    low_memory=False)
    m = m[m["agent"] != "job_summary"]
    t = lambda c: m[c].astype(str).str.lower().isin(["true", "1"])  # noqa: E731
    rej, cut = t("is_rejected"), t("is_server_terminated")
    n = float(len(m))
    return 100 * rej.sum() / n, 100 * (cut & ~rej).sum() / n


# ⚠ SHORT LABELS ON PURPOSE. A rotated right-axis label is drawn OUTSIDE the
# axes, and `tight_layout` fits the axes to the canvas without knowing the label
# will be longer than the panel is tall, so a long one is silently cut off at
# the canvas edge. "Coeff. of Variation Across Instances" lost its first letter
# that way. The word `Instance` carries the across-instances meaning and the
# units say the rest; the definition belongs in the caption. The fit is CHECKED
# at the end of `main` rather than trusted.
YLABEL = {"sum": "Requests in Fleet",
          "var": "Instance Variance (req$^2$)",
          "std": "Instance Std. Dev. (req)",
          "cv": "Coeff. of Variation",
          "rollvar": "Fleet Variance (req$^2$)"}
OUTNAME = {"sum": "fleet_residency_vs_trace",
           "var": "fleet_residency_var_vs_trace",
           "std": "fleet_residency_std_vs_trace",
           "cv": "fleet_residency_cv_vs_trace",
           "rollvar": "fleet_residency_rollvar_vs_trace"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--y", default="sum",
                    choices=["sum", "var", "std", "cv", "rollvar"],
                    help="what the right axis carries (see `residency`)")
    ap.add_argument("--roll-min", type=int, default=5,
                    help="window in minutes for --y rollvar")
    ap.add_argument("--arrival", default="rate", choices=["rate", "cv"],
                    help="draw the offered load as its rate or as its own "
                         "coefficient of variation over time")
    ap.add_argument("--arrival-roll", type=int, default=5,
                    help="window in minutes for --arrival cv")
    a = ap.parse_args()
    x, rate = arrivals(a.arrival, a.arrival_roll)
    # Both series dimensionless: one axis, and no second scale to misread.
    one_axis = (a.arrival == "cv" and a.y == "cv")
    res = residency(a.y, a.roll_min)
    if not res:
        sys.exit(f"no residency rows in {MIX}")

    rows = []
    fin = np.isfinite(rate)
    unit = "req/s" if a.arrival == "rate" else f"CV over {a.arrival_roll} min"
    print(f"offered load ({unit}): min {rate[fin].min():.2f}, "
          f"mean {rate[fin].mean():.2f}, max {rate[fin].max():.2f} "
          f"over {fin.sum()} minutes")
    for _arm, label, _c, _ls in ARMS:
        if label not in res:
            continue
        mins, y, lo, hi, run, n = res[label]
        ok = np.isfinite(y) & np.isfinite(rate)
        r = float(np.corrcoef(rate[ok], y[ok])[0, 1])
        rj, cut = rejection(run)
        print(f"  {label:11s} {n} repeat(s)  mean {np.nanmean(y):9.1f}"
              f"  min {np.nanmin(y):8.1f}  max {np.nanmax(y):9.1f}  "
              f"r(arrivals) {r:+.3f}   rejected {rj:4.1f}%  unfinished {cut:4.1f}%")
        for m_, v, l_, h_ in zip(mins, y, lo, hi):
            rows.append(dict(arm=label, run=run, minute=m_, resident=v,
                             resident_lo=l_, resident_hi=h_, stat=a.y,
                             offered=rate[int(m_)] if m_ < len(rate) else np.nan,
                             offered_kind=a.arrival,
                             pearson_r_with_arrivals=r, rejected_pct=rj,
                             unfinished_pct=cut, repeats=n))

    with plt.rc_context(ps.STYLE):
        fig, ax = plt.subplots(figsize=(FIG_W, FIG_H))
        # The trace is drawn first and filled, so the three policy lines read as
        # figures against it rather than as a fourth series of the same kind.
        ax.fill_between(np.append(x, XMAX), np.append(rate, rate[-1]),
                        step="post", color=TRACE_COLOR, alpha=0.45, lw=0)
        ax.step(np.append(x, XMAX), np.append(rate, rate[-1]), where="post",
                color="#8c8c8c", lw=0.7)
        ax.set_xlabel("Time (minutes)")
        ax.set_xlim(0, XMAX)
        ax.set_xticks([0, 15, 30, 45, 60])
        ax.grid(axis="y", **ps.GRID)
        ax.set_axisbelow(True)
        if one_axis:
            # ⚠ THE LIMIT IS SET AFTER THE ARMS ARE DRAWN, at the end of this
            # block. `set_ylim` turns autoscaling off, so setting it here --
            # with only the offered-load area drawn -- fixed the top at that
            # series' 0.58 and CUT EVERY ARM LINE ABOVE IT without any error.
            ax.set_ylabel("Coeff. of Variation")
        else:
            ax.set_ylabel("Arrival Rate (req/s)")
            ax.set_ylim(0, float(np.ceil(np.nanmax(rate) / 10.0) * 10))

        ax2 = ax if one_axis else ax.twinx()
        handles = [Patch(facecolor=TRACE_COLOR, alpha=0.45,
                         edgecolor="#8c8c8c", linewidth=0.5)]
        labels = ["Offered load" if a.arrival == "rate"
                  else f"Offered load ({a.arrival_roll} min)"]
        for _arm, label, colour, ls in ARMS:
            if label not in res:
                continue
            mins, y, lo, hi, _run, n = res[label]
            if n > 1:
                ax2.fill_between(mins, lo, hi, color=colour, alpha=0.18, lw=0)
            line, = ax2.plot(mins, y, color=colour, ls=ls, lw=0.9,
                             path_effects=STROKE)
            handles.append(line)
            labels.append(label)
        if one_axis:
            ax.relim()
            ax.autoscale(axis="y")
            ax.set_ylim(bottom=0)
        else:
            ax2.set_ylabel(YLABEL[a.y])
            ax2.set_ylim(0, None)
        for side in ("top",):
            ax.spines[side].set_visible(False)
            ax2.spines[side].set_visible(False)

        # ONE ROW, AND THE ORDER IS THE ORDER OF THE READING. With `ncol=2`
        # matplotlib fills column-major, so a two-row key of four entries reads
        # across as `Offered load, Llumnix / PolyServe, FluidServe` -- the two
        # policies that are being compared land in different rows and the eye
        # pairs the wrong ones. Four entries fit in one row at 7 pt with the
        # handles trimmed, and the fit is CHECKED below rather than assumed.
        # The type size is FITTED, not chosen: the entries change with `--y`
        # and `--arrival` (one of them carries a window length), and a key that
        # is 0.07 in too wide loses a whole entry off the canvas rather than
        # wrapping. Largest size that fits, down to 6 pt.
        for fs in (7.0, 6.6, 6.2, 6.0):
            key = fig.legend(handles, labels, loc="lower center", ncol=4,
                             bbox_to_anchor=(0.5, 1.0 - TOP_BAND / FIG_H),
                             frameon=False, fontsize=fs, columnspacing=0.7,
                             handlelength=1.4, handletextpad=0.35,
                             borderaxespad=0.0)
            fig.canvas.draw()
            kb = key.get_window_extent(fig.canvas.get_renderer())
            if kb.width <= fig.dpi * FIG_W - 2.0:
                break
            key.remove()
        fig.tight_layout(rect=(0, 0, 1, 1.0 - TOP_BAND / FIG_H), pad=0.35)
        fig.canvas.draw()
        rend = fig.canvas.get_renderer()
        for name, lab in (("left", ax.yaxis.label), ("right", ax2.yaxis.label)):
            bb = lab.get_window_extent(rend)
            if bb.x0 < -0.5 or bb.x1 > fig.dpi * FIG_W + 0.5:
                print(f"!! the {name} y-axis label is cut off by the canvas: "
                      f"{lab.get_text()!r}", file=sys.stderr)
        kb = key.get_window_extent(fig.canvas.get_renderer())
        if kb.x0 < 1.0 or kb.x1 > fig.dpi * FIG_W - 1.0:
            print(f"!! key is {kb.width / fig.dpi:.2f} in wide on a "
                  f"{FIG_W:.2f} in canvas -- it is being cut off",
                  file=sys.stderr)
        stem = OUTNAME[a.y] + ("_arrcv" if a.arrival == "cv" else "")
        ps.save(fig, os.path.join(HERE, stem + ".pdf"))
    out = os.path.join(HERE, stem + ".csv")
    pd.DataFrame(rows).to_csv(out, index=False)
    print(f"wrote {os.path.basename(out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
