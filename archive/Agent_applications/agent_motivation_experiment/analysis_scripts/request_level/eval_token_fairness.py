#!/usr/bin/env python3
"""Fairness re-measured in tokens instead of requests.

WHY. Scored per request, the two leading policies each look like they sacrifice
a class: FluidServe refuses 64-82% of agent-class ARRIVALS above 35 req/s and
llm-d refuses 55-93% of chat's. But the classes are wildly different sizes per
request -- an agent arrival carries about 5.6k input tokens, a chat arrival about
0.7k -- so the m1 mix that is 76.9 / 15.4 / 7.7 percent of REQUESTS is roughly
30 / 40 / 30 percent of INPUT TOKENS. A refusal of one agent request frees
capacity worth several chat requests, so the request-unit view may overstate
how uneven the treatment is. This scores the same runs in token units.

THREE QUANTITIES PER CLASS, per run:

  input acceptance    admitted input tokens / offered input tokens. The
                      token-denominated form of (1 - rejection rate). Input
                      tokens are recorded on 100% of rejected rows, so the
                      denominator is complete.
  goodput share       the class's share of the run's SLO-met output tokens,
                      set against its share of offered input tokens. Equal
                      treatment in token terms puts these two shares close.
  token attainment    SLO-met output tokens / (arrivals x the class's measured
                      mean output length). The denominator estimates the output
                      the class WOULD have produced had everything been served,
                      using the measured per-class means (accurate to 0.97-1.00x
                      of the deployed profile, B5). Rejected requests never
                      generated, so their forgone output has to be estimated;
                      this is the estimate and it is named as one.

Read only.  Prints; writes one CSV.

Usage:
    python3 eval_token_fairness.py
"""
import collections
import csv
import glob
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))

from exp22_fluidserve import load_run  # noqa: E402

CLASSES = ["chat", "deepresearch", "swe"]
MEAN_OUT = {"chat": 415.0, "deepresearch": 968.3, "swe": 494.6}   # measured, B5
ARMS = [("FluidServe", "results/*exp82r[12]_fspfx_m1_rpm_%d"),
        ("llm-d", "results/*exp82r[12]_llmdslo_m1f_rpm_%d")]
RATES = [10, 15, 20, 25, 35, 45, 55, 70]


def one(run_dir):
    r = load_run(run_dir)
    if r is None or r.empty:
        return None
    it = pd.to_numeric(r["input_tokens"], errors="coerce").fillna(0)
    ot = pd.to_numeric(r["output_tokens"], errors="coerce").fillna(0)
    met = (~r["violate_offered"]) & (~r["cutoff"])
    out = {}
    for c in CLASSES:
        s = r["class"] == c
        offered_in = float(it[s].sum())
        admitted_in = float(it[s & ~r["rejected"]].sum())
        good_out = float(ot[s & met].sum())
        n = int(s.sum())
        out[c] = {
            "offered_in": offered_in,
            "accept_in": 100.0 * admitted_in / offered_in if offered_in else np.nan,
            "reject_req": 100.0 * r.loc[s, "rejected"].mean(),
            "good_out": good_out,
            "tok_attain": 100.0 * good_out / (n * MEAN_OUT[c]) if n else np.nan,
            "req_attain": 100.0 * float((met & s).sum()) / n if n else np.nan,
        }
    return out


def main():
    rows = []
    agg = collections.defaultdict(lambda: collections.defaultdict(list))
    for arm, pat in ARMS:
        for rate in RATES:
            for d in sorted(glob.glob(os.path.join(EXPDIR, pat % (rate * 60)))):
                if "PRERUN" in d:
                    continue
                v = one(d)
                if v is None:
                    continue
                tot_in = sum(v[c]["offered_in"] for c in CLASSES)
                tot_good = sum(v[c]["good_out"] for c in CLASSES)
                for c in CLASSES:
                    row = dict(arm=arm, rate=rate, cls=c,
                               run=os.path.basename(d), **v[c])
                    row["share_offered_in"] = 100.0 * v[c]["offered_in"] / tot_in
                    row["share_good_out"] = (100.0 * v[c]["good_out"] / tot_good
                                             if tot_good else np.nan)
                    rows.append(row)
                    agg[(arm, rate)][c].append(row)

    out = os.path.join(EXPDIR, "results", "aggregate_analysis",
                       "paper_eval_2026-08", "token_fairness.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def rng(arm, rate, c, key):
        vs = [x[key] for x in agg[(arm, rate)][c]]
        return "%.0f-%.0f" % (min(vs), max(vs)) if max(vs) - min(vs) >= 0.5 \
            else "%.0f" % vs[0]

    for arm, _ in ARMS:
        print("\n=== %s" % arm)
        print("-- input-token acceptance (%%)  [request rejection %% in brackets]")
        print("req/s".rjust(6) + "".join(c.rjust(22) for c in CLASSES))
        for rate in RATES:
            line = ("%g" % rate).rjust(6)
            for c in CLASSES:
                line += ("%s  [rej %s]" % (rng(arm, rate, c, "accept_in"),
                                           rng(arm, rate, c, "reject_req"))).rjust(22)
            print(line)
        print("-- share of offered INPUT tokens -> share of SLO-met OUTPUT tokens")
        print("req/s".rjust(6) + "".join(c.rjust(22) for c in CLASSES))
        for rate in RATES:
            line = ("%g" % rate).rjust(6)
            for c in CLASSES:
                line += ("%s -> %s" % (rng(arm, rate, c, "share_offered_in"),
                                       rng(arm, rate, c, "share_good_out"))).rjust(22)
            print(line)
        print("-- token attainment (%%)  [request attainment %% in brackets]")
        print("req/s".rjust(6) + "".join(c.rjust(22) for c in CLASSES))
        for rate in RATES:
            line = ("%g" % rate).rjust(6)
            for c in CLASSES:
                line += ("%s  [%s]" % (rng(arm, rate, c, "tok_attain"),
                                       rng(arm, rate, c, "req_attain"))).rjust(22)
            print(line)
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
