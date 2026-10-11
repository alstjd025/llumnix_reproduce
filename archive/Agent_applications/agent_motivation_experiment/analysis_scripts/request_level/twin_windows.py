#!/usr/bin/env python3
"""Twin windows: can aggregate engine features predict the next window's pace,
or does the resident composition -- which those features do not contain -- carry
information the features cannot express?

Motivation (paper section 3.2). llm-d's latency predictor consumes aggregate
instance features (KV usage, running count, queue length, ...). Whatever it
learns, two moments with identical aggregate features must get the same
prediction. This script measures how much the realised NEXT-window pace differs
between such moments when their resident class composition differs -- an
irreducible error floor for any feature-marginal predictor, and exactly the
information a per-resident registry carries.

Measurement choices (stated per the reporting rule):
  window          5 s; features averaged over window w, target = mean of
                  scheduler_fluidserve_raw_step_ms over window w+1 (the raw
                  per-status-interval measured step time, unsmoothed)
  features        vllm:kv_cache_usage_perc, num_requests_running,
                  num_requests_waiting (engine Prometheus gauges)
  composition     resident requests at the window midpoint, residency
                  approximated as [start_time, start_time + latency] (includes
                  gateway wait -- same approximation as separation_measures.py);
                  class from task-id prefix (sg-=chat, sa-=deepresearch,
                  else swe); admitted requests only (is_rejected false)
  analysis window first arrival + 60 s warmup .. last arrival - 20 s drain
  bins            KV decile x running(40-wide) x waiting(0 / 1-10 / >10) --
                  at least as fine as llm-d's own training buckets (KV decile
                  x queue bucket), so "same bin" understates what its
                  predictor could distinguish in our favour of the baseline
  out-of-sample   bin-mean predictor fitted on repetition 1, evaluated on
                  repetition 2 (never in-sample)
"""

import csv
import json
import math
import sys
from collections import defaultdict

WINDOW_S = 5.0
WARMUP_S = 60.0
DRAIN_S = 20.0


def class_of(task_id: str) -> str:
    if task_id.startswith("sg-"):
        return "chat"
    if task_id.startswith("sa-"):
        return "deepresearch"
    return "swe"


PORTS = ("8000", "8001", "8002", "8003")


def load_run(run):
    req2port = {}
    with open(f"{run}/analysis/request_engine.csv") as f:
        for row in csv.DictReader(f):
            req2port[(row["task_id"], row["call_index"])] = row["engine_port"]

    # engine gauges per port; the pace target is the engine's own inter-token
    # latency histogram (cumulative sum/count), which exists in every arm.
    feats = defaultdict(list)  # port -> [(t, kv, run, wait)]
    itl = defaultdict(list)    # port -> [(t, cum_sum_s, cum_count)]
    for port in PORTS:
        try:
            fh = open(f"{run}/server_metrics/engine_{port}.jsonl")
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                d = json.loads(line)
                t = d.get("t")
                kv = rn = wt = None
                s = c = None
                for k, v in d.items():
                    if k.startswith("vllm:kv_cache_usage_perc"):
                        kv = float(v) * 100.0
                    elif k.startswith("vllm:num_requests_running"):
                        rn = float(v)
                    elif k.startswith("vllm:num_requests_waiting"):
                        wt = float(v)
                    elif k.startswith("vllm:inter_token_latency_seconds_sum"):
                        s = float(v)
                    elif k.startswith("vllm:inter_token_latency_seconds_count"):
                        c = float(v)
                if kv is not None and rn is not None:
                    feats[port].append((t, kv, rn, wt or 0.0))
                if s is not None and c is not None:
                    itl[port].append((t, s, c))

    # residents from client metrics
    reqs = defaultdict(list)  # port -> [(start, end, class)]
    starts = []
    with open(f"{run}/metrics.csv") as f:
        for row in csv.DictReader(f):
            if row.get("agent") != "request":
                continue
            if row.get("is_rejected", "").strip().lower() in ("true", "1"):
                continue
            try:
                s = float(row["start_time"])
                lat = float(row["latency"])
            except (ValueError, KeyError):
                continue
            starts.append(s)
            port = req2port.get((row["task_id"], row["call_index"]))
            if port:
                reqs[port].append((s, s + lat, class_of(row["task_id"])))
    t0, t1 = min(starts) + WARMUP_S, max(starts) - DRAIN_S
    return itl, feats, reqs, t0, t1


def hist_at(ser, t):
    """Last cumulative (sum, count) sample at or before t, or None."""
    lo, hi = 0, len(ser)
    while lo < hi:
        mid = (lo + hi) // 2
        if ser[mid][0] <= t:
            lo = mid + 1
        else:
            hi = mid
    return ser[lo - 1] if lo else None


