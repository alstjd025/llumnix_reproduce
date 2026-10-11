"""EXP-21 — PolyServe routing vs stock Llumnix load-balance, mix A (1:1:1).

Both arms run the SAME workload, the SAME rpm grid and the SAME EXP-14 protocol
(cold engine restart per condition, 60s warmup at 60 rpm, then 5 min steady), and
the client sends the packed per-request SLO in both -- load-balance simply
ignores it. The engine is stock FIFO in both. So the only difference is the
scheduler's routing policy:

  loadbalance : least of DispatchNeutralLoadMetric  (stock Llumnix)
  polyserve   : tier affinity + the PolyServe 4.5-4.7 feasibility test + least-load

Note on "admission": PolyServe's admission test decides WHICH SERVER may take a
request, not whether the system accepts it. It has no reject path, and neither
does this implementation -- the filter is dropped on Llumnix's fallback pass, so
a request that no server can serve within SLO still gets placed on the least
loaded server in its tier. Measured: 45 of 45,300 requests returned 503 under
PolyServe and 20 of 45,300 under load-balance, both from the gateway giving up
after its hold-and-retry window at extreme saturation, not from either policy
declining anything.

That makes this the complement of EXP-17..20, which varied the ENGINE scheduler
and held routing fixed. Attainment is judged by the same class-differentiated
rules those used, so the numbers are directly comparable with the 5-arm figures.

Usage:
  SWE_E2E_SLO_S=30 python analysis_scripts/request_level/exp21_polyserve.py \
      --out-dir results/aggregate_analysis/exp21_polyserve
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
# Identity is never colour-alone: each arm also carries a linestyle and marker.
ARM_STYLE = {
    "loadbalance": dict(color="#d62728", ls="--", marker="o",
                        label="Llumnix load-balance (routing baseline)"),
    "polyserve":   dict(color="#1f77b4", ls="-", marker="s",
                        label="PolyServe (tier partition + SLO feasibility + least-load)"),
}
ARMS = {
    "loadbalance": "*exp21_loadbalance_mixA_rpm_*",
    "polyserve":   "*exp21_polyserve_mixA_rpm_*",
}
CLASSES = ("chat", "deepresearch", "swe")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import served_rows, class_of  # noqa: E402
from exp14_per_class_slo import SLO_RULES, per_class_violate, attain, out_tps  # noqa: E402


def _axis(ax, ylabel):
    ax.set_xlabel("Offered rate (req/s)")
    ax.set_ylabel(ylabel)
    ax.grid(axis="y", linestyle=":", linewidth=0.7, alpha=0.6)
    ax.set_axisbelow(True)


def collect(results_dir, pattern):
    # A rate can appear twice if a condition was re-run; keep the newest
    # directory so the curve has one point per rate.
    by_rpm = {}
    for d in sorted(glob.glob(os.path.join(results_dir, pattern))):
        m = re.search(r"rpm_(\d+)", d)
        if m:
            by_rpm[int(m.group(1))] = d
    out = []
    for rpm in sorted(by_rpm):
        sr = served_rows(by_rpm[rpm])
        if sr is None or sr.empty:
            continue
        sr["class"] = sr["task_id"].map(class_of)
        sr["violate_pc"] = per_class_violate(sr)
        out.append((rpm, by_rpm[rpm], sr))
    return out


def engine_series(run_dir, keysub):
    vals = []
    for f in sorted(glob.glob(os.path.join(run_dir, "server_metrics", "engine_*.jsonl"))):
        with open(f) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if isinstance(rec, dict):
                    for k, v in rec.items():
                        if keysub in k:
                            try:
                                vals.append(float(v))
                            except (TypeError, ValueError):
                                pass
    return vals


def routing_spread(run_dir):
    """How unevenly each class was spread across engines.

    PolyServe's whole premise is that a class should be confined to its tier's
    servers, so this is the direct check that tier affinity did something: a
    class pinned to one engine of four scores 1.0, a class spread evenly scores
    0.0. Without this, an attainment tie would leave it unclear whether the
    partition was ever in force.
    """
    csv_path = os.path.join(run_dir, "analysis", "request_engine.csv")
    if not os.path.exists(csv_path):
        return None
    df = pd.read_csv(csv_path)
    if df.empty or "engine_port" not in df:
        return None
    df = df[~df.get("migrated", False).astype(bool)]
    df["class"] = df["task_id"].map(class_of)
    out = {}
    for cls, sub in df.groupby("class"):
        share = sub["engine_port"].value_counts(normalize=True)
        n = max(len(df["engine_port"].unique()), 1)
        # Normalised concentration: 0 when spread evenly over all engines, 1 when
        # confined to a single one.
        hhi = float((share ** 2).sum())
        out[cls] = (hhi - 1.0 / n) / (1.0 - 1.0 / n) if n > 1 else 1.0
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    print(f"SLO: chat TTFT<=5s & TBT<=50ms | deepresearch TTFT<=10s & TBT<=100ms "
          f"| swe E2E<={SLO_RULES['swe']['e2e']:.0f}s")

    recs = []
    for arm, pattern in ARMS.items():
        for rpm, run_dir, sr in collect(a.results_dir, pattern):
            rec = {"arm": arm, "rpm": rpm, "rate_rps": rpm / 60.0, "n": len(sr),
                   "fleet_attain": attain(sr), "out_tok_per_s": out_tps(sr),
                   "run_dir": os.path.basename(run_dir)}
            for c in CLASSES:
                sub = sr[sr["class"] == c]
                rec[f"attain_{c}"] = attain(sub)
                rec[f"n_{c}"] = len(sub)
            # fleet_attain weights each class by how many of its requests got
            # SERVED, and the arms do not serve the same mix: at 50 req/s
            # PolyServe's served set is 10% swe against load-balance's 30%,
            # because its protected classes stream through while swe starves.
            # That inflates fleet_attain for whichever arm completes more of the
            # easy classes. The workload is offered 1:1:1, so weight the classes
            # equally instead -- this is the mix-independent comparison.
            per_class = [rec[f"attain_{c}"] for c in CLASSES]
            rec["attain_equalmix"] = (float(np.nanmean(per_class))
                                      if not all(pd.isna(v) for v in per_class)
                                      else float("nan"))
            rec["served_per_s"] = len(sr) / 280.0
            kv = sorted(engine_series(run_dir, "kv_cache_usage_perc"))
            pre = engine_series(run_dir, "num_preemptions_total")
            rec["kv_p90"] = kv[int(len(kv) * .9)] if kv else float("nan")
            rec["preemptions"] = max(pre) if pre else 0
            spread = routing_spread(run_dir)
            for c in CLASSES:
                rec[f"concentration_{c}"] = (spread or {}).get(c, float("nan"))
            recs.append(rec)

    if not recs:
        sys.exit("no EXP-21 runs found; check --results-dir and the session names")

    df = pd.DataFrame(recs).sort_values(["arm", "rpm"])
    csv = os.path.join(a.out_dir, "exp21_polyserve.csv")
    df.to_csv(csv, index=False)
    print(f"\nwrote {csv}\n")

    arms = [k for k in ARM_STYLE if k in set(df["arm"])]
    piv = df.pivot(index="rpm", columns="arm")
    hdr = f"{'rpm':>6}{'req/s':>7} | " + "".join(f"{k[:11]+' eqmix':>19}" for k in arms)
    hdr += " | " + "".join(f"{k[:4]+' '+c[:4]:>11}" for c in CLASSES for k in arms)
    print("equal-mix attainment (classes weighted 1:1:1 as offered), then per class")
    print(hdr)
    for rpm in sorted(df["rpm"].unique()):
        def g(col, arm):
            try:
                v = piv[(col, arm)].loc[rpm]
                return v if pd.notna(v) else None
            except KeyError:
                return None
        def fmt(v, w):
            return f"{v:{w}.1f}" if v is not None else f"{'-':>{w}}"
        line = f"{rpm:>6}{rpm/60:>7.1f} | " + "".join(fmt(g("attain_equalmix", k), 19) for k in arms)
        line += " | " + "".join(fmt(g(f"attain_{c}", k), 11) for c in CLASSES for k in arms)
        print(line)

    print("\nrouting concentration (0 = spread evenly over engines, 1 = pinned to one)")
    for arm in arms:
        sub = df[df["arm"] == arm]
        vals = " ".join(f"{c}={sub[f'concentration_{c}'].mean():.2f}" for c in CLASSES)
        print(f"  {arm:>12}: {vals}")

    make_figures(df, a.out_dir, arms)


def _plot_arms(ax, df, ycol):
    for arm, st in ARM_STYLE.items():
        s = df[df["arm"] == arm].sort_values("rate_rps")
        if s.empty:
            continue
        ax.plot(s["rate_rps"], s[ycol], color=st["color"], ls=st["ls"],
                marker=st["marker"], label=st["label"],
                markeredgecolor="white", markeredgewidth=0.5)


def make_figures(df, out_dir, arms):
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(5.4, 3.6))
        _plot_arms(ax, df, "attain_equalmix")
        _axis(ax, "SLO attainment (%), classes weighted 1:1:1")
        ax.set_ylim(-3, 105)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=1)
        p = os.path.join(out_dir, "exp21_fleet_attainment.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig); print("wrote:", p)

        fig, axes = plt.subplots(1, 3, figsize=(9.6, 3.2), sharey=True)
        for ax, c in zip(axes, CLASSES):
            _plot_arms(ax, df, f"attain_{c}")
            _axis(ax, "SLO attainment (%)" if c == CLASSES[0] else "")
            ax.set_ylim(-3, 105)
            ax.set_title(c, pad=4)
        axes[0].legend(loc="lower center", bbox_to_anchor=(1.7, 1.10), ncol=2)
        p = os.path.join(out_dir, "exp21_per_class.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig); print("wrote:", p)

        # Mechanism: did the partition bind, and did the engines behave
        # differently? Separate axes, never twinned.
        fig, axes = plt.subplots(1, 3, figsize=(11.0, 3.2))
        _plot_arms(axes[0], df, "kv_p90")
        _axis(axes[0], "KV cache usage, p90 (fraction)")
        axes[0].set_ylim(0, 1.05)
        _plot_arms(axes[1], df, "preemptions")
        _axis(axes[1], "Preemptions (total)")
        axes[1].set_ylim(bottom=0)
        for c, mark in zip(CLASSES, ("o", "^", "s")):
            for arm, st in ARM_STYLE.items():
                s = df[df["arm"] == arm].sort_values("rate_rps")
                if s.empty or s[f"concentration_{c}"].isna().all():
                    continue
                axes[2].plot(s["rate_rps"], s[f"concentration_{c}"], color=st["color"],
                             ls=st["ls"], marker=mark, label=f"{arm[:4]} {c}",
                             markeredgecolor="white", markeredgewidth=0.5)
        _axis(axes[2], "Routing concentration (0=even, 1=pinned)")
        axes[2].set_ylim(-0.05, 1.05)
        axes[2].legend(loc="upper left", ncol=2, fontsize=6)
        axes[0].legend(loc="lower center", bbox_to_anchor=(0.5, 1.05), ncol=1)
        p = os.path.join(out_dir, "exp21_mechanism.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig); print("wrote:", p)


if __name__ == "__main__":
    main()
