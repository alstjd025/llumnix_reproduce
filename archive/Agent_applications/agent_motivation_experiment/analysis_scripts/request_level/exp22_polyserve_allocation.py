#!/usr/bin/env python3
"""Did PolyServe's tier-to-server allocation actually move during a run?

The repartitioner is the part of PolyServe that is supposed to respond to a
changing workload: every 10 s it re-derives how many servers each SLO tier
should get from the observed arrival rate and the per-request cost, and applies
the result once the same allocation has been computed twice in a row.

On a fleet of four instances with three tiers and a guarantee of at least one
server per tier, three of the four servers are pinned by that guarantee and only
one is free to follow demand. The reachable allocations are therefore just
(2,1,1), (1,2,1) and (1,1,2). Whether the mechanism does anything on a given
workload is an empirical question, and this answers it from the scraped
scheduler series rather than from the design.

Reports, per tier: how many distinct allocations occurred, when the first one
was applied, and how many times it changed afterwards; alongside the observed
demand so that a fixed allocation can be read against a moving demand.

Usage
-----
  python3 exp22_polyserve_allocation.py --run results/<polyserve run dir>
"""
import argparse
import collections
import json
import os
import sys


def tier_of(key):
    if "tpot_slo_ms=" not in key:
        return "?"
    return key.split("tpot_slo_ms=", 1)[1].rstrip("}").split(",")[0]


def load(path, prefix):
    out = collections.defaultdict(list)
    t0 = None
    with open(path) as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("ok"):
                continue
            if t0 is None:
                t0 = rec["t"]
            for k, v in rec.items():
                if isinstance(v, (int, float)) and k.startswith(prefix):
                    out[tier_of(k)].append((rec["t"] - t0, float(v)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    a = ap.parse_args()

    path = os.path.join(a.run, "server_metrics", "scheduler.jsonl")
    if not os.path.isfile(path):
        sys.exit(f"no scheduler series in {a.run}")

    servers = load(path, "scheduler_polyserve_tier_servers")
    demand = load(path, "scheduler_polyserve_tier_demand")
    if not servers:
        sys.exit("no polyserve allocation series; is this a PolyServe run, and "
                 "was scheduler_polyserve_tier_servers in the collector allowlist?")

    print(f"\n=== {os.path.basename(a.run)} ===")
    print("\ntier -> servers")
    total_changes = 0
    for tier in sorted(servers, key=lambda t: int(t) if t.isdigit() else 0):
        pts = servers[tier]
        # A zero reading means no allocation has been applied yet: the
        # repartitioner needs one window to observe and two to agree.
        applied = [(t, v) for t, v in pts if v > 0]
        if not applied:
            print(f"  {tier:>5}ms: never allocated ({len(pts)} samples)")
            continue
        values = [v for _, v in applied]
        changes = sum(1 for i in range(1, len(values)) if values[i] != values[i - 1])
        total_changes += changes
        print(f"  {tier:>5}ms: first applied at t={applied[0][0]:.0f}s as "
              f"{values[0]:.0f} server(s); distinct values "
              f"{sorted(set(values))}; changed {changes} time(s) over "
              f"{len(applied)} samples")

    print("\nobserved demand over the same period (server-seconds per second)")
    for tier in sorted(demand, key=lambda t: int(t) if t.isdigit() else 0):
        vals = [v for _, v in demand[tier]]
        if not vals:
            continue
        s = sorted(vals)
        swing = (max(vals) / min(vals)) if min(vals) > 0 else float("inf")
        print(f"  {tier:>5}ms: min={min(vals):8.3f}  median={s[len(s) // 2]:8.3f}  "
              f"max={max(vals):8.3f}   (swing {swing:.1f}x)")

    print()
    if total_changes == 0:
        print("The allocation never changed after it was first applied. Whatever "
              "the workload did over this run, the partition it was served under "
              "was static, so any comparison against it is a comparison against a "
              "fixed partition rather than against an adapting one.")
    else:
        print(f"The allocation changed {total_changes} time(s), so the "
              f"repartitioner was doing something on this workload.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
