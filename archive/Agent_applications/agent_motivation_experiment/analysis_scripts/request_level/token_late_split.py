#!/usr/bin/env python3
"""Split the tokens that missed their deadline into the two reasons a token can
be late, under the cumulative-deadline rule the outcome figures score with.

Each token i of a request has its own deadline T + i*P, where T is the class's
first-token budget and P its per-token budget. Write a_i for the offset at which
token i arrived, measured from the request's submission.

    late(i)        a_i > T + i*P
    pace-caused    (a_i - a_0) >  i*P      the elapsed time SINCE the first token
                                           already exceeds what the pace allows,
                                           so the token is late whatever a_0 was
    head-caused    (a_i - a_0) <= i*P      the pace since the first token was
                                           inside budget; the token is late only
                                           because the first token itself was

The two are a partition of the late tokens, and the test is shift invariant:
moving a_0 earlier rescues a head-caused token and cannot rescue a pace-caused
one. Token 0 falls under head-caused by construction.

⚠ This counts TOKENS, not requests. A request that missed the 95% bar still has
tokens that arrived on time and they are counted here as on time; a request that
met the bar still contributes its late tokens. That is the same accounting the
per-token goodput in deadline_ladder_attainment.py uses.

⚠ The budgets are the per-token form of the agent class (7 s, 75 ms). Numbers
from this script cannot sit beside anything scored against the 30 s end-to-end
rule.
"""
import argparse, json, os, sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
BUDGETS = {"chat": (5.0, 50.0), "deepresearch": (10.0, 100.0), "swe": (7.0, 75.0)}
WARMUP_S, DRAIN_S = 60.0, 60.0


# The canonical prefix map, the same one exp22_fluidserve.class_of uses. The
# task_id carries the workload's own prefix, not the class name.
def class_of(task_id):
    t = str(task_id)
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def truthy(df, col):
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].astype(str).str.lower().isin(("true", "1", "yes"))


def score(run):
    m = pd.read_csv(os.path.join(run, "metrics.csv"), low_memory=False)
    m = m[m["agent"] != "job_summary"].copy()
    t0 = m["start_time"].min()
    m["rel"] = m["start_time"] - t0
    end = min(m["end_time"].max() - t0, m["rel"].max())
    m = m[(m["rel"] >= WARMUP_S) & (m["rel"] < end - DRAIN_S)]
    keys = set(zip(m["task_id"], m["call_index"], m["iteration"]))

    agg = {}
    with open(os.path.join(run, "tbt_events.jsonl")) as f:
        for line in f:
            if '"agent": "request"' not in line:
                continue
            try:
                r = json.loads(line)
            except Exception:
                continue
            k = (r.get("task_id"), r.get("call_index"), r.get("iteration"))
            if k not in keys:
                continue
            cls = class_of(r.get("task_id"))
            if cls is None:
                continue
            T, P = BUDGETS[cls]
            ev = r.get("chunk_events") or []
            offs = [c.get("arrival_offset_ms") for c in ev]
            idx = [c.get("chunk_idx") for c in ev]
            if not offs or offs[0] is None:
                continue
            a0 = offs[0]
            n = ok = head = pace = 0
            for off, i in zip(offs, idx):
                n += 1
                if off is None:
                    pace += 1          # no timestamp: cannot be shown on time
                    continue
                if off <= T * 1000.0 + i * P:
                    ok += 1
                elif (off - a0) > i * P:
                    pace += 1
                else:
                    head += 1
            d = agg.setdefault(cls, [0, 0, 0, 0])
            d[0] += n; d[1] += ok; d[2] += head; d[3] += pace
    return agg


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True, help="label|dir[,dir]")
    a = ap.parse_args()
    print(f"{'arm':14s}{'class':14s}{'tokens':>13}{'on time':>9}"
          f"{'late':>9}{'  ├ TTFT 탓':>12}{'  └ TBT 탓':>11}")
    for spec in a.arm:
        label, dirs = spec.split("|")
        tot = {}
        for d in dirs.split(","):
            for cls, v in score(d).items():
                t = tot.setdefault(cls, [0, 0, 0, 0])
                for i in range(4):
                    t[i] += v[i]
        allv = [sum(t[i] for t in tot.values()) for i in range(4)]
        for cls in ("chat", "deepresearch", "swe", "ALL"):
            v = allv if cls == "ALL" else tot.get(cls)
            if not v or v[0] == 0:
                continue
            n, ok, head, pace = v
            late = head + pace
            print(f"{label if cls=='chat' else '':14s}{cls:14s}{n:13,}"
                  f"{100*ok/n:8.1f}%{100*late/n:8.1f}%"
                  f"{100*head/max(late,1):11.1f}%{100*pace/max(late,1):10.1f}%")
        print()


if __name__ == "__main__":
    main()
