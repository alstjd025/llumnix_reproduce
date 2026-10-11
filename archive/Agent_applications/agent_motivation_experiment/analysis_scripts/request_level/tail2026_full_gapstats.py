#!/usr/bin/env python3
"""Per-request and pooled per-token gap statistics for the EXP-82 runs.

WHY THIS EXISTS RATHER THAN THE CLIENT COLUMNS. `metrics.csv` already carries
`tbt_p50_ms` .. `tbt_p95_ms`, and `tail2026_quantile_ladder.py` substitutes
those into the scoring. Those columns are the RAW client percentiles: the client
charged each streamed chunk's inter-arrival gap as
`inter_arrival_ms / count_tokens(chunk_text)` repeated that many times, and
tokenising a chunk out of context splits it into about 1.92 pieces, so every one
of those percentiles is low by roughly that factor while `load_run`'s `itl_ms`
is the corrected mean. A table that puts the corrected mean next to the raw
percentiles is comparing two different scales, which is the caveat
`tail2026_quantile_ladder.py` states about itself and cannot remove.

This script removes it by going back to `tbt_events.jsonl` and taking the gap
between consecutive CHUNK ARRIVALS as the per-token time directly, with no
per-chunk token estimate involved. That is exact on this deployment rather than
approximate: chunks per token is 0.996 over the sample measured in
`fluidserve-implementation.md` 32, i.e. the engine emits one token per chunk, so
the gap between two chunks IS the gap between two tokens. Every statistic below
-- the request's own p50/p90/p95/p99, its mean, and the pooled distribution over
all gaps -- is therefore on the SAME scale as `load_run`'s corrected mean, and
`gap_mean_ms` is written out so that agreement can be checked instead of
assumed.

WHY THE RAW GAPS ARE USED WITHOUT BURST CORRECTION. On runs before EXP-82 the
Llumnix Go gateway exhausted its CFS CPU quota, the kernel stopped the whole
container until the next 100 ms period boundary, and every live stream froze and
resumed together, delivering several tokens at once; 14.11% of gaps were under
5 ms. `tail2026_token_level.py` exists to spread those bursts back out. EXP-82
re-ran both arms with that fixed and the sub-5 ms fraction fell to 0.29%, so the
recorded gaps are the engine's own per-token behaviour and no correction is
applied here. The sub-1/sub-5/sub-16 ms fractions are still reported per run so
that claim is checkable per condition rather than taken on trust.

POPULATIONS, which are not the same for the two outputs:

  per-request CSV   every request row that survives `load_run`'s analysis
                    window and has at least two chunk arrivals, whatever its
                    outcome. Attainment scoring needs these, and it needs them
                    for requests that missed as well as requests that met.
  pooled JSON       COMPLETED requests only -- `is_server_terminated`,
                    `is_error`, `is_timeout`, `is_job_timeout` and rejected all
                    dropped, via `tail2026_quantile_ladder.completed` -- because
                    a truncated stream's gap series stops at the truncation and
                    its statistics are a lower bound and not a measurement.

The analysis window is `load_run`'s and is applied here, so the keys written out
are exactly the keys the table builder will score.

  python3 tail2026_full_gapstats.py                     # all EXP-82 runs
  python3 tail2026_full_gapstats.py --runs <dir> ...    # named runs
"""
import argparse
import glob
import json
import os
import sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run  # noqa: E402
from tail2026_quantile_ladder import completed  # noqa: E402

EXPDIR = "/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
RESULTS = os.path.join(EXPDIR, "results")
OUTDIR = os.path.join(RESULTS, "aggregate_analysis/tail_2026-08-16/gapcache_exp82")

# Both arms, both repeats, excluding the PRERUN warm-up directories that the
# driver writes before each llm-d condition.
RUN_GLOBS = ("*exp82r[12]_fspfx_m1_rpm_*", "*exp82r[12]_llmdslo_m1f_rpm_*")

CLASSES = ["chat", "deepresearch", "swe"]
# The pooled ladder. p99.9 is the deepest the reader asked for; p1 is kept
# because it is what says whether the burst artefact is gone.
POOL_PCTS = (1, 50, 75, 90, 95, 99, 99.9)
MIN_CHUNKS = 2          # two arrivals is one gap, the least that defines any of these


def run_dirs():
    out = []
    for g in RUN_GLOBS:
        for d in glob.glob(os.path.join(RESULTS, g)):
            if os.path.isdir(d) and "PRERUN" not in os.path.basename(d):
                out.append(d)
    return sorted(out)


