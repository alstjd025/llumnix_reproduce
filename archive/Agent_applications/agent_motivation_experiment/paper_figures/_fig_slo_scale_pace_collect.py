#!/usr/bin/env python3
"""Child process: score ONE budget scale and print its rows as JSON.

Why a child. `exp22_fluidserve` reads the budgets into module-level constants at
IMPORT time, so one process can score exactly one scale. The parent runs one of
these per scale with that scale's six budgets in the environment.

Two quantities come out per (arm, scale):
  attainment  every arrival is the denominator; rejected and unfinished are
              violations. Taken from the repository's own scorer rather than
              recomputed here.
  pace        the median per-token time actually delivered to chat, measured
              from the client's own columns as (e2e - first token) / (tokens-1)
              over requests that were admitted and ran to completion. chat is
              the class that sets the gate on any instance holding it, so this
              is the quantity the budget is supposed to control.
"""
import glob, json, os, sys, warnings
warnings.filterwarnings("ignore")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts"))
os.chdir(ROOT)
import numpy as np
import pandas as pd
import all_arrivals_attainment as A
from exp22_fluidserve import load_run, SLO_RULES
import json as _json


def batch_p50(run_dir):
    """Fleet-summed decode batch, median over the loaded part of the run.

    Concurrency is not an independent quantity -- by Little's law it is the
    admitted rate times the mean time a request stays -- but it is the one that
    says how much of the engine a policy is actually occupying, which the
    request-level columns never show. Scrapes where the fleet sum is zero are the
    idle head and tail of the collection window and are dropped (CLAUDE.md
    group E: a quantile over the whole window describes a fleet that is idle half
    the time).
    """
    per = []
    try:
        fh = open(os.path.join(run_dir, "server_metrics", "scheduler.jsonl"))
    except OSError:
        return None
    for line in fh:
        try:
            o = _json.loads(line)
        except Exception:
            continue
        v = [o[k] for k in o
             if k.split("|")[0] == "instance_cms_decode_batch_size"
             and isinstance(o[k], (int, float))]
        if v and sum(v) > 0:
            per.append(sum(v))
    return float(np.percentile(per, 50)) if per else None

ARMS = [("fsv3capgnofrct75", "FluidServe"), ("fsv3capgnofrct75k", "FluidServe"),
        ("llmdslot75", "llm-d"), ("polyservept75", "PolyServe"),
        ("slot75", "Llumnix SLO")]


def pace(run_dir):
    d = load_run(run_dir)
    d = d[d["agent"] != "job_summary"]
    ok = d[~d["is_rejected"].fillna(False).astype(bool)]
    for f in ("is_server_terminated", "is_error", "is_timeout", "is_job_timeout"):
        if f in ok:
            ok = ok[~ok[f].fillna(False).astype(bool)]
    c = ok[(ok["class"] == "chat") & (ok["output_tokens"] > 1)]
    if not len(c):
        return None
    t = c["first_token_latency"].astype(float) * 1000
    p = (c["latency"].astype(float) * 1000 - t) / (c["output_tokens"].astype(float) - 1)
    return float(np.median(p))


def main():
    klabel, pattern, session = sys.argv[1], sys.argv[2], sys.argv[3]
    rows = []
    for arm, label in ARMS:
        ds = [x for x in sorted(glob.glob(pattern % arm)) if "PRERUN" not in x]
        if not ds:
            continue
        att, pc, gp, bt, tp = [], [], [], [], []
        for d in ds:
            r = A.one_run(d)
            if not r:
                continue
            att.append(r["all_arrivals"])
            gp.append(r["goodput_tok_s"])
            # Token throughput: EVERY output token the engines produced in the
            # window, whether or not the request that produced it met its SLO.
            # goodput / throughput is then the share of the computation that was
            # useful. A rejected request contributes nothing to either; a request
            # that ran and missed contributes its whole output to throughput and
            # nothing to goodput, which is what makes the ratio a waste measure.
            _df = load_run(d)
            tok = pd.to_numeric(_df["output_tokens"], errors="coerce").fillna(0)
            dur = _df["rel"].max() - _df["rel"].min()
            tp.append(float(tok.sum() / dur) if dur > 0 else 0.0)
            v = pace(d)
            if v is not None:
                pc.append(v)
            b = batch_p50(d)
            if b is not None:
                bt.append(b)
        if not att:
            continue
        rows.append({"k": float(klabel), "arm": label, "session": session,
                     "n_repeats": len(att),
                     "attainment_pct": float(np.mean(att)),
                     "attainment_min": float(min(att)), "attainment_max": float(max(att)),
                     "goodput_tok_s": float(np.mean(gp)),
                     "throughput_tok_s": float(np.mean(tp)) if tp else None,
                     "conversion_pct": (100.0 * float(np.mean(gp)) / float(np.mean(tp))
                                        if tp and np.mean(tp) else None),
                     "goodput_min": float(min(gp)), "goodput_max": float(max(gp)),
                     "batch_p50": float(np.mean(bt)) if bt else None,
                     "pace_ms_per_token": float(np.mean(pc)) if pc else None,
                     "pace_min": float(min(pc)) if pc else None,
                     "pace_max": float(max(pc)) if pc else None,
                     "chat_budget_ms": float(SLO_RULES["chat"]["tbt"])})
    print(json.dumps(rows))


if __name__ == "__main__":
    main()
