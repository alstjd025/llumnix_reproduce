#!/usr/bin/env python3
"""Re-run the four literature per-token rules on the EXP-82 runs.

WHY THIS EXISTS AND WHY IT IS A SECOND FILE. The scoring in
`tail2026_literature_rules.py` is correct and is reused verbatim here: this
module imports its `scan_run`, `score_run` and `_one` rather than restating
them, so no rule is redefined and no threshold is retuned. What changed is the
INPUT. The runs that `tail2026_literature_rules.py` scored were collected while
the Llumnix gateway was exhausting its CPU quota and being stopped by the
kernel, which froze every stream at once and then delivered several already
generated tokens in the same instant. That is a client-side transport artifact:
it leaves the mean inter-token time of a request alone (the span and the token
count are both unchanged) but it moves every per-token percentile, and it moved
it on the four control planes that reach the engines through the Llumnix
gateway and not on llm-d, which reaches them through its own inference gateway.
So it fell entirely on one side of the comparison.

The cause was removed at source by setting GOMAXPROCS=16 on the gateway, and
the two arms that the paper compares were re-measured as EXP-82. The fraction
of inter-chunk gaps below 5 ms -- the signature of a batched delivery, since
5 ms is far below any real decode interval on this hardware -- fell from 14.11%
to 0.29%. Rules 3 and 4 read short windows of the stream and are therefore the
columns that the artifact could move; rules 1 and 2 are much less exposed and
rule 1 is exposed only through the token count. This module recomputes all four
on the clean runs so that the size and the direction of the correction can be
read per cell against the contaminated numbers.

WHAT IS DIFFERENT FROM THE ORIGINAL DRIVER, and nothing else is:

  1. The run list comes from the results directory rather than from the pinned
     manifest, because the EXP-82 runs postdate the pinned sweep. Directories
     containing PRERUN are excluded -- those are the llm-d predictor warm-up
     passes, not measurements -- and so is any directory whose `metrics.csv`
     holds only a header or whose `tbt_events.jsonl` is empty, which is how a
     run that is still being written or that died before producing load looks
     on disk.
  2. Three quantities are recorded next to the attainment figures that the
     original driver did not need: the rejection rate, so the admitted
     denominator is never read on its own; and the median chat and deep
     research first-token latency and output length, which are the two numbers
     the rule-2-versus-rule-1 decomposition is computed from.

Usage
-----
  python3 tail2026_clean_rules.py --list          # enumerate, score nothing
  python3 tail2026_clean_rules.py --workers 8     # score everything found
"""
import argparse
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run                      # noqa: E402
from tail2026_literature_rules import (                    # noqa: E402
    EXPDIR, RESULTS, OUTDIR, _one,
)

RUN_RE = re.compile(r"_exp82r([12])_(fspfx|llmdslo)_m1f?_rpm_(\d+)$")


def enumerate_runs():
    """Every EXP-82 run on disk, with why each excluded one was excluded.

    Returns (jobs, skipped). `jobs` is the argument tuple `_one` takes.
    """
    jobs, skipped = [], []
    for name in sorted(os.listdir(RESULTS)):
        if "exp82" not in name:
            continue
        if "PRERUN" in name:
            skipped.append((name, "PRERUN: llm-d predictor warm-up pass"))
            continue
        m = RUN_RE.search(name)
        if m is None:
            skipped.append((name, "name does not parse as an EXP-82 condition"))
            continue
        rep, arm, rpm = int(m.group(1)), m.group(2), int(m.group(3))
        mpath = os.path.join(RESULTS, name, "metrics.csv")
        tpath = os.path.join(RESULTS, name, "tbt_events.jsonl")
        if not os.path.isfile(mpath) or os.path.getsize(mpath) < 10_000:
            skipped.append((name, "metrics.csv is a header only -- the run "
                                  "produced no load or is still being written"))
            continue
        if not os.path.isfile(tpath) or os.path.getsize(tpath) == 0:
            skipped.append((name, "tbt_events.jsonl is empty -- no per-token "
                                  "stream was recorded"))
            continue
        jobs.append((name, arm, rpm / 60.0, rep))
    return jobs, skipped


