#!/usr/bin/env python3
"""The four literature per-token rules, on all FIVE control planes.

`16_literature_rules_clean.md` scores them on FluidServe and llm-d only, because
those are the two arms EXP-82 re-measured after the gateway's CPU throttling was
removed. EXP-86 re-measured the other three under the same fixed instrumentation,
so the cumulative deadline -- the criterion four of the compared papers use -- can
finally be reported for every control plane in one table.

NOTHING IS REDEFINED HERE. The per-run work is `tail2026_clean_rules._one_clean`,
which is itself a thin wrapper over `scan_run`, `score_run` and `_one` from
`tail2026_literature_rules`. This file replaces only which runs are enumerated and
where the output goes, so no rule and no threshold can drift between the two-arm
table and the five-arm one. `tail2026_clean_rules.py` is deliberately left
untouched: it is the script that produced `16_literature_rules_clean.md`, and
editing it would make that document unreproducible.

    python3 tail2026_rules_five_arms.py --list      # enumerate and stop
    python3 tail2026_rules_five_arms.py --workers 8

⚠ RUN THIS ONLY AFTER EXP-86 HAS FINISHED. Scoring the pinned runs of those three
arms instead would score the contaminated streams, which is the thing EXP-86
exists to remove.

⚠ CHECK WHEN THE RESULT LANDS: `16_literature_rules_clean.md` asserts that swe
comes out identical under all four rules, because it is scored end to end. If that
does not hold for the three new arms, the rule code is doing something arm-specific
and the table cannot be read as a like-for-like comparison.
"""
import argparse, os, re, sys
import pandas as pd
from concurrent.futures import ProcessPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tail2026_literature_rules import RESULTS, OUTDIR          # noqa: E402
from tail2026_clean_rules import _one_clean                    # noqa: E402

# exp82 carries FluidServe and llm-d; exp86 the three that were re-measured later.
RUN_RE = re.compile(r"_exp(82|86)r([12])_(fspfx|llmdslo|polyserve|slo|vllmcache)"
                    r"_(m1f?)_rpm_(\d+)$")
LABEL = {"fspfx": "FluidServe", "llmdslo": "llm-d", "polyserve": "PolyServe",
         "slo": "Llumnix SLO", "vllmcache": "vLLM router"}


def enumerate_runs():
    jobs, skipped = [], []
    for name in sorted(os.listdir(RESULTS)):
        if not ("exp82" in name or "exp86" in name):
            continue
        if "PRERUN" in name:
            skipped.append((name, "PRERUN: llm-d predictor warm-up pass"))
            continue
        m = RUN_RE.search(name)
        if m is None:
            skipped.append((name, "name does not parse as an EXP-82/86 condition"))
            continue
        rep, arm, rpm = int(m.group(2)), m.group(3), int(m.group(5))
        mpath = os.path.join(RESULTS, name, "metrics.csv")
        tpath = os.path.join(RESULTS, name, "tbt_events.jsonl")
        if not os.path.isfile(mpath) or os.path.getsize(mpath) < 10_000:
            skipped.append((name, "metrics.csv is a header only"))
            continue
        if not os.path.isfile(tpath) or os.path.getsize(tpath) == 0:
            skipped.append((name, "tbt_events.jsonl is empty"))
            continue
        jobs.append((name, arm, rpm / 60.0, rep))
    return jobs, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", default="fivearm")
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    jobs, skipped = enumerate_runs()
    print(f"{len(jobs)} runs to score, {len(skipped)} excluded")
    for name, why in skipped:
        print(f"  EXCLUDED {name}: {why}")
    cells = {}
    for run, arm, rate, rep in jobs:
        cells.setdefault((LABEL[arm], round(rate)), []).append(rep)
    for k in sorted(cells):
        reps = sorted(cells[k])
        flag = "" if len(reps) == 2 else "   <-- NOT TWO REPEATS"
        print(f"  {k[0]:13s} {k[1]:3d} req/s  repeats {reps}{flag}")
    if a.list:
        return 0

    scores, details, errs = [], [], []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, res in enumerate(ex.map(_one_clean, jobs), 1):
            if "error" in res:
                errs.append(res)
                print(f"  [{i}/{len(jobs)}] FAILED {res['run']}: {res['error']}")
                continue
            scores.extend(res["score"])
            details.append(res["detail"])
            print(f"  [{i}/{len(jobs)}] {res['detail']['run']}")

    os.makedirs(OUTDIR, exist_ok=True)
    pd.DataFrame(scores).to_csv(
        os.path.join(OUTDIR, f"30_{a.tag}_per_run.csv"), index=False)
    pd.DataFrame(details).to_csv(
        os.path.join(OUTDIR, f"30_{a.tag}_detail.csv"), index=False)
    if errs:
        pd.DataFrame(errs).to_csv(
            os.path.join(OUTDIR, f"30_{a.tag}_errors.csv"), index=False)
    print(f"\nwrote 30_{a.tag}_per_run.csv and 30_{a.tag}_detail.csv to {OUTDIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
