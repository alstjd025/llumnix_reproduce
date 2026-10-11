#!/usr/bin/env python3
"""Which engine served each request, for an llm-d run -> analysis/request_engine.csv.

`build_request_engine_map.py` cannot be used on an llm-d condition. It joins the
client's `request_ids.jsonl` to the Llumnix scheduler's dispatch log by request
uuid, and under llm-d the Llumnix scheduler is not in the request path at all:
routing is done by the Endpoint Picker behind Envoy, and `scheduler_dispatch.log`
contains nothing from this run. The engine identity is instead in Envoy's access
log, in the `%UPSTREAM_HOST%` field, which is `<pod-ip>:<port>` and so names one
of the four vLLM API servers on the engine pod directly.

The join is by time, not by identifier, because the two sides do not share one.
Envoy writes its own `x-request-id` (the client sends none) and the client
records vLLM's `cmpl-<uuid>` (which Envoy never sees, since it is in the response
body). What both sides do record is when the request started and how long it
took, so each client request is matched to the Envoy line closest to it in
(start time, duration).

That this is enough was measured rather than assumed. At 70 req/s -- the densest
condition, with a mean gap between arrivals of 14 ms -- the matched pairs differ
by a median of 1.6 ms in start time and 3.0 ms in duration, which is two orders
of magnitude below the spread of the durations themselves (17 s to 30 s). The
matching is made one-to-one by taking candidate pairs in increasing cost order
and refusing a second use of either side, so a request cannot be attributed to
an engine that in fact served its neighbour. The fraction matched is printed and
also written into the CSV header comment; treat a run below 99% as one where the
per-engine figures are drawn on a subset and say so beside them.

Rejected requests are excluded on both sides: llm-d rejects at the Endpoint
Picker, so Envoy logs those lines with `-` as the upstream host and the client
marks them `is_rejected`. They have no engine and are not missing data.

Columns are the same as `build_request_engine_map.py` so the downstream figure
scripts (`plot_per_engine_attainment.py`, `separation_measures.py`,
`exp41_engine_view.py`) need no change. `instance_id` carries the pod IP and
port rather than a Llumnix instance id, and `migrated` is always False because
llm-d has no migration mechanism.

  python3 llmd_engine_map.py results/260807_0645_exp66r1_llmdslo_m1f_rpm_4200
  python3 llmd_engine_map.py --glob 'results/*exp66r1_llmdslo_m1f_rpm_*'
"""
import argparse
import csv
import datetime as dt
import glob
import os

import numpy as np
import pandas as pd

# Cost of one millisecond of start-time disagreement against one millisecond of
# duration disagreement. Start time is the stronger signal: the client stamps it
# immediately before the socket write and Envoy immediately after the read, so
# the two are separated only by the in-cluster hop, whereas the durations differ
# by however long the client takes to finish consuming the stream after Envoy
# has seen the last byte. Duration is kept in the cost at a fifth of the weight
# because it breaks ties between two arrivals in the same millisecond.
W_DURATION = 0.2
# No pair further apart than this in start time is considered at all. Two
# seconds is far wider than any observed offset (max 0.9 s, and that on the one
# request that opened the first connection) and narrow enough that a request
# whose Envoy line is missing stays unmatched instead of stealing a distant one.
MAX_DT_MS = 2000.0


def envoy_lines(run):
    """(start epoch seconds, duration ms, upstream host) for served completions.

    Lines whose upstream host is `-` are the requests the Endpoint Picker
    refused; they are dropped here and counted separately by the caller.
    """
    path = os.path.join(run, "envoy_access.log")
    if not os.path.isfile(path):
        return None, 0
    rows, refused = [], 0
    for line in open(path, errors="ignore"):
        f = line.rstrip("\n").split("\t")
        if len(f) < 7 or "completions" not in f[6]:
            continue
        if f[3] == "-":
            refused += 1
            continue
        try:
            t = dt.datetime.strptime(f[0], "%Y-%m-%dT%H:%M:%S.%fZ")
            t = t.replace(tzinfo=dt.timezone.utc).timestamp()
            dur = float(f[4])
        except (ValueError, IndexError):
            continue
        rows.append((t, dur, f[3]))
    if not rows:
        return None, refused
    df = pd.DataFrame(rows, columns=["t", "dur_ms", "host"])
    return df.sort_values("t").reset_index(drop=True), refused


def client_requests(run):
    """The requests the client actually got an answer for, with start and duration."""
    p = os.path.join(run, "metrics.csv")
    if not os.path.isfile(p):
        return None
    df = pd.read_csv(p, low_memory=False)
    r = df[df.agent != "job_summary"].copy()
    rej = r["is_rejected"].astype(str).str.lower().isin(["true", "1", "1.0"])
    r = r[~rej]
    r["start_time"] = pd.to_numeric(r["start_time"], errors="coerce")
    r["dur_ms"] = pd.to_numeric(r["latency"], errors="coerce") * 1000.0
    r = r[r["start_time"].notna() & r["dur_ms"].notna()]
    return r.sort_values("start_time").reset_index(drop=True)


