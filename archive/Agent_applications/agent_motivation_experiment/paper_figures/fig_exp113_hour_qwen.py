#!/usr/bin/env python3
"""Paper figure: FluidServe v0.4 on the mix-shift hour, served by Qwen2.5-72B.

  exp113_hour_qwen.pdf   7.0 x 1.70 in, `figure*`, width=\\textwidth
  exp113_hour_qwen.csv   the series drawn, same basename

THE PANELS ARE `exp109_hour_five_admitted.pdf`'s, in that order and no other:
(a) token goodput, (b) request SLO on the admitted denominator, (c) throughput.
The two figures are read against each other -- same rule, same trace shape, same
arm in one of them -- so a reader must not have to re-learn which panel is which
between them. The offered denominator is not a panel here; it is in the CSV
beside this file as `attainment_offered_pct`.

THE ARM SET GROWS WITH THE EXPERIMENT. EXP-113 is the first run of this workload
on a model other than Llama-3.1-70B and it is measured one arm at a time, so an
arm that has not finished is skipped with a message naming it rather than
holding the figure back. A curve missing from this figure means that arm's run
had not been scored when it was drawn -- the stderr output says which -- and not
anything about the policy.

⚠ THIS IS NOT COMPARABLE WITH `exp109_hour.pdf`, AND THE REASON IS THE TRACE,
NOT THE MODEL. Qwen2.5-72B is the slower model of the two on this hardware, so
EXP-113 runs a THINNED arrival trace -- `dyn60_shift_m2Am1B_b1045_q064.csv`,
which is the same hour with 64% of the arrivals. Measured: 63,818 arrivals at a
mean of 17.4 req/s here against 99,242 at 27.1 req/s there. The mix schedule and
its 15-minute boundaries are the same (m2 -> A -> m1 -> B, chat 93.0 -> 33.3 ->
76.9 -> 60.0% of arrivals), and so is every class budget. TWO THINGS THEREFORE
DIFFER AT ONCE between the two figures, the model and the offered load, and no
difference between them can be attributed to either alone.

SCORING is the fixed rule, the same one every figure in this directory uses and
the same one `exp109_hour.pdf` is drawn under: token i of a request is on time
if it arrives within TTFT_SLO + i * TBT_SLO of the send, counting the first
token as i = 0; the request is on time if at least 95% of its tokens are; token
goodput counts the tokens that met their own deadline. Class budgets are chat
(5 s, 50 ms), deepresearch (10 s, 100 ms), swe (7 s, 75 ms) -- unchanged from
the Llama runs, which is a CHOICE and not a neutral one: a budget is a promise
to a user and does not move when the fleet's model does, but a model that
decodes at a different rate meets the same promise differently. The caption has
to say the budgets were not retuned.

⚠ ONE ARM MEANS THE REJECTION RATE IS NOT VISIBLE ON THE FIGURE. With five arms
the admitted panel is read against the others; with one there is nothing to read
it against, and the 18.6% of arrivals this arm refused over the hour appears
nowhere in the drawing. THE CAPTION HAS TO CARRY IT, per window if the claim
needs it -- the CSV has `attainment_offered_pct` beside `attainment_admitted_pct`
and the difference between them is that window's rejection.

DATA. EXP-113 (2026-09-03), one repeat, chosen by
`/home/nxclab/tools/pick_usable_run.sh`: three directories match the pattern and
two of them are empty remains of runs that never produced a metrics file.

    python3 paper_figures/fig_exp113_hour_qwen.py
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
# The arms EXP-113 has, in the paper-wide order (vLLM, PolyServe, Llumnix SLO,
# llm-d, FluidServe, ours last), with the same colours and dash patterns as
# `exp109_hour_five_admitted.pdf` so that one arm looks the same in both.
#
# An arm whose run directory is absent, or whose run has no verdict file yet, is
# SKIPPED WITH A MESSAGE rather than failing the whole figure: this experiment
# is being measured one arm at a time, and a figure that refuses to draw until
# the last arm lands is a figure nobody sees during the run. The message names
# the arm, so a missing curve is never a silent one.
SERIES = [
    ("vLLM", "results/260903_0702_exp113r1_vllmcachet75_shiftq",
     ARM_COLOR["vllmrouter"], (0, (4, 1, 1, 1, 1, 1))),
    ("PolyServe", "results/260903_0436_exp113r1_polyservept75_shiftq",
     ARM_COLOR["polyserve"], "-."),
    ("Llumnix SLO", "results/260903_0548_exp113r1_slot75_shiftq",
     ARM_COLOR["slo"], "--"),
    ("llm-d", "results/260903_0834_exp113r1_llmdslot75_shiftq",
     ARM_COLOR["llmd"], (0, (6, 1.5))),
    ("FluidServe", "results/260903_0302_exp113r1_fsv3capgnofrct75_shiftq",
     ARM_COLOR["fluidserve"], "-"),
]
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
    args = ap.parse_args()

    # Every arm this experiment has. The five-arm figure of EXP-109 carries a
    # switch that drops the arm without admission control, because there the
    # admitted denominator needed the axis cut back to a quarter of the hour for
    # it; here the same measurement decides it -- see the inflation check below,
    # which is printed every run rather than assumed.
    wanted = list(SERIES)

    raw = []
    for lab, run, c, ls in wanted:
        d = os.path.join(ROOT, run)
        if not os.path.isdir(d):
            print(f"!! {lab}: no run directory {run} -- skipped", file=sys.stderr)
            continue
        s = series(d)
        if s is None:
            print(f"!! {lab}: no scored verdicts for {os.path.basename(run)} "
                  f"yet -- skipped", file=sys.stderr)
            continue
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
    if False:
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
    stem = "exp113_hour_qwen" + ("_marked" if args.markers else "")
    # PANELS_ADM, always: this figure exists to be read beside
    # exp109_hour_five_admitted.pdf and shares its panel design.
    build(data, dur, PANELS_ADM, os.path.join(HERE, stem + ".pdf"),
          markers=args.markers)
    write_csv(data, dur, os.path.join(HERE, stem + ".csv"), "exp113_qwen")
    return 0


if __name__ == "__main__":
    sys.exit(main())
