#!/usr/bin/env python3
"""Job-level goodput vs offered rate for dependency-chain workloads (EXP-06).

A JOB (SWE chain) counts toward goodput only if the WHOLE chain completed
(job_completed & success on its job_summary row). Two variants:
  goodput_complete = completed jobs / s
  goodput_slo      = completed jobs whose EVERY chain call also met the
                     per-request SLO (TTFT <= 5 s AND tbt_mean_ms <= 50;
                     calls with no TBT samples judged on TTFT alone) / s

Censoring control: only jobs SUBMITTED inside [--submit-from, --submit-to]
(seconds, relative to the first request in the run; default 60..360 = skip the
warmup minute, then a 5-min admission slice) are classified. With 12-min
measurement runs every such job has >= 420 s to finish, so "not completed" at
high rate is genuine overload, not run-end truncation. Jobs still unfinished at
run end DO count as failures under this definition (reported separately).

Job identity = (task_id, job_submit_time) — unique across MP shards; chain-call
rows link to their job by the same pair.

Outputs: <out>/job_goodput.csv, <out>/job_goodput_vs_rate.png
"""

import argparse
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

TTFT_SLO_S = 5.0
TBT_SLO_MS = 50.0

PAPER = {"font.family": "serif", "font.size": 9, "axes.labelsize": 10,
         "axes.titlesize": 9.5, "legend.fontsize": 8, "legend.frameon": False,
         "lines.linewidth": 1.4}


def analyze(run_dir, t_from, t_to):
    df = pd.read_csv(os.path.join(run_dir, "metrics.csv"))
    js = df[df.agent == "job_summary"].copy()
    calls = df[df.agent != "job_summary"].copy()
    if js.empty:
        return None
    t0 = df["start_time"].min()
    js["submit_rel"] = js["job_submit_time"] - t0

    el = js[(js["submit_rel"] >= t_from) & (js["submit_rel"] < t_to)].copy()
    if el.empty:
        return None
    done = el[(el["job_completed"] == True) & (el["success"] == True)]  # noqa: E712

    # per-call SLO pass, joined back to jobs
    ttft_ok = calls["first_token_latency"] <= TTFT_SLO_S
    tbt = pd.to_numeric(calls["tbt_mean_ms"], errors="coerce")
    calls["slo_ok"] = ttft_ok & (tbt.isna() | (tbt <= TBT_SLO_MS))
    per_job = calls.groupby(["task_id", "job_submit_time"])["slo_ok"].all()
    key = list(zip(done["task_id"], done["job_submit_time"]))
    slo_good = int(per_job.reindex(key).fillna(False).sum())

    mk = (done["job_end_time"] - done["job_submit_time"]).astype(float)
    span = t_to - t_from
    return dict(
        eligible=len(el), completed=len(done), slo_good=slo_good,
        unfinished=int((~el["job_completed"].fillna(False).astype(bool)).sum()),
        goodput_complete=len(done) / span, goodput_slo=slo_good / span,
        mk_p50=mk.quantile(0.5) if len(mk) else np.nan,
        mk_p95=mk.quantile(0.95) if len(mk) else np.nan,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="results/*exp06_swe_sweep_rpm_*")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/exp06/goodput")
    ap.add_argument("--submit-from", type=float, default=60.0)
    ap.add_argument("--submit-to", type=float, default=360.0)
    args = ap.parse_args()
    os.makedirs(args.out_dir, exist_ok=True)
    dirs = sorted(glob.glob(args.glob), key=lambda x: int(x.split("rpm_")[1]))

    rows = []
    for run in dirs:
        rpm = int(run.split("rpm_")[1])
        r = analyze(run, args.submit_from, args.submit_to)
        if r is None:
            print(f"rpm{rpm}: no eligible jobs, skipped"); continue
        r["offered_jobps"] = rpm / 60.0
        rows.append(r)
        print(f"{r['offered_jobps']:>5.2f} jobs/s: eligible={r['eligible']:>4} "
              f"completed={r['completed']:>4} ({100*r['completed']/r['eligible']:.0f}%) "
              f"slo_good={r['slo_good']:>4} goodput={r['goodput_complete']:.3f}/s "
              f"slo_goodput={r['goodput_slo']:.3f}/s mk_p50={r['mk_p50']:.0f}s")
    t = pd.DataFrame(rows)
    t.to_csv(os.path.join(args.out_dir, "job_goodput.csv"), index=False)

    with plt.rc_context(PAPER):
        fig, ax = plt.subplots(1, 2, figsize=(11, 4.2))
        x = t["offered_jobps"]
        a = ax[0]
        lim = x.max() * 1.05
        a.plot([0, lim], [0, lim], color="0.7", ls=":", label="ideal (all jobs good)")
        a.plot(x, t["goodput_complete"], "o-", color="#1f77b4", label="goodput: chain completed")
        a.plot(x, t["goodput_slo"], "s-", color="#d62728", label="goodput: completed + all calls in SLO")
        a.set_xlabel("offered rate (jobs/s)"); a.set_ylabel("goodput (jobs/s)")
        a.set_title("Job goodput vs offered rate")
        a.set_xticks(x); a.legend()

        b = ax[1]
        b.plot(x, 100 * t["completed"] / t["eligible"], "o-", color="#1f77b4",
               label="chain completion %")
        b.plot(x, 100 * t["slo_good"] / t["eligible"], "s-", color="#d62728",
               label="completed + SLO %")
        b.set_xlabel("offered rate (jobs/s)"); b.set_ylabel("% of eligible jobs")
        b.set_ylim(0, 105); b.set_title(
            f"Per-job outcome (submitted {args.submit_from:.0f}-{args.submit_to:.0f}s)")
        b.set_xticks(x); b.legend()
        for p in ax:
            p.grid(ls=":", lw=0.5, alpha=0.5)
        fig.tight_layout()
        out = os.path.join(args.out_dir, "job_goodput_vs_rate.png")
        fig.savefig(out, dpi=150, bbox_inches="tight"); plt.close(fig)
        print("wrote:", out)


if __name__ == "__main__":
    main()
