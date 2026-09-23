#!/usr/bin/env python3
"""Build Llumnix latency-profiling tables (ttft.json / tpot.json) from EXP-16
per-step scheduler dumps.

Llumnix's SLO-aware policies need two profiling tables, loaded by
`policy.GetLatencyPredictor()` (pkg/scheduler/policy/predict_utils.go).  Without
them the scheduler calls klog.Fatalf and never starts, so this is a hard
prerequisite for any `--scheduling-policy slo` (and for PolyServe).

    ttft.json  TtftData{results:[{tokens_num, p50, ...}]}
               -> InterpolationPredictor.AddSample(tokens_num, 0, p50)
               Queried as the cost of ONE scheduler step that prefills
               `tokens_num` tokens.  predictTtftLatencyByChunkPrefill() splits a
               pending prefill into ceil(n / maxNumBatchedTokens) chunks and
               sums Predict(chunk) + one decode step per chunk, so the table is
               per-STEP cost, not whole-prompt TTFT.

    tpot.json  ITLData{results:[{batch_size, tokens_per_request, p50, ...}]}
               -> AddSample(batch_size, tokens_per_request, p50)
               Cost of one decode-only step with `batch_size` running requests
               averaging `tokens_per_request` KV tokens each.

Source data
-----------
EXP-16 ran `llumnix_sched.InstrumentedScheduler`, which is stock vLLM
AsyncScheduler ordering plus a per-step JSONL log -- it adds NO scheduling
logic.  In particular the token budget stays at the engine's fixed
max_num_batched_tokens, unlike deadline_sched.DeadlineScheduler which resizes
chunks every step; QoServe runs are therefore deliberately NOT accepted here.

Measurement caveat that drives the whole design
-----------------------------------------------
`interval_ms` is the enter-to-enter gap between schedule() calls.  Under async
scheduling the scheduler runs a step ahead of the model, so this gap is BIMODAL:
either ~0.5 ms (ran ahead, did not block) or the true forward time (blocked on a
full output queue).  Per-step attribution is therefore unusable in general.  Two
regimes escape it:

  decode  In a long homogeneous decode phase the scheduler is throttled to the
          forward rate, so the MEDIAN interval over the phase is the true step
          time.  377k such steps -> the TPOT table is measured, cell by cell.

  prefill Sustained prefill-saturated windows only ever occur at a full chunk
          (chunked prefill always fills the budget; partial chunks happen only
          on a request's last chunk).  So prefill gives exactly ONE measured
          operating point, obtained as a token/second RATE over those windows,
          which is immune to per-step attribution.  The rest of the TTFT curve
          is a physics-anchored linear model through that point.

  => the TTFT table is provisional.  Replace it with a dedicated idle-engine
     prompt-length sweep (upstream's own tables were built that way: 3 reps per
     prompt length) before drawing conclusions that depend on TTFT accuracy.

Usage
-----
    python3 ms_dev/scripts/gen_profiling_from_stepdump.py \
        --results-glob '<results>/*exp16*/server_metrics/sched_steps.jsonl' \
        --out-dir deploy/profiling/llama31-70b-b200-tp2
"""

import argparse
import bisect
import collections
import glob
import json
import math
import os
import sys

import numpy as np

# ---------------------------------------------------------------------------
# Grid axes.  The Go InterpolationPredictor finds a bounding box from the
# MARGINAL axis values and then requires all four corners to exist in the map;
# a missing corner is an error that surfaces as +Inf predicted latency and
# silently drops the instance from every SLO filter.  So the emitted grid must
# be a COMPLETE cartesian product, and must span the whole reachable range.
# ---------------------------------------------------------------------------
BATCH_AXIS = [1, 2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512,
              768, 1024, 1536, 2048]
