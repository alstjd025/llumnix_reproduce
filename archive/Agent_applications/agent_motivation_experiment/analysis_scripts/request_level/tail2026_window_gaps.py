#!/usr/bin/env python3
"""Per engine, per one-second window: the distribution of the token gaps that ended in it.

The histogram passes bin gaps by the engine state they saw. This pass keeps the
window as the unit instead, so every gap statistic sits in the same row as the
engine's running batch, KV occupancy, prefill token rate, waiting queue and
preemption count for that same second. That makes the "do the two arms lie on
one curve" question answerable with the third variable held fixed as well as the
second, and it is the table the reweighting in the report is computed from.

A window is kept only if at least 30 gaps ended in it, so that its p90 means
something; the kept fraction is printed.

    python3 tail2026_window_gaps.py --results results --glob '*exp82r[12]_*' \
        --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import collections
import csv
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tail2026_step_batch import (  # noqa: E402
    engine_series, klass, parse_run, read_engine_map, usable_requests)

MIN_GAPS = 30


def run_one(run, map_dir):
    meta = parse_run(run)
    if meta is None:
        return []
    emap = read_engine_map(run, map_dir)
    ser = engine_series(run)
    if emap is None or not ser:
        return []
    ok, _, _ = usable_requests(run)

    # Collect gaps per (engine, window index). Window i is the interval that
    # ends at scrape i, so a gap is assigned to the scrape that first follows it.
    buckets = collections.defaultdict(list)
    with open(os.path.join(run, "tbt_events.jsonl")) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            key = (d.get("task_id"), str(d.get("call_index", "")))
            if key not in ok:
                continue
            port = emap.get(key)
            if port is None or port not in ser:
                continue
            ce = d.get("chunk_events") or []
            if len(ce) < 3:
                continue
            off = np.array([c.get("arrival_offset_ms") or 0.0 for c in ce], float)
            gaps = np.diff(off)
            keep = gaps >= 0
            gaps, off_end = gaps[keep], off[1:][keep]
            if gaps.size == 0:
                continue
            t = ser[port]["t"]
            idx = np.clip(np.searchsorted(t, d["start_time"] + off_end / 1000.0),
                          0, len(t) - 1)
            for i in np.unique(idx):
                buckets[(port, int(i))].append(gaps[idx == i])

    rows = []
    for (port, i), chunks in buckets.items():
        g = np.concatenate(chunks)
        if g.size < MIN_GAPS:
            continue
        s = ser[port]
        j = max(i - 1, 0)
        dt = s["t"][i] - s["t"][j] if i > j else 1.0
        dc = s["itl_count"][i] - s["itl_count"][j]
        rows.append({
            "run": meta["run"], "arm": meta["arm"], "rep": meta["rep"],
            "rate": meta["rate"], "engine": port, "t": s["t"][i],
            "batch": s["batch"][i], "kv": s["kv"][i], "queue": s["queue"][i],
            "prompt_tok_s": (s["prompt"][i] - s["prompt"][j]) / dt if dt > 0 else np.nan,
            "gen_tok_s": (s["gen"][i] - s["gen"][j]) / dt if dt > 0 else np.nan,
            "preempt": s["preempt"][i] - s["preempt"][j],
            "engine_itl_ms": 1000.0 * (s["itl_sum"][i] - s["itl_sum"][j]) / dc if dc > 0 else np.nan,
            "n": int(g.size), "p50": float(np.median(g)),
            "p90": float(np.percentile(g, 90)), "p99": float(np.percentile(g, 99)),
            "mean": float(g.mean()), "max": float(g.max()),
            "n_over250": int((g >= 250).sum()),
            "n_over500": int(((g >= 490) & (g <= 570)).sum()),
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--glob", default="*exp82r[12]_*")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--map-dir", default=None)
    a = ap.parse_args()
    if a.map_dir is None:
        a.map_dir = os.path.join(a.out_dir, "engine_maps")
    out = []
    for run in sorted(glob.glob(os.path.join(a.results, a.glob))):
        p = os.path.join(run, "tbt_events.jsonl")
        if not os.path.isdir(run) or "PRERUN" in run:
            continue
        if not os.path.isfile(p) or os.path.getsize(p) < 1e6:
            continue
        r = run_one(run, a.map_dir)
        print(f"  {os.path.basename(run)}: {len(r)} windows", file=sys.stderr)
        out.extend(r)
    with open(os.path.join(a.out_dir, "14_window_gaps.csv"), "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"wrote 14_window_gaps.csv ({len(out)} rows)")


if __name__ == "__main__":
    main()
