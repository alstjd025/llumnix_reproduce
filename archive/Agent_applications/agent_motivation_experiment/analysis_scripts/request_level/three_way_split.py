#!/usr/bin/env python3
"""What happened to the arrivals, split three ways: met / admitted-but-missed / rejected.

`motivation.md` section 2.3 defines this split and nothing computes it, so the
paper's metric list does not carry it. It is the metric that answers "is
rejecting bad?" directly, because a policy that rejects is trading the third
column against the second and no other metric shows the trade:

  met                      arrived, was accepted, finished inside its own rule.
                           Identical to offered attainment -- same numerator,
                           same denominator -- so this table always reconciles
                           with the attainment reported everywhere else.
  admitted but missed      arrived, was accepted, did not finish inside its rule.
                           The system spent engine time and delivered a
                           violation.
  rejected                 arrived and was refused. Counted as a violation on the
                           offered denominator, but distinguishable from the
                           previous column, which is the point.

THE DENOMINATOR IS THE ONE `attain()` USES. Run-boundary cutoffs -- requests
still in flight when the run ended -- have an unknown outcome and are dropped
from both denominators. They have to be dropped here too or the three columns
would not add to the attainment reported elsewhere.

The share dropped is printed as a fourth number and it is not a footnote. It is
0.3 to 2.7% for every arm that rejects or holds, and 35.9% for the static
partition at 45 req/s, because that arm accepts everything and builds a queue, so
the requests still unfinished at the end are precisely the slow ones. Dropping
them flatters that arm. Read the fourth column before comparing the first three.

  python3 three_way_split.py --runs 'results/*exp71*_full*' [--label hour]
  python3 three_way_split.py --runs 'results/*exp72r1_polyserve_m1_rpm_*'
"""
import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run  # noqa: E402


def split(run):
    r = load_run(run)
    if r is None or r.empty:
        return None
    n_all = len(r)
    cut = 100.0 * r["cutoff"].mean()
    r = r[~r["cutoff"]]
    if r.empty:
        return None
    rej = r["rejected"]
    served = r[~rej]
    met = int((~served["violate_served"]).sum())
    n = len(r)
    return dict(met=100.0 * met / n,
                missed=100.0 * (len(served) - met) / n,
                rej=100.0 * int(rej.sum()) / n,
                cutoff=cut, arrivals=n_all)


def main(pattern, out_csv):
    rows = []
    for d in sorted(glob.glob(pattern)):
        v = split(d)
        if not v:
            continue
        b = os.path.basename(d)
        # The arm is the token between the session prefix and either the mix
        # key or `full`. A directory name is the only place it is recorded, and
        # the two run shapes spell it differently -- `..._fspfx_m1_rpm_2700` and
        # `..._fspfx_fullb` -- so both forms are matched here rather than the
        # static one only. An unmatched name keeps the directory as its own arm
        # and shows up as a row of its own, which is loud rather than silent.
        m = re.search(r"_exp\d+\w*?_([a-z][a-z0-9-]*)_(?:m\d+f?_rpm_\d+|full)", b)
        arm = m.group(1) if m else b
        rate = re.search(r"_rpm_(\d+)$", b)
        rows.append(dict(run=b, arm=arm,
                         rate=(int(rate.group(1)) / 60.0 if rate else np.nan),
                         **v))
    if not rows:
        sys.exit(f"no runs matched {pattern}")
    df = pd.DataFrame(rows)
    g = df.groupby(["arm", "rate"], dropna=False).mean(numeric_only=True).reset_index()
    print(f"{len(df)} runs\n")
    print(f"{'arm':<14}{'req/s':>7}{'met':>8}{'missed':>9}{'rejected':>10}"
          f"{'cutoff':>9}{'n':>8}")
    for _, x in g.sort_values(["arm", "rate"]).iterrows():
        rate = "hour" if np.isnan(x.rate) else f"{x.rate:.0f}"
        print(f"{x.arm:<14}{rate:>7}{x.met:8.1f}{x.missed:9.1f}{x.rej:10.1f}"
              f"{x.cutoff:9.1f}{int(x.arrivals):8d}")
    if out_csv:
        os.makedirs(os.path.dirname(out_csv), exist_ok=True)
        df.to_csv(out_csv, index=False)
        print(f"\nwrote {out_csv}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", required=True)
    ap.add_argument("--out-csv", default="")
    a = ap.parse_args()
    main(a.runs, a.out_csv)
