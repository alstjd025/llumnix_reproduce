#!/usr/bin/env python3
"""Second pass over the token streams: gaps conditioned on engine state, and every large gap.

`tail2026_step_batch.py` pairs each token gap with the running batch of its
engine. That is enough for the centre of the step-time curve but not for two
questions it raised.

(1) THE SPREAD AT EQUAL WORK. The engine-side mean shows that step time is a
function of two things, not one: the number of running sequences AND how much
key-value cache they hold, because attention cost grows with total context
length and a batch of 120 long requests is not the same work as a batch of 120
short ones. So the gap distribution is binned here on BOTH axes, which is what
decides whether the two arms lie on one surface rather than one curve.

(2) THE LARGE GAPS. Both arms show a bump of gaps at roughly 500-550 ms with a
hard upper edge just above 555 ms, and nothing between 620 ms and several
seconds. A feature that sits at a fixed value regardless of load is the shape of
a timeout, a retry or a fixed scheduling quantum, not the shape of queueing. To
tell those apart every gap above --big-ms is written out with the instant it
ended, the engine it ended on, how far into the request it happened and what the
engine was doing at the time. Two tests then separate the cases: if the large
gaps of concurrently running requests on ONE engine end at the same instant they
are an engine step (one long step delays every sequence in the batch, which is
what continuous batching means); if they are synchronised ACROSS engines they
are upstream of the engines, in the client or the proxy; and if they sit at a
fixed offset from the start of the request they belong to the request's own
lifecycle rather than to the fleet's state.

OUTPUTS (into --out-dir)
  13_bk_hist_<run>.npz     gap histogram on (running batch x KV occupancy)
  13_biggaps_<run>.npz     every gap above the threshold, with its context
  13_bk_curve.csv          per arm x batch x KV cell: n, p50, p90, p99

    python3 tail2026_bigap.py --results results --glob '*exp82r[12]_*' \
        --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import csv
import glob
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tail2026_step_batch import (  # noqa: E402
    ENGINES, GAUGE, engine_series, klass, parse_run, read_engine_map, usable_requests)

# Coarser than the one-dimensional histogram because the support is now spread
# over two axes: 0.5 ms to 200 ms holds every ordinary step, 5 ms to 2 s holds
# the prefill-lengthened ones, 100 ms above that holds the stalls.
BK_EDGES = np.concatenate([np.arange(0.0, 200.0, 0.5),
                           np.arange(200.0, 2000.0, 5.0),
                           np.arange(2000.0, 30000.0, 100.0),
                           [np.inf]])
NBK = len(BK_EDGES) - 1
BATCH_EDGES = np.arange(0.0, 420.0, 20.0)      # 20-wide: two axes need coarser bins
KV_EDGES = np.arange(0.0, 1.05, 0.10)
NB, NK = len(BATCH_EDGES) - 1, len(KV_EDGES) - 1


def quantile(h, edges, qs):
    tot = h.sum()
    if tot == 0:
        return [np.nan] * len(qs)
    c = np.cumsum(h)
    out = []
    for q in qs:
        i = min(int(np.searchsorted(c, q * tot)), len(edges) - 2)
        hi = edges[i + 1] if np.isfinite(edges[i + 1]) else edges[i] + 100.0
        out.append(0.5 * (edges[i] + hi))
    return out


def run_one(run, map_dir, out_dir, big_ms):
    meta = parse_run(run)
    if meta is None:
        return None
    emap = read_engine_map(run, map_dir)
    ser = engine_series(run)
    if emap is None or not ser:
        return None
    ok, _, _ = usable_requests(run)

    bt, bidx_arr, kidx_arr = {}, {}, {}
    batch_v, kv_v, prompt_v = {}, {}, {}
    for p, s in ser.items():
        bt[p] = s["t"]
        bidx_arr[p] = np.clip(np.digitize(s["batch"], BATCH_EDGES) - 1, 0, NB - 1)
        kidx_arr[p] = np.clip(np.digitize(s["kv"], KV_EDGES) - 1, 0, NK - 1)
        batch_v[p], kv_v[p] = s["batch"], s["kv"]
        dt = np.diff(s["t"], prepend=s["t"][0] - 1.0)
        prompt_v[p] = np.diff(s["prompt"], prepend=s["prompt"][0]) / np.where(dt > 0, dt, 1)

    hist = np.zeros((NB, NK, NBK), dtype=np.int32)
    big = {k: [] for k in ("t_end", "engine", "gap", "offset", "batch", "kv",
                           "prompt_tok_s", "req", "klass")}
    kls_code = {"chat": 0, "deepresearch": 1, "swe": 2}
    n_req = 0
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
            if port is None or port not in bt:
                continue
            ce = d.get("chunk_events") or []
            if len(ce) < 3:
                continue
            st = d["start_time"]
            off = np.array([c.get("arrival_offset_ms") or 0.0 for c in ce], float)
            gaps = np.diff(off)
            keep = gaps >= 0
            gaps, off_end = gaps[keep], off[1:][keep]
            if gaps.size == 0:
                continue
            idx = np.clip(np.searchsorted(bt[port], st + off_end / 1000.0),
                          0, len(bt[port]) - 1)
            np.add.at(hist, (bidx_arr[port][idx], kidx_arr[port][idx],
                             np.clip(np.digitize(gaps, BK_EDGES) - 1, 0, NBK - 1)), 1)
            m = gaps >= big_ms
            if m.any():
                big["t_end"].append(st + off_end[m] / 1000.0)
                big["engine"].append(np.full(m.sum(), port))
                big["gap"].append(gaps[m])
                big["offset"].append(off_end[m])
                big["batch"].append(batch_v[port][idx[m]])
                big["kv"].append(kv_v[port][idx[m]])
                big["prompt_tok_s"].append(prompt_v[port][idx[m]])
                big["req"].append(np.full(m.sum(), n_req))
                big["klass"].append(np.full(m.sum(), kls_code[klass(d["task_id"])]))
            n_req += 1

    np.savez_compressed(os.path.join(out_dir, f"13_bk_hist_{meta['run']}.npz"),
                        hist=hist, edges=BK_EDGES, batch_edges=BATCH_EDGES,
                        kv_edges=KV_EDGES)
    packed = {k: (np.concatenate(v) if v else np.array([])) for k, v in big.items()}
    np.savez_compressed(os.path.join(out_dir, f"13_biggaps_{meta['run']}.npz"),
                        n_req=n_req, n_gaps=int(hist.sum()), **packed)
    meta["n_big"] = int(packed["gap"].size)
    meta["n_gaps"] = int(hist.sum())
    return meta, hist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--glob", default="*exp82r[12]_*")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--map-dir", default=None)
    ap.add_argument("--big-ms", type=float, default=250.0)
    a = ap.parse_args()
    if a.map_dir is None:
        a.map_dir = os.path.join(a.out_dir, "engine_maps")
    runs = sorted(d for d in glob.glob(os.path.join(a.results, a.glob))
                  if os.path.isdir(d) and "PRERUN" not in d)
    rows = []
    for run in runs:
        p = os.path.join(run, "tbt_events.jsonl")
        if not os.path.isfile(p) or os.path.getsize(p) < 1e6:
            continue
        r = run_one(run, a.map_dir, a.out_dir, a.big_ms)
        if r is None:
            continue
        meta, hist = r
        print(f"  {meta['run']}: {meta['n_gaps']} gaps, {meta['n_big']} above {a.big_ms} ms",
              file=sys.stderr)
        for b in range(NB):
            for k in range(NK):
                h = hist[b, k]
                if h.sum() < 200:
                    continue
                q = quantile(h, BK_EDGES, [0.5, 0.9, 0.95, 0.99])
                rows.append({"run": meta["run"], "arm": meta["arm"], "rep": meta["rep"],
                             "rate": meta["rate"],
                             "batch_lo": BATCH_EDGES[b], "kv_lo": round(KV_EDGES[k], 2),
                             "n": int(h.sum()), "p50": q[0], "p90": q[1],
                             "p95": q[2], "p99": q[3]})
    if rows:
        with open(os.path.join(a.out_dir, "13_bk_curve.csv"), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote 13_bk_curve.csv ({len(rows)} rows)")


if __name__ == "__main__":
    main()
