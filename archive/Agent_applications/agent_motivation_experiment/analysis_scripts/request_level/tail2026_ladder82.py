#!/usr/bin/env python3
"""The quantile ladder, re-run on the EXP-82 re-measurements.

`tail2026_quantile_ladder.py` answers the same question on the pinned static
sweep, and its scoring function is reused here unchanged. Two things force a
separate entry point rather than a flag on that script.

  * The runs are different. Every per-token percentile in the pinned sweep was
    taken while the Llumnix gateway container was exhausting its CPU quota and
    freezing all of its streams together, which pushed 14.11% of that arm's
    token gaps below 5 ms and dragged its first percentile down to 0.02 ms. The
    EXP-82 re-runs raise the gateway to GOMAXPROCS=16 and the artefact is gone
    (0.07-0.44% of gaps below 5 ms, first percentile 11.37 ms). Only these runs
    may be used for a percentile.
  * The statistic is computed here, not read from a column. `metrics.csv` stops
    at `tbt_p95_ms`, so a p99 of a request's own gaps does not exist in it; and
    the recorded percentile columns are computed by the client over its chunk
    arrivals, which is the quantity this script wants but is worth deriving in
    one place next to the p99. The derived mean is checked against `itl_ms`
    ((e2e - ttft)/(tokens - 1)) request by request, and the ratio is printed, so
    a scale disagreement between the two would be visible rather than assumed
    away.

Scored with all arrivals in the denominator: a rejected request and a request
that never finished are both violations. The swe class is scored on a 30 s
end-to-end budget and so must be bit-identical under every substitution -- that
identity is asserted, not hoped for.

    python3 tail2026_ladder82.py --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from exp22_fluidserve import load_run, CLASSES  # noqa: E402
from all_arrivals_attainment import parse_dir  # noqa: E402
from tail2026_quantile_ladder import score  # noqa: E402

QS = [50, 90, 95, 99]
ARM_LABEL = {"fspfx": "FluidServe", "llmdslo": "llm-d"}


def request_gap_stats(run):
    """(task_id, call_index) -> mean / p50 / p90 / p95 / p99 / max of its own gaps."""
    out = {}
    path = os.path.join(run, "tbt_events.jsonl")
    if not os.path.isfile(path):
        return out
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except Exception:
                continue
            ce = d.get("chunk_events") or []
            if len(ce) < 3:
                continue
            off = np.array([c.get("arrival_offset_ms") or 0.0 for c in ce], float)
            g = np.diff(off)
            g = g[g >= 0]
            if g.size < 2:
                continue
            key = (str(d.get("task_id")), str(d.get("call_index", "")))
            rec = {"gap_mean": float(g.mean()), "gap_max": float(g.max()),
                   "n_gap": int(g.size)}
            for q in QS:
                rec[f"gap_p{q}"] = float(np.percentile(g, q))
            out[key] = rec
    return out


def collect(results, pattern):
    recs, checks = [], []
    for d in sorted(glob.glob(os.path.join(results, pattern))):
        if not os.path.isdir(d) or "PRERUN" in d:
            continue
        if os.path.getsize(os.path.join(d, "tbt_events.jsonl")) < 1e6:
            continue
        rows = load_run(d)
        if rows is None or rows.empty:
            continue
        rows = rows[rows["agent"].astype(str) == "request"].copy()
        meta = parse_dir(d)
        rep = 2 if "exp82r2" in d else 1
        stats = request_gap_stats(d)
        key = list(zip(rows["task_id"].astype(str),
                       rows["call_index"].astype(str)))
        for name in ["gap_mean", "gap_max"] + [f"gap_p{q}" for q in QS]:
            rows[name] = [stats.get(k, {}).get(name, np.nan) for k in key]

        itl = pd.to_numeric(rows["itl_ms"], errors="coerce")
        ratio = (rows["gap_mean"] / itl).replace([np.inf, -np.inf], np.nan).dropna()
        checks.append({"run": os.path.basename(d), "arm": meta["arm"], "rep": rep,
                       "rate": meta["rate"], "n": len(rows),
                       "n_with_gaps": int(rows["gap_mean"].notna().sum()),
                       "n_completed_without_gaps": int(
                           (rows["gap_mean"].isna() & ~rows["rejected"]
                            & ~rows["errored"] & ~rows["cutoff"]).sum()),
                       "gapmean_over_itl_median": float(ratio.median())})

        base = {"run": os.path.basename(d), "arm": meta["arm"], "rep": rep,
                "rate": meta["rate"], "n": len(rows)}
        for stat in ["itl_ms", "gap_mean"] + [f"gap_p{q}" for q in QS]:
            v = pd.to_numeric(rows[stat], errors="coerce")
            a, n = score(rows, v)
            rec = dict(base, stat=stat, all_arrivals=a)
            for c in CLASSES:
                rec[c] = score(rows, v, rows["class"] == c)[0]
            recs.append(rec)
    return pd.DataFrame(recs), pd.DataFrame(checks)


def fmt(g):
    """mean of the repeats with the min..max range, or the single value."""
    v = sorted(g)
    if len(v) == 1:
        return f"{v[0]:.1f}"
    return f"{np.mean(v):.1f} [{v[0]:.1f}..{v[-1]:.1f}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--glob", default="*exp82r[12]_*")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    df, checks = collect(a.results, a.glob)
    os.makedirs(a.out_dir, exist_ok=True)
    df.to_csv(os.path.join(a.out_dir, "15_ladder82_per_run.csv"), index=False)
    checks.to_csv(os.path.join(a.out_dir, "15_ladder82_checks.csv"), index=False)

    print(checks.to_string(index=False))
    # The swe budget is end-to-end, so no per-token statistic can move it.
    swe = df.pivot_table(index=["run"], columns="stat", values="swe")
    dev = (swe.max(axis=1) - swe.min(axis=1)).max()
    print(f"\nswe attainment spread across all six statistics: {dev:.6f} pp "
          f"({'identical as required' if dev < 1e-9 else 'NOT IDENTICAL'})")

    stats = ["itl_ms", "gap_mean"] + [f"gap_p{q}" for q in QS]
    for col, label in [("all_arrivals", "all arrivals")] + [(c, c) for c in CLASSES]:
        print(f"\n=== attainment %, denominator = {label}")
        print("rate | " + " | ".join(f"{s:>18}" for s in stats))
        for rate in sorted(df.rate.unique()):
            for arm in ("fspfx", "llmdslo"):
                cells = []
                for s in stats:
                    g = df[(df.rate == rate) & (df.arm == arm) & (df.stat == s)][col].dropna()
                    cells.append(f"{fmt(g):>18}" if len(g) else f"{'-':>18}")
                print(f"{rate:4.0f} {ARM_LABEL[arm]:<10} | " + " | ".join(cells))


if __name__ == "__main__":
    main()
