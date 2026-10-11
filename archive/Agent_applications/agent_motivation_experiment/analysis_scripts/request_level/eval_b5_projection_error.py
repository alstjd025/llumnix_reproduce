#!/usr/bin/env python3
"""B5 -- how wrong is the KV projection NOW, and does the error reach a decision.

Why this exists. "The deployed projection under-predicts 88.5% of the time and
its absolute error is 3.6x that of not projecting at all" is quoted in this
repository's design documents. It was measured on ONE run from 2026-07-31, and
section 48.3 of the same document identified the cause -- a deep-research length
profile short by 3.4x -- which EXP-48 then corrected. It has never been
re-measured. Meanwhile B1 found that the condition the projection feeds
(`newKv <= capMem`) accounts for 17-20% of refusal events above 35 req/s, not the
7.2% EXP-49 measured on an hour-long trace, so the term binds harder than the
audit assumed.

This re-scores the same four predictors on the current workload, aggregated over
the pinned FluidServe set rather than one run, and puts the result beside the
share of refusals that condition is responsible for.

  kv        current occupancy, no projection -- what --fluidserve-enable-flux=false does
  kv+in     occupancy plus the resident set's growth, no release term
  shipped   kv + inflow - outflow, the deployed projection
  slope     occupancy plus its own smoothed rate of change (EXP-49's candidate,
            which was rejected end to end: it cost 2.4 points of attainment)

Read only.  Prints; writes one CSV. Wraps exp48_projection_error.score so the
pairing logic has one definition.

Usage:
    python3 eval_b5_projection_error.py [--manifest static_sweep_clean_2026-08]
"""
import argparse
import collections
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))

from exp48_projection_error import score  # noqa: E402

PRED = ["kv", "kv+in", "shipped", "slope"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="static_sweep_clean_2026-08")
    ap.add_argument("--arm", default="FluidServe")
    a = ap.parse_args()

    mpath = os.path.join(EXPDIR, "paper_experiment", a.manifest, "manifest.tsv")
    runs = [r for r in csv.DictReader(open(mpath), delimiter="\t")
            if r["arm_label"] == a.arm]

    per_rate = collections.defaultdict(lambda: collections.defaultdict(list))
    rows = []
    for r in sorted(runs, key=lambda x: (float(x["req_per_s"]), x["run"])):
        p = os.path.join(EXPDIR, "results", r["run"],
                         "server_metrics", "scheduler.jsonl")
        if not os.path.exists(p):
            continue
        acc, occ, horizon = score(p)
        if not acc.get("shipped"):
            continue
        rate = float(r["req_per_s"])
        row = {"req_per_s": rate, "run": r["run"], "pairs": len(acc["shipped"]),
               "horizon_s": horizon}
        for k in PRED:
            e = np.asarray(acc[k], float)
            row["mean_" + k] = float(e.mean())
            row["mae_" + k] = float(np.abs(e).mean())
            row["under_" + k] = 100.0 * float((e < 0).mean())
        row["mae_ratio_shipped_over_kv"] = row["mae_shipped"] / row["mae_kv"]
        rows.append(row)
        for k, v in row.items():
            if k not in ("req_per_s", "run"):
                per_rate[rate][k].append(v)

    out = os.path.join(EXPDIR, "results", "aggregate_analysis",
                       "paper_eval_2026-08", "b5_projection_error.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def m(rate, key):
        return float(np.mean(per_rate[rate][key]))

    print("\nthe deployed projection against what the engines actually held one "
          "horizon later\n(%s, %d runs, horizon %.1f s, %d-%d pairs per run)"
          % (a.arm, len(rows), np.mean([r["horizon_s"] for r in rows]),
             min(r["pairs"] for r in rows), max(r["pairs"] for r in rows)))
    print("\n%6s %14s %14s %14s %16s" % (
        "req/s", "shipped MAE", "no-projection", "shipped/kv", "shipped under (%)"))
    for rate in sorted(per_rate):
        print("%6g %14.0f %14.0f %13.2fx %16.1f"
              % (rate, m(rate, "mae_shipped"), m(rate, "mae_kv"),
                 m(rate, "mae_ratio_shipped_over_kv"), m(rate, "under_shipped")))

    print("\nall four predictors, averaged over the whole set")
    print("%-10s %14s %14s %16s" % ("predictor", "mean error", "MAE", "under (%)"))
    for k in PRED:
        print("%-10s %14.0f %14.0f %16.1f"
              % (k, np.mean([r["mean_" + k] for r in rows]),
                 np.mean([r["mae_" + k] for r in rows]),
                 np.mean([r["under_" + k] for r in rows])))
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
