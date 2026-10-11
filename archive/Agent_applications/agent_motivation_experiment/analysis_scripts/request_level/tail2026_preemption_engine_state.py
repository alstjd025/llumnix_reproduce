#!/usr/bin/env python3
"""Engine-side telemetry for the per-token latency tail: preemptions, key-value
occupancy, queue depth, running batch, and the engines' own inter-token latency.

WHY THIS EXISTS. `ms_dev/notes/tail-latency.md` established that at 20 req/s the
arm `fspfx` puts 77.7% of chat requests on one engine and that the client-side
per-token latency p90 on that engine is 77.6 ms against 17-18 ms on the two
engines that hold almost nothing. It named key-value pressure as a candidate
cause and recorded that nobody had counted the preemptions. This script counts
them and reads the engine state that would produce them.

WHAT THE PREEMPTION COUNTER ACTUALLY COUNTS. This project has been caught once
by a counter named for an event that counted decisions: the migration column of
`exp53_compare.py` counted `Generate rescheduling pairs` lines, and those lines
are emitted whether or not the migration call then failed with
`ResourceExhausted`. So the counter read here was checked against the source of
the engine that produced it before any number was taken from it.

The engines run the Llumnix vLLM image `vllm:20260306-165123`, whose scheduler is
vendored in this repository at `patches/vllm-sched/vendor-reference/sched/` with
`VLLM_VERSION.txt` reading `0.12.1.dev0+g4fd9d6a85.d20260305`. In that file
`vllm:num_preemptions_total` is fed from `IterationStats.num_preempted_reqs`,
which is incremented once per `EngineCoreEventType.PREEMPTED` event, and that
event is recorded only inside `Scheduler._preempt_request` (line 755). That
function is reached only after `kv_cache_manager.allocate_slots` has returned
None for a request that needs more blocks, and before it records the event it
has already called `kv_cache_manager.free(request)`, set the status to
PREEMPTED, and set `request.num_computed_tokens = 0`. The blocks are gone and the
computed prefix has been discarded by the time the counter moves, so each
increment is an eviction that happened, not one that was proposed. There is no
failure path between the decision and the count.

WHAT ONE INCREMENT COSTS, AND WHERE IT SHOWS. Because `num_computed_tokens` is
zeroed, the request re-prefills its whole prompt when it is rescheduled. On the
client that appears as ONE long gap in that request's stream: the interval from
the last token before the eviction to the first token after the recompute. It is
one gap out of however many tokens the request produces, which matters for how
it can and cannot show up in `tbt_p90_ms` -- see the correlation section of the
output.

WHY PERCENTILES AND NOT MEANS. Key-value occupancy has a ceiling at 100% and a
preemption fires when the engine reaches it, so the mean answers a different
question from the one being asked. The busiest engine is reported separately
from the fleet average for the same reason the project rule gives: an arm that
puts 77.7% of a class on one instance has a fleet average that describes an
engine that does not exist.

OUTPUTS (all under --out-dir)
  tail2026_engine_rows.csv    one row per (run, engine)
  tail2026_run_rows.csv       one row per run, fleet totals plus client metrics

    python3 tail2026_preemption_engine_state.py [--out-dir DIR]
"""
import argparse
import collections
import csv
import glob
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))

# The pinned copy of the static sweep names the exact 80 conditions the paper
# uses. It holds only metrics.csv, so the names are read from there and the
# engine telemetry is read from the full run directory of the same name.
PINNED = os.path.join(EXPDIR, "paper_experiment", "static_sweep_2026-08", "data")
RESULTS = os.path.join(EXPDIR, "results")
MIN_DIR = "260807_1900"

