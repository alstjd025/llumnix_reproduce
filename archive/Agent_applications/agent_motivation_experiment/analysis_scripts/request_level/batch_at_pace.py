#!/usr/bin/env python3
"""What batch size and KV occupancy does an engine run at when it delivers a
given per-token pace.

The step-time law this fleet was fitted with has TWO state terms,

    t = c0 + c_kv * KV + c_n * N          c0 = 16.3612 ms
                                          c_kv = 1.282e-5 ms/token
                                          c_n  = 0.0764286 ms/request

so "the batch size at 50 ms" is not a number: it is a line in (KV, N). This
reports the JOINT distribution actually observed, and prints the batch the law
would allow at the measured KV so that the two can be compared.

Three things the numbers depend on, all handled here.

1. The pace is a RATIO OF COUNTER INCREMENTS, not a gauge.
   vllm:inter_token_latency_seconds_{sum,count} are cumulative; the pace over a
   scrape interval is d(sum)/d(count), which weights by the tokens that engine
   actually produced in that interval. A ratio of the totals would weight the
   whole run by its busiest stretch, and a gauge read at one instant would not
   exist at all.

2. Intervals where the engine produced almost nothing are dropped.
   With d(count) below `--min-tokens` the ratio is a few samples wide and swings
   by tens of milliseconds; those intervals carry no information about the pace
   at load and they are where the implausible values live.

3. The batch and KV are gauges read at the END of the interval, so they are
   paired with the pace measured ACROSS it. That is a half-interval offset and
   it is stated rather than corrected -- the scrape period is about 1 s and the
   quantities move slowly against the run.

Usage
-----
  python3 batch_at_pace.py --runs 'results/*exp109r*_fsv3*_shift' --pace 50 --band 5
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

C0, C_KV, C_N = 16.3611972, 1.282e-5, 0.07642859

ITL_S = "vllm:inter_token_latency_seconds_sum"
ITL_C = "vllm:inter_token_latency_seconds_count"
RUN = "vllm:num_requests_running"
KVP = "vllm:kv_cache_usage_perc"
WAIT = "vllm:num_requests_waiting"


def series(row, name):
    for k, v in row.items():
        if k.split("|", 1)[0] == name and isinstance(v, (int, float)):
            return float(v)
    return None


def read_engine(path, min_tokens):
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    out = []
    prev = None
    for r in rows:
        s, c = series(r, ITL_S), series(r, ITL_C)
        if s is None or c is None:
            continue
        if prev is not None:
            ds, dc = s - prev[0], c - prev[1]
            if dc >= min_tokens and ds > 0:
                out.append(dict(pace_ms=1000.0 * ds / dc,
                                batch=series(r, RUN), kv=series(r, KVP),
                                waiting=series(r, WAIT)))
        prev = (s, c)
    return [o for o in out if o["batch"] is not None and o["kv"] is not None]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--pace", type=float, default=50.0)
    ap.add_argument("--band", type=float, default=5.0,
                    help="keep intervals whose pace is within +/- this of --pace")
    ap.add_argument("--min-tokens", type=float, default=200.0)
    ap.add_argument("--kv-tokens", type=float, default=None,
                    help="physical KV pool in tokens, to turn kv_cache_usage_perc "
                         "into the token count the law takes")
    a = ap.parse_args()

    dirs = sorted(d for d in glob.glob(a.runs) if os.path.isdir(d) and "PRERUN" not in d)
    if not dirs:
        sys.exit(f"no runs match {a.runs}")

    allrows = []
    for d in dirs:
        for ep in sorted(glob.glob(os.path.join(d, "server_metrics", "engine_*.jsonl"))):
            rs = read_engine(ep, a.min_tokens)
            port = os.path.basename(ep).split("_")[1].split(".")[0]
            for r in rs:
                r["run"], r["port"] = os.path.basename(d), port
            allrows += rs
    if not allrows:
        sys.exit("no usable intervals")

    pace = np.array([r["pace_ms"] for r in allrows])
    batch = np.array([r["batch"] for r in allrows])
    kv = np.array([r["kv"] for r in allrows])
    print(f"{len(allrows):,} scrape intervals over {len(dirs)} run(s), "
          f"d(count) >= {a.min_tokens:.0f} tokens\n")
    print("  all intervals            p10     p50     p90")
    for nm, v in (("pace (ms/token)", pace), ("batch (requests)", batch),
                  ("KV occupancy (%)", kv * 100.0)):
        print(f"  {nm:22s} {np.percentile(v,10):7.1f} {np.percentile(v,50):7.1f} "
              f"{np.percentile(v,90):7.1f}")

    m = np.abs(pace - a.pace) <= a.band
    print(f"\n  intervals delivering {a.pace:.0f} +/- {a.band:.0f} ms: "
          f"{m.sum():,} of {len(m):,} ({100.0*m.mean():.1f}%)")
    if m.sum() == 0:
        return 0
    print("                            p10     p50     p90")
    for nm, v in (("batch (requests)", batch[m]), ("KV occupancy (%)", kv[m] * 100.0)):
        print(f"  {nm:22s} {np.percentile(v,10):7.1f} {np.percentile(v,50):7.1f} "
              f"{np.percentile(v,90):7.1f}")

    if a.kv_tokens:
        kvtok = np.percentile(kv[m], 50) * a.kv_tokens
        allowed = (a.pace - C0 - C_KV * kvtok) / C_N
        print(f"\n  the fitted law at the median KV of this band "
              f"({kvtok:,.0f} tokens = {100*np.percentile(kv[m],50):.1f}% of "
              f"{a.kv_tokens:,.0f}):")
        print(f"    t = {C0:.2f} + {C_KV:.3e}*KV + {C_N:.5f}*N = {a.pace:.0f} ms "
              f"-> N = {allowed:,.0f} requests")
        print(f"    measured batch in the same band: p50 {np.percentile(batch[m],50):,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