def scan(run_dir):
    name = os.path.basename(run_dir.rstrip("/"))
    ev_path = os.path.join(run_dir, "tbt_events.jsonl")
    if not os.path.isfile(ev_path):
        return name, None, None

    rows = load_run(run_dir)
    if rows is None or rows.empty:
        return name, None, None
    rows = rows[rows["agent"].astype(str) == "request"].copy()
    if rows.empty:
        return name, None, None

    keep = set(zip(rows["task_id"].astype(str),
                   pd.to_numeric(rows["call_index"], errors="coerce")
                   .fillna(-1).astype(int)))
    comp = completed(rows)
    comp_keys = set(zip(comp["task_id"].astype(str),
                        pd.to_numeric(comp["call_index"], errors="coerce")
                        .fillna(-1).astype(int)))
    cls = dict(zip(zip(rows["task_id"].astype(str),
                       pd.to_numeric(rows["call_index"], errors="coerce")
                       .fillna(-1).astype(int)),
                   rows["class"]))

    per_req = []
    pooled = {c: [] for c in CLASSES}
    n_short = 0

    with open(ev_path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("agent") != "request":
                continue
            try:
                key = (str(d.get("task_id", "")), int(d.get("call_index")))
            except (TypeError, ValueError):
                continue
            if key not in keep:
                continue
            ev = d.get("chunk_events") or []
            if len(ev) < MIN_CHUNKS:
                n_short += 1
                continue
            off = np.fromiter((e["arrival_offset_ms"] for e in ev),
                              float, len(ev))
            g = np.diff(off)
            if not len(g) or not np.all(np.isfinite(g)) or g.min() < 0:
                continue
            q = np.percentile(g, [50, 90, 95, 99])
            per_req.append((key[0], key[1], cls.get(key, "?"), len(g),
                            float(g.mean()), float(q[0]), float(q[1]),
                            float(q[2]), float(q[3]), float(g.max())))
            if key in comp_keys:
                c = cls.get(key)
                if c in pooled:
                    pooled[c].append(g.astype(np.float32))

    pr = pd.DataFrame(per_req, columns=[
        "task_id", "call_index", "class", "n_gaps", "gap_mean_ms",
        "gap_p50_ms", "gap_p90_ms", "gap_p95_ms", "gap_p99_ms", "gap_max_ms"])

    meta = {"run": name, "n_requests_windowed": int(len(rows)),
            "n_with_gaps": int(len(pr)), "n_short_streams": int(n_short),
            "n_completed_windowed": int(len(comp))}
    # Pooled over every gap of every completed request, per class and with the
    # three classes concatenated. "all" is the population a paper means when it
    # prints "P90 TBT" without qualification.
    groups = {c: pooled[c] for c in CLASSES}
    groups["all"] = [a for c in CLASSES for a in pooled[c]]
    for gname, chunks in groups.items():
        if not chunks:
            continue
        a = np.concatenate(chunks)
        for p, v in zip(POOL_PCTS, np.percentile(a, POOL_PCTS)):
            meta["pool_%s_p%s" % (gname, str(p).replace(".", "_"))] = float(v)
        meta["pool_%s_mean" % gname] = float(a.mean())
        meta["pool_%s_max" % gname] = float(a.max())
        meta["pool_%s_n" % gname] = int(a.size)
        meta["pool_%s_nreq" % gname] = int(len(chunks))
        # The burst check. On the defective runs sub-5 ms was 14.11%; if these
        # are not small the gaps are the gateway's stall and not the engine's
        # per-token time.
        meta["pool_%s_frac_sub1" % gname] = float((a < 1.0).mean())
        meta["pool_%s_frac_sub5" % gname] = float((a < 5.0).mean())
        meta["pool_%s_frac_sub16" % gname] = float((a < 16.0).mean())
        del a
    return name, pr, meta


def one(run_dir):
    try:
        name, pr, meta = scan(run_dir)
    except Exception as e:                      # noqa: BLE001
        return {"run": os.path.basename(run_dir.rstrip("/")),
                "error": f"{type(e).__name__}: {e}"}
    if pr is None:
        return {"run": name, "error": "no usable rows"}
    os.makedirs(OUTDIR, exist_ok=True)
    pr.to_csv(os.path.join(OUTDIR, name + "_perreq.csv.gz"), index=False)
    with open(os.path.join(OUTDIR, name + "_pooled.json"), "w") as f:
        json.dump(meta, f, indent=1)
    return meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="*", default=None)
    ap.add_argument("--jobs", type=int, default=10)
    a = ap.parse_args()

    dirs = a.runs if a.runs else run_dirs()
    print(f"{len(dirs)} runs")
    os.makedirs(OUTDIR, exist_ok=True)
    todo = [d for d in dirs
            if not os.path.isfile(os.path.join(
                OUTDIR, os.path.basename(d.rstrip("/")) + "_pooled.json"))]
    print(f"{len(todo)} to scan, {len(dirs) - len(todo)} already cached")
    with Pool(min(a.jobs, max(1, len(todo)))) as p:
        for m in p.imap_unordered(one, todo):
            if "error" in m:
                print(f"  FAILED {m['run']}: {m['error']}")
            else:
                print(f"  {m['run']}: {m['n_with_gaps']} requests with gaps, "
                      f"pooled n={m.get('pool_all_n', 0)}, "
                      f"sub5={100 * m.get('pool_all_frac_sub5', float('nan')):.2f}%")
    print(f"cache in {OUTDIR}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
