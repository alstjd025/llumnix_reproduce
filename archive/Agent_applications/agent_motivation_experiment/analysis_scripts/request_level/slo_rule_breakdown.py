#!/usr/bin/env python3
"""Which SLO rule each class breaks, reported separately rather than combined.

An attainment figure answers "how often did this class meet its rule" and stops
there. Two classes of failure with completely different causes collapse into the
same number: a request whose tokens come out too slowly, and a request whose
first token never arrives. They call for opposite responses -- the first means
too much work is on the instance, the second means the request could not get to
an instance at all -- so combining them hides the one piece of information that
would tell them apart.

Reading them apart is what found the defect this script exists for. FluidServe
was meeting its time-between-tokens budget with room to spare (20 ms against 50)
and failing on time to first token at a median of 20 s against 5 s. No routing
explanation fits that shape: routing decides which instance serves a request and
therefore how fast its tokens come out, not whether it reaches one. The cause
was upstream of the scheduler entirely -- the gateway's worker pool.

Reports, per class: the share meeting each rule of its own SLO, the distribution
of each quantity, and how the time to first token moves over the run. A figure
that grows through a run is a backlog somewhere; a flat one is not.

Usage
-----
  python3 slo_rule_breakdown.py --runs results/<dir> [results/<dir> ...]
"""
import argparse
import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import class_of  # noqa: E402

# Same rules as exp22_fluidserve.py, kept here as two separate tests per class
# rather than one combined verdict.
RULES = {
    "chat":         {"ttft_s": 5.0,  "tbt_ms": 50.0},
    "deepresearch": {"ttft_s": 10.0, "tbt_ms": 100.0},
    "swe":          {"e2e_s": 30.0},
}
WARMUP_S = 60.0
DRAIN_S = 20.0


def load(run):
    p = os.path.join(run, "metrics.csv")
    if not os.path.isfile(p):
        return None
    df = pd.read_csv(p, low_memory=False)
    r = df[df.agent != "job_summary"].copy()
    r = r[pd.to_numeric(r.get("start_time"), errors="coerce").notna()]
    if r.empty:
        return None
    t0 = r["start_time"].min()
    r["rel"] = r["start_time"] - t0
    end = min(r["end_time"].max() - t0, r["rel"].max())
    r = r[(r["rel"] >= WARMUP_S) & (r["rel"] < end - DRAIN_S)].copy()
    if r.empty:
        return None
    r["class"] = r["task_id"].map(class_of)
    r["ttft"] = pd.to_numeric(r["first_token_latency"], errors="coerce")
    r["tbt"] = pd.to_numeric(r["tbt_mean_ms"], errors="coerce")
    r["e2e"] = pd.to_numeric(r["latency"], errors="coerce")
    r["done"] = r["success"].astype(str).str.lower().eq("true")
    # A request that produced no answer has a recorded latency anyway -- for a
    # rejection it is the time to be told no, which is short. Left as it is,
    # that reads as a request that finished well inside its end-to-end budget,
    # so a policy that rejects would score better the more it rejected. Every
    # rule is therefore evaluated only where an answer was actually produced,
    # and everything else counts as a miss.
    r.loc[~r["done"], ["ttft", "tbt", "e2e"]] = float("nan")
    return r


def pct(series, ok):
    """Share meeting a bound, counting a missing value as a miss.

    A request that never produced a first token has no time to first token, and
    dropping it would report the latency of the requests that succeeded as if it
    were the latency of the class.
    """
    if len(series) == 0:
        return float("nan")
    return 100.0 * ok.fillna(False).sum() / len(series)


def report(run):
    r = load(run)
    if r is None:
        print(f"\n=== {os.path.basename(run)} === no usable rows")
        return
    print(f"\n=== {os.path.basename(run)} ===  {len(r):,} requests in the window")
    for cname, rule in RULES.items():
        s = r[r["class"] == cname]
        if s.empty:
            continue
        print(f"  {cname:<13} n={len(s):>6,}  completed {100 * s['done'].mean():5.1f}%")
        if "ttft_s" in rule:
            b = rule["ttft_s"]
            print(f"    time to first token  <= {b:4.1f}s : "
                  f"{pct(s, s['ttft'] <= b):5.1f}%   "
                  f"p50 {s['ttft'].median():7.2f}s  p90 {s['ttft'].quantile(.9):7.2f}s")
        if "tbt_ms" in rule:
            b = rule["tbt_ms"]
            print(f"    mean time between    <= {b:4.0f}ms: "
                  f"{pct(s, s['tbt'] <= b):5.1f}%   "
                  f"p50 {s['tbt'].median():7.1f}ms p90 {s['tbt'].quantile(.9):7.1f}ms")
        if "e2e_s" in rule:
            b = rule["e2e_s"]
            print(f"    end to end           <= {b:4.1f}s : "
                  f"{pct(s, s['e2e'] <= b):5.1f}%   "
                  f"p50 {s['e2e'].median():7.1f}s  p90 {s['e2e'].quantile(.9):7.1f}s")

    # Time course of the first-token latency. A queue that builds shows up here
    # as a rising line and nowhere else in the request-level output.
    print("\n  time to first token over the run (median, seconds)")
    head = "    bin(s) " + "".join(f"{c[:4]:>8}" for c in RULES)
    print(head)
    for b, s in r.groupby((r["rel"] // 60).astype(int)):
        cells = "".join(
            f"{s[s['class'] == c]['ttft'].median():8.2f}"
            if not s[s["class"] == c].empty else f"{'-':>8}"
            for c in RULES)
        print(f"    {int(b) * 60:>6} {cells}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    a = ap.parse_args()
    runs = []
    for pattern in a.runs:
        runs.extend(sorted(glob.glob(pattern)) or [pattern])
    for run in runs:
        report(run)
    return 0


if __name__ == "__main__":
    sys.exit(main())
