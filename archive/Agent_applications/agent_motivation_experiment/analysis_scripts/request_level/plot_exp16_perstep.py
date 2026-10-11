#!/usr/bin/env python3
"""EXP-16 Phase 1: is per-step decode time modellable?

Consumes the per-step engine log written by InstrumentedScheduler
(``server_metrics/sched_steps.jsonl``) and asks, before fitting any
coefficients, whether the step time DECOMPOSES into separable terms:

    interval_ms  ~=  C0 + a*kv_tokens + b*n_decode + c*prefill_tokens_step
                     (+ T_schedule, measured directly as t_schedule_us)

We check four things and print a verdict:
  1. BIMODALITY  — interval split by prefill_tokens_step==0 vs >0. If the two
     clusters separate, the prefill term (c) exists and dominates the tail.
  2. T_schedule  — magnitude of t_schedule_us and its growth vs waiting-queue
     depth. If it stays << interval even at deep queues, the CPU term is not a
     latency source (rules out the earlier "queue-mass" attribution).
  3. KV term     — among prefill==0 (pure-decode) steps, does interval rise with
     kv_tokens? binned means + partial correlation vs n_decode.
  4. SEPARABILITY— one multiple linear regression: coefficients, R^2, and VIF
     (collinearity). High R^2 with tolerable VIF => the terms are individually
     identifiable => modellable.

Figures + a text summary go to --out-dir. Nothing is fit for publication here;
this is the feasibility gate.

Usage:
  plot_exp16_perstep.py --run RESULTS/<run>  --out-dir figs/exp16
  plot_exp16_perstep.py --steps path/to/sched_steps.jsonl --out-dir figs/exp16
"""
import argparse
import json
import os

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "lines.linewidth": 1.4, "lines.markersize": 4.5,
}


def load_steps(path):
    rows = [json.loads(l) for l in open(path) if l.strip()]
    df = pd.DataFrame(rows)
    for c in ("interval_ms", "t_schedule_us", "kv_tokens", "n_running",
              "n_waiting", "n_decode", "n_prefill_reqs", "prefill_tokens_step",
              "total_sched_tokens", "t_wall"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def busy_window(df, warmup=60.0, tail=30.0):
    """Trim to the steady, loaded region: contiguous busy span (n_running>0),
    then drop the first `warmup` s and last `tail` s of it by wall clock."""
    busy = df[df["n_running"].fillna(0) > 0]
    if busy.empty:
        return df.iloc[0:0]
    t0, t1 = busy["t_wall"].min(), busy["t_wall"].max()
    lo, hi = t0 + warmup, t1 - tail
    w = df[(df["t_wall"] >= lo) & (df["t_wall"] <= hi) &
           (df["interval_ms"].notna())]
    return w


def _pctl(s, ps):
    s = s.dropna().values
    if not len(s):
        return {p: float("nan") for p in ps}
    return {p: float(np.percentile(s, p)) for p in ps}


def summarize(w, dur_hint=None):
    out = {}
    iv = w["interval_ms"]
    out["n_steps"] = int(len(w))
    out["span_s"] = float(w["t_wall"].max() - w["t_wall"].min()) if len(w) else 0
    out["interval"] = _pctl(iv, [50, 90, 99])
    out["interval_mean"] = float(iv.mean())
    # 1. bimodality by prefill presence
    has_p = w["prefill_tokens_step"] > 0
    out["frac_steps_with_prefill"] = float(has_p.mean())
    out["interval_decode_only"] = _pctl(w[~has_p]["interval_ms"], [50, 90, 99])
    out["interval_with_prefill"] = _pctl(w[has_p]["interval_ms"], [50, 90, 99])
    # 2. T_schedule
    ts = w["t_schedule_us"]
    out["t_schedule_us"] = _pctl(ts, [50, 90, 99])
    out["t_schedule_ms_p99"] = out["t_schedule_us"][99] / 1e3
    out["interval_p99_ms"] = out["interval"][99]
    # crude correlation of T_schedule with queue depth
    m = w[["t_schedule_us", "n_waiting"]].dropna()
    out["corr_tsched_queue"] = (float(m["t_schedule_us"].corr(m["n_waiting"]))
                                if len(m) > 10 else float("nan"))
    return out


def regression(w):
    """interval ~ kv_tokens + n_decode + prefill_tokens_step, via numpy lstsq.
    Returns coefficients, R^2, and VIF per predictor (collinearity)."""
    cols = ["kv_tokens", "n_decode", "prefill_tokens_step"]
    d = w[["interval_ms"] + cols].dropna()
    d = d[(d[cols] >= 0).all(axis=1)]
    if len(d) < 50:
        return None
    X = d[cols].values.astype(float)
    y = d["interval_ms"].values.astype(float)
    Xd = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(Xd, y, rcond=None)
    yhat = Xd @ beta
    ss_res = float(np.sum((y - yhat) ** 2))
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else float("nan")
    # VIF: regress each predictor on the others
    vif = {}
    for i, c in enumerate(cols):
        others = np.column_stack([np.ones(len(X))] +
                                 [X[:, j] for j in range(len(cols)) if j != i])
        b2, *_ = np.linalg.lstsq(others, X[:, i], rcond=None)
        r = X[:, i] - others @ b2
        sst = np.sum((X[:, i] - X[:, i].mean()) ** 2)
        r2i = 1 - np.sum(r ** 2) / sst if sst > 0 else 0.0
        vif[c] = float(1 / (1 - r2i)) if r2i < 1 else float("inf")
    # scale coefficients to intuitive units
    coef = {"intercept_ms": float(beta[0]),
            "a_ms_per_Mtok_kv": float(beta[1] * 1e6),
            "b_ms_per_decode_req": float(beta[2]),
            "c_ms_per_Ktok_prefill": float(beta[3] * 1e3)}
    return {"coef": coef, "r2": float(r2), "vif": vif, "n": int(len(d))}


def fig_bimodal(w, out):
    has_p = w["prefill_tokens_step"] > 0
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(5.2, 3.4))
        bins = np.logspace(0, np.log10(max(10, w["interval_ms"].max())), 60)
        ax.hist(w[~has_p]["interval_ms"].dropna(), bins=bins, alpha=0.65,
                color="#1f77b4", label="decode-only steps (prefill=0)")
        ax.hist(w[has_p]["interval_ms"].dropna(), bins=bins, alpha=0.65,
                color="#d62728", label="steps carrying prefill (>0)")
        ax.set_xscale("log")
        ax.set_xlabel("engine step interval (ms)")
        ax.set_ylabel("step count")
        ax.set_title("Per-step ITL is bimodal — prefill steps form the slow mode")
        ax.legend(loc="upper right")
        fig.tight_layout()
        p = os.path.join(out, "exp16_bimodal_interval.png")
        fig.savefig(p, dpi=300); print("wrote:", p)


