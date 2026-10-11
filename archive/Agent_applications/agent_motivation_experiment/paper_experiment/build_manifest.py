#!/usr/bin/env python3
"""Pin the runs behind a paper result, so the result stops depending on a glob.

    python3 paper_experiment/build_manifest.py <result-name> \
        --arm "FluidServe=results/*exp68s*_fspfx_m1_rpm_*,results/*exp80r2_fspfx_m1_rpm_*" \
        --arm "llm-d=results/*exp70*_llmdslo_m1f_rpm_*" \
        --since 260807_1900

WHY THIS EXISTS. Every figure and table in this project selects its runs with a
glob, and every one of the selection failures this repository has recorded is the
same shape: the glob kept meaning what it meant while the directory underneath it
changed.

  - EXP-57 re-measured PolyServe after a profile fix; the old EXP-53 runs still
    matched `results/*exp53*_rpm_*` and would have been averaged into the new
    ones, drawing a policy that never ran.
  - EXP-68 added the `fspfx` arm; three figure scripts built their arm list as
    `[k for k in TABLE if k in data]`, so the new arm was absent and the figures
    came out plausible and wrong.
  - EXP-80, on 2026-08-13, counted the post-fix runs with a `>= 260808` cut. The
    workload fix landed 2026-08-08 11:00 KST and directories are stamped in the
    runner pod's UTC-7, sixteen hours behind, so the boundary is 260807_1900 and
    the cut discarded six valid llm-d conditions. The plan built on that count
    was wrong until the numbers were checked by hand.

A glob is a promise about the future. A manifest is a record of the past. This
writes the resolved list, and `verify.py` re-resolves it and fails when the two
disagree -- which is the only way a silent change becomes a loud one.

WHAT IS COPIED AND WHAT IS NOT. `metrics.csv` is 4.8 MB of a 657 MB run
directory; `tbt_events.jsonl` is 568 MB of it and is read by two ITL CDF figures
only, since per-token time has been derived from metrics.csv (implementation.md
section 32). So the scored input is archived and the event stream is not: 0.4 GB
for a 55-run sweep against 36 GB for whole directories. That matters because
`results/` reached 1.5 TB once and had to be cut back, and a paper result should
not depend on a directory that is a disk-pressure incident away from being
deleted.

WHAT IS RECORDED PER RUN comes from files the runner already writes -- nothing is
retyped, because a number copied by hand into a second place is this project's
most frequent error:

  run_config.json                       model, arrival rate, duration, seed,
                                        and the whole workload config including
                                        the SLO block and the class mix
  results/exp07_meta/scheduler_deploy_<session>.yaml
                                        the deployed scheduler spec, i.e. every
                                        policy flag that was in force
"""
import argparse
import csv
import glob as globmod
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

DIRNAME = re.compile(r"^(?P<stamp>\d{6}_\d{4})_(?P<session>[^_]+)_(?P<arm>[^_]+)_"
                     r"(?P<mix>m1f?|m[2-5]f?)(?:_rpm_(?P<rpm>\d+))?$")

# The load-generator fix. Runs on either side of it are different workloads and
# must not enter one table; STATUS.md carries the full account.
WORKLOAD_BOUNDARY = "260807_1900"
WORKLOAD_NOTE = ("2026-08-08 11:00 KST, per-worker dataset split fixed and the "
                 "chat pool raised 1,000 -> 10,000 conversations; directories "
                 "are stamped UTC-7 so the boundary reads 260807_1900")


def kst(stamp):
    """Directory stamps are the runner pod's UTC-7. KST is sixteen hours later."""
    import datetime
    t = datetime.datetime.strptime(stamp, "%y%m%d_%H%M")
    return (t + datetime.timedelta(hours=16)).strftime("%Y-%m-%d %H:%M KST")


