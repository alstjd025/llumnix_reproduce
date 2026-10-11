#!/usr/bin/env python3
"""What the hour-long trace shows that a static condition cannot.

A static condition holds the arrival rate and the class mix fixed. The claimed
capability of FluidServe is that it re-allocates instance capacity as the class
mix moves, and a condition in which the mix does not move cannot show that at
all -- it can only show the steady state that the re-allocation happens to
settle on. This script asks what the hour-long trace of EXP-71 adds, using only
data that already exists, in three parts.

  1. Transition cost. For each of the three mix boundaries, the primary-metric
     attainment in the seconds after the boundary, against the steady stretch
     before it and the steady stretch after it, with the arrival rate of each
     stretch printed beside it so a drop caused by the rate is not read as a
     drop caused by the mix.

  2. Re-allocation speed. The effective number of instances a class runs on,
     1 / sum(share^2) over the instances (4.0 = evenly over four instances,
     1.0 = one instance, 2.0 = two instances holding half each), per class per
     window -- the quantity separation_measures.py reports as a median, here as
     a time series so the movement after a boundary is visible. Beside it, the
     total variation distance between the class's instance distribution in a
     window and its distribution in the steady stretch BEFORE the boundary, and
     the time at which half of the eventual move has been made.

  3. Static against dynamic. The same primary metric on the fixed-rate,
     fixed-mix conditions of the static sweep, at the rates that bracket the
     dynamic trace's mean, so that the advantage on the moving workload can be
     put next to the advantage on the still one. The comparison is made on the
     two m1 segments of the trace, because m1 is the mix the static conditions
     hold; s1_m2 and s2_m3 have no static counterpart at all, which is itself
     part of the answer.

Usage
-----
  python3 tail2026_dyn_adapt.py --out-dir <dir>
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import CLASSES, load_run  # noqa: E402
from exp41_engine_view import attribute_engines  # noqa: E402
from tail2026_dyn_rescore import (  # noqa: E402
    REPO, RUNS, ARM_LABEL, ARM_ORDER, attain_offered, prepare,
    segments_from_plan,
)

# The static sweep, restricted to directories at or after 260807_1900 as the
# analysis rule requires. Two repeats per arm per rate. 1500 and 2100 requests
# per minute are 25 and 35 requests per second, which bracket the dynamic
# trace's mean arrival rate over its measured window.
STATIC = {
    1500: {
        "fspfx": ["260808_0956_exp70r1_fspfx_m1_rpm_1500",
                  "260813_0707_exp80r2_fspfx_m1_rpm_1500"],
        "llmdslo": ["260808_1022_exp70r1_llmdslo_m1f_rpm_1500",
                    "260813_0102_exp80r2_llmdslo_m1f_rpm_1500"],
        "slo": ["260809_0717_exp72r1_slo_m1f_rpm_1500",
                "260813_0441_exp80r2_slo_m1f_rpm_1500"],
        "polyserve": ["260809_0517_exp72r1_polyserve_m1_rpm_1500",
                      "260813_0242_exp80r2_polyserve_m1_rpm_1500"],
    },
    2100: {
        "fspfx": ["260807_2056_exp68sr1_fspfx_m1_rpm_2100",
                  "260807_2254_exp68sr2_fspfx_m1_rpm_2100"],
        "llmdslo": ["260807_2122_exp68sr1_llmdslo_m1f_rpm_2100",
                    "260807_2325_exp68sr2_llmdslo_m1f_rpm_2100"],
        "slo": ["260809_0730_exp72r1_slo_m1f_rpm_2100",
                "260813_0456_exp80r2_slo_m1f_rpm_2100"],
        "polyserve": ["260809_0531_exp72r1_polyserve_m1_rpm_2100",
                      "260813_0256_exp80r2_polyserve_m1_rpm_2100"],
    },
}
STATIC_DIR = os.path.join(REPO, "paper_experiment/static_sweep_2026-08/data")
CUT = 3360.0            # from tail2026_dyn_rescore.py part B
WIN, STEP = 60.0, 30.0
MIN_PER_WINDOW = 40


def rate_of(r, lo, hi):
    w = r[(r["rel"] >= lo) & (r["rel"] < hi)]
    return len(w) / (hi - lo) if hi > lo else np.nan


def eff_count(counts):
    n = np.asarray([c for c in counts if c > 0], dtype=float)
    if n.sum() <= 0:
        return np.nan
    p = n / n.sum()
    return 1.0 / float((p ** 2).sum())


def class_dist(e, lo, hi, cls, insts):
    w = e[(e["rel"] >= lo) & (e["rel"] < hi) & (e["class"] == cls)]
    if len(w) < MIN_PER_WINDOW:
        return None
    n = w["engine_port"].value_counts()
    p = np.array([n.get(i, 0) for i in insts], dtype=float)
    return p / p.sum()


def tv(p, q):
    return 0.5 * float(np.abs(p - q).sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(
        REPO, "results/aggregate_analysis/tail_2026-08-16"))
    ap.add_argument("--settle", type=float, default=90.0)
    ap.add_argument("--steady", type=float, default=300.0,
                    help="length of the steady stretch either side of a boundary")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    data, eng = {}, {}
    for arm, dirs in RUNS.items():
        for d in dirs:
            p = os.path.join(REPO, d)
            r = prepare(p)
            if r is None:
                continue
            data.setdefault(arm, []).append((os.path.basename(p), r))
            e, n = attribute_engines(p, r)
            if e is not None and not e.empty:
                eng.setdefault(arm, []).append(
                    (os.path.basename(p), e, 100.0 * len(e) / max(n, 1)))

    segs = segments_from_plan()
    bounds = [(t0, name, prev[0]) for (name, t0, t1, _), prev in
              zip(segs[1:], segs[:-1])]

    out = []
    out.append("## G. the segments, with their arrival rates")
    out.append("")
    hdr = f"{'segment':<12}{'window (min)':>16}{'req/s':>9}{'mix (chat/dr/swe)':>22}"
    out.append(hdr)
    out.append("-" * len(hdr))
    ref = data["fspfx"][0][1]
    for name, t0, t1, ratio in segs:
        hi = min(t1, CUT)
        if t0 >= CUT:
            continue
        mix = "/".join(f"{100*ratio.get(c,0):.0f}" for c in CLASSES)
        out.append(f"{name:<12}{f'{t0/60:.0f}-{hi/60:.1f}':>16}"
                   f"{rate_of(ref, t0, hi):9.1f}{mix:>22}")
    out.append(f"\nwhole measured window 1.0-{CUT/60:.1f} min: "
               f"{rate_of(ref, 60.0, CUT):.1f} req/s mean")
    out.append("")

    # ---------------------------------------------------------------- part 1
    out.append("## H. transition cost at each mix boundary")
    out.append("")
    out.append(f"Primary metric, corrected mean per-token time. `before` is the "
               f"{a.steady:.0f} s ending at the boundary, `after` the "
               f"{a.settle:.0f} s beginning at it, `settled` the {a.steady:.0f} s "
               f"beginning {a.settle:.0f} s after it. Mean of the two repeats. "
               f"The arrival rate of each stretch is printed because the rate "
               f"moves inside a segment as well as across a boundary.")
    out.append("")
    for b, name, prevname in bounds:
        if b + a.settle + a.steady > CUT:
            out.append(f"### boundary at {b/60:.0f} min ({prevname} -> {name}) "
                       f"— DROPPED, its settled stretch runs past the cut")
            out.append("")
            continue
        out.append(f"### boundary at {b/60:.0f} min: {prevname} -> {name}")
        out.append("")
        hdr = (f"{'arm':<16}{'before':>9}{'after':>9}{'settled':>9}"
               f"{'after-before':>14}{'after-settled':>15}"
               f"{'rate b/a/s':>18}")
        out.append(hdr)
        out.append("-" * len(hdr))
        for arm in ARM_ORDER:
            v = {}
            for k, (lo, hi) in {"before": (b - a.steady, b),
                                "after": (b, b + a.settle),
                                "settled": (b + a.settle,
                                            b + a.settle + a.steady)}.items():
                s = [attain_offered(r[(r["rel"] >= lo) & (r["rel"] < hi)], "itl_ms")
                     for _, r in data[arm]]
                v[k] = float(np.nanmean(s))
                v[k + "_rate"] = float(np.mean(
                    [rate_of(r, lo, hi) for _, r in data[arm]]))
            rates = (f"{v['before_rate']:.0f}/{v['after_rate']:.0f}/"
                     f"{v['settled_rate']:.0f}")
            out.append(f"{ARM_LABEL[arm]:<16}{v['before']:9.1f}{v['after']:9.1f}"
                       f"{v['settled']:9.1f}{v['after']-v['before']:14.1f}"
                       f"{v['after']-v['settled']:15.1f}{rates:>18}")
        out.append("")

    out.append("### the same, in 30 s bins after each boundary")
    out.append("")
    out.append("Attainment in each 30 s bin, mean over the two repeats, so the "
               "shape of the recovery is visible rather than one pooled number.")
    out.append("")
    for b, name, prevname in bounds:
        if b + 300 > CUT:
            continue
        out.append(f"boundary {b/60:.0f} min ({prevname} -> {name})")
        binstarts = np.arange(b - 120, b + 330, 30.0)
        hdr = f"{'arm':<16}" + "".join(
            f"{(s-b)/1:>7.0f}" for s in binstarts)
        out.append(f"{'  s from boundary':<16}" + "".join(
            f"{(s-b):>7.0f}" for s in binstarts))
        for arm in ARM_ORDER:
            cells = []
            for s in binstarts:
                vals = [attain_offered(r[(r["rel"] >= s) & (r["rel"] < s + 30)],
                                       "itl_ms") for _, r in data[arm]]
                cells.append(f"{np.nanmean(vals):7.0f}")
            out.append(f"{ARM_LABEL[arm]:<16}" + "".join(cells))
        out.append("")

    # ---------------------------------------------------------------- part 2
    out.append("## I. how fast each arm re-allocates")
    out.append("")
    out.append("Effective number of instances a class runs on, 1/sum(share^2), "
               f"over {WIN:.0f} s windows stepped {STEP:.0f} s. 4.0 is an even "
               "spread over the four instances, 2.0 is two instances holding "
               "half each, 1.0 is one instance. Median over the windows of each "
               "segment, mean of the two repeats. The share of admitted "
               "requests the engine attribution covers is printed first: the "
               "scheduler's dispatch log loses lines under load and the loss is "
               "concentrated in the busiest minutes, so a low coverage "
               "understates any imbalance.")
    out.append("")
    hdr = f"{'arm':<16}{'attributed %':>14}"
    out.append(hdr)
    out.append("-" * len(hdr))
    for arm in ARM_ORDER:
        fr = float(np.mean([f for _, _, f in eng.get(arm, [(None, None, np.nan)])]))
        out.append(f"{ARM_LABEL[arm]:<16}{fr:14.1f}")
    out.append("")

    seg_windows = {}
    for name, t0, t1, _ in segs:
        hi = min(t1, CUT)
        if t0 >= CUT:
            continue
        seg_windows[name] = (t0, hi)

    hdr = (f"{'arm':<16}{'segment':<10}" +
           "".join(f"{c[:4]:>9}" for c in CLASSES) + f"{'mean':>9}")
    out.append(hdr)
    out.append("-" * len(hdr))
    for arm in ARM_ORDER:
        for sname, (t0, t1) in seg_windows.items():
            per_class = {c: [] for c in CLASSES}
            for _, e, _ in eng.get(arm, []):
                t = t0 + WIN / 2
                while t + WIN / 2 <= t1:
                    w = e[(e["rel"] >= t - WIN / 2) & (e["rel"] < t + WIN / 2)]
                    for c in CLASSES:
                        sub = w[w["class"] == c]
                        if len(sub) >= MIN_PER_WINDOW:
                            per_class[c].append(
                                eff_count(sub["engine_port"].value_counts().values))
                    t += STEP
            vals = [float(np.median(per_class[c])) if per_class[c] else np.nan
                    for c in CLASSES]
            out.append(f"{ARM_LABEL[arm]:<16}{sname:<10}"
                       + "".join(f"{v:9.2f}" for v in vals)
                       + f"{np.nanmean(vals):9.2f}")
        out.append("")

    out.append("### how much of the re-allocation is done, by time after the "
               "boundary")
    out.append("")
    out.append("For each class, `p_before` is its distribution over the four "
               f"instances in the {a.steady:.0f} s ending at the boundary and "
               f"`p_settled` its distribution in the {a.steady:.0f} s beginning "
               f"{a.settle:.0f} s after it. `move` is the total variation "
               "distance between the two, which is how much that class was "
               "re-allocated at all; a value near 0 means the arm put the class "
               "in the same place after the mix changed as before. `t_half` is "
               "the first 30 s bin after the boundary whose distance from "
               "p_before reaches half of `move`, which is how quickly the move "
               "was made. `t_half` is meaningless when `move` is small and is "
               "printed as n/a below 0.05.")
    out.append("")
    hdr = (f"{'arm':<16}{'boundary':<10}{'class':<14}{'move':>8}{'t_half s':>10}")
    out.append(hdr)
    out.append("-" * len(hdr))
    for arm in ARM_ORDER:
        for b, name, prevname in bounds:
            if b + a.settle + a.steady > CUT:
                continue
            for c in CLASSES:
                moves, halves = [], []
                for _, e, _ in eng.get(arm, []):
                    insts = sorted(e["engine_port"].unique())
                    p0 = class_dist(e, b - a.steady, b, c, insts)
                    p1 = class_dist(e, b + a.settle, b + a.settle + a.steady,
                                    c, insts)
                    if p0 is None or p1 is None:
                        continue
                    mv = tv(p0, p1)
                    moves.append(mv)
                    th = np.nan
                    for s in np.arange(b, b + 300, 30.0):
                        p = class_dist(e, s, s + 30.0, c, insts)
                        if p is None:
                            continue
                        if tv(p, p0) >= 0.5 * mv:
                            th = s + 15.0 - b
                            break
                    halves.append(th)
                if not moves:
                    continue
                mv = float(np.mean(moves))
                th = float(np.nanmean(halves)) if np.any(~np.isnan(halves)) else np.nan
                ths = "n/a" if (mv < 0.05 or np.isnan(th)) else f"{th:.0f}"
                out.append(f"{ARM_LABEL[arm]:<16}{f'{b/60:.0f} min':<10}"
                           f"{c:<14}{mv:8.3f}{ths:>10}")
        out.append("")

    # ---------------------------------------------------------------- part 3
    out.append("## J. static against dynamic, on the same mix")
    out.append("")
    out.append("Static conditions hold the m1 mix (chat 77 / deep research 15 / "
               "software engineering 8 by request count). The trace's s0_m1 and "
               "s3_m1 segments hold the same mix, so those two are the only "
               "segments with a static counterpart; s1_m2 and s2_m3 have none, "
               "which is the first thing the hour-long trace supplies that the "
               "static sweep does not. Primary metric, corrected mean per-token "
               "time, mean of two repeats, bracket is the spread.")
    out.append("")
    stat = {}
    for rpm, arms in STATIC.items():
        for arm, dirs in arms.items():
            vals = []
            for d in dirs:
                p = os.path.join(STATIC_DIR, d)
                r = load_run(p)
                if r is None or r.empty:
                    print(f"  missing {d}", file=sys.stderr)
                    continue
                for c in ["tbt_p50_ms", "tbt_p90_ms", "tbt_p95_ms",
                          "first_token_latency", "latency", "output_tokens"]:
                    r[c] = pd.to_numeric(r.get(c), errors="coerce")
                vals.append((attain_offered(r, "itl_ms"),
                             attain_offered(r, "tbt_p90_ms")))
            if vals:
                stat[(rpm, arm)] = (
                    float(np.mean([v[0] for v in vals])),
                    float(np.max([v[0] for v in vals]) - np.min([v[0] for v in vals])),
                    float(np.mean([v[1] for v in vals])),
                    len(vals))
    hdr = (f"{'arm':<16}{'static 25/s':>16}{'static 35/s':>16}"
           f"{'dyn s0_m1':>14}{'dyn s3_m1':>14}")
    out.append("mean per-token scoring")
    out.append(hdr)
    out.append("-" * len(hdr))
    dyn = {}
    for arm in ARM_ORDER:
        for sname in ("s0_m1", "s3_m1"):
            t0, t1 = seg_windows[sname]
            v = [attain_offered(r[(r["rel"] >= t0) & (r["rel"] < t1)], "itl_ms")
                 for _, r in data[arm]]
            p90 = [attain_offered(r[(r["rel"] >= t0) & (r["rel"] < t1)],
                                  "tbt_p90_ms") for _, r in data[arm]]
            dyn[(arm, sname)] = (float(np.mean(v)),
                                 float(np.max(v) - np.min(v)),
                                 float(np.mean(p90)))
    for arm in ARM_ORDER:
        c = []
        for rpm in (1500, 2100):
            s = stat.get((rpm, arm))
            c.append(f"{s[0]:.1f} ({s[1]:.1f})" if s else "n/a")
        for sname in ("s0_m1", "s3_m1"):
            d = dyn[(arm, sname)]
            c.append(f"{d[0]:.1f} ({d[1]:.1f})")
        out.append(f"{ARM_LABEL[arm]:<16}" + "".join(f"{x:>16}" for x in c[:2])
                   + "".join(f"{x:>14}" for x in c[2:]))
    out.append("")
    out.append("p90 per-token scoring")
    out.append(hdr)
    out.append("-" * len(hdr))
    for arm in ARM_ORDER:
        c = []
        for rpm in (1500, 2100):
            s = stat.get((rpm, arm))
            c.append(f"{s[2]:.1f}" if s else "n/a")
        for sname in ("s0_m1", "s3_m1"):
            c.append(f"{dyn[(arm, sname)][2]:.1f}")
        out.append(f"{ARM_LABEL[arm]:<16}" + "".join(f"{x:>16}" for x in c[:2])
                   + "".join(f"{x:>14}" for x in c[2:]))
    out.append("")
    out.append("the advantage of FluidServe over each other arm, mean scoring")
    hdr = (f"{'against':<16}{'static 25/s':>14}{'static 35/s':>14}"
           f"{'dyn s0_m1':>14}{'dyn s3_m1':>14}{'dyn whole':>14}")
    out.append(hdr)
    out.append("-" * len(hdr))
    whole = {}
    for arm in ARM_ORDER:
        v = [attain_offered(r[(r["rel"] >= 60.0) & (r["rel"] < CUT)], "itl_ms")
             for _, r in data[arm]]
        whole[arm] = float(np.mean(v))
    for arm in ARM_ORDER[1:]:
        c = []
        for rpm in (1500, 2100):
            s0, s1 = stat.get((rpm, "fspfx")), stat.get((rpm, arm))
            c.append(f"{s0[0]-s1[0]:+.1f}" if s0 and s1 else "n/a")
        for sname in ("s0_m1", "s3_m1"):
            c.append(f"{dyn[('fspfx', sname)][0]-dyn[(arm, sname)][0]:+.1f}")
        c.append(f"{whole['fspfx']-whole[arm]:+.1f}")
        out.append(f"{ARM_LABEL[arm]:<16}" + "".join(f"{x:>14}" for x in c))
    out.append("")

    # ---------------------------------------------------------------- part 4
    out.append("## K. separating the rate from the mix")
    out.append("")
    out.append("The trace moves the arrival rate and the class mix at the same "
               "time, so nothing in H or J is a clean measurement of either on "
               "its own. Three things can still be said, and this section says "
               "them with the arithmetic in view.")
    out.append("")
    out.append("### K1. the arrival rate moves inside a segment as well as "
               "across a boundary")
    out.append("")
    out.append("30 s bins of the arrival stream, per segment. A static "
               "condition is a single point on this axis; a segment mean is an "
               "average over the spread below, and attainment is not linear in "
               "the rate, so a segment cannot be replaced by its mean.")
    out.append("")
    hdr = f"{'segment':<12}{'p10':>8}{'p50':>8}{'p90':>8}{'min':>8}{'max':>8}"
    out.append(hdr)
    out.append("-" * len(hdr))
    for sname, (t0, t1) in seg_windows.items():
        b = ((ref["rel"] - t0) // 30.0).astype(int)
        w = ref[(ref["rel"] >= t0) & (ref["rel"] < t1)]
        cnt = (w["rel"] // 30.0).value_counts().sort_index().values / 30.0
        out.append(f"{sname:<12}{np.percentile(cnt,10):8.1f}"
                   f"{np.percentile(cnt,50):8.1f}{np.percentile(cnt,90):8.1f}"
                   f"{cnt.min():8.1f}{cnt.max():8.1f}")
    out.append("")

    out.append("### K2. how much of each boundary's change the rate accounts for")
    out.append("")
    out.append("The static sweep gives each arm a rate sensitivity: the change "
               "in attainment per additional request per second, taken as the "
               "slope between the 25 and 35 req/s conditions of the same m1 "
               "mix. Multiplying it by the change in arrival rate across the "
               "boundary predicts the part of the change the rate can account "
               "for; what is left is the part the mix change has to account "
               "for. This is a linear extrapolation of a curve that is not "
               "linear, so it is a bound on the rate's contribution rather "
               "than an exact split.")
    out.append("")
    slope = {}
    for arm in ARM_ORDER:
        s25, s35 = stat.get((1500, arm)), stat.get((2100, arm))
        slope[arm] = (s35[0] - s25[0]) / 10.0 if s25 and s35 else np.nan
    out.append(f"{'arm':<16}{'points per req/s':>18}")
    for arm in ARM_ORDER:
        out.append(f"{ARM_LABEL[arm]:<16}{slope[arm]:18.2f}")
    out.append("")
    hdr = (f"{'boundary':<12}{'arm':<16}{'d rate':>8}{'observed':>10}"
           f"{'rate part':>11}{'residual':>10}")
    out.append(hdr)
    out.append("-" * len(hdr))
    for b, name, prevname in bounds:
        if b + a.settle + a.steady > CUT:
            continue
        for arm in ARM_ORDER:
            before = float(np.nanmean(
                [attain_offered(r[(r["rel"] >= b - a.steady) & (r["rel"] < b)],
                                "itl_ms") for _, r in data[arm]]))
            after = float(np.nanmean(
                [attain_offered(r[(r["rel"] >= b) & (r["rel"] < b + a.settle)],
                                "itl_ms") for _, r in data[arm]]))
            rb = float(np.mean([rate_of(r, b - a.steady, b) for _, r in data[arm]]))
            ra = float(np.mean([rate_of(r, b, b + a.settle) for _, r in data[arm]]))
            pred = slope[arm] * (ra - rb)
            out.append(f"{f'{b/60:.0f} min':<12}{ARM_LABEL[arm]:<16}"
                       f"{ra-rb:8.1f}{after-before:10.1f}{pred:11.1f}"
                       f"{after-before-pred:10.1f}")
        out.append("")

    out.append("### K3. rate-matched and mix-matched: the same rate, the same "
               "mix, moving against still")
    out.append("")
    out.append("Only the 60 s windows of the two m1 segments whose own arrival "
               "rate is within 10% of 25 req/s are scored, so the comparison "
               "against the static 25 req/s m1 condition holds both the rate "
               "and the mix. What is left of the difference is that one "
               "workload had been moving and the other had not.")
    out.append("")
    hdr = (f"{'arm':<16}{'dynamic, 25/s m1':>20}{'n':>9}"
           f"{'static 25/s m1':>18}{'difference':>13}")
    out.append(hdr)
    out.append("-" * len(hdr))
    for arm in ARM_ORDER:
        vals, ns = [], []
        for _, r in data[arm]:
            keep = []
            for sname in ("s0_m1", "s3_m1"):
                t0, t1 = seg_windows[sname]
                t = t0
                while t + WIN <= t1:
                    w = r[(r["rel"] >= t) & (r["rel"] < t + WIN)]
                    if 22.5 <= len(w) / WIN <= 27.5:
                        keep.append(w)
                    t += WIN
            if keep:
                k = pd.concat(keep)
                vals.append(attain_offered(k, "itl_ms"))
                ns.append(len(k))
        s = stat.get((1500, arm))
        dv = float(np.mean(vals)) if vals else np.nan
        out.append(f"{ARM_LABEL[arm]:<16}{dv:20.1f}{int(np.sum(ns)):9d}"
                   f"{s[0]:18.1f}{dv-s[0]:13.1f}")
    out.append("")

    text = "\n".join(out)
    print(text)
    p = os.path.join(a.out_dir, "tail2026_dyn_adapt.txt")
    with open(p, "w") as f:
        f.write(text + "\n")
    print(f"\nwrote {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
