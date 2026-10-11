#!/usr/bin/env python3
"""Does a resident request's budget become a stock, and does nominal accounting miss it?

THE QUESTION THIS ANSWERS. The paper says existing routers ignore how the
requests already running have been doing. The obvious rebuttal is that llm-d and
Llumnix DO predict the TTFT and TPOT a new request would see, and predicting that
is predicting the state of the residents. The distinction is stock versus rate.

A request that has produced 300 tokens at 40 ms each against a 50 ms budget has
BANKED 3 seconds; its remaining tokens may run at 60 ms and still meet the SLO.
One that ran slow is in DEBT and its remaining tokens must beat the budget. llm-d
keeps, per endpoint, the tightest NOMINAL TPOT SLO among running requests -- a
constant carried on the request header, never revised by how that request has
actually been served (running_request_tpot_slo_queue.go). FluidServe computes
allowanceMs = (budget left) / (tokens left) per resident, and takes the minimum of
THAT (fluidserve_registry.go:613-654, fluidserve.go:1035-1052).

The scheduler publishes both minima per instance, so the two accountings can be
differenced directly on our own runs:

  gate_allowance_ms      min over residents of the NOMINAL per-token budget
  tightest_allowance_ms  min over residents of the REMAINING budget per token

  divergence = tightest - gate
    > 0  the residents have banked: nominal accounting locks the instance
         tighter than the requests actually need   (FALSE TIGHT -> wasted capacity)
    < 0  the residents are in debt: nominal accounting thinks there is room the
         requests no longer have                   (FALSE LOOSE -> over-admission)

WHAT THIS IS AND IS NOT. It is a measurement of the QUANTITY the nominal
accounting cannot see, taken on runs of our policy, because only our policy
publishes the remaining-budget minimum. It is NOT a re-simulation of llm-d and
does not claim what llm-d would have decided.

TWO CAVEATS THAT CHANGE THE NUMBERS, both handled:
  - tightest_allowance EXCLUDES residents already slower than the instance's
    delivered pace (fluidserve.go:1047), i.e. exactly the deepest debtors, so the
    published series UNDERSTATES the debt side. Reported as a bound, and the
    request-level half of this script measures debt without that filter.
  - an instance holding nothing publishes -1 for both; those samples are dropped.

Read only. Prints; writes one CSV.

Usage:
    python3 eval_budget_stock.py 'results/*exp91r[12]_*_fullb' --label hour
"""
import argparse
import collections
import csv
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
from exp22_fluidserve import load_run, mean_inter_token_ms  # noqa: E402

LAB = re.compile(r"\|.*?=(.+)$")
GATE = "scheduler_fluidserve_gate_allowance_ms"
TIGHT = "scheduler_fluidserve_tightest_allowance_ms"
STEP = "scheduler_fluidserve_observed_step_ms"
NOMINAL = {"chat": 50.0, "deepresearch": 100.0}   # per-token tiers; swe is end-to-end


def instance_samples(run_dir):
    """(t, instance) -> {gate, tight, step}, only where both minima are finite."""
    p = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(p):
        return []
    out = []
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
                for name, short in ((GATE, "gate"), (TIGHT, "tight"), (STEP, "step")):
                    if k.startswith(name):
                        per[m.group(1)][short] = v
            for inst, s in per.items():
                if s.get("gate", -1) > 0 and s.get("tight", -1) > 0:
                    out.append((t, inst, s))
    return out


def request_stock(run_dir):
    """Per completed request: banked ms against its class's nominal per-token budget.

    Positive = ran faster than budget and has slack; negative = behind. Only the
    two per-token classes; the agent class is scored end to end and has no
    per-token nominal to bank against.
    """
    r = load_run(run_dir)
    if r is None or r.empty:
        return None
    ttft = pd.to_numeric(r["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(r["latency"], errors="coerce")
    itl = mean_inter_token_ms(r, ttft, e2e)
    ntok = pd.to_numeric(r["output_tokens"], errors="coerce")
    ok = (~r["rejected"]) & (~r["cutoff"]) & (~r["errored"]) & itl.notna() & (ntok > 1)
    rows = {}
    for c, budget in NOMINAL.items():
        sel = ok & (r["class"] == c)
        if not sel.any():
            continue
        # banked over the whole decode, in seconds
        rows[c] = ((budget - itl[sel]) * (ntok[sel] - 1) / 1000.0).values
    return rows


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
        s = instance_samples(d)
        if not s:
            print("  ! %s: no allowance series" % os.path.basename(d), file=sys.stderr)
            continue
        div = np.array([x[2]["tight"] - x[2]["gate"] for x in s])
        gate = np.array([x[2]["gate"] for x in s])
        # A divergence only matters if a placement's pace could fall between the
        # two minima; size it relative to the nominal the instance is held to.
        rel = 100.0 * div / gate
        stock = request_stock(d)
        row = {
            "run": os.path.basename(d), "label": a.label, "n_samples": len(s),
            "div_p10": float(np.percentile(div, 10)),
            "div_p50": float(np.percentile(div, 50)),
            "div_p90": float(np.percentile(div, 90)),
            "rel_p50": float(np.percentile(rel, 50)),
            "banked_pct": 100.0 * float((div > 1.0).mean()),
            "debt_pct": 100.0 * float((div < -1.0).mean()),
            "wide_pct": 100.0 * float((np.abs(div) > 10.0).mean()),
        }
        for c in NOMINAL:
            v = stock.get(c) if stock else None
            if v is not None and len(v):
                row["stock_%s_p10" % c] = float(np.percentile(v, 10))
                row["stock_%s_p50" % c] = float(np.percentile(v, 50))
                row["stock_%s_p90" % c] = float(np.percentile(v, 90))
                row["behind_%s_pct" % c] = 100.0 * float((v < 0).mean())
        rows.append(row)
        print("  %-42s samples %6d  divergence p10/p50/p90 %+6.1f /%+6.1f /%+6.1f ms"
              "  banked %5.1f%%  debt %5.1f%%"
              % (row["run"][12:], row["n_samples"], row["div_p10"], row["div_p50"],
                 row["div_p90"], row["banked_pct"], row["debt_pct"]))

    if not rows:
        sys.exit("nothing measured")
    out = os.path.join(EXPDIR, "results", "aggregate_analysis",
                       "paper_eval_2026-08", "budget_stock.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    write_header = not os.path.exists(out)
    keys = sorted({k for r in rows for k in r})
    old = []
    if not write_header:
        with open(out) as f:
            old = [r for r in csv.DictReader(f) if r.get("label") != a.label]
        keys = sorted(set(keys) | {k for r in old for k in r})
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(old + rows)
    print("\nwrote %s (label=%s)" % (out, a.label))


if __name__ == "__main__":
    main()
