"""Token-level inter-token-latency (time-between-tokens) distributions.

One pass per run over ``tbt_events.jsonl``.  Everything the caller asked for is
computed in that single pass and written as one JSON record per run:

  * pooled percentiles over every gap of every request in the condition;
  * the token-level violation rate against the 50 ms chat budget, both as a
    fraction of gaps and as a fraction of streaming time;
  * the three per-request collapsing rules (mean / p90 / max), each summarised
    across requests;
  * the time-scale curve: non-overlapping windows of W consecutive gaps, mean
    inside each window, per-request p90 of the window means, median across
    requests;
  * the number of gaps each request contributes, bucketed, so the pooled and
    the per-request views can be told apart.

Every quantity is produced three times: on the recorded chunk arrival gaps
("raw"), and on the burst-corrected series at the headline threshold
("cor15", tau = 15 ms) and at the threshold the earlier tables used ("cor5",
tau = 5 ms).  The correction is the one written for
``results/aggregate_analysis/tail_2026-08-16/burst_correct.py`` and is copied
here verbatim so this script has no import path into that directory: a run of
chunks whose arrival gaps fall below TAU is one burst, and the wall-clock span
the burst covers (measured from the last chunk before it) is divided evenly
among the tokens inside it.  A stream with no sub-TAU gap is left bit-for-bit
unchanged, which is the case for every llm-d request, so the correction cannot
favour one arm over the other.

The bursts are not a client or a transport difference.  All 80 pinned runs use
the same client class and the same read loop against the same four vLLM
engines; the only structural difference is the proxy in front.  The Llumnix Go
gateway exhausts its CFS CPU quota (cpu.max 800000/100000, i.e. 8 cores on a
100 ms period) and the kernel stops the whole container until the next period
boundary, so every live stream freezes and resumes together.  That is why the
corrected series is the ENGINE-side per-token behaviour and the raw series is
the CLIENT-side one: the raw series includes a stall of our own test harness
that a deployment sized differently would not have.

Usage:
    python3 tail2026_token_level.py            # all target runs, writes JSON
    python3 tail2026_token_level.py <run> ...  # named runs only
"""
import json
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

EXPDIR = "/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
RESULTS = os.path.join(EXPDIR, "results")
PINNED = os.path.join(EXPDIR, "paper_experiment/static_sweep_2026-08/data")
OUTDIR = os.path.join(RESULTS, "aggregate_analysis/tail_2026-08-16")

# chat class only.  sg- = sharegpt (chat), sa- = searcharena (deepresearch),
# everything else = swe.
CHAT_PREFIX = "sg-"
BUDGET_MS = 50.0                      # chat per-token budget
# Burst thresholds.  15 ms is the headline: a real decode step cannot be
# shorter than about 16 ms on this deployment (llm-d's 1st percentile is
# 15.98 ms), so 2/5/10 ms all cut into the burst-internal population and the
# answer still moves with the threshold there.  Between 13 and 16 ms it is
# stable.  5 ms is kept so the earlier tables stay reproducible.
TAUS = (2.0, 5.0, 10.0, 13.0, 15.0, 16.0)
TAU_MAIN = 15.0                       # headline
TAU_ALT = 5.0                         # sensitivity, matches the earlier tables
SERIES = ("raw", "cor15", "cor5")
WINDOWS = (1, 2, 5, 10, 20, 50, 100, 200)
WMAX = max(WINDOWS)                   # fixed request population for the curve
MIN_CHUNKS = 3                        # the correction needs three arrivals
PCTS = (50, 75, 90, 95, 96, 97, 98, 99, 99.5, 99.9)
LEN_BUCKETS = ((2, 50), (50, 200), (200, 500), (500, 10 ** 9))


def corrected(offsets, tau):
    """Per-token times from chunk arrival offsets (ms), bursts spread evenly.

    Verbatim from tail_2026-08-16/burst_correct.py.
    """
    a = np.asarray(offsets, float)
    if len(a) < 3:
        return None
    g = np.diff(a)
    out = np.empty(len(g))
    i = 0
    while i < len(g):
        j = i + 1
        while j < len(g) and g[j] < tau:
            j += 1
        span = a[j] - a[i]
        k = j - i
        out[i:j] = span / k
        i = j
    return out


