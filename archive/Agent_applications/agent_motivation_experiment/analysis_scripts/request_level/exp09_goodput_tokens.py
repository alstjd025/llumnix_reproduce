"""EXP-09 — throughput vs goodput (output tokens/s) per KV-admission threshold θ.

Question: KV-capacity admission buys request-level SLO attainment, but what does
it cost in *token* terms? Arms are θ ∈ {off, 0.3, 0.4, 0.5, 0.6}; θ=off reuses the
EXP-06 rate sweep (same workload/engine, admission disabled).

Goodput definition (EXP-17-identical, request-level, all-or-nothing):
a request is judged as a WHOLE — if it met the SLO (TTFT<=5s AND mean TBT<=50ms,
the same rule EXP-09's own analysis uses) then ALL of its OUTPUT tokens are
goodput, otherwise all of them are wasted. Input tokens are never counted. There
is no partial credit and no chain/job-level accounting here: a call belonging to
a chain that later dies still counts as goodput if that call itself met the SLO.

Time placement (same as EXP-17): tokens are spread uniformly over the interval in
which they were actually produced — [start+TTFT, end] — not dumped into the bin of
the completion instant, which would make long requests appear as an end-spike.

Three curves per panel so the reconstruction is verifiable:
  * server  — sum over the 4 engines of d(vllm:generation_tokens_total)/dt from
              the 1 s Prometheus scrapes. Ground truth, SLO-blind.
  * client  — the same spreading applied to every request that produced tokens,
              *including* run-boundary-terminated partials (they are real tokens
              the engine emitted). If it tracks the server curve the spreading
              assumption is sound.
  * goodput — its SLO-meeting subset. The gap is wasted.

Deviation from EXP-17 worth knowing: EXP-17 builds both curves from
`served_rows()`, which drops errored/timed-out/terminated calls. Here the client
throughput curve keeps their tokens (errored/timed-out calls carry 0 output
tokens in metrics.csv, so only run-boundary partials are affected, ~2-3% of
tokens at high rate) — otherwise the off arm's collapse would silently erase real
generated tokens and the client curve would undershoot the server counter. The
goodput numerator is unaffected: it always excludes them.

Window: fixed [60, 740] s (warmup skipped, 40 s drain of the ~780 s runs), 20 s
bins. Every arm is normalised by the same 680 s span — never by a run's own
observed span, which collapses under overload and would flatter the bad arm.

Usage:
  python analysis_scripts/request_level/exp09_goodput_tokens.py \
      --out-dir results/aggregate_analysis/exp09_tokens
"""

import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp17_goodput_tokens import _spread  # noqa: E402  (token spreading, verbatim)

# θ -> results glob. θ=0.0 is the EXP-06 no-admission sweep.
ARMS = {
    0.0: "*exp06_swe_sweep_rpm_*",
    0.3: "*exp09_swe_kvadm_th0300_rpm_*",
    0.4: "*exp09_swe_kvadm_th0400_rpm_*",
    0.5: "*exp09_swe_kvadm_th0500_rpm_*",
    0.6: "*exp09_swe_kvadm_th0600_rpm_*",
}
# Same rule as plot_exp09_swe.py / plot_exp07_theta.condition_stats.
TTFT_SLO_S, TBT_SLO_MS = 5.0, 50.0
WIN_LO, WIN_HI, BIN_S = 60.0, 740.0, 20.0
WIN_S = WIN_HI - WIN_LO
EDGES = np.arange(WIN_LO, WIN_HI + BIN_S, BIN_S)
CENTERS_MIN = (EDGES[:-1] + BIN_S / 2) / 60.0
PANEL_RATES = (0.75, 1.0, 1.5, 5.0)

# Same θ colours as the existing EXP-09 figures.
COLORS = {0.0: "0.25", 0.3: "#9467bd", 0.4: "#2ca02c", 0.5: "#ff7f0e", 0.6: "#d62728"}
PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "xtick.major.size": 3.0, "ytick.major.size": 3.0,
    "xtick.major.width": 0.7, "ytick.major.width": 0.7,
    "lines.linewidth": 1.4, "lines.markersize": 4.5,
}


def label_of(theta):
    return "no admission" if theta == 0 else f"$\\theta$={theta:g}"


