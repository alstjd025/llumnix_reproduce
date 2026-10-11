#!/usr/bin/env python3
"""EXP-10 vs EXP-06: request-level SLO attainment on a measured call-rate axis.

EXP-06 releases jobs (closed-loop chains: call n+1 waits for call n), so its
jobs/s axis is not comparable to EXP-10's open-loop requests/s. The common
axis is the *measured* call arrival rate in the steady window [60s, dur-20s]:
count every request-row arrival (classified + excluded + rejected) divided by
the window length. Attainment is the classified-only steady rate (same
definition as slo_sliding_window.py).

Outputs (in --out-dir):
  exp10_vs_exp06_callrate.csv
  exp10_vs_exp06_callrate.png
"""

import argparse
import glob
import os
import importlib.util as _ilu

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

_spec = _ilu.spec_from_file_location(
    "slomod", os.path.join(os.path.dirname(__file__), "slo_sliding_window.py"))
_slo = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_slo)

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "legend.fontsize": 8, "legend.frameon": False, "lines.linewidth": 1.5}


def steady_stats(run_dir: str, offered_label: float) -> dict:
    """Measured steady-window call arrival rate + classified attainment.

    Steady window = arrivals in [60s, dur-20s] (same convention as
    plot_slo_vs_throughput's steady view). classify() itself is whole-run,
    so the window filter is applied here on the arrival timeline.
    """
    df = pd.read_csv(os.path.join(run_dir, "metrics.csv"))
    r = df[df.agent != "job_summary"].copy()
    r["rel"] = r["start_time"] - r["start_time"].min()
    dur = r["rel"].max()
    lo, hi = 60.0, dur - 20.0
    win = r[(r["rel"] >= lo) & (r["rel"] < hi)]
    win_s = max(1.0, hi - lo)

    bl = lambda c: (win[c].fillna(False).astype(bool)
                    if c in win.columns else pd.Series(False, index=win.index))
    rejected = bl("is_rejected")
    gw = _slo.gw_timeout_mask(win, bl)   # gateway 300s kills -> TTFT violations
    excluded = (bl("is_error") | bl("is_timeout") | bl("is_server_terminated")) & ~rejected & ~gw
    cls = win[~excluded & ~rejected]
    ttft_viol = (cls["first_token_latency"] > _slo.TTFT_SLO_S) | gw.reindex(cls.index, fill_value=False)
    tbt_viol = pd.to_numeric(cls["tbt_mean_ms"], errors="coerce") > _slo.TBT_SLO_MS
    n_at = int((~(ttft_viol | tbt_viol)).sum())
    return dict(
        run=os.path.basename(run_dir),
        offered=offered_label,
        call_rate=len(win) / win_s,
        classified=len(cls),
        attain_pct=100.0 * n_at / max(1, len(cls)),
        excluded=int(excluded.sum()),
        rejected=int(rejected.sum()),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--exp10-glob", default="results/*exp10_replay_lambda_*")
    ap.add_argument("--exp06-glob", default="results/*exp06_swe_sweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp10_slo")
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    rows = []
    for d in sorted(glob.glob(args.exp10_glob),
                    key=lambda x: float(x.split("lambda_")[1])):
        lam = float(d.split("lambda_")[1])
        s = steady_stats(d, lam); s["series"] = "exp10 open-loop (req/s offered)"
        rows.append(s)
        print(f"exp10 λ={lam:5.1f}: measured {s['call_rate']:5.2f} calls/s "
              f"attain={s['attain_pct']:5.1f}%")
    for d in sorted(glob.glob(args.exp06_glob),
                    key=lambda x: float(x.split("rpm_")[1])):
        jobs_s = float(d.split("rpm_")[1]) / 60.0
        s = steady_stats(d, jobs_s); s["series"] = "exp06 closed-loop (jobs/s offered)"
        rows.append(s)
        print(f"exp06 {jobs_s:4.2f} jobs/s: measured {s['call_rate']:5.2f} calls/s "
              f"attain={s['attain_pct']:5.1f}%")

    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(args.out_dir, "exp10_vs_exp06_callrate.csv"), index=False)

    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(figsize=(7.2, 4.6))
        for series, style, color in (
            ("exp10 open-loop (req/s offered)", "o-", "#1f77b4"),
            ("exp06 closed-loop (jobs/s offered)", "s--", "#d62728"),
        ):
            g = df[df["series"] == series].sort_values("call_rate")
            ax.plot(g["call_rate"], g["attain_pct"], style, color=color,
                    label=series, ms=5)
            for _, r in g.iterrows():
                ax.annotate(f"{r['offered']:g}", (r["call_rate"], r["attain_pct"]),
                            textcoords="offset points", xytext=(4, 4), fontsize=6.5,
                            color=color)
        ax.set_xlabel("measured call arrival rate in steady window (calls/s)")
        ax.set_ylabel("request SLO attainment (%)")
        ax.set_ylim(-3, 105)
        ax.grid(axis="both", ls=":", lw=0.5, alpha=0.5)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=1)
        ax.set_title("SWE calls: open-loop replay vs closed-loop chains\n"
                     "(point labels = offered rate; SLO TTFT≤5s & meanTBT≤50ms; "
                     "classified-only attainment)", fontsize=8.5, pad=28)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp10_vs_exp06_callrate.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)


if __name__ == "__main__":
    main()
