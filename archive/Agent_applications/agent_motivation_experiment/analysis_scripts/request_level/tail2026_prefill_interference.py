#!/usr/bin/env python3
"""Per engine: how often a step carried a prefill chunk, and how uneven the
token gaps of the requests on that engine were.

This is the discriminating measurement between the two mechanisms that can raise
the within-request p90/p50 ratio of inter-token gaps.

  (a) recompute preemption   The engine frees a running request's key-value
                             blocks and zeroes its computed prefix, so the
                             request re-prefills its whole prompt. That is one
                             gap of several hundred median-gaps, and it needs
                             `vllm:num_preemptions_total` to move.
  (b) prefill interference   vLLM V1 runs chunked prefill, so a prefill chunk is
                             co-scheduled INTO the same engine step as everyone
                             else's decode. That step processes up to
                             `max_num_batched_tokens` tokens instead of one per
                             running sequence, so it is longer for every request
                             decoding in it. It damages many gaps moderately and
                             requires no preemption at all.

The prediction (b) makes is quantitative and is what this script checks: the
share of a request's gaps that are noticeably longer than its own median should
be close to the share of that engine's steps that carried a prefill chunk.

HOW THE PREFILL-STEP SHARE IS ESTIMATED, AND WHAT IS ASSUMED. The engines expose
no step counter, so both terms are derived from token counters over the same
interval:

  decode steps   `vllm:generation_tokens_total` divided by the mean running
                 batch. Every running sequence emits exactly one token per step,
                 so tokens generated over a window divided by the mean number of
                 running sequences is the number of steps in that window. This
                 assumes the batch gauge, sampled about once a second, is a fair
                 average of the batch the steps actually ran at.
  prefill steps  the prompt tokens the engine actually had to COMPUTE, divided
                 by the batched-token budget of 8,192 (the value in
                 `deploy/profiling/llama31-70b-b200-tp2/ttft.json`). Computed
                 tokens are `vllm:prompt_tokens_total` times one minus the
                 engine's own prefix hit rate, because the total counter
                 includes tokens served out of the prefix cache and those are not
                 compute. This assumes a prefill chunk fills its budget, which
                 makes the estimate a LOWER bound on the number of prefill steps:
                 a chunk that only partly fills the budget still occupies a step.

Both are estimates from counters, not measured step counts, so the comparison
against the observed share of long gaps is an order-of-magnitude check, not a
fit. It is stated that way in the output.

Requires `analysis/request_engine.csv`, built by `build_request_engine_map.py`
for the Llumnix-scheduled arms and by `llmd_engine_map.py` for llm-d. The
fraction of client requests that carry an engine is printed, because the
scheduler log drops lines under load and a per-engine number drawn on 86% of the
requests is a different object from one drawn on all of them.

    python3 tail2026_prefill_interference.py --run results/<dir> [--run ...]
"""
import argparse
import collections
import csv
import glob
import json
import os
import sys

import numpy as np

CHUNK = 8192.0
PRE = "vllm:num_preemptions_total"


def klass(tid):
    p = str(tid).split("-")[0]
    return {"sg": "chat", "sa": "deepresearch"}.get(p, "swe")


def engine_map(run):
    """(task_id, call_index) -> engine port, from the join built beforehand."""
    p = os.path.join(run, "analysis", "request_engine.csv")
    if not os.path.exists(p):
        return {}
    out = {}
    with open(p) as fh:
        for row in csv.DictReader(l for l in fh if not l.startswith("#")):
            port = row.get("engine_port")
            if not port:
                continue
            out[(row["task_id"], str(row.get("call_index", "")))] = int(port)
    return out


def engine_counters(run):
    """Per-engine run deltas and gauge summaries, plus the derived step counts."""
    out = {}
    for path in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        port = int(os.path.basename(path).replace("engine_", "").replace(".jsonl", ""))
        first, last, batch, kv = {}, {}, [], []
        t0 = t1 = None
        for line in open(path):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not d.get("ok"):
                continue
            t1 = float(d["t"])
            if t0 is None:
                t0 = t1
            for k, v in d.items():
                if not isinstance(v, (int, float)):
                    continue
                base = k.split("|")[0]
                if base.endswith("_total") or base.endswith("_sum") or base.endswith("_count"):
                    if base not in first:
                        first[base] = v
                    last[base] = v
                if base == "vllm:num_requests_running":
                    batch.append(v)
                if base == "vllm:kv_cache_usage_perc":
                    kv.append(v)

        def dl(n):
            return last.get(n, 0.0) - first.get(n, 0.0)

        gen = dl("vllm:generation_tokens_total")
        prm = dl("vllm:prompt_tokens_total")
        hit = dl("vllm:prefix_cache_hits_total")
        qry = dl("vllm:prefix_cache_queries_total")
        b = float(np.mean([x for x in batch if x > 0])) if any(x > 0 for x in batch) else 0.0
        hit_rate = hit / qry if qry else 0.0
        computed = prm * (1.0 - hit_rate)
        dec_steps = gen / b if b > 0 else 0.0
        pf_steps = computed / CHUNK
        isum, icnt = dl("vllm:inter_token_latency_seconds_sum"), dl("vllm:inter_token_latency_seconds_count")
        out[port] = {
            "span_s": (t1 - t0) if t0 else 0.0,
            "preemptions": round(dl(PRE)),
            "batch_mean": round(b, 1),
            "batch_p90": round(float(np.percentile(batch, 90)), 1) if batch else 0.0,
            "kv_p50": round(100 * float(np.percentile(kv, 50)), 1) if kv else 0.0,
            "kv_p90": round(100 * float(np.percentile(kv, 90)), 1) if kv else 0.0,
            "kv_p99": round(100 * float(np.percentile(kv, 99)), 1) if kv else 0.0,
            "gen_tokens": round(gen),
            "prompt_tokens": round(prm),
            "prefix_hit_pct": round(100 * hit_rate, 1),
            "computed_prefill_tokens": round(computed),
            "est_decode_steps": round(dec_steps),
            "est_prefill_steps": round(pf_steps),
            "est_prefill_step_share_pct": round(100 * pf_steps / dec_steps, 1) if dec_steps else None,
            "engine_itl_mean_ms": round(1000 * isum / icnt, 2) if icnt else None,
        }
    return out


