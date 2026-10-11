#!/usr/bin/env python3
"""EXP-133 -- the cumulative ablation staircase, and the redundancy it exposes.

Each arm removes one more mechanism than the arm above it, so the STEP between
two rows is that mechanism's contribution with its redundancy removed. The
leave-one-out value from EXP-131 is printed beside it, and the difference
between the two columns is how much of that mechanism another mechanism was
covering for.

WHY BOTH COLUMNS. Leave-one-out asks "what happens if only this is gone", which
is zero whenever something else achieves the same protection. The cumulative
step asks "what happens if this goes and everything above it is already gone".
EXP-132 measured a concrete case: with the class preference off, the feasibility
test still leaves 1.8 of four instances free of chat against 2.1 with it on, so
86% of that separation survives the removal and leave-one-out reads nearly zero.

  python3 exp133_staircase.py
  python3 exp133_staircase.py --runs 'results/*exp133r[12]_*'
"""
import argparse, glob, os, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, HERE)

# The scoring form, pinned before the import because the budgets are read into
# constants at import time and the default is the end-to-end form these runs
# were not produced under. load_run also refuses a mismatch now, but pinning it
# here means the script runs rather than stopping.
for _k, _v in (("FS_SWE_TBT_MS", "75"), ("FS_SWE_TTFT_S", "7")):
    os.environ.setdefault(_k, _v)

import all_arrivals_attainment as A

EXCLUDED = os.path.join(REPO, "ms_dev", "notes", "excluded_runs.tsv")

# In order. The label is what the arm removed ON TOP of the arm above it.
STEPS = [
    ("cum0", "(control) deployed configuration"),
    ("cum1", "- class preference"),
    ("cum2", "- deferral (pend)"),
    ("cum3", "- per-class instance cap"),
    ("cum4", "- KV flux projection"),
    ("cum5", "- future-decode charge (horizon 1)"),
]

# EXP-131's leave-one-out losses at the same cell, for the redundancy column.
# Stated as the range against the two control repeats. None where EXP-131 did
# not measure that element on its own.
LEAVE_ONE_OUT = {
    "cum1": (3.3, 5.6),      # fsv3t75noaff, 3 repeats
    "cum2": (0.0, 0.0),      # fsv3t75nopend, 2 repeats: 74.1/75.6 vs 73.6/75.5
    "cum3": None,            # the cap alone was measured in EXP-107, not here
    "cum4": (-7.7, -5.5),    # fsv3t75noflux: 81.3/81.0, i.e. BETTER without
    "cum5": None,            # h=1 in EXP-131 had the projection still on
}

# EXP-108's control at this cell, which cum0 has to reproduce.
CONTROL_REF = (73.6, 75.5)


def excluded_runs():
    out = set()
    if os.path.exists(EXCLUDED):
        for line in open(EXCLUDED):
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line.split("\t")[0])
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="results/*exp133r[12]_*")
    a = ap.parse_args()

    skip = excluded_runs()
    by = collections.defaultdict(list)
    for d in sorted(glob.glob(a.runs)):
        base = os.path.basename(d.rstrip("/"))
        if "PRERUN" in base or base in skip:
            continue
        for arm, _ in STEPS:
            if "_%s_" % arm in base:
                r = A.one_run(d)
                if r is not None:
                    by[arm].append(r)
                break

    if not by:
        sys.exit("no runs matched %r" % a.runs)

    def cell(arm, key="all_arrivals"):
        v = [r[key] for r in by.get(arm, [])]
        return (min(v), max(v), len(v)) if v else None

    c0 = cell("cum0")
    print("in-session control cum0: %s" % (
        "%.1f..%.1f (n=%d)" % c0 if c0 else "NOT RUN YET"))
    if c0:
        ok = (c0[0] >= CONTROL_REF[0] - 1.0) and (c0[1] <= CONTROL_REF[1] + 1.0)
        print("  EXP-108 reference %.1f..%.1f -> %s" % (
            CONTROL_REF[0], CONTROL_REF[1],
            "reproduced" if ok else "DOES NOT REPRODUCE -- do not read the rows below"))
    print()

    hdr = "%-6s %-34s %3s %14s %10s %9s %9s %10s   %s" % (
        "arm", "removed (cumulative)", "n", "all arrivals", "step", "admitted",
        "rej%", "goodput", "leave-one-out (EXP-131)")
    print(hdr)
    print("-" * len(hdr))
    prev = None
    for arm, label in STEPS:
        c = cell(arm)
        if c is None:
            print("%-6s %-34s %3s %14s" % (arm, label, "-", "not run yet"))
            continue
        lo, hi, n = c
        ad = cell(arm, "admitted")
        rj = cell(arm, "rejected_pct")
        gp = cell(arm, "goodput_tok_s")
        # The step is against the arm above it, as a range over the two ends.
        if prev is None:
            step = ""
        else:
            d_lo, d_hi = lo - prev[1], hi - prev[0]
            step = "%+.1f..%+.1f" % (d_lo, d_hi)
        l1 = LEAVE_ONE_OUT.get(arm)
        l1s = ("-%.1f..-%.1f" % (l1[1], l1[0]) if l1 and l1[1] > 0 else
               "+%.1f..+%.1f" % (-l1[1], -l1[0]) if l1 else "not measured alone")
        print("%-6s %-34s %3d %14s %10s %9s %9s %10s   %s" % (
            arm, label, n, "%.1f..%.1f" % (lo, hi), step,
            "%.1f..%.1f" % (ad[0], ad[1]) if ad else "-",
            "%.1f..%.1f" % (rj[0], rj[1]) if rj else "-",
            "%.0f..%.0f" % (gp[0], gp[1]) if gp else "-",
            l1s))
        prev = (lo, hi)

    # Total drop, which is what has to be compared against the gap to llm-d --
    # and the comparison is only legitimate on the same workload, so the caveat
    # is printed rather than left to be remembered.
    c5 = cell("cum5")
    if c0 and c5:
        print("\ncum0 -> cum5 total: %+.1f..%+.1f points" % (
            c5[0] - c0[1], c5[1] - c0[0]))
        print("  These are STATIC 35 req/s. llm-d's 47.7/54.0 is the HOUR trace; "
              "do not subtract one from the other.")


if __name__ == "__main__":
    main()
