#!/usr/bin/env python3
"""B6 -- how much traffic does the length profile need before the policy can use it?

WHY THIS QUESTION AND NOT "what if the profile is wrong by 30%".

Of the three quantities FluidServe predicts, two correct themselves while the
system runs: the step-time model trains a multiplicative correction against what
the engines report (`noteResidual`, capacity model), and the prefix prefill charge
has its own calibration. The class length distribution does NOT. It is read once
per process behind a `sync.Once` and never revisited, so an error there is the
only one that persists.

That makes the deployment question concrete rather than synthetic. An operator
turning this on has no history. How much traffic is needed before the profile is
good enough, and good enough FOR WHAT -- because the policy does not use the mean
output length. It uses two quantities derived from the survival function
S(j) = P(length > j) on a 16-token grid:

    completionProb(j, k)   = (S(j) - S(j+k)) / S(j)
    expectedRemaining(j)   = sum over x > j of S(x)/S(j) * (grid spacing)

reproduced here exactly as fluidserve_profile.go computes them. `k` is the
planning horizon, 100 iterations.

METHOD. The pool of measured output lengths per class is split in half. The
reference profile is built from ALL of half A; samples of size N are drawn from
half B. Estimating out of sample matters: drawing the sample from the same set the
reference was built from makes the two correlated and understates the error at
small N.

WEIGHTING. The error is reported where the policy actually evaluates these
functions, not uniformly over the grid. A request contributes an evaluation at j
only if it is still alive at j, so evaluations are distributed as S(j). The
weighted error is therefore the error the decision path actually sees.

Read only. Prints; writes one CSV and caches the extracted lengths.

Usage:
    python3 eval_b6_profile_sample_size.py [--repeats 200]
"""
import argparse
import collections
import csv
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
EXPDIR = os.path.abspath(os.path.join(HERE, "..", ".."))
REPO = os.path.abspath(os.path.join(EXPDIR, "..", ".."))
PROFILE = os.path.join(REPO, "deploy", "profiling",
                       "llama31-70b-b200-tp2", "fluidserve.json")
CACHE = os.path.join(EXPDIR, "results", "aggregate_analysis",
                     "paper_eval_2026-08", "b6_lengths.npz")
HORIZON = 100
CLASSES = ["chat", "deepresearch", "swe"]


def extract_lengths():
    """Measured output lengths per class, from the pinned FluidServe runs."""
    if os.path.exists(CACHE):
        z = np.load(CACHE)
        return {c: z[c] for c in CLASSES}
    from exp22_fluidserve import load_run
    import pandas as pd
    mpath = os.path.join(EXPDIR, "paper_experiment",
                         "static_sweep_clean_2026-08", "manifest.tsv")
    acc = collections.defaultdict(list)
    for r in csv.DictReader(open(mpath), delimiter="\t"):
        if r["arm_label"] != "FluidServe":
            continue
        d = load_run(os.path.join(EXPDIR, "results", r["run"]))
        if d is None or d.empty:
            continue
        # A truncated request's output_tokens is a lower bound, not a length.
        ok = (~d["rejected"]) & (~d["cutoff"]) & (~d["errored"])
        out = pd.to_numeric(d["output_tokens"], errors="coerce")
        for c in CLASSES:
            s = out[ok & (d["class"] == c) & out.notna() & (out > 0)]
            if len(s):
                acc[c].append(s.values.astype(np.int64))
    res = {c: np.concatenate(acc[c]) for c in CLASSES}
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    np.savez_compressed(CACHE, **res)
    return res


def survival(lengths, grid):
    """S(j) = P(length > j) on the grid, as the profile stores it."""
    n = len(lengths)
    if n == 0:
        return np.zeros(len(grid))
    ls = np.sort(lengths)
    # count of lengths strictly greater than each grid point
    idx = np.searchsorted(ls, grid, side="right")
    return (n - idx) / float(n)


