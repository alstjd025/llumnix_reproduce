#!/usr/bin/env python3
"""Are the reconstructed "long engine steps" really gateway stalls?

tail2026_step_time.py reconstructs an engine's step-time series by pooling the
token arrivals of every request attributed to that engine and clustering them in
absolute time.  Its defence against the burst artefact was that pooling many
requests cannot be fooled by something that happens to one connection.  That
defence does not cover the artefact in 10_burst_layer.md, which freezes every
live stream at once: when the gateway is released, tokens that the engine
produced over N consecutive steps arrive in a single cluster, so N real steps are
reconstructed as one apparent step of N times the duration.

Two things separate a gateway stall from a genuinely long engine step:

  * A gateway stall is a property of one process in front of all four engines,
    so it must appear on all four engines at the same instant.  A long engine
    step is a property of one engine and must be uncorrelated with the others.
  * A gateway stall coincides with the synchronised burst-leader events that
    tail2026_stall_events.py already detects from the client's own arrival
    stream.

This script measures both, and then re-runs the reconstruction on burst-
corrected arrival times so the long-step statistic can be quoted with the
gateway's contribution removed.

    python3 tail2026_step_stall_overlap.py --run results/<dir> \
        [--ports 8000,8001,8002,8003] [--start-frac 0.4] [--window-s 60]
        [--tol-ms 3] [--tau 15] [--match-ms 5]
"""
import argparse
import bisect
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tail2026_step_time import cluster, engine_map, run_window  # noqa: E402


def corrected_offsets(a, tau):
    """Burst-corrected arrival offsets: bursts spread evenly, span preserved."""
    g = np.diff(a)
    out = np.empty(len(g))
    i, n = 0, len(g)
    while i < n:
        j = i + 1
        while j < n and g[j] < tau:
            j += 1
        out[i:j] = (a[j] - a[i]) / (j - i)
        i = j
    return np.concatenate([[a[0]], a[0] + np.cumsum(out)])


def scan(run, emap, ports, t_lo, t_hi, tau):
    """One pass: per-port arrival streams (raw and corrected) plus burst leaders."""
    raw = {p: [] for p in ports}
    cor = {p: [] for p in ports}
    leaders = []
    pset = set(ports)
    with open(os.path.join(run, "tbt_events.jsonl")) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("agent") != "request":
                continue
            st = d.get("start_time")
            if st is None:
                continue
            ev = d.get("chunk_events") or []
            if len(ev) < 21:
                continue
            off = np.asarray([e["arrival_offset_ms"] for e in ev], float)

            # burst leaders, for cross-referencing with the stall events
            g = np.diff(off)
            small = g < tau
            for i in range(1, len(g)):
                if small[i] and not small[i - 1]:
                    leaders.append(st + off[i] / 1000.0)

            port = emap.get((str(d.get("task_id")), str(d.get("call_index"))))
            if port not in pset:
                continue
            rid = f"{d['task_id']}#{d.get('call_index')}"
            co = corrected_offsets(off, tau)
            for k in range(len(off)):
                t = st + off[k] / 1000.0
                if t_lo <= t <= t_hi:
                    raw[port].append((t, rid))
                tc = st + co[k] / 1000.0
                if t_lo <= tc <= t_hi:
                    cor[port].append((tc, rid))
    for p in ports:
        raw[p].sort()
        cor[p].sort()
    leaders.sort()
    return raw, cor, leaders


def stall_events(leaders, t_lo, t_hi, gap_ms=5.0, min_leaders=5):
    """Synchronised release instants: >=min_leaders burst leaders within gap_ms."""
    ls = [t for t in leaders if t_lo <= t <= t_hi]
    if not ls:
        return []
    out, cur = [], [ls[0]]
    gw = gap_ms / 1000.0
    for t in ls[1:]:
        if t - cur[-1] <= gw:
            cur.append(t)
        else:
            if len(cur) >= min_leaders:
                out.append((cur[0], cur[-1]))
            cur = [t]
    if len(cur) >= min_leaders:
        out.append((cur[0], cur[-1]))
    return out


def steps_of(ev, tol_ms):
    st = cluster(ev, tol_ms)
    ts = np.array([s[0] for s in st])
    reqs = np.array([s[1] for s in st], float)
    dur = 1000.0 * np.diff(ts)
    return ts, dur, reqs


def near(sorted_times, t, w):
    i = bisect.bisect_left(sorted_times, t - w)
    return i < len(sorted_times) and sorted_times[i] <= t + w


