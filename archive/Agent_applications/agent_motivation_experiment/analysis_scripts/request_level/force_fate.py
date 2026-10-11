#!/usr/bin/env python3
"""What happened to the requests FluidServe force-placed, and to the requests
that were running beside them.

FORCE is the branch selectInstance takes when no instance is feasible, the
request can no longer wait, and the model predicts the request itself would
still meet its own budget on the least-damaging instance. The placement is made
knowing the instance's feasibility conditions refused it. Whether that branch
should exist is under review (the original design table says expired
latency-sensitive requests are REJECTED, not forced); this script prices the
branch from runs already on disk.

Two questions, both per class:

  1. FATE      Did force-placed requests actually meet their own SLO, against
               route-placed requests of the same class in the same run?
  2. EXPOSURE  Did requests running on the instance AT THE MOMENT of a force
               placement violate more often than requests running on the OTHER
               instances at those same moments? Same instants, so fleet-wide
               stress is controlled; what varies is being co-resident with the
               forced arrival. This is an association, not a causal estimate:
               the router chose the force destination as least-damage, which
               biases the exposed instance TOWARD healthier residents.

Join: scheduler_dispatch.log fsplacement lines (uuid, tier, decision, inst)
+ generic dispatch lines (uuid -> wall clock) + request_ids.jsonl
(uuid -> task_id, call_index, client end time) + metrics.csv (outcomes),
exactly as exp101_first_token_truth.py does. Reports join coverage; the
dispatch log is known to drop lines under peak load on some arms.

SLO rules: chat ttft<=5s and mean per-token (latency-ttft)/(out-1) <= 50 ms;
deepresearch 10s / 100 ms; swe end-to-end <= 30 s, or 40 s when the run
directory name carries "b40" (EXP-105 arms move the swe budget to 40 s in both
the policy and the scoring; this script must follow the arm).
"""
import datetime
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

DISPATCH = re.compile(
    r"^I(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{6})\s+\d+ \S+ "
    r"\[Schedule\] dispatch request ([0-9a-fA-F-]{8,}) to \S+ instance (\d+)")
_NUM = r"[-+]?(?:\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|Inf|NaN)"
PLACEMENT = re.compile(
    r"\[Schedule\] dispatch request (\S+) fsplacement tier=(\d+) waited=(-?\d+) "
    r"prefillest=(" + _NUM + r") prefillraw=(" + _NUM + r") prompt=(\d+) "
    r"decision=(\w+) inst=(\d+)")


def classify(task_id):
    t = str(task_id)
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def read_dispatch(path, year):
    when, pred = {}, {}
    with open(path, errors="ignore") as fh:
        for line in fh:
            m = DISPATCH.match(line)
            if m:
                mo, d, h, mi, s, us, u, _inst = m.groups()
                if u not in when:
                    when[u] = datetime.datetime(
                        year, int(mo), int(d), int(h), int(mi), int(s),
                        int(us), tzinfo=datetime.timezone.utc).timestamp()
                continue
            m = PLACEMENT.search(line)
            if m:
                u, tier, waited, est, raw, prompt, decision, inst = m.groups()
                u = u.split("cmpl-")[-1]
                if u in pred:
                    continue
                pred[u] = dict(tier=int(tier), decision=decision, inst=inst,
                               waited_ms=float(waited))
    return when, pred


def meets_slo(row, swe_e2e_s):
    cls = row["cls"]
    if cls == "swe":
        return row["latency"] <= swe_e2e_s
    ttft_s, tok_ms = (5.0, 50.0) if cls == "chat" else (10.0, 100.0)
    if row["first_token_latency"] > ttft_s:
        return False
    out = row["output_tokens"]
    if out < 2:
        return True
    per_tok_ms = (row["latency"] - row["first_token_latency"]) / (out - 1) * 1e3
    return per_tok_ms <= tok_ms