# KV tokens per decoding request.  NOTE this is the *logical* count (sum of
# num_computed_tokens), which prefix caching inflates well past physical KV
# capacity -- EXP-16 saw 7.0M aggregate on a fleet whose physical capacity is
# ~0.6M.  That is fine and intended: Llumnix's allDecodesTokensNum is the same
# logical quantity, so table and query agree.
TOKENS_AXIS = [8, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384,
               32768, 65536]
# Prefill chunk sizes.  Engine max_num_batched_tokens is 8192, so Predict() is
# only ever called with <= 8192; the tail past it exists so a metadata mismatch
# (Llumnix's own --max-num-batched-tokens defaults to 65536) cannot produce an
# out-of-range error.
PREFILL_AXIS = [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 768, 1024, 1536, 2048,
                3072, 4096, 5120, 6144, 7168, 8192, 12288, 16384, 24576,
                32768, 49152, 65536]

MIN_SAMPLES = 30        # per-cell samples required to trust an empirical p50
PREFILL_SATURATED = 6144  # chunk size above which a window counts as saturated
PREFILL_RUN = 4         # consecutive saturated steps required to form a window
LINK_TOL_S = 0.002      # tolerance when re-linking a step to its predecessor


# ---------------------------------------------------------------------------
# load + per-engine chain reconstruction
# ---------------------------------------------------------------------------
# Set from --allow-missing-prefill-anchor; see prefill_anchor().
ALLOW_MISSING_PREFILL_ANCHOR = False


def load_steps(pattern):
    """Read every step record.  All four engines write into one host-mounted
    file, so records interleave; `_run` plus the step counter disambiguates."""
    rows, bad, files = [], 0, sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"no step logs matched {pattern!r}")
    for f in files:
        run = os.path.basename(os.path.dirname(os.path.dirname(f)))
        if "qoserve" in run or "deadline" in run:
            sys.exit(f"refusing {run}: DeadlineScheduler resizes chunks per step")
        n0 = len(rows)
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    bad += 1
                    continue
                r["_run"] = run
                r["_next"] = None
                r["_prev"] = None
                rows.append(r)
        print(f"  {run:<48s} {len(rows)-n0:>8,d} steps")
    print(f"  {'TOTAL':<48s} {len(rows):>8,d} steps ({bad} unparseable)")
    return rows


def link_chains(rows):
    """Rebuild each engine's step chain.  interval_ms gives the predecessor's
    entry time exactly, which picks the right one out of the four records that
    share a step number."""
    by = collections.defaultdict(list)
    for r in rows:
        by[(r["_run"], r["step"])].append(r)
    linked = 0
    for r in rows:
        iv = r.get("interval_ms")
        if iv is None:
            continue
        cand = by.get((r["_run"], r["step"] - 1))
        if not cand:
            continue
        want = r["t_wall"] - iv / 1000.0
        best = min(cand, key=lambda c: abs(c["t_wall"] - want))
        if abs(best["t_wall"] - want) < LINK_TOL_S:
            r["_prev"] = best
            best["_next"] = r
            linked += 1
    chains = []
    for s in rows:
        if s["_prev"] is None:
            ch, cur = [], s
            while cur is not None:
                ch.append(cur)
                cur = cur["_next"]
            if len(ch) >= PREFILL_RUN + 1:
                chains.append(ch)
    print(f"  linked {linked:,}/{len(rows):,} steps into {len(chains):,} usable chains")
    return chains


# ---------------------------------------------------------------------------
# decode surface
# ---------------------------------------------------------------------------
def snap(value, axis):
    """Nearest axis node in log space (the axes are ~geometric)."""
    lv = math.log(max(value, 1e-9))
    i = bisect.bisect_left([math.log(a) for a in axis], lv)
    if i == 0:
        return axis[0]
    if i >= len(axis):
        return axis[-1]
    lo, hi = axis[i - 1], axis[i]
    return lo if (lv - math.log(lo)) <= (math.log(hi) - lv) else hi