def describe(tag, dur):
    med = float(np.median(dur))
    long_i = np.where(dur > 2 * med)[0]
    return {
        "tag": tag, "n": len(dur), "median": med,
        "p90": float(np.percentile(dur, 90)),
        "long_frac": 100.0 * len(long_i) / len(dur),
        "long_time": 100.0 * dur[long_i].sum() / dur.sum(),
        "long_i": long_i,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--ports", default=os.environ.get("ENGINE_PORTS") or "8000,8001,8002,8003")
    ap.add_argument("--start-frac", type=float, default=0.4)
    ap.add_argument("--window-s", type=float, default=60.0)
    ap.add_argument("--tol-ms", type=float, default=3.0)
    ap.add_argument("--tau", type=float, default=15.0)
    ap.add_argument("--match-ms", type=float, default=5.0)
    a = ap.parse_args()

    ports = [int(x) for x in a.ports.split(",")]
    lo, hi = run_window(a.run)
    t0 = lo + a.start_frac * (hi - lo)
    t1 = t0 + a.window_s
    emap = engine_map(a.run)
    raw, cor, leaders = scan(a.run, emap, ports, t0, t1, a.tau)
    ev_st = stall_events(leaders, t0, t1)
    w = a.match_ms / 1000.0
    T = t1 - t0

    print(f"\n=== {os.path.basename(a.run)} ===")
    print(f"  window {a.window_s:.0f}s from {100*a.start_frac:.0f}% into the load; "
          f"burst leaders in window {sum(1 for t in leaders if t0<=t<=t1):,}; "
          f"synchronised stall events {len(ev_st):,} ({len(ev_st)/T:.2f}/s)")

    ends = {}
    stats = {}
    for p in ports:
        if len(raw[p]) < 1000:
            print(f"  engine {p}: only {len(raw[p])} arrivals; skipped")
            continue
        ts, dur, reqs = steps_of(raw[p], a.tol_ms)
        s = describe(f"{p} raw", dur)
        s["reqs_med"] = float(np.median(reqs))
        stats[p] = s
        # a step is the interval [ts[i], ts[i+1]]; it ENDS at the release instant
        ends[p] = ts[1:][s["long_i"]].tolist()

    if len(stats) < 2:
        print("  fewer than two engines reconstructed; cannot test coincidence")
        return 0

    print(f"\n  {'engine':>7s} {'steps':>7s} {'reqs/step':>10s} {'p50 ms':>8s} "
          f"{'p90 ms':>8s} {'>2x med':>9s} {'of time':>9s} {'long n':>7s}")
    for p, s in stats.items():
        print(f"  {p:>7d} {s['n']:>7,} {s['reqs_med']:>10.0f} {s['median']:>8.1f} "
              f"{s['p90']:>8.1f} {s['long_frac']:>8.1f}% {s['long_time']:>8.1f}% "
              f"{len(s['long_i']):>7,}")

    print(f"\n  --- cross-engine coincidence of long steps (+-{a.match_ms:g} ms on the "
          f"release instant) ---")
    print("  a gateway stall hits all engines at once; a real long step does not")
    for p in ends:
        others = [o for o in ends if o != p]
        for o in others:
            srt = sorted(ends[o])
            if not ends[p] or not srt:
                continue
            hit = sum(1 for t in ends[p] if near(srt, t, w))
            obs = 100.0 * hit / len(ends[p])
            chance = 100.0 * min(1.0, len(srt) * 2 * w / T)
            print(f"    {p} long steps also long on {o}: observed {obs:5.1f}%  "
                  f"chance {chance:5.1f}%  ratio {obs/chance if chance else float('nan'):5.1f}x")
        srt_all = sorted(t for o in others for t in ends[o])
        allhit = sum(1 for t in ends[p] if all(near(sorted(ends[o]), t, w) for o in others))
        print(f"    {p} long steps long on ALL {len(others)} other engines: "
              f"{100.0*allhit/max(1,len(ends[p])):5.1f}%")

    print(f"\n  --- long steps sitting inside a synchronised gateway stall event ---")
    st_starts = [s for s, _ in ev_st]
    st_ends = [e for _, e in ev_st]
    for p, s in stats.items():
        if not ends[p]:
            continue
        n_in = 0
        for t in ends[p]:
            i = bisect.bisect_left(st_ends, t - w)
            if i < len(st_ends) and st_starts[i] - w <= t <= st_ends[i] + w:
                n_in += 1
        print(f"    engine {p}: {100.0*n_in/len(ends[p]):5.1f}% of long steps "
              f"({n_in:,}/{len(ends[p]):,}) end inside a stall event")

    print(f"\n  --- same reconstruction on burst-corrected arrivals (tau={a.tau:g} ms) ---")
    print(f"  {'engine':>7s} {'steps':>7s} {'reqs/step':>10s} {'p50 ms':>8s} "
          f"{'p90 ms':>8s} {'>2x med':>9s} {'of time':>9s}   (raw -> corrected)")
    for p in stats:
        if len(cor[p]) < 1000:
            continue
        ts, dur, reqs = steps_of(cor[p], a.tol_ms)
        c = describe(f"{p} cor", dur)
        r = stats[p]
        print(f"  {p:>7d} {c['n']:>7,} {np.median(reqs):>10.0f} {c['median']:>8.1f} "
              f"{c['p90']:>8.1f} {c['long_frac']:>8.1f}% {c['long_time']:>8.1f}%   "
              f"({r['long_frac']:.1f}% -> {c['long_frac']:.1f}%,  "
              f"{r['long_time']:.1f}% -> {c['long_time']:.1f}% of time,  "
              f"p90 {r['p90']:.1f} -> {c['p90']:.1f},  "
              f"batch {r['reqs_med']:.0f} -> {np.median(reqs):.0f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
