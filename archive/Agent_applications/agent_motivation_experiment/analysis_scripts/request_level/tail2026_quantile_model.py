#!/usr/bin/env python3
"""Can a QUANTILE of engine step time be predicted from what the scheduler sees?

The FluidServe admission test is `meanAfter > gateAfter` and `meanAfter >
tightestAllowance` (`pkg/scheduler/policy/fluidserve.go:1600-1601`), and the
capacity model that produces `meanAfter` holds one deterministic correction
factor and no second moment (`pkg/scheduler/policy/fluidserve_capacity.go:35-50`).
Putting a quantile on the left-hand side instead requires a quantity that does
not exist anywhere in the control plane. This script asks whether one could be
built out of quantities that do exist, and answers in milliseconds against the
50 ms chat budget rather than in correlation.

FOUR PASSES, in the order the report reads them.

  target       per engine per one-second window, the mean, the p90 and the ratio
               p90/mean of the step times reconstructed by
               `tail2026_window_gaps.py`. Read from `14_window_gaps.csv`.

  predict      out-of-sample prediction of the window p90 and of the ratio from
               three nested feature sets, split so that no run appears in both
               train and test, and additionally with a whole arm or a whole
               arrival rate held out. Errors are reported as median absolute
               error and root mean squared error in milliseconds, and as a
               fraction of the 50 ms chat budget.

  arrival      of the prefill volume an instance will process over the next
               1, 2, 3 and 5 seconds, how much is owed to requests the scheduler
               had already placed on it, and how much to requests placed after
               the decision instant. Then, separately, how well the volume over
               the next horizon is predicted by the volume over the last one,
               which is the persistence estimate the policy already computes as
               `arrivingPrefill` (`fluidserve.go:1006-1007`).

  mixture      the two-population hypothesis. If a window's steps are a mixture
               of decode-only steps and prefill-bearing steps, then the window's
               mean carries the mixing rate: with a decode-only reference
               mu_d(batch, kv) taken from windows that processed almost no
               prefill, the share of window time spent beyond decode is
               phi = 1 - mu_d/mean, which is the same quantity the policy
               already measures per instance as `prefillDuty`
               (`fluidserve.go:580-583`). The test is whether p90 is a function
               of (mu_d, phi) alone, because both are already in the scheduler.

    python3 tail2026_quantile_model.py \
        --dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import os
import sys

# The cluster is usually busy with a running experiment, and the boosted-tree
# fits below use OpenMP; letting them take every core makes them slower, not
# faster. Set before sklearn is imported, which is when the pool is sized.
os.environ.setdefault("OMP_NUM_THREADS", "8")

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import Ridge

BUDGET_MS = 50.0          # chat per-token budget, workload_configs/mix_short_m1_*.json
NEAR_ZERO_PREFILL = 200.0  # tok/s below which a window is treated as decode-only

STATE = ["batch", "kv", "queue", "mean_lag", "n_chat", "n_dr", "n_swe"]
LAGGED = STATE + ["prompt_tok_s_lag", "arr_prompt_tok_lag", "phi_lag"]
CONCURRENT = LAGGED + ["prompt_tok_s", "arr_prompt_tok", "phi"]


def load(d):
    g = pd.read_csv(os.path.join(d, "14_window_gaps.csv"))
    f = pd.read_csv(os.path.join(d, "15_window_features.csv"))
    g["tk"] = g["t"].round(3)
    f["tk"] = f["t"].round(3)
    m = g.merge(f.drop(columns=["arm", "rep", "rate"]), on=["run", "engine", "tk"],
                suffixes=("", "_f"))
    m = m.sort_values(["run", "engine", "t"]).reset_index(drop=True)
    grp = m.groupby(["run", "engine"], sort=False)
    for c in ("mean", "p90", "prompt_tok_s", "arr_prompt_tok", "batch", "kv"):
        m[c + "_lag"] = grp[c].shift(1)
    m["ratio"] = m["p90"] / m["mean"]
    return m


def decode_reference(m):
    """mu_d(batch, kv): the window mean where almost no prefill was processed."""
    q = m[m["prompt_tok_s"] < NEAR_ZERO_PREFILL].copy()
    q["bb"] = (q["batch"] // 20).astype(int)
    q["kb"] = (q["kv"] * 20).round().astype(int)
    tab = q.groupby(["bb", "kb"])["mean"].agg(["median", "size"])
    tab = tab[tab["size"] >= 20]["median"]
    # fall back to a batch-only curve where the joint cell is empty
    tabb = q.groupby("bb")["mean"].agg(["median", "size"])
    tabb = tabb[tabb["size"] >= 20]["median"]
    gmed = float(q["mean"].median())

    bb = (m["batch"] // 20).astype(int)
    kb = (m["kv"] * 20).round().astype(int)
    mu = pd.Series(pd.MultiIndex.from_arrays([bb, kb]).map(tab), index=m.index)
    mu = mu.fillna(pd.Series(bb.map(tabb), index=m.index)).fillna(gmed)
    return mu.astype(float)


def new_model(model):
    if model == "gbm":
        return HistGradientBoostingRegressor(
            max_iter=200, learning_rate=0.08, max_depth=6,
            min_samples_leaf=40, random_state=0)
    return Ridge(alpha=1.0)


def fit_eval(df, feats, target, group_col="run", model="gbm", folds=5):
    """Grouped cross-validation, pooled errors in ms.

    Whole groups go to one side of the split, so no window of a run that is
    being predicted was ever trained on. Runs are dealt into `folds` folds
    rather than held out one at a time only to keep the number of fits down;
    with `arm` or `rate` as the group there are fewer groups than folds and the
    split degenerates to leave-one-group-out, which is what is wanted there.
    """
    X = df[feats].to_numpy(float)
    y = df[target].to_numpy(float)
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    X, y = X[ok], y[ok]
    grp = df[group_col].to_numpy()[ok]
    uniq = np.unique(grp)
    assign = {g: i % max(min(folds, len(uniq)), 1) for i, g in enumerate(uniq)}
    fold = np.array([assign[g] for g in grp])
    pred = np.full(len(y), np.nan)
    for k in range(fold.max() + 1):
        te = fold == k
        tr = ~te
        if tr.sum() < 500 or te.sum() < 50:
            continue
        mdl = new_model(model)
        mdl.fit(X[tr], y[tr])
        pred[te] = mdl.predict(X[te])
    ok2 = np.isfinite(pred)
    e = pred[ok2] - y[ok2]
    ss = ((y[ok2] - y[ok2].mean()) ** 2).sum()
    return {"n": int(ok2.sum()), "mae": float(np.median(np.abs(e))),
            "rmse": float(np.sqrt((e ** 2).mean())),
            "p90err": float(np.percentile(np.abs(e), 90)),
            "r2": float(1 - (e ** 2).sum() / ss) if ss > 0 else np.nan,
            "bias": float(e.mean())}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    a = ap.parse_args()
    d = a.dir
    m = load(d)
    m["mu_d"] = decode_reference(m)
    m["phi"] = np.clip(1.0 - m["mu_d"] / m["mean"], 0.0, 0.99)
    m["phi_lag"] = np.clip(1.0 - m["mu_d"] / m["mean_lag"], 0.0, 0.99)
    print(f"windows: {len(m):,} over {m['run'].nunique()} runs\n")

    out = []

    # ---------- 1. target ----------
    t = (m.groupby(["arm", "rate"])
         .agg(n=("p90", "size"), mean_med=("mean", "median"),
              p90_med=("p90", "median"), p90_p90=("p90", lambda s: s.quantile(0.9)),
              ratio_med=("ratio", "median"),
              ratio_p90=("ratio", lambda s: s.quantile(0.9)),
              over_budget=("p90", lambda s: 100.0 * (s > BUDGET_MS).mean()))
         .reset_index())
    t.to_csv(os.path.join(d, "15_target_by_arm_rate.csv"), index=False)
    print("=== 1. target: per-window step-time summary ===")
    print(t.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    print()

    # ---------- 2. predictability ----------
    print("=== 2. out-of-sample prediction of the window p90 (leave-one-run-out) ===")
    rows = []
    for name, feats in (("state", STATE), ("state+lagged prefill", LAGGED),
                        ("state+concurrent prefill (hindsight)", CONCURRENT)):
        for mdl in ("ridge", "gbm"):
            r = fit_eval(m, feats, "p90", "run", mdl)
            r.update(features=name, model=mdl, target="p90")
            rows.append(r)
    for name, feats in (("state", STATE), ("state+lagged prefill", LAGGED),
                        ("state+concurrent prefill (hindsight)", CONCURRENT)):
        r = fit_eval(m, feats, "ratio", "run", "gbm")
        r.update(features=name, model="gbm", target="p90/mean")
        rows.append(r)
    pr = pd.DataFrame(rows)[["target", "features", "model", "n", "mae", "rmse",
                             "p90err", "r2", "bias"]]
    print(pr.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    print()
    # held-out arm and held-out rate
    print("--- generalisation: hold out a whole arm, and a whole arrival rate ---")
    rows2 = []
    for gcol in ("arm", "rate"):
        for name, feats in (("state", STATE), ("state+lagged prefill", LAGGED),
                            ("state+concurrent prefill (hindsight)", CONCURRENT)):
            r = fit_eval(m, feats, "p90", gcol, "gbm")
            r.update(features=name, held_out=gcol)
            rows2.append(r)
    pr2 = pd.DataFrame(rows2)[["held_out", "features", "n", "mae", "rmse", "r2"]]
    print(pr2.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    pd.concat([pr.assign(held_out="run"), pr2.assign(target="p90", model="gbm")],
              ignore_index=True).to_csv(
        os.path.join(d, "15_predictability.csv"), index=False)
    print()
    # per-rate breakdown of the best decision-time model
    print("--- decision-time model (state+lagged), error by arrival rate ---")
    X = m[LAGGED].to_numpy(float)
    y = m["p90"].to_numpy(float)
    ok = np.isfinite(X).all(1) & np.isfinite(y)
    pred = np.full(len(y), np.nan)
    runs = m["run"].to_numpy()
    uniq = np.unique(runs)
    assign = {g: i % 5 for i, g in enumerate(uniq)}
    fold = np.array([assign[g] for g in runs])
    for k in range(5):
        te = ok & (fold == k)
        tr = ok & (fold != k)
        mdl = new_model("gbm")
        mdl.fit(X[tr], y[tr])
        pred[te] = mdl.predict(X[te])
    m["p90_pred_lagged"] = pred
    br = (m.dropna(subset=["p90_pred_lagged"])
          .assign(ae=lambda x: (x["p90_pred_lagged"] - x["p90"]).abs())
          .groupby(["arm", "rate"])
          .agg(n=("ae", "size"), mae=("ae", "median"),
               p90_ae=("ae", lambda s: s.quantile(0.9)),
               true_p90=("p90", "median"))
          .reset_index())
    br["mae_over_budget_pct"] = 100.0 * br["mae"] / BUDGET_MS
    print(br.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    br.to_csv(os.path.join(d, "15_pred_error_by_rate.csv"), index=False)
    print()

    # ---------- 3. where the prefill comes from ----------
    print("=== 3. forward prefill: already placed vs placed during the horizon ===")
    rows3 = []
    for (arm, rate), g in m.groupby(["arm", "rate"]):
        r = {"arm": arm, "rate": rate, "n": len(g)}
        for h in (1, 2, 3, 5):
            tot = g[f"fwd{h}_tot"]
            kn = g[f"fwd{h}_known"]
            sel = tot > 0
            r[f"known_h{h}_pooled"] = 100.0 * kn[sel].sum() / tot[sel].sum()
            r[f"known_h{h}_med"] = 100.0 * (kn[sel] / tot[sel]).median()
        rows3.append(r)
    p3 = pd.DataFrame(rows3)
    print(p3[["arm", "rate", "n"] + [f"known_h{h}_pooled" for h in (1, 2, 3, 5)]]
          .to_string(index=False, float_format=lambda v: f"{v:.1f}"))
    p3.to_csv(os.path.join(d, "15_prefill_arrival.csv"), index=False)
    print()
    print("--- is the forward volume predictable from the last window instead? ---")
    rows4 = []
    for (arm, rate), g in m.groupby(["arm", "rate"]):
        g = g.dropna(subset=["prompt_tok_s_lag"])
        for h in (1, 3):
            fwd = g[f"fwd{h}_tot"].to_numpy(float)
            lag = (g["prompt_tok_s_lag"] * h).to_numpy(float)
            ok = np.isfinite(fwd) & np.isfinite(lag)
            if ok.sum() < 100:
                continue
            e = lag[ok] - fwd[ok]
            ss = ((fwd[ok] - fwd[ok].mean()) ** 2).sum()
            rows4.append({"arm": arm, "rate": rate, "h": h, "n": int(ok.sum()),
                          "corr": float(np.corrcoef(lag[ok], fwd[ok])[0, 1]),
                          "r2_persistence": float(1 - (e ** 2).sum() / ss),
                          "med_abs_err_tok": float(np.median(np.abs(e))),
                          "med_fwd_tok": float(np.median(fwd[ok]))})
    p4 = pd.DataFrame(rows4)
    print(p4.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    p4.to_csv(os.path.join(d, "15_prefill_persistence.csv"), index=False)
    print()

    # ---------- 4. two populations ----------
    print("=== 4. two-population check: is p90 a function of (mu_d, phi)? ===")
    m["phi_b"] = pd.cut(m["phi"], [-.01, .02, .05, .1, .2, .35, .5, 1.01])
    m["mu_b"] = pd.cut(m["mu_d"], [0, 22, 26, 30, 34, 40, 1e9])
    tab = (m.groupby(["mu_b", "phi_b"], observed=True)
           .agg(n=("p90", "size"), p50=("p50", "median"), p90=("p90", "median"),
                p90_iqr=("p90", lambda s: s.quantile(.75) - s.quantile(.25)),
                ratio=("ratio", "median"))
           .reset_index())
    print(tab[tab["n"] >= 100].to_string(index=False,
                                         float_format=lambda v: f"{v:.1f}"))
    tab.to_csv(os.path.join(d, "15_mixture_table.csv"), index=False)
    print()
    rows5 = []
    for name, feats in (("mu_d only", ["mu_d"]),
                        ("mu_d + phi (hindsight)", ["mu_d", "phi"]),
                        ("mu_d + phi_lag (decision time)", ["mu_d", "phi_lag"]),
                        ("batch,kv + phi (hindsight)", ["batch", "kv", "phi"]),
                        ("batch,kv + phi_lag", ["batch", "kv", "phi_lag"])):
        r = fit_eval(m, feats, "p90", "run", "gbm")
        r["features"] = name
        rows5.append(r)
    p5 = pd.DataFrame(rows5)[["features", "n", "mae", "rmse", "p90err", "r2"]]
    print(p5.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    p5.to_csv(os.path.join(d, "15_mixture_predictors.csv"), index=False)
    print()

    # decision-usable summary: how often does the predictor put a window on the
    # wrong side of the 50 ms budget?
    print("=== 5. admission-relevant confusion at the 50 ms chat budget ===")
    sub = m.dropna(subset=["p90_pred_lagged"])
    tp = ((sub["p90_pred_lagged"] > BUDGET_MS) & (sub["p90"] > BUDGET_MS)).mean()
    fp = ((sub["p90_pred_lagged"] > BUDGET_MS) & (sub["p90"] <= BUDGET_MS)).mean()
    fn = ((sub["p90_pred_lagged"] <= BUDGET_MS) & (sub["p90"] > BUDGET_MS)).mean()
    tn = ((sub["p90_pred_lagged"] <= BUDGET_MS) & (sub["p90"] <= BUDGET_MS)).mean()
    print(f"  true over  {100*tp:5.1f}%   false over  {100*fp:5.1f}%")
    print(f"  false under{100*fn:5.1f}%   true under  {100*tn:5.1f}%")
    print(f"  windows actually over budget: {100*(sub['p90']>BUDGET_MS).mean():.1f}%")
    print(f"  predictor says over budget:   "
          f"{100*(sub['p90_pred_lagged']>BUDGET_MS).mean():.1f}%")
    out.append(None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