def decode_cells(rows):
    """Empirical p50 step time per (batch, tokens/req) cell, from decode-only
    steps.  Only these are trustworthy: see the module docstring."""
    buckets = collections.defaultdict(list)
    for r in rows:
        iv = r.get("interval_ms")
        nd = r.get("n_decode")
        kv = r.get("kv_tokens")
        if iv is None or not nd or kv is None or kv < 0:
            continue
        if r.get("prefill_tokens_step"):      # decode-only steps only
            continue
        buckets[(snap(nd, BATCH_AXIS), snap(kv / nd, TOKENS_AXIS))].append(iv)
    cells = {k: (float(np.percentile(v, 50)), v)
             for k, v in buckets.items() if len(v) >= MIN_SAMPLES}
    print(f"  decode-only steps binned: {sum(len(v) for v in buckets.values()):,}")
    print(f"  cells with >={MIN_SAMPLES} samples: {len(cells)} / "
          f"{len(BATCH_AXIS)*len(TOKENS_AXIS)}")
    return cells


def fit_decode_model(cells):
    """t = c0 + c_kv*(batch*tokens) + c_nd*batch, fitted on cell medians so each
    cell weighs equally and the heavy interval tail cannot drag the fit."""
    b = np.array([k[0] for k in cells], float)
    t = np.array([k[1] for k in cells], float)
    y = np.array([v[0] for v in cells.values()], float)
    X = np.column_stack([np.ones(len(y)), b * t, b])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    rel = np.abs(pred - y) / y
    print(f"  model: t = {coef[0]:.3f} + {coef[1]:.4e}*(B*T) + {coef[2]:.4f}*B  "
          f"R2={r2:.4f}  rel-err p50={rel.mean()*0+np.percentile(rel,50)*100:.1f}% "
          f"p90={np.percentile(rel,90)*100:.1f}%")
    return coef


def fill_grid(cells, coef):
    """Complete the grid.  Measured cells are kept verbatim; the rest take the
    model value scaled by an inverse-distance-weighted mean of the neighbouring
    cells' log-residuals, so filled cells join continuously onto measured ones
    and far extrapolation decays to the plain model."""
    def model(b, t):
        return coef[0] + coef[1] * b * t + coef[2] * b

    known = [(math.log(b), math.log(t), math.log(v[0] / model(b, t)))
             for (b, t), v in cells.items()]
    grid, source = {}, {}
    for b in BATCH_AXIS:
        for t in TOKENS_AXIS:
            if (b, t) in cells:
                grid[(b, t)] = cells[(b, t)][0]
                source[(b, t)] = "measured"
                continue
            lb, lt = math.log(b), math.log(t)
            d = sorted(((lb - kb) ** 2 + (lt - kt) ** 2, res) for kb, kt, res in known)[:3]
            w = [(1.0 / (dist + 0.25), res) for dist, res in d]
            resid = sum(wi * ri for wi, ri in w) / sum(wi for wi, _ in w)
            grid[(b, t)] = max(1e-3, model(b, t) * math.exp(resid))
            source[(b, t)] = "modelled"
    return grid, source


def isotonize(grid):
    """Force the surface non-decreasing along both axes.  A decode step cannot
    get cheaper when the batch or the KV per request grows, and a violation
    would let a selector rank a MORE loaded instance as faster.  Repeated
    pairwise averaging of violators is the 2-D analogue of pool-adjacent-
    violators: it converges and moves values as little as possible."""
    M = np.array([[grid[(b, t)] for t in TOKENS_AXIS] for b in BATCH_AXIS], float)
    before = M.copy()
    for _ in range(2000):
        worst = 0.0
        for ax in (0, 1):
            A = M if ax == 0 else M.T
            lo, hi = A[:-1], A[1:]
            bad = lo > hi
            if bad.any():
                worst = max(worst, float((lo - hi)[bad].max()))
                mid = (lo[bad] + hi[bad]) / 2.0
                lo[bad] = mid
                hi[bad] = mid
        if worst < 1e-9:
            break
    moved = np.abs(M - before)
    n = int((moved > 1e-6).sum())
    print(f"  isotonic: adjusted {n} cells, max change {moved.max():.2f} ms "
          f"({(moved/before).max()*100:.1f}%)")
    return {(b, t): float(M[i, j])
            for i, b in enumerate(BATCH_AXIS) for j, t in enumerate(TOKENS_AXIS)}


