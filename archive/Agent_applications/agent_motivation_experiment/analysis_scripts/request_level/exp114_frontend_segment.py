#!/usr/bin/env python3
"""EXP-114: split a request's time-to-first-token into the two segments the
engine can see and the one it cannot, per engine.

WHY THIS EXISTS. vLLM's three per-request timers do not share an origin:

    vllm:time_to_first_token   iteration_timestamp - arrival_time  (API server clock)
    vllm:request_queue_time    scheduled_ts - queued_ts            (engine-core events)
    vllm:request_prefill_time  first_token_ts - scheduled_ts       (engine-core events)

so TTFT - (queue + prefill) is, by construction, the time between the API server
receiving a request and the engine core queueing it, plus whatever the front end
takes to hand the first token back. On the eight-instance Llama-3.1-8B fleet with
one API server per engine that residual was tens of seconds on the engines
FluidServe concentrates chat onto, while the engine core's own two segments were
milliseconds -- one Python process cannot both stream a thousand responses and
admit new requests. --api-server-count is the only knob for that segment.

WINDOW. The collector scrapes from the moment the runner starts until it ends, so
an eight-minute condition's scrape window contains a long idle stretch at each
end. Reading these series over the whole window has already produced three
retracted mechanism stories in this experiment (CLAUDE.md trap E). These are
counters, so the honest reading is a DELTA over the load window: the mean over
exactly the requests that the window contains, and nothing before or after.

    python3 exp114_frontend_segment.py <run-dir> [<run-dir> ...]
"""
import glob
import json
import os
import sys

import pandas as pd

SERIES = {
    "ttft":    "vllm:time_to_first_token_seconds",
    "queue":   "vllm:request_queue_time_seconds",
    "prefill": "vllm:request_prefill_time_seconds",
}


def load_window(run):
    """First and last arrival of the measured load, warmup excluded."""
    df = pd.read_csv(os.path.join(run, "metrics.csv"), low_memory=False)
    df = df[df["agent"] != "job_summary"]
    cfg = json.load(open(os.path.join(run, "run_config.json")))
    t0 = df["start_time"].min() + float(cfg.get("warmup_sec", 0) or 0)
    return t0, df["start_time"].max()


def pick(rec, base, suffix):
    """The metric name carries labels, so match on the prefix."""
    want = base + "_" + suffix + "|"
    for k, v in rec.items():
        if k.startswith(want):
            return v
    return None


# A single request's prefill cannot take five minutes on this hardware, and the
# engine reports values that say it did: on the eight-instance fleet
# vllm:request_prefill_time_seconds_sum reaches 1.2e8 SECONDS (3.9 years) on one
# engine while its count is 18,875, i.e. 6,460 s per request. The neighbouring
# time-to-first-token series on the same engine is sane (17.3 s mean), so this is
# a defect in that one series, not in the scrape or in this arithmetic.
#
# Averaging over the whole window folds those samples in and silently produces a
# residual of the wrong SIGN, which is how a mechanism story gets built on a
# broken counter. So the mean is taken over per-scrape increments, and increments
# whose implied per-request mean is non-physical are DROPPED AND COUNTED -- never
# dropped quietly, because how much was dropped is what says whether the
# remaining number can be read at all.
MAX_PHYSICAL_S = 300.0


def engine_row(path, t0, t1):
    seq = []
    for line in open(path):
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        t = rec.get("t")
        if t is None or not (t0 <= t <= t1):
            continue
        vals = {}
        for name, base in SERIES.items():
            s, c = pick(rec, base, "sum"), pick(rec, base, "count")
            if s is None or c is None:
                return None
            vals[name] = (s, c)
        seq.append(vals)
    if len(seq) < 2:
        return None

    out = {}
    for name in SERIES:
        kept_s = kept_c = 0.0
        drop_c = 0.0
        for a, b in zip(seq, seq[1:]):
            ds = b[name][0] - a[name][0]
            dc = b[name][1] - a[name][1]
            if dc <= 0:
                continue              # no request finished this segment; nothing to average
            if ds < 0 or ds / dc > MAX_PHYSICAL_S:
                drop_c += dc          # counter reset, or the defect described above
                continue
            kept_s += ds
            kept_c += dc
        out[name + "_n"] = kept_c
        out[name + "_drop"] = drop_c
        # A mean over zero requests is undefined, not zero: a printed 0 reads as
        # "fast", which is the opposite of what an all-dropped series means.
        out[name + "_ms"] = (kept_s / kept_c * 1000.0) if kept_c > 0 else float("nan")
    out["frontend_ms"] = out["ttft_ms"] - out["queue_ms"] - out["prefill_ms"]
    return out


def main(runs):
    rows = []
    for run in runs:
        t0, t1 = load_window(run)
        for path in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
            port = os.path.basename(path).split("_")[1].split(".")[0]
            r = engine_row(path, t0, t1)
            if r is None:
                continue
            r["run"] = os.path.basename(run)[:46]
            r["port"] = port
            rows.append(r)
    if not rows:
        sys.exit("no engine series in the load window")
    df = pd.DataFrame(rows)
    df["dropped_pct"] = 100.0 * df["prefill_drop"] / (df["prefill_drop"] + df["prefill_n"]).replace(0, float("nan"))
    cols = ["run", "port", "ttft_n", "ttft_ms", "queue_ms", "prefill_ms",
            "frontend_ms", "dropped_pct"]
    pd.set_option("display.width", 200)
    print("\nper engine, mean over the load window (counter deltas)\n")
    print(df[cols].to_string(index=False, float_format=lambda v: f"{v:,.1f}"))
    print("\nper run: the busiest engine is what a class that is concentrated sees\n")
    g = df.groupby("run").agg(engines=("port", "count"),
                              ttft_mean_ms=("ttft_ms", "mean"),
                              ttft_max_ms=("ttft_ms", "max"),
                              frontend_max_ms=("frontend_ms", "max"),
                              queue_max_ms=("queue_ms", "max"),
                              prefill_max_ms=("prefill_ms", "max"))
    print(g.to_string(float_format=lambda v: f"{v:,.1f}"))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    main(sys.argv[1:])
