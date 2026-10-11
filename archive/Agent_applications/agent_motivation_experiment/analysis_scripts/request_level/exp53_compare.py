#!/usr/bin/env python3
"""EXP-53: four control planes on one static rate sweep.

  FluidServe   our policy, gate slack 1.0, the configuration tagged v0.1.1
  PolyServe    static per-class partition, ported
  Llumnix SLO  the shipped SLO-aware policy, MIGRATION ON
  Llumnix      the shipped load-balancing policy, no SLO input, MIGRATION ON

**The two Llumnix arms run with their own migration mechanism enabled and the
two policies under test do not, because neither uses it.** That is stated on
every figure rather than left to a reader to discover: it is an asymmetry in the
baselines' favour, and a comparison that hides it is not worth making.

The Llumnix SLO arm also takes the `m1f` workload config. Its --ttft-slo and
--tpot-slo are single global values with no class dimension, and the default
decomposition hands it the agent class's 25 ms as a literal per-token target, at
which it rejected 98% of that class (EXP-28). `m1f` restates that class inside
the same 30 s end-to-end budget. Scoring is unaffected -- the analysis judges
that class end to end whatever the config says.

Outputs the standard set: attainment on both denominators, token goodput, and
per-class attainment, plus the table the figures are drawn from.

  python3 exp53_compare.py --runs 'results/*exp53r*' --out results/aggregate_analysis/exp53
"""
import argparse
import glob
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    PAPER_STYLE, CLASSES, CLASS_COLORS, load_run, attain,
)

# Colours are the fixed policy ones so they mean the same thing across figures.
ARMS = {
    # The third field is unused: every arm is drawn solid. See band().
    "fluidserve":  ("FluidServe",  "#1f77b4", "-"),
    "polyserve":   ("PolyServe",   "#d62728", "-"),
    "slo":         ("Llumnix SLO", "#2ca02c", "-"),
    "loadbalance": ("Llumnix",     "#9467bd", "-"),
    # EXP-66. Brown rather than the orange next in tab10, because orange is
    # bound to the deep-research class in the per-class figures and the two
    # kinds of figure sit next to each other in the same directory.
    "llmdslo":     ("llm-d",       "#8c564b", "-"),
}
# `llmdslo` runs carry the m1f config for the same reason the Llumnix SLO arm
# does: neither policy can express an end-to-end budget, so the agent class is
# restated as 2,500 ms + 52 ms/token inside the same 30 s. Scoring is unaffected
# -- load_run judges that class end to end whatever the config said.
ARM_RE = re.compile(r"_(fluidserve|polyserve|slo|loadbalance|llmdslo)_m1f?_rpm_(\d+)$")


def rescheduling_pairs(run):
    """(pairs the scheduler decided on, pairs whose migration call failed).

    Migration being enabled is not the same as migration happening. The smoke
    condition had it on, ran the loop 19 times, and generated zero pairs every
    time, so a sweep could finish with the feature nominally on and never
    engaged. That has to appear in the table rather than be assumed either way.

    The failure count is separate because deciding on a pair and moving a
    request are two different events, and until 2026-08-07 only the first was
    counted. When the engine is started without migration the scheduler still
    decides on pairs and every call comes back
    `ResourceExhausted: No enough migrate out slots for requests`; the scheduler
    then logs `Finish migrating from X to Y` on the next line whether or not
    anything moved, so neither the decision count nor that line is evidence that
    a KV cache was transferred. The EXP-66 llm-d conditions made this visible:
    they read 125 decided pairs while their engines had migration off, and all
    125 calls failed. Checked against the arms already measured, PolyServe's 77
    are all failures for the same reason and the Llumnix load-balance arm's 23
    are real.
    """
    p = os.path.join(run, "server_metrics", "migration_events.log")
    if not os.path.exists(p):
        return None
    n = failed = 0
    for line in open(p, errors="ignore"):
        m = re.search(r"Generate rescheduling pairs, count: (\d+)", line)
        if m and int(m.group(1)) > 0:
            n += int(m.group(1))
        elif "failed to migrate instance" in line:
            failed += 1
    return n, failed


def collect(patterns):
    rows = []
    for pat in patterns:
        for d in sorted(glob.glob(pat)):
            m = ARM_RE.search(os.path.basename(d))
            if not m:
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            span = r["rel"].max() - r["rel"].min()
            ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
            adm = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])]
            tok = pd.to_numeric(ok.get("output_tokens"), errors="coerce").fillna(0).sum()
            rec = dict(arm=m.group(1), rate=int(m.group(2)) / 60.0,
                       off=attain(r, "violate_offered"),
                       adm=attain(r, "violate_served"),
                       rej=100.0 * r["rejected"].mean(),
                       goodput=tok / span,
                       total=pd.to_numeric(adm.get("output_tokens"),
                                           errors="coerce").fillna(0).sum() / span,
                       run=os.path.basename(d))
            mig = rescheduling_pairs(d)
            rec["pairs"] = None if mig is None else mig[0]
            rec["pairs_failed"] = None if mig is None else mig[1]
            for c in CLASSES:
                rec[c] = attain(r[r["class"] == c], "violate_offered")
                rec[c + "_adm"] = attain(r[r["class"] == c], "violate_served")
            # Equal weight across the three classes rather than per request.
            # It is NOT the headline: it gives a class that is 7.7% of arrivals
            # the same say as one that is 76.9%, so a policy that keeps two small
            # classes at 100 while collapsing the large one scores well on it.
            # Reported because the two aggregations disagree along the axis this
            # project is choosing on, and hiding the one that disagrees is the
            # failure the plotting rules exist to prevent.
            rec["eq_adm"] = float(np.mean([rec[c + "_adm"] for c in CLASSES]))
            rec["eq_off"] = float(np.mean([rec[c] for c in CLASSES]))
            rows.append(rec)
    return pd.DataFrame(rows)


