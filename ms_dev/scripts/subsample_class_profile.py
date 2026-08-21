#!/usr/bin/env python3
"""Write a copy of fluidserve.json whose classes[] were built from N samples.

WHY. The class length distribution is the one input the policy never corrects
while running (it is read once behind a sync.Once), so how much traffic it needs
is a deployment question. The offline half (eval_b6_profile_sample_size.py)
measured how far the two quantities the decision path reads drift at small N;
this builds the actual profile an operator would have after that much traffic,
so the cluster half can measure what the drift costs end to end.

WHAT IT REPLACES. classes[] only -- grid, survival, mean, p50/p90/p99, max, n --
rebuilt from N lengths per class drawn from the measured pool. The pool is the
pinned EXP-82 FluidServe runs' completed requests (truncated ones excluded),
cached by the offline script. The judged conditions are NEW runs, so the sample
is out of sample with respect to what it will be judged on.

WHAT IT DOES NOT TOUCH. decode_step_law and prefill_step_law are properties of
the engine, not of the workload. Same rule as scale_class_profile.py.

TRAPS THIS ENCODES (each one cost a run before):
  - grid entries must be ints; a float 0.0 killed the scheduler at start-up and
    lost four and a half hours (EXP-63 first attempt).
  - --src is REQUIRED, not defaulted to the live file, so a derived profile can
    never be derived from an already-derived one (the compounding-scale bug).
  - the written file is re-read and its stated means are checked against the
    sample means before the script reports success.

  python3 ms_dev/scripts/subsample_class_profile.py 250 --src <orig> --out /tmp/p.json
"""
import argparse
import copy
import json

import numpy as np

CACHE = ("/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment/"
         "results/aggregate_analysis/paper_eval_2026-08/b6_lengths.npz")


def survival_on_grid(lengths, grid):
    ls = np.sort(np.asarray(lengths))
    idx = np.searchsorted(ls, grid, side="right")
    return ((len(ls) - idx) / float(len(ls))).tolist()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", type=int, help="samples per class")
    ap.add_argument("--src", required=True,
                    help="the ORIGINAL profile; required so a derived file is "
                         "never derived from a derived one")
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=90)
    a = ap.parse_args()

    prof = json.load(open(a.src))
    pool = np.load(CACHE)
    rng = np.random.default_rng(a.seed)

    expected = {}
    for c in prof["classes"]:
        name = c["name"]
        lengths = pool[name]
        if a.n > len(lengths):
            raise SystemExit("class %s has only %d measured lengths, asked for %d"
                             % (name, len(lengths), a.n))
        s = rng.choice(lengths, size=a.n, replace=False)
        grid = [int(g) for g in c["grid"]]          # ints, or the scheduler dies
        c["grid"] = grid
        c["survival"] = survival_on_grid(s, grid)
        c["n"] = int(a.n)
        c["mean"] = float(np.mean(s))
        c["p50"] = float(np.percentile(s, 50))
        c["p90"] = float(np.percentile(s, 90))
        c["p99"] = float(np.percentile(s, 99))
        c["max"] = int(np.max(s))
        expected[name] = c["mean"]

    prof.setdefault("metadata", {})["subsample"] = {
        "n_per_class": a.n, "seed": a.seed, "source_pool": CACHE,
        "note": "classes[] rebuilt from N sampled lengths; step laws untouched"}
    with open(a.out, "w") as f:
        json.dump(prof, f)

    # Read back and check, rather than trusting the write.
    back = json.load(open(a.out))
    for c in back["classes"]:
        assert isinstance(c["grid"][0], int), "grid is not int"
        got, want = c["mean"], expected[c["name"]]
        assert abs(got - want) < 1e-6, (c["name"], got, want)
        assert abs(c["survival"][0] - 1.0) < 0.05, \
            "survival at 0 should be ~1, got %r" % c["survival"][0]
    print("wrote %s  (n=%d per class; means: %s)"
          % (a.out, a.n,
             ", ".join("%s %.0f" % (c["name"], c["mean"]) for c in back["classes"])))


if __name__ == "__main__":
    main()