def window_p90(g, w):
    """p90 and p90/p50 across the means of non-overlapping windows of w gaps."""
    n = len(g) // w
    if n < 1:
        return None, None
    if w == 1:
        m = g[: n]
    else:
        m = g[: n * w].reshape(n, w).mean(axis=1)
    p90, p50 = np.percentile(m, [90, 50])
    return float(p90), (float(p90 / p50) if p50 > 0 else None)


def allowed_requests(run):
    """(task_id, call_index) of chat requests that count, plus the exclusions."""
    df = pd.read_csv(os.path.join(PINNED, run, "metrics.csv"), low_memory=False)
    df = df[df["agent"] == "request"]
    chat = df[df["task_id"].astype(str).str.startswith(CHAT_PREFIX)]
    flags = ["is_server_terminated", "is_error", "is_timeout", "is_job_timeout"]
    bad = np.zeros(len(chat), bool)
    per_flag = {}
    for f in flags:
        v = chat[f].fillna(False).astype(bool).to_numpy() if f in chat.columns \
            else np.zeros(len(chat), bool)
        per_flag[f] = int(v.sum())
        bad |= v
    rej = chat["is_rejected"].fillna(False).astype(bool).to_numpy() \
        if "is_rejected" in chat.columns else np.zeros(len(chat), bool)
    keep = chat[~bad]
    ok = set(zip(keep["task_id"].astype(str), keep["call_index"].astype(int)))
    excl = dict(chat_rows=int(len(chat)), excluded=int(bad.sum()),
                rejected=int(rej.sum()), rejected_and_excluded=int((rej & bad).sum()),
                **{("excl_" + k): v for k, v in per_flag.items()})
    return ok, excl


