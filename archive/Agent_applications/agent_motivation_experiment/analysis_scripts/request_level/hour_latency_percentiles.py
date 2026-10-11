#!/usr/bin/env python3
"""Per-request latency percentiles on the one-hour trace, per arm.

For each run: P50 and P95 of end-to-end latency, time to first token, and the
per-request mean time between tokens, over the requests that COMPLETED.

POPULATION. `exp22_fluidserve.load_run` (the analysis window: the first 60 s of
lead-in and the last 20 s dropped), then only requests that were admitted, not
errored, not cut off by the run boundary, and produced a first token. A
rejected request has no latency, and a request still running at the end has an
unknown one.

⚠ SURVIVOR POPULATION. The percentiles describe the requests each arm chose to
finish, so they are not comparable as "how fast is the arm" unless the
rejection share and the unfinished share are read beside them: an arm that
refuses the requests that would have been slow reports a faster distribution
for that reason alone. Both shares are written into every row.

⚠ THE CLASS MIX OF THE COMPLETED POPULATION DIFFERS BY ARM, and the three
classes have different output lengths and budgets, so the pooled percentile
moves when the mix moves. The per-class rows are written too.

TBT is `itl_ms` = (latency - first_token_latency) / (output_tokens - 1), the
corrected per-request mean (the raw `tbt_mean_ms` column is half the true value
on the older collector). Requests with fewer than two output tokens have none.

    python3 analysis_scripts/request_level/hour_latency_percentiles.py
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location(
    "e22", os.path.join(HERE, "exp22_fluidserve.py"))
E = importlib.util.module_from_spec(spec)
spec.loader.exec_module(E)

RUNS = {
    "vLLM router": ("260901_2137_exp109r1_vllmcachet75_shift",
                    "260901_2010_exp109r2_vllmcachet75_shift"),
    "Llumnix SLO": ("260831_2346_exp109r1_slot75_shift",
                    "260901_1856_exp109r2_slot75_shift"),
    "PolyServe": ("260831_2232_exp109r1_polyservept75_shift",
                  "260901_1742_exp109r2_polyservept75_shift"),
    "llm-d": ("260831_2128_exp109r1_llmdslot75_shift",
              "260901_1637_exp109r2_llmdslot75_shift"),
    "FluidServe": ("260831_2015_exp109r1_fsv3capgnofrct75_shift",
                   "260901_1514_exp109r2_fsv3capgnofrct75_shift"),
}
OUT = os.path.join(ROOT, "results", "aggregate_analysis", "hour_latency")


def pct(v, q):
    v = v[np.isfinite(v)]
    return float(np.percentile(v, q)) if len(v) else np.nan


def one(arm, run):
    r = E.load_run(os.path.join(ROOT, "results", run))
    n = len(r)
    done = r[~r["rejected"] & ~r["errored"] & ~r["cutoff"]
             & r["first_token_latency"].notna()]
    rows = []
    for cls in ["all"] + list(E.CLASSES):
        g = done if cls == "all" else done[done["class"] == cls]
        base = r if cls == "all" else r[r["class"] == cls]
        e2e = pd.to_numeric(g["latency"], errors="coerce").to_numpy(float)
        ttft = pd.to_numeric(g["first_token_latency"],
                             errors="coerce").to_numpy(float)
        itl = pd.to_numeric(g["itl_ms"], errors="coerce").to_numpy(float)
        rows.append(dict(
            arm=arm, run=run, cls=cls, arrivals=len(base), completed=len(g),
            rejected_pct=100.0 * base["rejected"].mean(),
            unfinished_pct=100.0 * base["cutoff"].mean(),
            errored_pct=100.0 * base["errored"].mean(),
            e2e_p50_s=pct(e2e, 50), e2e_p95_s=pct(e2e, 95),
            ttft_p50_s=pct(ttft, 50), ttft_p95_s=pct(ttft, 95),
            tbt_p50_ms=pct(itl, 50), tbt_p95_ms=pct(itl, 95)))
    return rows


def main():
    rows = []
    for arm, runs in RUNS.items():
        for run in runs:
            print(f"  {arm:12s} {run}", flush=True)
            rows += one(arm, run)
    df = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "exp109_latency_percentiles.csv")
    df.to_csv(path, index=False, float_format="%.4f")
    print(f"wrote {path}  ({len(df)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
