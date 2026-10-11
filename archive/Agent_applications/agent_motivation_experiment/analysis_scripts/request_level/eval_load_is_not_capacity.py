#!/usr/bin/env python3
"""How often is the least-loaded instance the one that cannot take the request?

The paper's motivation says an instance's ability to accept a given request is
not a function of how loaded it is: an instance is held to the tightest per-token
budget among the requests already on it, so one chat request (50 ms) makes an
otherwise idle instance unusable for anything that needs a slower pace to be
worth placing there, while a busier instance holding only deep research (100 ms)
stays usable.

That has been argued from the code and from refusal-reason counters. This measures
it directly, and in the form a load-balancing router would see:

  at every scrape, rank the instances by `obs_decode_batch` -- the quantity a
  load-aware router minimises -- and ask whether the instance it would pick is
  one whose `gate_allowance_ms` is the TIGHTEST on the fleet while some
  more-loaded instance has a looser one.

`gate_allowance_ms` is what the policy publishes as the pace an instance may be
held to, derived from the budgets of the requests resident on it. It takes the
class budget values directly: 50 (chat), 62.5 (agent), 100 (deep research).

Two numbers come out, and they answer different questions:

  disagree      the least-loaded instance does not have the loosest allowance.
                This is the weak form: load and admissible pace are not the same
                ordering.
  inverted      the least-loaded instance has the TIGHTEST allowance on the
                fleet AND another instance has a strictly looser one. This is
                the strong form: choosing by load picks the instance that can
                accept the least.

Both are reported per arrival rate with the spread over repeats, plus how much
less loaded the least-loaded instance was when the inversion happened -- an
inversion of one request is not the same claim as an inversion of forty.

A SECOND TABLE, which turned out to carry more than the first. Counting how many
DISTINCT allowances the fleet offers at each scrape says how much room a router
has to differentiate at all, and it moves non-monotonically with load: three
distinct paces below 20 req/s, a collapse to one at 25 where 61-74% of scrapes
have every instance on the same value, and then a settling at two -- about three
quarters of instance-samples at chat's 50 ms and about a fifth at deep research's
100 ms, which on four engines is one engine. That is the dedicated instance
`28_backlog_at_placement.md` diagnosed, visible from the fleet side.

An allowance of -1 is published when an instance holds nothing, so it is reported
separately rather than counted as a fourth pace.

Read only.  Prints; writes one CSV.

Usage:
    python3 eval_load_is_not_capacity.py [--manifest static_sweep_clean_2026-08]
"""
import argparse
import collections
import csv
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))

BATCH = "scheduler_fluidserve_obs_decode_batch"
GATE = "scheduler_fluidserve_gate_allowance_ms"
LIVE = "scheduler_fluidserve_live_requests"
LABEL = re.compile(r"\|.*?=(.+)$")


def samples(run_dir):
    """Yield {instance: {batch, gate, live}} for every scrape that has all four."""
    path = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(path):
        return
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            per = collections.defaultdict(dict)
            for k, v in d.items():
                for name, short in ((BATCH, "batch"), (GATE, "gate"), (LIVE, "live")):
                    if k.startswith(name) and "|" in k:
                        m = LABEL.search(k)
                        if m:
                            per[m.group(1)][short] = v
            full = {i: s for i, s in per.items() if len(s) == 3}
            if len(full) >= 2:
                yield full


