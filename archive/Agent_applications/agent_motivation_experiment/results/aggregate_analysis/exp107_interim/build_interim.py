#!/usr/bin/env python3
"""EXP-107 interim (rep 1 only): fsv3cap vs the EXP-104 fsv3 control vs llm-d,
on the mix-shift hour trace. PRELIMINARY -- fsv3cap has ONE run; control and
llm-d have two each, from different sessions. Read only differences larger
than the control's repeat spread.

Outcome decomposition on the all-arrivals denominator (agent != job_summary,
so grace_cut arrivals stay in the denominator -- the trap-D rule): every
arrival is exactly one of met / tbt_viol / ttft_viol / e2e_viol / rejected /
error / cut. Scoring: chat ttft<=5s & mean per-token <=50ms, deepresearch
10s/100ms, swe e2e<=30s (every run here scored at 30 s).
"""
import glob
import os
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.abspath(os.path.join(HERE, "..", ".."))

ARMS = {
    "fsv3 (control)": ["*exp104r[12]_fsv3_shift"],
    "fsv3cap (n=1)": ["*exp107r1_fsv3cap_shift"],
    "llm-d": ["*exp93r1_llmdslo_shift", "*exp93br1_llmdslo_shift"],
}
COLOR = {"fsv3 (control)": "#1f77b4", "fsv3cap (n=1)": "#9467bd", "llm-d": "#8c564b"}
CLASSES = ["chat", "deepresearch", "swe"]
TRACE_S = 3600.0


def classify(t):
    t = str(t)
    return "chat" if t.startswith("sg-") else (
        "deepresearch" if t.startswith("sa-") else "swe")


def decompose(run):
    df = pd.read_csv(os.path.join(run, "metrics.csv"), low_memory=False)
    df = df[df.agent != "job_summary"].copy()
    df["cls"] = df.task_id.map(classify)
    rej = df.is_rejected.astype(bool)
    cut = (df.is_server_terminated.astype(bool) | df.is_timeout.astype(bool)
           | df.is_job_timeout.astype(bool)) & ~rej
    err = df.is_error.astype(bool) & ~rej & ~cut
    served = ~(rej | cut | err)
    out = {}
    for cls in CLASSES:
        m = df.cls == cls
        g = df[m & served]
        if cls == "swe":
            met = g.latency <= 30.0
            ttft_v = pd.Series(False, index=g.index)
            tbt_v = pd.Series(False, index=g.index)
            e2e_v = ~met
        else:
            ttft_b, tok_b = (5.0, 50.0) if cls == "chat" else (10.0, 100.0)
            per = (g.latency - g.first_token_latency) / \
                (g.output_tokens - 1).clip(lower=1) * 1e3
            ttft_v = g.first_token_latency > ttft_b
            tbt_v = ~ttft_v & (g.output_tokens >= 2) & (per > tok_b)
            met = ~ttft_v & ~tbt_v
            e2e_v = pd.Series(False, index=g.index)
        n = int(m.sum())
        good_tok = float(g.output_tokens[met].sum())
        all_tok = float(df.output_tokens[m].fillna(0).sum())
        out[cls] = dict(
            n=n,
            met=100.0 * met.sum() / n,
            tbt_viol=100.0 * tbt_v.sum() / n,
            ttft_viol=100.0 * ttft_v.sum() / n,
            e2e_viol=100.0 * e2e_v.sum() / n,
            rejected=100.0 * (rej & m).sum() / n,
            cut=100.0 * (cut & m).sum() / n,
            error=100.0 * (err & m).sum() / n,
            admitted_att=100.0 * met.sum() / max(1, len(g)),
            good_tok_s=good_tok / TRACE_S,
            all_tok_s=all_tok / TRACE_S,
        )
    return out


def agg(rows_per_run):
    """mean and min..max range over the runs of one arm, per class per key."""
    keys = rows_per_run[0]["chat"].keys()
    out = {}
    for cls in CLASSES:
        out[cls] = {k: (np.mean([r[cls][k] for r in rows_per_run]),
                        min(r[cls][k] for r in rows_per_run),
                        max(r[cls][k] for r in rows_per_run)) for k in keys}
    return out


