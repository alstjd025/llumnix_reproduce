#!/usr/bin/env python3
"""Reconstruct the engine's step-time distribution from the token arrival stream.

WHY THIS IS POSSIBLE AT ALL. The engines expose their inter-token latency only as
a histogram `_sum` and `_count` -- `llumnix_metrics.py` keeps histogram families
through that pair and drops the `_bucket` lines -- so no engine-side percentile
of step time exists in any of these runs, and the scheduler's
`scheduler_fluidserve_raw_step_ms` is an average over a whole status interval of
about half a second and only moves for the arms the FluidServe policy actually
drives. What does exist is `tbt_events.jsonl`, which records the arrival offset
of every streamed chunk of every request.

In continuous batching every sequence running on an engine advances by exactly
one token per step, so all the requests decoding on one engine emit their tokens
at the same instants. Their arrival timestamps therefore cluster, one cluster per
engine step, and the spacing between consecutive clusters is that step's
duration. Pooling the token arrivals of all requests attributed to one engine and
clustering them in absolute time reconstructs the step-time series that the
engine itself does not report.

That the clustering is real and not an artefact of the binning is checked before
any distribution is printed: the script reports how many distinct requests
contribute to the median cluster. If tokens were arriving independently, a
cluster would hold one request; if they are engine steps, it holds a large
fraction of the running batch.

WHAT IT CANNOT SEE. A step during which this engine served no request that the
client was still streaming leaves no cluster, so a stretch with nothing running
produces one long apparent step rather than several. The window is therefore
chosen inside the loaded part of the run, and the running-batch gauge is printed
next to the result so a reader can see the engine was busy throughout.

    python3 tail2026_step_time.py --run results/<dir> --engine 8002 \
        [--start-frac 0.4] [--window-s 60] [--tol-ms 3]
"""
import argparse
import csv
import json
import os
import sys

import numpy as np


def klass(tid):
    p = str(tid).split("-")[0]
    return {"sg": "chat", "sa": "deepresearch"}.get(p, "swe")


def engine_map(run):
    p = os.path.join(run, "analysis", "request_engine.csv")
    if not os.path.exists(p):
        sys.exit(f"missing {p}: build it with build_request_engine_map.py "
                 "(Llumnix arms) or llmd_engine_map.py (llm-d)")
    out = {}
    with open(p) as fh:
        for row in csv.DictReader(l for l in fh if not l.startswith("#")):
            if row.get("engine_port"):
                out[(row["task_id"], str(row.get("call_index", "")))] = int(row["engine_port"])
    return out


def arrivals(run, emap, port, t_lo, t_hi):
    """(absolute arrival time, request key) for every streamed chunk on `port`."""
    ev = []
    for line in open(os.path.join(run, "tbt_events.jsonl")):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("agent") != "request":
            continue
        if emap.get((str(d.get("task_id")), str(d.get("call_index")))) != port:
            continue
        st = d.get("start_time")
        if st is None:
            continue
        rid = f"{d['task_id']}#{d.get('call_index')}"
        for e in d.get("chunk_events") or []:
            t = st + e["arrival_offset_ms"] / 1000.0
            if t_lo <= t <= t_hi:
                ev.append((t, rid))
    ev.sort()
    return ev


def cluster(ev, tol_ms):
    """Group arrivals into steps: a new step starts when the gap exceeds tol."""
    steps = []
    cur_t, cur_r = [], set()
    tol = tol_ms / 1000.0
    for t, rid in ev:
        if cur_t and t - cur_t[-1] > tol:
            steps.append((float(np.mean(cur_t)), len(cur_r), len(cur_t)))
            cur_t, cur_r = [], set()
        cur_t.append(t)
        cur_r.add(rid)
    if cur_t:
        steps.append((float(np.mean(cur_t)), len(cur_r), len(cur_t)))
    return steps


def run_window(run):
    """Load-bearing part of the run, taken from the client's own request stream."""
    lo, hi = None, None
    with open(os.path.join(run, "metrics.csv")) as fh:
        for row in csv.DictReader(fh):
            if row.get("agent") != "request":
                continue
            try:
                s = float(row["start_time"])
            except (TypeError, ValueError, KeyError):
                continue
            lo = s if lo is None else min(lo, s)
            hi = s if hi is None else max(hi, s)
    return lo, hi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--engine", type=int, required=True)
    ap.add_argument("--start-frac", type=float, default=0.4)
    ap.add_argument("--window-s", type=float, default=60.0)
    ap.add_argument("--tol-ms", type=float, default=3.0)
    a = ap.parse_args()

    lo, hi = run_window(a.run)
    t0 = lo + a.start_frac * (hi - lo)
    t1 = t0 + a.window_s
    emap = engine_map(a.run)
    ev = arrivals(a.run, emap, a.engine, t0, t1)
    if len(ev) < 1000:
        print(f"=== {os.path.basename(a.run)} engine {a.engine} === "
              f"only {len(ev)} arrivals in the window; nothing to reconstruct")
        return 0
    steps = cluster(ev, a.tol_ms)
    reqs = np.array([s[1] for s in steps], dtype=float)
    ts = np.array([s[0] for s in steps])
    dur = 1000.0 * np.diff(ts)

    print(f"\n=== {os.path.basename(a.run)}  engine {a.engine} ===")
    print(f"  window {a.window_s:.0f} s from {100 * a.start_frac:.0f}% into the load, "
          f"{len(ev):,} token arrivals -> {len(steps):,} clusters")
    print(f"  distinct requests per cluster: median {np.median(reqs):.0f}, "
          f"p90 {np.percentile(reqs, 90):.0f}, max {reqs.max():.0f}   "
          "(one means the clustering found no shared step; a large number means "
          "the cluster is an engine step shared by the running batch)")
    if np.median(reqs) < 2:
        print("  -> clusters hold one request each, so these are not shared steps "
              "and the numbers below would not be step times. Stopping.")
        return 0
    qs = [1, 5, 10, 25, 50, 60, 70, 75, 80, 85, 90, 95, 99, 99.9]
    print("  step duration ms: " +
          "  ".join(f"p{q}={np.percentile(dur, q):.1f}" for q in qs))
    print(f"  mean {dur.mean():.1f}  max {dur.max():.0f}  n={len(dur):,}")
    edges = [0, 15, 20, 25, 30, 35, 40, 50, 60, 80, 100, 150, 200, 300, 400, 600, 1e9]
    h, _ = np.histogram(dur, bins=edges)
    print("  histogram of step durations:")
    for a_, b_, c in zip(edges[:-1], edges[1:], h):
        if c:
            print(f"    {a_:>5.0f}-{b_ if b_ < 1e8 else float('inf'):>6.0f} ms  "
                  f"{100 * c / len(dur):>6.2f}%  {c:>7,}   "
                  + "#" * int(60 * c / max(h)))
    # A step that carries a prefill chunk should be long AND should not change
    # how many requests are decoding, so the batch size at long steps is printed
    # against the batch size at short ones. A large drop would mean the long
    # steps are the ones where the batch emptied, which is a different story.
    long_i = np.where(dur > 2 * np.median(dur))[0]
    short_i = np.where(dur <= np.median(dur))[0]
    print(f"  requests emitting at a step longer than 2x the median: "
          f"{np.median(reqs[long_i]):.0f}  (at a step at or below the median: "
          f"{np.median(reqs[short_i]):.0f})")
    print(f"  share of steps longer than 2x the median: "
          f"{100 * len(long_i) / len(dur):.1f}%; "
          f"share of the window's time they hold: "
          f"{100 * dur[long_i].sum() / dur.sum():.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
