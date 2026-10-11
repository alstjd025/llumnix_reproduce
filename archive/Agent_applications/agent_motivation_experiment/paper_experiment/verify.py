#!/usr/bin/env python3
"""Check that a pinned result still says what its manifest says it says.

    python3 paper_experiment/verify.py static_sweep_2026-08

Three things can drift between a manifest being written and a paper being
submitted, and this fails on each of them separately so the message names which:

  the run set        a run listed in the manifest was deleted, or -- the case
                     that has actually happened here -- a NEW run landed that
                     the original glob would now also match, so the figure and
                     the manifest have quietly stopped describing the same
                     measurement. The manifest cannot see that by itself, so
                     --glob re-resolves the selection and compares.
  the inputs         metrics.csv changed under a run. Re-scoring a run is
                     normal; the FILE changing is not, and a hash catches a
                     partially rewritten or re-merged file.
  the numbers        the scoring changed. load_run has been corrected twice --
                     per-token time was 1/1.92 of the truth until
                     implementation.md section 32, and rejections sat in the
                     wrong denominator until 2026-07-28 -- and each time every
                     recorded figure silently became a different quantity. Here
                     the table is recomputed and diffed, so a correction shows
                     up as a list of changed cells rather than as nothing.

Exit status is non-zero if any check fails, so this can go in front of a figure
regeneration rather than being something to remember to run.
"""
import argparse
import csv
import glob as globmod
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--glob", action="append", default=[], metavar="GLOB",
                    help="re-resolve this pattern and report runs that match it "
                         "but are absent from the manifest. Give the same "
                         "patterns build_manifest.py was given")
    ap.add_argument("--tolerance", type=float, default=0.05,
                    help="attainment points a recomputed cell may move before it "
                         "is reported (default 0.05)")
    a = ap.parse_args()

    outdir = os.path.join(HERE, a.name)
    man = os.path.join(outdir, "manifest.tsv")
    if not os.path.exists(man):
        sys.exit(f"{man} does not exist")
    rows = list(csv.DictReader(open(man), delimiter="\t"))
    print(f"manifest      {len(rows)} runs")

    bad = 0

    # 1. the run set
    missing = [r for r in rows
               if not os.path.isdir(os.path.join(ROOT, "results", r["run"]))]
    if missing:
        bad += 1
        print(f"MISSING       {len(missing)} runs are no longer in results/")
        for r in missing[:5]:
            print(f"  {r['run']}")
        print("  the archived metrics.csv under data/ is what remains of them")

    if a.glob:
        listed = {r["run"] for r in rows}
        found = set()
        for p in a.glob:
            for d in globmod.glob(os.path.join(ROOT, p)):
                if os.path.isdir(d) and "PRERUN" not in d:
                    found.add(os.path.basename(d))
        extra = sorted(found - listed)
        if extra:
            bad += 1
            print(f"NEW           {len(extra)} runs now match the selection but "
                  f"are not in the manifest")
            for b in extra[:8]:
                print(f"  {b}")
            print("  a figure drawn from the glob and this manifest are now two "
                  "different measurements; rebuild or exclude deliberately")

    # 2. the inputs
    changed = []
    for r in rows:
        live = os.path.join(ROOT, "results", r["run"], "metrics.csv")
        arch = os.path.join(outdir, "data", r["run"], "metrics.csv")
        if os.path.exists(live) and r["metrics_sha256"] not in ("", "MISSING"):
            if sha256(live) != r["metrics_sha256"]:
                changed.append((r["run"], "live copy differs from the manifest hash"))
        if os.path.exists(arch) and r["metrics_sha256"] not in ("", "MISSING"):
            if sha256(arch) != r["metrics_sha256"]:
                changed.append((r["run"], "ARCHIVED copy differs from the manifest hash"))
    if changed:
        bad += 1
        print(f"INPUT CHANGED {len(changed)}")
        for run, why in changed[:8]:
            print(f"  {run}  --  {why}")

    # 3. the numbers
    from all_arrivals_attainment import one_run  # noqa: E402
    tab = os.path.join(outdir, "table.csv")
    if os.path.exists(tab):
        old = {r["run"]: r for r in csv.DictReader(open(tab))}
        moved = []
        for r in rows:
            d = os.path.join(ROOT, "results", r["run"])
            if not os.path.isdir(d) or r["run"] not in old:
                continue
            s = one_run(d)
            if s is None:
                continue
            for k in ("offered", "admitted", "all_arrivals"):
                was = float(old[r["run"]][k])
                if abs(s[k] - was) > a.tolerance:
                    moved.append((r["run"], k, was, s[k]))
        if moved:
            bad += 1
            print(f"SCORE MOVED   {len(moved)} cells changed by more than "
                  f"{a.tolerance} points")
            for run, k, was, now in moved[:10]:
                print(f"  {run:<45} {k:<13} {was:6.2f} -> {now:6.2f}")
            print("  the scoring changed, not the data. Re-read what changed in "
                  "load_run before regenerating anything")
        else:
            print(f"scores        every cell reproduces within {a.tolerance} points")

    if bad:
        print(f"\n{bad} check(s) failed")
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
