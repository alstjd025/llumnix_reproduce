#!/usr/bin/env python3
"""Build the FluidServe offline profile (fluidserve.json) from data we already have.

FluidServe needs two things before it can make a decision, and both are estimated
offline here so the controller does not have to start from an uninformed state
(the design note calls this the seeding that prevents the calibration loop from
staying conservative forever because it never observes high-occupancy steps):

  1. decode step law    t_dec(M, n) = c0 + c_kv*M + c_n*n
     M = total KV tokens held on the instance, n = number of decoding requests.
     Fitted on decode-only steps from the EXP-16 per-step scheduler dumps.

  2. class output-length survival   S_c(j) = P(output length > j)
     Per SLO tier, from the per-request metrics of completed runs. Used for the
     conditional completion probability p_c(j,k) = [S(j) - S(j+k)] / S(j), i.e.
     "a request that has already produced j tokens finishes within k more steps".

It also runs a validation that the FluidServe capacity model needs to be true:

  3. mixed-step check   mean_step ~= (1-phi)*t_dec + phi*(t_pre(chunk) + t_dec - c0)
     over windows of consecutive steps, where phi is the fraction of steps that
     carried a prefill chunk. This is the assumption that lets the controller
     price the interference a newly admitted prompt inflicts on the requests
     already decoding on that instance. The cost of a prefill-carrying step is
     additive because that step executes both the prefill GEMMs and the decode
     attention for every running request; the fixed per-step overhead c0 is paid
     once, hence the subtraction. The alternative of charging only t_pre(chunk)
     is what ttft.json alone would suggest, but that table was measured on an
     idle engine and therefore contains no decode work. Measured against real
     windows the additive form is accurate to about 10% in prefill-heavy windows
     where the separate form underestimates by 35-40%; both are reported.

Measurement caveat inherited from gen_profiling_from_stepdump.py: `interval_ms`
is the enter-to-enter gap between schedule() calls and is bimodal under async
scheduling (the scheduler either runs a step ahead at ~0.5 ms or blocks for the
true forward time). Per-step attribution is therefore not usable. Two defences
are applied: the decode law is fitted on per-cell medians, and the mixed-step
check measures a window's mean as (wall time spanned) / (steps in window), which
averages the run-ahead out instead of trying to attribute it.

Usage
-----
    python3 ms_dev/scripts/gen_fluidserve_profile.py \
        --stepdump-glob '<results>/*exp16*/server_metrics/sched_steps.jsonl' \
        --metrics-glob  '<results>/*exp21*/metrics.csv' \
        --out deploy/profiling/llama31-70b-b200-tp2/fluidserve.json
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

# Tier key -> workload class. The tier key is the TPOT SLO in ms, which is what
# the gateway packs into the request's priority field, so it is the identifier
# the scheduler actually sees.
CLASS_TIERS = {"chat": 50, "deepresearch": 100, "swe": 25}

# Binning axes for the decode-law fit. Medians are taken per cell so that each
# operating point weighs equally regardless of how long the engine happened to
# sit there, and so the heavy upper tail of interval_ms cannot drag the fit.
BATCH_AXIS = [1, 2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512,
              768, 1024, 1536, 2048]
KV_AXIS = [1024, 2048, 4096, 8192, 16384, 32768, 65536, 131072, 262144,
           524288, 1048576, 2097152, 4194304]
MIN_SAMPLES = 30

# Survival-function grid. Resolution near the origin has to be finer than the
# planning horizon (~100 steps) or the conditional completion probability is
# quantised too coarsely to distinguish a request that is about to finish.
SURVIVAL_GRID = ([j for j in range(0, 2048, 16)] +
                 [j for j in range(2048, 8193, 256)])

WINDOW_STEPS = 150      # window length for the mixed-step validation
MIN_WINDOW_S = 0.5      # ignore windows too short to average the run-ahead out
LINK_TOL_S = 0.002      # tolerance when re-linking a step to its predecessor


def snap(value, axis):
    """Nearest axis node in log space (the axes are geometric)."""
    lv = math.log(max(value, 1e-9))
    logs = [math.log(a) for a in axis]
    i = bisect.bisect_left(logs, lv)
    if i == 0:
        return axis[0]
    if i >= len(axis):
        return axis[-1]
    return axis[i - 1] if (lv - logs[i - 1]) <= (logs[i] - lv) else axis[i]


# ---------------------------------------------------------------------------
# step dumps
# ---------------------------------------------------------------------------
def load_steps(pattern):
    rows, files = [], sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"no step logs matched {pattern!r}")
    for f in files:
        run = os.path.basename(os.path.dirname(os.path.dirname(f)))
        if "qoserve" in run or "deadline" in run:
            sys.exit(f"refusing {run}: DeadlineScheduler resizes the chunk every "
                     f"step, so its steps do not describe the stock engine")
        n0 = len(rows)
        with open(f) as fh:
            for line in fh:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                r["_run"] = run
                r["_prev"] = r["_next"] = None
                rows.append(r)
        print(f"  {run:<46s} {len(rows) - n0:>9,d} steps")
    print(f"  {'TOTAL':<46s} {len(rows):>9,d} steps")
    return rows


def link_chains(rows):
    """Rebuild each engine's own step sequence.

    All four engines of a run append to one host-mounted file and the records
    carry no engine identifier, so a naive sort by wall time interleaves four
    independent step sequences. Any quantity computed over consecutive records
    would then be wrong by roughly the number of engines: measured over such a
    mixture, a window of 150 records spans only about 37 steps of real engine
    time. `interval_ms` gives the exact entry time of a record's predecessor,
    which picks the right one out of the records sharing a step number.
    """
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
            r["_prev"], best["_next"] = best, r
            linked += 1
    chains = []
    for s in rows:
        if s["_prev"] is None:
            ch, cur = [], s
            while cur is not None:
                ch.append(cur)
                cur = cur["_next"]
            if len(ch) >= 8:
                chains.append(ch)
    print(f"  linked {linked:,}/{len(rows):,} steps into {len(chains):,} chains "
          f"(median length {int(np.median([len(c) for c in chains])) if chains else 0})")
    return chains


def fit_decode_law(rows):
    """t_dec = c0 + c_kv*M + c_n*n, on decode-only steps.

    Both terms are kept because they are physically distinct: c_kv is the cost of
    reading the KV cache, which grows with the total tokens held, while c_n is the
    per-request overhead that does not depend on how long each request is. EXP-11
    measured the KV term at roughly 3 ns per token and found it workload
    invariant, so that coefficient is the one to sanity check.
    """
    buckets = collections.defaultdict(list)
    for r in rows:
        iv, nd, kv = r.get("interval_ms"), r.get("n_decode"), r.get("kv_tokens")
        if iv is None or not nd or kv is None or kv <= 0:
            continue
        if r.get("prefill_tokens_step"):
            continue
        buckets[(snap(nd, BATCH_AXIS), snap(kv, KV_AXIS))].append(iv)

    cells = {k: float(np.median(v)) for k, v in buckets.items()
             if len(v) >= MIN_SAMPLES}
    total = sum(len(v) for v in buckets.values())
    print(f"  decode-only steps: {total:,}  ->  {len(cells)} cells with "
          f">= {MIN_SAMPLES} samples")
    if len(cells) < 8:
        sys.exit("too few decode cells to fit the step law")

    n = np.array([k[0] for k in cells], float)
    m = np.array([k[1] for k in cells], float)
    y = np.array(list(cells.values()), float)
    X = np.column_stack([np.ones(len(y)), m, n])
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    pred = X @ coef
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    rel = np.abs(pred - y) / y
    print(f"  t_dec = {coef[0]:.3f} ms + {coef[1]:.4e}*M + {coef[2]:.4e}*n   "
          f"R2={r2:.4f}  rel-err p50={np.percentile(rel, 50) * 100:.1f}% "
          f"p90={np.percentile(rel, 90) * 100:.1f}%")
    # EXP-11 reported roughly 3 ns per token against the FLEET aggregate KV of
    # four engines; the per-engine slope is therefore expected to be about four
    # times that, which is what this fit gives.
    print(f"  KV term = {coef[1] * 1e6:.2f} ns/token per engine "
          f"(EXP-11 fleet-aggregate slope was about 3 ns/token over 4 engines)")
    return dict(c0_ms=float(coef[0]), c_kv_ms_per_token=float(coef[1]),
                c_n_ms_per_request=float(coef[2]), r2=float(r2),
                cells=len(cells), samples=int(total),
                rel_err_p50=float(np.percentile(rel, 50)),
                rel_err_p90=float(np.percentile(rel, 90)))


def load_prefill_curve(path):
    """Cost of one iteration that carries a prefill chunk, versus chunk size.

    Read from the existing ttft.json rather than re-derived here. That table was
    measured directly with an idle-engine prompt-length sweep (one request at a
    time, fresh random token ids so the prefix cache cannot serve a repeat),
    which is a far better estimate than anything recoverable from loaded-engine
    step dumps, where sustained prefill windows only ever occur at a full chunk.
    Keeping one source of truth also means the controller and this validation
    cannot disagree about the hardware.
    """
    with open(path) as f:
        doc = json.load(f)
    pts = sorted((r["tokens_num"], r["p50"]) for r in doc["results"])
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]

    def curve(c):
        if c <= xs[0]:
            return ys[0]
        if c >= xs[-1]:
            return ys[-1]
        i = bisect.bisect_left(xs, c)
        w = (c - xs[i - 1]) / (xs[i] - xs[i - 1])
        return (1 - w) * ys[i - 1] + w * ys[i]

    print(f"  from {path}: chunk 1024 -> {curve(1024):.1f} ms, "
          f"4096 -> {curve(4096):.1f} ms, 8192 -> {curve(8192):.1f} ms")
    return curve, dict(source=os.path.basename(path),
                       ms_at_1024=curve(1024), ms_at_4096=curve(4096),
                       ms_at_8192=curve(8192))


def validate_mixed_steps(chains, decode, t_pre, window_steps=WINDOW_STEPS):
    """Check the capacity model's mixture term against measured windows.

    For a window of consecutive steps the model says

        mean_step = (1 - phi) * t_dec(M, n) + phi * t_pre(chunk)

    with phi the fraction of steps that carried a prefill chunk. The measured
    mean is the wall time spanned divided by the number of steps, which is
    immune to the async run-ahead. Windows are grouped by phi so that any
    systematic error shows up as a function of how prefill-heavy the window was.
    """
    def t_dec(m, n):
        return (decode["c0_ms"] + decode["c_kv_ms_per_token"] * m
                + decode["c_n_ms_per_request"] * n)

    buckets = collections.defaultdict(list)
    n_windows = 0
    for rs in chains:
        for s in range(0, len(rs) - window_steps, window_steps):
            w = rs[s:s + window_steps]
            span = w[-1]["t_wall"] - w[0]["t_wall"]
            if span < MIN_WINDOW_S or span > 120:
                continue
            nd = np.mean([x.get("n_decode") or 0 for x in w])
            kv = np.mean([x.get("kv_tokens") or 0 for x in w])
            if nd < 1 or kv <= 0:
                continue
            pre = [x.get("prefill_tokens_step", 0) for x in w]
            phi = float(np.mean([p > 0 for p in pre]))
            chunk = float(np.mean([p for p in pre if p > 0])) if phi > 0 else 0.0
            measured = span / (len(w) - 1) * 1000.0
            d = t_dec(kv, nd)
            c0 = decode["c0_ms"]
            predicted = (1 - phi) * d + phi * (t_pre(chunk) + d - c0)
            separate = (1 - phi) * d + phi * t_pre(chunk)
            buckets[round(min(phi, 0.999) * 5) / 5].append(
                (measured, predicted, separate))
            n_windows += 1

    print(f"  {n_windows:,} windows of {window_steps} steps")
    print(f"  {'phi':>6} {'n':>7} {'measured':>10} {'additive':>10} {'ratio':>7} "
          f"{'separate':>10} {'ratio':>7}")
    out = []
    for phi in sorted(buckets):
        t = np.array(buckets[phi], float)
        meas, pred, sep = t[:, 0], t[:, 1], t[:, 2]
        ratio = float(np.median(pred / meas))
        sratio = float(np.median(sep / meas))
        print(f"  {phi:>6.1f} {len(t):>7,d} {np.median(meas):>9.1f}ms "
              f"{np.median(pred):>9.1f}ms {ratio:>7.2f} "
              f"{np.median(sep):>9.1f}ms {sratio:>7.2f}")
        out.append(dict(phi_bucket=float(phi), n=len(t),
                        measured_median_ms=float(np.median(meas)),
                        predicted_median_ms=float(np.median(pred)),
                        pred_over_meas=ratio,
                        separate_model_over_meas=sratio))
    return out


# ---------------------------------------------------------------------------
# output-length survival per class
# ---------------------------------------------------------------------------
def class_of(task_id):
    t = str(task_id)
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def build_survival(pattern):
    """Empirical survival function of output length, per class.

    Requests cut off by the end of the run are excluded rather than treated as
    censored observations: their recorded output length is an artefact of when
    the run stopped, and including them would bias the distribution towards
    short outputs exactly in the overloaded runs where the tail matters most.
    Rejected and errored requests are excluded for the same reason.
    """
    import pandas as pd

    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"no metrics files matched {pattern!r}")
    lens = collections.defaultdict(list)
    for f in files:
        try:
            df = pd.read_csv(f, low_memory=False)
        except Exception as e:                                   # noqa: BLE001
            print(f"  skip {f}: {e}")
            continue
        if "agent" in df:
            df = df[df.agent != "job_summary"]
        for col, want in (("is_error", False), ("is_rejected", False),
                          ("is_timeout", False), ("is_server_terminated", False)):
            if col in df:
                df = df[df[col].astype(str).str.lower().isin(
                    ["false", "0", "0.0", "nan", ""])] if want is False else df
        ot = pd.to_numeric(df.get("output_tokens"), errors="coerce")
        df = df[ot.notna() & (ot > 0)]
        for tid, n in zip(df["task_id"], pd.to_numeric(df["output_tokens"])):
            lens[class_of(tid)].append(int(n))
    out = []
    for name, tier in sorted(CLASS_TIERS.items(), key=lambda kv: kv[1]):
        a = np.array(lens.get(name, []), float)
        if len(a) < 100:
            print(f"  {name:<14s} only {len(a)} samples -- refusing to profile it")
            continue
        surv = [float((a > j).mean()) for j in SURVIVAL_GRID]
        print(f"  {name:<14s} tier {tier:>3}ms  n={len(a):>7,d}  "
              f"mean={a.mean():>6.0f}  p50={np.percentile(a, 50):>6.0f}  "
              f"p90={np.percentile(a, 90):>6.0f}  max={a.max():>6.0f}")
        out.append(dict(name=name, tpot_slo_ms=tier, n=int(len(a)),
                        mean=float(a.mean()),
                        p50=float(np.percentile(a, 50)),
                        p90=float(np.percentile(a, 90)),
                        p99=float(np.percentile(a, 99)),
                        max=float(a.max()),
                        grid=SURVIVAL_GRID, survival=surv))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stepdump-glob", required=True)
    ap.add_argument("--metrics-glob", required=True)
    ap.add_argument("--prefill-table", required=True,
                    help="ttft.json from the idle-engine prompt-length sweep")
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", default="meta-llama/Meta-Llama-3.1-70B-Instruct")
    ap.add_argument("--hardware", default="B200 x2 (TP2)")
    ap.add_argument("--timestamp", required=True)
    ap.add_argument("--window-steps", type=int, default=WINDOW_STEPS,
                    help="steps per window in the mixed-step validation")
    a = ap.parse_args()

    print("[load step dumps]")
    rows = load_steps(a.stepdump_glob)

    chains = link_chains(rows)

    print("\n[decode step law]")
    decode = fit_decode_law(rows)

    print("\n[prefill step law]")
    t_pre, prefill = load_prefill_curve(a.prefill_table)

    print("\n[mixed-step validation]")
    mixed = validate_mixed_steps(chains, decode, t_pre, a.window_steps)

    print("\n[output-length survival]")
    classes = build_survival(a.metrics_glob)

    doc = {
        "metadata": {
            "model": a.model,
            "hardware": a.hardware,
            "timestamp": a.timestamp,
            "stepdump_glob": a.stepdump_glob,
            "metrics_glob": a.metrics_glob,
            "description": (
                "FluidServe offline seed. decode_step_law gives the cost of a "
                "decode-only iteration as a function of the KV tokens held and "
                "the number of decoding requests. prefill_step_law gives the "
                "cost of an iteration that carries a prefill chunk. classes[] "
                "gives the empirical survival function of output length per SLO "
                "tier, used for the conditional completion probability. "
                "mixed_step_validation reports how well the mixture model "
                "reproduces measured window means, bucketed by the fraction of "
                "steps that carried a prefill chunk."),
        },
        "decode_step_law": decode,
        "prefill_step_law": prefill,
        "mixed_step_validation": mixed,
        "classes": classes,
    }
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(doc, f, indent=1)
    print(f"\nwrote {a.out} ({os.path.getsize(a.out):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
