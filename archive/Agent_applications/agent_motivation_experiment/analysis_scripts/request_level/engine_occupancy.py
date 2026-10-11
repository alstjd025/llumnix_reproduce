#!/usr/bin/env python3
"""What each engine was actually doing, from the scraped per-engine series.

This exists because a whole class of failure is invisible in the request-level
numbers. A run can report a plausible attainment and a plausible goodput while
one engine holds a queue of several thousand requests and the rest sit
completely idle: the requests on the idle engines finish fine and are counted,
the ones behind the queue never finish at all and are dropped from the analysis
as "still in flight", and the aggregate looks merely mediocre rather than
broken. Reading `num_requests_running` and `num_requests_waiting` per engine over
time makes it obvious in one table.

Reports, per engine and over time: how many requests were decoding, how many
were queued behind them, and how full the KV pool was. Then three summary
figures that say whether the fleet was used as a fleet:

  peak queue depth   the largest number of requests waiting on any one engine.
                     A healthy run keeps this in the tens; thousands means the
                     router committed work to an engine that could not take it,
                     and since a dispatched request cannot be recalled, that is
                     not recoverable.
  idle-while-queued  seconds in which at least one engine had nothing to decode
                     while another had requests waiting. This is capacity that
                     existed and was not used, and it is the direct measure of a
                     routing failure as opposed to an overload.
  imbalance          the ratio of the busiest to the least busy engine, averaged
                     over the run.
  preemptions        requests the engine evicted because its KV pool was full.
                     vLLM V1 preempts by RECOMPUTE, so each one throws away the
                     prefill already done and pays for it again when the request
                     is rescheduled. It is invisible in every request-level
                     number -- the request still completes, just later -- and it
                     is not small: PolyServe preempted 549 times in one 8-minute
                     condition at 3000 rpm, which at that workload's 7,460
                     recomputed tokens per preemption is about 27% of the two
                     affected engines' time spent redoing work.
  prefix hit rate    the engine's own report of how much of each prompt it
                     served from cache. It reads out the ROUTING: a policy that
                     keeps a class on one engine drives that engine's hit rate up
                     (PolyServe's chat engine reached 94.5% while its agent
                     engines sat at 66.7%), and four engines all reporting the
                     same figure means the classes are mixed.

Usage
-----
  python3 engine_occupancy.py --run results/<run_dir> [--step 30]
  python3 engine_occupancy.py --runs 'results/*exp22*' --summary-only
"""
import argparse
import csv
import glob
import json
import os
import sys

RUNNING = "vllm:num_requests_running"
WAITING = "vllm:num_requests_waiting"
KVUSED = "vllm:kv_cache_usage_perc"
PREEMPT = "vllm:num_preemptions_total"
PFX_HIT = "vllm:prefix_cache_hits_total"
PFX_Q = "vllm:prefix_cache_queries_total"


def pick(rec, prefix):
    """The engine series carry model-name labels, so match on the prefix."""
    for k, v in rec.items():
        if k.startswith(prefix) and isinstance(v, (int, float)):
            return float(v)
    return None


def load_engines(run):
    out = {}
    for path in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        rows = []
        with open(path) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not rec.get("ok"):
                    continue
                rows.append((rec["t"], pick(rec, RUNNING) or 0.0,
                             pick(rec, WAITING) or 0.0, pick(rec, KVUSED) or 0.0,
                             pick(rec, PREEMPT), pick(rec, PFX_HIT),
                             pick(rec, PFX_Q)))
        if rows:
            out[os.path.basename(path).replace("engine_", "").replace(".jsonl", "")] = rows
    return out


