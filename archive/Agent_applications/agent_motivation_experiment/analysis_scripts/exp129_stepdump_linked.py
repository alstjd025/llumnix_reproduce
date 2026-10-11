#!/usr/bin/env python3
"""EXP-129: build a LINKED per-step table from InstrumentedScheduler dumps.

Why this exists.  `sched_steps.jsonl` holds the interleaved step records of all
engine processes on the node (one file, one `open(path,"a")` per process), and
`interval_ms` is the ENTER-TO-ENTER gap between two consecutive schedule()
calls of the SAME process.  A row's interval therefore describes the work the
engine did between the previous step's entry and this step's entry -- i.e. the
PREVIOUS row's step composition, not its own.  Any analysis that pairs
interval_ms[k] with prefill_tokens_step[k] is off by one step.

This module rebuilds each engine's chain with the same predecessor test the
production fitter uses (gen_profiling_from_stepdump.link_chains: the
predecessor's entry time is t_wall - interval_ms/1000, matched within 2 ms) and
emits, for every linked step, its own features together with those of its
predecessor and successor so the lag can be chosen by measurement.

Read-only; writes a .npz cache next to nothing in the results tree.
"""
import argparse, collections, glob, json, os, sys
import numpy as np

# The predecessor test.  t_wall is time.time() sampled AFTER super().schedule()
# returns, while interval_ms is a perf_counter difference between schedule()
# ENTRIES.  So t_wall[k] - t_schedule_us[k] reconstructs step k's entry time,
# and entry[k-1] == entry[k] - interval_ms[k] holds to within clock jitter.
# The production fitter (gen_profiling_from_stepdump.link_chains) compares
# t_wall directly instead, so its residual is |t_schedule[k] - t_schedule[k-1]|
# and its 2 ms tolerance drops exactly the steps where scheduling time is
# volatile -- i.e. the heavily loaded ones.  That shatters the chains at high
# arrival rates (4,469 chains for 8 engines at rate 45) and, because a chain
# head looks like "no prefill anywhere behind me", it manufactures apparent
# runs of pure decode.  Reconstructing the entry time keeps the chains whole.
LINK_TOL_S = 0.0005

FIELDS = ("interval_ms", "kv_tokens", "n_running", "n_waiting", "n_decode",
          "n_prefill_reqs", "prefill_tokens_step", "total_sched_tokens",
          "t_schedule_us", "t_wall", "step")


def load_run(path):
    rows = []
    with open(path) as fh:
        for line in fh:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def entry(r):
    """Reconstructed schedule() entry time (seconds, time.time() epoch)."""
    return r["t_wall"] - (r.get("t_schedule_us") or 0.0) / 1e6


def link(rows):
    """Return index arrays prev_idx/next_idx (-1 = none) for one run."""
    by = collections.defaultdict(list)
    for i, r in enumerate(rows):
        by[r["step"]].append(i)
    n = len(rows)
    prev_idx = np.full(n, -1, np.int64)
    next_idx = np.full(n, -1, np.int64)
    for i, r in enumerate(rows):
        iv = r.get("interval_ms")
        if iv is None:
            continue
        cand = by.get(r["step"] - 1)
        if not cand:
            continue
        want = entry(r) - iv / 1000.0
        best = min(cand, key=lambda c: abs(entry(rows[c]) - want))
        if abs(entry(rows[best]) - want) < LINK_TOL_S:
            prev_idx[i] = best
            next_idx[best] = i
    return prev_idx, next_idx


