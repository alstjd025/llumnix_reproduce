#!/usr/bin/env python3
"""Within-request timing variability against the engines' preemption count.

WHY A RATIO AND NOT AN ABSOLUTE LATENCY. The client's absolute per-token columns
are under suspicion. `tbt_mean_ms` was recorded at roughly half the true value
until 2026-07-30 because each streamed chunk was tokenised out of the context it
belonged to, and only the mean was ever corrected; the size of that bias can
differ per arm because it depends on how chunks split. Any calibration error
that multiplies every gap of a request by the same factor cancels in
`tbt_p90_ms / tbt_p50_ms` taken WITHIN one request, so that ratio measures how
uneven the token timing is inside a single request without depending on the
absolute scale being right.

WHAT THE RATIO CANNOT DO. It compares two order statistics of the same request's
gap series, so it needs enough gaps for p90 and p50 to be distinct estimates.
Requests with very few output tokens are dropped for that reason (--min-tokens).

WHAT THIS TESTS. Two mechanisms produce an uneven gap series and they are
distinguishable by how many gaps they damage:

  (a) recompute preemption   The engine evicts the request when key-value cache
                             runs short, frees its blocks and zeroes its computed
                             prefix, so the request pays its whole prefill again.
                             That is ONE very long gap per eviction. For it to
                             move a request's p90 rather than only its maximum,
                             more than a tenth of that request's gaps have to be
                             long, so a 400-token request needs about 40
                             evictions. The signature is therefore a p90/p50
                             ratio that stays near 1 while the MAXIMUM explodes,
                             and a preemption count that tracks the share of
                             affected requests.
  (b) prefill interference   A prefill chunk of another request is co-scheduled
                             into the same engine step, so that step is long for
                             every request decoding in it. This damages many
                             gaps moderately and needs no preemption at all.
                             The signature is a raised p90/p50 with a maximum
                             that is large but not thousands of times the median.

So the discriminating measurement is not the ratio alone: it is the ratio next
to the preemption count and next to the maximum gap.

Outputs (all under --out-dir):
  tail2026_ratio_per_run.csv   one row per run: ratio quantiles, share above the
                               threshold, gap-shape counters, preemptions joined
                               from tail2026_run_rows.csv

    python3 tail2026_ratio_vs_preemption.py [--out-dir DIR] [--ratio-threshold 2.0]
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))
DEFAULT_OUT = os.path.join(EXPDIR, "results", "aggregate_analysis", "tail_2026-08-16")

# Task-id prefixes to class. `sg` is the ShareGPT chat stream, `sa` the
# Search-Arena deep-research stream, everything else is a SWE-bench repository
# name and belongs to the coding class.
def klass(tid):
    p = str(tid).split("-")[0]
    return {"sg": "chat", "sa": "deepresearch"}.get(p, "swe")


def run_rows(run_dir, min_tokens, thr):
    p = os.path.join(run_dir, "metrics.csv")
    if not os.path.exists(p):
        return None
    df = pd.read_csv(p, low_memory=False)
    if "agent" not in df.columns:
        return None
    df = df[df["agent"] == "request"].copy()
    if df.empty:
        return None

    def flag(col):
        if col not in df.columns:
            return pd.Series(False, index=df.index)
        return df[col].astype(str).str.lower().isin(["true", "1", "1.0"])

    # Only requests that actually ran to completion. A request cut off by the end
    # of the load window has a truncated gap series whose p90 is taken over
    # whatever arrived before the cut, and a rejected request has no series at
    # all. Both are excluded for the same reason the project rule gives for
    # output length: a truncated value is worse than a missing one because it
    # looks usable.
    bad = flag("is_rejected") | flag("is_error") | flag("is_timeout") | \
        flag("is_job_timeout") | flag("is_server_terminated")
    df["klass"] = df["task_id"].map(klass)
    ok = df[(~bad) & df["tbt_p50_ms"].notna() & df["tbt_p90_ms"].notna()
            & (df["tbt_p50_ms"] > 0) & (df["output_tokens"] >= min_tokens)].copy()
    if ok.empty:
        return None
    ok["ratio"] = ok["tbt_p90_ms"].astype(float) / ok["tbt_p50_ms"].astype(float)
    ok["spike"] = ok["tbt_max_ms"].astype(float) / ok["tbt_p50_ms"].astype(float)

    out = {"n_complete": len(ok), "n_offered": len(df),
           "reject_pct": round(100.0 * float(flag("is_rejected").mean()), 1)}
    for name, sub in [("all", ok), ("chat", ok[ok.klass == "chat"])]:
        if sub.empty:
            continue
        r, s = sub["ratio"], sub["spike"]
        out[f"{name}_n"] = len(sub)
        out[f"{name}_ratio_med"] = round(float(r.median()), 3)
        out[f"{name}_ratio_p90"] = round(float(r.quantile(0.90)), 3)
        out[f"{name}_frac_ratio_gt_thr"] = round(100.0 * float((r > thr).mean()), 2)
        # Gap-shape counters. `spike` is the request's largest gap in units of
        # its own median gap, so it is calibration-free in the same way as the
        # ratio. A recompute of a 1,500-token prompt at roughly 50 ms per decode
        # step is several hundred median-gaps; a step lengthened by one prefill
        # chunk is a few.
        out[f"{name}_spike_med"] = round(float(s.median()), 1)
        out[f"{name}_spike_p90"] = round(float(s.quantile(0.90)), 1)
        out[f"{name}_frac_spike_gt50"] = round(100.0 * float((s > 50).mean()), 2)
        out[f"{name}_frac_maxgap_gt2s"] = round(
            100.0 * float((sub["tbt_max_ms"].astype(float) > 2000).mean()), 2)
        out[f"{name}_tbt_p50_med"] = round(float(sub["tbt_p50_ms"].median()), 1)
        out[f"{name}_tbt_p90_med"] = round(float(sub["tbt_p90_ms"].median()), 1)
        out[f"{name}_outtok_med"] = round(float(sub["output_tokens"].median()), 0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=DEFAULT_OUT)
    ap.add_argument("--ratio-threshold", type=float, default=2.0)
    ap.add_argument("--min-tokens", type=int, default=20,
                    help="drop requests with fewer output tokens: p90 and p50 of "
                         "a handful of gaps are not distinct estimates")
    a = ap.parse_args()

    base = pd.read_csv(os.path.join(a.out_dir, "tail2026_run_rows.csv"))
    recs = []
    for _, row in base.iterrows():
        d = os.path.join(EXPDIR, "results", row["run"])
        r = run_rows(d, a.min_tokens, a.ratio_threshold)
        if r is None:
            print(f"no usable client rows: {row['run']}")
            continue
        r.update(run=row["run"], arm=row["arm"], rate=row["rate"], rep=row["rep"],
                 preempt_total=row["preempt_total"],
                 preempt_max_engine=row["preempt_max_engine"],
                 kv_p90_busiest=row["kv_p90_busiest"],
                 kv_p90_fleet=row["kv_p90_fleet"],
                 admitted=row["admitted"])
        r["preempt_per_1k_admitted"] = (
            round(1000.0 * row["preempt_total"] / row["admitted"], 2)
            if row["admitted"] else None)
        recs.append(r)

    df = pd.DataFrame(recs)
    front = ["run", "arm", "rate", "rep", "preempt_total", "preempt_per_1k_admitted",
             "kv_p90_busiest", "all_ratio_med", "chat_ratio_med",
             "chat_frac_ratio_gt_thr", "chat_spike_med", "chat_spike_p90"]
    cols = front + [c for c in df.columns if c not in front]
    df = df[cols].sort_values(["arm", "rate", "rep"])
    out = os.path.join(a.out_dir, "tail2026_ratio_per_run.csv")
    df.to_csv(out, index=False)
    print(f"wrote {out}  ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
