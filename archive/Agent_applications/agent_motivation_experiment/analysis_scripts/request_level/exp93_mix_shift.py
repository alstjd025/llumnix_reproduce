#!/usr/bin/env python3
"""EXP-93: does a shift in the class mix move capacity, and does the arrival rate report it?

The judgement rules this implements were written before the run and are in
experiments/EXP-93_mix-shift-stress.md section 4. Nothing here chooses a
threshold after seeing data; the thresholds are constants below.

The trace's segment boundaries and target mixes are READ FROM THE PLAN JSON the
generator wrote, not hardcoded, so the same script runs against the deployed
hour trace (schedule m1,m2,m3,m1) as a dry run and against the shift trace
(m2,A,m1,B) for the measurement.

The primary comparison is BETWEEN SEGMENTS OF ONE RUN at matched arrival rates.
That is deliberate: the two segments differ only in the class mix, they share a
session, a binary and a fleet, so the between-session movement that this
repository keeps having to reason about cannot reach the result.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    CLASSES, attain, goodput_tokens, load_run, arm_of,
)

# Pre-registered thresholds (EXP-93 section 4). Do not tune these.
H1_CONFIRM = 20.0      # mean gap between the chat-heavy and the even segment
H1_REFUTE = 8.0
H2_DELTA = 5.0         # arm difference in the drop across the big downward step
H3_CONFIRM = 20.0      # FluidServe minus llm-d over the whole hour
H3_REFUTE = 12.0
V5_COLLAPSE = 20.0     # below this for BOTH arms, a segment is not read
V3_ARRIVAL_TOL = 0.01  # arrival counts between arms

TRANSITION_S = 180.0   # three minutes either side of a boundary
WIN_W, WIN_STEP = 60.0, 20.0
RATE_EDGES = (10, 15, 20, 25, 30, 35, 40, 45, 60)
KEY = "violate_offered"   # every arrival in the denominator; a rejection is a violation


# --------------------------------------------------------------------------
def read_plan(plan_path):
    """Segment name, time range and target mix, from the generator's own output."""
    p = json.load(open(plan_path))
    segs = []
    for name, sd in p["segments"].items():
        if name == "warmup":
            continue
        t0, t1 = sd["t_range_s"]
        segs.append({"name": name, "t0": float(t0), "t1": float(t1),
                     "target": sd["realised_ratio"]})
    segs.sort(key=lambda s: s["t0"])
    return segs


def tag_segments(rows, segs):
    """Attach the segment each request ARRIVED in.

    Arrival, not completion: a request that starts in one segment and finishes
    in the next was admitted under the first segment's mix, and admission is
    what is under test.
    """
    rows = rows.copy()
    rows["segment"] = None
    for s in segs:
        m = (rows["rel"] >= s["t0"]) & (rows["rel"] < s["t1"])
        rows.loc[m, "segment"] = s["name"]
    return rows[rows["segment"].notna()]


def windows(rows, width=WIN_W, step=WIN_STEP):
    """Per-request offered attainment and offered rate, per sliding window.

    exp22_fluidserve.sliding() reports the class-equal average as its headline
    column; the aggregation this repository uses for a headline is per request,
    so the window is rebuilt here rather than reinterpreted. Both are emitted.
    """
    lo, hi = rows["rel"].min(), rows["rel"].max()
    out, t = [], lo
    while t + width <= hi:
        w = rows[(rows["rel"] >= t) & (rows["rel"] < t + width)]
        if len(w) >= 10:
            seg = w["segment"].mode()
            rec = {"t": t + width / 2.0,
                   "offered_rps": len(w) / width,
                   "segment": seg.iloc[0] if len(seg) else None,
                   "pure": float((w["segment"] == (seg.iloc[0] if len(seg) else None)).mean()),
                   "per_request": attain(w, KEY),
                   "goodput_tps": goodput_tokens(w, width, KEY),
                   "chat_share": float((w["class"] == "chat").mean())}
            for c in CLASSES:
                rec[c] = attain(w[w["class"] == c], KEY)
            out.append(rec)
        t += step
    return pd.DataFrame(out)