def build(pattern, out):
    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"no step logs matched {pattern!r}")
    cols = {f: [] for f in FIELDS}
    pcols = {f: [] for f in ("kv_tokens", "n_decode", "prefill_tokens_step",
                             "n_running", "t_schedule_us", "interval_ms")}
    ncols = {"prefill_tokens_step": []}
    dcols = {"d_prev_prefill": [], "d_next_prefill": [], "chain_pos": [],
             "chain_id": [], "pf_frac_8": [], "pf_frac_32": [],
             "pf_tok_8": [], "pf_tok_32": []}
    runs, rates = [], []
    for path in files:
        run = os.path.basename(os.path.dirname(os.path.dirname(path)))
        rows = load_run(path)
        p, nx = link(rows)
        # walk each engine chain to get distance (in steps) to the nearest
        # prefill step backwards and forwards.  d_prev_prefill == 1 means the
        # immediate predecessor was a prefill step.
        nrow = len(rows)
        dprev = np.full(nrow, 1 << 30, np.int64)
        dnext = np.full(nrow, 1 << 30, np.int64)
        cpos = np.full(nrow, -1, np.int64)
        cid = np.full(nrow, -1, np.int64)
        hist_f = np.zeros(nrow)
        hist_t = np.zeros(nrow)
        store = {k: np.zeros(nrow) for k in
                 ("pf_frac_8", "pf_frac_32", "pf_tok_8", "pf_tok_32")}
        heads = [i for i in range(nrow) if p[i] < 0]
        for ci, h in enumerate(heads):
            chain, cur = [], h
            while cur >= 0:
                chain.append(cur)
                cur = nx[cur]
            last = -(1 << 29)
            for j, i in enumerate(chain):
                cpos[i] = j
                cid[i] = ci
                dprev[i] = j - last
                if rows[i].get("prefill_tokens_step", 0):
                    last = j
            # prefill history over the previous K steps of THIS engine.  Counted
            # in steps and tokens, never in wall time: a duty defined as "share
            # of recent wall time spent prefilling" is built out of interval_ms,
            # which is the regression target, and would make any fit that uses
            # it circular.
            pf = np.array([1.0 if rows[i].get("prefill_tokens_step", 0) else 0.0
                           for i in chain])
            tk = np.array([float(rows[i].get("prefill_tokens_step", 0) or 0)
                           for i in chain])
            for K, fname, tname in ((8, "pf_frac_8", "pf_tok_8"),
                                    (32, "pf_frac_32", "pf_tok_32")):
                cf = np.concatenate([[0.0], np.cumsum(pf)])
                ct = np.concatenate([[0.0], np.cumsum(tk)])
                idx = np.arange(len(chain))
                lo = np.maximum(0, idx - K)
                cnt = np.maximum(1, idx - lo)
                hist_f[np.array(chain, np.int64)] = (cf[idx] - cf[lo]) / cnt
                hist_t[np.array(chain, np.int64)] = (ct[idx] - ct[lo]) / cnt
                store[fname][np.array(chain, np.int64)] = hist_f[np.array(chain, np.int64)]
                store[tname][np.array(chain, np.int64)] = hist_t[np.array(chain, np.int64)]
            nxt = (1 << 29)
            for j in range(len(chain) - 1, -1, -1):
                i = chain[j]
                dnext[i] = nxt - j
                if rows[i].get("prefill_tokens_step", 0):
                    nxt = j
        keep = np.where(p >= 0)[0]
        print(f"  {run:<44s} {len(rows):>8,d} steps  linked {len(keep):>8,d} "
              f"({100*len(keep)/max(1,len(rows)):.1f}%)")
        for f in FIELDS:
            cols[f].append(np.array([rows[i].get(f) if rows[i].get(f) is not None
                                     else np.nan for i in keep], float))
        for f in pcols:
            pcols[f].append(np.array([rows[p[i]].get(f) if rows[p[i]].get(f) is not None
                                      else np.nan for i in keep], float))
        dcols["d_prev_prefill"].append(np.minimum(dprev[keep], 1 << 20).astype(float))
        dcols["d_next_prefill"].append(np.minimum(dnext[keep], 1 << 20).astype(float))
        dcols["chain_pos"].append(cpos[keep].astype(float))
        dcols["chain_id"].append(cid[keep].astype(float))
        for k in ("pf_frac_8", "pf_frac_32", "pf_tok_8", "pf_tok_32"):
            dcols[k].append(store[k][keep])
        ncols["prefill_tokens_step"].append(np.array(
            [rows[nx[i]]["prefill_tokens_step"] if nx[i] >= 0 else np.nan
             for i in keep], float))
        runs.append(np.full(len(keep), files.index(path), np.int32))
        rate = run.split("_r")[-1].split("_")[0]
        rates.append(np.full(len(keep), float(rate) if rate.replace(".", "").isdigit() else np.nan))
    d = {f"s_{f}": np.concatenate(cols[f]) for f in FIELDS}
    d.update({f"p_{f}": np.concatenate(pcols[f]) for f in pcols})
    d["n_prefill_tokens_step"] = np.concatenate(ncols["prefill_tokens_step"])
    d.update({k: np.concatenate(v) for k, v in dcols.items()})
    d["run_id"] = np.concatenate(runs)
    d["rate"] = np.concatenate(rates)
    d["run_names"] = np.array([os.path.basename(os.path.dirname(os.path.dirname(f)))
                               for f in files])
    np.savez_compressed(out, **d)
    print(f"  wrote {out}  ({len(d['s_interval_ms']):,} linked steps)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-glob", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    build(a.results_glob, a.out)
