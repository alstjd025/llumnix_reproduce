#!/usr/bin/env python3
"""Does the burst fraction follow the arm or the client's concurrency?

Candidate explanation to be tested: the load generator services many streams in
one process, so a stream whose reading loop is delayed finds several events
already in the socket and reads them back to back.  That explanation predicts
the sub-tau gap fraction tracks how many live streams the CLIENT is carrying,
not which arm produced them.  It matters because the arms do not admit the same
number of requests, so our arm carries more live streams.

For each run this reports
  * frac_sub_tau : share of chat inter-arrival gaps below tau (subsampled)
  * live_p50/p90 : streams open at the client, from metrics.csv start/end times
  * tokens_s     : streamed chunks per second the client had to read
and then fits frac ~ a + b*live within each arm and across arms.

Usage:
  tail2026_burst_regress.py --data-dir DIR [--runs-file F | RUNNAME ...]
                            [--tau 5] [--stride 9] [--max-requests 500]
"""
import argparse
import bisect
import csv
import json
import os
import statistics
import sys


ARMS = ("fspfx", "polyserve", "slo", "vllmcache", "llmdslo")


def arm_of(name):
    for a in ARMS:
        if f"_{a}_" in name:
            return a
    return "?"


def rate_of(name):
    if "_rpm_" in name:
        try:
            return int(name.rsplit("_rpm_", 1)[1]) / 60.0
        except ValueError:
            pass
    return float("nan")


def client_concurrency(rundir):
    """Live streams at the client, sampled across the middle of the run."""
    path = os.path.join(rundir, "metrics.csv")
    if not os.path.exists(path):
        return None
    iv = []
    chunks = 0
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            if r.get("agent") != "request":
                continue
            try:
                s = float(r["start_time"])
                e = float(r["end_time"])
            except (TypeError, ValueError, KeyError):
                continue
            if e <= s:
                continue
            iv.append((s, e))
            try:
                chunks += int(float(r.get("stream_chunks") or 0))
            except ValueError:
                pass
    if len(iv) < 50:
        return None
    starts = sorted(s for s, _ in iv)
    ends = sorted(e for _, e in iv)
    t0, t1 = starts[0], ends[-1]
    lo, hi = t0 + 0.1 * (t1 - t0), t0 + 0.9 * (t1 - t0)
    n = 200
    live = []
    for i in range(n):
        t = lo + (hi - lo) * i / (n - 1)
        live.append(bisect.bisect_right(starts, t) - bisect.bisect_right(ends, t))
    live.sort()
    return {
        "live_p50": live[len(live) // 2],
        "live_p90": live[int(0.9 * (len(live) - 1))],
        "requests": len(iv),
        "chunks_per_s": chunks / (t1 - t0) if t1 > t0 else float("nan"),
        "window_s": t1 - t0,
    }


def burst_fraction(rundir, tau, stride, max_requests):
    path = os.path.join(rundir, "tbt_events.jsonl")
    if not os.path.exists(path):
        return None
    ngap = nsmall = nreq = 0
    lead_excess = []
    with open(path) as f:
        for k, line in enumerate(f):
            if nreq >= max_requests:
                break
            if k % stride:
                continue
            if '"sg-' not in line[:60]:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("agent") != "request":
                continue
            evs = rec.get("chunk_events") or []
            if len(evs) < 20:
                continue
            g = [e["inter_arrival_ms"] for e in evs
                 if e.get("inter_arrival_ms") is not None]
            if len(g) < 20:
                continue
            nreq += 1
            ngap += len(g)
            nsmall += sum(1 for v in g if v < tau)
    if ngap == 0:
        return None
    return {"frac_sub_tau": nsmall / ngap, "gaps": ngap, "sampled_requests": nreq}


def ols(xs, ys):
    n = len(xs)
    if n < 3:
        return float("nan"), float("nan"), float("nan")
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return float("nan"), float("nan"), float("nan")
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    a = my - b * mx
    syy = sum((y - my) ** 2 for y in ys)
    r2 = (b * b * sxx / syy) if syy > 0 else float("nan")
    return a, b, r2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--results-dir", default=None)
    ap.add_argument("--tau", type=float, default=5.0)
    ap.add_argument("--stride", type=int, default=9)
    ap.add_argument("--max-requests", type=int, default=500)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()

    base = a.results_dir or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "results")
    base = os.path.abspath(base)

    rows = []
    for name in a.runs:
        d = os.path.join(base, name)
        cc = client_concurrency(d)
        bf = burst_fraction(d, a.tau, a.stride, a.max_requests)
        if not cc or not bf:
            print(f"# skip {name}", file=sys.stderr)
            continue
        rows.append({
            "run": name, "arm": arm_of(name), "rate_s": rate_of(name),
            **cc, **bf,
        })
        print(f"{name:52s} arm={rows[-1]['arm']:10s} rate={rows[-1]['rate_s']:5.1f}/s "
              f"live p50={cc['live_p50']:4d} p90={cc['live_p90']:4d} "
              f"chunks/s={cc['chunks_per_s']:8.0f} "
              f"sub{a.tau:g}ms={bf['frac_sub_tau']*100:6.2f}%  "
              f"(n={bf['gaps']})")

    if not rows:
        return
    print("\n--- fit  frac_sub_tau (%) ~ a + b * live_p50 ---")
    for arm in ARMS:
        sub = [r for r in rows if r["arm"] == arm]
        if len(sub) < 3:
            if sub:
                print(f"{arm:10s} n={len(sub)}  "
                      f"frac={[round(r['frac_sub_tau']*100,2) for r in sub]}")
            continue
        xs = [r["live_p50"] for r in sub]
        ys = [r["frac_sub_tau"] * 100 for r in sub]
        A, B, R2 = ols(xs, ys)
        print(f"{arm:10s} n={len(sub):2d}  live {min(xs):4d}-{max(xs):4d}  "
              f"frac {min(ys):5.2f}-{max(ys):5.2f}%   "
              f"slope={B:+.5f} %/stream  intercept={A:.2f}%  R2={R2:.3f}")

    llum = [r for r in rows if r["arm"] != "llmdslo"]
    lmd = [r for r in rows if r["arm"] == "llmdslo"]
    if llum and lmd:
        lo = min(r["live_p50"] for r in llum)
        hi = max(r["live_p50"] for r in llum)
        over = [r for r in lmd if lo <= r["live_p50"] <= hi]
        print(f"\nllm-d runs whose client concurrency lies inside the range the "
              f"Llumnix-gateway arms cover ({lo}-{hi} live streams): {len(over)}")
        for r in over:
            print(f"   {r['run']:52s} live={r['live_p50']:4d} "
                  f"sub{a.tau:g}ms={r['frac_sub_tau']*100:.3f}%")
        if over:
            m_l = statistics.fmean([r["frac_sub_tau"] * 100 for r in llum
                                    if lo <= r["live_p50"] <= hi])
            m_d = statistics.fmean([r["frac_sub_tau"] * 100 for r in over])
            print(f"   at matched client concurrency: Llumnix-gateway arms "
                  f"{m_l:.2f}%  vs  llm-d {m_d:.3f}%")

    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote {a.csv}")


if __name__ == "__main__":
    main()