SERIES = {
    "batch": "vllm:num_requests_running",
    "queue": "vllm:num_requests_waiting",
    "kv": "vllm:kv_cache_usage_perc",
}
COUNTERS = {
    "preempt": "vllm:num_preemptions_total",
    "pfx_hit": "vllm:prefix_cache_hits_total",
    "pfx_q": "vllm:prefix_cache_queries_total",
    "gen_tok": "vllm:generation_tokens_total",
    "prompt_tok": "vllm:prompt_tokens_total",
    "itl_sum": "vllm:inter_token_latency_seconds_sum",
    "itl_cnt": "vllm:inter_token_latency_seconds_count",
    "ttft_sum": "vllm:time_to_first_token_seconds_sum",
    "ttft_cnt": "vllm:time_to_first_token_seconds_count",
    "pf_sum": "vllm:request_prefill_time_seconds_sum",
    "pf_cnt": "vllm:request_prefill_time_seconds_count",
    "dec_sum": "vllm:request_decode_time_seconds_sum",
    "dec_cnt": "vllm:request_decode_time_seconds_count",
}
NAME_RE = re.compile(r"^(\d{6}_\d{4})_(exp\S+?)_([a-z]+)_(m1f?)_rpm_(\d+)$")


def pick(rec, prefix):
    for k, v in rec.items():
        if k.startswith(prefix) and isinstance(v, (int, float)):
            return float(v)
    return None


def load_engine(path):
    """Time-ordered samples of every gauge and counter this analysis reads."""
    ts, gauges, counters = [], collections.defaultdict(list), collections.defaultdict(list)
    with open(path) as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("ok"):
                continue
            ts.append(float(rec["t"]))
            for name, pfx in SERIES.items():
                gauges[name].append(pick(rec, pfx))
            for name, pfx in COUNTERS.items():
                counters[name].append(pick(rec, pfx))
    return ts, gauges, counters


def counter_delta(vals):
    """Last minus first, plus the number of times the counter went backwards.

    A backwards step means the engine process restarted inside the run and the
    difference of the two ends understates the total, so it is reported rather
    than silently absorbed.
    """
    xs = [v for v in vals if v is not None]
    if len(xs) < 2:
        return None, 0
    drops = sum(1 for a, b in zip(xs, xs[1:]) if b < a - 1e-9)
    return xs[-1] - xs[0], drops


def q(xs, p):
    xs = [v for v in xs if v is not None]
    return float(np.percentile(xs, p)) if xs else float("nan")


def interval_itl(ts, counters):
    """Per-sample-interval mean inter-token latency, in milliseconds.

    The collector keeps only the `_sum` and `_count` of the engine's inter-token
    latency histogram -- `llumnix_metrics.py` captures histogram families through
    that pair and drops the `_bucket` lines -- so no engine-side percentile of
    individual token gaps exists anywhere in these runs. What can be built is the
    MEAN over each roughly one-second scrape interval, and then a distribution
    over those interval means. That is a weaker object than a distribution over
    token gaps: a single 8-second recompute gap inside an interval that also
    carried several thousand ordinary gaps is averaged away almost completely.
    It is reported for what it is.
    """
    s, c = counters["itl_sum"], counters["itl_cnt"]
    out = []
    for i in range(1, len(s)):
        if s[i] is None or s[i - 1] is None or c[i] is None or c[i - 1] is None:
            continue
        ds, dc = s[i] - s[i - 1], c[i] - c[i - 1]
        if dc > 0 and ds >= 0:
            out.append(1000.0 * ds / dc)
    return out


