#!/usr/bin/env python3
"""Score the KV projection against what the engines actually held one horizon later.

The scheduler publishes, per instance and about once a second,
`scheduler_fluidserve_projected_kv_tokens` (the quantity `newKv <= capMem` is
tested against), `scheduler_fluidserve_obs_kv_tokens` (what the engine reported
holding), `scheduler_fluidserve_obs_decode_batch` and
`scheduler_fluidserve_outflow_tokens`, plus `scheduler_fluidserve_pace_ms`. The
planning horizon is `horizonSteps` iterations, so in seconds it is
`100 * pace_ms / 1000`. Pairing the projection at time t with the occupancy
reported at t + that, per instance, gives the projection's error directly.

Four predictors are scored on the same pairs so the terms can be attributed:

  kv          current occupancy, no projection at all -- what the
              --fluidserve-enable-flux=false ablation computes
  kv+in       occupancy plus the growth of the resident set, no release term
  shipped     kv + inflow - outflow, the deployed projection
  slope       occupancy plus its own EWMA rate of change, extrapolated over the
              horizon -- candidate H2, which models neither term separately

Mean error signs the bias: negative means the projection said less than the
engine turned out to hold, which is the direction that admits work the engine
cannot carry. Mean absolute error sizes it. The fraction below zero says whether
the error is one-sided, which matters more than its size: a symmetric error
averages out over many decisions and a one-sided one accumulates.

Usage:
    python3 exp48_projection_error.py 'results/*exp45r1_fluidserve_full' ...
"""
import collections
import glob
import json
import os
import sys

import numpy as np

WANT = ("scheduler_fluidserve_projected_kv_tokens",
        "scheduler_fluidserve_obs_kv_tokens",
        "scheduler_fluidserve_obs_decode_batch",
        "scheduler_fluidserve_outflow_tokens",
        "scheduler_fluidserve_pace_ms")
HORIZON_STEPS = 100
SLOPE_ALPHA = 0.1


def series(path):
    """Per-instance [(t, {metric: value})], keeping only fully populated samples."""
    out = collections.defaultdict(list)
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("t")
            if t is None:
                continue
            per = collections.defaultdict(dict)
            for k, v in d.items():
                if "|instance=" not in k:
                    continue
                base, iid = k.split("|instance=", 1)
                if base in WANT:
                    per[iid][base] = v
            for iid, m in per.items():
                if len(m) == len(WANT):
                    out[iid].append((t, m))
    return out


def score(path):
    acc = collections.defaultdict(list)
    occ = []
    for rows in series(path).values():
        rows.sort(key=lambda r: r[0])
        ts = np.array([r[0] for r in rows], float)
        kv = np.array([r[1]["scheduler_fluidserve_obs_kv_tokens"] for r in rows], float)
        nd = np.array([r[1]["scheduler_fluidserve_obs_decode_batch"] for r in rows], float)
        of = np.array([r[1]["scheduler_fluidserve_outflow_tokens"] for r in rows], float)
        pc = np.array([r[1]["scheduler_fluidserve_pace_ms"] for r in rows], float)
        horizon_s = HORIZON_STEPS * pc / 1000.0

        # The instance's own rate of change, smoothed the way prefillDutyOf is.
        slope, sl = 0.0, np.zeros(len(ts))
        for i in range(1, len(ts)):
            dt = ts[i] - ts[i - 1]
            if dt > 1e-6:
                slope += SLOPE_ALPHA * ((kv[i] - kv[i - 1]) / dt - slope)
            sl[i] = slope

        for i in range(1, len(ts)):
            j = np.searchsorted(ts, ts[i] + horizon_s[i])
            if j >= len(ts):
                break
            actual = kv[j]
            acc["kv"].append(kv[i] - actual)
            acc["kv+in"].append(kv[i] + nd[i] * HORIZON_STEPS - actual)
            acc["shipped"].append(
                max(kv[i] + nd[i] * HORIZON_STEPS - of[i], 0.0) - actual)
            acc["slope"].append(kv[i] + sl[i] * horizon_s[i] - actual)
            occ.append((kv[i], actual, acc["shipped"][-1]))
    return acc, np.array(occ), float(np.median(horizon_s)) if len(ts) else float("nan")


def main(patterns):
    dirs = []
    for p in patterns:
        dirs.extend(sorted(glob.glob(p)))
    if not dirs:
        sys.exit("no run directories matched")

    print(f"{'run':<44}{'horizon':>8}{'n':>8}  "
          f"{'predictor':<10}{'mean':>10}{'MAE':>10}{'under%':>8}")
    for d in dirs:
        path = os.path.join(d, "server_metrics", "scheduler.jsonl")
        if not os.path.exists(path):
            print(f"{os.path.basename(d):<44}  no scheduler.jsonl")
            continue
        acc, occ, horizon = score(path)
        if not acc["kv"]:
            print(f"{os.path.basename(d):<44}  no paired samples")
            continue
        n = len(acc["kv"])
        for k, name in enumerate(("kv", "kv+in", "shipped", "slope")):
            e = np.array(acc[name])
            head = f"{os.path.basename(d):<44}{horizon:>7.1f}s{n:>8,d}  " if k == 0 \
                else " " * 62
            print(f"{head}{name:<10}{e.mean():>10,.0f}"
                  f"{np.abs(e).mean():>10,.0f}{100 * (e < 0).mean():>8.1f}")

        # Where the shipped projection is worst, by how full the engine already is.
        kvnow, actual, err = occ[:, 0], occ[:, 1], occ[:, 2]
        qs = np.percentile(kvnow, [0, 25, 50, 75, 90, 100])
        print(f"{'':<62}shipped bias by current occupancy:")
        for lo, hi in zip(qs[:-1], qs[1:]):
            m = (kvnow >= lo) & (kvnow < hi)
            if m.sum() < 50:
                continue
            print(f"{'':<62}  {lo/1000:>6.0f}k-{hi/1000:>6.0f}k  n={m.sum():>6,d}  "
                  f"mean={err[m].mean():>10,.0f}  "
                  f"{100*err[m].mean()/max(actual[m].mean(),1):>6.1f}% of actual")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or ["results/*_full"]))
