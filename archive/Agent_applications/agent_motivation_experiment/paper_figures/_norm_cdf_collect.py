#!/usr/bin/env python3
"""Child: emit the normalised per-token latency of every arrival, for one scale.

Each admitted request that produced tokens contributes the WORSE of its two
terms, each divided by its own budget:

    max( first-token time / first-token budget ,
         per-token time   / per-token budget   )

so chat, deepresearch and swe land on ONE axis, and because a request meets its
SLO exactly when both terms are under their budgets, "x <= 1" is "met" and the
height of the CDF at x = 1 is the attainment itself.

Arrivals that never produced a per-token time -- rejected, errored, or cut off by
the end of the window -- are counted in the denominator but contribute no x
value. The CDF therefore saturates at (1 - their share) rather than at 1.0, which
is what makes the plateau readable as the rejection cost.
"""
import glob, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts"))
os.chdir(ROOT)
import numpy as np, pandas as pd
from exp22_fluidserve import load_run, SLO_RULES

ARMS = [("fsv3capgnofrct75", "FluidServe"), ("fsv3capgnofrct75k", "FluidServe"),
        ("llmdslot75", "llm-d"), ("polyservept75", "PolyServe"),
        ("slot75", "Llumnix SLO")]


def main():
    klabel, pattern = sys.argv[1], sys.argv[2]
    out = []
    for arm, label in ARMS:
        ds = [x for x in sorted(glob.glob(pattern % arm)) if "PRERUN" not in x]
        if not ds:
            continue
        d = ds[0]                      # one repeat: a CDF of two runs pooled would
        r = load_run(d)                # hide that they are two runs
        if r is None or r.empty:
            continue
        n = len(r)
        ttft = pd.to_numeric(r["first_token_latency"], errors="coerce") * 1000
        e2e = pd.to_numeric(r["latency"], errors="coerce") * 1000
        tok = pd.to_numeric(r["output_tokens"], errors="coerce")
        per = (e2e - ttft) / (tok - 1)
        tbud = r["class"].map(lambda c: SLO_RULES[c]["tbt"])
        fbud = r["class"].map(lambda c: SLO_RULES[c]["ttft"] * 1000.0)
        # THE WORSE OF THE TWO TERMS, each divided by its own budget. A request
        # meets its SLO exactly when BOTH are under 1, so taking the max makes
        # "x <= 1" identical to "met", and the height of the CDF at x = 1 is the
        # attainment rather than one term of it.
        #
        # This is not cosmetic. With the per-token term alone the Llumnix SLO
        # curve reaches 24% at x = 1 on the loose panel while its measured
        # attainment is 6.7: that policy fails on FIRST TOKEN, not on pace
        # (deepresearch 62.6% and swe 77.0% over their first-token budgets at
        # k = 1.3), and a figure built on the pace term alone would have said the
        # opposite of the table.
        x_ratio = np.maximum((per / tbud).astype(float), (ttft / fbud).astype(float))
        usable = (~r["rejected"].astype(bool)) & (~r["cutoff"].astype(bool)) \
                 & (~r["errored"].astype(bool)) & per.notna() & (tok > 1)
        x = x_ratio[usable].values
        out.append({"k": float(klabel), "arm": label, "n_arrivals": int(n),
                    "x": sorted(round(float(v), 4) for v in x)})
    print(json.dumps(out))


if __name__ == "__main__":
    main()
