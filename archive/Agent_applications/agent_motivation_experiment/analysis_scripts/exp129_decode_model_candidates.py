#!/usr/bin/env python3
"""EXP-129: candidate decode step-time models for the Qwen2.5-14B x 8 fleet.

Fits are done in the SAME space as the production fitter
(gen_profiling_from_stepdump.fit_decode_model): one point per (batch,
tokens-per-request) cell holding >= MIN_SAMPLES decode-only steps, the target
being the cell's median interval_ms.  That keeps "median relative error"
comparable with the 8.0% the current law reaches on Llama-3.1-8B.

Every added regressor is counted in STEPS or TOKENS, never in wall time.  A
"prefill duty" defined as the share of recent wall time spent in prefill steps
is built out of interval_ms, which is the regression target, so a fit using it
would report a good score for a circular reason.
"""
import argparse
import bisect
import math

import numpy as np

BATCH = [1, 2, 4, 8, 16, 24, 32, 48, 64, 96, 128, 192, 256, 384, 512, 768,
         1024, 1536, 2048]
TOK = [8, 32, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768, 65536]
MIN_SAMPLES = 30


def snap(v, ax):
    lv = math.log(max(v, 1e-9))
    la = [math.log(a) for a in ax]
    i = bisect.bisect_left(la, lv)
    if i == 0:
        return ax[0]
    if i >= len(ax):
        return ax[-1]
    return ax[i - 1] if (lv - la[i - 1]) <= (la[i] - lv) else ax[i]


def scores(y, pred):
    r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
    rel = np.abs(pred - y) / y
    return r2, np.percentile(rel, 50) * 100, np.percentile(rel, 90) * 100


def vif(X):
    """Variance inflation factor per non-intercept column."""
    out = []
    for j in range(1, X.shape[1]):
        others = np.delete(X, j, axis=1)
        c, *_ = np.linalg.lstsq(others, X[:, j], rcond=None)
        resid = X[:, j] - others @ c
        ss = ((X[:, j] - X[:, j].mean()) ** 2).sum()
        out.append(np.inf if ss <= 0 or resid.var() == 0
                   else 1.0 / max(1e-12, 1 - (1 - (resid ** 2).sum() / ss)))
    return out


