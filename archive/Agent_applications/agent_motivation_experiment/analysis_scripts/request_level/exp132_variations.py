#!/usr/bin/env python3
"""EXP-132 -- three FluidServe variations against the EXP-109 control, one table.

Each treatment arm is the EXP-109 `fsv3capgnofrct75` configuration with exactly
one thing changed, so the table is a column of differences and nothing else. It
prints, per arm: the three denominators, the rejection rate, token goodput, the
per-class attainment, and the preemption count -- the last because two of the
three refutation conditions written before the run are stated in preemptions
(a projection that over-credits departures admits too much and then evicts).

WHY THE DIFFERENCE IS AGAINST A RANGE AND NOT A MEAN. The control has two
repeats and each treatment has one, so a difference against the control mean
hides that the control itself moved. The table prints the control's min and max
and the difference against each, and the verdict column applies the rule written
into the experiment file before the run: below 1.0 points is no difference,
1.0 to 3.0 is not judged, 3.0 and above is read as real.

  python3 exp132_variations.py
  python3 exp132_variations.py --control 'results/*exp109*fsv3capgnofrct75_shift' \
                               --runs 'results/*exp132r1_*'
"""
import argparse, glob, os, sys, collections

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

# THE SCORING RULE FOR swe IS PINNED HERE, BEFORE THE IMPORT, AND THAT IS NOT
# OPTIONAL.
#
# exp22_fluidserve reads its budgets into module-level constants at import time,
# so an environment variable set afterwards has no effect. Its default for swe is
# the END-TO-END form, 30 seconds. Every run this script reads is a `t75` run,
# produced under the PER-TOKEN form -- first token within 7 s and a mean of 75 ms
# per token thereafter -- which is what EXP-107 adopted and what the `t75` suffix
# in the arm name records.
#
# Running without this pin does not fail and does not warn: the default 30 s
# form scores, the table prints, and swe's numbers are wrong while chat's and
# deep research's are right. On 2026-09-14 that produced a per-class conclusion
# with the wrong SIGN -- swe appeared to gain 3.9 to 4.7 points when the class
# preference was removed, and under the correct rule it does not move at all.
# The difference is not small: swe reads 33.9 against 67.2 on the same run.
#
# The check below is the other half: if the caller pinned something else, stop
# rather than silently score one arm against one rule and the record against
# another.
_WANT = {"FS_SWE_TBT_MS": "75", "FS_SWE_TTFT_S": "7"}
for _k, _v in _WANT.items():
    _have = os.environ.get(_k)
    if _have is None:
        os.environ[_k] = _v
    elif _have != _v:
        sys.exit("%s=%s but every run here is a t75 run, which is scored with "
                 "%s=%s. Unset it or pass the right value." % (_k, _have, _k, _v))

import all_arrivals_attainment as A

EXCLUDED = os.path.join(REPO, "ms_dev", "notes", "excluded_runs.tsv")

# The one thing each arm changes, printed beside it so the table is readable
# without the experiment file open.
CHANGED = {
    "fsv3capgnofrct75": "(control) v0.3 + class-instance cap + explicit rejection",
    "orct75":     "per-request output-length oracle (length AND flux)",
    "noafft75":   "class preference off",
    "flatlent75": "one deterministic length of 532 tokens for all three classes",
    "nocaphour":      "class-instance cap off (EXP-135)",
    "noaffnocaphour": "class preference AND class-instance cap both off (EXP-138)",
}


def excluded_runs():
    """Runs that exist and are valid artifacts but must not enter an aggregate.

    Read from the file rather than narrowed out of the glob, because a glob that
    is narrow enough to miss one bad run is also narrow enough to miss a good
    run added later -- this repository has drawn a figure with n=3 and a 13.6
    point error bar that way.
    """
    out = set()
    if os.path.exists(EXCLUDED):
        for line in open(EXCLUDED):
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line.split("\t")[0])
    return out


def arm_of(d):
    """Arm name out of the directory name: <date>_<time>_<session>_<arm>_<variant>."""
    base = os.path.basename(d.rstrip("/"))
    for a in CHANGED:
        if "_%s_" % a in base:
            return a
    return "?"


def preemptions(d):
    """Preemptions over the condition, summed across the four engines.

    One file per engine port, one JSON object per scrape, series keyed
    "<name>|<labels>" at the TOP level of the object rather than under a
    "metrics" key. The key is split on the FIRST "|", never on the first "=" --
    a series that gains a label would otherwise be read as a different series
    from one run to the next, which this repository has already had happen once
    and which produces a wrong number rather than a missing one.

    vllm:num_preemptions_total is a counter, and the runner cold-restarts the
    fleet at the start of every condition, so the counter starts at zero for
    this run. It is still taken as max minus min per file rather than as the
    last value, so that a scrape which begins after a restart that did not
    happen cannot be charged the previous condition's preemptions.

    Returns None when the files are absent, which is not fatal: the
    request-level verdict does not depend on it.
    """
    import json
    files = sorted(glob.glob(os.path.join(d, "server_metrics", "engine_*.jsonl")))
    if not files:
        return None
    total = 0
    seen = False
    for f in files:
        lo = hi = None
        for line in open(f):
            try:
                rec = json.loads(line)
            except Exception:
                continue
            v = None
            for k, val in rec.items():
                if not isinstance(k, str):
                    continue
                if k.split("|", 1)[0] == "vllm:num_preemptions_total":
                    try:
                        v = float(val)
                    except (TypeError, ValueError):
                        v = None
                    break
            if v is None:
                continue
            seen = True
            lo = v if lo is None else min(lo, v)
            hi = v if hi is None else max(hi, v)
        if lo is not None:
            total += hi - lo
    return int(total) if seen else None


