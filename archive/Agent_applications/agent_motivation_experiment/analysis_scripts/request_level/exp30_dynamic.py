#!/usr/bin/env python3
"""EXP-30 - the dynamic trace, broken down by mix segment and by transition.

A whole-run average of a run whose workload changes cannot answer the question
the run was built to ask. If a static partition costs anything when the mix
moves, the cost is in the seconds after each change and is diluted by the steady
stretch on either side; a run mean would report it as a small difference or as
none. So the run is scored three ways:

  whole run     comparable to the fixed-rate sweep, and the number a paper would
                quote.
  per segment   each mix phase separately, excluding the first `--settle` seconds
                after each boundary. This is the steady state of each mix and
                should look like the fixed-rate sweep at that mix.
  transitions   only the `--settle` seconds after each boundary, pooled. If
                adaptation costs anything this is where it is.

The segment boundaries come from the trace's own plan file, not from assuming
even spacing: the flat-rate ablation has two-minute segments after a one-minute
warmup and the hour-long trace has fifteen-minute ones, and reading them from
the plan keeps one script honest about both.

Usage
-----
  python3 exp30_dynamic.py --runs 'results/*exp30r*_ablation' \\
      --plan traces/dynamic/canonical/dyn09_short_mixcycle_flat.plan.json
"""
import argparse
import collections
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    CLASSES, load_run, per_request, goodput_tokens, arm_of,
)

ARM_DISPLAY = {"slo": "llumnix-slo"}


def segments_from_plan(plan_path):
    """[(name, t0_s, t1_s)] for the measured segments, warmup excluded."""
    with open(plan_path) as f:
        plan = json.load(f)
    # The plan keys segments by name and carries the bounds as t_range_s.
    out = []
    for name, seg in plan.get("segments", {}).items():
        if str(name).startswith("warmup"):
            continue
        t0, t1 = seg["t_range_s"]
        out.append((name, float(t0), float(t1)))
    out.sort(key=lambda x: x[1])
    if not out:
        sys.exit(f"{plan_path}: no measured segments; keys were "
                 f"{sorted(plan.keys())}")
    return out


def score(rows, window_s):
    """The four numbers, on the rows whose arrival falls in `window_s`."""
    if rows is None or rows.empty:
        return None
    sub = rows
    if window_s is not None:
        lo, hi = window_s
        sub = rows[(rows["rel"] >= lo) & (rows["rel"] < hi)]
    if sub.empty or len(sub) < 50:
        return None
    span = sub["rel"].max() - sub["rel"].min()
    if span <= 0:
        return None
    return {
        "n": len(sub),
        "adm": per_request(sub, "violate_served"),
        "off": per_request(sub, "violate_offered"),
        "rej": 100.0 * sub["rejected"].mean(),
        "gp": goodput_tokens(sub, span),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--settle", type=float, default=60.0,
                    help="seconds after each mix boundary counted as transition")
    a = ap.parse_args()

    segs = segments_from_plan(a.plan)
    print(f"\nsegments from {os.path.basename(a.plan)}:")
    for name, t0, t1 in segs:
        print(f"  {name:>10}  {t0/60:5.1f} - {t1/60:5.1f} min")
    print(f"transition window: first {a.settle:.0f} s of each segment after the "
          f"first\n")

    by_arm = collections.defaultdict(list)
    for pat in a.runs:
        for d in sorted(glob.glob(pat)):
            r = load_run(d)
            if r is None or r.empty:
                continue
            arm = arm_of(d)
            by_arm[ARM_DISPLAY.get(arm, arm)].append((os.path.basename(d), r))
    if not by_arm:
        sys.exit("no runs matched")

    def emit(title, windows):
        print(f"\n{title}")
        hdr = (f"{'arm':<14}{'n':>8}{'req-adm':>9}{'req-off':>9}"
               f"{'rej%':>7}{'goodput':>9}{'runs':>6}")
        print(hdr)
        print("-" * len(hdr))
        for arm in sorted(by_arm):
            vals = []
            for _, r in by_arm[arm]:
                for w in windows:
                    s = score(r, w)
                    if s:
                        vals.append(s)
            if not vals:
                print(f"{arm:<14}{'(no data)':>8}")
                continue
            tot = sum(v["n"] for v in vals)
            wm = lambda k: sum(v[k] * v["n"] for v in vals) / tot
            print(f"{arm:<14}{tot:8d}{wm('adm'):9.1f}{wm('off'):9.1f}"
                  f"{wm('rej'):7.1f}{wm('gp'):9.0f}{len(by_arm[arm]):6d}")

    emit("WHOLE RUN", [None])

    for name, t0, t1 in segs:
        emit(f"SEGMENT {name}  ({t0/60:.1f}-{t1/60:.1f} min, "
             f"first {a.settle:.0f}s dropped)",
             [(t0 + a.settle, t1)])

    trans = [(t0, t0 + a.settle) for _, t0, _ in segs[1:]]
    if trans:
        emit(f"TRANSITIONS ONLY  ({len(trans)} boundaries x {a.settle:.0f}s)",
             trans)

    print("\nper-class attainment, admitted denominator, whole run")
    hdr = f"{'arm':<14}" + "".join(f"{c:>15}" for c in CLASSES)
    print(hdr)
    print("-" * len(hdr))
    for arm in sorted(by_arm):
        cells = []
        for c in CLASSES:
            vals = []
            for _, r in by_arm[arm]:
                sub = r[r["class"] == c]
                s = score(sub, None)
                if s:
                    vals.append(s)
            if vals:
                tot = sum(v["n"] for v in vals)
                cells.append(sum(v["adm"] * v["n"] for v in vals) / tot)
            else:
                cells.append(float("nan"))
        print(f"{arm:<14}" + "".join(f"{v:15.1f}" for v in cells))

    print("\nNote: arms measured in the same session are comparable; between "
          "sessions this workload moves up to 4.6 points at 80 req/s.")


if __name__ == "__main__":
    main()
