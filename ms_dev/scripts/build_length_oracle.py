#!/usr/bin/env python3
"""Build the per-request output-length table EXP-64 feeds back to the workload.

    python3 ms_dev/scripts/build_length_oracle.py <run_dir> [<run_dir> ...] --out FILE
    python3 ms_dev/scripts/build_length_oracle.py <run_dir> ... --coverage-against <run_dir>

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
without that filter is what makes a run look full of duplicate keys -- it made
this script reject a healthy static condition on its first try.

The uniqueness check below stays anyway. It costs nothing and it is the thing
that would catch a workload change that made task_id ambiguous, which would
otherwise emit a table silently mapping many requests onto one length.

COVERAGE IS PART OF THE RESULT. A request the workload cannot find in the table
falls back to the class distribution, which is the control's behaviour, so a
sparse table quietly turns the treatment arm back into its control. The fraction
covered is printed here and has to be checked per condition; EXP-64 sets the bar
at 95%.

WHY SEVERAL RUNS ARE UNIONED. A single eight-minute run at 35 req/s covers only
about 95% of the next run's requests, because the requests it refused produced
no tokens and so contribute no entry, and refusal is exactly the regime the
experiment is about. Unioning every run of that rate raises coverage without
weakening the table, because the output length was measured not to depend on
which policy produced it: pairing arms at the same rate gives 94.1 to 95.0%
identical lengths, against 95.9% between two repeats of one arm. What it must
NOT be unioned across is the arrival rate -- a table built at 10 req/s covers
only 28.8% of a 35 req/s run and agrees on length 62% of the time, because the
output length depends on the load the request was served under.

Where a key appears in more than one run the entries disagree sometimes, and the
median of the observations is taken. The disagreement rate is printed, and it is
the same quantity as the oracle's accuracy: it is how far from exact a perfect
predictor of this workload can be.
"""
import argparse
import json
import os
import sys

import pandas as pd


