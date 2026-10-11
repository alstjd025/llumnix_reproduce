#!/usr/bin/env python3
"""Paper figure: five control planes on the hour-long mix-shift trace, with the
agent class promised per token.

  exp109_hour.pdf          7.0 x 1.70 in, `figure*`, width=\\textwidth
  exp109_hour_4panel.pdf   7.0 x 1.75 in, the same with throughput added
  exp109_hour_five.pdf     all five arms, and therefore a much shorter x axis
  each with a `.csv` of the same basename holding the plotted series

This replaces `exp71_hour.pdf`, and it is NOT a redraw of it: three things
differ and each on its own would forbid putting a number from one beside a
number from the other.

  THE TRACE.    `exp71_hour.pdf` runs `dyn60_short_m123_b1045.csv`, whose class
                mix steps m1 -> m2 -> m3 -> m1. This is the MIX-SHIFT trace
                `dyn60_shift_m2Am1B_b1045.csv`: same arrival times to the byte,
                same 10.8-45.0 req/s band, but the mix steps m2 -> A -> m1 -> B,
                so chat goes 93.0 -> 33.3 -> 76.9 -> 60.0% of arrivals. The
                middle segment is an EVEN mix, which the older trace never
                approaches, and it is where the fleet's work per request is
                about four times what it is in the first segment.
  THE PROMISE.  The agent class is promised TTFT 7 s + 75 ms per token here and
                an end-to-end 30 s there. Every arm that reads the promise was
                TOLD this one (FluidServe through
                `--fluidserve-class-budgets 25:decode:75`, the other four
                through `slo.swe.tbt_ms = 75`), and scoring matches.
  THE SYSTEM.   FluidServe here is v0.4 (binary `6dc9f035`); there it was v0.2.

⚠ WHY THERE ARE TWO ARM SETS, AND WHY THE FIVE-ARM ONE STOPS AT 26 MINUTES.
A request still in flight when the hour ends leaves BOTH denominators, so the
final windows of a backlogged arm keep only the requests that finished -- the
fast ones -- and read far too high. Measured per run: the four arms that reject
or hold stay under 20% in flight until minute 60, and the vLLM router, which
refuses nothing, crosses 20% at minute 26 and is at 100% from minute 58. Every
arm in one figure has to be cut at the same minute or the panels show different
stretches of the hour, so:

  exp109_hour.pdf / _4panel.pdf   the four arms that reject or hold, 0-60 min
  exp109_hour_five.pdf            all five, 0-26 min -- the only stretch in
                                  which the five can be compared at all

THAT THE TWO CUTS ARE 60 AND 26 IS ITSELF THE RESULT, not a reason to give the
arms different x extents.

THE PANELS. Throughput is every output token the engines emitted, whether or not
its request met its rule or even finished. Token goodput is the tokens of
requests that met their rule. The two attainment panels are one measurement on
two populations and the distance between them is the rejection rate; neither
alone supports a ranking, which is why both are drawn.

DATA. EXP-109 (2026-09-01/02), repeat 2 of two, one run per arm, chosen by
`/home/nxclab/tools/pick_usable_run.sh` -- merged, and end-to-end within 90
minutes. That selection is not cosmetic: `results/*exp109r1_vllmcachet75_shift`
matches five directories, of which one is a 293-minute run that never
terminated and three are header-only remains, and `ls | head -1` picks the
oldest. The whole-hour numbers of both repeats are in
`experiments/EXP-109_per-token-swe-hour-trace.md`.

⚠ SCORED WITH THE REPOSITORY'S STANDING RULE, not the token-level cumulative
deadline used by `pace_and_outcome_35.pdf` and
`motivation_throughput_vs_goodput_4panel_t75.pdf`: a request meets its class
rule when its first token is inside the class budget AND its mean per-token time
is inside it. The cumulative rule needs per-token arrival events, and at about
5 GB per hour-long run for ten runs that is a separate pass; EXP-109 section 4
reports the whole-hour figures under it. NO NUMBER HERE MAY BE QUOTED BESIDE ONE
FROM THOSE TWO FIGURES.

    python3 paper_figures/fig_exp109_hour.py [--arms four|five]
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

# The agent class's promise is part of the scoring and has to be fixed BEFORE
# exp22_fluidserve is imported, because that module reads it at import time.
os.environ.setdefault("FS_SWE_TBT_MS", "75")
os.environ.setdefault("FS_SWE_TTFT_S", "7")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import TEXT_W, STYLE, GRID, ARM_COLOR, kfmt, save  # noqa: E402
from exp41_dynamic_timeline import windows, WIN  # noqa: E402

# Label, run directory, line style. The directories are named rather than
# globbed: `results/*exp71*_fspfx_fullb` matches BOTH passes, and this figure is
# one pass. The arm names and the paper names disagree on purpose -- `fspfx` is
# FluidServe v0.2, which is the deployed default, while a directory named
# `fluidserve` is the ablation with prefix accounting turned off. llm-d ran from
# a different driver, hence the `full` variant where the others carry `fullb`;
# all four replay the same trace file and see the same 98,200 arrivals.
# The order is the paper-wide convention: vLLM, PolyServe, Llumnix SLO, llm-d,
# FluidServe, ours last. It sets the legend order. Repeat 2 of two, to match the
# convention of the figure this replaces, which drew one pass; the other repeat
# and the whole-hour numbers of both are in the experiment file.
SERIES = [
    ("vLLM", "results/260901_2010_exp109r2_vllmcachet75_shift",
     ARM_COLOR["vllmrouter"], (0, (4, 1, 1, 1, 1, 1))),
    ("PolyServe", "results/260901_1742_exp109r2_polyservept75_shift",
     ARM_COLOR["polyserve"], "-."),
    ("Llumnix SLO", "results/260901_1856_exp109r2_slot75_shift",
     ARM_COLOR["slo"], "--"),
    ("llm-d", "results/260901_1637_exp109r2_llmdslot75_shift",
     ARM_COLOR["llmd"], (0, (6, 1.5))),
    ("FluidServe", "results/260901_1514_exp109r2_fsv3capgnofrct75_shift",
     ARM_COLOR["fluidserve"], "-"),
]
# The arm the five-arm set adds and the four-arm set drops. It is dropped for a
# stated reason -- it is the only arm whose timeline stops being readable before
# the hour is out -- and never because of where it lands.
NO_ADMISSION = "vLLM"

# Marker per arm for the `--markers` variant, the same assignment
# `motivation_throughput_vs_goodput_4panel_t75.pdf` uses, so one arm is one
# shape wherever it is drawn. The sizes are not uniform: a triangle carries
# about half the ink of a square of the same side and a pentagon needs the extra
# size to be told from the circle at all.
MARKERS = {"vLLM": ("p", 2.6), "PolyServe": ("o", 1.8),
           "Llumnix SLO": ("^", 2.4), "llm-d": ("D", 1.6),
           "FluidServe": ("s", 1.8)}
# One marker every MARK_EVERY points. A timeline carries about 118 points per
# series at 30 s steps, and a marker on each of them is a solid band that hides
# the shape the figure exists to show -- which is why the default version of
# this figure has no markers at all. Marking one point in ten leaves about a
# dozen per curve, enough to identify a series where it crosses another and
# few enough to still read as a line.
MARK_EVERY = 10

MIN_IN_WINDOW = 30              # same floor as the source timeline figure
MIX_BOUNDARIES = [15, 30, 45]   # the trace steps its mix every 15 minutes
FIG_H = 1.70
MAX_CUTOFF = 0.20               # see the RUN-BOUNDARY note in the docstring
END_TRIM_MIN = 2.0              # the tail every arm loses to the trace ending
HOUR_MIN = 60.0                 # the hour the trace covers; the x axis spans it

# Two panel sets from one collection pass. `PANELS3` is the figure as it was;
# `PANELS4` puts throughput first, so the reader sees what the engines produced
# before what any of it was worth. Each entry is (key, y label, title stem); the
# (a)/(b)/... letters are attached at draw time so the same stem can be second
# in one figure and third in the other.
PANELS3 = [("gp", "Goodput token (t/s)", "Token goodput"),
           ("adm", "SLO attainment (%)", "Request SLO (admitted)"),
           ("off", "SLO attainment (%)", "Request SLO (offered)")]
# The two attainment titles are shortened for this one. At four panels each is
# 1.75 in wide and "(d) Request SLO (offered)" is wider than that, so the label,
# which `tight_layout` centres on its axes, ran off the right edge of the
# canvas. Three panels give 2.33 in each and the long form fits.
# The five-arm set. Every panel here is immune to the run-boundary artifact, so
# it needs no cut: tokens emitted are tokens emitted, and the attainment panel
# counts an unfinished request as the violation it is instead of dropping it.
# The five-arm sets. Goodput first because it is the quantity the figure claims,
# then the request-level view of the same question, then what the engines were
# producing while that happened -- the order asked for on 2026-09-03.
PANELS_ALL = [("gp", "Goodput token (t/s)", "Token goodput"),
              ("all", "SLO attainment (%)", "Request SLO (all arrivals)"),
              ("thru", "Tokens/s", r"Throughput")]
PANELS_ADM = [("gp", "Goodput token (t/s)", "Token goodput"),
              ("adm", "SLO attainment (%)", "Request SLO (admitted)"),
              ("thru", "Tokens/s", r"Throughput")]
PANELS4 = [("thru", "Tokens/s", r"Throughput"),
           ("gp", "Goodput token (t/s)", "Token goodput"),
           ("adm", "SLO attainment (%)", "SLO (admitted)"),
           ("off", "SLO attainment (%)", "SLO (offered)")]
# The panel titles were set bold through mathtext until 2026-09-03 and are now
# plain, at the same weight as every other label on the page. `MATH_SERIF` stays
# because it is passed to the rc context and costs nothing; with no mathtext
# left in the figure it selects a font that nothing uses.
MATH_SERIF = {"mathtext.fontset": "dejavuserif"}


def engine_throughput(run, grid_min, t0):
    """Fleet output tokens per second, from the ENGINES' own counter.

    ⚠ THIS REPLACES A CLIENT-SIDE QUANTITY THAT SHARES ITS NAME. Summing each
    request's `output_tokens` into the window the request ARRIVED in is not the
    rate the engines were producing at that minute: a request that arrives at
    minute 40 and generates for twenty minutes puts all of its tokens at minute
    40, and one that never finishes puts a truncated count there. For an arm
    with a small backlog the two agree; for one where half the arrivals never
    finish they do not, and the panel would read as a collapse in production
    where there was a collapse in ATTRIBUTION. `vllm:generation_tokens_total`
    is a monotone counter on each engine, so the difference between two scrapes
    divided by their spacing is the rate at that moment, whoever the tokens
    belonged to.

    `t0` is the origin of the client's `rel` axis, so both series land on one
    clock.
    """
    total = np.zeros_like(grid_min, dtype=float)
    found = 0
    for f in sorted(glob.glob(os.path.join(run, "server_metrics",
                                           "engine_*.jsonl"))):
        t, g = [], []
        for line in open(f):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            v = next((o[k] for k in o
                      if k.startswith("vllm:generation_tokens_total")
                      and o[k] is not None), None)
            if v is None or o.get("t") is None:
                continue
            t.append(float(o["t"]))
            g.append(float(v))
        if len(t) < 3:
            continue
        t = np.array(t)
        rate = np.diff(np.array(g)) / np.maximum(np.diff(t), 1e-9)
        mid = ((t[1:] + t[:-1]) / 2.0 - t0) / 60.0
        total += np.interp(grid_min, mid, rate, left=np.nan, right=np.nan)
        found += 1
    if not found:
        return None
    return total


VERDICTS = os.path.join(ROOT, "results", "aggregate_analysis", "ladder95",
                        "verdicts")


def series(run):
    """Per-window attainment on three denominators, and token goodput.

    ⚠ THE RULE IS NOT COMPUTED HERE. `deadline_ladder_attainment.py` owns it and
    writes one verdict per request; this function windows those verdicts by
    ARRIVAL time and counts them. Token goodput is therefore the ladder's:
    tokens that met their own deadline, per second, counted whether or not the
    request they belonged to cleared the 95% bar.
    """
    name = os.path.basename(run)
    vpath = os.path.join(VERDICTS, name + ".csv")
    if not os.path.exists(vpath):
        print(f"no verdict file for {name}; run deadline_ladder_attainment.py "
              f"--dump-verdicts first", file=sys.stderr)
        return None
    r = pd.read_csv(vpath)
    if r.empty:
        return None
    dur = r["rel"].max()
    col = {k: [] for k in ("x", "adm", "off", "all", "gp", "thru", "cut")}
    for t, g in windows(r, dur):
        if len(g) < MIN_IN_WINDOW:
            continue
        live = g[~g["cutoff"]]
        served = live[~live["rejected"]]
        ok = g["ladder_ok"] & ~g["rejected"] & ~g["errored"]
        col["x"].append(t)
        col["cut"].append(float(g["cutoff"].mean()))
        col["adm"].append(100.0 * float(served["ladder_ok"].mean())
                          if len(served) else np.nan)
        col["off"].append(100.0 * float(ok[~g["cutoff"]].mean())
                          if len(live) else np.nan)
        # Every request that ARRIVED in this window is in the denominator and
        # one still in flight when the hour ended is a violation rather than a
        # row removed from both sides.
        col["all"].append(100.0 * float((ok & ~g["cutoff"]).mean()))
        col["gp"].append(float((g["n_tokens"] - g["n_late"]).sum()) / WIN)
        col["thru"].append(float(g["n_tokens"].sum()) / WIN)
    out = {k: np.array(v, dtype=float) for k, v in col.items()}
    mp = os.path.join(run, "metrics.csv")
    t0 = float(pd.read_csv(mp, usecols=["start_time"])["start_time"].min())
    eng = engine_throughput(run, out["x"], t0)
    if eng is not None and np.isfinite(eng).sum() > 0.5 * len(eng):
        out["thru"] = eng
    else:
        print(f"!! {name}: no usable engine counter; panel (a) falls back to "
              f"tokens attributed to the arrival window, which is a different "
              f"quantity", file=sys.stderr)
    return out | {"verdicts": r}


def build(data, dur, panels, out, fig_h=None, markers=False):
    n = len(panels)
    with plt.rc_context({**STYLE, **MATH_SERIF}):
        fig, ax = plt.subplots(1, n, figsize=(TEXT_W, fig_h or FIG_H))

        handles, labels = [], []
        for lab, s, c, ls in data:
            # No markers: about 110 points per line at 90 s steps would draw as
            # a solid band and hide the shape the figure exists to show.
            h = None
            for i, (key, _, _) in enumerate(panels):
                # 0.8 pt rather than 1.1: five series over about 110 points
                # each cross often, and a thinner line keeps the crossings
                # readable instead of merging them into a band.
                mk = {}
                if markers and lab in MARKERS:
                    shape, msz = MARKERS[lab]
                    mk = dict(marker=shape, ms=msz, markevery=MARK_EVERY)
                line, = ax[i].plot(s["x"], s[key], color=c, ls=ls, lw=0.8,
                                   **mk)
                h = h or line
            handles.append(h)
            labels.append(lab)

        att = [i for i, (k, _, _) in enumerate(panels) if k in ("adm", "off")]
        tok = [i for i, (k, _, _) in enumerate(panels) if k in ("gp", "thru")]
        for i in att:
            ax[i].set_ylabel(panels[i][1])
            ax[i].set_ylim(0, 105)
            ax[i].set_yticks([0, 25, 50, 75, 100])
        # The two attainment panels share the axis explicitly rather than by
        # coincidence: the distance between a policy's two curves is the
        # figure's second claim, and it can only be read off on one scale.
        for i in att[1:]:
            ax[i].sharey(ax[att[0]])
        # Throughput and goodput share theirs for the same reason -- the whole
        # point of showing both is the gap between them, and two panels on
        # different scales cannot be subtracted by eye.
        top = max(s[k].max() for _, s, _, _ in data for k in ("thru", "gp")
                  if any(kk == k for kk, _, _ in panels))
        for i in tok:
            ax[i].set_ylabel(panels[i][1])
            ax[i].set_ylim(0, top * 1.08)
            ax[i].yaxis.set_major_formatter(kfmt())

        for i in range(n):
            # The panel title is the SECOND LINE of the x label rather than a
            # text box in axes coordinates, because `tight_layout` reserves room
            # for an axis label and knows nothing about a hand-placed artist.
            title = "(%s) %s" % ("abcd"[i], panels[i][2])
            ax[i].set_xlabel(f"Time (minutes)\n{title}",
                             labelpad=1.5, linespacing=1.6)
            # The axis spans the hour the trace covers, not the last window
            # that survived trimming. The three versions of this figure stop
            # drawing at 59.2, 59.2 and 57.8 minutes for reasons that have
            # nothing to do with each other -- warmup and drain trimming, the
            # window being plotted at its centre, and the in-flight rule -- and
            # putting each of those on its own axis made three figures of the
            # same hour with three different x extents, which reads as three
            # different runs. The short blank at the right edge is where the
            # trimming is; the README says how much and why.
            ax[i].set_xlim(0, HOUR_MIN)
            ax[i].set_xticks(list(range(0, int(HOUR_MIN) + 1, 15)))
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
            for m in MIX_BOUNDARIES:
                ax[i].axvline(m, color="#999999", lw=0.5, ls=":", zorder=0)

        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 0.872), frameon=False,
                   columnspacing=1.4, handlelength=2.0, handletextpad=0.5,
                   borderaxespad=0.0)
        # The bottom of `rect` was -0.03, below the canvas: mathtext reports a
        # box taller than the glyphs it draws, so the bold panel titles needed
        # the allowance taken back. With the titles set plain that allowance is
        # gone and a negative bottom cuts the titles in half -- 0.0 is what a
        # plain second line of an x label needs.
        fig.tight_layout(rect=(0, 0.0, 1, 0.878), w_pad=1.6, pad=0.25)
        save(fig, out)


def write_csv(data, dur, out_csv, arms_label):
    """The series the panels draw, on the window grid they are drawn on."""
    rows = []
    for lab, s, _, _ in data:
        for i, t in enumerate(s["x"]):
            rows.append(dict(arm=lab, arm_set=arms_label, minute=float(t),
                             throughput_tok_s=float(s["thru"][i]),
                             goodput_tok_s=float(s["gp"][i]),
                             attainment_admitted_pct=float(s["adm"][i]),
                             attainment_offered_pct=float(s["off"][i]),
                             attainment_all_arrivals_pct=float(s["all"][i]),
                             in_flight_at_end_frac=float(s["cut"][i]),
                             cut_at_minute=float(dur)))
    df = pd.DataFrame(rows)
    df.to_csv(out_csv, index=False, float_format="%.4f")
    print(f"wrote {out_csv}  ({len(df)} rows = {df['arm'].nunique()} arms x "
          f"{df['minute'].nunique()} windows)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--markers", action="store_true",
                    help="also give every arm its own marker shape, one point "
                         "in ten, and write the result under `_marked`")
    ap.add_argument("--arms", default="four", choices=["four", "five"],
                    help="four drops the arm with no admission control")
    ap.add_argument("--denom", default="all", choices=["all", "admitted"],
                    help="five-arm set only. `all` counts an unfinished request "
                         "as a violation and needs no cut; `admitted` drops it "
                         "from the denominator and therefore does")
    args = ap.parse_args()
    wanted = [x for x in SERIES
              if args.arms == "five" or x[0] != NO_ADMISSION]

    raw = []
    for lab, run, c, ls in wanted:
        d = os.path.join(ROOT, run)
        if not os.path.isdir(d):
            print(f"missing run directory {run}", file=sys.stderr)
            return 1
        s = series(d)
        if s is None:
            print(f"no rows for {run}", file=sys.stderr)
            return 1
        v, cut = s["verdicts"], s["cut"]
        live = v[~v["cutoff"]]
        served = live[~live["rejected"]]
        okl = live["ladder_ok"] & ~live["rejected"] & ~live["errored"]
        print(f"{lab:12s} {len(v):6d} arrivals, {len(s['x']):3d} windows, "
              f"whole-hour offered {100.0 * okl.mean():5.1f}%, "
              f"admitted {100.0 * served['ladder_ok'].mean():5.1f}%, "
              f"rejected {100.0 * live['rejected'].mean():4.1f}%, "
              f"max cutoff share {100.0 * cut.max():5.1f}%")
        raw.append((lab, s, c, ls))

    # One cut point for every arm, so the panels stay directly comparable: the
    # earliest time at which ANY arm's window crosses MAX_CUTOFF. Giving the
    # arms different x extents would itself invite a wrong reading.
    full = max(s["x"].max() for _, s, _, _ in raw)
    if args.arms == "five" and args.denom == "all":
        # No 20% cut here. On the all-arrivals denominator a request still in
        # flight is a violation rather than a row removed from both sides, so
        # the artifact the cut exists for is not present. The only trim is the
        # end of the trace itself: a request arriving in the last minutes
        # cannot finish inside the run whatever the policy does, and that
        # depresses EVERY arm's last windows equally.
        dur = min(s["x"].max() for _, s, _, _ in raw) - END_TRIM_MIN
        print(f"five-arm set, all-arrivals denominator: no in-flight cut "
              f"needed; trimmed the last {END_TRIM_MIN:.1f} min, to "
              f"{dur:.1f} min")
    else:
        # The 20% in-flight rule is a PROXY for one thing: the last windows of a
        # backlogged arm keep only the requests that finished, so if those met
        # their rule the admitted curve is flattered. The proxy is computed over
        # the arms that have admission control. The arm without it crosses 20%
        # at minute 26, which would shorten the axis to a quarter of the hour --
        # so instead of trusting the proxy for that arm, the inflation it stands
        # for is MEASURED below, and the axis is only shortened if it is real.
        judged = [(lab, sv) for lab, sv, _, _ in raw if lab != NO_ADMISSION]
        ends = [sv["x"][sv["cut"] <= MAX_CUTOFF].max()
                if (sv["cut"] <= MAX_CUTOFF).any() else sv["x"].min()
                for _, sv in judged]
        dur = min(ends)
        for lab, sv, _, _ in raw:
            if lab != NO_ADMISSION:
                continue
            m = (sv["x"] > dur) if False else (sv["x"] >= 0)
            infl = np.nanmax(sv["adm"][m] - sv["all"][m])
            n_nan = int(np.isnan(sv["adm"][m]).sum())
            print(f"{lab}: admitted exceeds the all-arrivals value by at most "
                  f"{infl:.2f} points over the drawn axis, and is undefined in "
                  f"{n_nan} windows (nothing finished in them). The axis is NOT "
                  f"shortened for it.")
    if dur < full:
        who = [lab for lab, s, _, _ in raw if (s["cut"] > MAX_CUTOFF).any()]
        print(f"trimmed to {dur:.1f} min (from {full:.1f}); "
              f"{', '.join(who)} exceeded {MAX_CUTOFF:.0%} in-flight-at-end "
              f"beyond that")

    data = []
    for lab, s, c, ls in raw:
        keep = s["x"] <= dur
        data.append((lab, {k: v[keep] for k, v in s.items()
                           if k != "verdicts"}, c, ls))
    stem = "exp109_hour" + ("_five" if args.arms == "five" else "")
    if args.arms == "five":
        stem += "" if args.denom == "all" else "_admitted"
        stem += "_marked" if args.markers else ""
        build(data, dur, PANELS_ALL if args.denom == "all" else PANELS_ADM,
              os.path.join(HERE, stem + ".pdf"), markers=args.markers)
    else:
        stem += "_marked" if args.markers else ""
        build(data, dur, PANELS3, os.path.join(HERE, stem + ".pdf"),
              markers=args.markers)
        build(data, dur, PANELS4, os.path.join(HERE, stem + "_4panel.pdf"),
              fig_h=1.75, markers=args.markers)
    write_csv(data, dur, os.path.join(HERE, stem + ".csv"), args.arms)
    return 0


if __name__ == "__main__":
    sys.exit(main())