# --------------------------------------------------------------------------
def v_checks(runs, segs, out):
    out.append("## Validity checks\n")
    # V3: the two arms must have seen the same arrivals. Counted the way
    # load_run counts them (agent != job_summary), because agent=="request"
    # alone drops the grace_cut rows and removes up to a third of the arrivals
    # on some arms but none on others.
    out.append("| run | arm | arrivals in window | segments seen | chat share by segment (target) |")
    out.append("|---|---|---|---|---|")
    counts = {}
    for name, arm, rows in runs:
        by = []
        for s in segs:
            w = rows[rows["segment"] == s["name"]]
            got = float((w["class"] == "chat").mean()) if len(w) else float("nan")
            by.append(f"{s['name']} {100*got:.1f} ({100*s['target']['chat']:.1f})")
        counts.setdefault(arm, []).append(len(rows))
        out.append(f"| {name} | {arm} | {len(rows):,} | {rows['segment'].nunique()} | "
                   + " · ".join(by) + " |")
    out.append("")
    allc = [c for v in counts.values() for c in v]
    spread = (max(allc) - min(allc)) / max(allc) if allc else 0.0
    ok3 = spread <= V3_ARRIVAL_TOL
    out.append(f"**V3 arrival counts within {100*V3_ARRIVAL_TOL:.0f}%**: "
               f"{'PASS' if ok3 else 'FAIL'} (spread {100*spread:.2f}%)\n")
    out.append("> If the realised chat share does not step at the plan's boundaries, the "
               "segment tagging is misaligned and every segment-wise number below is wrong. "
               "That is why the target is printed beside it.\n")
    return ok3


def h1(per_arm, segs, out):
    """Between two segments of ONE run, at matched arrival rates."""
    out.append("## H1 — the same arrival rate, two mixes (within run, no session effect)\n")
    # The chat-heavy segment and the even one, identified from the plan rather
    # than by position, so the script is not silently tied to one schedule.
    heavy = max(segs, key=lambda s: s["target"]["chat"])["name"]
    even = min(segs, key=lambda s: abs(s["target"]["chat"] - 1.0 / 3.0))["name"]
    out.append(f"Chat-heavy segment `{heavy}`, even segment `{even}`. "
               f"Only windows lying wholly inside one segment are used "
               f"(`pure == 1.0`), so a window straddling a boundary cannot "
               f"contribute to either.\n")
    verdicts = {}
    for arm, sl in per_arm.items():
        sl = sl[sl["pure"] >= 1.0]
        a, b = sl[sl["segment"] == heavy], sl[sl["segment"] == even]
        if a.empty or b.empty:
            out.append(f"**{arm}**: one of the two segments has no pure window; not read.\n")
            continue
        out.append(f"### {arm}\n")
        out.append("| req/s bin | n win heavy | attain heavy | n win even | attain even | gap |")
        out.append("|---|---|---|---|---|---|")
        gaps = []
        for lo, hi in zip(RATE_EDGES[:-1], RATE_EDGES[1:]):
            wa = a[(a.offered_rps >= lo) & (a.offered_rps < hi)]
            wb = b[(b.offered_rps >= lo) & (b.offered_rps < hi)]
            if len(wa) < 3 or len(wb) < 3:
                continue
            ga = wa.per_request.mean(); gb = wb.per_request.mean()
            gaps.append(ga - gb)
            out.append(f"| {lo}-{hi} | {len(wa)} | {ga:.1f} | {len(wb)} | {gb:.1f} | "
                       f"**{ga-gb:+.1f}** |")
        if not gaps:
            out.append("| (no rate bin has three windows in both segments) | | | | | |")
            out.append("")
            continue
        mean_gap = float(np.mean(gaps))
        same_sign = all(g > 0 for g in gaps) or all(g < 0 for g in gaps)
        if mean_gap >= H1_CONFIRM and same_sign:
            v = "CONFIRMED"
        elif mean_gap < H1_REFUTE or not same_sign:
            v = "REFUTED"
        else:
            v = "weak (distinguishable but under the confirm threshold)"
        verdicts[arm] = (mean_gap, v)
        out.append("")
        out.append(f"**mean gap {mean_gap:+.1f} points over {len(gaps)} matched bins, "
                   f"signs {'all the same' if same_sign else 'MIXED'} -> {v}** "
                   f"(confirm >= {H1_CONFIRM}, refute < {H1_REFUTE} or mixed signs)\n")
    return verdicts