def class_of(task_id):
    """Workload class from the task_id prefix.

    Same rule as `plot_per_engine_attainment.class_of`, which is the canonical
    one; metrics.csv carries no class column, so every analysis derives it here.
    """
    t = str(task_id)
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def usable_rows(run_dir):
    """The request rows of one run that carry a trustworthy output length.

    Returns (rows, n_request_rows, n_truncated), where `rows` carries a `key`
    column holding the `task_id|call_index` string the workload looks itself up
    under.
    """
    src = os.path.join(run_dir, "metrics.csv")
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

    # A request that was cut off mid-stream DID produce tokens, and the count
    # recorded for it is a lower bound rather than its length. Those entries are
    # worse than missing ones: a missing entry sends no hint and the scheduler
    # falls back to the class distribution, while a truncated one is a wrong
    # number the policy acts on. Measured at 35 req/s they are 4.9% of the
    # candidates and their median is 343 tokens against 449 for the completed
    # ones, so they are systematically short and would tell the policy that
    # requests finish sooner than they do.
    #
    # Excluded, therefore, along with errors and timeouts, which truncate for the
    # same reason. Rejected requests need no filter: they produced nothing and
    # the positive-output test already drops them.
    truncated = pd.Series(False, index=df.index)
    for col in ("is_server_terminated", "is_error", "is_timeout", "is_job_timeout"):
        if col in df.columns:
            v = df[col]
            truncated |= v.where(v.notna(), False).infer_objects(copy=False).astype(bool)
    n_trunc = int((truncated & (df["output_tokens"].fillna(0) > 0)).sum())

    rows = df[(df["output_tokens"].fillna(0) > 0) & ~truncated].copy()
    rows["call_index"] = rows["call_index"].astype(int)

    dup = rows.duplicated(subset=["task_id", "call_index"]).sum()
    if dup:
        sys.exit(
            f"{run_dir}: {dup} of {len(rows)} rows share a (task_id, call_index), "
            f"so the key does not identify a request in this run. That is the "
            f"hour-long trace's shape, where tasks are replayed hundreds of times, "
            f"and a table built from it would map many different requests onto one "
            f"length. Check whether the workload changed what task_id means.")

    rows["key"] = rows["task_id"].astype(str) + "|" + rows["call_index"].astype(str)
    rows["class"] = rows["task_id"].map(class_of)
    return rows, len(df), n_trunc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", nargs="+",
                    help="runs to union. All must be at the same arrival rate; "
                         "the output length depends on load, so mixing rates "
                         "produces a table that is wrong where it is not sparse")
    ap.add_argument("--out", default=None,
                    help="default <first run_dir>/analysis/length_oracle.json")
    ap.add_argument("--coverage-against", default=None, metavar="RUN_DIR",
                    help="report what fraction of this run's requests would find "
                         "an entry, without adding it to the table")
    a = ap.parse_args()

    per_run = []
    frames = []
    for d in a.run_dirs:
        rows, n_rows, n_trunc = usable_rows(d)
        per_run.append((d, n_rows, n_trunc, len(rows)))
        frames.append(rows[["key", "output_tokens", "class"]])
    allrows = pd.concat(frames, ignore_index=True)

    g = allrows.groupby("key")["output_tokens"]
    lengths = g.median().round().astype(int)
    seen = g.size()
    spread = (g.max() - g.min())
    repeated = int((seen > 1).sum())
    disagreed = int(((seen > 1) & (spread > 0)).sum())

    table = lengths.to_dict()

    out = a.out or os.path.join(a.run_dirs[0], "analysis", "length_oracle.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as fh:
        json.dump({"source_runs": [os.path.basename(os.path.abspath(d))
                                   for d in a.run_dirs],
                   "entries": len(table),
                   "table": {k: int(v) for k, v in table.items()}}, fh)

    print(f"runs unioned  {len(per_run)}")
    for d, n_rows, n_trunc, n_use in per_run:
        print(f"  {os.path.basename(os.path.abspath(d)):<48} "
              f"requests {n_rows:>7,}  usable {n_use:>7,}  "
              f"truncated excluded {n_trunc:>5,}")
    print(f"entries       {len(table):,} distinct requests")
    if repeated:
        # This is "all of the observations of that key agree", not the pairwise
        # agreement between two runs, and the two are very different numbers
        # once several runs are unioned: with N observations per key the chance
        # that all N coincide falls as N grows even when every pair agrees 94%
        # of the time. Do not quote it as the oracle's accuracy. The accuracy is
        # what --coverage-against reports against a run that was held out.
        print(f"repeated      {repeated:,} keys appear in more than one run "
              f"(median {int(seen[seen > 1].median())} observations each); "
              f"{disagreed:,} of them ({100.0*disagreed/repeated:.1f}%) have at "
              f"least two observations that differ, and the median is taken "
              f"there. This is not the pairwise agreement and must not be "
              f"quoted as the oracle's accuracy")
    ln = pd.Series(list(table.values()))
    print(f"lengths       p50 {ln.quantile(.5):.0f}  p90 {ln.quantile(.9):.0f}  "
          f"max {ln.max():.0f}")
    if "class" in allrows.columns:
        cls = allrows.drop_duplicates("key").set_index("key")["class"]
        for c, keys in cls.groupby(cls):
            sub = pd.Series([table[k] for k in keys.index])
            print(f"  {c:<14} n={len(sub):>6}  p50 {sub.quantile(.5):>6.0f}"
                  f"  p90 {sub.quantile(.9):>6.0f}")
    print(f"wrote         {out}")

    if a.coverage_against:
        rows, n_rows, n_trunc = usable_rows(a.coverage_against)
        # Coverage is asked of every request the workload will SEND, which is
        # every arrival, not only the ones that produced tokens. A request that
        # is going to be refused still consults the table when it is built, and
        # a refused request in the source run is exactly the one missing from
        # it, so measuring against the completed requests alone would report a
        # coverage the treatment arm never sees.
        df = pd.read_csv(os.path.join(a.coverage_against, "metrics.csv"),
                         low_memory=False)
        if "agent" in df.columns:
            df = df[df["agent"] == "request"]
        df = df.copy()
        df["key"] = (df["task_id"].astype(str) + "|" +
                     df["call_index"].astype(int).astype(str))
        df["class"] = df["task_id"].map(class_of)
        hit = df["key"].isin(table)
        print(f"\ncoverage against {os.path.basename(os.path.abspath(a.coverage_against))}")
        print(f"  all arrivals   {hit.sum():,} of {len(df):,} "
              f"({100.0*hit.mean():.1f}%) find an entry")
        if "class" in df.columns:
            for c, gg in df.groupby("class"):
                print(f"    {c:<14} {gg['key'].isin(table).mean()*100:>5.1f}%  "
                      f"(n={len(gg):,})")
        done = df[df["output_tokens"].fillna(0) > 0]
        if len(done):
            print(f"  produced output {done['key'].isin(table).mean()*100:.1f}% "
                  f"(n={len(done):,}) -- the rest were refused or never started, "
                  f"and they are what a table built from runs of the same "
                  f"condition cannot contain")

        # How wrong the table is where it does answer. This is the oracle's
        # accuracy, and it is what bounds the experiment: a hint that is off by
        # a few tokens is still a far better length estimate than the class
        # distribution, so the bound stays an upper bound, but it is not exact
        # and the experiment file has to say by how much.
        truth = rows.set_index("key")["output_tokens"]
        common = [k for k in truth.index if k in table]
        if common:
            t = truth.loc[common].astype(float)
            p = pd.Series([table[k] for k in common], index=common, dtype=float)
            d = (t - p).abs()
            print(f"  accuracy on the {len(common):,} completed requests the "
                  f"table answers for: exact {100.0*(d == 0).mean():.1f}%, "
                  f"median absolute error {d.median():.0f} tokens, "
                  f"p90 {d.quantile(.9):.0f}, "
                  f"relative median {100.0*(d/t.clip(lower=1)).median():.1f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
