#!/usr/bin/env python3
"""Measure per-step prefill cost directly, and rewrite ttft.json from it.

Why this exists
---------------
The first ttft.json was derived from EXP-16 step dumps, and that source can only
pin down ONE operating point. `interval_ms` there is the enter-to-enter gap
between schedule() calls, and under async scheduling the scheduler runs a step
ahead of the model, so the gap is bimodal: either it ran ahead (~0.5 ms) or it
blocked on the real forward. Medians recover the truth only inside a long
homogeneous regime, and for prefill the only such regime is a full chunk,
because chunked prefill always fills the token budget and partial chunks happen
only on a request's last fragment.

So instead of inferring, measure: send a single request of a known prompt length
to an otherwise idle engine and time the first streamed token. With nothing else
running, that IS one prefill step for prompts up to the engine's
max_num_batched_tokens. This is how upstream built its own tables (42 prompt
lengths, 3 reps each).

Two things this controls for that a naive sweep gets wrong:

  prefix cache  Repeating a prompt makes every rep after the first a cache hit
                and reports a few milliseconds. Each rep therefore uses freshly
                drawn random token ids, which share no prefix.
  warm-up       The first requests after a restart pay CUDA graph capture and
                allocator growth. A discarded warm-up round precedes the sweep.

Talk to ONE engine directly, not the gateway: routing, scheduling and the
llumlet hop would all land inside the measurement.

Usage
-----
    kubectl -n llumnix port-forward pod/neutral-0 18000:8000 &
    python3 ms_dev/scripts/measure_ttft_sweep.py \
        --url http://127.0.0.1:18000 \
        --model meta-llama/Meta-Llama-3.1-70B-Instruct \
        --out deploy/profiling/llama31-70b-b200-tp2/ttft.json
"""

import argparse
import json
import os
import random
import statistics
import sys
import time
import urllib.request

# Prompt lengths to time. Dense below 1k because that is where the memory-bound
# floor gives way to compute, and up to the engine's 8192-token budget because
# beyond it vLLM splits the prompt and the measurement stops being one step.
DEFAULT_LENGTHS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 768, 1024, 1536,
                   2048, 3072, 4096, 5120, 6144, 7168, 8192]
# Measured but NOT written to the table: these span more than one chunk, so they
# check that the fitted per-step curve predicts a multi-chunk prefill correctly.
VALIDATION_LENGTHS = [12288, 16384, 24576]

# Llama-3.1 reserves ids from 128000 up for special tokens; stay well below.
TOKEN_LO, TOKEN_HI = 10, 120000


def stream_ttft(url, model, token_ids, timeout):
    """Time to the first streamed token, in milliseconds."""
    body = json.dumps({
        "model": model,
        "prompt": token_ids,
        "max_tokens": 1,
        "temperature": 0,
        "stream": True,
    }).encode()
    req = urllib.request.Request(
        f"{url}/v1/completions", data=body,
        headers={"Content-Type": "application/json"})
    start = time.perf_counter()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode(errors="replace").strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            # First data frame carrying a choice is the first token.
            try:
                if json.loads(payload).get("choices"):
                    return (time.perf_counter() - start) * 1000.0
            except ValueError:
                continue
    return None