def engine_rows(run):
    rows = []
    for path in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        eng = os.path.basename(path).replace("engine_", "").replace(".jsonl", "")
        ts, g, c = load_engine(path)
        if len(ts) < 2:
            continue
        pre, pre_drops = counter_delta(c["preempt"])
        hit, _ = counter_delta(c["pfx_hit"])
        qry, _ = counter_delta(c["pfx_q"])
        gen, _ = counter_delta(c["gen_tok"])
        prm, _ = counter_delta(c["prompt_tok"])
        isum, _ = counter_delta(c["itl_sum"])
        icnt, _ = counter_delta(c["itl_cnt"])
        psum, _ = counter_delta(c["pf_sum"])
        pcnt, _ = counter_delta(c["pf_cnt"])
        dsum, _ = counter_delta(c["dec_sum"])
        dcnt, _ = counter_delta(c["dec_cnt"])
        iv = interval_itl(ts, c)
        row = {
            "run": os.path.basename(run), "engine": eng,
            "samples": len(ts), "span_s": round(ts[-1] - ts[0], 1),
            "preemptions": None if pre is None else round(pre),
            "preempt_counter_drops": pre_drops,
            "kv_p50": round(100 * q(g["kv"], 50), 1),
            "kv_p90": round(100 * q(g["kv"], 90), 1),
            "kv_p99": round(100 * q(g["kv"], 99), 1),
            "kv_mean": round(100 * float(np.mean([v for v in g["kv"] if v is not None])), 1),
            "kv_max": round(100 * max(v for v in g["kv"] if v is not None), 1),
            "kv_frac_ge95": round(100 * float(np.mean(
                [1.0 if v >= 0.95 else 0.0 for v in g["kv"] if v is not None])), 1),
            "queue_p50": round(q(g["queue"], 50), 1),
            "queue_p90": round(q(g["queue"], 90), 1),
            "queue_p99": round(q(g["queue"], 99), 1),
            "batch_p50": round(q(g["batch"], 50), 1),
            "batch_p90": round(q(g["batch"], 90), 1),
            "batch_p99": round(q(g["batch"], 99), 1),
            "prefix_hit_pct": round(100 * hit / qry, 1) if qry else None,
            "gen_tokens": None if gen is None else round(gen),
            "prompt_tokens": None if prm is None else round(prm),
            # Engine-side mean inter-token latency over the whole run: the
            # histogram sum divided by its count, both taken as run deltas.
            "itl_mean_ms": round(1000.0 * isum / icnt, 2) if icnt else None,
            "itl_samples": None if icnt is None else round(icnt),
            # Distribution over per-scrape-interval means, not over token gaps.
            "itl_iv_p50_ms": round(q(iv, 50), 2) if iv else None,
            "itl_iv_p90_ms": round(q(iv, 90), 2) if iv else None,
            "itl_iv_p99_ms": round(q(iv, 99), 2) if iv else None,
            "itl_iv_max_ms": round(max(iv), 2) if iv else None,
            "itl_iv_n": len(iv),
            "prefill_mean_s": round(psum / pcnt, 3) if pcnt else None,
            "decode_mean_s": round(dsum / dcnt, 3) if dcnt else None,
        }
        rows.append(row)
    return rows


