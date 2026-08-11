#!/usr/bin/env python3
"""Build the per-request output-length table EXP-64 feeds back to the workload.

    python3 ms_dev/scripts/build_length_oracle.py <run_dir> [--out FILE]

The table maps `(task_id, call_index)` to the number of output tokens that same
request produced in an earlier run of the same condition. The workload writes it
into the OpenAI `user` field as `len:<n>`, the gateway parses it, and with
--fluidserve-oracle-length the scheduler uses it in place of the class's length
distribution.

WHAT THIS IS AND IS NOT. It is not an oracle in the strict sense: the same input
does not always produce the same output length, because continuous batching
changes the reduction order between runs and flips near-ties. Measured on the
current workload by pairing EXP-73's two fspfx repeats, the output length is
identical 92.1% of the time at 25 req/s and 94.0% at 35, with a median absolute
difference of zero in every class and a p90 of 0 for chat and 27 to 31 for deep
research. So it is about as accurate as any predictor could plausibly be, which
is exactly what makes it an upper bound on what finer-grained prediction is
worth. The number to quote is the one printed by this script for the run it was
built from, not a remembered one -- an earlier draft of the experiment carried
65.7%, measured before the 2026-08-08 workload change.

WHY THE KEY IS (task_id, call_index), and a correction to what EXP-64 assumed.
That pair identifies a request uniquely in metrics.csv -- in the eight-minute
static conditions AND on the hour-long trace -- because task_id carries the
replay index, as in `astropy__astropy-12907__replay02__call03__r01`.

The experiment file said the pair is not unique on the hour trace, citing 95,558
of 106,116 rows sharing it. That figure is real but it belongs to
`request_engine.csv`, the join of the client's records with the scheduler's
dispatch log, and not to metrics.csv. Checked on 2026-08-11: filtering to the
`request` rows leaves zero duplicates in both shapes. Every row appears twice in
metrics.csv, once as `request` and once as `job_summary`, and reading the file
without that filter is what makes a run look full of duplicates -- it made this
script reject a healthy static condition on its first try.

The uniqueness check below stays anyway. It costs nothing and it is the thing
that would catch a workload change that made task_id ambiguous, which would
otherwise emit a table silently mapping many requests onto one length.

COVERAGE IS PART OF THE RESULT. A request the workload cannot find in the table
falls back to the class distribution, which is the control's behaviour, so a
sparse table quietly turns the treatment arm back into its control. The fraction
covered is printed here and has to be checked per condition; EXP-64 sets the bar
at 95%.
"""
import argparse
import json
import os
import sys

import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--out", default=None,
                    help="default <run_dir>/analysis/length_oracle.json")
    a = ap.parse_args()

    src = os.path.join(a.run_dir, "metrics.csv")
    if not os.path.exists(src):
        sys.exit(f"{src} does not exist")
    df = pd.read_csv(src, low_memory=False)

    need = {"task_id", "call_index", "output_tokens"}
    missing = need - set(df.columns)
    if missing:
        sys.exit(f"{src} has no {sorted(missing)}")

    # metrics.csv holds two rows per request, `request` and `job_summary`. Only
    # the first is a request; taking both is what made this script report every
    # healthy run as full of duplicate keys.
    if "agent" in df.columns:
        df = df[df["agent"] == "request"]
    rows = df[df["output_tokens"].fillna(0) > 0].copy()
    rows["call_index"] = rows["call_index"].astype(int)

    dup = rows.duplicated(subset=["task_id", "call_index"]).sum()
    if dup:
        sys.exit(
            f"{dup} of {len(rows)} rows share a (task_id, call_index), so the key "
            f"does not identify a request in this run. That is the hour-long "
            f"trace's shape, where tasks are replayed hundreds of times, and a "
            f"table built from it would map many different requests onto one "
            f"length. Check whether the workload changed what task_id means.")

    table = {f"{t}|{c}": int(n) for t, c, n in
             zip(rows["task_id"], rows["call_index"], rows["output_tokens"])}

    out = a.out or os.path.join(a.run_dir, "analysis", "length_oracle.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump({"source_run": os.path.basename(os.path.abspath(a.run_dir)),
                   "entries": len(table),
                   "table": table}, fh)

    total = len(df)
    print(f"source        {a.run_dir}")
    print(f"rows          {total:,} in metrics.csv")
    print(f"entries       {len(table):,} with a positive output length "
          f"({100.0*len(table)/max(1,total):.1f}% of rows)")
    print(f"lengths       p50 {rows['output_tokens'].quantile(.5):.0f}  "
          f"p90 {rows['output_tokens'].quantile(.9):.0f}  "
          f"max {rows['output_tokens'].max():.0f}")
    if "class" in rows.columns:
        for c, g in rows.groupby("class"):
            print(f"  {c:<14} n={len(g):>6}  p50 {g['output_tokens'].quantile(.5):>6.0f}"
                  f"  p90 {g['output_tokens'].quantile(.9):>6.0f}")
    print(f"wrote         {out}")
    print("\nThe fraction of the NEXT run's requests that find an entry here is "
          "the coverage EXP-64 requires to be above 95%; a request that misses "
          "falls back to the class distribution, which is the control's "
          "behaviour, so a sparse table turns the treatment arm into its control.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
