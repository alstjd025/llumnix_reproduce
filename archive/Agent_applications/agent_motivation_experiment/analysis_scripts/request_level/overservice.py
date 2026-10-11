#!/usr/bin/env python3
"""Over-service: the share of delivered decode speed that was faster than entitled.

WHY THIS EXISTS. Every attempt to justify the class preference as "separation is
good" has met a counterexample, and the reason is that separation is not the
objective. An instance's admissible per-token pace is the MINIMUM budget among
the requests resident on it, so a single tight request holds the whole instance
to its own pace and every other resident is then served faster than its class
asked for. That excess speed is not free: the instance cannot grow its batch past
the point where the step time reaches the tightest resident's budget, so the
capacity spent delivering it is capacity unavailable to admit other work.

The quantity below measures that excess directly, from the client's own records
and with no counterfactual:

    waste = sum_r out_r * max(0, 1 - p_r / b_r) / sum_r out_r

where p_r is the request's realised mean inter-token latency and b_r its own
entitlement. Zero means served exactly at entitlement; 0.5 means served at twice
the speed it asked for. It is a fraction, so it compares across arrival rates and
across mixes.

ENTITLEMENT, per class, matching what the policy is told and what the analysis
scores:
  chat          50 ms per token
  deepresearch 100 ms per token
  swe          an END-TO-END budget of 30 s, so its per-token entitlement is
               30000 / its own output length and differs request by request.

WHAT THIS DOES NOT SAY. Over-service is not by itself recoverable capacity. A
fleet that is simply underloaded over-serves everything, and no routing decision
would change that. The part attributable to MIXING is the part where the
request sat on an instance whose minimum resident budget was tighter than its
own, and separating that part needs the request-to-engine join; --mixing adds it
for runs that have `analysis/request_engine.csv`.

Usage:
    python3 overservice.py 'results/*exp94*_m1_rpm_1500' [--mixing] [--by-class]
"""
import argparse, glob, os, sys
import numpy as np, pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from exp22_fluidserve import load_run, CLASSES        # noqa: E402

NOMINAL = {"chat": 50.0, "deepresearch": 100.0}       # per token, ms
SWE_E2E_MS = 30000.0                                  # swe is an end-to-end budget


def entitlement(rows):
    """Per-request per-token entitlement in ms, as a Series aligned to rows."""
    b = pd.Series(np.nan, index=rows.index)
    for c, v in NOMINAL.items():
        b.loc[rows["class"] == c] = v
    m = rows["class"] == "swe"
    out = pd.to_numeric(rows.loc[m, "output_tokens"], errors="coerce")
    # A request that produced nothing has no per-token entitlement to speak of;
    # it is dropped below with the other unusable rows rather than given one.
    b.loc[m] = SWE_E2E_MS / out.where(out > 0)
    return b


def usable(rows):
    """Completed requests with a realised pace. Rejections have no stream, and a
    run-boundary cutoff has a truncated one whose mean pace is not the pace the
    request would have had."""
    ok = (~rows["rejected"]) & (~rows["errored"]) & (~rows["cutoff"])
    return rows[ok & rows["itl_ms"].notna() & (rows["itl_ms"] > 0)]


def waste(rows):
    r = usable(rows)
    b = entitlement(r)
    out = pd.to_numeric(r["output_tokens"], errors="coerce").fillna(0.0)
    ex = (1.0 - r["itl_ms"] / b).clip(lower=0.0)
    keep = b.notna() & (out > 0)
    if not keep.any():
        return np.nan, 0
    # Total over total, never a mean of per-request ratios: a long request holds
    # the instance for longer and must count for more.
    return float((out[keep] * ex[keep]).sum() / out[keep].sum()), int(keep.sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("globs", nargs="+")
    ap.add_argument("--by-class", action="store_true")
    a = ap.parse_args()
    dirs = sorted({d for g in a.globs for d in glob.glob(g) if os.path.isdir(d)})
    dirs = [d for d in dirs if "PRERUN" not in d]
    if not dirs:
        sys.exit("no run directories matched")
    hdr = f"{'run':46s} {'n':>7s} {'waste':>7s}"
    if a.by_class:
        hdr += "".join(f"{c[:4]:>8s}" for c in CLASSES)
    print(hdr)
    for d in dirs:
        rows = load_run(d)
        if rows is None or rows.empty:
            print(f"{os.path.basename(d)[:46]:46s} (no rows)"); continue
        w, n = waste(rows)
        line = f"{os.path.basename(d)[:46]:46s} {n:7,d} {w:7.3f}"
        if a.by_class:
            for c in CLASSES:
                wc, _ = waste(rows[rows["class"] == c])
                line += f"{wc:8.3f}"
        print(line)


if __name__ == "__main__":
    main()