def extras(run):
    """Rejection rate and the two medians the rule-2 decomposition needs.

    `load_run` is the same loader the attainment tables use, so the analysis
    window, the class assignment and the cutoff treatment are identical to the
    ones behind every other column. A rejected request has `is_error` set and
    no first token, so it is counted here and is NOT put through any truncation
    filter; cutoffs -- requests still in flight when the load window closed --
    leave the denominator because their outcome was never determined.
    """
    rows = load_run(os.path.join(RESULTS, run))
    if rows is None or rows.empty:
        return {}
    live = rows[~rows["cutoff"]]
    out = {"n_rows": len(rows),
           "reject_pct": 100.0 * live["rejected"].mean() if len(live) else np.nan,
           "error_pct": 100.0 * live["errored"].mean() if len(live) else np.nan,
           "cutoff_pct": 100.0 * rows["cutoff"].mean()}
    for cname, tag in (("chat", "chat"), ("deepresearch", "dr")):
        sub = live[(live["class"] == cname) & (~live["rejected"]) &
                   (~live["errored"])]
        ttft = pd.to_numeric(sub["first_token_latency"], errors="coerce")
        tok = pd.to_numeric(sub["output_tokens"], errors="coerce")
        out[f"{tag}_ttft_ms_q50"] = 1000.0 * float(ttft.median()) \
            if len(sub) else np.nan
        out[f"{tag}_outtok_q50"] = float(tok.median()) if len(sub) else np.nan
        out[f"{tag}_outtok_q10"] = float(tok.quantile(0.10)) \
            if len(sub) else np.nan
        out[f"{tag}_reject_pct"] = 100.0 * float(
            live.loc[live["class"] == cname, "rejected"].mean()) \
            if (live["class"] == cname).any() else np.nan
    return out


def _one_clean(args):
    run, arm, rate, rep = args
    res = _one((run, arm, rate, False))
    if "error" in res:
        return res
    ex = extras(run)
    for s in res["score"]:
        s["repeat"] = rep
        s.update(ex)
    res["detail"]["repeat"] = rep
    res["detail"].update(ex)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--tag", default="clean")
    ap.add_argument("--list", action="store_true",
                    help="enumerate what exists and stop")
    a = ap.parse_args()

    jobs, skipped = enumerate_runs()
    print(f"{len(jobs)} EXP-82 runs to score, {len(skipped)} excluded")
    for name, why in skipped:
        print(f"  EXCLUDED {name}: {why}")
    cells = {}
    for run, arm, rate, rep in jobs:
        cells.setdefault((arm, round(rate)), []).append(rep)
    for k in sorted(cells, key=lambda k: (k[0], k[1])):
        reps = sorted(cells[k])
        flag = "" if len(reps) == 2 else "   <-- ONE REPEAT ONLY"
        print(f"  {k[0]:9s} {k[1]:3d} req/s  repeats {reps}{flag}")
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
    sc = pd.DataFrame(scores)
    dt = pd.DataFrame(details)
    sc.to_csv(os.path.join(OUTDIR, f"16_{a.tag}_per_run.csv"), index=False)
    dt.to_csv(os.path.join(OUTDIR, f"16_{a.tag}_detail.csv"), index=False)
    if errs:
        pd.DataFrame(errs).to_csv(
            os.path.join(OUTDIR, f"16_{a.tag}_errors.csv"), index=False)
    print(f"\nwrote {OUTDIR}/16_{a.tag}_per_run.csv  ({len(sc)} rows)")
    print(f"wrote {OUTDIR}/16_{a.tag}_detail.csv  ({len(dt)} rows)")
    print(f"EXPDIR = {EXPDIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