def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def policy_flags(spec_path):
    """The scheduler's own arguments, from the deployment spec the driver saved.

    Read rather than reconstructed: the start-up line is the only authority on
    what the scheduler actually ran, and a flag that was set and a flag that was
    intended have differed often enough here to be a rule (CLAUDE.md trap A).
    """
    if not os.path.exists(spec_path):
        return "", ""
    try:
        import yaml
        with open(spec_path) as fh:
            d = yaml.safe_load(fh)
        cs = d["spec"]["template"]["spec"]["containers"]
        args = next(c.get("args", []) for c in cs)
    except Exception:
        # yaml is not guaranteed present; fall back to the raw text, which still
        # pins the spec by hash even if it cannot be parsed into flags.
        return "", sha256(spec_path)
    flat = " ".join(args)
    return flat, sha256(spec_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name", help="result name, becomes the subdirectory")
    ap.add_argument("--arm", action="append", required=True, metavar="LABEL=GLOB[,GLOB...]",
                    help="one per arm; several globs comma-separated")
    ap.add_argument("--since", default=WORKLOAD_BOUNDARY,
                    help=f"drop runs sorting before this (default {WORKLOAD_BOUNDARY})")
    ap.add_argument("--no-copy", action="store_true",
                    help="write the manifest but do not archive metrics.csv")
    a = ap.parse_args()

    outdir = os.path.join(HERE, a.name)
    os.makedirs(os.path.join(outdir, "data"), exist_ok=True)
    os.makedirs(os.path.join(outdir, "scheduler_specs"), exist_ok=True)

    from all_arrivals_attainment import one_run  # noqa: E402

    rows, dropped, specs = [], [], set()
    for entry in a.arm:
        label, pats = entry.split("=", 1)
        paths = []
        for p in pats.split(","):
            paths += [d for d in globmod.glob(os.path.join(ROOT, p.strip()))
                      if os.path.isdir(d) and "PRERUN" not in d]
        for d in sorted(set(paths)):
            b = os.path.basename(d)
            m = DIRNAME.match(b)
            if not m:
                dropped.append((b, "directory name does not parse")); continue
            if m.group("stamp") < a.since:
                dropped.append((b, f"before the workload boundary {a.since}")); continue

            cfg = {}
            cpath = os.path.join(d, "run_config.json")
            if os.path.exists(cpath):
                cfg = json.load(open(cpath))
            session_full = f"{m.group('session')}_{m.group('arm')}_{m.group('mix')}"
            spec = os.path.join(ROOT, "results", "exp07_meta",
                                f"scheduler_deploy_{session_full}.yaml")
            flags, spec_hash = policy_flags(spec)
            # llm-d does not use the Llumnix scheduler at all: its decisions are
            # made by the endpoint-picker in the llmd namespace, and its driver
            # saves that ConfigMap into the run directory as epp_config.yaml.
            # Leaving these rows blank would read as a defect in the manifest
            # rather than as the two systems being configured in different
            # places, so the equivalent artifact is recorded under the same two
            # columns with the source named.
            epp = os.path.join(d, "epp_config.yaml")
            if not os.path.exists(spec) and os.path.exists(epp):
                spec = epp
                spec_hash = sha256(epp)
                flags = "llm-d endpoint-picker config (not the Llumnix scheduler)"
                dst = os.path.join(outdir, "scheduler_specs", f"epp_config_{b}.yaml")
                shutil.copy2(epp, dst)
                specs.add(epp)
            if os.path.exists(spec) and spec not in specs:
                shutil.copy2(spec, os.path.join(outdir, "scheduler_specs",
                                                os.path.basename(spec)))
                specs.add(spec)

            mcsv = os.path.join(d, "metrics.csv")
            rows.append(dict(
                arm_label=label, run=b, started_kst=kst(m.group("stamp")),
                experiment=re.match(r"([a-z]+\d+)", m.group("session")).group(1)
                    if re.match(r"([a-z]+\d+)", m.group("session")) else m.group("session"),
                session=m.group("session"), arm=m.group("arm"), mix=m.group("mix"),
                req_per_s=int(m.group("rpm")) / 60.0 if m.group("rpm") else "",
                duration_min=cfg.get("duration_min", ""), model=cfg.get("model", ""),
                seed=cfg.get("seed", ""), engine=cfg.get("engine", ""),
                metrics_sha256=sha256(mcsv) if os.path.exists(mcsv) else "MISSING",
                metrics_bytes=os.path.getsize(mcsv) if os.path.exists(mcsv) else 0,
                scheduler_spec=(os.path.basename(spec) if os.path.exists(spec)
                                else "MISSING"),
                scheduler_spec_sha256=spec_hash, policy_flags=flags))

            if not a.no_copy and os.path.exists(mcsv):
                dd = os.path.join(outdir, "data", b)
                os.makedirs(dd, exist_ok=True)
                shutil.copy2(mcsv, os.path.join(dd, "metrics.csv"))

    if not rows:
        sys.exit("no runs matched")

    man = os.path.join(outdir, "manifest.tsv")
    with open(man, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]), delimiter="\t")
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["arm_label"], r["req_per_s"] or 0)):
            w.writerow(r)

    # The scored table, computed from the same loader every figure uses so a
    # correction there reaches this too.
    tab = os.path.join(outdir, "table.csv")
    trows = []
    for r in sorted(rows, key=lambda r: (r["arm_label"], r["req_per_s"] or 0)):
        s = one_run(os.path.join(ROOT, "results", r["run"]))
        if s is None:
            continue
        trows.append(dict(arm_label=r["arm_label"], run=r["run"],
                          req_per_s=r["req_per_s"],
                          **{k: round(v, 3) if isinstance(v, float) else v
                             for k, v in s.items()
                             if k in ("n", "offered", "admitted", "all_arrivals",
                                      "rejected_pct", "unfinished_pct",
                                      "goodput_tok_s", "met_n")}))
    with open(tab, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(trows[0]))
        w.writeheader(); w.writerows(trows)

    size = sum(r["metrics_bytes"] for r in rows) / 1e9
    print(f"result        {a.name}")
    print(f"runs          {len(rows)} across {len(set(r['arm_label'] for r in rows))} arms")
    for lab in sorted(set(r["arm_label"] for r in rows)):
        rr = [r for r in rows if r["arm_label"] == lab]
        rates = sorted(set(r["req_per_s"] for r in rr))
        print(f"  {lab:<14} {len(rr):>2} runs at {len(rates)} rates "
              f"({', '.join(f'{x:g}' for x in rates)} req/s)")
    print(f"workload      only runs at or after {a.since} "
          f"({WORKLOAD_NOTE})")
    if dropped:
        print(f"dropped       {len(dropped)}")
        for b, why in dropped[:8]:
            print(f"  {b}  --  {why}")
        if len(dropped) > 8:
            print(f"  ... and {len(dropped)-8} more")
    print(f"archived      {size:.2f} GB of metrics.csv"
          + (" (skipped)" if a.no_copy else ""))
    print(f"wrote         {man}")
    print(f"              {tab}")
    print(f"              {len(specs)} scheduler specs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
