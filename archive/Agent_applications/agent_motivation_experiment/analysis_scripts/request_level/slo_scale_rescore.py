#!/usr/bin/env python3
"""Re-score finished hour-trace runs at a multiplier on the per-token budgets.

WHAT THIS IS. Every scored quantity in `exp22_fluidserve.load_run` is derived
from columns of the run's own `metrics.csv` -- first token latency, end-to-end
latency, output token count -- and the rule it is judged against lives in the
module constant `SLO_RULES`. Multiplying that constant and calling `load_run`
again is therefore a PURE RE-SCORE of data already on disk: no cluster, no
re-run, and the scoring code is the repository's own rather than a second copy
of it.

WHAT IT IS NOT, and this has to travel with every number it produces. A
re-score answers "is the ranking robust to the scoring rule". It does NOT
answer "how would the policy have behaved under a different budget". Every
policy here consumes the budget as an INPUT -- FluidServe through
`--fluidserve-class-budgets`, PolyServe / Llumnix SLO / llm-d through
`slo.<class>.tbt_ms` -- so the admission decisions, and therefore the
rejections, are frozen at the value the run was deployed with. An arm's offered
attainment at any k is bounded above by `100 - rejection - errors`, and that
bound is written into the output so the ceiling is visible.

WHICH BUDGETS MOVE. The multiplier `k` scales the PER-TOKEN budgets only
(chat 50, deepresearch 100, swe 75 ms/token); the first-token budgets
(5 / 10 / 7 s) are held fixed. Two reasons: the first-token budget is a
property of the interaction and not of the token rate, and the crossover
footprint of the companion figure is a statement about the per-token ceiling
alone, so moving both would make the two figures answer different questions.

THE AGENT CLASS. swe must be scored in per-token form (`FS_SWE_TBT_MS=75`,
`FS_SWE_TTFT_S=7`) or its column is judged against a 30 s end-to-end budget,
which no multiplier on a per-token budget can touch, and the column cannot sit
beside the other two. This module sets both envs at import time, BEFORE
importing `exp22_fluidserve`, because that module reads them at ITS import
time.

READING THE CSV ONCE. `load_run` re-reads `metrics.csv` on every call, and the
eight-instance runs are 310 MB each; ten multipliers would be ten reads of the
same bytes. `pandas.read_csv` is wrapped in a per-path cache for the duration
of the run, and the cache returns a COPY, so `load_run` still receives a frame
it may mutate and its behaviour is unchanged. Nothing else about the scoring
path is touched.

  python3 slo_scale_rescore.py --runs results/*exp109* --out out.csv \
      --k 0.7 0.8 0.9 1.0 1.1 1.2 1.3 1.5 2.0 3.0
"""
import argparse
import copy
import glob
import os
import sys

import numpy as np
import pandas as pd

# Both must be set before exp22_fluidserve is imported: it builds SLO_RULES at
# import time from these.
os.environ.setdefault("FS_SWE_TBT_MS", "75")
os.environ.setdefault("FS_SWE_TTFT_S", "7")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp22_fluidserve as exp22  # noqa: E402

# The deployed rule, captured once at import so a re-score at one k cannot leak
# into the next. Everything below scales a copy of this, never the live object.
BASE_RULES = copy.deepcopy(exp22.SLO_RULES)


def scaled_rules(k):
    """The deployed rule with every per-token budget multiplied by `k`.

    `ttft` is copied through untouched. A rule with an `e2e` key would be
    untouched too and is therefore rejected: an end-to-end budget has no
    per-token term for `k` to act on, so a figure mixing one in would draw a
    flat line and call it a curve.
    """
    out = {}
    for cls, rule in BASE_RULES.items():
        if "tbt" not in rule:
            raise SystemExit(
                f"class {cls} is scored with rule {rule}, which has no "
                f"per-token term. Set FS_SWE_TBT_MS=75 (and FS_SWE_TTFT_S=7) "
                f"so every class is judged in per-token form before scaling.")
        out[cls] = dict(rule, tbt=rule["tbt"] * k)
    return out