# ---------------------------------------------------------------------------
# prefill anchor
# ---------------------------------------------------------------------------
def prefill_anchor(chains):
    """Sustained prefill throughput, as tokens/second over windows of
    consecutive saturated steps.  Dividing summed tokens by summed wall time
    averages the async run-ahead out instead of trying to attribute it."""
    tok = sec = 0.0
    wins = steps = 0
    for ch in chains:
        i = 0
        while i < len(ch):
            if ch[i].get("prefill_tokens_step", 0) >= PREFILL_SATURATED:
                j = i
                while j < len(ch) and ch[j].get("prefill_tokens_step", 0) >= PREFILL_SATURATED:
                    j += 1
                if j - i >= PREFILL_RUN:
                    span = ch[j - 1]["t_wall"] - ch[i]["t_wall"]
                    if span > 0.02:
                        tok += sum(ch[m]["prefill_tokens_step"] for m in range(i, j - 1))
                        sec += span
                        steps += j - 1 - i
                        wins += 1
                i = j
            else:
                i += 1
    if not wins:
        # EXP-129. This anchor feeds the PREFILL table only, and the documented
        # order for building a profile overwrites that table afterwards with the
        # idle-engine sweep from measure_ttft_sweep.py, which the existing
        # profile directories show was the order they were built in. So a set of
        # dumps with no saturated window is not a reason to refuse to write
        # tpot.json, and refusing costs a whole profiling stage: the decode law
        # is identified in the UNSATURATED region, and the saturated conditions
        # that would supply this anchor are exactly the ones that destroy it. On
        # Qwen2.5-14B the median relative error of the decode surface is 17.9%
        # on unsaturated dumps and 66.9-73.4% as soon as one saturated condition
        # is added.
        if ALLOW_MISSING_PREFILL_ANCHOR:
            print("  no sustained prefill window -- writing tpot.json only "
                  "(--allow-missing-prefill-anchor)")
            return None, None
        sys.exit("no sustained prefill window found -- cannot anchor the TTFT table")
    tps = tok / sec
    chunk = tok / steps
    print(f"  {wins} saturated windows, {sec:.1f}s, {tok/1e6:.2f}M prefill tokens")
    print(f"  sustained prefill throughput {tps:,.0f} tok/s; "
          f"mean chunk {chunk:,.0f} tok -> {chunk/tps*1000:.1f} ms/step")
    return tps, chunk


def prefill_table(tps, chunk, floor_ms):
    """t(c) = floor + c/rate.  `floor` is the decode-step floor (the same weight
    read every step pays) and `rate` is set so the model reproduces the single
    measured operating point exactly."""
    step_ms = chunk / tps * 1000.0
    rate = chunk / (step_ms - floor_ms) * 1000.0     # tokens per second
    print(f"  t(c) = {floor_ms:.2f} ms + c / {rate:,.0f} tok/s   "
          f"[anchored at c={chunk:,.0f} -> {step_ms:.1f} ms]")
    return {c: floor_ms + c / rate * 1000.0 for c in PREFILL_AXIS}, rate


# ---------------------------------------------------------------------------
# emit + validate
# ---------------------------------------------------------------------------
def stats(p50, samples=None):
    if samples:
        a = np.array(samples, float)
        return dict(mean=float(a.mean()), std=float(a.std()),
                    min=float(a.min()), max=float(a.max()),
                    p50=float(p50), p95=float(np.percentile(a, 95)),
                    p99=float(np.percentile(a, 99)), n=len(a))
    return dict(mean=p50, std=0.0, min=p50, max=p50,
                p50=p50, p95=p50, p99=p50, n=0)


