#!/usr/bin/env python3
"""Group synchronised burst onsets into stall events and describe their timing.

tail2026_burst_sync.py shows that burst leaders from many different requests
land in the same millisecond.  This script groups them into stall events and
asks what kind of process produces them:

  * A periodic global operation (a timer-driven refresh that takes a lock every
    N ms, or CFS quota throttling on a 100 ms period) gives events at a
    regular spacing: a low coefficient of variation of the inter-event
    interval, or a strong concentration of event times modulo 100 ms.
  * Garbage collection or scheduler overload gives irregular spacing with a
    coefficient of variation near or above 1 and no grid alignment.

For each event it also reports how many of the live streams took part and how
long the stall lasted, measured as the median over participating streams of
(leader gap - that stream's ordinary step).

Usage:
  tail2026_stall_events.py RUNDIR [...] [--tau 5] [--cluster-ms 5]
                           [--min-leaders 5] [--class chat]
"""
import argparse
import bisect
import json
import math
import os
import statistics
import sys
from collections import Counter


def classify(task_id: str) -> str:
    t = task_id or ""
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("dr-") or t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def q(xs, p):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = p * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def collect(path, tau, want_class):
    ev = os.path.join(path, "tbt_events.jsonl")
    if not os.path.exists(ev):
        return None
    leaders = []     # (t_abs, lead_gap_ms, step_ms, burst_size)
    spans = []
    with open(ev) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("agent") != "request":
                continue
            if want_class and classify(rec.get("task_id", "")) != want_class:
                continue
            evs = rec.get("chunk_events") or []
            if len(evs) < 20:
                continue
            t0 = rec.get("start_time")
            if not t0:
                continue
            gaps = [e.get("inter_arrival_ms") for e in evs]
            offs = [e.get("arrival_offset_ms") for e in evs]
            idxs = [i for i in range(1, len(gaps)) if gaps[i] is not None]
            if len(idxs) < 20:
                continue
            spans.append((t0 + offs[idxs[0]] / 1000.0, t0 + offs[idxs[-1]] / 1000.0))
            smallset = {i for i in idxs if gaps[i] < tau}
            lead_idx = [i for i in idxs
                        if (i + 1) in smallset and i not in smallset]
            leadset = set(lead_idx)
            iso = [gaps[i] for i in idxs if i not in smallset and i not in leadset]
            if len(iso) < 10:
                continue
            step = statistics.median(iso)
            for i in lead_idx:
                j = i + 1
                sz = 1
                while j in smallset:
                    sz += 1
                    j += 1
                leaders.append((t0 + offs[i] / 1000.0, gaps[i], step, sz))
    return leaders, spans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="+")
    ap.add_argument("--tau", type=float, default=5.0)
    ap.add_argument("--cluster-ms", type=float, default=5.0)
    ap.add_argument("--min-leaders", type=int, default=5)
    ap.add_argument("--class", dest="cls", default="chat")
    a = ap.parse_args()

    for d in a.rundirs:
        got = collect(d, a.tau, a.cls)
        if not got or len(got[0]) < 100:
            print(f"\n== {os.path.basename(d)}: fewer than 100 burst leaders "
                  f"({0 if not got else len(got[0])}) -- no stall events to describe")
            continue
        leaders, spans = got
        leaders.sort()
        starts = sorted(s for s, _ in spans)
        ends = sorted(e for _, e in spans)

        gapw = a.cluster_ms / 1000.0
        clusters = []
        cur = [leaders[0]]
        for L in leaders[1:]:
            if L[0] - cur[-1][0] <= gapw:
                cur.append(L)
            else:
                clusters.append(cur)
                cur = [L]
        clusters.append(cur)

        big = [c for c in clusters if len(c) >= a.min_leaders]
        t_lo, t_hi = leaders[0][0], leaders[-1][0]
        dur = t_hi - t_lo

        n_in_big = sum(len(c) for c in big)
        print(f"\n== {os.path.basename(d)}  tau={a.tau} cluster<={a.cluster_ms}ms "
              f"class={a.cls}")
        print(f"   leaders={len(leaders)}  clusters={len(clusters)}  "
              f"clusters with >={a.min_leaders} leaders: {len(big)} "
              f"({n_in_big/len(leaders)*100:.1f}% of all leaders)")
        if not big:
            continue
        print(f"   stall events/s = {len(big)/dur:.2f}")

        sizes = [len(c) for c in big]
        widths = [(c[-1][0] - c[0][0]) * 1000 for c in big]
        parts = []
        durs = []
        bsz = []
        for c in big:
            t = c[0][0]
            live = bisect.bisect_right(starts, t) - bisect.bisect_right(ends, t)
            if live > 0:
                parts.append(len(c) / live)
            durs.append(statistics.median([g - s for _, g, s, _ in c]))
            bsz.append(statistics.median([b for _, _, _, b in c]))
        print(f"   leaders per event p10/p50/p90 = {q(sizes,.1):.0f}/"
              f"{q(sizes,.5):.0f}/{q(sizes,.9):.0f}")
        print(f"   share of live streams taking part p10/p50/p90 = "
              f"{q(parts,.1):.2f}/{q(parts,.5):.2f}/{q(parts,.9):.2f}")
        print(f"   event width (first to last leader) p50/p90 = "
              f"{q(widths,.5):.2f}/{q(widths,.9):.2f} ms")
        print(f"   stall length (lead gap - step) p10/p50/p90 = "
              f"{q(durs,.1):.1f}/{q(durs,.5):.1f}/{q(durs,.9):.1f} ms")
        print(f"   burst size in event p10/p50/p90 = "
              f"{q(bsz,.1):.1f}/{q(bsz,.5):.1f}/{q(bsz,.9):.1f}")

        iv = [(big[i + 1][0][0] - big[i][0][0]) * 1000 for i in range(len(big) - 1)]
        m = statistics.fmean(iv)
        cv = statistics.pstdev(iv) / m if m else float("nan")
        print(f"   inter-event interval mean={m:.1f} ms  p10/p50/p90="
              f"{q(iv,.1):.1f}/{q(iv,.5):.1f}/{q(iv,.9):.1f}  CV={cv:.2f} "
              f"(1.0 = memoryless, <<1 = periodic)")

        # grid alignment: concentration of event times modulo P
        for P in (0.100, 0.050, 0.250, 1.000):
            ph = [((c[0][0] % P) / P) * 2 * math.pi for c in big]
            R = math.hypot(sum(math.cos(x) for x in ph),
                           sum(math.sin(x) for x in ph)) / len(ph)
            print(f"   alignment to a {P*1000:.0f} ms grid: R={R:.3f} "
                  f"(0 = none, 1 = perfect; noise floor ~{1/math.sqrt(len(ph)):.3f})")


if __name__ == "__main__":
    main()
