#!/usr/bin/env python3
"""What the FluidServe scheduler itself believed, window by window, against what
the engine actually did.

The two passes before this one asked whether a quantile could be predicted from
features a scheduler COULD read. This one uses the numbers the running scheduler
DID read and DID compute, because it publishes them per instance once a second:

  scheduler_fluidserve_predicted_step_ms   the capacity model's mean iteration
                                           time for that instance, which is the
                                           `meanStep` the admission test is
                                           built on (`fluidserve.go:1013-1014`,
                                           compared at `fluidserve.go:1600-1601`)
  scheduler_fluidserve_observed_step_ms    the mean iteration time the engine
                                           actually delivered over the last
                                           status interval (`fluidserve.go:655`)
  scheduler_fluidserve_prefill_duty        the share of engine time the instance
                                           was measured spending on prefill,
                                           smoothed with weight 0.1 per status
                                           interval (`fluidserve.go:580-583`,
                                           `fluidserve.go:636`)
  scheduler_fluidserve_arriving_prefill_tokens  the forward prefill estimate the
                                           duty cycle is converted into over the
                                           planning horizon (`fluidserve.go:1006`)

Joining those to the reconstructed per-window step-time distribution answers
three things that the feature-set study can only bound:

  1. how far the capacity model's MEAN prediction is from the mean the engine
     delivered, in milliseconds, per one-second window;
  2. whether the scheduler's own prefill duty cycle, which is the quantity the
     two-population shortcut needs, tracks the realised prefill share; and
  3. what p90 the shortcut would have produced from the numbers the scheduler
     held at that instant, and how far that is from the p90 the engine produced.

Only the FluidServe arm has a scheduler of this kind; the llm-d arm is routed by
an Envoy-based endpoint picker that publishes none of these, so it is absent
here by construction rather than by omission.

    python3 tail2026_quantile_scheduler.py --results results \
        --dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import csv
import json
import os
import sys

import numpy as np
import pandas as pd

GAUGES = ("predicted_step_ms", "observed_step_ms", "prefill_duty",
          "arriving_prefill_tokens", "queued_prefill_tokens",
          "obs_decode_batch", "obs_kv_tokens", "pace_ms", "raw_step_ms",
          "gate_allowance_ms", "tightest_allowance_ms")
NEAR_ZERO_PREFILL = 200.0
BUDGET_MS = 50.0


def instance_ports(run, map_dir=None):
    """instance id -> engine port, from whichever copy of the map exists.

    The repeat-2 runs were mapped into the shared `engine_maps/` directory
    rather than into the run's own `analysis/`, so looking only inside the run
    silently drops half the FluidServe runs.
    """
    cands = [os.path.join(run, "analysis", "request_engine.csv")]
    if map_dir:
        cands.append(os.path.join(map_dir,
                                  os.path.basename(run.rstrip("/")) + ".csv"))
    for p in cands:
        if not os.path.isfile(p):
            continue
        out = {}
        with open(p) as fh:
            for row in csv.DictReader(l for l in fh if not l.startswith("#")):
                if row.get("instance_id") and row.get("engine_port"):
                    out[row["instance_id"]] = int(row["engine_port"])
        if out:
            return out
    return {}


def scheduler_frame(run, map_dir=None):
    """One row per scrape per instance, columns are the fluidserve gauges."""
    ports = instance_ports(run, map_dir)
    if not ports:
        return None
    p = os.path.join(run, "server_metrics", "scheduler.jsonl")
    if not os.path.isfile(p):
        return None
    rows = []
    with open(p) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if not d.get("ok"):
                continue
            per = {}
            for k, v in d.items():
                if "|instance=" not in k:
                    continue
                name, inst = k.split("|instance=")
                name = name.replace("scheduler_fluidserve_", "")
                if name not in GAUGES:
                    continue
                per.setdefault(inst, {})[name] = v
            for inst, g in per.items():
                port = ports.get(inst)
                if port is None:
                    continue
                g.update(t_sched=d["t"], engine=port)
                rows.append(g)
    if not rows:
        return None
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--dir", required=True)
    ap.add_argument("--map-dir", default=None)
    a = ap.parse_args()
    if a.map_dir is None:
        a.map_dir = os.path.join(a.dir, "engine_maps")
    d = a.dir

    g = pd.read_csv(os.path.join(d, "14_window_gaps.csv"))
    f = pd.read_csv(os.path.join(d, "15_window_features.csv"))
    g["tk"] = g["t"].round(3)
    f["tk"] = f["t"].round(3)
    m = g.merge(f.drop(columns=["arm", "rep", "rate"]),
                on=["run", "engine", "tk"], suffixes=("", "_f"))
    m = m[m["arm"] == "fspfx"].copy()

    parts = []
    for run in sorted(m["run"].unique()):
        sf = scheduler_frame(os.path.join(a.results, run), a.map_dir)
        if sf is None:
            print(f"  {run}: no scheduler gauges", file=sys.stderr)
            continue
        sub = m[m["run"] == run].sort_values("t")
        merged = []
        for port, s in sub.groupby("engine"):
            q = sf[sf["engine"] == port].sort_values("t_sched")
            if q.empty:
                continue
            j = pd.merge_asof(s.sort_values("t"), q, left_on="t",
                              right_on="t_sched", direction="backward",
                              tolerance=2.0)
            merged.append(j)
        if merged:
            parts.append(pd.concat(merged, ignore_index=True))
            print(f"  {run}: {len(parts[-1])} windows joined", file=sys.stderr)
    m = pd.concat(parts, ignore_index=True)
    m = m.dropna(subset=["predicted_step_ms"])
    print(f"\n{len(m):,} FluidServe windows carry the scheduler's own numbers\n")

    # decode-only reference, same construction as the other two passes
    q = m[m["prompt_tok_s"] < NEAR_ZERO_PREFILL]
    bb = (m["batch"] // 20).astype(int)
    kb = (m["kv"] * 20).round().astype(int)
    qb = (q["batch"] // 20).astype(int)
    qk = (q["kv"] * 20).round().astype(int)
    tab = q.groupby([qb, qk])["mean"].agg(["median", "size"])
    tab = tab[tab["size"] >= 20]["median"]
    tabb = q.groupby(qb)["mean"].agg(["median", "size"])
    tabb = tabb[tabb["size"] >= 20]["median"]
    mu = pd.Series(pd.MultiIndex.from_arrays([bb, kb]).map(tab), index=m.index)
    m["mu_d"] = mu.fillna(pd.Series(bb.map(tabb), index=m.index)).fillna(
        float(q["mean"].median())).astype(float)
    m["phi"] = np.clip(1.0 - m["mu_d"] / m["mean"], 0.0, 0.99)
    m["phi_pred"] = np.clip(1.0 - m["mu_d"] / m["predicted_step_ms"], 0.0, 0.99)

    print("=== A. the capacity model's MEAN prediction against the delivered mean ===")
    rows = []
    for rate, s in m.groupby("rate"):
        e_pred = s["predicted_step_ms"] - s["mean"]
        e_obs = s["observed_step_ms"] - s["mean"]
        rows.append({
            "rate": rate, "n": len(s),
            "true_mean": s["mean"].median(),
            "pred_med": s["predicted_step_ms"].median(),
            "pred_mae": e_pred.abs().median(),
            "pred_bias": e_pred.median(),
            "obs_med": s["observed_step_ms"].median(),
            "obs_mae": e_obs.abs().median(),
        })
    pa = pd.DataFrame(rows)
    print(pa.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
    pa.to_csv(os.path.join(d, "15_sched_mean_error.csv"), index=False)
    print()

    print("=== B. the scheduler's own prefill duty against the realised share ===")
    rows = []
    for rate, s in m.groupby("rate"):
        ok = np.isfinite(s["prefill_duty"]) & np.isfinite(s["phi"])
        c = float(np.corrcoef(s.loc[ok, "prefill_duty"], s.loc[ok, "phi"])[0, 1])
        rows.append({"rate": rate, "n": int(ok.sum()),
                     "duty_med": s["prefill_duty"].median(),
                     "phi_med": s["phi"].median(),
                     "corr": c, "corr2": c * c,
                     "mae": float((s.loc[ok, "prefill_duty"]
                                   - s.loc[ok, "phi"]).abs().median())})
    pb = pd.DataFrame(rows)
    print(pb.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    pb.to_csv(os.path.join(d, "15_sched_duty.csv"), index=False)
    print()

    print("=== C. the two-population shortcut run on the scheduler's own numbers ===")
    # The shortcut is p90 = g(mu_d, phi). g is fitted on realised phi with whole
    # runs held out, exactly as in the model pass, and then evaluated three ways:
    # on realised phi, on the scheduler's smoothed duty cycle, and on the phi
    # implied by the capacity model's predicted mean.
    from sklearn.ensemble import HistGradientBoostingRegressor
    runs = m["run"].to_numpy()
    uniq = np.unique(runs)
    fold = np.array([{r: i % 5 for i, r in enumerate(uniq)}[r] for r in runs])
    y = m["p90"].to_numpy(float)
    cols = {"phi (realised, hindsight)": "phi",
            "prefill_duty (scheduler held it)": "prefill_duty",
            "phi from predicted mean": "phi_pred"}
    preds = {k: np.full(len(y), np.nan) for k in cols}
    Xtr_all = m[["mu_d", "phi"]].to_numpy(float)
    for k in range(5):
        te, tr = fold == k, fold != k
        ok = tr & np.isfinite(Xtr_all).all(1) & np.isfinite(y)
        mdl = HistGradientBoostingRegressor(max_iter=200, learning_rate=0.08,
                                            max_depth=6, min_samples_leaf=40,
                                            random_state=0)
        mdl.fit(Xtr_all[ok], y[ok])
        for name, col in cols.items():
            X = m[["mu_d", col]].to_numpy(float)
            sel = te & np.isfinite(X).all(1)
            preds[name][sel] = mdl.predict(X[sel])
    rows = []
    for name in cols:
        p = preds[name]
        ok = np.isfinite(p) & np.isfinite(y)
        e = p[ok] - y[ok]
        ss = ((y[ok] - y[ok].mean()) ** 2).sum()
        rows.append({"phi source": name, "n": int(ok.sum()),
                     "mae": float(np.median(np.abs(e))),
                     "rmse": float(np.sqrt((e ** 2).mean())),
                     "p90err": float(np.percentile(np.abs(e), 90)),
                     "r2": float(1 - (e ** 2).sum() / ss),
                     "over50_true": float(100 * (y[ok] > BUDGET_MS).mean()),
                     "over50_said": float(100 * (p[ok] > BUDGET_MS).mean())})
    pc = pd.DataFrame(rows)
    print(pc.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    pc.to_csv(os.path.join(d, "15_sched_shortcut.csv"), index=False)
    print()

    print("=== D. arriving-prefill estimate against the prefill that arrived ===")
    rows = []
    for rate, s in m.groupby("rate"):
        for h in (1, 3):
            x = s["arriving_prefill_tokens"].to_numpy(float)
            yv = s[f"fwd{h}_tot"].to_numpy(float)
            ok = np.isfinite(x) & np.isfinite(yv) & (yv > 0)
            if ok.sum() < 100:
                continue
            c = float(np.corrcoef(x[ok], yv[ok])[0, 1])
            rows.append({"rate": rate, "h_s": h, "n": int(ok.sum()),
                         "est_med": float(np.median(x[ok])),
                         "actual_med": float(np.median(yv[ok])),
                         "corr": c, "corr2_ceiling": c * c})
    pd_ = pd.DataFrame(rows)
    print(pd_.to_string(index=False, float_format=lambda v: f"{v:.3f}"))
    pd_.to_csv(os.path.join(d, "15_sched_arriving.csv"), index=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