def h2(per_arm, segs, out):
    out.append("## H2 — the cost of a transition, three minutes either side\n")
    out.append("| boundary | mix step | arm | before | after | drop |")
    out.append("|---|---|---|---|---|---|")
    drops = {}
    bounds = [(segs[i]["t1"], segs[i]["name"], segs[i + 1]["name"],
               segs[i]["target"]["chat"], segs[i + 1]["target"]["chat"])
              for i in range(len(segs) - 1)]
    for t, na, nb, ca, cb in bounds:
        for arm, sl in per_arm.items():
            bef = sl[(sl.t >= t - TRANSITION_S) & (sl.t < t)]
            aft = sl[(sl.t >= t) & (sl.t < t + TRANSITION_S)]
            if bef.empty or aft.empty:
                continue
            d = bef.per_request.mean() - aft.per_request.mean()
            drops.setdefault((na, nb), {})[arm] = d
            out.append(f"| {na} -> {nb} @ {t:.0f}s | chat {100*ca:.1f} -> {100*cb:.1f}% | "
                       f"{arm} | {bef.per_request.mean():.1f} | {aft.per_request.mean():.1f} | "
                       f"**{d:+.1f}** |")
    out.append("")
    out.append("> The arrival rate moves inside these windows too, but the generator places "
               "the segment boundaries away from the rate peaks and both arms replay the "
               "same arrival times, so the rate contribution cancels in the ARM DIFFERENCE "
               "and not in either column on its own.\n")
    # The pre-registered comparison is the largest downward step in chat share.
    if drops:
        key = max(drops, key=lambda k: next(
            (ca - cb for t, na, nb, ca, cb in bounds if (na, nb) == k), 0.0))
        d = drops[key]
        if len(d) >= 2:
            fs = next((v for a, v in d.items() if a.startswith("fs")), None)
            ld = next((v for a, v in d.items() if "llmd" in a), None)
            if fs is not None and ld is not None:
                diff = ld - fs
                v = ("CONFIRMED" if diff >= H2_DELTA
                     else "REFUTED" if diff <= -H2_DELTA else "not distinguishable")
                out.append(f"**Largest downward step {key[0]} -> {key[1]}: llm-d drops "
                           f"{ld:+.1f}, FluidServe {fs:+.1f}, difference {diff:+.1f} -> {v}** "
                           f"(threshold +-{H2_DELTA})\n")
    return drops