# ---------------------------------------------------------------- loading ---
def load_run(run_dir):
    """Per-call frame restricted to the fixed window, + absolute t0."""
    df = pd.read_csv(os.path.join(run_dir, "metrics.csv"), low_memory=False)
    r = df[df.agent != "job_summary"].copy()
    r = r[pd.to_numeric(r.get("start_time"), errors="coerce").notna()]
    if r.empty:
        return None, None
    t0 = float(r["start_time"].min())
    rel = pd.to_numeric(r["start_time"], errors="coerce") - t0

    def bl(c):
        return (r[c].fillna(False).astype(bool) if c in r.columns
                else pd.Series(False, index=r.index))

    rejected = bl("is_rejected")
    failed = (bl("is_error") | bl("is_timeout") | bl("is_server_terminated")) & ~rejected
    ttft = pd.to_numeric(r["first_token_latency"], errors="coerce")
    tbt = pd.to_numeric(r["tbt_mean_ms"], errors="coerce")
    # NaN-tolerant, exactly as condition_stats: violate = (ttft>SLO) | (tbt>SLO)
    violate = (ttft > TTFT_SLO_S) | (tbt > TBT_SLO_MS)

    out = pd.DataFrame({
        "rel": rel,
        "ttft": ttft.fillna(0.0),
        "lat": pd.to_numeric(r["latency"], errors="coerce").fillna(0.0),
        "out": pd.to_numeric(r["output_tokens"], errors="coerce").fillna(0.0),
        "good": (~failed & ~rejected & ~violate).values,
        "served": (~failed & ~rejected).values,
        "rejected": rejected.values,
    })
    out = out[(out["rel"] >= WIN_LO) & (out["rel"] < WIN_HI)]
    return out.reset_index(drop=True), t0


def load(results_dir):
    """{theta: {rate_jobs_per_s: dict(df=..., run_dir=..., t0=...)}}"""
    data = {}
    for th, pattern in ARMS.items():
        per_rate = {}
        for d in sorted(glob.glob(os.path.join(results_dir, pattern))):
            m = re.search(r"rpm_(\d+)$", os.path.basename(d))
            if not m:
                continue
            df, t0 = load_run(d)
            if df is None or df.empty:
                print(f"  [skip] {d}")
                continue
            per_rate[int(m.group(1)) / 60.0] = dict(df=df, run_dir=d, t0=t0)
        data[th] = per_rate
        print(f"θ={th:g}: {len(per_rate)} conditions")
    return data


# ------------------------------------------------------------- token math ---
def client_series(df):
    """(throughput, goodput) output tokens/s, spread over each decode window."""
    if df.empty:
        z = np.zeros(len(EDGES) - 1)
        return z, z
    gen_start = (df["rel"] + df["ttft"]).values     # first token emitted
    gen_end = (df["rel"] + df["lat"]).values        # last token emitted
    w = df["out"].values
    thr = _spread(gen_start, gen_end, w, edges=EDGES)
    g = df["good"].values
    gp = _spread(gen_start[g], gen_end[g], w[g], edges=EDGES)
    return thr / BIN_S, gp / BIN_S


def server_series(run_dir, t0):
    """Ground-truth output tokens/s from vllm:generation_tokens_total counters."""
    acc = np.zeros(len(EDGES) - 1)
    for f in sorted(glob.glob(os.path.join(run_dir, "server_metrics", "engine_*.jsonl"))):
        ts, cum = [], []
        with open(f) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or "t" not in rec:
                    continue
                k = next((x for x in rec if "generation_tokens_total" in x), None)
                if k is None:
                    continue
                try:
                    ts.append(float(rec["t"]) - t0)
                    cum.append(float(rec[k]))
                except (TypeError, ValueError):
                    continue
        if len(ts) < 2:
            continue
        o = np.argsort(ts)
        ts, cum = np.asarray(ts)[o], np.asarray(cum)[o]
        d = np.diff(cum)
        d[d < 0] = 0.0                                   # counter reset
        acc += _spread(ts[:-1], ts[1:], d, edges=EDGES)  # credit each scrape gap
    return acc / BIN_S


# ---------------------------------------------------------------- figures ---
def fig_over_time(data, out_dir):
    thetas = sorted(data)
    rates = [r for r in PANEL_RATES]
    fig, axes = plt.subplots(len(thetas), len(rates),
                             figsize=(2.15 * len(rates), 1.85 * len(thetas)),
                             sharex=True, sharey=True)
    axes = np.atleast_2d(axes)
    ymax = 1.0
    for i, th in enumerate(thetas):
        col = COLORS[th]
        for j, rate in enumerate(rates):
            ax = axes[i, j]
            e = data[th].get(rate)
            if e is not None:
                thr, gp = client_series(e["df"])
                srv = server_series(e["run_dir"], e["t0"])
                ymax = max(ymax, float(thr.max()), float(srv.max()))
                ax.fill_between(CENTERS_MIN, gp, thr, color=col, alpha=0.15,
                                linewidth=0)
                ax.plot(CENTERS_MIN, srv, color="0.45", ls=":", linewidth=1.0)
                ax.plot(CENTERS_MIN, thr, color=col, ls="--", linewidth=1.0)
                ax.plot(CENTERS_MIN, gp, color=col, ls="-", linewidth=1.5)
            if i == 0:
                ax.set_title(f"{rate:g} jobs/s", pad=3)
            if j == 0:
                ax.set_ylabel(f"{label_of(th)}\noutput tokens/s")
            if i == len(thetas) - 1:
                ax.set_xlabel("Time in run (min)")
            ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
            ax.set_axisbelow(True)
    axes[0, 0].set_ylim(0, ymax * 1.05)
    h = [plt.Line2D([], [], color="0.45", ls=":", label="throughput (server counter)"),
         plt.Line2D([], [], color="0.35", ls="--", label="throughput (client, reconstructed)"),
         plt.Line2D([], [], color="0.35", ls="-", label="goodput (SLO-meeting requests)"),
         plt.Rectangle((0, 0), 1, 1, color="0.35", alpha=0.15, label="wasted (gap)")]
    axes[0, 0].legend(handles=h, loc="lower center",
                      bbox_to_anchor=(len(rates) / 2.0, 1.06), ncol=4)
    p = os.path.join(out_dir, "exp09_throughput_vs_goodput_over_time.png")
    fig.tight_layout(rect=(0, 0, 1, 0.975))
    fig.savefig(p, dpi=300)
    plt.close(fig)
    print("wrote:", p)


