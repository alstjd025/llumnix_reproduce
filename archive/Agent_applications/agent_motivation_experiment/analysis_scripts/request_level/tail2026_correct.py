#!/usr/bin/env python3
"""Sensitivity of the burst correction to the threshold tau.

The correction is the one in
results/aggregate_analysis/tail_2026-08-16/burst_correct.py: consecutive chunks
whose arrival gap is below tau form one burst, and every token in the burst is
charged the same share of the wall-clock time the burst covers, measured from
the last chunk that arrived before it.  It is a no-op on a stream with no
sub-tau gaps.

What this adds is the threshold sweep in one pass, so the question "is the
answer an artefact of the threshold" can be answered directly.  The two
populations the threshold has to separate are

  * gaps inside a burst, which run from the client's own per-chunk decoding
    cost up to a few ms, and
  * real decode steps, whose floor on this hardware is about 16 ms -- the
    llm-d arm, which does not burst, has a 1st percentile of 15.98 ms.

so any tau from about 13 to 16 ms should give the same answer if the
correction is well posed.

Usage:
  tail2026_correct.py RUNDIR [...] [--taus 2,5,10,13,15] [--class chat]
"""
import argparse
import json
import os
import sys

import numpy as np


def classify(task_id: str) -> str:
    t = task_id or ""
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("dr-") or t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def corrected(a, tau):
    """Per-token times (ms) from chunk arrival offsets, bursts spread evenly."""
    g = np.diff(a)
    out = np.empty(len(g))
    i = 0
    n = len(g)
    while i < n:
        j = i + 1
        while j < n and g[j] < tau:
            j += 1
        out[i:j] = (a[j] - a[i]) / (j - i)
        i = j
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="+")
    ap.add_argument("--taus", default="2,5,10,13,15")
    ap.add_argument("--class", dest="cls", default="chat")
    ap.add_argument("--max-requests", type=int, default=1500)
    a = ap.parse_args()
    taus = [float(x) for x in a.taus.split(",")]

    print(f"{'run':44s} {'tau':>5s} {'n':>5s} {'<tau%':>6s} {'mean':>7s} "
          f"{'corMean':>8s} {'p50':>7s} {'corP50':>7s} {'p90':>8s} {'corP90':>8s} "
          f"{'p90/p50':>8s} {'cor r':>7s}")
    for d in a.rundirs:
        p = os.path.join(d, "tbt_events.jsonl")
        if not os.path.exists(p):
            print(f"# missing {p}", file=sys.stderr)
            continue
        acc = {t: {"m": [], "cm": [], "p50": [], "cp50": [], "p90": [],
                   "cp90": [], "f": []} for t in taus}
        n = 0
        with open(p) as f:
            for line in f:
                if n >= a.max_requests:
                    break
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("agent") != "request":
                    continue
                if classify(rec.get("task_id", "")) != a.cls:
                    continue
                ev = rec.get("chunk_events") or []
                if len(ev) < 21:
                    continue
                off = np.asarray([e["arrival_offset_ms"] for e in ev], float)
                g = np.diff(off)
                n += 1
                for t in taus:
                    c = corrected(off, t)
                    z = acc[t]
                    z["f"].append(float((g < t).mean()))
                    z["m"].append(float(g.mean()))
                    z["cm"].append(float(c.mean()))
                    z["p50"].append(float(np.percentile(g, 50)))
                    z["cp50"].append(float(np.percentile(c, 50)))
                    z["p90"].append(float(np.percentile(g, 90)))
                    z["cp90"].append(float(np.percentile(c, 90)))
        if n == 0:
            continue
        name = os.path.basename(d)
        for t in taus:
            z = acc[t]
            md = lambda k: float(np.median(z[k]))
            print(f"{name:44s} {t:5.0f} {n:5d} {md('f')*100:6.2f} "
                  f"{md('m'):7.2f} {md('cm'):8.2f} {md('p50'):7.2f} "
                  f"{md('cp50'):7.2f} {md('p90'):8.2f} {md('cp90'):8.2f} "
                  f"{md('p90')/md('p50'):8.3f} {md('cp90')/md('cp50'):7.3f}")


if __name__ == "__main__":
    main()