class _CsvCache:
    """Cache `read_csv` by path for the lifetime of one re-scoring pass."""

    def __init__(self):
        self._real = pd.read_csv
        self._store = {}

    def __enter__(self):
        exp22.pd.read_csv = self._call
        return self

    def __exit__(self, *_):
        exp22.pd.read_csv = self._real
        self._store.clear()
        return False

    def _call(self, path, *a, **kw):
        key = os.path.abspath(path) if isinstance(path, str) else None
        # Only the big per-run metrics file is worth caching, and only when it
        # is read with the same arguments load_run uses. Anything else goes
        # straight through to pandas.
        if key is None or not key.endswith("metrics.csv") or a or set(kw) - {
                "low_memory"}:
            return self._real(path, *a, **kw)
        if key not in self._store:
            self._store[key] = self._real(path, **kw)
        return self._store[key].copy()


def score_one(rows):
    """The four numbers this repository reports together, plus the ceiling.

    Attainment on both denominators, because admitted alone rewards refusing
    the requests that were going to miss; the rejection rate, because admitted
    attainment is uninterpretable without it; token goodput; and the upper
    bound on offered attainment that the frozen rejections impose.
    """
    n = len(rows)
    live = rows[~rows["cutoff"]]
    n_live = len(live)
    window = rows["rel"].max() - rows["rel"].min()
    rej = float(live["rejected"].mean()) if n_live else np.nan
    err = float((live["errored"] & ~live["rejected"]).mean()) if n_live else np.nan
    rec = {
        "n_arrivals": n,
        "n_scored": n_live,
        "n_cutoff": int(rows["cutoff"].sum()),
        "cutoff_pct": 100.0 * float(rows["cutoff"].mean()) if n else np.nan,
        "window_s": round(float(window), 1),
        "offered_pct": exp22.per_request(rows, "violate_offered"),
        "admitted_pct": exp22.per_request(rows, "violate_served"),
        "rejection_pct": 100.0 * rej,
        "error_pct": 100.0 * err,
        "offered_ceiling_pct": 100.0 * (1.0 - rej - err),
        "goodput_tok_s": exp22.goodput_tokens(rows, window),
        "throughput_tok_s": exp22.total_tokens(rows, window),
    }
    for c in exp22.CLASSES:
        sub = rows[rows["class"] == c]
        rec[f"{c}_offered_pct"] = exp22.attain(sub, "violate_offered")
        rec[f"{c}_admitted_pct"] = exp22.attain(sub, "violate_served")
        rec[f"n_{c}"] = len(sub)
    return rec


def rescore(run_dirs, ks):
    """One row per (run, k). Returns a tidy frame."""
    rows = []
    with _CsvCache():
        for d in run_dirs:
            for k in ks:
                exp22.SLO_RULES = scaled_rules(k)
                r = exp22.load_run(d)
                if r is None or r.empty:
                    print(f"  skip {os.path.basename(d)} @ k={k}: no rows",
                          file=sys.stderr)
                    continue
                rec = {"run": os.path.basename(d), "run_dir": d,
                       "arm": exp22.arm_of(d), "k": float(k)}
                rec.update(score_one(r))
                rec["rule"] = ("per-token budgets x k, first-token budgets "
                               "fixed; chat %g/%g, dr %g/%g, swe %g/%g "
                               "(s, ms/token)" % (
                                   BASE_RULES["chat"]["ttft"],
                                   BASE_RULES["chat"]["tbt"] * k,
                                   BASE_RULES["deepresearch"]["ttft"],
                                   BASE_RULES["deepresearch"]["tbt"] * k,
                                   BASE_RULES["swe"]["ttft"],
                                   BASE_RULES["swe"]["tbt"] * k))
                rows.append(rec)
                print(f"  {os.path.basename(d):55s} k={k:<4g} "
                      f"offered {rec['offered_pct']:5.1f}  "
                      f"admitted {rec['admitted_pct']:5.1f}  "
                      f"rej {rec['rejection_pct']:4.1f}%")
    exp22.SLO_RULES = copy.deepcopy(BASE_RULES)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--k", nargs="+", type=float,
                    default=[0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3, 1.5, 2.0, 3.0])
    a = ap.parse_args()
    dirs = []
    for pat in a.runs:
        dirs.extend(sorted(glob.glob(pat)))
    dirs = [d for d in dirs if os.path.isfile(os.path.join(d, "metrics.csv"))]
    if not dirs:
        sys.exit("no runs matched")
    df = rescore(dirs, sorted(a.k))
    df.to_csv(a.out, index=False, float_format="%.4f")
    print(f"wrote {a.out}  ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