def fig_vs_rate(summ, out_dir):
    """Absolute goodput tok/s | wasted share | goodput relative to no-admission."""
    fig, axes = plt.subplots(1, 3, figsize=(10.2, 3.2))
    ticks = sorted(summ["rate_jps"].unique())
    base = (summ[summ["theta"] == 0.0]
            .set_index("rate_jps")["goodput_tok_per_s"])
    for th in sorted(summ["theta"].unique()):
        s = summ[summ["theta"] == th].sort_values("rate_jps")
        st = dict(color=COLORS[th], marker="o", markeredgecolor="white",
                  markeredgewidth=0.5, label=label_of(th))
        axes[0].plot(s["rate_jps"], s["goodput_tok_per_s"], **st)
        axes[0].plot(s["rate_jps"], s["throughput_tok_per_s"],
                     color=COLORS[th], ls=":", lw=0.9, alpha=0.55)
        axes[1].plot(s["rate_jps"], s["wasted_pct"], **st)
        rel = 100.0 * (s["goodput_tok_per_s"].values
                       / base.reindex(s["rate_jps"]).values - 1.0)
        axes[2].plot(s["rate_jps"], rel, **st)
    axes[0].set_ylabel("Output tokens/s")
    axes[0].set_title("Goodput (solid) vs throughput (dotted)")
    axes[1].set_ylabel("Wasted share of produced tokens (%)")
    axes[1].set_title("Wasted fraction")
    axes[1].set_ylim(-3, 103)
    axes[2].axhline(0.0, color="0.25", lw=0.8)
    axes[2].set_ylabel("Goodput vs no admission (%)")
    axes[2].set_title("Relative goodput (log scale, 0 = parity)")
    axes[2].set_yscale("symlog", linthresh=10)
    axes[2].set_yticks([-10, 0, 10, 100, 700],
                       ["-10", "0", "+10", "+100", "+700"])
    for ax in axes:
        ax.set_xlabel("Offered rate (jobs/s)")
        ax.set_xticks(ticks, [f"{r:g}" for r in ticks], fontsize=7,
                      rotation=45, ha="right", rotation_mode="anchor")
        ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
        ax.set_axisbelow(True)
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.99),
               ncol=len(ARMS))
    fig.tight_layout()
    p = os.path.join(out_dir, "exp09_goodput_vs_rate.png")
    fig.savefig(p, dpi=300, bbox_inches="tight")
    plt.close(fig)
    print("wrote:", p)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp09_tokens")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    print(f"goodput = OUTPUT tokens of SLO-meeting requests (whole-request, "
          f"TTFT<={TTFT_SLO_S:g}s & meanTBT<={TBT_SLO_MS:g}ms), spread over "
          f"[start+TTFT, end]; window [{WIN_LO:g},{WIN_HI:g}]s / {WIN_S:g}s span")

    data = load(a.results_dir)
    rows = []
    for th, per_rate in sorted(data.items()):
        for rate, e in sorted(per_rate.items()):
            df = e["df"]
            g = df["good"].values
            srv = server_series(e["run_dir"], e["t0"])
            produced = float(df["out"].sum())
            good_tok = float(df.loc[g, "out"].sum())
            rows.append({
                "theta": th, "rate_jps": rate,
                "n_calls": len(df), "n_good": int(g.sum()),
                "n_rejected": int(df["rejected"].sum()),
                "goodput_out_tok": good_tok,
                "wasted_out_tok": produced - good_tok,
                "goodput_tok_per_s": good_tok / WIN_S,
                "throughput_tok_per_s": produced / WIN_S,
                "throughput_served_tok_per_s": float(
                    df.loc[df["served"].values, "out"].sum()) / WIN_S,
                "server_tok_per_s": float(srv.mean()),
                "wasted_pct": 100.0 * (1.0 - good_tok / produced) if produced else np.nan,
            })
    summ = pd.DataFrame(rows)
    csv = os.path.join(a.out_dir, "exp09_goodput_tokens.csv")
    summ.to_csv(csv, index=False)
    print("wrote:", csv)

    piv = summ.pivot(index="rate_jps", columns="theta",
                     values=["goodput_tok_per_s", "throughput_tok_per_s",
                             "server_tok_per_s", "wasted_pct"])
    print("\n(goodput | client throughput | server throughput) tokens/s, wasted %")
    print(piv.round(0).to_string())

    with plt.rc_context(PAPER_STYLE):
        fig_over_time(data, a.out_dir)
        fig_vs_rate(summ, a.out_dir)


if __name__ == "__main__":
    main()