def band(ax, df, arm, col, dotted=False):
    """One arm, one quantity.

    Colour carries the ARM and nothing else, so every arm is drawn solid and the
    only dotted line on a figure is the offered denominator laid over the
    admitted one. Giving each arm its own dash pattern as well made the
    two-denominator figure unreadable: with four dash patterns and a fifth for
    the overlay, a reader cannot tell which dotted line belongs to which solid
    one, which is the single comparison that figure exists to show.
    """
    lab, c, _ = ARMS[arm]
    ls = "-"
    s = df[df.arm == arm].groupby("rate")[col]
    if not len(s):
        return
    x = np.array(sorted(s.groups))
    mean = np.array([s.get_group(v).mean() for v in x])
    lo = np.array([s.get_group(v).min() for v in x])
    hi = np.array([s.get_group(v).max() for v in x])
    ax.errorbar(x, mean, yerr=[mean - lo, hi - mean], color=c,
                linestyle=":" if dotted else ls, marker="o", markersize=3,
                markerfacecolor="none" if dotted else c, capsize=2,
                elinewidth=0.8, label=None if dotted else lab)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", default=["results/*exp53r*"])
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    df = collect(a.runs)
    if df.empty:
        sys.exit("no EXP-53 conditions matched")
    os.makedirs(a.out, exist_ok=True)

    print(f"{len(df)} conditions.  offered = every arrival in the denominator, "
          f"a rejection is a violation.  admitted = rejections leave it.\n")
    print(f"{'rate':>5} {'arm':>12} {'off':>6} {'adm':>6} {'rej%':>6} {'goodput':>8} "
          f"{'total':>8} {'chat':>6} {'dr':>6} {'swe':>6} {'mig':>9}")
    for _, r in df.sort_values(["rate", "arm"]).iterrows():
        # decided/failed, so a column of "7/7" cannot be read as seven moves.
        pairs = ("-" if r["pairs"] is None
                 else f"{int(r['pairs'])}/{int(r['pairs_failed'])}")
        print(f"{r['rate']:>5.0f} {r['arm']:>12} {r['off']:>6.1f} {r['adm']:>6.1f} "
              f"{r['rej']:>6.1f} {r['goodput']:>8.0f} {r['total']:>8.0f} "
              f"{r['chat']:>6.1f} {r['deepresearch']:>6.1f} {r['swe']:>6.1f} {pairs:>9}")

    print("\nequal weight across classes -- reported beside the per-request "
          "table above, not instead of it")
    print(f"{'rate':>5} " + "".join(f"{ARMS[a][0]:>16}" for a in ARMS))
    for rt in sorted(df.rate.unique()):
        row = f"{rt:>5.0f} "
        for arm in ARMS:
            g = df[(df.arm == arm) & (df.rate == rt)]
            row += ("     -          " if g.empty else
                    f"{g['eq_adm'].mean():>7.1f}/{g['eq_off'].mean():<8.1f}")
        print(row)
    print("      (admitted / offered)")

    tot = df.groupby("arm")["pairs"].sum(min_count=1)
    bad = df.groupby("arm")["pairs_failed"].sum(min_count=1)
    print("\nmigration, summed over each arm's conditions: pairs the scheduler "
          "decided on, of which failed, leaving how many requests actually moved")
    for arm in ARMS:
        if arm in tot.index:
            v, f = tot[arm], bad.get(arm)
            if pd.isna(v):
                print(f"  {ARMS[arm][0]:<24}n/a")
            else:
                f = 0 if pd.isna(f) else int(f)
                print(f"  {ARMS[arm][0]:<24}{int(v):>5} decided, {f:>5} failed, "
                      f"{int(v) - f:>5} moved")
    print("  Zero moved means the feature was either off at the engine or "
          "enabled and never engaged; both are results to report rather than "
          "assumptions to make. A failed call is logged as "
          "'No enough migrate out slots', and the scheduler prints "
          "'Finish migrating' on the next line either way.")

    note = ("Llumnix arms run with migration enabled; FluidServe, PolyServe and "
            "llm-d do not use it and run without. Bars are min..max over repeats.")
    if "llmdslo" in set(df.arm):
        # Said on the figure because none of it can be recovered from the lines.
        note += ("\nllm-d: one repeat, a session five days later, and the only "
                 "arm whose conditions were preceded by a 3 min pre-run.")
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(3.4, 2.6))
        for arm in ARMS:
            band(ax, df, arm, "adm")
            band(ax, df, arm, "off", dotted=True)
        ax.set_xlabel("request rate (req/s)")
        ax.set_ylabel("SLO attainment (%), per request")
        ax.set_ylim(0, 105)
        ax.set_xticks(sorted(df.rate.unique()))
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        # In the empty corner rather than over the legend or the data.
        ax.text(0.03, 0.05, "solid: admitted\ndotted: offered\ngap: rejection",
                transform=ax.transAxes, fontsize=7, va="bottom", ha="left",
                color="#444444")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
                  columnspacing=1.0, handletextpad=0.4, fontsize=7)
        fig.savefig(os.path.join(a.out, "attainment.png"), dpi=300,
                    bbox_inches="tight")
        plt.close(fig)

        # The same quantity with one denominator only. The two-denominator
        # figure is the honest one and stays; this is the one that can be read
        # at a glance, and it is only safe to read beside the rejection rates in
        # the table -- a policy that refuses everything scores 100 here.
        fig, ax = plt.subplots(figsize=(3.4, 2.4))
        for arm in ARMS:
            band(ax, df, arm, "adm")
        ax.set_xlabel("request rate (req/s)")
        ax.set_ylabel("SLO attainment (%), admitted")
        ax.set_ylim(0, 105)
        ax.set_xticks(sorted(df.rate.unique()))
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
                  columnspacing=1.0, handletextpad=0.4, fontsize=7)
        fig.savefig(os.path.join(a.out, "attainment_admitted.png"), dpi=300,
                    bbox_inches="tight")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(3.4, 2.4))
        for arm in ARMS:
            band(ax, df, arm, "offered" if False else "off")
        ax.set_xlabel("request rate (req/s)")
        ax.set_ylabel("SLO attainment (%), offered")
        ax.set_ylim(0, 105)
        ax.set_xticks(sorted(df.rate.unique()))
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
                  columnspacing=1.0, handletextpad=0.4, fontsize=7)
        fig.savefig(os.path.join(a.out, "attainment_offered.png"), dpi=300,
                    bbox_inches="tight")
        plt.close(fig)

        # Equal class weighting, admitted denominator. Read beside the
        # per-request figure and the rejection column, never instead of them.
        fig, ax = plt.subplots(figsize=(3.4, 2.4))
        for arm in ARMS:
            band(ax, df, arm, "eq_adm")
            band(ax, df, arm, "eq_off", dotted=True)
        ax.set_xlabel("request rate (req/s)")
        ax.set_ylabel("SLO attainment (%),\nequal weight per class")
        ax.set_ylim(0, 105)
        ax.set_xticks(sorted(df.rate.unique()))
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.text(0.03, 0.05, "solid: admitted\ndotted: offered",
                transform=ax.transAxes, fontsize=7, va="bottom", color="#444444")
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
                  columnspacing=1.0, handletextpad=0.4, fontsize=7)
        fig.savefig(os.path.join(a.out, "attainment_classequal.png"), dpi=300,
                    bbox_inches="tight")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(3.4, 2.4))
        for arm in ARMS:
            band(ax, df, arm, "goodput")
        ax.set_xlabel("request rate (req/s)")
        ax.set_ylabel("goodput (output tokens/s)")
        ax.set_xticks(sorted(df.rate.unique()))
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=2,
                  columnspacing=1.0, handletextpad=0.4, fontsize=7)
        fig.savefig(os.path.join(a.out, "goodput.png"), dpi=300, bbox_inches="tight")
        plt.close(fig)

        fig, axes = plt.subplots(1, 3, figsize=(7.2, 2.4), sharey=True)
        for ax, cl in zip(axes, CLASSES):
            for arm in ARMS:
                band(ax, df, arm, cl)
            ax.set_title(cl, color=CLASS_COLORS[cl], fontsize=8)
            ax.set_xlabel("req/s")
            ax.set_xticks(sorted(df.rate.unique()))
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            ax.set_ylim(0, 105)
        axes[0].set_ylabel("attainment (%), offered")
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, 1.12), ncol=4,
                       fontsize=7, columnspacing=1.0, handletextpad=0.4)
        fig.savefig(os.path.join(a.out, "per_class.png"), dpi=300,
                    bbox_inches="tight")
        plt.close(fig)

    n_per = df.groupby(["arm", "rate"]).size()
    single = sorted({f"{k[0]}@{k[1]:.0f}" for k, v in n_per.items() if v < 2})
    print(f"\nwrote 6 figures to {a.out}")
    print(note)
    if single:
        print(f"ONE REPEAT ({len(single)} cells), no bar: " + ", ".join(single))
    return 0


if __name__ == "__main__":
    sys.exit(main())
