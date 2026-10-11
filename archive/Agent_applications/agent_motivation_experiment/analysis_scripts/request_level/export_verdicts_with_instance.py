#!/usr/bin/env python3
"""Per-request ladder95 verdicts with the instance that served each request.

Writes, for each run, `<run>_with_instance.csv`: every column of
`results/aggregate_analysis/ladder95/verdicts/<run>.csv`, plus

  engine_port     8000-8003, the instance that served the request; empty when
                  there is none (see engine_status)
  engine_status   attributed   -- tied to an instance through the dispatch log
                  rejected     -- refused before routing, so no instance exists
                  not_found    -- admitted, but its dispatch line is missing
                                  from the scheduler log (lost under load)

The join is `exp41_engine_view.attribute_engines`: request ids from
`request_ids.jsonl`, matched to `analysis/request_engine.csv`, and to the client
rows by (task_id, call_index) with start_time nearest within 2 s, admitted rows
only. The verdicts and the loader's rows are then joined on
(task_id, call_index, iteration), which is unique in both.

    FS_SWE_TBT_MS=75 FS_SWE_TTFT_S=7 \\
      python3 analysis_scripts/request_level/export_verdicts_with_instance.py \\
      --runs <run> [<run> ...] --out <dir>
"""
import argparse
import importlib.util
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
spec = importlib.util.spec_from_file_location(
    "ev", os.path.join(HERE, "exp41_engine_view.py"))
EV = importlib.util.module_from_spec(spec)
spec.loader.exec_module(EV)
VERDICTS = os.path.join(ROOT, "results", "aggregate_analysis", "ladder95",
                        "verdicts")
KEYS = ["task_id", "call_index", "iteration"]


def one(run, out_dir):
    rd = os.path.join(ROOT, "results", run)
    v = pd.read_csv(os.path.join(VERDICTS, run + ".csv"))
    if v.duplicated(KEYS).any():
        sys.exit(f"{run}: verdict keys are not unique, refusing to join")
    r = EV.load_run(rd)
    j, n_adm = EV.attribute_engines(rd, r)
    if j.duplicated(KEYS).any():
        sys.exit(f"{run}: attributed keys are not unique, refusing to join")
    m = v.merge(j[KEYS + ["engine_port"]], on=KEYS, how="left")
    if len(m) != len(v):
        sys.exit(f"{run}: the join changed the row count {len(v)} -> {len(m)}")
    rej = m["rejected"].astype(bool)
    if m.loc[rej, "engine_port"].notna().any():
        sys.exit(f"{run}: a rejected request was given an instance")
    m["engine_port"] = m["engine_port"].astype("Int64")
    m["engine_status"] = "attributed"
    m.loc[rej, "engine_status"] = "rejected"
    m.loc[~rej & m["engine_port"].isna(), "engine_status"] = "not_found"
    out = os.path.join(out_dir, run + "_with_instance.csv")
    if os.path.exists(out):
        sys.exit(f"{out} exists; not overwriting")
    m.to_csv(out, index=False)
    adm = int((~rej).sum())
    got = int((m["engine_status"] == "attributed").sum())
    print(f"  {run}: {len(m)} rows, admitted {adm}, attributed {got} "
          f"({100.0 * got / max(adm, 1):.1f}%), rejected {int(rej.sum())}")
    return m


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