def main():
    data = {}
    for arm, globs in ARMS.items():
        runs = []
        for g in globs:
            runs.extend(sorted(glob.glob(os.path.join(RESULTS, g))))
        assert runs, f"no runs for {arm}"
        print(f"{arm}: {[os.path.basename(r) for r in runs]}")
        data[arm] = agg([decompose(r) for r in runs])

    # ---- table ------------------------------------------------------------
    print(f"\n{'arm':16s} {'class':13s} {'met%':>6s} {'tbtV%':>6s} {'ttftV%':>7s} "
          f"{'e2eV%':>6s} {'rej%':>6s} {'cut%':>5s} {'admit%':>7s} "
          f"{'goodtok/s':>10s} {'alltok/s':>9s}")
    for arm, d in data.items():
        for cls in CLASSES:
            v = d[cls]
            print(f"{arm:16s} {cls:13s} {v['met'][0]:6.1f} {v['tbt_viol'][0]:6.1f} "
                  f"{v['ttft_viol'][0]:7.1f} {v['e2e_viol'][0]:6.1f} "
                  f"{v['rejected'][0]:6.1f} {v['cut'][0]:5.1f} "
                  f"{v['admitted_att'][0]:7.1f} {v['good_tok_s'][0]:10.0f} "
                  f"{v['all_tok_s'][0]:9.0f}")

    # ---- figure -----------------------------------------------------------
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    arms = list(data.keys())
    x = np.arange(len(CLASSES))
    w = 0.26

    ax = axes[0][0]
    for i, arm in enumerate(arms):
        means = [data[arm][c]["met"][0] for c in CLASSES]
        lo = [data[arm][c]["met"][0] - data[arm][c]["met"][1] for c in CLASSES]
        hi = [data[arm][c]["met"][2] - data[arm][c]["met"][0] for c in CLASSES]
        ax.bar(x + (i - 1) * w, means, w, yerr=[lo, hi], capsize=3,
               color=COLOR[arm], label=arm)
    ax.set_xticks(x, CLASSES)
    ax.set_ylabel("offered SLO attainment (%), per request")
    ax.set_title("(a) offered attainment by class (bars n=2 except fsv3cap n=1)")
    ax.legend(fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.6)

    ax = axes[0][1]
    cats = ["met", "tbt_viol", "ttft_viol", "rejected", "cut", "error"]
    cat_c = {"met": "#2ca02c", "tbt_viol": "#d62728", "ttft_viol": "#ff7f0e",
             "rejected": "#7f7f7f", "cut": "#c7c7c7", "error": "#000000"}
    for i, arm in enumerate(arms):
        bottom = 0.0
        for cat in cats:
            v = data[arm]["chat"][cat][0]
            ax.bar(i, v, 0.6, bottom=bottom, color=cat_c[cat],
                   label=cat if i == 0 else None)
            bottom += v
    ax.set_xticks(range(len(arms)), [a.split(" (")[0] for a in arms])
    ax.set_ylabel("share of chat arrivals (%)")
    ax.set_title("(b) what happened to every chat arrival")
    ax.legend(fontsize=7, ncol=2)

    ax = axes[1][0]
    for i, arm in enumerate(arms):
        # totals weighted by class arrival counts
        ns = [data[arm][c]["n"][0] for c in CLASSES]
        off = sum(data[arm][c]["met"][0] * n for c, n in zip(CLASSES, ns)) / sum(ns)
        adm = sum(data[arm][c]["admitted_att"][0] * n for c, n in zip(CLASSES, ns)) / sum(ns)
        ax.bar(i - 0.17, off, 0.32, color=COLOR[arm], label="offered" if i == 0 else None)
        ax.bar(i + 0.17, adm, 0.32, color=COLOR[arm], alpha=0.45,
               label="admitted" if i == 0 else None)
        ax.text(i - 0.17, off + 1, f"{off:.1f}", ha="center", fontsize=8)
        ax.text(i + 0.17, adm + 1, f"{adm:.1f}", ha="center", fontsize=8)
    ax.set_xticks(range(len(arms)), [a.split(" (")[0] for a in arms])
    ax.set_ylabel("SLO attainment (%), per request")
    ax.set_ylim(0, 108)
    ax.set_title("(c) offered vs admitted, whole hour")
    ax.legend(fontsize=8)
    ax.grid(axis="y", ls=":", alpha=0.6)

    ax = axes[1][1]
    for i, arm in enumerate(arms):
        bottom = 0.0
        for cls, cc in zip(CLASSES, ["#1f77b4", "#ff7f0e", "#2ca02c"]):
            v = data[arm][cls]["good_tok_s"][0]
            ax.bar(i, v, 0.45, bottom=bottom, color=cc,
                   label=cls if i == 0 else None)
            bottom += v
        tot = sum(data[arm][c]["all_tok_s"][0] for c in CLASSES)
        ax.plot([i - 0.3, i + 0.3], [tot, tot], color="k", lw=1.2)
        ax.text(i, tot + 150, f"total {tot:,.0f}", ha="center", fontsize=7)
    ax.set_xticks(range(len(arms)), [a.split(" (")[0] for a in arms])
    ax.set_ylabel("output tokens/s")
    ax.set_title("(d) goodput tokens (bars, SLO-met) vs total output (line)")
    ax.legend(fontsize=8)

    fig.suptitle(textwrap.fill(
        "EXP-107 interim, mix-shift hour trace: fsv3cap rep 1 only (n=1) vs "
        "EXP-104 fsv3 control (n=2) vs EXP-93 llm-d (n=2, different session). "
        "swe scored e2e<=30s everywhere.", 110), fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out = os.path.join(HERE, "exp107_interim.png")
    fig.savefig(out, dpi=150)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
