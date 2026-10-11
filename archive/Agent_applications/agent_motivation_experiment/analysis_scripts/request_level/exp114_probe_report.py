#!/usr/bin/env python3
"""Did the load generator actually offer what the rate asked for, and did the
measurement see every engine?

Three questions, in the order in which a wrong answer invalidates the next one:

  1. Are all the engine series present? A fleet of eight scraped as four keeps
     every request-level number correct and silently drops half the engine
     layer, so this is asked before anything is read out of that layer.

  2. Did the client keep up? An arm with no admission control cannot reject, so
     when the fleet stops absorbing, the backlog appears in the CLIENT: attempts
     per second fall below the trace's arrival rate, or connection-level errors
     appear. Above that point the run measures the load generator. This is the
     shape of EXP-54, where a saturated arm exhausted ephemeral ports and its
     "170 req/s" was the rate at which the client was failing and retrying.

  3. What did the fleet do? Queue depth and KV occupancy, reported as p90 rather
     than mean: both are quantities with a ceiling, and a mean over a run that
     spends a third of its time at the ceiling reads as comfortable.

Usage: exp114_probe_report.py <run-dir>
"""
import collections
import glob
import json
import os
import sys

import numpy as np
import pandas as pd


def engine_files(run):
    return sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl")))


def gauge(path, key):
    """Every value of one gauge in one engine file, in tick order."""
    out = []
    for line in open(path):
        try:
            rec = json.loads(line)
        except Exception:
            continue
        vals = [v for k, v in rec.items() if key in k and isinstance(v, (int, float))]
        if vals:
            out.append(max(vals))
    return np.asarray(out, dtype=float)


