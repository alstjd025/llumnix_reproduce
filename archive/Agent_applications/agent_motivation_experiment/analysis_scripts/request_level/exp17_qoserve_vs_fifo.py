"""EXP-17 — QoServe (Niyama DeadlineScheduler) vs FIFO baseline, mix A (1:1:1).

Both arms use the SAME workload (mixed_request_level_poisson 1:1:1), the SAME
rpm grid, and the SAME EXP-14 protocol (cold restart per condition, warmup 60s +
5 min, load 12 procs). The only difference is the engine scheduler:

  FIFO    : results/*exp14_mixA_rpm_*         (stock vLLM ordering)
  QoServe : results/*exp17_qoserve_mixA_rpm_* (deadline_sched.DeadlineScheduler,
            client sends priority = slo_ms*1000 + tbt_ms)

Attainment is judged post-hoc by the SAME class-differentiated SLO rules for both
arms, reusing `exp14_per_class_slo` (chat TTFT<=5s & TBT<=50ms, deepresearch
TTFT<=10s & TBT<=100ms, swe E2E<=SWE_E2E_SLO_S) and the standard arrival-anchored
window from `plot_per_engine_attainment.served_rows`. So any difference is the
scheduler, not the measurement.

Usage:
  SWE_E2E_SLO_S=30 python analysis_scripts/request_level/exp17_qoserve_vs_fifo.py \
      --out-dir results/aggregate_analysis/exp17_qoserve
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

# Project figure convention (CLAUDE.md "Figure Styling"): serif, thin marks,
# frameless legend above the axes, y-only dotted grid, dpi 300.
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
# Two systems, not the named metric roles -> tab10 in fixed order. Identity is
# never colour-alone: each arm also carries its own linestyle + marker.
ARM_STYLE = {
    "fifo":    dict(color="#d62728", ls="--", marker="o", label="FIFO (baseline)"),
    "edf":     dict(color="#2ca02c", ls="-.", marker="^", label="EDF (deadline order only)"),
    "sjf":     dict(color="#9467bd", ls=":",  marker="D", label="SJF (shortest job)"),
    "srpf":    dict(color="#8c564b", ls=(0,(3,1,1,1)), marker="v", label="SRPF (shortest remaining)"),
    "qoserve": dict(color="#1f77b4", ls="-",  marker="s", label="QoServe (deadline)"),
    "polyserve": dict(color="#000000", ls="-", marker="*", label="PolyServe (ROUTING, engine=FIFO)"),
}


def _axis(ax, ylabel):
    ax.set_xlabel("Offered rate (req/s)")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
    ax.set_axisbelow(True)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import served_rows, class_of  # noqa: E402
from exp14_per_class_slo import SLO_RULES, per_class_violate, attain, out_tps  # noqa: E402

ARMS = {
    "fifo":    "*exp14_mixA_rpm_*",
    "edf":     "*exp18_edf_mixA_rpm_*",
    "sjf":     "*exp19_sjf_mixA_rpm_*",
    "srpf":    "*exp20_srpf_mixA_rpm_*",
    # fixed DeadlineScheduler (step TOTAL token budget, bounded relegation scan).
    # The pre-fix arm (*exp17_qoserve_mixA_rpm_*) is kept on disk but excluded:
    # it measured the admission-explosion bug, not the policy.
    "qoserve": "*exp17b_qoservefix_mixA_rpm_*",
    # EXP-21 varies the ROUTING policy with the engine held at stock FIFO, the
    # perpendicular axis to the five above (which vary the engine scheduler with
    # routing held at load-balance). Its load-balance arm reproduces the fifo arm
    # to within 0.2pp at the one rate they share, which is what makes the two
    # experiments joinable on one chart despite different rpm grids -- rate is a
    # continuous axis, each arm just carries its own sample points.
    "polyserve": "*exp21_polyserve_mixA_rpm_*",
}
CLASSES = ("chat", "deepresearch", "swe")


def load_condition(run_dir):
    sr = served_rows(run_dir)
    if sr is None or sr.empty:
        return None
    sr["class"] = sr["task_id"].map(class_of)
    sr["violate_pc"] = per_class_violate(sr)
    return sr


def collect(results_dir, pattern):
    # A rate can appear twice (e.g. a standalone verification run reusing the
    # session name); keep the newest directory for each rpm so the curve has one
    # point per rate.
    by_rpm = {}
    for d in sorted(glob.glob(os.path.join(results_dir, pattern))):
        rpm = int(re.search(r"rpm_(\d+)", d).group(1))
        by_rpm[rpm] = d  # later dir name (newer timestamp prefix) wins
    rows = []
    for rpm in sorted(by_rpm):
        sr = load_condition(by_rpm[rpm])
        if sr is None:
            continue
        rows.append((rpm, sr))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    print(f"SLO: chat TTFT<=5s&TBT<=50ms | deepresearch TTFT<=10s&TBT<=100ms "
          f"| swe E2E<={SLO_RULES['swe']['e2e']:.0f}s")

    recs = []
    for arm, pattern in ARMS.items():
        for rpm, sr in collect(a.results_dir, pattern):
            rec = {"arm": arm, "rpm": rpm, "rate_rps": rpm / 60.0,
                   "n": len(sr), "fleet_attain": attain(sr),
                   "out_tok_per_s": out_tps(sr)}
            for c in CLASSES:
                sub = sr[sr["class"] == c]
                rec[f"attain_{c}"] = attain(sub)
                rec[f"n_{c}"] = len(sub)
            # fleet_attain weights each class by how many of ITS requests got
            # served, and the arms do not serve the same mix. PolyServe protects
            # two classes so completely that they dominate its served set (10%
            # swe at 50 req/s vs ~30% for the engine-scheduler arms), which
            # inflates its fleet number. The workload is offered 1:1:1, so weight
            # the classes equally -- that is the mix-independent comparison and
            # the one the headline figure uses.
            per_class = [rec[f"attain_{c}"] for c in CLASSES]
            rec["attain_equalmix"] = (float(np.nanmean(per_class))
                                      if not all(pd.isna(v) for v in per_class)
                                      else float("nan"))
            recs.append(rec)

    df = pd.DataFrame(recs).sort_values(["arm", "rpm"])
    csv = os.path.join(a.out_dir, "exp17_qoserve_vs_fifo.csv")
    df.to_csv(csv, index=False)
    print(f"\nwrote {csv}\n")

    # side-by-side table per rate, one block of columns per arm
    piv = df.pivot(index="rpm", columns="arm")
    arms = [k for k in ARM_STYLE if k in set(df["arm"])]
    hdr = f"{'rpm':>6}{'req/s':>7} | " + "".join(f"{k[:7]+' eqmix':>14}" for k in arms)
    hdr += " | " + "".join(f"{k[:4]+' '+c[:4]:>11}" for c in CLASSES for k in arms)
    print(hdr)
    for rpm in sorted(df["rpm"].unique()):
        def g(col, arm):
            try:
                v = piv[(col, arm)].loc[rpm]
                return v if pd.notna(v) else None
            except KeyError:
                return None
        def fmt(v, w=14):
            return f"{v:{w}.1f}" if v is not None else f"{'-':>{w}}"
        line = f"{rpm:>6}{rpm/60:>7.1f} | " + "".join(fmt(g("attain_equalmix", k)) for k in arms)
        line += " | " + "".join(fmt(g(f"attain_{c}", k), 11) for c in CLASSES for k in arms)
        print(line)

    make_figures(df, a.results_dir, a.out_dir)


def engine_series(run_dir, keysub):
    vals = []
    for f in sorted(glob.glob(os.path.join(run_dir, "server_metrics", "engine_*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict):
                    continue
                for k, v in rec.items():
                    if keysub in k:
                        try:
                            vals.append(float(v))
                        except (TypeError, ValueError):
                            pass
    return vals


def mechanism_table(results_dir):
    """KV p90 + total preemptions per (arm, rate) — the 'why' behind attainment."""
    rows = []
    for arm, pattern in ARMS.items():
        by_rpm = {}
        for d in sorted(glob.glob(os.path.join(results_dir, pattern))):
            by_rpm[int(re.search(r"rpm_(\d+)", d).group(1))] = d
        for rpm, d in sorted(by_rpm.items()):
            kv = sorted(engine_series(d, "kv_cache_usage_perc"))
            pre = engine_series(d, "num_preemptions_total")
            rows.append({"arm": arm, "rate_rps": rpm / 60.0,
                         "kv_p90": kv[int(len(kv) * .9)] if kv else float("nan"),
                         "preemptions": max(pre) if pre else 0})
    return pd.DataFrame(rows)


def _plot_arms(ax, df, ycol):
    for arm, st in ARM_STYLE.items():
        s = df[df["arm"] == arm].sort_values("rate_rps")
        if s.empty:
            continue
        ax.plot(s["rate_rps"], s[ycol], color=st["color"], ls=st["ls"],
                marker=st["marker"], label=st["label"],
                markeredgecolor="white", markeredgewidth=0.5)


def make_figures(df, results_dir, out_dir):
    with plt.rc_context(PAPER_STYLE):
        # 1) headline: fleet attainment
        fig, ax = plt.subplots(figsize=(5.4, 3.6))
        _plot_arms(ax, df, "attain_equalmix")
        _axis(ax, "SLO attainment (%), classes weighted 1:1:1")
        ax.set_ylim(-3, 105)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2)
        p = os.path.join(out_dir, "exp17_fleet_attainment.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig); print("wrote:", p)

        # 2) per-class small multiples
        fig, axes = plt.subplots(1, 3, figsize=(9.6, 3.2), sharey=True)
        for ax, c in zip(axes, CLASSES):
            _plot_arms(ax, df, f"attain_{c}")
            _axis(ax, "SLO attainment (%)" if c == CLASSES[0] else "")
            ax.set_ylim(-3, 105)
            ax.set_title(c, pad=4)
        axes[0].legend(loc="lower center", bbox_to_anchor=(1.7, 1.10), ncol=2)
        p = os.path.join(out_dir, "exp17_per_class.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig); print("wrote:", p)

        # 3) mechanism: KV saturation + preemption thrash (separate axes, never twinned)
        mech = mechanism_table(results_dir)
        mech.to_csv(os.path.join(out_dir, "exp17_mechanism.csv"), index=False)
        fig, axes = plt.subplots(1, 2, figsize=(7.6, 3.2))
        _plot_arms(axes[0], mech, "kv_p90")
        _axis(axes[0], "KV cache usage, p90 (fraction)")
        axes[0].set_ylim(0, 1.05)
        _plot_arms(axes[1], mech, "preemptions")
        _axis(axes[1], "Preemptions (total)")
        axes[1].set_ylim(bottom=0)
        axes[0].legend(loc="lower center", bbox_to_anchor=(1.1, 1.05), ncol=2)
        p = os.path.join(out_dir, "exp17_mechanism.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig); print("wrote:", p)


if __name__ == "__main__":
    main()