def score_run(run_dir):
    n = disagree = inverted = uniform = 0
    gaps, live_gaps, ndistinct = [], [], []
    mix = collections.Counter()
    for s in samples(run_dir):
        vals = [round(s[i]["gate"], 1) for i in s]
        for v in vals:
            mix[v] += 1
        # An empty instance publishes -1; it is not a fourth pace, so the count
        # of distinct paces ignores it and the share is reported on its own.
        real = {v for v in vals if v > 0}
        ndistinct.append(len(real))
        if len(real) <= 1:
            uniform += 1
        insts = list(s)
        # The instance a load-aware router picks: fewest requests decoding.
        pick = min(insts, key=lambda i: s[i]["batch"])
        loosest = max(s[i]["gate"] for i in insts)
        tightest = min(s[i]["gate"] for i in insts)
        n += 1
        if s[pick]["gate"] < loosest - 1e-9:
            disagree += 1
        # Strong form: the pick is at the fleet's tightest allowance and some
        # other instance is strictly looser.
        if abs(s[pick]["gate"] - tightest) < 1e-9 and loosest > tightest + 1e-9:
            inverted += 1
            other = [i for i in insts if s[i]["gate"] > tightest + 1e-9]
            gaps.append(min(s[i]["batch"] for i in other) - s[pick]["batch"])
            live_gaps.append(min(s[i]["live"] for i in other) - s[pick]["live"])
    if not n:
        return None
    tot = sum(mix.values())
    out = {"scrapes": n,
           "disagree_pct": 100.0 * disagree / n,
           "inverted_pct": 100.0 * inverted / n,
           "median_batch_gap": float(np.median(gaps)) if gaps else np.nan,
           "median_live_gap": float(np.median(live_gaps)) if live_gaps else np.nan,
           "one_pace_pct": 100.0 * uniform / n,
           "mean_distinct_paces": float(np.mean(ndistinct))}
    for v, name in ((50.0, "p50"), (62.5, "p62"), (100.0, "p100"), (-1.0, "pidle")):
        # 62.5 is published rounded; accept either spelling.
        keys = [k for k in mix if abs(k - v) < 0.7]
        out["share_" + name] = 100.0 * sum(mix[k] for k in keys) / tot
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", default="static_sweep_clean_2026-08")
    ap.add_argument("--arm", default="FluidServe")
    a = ap.parse_args()

    mpath = os.path.join(EXPDIR, "paper_experiment", a.manifest, "manifest.tsv")
    runs = [r for r in csv.DictReader(open(mpath), delimiter="\t")
            if r["arm_label"] == a.arm]

    rows, per_rate = [], collections.defaultdict(list)
    for r in sorted(runs, key=lambda x: (float(x["req_per_s"]), x["run"])):
        s = score_run(os.path.join(EXPDIR, "results", r["run"]))
        if s is None:
            print("  ! %s: no usable scrapes" % r["run"], file=sys.stderr)
            continue
        s.update(req_per_s=float(r["req_per_s"]), run=r["run"])
        rows.append(s)
        per_rate[s["req_per_s"]].append(s)
        print("  %-46s scrapes %5d  disagree %5.1f%%  inverted %5.1f%%  "
              "one pace %5.1f%%  distinct %.2f"
              % (r["run"], s["scrapes"], s["disagree_pct"], s["inverted_pct"],
                 s["one_pace_pct"], s["mean_distinct_paces"]))

    out = os.path.join(EXPDIR, "results", "aggregate_analysis",
                       "paper_eval_2026-08", "load_is_not_capacity.csv")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    def rng(rate, key):
        vs = [x[key] for x in per_rate[rate] if x[key] == x[key]]
        if not vs:
            return "-"
        return ("%.1f" % vs[0]) if max(vs) - min(vs) < 0.05 \
            else "%.1f-%.1f" % (min(vs), max(vs))

    print("\nof the scrapes where the fleet held more than one instance, the "
          "share where a load-aware pick lands on an instance that can accept "
          "the least (%s, 2 repeats)" % a.arm)
    print("req/s".rjust(6) + "disagree".rjust(14) + "inverted".rjust(14)
          + "batch gap".rjust(14) + "live gap".rjust(14))
    for rate in sorted(per_rate):
        print(("%.0f" % rate).rjust(6) + rng(rate, "disagree_pct").rjust(14)
              + rng(rate, "inverted_pct").rjust(14)
              + rng(rate, "median_batch_gap").rjust(14)
              + rng(rate, "median_live_gap").rjust(14))
    print("\nhow many distinct paces the fleet can offer, and which "
          "(%s, 2 repeats)" % a.arm)
    print("req/s".rjust(6) + "one pace (%)".rjust(15) + "distinct".rjust(12)
          + "at 50ms".rjust(11) + "at 62.5ms".rjust(12) + "at 100ms".rjust(11)
          + "idle".rjust(9))
    for rate in sorted(per_rate):
        print(("%.0f" % rate).rjust(6) + rng(rate, "one_pace_pct").rjust(15)
              + rng(rate, "mean_distinct_paces").rjust(12)
              + rng(rate, "share_p50").rjust(11)
              + rng(rate, "share_p62").rjust(12)
              + rng(rate, "share_p100").rjust(11)
              + rng(rate, "share_pidle").rjust(9))

    print("\nwrote %s" % out)


if __name__ == "__main__":
    main()
