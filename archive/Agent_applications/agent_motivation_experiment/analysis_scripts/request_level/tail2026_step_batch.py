#!/usr/bin/env python3
"""Pair every token gap with the running batch of the engine that produced it.

WHAT THIS TESTS. The proposed causal chain is: our routing concentrates running
requests onto fewer engines -> the loaded engine carries a larger decode batch ->
its step takes longer, both in the centre and in the spread -> every request on
that engine sees a longer time between tokens. In continuous batching a running
sequence advances by one token per engine step, so a request's time between
tokens is the step duration of its engine. The chain is therefore testable by
asking whether one curve of per-token latency against running batch size fits
BOTH arms. If it does, the arms differ only in where on that curve they choose
to sit. If our engine is slower at the SAME batch size, the placement itself
does something the batch size does not account for.

THE TWO SIDES OF THE MEASUREMENT, and why both are here.

  engine side   `vllm:inter_token_latency_seconds_{sum,count}` are cumulative
                counters scraped once a second per engine. The difference of the
                pair between two scrapes is the mean time between tokens of every
                token the engine emitted in that second, computed by the engine
                itself. It cannot be affected by anything on the client, and it
                is the quantity the client-side numbers are checked against. It
                gives only a mean: vLLM does not export the histogram buckets, so
                no engine-side percentile of step time exists in these runs.

  client side   `tbt_events.jsonl` records the arrival offset of every streamed
                chunk of every request. Attributing each request to an engine
                (`analysis/request_engine.csv`) and stamping each gap with the
                engine's running-batch gauge at the instant the gap ended gives
                the full distribution of gaps at each batch size, so median and
                p90 both exist. This side is the one the gateway CPU-quota defect
                contaminated, which is why only the EXP-82 re-runs are used.

Both are reported at every batch size. Where they disagree the engine side wins
on the centre, because it is the engine's own arithmetic.

CONTROL VARIABLES. A window's batch size is not the only thing that sets its step
time, so each window also carries the prefill token rate (`prompt_tokens_total`
delta, which is the chunked-prefill work mixed into the same steps), the KV
occupancy, the waiting queue and the preemption count. These are what a residual
difference between the arms at equal batch would have to be explained by.

OUTPUTS (into --out-dir)
  12_engine_windows.csv     one row per run x engine x one-second window
  12_gap_hist_<run>.npz     2-D histogram, running batch x gap length, per run
  12_batch_curve.csv        per arm x rate x batch bucket: n, mean, p50, p90, p99
  12_validity.csv           the sub-5-ms gap fraction and engine/client ratio

    python3 tail2026_step_batch.py --results results \
        --glob '*exp82r[12]_*' --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import csv
import glob
import json
import os
import re
import sys

import numpy as np

# Gap histogram edges. 0.1 ms resolution to 1 s covers every plausible step time
# at full precision; 2 ms resolution from there to 20 s covers the stalls; one
# overflow bin above. Quantiles are read off the cumulative counts, so the
# resolution bounds the error on a reported percentile, not the tail mass.
EDGES = np.concatenate([np.arange(0.0, 1000.0, 0.1),
                        np.arange(1000.0, 20000.0, 2.0),
                        [np.inf]])
NBIN = len(EDGES) - 1
# Running batch is bucketed in tens. Below about 20 the engine is not in the
# regime the chain is about, and above 300 only our arm ever goes, so the shared
# support of the two arms is what the comparison is actually made on.
BATCH_EDGES = np.arange(0.0, 420.0, 10.0)
NBB = len(BATCH_EDGES) - 1  # last bucket 400-410; anything above clips into it

# The fleet's size is a property of the run, not of this file. A literal
# 8000..8003 drops half of an eight-instance fleet from every panel with nothing
# saying so, so the ports come from the run's own server_metrics directory;
# $ENGINE_PORTS, then the historical four, are the fallbacks.
def engine_ports_of(run=None):
    if run:
        found = sorted(
            int(os.path.basename(f)[len("engine_"):-len(".jsonl")])
            for f in glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl")))
        if found:
            return tuple(found)
    raw = os.environ.get("ENGINE_PORTS", "").strip()
    if raw:
        return tuple(int(p) for p in raw.split(",") if p.strip())
    return (8000, 8001, 8002, 8003)


ENGINES = engine_ports_of()
GAUGE = {
    "batch": "vllm:num_requests_running",
    "queue": "vllm:num_requests_waiting",
    "kv": "vllm:kv_cache_usage_perc",
    "itl_sum": "vllm:inter_token_latency_seconds_sum",
    "itl_count": "vllm:inter_token_latency_seconds_count",
    "gen": "vllm:generation_tokens_total",
    "prompt": "vllm:prompt_tokens_total",
    "preempt": "vllm:num_preemptions_total",
}

RUN_RE = re.compile(r"exp82r(\d)_(\w+?)_m1f?_(?:PRERUN_)?rpm_(\d+)$")


def parse_run(path):
    m = RUN_RE.search(os.path.basename(path.rstrip("/")))
    if not m:
        return None
    rep, arm, rpm = int(m.group(1)), m.group(2), int(m.group(3))
    return {"rep": rep, "arm": arm, "rate": rpm / 60.0, "rpm": rpm,
            "run": os.path.basename(path.rstrip("/"))}


def klass(tid):
    p = str(tid).split("-")[0]
    return {"sg": "chat", "sa": "deepresearch"}.get(p, "swe")


def engine_series(run):
    """port -> dict of numpy arrays, one entry per scrape that reported ok."""
    out = {}
    for port in ENGINES:
        path = os.path.join(run, "server_metrics", f"engine_{port}.jsonl")
        if not os.path.isfile(path):
            continue
        cols = {k: [] for k in GAUGE}
        ts = []
        with open(path) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not d.get("ok"):
                    continue
                g = {}
                for k, v in d.items():
                    if "|" in k:
                        g[k.split("|")[0]] = v
                ts.append(d["t"])
                for name, key in GAUGE.items():
                    cols[name].append(g.get(key, np.nan))
        if not ts:
            continue
        out[port] = {"t": np.array(ts, float)}
        for name in GAUGE:
            out[port][name] = np.array(cols[name], float)
    return out


def read_engine_map(run, map_dir):
    """(task_id, call_index) -> engine port."""
    cands = [os.path.join(run, "analysis", "request_engine.csv")]
    if map_dir:
        cands.append(os.path.join(map_dir, os.path.basename(run.rstrip("/")) + ".csv"))
    for p in cands:
        if not os.path.isfile(p):
            continue
        out = {}
        with open(p) as fh:
            for row in csv.DictReader(l for l in fh if not l.startswith("#")):
                if row.get("engine_port"):
                    out[(row["task_id"], str(row.get("call_index", "")))] = int(row["engine_port"])
        if out:
            return out
    return None


def usable_requests(run):
    """(task_id, call_index) of request rows that ran to completion.

    A rejected request carries `is_error` and has no stream at all, so it never
    reaches the histogram; a request cut off when the load window closed has a
    truncated gap series whose end is missing, and truncation concentrates in the
    busiest minutes, so leaving it in would bias the tail systematically. This is
    the exclusion rule the repository already uses for output length.
    """
    bad_cols = ("is_error", "is_timeout", "is_job_timeout", "is_server_terminated")
    ok, total, rejected = set(), 0, 0
    with open(os.path.join(run, "metrics.csv")) as fh:
        for row in csv.DictReader(fh):
            if row.get("agent") != "request":
                continue
            total += 1
            if str(row.get("is_rejected", "")).lower() == "true":
                rejected += 1
            if any(str(row.get(c, "")).lower() == "true" for c in bad_cols):
                continue
            ok.add((row["task_id"], str(row.get("call_index", ""))))
    return ok, total, rejected


def build(run, map_dir, out_dir, klass_split=True):
    meta = parse_run(run)
    if meta is None:
        return None
    emap = read_engine_map(run, map_dir)
    if emap is None:
        print(f"  {meta['run']}: no engine map, skipped", file=sys.stderr)
        return None
    ser = engine_series(run)
    if not ser:
        print(f"  {meta['run']}: no engine series, skipped", file=sys.stderr)
        return None
    ok, n_req, n_rej = usable_requests(run)

    # Per-engine per-window table. Window i runs from scrape i to scrape i+1.
    win_rows = []
    for port, s in ser.items():
        t, dt = s["t"], np.diff(s["t"])
        d = {k: np.diff(s[k]) for k in ("itl_sum", "itl_count", "gen", "prompt", "preempt")}
        batch_mid = 0.5 * (s["batch"][:-1] + s["batch"][1:])
        for i in range(len(dt)):
            if dt[i] <= 0:
                continue
            dc = d["itl_count"][i]
            itl = 1000.0 * d["itl_sum"][i] / dc if dc > 0 else np.nan
            gen = d["gen"][i]
            # Independent estimate: with a batch of B advancing one token per
            # step, B tokens leave the engine per step, so the step lasted
            # batch / (tokens per second).
            step_thr = 1000.0 * batch_mid[i] * dt[i] / gen if gen > 0 else np.nan
            win_rows.append({
                "run": meta["run"], "arm": meta["arm"], "rep": meta["rep"],
                "rate": meta["rate"], "engine": port, "t0": t[i], "dt_s": dt[i],
                "batch": batch_mid[i], "queue": 0.5 * (s["queue"][i] + s["queue"][i + 1]),
                "kv": 0.5 * (s["kv"][i] + s["kv"][i + 1]),
                "itl_ms": itl, "itl_tokens": dc,
                "gen_tok_s": gen / dt[i], "prompt_tok_s": d["prompt"][i] / dt[i],
                "step_ms_thr": step_thr, "preempt": d["preempt"][i],
            })

    # Gap histogram. For each engine keep the scrape times and the batch gauge so
    # a gap can be stamped with the batch that was running when it ended.
    bt = {p: ser[p]["t"] for p in ser}
    bb = {p: np.clip(np.digitize(ser[p]["batch"], BATCH_EDGES) - 1, 0, NBB - 1) for p in ser}

    hist = np.zeros((NBB, NBIN), dtype=np.int64)
    hist_cls = {c: np.zeros((NBB, NBIN), dtype=np.int64)
                for c in ("chat", "deepresearch", "swe")} if klass_split else {}
    # Per-request statistics keyed by engine, for the "is the request's own p90
    # explained by its engine" question.
    per_req = []
    n_used = n_nomap = 0
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
                n_nomap += 1
                continue
            ce = d.get("chunk_events") or []
            if len(ce) < 3:
                continue
            st = d.get("start_time")
            off = np.array([c.get("arrival_offset_ms") or 0.0 for c in ce], float)
            gaps = np.diff(off)
            gaps = gaps[gaps >= 0]
            if gaps.size == 0:
                continue
            # A gap is stamped with the batch of the scrape nearest the instant
            # the gap ENDED, which is when the token that closed it was emitted.
            tabs = st + off[1:] / 1000.0
            idx = np.searchsorted(bt[port], tabs)
            idx = np.clip(idx, 0, len(bt[port]) - 1)
            bidx = bb[port][idx]
            gidx = np.clip(np.digitize(gaps, EDGES) - 1, 0, NBIN - 1)
            np.add.at(hist, (bidx, gidx), 1)
            if klass_split:
                np.add.at(hist_cls[klass(d["task_id"])], (bidx, gidx), 1)
            n_used += 1
            per_req.append((port, float(np.median(gaps)), float(np.percentile(gaps, 90)),
                            float(np.mean(gaps)), float(gaps.max()), float(np.mean(bb[port][idx])),
                            klass(d["task_id"]), st))

    np.savez_compressed(
        os.path.join(out_dir, f"12_gap_hist_{meta['run']}.npz"),
        hist=hist, edges=EDGES, batch_edges=BATCH_EDGES,
        **{f"hist_{c}": h for c, h in hist_cls.items()})
    meta.update({"n_req": n_req, "n_rejected": n_rej, "n_streams": n_used,
                 "n_nomap": n_nomap, "n_gaps": int(hist.sum())})
    return meta, win_rows, hist, per_req


def quantile_from_hist(h, qs):
    """Quantiles of a binned distribution, read at the bin's upper edge."""
    tot = h.sum()
    if tot == 0:
        return [np.nan] * len(qs)
    c = np.cumsum(h)
    out = []
    for q in qs:
        i = int(np.searchsorted(c, q * tot))
        i = min(i, NBIN - 1)
        lo = EDGES[i]
        hi = EDGES[i + 1] if np.isfinite(EDGES[i + 1]) else EDGES[i] + 2.0
        out.append(0.5 * (lo + hi))
    return out


