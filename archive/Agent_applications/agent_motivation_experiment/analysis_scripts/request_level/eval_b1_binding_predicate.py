#!/usr/bin/env python3
"""B1 -- which of FluidServe's four refusal conditions actually blocks a placement.

Why this exists. The claim "the pace gate is the bottleneck and memory never
binds" has been carried in five documents from EXP-67b, which ran BEFORE the
2026-08-08 workload fix. EXP-73 measured 18.8% memory at 45 req/s afterwards, so
the sentence is not true of the deployed configuration. This recomputes the
shares on the pinned five-arm set, which is the current workload, all eight
arrival rates, two repeats.

What is read. `server_metrics/scheduler.jsonl` publishes
`scheduler_fluidserve_infeasible_total{reason}` and
`scheduler_fluidserve_decisions_total{decision}` about once a second. The runner
restarts the scheduler for every condition, so the counter starts at zero and
its last sample is that condition's total. A reason that never fires does not
appear as a series at all, which is why absence is reported as zero rather than
dropped.

What this does NOT say. The share of refusals a condition accounts for is not
the share of requests it costs: one arriving request is tested against every
instance, so a request refused everywhere contributes four events. Read it as
"when an instance was rejected as a destination, why", not as "this fraction of
requests failed because of X".

Usage:
    python3 eval_b1_binding_predicate.py [--manifest static_sweep_clean_2026-08]
"""
import argparse
import collections
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))

REASONS = ("gate", "incumbents", "memory", "unpredictable")
DECISIONS = ("route", "pend", "shed", "force")


def last_counters(run_dir, prefix):
    """Last sample of every series whose key starts with prefix, by label value."""
    path = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(path):
        return None
    out = {}
    with open(path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for k, v in d.items():
                if k.startswith(prefix) and "|" in k:
                    out[k.split("=", 1)[1]] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="static_sweep_clean_2026-08")
    ap.add_argument("--arm", default="FluidServe")
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    mpath = os.path.join(EXPDIR, "paper_experiment", a.manifest, "manifest.tsv")
    rows = list(csv.DictReader(open(mpath), delimiter="\t"))
    runs = [r for r in rows if r["arm_label"] == a.arm]
    if not runs:
        sys.exit("no runs for arm %r in %s" % (a.arm, mpath))

    per_rate = collections.defaultdict(list)
    for r in runs:
        run_dir = os.path.join(EXPDIR, "results", r["run"])
        inf = last_counters(run_dir, "scheduler_fluidserve_infeasible_total")
        dec = last_counters(run_dir, "scheduler_fluidserve_decisions_total")
        if inf is None or dec is None:
            print("  ! %s has no scheduler.jsonl -- skipped" % r["run"], file=sys.stderr)
            continue
        tot = sum(inf.get(k, 0.0) for k in REASONS)
        dtot = sum(dec.get(k, 0.0) for k in DECISIONS)
        per_rate[float(r["req_per_s"])].append({
            "run": r["run"],
            "infeasible_total": tot,
            **{"r_" + k: (100.0 * inf.get(k, 0.0) / tot if tot else 0.0) for k in REASONS},
            "decisions_total": dtot,
            **{"d_" + k: (100.0 * dec.get(k, 0.0) / dtot if dtot else 0.0) for k in DECISIONS},
        })

    out = a.out or os.path.join(EXPDIR, "results", "aggregate_analysis",
                                "paper_eval_2026-08", "b1_binding_predicate.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cols = (["req_per_s", "run", "infeasible_total"] + ["r_" + k for k in REASONS]
            + ["decisions_total"] + ["d_" + k for k in DECISIONS])
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for rate in sorted(per_rate):
            for row in per_rate[rate]:
                w.writerow(dict(row, req_per_s=rate))

    def rng(rate, key):
        vs = [row[key] for row in per_rate[rate]]
        return ("%.1f" % vs[0]) if len(set(round(v, 1) for v in vs)) == 1 \
            else "%.1f-%.1f" % (min(vs), max(vs))

    print("\n%s -- share of refusal events by which condition refused (%%), "
          "%d repeats per rate" % (a.arm, len(per_rate[sorted(per_rate)[0]])))
    print("  a request is tested against every instance, so this is per (request, instance) event")
    hdr = "req/s".rjust(6) + "".join(k.rjust(14) for k in REASONS) + "events".rjust(12)
    print(hdr)
    for rate in sorted(per_rate):
        line = ("%.0f" % rate).rjust(6)
        line += "".join(rng(rate, "r_" + k).rjust(14) for k in REASONS)
        line += ("%.0f" % (sum(r["infeasible_total"] for r in per_rate[rate])
                           / len(per_rate[rate]))).rjust(12)
        print(line)

    print("\n%s -- what the policy decided (%% of decisions)" % a.arm)
    print("req/s".rjust(6) + "".join(k.rjust(14) for k in DECISIONS))
    for rate in sorted(per_rate):
        print(("%.0f" % rate).rjust(6)
              + "".join(rng(rate, "d_" + k).rjust(14) for k in DECISIONS))
    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
