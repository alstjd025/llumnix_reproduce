#!/usr/bin/env python3
"""Per-class goodput tokens, which is a different question from per-class attainment.

Attainment asks what fraction of a class met its rule; it says nothing about how
much work that class actually got. At overload every policy sacrifices something
-- that is conservation, not a design flaw -- so the question worth asking is
WHICH class is given up and how much of the total survives. Measured in tokens,
a policy that looks even-handed on attainment can turn out to be even-handed
only because it destroyed the large classes.
"""
import glob, os, re, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, CLASSES, load_run

# EXP-71. fspfx is the deployed default since v0.2 (prefix-aware prefill
# charging); the arm named `fluidserve` is its prefix-off ablation. llm-d is
# brown because orange is the deep-research class colour. An arm missing from
# this table is dropped from the figure without a message.
ARMS = {"fluidserve": ("FluidServe", "#1f77b4"), "polyserve": ("PolyServe", "#d62728"),
        "slo": ("Llumnix SLO", "#2ca02c"), "loadbalance": ("Llumnix", "#9467bd"),
        "llmdslo": ("llm-d", "#8c564b"),
        "fspfx": ("FluidServe v0.2", "#17becf")}
R = re.compile(r"_(fluidserve|polyserve|slo|loadbalance|llmdslo)_m1f?_rpm_(\d+)$")


# The default is EXP-53's own sessions. It is a DEFAULT and not a fact about
# the sweep: when an arm is re-measured in a later session, globbing the old one
# averages the superseded runs into the new ones without saying so. PolyServe was
# re-measured as EXP-57 on 2026-08-05, so the caller passes per-arm patterns.
DEFAULT_RUNS = ("results/*exp53r*", "results/*exp53p2*")


def sweep(out, runs=DEFAULT_RUNS):
    rows = []
    for pat in runs:
        for d in sorted(glob.glob(pat)):
            m = R.search(os.path.basename(d))
            if not m:
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            span = r["rel"].max() - r["rel"].min()
            ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
            rec = dict(arm=m.group(1), rate=int(m.group(2)) / 60)
            for c in CLASSES:
                rec[c] = pd.to_numeric(ok[ok["class"] == c].get("output_tokens"),
                                       errors="coerce").fillna(0).sum() / span
            rows.append(rec)
    df = pd.DataFrame(rows).groupby(["arm", "rate"]).mean().reset_index()
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(7.4, 2.5), sharex=True)
        for a_, c in zip(ax, CLASSES):
            for k, (lab, col) in ARMS.items():
                g = df[df.arm == k].sort_values("rate")
                a_.plot(g.rate, g[c], color=col, marker="o", ms=3, label=lab)
            a_.set_title(c, fontsize=8)
            a_.set_xlabel("request rate (req/s)")
            a_.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax[0].set_ylabel("goodput (output tokens/s)")
        ax[1].legend(loc="lower center", bbox_to_anchor=(0.5, 1.14), ncol=4,
                     fontsize=7, columnspacing=1.2)
        fig.savefig(os.path.join(out, "class_goodput_sweep.png"), dpi=300,
                    bbox_inches="tight")
    return df


def hour(out, series, name="class_goodput_hour.png"):
    WIN, STEP = 90.0, 30.0
    totals = {}
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(7.4, 2.5), sharex=True, sharey=True)
        for lab, col, path in series:
            r = load_run(path)
            ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
            dur = r["rel"].max()
            xs, ys = [], {c: [] for c in CLASSES}
            t = WIN / 2
            while t + WIN / 2 <= dur:
                w = ok[(ok["rel"] >= t - WIN / 2) & (ok["rel"] < t + WIN / 2)]
                xs.append(t / 60)
                for c in CLASSES:
                    ys[c].append(pd.to_numeric(w[w["class"] == c].get("output_tokens"),
                                 errors="coerce").fillna(0).sum() / WIN)
                t += STEP
            for a_, c in zip(ax, CLASSES):
                a_.plot(xs, ys[c], color=col, lw=1.1, label=lab)
            # The time series answers "when", the totals answer "how much", and
            # the second question is the one the fairness argument turns on: a
            # policy can hold a class steady all hour at a level that adds up to
            # nothing. Both are printed so neither is read alone.
            totals[lab] = {c: pd.to_numeric(ok[ok["class"] == c].get("output_tokens"),
                           errors="coerce").fillna(0).sum() / dur for c in CLASSES}
        for a_, c in zip(ax, CLASSES):
            a_.set_title(c, fontsize=8)
            a_.set_xlabel("time (minutes)")
            a_.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax[0].set_ylabel("goodput (output tokens/s)")
        ax[1].legend(loc="lower center", bbox_to_anchor=(0.5, 1.14), ncol=3,
                     fontsize=7, columnspacing=1.2)
        fig.savefig(os.path.join(out, name), dpi=300, bbox_inches="tight")
    print(f"\nhour-trace goodput, output tokens/s, whole run ({name})")
    print(f"{'arm':<16}" + "".join(f"{c:>16}" for c in CLASSES) + f"{'total':>10}")
    for lab, v in totals.items():
        print(f"{lab:<16}" + "".join(f"{v[c]:>16,.0f}" for c in CLASSES)
              + f"{sum(v.values()):>10,.0f}")
    return totals


EXP53_HOUR = [("FluidServe", "#1f77b4", "results/260802_0754_exp52p4r1_fluidserve_full"),
              ("+ candidate C", "#d62728", "results/260802_1008_exp52p4r1_fsc_full"),
              ("Llumnix SLO", "#2ca02c", "results/260731_2203_exp45r1_slo_full")]

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/aggregate_analysis/exp53")
    ap.add_argument("--sweep", action="store_true",
                    help="also draw the rate-sweep panel")
    ap.add_argument("--runs", nargs="+", default=list(DEFAULT_RUNS),
                    help="run globs for the sweep panel. Pass per-arm patterns "
                         "when an arm has been re-measured, or the superseded "
                         "runs are averaged in silently")
    ap.add_argument("--hour", nargs="*", default=None,
                    help="label|colour|run-dir, repeatable; defaults to EXP-52's three")
    ap.add_argument("--hour-name", default="class_goodput_hour.png")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    if a.sweep or a.hour is None:
        sweep(a.out, tuple(a.runs))
    series = EXP53_HOUR if not a.hour else [tuple(s.split("|", 2)) for s in a.hour]
    hour(a.out, series, a.hour_name)
    print("wrote to", a.out)