def h3(runs, segs, out):
    out.append("## H3 — the whole hour (secondary: this one crosses sessions)\n")
    out.append("| run | arm | offered | admitted | rejected % | goodput tok/s |")
    out.append("|---|---|---|---|---|---|")
    by_arm = {}
    for name, arm, rows in runs:
        span = rows["rel"].max() - rows["rel"].min()
        off = attain(rows, KEY)
        adm = attain(rows, "violate_served")
        rej = 100.0 * rows["rejected"].mean()
        gp = goodput_tokens(rows, span, KEY)
        by_arm.setdefault(arm, []).append(off)
        out.append(f"| {name} | {arm} | **{off:.1f}** | {adm:.1f} | {rej:.1f} | {gp:,.0f} |")
    out.append("")
    for arm, v in by_arm.items():
        out.append(f"- `{arm}`: mean {np.mean(v):.1f}, "
                   + (f"repeats {min(v):.1f}..{max(v):.1f} (spread {max(v)-min(v):.1f})"
                      if len(v) > 1 else "**one repeat, no error bar**"))
    fs = [v for a, v in by_arm.items() if a.startswith("fs")]
    ld = [v for a, v in by_arm.items() if "llmd" in a]
    if fs and ld:
        gap = float(np.mean(fs[0]) - np.mean(ld[0]))
        v = ("CONFIRMED" if gap >= H3_CONFIRM
             else "REFUTED" if gap <= H3_REFUTE else "weak")
        out.append("")
        out.append(f"**gap {gap:+.1f} points -> {v}** (confirm >= {H3_CONFIRM}, "
                   f"refute <= {H3_REFUTE}; the deployed hour trace read +24.3 in EXP-71, "
                   f"a different session and binary)\n")
    return by_arm


def v5(runs, segs, out):
    out.append("## V5 — segments where both arms collapsed are not read\n")
    out.append("| segment | " + " | ".join(sorted({a for _, a, _ in runs})) + " | read? |")
    arms = sorted({a for _, a, _ in runs})
    out.append("|---" * (len(arms) + 2) + "|")
    skip = set()
    for s in segs:
        vals = {}
        for _, arm, rows in runs:
            w = rows[rows["segment"] == s["name"]]
            if len(w):
                vals.setdefault(arm, []).append(attain(w, KEY))
        means = {a: float(np.mean(v)) for a, v in vals.items()}
        collapsed = means and all(m < V5_COLLAPSE for m in means.values())
        if collapsed:
            skip.add(s["name"])
        out.append(f"| {s['name']} | "
                   + " | ".join(f"{means.get(a, float('nan')):.1f}" for a in arms)
                   + f" | {'NO - both below ' + str(V5_COLLAPSE) if collapsed else 'yes'} |")
    out.append("")
    return skip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True, help="run directory globs")
    ap.add_argument("--plan", required=True, help="the trace's .plan.json")
    ap.add_argument("--out", default=None, help="write the report here as well as stdout")
    a = ap.parse_args()

    segs = read_plan(a.plan)
    dirs = sorted({d for g in a.runs for d in glob.glob(g) if os.path.isdir(d)})
    dirs = [d for d in dirs if "PRERUN" not in d]     # llm-d predictor warm-up, not a measurement
    runs, per_arm = [], {}
    for d in dirs:
        rows = load_run(d)
        if rows is None or rows.empty:
            print(f"[exp93] SKIP {os.path.basename(d)}: no usable rows "
                  f"(check for an unmerged shards/ directory)", file=sys.stderr)
            continue
        rows = tag_segments(rows, segs)
        arm = arm_of(d)
        runs.append((os.path.basename(d), arm, rows))
        per_arm.setdefault(arm, []).append(windows(rows))
    if not runs:
        sys.exit("[exp93] no runs loaded")
    per_arm = {k: pd.concat(v, ignore_index=True) for k, v in per_arm.items()}

    out = [f"# EXP-93 — mix shift, {len(runs)} runs, "
           f"segments {' -> '.join(s['name'] for s in segs)}\n",
           "Scored on the offered denominator: every arrival counts and a rejection is a "
           "violation. Aggregated per request. Windows are 60 s, stepped 20 s.\n"]
    v_checks(runs, segs, out)
    skipped = v5(runs, segs, out)
    if skipped:
        out.append(f"> ⚠ collapsed segments: {sorted(skipped)}. "
                   f"Any H1/H2 line touching them is reported but not concluded from.\n")
    h1(per_arm, segs, out)
    h2(per_arm, segs, out)
    h3(runs, segs, out)

    text = "\n".join(out)
    print(text)
    if a.out:
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        open(a.out, "w").write(text + "\n")
        print(f"\n[exp93] wrote {a.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