def fig_tschedule(w, out):
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 2, figsize=(8.6, 3.3))
        ax[0].hist(w["t_schedule_us"].dropna() / 1e3, bins=60, color="#7f7f7f")
        ax[0].set_xlabel("T_schedule (ms, time inside schedule())")
        ax[0].set_ylabel("step count")
        ax[0].set_title("T_schedule magnitude")
        m = w[["n_waiting", "t_schedule_us"]].dropna()
        ax[1].scatter(m["n_waiting"], m["t_schedule_us"] / 1e3, s=3, alpha=0.25,
                      color="#7f7f7f")
        ax[1].set_xlabel("waiting-queue depth")
        ax[1].set_ylabel("T_schedule (ms)")
        ax[1].set_title("T_schedule vs queue depth")
        fig.tight_layout()
        p = os.path.join(out, "exp16_tschedule.png")
        fig.savefig(p, dpi=300); print("wrote:", p)


def _binned(ax, x, y, nb=12, color="#1f77b4", label=None):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 10:
        return
    ax.scatter(d["x"], d["y"], s=2, alpha=0.12, color=color)
    q = pd.qcut(d["x"], min(nb, d["x"].nunique()), duplicates="drop")
    g = d.groupby(q, observed=True).agg(x=("x", "mean"), y=("y", "median"))
    ax.plot(g["x"], g["y"], "o-", color=color, label=label, zorder=5)


