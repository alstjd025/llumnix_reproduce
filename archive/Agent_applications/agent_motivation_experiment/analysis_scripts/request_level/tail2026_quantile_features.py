#!/usr/bin/env python3
"""Per engine, per one-second window: the step-time quantiles next to the
features a scheduler could actually read at the moment it places a request.

WHY THIS TABLE EXISTS. The FluidServe admission test compares a predicted MEAN
iteration time against a per-token budget
(`pkg/scheduler/policy/fluidserve.go:1600-1601`), and the capacity model behind
that prediction carries no second moment at all
(`pkg/scheduler/policy/fluidserve_capacity.go:35-50`). If the service level
objective is written on a quantile of per-token time instead of on its mean,
then the predicate would have to put a quantile on its left-hand side, and
nothing in the control plane currently represents one. This table is the input
to the question of whether such a quantile could be predicted from what the
scheduler already observes.

WHAT IS REUSED. The step-time reconstruction is not redone here. In continuous
batching every sequence on an engine advances one token per step, so the gap
between two streamed chunks of a request is the duration of one step of the
engine that request sits on; `tail2026_window_gaps.py` already pools those gaps
per engine per one-second scrape window and writes `14_window_gaps.csv` with the
window's mean, p50, p90, p99 next to the engine's own running batch, KV
occupancy, waiting queue, prefill token rate and preemption delta. That file is
read as given. What is ADDED here is everything the scheduler knows that the
engine gauges do not carry, and the split of prefill volume by when the request
that caused it was placed.

THE THREE FEATURE SETS, and why they are kept apart.

  state       running batch, KV occupancy, waiting queue, the engine's own mean
              iteration time over the PREVIOUS window, and the number of
              resident requests of each class. Every one of these is on the
              instance view the policy holds at decision time
              (`fluidserve.go:186-239` for the instance record,
              `fluidserve_registry.go:145-165` for the per-request records).

  lagged      the above plus the prefill token rate of the PREVIOUS window.
              This is the causal analogue of what the policy already computes as
              `arrivingPrefill`, which is a persistence estimate of prefill over
              the horizon taken from the measured prefill duty cycle
              (`fluidserve.go:1006-1007`, `fluidserve.go:882-903`).

  concurrent  the above plus the prefill token rate of the SAME window. This is
              not available when the placement is decided. It is carried so that
              the gap between it and `lagged` measures exactly how much of the
              quantile lives in information the scheduler cannot have.

PREFILL ATTRIBUTION. A request's prompt is computed by the engine somewhere
between the moment the request reaches it and the moment its first token comes
back, so its prompt tokens are spread uniformly over `[start_time, start_time +
first_token_latency]` and accumulated into the scrape windows they fall in. The
sum is checked against the engine's own `vllm:prompt_tokens_total` delta and the
ratio is reported per run; the attribution is only usable to the extent that
ratio is near one. Each window's attributed volume is then split in two: the
part contributed by requests that had already been placed before the window
opened, and the part contributed by requests placed inside it. The same split is
carried forward from each scrape instant over horizons of 1, 2, 3 and 5 seconds,
because the policy's planning horizon is 100 iterations
(`pkg/consts/consts.go:236`) which at 30 ms an iteration is about 3 seconds.

OUTPUT (into --out-dir)
  15_window_features.csv   one row per run x engine x window
  15_prefill_check.csv     per run: attributed prompt tokens / engine counter

    python3 tail2026_quantile_features.py \
        --results results --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import csv
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tail2026_step_batch import (  # noqa: E402
    engine_series, klass, parse_run, read_engine_map)

CLASSES = ("chat", "deepresearch", "swe")
HORIZONS = (1.0, 2.0, 3.0, 5.0)


def read_requests(run):
    """Per-request arrival, first-token and completion times, per class.

    Rejected requests are dropped: they never reach an engine, so they cause no
    prefill and hold no KV. Requests cut off when the load window closed ARE
    kept, because they did run and did cost the engine prefill work; the
    exclusion rule that drops them applies to the gap statistics, which are read
    from `14_window_gaps.csv` and not recomputed here.
    """
    out = []
    with open(os.path.join(run, "metrics.csv")) as fh:
        for row in csv.DictReader(fh):
            if row.get("agent") != "request":
                continue
            if str(row.get("is_rejected", "")).lower() == "true":
                continue
            try:
                t0 = float(row["start_time"])
                ttft = float(row["first_token_latency"])
                lat = float(row["latency"])
                pin = float(row["input_tokens"])
            except (TypeError, ValueError, KeyError):
                continue
            if not np.isfinite(t0) or pin <= 0 or ttft <= 0 or lat <= 0:
                continue
            out.append((row["task_id"], str(row.get("call_index", "")),
                        t0, t0 + ttft, t0 + lat, pin, klass(row["task_id"])))
    return out


def spread(a, b, tokens, t):
    """Contribution of one prefill interval [a, b] to each window of grid `t`.

    Window i is (t[i-1], t[i]]. Returns (lo, hi, vals) with vals aligned to
    window indices lo..hi-1, so the caller can add into a slice.
    """
    lo = int(np.searchsorted(t, a, side="left"))
    hi = int(np.searchsorted(t, b, side="left")) + 1
    lo = max(lo, 1)
    hi = min(hi, len(t))
    if hi <= lo:
        return lo, lo, np.zeros(0)
    edges = np.concatenate(([t[lo - 1]], t[lo:hi]))
    if b > a:
        f = np.clip((edges - a) / (b - a), 0.0, 1.0)
    else:
        f = (edges >= a).astype(float)
    return lo, hi, tokens * np.diff(f)


def build_run(run, map_dir):
    meta = parse_run(run)
    if meta is None:
        return [], None
    emap = read_engine_map(run, map_dir)
    ser = engine_series(run)
    if emap is None or not ser:
        return [], None
    reqs = read_requests(run)

    by_port = {p: [] for p in ser}
    unmapped = 0
    for tid, ci, a, b, e, pin, kl in reqs:
        p = emap.get((tid, ci))
        if p is None or p not in by_port:
            unmapped += 1
            continue
        by_port[p].append((a, b, e, pin, kl))

    rows = []
    attributed = 0.0
    counted = 0.0
    for port, s in ser.items():
        t = s["t"]
        n = len(t)
        if n < 5:
            continue
        pf_tot = np.zeros(n)          # prefill tokens attributed to window i
        pf_new = np.zeros(n)          # ... from requests placed inside window i
        # forward horizons: volume in (t[j], t[j]+H] and the part of it owed to
        # requests already placed at t[j]
        fwd_tot = {h: np.zeros(n) for h in HORIZONS}
        fwd_known = {h: np.zeros(n) for h in HORIZONS}
        res = {k: np.zeros(n) for k in CLASSES}   # decoding at instant t[i]
        arr_tok = np.zeros(n)                     # prompt tokens ARRIVING in i
        arr_n = np.zeros(n)                       # placements made in i
        arr_max = np.zeros(n)                     # largest single prompt in i

        for a, b, e, pin, kl in by_port[port]:
            lo, hi, v = spread(a, b, pin, t)
            if v.size:
                pf_tot[lo:hi] += v
                ja = int(np.searchsorted(t, a, side="left"))
                if lo <= ja < hi:
                    pf_new[ja] += v[ja - lo]
                # forward view from every scrape instant this request straddles
                for j in range(max(lo - 1, 0), hi):
                    if t[j] < a:
                        continue
                    for h in HORIZONS:
                        k = min(int(np.searchsorted(t, t[j] + h, side="left")) + 1, hi)
                        if k > j + 1:
                            fwd_known[h][j] += v[max(j + 1, lo) - lo:k - lo].sum()
            ia = int(np.searchsorted(t, a, side="left"))
            if 0 <= ia < n:
                arr_tok[ia] += pin
                arr_n[ia] += 1
                arr_max[ia] = max(arr_max[ia], pin)
            # decoding residency: from first token to completion
            i0 = int(np.searchsorted(t, b, side="left"))
            i1 = int(np.searchsorted(t, e, side="right"))
            if i1 > i0:
                res[kl][i0:min(i1, n)] += 1.0

        for h in HORIZONS:
            c = np.concatenate(([0.0], np.cumsum(pf_tot)))
            idx = np.clip(np.searchsorted(t, t + h, side="left") + 1, 0, n)
            fwd_tot[h] = c[idx] - c[np.arange(n) + 1]

        # engine-side check
        dpr = np.diff(s["prompt"])
        counted += float(np.nansum(dpr[dpr >= 0]))
        attributed += float(pf_tot[1:].sum())

        dt = np.diff(t)
        for i in range(1, n):
            if dt[i - 1] <= 0:
                continue
            r = {"run": meta["run"], "arm": meta["arm"], "rep": meta["rep"],
                 "rate": meta["rate"], "engine": port, "t": t[i],
                 "pf_tok": pf_tot[i], "pf_tok_new": pf_new[i],
                 "pf_tok_carried": pf_tot[i] - pf_new[i],
                 "arr_prompt_tok": arr_tok[i], "arr_n": arr_n[i],
                 "arr_max_tok": arr_max[i],
                 "n_chat": res["chat"][i], "n_dr": res["deepresearch"][i],
                 "n_swe": res["swe"][i]}
            for h in HORIZONS:
                r[f"fwd{int(h)}_tot"] = fwd_tot[h][i]
                r[f"fwd{int(h)}_known"] = min(fwd_known[h][i], fwd_tot[h][i])
            rows.append(r)

    chk = {"run": meta["run"], "arm": meta["arm"], "rate": meta["rate"],
           "attributed_prompt_tok": attributed, "engine_prompt_tok": counted,
           "ratio": attributed / counted if counted > 0 else np.nan,
           "unmapped_requests": unmapped, "requests": len(reqs)}
    return rows, chk


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--map-dir", default=None)
    ap.add_argument("--gaps", default=None,
                    help="14_window_gaps.csv; its runs define the run set")
    a = ap.parse_args()
    if a.map_dir is None:
        a.map_dir = os.path.join(a.out_dir, "engine_maps")
    if a.gaps is None:
        a.gaps = os.path.join(a.out_dir, "14_window_gaps.csv")

    runs = []
    with open(a.gaps) as fh:
        for row in csv.DictReader(fh):
            if row["run"] not in runs:
                runs.append(row["run"])
    print(f"{len(runs)} runs carried by {os.path.basename(a.gaps)}", file=sys.stderr)

    out, checks = [], []
    for name in runs:
        run = os.path.join(a.results, name)
        rows, chk = build_run(run, a.map_dir)
        if chk:
            checks.append(chk)
            print(f"  {name}: {len(rows)} windows, prefill attribution ratio "
                  f"{chk['ratio']:.3f}, {chk['unmapped_requests']} unmapped",
                  file=sys.stderr)
        out.extend(rows)

    p = os.path.join(a.out_dir, "15_window_features.csv")
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)
    print(f"wrote {p} ({len(out)} rows)")
    p = os.path.join(a.out_dir, "15_prefill_check.csv")
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(checks[0].keys()))
        w.writeheader()
        w.writerows(checks)
    print(f"wrote {p} ({len(checks)} runs)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
