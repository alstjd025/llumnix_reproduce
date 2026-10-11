#!/usr/bin/env python3
"""EXP-34 - how often the KV projection changes a decision.

The projection enters at one place, newKv = proj + cost, and reaches the outcome
only through the feasibility test. So the question "does the fluidic KV model
matter here" reduces to "how often does the prediction land within the
projection's width of the gate", and that is a count, not an outcome comparison.

Three levels are counted separately because they are three different claims:

  candidate   one instance's feasibility flipped. The rate here is about the
              width of the band around the gate.
  decision    the request would have been placed rather than held, or the
              reverse. This is the one that changes what a client sees.
  target      the request is placed either way but on a different instance.
              Real, but second-order: both instances were judged feasible.

Reads the counters straight out of the scheduler scrape and reports each as a
fraction of the matching evaluation count.

Usage
-----
  python3 exp34_flux_flips.py 'results/*exp34flux*'
"""
import collections
import glob
import json
import os
import re
import sys


def rate_of(run_dir):
    m = re.search(r"_rpm_(\d+)", os.path.basename(run_dir))
    return int(m.group(1)) if m else None


def counters(run_dir, skip_s=120.0):
    """Total increments of each counter over the run, past the opening minutes."""
    path = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(path):
        return None
    first, last = {}, {}
    t0 = None
    for line in open(path):
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("t")
        if t is None:
            continue
        if t0 is None:
            t0 = t
        if t - t0 < skip_s:
            continue
        for k, v in r.items():
            if not isinstance(k, str) or not isinstance(v, (int, float)):
                continue
            if "flux_flips_total" in k or "flux_evaluations_total" in k:
                first.setdefault(k, v)
                last[k] = v
    return {k: last[k] - first[k] for k in last}


def level_of(key):
    m = re.search(r'level="?([a-z]+)"?', key)
    return m.group(1) if m else "?"


def main():
    pats = sys.argv[1:] or ["results/*exp34flux*"]
    runs = sorted({d for p in pats for d in glob.glob(p)})
    if not runs:
        sys.exit("no runs matched")

    print(f"\n{'run':<44}{'rate':>7}{'level':>11}{'flips':>12}"
          f"{'evaluations':>14}{'share':>9}")
    print("-" * 97)
    for d in runs:
        c = counters(d)
        if not c:
            print(f"{os.path.basename(d)[:43]:<44}  (no scheduler metrics)")
            continue
        flips = collections.defaultdict(float)
        evals = collections.defaultdict(float)
        for k, v in c.items():
            if "flux_flips_total" in k:
                flips[level_of(k)] += v
            else:
                evals[level_of(k)] += v
        if not flips and not evals:
            print(f"{os.path.basename(d)[:43]:<44}  (counters absent -- binary "
                  f"predates the instrumentation, or not on the collector list)")
            continue
        rpm = rate_of(d)
        rate = f"{rpm/60:.0f}/s" if rpm else "?"
        for lvl in ("candidate", "decision", "target"):
            # target flips are counted only among requests that were placed
            # either way, so their denominator is the decision count too.
            den = evals.get(lvl if lvl == "candidate" else "decision", 0.0)
            f = flips.get(lvl, 0.0)
            share = f"{100*f/den:.3f}%" if den else "-"
            print(f"{os.path.basename(d)[:43]:<44}{rate:>7}{lvl:>11}"
                  f"{f:>12,.0f}{den:>14,.0f}{share:>9}")
        print()

    print("reading it (EXP-34 pre-registered):")
    print("  decision-level below 1%   the projection is not what decides here.")
    print("                            The system-effect ablation is not worth")
    print("                            two hours; change the workload instead.")
    print("  decision-level above 10%  it decides often. The ablation is worth")
    print("                            running because there is an effect large")
    print("                            enough to clear the six-point spread.")
    print("  in between                run the ablation at 60 req/s only, where")
    print("                            the spread is 0.05 points rather than 6.")


if __name__ == "__main__":
    main()