def mean_from_hist(h):
    tot = h.sum()
    if tot == 0:
        return np.nan
    centres = np.where(np.isfinite(EDGES[1:]), 0.5 * (EDGES[:-1] + EDGES[1:]), EDGES[:-1] + 2.0)
    return float((h * centres).sum() / tot)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--glob", default="*exp82r[12]_*")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--map-dir", default=None)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    if a.map_dir is None:
        a.map_dir = os.path.join(a.out_dir, "engine_maps")

    runs = sorted(d for d in glob.glob(os.path.join(a.results, a.glob))
                  if os.path.isdir(d) and "PRERUN" not in d)
    win_all, meta_all, curve_rows, req_rows = [], [], [], []
    for run in runs:
        if not os.path.isfile(os.path.join(run, "tbt_events.jsonl")):
            continue
        if os.path.getsize(os.path.join(run, "tbt_events.jsonl")) < 1e6:
            print(f"  {os.path.basename(run)}: tbt_events too small (in flight?), skipped",
                  file=sys.stderr)
            continue
        r = build(run, a.map_dir, a.out_dir)
        if r is None:
            continue
        meta, win_rows, hist, per_req = r
        print(f"  {meta['run']}: {meta['n_streams']} streams, {meta['n_gaps']} gaps",
              file=sys.stderr)
        win_all.extend(win_rows)
        meta_all.append(meta)
        for pr in per_req:
            req_rows.append({"run": meta["run"], "arm": meta["arm"], "rep": meta["rep"],
                             "rate": meta["rate"], "engine": pr[0], "p50": pr[1],
                             "p90": pr[2], "mean": pr[3], "max": pr[4],
                             "batch_bucket_mean": pr[5], "klass": pr[6], "start": pr[7]})
        for b in range(NBB):
            h = hist[b]
            if h.sum() == 0:
                continue
            q = quantile_from_hist(h, [0.5, 0.9, 0.95, 0.99, 0.999])
            curve_rows.append({
                "run": meta["run"], "arm": meta["arm"], "rep": meta["rep"],
                "rate": meta["rate"], "batch_lo": BATCH_EDGES[b], "batch_hi": BATCH_EDGES[b + 1],
                "n_gaps": int(h.sum()), "mean_ms": mean_from_hist(h),
                "p50_ms": q[0], "p90_ms": q[1], "p95_ms": q[2], "p99_ms": q[3],
                "p999_ms": q[4],
                "frac_lt5": float(h[EDGES[:-1] < 5.0].sum() / h.sum()),
            })

    def dump(rows, name, fields=None):
        if not rows:
            return
        fields = fields or list(rows[0].keys())
        with open(os.path.join(a.out_dir, name), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {name} ({len(rows)} rows)")

    dump(win_all, "12_engine_windows.csv")
    dump(curve_rows, "12_batch_curve.csv")
    dump(meta_all, "12_run_meta.csv")
    dump(req_rows, "12_per_request_gaps.csv")


if __name__ == "__main__":
    main()