def go_predict(table, a, b, a_axis, b_axis):
    """Faithful replay of Go's findBoundingBox + 4-corner bilinear lookup, so we
    can prove every realistic query resolves instead of erroring to +Inf."""
    if a == 0 and b == 0:
        return 0.0
    def bracket(v, axis):
        lo = max((x for x in axis if x <= v), default=None)
        hi = min((x for x in axis if x >= v), default=None)
        return lo, hi
    a1, a2 = bracket(a, a_axis)
    b1, b2 = bracket(b, b_axis)
    if a1 is None or a2 is None or b1 is None or b2 is None:
        raise KeyError(f"({a},{b}) outside data range")
    for c in ((a1, b1), (a1, b2), (a2, b1), (a2, b2)):
        if c not in table:
            raise KeyError(f"corner {c} missing")
    if a1 == a2 and b1 == b2:
        return table[(a1, b1)]
    if a1 == a2:
        w = (b - b1) / (b2 - b1)
        return (1 - w) * table[(a1, b1)] + w * table[(a1, b2)]
    if b1 == b2:
        w = (a - a1) / (a2 - a1)
        return (1 - w) * table[(a1, b1)] + w * table[(a2, b1)]
    wa, wb = (a - a1) / (a2 - a1), (b - b1) / (b2 - b1)
    return ((1 - wa) * (1 - wb) * table[(a1, b1)] + (1 - wa) * wb * table[(a1, b2)]
            + wa * (1 - wb) * table[(a2, b1)] + wa * wb * table[(a2, b2)])