def summarise(engines):
    """Peak queue depth, idle-while-queued seconds, and mean imbalance."""
    names = sorted(engines)
    n = min(len(engines[e]) for e in names)
    t0 = engines[names[0]][0][0]
    peak_wait, idle_while_queued, ratios = 0.0, 0, []
    span = 0.0
    for i in range(n):
        run_i = [engines[e][i][1] for e in names]
        wait_i = [engines[e][i][2] for e in names]
        peak_wait = max(peak_wait, max(wait_i))
        # An engine with nothing decoding is free capacity. If any other engine
        # has work queued at the same instant, that capacity was reachable and
        # was not used.
        if min(run_i) == 0 and max(wait_i) > 0:
            idle_while_queued += 1
        if max(run_i) > 0:
            ratios.append(max(run_i) / max(min(run_i), 1.0))
        span = engines[names[0]][i][0] - t0
    # Samples are one per second in this collector; report the count as seconds
    # and the span alongside so the reader can check that assumption.
    return {
        "engines": len(names),
        "samples": n,
        "span_s": round(span, 1),
        "peak_queue_depth": peak_wait,
        "idle_while_queued_samples": idle_while_queued,
        "idle_while_queued_pct": round(100.0 * idle_while_queued / n, 1) if n else 0.0,
        "mean_busiest_over_least_busy": round(sum(ratios) / len(ratios), 1) if ratios else 0.0,
    }


def pct(xs, q):
    """Percentile without pulling numpy in for four numbers."""
    if not xs:
        return float("nan")
    xs = sorted(xs)
    if len(xs) == 1:
        return float(xs[0])
    i = q / 100.0 * (len(xs) - 1)
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return float(xs[lo] + (xs[hi] - xs[lo]) * (i - lo))


