#!/usr/bin/env python3
"""What values does the gate and the tightest remaining budget actually take, per instance.

The two series this reads are the two quantities the feasibility conjunction
compares a prediction against, and they are different KINDS of quantity:

  scheduler_fluidserve_gate_allowance_ms      the tightest NOMINAL per-token
      budget among the achievable residents. A property of the classes present,
      so it takes one of the tier values (50 / 75 / 100 here) and does not move
      as an instance falls behind.

  scheduler_fluidserve_tightest_allowance_ms  the tightest REMAINING per-token
      budget among the residents that are still achievable AND not already
      slower than the instance's current pace. Continuous, and it moves both
      ways: a resident that has been served faster than its budget accumulates
      slack and pushes this UP, one that has fallen behind pushes it DOWN.

Three things have to be right or the numbers mean something else.

1. The Inf sentinel. buildFlux initialises both to +Inf and the emitter passes
   them through finiteOrMinusOne, so an instance whose achievable resident set
   is empty reports -1. Averaging that in gives a number below every real
   observation. Rows at -1 are counted and reported separately, never mixed in.

2. The scrape window is not the load window. The collector runs from before the
   runner starts to after it stops, so a static condition's window holds a long
   idle stretch at both ends. Percentiles over the whole window describe an
   empty fleet. The loaded stretch is cut here by the fleet's own resident
   count: scrapes where the sum of live_requests is at least `--load-frac` of
   that run's p90 of the same sum.

3. Instance ids outlive the pod. These are Prometheus gauges in the scheduler
   process, and the runner restarts the control plane per condition, so a run's
   scrape series spans two scheduler processes and carries both generations of
   instance id. Only the ids present in the LAST scrape are used.

Reported per instance as p10/p50/p90 rather than a mean, because both series
sit against ceilings -- the gate takes tier values and the tightest allowance is
bounded below by the instance's current pace -- and a mean hides how often a
series is at its bound.

Usage
-----
  python3 allowance_distribution.py --runs 'results/*exp108r1_fsv3*_rpm_*'
  python3 allowance_distribution.py --runs '...' --per-instance --csv out.csv
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
UNACH = "scheduler_fluidserve_unachievable_requests"
OBS = "scheduler_fluidserve_observed_step_ms"


def label_of(key, name):
    """Value of label `name` in a 'series|a=1,b=2' key, parsed by label name.

    Splitting on the first '=' breaks the moment a second label is added to a
    series, which has happened once in this repository and silently changed
    which quantity a script read.
    """
    if "|" not in key:
        return None
    for part in key.split("|", 1)[1].split(","):
        k, _, v = part.partition("=")
        if k == name:
            return v
    return None


def read_run(path):
    """-> (list of per-scrape dicts keyed (series, instance) -> value)."""
    scrapes = []
    with open(os.path.join(path, "server_metrics", "scheduler.jsonl")) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            row = {}
            for k, v in d.items():
                if not isinstance(v, (int, float)):
                    continue
                name = k.split("|", 1)[0]
                if name in (GATE, TIGHT, LIVE, UNACH, OBS):
                    inst = label_of(k, "instance")
                    if inst:
                        row[(name, inst)] = float(v)
            if row:
                row[("_t", "")] = d.get("t", 0.0)
                scrapes.append(row)
    return scrapes


def analyse(path, load_frac):
    scrapes = read_run(path)
    if not scrapes:
        return None
    # The live generation of instance ids: those in the last scrape that
    # reported a gate at all.
    live_ids = sorted({i for (n, i) in scrapes[-1] if n == GATE})
    if not live_ids:
        return None

    def fleet_live(row):
        return sum(row.get((LIVE, i), 0.0) for i in live_ids)

    tot = np.array([fleet_live(r) for r in scrapes])
    if not len(tot) or tot.max() <= 0:
        return None
    thresh = load_frac * np.percentile(tot, 90)
    keep = [r for r, t in zip(scrapes, tot) if t >= thresh]
    if not keep:
        return None

    out = {"run": os.path.basename(path), "n_scrapes": len(scrapes),
           "n_loaded": len(keep), "instances": {}}
    for i in live_ids:
        g = np.array([r[(GATE, i)] for r in keep if (GATE, i) in r])
        t = np.array([r[(TIGHT, i)] for r in keep if (TIGHT, i) in r])
        lv = np.array([r[(LIVE, i)] for r in keep if (LIVE, i) in r])
        ua = np.array([r[(UNACH, i)] for r in keep if (UNACH, i) in r])
        ob = np.array([r[(OBS, i)] for r in keep if (OBS, i) in r])
        g_inf, t_inf = int((g < 0).sum()), int((t < 0).sum())
        g, t = g[g >= 0], t[t >= 0]
        ob = ob[ob >= 0] if len(ob) else ob

        def pct(a, q):
            return float(np.percentile(a, q)) if len(a) else float("nan")

        # Paired per scrape, both directions. What the user's question needs is
        # not "how big is the slack" but "does the remaining budget ever fall
        # below the nominal one", which lives in the LOW tail, not the median.
        pairs = [(r[(TIGHT, i)], r[(GATE, i)]) for r in keep
                 if (TIGHT, i) in r and (GATE, i) in r
                 and r[(TIGHT, i)] >= 0 and r[(GATE, i)] >= 0]
        below = 100.0 * float(np.mean([a < b for a, b in pairs])) if pairs else float("nan")
        out["instances"][i] = dict(
            gate_p50=pct(g, 50), gate_mode=(float(np.bincount(g.astype(int)).argmax())
                                            if len(g) else float("nan")),
            gate_inf_pct=100.0 * g_inf / max(len(keep), 1),
            tight_min=float(t.min()) if len(t) else float("nan"),
            tight_p01=pct(t, 1), tight_p10=pct(t, 10), tight_p50=pct(t, 50),
            tight_p90=pct(t, 90),
            tight_inf_pct=100.0 * t_inf / max(len(keep), 1),
            below_gate_pct=below,
            live_p50=pct(lv, 50), live_p90=pct(lv, 90),
            # A count against a ceiling: p50 says nothing about how bad it gets,
            # and this repository's rule for such a quantity is upper quantiles.
            unach_p50=pct(ua, 50), unach_p90=pct(ua, 90), unach_p99=pct(ua, 99),
            unach_max=float(ua.max()) if len(ua) else float("nan"),
            unach_any_pct=100.0 * float(np.mean(ua > 0)) if len(ua) else float("nan"),
            obs_p50=pct(ob, 50),
            # How often the remaining budget is LOOSER than the nominal gate,
            # i.e. the residents have banked slack. Paired per scrape.
            slack_pct=100.0 * float(np.mean([
                r[(TIGHT, i)] > r[(GATE, i)]
                for r in keep
                if (TIGHT, i) in r and (GATE, i) in r
                and r[(TIGHT, i)] >= 0 and r[(GATE, i)] >= 0])) if any(
                    (TIGHT, i) in r and (GATE, i) in r for r in keep) else float("nan"),
        )
    return out


def rate_of(name):
    m = re.search(r"_rpm_(\d+)", name)
    return f"{int(m.group(1))/60:.0f}" if m else name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--load-frac", type=float, default=0.5,
                    help="keep scrapes whose fleet resident count is at least "
                         "this fraction of the run's p90 (default 0.5)")
    ap.add_argument("--per-instance", action="store_true")
    ap.add_argument("--csv")
    a = ap.parse_args()
    dirs = sorted(d for d in glob.glob(a.runs)
                  if os.path.isdir(d) and "PRERUN" not in d
                  and os.path.exists(os.path.join(d, "server_metrics", "scheduler.jsonl")))
    if not dirs:
        sys.exit(f"no runs with scheduler.jsonl match {a.runs}")
    rows = []
    print(f"{len(dirs)} runs\n")
    hdr = (f"{'rate':>5} {'inst':>4} {'gate':>5} {'t.min':>6} {'t.p01':>6} "
           f"{'t.p50':>6} {'obs':>6} {'live':>5} {'<gate%':>7} "
           f"{'unachP90':>8} {'p99':>5} {'max':>5} {'any%':>6} {'inf%':>5}")
    for d in dirs:
        r = analyse(d, a.load_frac)
        if r is None:
            print(f"  skipped (no usable scrapes): {os.path.basename(d)}", file=sys.stderr)
            continue
        rate = rate_of(r["run"])
        print(f"--- {r['run']}  ({r['n_loaded']}/{r['n_scrapes']} scrapes in load window)")
        print(hdr)
        for n, (i, s) in enumerate(sorted(r["instances"].items(),
                                          key=lambda kv: -kv[1]["live_p50"])):
            print(f"{rate:>5} {n:>4} {s['gate_mode']:>5.0f} {s['tight_min']:>6.1f} "
                  f"{s['tight_p01']:>6.1f} {s['tight_p50']:>6.1f} {s['obs_p50']:>6.1f} "
                  f"{s['live_p50']:>5.0f} {s['below_gate_pct']:>7.1f} "
                  f"{s['unach_p90']:>8.0f} {s['unach_p99']:>5.0f} {s['unach_max']:>5.0f} "
                  f"{s['unach_any_pct']:>6.1f} {s['tight_inf_pct']:>5.1f}")
            rows.append(dict(run=r["run"], rate=rate, instance=i, rank=n, **s))
        print()
    if a.csv and rows:
        import pandas as pd
        pd.DataFrame(rows).to_csv(a.csv, index=False)
        print(f"wrote {a.csv}")


if __name__ == "__main__":
    main()
