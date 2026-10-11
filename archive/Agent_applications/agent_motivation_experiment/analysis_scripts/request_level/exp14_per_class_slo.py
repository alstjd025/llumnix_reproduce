#!/usr/bin/env python3
"""EXP-14 attainment under CLASS-DIFFERENTIATED SLOs.

The standard figures judge every request by one global rule (TTFT<=5s &
meanTBT<=50ms). Real multi-tenant serving gives each application its own SLO.
This script re-scores the SAME EXP-14 runs with per-class rules and redraws the
attainment figures into a SEPARATE output folder (no re-experiment):

    chat          : mean TTFT <= 5s  AND  mean TBT <= 50ms   (interactive)
    deepresearch  : mean TTFT <= 10s AND  mean TBT <= 100ms  (batch-ish, looser)
    swe           : E2E latency <= 20s                        (background agent;
                    whole-call latency is what matters, ~2x the ~10-15s healthy
                    pre-knee E2E)

Row selection + analysis window are inherited verbatim from
plot_per_engine_attainment.served_rows (arrival-anchored [60s, min(last, 360)-20],
errors/timeouts/run-end-cut excluded, admission rejects excluded) — ONLY the
per-request `violate` flag is recomputed per class. gateway-timeout
reclassification is not applicable here (no-timeout gateway).

Produces, in --out-dir:
  exp14_fleet_attainment_slo.png       fleet attainment vs rate, one line/mix
  exp14_per_class_slo_mix{A,B,C}.png   per-class attainment within each mix
  per_engine_attainment_slo_mix{A,B,C}.png  per-engine (needs request_engine.csv)
  exp14_summary_slo.csv                per (mix,rate) attainment + n
"""
import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_per_engine_attainment import served_rows, class_of  # noqa: E402

