#!/usr/bin/env python3
"""One row per request: when it was sent, when and where it was routed, when its
first token came back, when it ended, and how it was judged.

Writes `<run>_trace.csv` per run. The rows are exactly the rows of the ladder95
verdict file for that run (the analysis window of `exp22_fluidserve.load_run`:
the first 60 s and the last 20 s of arrivals are left out).

Where each column comes from:

  send_ts, end_ts, ttft_s, e2e_s,        metrics.csv (the client), epoch seconds
  input/output_tokens, rejection_reason
  first_token_ts                         send_ts + ttft_s
  mean_tbt_ms                            (e2e - ttft) / (output_tokens - 1)
  ladder_ok                              the ladder95 verdict file
  request_id                             request_ids.jsonl, matched on
                                         (task_id, call_index) and the nearest
                                         send time within 2 s; admitted rows only,
                                         because a rejected request has no id
  engine_port                            analysis/request_engine.csv
  route_ts                               Llumnix-scheduled arms: the time of the
                                         request's first "[Schedule] dispatch
                                         request <id> to ... instance" line in
                                         server_metrics/scheduler_dispatch.log.
                                         llm-d: the start time Envoy recorded for
                                         the request in envoy_access.log (Envoy
                                         receives it, then the Endpoint Picker
                                         chooses the instance)
  fs_* (FluidServe only)                 the same log's "fsplacement" line

The client, the scheduler and Envoy run on one host and read one clock. The
scheduler log carries no year and is in UTC; the year is taken from the run.

    FS_SWE_TBT_MS=75 FS_SWE_TTFT_S=7 python3 export_request_trace.py \\
        --runs <run> [<run> ...] --out <dir>
"""
import argparse
import datetime
import importlib.util
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


EV = _load("ev", os.path.join(HERE, "exp41_engine_view.py"))
FT = _load("ft", os.path.join(HERE, "exp101_first_token_truth.py"))
LM = _load("lm", os.path.join(HERE, "llmd_engine_map.py"))
VERDICTS = os.path.join(ROOT, "results", "aggregate_analysis", "ladder95",
                        "verdicts")
KEYS = ["task_id", "call_index", "iteration"]


def request_ids(rd):
    recs = [json.loads(l) for l in open(os.path.join(rd, "request_ids.jsonl"))
            if l.strip()]
    ids = pd.DataFrame(recs)
    ids["start_time"] = pd.to_numeric(ids["start_time"], errors="coerce")
    return ids.dropna(subset=["start_time"]).sort_values("start_time")


def envoy_times(rd):
    """request_id -> Envoy start epoch seconds, through the llm-d matcher."""
    env, _refused = LM.envoy_lines(rd)
    cli = LM.client_requests(rd)
    if env is None or cli is None or cli.empty:
        return {}
    idx = LM.match(cli, env)
    rid = LM.client_ids(rd, cli)
    out = {}
    for k in range(len(cli)):
        if idx[k] >= 0 and rid[k]:
            out[rid[k]] = float(env["t"].values[idx[k]])
    return out


