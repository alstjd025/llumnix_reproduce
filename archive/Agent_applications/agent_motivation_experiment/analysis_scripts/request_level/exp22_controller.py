#!/usr/bin/env python3
"""EXP-22 - what the FluidServe controller did, from the scraped scheduler series.

The per-decision detail lives only in the scheduler's log, which does not
survive a long run, so this reads the time series the collector recorded and
answers the questions an attainment number cannot:

  decision mix     how often a request was placed immediately, held at the
                   gateway, rejected because no placement could meet its own
                   budget, or placed anyway because it could still meet that
                   budget but nothing could serve it at its promised pace
  model accuracy   measured iteration time against what the capacity model
                   predicted, per instance. This is the assumption every
                   placement rests on, and it is measurable live rather than
                   only offline
  separation       whether the tightest budget in force differs across
                   instances. If the controller works as intended the classes
                   collect on different instances without any of them being
                   reserved, so the per-instance tightest budget should spread
                   apart under load rather than track each other
  capacity vs use  projected occupancy against the capacity the latency budget
                   allows, per instance

Usage
-----
  python3 exp22_controller.py --run results/<run_dir> [--out-dir DIR]
"""
import argparse
import collections
import glob
import json
import os
import sys

import numpy as np


def load_series(path):
    """Read the scrape file into {metric_key: [(t, value), ...]}."""
    series = collections.defaultdict(list)
    with open(path) as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("ok"):
                continue
            t = rec.get("t")
            for k, v in rec.items():
                if k in ("t", "ok") or not isinstance(v, (int, float)):
                    continue
                series[k].append((t, float(v)))
    return series


def by_instance(series, prefix):
    """Group `name|label=value,...` keys by instance id."""
    out = {}
    for key, pts in series.items():
        if not key.startswith(prefix):
            continue
        inst = "?"
        if "|" in key:
            for part in key.split("|", 1)[1].split(","):
                if part.startswith("instance="):
                    inst = part.split("=", 1)[1]
        out[inst] = pts
    return out


