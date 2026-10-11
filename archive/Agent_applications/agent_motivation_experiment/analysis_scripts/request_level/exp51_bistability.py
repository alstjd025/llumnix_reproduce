#!/usr/bin/env python3
"""EXP-51: classify each 45 req/s condition as collapsed or healthy, and cross it
against whether any engine was free of chat.

The classification rule is fixed before the run and is mechanical: a run is
COLLAPSED if its route share over the steady window is below 40%. Every one of
the fourteen conditions recorded before EXP-51 falls unambiguously on one side of
that -- the highest collapsed run routes 15.3% and the lowest healthy one 72.4%.

The state variable is `gate_allowance_ms` per instance, the minimum nominal
budget over the requests live there. With chat at 50 ms, swe at 61.9 and deep
research at 100, an instance reading above 50 is holding no chat, and an
instance reading 100 is holding deep research alone. The claim under test is that
a fleet with no such instance has nowhere to put work with a loose budget,
because every gate is then chat's 50 x 0.90 = 45.0 ms whatever the arriving
request's own budget is.

Candidate C removes `gate_allowance` from the decision, so under C the
all-four-at-50 configuration should still occur and should no longer decide the
outcome. That is what the last table checks.

  python3 exp51_bistability.py --runs 'results/*_m1_rpm_2700'
"""
import argparse
import collections
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, attain  # noqa: E402

COLLAPSE_ROUTE = 40.0     # route share below this is COLLAPSED. Fixed before the run.
CHAT_NOMINAL_MS = 50.0    # an instance above this is holding no chat
STEADY_FROM = 120.0       # seconds; the state is read after the fleet has filled


def scheduler_rows(run):
    p = os.path.join(run, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(p):
        return None
    rows = []
    for line in open(p):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not o.get("ok"):
            continue
        rec = {"t": o["t"]}
        for k, v in o.items():
            m = re.match(r"scheduler_fluidserve_decisions_total\|decision=(\w+)", k)
            if m:
                rec[m.group(1)] = v
            if k.startswith("scheduler_fluidserve_gate_allowance_ms|instance="):
                rec["gate::" + k.split("=")[-1]] = v
        rows.append(rec)
    if len(rows) < 5:
        return None
    d = pd.DataFrame(rows)
    d["t"] -= d["t"].min()
    return d


def summarise(run):
    d = scheduler_rows(run)
    if d is None:
        return None
    w = d[(d["t"] >= STEADY_FROM) & (d["t"] < d["t"].max() - 60)]
    if len(w) < 3:
        return None

    dec = {c: float(w[c].ffill().iloc[-1] - w[c].bfill().iloc[0])
           for c in ("route", "pend", "shed", "force") if c in w}
    n = max(sum(dec.values()), 1.0)
    route = 100.0 * dec.get("route", 0.0) / n

    gcols = [c for c in w.columns if c.startswith("gate::")]
    # Per instance, the median over the steady window; then how many of the four
    # are above chat's budget, i.e. holding no chat.
    medians = sorted(w[c].median() for c in gcols)
    free = sum(1 for m in medians if m > CHAT_NOMINAL_MS + 0.5)

    r = load_run(run)
    return dict(
        run=os.path.basename(run),
        route=route, force=100.0 * dec.get("force", 0.0) / n,
        chat_free=free, gates="/".join(f"{m:.0f}" for m in medians),
        off=attain(r, "violate_offered") if r is not None else float("nan"),
        state="COLLAPSED" if route < COLLAPSE_ROUTE else "healthy",
    )


ARM_RE = re.compile(r"_(fsbase|fluidserve|fsa|fsc|fskv|fsac|fsah|fsg\d+|polyserve|slo|loadbalance)_m1_rpm_")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", default=["results/*_m1_rpm_2700"])
    ap.add_argument("--arms", nargs="+",
                    default=["fsbase", "fluidserve", "fsc", "fskv"])
    a = ap.parse_args()

    rows = []
    for pat in a.runs:
        for d in sorted(glob.glob(pat)):
            m = ARM_RE.search(os.path.basename(d))
            if not m or m.group(1) not in a.arms:
                continue
            s = summarise(d)
            if s:
                # fsbase and fluidserve are the same configuration under two names.
                s["arm"] = "baseline" if m.group(1) in ("fsbase", "fluidserve") \
                    else m.group(1)
                rows.append(s)
    if not rows:
        sys.exit("no conditions matched")
    df = pd.DataFrame(rows).sort_values(["arm", "route"])

    print(f"{len(df)} conditions at 45 req/s. COLLAPSED = route share below "
          f"{COLLAPSE_ROUTE:.0f}% over the steady window.\n")
    print(f"{'run':<44}{'arm':>10}{'route%':>8}{'force%':>8}{'off':>7}"
          f"{'chat-free':>11}  gates (median per instance)")
    for _, r in df.iterrows():
        print(f"{r['run']:<44}{r['arm']:>10}{r['route']:>8.1f}{r['force']:>8.1f}"
              f"{r['off']:>7.1f}{r['chat_free']:>11d}  {r['gates']}"
              + ("   <-- COLLAPSED" if r["state"] == "COLLAPSED" else ""))

    print("\ncollapse rate per arm")
    print(f"{'arm':>10}{'n':>5}{'collapsed':>11}{'rate':>8}")
    for arm, g in df.groupby("arm"):
        c = (g.state == "COLLAPSED").sum()
        print(f"{arm:>10}{len(g):>5}{c:>11}{100*c/len(g):>7.0f}%")

    print("\nrule 3 -- does the state variable track the outcome? (baseline only)")
    b = df[df.arm == "baseline"]
    tab = collections.Counter((int(r.chat_free > 0), r.state) for r in b.itertuples())
    print(f"{'':>16}{'healthy':>10}{'COLLAPSED':>11}")
    for free, lab in ((1, "chat-free >=1"), (0, "no chat-free")):
        print(f"{lab:>16}{tab[(free,'healthy')]:>10}{tab[(free,'COLLAPSED')]:>11}")

    print("\nrule 1 -- the discriminating test: candidate C with NO chat-free engine")
    c = df[(df.arm == "fsc") & (df.chat_free == 0)]
    if c.empty:
        print("  no such condition yet; rule 1 cannot be read")
    else:
        print(f"  {len(c)} conditions, route share "
              f"{', '.join(f'{v:.1f}' for v in c.route)}")
        ok = (c.route >= COLLAPSE_ROUTE).all()
        print(f"  [{'PASS' if ok else 'FAIL'}] every one must stay above "
              f"{COLLAPSE_ROUTE:.0f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
