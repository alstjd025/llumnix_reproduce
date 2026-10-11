#!/usr/bin/env python3
"""Produce the one table of headline numbers that documents cite.

WHY. The same measurement lives in several documents by hand, and a
re-measurement leaves the old value behind in some of them. This makes one
generated home for the numbers that get quoted, keyed so a document can name a
value instead of copying it.

KEY: experiment.condition.arm.metric

  exp57.static45.polyserve.offered
  exp60.hour.fspin-demand.offered
  exp61.static50.loadbalance-qoserve.goodput

The row carries the mean, the repeat count, and the min and max, so that
check_numbers.py can enforce this project's own rule that a mean over more than
one repeat is quoted with its range.

WHAT IT DOES NOT DO. It does not read the documents and it does not rewrite
them. It reads result directories and writes numbers.tsv. Everything it reports
comes from the same loaders the figures use (`exp22_fluidserve.load_run` and
`attain`), so a number here is the number a figure would draw; the goodput
divisor is the one exp56_affinity.py uses, because the same name computed two
ways is the failure this file exists to prevent.

  python3 ms_dev/scripts/collect_numbers.py
  python3 ms_dev/scripts/collect_numbers.py --pattern 'results/*exp6*'
"""
import argparse
import glob
import os
import re
import sys
from collections import defaultdict

REPO = "/home/nxclab/llumnix_reproduce"
EXPDIR = os.path.join(REPO, "Agent_applications/agent_motivation_experiment")
sys.path.insert(0, os.path.join(EXPDIR, "analysis_scripts/request_level"))
OUT = os.path.join(REPO, "ms_dev/notes/numbers.tsv")
EXCLUDED = os.path.join(REPO, "ms_dev/notes/excluded_runs.tsv")

import numpy as np                                  # noqa: E402
import pandas as pd                                 # noqa: E402
from exp22_fluidserve import load_run, attain, CLASSES  # noqa: E402

# A result directory is  YYMMDD_HHMM_<session>_<arm>_<variant>[_rpm_<rpm>].
# The session tag carries the experiment number and the repeat; neither has one
# fixed shape (exp53p2r1, exp56hr1, exp57unchanged), so the parse is permissive
# and anything it cannot read is skipped with a line rather than guessed at.
DIRPAT = re.compile(r"^\d{6}_\d{4}_(exp\d+)([a-z0-9]*)_(.+?)"
                    r"(?:_(m\d+f?|full|verbatim))?(?:_rpm_(\d+))?$")


def condition_of(variant, rpm):
    if rpm:
        return f"static{int(rpm) / 60:.0f}"
    return variant or "hour"


def load_excluded():
    """Runs that exist and are valid data but must not enter an aggregate.

    A contaminated condition averaged into its arm is exactly the silent error
    this table exists to prevent: the first run of collect_numbers.py reported
    exp60.full.fluidserve as a mean of two conditions spanning 68.77 to 74.48,
    one of which had the class pin inherited from the previous arm.
    """
    out = []
    if not os.path.exists(EXCLUDED):
        return out
    for line in open(EXCLUDED):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        out.append((f[0].strip(), f[1].strip() if len(f) > 1 else ""))
    return out


def rows_for(d):
    b = os.path.basename(d)
    m = DIRPAT.match(b)
    if not m:
        return None
    exp, tail, arm, variant, rpm = m.groups()
    if "smoke" in tail or "smoke" in arm:
        return None
    r = load_run(d)
    if r is None or r.empty:
        return None
    served = r[~r["rejected"]]
    met = served[~served["violate_served"]]
    dur = max(r["rel"].max(), 1.0)
    tok = lambda g: float(pd.to_numeric(  # noqa: E731
        g["output_tokens"], errors="coerce").fillna(0).sum())
    out = {
        "offered": attain(r, "violate_offered"),
        "admitted": attain(served, "violate_served"),
        "reject": 100.0 * float(r["rejected"].mean()),
        "goodput": tok(met) / dur,
        "throughput": tok(served) / dur,
    }
    for c in CLASSES:
        g = r[r["class"] == c]
        if len(g) < 50:
            continue
        out[f"offered.{c}"] = attain(g, "violate_offered")
        out[f"reject.{c}"] = 100.0 * float(g["rejected"].mean())
    return exp, condition_of(variant, rpm), arm, out