def per_class(d):
    """Per-class all-arrivals attainment, so a mechanism claim can be checked.

    Two of the three refutation conditions name a class: flatlent75 should hurt
    deepresearch most (its mean output is 984.8 tokens and the flat profile calls
    every class 532), and noafft75 should show up in whichever classes the
    preference was separating. A claim that names a class and is checked against
    the total is not checked.

    Computed from the same rows and the same met/cutoff definition that
    one_run uses, by grouping rather than by re-deriving, so the per-class
    numbers cannot drift from the total they decompose.
    """
    try:
        rows = A.load_run(d)
    except Exception:
        return None
    if rows is None or getattr(rows, "empty", True):
        return None
    if "class" not in rows.columns:
        return None
    met = (~rows["violate_offered"]) & (~rows["cutoff"])
    out = {}
    for cls, idx in rows.groupby("class").groups.items():
        sub = met.loc[idx]
        if len(sub):
            out[str(cls)] = 100.0 * sub.sum() / len(sub)
    return out or None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--control", default="results/*exp109*fsv3capgnofrct75_shift")
    ap.add_argument("--runs", default="results/*exp132r1_*")
    a = ap.parse_args()

    skip = excluded_runs()
    rows = collections.defaultdict(list)
    dropped = []
    for pat in (a.control, a.runs):
        for d in sorted(glob.glob(pat)):
            base = os.path.basename(d.rstrip("/"))
            if "PRERUN" in base:
                continue
            if base in skip:
                dropped.append((base, "excluded_runs.tsv"))
                continue
            r = A.one_run(d)
            if r is None:
                dropped.append((base, "one_run returned nothing -- header-only metrics.csv?"))
                continue
            r["_dir"] = d
            r["_pre"] = preemptions(d)
            # Per 1,000 completions as well as the raw count: two runs that
            # completed different numbers of requests cannot be compared on the
            # raw count, and the arms here differ in rejection rate by up to
            # 2.4 points.
            r["_pre_k"] = (1000.0 * r["_pre"] / r["met_n"]
                           if r["_pre"] is not None and r.get("met_n") else None)
            r["_cls"] = per_class(d)
            rows[arm_of(d)].append(r)

    if dropped:
        print("skipped:")
        for b, why in dropped:
            print("  %-52s %s" % (b, why))
        print()

    ctl = rows.get("fsv3capgnofrct75", [])
    if not ctl:
        sys.exit("no control runs matched %r -- refusing to print differences "
                 "against nothing" % a.control)
    lo = min(r["all_arrivals"] for r in ctl)
    hi = max(r["all_arrivals"] for r in ctl)

    import exp22_fluidserve as _E
    print("scoring rule in force: %s" % _E.SLO_RULES)
    print("control: %d repeats, all arrivals %.1f..%.1f" % (len(ctl), lo, hi))
    print("verdict rule (written before the run): |d| < 1.0 no difference, "
          "1.0-3.0 not judged, >= 3.0 real\n")

    hdr = ("%-18s %8s %9s %9s %8s %10s %8s %8s   %s"
           % ("arm", "allarr", "admitted", "offered", "rej%", "goodput",
              "preempt", "/1k met", "d vs control"))
    print(hdr)
    print("-" * len(hdr))
    for arm in ("fsv3capgnofrct75", "orct75", "noafft75", "flatlent75",
                "nocaphour", "noaffnocaphour"):
        for r in rows.get(arm, []):
            aa = r["all_arrivals"]
            if arm == "fsv3capgnofrct75":
                delta = ""
            else:
                d_lo, d_hi = aa - hi, aa - lo          # against the better and the worse control
                # The verdict is taken over the WHOLE interval, not over its
                # nearest end. Taking the nearest end reads a difference of
                # 0.9 against one control repeat and 1.5 against the other as
                # "no difference", which is the reading that flatters whichever
                # conclusion the reader already holds. A range that crosses a
                # threshold is reported as crossing it.
                near, far = min(abs(d_lo), abs(d_hi)), max(abs(d_lo), abs(d_hi))
                def band(x):
                    return 0 if x < 1.0 else (1 if x < 3.0 else 2)
                names = ["no difference", "NOT JUDGED", "REAL"]
                tag = (names[band(near)] if band(near) == band(far)
                       else "STRADDLES %s/%s" % (names[band(near)], names[band(far)]))
                delta = "%+.1f..%+.1f  %s" % (d_lo, d_hi, tag)
            print("%-18s %8.1f %9.1f %9.1f %8.1f %10.0f %8s %8s   %s"
                  % (arm, aa, r["admitted"], r["offered"], r["rejected_pct"],
                     r["goodput_tok_s"],
                     r["_pre"] if r["_pre"] is not None else "-",
                     "%.1f" % r["_pre_k"] if r.get("_pre_k") is not None else "-",
                     delta))
        if rows.get(arm):
            print("   %s" % CHANGED[arm])

    # Per-class, only when the loader gave it -- a mechanism claim that names a
    # class has to be checked against that class, not against the total.
    have = [(arm, r) for arm in rows for r in rows[arm] if r.get("_cls")]
    if have:
        print("\nper-class all arrivals")
        classes = sorted({c for _, r in have for c in r["_cls"]})
        print("%-18s %s" % ("arm", "".join("%14s" % c for c in classes)))
        for arm, r in have:
            print("%-18s %s" % (arm, "".join("%14.1f" % r["_cls"].get(c, float("nan"))
                                             for c in classes)))
    else:
        print("\nper-class not available from this loader -- use exp22_fluidserve.py "
              "for the class breakdown")


if __name__ == "__main__":
    main()