def scan(run):
    ok, excl = allowed_requests(run)

    pooled = {"raw": []}
    for t in TAUS:
        pooled["cor%g" % t] = []
    # per-request collapses, on the raw series and on both corrected series
    pr = {"%s_%s" % (s, k): [] for s in SERIES for k in ("mean", "p90", "max")}
    pr["ngaps"] = []
    # violation accounting
    vio = {k: dict(n=0, ntot=0, t=0.0, ttot=0.0) for k in SERIES}
    # time-scale curve, fixed population (ngaps >= WMAX) and all requests
    curve = {("%s_%d_%s" % (s, w, pop)): []
             for s in SERIES for w in WINDOWS for pop in ("fix", "all")}
    ratio = {("%s_%d_%s" % (s, w, pop)): []
             for s in SERIES for w in WINDOWS for pop in ("fix", "all")}
    buckets = {b: dict(nreq=0, ngaps=0, means=[], p90s=[]) for b in LEN_BUCKETS}
    n_short = 0

    with open(os.path.join(RESULTS, run, "tbt_events.jsonl")) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("agent") != "request":
                continue
            tid = str(d.get("task_id", ""))
            if not tid.startswith(CHAT_PREFIX):
                continue
            try:
                key = (tid, int(d.get("call_index")))
            except (TypeError, ValueError):
                continue
            if key not in ok:
                continue
            ev = d.get("chunk_events") or []
            if len(ev) < MIN_CHUNKS:
                n_short += 1
                continue
            off = np.fromiter((e["arrival_offset_ms"] for e in ev), float, len(ev))
            g = np.diff(off)
            if not np.all(np.isfinite(g)) or g.min() < 0:
                continue
            cors = {t: corrected(off, t) for t in TAUS}
            series = (("raw", g), ("cor15", cors[TAU_MAIN]), ("cor5", cors[TAU_ALT]))

            pooled["raw"].append(g.astype(np.float32))
            for t in TAUS:
                pooled["cor%g" % t].append(cors[t].astype(np.float32))

            pr["ngaps"].append(len(g))
            for name, arr in series:
                pr[name + "_mean"].append(arr.mean())
                pr[name + "_p90"].append(np.percentile(arr, 90))
                pr[name + "_max"].append(arr.max())

            for name, arr in series:
                over = arr > BUDGET_MS
                v = vio[name]
                v["n"] += int(over.sum())
                v["ntot"] += len(arr)
                v["t"] += float(arr[over].sum())
                v["ttot"] += float(arr.sum())

            for name, arr in series:
                fix = len(arr) >= WMAX
                for w in WINDOWS:
                    p, r = window_p90(arr, w)
                    if p is None:
                        continue
                    curve["%s_%d_all" % (name, w)].append(p)
                    if fix:
                        curve["%s_%d_fix" % (name, w)].append(p)
                    if r is not None:
                        ratio["%s_%d_all" % (name, w)].append(r)
                        if fix:
                            ratio["%s_%d_fix" % (name, w)].append(r)

            for lo, hi in LEN_BUCKETS:
                if lo <= len(g) < hi:
                    b = buckets[(lo, hi)]
                    b["nreq"] += 1
                    b["ngaps"] += len(g)
                    b["means"].append(g.mean())
                    b["p90s"].append(np.percentile(g, 90))
                    break

    out = dict(run=run, n_requests=len(pr["ngaps"]), n_short=n_short, **excl)

    for name, chunks in pooled.items():
        if not chunks:
            continue
        a = np.concatenate(chunks)
        qs = np.percentile(a, PCTS)
        for p, q in zip(PCTS, qs):
            out["pool_%s_p%s" % (name, str(p).replace(".", "_"))] = float(q)
        out["pool_%s_max" % name] = float(a.max())
        out["pool_%s_mean" % name] = float(a.mean())
        out["pool_%s_n" % name] = int(len(a))
        out["pool_%s_p1" % name] = float(np.percentile(a, 1))
        out["pool_%s_frac_sub1" % name] = float((a < 1.0).mean())
        out["pool_%s_frac_sub5" % name] = float((a < 5.0).mean())
        out["pool_%s_frac_sub16" % name] = float((a < 16.0).mean())
        del a

    for name in SERIES:
        v = vio[name]
        out["vio_%s_frac_gaps" % name] = v["n"] / v["ntot"] if v["ntot"] else float("nan")
        out["vio_%s_frac_time" % name] = v["t"] / v["ttot"] if v["ttot"] else float("nan")
        out["vio_%s_n_over" % name] = v["n"]
        out["vio_%s_n_gaps" % name] = v["ntot"]
        out["vio_%s_stream_s" % name] = v["ttot"] / 1000.0

    for k in ["%s_%s" % (s, q) for s in SERIES for q in ("mean", "p90", "max")]:
        a = np.asarray(pr[k], float)
        if not len(a):
            continue
        out["req_%s_med" % k] = float(np.median(a))
        out["req_%s_p90" % k] = float(np.percentile(a, 90))
        out["req_%s_p99" % k] = float(np.percentile(a, 99))
    ng = np.asarray(pr["ngaps"], float)
    if len(ng):
        for p in (10, 25, 50, 75, 90, 99):
            out["ngaps_p%d" % p] = float(np.percentile(ng, p))
        out["ngaps_mean"] = float(ng.mean())
        out["ngaps_total"] = int(ng.sum())
        srt = np.sort(ng)[::-1]
        cum = np.cumsum(srt) / srt.sum()
        out["gapshare_top10pct_requests"] = float(cum[max(0, int(0.10 * len(srt)) - 1)])
        out["gapshare_bottom50pct_requests"] = float(
            1.0 - cum[max(0, int(0.50 * len(srt)) - 1)])

    for k, v in curve.items():
        out["curve_" + k] = float(np.median(v)) if v else float("nan")
        out["curve_n_" + k] = len(v)
    for k, v in ratio.items():
        out["ratio_" + k] = float(np.median(v)) if v else float("nan")

    for (lo, hi), b in buckets.items():
        tag = "b%d_%d" % (lo, hi if hi < 10 ** 9 else 0)
        out[tag + "_nreq"] = b["nreq"]
        out[tag + "_ngaps"] = b["ngaps"]
        out[tag + "_med_mean"] = float(np.median(b["means"])) if b["means"] else float("nan")
        out[tag + "_med_p90"] = float(np.median(b["p90s"])) if b["p90s"] else float("nan")

    return out


def target_runs():
    runs = []
    for name in sorted(os.listdir(PINNED)):
        if not os.path.isdir(os.path.join(PINNED, name)):
            continue
        if name < "260807_1900":
            continue
        if "_fspfx_" not in name and "_llmdslo_" not in name:
            continue
        if not any(name.endswith("_rpm_%d" % r) for r in (600, 900, 1200, 1500, 2100)):
            continue
        runs.append(name)
    return runs


def main():
    runs = sys.argv[1:] or target_runs()
    os.makedirs(OUTDIR, exist_ok=True)
    with Pool(min(len(runs), 20)) as pool:
        rows = pool.map(scan, runs)
    df = pd.DataFrame(rows)
    path = os.path.join(OUTDIR, "tail2026_tokenlevel_per_run.csv")
    df.to_csv(path, index=False)
    print("wrote", path, df.shape)


if __name__ == "__main__":
    main()
