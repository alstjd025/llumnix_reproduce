#!/usr/bin/env python3
"""B2 -- of the requests FluidServe chose to PLACE, what share missed anyway.

Why this exists. The policy refuses a request when no instance can carry it and
places it otherwise, so every placement is a prediction: "this request will be
served inside its budget here". That prediction has never been scored. A reviewer
asks it directly -- an admission controller that admits what it cannot serve is
not an admission controller -- and `28_backlog_at_placement.md` already found one
slice of the answer by hand: at 45 req/s, deep research requests held 2-5 s were
routed onto an instance whose prefill queue was 76,813-87,845 tokens, and took
10.3-11.6 s to their first token against a 10 s budget.

This generalises that to every placed request at all eight arrival rates, and
splits the failures by WHICH rule broke, because the remedy differs: a first-token
failure is a queue the feasibility test does not look at, and a per-token failure
is the pace model being wrong or the instance changing after the decision.

Denominator, stated because it is the whole point. This is NOT attainment. The
denominator is placed requests only -- rejected requests are excluded by
construction, since the question is about the placements. So a policy that
refuses everything scores perfectly here, which is why it is reported beside the
rejection rate and never alone.

What limits it. The join is metrics.csv -> request_ids.jsonl -> the scheduler's
`[Schedule] dispatch request` lines. Those lines are dropped under load in some
conditions (motivation.md 9.2 records 86.4% on one PolyServe hour), so the join
rate is printed per run and a run below 95% is flagged rather than quietly
averaged in.

`force` placements cannot be told from `route` placements in the dispatch log, so
the population here is "placed", not "called feasible". From B1's decision
counters force is 0.0-2.2% of decisions, which bounds the difference.

Read only.  Prints; writes one CSV.

Usage:
    python3 eval_b2_feasible_but_missed.py [--manifest static_sweep_clean_2026-08]
"""
import argparse
import collections
import csv
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))

from exp22_fluidserve import SLO_RULES, mean_inter_token_ms  # noqa: E402
from tail2026_holdwindow_state import placement_frame        # noqa: E402

CLASSES = ["chat", "deepresearch", "swe"]


def classify(m):
    """Per placed request: did it miss, and which clause of its rule broke."""
    ttft = pd.to_numeric(m["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(m["latency"], errors="coerce")
    tbt = mean_inter_token_ms(m, ttft, e2e)
    kind = pd.Series("met", index=m.index, dtype=object)
    for cname, rule in SLO_RULES.items():
        sel = m["class"] == cname
        if "e2e" in rule:
            kind.loc[sel & (e2e > rule["e2e"])] = "e2e"
        else:
            slow_first = ttft > rule["ttft"]
            slow_pace = tbt > rule["tbt"]
            kind.loc[sel & slow_first & ~slow_pace] = "ttft"
            kind.loc[sel & ~slow_first & slow_pace] = "pace"
            kind.loc[sel & slow_first & slow_pace] = "both"
    # No first token at all, and not cut off by the window closing: nothing was
    # produced, which is a miss under any rule rather than a missing value.
    kind.loc[ttft.isna() & ~m["cutoff"]] = "nothing"
    return kind


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="static_sweep_clean_2026-08")
    ap.add_argument("--arm", default="FluidServe")
    ap.add_argument("--min-join", type=float, default=95.0)
    a = ap.parse_args()

    mpath = os.path.join(EXPDIR, "paper_experiment", a.manifest, "manifest.tsv")
    runs = [r for r in csv.DictReader(open(mpath), delimiter="\t")
            if r["arm_label"] == a.arm]
    if not runs:
        sys.exit("no runs for arm %r" % a.arm)

    out_rows, per_rate = [], collections.defaultdict(list)
    for r in sorted(runs, key=lambda x: (float(x["req_per_s"]), x["run"])):
        run_dir = os.path.join(EXPDIR, "results", r["run"])
        m, join = placement_frame(run_dir)
        if m is None or not len(m):
            print("  ! %s: no placement frame" % r["run"], file=sys.stderr)
            continue
        m = m[~m["rejected"]].copy()
        m["kind"] = classify(m)
        rate = float(r["req_per_s"])
        n = len(m)
        row = {"req_per_s": rate, "run": r["run"], "join_pct": join, "placed": n,
               "missed_pct": 100.0 * (m["kind"] != "met").mean()}
        for k in ("ttft", "pace", "both", "e2e", "nothing"):
            row["k_" + k] = 100.0 * (m["kind"] == k).mean()
        for c in CLASSES:
            s = m[m["class"] == c]
            row["miss_" + c] = 100.0 * (s["kind"] != "met").mean() if len(s) else np.nan
        out_rows.append(row)
        per_rate[rate].append(row)
        flag = "" if join >= a.min_join else "   <-- JOIN BELOW %.0f%%" % a.min_join
        print("  %-46s join %5.1f%%  placed %6d  missed %5.1f%%%s"
              % (r["run"], join, n, row["missed_pct"], flag))

    outp = os.path.join(EXPDIR, "results", "aggregate_analysis",
                        "paper_eval_2026-08", "b2_feasible_but_missed.csv")
    os.makedirs(os.path.dirname(outp), exist_ok=True)
    with open(outp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(out_rows[0].keys()))
        w.writeheader()
        w.writerows(out_rows)

    def rng(rate, key):
        vs = [r[key] for r in per_rate[rate] if not np.isnan(r[key])]
        if not vs:
            return "-"
        return ("%.1f" % vs[0]) if max(vs) - min(vs) < 0.05 \
            else "%.1f-%.1f" % (min(vs), max(vs))

    print("\n%s -- of PLACED requests, %% that missed their own budget "
          "(denominator excludes rejections)" % a.arm)
    print("req/s".rjust(6) + "missed".rjust(12) + "".join(
        c.rjust(14) for c in CLASSES))
    for rate in sorted(per_rate):
        print(("%.0f" % rate).rjust(6) + rng(rate, "missed_pct").rjust(12)
              + "".join(rng(rate, "miss_" + c).rjust(14) for c in CLASSES))

    print("\nwhich clause broke, %% of ALL placed requests")
    print("req/s".rjust(6) + "".join(k.rjust(12) for k in
                                     ("ttft", "pace", "both", "e2e", "nothing")))
    for rate in sorted(per_rate):
        print(("%.0f" % rate).rjust(6) + "".join(
            rng(rate, "k_" + k).rjust(12)
            for k in ("ttft", "pace", "both", "e2e", "nothing")))
    print("\nwrote %s" % outp)


if __name__ == "__main__":
    main()
