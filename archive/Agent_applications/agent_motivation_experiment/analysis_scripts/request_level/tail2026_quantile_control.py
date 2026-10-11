#!/usr/bin/env python3
"""Follow-ups to `tail2026_quantile_model.py`, all of them about the same thing:
if the quantile cannot be PREDICTED at the moment of a placement, can it be
CONTROLLED instead?

Three passes.

  persistence  The model script measured how well the prefill volume of the next
               horizon is predicted by the last window's prefill rate using the
               engine's own `prompt_tokens_total` counter on one side and the
               attributed volume on the other. Here both sides come from the
               attribution, so the two-source mismatch cannot be blamed, and the
               ceiling of any linear rescaling of the persistence estimate is
               reported as the squared correlation rather than as the raw
               R-squared of the unscaled estimate.

  granularity  How many placements make up one window's prefill, and what share
               of it the single largest arrival carries. A placement can only be
               a control input for the quantile to the extent that one placement
               moves it. If a window's prefill is the sum of many arrivals, no
               single admission decision can hold the tail down; only the rate at
               which decisions are issued can.

  controllable The prefill time share phi is what the mixture pass showed the
               p90 is a function of. Every prompt in it was put on that instance
               by the scheduler itself. So phi is regressed on the prompt tokens
               the scheduler placed on that instance in the same window, to see
               how tightly the aggregate it controls determines the quantity it
               cannot forecast.

    python3 tail2026_quantile_control.py \
        --dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

NEAR_ZERO_PREFILL = 200.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    a = ap.parse_args()
    d = a.dir
    g = pd.read_csv(os.path.join(d, "14_window_gaps.csv"))
    f = pd.read_csv(os.path.join(d, "15_window_features.csv"))
    g["tk"] = g["t"].round(3)
    f["tk"] = f["t"].round(3)
    m = g.merge(f.drop(columns=["arm", "rep", "rate"]),
                on=["run", "engine", "tk"], suffixes=("", "_f"))
    m = m.sort_values(["run", "engine", "t"]).reset_index(drop=True)
    grp = m.groupby(["run", "engine"], sort=False)
    m["pf_tok_lag"] = grp["pf_tok"].shift(1)
    m["mean_lag"] = grp["mean"].shift(1)

    # decode-only reference, same construction as the model script
    q = m[m["prompt_tok_s"] < NEAR_ZERO_PREFILL].copy()
    q["bb"] = (q["batch"] // 20).astype(int)
    q["kb"] = (q["kv"] * 20).round().astype(int)
    tab = q.groupby(["bb", "kb"])["mean"].agg(["median", "size"])
    tab = tab[tab["size"] >= 20]["median"]
    tabb = q.groupby("bb")["mean"].agg(["median", "size"])
    tabb = tabb[tabb["size"] >= 20]["median"]
    bb = (m["batch"] // 20).astype(int)
    kb = (m["kv"] * 20).round().astype(int)
    mu = pd.Series(pd.MultiIndex.from_arrays([bb, kb]).map(tab), index=m.index)
    m["mu_d"] = mu.fillna(pd.Series(bb.map(tabb), index=m.index)).fillna(
        float(q["mean"].median())).astype(float)
    m["phi"] = np.clip(1.0 - m["mu_d"] / m["mean"], 0.0, 0.99)

    print("=== A. persistence of forward prefill, both sides from the same source ===")
    rows = []
    for (arm, rate), s in m.groupby(["arm", "rate"]):
        s = s.dropna(subset=["pf_tok_lag"])
        for h in (1, 3):
            y = s[f"fwd{h}_tot"].to_numpy(float)
            x = (s["pf_tok_lag"] * h).to_numpy(float)
            ok = np.isfinite(x) & np.isfinite(y) & (y > 0)
            if ok.sum() < 100:
                continue
            c = float(np.corrcoef(x[ok], y[ok])[0, 1])
            rows.append({"arm": arm, "rate": rate, "h_s": h, "n": int(ok.sum()),
                         "corr": c, "corr2_ceiling": c * c,
                         "med_fwd_tok": float(np.median(y[ok]))})
    pa = pd.DataFrame(rows)
    print(pa.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    pa.to_csv(os.path.join(d, "15_persistence_same_source.csv"), index=False)
    print(f"\n  best squared correlation anywhere in the table: "
          f"{pa['corr2_ceiling'].max():.3f}\n")

    print("=== B. how many placements make one window's prefill ===")
    rows = []
    for (arm, rate), s in m.groupby(["arm", "rate"]):
        sel = s["pf_tok"] > 0
        rows.append({
            "arm": arm, "rate": rate, "n": int(sel.sum()),
            "arrivals_per_engine_s": float(s.loc[sel, "arr_n"].median()),
            "arr_tok_med": float(s.loc[sel, "arr_prompt_tok"].median()),
            "pf_tok_med": float(s.loc[sel, "pf_tok"].median()),
            "largest_arrival_share_pct": float(
                100.0 * (s.loc[sel, "arr_max_tok"]
                         / s.loc[sel, "pf_tok"].clip(lower=1)).median()),
        })
    pb = pd.DataFrame(rows)
    print(pb.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    pb.to_csv(os.path.join(d, "15_placement_granularity.csv"), index=False)
    print()

    print("=== C. is phi determined by the prompt tokens the scheduler placed? ===")
    rows = []
    for (arm, rate), s in m.groupby(["arm", "rate"]):
        for col, name in (("arr_prompt_tok", "placed in this window"),
                          ("pf_tok", "prefilled in this window")):
            x = s[col].to_numpy(float)
            y = s["phi"].to_numpy(float)
            ok = np.isfinite(x) & np.isfinite(y)
            if ok.sum() < 100:
                continue
            c = float(np.corrcoef(x[ok], y[ok])[0, 1])
            rows.append({"arm": arm, "rate": rate, "driver": name,
                         "n": int(ok.sum()), "corr": c, "corr2": c * c})
    pc = pd.DataFrame(rows)
    print(pc.pivot_table(index=["arm", "rate"], columns="driver", values="corr2")
          .to_string(float_format=lambda v: f"{v:.3f}"))
    pc.to_csv(os.path.join(d, "15_phi_drivers.csv"), index=False)
    print()

    print("=== D. what one placement is worth against the tail ===")
    # A single arriving prompt, expressed as the share of the horizon's prefill
    # volume it would add, is the largest disturbance one admission decision can
    # withhold. Compare it with the spread of the horizon volume itself.
    for h in (1, 3):
        tot = m[f"fwd{h}_tot"]
        sel = tot > 0
        one = m.loc[sel, "arr_max_tok"].replace(0, np.nan)
        print(f"  horizon {h} s: median volume {tot[sel].median():,.0f} tok, "
              f"interquartile spread "
              f"{tot[sel].quantile(.75) - tot[sel].quantile(.25):,.0f} tok; "
              f"one prompt median {one.median():,.0f} tok "
              f"({100 * (one / tot[sel]).median():.1f}% of the horizon)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
