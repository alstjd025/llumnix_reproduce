#!/usr/bin/env python3
"""Write a copy of fluidserve.json with one class's length distribution scaled.

WHY. The policy takes each class's output-length distribution as an input and
uses it in three places: the expected remaining tokens `E[L-j | L>j]` that the
feasibility test multiplies by the pace, the completion probability over a
horizon, and the KV growth projection. We know one point on the cost of getting
that input wrong -- when the deepresearch profile was 3.4x too small, static
60 req/s scored 36.6 instead of 56.8, a loss of 20.2 points (section 49) -- and
nothing between. EXP-63 fills that in.

WHAT IT SCALES. The class's whole length distribution, not only its mean: the
`grid` (token counts) is multiplied by the factor and `survival` is left alone,
which is exactly "the same shape, s times longer". mean/p50/p90/p99/max are
scaled to match so that anything reading a summary sees the same distribution
the grid describes.

WHAT IT DOES NOT TOUCH. `decode_step_law` and `prefill_step_law` are properties
of the engine, not of the workload; changing them at the same time would move
two things at once. `classes[]` entries other than the named one are copied
verbatim.

  python3 ms_dev/scripts/scale_class_profile.py deepresearch 1.5 --out /tmp/p.json
"""
import argparse
import copy
import json
import os

REPO = "/home/nxclab/llumnix_reproduce"
SRC = os.path.join(REPO, "deploy/profiling/llama31-70b-b200-tp2/fluidserve.json")
SCALED = ("mean", "p50", "p90", "p99", "max")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("klass")
    ap.add_argument("factor", type=float)
    ap.add_argument("--src", default=SRC)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    d = json.load(open(a.src))
    hit = None
    for c in d["classes"]:
        if c["name"] == a.klass:
            hit = c
            break
    if hit is None:
        raise SystemExit(f"no class named {a.klass}; have "
                         f"{[c['name'] for c in d['classes']]}")
    before = hit["mean"]
    hit["grid"] = [x * a.factor for x in hit["grid"]]
    for k in SCALED:
        if k in hit:
            hit[k] = hit[k] * a.factor
    d.setdefault("metadata", {})["scaled"] = {
        "class": a.klass, "factor": a.factor, "mean_before": before,
        "mean_after": hit["mean"],
        "note": "EXP-63 profile sensitivity; grid scaled, survival unchanged",
    }
    json.dump(d, open(a.out, "w"))
    print(f"{a.klass} mean {before:.1f} -> {hit['mean']:.1f} (x{a.factor}) "
          f"written to {a.out}")


if __name__ == "__main__":
    main()