def per_engine_rows(run, engines, window_s=None):
    """One record per (run, engine). This is what --csv writes.

    Percentiles rather than the mean, because the mean of a queue depth over an
    hour says very little: the same mean comes from a queue that is always 200
    deep and from one that is empty for fifty minutes and 2,000 deep for ten.

    The window argument exists because a run's file can be much longer than the
    period in which it was under load, and every statistic taken over the whole
    file is then diluted by the idle part. The PolyServe hour run
    (260808_2245_exp71r1_polyserve_fullb) is the case that prompted this: the
    engines are at KV 99.4-99.8% with a median queue of 700-5,600 requests from
    500 s to 3,500 s, and from 4,000 s to the end of the 7,300 s file they are at
    KV 0.0% with an empty queue. Over the whole file that engine's KV median
    reads 9.0%; over the first 3,700 s it reads 99.6%. The second number is the
    one that describes the run.
    """
    out = []
    names = sorted(engines)
    t0 = engines[names[0]][0][0]
    for e in names:
        rows = engines[e]
        if window_s is not None:
            rows = [r for r in rows if r[0] - t0 <= window_s]
        if not rows:
            continue
        run_b = [r[1] for r in rows]
        wait = [r[2] for r in rows]
        kv = [r[3] for r in rows]
        pre = [r[4] for r in rows if r[4] is not None]
        hit = [r[5] for r in rows if r[5] is not None]
        qry = [r[6] for r in rows if r[6] is not None]
        out.append({
            "run": os.path.basename(run),
            "engine": e,
            "samples": len(rows),
            "span_s": round(rows[-1][0] - rows[0][0], 1),
            "batch_p50": round(pct(run_b, 50), 1),
            "batch_p95": round(pct(run_b, 95), 1),
            "kv_p50_pct": round(100 * pct(kv, 50), 1),
            "kv_p95_pct": round(100 * pct(kv, 95), 1),
            "kv_max_pct": round(100 * max(kv), 1),
            "queue_p50": round(pct(wait, 50), 1),
            "queue_p95": round(pct(wait, 95), 1),
            "queue_max": round(max(wait), 1),
            "preemptions": round(pre[-1] - pre[0], 0) if len(pre) > 1 else "",
            "prefix_hit_pct": (round(100 * (hit[-1] - hit[0]) / (qry[-1] - qry[0]), 1)
                               if len(hit) > 1 and len(qry) > 1 and qry[-1] > qry[0] else ""),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run")
    ap.add_argument("--runs", help="glob over run directories")
    ap.add_argument("--step", type=int, default=30, help="rows to skip between printed samples")
    ap.add_argument("--summary-only", action="store_true")
    # Until 2026-08-09 this script printed everything and wrote nothing, so every
    # engine-layer number that reached a document had been copied by hand out of
    # a terminal and could not be checked against the run afterwards.
    ap.add_argument("--csv", help="also write one row per (run, engine) here")
    ap.add_argument("--window-s", type=float,
                    help="for --csv only: restrict to the first N seconds of each "
                         "run, measured from its first sample. Use it when the "
                         "drain tail is long enough to dilute the statistics. The "
                         "printed summary above the CSV line always covers the "
                         "whole file, so the two disagree on purpose when this is set.")
    a = ap.parse_args()
    csv_rows = []

    runs = []
    if a.run:
        runs.append(a.run)
    if a.runs:
        runs.extend(sorted(glob.glob(a.runs)))
    if not runs:
        sys.exit("give --run or --runs")

    for run in runs:
        engines = load_engines(run)
        if not engines:
            print(f"\n=== {os.path.basename(run)} === no per-engine series")
            continue
        names = sorted(engines)
        print(f"\n=== {os.path.basename(run)} ===")
        if not a.summary_only:
            print("   t(s) | " + " | ".join(f"{e:^18}" for e in names))
            print("        | " + " | ".join(f"{'run  wait   kv':^18}" for e in names))
            t0 = engines[names[0]][0][0]
            n = min(len(engines[e]) for e in names)
            for i in range(0, n, a.step):
                cells = []
                for e in names:
                    _, r, w, kv = engines[e][i][:4]
                    cells.append(f"{r:4.0f} {w:5.0f} {100 * kv:4.0f}%")
                print(f"  {engines[names[0]][i][0] - t0:5.0f} | " + " | ".join(cells))
        # Counters, so the run's total is the difference between its ends. Taken
        # over the whole file rather than the printed window: a preemption during
        # warmup is still work the engine had to redo.
        print()
        print(f"  {'engine':<8}{'preemptions':>13}{'prefix hit':>12}"
              f"{'KV mean':>9}{'KV max':>8}")
        for e in names:
            rows = engines[e]
            pre = [r[4] for r in rows if r[4] is not None]
            hit = [r[5] for r in rows if r[5] is not None]
            qry = [r[6] for r in rows if r[6] is not None]
            kv = [r[3] for r in rows]
            npre = (pre[-1] - pre[0]) if len(pre) > 1 else float("nan")
            rate = ((hit[-1] - hit[0]) / (qry[-1] - qry[0])
                    if len(hit) > 1 and len(qry) > 1 and qry[-1] > qry[0]
                    else float("nan"))
            print(f"  {e:<8}{npre:>13,.0f}{100 * rate:>11.1f}%"
                  f"{100 * (sum(kv) / len(kv)):>8.1f}%{100 * max(kv):>7.1f}%")

        s = summarise(engines)
        print(f"\n  engines {s['engines']}, {s['samples']} samples over {s['span_s']:.0f}s")
        print(f"  peak queue depth on one engine   {s['peak_queue_depth']:>8,.0f}")
        print(f"  an engine idle while another had work queued  "
              f"{s['idle_while_queued_pct']:>5.1f}% of samples "
              f"({s['idle_while_queued_samples']:,})")
        print(f"  mean busiest / least busy        {s['mean_busiest_over_least_busy']:>8.1f}x")
        total_pre = 0.0
        for e in names:
            pre = [r[4] for r in engines[e] if r[4] is not None]
            if len(pre) > 1:
                total_pre += pre[-1] - pre[0]
        if total_pre > 0:
            print(f"  -> {total_pre:,.0f} preemptions. Each one discards the prefill "
                  "already done for that request and pays for it again on reschedule, "
                  "which is engine time no request-level metric attributes to anything.")
        if s["peak_queue_depth"] > 500:
            print("  -> a queue this deep is work the router committed to an engine that "
                  "could not take it. A dispatched request cannot be recalled, so this is "
                  "not something later decisions can undo.")
        if a.csv:
            csv_rows.extend(per_engine_rows(run, engines, a.window_s))

    if a.csv:
        if not csv_rows:
            print("\nno per-engine series in any run; nothing written")
            return 1
        os.makedirs(os.path.dirname(os.path.abspath(a.csv)) or ".", exist_ok=True)
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
            w.writeheader()
            w.writerows(csv_rows)
        note = f", first {a.window_s:.0f}s of each run" if a.window_s else ""
        print(f"\nwrote {a.csv}  ({len(csv_rows)} rows{note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