def _itl_ms(rows):
    """Mean inter-token latency, derived rather than read from tbt_mean_ms.

    The recorded column is half the true value on every run collected before
    2026-07-30: the client divided each inter-chunk gap by a per-chunk token
    estimate that tokenises the chunk out of context and comes to 1.92x the
    true count. Deriving it from columns that are timestamp differences avoids
    the defect and needs no re-measurement. See fluidserve-implementation.md 32.
    """
    out = pd.to_numeric(rows.get("output_tokens"), errors="coerce")
    ttft = pd.to_numeric(rows["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(rows["latency"], errors="coerce")
    return (e2e - ttft) * 1000.0 / (out - 1.0).where(out > 1.0)


# Per-class SLO rules. TTFT/TBT in (s, ms); e2e in s. A class uses e2e XOR
# (ttft & tbt).
SLO_RULES = {
    "chat":         {"ttft": 5.0,  "tbt": 50.0},
    "deepresearch": {"ttft": 10.0, "tbt": 100.0},
    "swe":          {"e2e": float(os.environ.get("SWE_E2E_SLO_S", "20.0"))},
}

PAPER_STYLE = {
    "font.family": "serif",
    "font.serif": ["DejaVu Serif", "Times New Roman", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.labelsize": 8, "ytick.labelsize": 8,
    "xtick.direction": "in", "ytick.direction": "in",
    "lines.linewidth": 1.6, "lines.markersize": 5,
}
MIXES = {"mixA": ("A  1:1:1", "#1f77b4"),
         "mixB": ("B  6:3:1 (light)", "#2ca02c"),
         "mixC": ("C  1:1:3 (heavy)", "#d62728")}
CLASS_COLORS = {"chat": "#1f77b4", "deepresearch": "#ff7f0e", "swe": "#d62728"}
ENGINE_COLORS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd"]


def per_class_violate(rows):
    """Recompute `violate` per class rule. rows must have class + raw metrics."""
    ttft = pd.to_numeric(rows["first_token_latency"], errors="coerce")
    tbt = _itl_ms(rows)
    e2e = pd.to_numeric(rows["latency"], errors="coerce")
    v = pd.Series(False, index=rows.index)
    for cname, rule in SLO_RULES.items():
        m = rows["class"] == cname
        if "e2e" in rule:
            v.loc[m] = e2e[m] > rule["e2e"]
        else:
            v.loc[m] = (ttft[m] > rule["ttft"]) | (tbt[m] > rule["tbt"])
    return v


def attain(sub):
    return 100.0 * (~sub["violate_pc"]).mean() if len(sub) else np.nan


def out_tps(sr, window_s=280.0):
    """Steady-window output token throughput (tok/s) over served rows."""
    return pd.to_numeric(sr["output_tokens"], errors="coerce").sum() / window_s


def load_condition(run_dir, with_engine=False):
    sr = served_rows(run_dir)
    if sr is None or sr.empty:
        return None
    sr["class"] = sr["task_id"].map(class_of)
    sr["violate_pc"] = per_class_violate(sr)
    if with_engine:
        p = os.path.join(run_dir, "analysis", "request_engine.csv")
        if os.path.isfile(p):
            m = pd.read_csv(p)[["task_id", "engine_port"]].drop_duplicates("task_id")
            sr = sr.merge(m, on="task_id", how="left")
    return sr


def collect(results_dir, tag, with_engine=False):
    out = []
    G = (f"{results_dir}/*exp14_mixB_rpm_[0-9][0-9][0-9]*" if tag == "mixB"
         else f"{results_dir}/*exp14_{tag}_rpm_*")
    for d in sorted(glob.glob(G),
                    key=lambda x: int(re.search(r"rpm_(\d+)", x).group(1))):
        rate = int(re.search(r"rpm_(\d+)", d).group(1)) / 60
        sr = load_condition(d, with_engine)
        if sr is None:
            continue
        out.append((rate, sr))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results-dir", default="results")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    rulestr = ("SLO: chat TTFT<=5s&TBT<=50ms | deepresearch TTFT<=10s&TBT<=100ms"
               f" | swe E2E<={SLO_RULES['swe']['e2e']:.0f}s")
    print(rulestr)
    summary = []
    fleet_by_mix = {}
    for tag, (label, color) in MIXES.items():
        data = collect(a.results_dir, tag, with_engine=True)
        if not data:
            continue
        fleet_by_mix[tag] = [(r, attain(sr)) for r, sr in data]
        # summary + per-class figure
        print(f"\n=== {label} ===")
        print(f"{'req/s':>6} | {'chat':>6} {'deepr':>6} {'swe':>6} | {'fleet':>6}")
        rows_pc = {"chat": [], "deepresearch": [], "swe": [], "fleet": [],
                   "rate": [], "tps": []}
        for rate, sr in data:
            rec = {"mix": tag, "rate": rate, "n": len(sr), "fleet": attain(sr),
                   "out_tok_per_s": out_tps(sr)}
            rows_pc["rate"].append(rate); rows_pc["fleet"].append(attain(sr))
            rows_pc["tps"].append(out_tps(sr))
            cells = []
            for c in ("chat", "deepresearch", "swe"):
                v = attain(sr[sr["class"] == c])
                rec[f"attain_{c}"] = v
                rows_pc[c].append(v)
                cells.append(f"{v:6.1f}")
            summary.append(rec)
            print(f"{rate:6.1f} | {cells[0]} {cells[1]} {cells[2]} | {attain(sr):6.1f}")

        with plt.rc_context(PAPER_STYLE):
            fig, ax = plt.subplots(figsize=(5.2, 3.5))
            for c in ("chat", "deepresearch", "swe"):
                ax.plot(rows_pc["rate"], rows_pc[c], "-o", color=CLASS_COLORS[c],
                        label=c, mec="white", mew=0.5)
            ax.plot(rows_pc["rate"], rows_pc["fleet"], "--", color="0.4", label="fleet")
            ax.set_xlabel("Offered rate (req/s)")
            ax.set_ylabel("SLO attainment (%)"); ax.set_ylim(-3, 105)
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=4)
            ax.set_title(f"Per-class differentiated SLO — mix {label}", pad=26)
            fig.tight_layout()
            p = os.path.join(a.out_dir, f"exp14_per_class_slo_{tag}.png")
            fig.savefig(p, dpi=300); print("wrote:", p)

        # slo_vs_throughput style: per-class + fleet attainment (left) +
        # output token throughput (right), all under the differentiated SLO.
        swe_slo = SLO_RULES["swe"]["e2e"]
        with plt.rc_context(PAPER_STYLE):
            fig, ax = plt.subplots(figsize=(5.6, 3.6))
            for c in ("chat", "deepresearch", "swe"):
                ax.plot(rows_pc["rate"], rows_pc[c], "-o", color=CLASS_COLORS[c],
                        label=c, mec="white", mew=0.5)
            ax.plot(rows_pc["rate"], rows_pc["fleet"], "--", color="0.35",
                    label="fleet", lw=1.4)
            ax.set_xlabel("Offered rate (req/s)")
            ax.set_ylabel("SLO attainment (%)", color="0.2"); ax.set_ylim(-3, 105)
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            ax2 = ax.twinx()
            ax2.plot(rows_pc["rate"], rows_pc["tps"], "-^", color="#8c564b",
                     label="output tok/s", mec="white", mew=0.5, alpha=0.85)
            ax2.set_ylabel("Output tokens/s", color="#8c564b")
            ax2.tick_params(axis="y", colors="#8c564b")
            ax2.set_ylim(bottom=0)
            h1, l1 = ax.get_legend_handles_labels()
            h2, l2 = ax2.get_legend_handles_labels()
            ax.legend(h1 + h2, l1 + l2, loc="lower center",
                      bbox_to_anchor=(0.5, 1.02), ncol=5, columnspacing=1.0)
            ax.set_title(f"Mix {label} — per-class SLO (chat 5s/50ms, dr "
                         f"10s/100ms, swe E2E {swe_slo:.0f}s) + throughput",
                         pad=26, fontsize=8)
            fig.tight_layout()
            p = os.path.join(a.out_dir, f"slo_vs_throughput_slo_{tag}.png")
            fig.savefig(p, dpi=300); print("wrote:", p)

        # per-engine figure (fleet attainment per engine, per-class SLO applied)
        if any("engine_port" in sr.columns for _, sr in data):
            ports = sorted({int(p) for _, sr in data if "engine_port" in sr.columns
                            for p in sr["engine_port"].dropna().unique()})
            with plt.rc_context(PAPER_STYLE):
                fig, ax = plt.subplots(figsize=(5.2, 3.5))
                for port, col in zip(ports, ENGINE_COLORS):
                    ys = [attain(sr[sr.get("engine_port") == port])
                          if "engine_port" in sr.columns else np.nan
                          for _, sr in data]
                    ax.plot([r for r, _ in data], ys, "-o", color=col,
                            label=f"engine {port}", mec="white", mew=0.5)
                ax.set_xlabel("Offered rate (req/s)")
                ax.set_ylabel("SLO attainment (%)"); ax.set_ylim(-3, 105)
                ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
                ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=4)
                ax.set_title(f"Per-engine attainment (per-class SLO) — mix {label}", pad=26)
                fig.tight_layout()
                p = os.path.join(a.out_dir, f"per_engine_attainment_slo_{tag}.png")
                fig.savefig(p, dpi=300); print("wrote:", p)

    pd.DataFrame(summary).to_csv(os.path.join(a.out_dir, "exp14_summary_slo.csv"),
                                 index=False)

    # cross-ratio fleet attainment
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(5.4, 3.6))
        for tag, (label, color) in MIXES.items():
            pts = fleet_by_mix.get(tag)
            if not pts:
                continue
            ax.plot([r for r, _ in pts], [v for _, v in pts], "-o", color=color,
                    label=label, mec="white", mew=0.5)
        ax.set_xlabel("Offered rate (req/s)")
        ax.set_ylabel("Fleet SLO attainment (%)"); ax.set_ylim(-3, 105)
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=3)
        ax.set_title("Mix knee under class-differentiated SLOs", pad=26)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp14_fleet_attainment_slo.png")
        fig.savefig(p, dpi=300); print("\nwrote:", p)


if __name__ == "__main__":
    main()