def gap_by_engine(run, emap, want_class, min_tokens):
    """Per-request gap-shape counters, grouped by the engine that served it."""
    per = collections.defaultdict(list)
    unmapped = 0
    total = 0
    p = os.path.join(run, "tbt_events.jsonl")
    if not os.path.exists(p):
        return per, 0, 0
    for line in open(p):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("agent") != "request":
            continue
        if want_class and klass(d.get("task_id")) != want_class:
            continue
        gaps = [e["inter_arrival_ms"] for e in (d.get("chunk_events") or [])
                if e.get("inter_arrival_ms") is not None]
        if len(gaps) < min_tokens:
            continue
        total += 1
        port = emap.get((str(d.get("task_id")), str(d.get("call_index"))))
        if port is None:
            unmapped += 1
            continue
        g = np.asarray(gaps, dtype=float)
        med = float(np.median(g))
        if med <= 0:
            continue
        per[port].append({
            "med": med, "p90": float(np.percentile(g, 90)), "max": float(g.max()),
            "ratio": float(np.percentile(g, 90)) / med,
            "sh2": float((g > 2 * med).mean()), "sh5": float((g > 5 * med).mean()),
            "sh20": float((g > 20 * med).mean()),
            "t2": float(g[g > 2 * med].sum()) / float(g.sum()),
        })
    return per, unmapped, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True)
    ap.add_argument("--class", dest="klass", default="chat")
    ap.add_argument("--min-tokens", type=int, default=100)
    a = ap.parse_args()

    for run in a.run:
        emap = engine_map(run)
        ec = engine_counters(run)
        per, unmapped, total = gap_by_engine(run, emap, a.klass, a.min_tokens)
        print(f"\n=== {os.path.basename(run)} ===  class={a.klass}, "
              f"requests with >={a.min_tokens} gaps: {total:,}; "
              f"attributed to an engine: {total - unmapped:,} "
              f"({100.0 * (total - unmapped) / total:.1f}%)" if total else
              f"\n=== {os.path.basename(run)} === no requests")
        if not total:
            continue
        print(f"  {'port':>5}{'reqs':>7}{'batch':>7}{'KVp50':>7}{'KVp90':>7}{'preem':>7}"
              f"{'pfxhit':>8}{'pf-step%':>10}{'engITL':>8}"
              f"{'medgap':>8}{'ratio':>7}{'gaps>2x':>9}{'gaps>5x':>9}{'gaps>20x':>10}{'t>2x':>7}")
        for port in sorted(ec):
            e = ec[port]
            rs = per.get(port, [])
            if rs:
                med = lambda k: float(np.median([r[k] for r in rs]))  # noqa: E731
                cells = (f"{med('med'):>8.1f}{med('ratio'):>7.2f}"
                         f"{100 * med('sh2'):>8.1f}%{100 * med('sh5'):>8.1f}%"
                         f"{100 * med('sh20'):>9.1f}%{100 * med('t2'):>6.0f}%")
            else:
                cells = f"{'-':>8}{'-':>7}{'-':>9}{'-':>9}{'-':>10}{'-':>7}"
            print(f"  {port:>5}{len(rs):>7}{e['batch_mean']:>7.0f}{e['kv_p50']:>7.1f}"
                  f"{e['kv_p90']:>7.1f}{e['preemptions']:>7}{e['prefix_hit_pct']:>7.1f}%"
                  f"{(e['est_prefill_step_share_pct'] if e['est_prefill_step_share_pct'] is not None else float('nan')):>10.1f}"
                  f"{(e['engine_itl_mean_ms'] or float('nan')):>8.1f}" + cells)
        print("  pf-step% = estimated share of engine steps that carried a prefill chunk "
              "(computed prompt tokens / 8192, over generation tokens / mean batch); "
              "gaps>Nx = median over requests of the share of that request's gaps "
              "above N times its own median gap; t>2x = share of the request's "
              "streaming time inside gaps above 2x its median.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