def delta(pts):
    """Total increase of a counter over the run, robust to a scrape restart."""
    if len(pts) < 2:
        return 0.0
    vals = [v for _, v in pts]
    total, prev = 0.0, vals[0]
    for v in vals[1:]:
        if v >= prev:
            total += v - prev
        else:
            total += v          # counter reset: everything after it is new
        prev = v
    return total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out-dir")
    a = ap.parse_args()

    path = os.path.join(a.run, "server_metrics", "scheduler.jsonl")
    if not os.path.isfile(path):
        sys.exit(f"no scheduler series in {a.run}")
    s = load_series(path)
    gw_path = os.path.join(a.run, "server_metrics", "gateway.jsonl")
    gw = load_series(gw_path) if os.path.isfile(gw_path) else {}

    print(f"\n=== {os.path.basename(a.run)} ===")

    print("\n[decisions]")
    total = 0.0
    counts = {}
    for key, pts in s.items():
        if key.startswith("scheduler_fluidserve_decisions_total"):
            kind = "?"
            if "|" in key:
                for part in key.split("|", 1)[1].split(","):
                    if part.startswith("decision="):
                        kind = part.split("=", 1)[1]
            counts[kind] = delta(pts)
            total += counts[kind]
    if not counts:
        print("  none recorded (is this a FluidServe run?)")
    # A held request is re-asked at every retry interval, so a pend is one
    # re-decision rather than one request. Sharing it against the total makes a
    # policy that holds briefly look like one that holds everything: at a 100 ms
    # retry interval, a single request held for one second contributes ten
    # pends against one route. The terminal decisions -- the ones that happen
    # once per request -- are shared among themselves, and holding is reported
    # separately in the units it is actually in.
    terminal = sum(counts.get(k, 0.0) for k in ("route", "shed", "force"))
    for kind in ("route", "shed", "force"):
        if kind in counts:
            share = 100.0 * counts[kind] / terminal if terminal else 0.0
            print(f"  {kind:<6} {counts[kind]:>10,.0f}  ({share:4.1f}% of requests placed or rejected)")
    if counts.get("pend"):
        print(f"  {'pend':<6} {counts['pend']:>10,.0f}  "
              f"({counts['pend'] / max(terminal, 1):.1f} re-decisions per request)")

    for name, label in (("gateway_scheduling_waited_total", "requests that waited"),
                        ("gateway_scheduling_rejected_total", "requests rejected"),
                        ("gateway_scheduling_gave_up_total", "requests given up on")):
        pts = next((v for k, v in gw.items() if k.startswith(name)), None)
        if pts:
            print(f"  {label:<24} {delta(pts):>10,.0f}")
    wsum = next((v for k, v in gw.items()
                 if k.startswith("gateway_scheduling_wait_milliseconds_sum")), None)
    wcnt = next((v for k, v in gw.items()
                 if k.startswith("gateway_scheduling_wait_milliseconds_count")), None)
    if wsum and wcnt:
        n = delta(wcnt)
        if n:
            print(f"  {'mean wait':<24} {delta(wsum) / n:>10,.0f} ms")

    print("\n[capacity model accuracy]  measured iteration time vs predicted")
    obs = by_instance(s, "scheduler_fluidserve_observed_step_ms")
    pred = by_instance(s, "scheduler_fluidserve_predicted_step_ms")
    print(f"  {'instance':<22}{'n':>7}{'meas p50':>10}{'pred p50':>10}"
          f"{'ratio p50':>11}{'ratio p90':>11}")
    ratios_all = []
    for inst in sorted(obs):
        o = {round(t, 1): v for t, v in obs[inst] if v > 0}
        p = {round(t, 1): v for t, v in pred.get(inst, []) if v > 0}
        common = sorted(set(o) & set(p))
        if len(common) < 5:
            continue
        ov = np.array([o[t] for t in common])
        pv = np.array([p[t] for t in common])
        r = pv / ov
        ratios_all.extend(r.tolist())
        print(f"  {inst[:20]:<22}{len(common):>7}{np.median(ov):>9.1f}ms"
              f"{np.median(pv):>9.1f}ms{np.median(r):>11.2f}{np.percentile(r, 90):>11.2f}")
    if ratios_all:
        r = np.array(ratios_all)
        print(f"  {'ALL':<22}{len(r):>7}{'':>10}{'':>10}"
              f"{np.median(r):>11.2f}{np.percentile(r, 90):>11.2f}")
        print("  ratio > 1 means the model predicted a slower iteration than the")
        print("  engine achieved, which is the conservative direction.")

    corr = next((v for k, v in s.items()
                 if k.startswith("scheduler_fluidserve_capacity_correction")), None)
    if corr:
        vals = [v for _, v in corr]
        print(f"  online correction factor: start {vals[0]:.3f} -> end {vals[-1]:.3f}")

    print("\n[per-instance state]  medians over the run")
    fields = [("scheduler_fluidserve_tightest_allowance_ms", "budget ms"),
              ("scheduler_fluidserve_cap_kv_tokens", "cap kv"),
              ("scheduler_fluidserve_projected_kv_tokens", "proj kv"),
              ("scheduler_fluidserve_headroom_tokens", "headroom"),
              ("scheduler_fluidserve_live_requests", "live"),
              ("scheduler_fluidserve_unachievable_requests", "unachiev")]
    grouped = {name: by_instance(s, name) for name, _ in fields}
    insts = sorted({i for g in grouped.values() for i in g})
    hdr = f"  {'instance':<22}" + "".join(f"{lbl:>12}" for _, lbl in fields)
    print(hdr)
    tightest = {}
    for inst in insts:
        row = f"  {inst[:20]:<22}"
        for name, _ in fields:
            pts = grouped[name].get(inst, [])
            vals = [v for _, v in pts if v >= 0]
            med = np.median(vals) if vals else float("nan")
            if name.endswith("allowance_ms"):
                tightest[inst] = med
            row += f"{med:>12,.0f}" if not np.isnan(med) else f"{'-':>12}"
        print(row)
    live = {k: v for k, v in tightest.items() if not np.isnan(v)}
    if len(live) >= 2:
        spread = max(live.values()) - min(live.values())
        print(f"\n  spread of the tightest budget across instances: {spread:.0f} ms")
        print("  A spread means the classes ended up on different instances "
              "without any being reserved;")
        print("  near zero means every instance is holding the same class mixture.")

    if a.out_dir:
        os.makedirs(a.out_dir, exist_ok=True)
        out = os.path.join(a.out_dir,
                           f"controller_{os.path.basename(a.run)}.json")
        with open(out, "w") as f:
            json.dump({"decisions": counts,
                       "tightest_allowance_ms": tightest}, f, indent=1)
        print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
