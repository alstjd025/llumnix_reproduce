#!/usr/bin/env python3
"""EXP-107: one figure, three questions per class and per arm, whole hour.

  (a) offered SLO attainment   -- every arrival counts once; a rejection and a
                                  request that ran and missed score the same
  (b) admitted SLO attainment  -- did the system keep what it accepted
  (c) SLO-met output tokens    -- the token goodput each class actually got,
                                  in millions over the hour, with the class's
                                  TOTAL output (met or not) as a thin marker

Arms are selected exactly as redraw_hour_trace_exp107.sh selects them,
including the completion guard (a directory whose metrics.csv has not been
merged yet is skipped and named), so rerunning after the chain adds the
missing repeats and arms. Control and llm-d are from other sessions; the
error bars are min..max over the repeats that exist, a floor and not a
variance, and single-run arms carry no bar.

Scoring: chat ttft<=5s and mean per-token <=50 ms; deepresearch 10 s /
100 ms; swe end-to-end <=30 s. Every run here is scored at 30 s.
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

ARM_GLOBS = [
    ("fsv3 control", "#1f77b4", ["*exp104r[12]_fsv3_shift"]),
    ("+instance cap", "#9467bd", ["*exp107r[12]_fsv3cap_shift"]),
    ("force off", "#2ca02c", ["*exp107r[12]_fsv3nofrc_shift"]),
    ("cap + force off", "#d62728", ["*exp107r[12]_fsv3capnofrc_shift"]),
    ("guardrail cap", "#7b3294", ["*exp107gr[12]_fsv3capg_shift"]),
    ("guardrail + force off", "#e6550d", ["*exp107hr[12]_fsv3capgnofrc_shift"]),
    ("llm-d", "#8c564b", ["*exp93r1_llmdslo_shift", "*exp93br1_llmdslo_shift"]),
]
CLASSES = ["chat", "deepresearch", "swe"]
CLS_LABEL = {"chat": "chat", "deepresearch": "deep\nresearch", "swe": "swe"}


def classify(t):
    t = str(t)
    return "chat" if t.startswith("sg-") else (
        "deepresearch" if t.startswith("sa-") else "swe")


def complete(run):
    p = os.path.join(run, "metrics.csv")
    return os.path.isfile(p) and os.path.getsize(p) > 100_000


def score(run):
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
        else:
            ttft_b, tok_b = (5.0, 50.0) if cls == "chat" else (10.0, 100.0)
            per = (g.latency - g.first_token_latency) / \
                (g.output_tokens - 1).clip(lower=1) * 1e3
            met = (g.first_token_latency <= ttft_b) & \
                ((g.output_tokens < 2) | (per <= tok_b))
        out[cls] = dict(
            offered=100.0 * met.sum() / max(1, int(m.sum())),
            admitted=100.0 * met.sum() / max(1, len(g)),
            good_mtok=float(g.output_tokens[met].sum()) / 1e6,
            all_mtok=float(df.output_tokens[m].fillna(0).sum()) / 1e6,
        )
    return out


def main():
    arms = []
    for name, color, globs in ARM_GLOBS:
        runs = []
        for g in globs:
            runs.extend(sorted(glob.glob(os.path.join(RESULTS, g))))
        kept = [r for r in runs if complete(r)]
        for r in runs:
            if r not in kept:
                print(f"  NOTE: {os.path.basename(r)} incomplete -- skipped")
        if not kept:
            print(f"  {name}: no complete runs yet -- omitted")
            continue
        print(f"{name}: {[os.path.basename(r) for r in kept]}")
        arms.append((name, color, [score(r) for r in kept]))

    def stat(scores, cls, key):
        vals = [s[cls][key] for s in scores]
        return np.mean(vals), np.mean(vals) - min(vals), max(vals) - np.mean(vals)

    fig, axes = plt.subplots(1, 3, figsize=(13, 4.4))
    x = np.arange(len(CLASSES))
    n = len(arms)
    w = 0.8 / n

    for ax, key, title, ylab in [
        (axes[0], "offered", "(a) offered SLO attainment",
         "attainment (%), all arrivals in the denominator"),
        (axes[1], "admitted", "(b) admitted SLO attainment",
         "attainment (%), accepted requests only"),
        (axes[2], "good_mtok", "(c) token goodput per class",
         "SLO-met output tokens (millions / hour)"),
    ]:
        for i, (name, color, scores) in enumerate(arms):
            pos = x + (i - (n - 1) / 2) * w
            means, lo, hi = zip(*[stat(scores, c, key) for c in CLASSES])
            label = f"{name} (n={len(scores)})"
            ax.bar(pos, means, w * 0.92, yerr=[lo, hi], capsize=2.5,
                   color=color, label=label)
            if key == "good_mtok":
                tot = [stat(scores, c, "all_mtok")[0] for c in CLASSES]
                ax.plot(pos, tot, ls="none", marker="_", ms=11, mew=1.6,
                        color="black")
        ax.set_xticks(x, [CLS_LABEL[c] for c in CLASSES])
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(ylab, fontsize=9)
        ax.grid(axis="y", ls=":", alpha=0.6)
        if key != "good_mtok":
            ax.set_ylim(0, 105)
    axes[0].legend(fontsize=7.5, loc="lower left")
    axes[2].plot([], [], ls="none", marker="_", ms=11, mew=1.6, color="black",
                 label="total output (met or not)")
    axes[2].legend(fontsize=7.5)

    fig.suptitle(textwrap.fill(
        "EXP-107 mix-shift hour: per class, what share of arrivals was served "
        "within its rule (a), what share of ACCEPTED requests was (b), and how "
        "many SLO-met output tokens the class received (c). Error bars are "
        "min..max over repeats; arms without bars have one run. Control and "
        "llm-d are from other sessions.", 130), fontsize=9)
    fig.tight_layout(rect=[0, 0, 1, 0.90])
    out = os.path.join(HERE, "class_summary_bars.png")
    fig.savefig(out, dpi=150)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
