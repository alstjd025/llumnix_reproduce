#!/usr/bin/env python3
"""Did the placements we actually made land in the band the two accountings dispute?

16_budget_stock.md measured that on 3.9-6.0% of instance-seconds of the hour
trace, the pace an instance is delivering falls BETWEEN its nominal minimum
(gate_allowance_ms, what llm-d-style accounting would hold it to) and its
remaining-budget minimum (tightest_allowance_ms, what FluidServe holds it to).
That is a statement about states, not about decisions: it says the disagreement
exists, not that we ever acted on it.

This asks the decision question. For every request the scheduler dispatched, join
its dispatch instant to that instance's gauges at the nearest scrape and classify:

  in-band      gate < step <= tight
               the nominal accounting would have called this instance too tight
               to take the request; the remaining-budget accounting called it fine
               and we placed there. These are placements the nominal rule forgoes.
  inverted     tight < step <= gate
               the nominal accounting would have thought there was room the
               residents no longer had.
  agree        both accountings say the same thing.

Then the placements are scored: of the in-band ones, what share met their SLO. If
they meet at a rate comparable to the rest, the capacity the nominal rule forgoes
is real capacity. If they miss, the nominal rule's conservatism was right.

CAVEATS, all of which change how the numbers read.
  - The dispatch log gives no way to tell a routed placement from a forced one,
    so the population is "placed". Forced placements are 1.3-2.3% of decisions.
  - Gauges are scraped about once a second and a placement is matched to the
    nearest scrape within a tolerance; a placement is classified on the
    instance's state around it, not at the instant.
  - `tightest_allowance` excludes residents already slower than the delivered
    pace, so the inverted side is a lower bound (fluidserve.go:1047).
  - This is measured on FluidServe runs. It does not say what llm-d decided.

Read only.  Prints; writes one CSV.

Usage:
    python3 eval_stock_placements.py 'results/*exp91r[12]_fspfx_fullb' --label hour
"""
import argparse
import collections
import csv
import datetime
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))
from exp22_fluidserve import load_run                       # noqa: E402
from tail2026_holdwindow_state import dispatch_pairs        # noqa: E402

LAB = re.compile(r"\|.*?=(.+)$")
W = {"scheduler_fluidserve_gate_allowance_ms": "gate",
     "scheduler_fluidserve_tightest_allowance_ms": "tight",
     "scheduler_fluidserve_observed_step_ms": "step"}
TOL = 1.5    # seconds; a dispatch is matched to a scrape within this


def gauges(run_dir):
    """instance -> (times, {field: array}), sorted by time."""
    acc = collections.defaultdict(list)
    p = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    with open(p, errors="replace") as f:
        for line in f:
            try:
                q = json.loads(line)
            except ValueError:
                continue
            t = q.get("t")
            if t is None:
                continue
            per = collections.defaultdict(dict)
            for k, v in q.items():
                if "|" not in k:
                    continue
                m = LAB.search(k)
                if not m:
                    continue
                for name, short in W.items():
                    if k.startswith(name):
                        per[m.group(1)][short] = v
            for inst, s in per.items():
                if len(s) == len(W):
                    acc[inst].append((t, s))
    out = {}
    for inst, rows in acc.items():
        rows.sort(key=lambda r: r[0])
        out[inst] = (np.array([r[0] for r in rows]),
                     {f: np.array([r[1][f] for r in rows]) for f in W.values()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("patterns", nargs="+")
    ap.add_argument("--label", default="")
    a = ap.parse_args()

    dirs = []
    for p in a.patterns:
        dirs.extend(sorted(d for d in glob.glob(os.path.join(EXPDIR, p))
                           if "PRERUN" not in d))
    rows = []
    for d in dirs:
        r = load_run(d)
        if r is None or r.empty:
            continue
        # request id -> (t, instance)
        year = datetime.datetime.fromtimestamp(
            float(r["start_time"].min()), datetime.timezone.utc).year
        disp = dispatch_pairs(d, year)
        ids = []
        p = os.path.join(d, "request_ids.jsonl")
        with open(p, errors="replace") as fh:
            for line in fh:
                try:
                    q = json.loads(line)
                except ValueError:
                    continue
                ids.append((q.get("task_id"), q.get("call_index"),
                            str(q.get("request_id", "")).replace("cmpl-", "")))
        idf = pd.DataFrame(ids, columns=["task_id", "call_index", "rid"])
        idf["call_index"] = pd.to_numeric(idf["call_index"], errors="coerce")
        r = r.copy()
        r["call_index"] = pd.to_numeric(r["call_index"], errors="coerce")
        m = r.merge(idf, on=["task_id", "call_index"], how="left")
        m = m[~m["rejected"] & m["rid"].notna()]

        g = gauges(d)
        cls = collections.Counter()
        met = collections.Counter()
        for rid, met_flag in zip(m["rid"], ~m["violate_offered"] & ~m["cutoff"]):
            if rid not in disp:
                continue
            t, inst = disp[rid]
            if inst not in g:
                continue
            ts, f = g[inst]
            i = int(np.searchsorted(ts, t))
            best = None
            for j in (i - 1, i):
                if 0 <= j < len(ts) and abs(ts[j] - t) <= TOL:
                    if best is None or abs(ts[j] - t) < abs(ts[best] - t):
                        best = j
            if best is None:
                cls["unmatched"] += 1
                continue
            gate, tight, step = f["gate"][best], f["tight"][best], f["step"][best]
            if gate <= 0 or tight <= 0 or step <= 0:
                cls["nogauge"] += 1
                continue
            if gate < step <= tight:
                k = "in_band"
            elif tight < step <= gate:
                k = "inverted"
            else:
                k = "agree"
            cls[k] += 1
            met[k] += bool(met_flag)
        tot = sum(cls[k] for k in ("in_band", "inverted", "agree"))
        if not tot:
            continue
        row = {"run": os.path.basename(d), "label": a.label, "classified": tot,
               "unmatched": cls["unmatched"], "nogauge": cls["nogauge"]}
        for k in ("in_band", "inverted", "agree"):
            row[k + "_pct"] = 100.0 * cls[k] / tot
            row[k + "_met"] = 100.0 * met[k] / cls[k] if cls[k] else np.nan
            row[k + "_n"] = cls[k]
        rows.append(row)
        print("  %-40s placed %6d  in-band %5.2f%% (met %5.1f%%, n=%d)  "
              "inverted %4.2f%% (met %5.1f%%)  agree met %5.1f%%"
              % (row["run"][12:], tot, row["in_band_pct"], row["in_band_met"],
                 row["in_band_n"], row["inverted_pct"], row["inverted_met"],
                 row["agree_met"]))

    if not rows:
        sys.exit("nothing measured")
    out = os.path.join(EXPDIR, "results", "aggregate_analysis",
                       "paper_eval_2026-08", "stock_placements.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    old = []
    if os.path.exists(out):
        with open(out) as f:
            old = [x for x in csv.DictReader(f) if x.get("label") != a.label]
    keys = sorted({k for x in rows + old for k in x})
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(old + rows)
    print("\nwrote %s (label=%s)" % (out, a.label))


if __name__ == "__main__":
    main()