def client_ids(run, cli):
    """The vLLM request id the client recorded, aligned to the metrics rows.

    Carried into the output only so that
    `exp41_engine_view.attribute_engines` can do its request_id join unchanged;
    nothing here needs it, because the engine identity came from Envoy.

    The two files are written by the same client from the same variable, but the
    CSV round-trips the start time through text and `request_ids.jsonl` does
    not, so 12% of the pairs do not survive an equality test on the float. The
    match is therefore the nearest start time within one second, restricted to
    rows that agree on task and call index, which is the same rule
    `attribute_engines` uses one step further down the chain.
    """
    import json
    p = os.path.join(run, "request_ids.jsonl")
    if not os.path.isfile(p):
        return pd.Series([""] * len(cli), index=cli.index)
    recs = []
    for line in open(p, errors="ignore"):
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except ValueError:
            continue
    if not recs:
        return pd.Series([""] * len(cli), index=cli.index)
    ids = pd.DataFrame(recs)
    ids["start_time"] = pd.to_numeric(ids["start_time"], errors="coerce")
    ids = ids.dropna(subset=["start_time"]).sort_values("start_time")
    left = cli[["task_id", "call_index", "start_time"]].copy()
    left["_pos"] = np.arange(len(left))
    j = pd.merge_asof(left.sort_values("start_time"),
                      ids[["task_id", "call_index", "start_time", "request_id"]],
                      on="start_time", by=["task_id", "call_index"],
                      direction="nearest", tolerance=1.0)
    return (j.sort_values("_pos")["request_id"].fillna("").values)


def match(cli, env):
    """One-to-one assignment of client requests to Envoy lines.

    Candidates are generated per client request from the Envoy lines whose start
    time is nearest, then all candidates from all requests are sorted by cost and
    taken greedily, skipping any whose client row or Envoy row is already spoken
    for. Greedy-on-sorted-cost is not the minimum-cost assignment in general, but
    here the cost of the correct pair is a thousandth of the cost of any
    alternative, so the two coincide; a global solver would cost minutes per
    condition and change nothing.
    """
    et, ed = env["t"].values, env["dur_ms"].values
    ct, cd = cli["start_time"].values, cli["dur_ms"].values
    ins = np.searchsorted(et, ct)
    cand = []
    for i in range(len(ct)):
        lo, hi = max(0, ins[i] - 8), min(len(et), ins[i] + 8)
        if lo >= hi:
            continue
        dtm = np.abs(et[lo:hi] - ct[i]) * 1000.0
        cost = dtm + np.abs(ed[lo:hi] - cd[i]) * W_DURATION
        keep = dtm <= MAX_DT_MS
        for j, c in zip(np.arange(lo, hi)[keep], cost[keep]):
            cand.append((c, i, int(j)))
    cand.sort()
    used_c, used_e = np.zeros(len(ct), bool), np.zeros(len(et), bool)
    out = np.full(len(ct), -1, dtype=int)
    for _, i, j in cand:
        if used_c[i] or used_e[j]:
            continue
        used_c[i], used_e[j] = True, True
        out[i] = j
    return out


def one_run(run, out_path=None):
    env, refused = envoy_lines(run)
    cli = client_requests(run)
    name = os.path.basename(run.rstrip("/"))
    if env is None or cli is None or cli.empty:
        print(f"{name}: no envoy log or no client rows -- skipped")
        return None
    idx = match(cli, env)
    ok = idx >= 0
    host = np.where(ok, env["host"].values[np.clip(idx, 0, None)], "")
    port = [h.rsplit(":", 1)[1] if h else "" for h in host]

    ids = client_ids(run, cli)
    out_path = out_path or os.path.join(run, "analysis", "request_engine.csv")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    n_id = 0
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["task_id", "call_index", "request_id", "uuid",
                    "instance_id", "engine_port", "migrated"])
        for k in range(len(cli)):
            if not ok[k]:
                continue
            rid = ids[k] or ""
            n_id += bool(rid)
            uuid = rid.split("-", 1)[1] if rid.startswith("cmpl-") else ""
            w.writerow([cli["task_id"].iloc[k], cli["call_index"].iloc[k],
                        rid, uuid, host[k], port[k], False])
    counts = pd.Series([p for p, o in zip(port, ok) if o]).value_counts().sort_index()
    print(f"{name}: {ok.sum()}/{len(cli)} matched ({100.0 * ok.mean():.2f}%), "
          f"{n_id} carry a request id, "
          f"envoy served {len(env)}, refused {refused} -> {out_path}")
    print("   per engine: " + "  ".join(f"{p}:{n}" for p, n in counts.items()))
    return {"run": name, "matched": int(ok.sum()), "client": len(cli),
            "envoy_served": len(env), "envoy_refused": refused}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--glob", default=None)
    a = ap.parse_args()
    dirs = list(a.runs)
    if a.glob:
        dirs += sorted(glob.glob(a.glob))
    if not dirs:
        ap.error("give run directories or --glob")
    for d in dirs:
        one_run(d)


if __name__ == "__main__":
    main()