def validate(tpot, ttft):
    print("\n[validate] replaying Go InterpolationPredictor semantics")
    tp = {(b, t): v for (b, t), v in tpot.items()}
    tt = {(c, 0): v for c, v in ttft.items()}
    fails = 0
    for batch in (1, 3, 17, 63, 129, 255, 611, 1132, 2048):
        for tpr in (8, 100, 777, 4095, 27953, 65536):
            try:
                go_predict(tp, batch, max(tpr, 8.0), BATCH_AXIS, TOKENS_AXIS)
            except KeyError as e:
                print(f"  TPOT FAIL batch={batch} tok={tpr}: {e}")
                fails += 1
    for c in (1, 7, 128, 999, 4097, 8192, 8193, 65536):
        try:
            go_predict(tt, c, 0, PREFILL_AXIS, [0])
        except KeyError as e:
            print(f"  TTFT FAIL chunk={c}: {e}")
            fails += 1
    print(f"  {'all queries resolved' if not fails else str(fails)+' FAILURES'}")
    print("\n[validate] representative predictions")
    for batch, tpr in ((1, 8), (32, 1024), (64, 8192), (256, 8192), (612, 16384), (1132, 8192)):
        print(f"  decode step  batch={batch:>5} tok/req={tpr:>6} -> "
              f"{go_predict(tp, batch, tpr, BATCH_AXIS, TOKENS_AXIS):8.2f} ms")
    for c in (674, 4055, 8192):
        print(f"  prefill step chunk={c:>6}            -> "
              f"{go_predict(tt, c, 0, PREFILL_AXIS, [0]):8.2f} ms")
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-glob", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--model", default="meta-llama/Meta-Llama-3.1-70B-Instruct")
    ap.add_argument("--hardware", default="B200 x2 (TP2)")
    ap.add_argument("--timestamp", required=True,
                    help="provenance stamp, e.g. '2026-07-25 20:30:00'")
    ap.add_argument("--allow-missing-prefill-anchor", action="store_true",
                    help="write tpot.json even when the dumps contain no "
                         "saturated prefill window, and skip ttft.json. For "
                         "dumps that are deliberately all unsaturated because "
                         "that is where the decode law is identified; the "
                         "prefill table then comes from measure_ttft_sweep.py.")
    a = ap.parse_args()
    global ALLOW_MISSING_PREFILL_ANCHOR
    ALLOW_MISSING_PREFILL_ANCHOR = a.allow_missing_prefill_anchor
    os.makedirs(a.out_dir, exist_ok=True)

    print("[load]")
    rows = load_steps(a.results_glob)
    chains = link_chains(rows)

    print("\n[decode surface]")
    cells = decode_cells(rows)
    coef = fit_decode_model(cells)
    grid, source = fill_grid(cells, coef)
    grid = isotonize(grid)

    print("\n[prefill anchor]")
    tps, chunk = prefill_anchor(chains)
    floor = float(coef[0])
    if tps is None:
        ttft, rate = None, None
    else:
        ttft, rate = prefill_table(tps, chunk, floor)

    common = dict(model=a.model, timestamp=a.timestamp)
    tpot_doc = {
        "metadata": dict(
            common,
            description=("ITL (decode step time) vs batch size and KV tokens per "
                         "request, from EXP-16 InstrumentedScheduler step dumps "
                         "(stock FIFO, fixed 8192-token budget). Measured cells "
                         "are per-cell medians of decode-only steps; modelled "
                         "cells fill the grid so the Go bilinear lookup always "
                         "finds four corners. tokens_per_request is the logical "
                         "KV count, inflated by prefix cache sharing -- the same "
                         "quantity Llumnix queries with."),
            hardware=a.hardware,
            source_glob=a.results_glob,
            model_fit={"c0_ms": coef[0], "c_kv_ms_per_tok": coef[1],
                       "c_batch_ms_per_req": coef[2]},
            measured_cells=sum(1 for v in source.values() if v == "measured"),
            total_cells=len(grid)),
        "results": [dict(batch_size=b, tokens_per_request=t,
                         source=source[(b, t)],
                         **stats(grid[(b, t)],
                                 cells[(b, t)][1] if (b, t) in cells else None))
                    for b in BATCH_AXIS for t in TOKENS_AXIS],
    }
    ttft_doc = None if ttft is None else {
        "metadata": dict(
            common,
            description=("Per-STEP prefill cost vs chunk size. PROVISIONAL: async "
                         "scheduling makes per-step attribution unusable except in "
                         "sustained saturated windows, which only occur at a full "
                         "chunk, so exactly one operating point is measured "
                         f"({chunk:,.0f} tok -> {chunk/tps*1000:.1f} ms) and the rest is "
                         "t = floor + c/rate. Replace with a dedicated idle-engine "
                         "prompt-length sweep before trusting TTFT numerically."),
            hardware=a.hardware,
            source_glob=a.results_glob,
            anchor={"sustained_tok_per_s": tps, "mean_chunk_tokens": chunk,
                    "floor_ms": floor, "fitted_rate_tok_per_s": rate},
            engine_max_num_batched_tokens=8192),
        "results": [dict(tokens_num=c, source="modelled" if c != round(chunk) else "measured",
                         **stats(ttft[c])) for c in PREFILL_AXIS],
    }

    docs = [("tpot.json", tpot_doc)]
    if ttft is not None:
        docs.append(("ttft.json", ttft_doc))
    for name, doc in docs:
        p = os.path.join(a.out_dir, name)
        with open(p, "w") as f:
            json.dump(doc, f, indent=1)
        print(f"\nwrote {p}  ({len(doc['results'])} results, {os.path.getsize(p):,} bytes)")

    if ttft is None:
        # validate() replays the Go predictor over BOTH tables, so it cannot run
        # on a decode-only build. The decode grid is still checked above by the
        # isotonic pass and by the per-cell sample counts written into the file;
        # the pair is validated when the profile directory is complete, which is
        # what set_scheduler_profiling.py reports on every deployment.
        print("\n[validate] skipped: this run wrote the decode grid only, so "
              "there is no prefill table to replay against. Validate the pair "
              "after measure_ttft_sweep.py has written ttft.json.")
        return True
    return validate(grid, ttft)


if __name__ == "__main__":
    sys.exit(1 if main() else 0)