def build_cells(d, extra=(), mask_extra=None):
    """Cell medians.  `extra` names columns whose cell median joins the design."""
    iv = d["s_interval_ms"]; sp = d["s_prefill_tokens_step"]
    nd = d["s_n_decode"]; kv = d["s_kv_tokens"]
    m = np.isfinite(iv) & (sp == 0) & (nd > 0) & (kv > 0)
    if mask_extra is not None:
        m = m & mask_extra
    iv, nd, kv = iv[m], nd[m], kv[m]
    ex = [d[e][m] for e in extra]
    b = np.array([snap(x, BATCH) for x in nd])
    t = np.array([snap(x, kvx / ndx) for kvx, ndx in zip(kv, nd)]) if False else \
        np.array([snap(x, TOK) for x in kv / nd])
    key = b.astype(np.int64) * 10 ** 6 + t.astype(np.int64)
    uk, inv = np.unique(key, return_inverse=True)
    B, T, Y, E, N = [], [], [], [], []
    for i, k in enumerate(uk):
        s = inv == i
        n = int(s.sum())
        if n < MIN_SAMPLES:
            continue
        B.append(k // 10 ** 6); T.append(k % 10 ** 6)
        Y.append(float(np.median(iv[s]))); N.append(n)
        E.append([float(np.median(e[s])) for e in ex])
    return (np.array(B, float), np.array(T, float), np.array(Y, float),
            np.array(E, float).reshape(len(Y), len(extra)), np.array(N))


def report(label, X, y, names):
    coef, *_ = np.linalg.lstsq(X, y, rcond=None)
    r2, p50, p90 = scores(y, X @ coef)
    v = vif(X) if X.shape[1] > 1 else []
    print(f"  {label:<52s} R2={r2:6.4f}  rel-err p50={p50:5.1f}%  p90={p90:5.1f}%")
    print(f"    {'terms:':<10s}" + "  ".join(
        f"{n}={c:.4g}" for n, c in zip(names, coef)))
    if v:
        print(f"    {'VIF:':<10s}" + "  ".join(
            f"{n}={x:.1f}" for n, x in zip(names[1:], v)))
    return r2, p50, p90


def run(path, label):
    d = np.load(path, allow_pickle=True)
    print(f"\n################ {label} ################")
    B, T, Y, E, N = build_cells(d, ("pf_tok_32", "pf_frac_32", "d_prev_prefill"))
    M = B * T
    print(f"  cells={len(Y)}  steps={N.sum():,}")
    ones = np.ones(len(Y))

    print("\n  -- A: current law (baseline) --")
    report("t = c0 + c_kv*M + c_n*B",
           np.column_stack([ones, M, B]), Y, ["c0", "c_kv", "c_n"])

    print("\n  -- B: + mean prefill tokens per step over the previous 32 steps --")
    report("t = c0 + c_kv*M + c_n*B + c_p*pf_tok_32",
           np.column_stack([ones, M, B, E[:, 0]]), Y,
           ["c0", "c_kv", "c_n", "c_p"])

    print("\n  -- C: + share of the previous 32 steps that were prefill --")
    report("t = c0 + c_kv*M + c_n*B + c_p*pf_frac_32",
           np.column_stack([ones, M, B, E[:, 1]]), Y,
           ["c0", "c_kv", "c_n", "c_p"])

    print("\n  -- D: + prefill share and a recovery term exp(-d/8) --")
    report("t = c0 + c_kv*M + c_n*B + c_p*pf_frac_32 + c_r*exp(-d/8)",
           np.column_stack([ones, M, B, E[:, 1], np.exp(-E[:, 2] / 8.0)]), Y,
           ["c0", "c_kv", "c_n", "c_p", "c_r"])

    print("\n  -- E: power law, log t = a + b*log B + c*log T --")
    Xe = np.column_stack([ones, np.log(B), np.log(T)])
    ce, *_ = np.linalg.lstsq(Xe, np.log(Y), rcond=None)
    r2, p50, p90 = scores(Y, np.exp(Xe @ ce))
    print(f"  {'t = exp(a) * B^b * T^c':<52s} R2={r2:6.4f}  rel-err p50={p50:5.1f}%  p90={p90:5.1f}%")
    print(f"    terms: a={ce[0]:.4g}  b={ce[1]:.4g}  c={ce[2]:.4g}")

    print("\n  -- F: current law, fitted on well-separated pure-decode steps only --")
    sep = d["d_prev_prefill"] > 4
    Bf, Tf, Yf, _, Nf = build_cells(d, (), mask_extra=sep)
    report(f"t = c0 + c_kv*M + c_n*B   (cells={len(Yf)}, steps={Nf.sum():,})",
           np.column_stack([np.ones(len(Yf)), Bf * Tf, Bf]), Yf,
           ["c0", "c_kv", "c_n"])

    print("\n  -- G: saturating in batch: + A0/(1+exp(-(B-B0)/s)), grid over B0,s --")
    best = None
    for B0 in (16, 24, 32, 48, 64, 96, 128):
        for sc in (2, 4, 8, 16, 32):
            X = np.column_stack([ones, M, B, 1.0 / (1.0 + np.exp(-(B - B0) / sc))])
            c, *_ = np.linalg.lstsq(X, Y, rcond=None)
            r2, p50, p90 = scores(Y, X @ c)
            if best is None or p50 < best[0]:
                best = (p50, r2, p90, B0, sc, c, X)
    p50, r2, p90, B0, sc, c, X = best
    print(f"  {f't = c0 + c_kv*M + c_n*B + A0*sigmoid((B-{B0})/{sc})':<52s} "
          f"R2={r2:6.4f}  rel-err p50={p50:5.1f}%  p90={p90:5.1f}%")
    print(f"    terms: c0={c[0]:.4g}  c_kv={c[1]:.4g}  c_n={c[2]:.4g}  A0={c[3]:.4g}")
    print(f"    VIF:   " + "  ".join(f"{n}={x:.1f}" for n, x in
                                     zip(["M", "B", "sigmoid"], vif(X))))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--qwen", required=True)
    ap.add_argument("--llama", required=True)
    a = ap.parse_args()
    run(a.qwen, "Qwen2.5-14B-Instruct  x8 TP=1")
    run(a.llama, "Llama-3.1-8B-Instruct x8 TP=1")
