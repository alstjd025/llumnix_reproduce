#!/usr/bin/env python3
"""EXP-09 overlay: SWE tool-delay rate sweeps at several admission thetas
vs the no-admission baseline (EXP-06).

Two views per rate (x = offered jobs/s), one line per theta:
  request-level (steady window, exp07/08 definitions):
    exp09_req_attain_vs_rate.png   offered / admitted attainment + reject%
  job-level (submit window [60,360)s, chain-complete definitions):
    exp09_job_goodput_vs_rate.png  completion% and completed+all-calls-SLO%

Thetas are auto-discovered from results/*exp09_swe_kvadm_th####_rpm_*; a theta
is included only when at least --min-conds conditions exist (skips sweeps
still in progress). Re-run after the remaining sweeps finish to extend.
"""

import argparse
import glob
import os
import re
import importlib.util as _ilu

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_spec = _ilu.spec_from_file_location(
    "exp07mod", os.path.join(os.path.dirname(__file__), "plot_exp07_theta.py"))
_exp07 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(_exp07)
condition_stats = _exp07.condition_stats

TTFT_SLO_S, TBT_SLO_MS = 5.0, 50.0
SUB_LO, SUB_HI = 60.0, 360.0
COLORS = {0.0: "0.25", 0.3: "#9467bd", 0.4: "#2ca02c", 0.5: "#ff7f0e", 0.6: "#d62728"}
PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 8, "legend.frameon": False,
         "lines.linewidth": 1.5, "lines.markersize": 4.5}


def job_stats(run_dir):
    df = pd.read_csv(os.path.join(run_dir, "metrics.csv"), low_memory=False)
    js = df[df.agent == "job_summary"].copy()
    calls = df[df.agent != "job_summary"].copy()
    t0 = df["start_time"].min()
    js["sub"] = js["job_submit_time"] - t0
    el = js[(js["sub"] >= SUB_LO) & (js["sub"] < SUB_HI)]
    done = el[(el["job_completed"] == True) & (el["success"] == True)]  # noqa: E712
    ttft_ok = pd.to_numeric(calls["first_token_latency"], errors="coerce") <= TTFT_SLO_S
    tbt = pd.to_numeric(calls["tbt_mean_ms"], errors="coerce")
    calls["ok"] = ttft_ok & (tbt.isna() | (tbt <= TBT_SLO_MS))
    per_job = calls.groupby(["task_id", "job_submit_time"])["ok"].all()
    key = list(zip(done["task_id"], done["job_submit_time"]))
    slo_good = int(per_job.reindex(key).fillna(False).sum())
    # jobs killed specifically by an admission-rejected call
    rej_killed = int(el["is_rejected"].fillna(False).astype(bool).sum()) \
        if "is_rejected" in el.columns else 0
    return dict(eligible=len(el), completed=len(done), slo_good=slo_good,
                rej_killed=rej_killed)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp09")
    ap.add_argument("--min-conds", type=int, default=11)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)

    groups = {0.0: sorted(glob.glob("results/*exp06_swe_sweep_rpm_*"))}
    for d in glob.glob("results/*exp09_swe_kvadm_th*_rpm_*"):
        th = int(re.search(r"th(\d{4})_rpm", d).group(1)) / 1000.0
        groups.setdefault(th, []).append(d)
    groups = {th: sorted(ds, key=lambda x: int(x.split("rpm_")[1]))
              for th, ds in groups.items() if len(ds) >= args.min_conds or th == 0.0}
    print("thetas included:", sorted(groups))

    rows = []
    for th, dirs in sorted(groups.items()):
        for d in dirs:
            rpm = int(d.split("rpm_")[1])
            s = condition_stats(d)
            s.update(job_stats(d))
            s.update(theta=th, rate=rpm / 60.0)
            rows.append(s)
            print(f"th={th if th else 'off':>4} {s['rate']:>5.2f} j/s: "
                  f"req adm={s['attain_admitted']:5.1f} off={s['attain_offered']:5.1f} "
                  f"rej={s['reject_pct']:4.1f} | job cmpl={100*s['completed']/max(1,s['eligible']):5.1f} "
                  f"slo={100*s['slo_good']/max(1,s['eligible']):5.1f}")
    t = pd.DataFrame(rows).sort_values(["theta", "rate"])
    t["job_complete_pct"] = 100 * t["completed"] / t["eligible"].clip(lower=1)
    t["job_slo_pct"] = 100 * t["slo_good"] / t["eligible"].clip(lower=1)
    t.to_csv(os.path.join(args.out_dir, "exp09_summary.csv"), index=False)

    def lines(ax, col, style="o-"):
        for th, g in t.groupby("theta"):
            g = g.sort_values("rate")
            ax.plot(g["rate"], g[col], style, color=COLORS.get(th, "0.5"),
                    label=("no admission" if th == 0 else f"θ={th:g}"))
        ax.set_xlabel("offered rate (jobs/s)")
        ax.set_xticks(sorted(t["rate"].unique()),
                      [f"{r:g}" for r in sorted(t["rate"].unique())], fontsize=7)
        ax.grid(axis="y", ls=":", lw=0.6, alpha=0.6)

    with plt.rc_context(PAPER):
        fig, axes = plt.subplots(1, 3, figsize=(13.2, 4.2), sharey=True)
        lines(axes[0], "attain_offered"); axes[0].set_title("Request SLO attainment (offered)")
        axes[0].set_ylabel("% of steady-window requests"); axes[0].set_ylim(-3, 105)
        lines(axes[1], "attain_admitted"); axes[1].set_title("Request SLO attainment (admitted only)")
        lines(axes[2], "reject_pct", "x--"); axes[2].set_title("Rejection rate")
        h, l = axes[0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.99), ncol=len(groups))
        fig.suptitle("EXP-09 — SWE tool-delay: request-level SLO vs offered rate, per admission θ",
                     y=1.12)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp09_req_attain_vs_rate.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)

        fig, axes = plt.subplots(1, 2, figsize=(10.4, 4.4), sharey=True)
        lines(axes[0], "job_complete_pct"); axes[0].set_title("Chain completion %")
        axes[0].set_ylabel("% of eligible jobs (submitted 60–360 s)"); axes[0].set_ylim(-3, 105)
        lines(axes[1], "job_slo_pct"); axes[1].set_title("Completed + every call in SLO %")
        h, l = axes[0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", bbox_to_anchor=(0.5, 0.99), ncol=len(groups))
        fig.suptitle("EXP-09 — SWE tool-delay: job-level goodput vs offered rate, per admission θ",
                     y=1.14)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "exp09_job_goodput_vs_rate.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)


if __name__ == "__main__":
    main()
