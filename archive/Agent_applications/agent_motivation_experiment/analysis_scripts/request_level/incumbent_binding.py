#!/usr/bin/env python3
"""How often does the incumbents' remaining budget actually refuse a placement.

Three quantities, and they answer three different questions. Conflating them is
the mistake this script exists to prevent.

  1. Is the remaining budget tighter than the gate the code compares against?
     The comparison in evaluate() is meanAfter against BOTH gateAfter and
     tightestAllowance, where gateAfter = gate x 0.90. So the incumbent term is
     the tighter of the two only when tightestAllowance < gate x 0.90 -- not
     when it is merely below the raw gate. Measured here per scrape from the two
     gauges, paired.

  2. Did the term fire? scheduler_fluidserve_infeasible_total{reason=incumbents}.
     Every condition that is true increments its own reason, so one refused
     candidate can raise several. A share computed over this counter says how
     often the term was among the reasons, which is an upper bound on its effect.

  3. Did the term refuse anything that would otherwise have been routed?
     scheduler_fluidserve_infeasible_sole_total{reason=incumbents}, incremented
     only when len(failed) == 1. This is the one an ablation of the predicate
     would move, and the only one that supports a claim that the term binds.

Counters are cumulative within a scheduler process. The runner restarts the
control plane per condition, so the series resets inside a run: the value is
taken as the maximum over scrapes rather than the last, and the reset point is
reported so a run whose counters restart mid-window is visible.

The tier label was added to both counters after some runs were recorded, so the
label is parsed by NAME. Splitting a series key at the first '=' reads
'gate,tier' in one repeat and 'gate' in another, which has already produced a
0.0% where the true value was 21%.
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np

GATE = "scheduler_fluidserve_gate_allowance_ms"
TIGHT = "scheduler_fluidserve_tightest_allowance_ms"
LIVE = "scheduler_fluidserve_live_requests"
INF = "scheduler_fluidserve_infeasible_total"
SOLE = "scheduler_fluidserve_infeasible_sole_total"
UTIL = 0.90  # fsAllowanceUtilisation


def labels_of(key):
    if "|" not in key:
        return {}
    out = {}
    for part in key.split("|", 1)[1].split(","):
        k, _, v = part.partition("=")
        out[k] = v
    return out


def analyse(path, load_frac):
    rows = []
    with open(os.path.join(path, "server_metrics", "scheduler.jsonl")) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    if not rows:
        return None
    live_ids = sorted({labels_of(k).get("instance") for k in rows[-1]
                       if k.split("|", 1)[0] == GATE} - {None})
    if not live_ids:
        return None

    # --- part 1: is the remaining budget below the threshold the code uses ---
    def fleet_live(r):
        return sum(v for k, v in r.items()
                   if k.split("|", 1)[0] == LIVE
                   and labels_of(k).get("instance") in live_ids
                   and isinstance(v, (int, float)))

    tot = np.array([fleet_live(r) for r in rows])
    keep = [r for r, t in zip(rows, tot) if t >= load_frac * np.percentile(tot, 90)]
    per_inst = {}
    for i in live_ids:
        gk, tk = f"{GATE}|instance={i}", f"{TIGHT}|instance={i}"
        pairs = [(r[tk], r[gk]) for r in keep
                 if gk in r and tk in r and r[tk] >= 0 and r[gk] >= 0]
        if not pairs:
            continue
        t = np.array([a for a, _ in pairs])
        g = np.array([b for _, b in pairs])
        tighter = t < g * UTIL
        per_inst[i] = dict(
            n=len(pairs),
            gate_mode=float(np.bincount(g.astype(int)).argmax()),
            below_raw_gate_pct=100.0 * float((t < g).mean()),
            tighter_than_gateafter_pct=100.0 * float(tighter.mean()),
            tight_p50=float(np.percentile(t, 50)),
            tight_min=float(t.min()),
            # What the value IS on the scrapes where it is the tighter term.
            tight_when_binding_p50=(float(np.percentile(t[tighter], 50))
                                    if tighter.any() else float("nan")),
            tight_when_binding_min=(float(t[tighter].min())
                                    if tighter.any() else float("nan")),
            live_p50=float(np.percentile(
                [r[f"{LIVE}|instance={i}"] for r in keep
                 if f"{LIVE}|instance={i}" in r] or [np.nan], 50)),
        )

    # --- parts 2 and 3: the two counters, by reason, summed over tiers ---
    def counter_by_reason(series):
        # max over scrapes per exact key, then sum keys sharing a reason. Guards
        # the mid-run reset: a key that restarts contributes its post-reset peak
        # only, which understates rather than inventing.
        peak, resets = {}, 0
        prev = {}
        for r in rows:
            for k, v in r.items():
                if k.split("|", 1)[0] != series or not isinstance(v, (int, float)):
                    continue
                if k in prev and v < prev[k]:
                    resets += 1
                prev[k] = v
                peak[k] = max(peak.get(k, 0.0), v)
        by = {}
        for k, v in peak.items():
            by[labels_of(k).get("reason", "?")] = by.get(labels_of(k).get("reason", "?"), 0.0) + v
        return by, resets

    inf, r1 = counter_by_reason(INF)
    sole, r2 = counter_by_reason(SOLE)
    return dict(run=os.path.basename(path), instances=per_inst,
                inf=inf, sole=sole, resets=r1 + r2,
                n_keep=len(keep), n_all=len(rows))


def rate_of(n):
    m = re.search(r"_rpm_(\d+)", n)
    return f"{int(m.group(1))//60}" if m else "hour"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--load-frac", type=float, default=0.5)
    a = ap.parse_args()
    dirs = sorted(d for d in glob.glob(a.runs) if os.path.isdir(d)
                  and "PRERUN" not in d
                  and os.path.exists(os.path.join(d, "server_metrics", "scheduler.jsonl")))
    if not dirs:
        sys.exit(f"no runs match {a.runs}")
    for d in dirs:
        r = analyse(d, a.load_frac)
        if not r:
            print(f"skipped {os.path.basename(d)}", file=sys.stderr)
            continue
        rate = rate_of(r["run"])
        print(f"\n=== {r['run']}   rate={rate} req/s   "
              f"{r['n_keep']}/{r['n_all']} scrapes in load window, "
              f"counter resets={r['resets']}")
        print("  per instance: is the remaining budget tighter than gate x 0.90?")
        print(f"    {'gate':>5} {'live':>5} {'t.p50':>7} {'t.min':>7} "
              f"{'<gate%':>7} {'<gate*0.9%':>11} {'val|binding p50':>16} {'min':>7}")
        for i, s in sorted(r["instances"].items(), key=lambda kv: -kv[1]["live_p50"]):
            print(f"    {s['gate_mode']:>5.0f} {s['live_p50']:>5.0f} {s['tight_p50']:>7.1f} "
                  f"{s['tight_min']:>7.1f} {s['below_raw_gate_pct']:>7.1f} "
                  f"{s['tighter_than_gateafter_pct']:>11.1f} "
                  f"{s['tight_when_binding_p50']:>16.1f} {s['tight_when_binding_min']:>7.1f}")
        for name, d2 in (("fired (infeasible_total)", r["inf"]),
                         ("SOLE reason (infeasible_sole_total)", r["sole"])):
            tot = sum(d2.values()) or 1.0
            parts = ", ".join(f"{k} {v:,.0f} ({100*v/tot:.1f}%)"
                              for k, v in sorted(d2.items(), key=lambda kv: -kv[1]))
            print(f"  {name}: total {tot:,.0f} -> {parts}")


if __name__ == "__main__":
    main()