def one(run, out_dir):
    rd = os.path.join(ROOT, "results", run)
    out = os.path.join(out_dir, run + "_trace.csv")
    if os.path.exists(out):
        sys.exit(f"{out} exists; not overwriting")
    r = EV.load_run(rd).copy()
    v = pd.read_csv(os.path.join(VERDICTS, run + ".csv"))
    if r.duplicated(KEYS).any() or v.duplicated(KEYS).any():
        sys.exit(f"{run}: request keys are not unique, refusing to join")
    r = r.merge(v[KEYS + ["ladder_ok"]], on=KEYS, how="left")
    if len(r) != len(v):
        sys.exit(f"{run}: rows {len(r)} do not match verdicts {len(v)}")

    # request id, admitted rows only (as attribute_engines does)
    ids = request_ids(rd)
    r["start_time"] = pd.to_numeric(r["start_time"], errors="coerce")
    r["_pos"] = np.arange(len(r))
    adm = r[~r["rejected"]].sort_values("start_time")
    j = pd.merge_asof(adm[["_pos", "task_id", "call_index", "start_time"]],
                      ids[["task_id", "call_index", "start_time", "request_id"]],
                      on="start_time", by=["task_id", "call_index"],
                      direction="nearest", tolerance=2.0)
    r = r.merge(j[["_pos", "request_id"]], on="_pos", how="left")

    mp = pd.read_csv(os.path.join(rd, "analysis", "request_engine.csv"))
    mp = mp.drop_duplicates("request_id")
    r = r.merge(mp[["request_id", "engine_port"]], on="request_id", how="left")

    is_llmd = "_llmd" in run
    fs = {}
    if is_llmd:
        when = envoy_times(rd)
        r["route_ts"] = r["request_id"].map(when)
        r["route_source"] = np.where(r["route_ts"].notna(), "envoy_access_log", "")
    else:
        year = datetime.datetime.fromtimestamp(
            float(ids["start_time"].iloc[0]), datetime.timezone.utc).year
        when, fs = FT.read_dispatch(os.path.join(rd, "server_metrics",
                                                 "scheduler_dispatch.log"), year)
        uuid = r["request_id"].fillna("").str.split("cmpl-").str[-1]
        r["route_ts"] = uuid.map(when)
        r["route_source"] = np.where(r["route_ts"].notna(),
                                     "scheduler_dispatch_log", "")

    ttft = pd.to_numeric(r["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(r["latency"], errors="coerce")
    outcome = np.where(r["rejected"], "rejected",
                       np.where(r["cutoff"], "unfinished",
                                np.where(r["ladder_ok"].astype(bool), "met",
                                         "missed")))
    status = np.where(r["rejected"], "rejected",
                      np.where(r["engine_port"].notna(), "attributed",
                               "not_found"))
    t = pd.DataFrame({
        "task_id": r["task_id"], "call_index": r["call_index"],
        "iteration": r["iteration"], "class": r["class"],
        "request_id": r["request_id"].fillna(""),
        "send_ts": r["start_time"],
        "rel_s": pd.to_numeric(r["rel"], errors="coerce"),
        "route_ts": r["route_ts"], "route_source": r["route_source"],
        "wait_ms": (r["route_ts"] - r["start_time"]) * 1000.0,
        "engine_port": r["engine_port"].astype("Int64"),
        "engine_status": status,
        "first_token_ts": r["start_time"] + ttft,
        "end_ts": pd.to_numeric(r["end_time"], errors="coerce"),
        "ttft_s": ttft, "e2e_s": e2e,
        "mean_tbt_ms": pd.to_numeric(r["itl_ms"], errors="coerce"),
        "input_tokens": pd.to_numeric(r["input_tokens"], errors="coerce").astype("Int64"),
        "output_tokens": pd.to_numeric(r["output_tokens"], errors="coerce").astype("Int64"),
        "outcome": outcome, "ladder_ok": r["ladder_ok"],
        "rejection_reason": r.get("rejection_reason", pd.Series([""] * len(r))).fillna(""),
    })
    if fs:
        uuid = t["request_id"].str.split("cmpl-").str[-1]
        for col, key in (("fs_tier", "tier"), ("fs_waited_ms", "waited_ms"),
                         ("fs_prefill_est_ms", "prefillest_ms"),
                         ("fs_prompt_tokens", "prompt"),
                         ("fs_decision", "decision")):
            t[col] = uuid.map(lambda u, k=key: fs.get(u, {}).get(k))
    t = t.sort_values("send_ts").reset_index(drop=True)
    t.to_csv(out, index=False, float_format="%.6f")

    adm_n = int((t["outcome"] != "rejected").sum())
    routed = t["route_ts"].notna()
    w = t.loc[routed, "wait_ms"]
    ft_before = (t.loc[routed & t["first_token_ts"].notna(), "first_token_ts"]
                 < t.loc[routed & t["first_token_ts"].notna(), "route_ts"]).mean()
    print(f"  {run}: {len(t)} rows; admitted {adm_n}, with instance "
          f"{int((t['engine_status'] == 'attributed').sum())}, with route time "
          f"{int(routed.sum())} ({100.0 * routed.sum() / max(adm_n, 1):.1f}% of admitted); "
          f"wait p50 {w.median():.1f} ms, p99 {w.quantile(.99):.1f} ms, "
          f"negative {100.0 * (w < -1.0).mean():.2f}%; "
          f"first token before route {100.0 * ft_before:.2f}%")
    return t


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for run in a.runs:
        one(run, a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
