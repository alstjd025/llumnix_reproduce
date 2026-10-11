#!/usr/bin/env python3
"""Are burst onsets synchronised across concurrently streaming requests?

A burst leader is the chunk reached by the last gap >= tau before a run of
gaps < tau: the instant at which a stalled stream resumes and delivers several
already-generated tokens at once.

Two mechanisms predict opposite things about the absolute wall-clock times of
those instants across different requests:

  * A stall inside a shared component that serves every stream at once (the
    proxy process being descheduled, garbage collection, CFS quota
    throttling) resumes all of its live streams together, so leaders from many
    different requests land in the same millisecond.
  * Buffering that is private to one connection (a socket read that happens to
    find two events waiting) fires independently per request, so leaders are
    spread out in proportion to the number of live streams.

The test bins leader times and compares the variance-to-mean ratio (Fano
factor) of the per-bin counts against a null built by shifting each request's
own leader train by an independent random offset.  The shift keeps every
request's internal burst structure and its position in the run, and destroys
only the alignment between requests.

Usage:
  tail2026_burst_sync.py RUNDIR [RUNDIR ...] [--tau 5.0] [--bin-ms 2]
                         [--max-requests 100000] [--class chat]
"""
import argparse
import json
import os
import random
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


def fano(counts_by_bin, n_bins):
    """Variance-to-mean ratio over n_bins bins, most of them empty."""
    tot = sum(counts_by_bin.values())
    if n_bins <= 1 or tot == 0:
        return float("nan"), 0.0
    mean = tot / n_bins
    ss = sum(v * v for v in counts_by_bin.values())
    var = (ss - 2 * mean * tot + n_bins * mean * mean) / (n_bins - 1)
    return var / mean, mean


def collect(path, tau, max_requests, want_class):
    ev = os.path.join(path, "tbt_events.jsonl")
    if not os.path.exists(ev):
        return None
    leaders = []        # (abs_time, request_index, burst_size)
    spans = []          # (t_start, t_end) of each request's streaming window
    n = 0
    with open(ev) as f:
        for line in f:
            if n >= max_requests:
                break
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
            ridx = n
            n += 1
            spans.append((t0 + offs[idxs[0]] / 1000.0, t0 + offs[idxs[-1]] / 1000.0))
            smallset = {i for i in idxs if gaps[i] < tau}
            for i in idxs:
                if (i + 1) in smallset and i not in smallset:
                    j = i + 1
                    sz = 1
                    while j in smallset:
                        sz += 1
                        j += 1
                    leaders.append((t0 + offs[i] / 1000.0, ridx, sz))
    if n == 0:
        return None
    return leaders, spans, n


def live_streams(spans, times):
    """Number of streaming windows covering each query time (sorted input)."""
    starts = sorted(s for s, _ in spans)
    ends = sorted(e for _, e in spans)
    out = []
    import bisect
    for t in times:
        out.append(bisect.bisect_right(starts, t) - bisect.bisect_right(ends, t))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="+")
    ap.add_argument("--tau", type=float, default=5.0)
    ap.add_argument("--bin-ms", type=float, default=2.0)
    ap.add_argument("--max-requests", type=int, default=100000)
    ap.add_argument("--class", dest="cls", default="chat")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    rng = random.Random(a.seed)
    binw = a.bin_ms / 1000.0

    for d in a.rundirs:
        got = collect(d, a.tau, a.max_requests, a.cls)
        if not got:
            print(f"# no data: {d}", file=sys.stderr)
            continue
        leaders, spans, nreq = got
        print(f"\n== {os.path.basename(d)}  tau={a.tau} bin={a.bin_ms}ms "
              f"class={a.cls} requests={nreq} leaders={len(leaders)}")
        if len(leaders) < 100:
            print("   too few burst leaders to test synchronisation "
                  f"({len(leaders)}) -- this arm essentially does not burst")
            continue

        t_lo = min(t for t, _, _ in leaders)
        t_hi = max(t for t, _, _ in leaders)
        dur = t_hi - t_lo
        n_bins = max(1, int(dur / binw))

        obs = Counter()
        for t, _, _ in leaders:
            obs[int((t - t_lo) / binw)] += 1
        f_obs, mean = fano(obs, n_bins)

        # null: shift each request's whole leader train by an independent
        # uniform offset in +-1 s.  Internal structure preserved.
        offsets = {}
        nul = Counter()
        for t, r, _ in leaders:
            if r not in offsets:
                offsets[r] = rng.uniform(-1.0, 1.0)
            nul[int(((t + offsets[r]) - t_lo) / binw)] += 1
        f_nul, _ = fano(nul, n_bins)

        # how crowded are the crowded bins, and how many distinct requests
        top = sorted(obs.items(), key=lambda kv: -kv[1])[:5]
        occ = Counter(obs.values())
        multi = sum(v for k, v in occ.items() if k >= 2)
        print(f"   leaders/s = {len(leaders)/dur:.1f}   mean per bin = {mean:.4f}")
        print(f"   Fano factor  observed = {f_obs:.3f}   shifted null = {f_nul:.3f}"
              f"   ratio = {f_obs/f_nul:.3f}" if f_nul else "")
        print(f"   bins with >=2 leaders: observed {multi}  "
              f"null {sum(v for k, v in Counter(nul.values()).items() if k >= 2)}")
        print(f"   busiest bins (count): {[c for _, c in top]}")

        # relate burst rate to the number of live client streams
        step = max(1.0, dur / 240.0)
        edges = [t_lo + i * step for i in range(int(dur / step))]
        cnt = Counter()
        for t, _, _ in leaders:
            cnt[int((t - t_lo) / step)] += 1
        live = live_streams(spans, edges)
        rates = [cnt.get(i, 0) / step for i in range(len(edges))]
        pairs = [(l, r) for l, r in zip(live, rates) if l > 0]
        if len(pairs) > 10:
            xs = [p[0] for p in pairs]
            ys = [p[1] for p in pairs]
            mx, my = statistics.fmean(xs), statistics.fmean(ys)
            sx = statistics.pstdev(xs) or 1e-9
            sy = statistics.pstdev(ys) or 1e-9
            r = statistics.fmean([(x - mx) * (y - my) for x, y in pairs]) / (sx * sy)
            # per-live-stream burst rate: if each stream bursts independently
            # this is flat; ys/xs should be constant
            per = [y / x for x, y in pairs]
            print(f"   live streams p50={statistics.median(xs):.0f} "
                  f"max={max(xs):.0f}   burst leaders/s p50={statistics.median(ys):.1f}")
            print(f"   corr(live streams, leaders/s) = {r:.3f}   "
                  f"leaders/s per live stream p50 = {statistics.median(per):.4f} "
                  f"(p10={sorted(per)[len(per)//10]:.4f} "
                  f"p90={sorted(per)[9*len(per)//10]:.4f})")


if __name__ == "__main__":
    main()