def main(run):
    print(f"== {os.path.basename(run)}")

    # ---- 1. every engine present -------------------------------------------
    files = engine_files(run)
    ports = [int(os.path.basename(f)[len("engine_"):-len(".jsonl")]) for f in files]
    print(f"  engines in server_metrics: {len(ports)} -> {ports}")

    # ---- 2. did the client keep up -----------------------------------------
    # agent != "job_summary" is the arrival denominator: "agent == request" drops
    # the grace_cut rows, which are separate arrivals rather than duplicates, and
    # up to a third of arrivals in some arms.
    m = os.path.join(run, "metrics.csv")
    if not os.path.isfile(m):
        print("  metrics.csv missing"); return
    df = pd.read_csv(m, low_memory=False)
    if "agent" in df.columns:
        df = df[df["agent"] != "job_summary"]
    t = pd.to_numeric(df.get("start_time"), errors="coerce").dropna().sort_values()
    asked, warm_s, dur_s = None, 0.0, None
    cfg = os.path.join(run, "run_config.json")
    if os.path.isfile(cfg):
        try:
            c = json.load(open(cfg))
            if "request_rate_per_min" in c:
                asked = float(c["request_rate_per_min"]) / 60.0
            warm_s = float(c.get("warmup_sec") or 0.0)
            if c.get("duration_min"):
                dur_s = float(c["duration_min"]) * 60.0
        except Exception:
            pass
    if asked is None:
        base = os.path.basename(run)
        if "_rpm_" in base:
            try:
                asked = float(base.rsplit("_rpm_", 1)[1].split("_")[0]) / 60.0
            except Exception:
                pass
    # The warmup runs at a different rate (60 rpm by default) for warmup_sec
    # before the measured window opens, and counting it makes a client that kept
    # up perfectly read as one that offered two thirds of the rate -- which is
    # what this script said the first time it ran. The measured window starts
    # warmup_sec after the first arrival.
    if len(t) < 2:
        # A run whose metrics.csv is still a header is a run that has not merged
        # its worker shards yet -- either still going, or dead before the merge.
        # shards/ still holding files says which.
        nsh = len(glob.glob(os.path.join(run, "shards", "*")))
        print(f"  no arrivals recorded yet ({len(t)} rows); "
              f"shards/ holds {nsh} files -- the run has not merged")
        return
    t0 = float(t.iloc[0]) + warm_s
    tm = t[t >= t0]
    span = float(tm.iloc[-1] - tm.iloc[0]) if len(tm) > 1 else 0.0
    got = len(tm) / span if span > 0 else float("nan")
    ratio = (got / asked) if asked else float("nan")
    print(f"  arrivals: {len(df):,} total, {len(tm):,} in the measured window "
          f"({span:,.0f}s after {warm_s:.0f}s warmup"
          + (f", asked for {dur_s:.0f}s" if dur_s else "") + ")")
    print(f"  offered:  asked {asked if asked else float('nan'):.1f}/s   "
          f"realised {got:.1f}/s   ratio {ratio:.3f}")
    if asked and ratio < 0.95:
        print(f"  ** the client did not keep up: it offered {100*ratio:.1f}% of the "
              f"rate. Above this point the run measures the load generator.")

    # error kinds, connection-level separated -- a rejection is an error row too,
    # so counting "errors" without splitting them conflates admission with failure.
    dfm = df[pd.to_numeric(df["start_time"], errors="coerce") >= t0]
    if "is_error" in dfm.columns:
        err = dfm[dfm["is_error"].astype(str).isin(("True", "true", "1"))]
        conn_keys = ("connection", "cannot assign requested address", "max retries",
                     "connectionpool", "timed out", "remote end closed")
        kinds = collections.Counter()
        for msg in err.get("error_msg", pd.Series(dtype=str)).fillna("").astype(str):
            low = msg.lower()
            hit = next((k for k in conn_keys if k in low), None)
            kinds[f"CONNECTION: {hit}" if hit else msg[:56]] += 1
        rej = 0
        if "is_rejected" in dfm.columns:
            rej = int(dfm["is_rejected"].astype(str).isin(("True", "true", "1")).sum())
        # Cut requests are NOT client failures and they are not errors on the
        # request row: is_error lives on the job_summary row, which is why this
        # script first read a condition with 1,027 cut requests as "1 error".
        # They are the requests still streaming when the load window closed, so
        # their count tracks the fleet's concurrency (830 in flight at 120 req/s)
        # rather than anything going wrong. They matter here because at high
        # rates they grow, and because their output_tokens is a lower bound
        # rather than a length.
        cut = 0
        if "is_server_terminated" in dfm.columns:
            cut = int(dfm["is_server_terminated"].astype(str)
                      .isin(("True", "true", "1")).sum())
        print(f"  rejected {rej:,} ({100.0*rej/max(len(dfm),1):.1f}%)   "
              f"cut at window close {cut:,} ({100.0*cut/max(len(dfm),1):.1f}%)   "
              f"error rows {len(err):,} ({100.0*len(err)/max(len(dfm),1):.1f}%)")
        for k, n in kinds.most_common(5):
            print(f"    {n:>7,}  {k}")

    # ---- 3. what the fleet did ---------------------------------------------
    if files:
        print(f"  {'port':>6} {'batch p90':>10} {'queue p90':>10} {'KV% p50':>8} "
              f"{'KV% p90':>8} {'preempt':>8}")
        for f, p in zip(files, ports):
            run_g = gauge(f, "vllm:num_requests_running")
            wait_g = gauge(f, "vllm:num_requests_waiting")
            kv = gauge(f, "vllm:kv_cache_usage_perc")
            pre = gauge(f, "vllm:num_preemptions_total")
            sc = 100.0 if (len(kv) and np.nanmax(kv) <= 1.5) else 1.0
            q = lambda a, x: (np.nanpercentile(a, x) if len(a) else float("nan"))
            npre = (np.nanmax(pre) - np.nanmin(pre)) if len(pre) else float("nan")
            print(f"  {p:>6} {q(run_g,90):>10.0f} {q(wait_g,90):>10.0f} "
                  f"{q(kv,50)*sc:>8.1f} {q(kv,90)*sc:>8.1f} {npre:>8.0f}")

        # Fleet throughput, from the engines' own counters. Whether the fleet is
        # saturated is not answerable from the client alone: a client that falls
        # behind and a fleet that has stopped absorbing look the same in the
        # arrival stream, and only the engine counters separate them.
        def counter_rate(path, key):
            ts, vs = [], []
            for line in open(path):
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                v = [x for k, x in rec.items() if key in k and isinstance(x, (int, float))]
                if v and rec.get("t") is not None:
                    ts.append(rec["t"]); vs.append(max(v))
            if len(ts) < 2:
                return float("nan")
            return (vs[-1] - vs[0]) / max(ts[-1] - ts[0], 1e-9)

        gen = sum(counter_rate(f, "vllm:generation_tokens_total") for f in files)
        pro = sum(counter_rate(f, "vllm:prompt_tokens_total") for f in files)
        print(f"  fleet: {gen:,.0f} generated tok/s, {pro:,.0f} prompt tok/s "
              f"over {len(files)} engines")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