def survival_at(grid, surv, j):
    """Linear interpolation, clamped -- fluidserve_profile.go survivalAt."""
    return np.interp(j, grid, surv, left=surv[0], right=surv[-1])


def expected_remaining_table(grid, surv):
    """expectedRemainingCache -- fluidserve_profile.go precompute()."""
    widths = np.diff(grid).astype(float)
    out = np.empty(len(grid))
    # acc[i] = sum_{x>i} S(x) * (grid[x]-grid[x-1]); computed by suffix sum
    tail = np.concatenate([np.cumsum((surv[1:] * widths)[::-1])[::-1], [0.0]])
    for i in range(len(grid)):
        sj = surv[i]
        if sj <= 1e-9:
            out[i] = 1.0
            continue
        acc = tail[i] / sj
        out[i] = acc if acc >= 1.0 else 1.0
    return out


def derived(grid, surv, js):
    """The two quantities the decision path reads, evaluated at js."""
    er_tab = expected_remaining_table(grid, surv)
    er = np.interp(js, grid, er_tab, left=er_tab[0], right=1.0)
    er = np.maximum(er, 1.0)
    sj = survival_at(grid, surv, js)
    sjk = survival_at(grid, surv, js + HORIZON)
    with np.errstate(divide="ignore", invalid="ignore"):
        cp = np.where(sj <= 1e-9, 1.0, (sj - sjk) / np.maximum(sj, 1e-12))
    return er, np.clip(cp, 0.0, 1.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=200)
    ap.add_argument("--seed", type=int, default=20260821)
    a = ap.parse_args()

    grid = np.array(json.load(open(PROFILE))["classes"][0]["grid"], dtype=float)
    lengths = extract_lengths()
    rng = np.random.default_rng(a.seed)

    # Evaluation points: the grid, weighted by how often the decision path lands
    # there. A request is evaluated at j only if it is still alive at j.
    sizes = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]
    rows = []
    print("measured output lengths per class: " + ", ".join(
        "%s %d" % (c, len(lengths[c])) for c in CLASSES))
    print("\nout-of-sample error in the TWO QUANTITIES THE POLICY READS,")
    print("weighted by where the decision path evaluates them (%d resamples)\n"
          % a.repeats)

    for c in CLASSES:
        pool = lengths[c]
        rng.shuffle(pool)
        half = len(pool) // 2
        ref_pool, draw_pool = pool[:half], pool[half:]
        ref = survival(ref_pool, grid)
        w = ref / max(ref.sum(), 1e-12)          # evaluation weights = S(j)
        er_ref, cp_ref = derived(grid, ref, grid)

        print("%s  (pool %d, reference from %d, sampled from %d)"
              % (c, len(pool), len(ref_pool), len(draw_pool)))
        print("  %8s %18s %18s %16s" % (
            "N", "E[remaining] p50", "그 p90", "completionProb p90"))
        for N in sizes:
            if N > len(draw_pool):
                continue
            e_er, e_cp = [], []
            for _ in range(a.repeats):
                s = rng.choice(draw_pool, size=N, replace=False)
                er, cp = derived(grid, survival(s, grid), grid)
                rel = np.abs(er - er_ref) / np.maximum(er_ref, 1e-9)
                e_er.append(np.average(rel, weights=w))
                e_cp.append(np.average(np.abs(cp - cp_ref), weights=w))
            er_p50, er_p90 = np.percentile(e_er, [50, 90])
            cp_p90 = np.percentile(e_cp, 90)
            rows.append({"class": c, "n": N, "er_rel_p50": er_p50,
                         "er_rel_p90": er_p90, "cp_abs_p90": cp_p90})
            print("  %8d %17.1f%% %17.1f%% %15.3f" % (
                N, 100 * er_p50, 100 * er_p90, cp_p90))
        print()

    out = os.path.join(EXPDIR, "results", "aggregate_analysis",
                       "paper_eval_2026-08", "b6_profile_sample_size.csv")
    with open(out, "w", newline="") as f:
        w2 = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w2.writeheader()
        w2.writerows(rows)
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