def client_stats(run):
    """Per-request rows only, then the numbers the tail question is about."""
    import pandas as pd
    p = os.path.join(run, "metrics.csv")
    if not os.path.exists(p):
        return {}
    df = pd.read_csv(p, low_memory=False)
    if "agent" not in df.columns:
        return {}
    df = df[df["agent"] == "request"]
    if df.empty:
        return {}

    def flag(col):
        if col not in df.columns:
            return np.zeros(len(df), dtype=bool)
        return df[col].astype(str).str.lower().isin(["true", "1", "1.0"])

    rej = flag("is_rejected")
    offered = len(df)
    admitted = int((~rej).sum())
    # Requests that produced a usable per-token series. A request cut off by the
    # end of the load window has a truncated token stream, so its gaps are still
    # real gaps and are kept; a rejected request has no stream at all.
    ok = df[(~rej) & df["tbt_p90_ms"].notna()]
    tp90 = ok["tbt_p90_ms"].astype(float)
    tmax = ok["tbt_max_ms"].astype(float) if "tbt_max_ms" in ok.columns else tp90
    out = {
        "offered": offered,
        "admitted": admitted,
        "reject_pct": round(100.0 * int(rej.sum()) / offered, 1) if offered else None,
        "n_tbt": len(ok),
        # The headline of task 3: the client's per-request p90 of inter-token
        # gaps, summarised across requests. Its median and its own upper tail
        # are both given because they answer different questions.
        "tbtp90_med": round(float(tp90.median()), 1) if len(ok) else None,
        "tbtp90_mean": round(float(tp90.mean()), 1) if len(ok) else None,
        "tbtp90_p90": round(float(tp90.quantile(0.90)), 1) if len(ok) else None,
        "tbtmax_p90": round(float(tmax.quantile(0.90)), 1) if len(ok) else None,
        "tbtmax_p99": round(float(tmax.quantile(0.99)), 1) if len(ok) else None,
        # How many requests saw a gap long enough to be a recompute rather than
        # a slow step. 1 s is far above any step time measured here (the decode
        # law sits near 50 ms) and far below a whole prefill of a long prompt.
        "frac_gap_gt1s": round(100.0 * float((tmax > 1000).mean()), 1) if len(ok) else None,
        "out_tok_med": round(float(ok["output_tokens"].median()), 0) if len(ok) else None,
    }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(
        EXPDIR, "results", "aggregate_analysis", "tail_2026-08-16"))
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    names = sorted(os.listdir(PINNED))
    erows, rrows = [], []
    for name in names:
        if name[:11] < MIN_DIR:
            print(f"skip (older than {MIN_DIR}): {name}")
            continue
        m = NAME_RE.match(name)
        if not m:
            print(f"skip (name does not parse): {name}")
            continue
        run = os.path.join(RESULTS, name)
        if not os.path.isdir(run):
            print(f"skip (no full run directory): {name}")
            continue
        arm, rate = m.group(3), int(m.group(5)) / 60.0
        rep = 2 if "r2" in m.group(2) else 1
        er = engine_rows(run)
        if not er:
            print(f"skip (no engine series): {name}")
            continue
        for r in er:
            r.update(arm=arm, rate=rate, rep=rep)
        erows.extend(er)

        pre = [r["preemptions"] for r in er if r["preemptions"] is not None]
        busiest = max(er, key=lambda r: r["kv_p90"])
        cs = client_stats(run)
        rr = {
            "run": name, "arm": arm, "rate": rate, "rep": rep,
            "engines": len(er),
            "preempt_total": sum(pre) if pre else None,
            "preempt_max_engine": max(pre) if pre else None,
            "preempt_counter_drops": sum(r["preempt_counter_drops"] for r in er),
            "kv_p90_fleet": round(float(np.mean([r["kv_p90"] for r in er])), 1),
            "kv_p90_busiest": round(max(r["kv_p90"] for r in er), 1),
            "kv_p99_busiest": round(max(r["kv_p99"] for r in er), 1),
            "kv_frac_ge95_busiest": round(max(r["kv_frac_ge95"] for r in er), 1),
            "queue_p90_fleet": round(float(np.mean([r["queue_p90"] for r in er])), 1),
            "queue_p90_busiest": round(max(r["queue_p90"] for r in er), 1),
            "batch_p90_fleet": round(float(np.mean([r["batch_p90"] for r in er])), 1),
            "batch_p90_busiest": round(max(r["batch_p90"] for r in er), 1),
            "busiest_engine": busiest["engine"],
            "itl_mean_ms_fleet": round(float(np.mean(
                [r["itl_mean_ms"] for r in er if r["itl_mean_ms"]])), 2),
            "itl_mean_ms_busiest": busiest["itl_mean_ms"],
            "itl_iv_p90_ms_busiest": busiest["itl_iv_p90_ms"],
        }
        rr.update(cs)
        if rr.get("admitted"):
            rr["preempt_per_1k_admitted"] = (
                round(1000.0 * rr["preempt_total"] / rr["admitted"], 2)
                if rr["preempt_total"] is not None else None)
        rrows.append(rr)

    for rows, fn in ((erows, "tail2026_engine_rows.csv"),
                     (rrows, "tail2026_run_rows.csv")):
        keys = sorted({k for r in rows for k in r})
        with open(os.path.join(a.out_dir, fn), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {os.path.join(a.out_dir, fn)}  ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