def windows_of(run):
    itl, feats, reqs, t0, t1 = load_run(run)
    out = []
    for port in feats:
        iser = sorted(itl.get(port, []))
        fser = sorted(feats[port])
        rser = sorted(reqs.get(port, []))
        w = t0
        while w + 2 * WINDOW_S <= t1:
            fs = [x for x in fser if w <= x[0] < w + WINDOW_S]
            a = hist_at(iser, w + WINDOW_S)
            b = hist_at(iser, w + 2 * WINDOW_S)
            mid = w + WINDOW_S / 2
            res = [(s, e, c) for (s, e, c) in rser if s <= mid < e]
            if fs and a and b and b[2] - a[2] >= 50 and res:
                kv = sum(x[1] for x in fs) / len(fs)
                rn = sum(x[2] for x in fs) / len(fs)
                wt = sum(x[3] for x in fs) / len(fs)
                nxt = (b[1] - a[1]) / (b[2] - a[2]) * 1000.0
                n = len(res)
                dr = sum(1 for r in res if r[2] == "deepresearch") / n
                ch = sum(1 for r in res if r[2] == "chat") / n
                prog = sum((mid - s) / max(e - s, 1e-9) for (s, e, _) in res) / n
                out.append(dict(port=port, t=w, kv=kv, run=rn, wait=wt,
                                nxt=nxt, dr=dr, chat=ch, prog=prog, n=n))
            w += WINDOW_S
    return out


def bin_of(w):
    kvb = min(int(w["kv"] // 10), 9)
    rnb = int(w["run"] // 40)
    wtb = 0 if w["wait"] < 0.5 else (1 if w["wait"] <= 10 else 2)
    return (kvb, rnb, wtb)


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def main():
    train_runs, test_runs = [], []
    mode = None
    for a in sys.argv[1:]:
        if a == "--train":
            mode = "tr"
        elif a == "--test":
            mode = "te"
        elif mode == "tr":
            train_runs.append(a)
        elif mode == "te":
            test_runs.append(a)
    tr = [w for r in train_runs for w in windows_of(r)]
    te = [w for r in test_runs for w in windows_of(r)]
    print(f"windows: train {len(tr)}, test {len(te)}")

    # --- A. within-bin composition split (pooled, descriptive) -------------
    both = tr + te
    bins = defaultdict(list)
    for w in both:
        bins[bin_of(w)].append(w)
    gaps = []
    for b, ws in sorted(bins.items()):
        lo = [w for w in ws if w["dr"] < 0.34]
        hi = [w for w in ws if w["dr"] > 0.66]
        if len(lo) >= 4 and len(hi) >= 4:
            gaps.append((b, len(lo), len(hi), mean([w["nxt"] for w in lo]),
                         mean([w["nxt"] for w in hi])))
    print("\n[A] same aggregate-feature bin, split by deepresearch share "
          "(<1/3 vs >2/3), next-window pace (ms):")
    print("bin(kv_decile,run/40,waitb)  n_lo n_hi  pace_lo  pace_hi   gap")
    for b, nlo, nhi, plo, phi in gaps:
        print(f"  {b}  {nlo:4d} {nhi:4d}  {plo:7.1f}  {phi:7.1f}  {phi-plo:+6.1f}")
    if gaps:
        wsum = sum(min(g[1], g[2]) for g in gaps)
        wgap = sum(min(g[1], g[2]) * abs(g[4] - g[3]) for g in gaps) / wsum
        print(f"  weighted mean |gap| over {len(gaps)} bins: {wgap:.1f} ms")

    # --- B. out-of-sample predictor comparison -----------------------------
    def fit(ws, key):
        m = defaultdict(list)
        for w in ws:
            m[key(w)].append(w["nxt"])
        return {k: mean(v) for k, v in m.items()}, mean([w["nxt"] for w in ws])

    key_agg = bin_of
    key_cmp = lambda w: bin_of(w) + (w["dr"] > 0.5, w["prog"] > 0.5)
    for name, key in (("aggregate bins only", key_agg),
                      ("+ composition (dr>1/2, progress>1/2)", key_cmp)):
        model, fallback = fit(tr, key)
        errs, miss = [], 0
        for w in te:
            p = model.get(key(w))
            if p is None:
                p = fallback
                miss += 1
            errs.append(abs(p - w["nxt"]))
        print(f"\n[B] {name}: test MAE {mean(errs):.1f} ms "
              f"(unseen-bin fallback {100*miss/len(te):.0f}%)")

    # --- C. twin exhibits ---------------------------------------------------
    print("\n[C] twin windows (same bin, |dKV|<3, |drun|<15, ddr>0.5):")
    shown = 0
    for b, ws in sorted(bins.items()):
        ws = sorted(ws, key=lambda w: w["dr"])
        for i in range(len(ws)):
            for j in range(len(ws) - 1, i, -1):
                a, c = ws[i], ws[j]
                if (c["dr"] - a["dr"] > 0.5 and abs(a["kv"] - c["kv"]) < 3
                        and abs(a["run"] - c["run"]) < 15):
                    print(f"  kv {a['kv']:.0f}/{c['kv']:.0f}%  run "
                          f"{a['run']:.0f}/{c['run']:.0f}  wait "
                          f"{a['wait']:.0f}/{c['wait']:.0f} | dr "
                          f"{a['dr']:.2f} vs {c['dr']:.2f} | next pace "
                          f"{a['nxt']:.1f} vs {c['nxt']:.1f} ms")
                    shown += 1
                    break
            if shown >= 8:
                break
        if shown >= 8:
            break


if __name__ == "__main__":
    main()