def measure(url, model, lengths, reps, timeout, rng, label):
    results = {}
    for n in lengths:
        samples = []
        for _ in range(reps):
            # Fresh random ids per rep so no two requests share a prefix.
            tokens = [rng.randint(TOKEN_LO, TOKEN_HI) for _ in range(n)]
            try:
                ms = stream_ttft(url, model, tokens, timeout)
            except Exception as e:                      # keep the sweep going
                print(f"  {label} {n:>6} tok: request failed: {e}")
                continue
            if ms is not None:
                samples.append(ms)
        if not samples:
            print(f"  {label} {n:>6} tok: no samples")
            continue
        results[n] = samples
        print(f"  {label} {n:>6} tok: p50={statistics.median(samples):8.1f} ms "
              f"min={min(samples):8.1f} max={max(samples):8.1f} n={len(samples)}")
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--reps", type=int, default=5)
    ap.add_argument("--timeout", type=float, default=180.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-num-batched-tokens", type=int, default=8192)
    ap.add_argument("--timestamp", required=True)
    ap.add_argument("--hardware", default="B200 x2 (TP2)")
    ap.add_argument("--skip-validation", action="store_true")
    a = ap.parse_args()
    rng = random.Random(a.seed)

    print("[warm-up] discarding a round so CUDA graph capture is not measured")
    measure(a.url, a.model, [16, 1024, 8192], 1, a.timeout, rng, "warm")

    print("\n[sweep] single request, idle engine, fresh random tokens per rep")
    results = measure(a.url, a.model, DEFAULT_LENGTHS, a.reps, a.timeout, rng, "step")
    if not results:
        sys.exit("no measurements collected")

    validation = {}
    if not a.skip_validation:
        print("\n[validation] prompts spanning several chunks (not written to the table)")
        validation = measure(a.url, a.model, VALIDATION_LENGTHS, max(2, a.reps // 2),
                             a.timeout, rng, "multi")

    # A single request's TTFT is one prefill step plus a fixed HTTP/tokenizer
    # overhead. Both are what Llumnix will effectively see per chunk, and
    # upstream's tables include the same overhead, so the value is written as
    # measured rather than trying to subtract an unobservable constant.
    doc = {
        "metadata": {
            "model": a.model,
            "timestamp": a.timestamp,
            "description": (
                "Per-STEP prefill cost vs chunk size, measured directly: one "
                "request at a time against an idle engine, timing the first "
                "streamed token. Fresh random token ids per repetition so the "
                "prefix cache cannot serve a repeat. Prompts up to "
                f"{a.max_num_batched_tokens} tokens are a single scheduler step, "
                "which is what predictTtftLatencyByChunkPrefill sums over. "
                "Supersedes the EXP-16 step-dump derivation, which could only "
                "pin down the full-chunk point."),
            "hardware": a.hardware,
            "method": "idle-engine prompt-length sweep",
            "reps_per_length": a.reps,
            "engine_max_num_batched_tokens": a.max_num_batched_tokens,
            "validation_multi_chunk_ms": {
                str(n): statistics.median(s) for n, s in validation.items()},
        },
        "results": [
            {
                "tokens_num": n,
                "mean": statistics.fmean(s),
                "std": statistics.pstdev(s) if len(s) > 1 else 0.0,
                "min": min(s),
                "max": max(s),
                "p50": statistics.median(s),
                "p95": sorted(s)[min(len(s) - 1, int(len(s) * 0.95))],
                "p99": sorted(s)[min(len(s) - 1, int(len(s) * 0.99))],
                "n": len(s),
                "source": "measured",
                "raw_data": [round(v, 3) for v in s],
            }
            for n, s in sorted(results.items())
        ],
    }

    # The Go interpolator brackets a query between the nearest axis values and
    # then needs both endpoints present, so the axis must reach past anything it
    # will be asked about. Chunks never exceed the engine budget, but Llumnix's
    # own --max-num-batched-tokens defaults to 65536, and a metadata mismatch
    # must not fall off the end. Extend linearly from the two largest measured
    # points, flagged as extrapolated.
    tail = doc["results"][-2:]
    if len(tail) == 2:
        (x0, y0), (x1, y1) = ((t["tokens_num"], t["p50"]) for t in tail)
        slope = (y1 - y0) / (x1 - x0) if x1 != x0 else 0.0
        for n in (12288, 16384, 24576, 32768, 49152, 65536):
            if n <= x1:
                continue
            value = y1 + slope * (n - x1)
            doc["results"].append({
                "tokens_num": n, "mean": value, "std": 0.0, "min": value,
                "max": value, "p50": value, "p95": value, "p99": value,
                "n": 0, "source": "extrapolated", "raw_data": [],
            })

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"\nwrote {a.out}  ({len(doc['results'])} points, "
          f"{sum(1 for r in doc['results'] if r['source'] == 'measured')} measured)")

    if validation:
        print("\n[validation] measured vs summing the per-step table in chunks")
        table = {r["tokens_num"]: r["p50"] for r in doc["results"]}
        axis = sorted(table)
        for n, samples in sorted(validation.items()):
            budget = a.max_num_batched_tokens
            predicted, remaining = 0.0, n
            while remaining > 0:
                chunk = min(remaining, budget)
                remaining -= chunk
                lo = max([x for x in axis if x <= chunk], default=axis[0])
                hi = min([x for x in axis if x >= chunk], default=axis[-1])
                if hi == lo:
                    predicted += table[lo]
                else:
                    w = (chunk - lo) / (hi - lo)
                    predicted += (1 - w) * table[lo] + w * table[hi]
            actual = statistics.median(samples)
            print(f"  {n:>6} tok: measured {actual:8.1f} ms, "
                  f"chunk-sum {predicted:8.1f} ms, ratio {actual/predicted:.2f}")


if __name__ == "__main__":
    main()