# The agent class carries two promises and a run belongs to exactly one of them:
# the original end-to-end 30 s, and the per-token form (TTFT 7 s + 75 ms/token)
# adopted in v0.4. Which one a run promised is recorded in its arm name -- every
# arm run under the per-token form carries `t75`, which is the convention the
# drivers already use. `exp22_fluidserve` fixes the rule at IMPORT from
# FS_SWE_TBT_MS, so one process can only score one way, and a single pass over
# every run therefore scores the t75 runs under a promise they never made. It did:
# EXP-113's FluidServe hour read 72.05 offered here against 78.6 in the
# experiment file, and its swe column read 8.35, because a Qwen agent request
# averaging 1,006 tokens almost never finishes inside 30 s.
#
# So the collection runs TWICE, as subprocesses, and each pass takes only the runs
# that belong to it. That keeps the loader untouched -- every figure imports it --
# and keeps the two promises out of one table, which is exactly what the loader's
# own warning asks for.
T75_RE = re.compile(r"t75")


def _child(pattern, out, only_t75):
    env = dict(os.environ)
    if only_t75:
        env["FS_SWE_TBT_MS"] = env.get("FS_SWE_TBT_MS", "75")
    else:
        env.pop("FS_SWE_TBT_MS", None)
    import subprocess
    r = subprocess.run(
        [sys.executable, os.path.abspath(__file__), "--pattern", pattern,
         "--out", out, "--select", "t75" if only_t75 else "e2e", "--child"],
        env=env, capture_output=True, text=True)
    sys.stdout.write(r.stdout)
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        raise SystemExit(f"collection pass ({'t75' if only_t75 else 'e2e'}) failed")
    return r.stderr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="results/*")
    ap.add_argument("--out", default=OUT)
    ap.add_argument("--select", choices=["all", "t75", "e2e"], default="all",
                    help="which runs this pass scores; `all` splits into two passes")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    a = ap.parse_args()

    if not a.child and a.select == "all":
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            f1, f2 = os.path.join(td, "e2e.tsv"), os.path.join(td, "t75.tsv")
            _child(a.pattern, f1, False)
            _child(a.pattern, f2, True)
            head, body = [], []
            for f in (f1, f2):
                for line in open(f):
                    (head if line.startswith("#") else body).append(line)
            seen_keys = set()
            merged = []
            for line in sorted(body):
                k = line.split("\t", 1)[0]
                if k in seen_keys:
                    raise SystemExit(f"key {k!r} produced by both passes -- a run "
                                     f"was scored under both promises")
                seen_keys.add(k)
                merged.append(line)
            with open(a.out, "w") as f:
                f.write("# generated by ms_dev/scripts/collect_numbers.py — do not edit\n")
                f.write("# runs whose arm name contains `t75` are scored with the agent\n")
                f.write("# class promised TTFT 7 s + 75 ms/token; all others end-to-end 30 s\n")
                f.write("# key\tvalue\tn\tmin\tmax\tnote\n")
                f.writelines(merged)
            print(f"{len(merged)} keys -> {a.out} (two passes: e2e and t75)")
        return

    os.chdir(EXPDIR)
    acc = defaultdict(list)
    excluded = load_excluded()
    seen = skipped = dropped = 0
    for d in sorted(glob.glob(a.pattern)):
        if not os.path.isdir(d) or "aggregate_analysis" in d:
            continue
        is_t75 = bool(T75_RE.search(os.path.basename(d)))
        if a.select == "t75" and not is_t75:
            continue
        if a.select == "e2e" and is_t75:
            continue
        hit = next((r for pat, r in excluded if pat in os.path.basename(d)), None)
        if hit is not None:
            print(f"  excluded {os.path.basename(d)}: {hit}")
            dropped += 1
            continue
        got = rows_for(d)
        if got is None:
            skipped += 1
            continue
        seen += 1
        exp, cond, arm, metrics = got
        for k, v in metrics.items():
            if v is not None and np.isfinite(v):
                acc[f"{exp}.{cond}.{arm}.{k}"].append(float(v))

    fmt = lambda k, v: (f"{v:,.0f}" if k.endswith(("goodput", "throughput"))  # noqa: E731
                        else f"{v:.2f}")
    with open(a.out, "w") as f:
        f.write("# generated by ms_dev/scripts/collect_numbers.py — do not edit\n")
        f.write("# key\tvalue\tn\tmin\tmax\tnote\n")
        for key in sorted(acc):
            v = acc[key]
            metric = key.rsplit(".", 1)[1]
            f.write(f"{key}\t{fmt(metric, float(np.mean(v)))}\t{len(v)}\t"
                    f"{fmt(metric, min(v))}\t{fmt(metric, max(v))}\t\n")
    print(f"{seen} runs read, {dropped} excluded, {skipped} unparsed, "
          f"{len(acc)} keys -> {a.out}")


if __name__ == "__main__":
    main()