def fig_terms(w, out):
    dec = w[w["prefill_tokens_step"] == 0]        # pure-decode steps
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(11.5, 3.4))
        _binned(ax[0], dec["kv_tokens"] / 1e6, dec["interval_ms"])
        ax[0].set_xlabel("batch KV (Mtok)"); ax[0].set_ylabel("interval (ms)")
        ax[0].set_title("KV term (decode-only steps)")
        _binned(ax[1], dec["n_decode"], dec["interval_ms"], color="#2ca02c")
        ax[1].set_xlabel("decode batch size"); ax[1].set_ylabel("interval (ms)")
        ax[1].set_title("Batch-count term (decode-only steps)")
        _binned(ax[2], w["prefill_tokens_step"], w["interval_ms"],
                color="#d62728")
        ax[2].set_xlabel("prefill tokens in step")
        ax[2].set_ylabel("interval (ms)")
        ax[2].set_title("Prefill term (all steps)")
        fig.tight_layout()
        p = os.path.join(out, "exp16_terms.png")
        fig.savefig(p, dpi=300); print("wrote:", p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", help="results/<run> dir (uses server_metrics/)")
    ap.add_argument("--steps", help="explicit sched_steps.jsonl path")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--warmup-sec", type=float, default=60.0)
    ap.add_argument("--tail-sec", type=float, default=30.0)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    steps = a.steps or os.path.join(a.run, "server_metrics", "sched_steps.jsonl")
    if not os.path.exists(steps):
        raise SystemExit(f"no step log at {steps}")
    df = load_steps(steps)
    w = busy_window(df, a.warmup_sec, a.tail_sec)
    if w.empty:
        raise SystemExit("empty busy window — check the log / warmup settings")

    s = summarize(w)
    reg = regression(w)

    lines = []
    P = lines.append
    P("=" * 68)
    P("EXP-16 per-step decode-latency decomposition (feasibility)")
    P("=" * 68)
    P(f"steps in window : {s['n_steps']}  span={s['span_s']:.0f}s")
    P(f"interval ms     : p50={s['interval'][50]:.1f} "
      f"p90={s['interval'][90]:.1f} p99={s['interval'][99]:.1f} "
      f"mean={s['interval_mean']:.1f}")
    P("")
    P("[1] BIMODALITY (prefill term exists?)")
    P(f"  steps carrying prefill : {100*s['frac_steps_with_prefill']:.1f}%")
    P(f"  decode-only interval   : p50={s['interval_decode_only'][50]:.1f} "
      f"p99={s['interval_decode_only'][99]:.1f} ms")
    P(f"  with-prefill interval  : p50={s['interval_with_prefill'][50]:.1f} "
      f"p99={s['interval_with_prefill'][99]:.1f} ms")
    sep = (s['interval_with_prefill'][50] /
           max(1e-9, s['interval_decode_only'][50]))
    P(f"  slow/fast p50 ratio    : {sep:.2f}x  "
      f"-> {'SEPARATED' if sep > 1.5 else 'not separated'}")
    P("")
    P("[2] T_schedule (CPU term a latency source?)")
    P(f"  t_schedule ms          : p50={s['t_schedule_us'][50]/1e3:.3f} "
      f"p99={s['t_schedule_ms_p99']:.3f}")
    P(f"  interval p99 ms        : {s['interval_p99_ms']:.1f}")
    frac = s['t_schedule_ms_p99'] / max(1e-9, s['interval_p99_ms'])
    P(f"  T_sched.p99 / iv.p99   : {100*frac:.2f}%  "
      f"-> {'NEGLIGIBLE' if frac < 0.1 else 'material'}")
    P(f"  corr(T_sched, queue)   : {s['corr_tsched_queue']:.3f}")
    P("")
    P("[3+4] SEPARABILITY (multiple regression)")
    if reg:
        c = reg["coef"]
        P(f"  n={reg['n']}  R^2={reg['r2']:.3f}")
        P(f"  intercept C0           : {c['intercept_ms']:.2f} ms")
        P(f"  a (KV)                 : {c['a_ms_per_Mtok_kv']:.2f} ms/Mtok")
        P(f"  b (decode batch)       : {c['b_ms_per_decode_req']:.4f} ms/req")
        P(f"  c (prefill)            : {c['c_ms_per_Ktok_prefill']:.3f} ms/Ktok")
        P(f"  VIF                    : " +
          ", ".join(f"{k}={v:.1f}" for k, v in reg["vif"].items()))
        modellable = (reg["r2"] > 0.5 and
                      max(reg["vif"].values()) < 10 and sep > 1.5)
        P("")
        P(f"VERDICT: terms are {'SEPARABLE / MODELLABLE' if modellable else 'ENTANGLED — needs decoupling runs'}")
    else:
        P("  regression skipped (too few rows)")
    txt = "\n".join(lines)
    print(txt)
    with open(os.path.join(a.out_dir, "exp16_summary.txt"), "w") as f:
        f.write(txt + "\n")

    fig_bimodal(w, a.out_dir)
    fig_tschedule(w, a.out_dir)
    fig_terms(w, a.out_dir)


if __name__ == "__main__":
    main()