def build(run):
    ids_path = os.path.join(run, "request_ids.jsonl")
    log_path = os.path.join(run, "server_metrics", "scheduler_dispatch.log")
    for p in (ids_path, log_path, os.path.join(run, "metrics.csv")):
        if not os.path.isfile(p):
            print(f"  missing {p}")
            return None, None
    ids = [json.loads(l) for l in open(ids_path, encoding="utf-8") if l.strip()]
    year = datetime.datetime.fromtimestamp(
        ids[0]["start_time"], datetime.timezone.utc).year
    when, pred = read_dispatch(log_path, year)

    placed = {u for u, p in pred.items() if p["decision"] in ("route", "force")}
    rows = []
    for r in ids:
        u = r["request_id"].split("cmpl-")[-1]
        p = pred.get(u)
        if p is None or u not in when:
            continue
        rows.append(dict(task_id=r["task_id"], call_index=r["call_index"],
                         uuid=u, dispatch_s=when[u], end_s=r["end_time"],
                         **p))
    dp = pd.DataFrame(rows)
    print(f"  placement lines: {len(pred):,}  "
          f"joined to client ids: {len(dp):,} "
          f"({100.0 * len(dp) / max(1, len(placed)):.1f}% of placed)")

    df = pd.read_csv(os.path.join(run, "metrics.csv"), low_memory=False)
    df = df[df.agent == "request"]
    j = df.merge(dp, on=["task_id", "call_index"], how="inner")
    j["cls"] = j.task_id.map(classify)
    swe_e2e = 40.0 if "b40" in os.path.basename(run) else 30.0
    trunc = (j.is_error.astype(bool) | j.is_server_terminated.astype(bool)
             | j.is_timeout.astype(bool) | j.is_job_timeout.astype(bool))
    j["truncated"] = trunc
    ok = j[~trunc].copy()
    ok["met"] = ok.apply(meets_slo, axis=1, swe_e2e_s=swe_e2e)
    return j, ok


def fate(j, ok):
    print("\n  1. FATE of force placements (truncated/errored excluded from "
          "the met%% denominator, counted in trunc%%)")
    print(f"  {'class':>14} {'decision':>8} {'n':>7} {'met own SLO':>12} "
          f"{'trunc%':>7} {'waited_ms p50':>14}")
    for cls, g in j.groupby("cls"):
        for dec in ("route", "force"):
            gd = g[g.decision == dec]
            if gd.empty:
                continue
            gok = ok[(ok.cls == cls) & (ok.decision == dec)]
            met = 100.0 * gok.met.mean() if len(gok) else float("nan")
            tr = 100.0 * gd.truncated.mean()
            print(f"  {cls:>14} {dec:>8} {len(gd):7d} {met:11.1f}% "
                  f"{tr:6.1f}% {gd.waited_ms.median():14.0f}")


def exposure(j, ok):
    forces = j[j.decision == "force"][["dispatch_s", "inst"]].values
    if len(forces) == 0:
        print("\n  2. EXPOSURE: no force placements in this run")
        return
    # For every completed, scored request: was it running on the force
    # instance at any force instant (exposed), or running elsewhere at one of
    # those instants (control)? A request can be both across different
    # instants; exposure wins, so the control group is never contaminated.
    starts = ok.dispatch_s.values
    ends = ok.end_s.values
    insts = ok.inst.values
    exposed = np.zeros(len(ok), dtype=bool)
    at_instant = np.zeros(len(ok), dtype=bool)
    for t, fi in forces:
        running = (starts <= t) & (ends >= t)
        exposed |= running & (insts == fi)
        at_instant |= running
    ctrl = at_instant & ~exposed
    ok = ok.assign(exposed=exposed, ctrl=ctrl)
    print("\n  2. EXPOSURE: violation rate of requests running AT force "
          "instants,\n     on the force instance vs on the other instances "
          "(same instants)")
    print(f"  {'class':>14} {'co-resident n':>14} {'viol%':>7} "
          f"{'elsewhere n':>12} {'viol%':>7}")
    for cls, g in ok.groupby("cls"):
        e, c = g[g.exposed], g[g.ctrl]
        if e.empty and c.empty:
            continue
        ev = 100.0 * (1 - e.met.mean()) if len(e) else float("nan")
        cv = 100.0 * (1 - c.met.mean()) if len(c) else float("nan")
        print(f"  {cls:>14} {len(e):14d} {ev:6.1f}% {len(c):12d} {cv:6.1f}%")


def main():
    runs = sys.argv[1:]
    if not runs:
        print("usage: force_fate.py <run dir or glob> ...")
        sys.exit(1)
    paths = []
    for r in runs:
        paths.extend(sorted(glob.glob(r)))
    for run in paths:
        print(f"\n{os.path.basename(run)}")
        j, ok = build(run)
        if j is None or j.empty:
            continue
        fate(j, ok)
        exposure(j, ok)


if __name__ == "__main__":
    main()
