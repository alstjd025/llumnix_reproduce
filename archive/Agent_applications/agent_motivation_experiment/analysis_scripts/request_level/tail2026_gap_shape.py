#!/usr/bin/env python3
"""The actual shape of the inter-token gap series inside single requests.

The question this answers is which of two mechanisms produces the raised
within-request p90/p50 ratio of the `fspfx` arm:

  (a) recompute preemption   a few enormous gaps, each one a whole prefill
                             recomputed after the engine freed the request's
                             key-value blocks
  (b) prefill interference   many moderately long gaps, each one an engine step
                             that also carried a prefill chunk for some other
                             request

The two are separated by counting, not by reasoning about the ratio. For each
request this reads the raw chunk arrival offsets out of `tbt_events.jsonl` and
reports, in units of that request's OWN median gap so the absolute-calibration
problem cannot enter, how many gaps are moderately long (2x, 5x) and how many
are large enough to be a recompute (20x, 50x, 100x). A 1,500-token prompt
recomputed at a decode step near 40-50 ms is on the order of hundreds of median
gaps; a step that carried one prefill chunk is a few.

`tbt_events.jsonl` is about 900 MB per 8-minute run, so it is streamed line by
line and only the derived per-request counters are kept.

    python3 tail2026_gap_shape.py --run results/<dir> [--run ...] \
        [--class chat] [--min-tokens 100] [--examples 8]
"""
import argparse
import json
import os
import sys

import numpy as np

MULT = [2, 3, 5, 10, 20, 50, 100]


def klass(tid):
    p = str(tid).split("-")[0]
    return {"sg": "chat", "sa": "deepresearch"}.get(p, "swe")


def per_request(path, want_class, min_tokens):
    """One record per completed request of the wanted class."""
    recs = []
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("agent") != "request":
                continue
            if want_class and klass(d.get("task_id")) != want_class:
                continue
            ev = d.get("chunk_events") or []
            gaps = [e["inter_arrival_ms"] for e in ev
                    if e.get("inter_arrival_ms") is not None]
            if len(gaps) < min_tokens:
                continue
            g = np.asarray(gaps, dtype=float)
            med = float(np.median(g))
            if med <= 0:
                continue
            r = {"task_id": d.get("task_id"), "n_gaps": len(g),
                 "out_tok": d.get("output_tokens"),
                 "in_tok": d.get("input_tokens"),
                 "med_ms": med,
                 "p90_ms": float(np.percentile(g, 90)),
                 "p99_ms": float(np.percentile(g, 99)),
                 "max_ms": float(g.max()),
                 "sum_ms": float(g.sum())}
            r["ratio"] = r["p90_ms"] / med
            for m in MULT:
                r[f"n_gt{m}x"] = int((g > m * med).sum())
                # Share of the request's total streaming time spent inside gaps
                # above this multiple. A mechanism that damages the tail has to
                # own a large share of the time, not only a large count.
                r[f"time_gt{m}x"] = float(g[g > m * med].sum()) / r["sum_ms"]
            recs.append(r)
    return recs


def report(tag, recs, examples):
    if not recs:
        print(f"\n=== {tag} === no requests matched")
        return
    n = len(recs)
    ratio = np.array([r["ratio"] for r in recs])
    print(f"\n=== {tag} ===  {n:,} completed requests")
    print(f"  within-request p90/p50 ratio: median {np.median(ratio):.2f}, "
          f"p90 {np.percentile(ratio, 90):.2f}, share above 2.0 "
          f"{100 * (ratio > 2).mean():.1f}%")
    print(f"  median gap {np.median([r['med_ms'] for r in recs]):.1f} ms, "
          f"median of the per-request maximum gap "
          f"{np.median([r['max_ms'] for r in recs]):.0f} ms, "
          f"p99 of it {np.percentile([r['max_ms'] for r in recs], 99):.0f} ms")
    print(f"  {'mult':>6}{'gaps/req (med)':>16}{'gaps/req (mean)':>17}"
          f"{'% req with >=1':>16}{'% of stream time':>18}")
    for m in MULT:
        c = np.array([r[f"n_gt{m}x"] for r in recs], dtype=float)
        t = np.array([r[f"time_gt{m}x"] for r in recs], dtype=float)
        print(f"  >{m:>4}x{np.median(c):>16.1f}{c.mean():>17.2f}"
              f"{100 * (c > 0).mean():>15.1f}%{100 * np.mean(t):>17.1f}%")

    hi = sorted([r for r in recs if r["ratio"] > 2.0],
                key=lambda r: -r["ratio"])[:examples]
    if hi:
        print(f"  highest-ratio requests (of the {int((ratio > 2).sum()):,} above 2.0):")
        print(f"    {'out_tok':>8}{'med_ms':>9}{'p90_ms':>9}{'max_ms':>9}"
              f"{'ratio':>7}{'>2x':>6}{'>5x':>6}{'>20x':>6}{'>50x':>6}")
        for r in hi:
            print(f"    {r['out_tok']:>8}{r['med_ms']:>9.1f}{r['p90_ms']:>9.1f}"
                  f"{r['max_ms']:>9.0f}{r['ratio']:>7.2f}"
                  f"{r['n_gt2x']:>6}{r['n_gt5x']:>6}{r['n_gt20x']:>6}{r['n_gt50x']:>6}")
    # The same table for the requests in the middle of the ratio distribution,
    # because the extreme tail of a distribution can be a different mechanism
    # from its body and the body is what moves the arm's median.
    mid = sorted(recs, key=lambda r: abs(r["ratio"] - float(np.median(ratio))))[:examples]
    print("  requests at the median ratio:")
    print(f"    {'out_tok':>8}{'med_ms':>9}{'p90_ms':>9}{'max_ms':>9}"
          f"{'ratio':>7}{'>2x':>6}{'>5x':>6}{'>20x':>6}{'>50x':>6}")
    for r in mid:
        print(f"    {r['out_tok']:>8}{r['med_ms']:>9.1f}{r['p90_ms']:>9.1f}"
              f"{r['max_ms']:>9.0f}{r['ratio']:>7.2f}"
              f"{r['n_gt2x']:>6}{r['n_gt5x']:>6}{r['n_gt20x']:>6}{r['n_gt50x']:>6}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--class", dest="klass", default="chat")
    ap.add_argument("--min-tokens", type=int, default=100)
    ap.add_argument("--examples", type=int, default=8)
    a = ap.parse_args()
    for run in a.run:
        p = os.path.join(run, "tbt_events.jsonl")
        if not os.path.exists(p):
            print(f"\n=== {os.path.basename(run)} === no tbt_events.jsonl")
            continue
        report(os.path.basename(run), per_request(p, a.klass, a.min_tokens),
               a.examples)
    return 0


if __name__ == "__main__":
    sys.exit(main())
