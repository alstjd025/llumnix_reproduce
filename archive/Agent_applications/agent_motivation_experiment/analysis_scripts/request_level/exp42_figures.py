#!/usr/bin/env python3
"""EXP-42 figures: one flag, two arms, one session.

Three figures, because they answer three questions and a reader who wants one
of them should not have to find it inside the others.

  headline    attainment on both denominators, token goodput, rejection rate.
              Solid is the admitted denominator, dotted the offered one, and the
              gap between them IS what the rejection costs. The change under
              test raises the rejection rate on purpose, so drawing the admitted
              view alone would score it for refusing the requests that were
              going to miss -- which is the axis under test.
  mechanism   what the policy decided, chat's inter-token latency against the
              50 ms budget that decides most of the score, and the three classes
              separately. Nothing here is a headline; it is why the headline
              moved.
  regimes     every condition as one point, route share against attainment.
              45 req/s is bistable on the shipped policy and this is the only
              view in which that reads as two states rather than a wide error
              bar.

Aggregation is per request on both denominators, and the titles say so.
Error bars are min..max over repeats; a point with one repeat gets none and the
note says so.

  python3 exp42_figures.py --runs 'results/*exp42*' --out-dir <dir>
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
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    PAPER_STYLE, load_run, attain, CLASSES, CLASS_COLORS,
)

# fsbase IS FluidServe as shipped, so it keeps FluidServe's colour. The variant
# takes one that none of the three fixed policy colours uses.
ARMS = {
    "fsbase": ("FluidServe as shipped", "#1f77b4", "o"),
    "fsa":    ("+ same margin on forced placement", "#ff7f0e", "s"),
}
PAT = re.compile(r"exp4(\d)r(\d)_(fsbase|fsa|fsah)_m1f?_rpm_(\d+)")


def decisions(run):
    rows = []
    p = os.path.join(run, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(p):
        return {}
    for line in open(p):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not o.get("ok"):
            continue
        rec = {"t": o["t"]}
        for k, v in o.items():
            m = re.match(r"scheduler_fluidserve_decisions_total\|decision=(\w+)", k)
            if m:
                rec[m.group(1)] = v
        rows.append(rec)
    if len(rows) < 5:
        return {}
    d = pd.DataFrame(rows).fillna(0)
    d["t"] -= d["t"].min()
    d = d[(d["t"] > 120) & (d["t"] < d["t"].max() - 60)]
    if len(d) < 3:
        return {}
    cols = [c for c in ("route", "pend", "shed", "force") if c in d.columns]
    tot = {c: float(d[c].iloc[-1] - d[c].iloc[0]) for c in cols}
    n = max(sum(tot.values()), 1.0)
    return {c: 100.0 * tot.get(c, 0.0) / n for c in ("route", "pend", "shed", "force")}


def collect(patterns, want_exp):
    rows = []
    for pat in patterns:
        for d in sorted(glob.glob(pat)):
            m = PAT.search(os.path.basename(d))
            if not m or m.group(1) != want_exp or m.group(3) not in ARMS:
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            span = r["rel"].max() - r["rel"].min()
            ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
            adm = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])]
            ch = adm[adm["class"] == "chat"]
            tok = lambda g: pd.to_numeric(g.get("output_tokens"),
                                          errors="coerce").fillna(0).sum()
            rec = dict(rep=int(m.group(2)), arm=m.group(3), rate=int(m.group(4)) / 60.0,
                       off=attain(r, "violate_offered"), adm=attain(r, "violate_served"),
                       rej=100.0 * r["rejected"].mean(), goodput=tok(ok) / span,
                       chat_itl=pd.to_numeric(ch["itl_ms"], errors="coerce").median())
            for cl in CLASSES:
                rec[f"{cl}_off"] = attain(r[r["class"] == cl], "violate_offered")
            rec.update({f"dec_{k}": v for k, v in decisions(d).items()})
            rows.append(rec)
    return pd.DataFrame(rows)


def band(ax, df, arm, col, ls="-", label=None, mk=None):
    """Mean with min..max over repeats. One repeat draws no bar, by construction."""
    s = df[df.arm == arm]
    if s.empty:
        return 0
    g = s.groupby("rate")[col]
    mu, lo, hi = g.mean(), g.min(), g.max()
    _, c, m = ARMS[arm]
    ax.errorbar(mu.index, mu.values,
                yerr=[mu.values - lo.values, hi.values - mu.values],
                color=c, ls=ls, marker=mk or m, capsize=2, label=label,
                markeredgecolor="white", markeredgewidth=0.5)
    return int(s.groupby("rate").size().min())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--exp", default="2", help="4<N> series to select, e.g. 2 for EXP-42")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)
    df = collect(a.runs, a.exp)
    if df.empty:
        sys.exit("no runs matched")
    arms = [x for x in ARMS if x in set(df.arm)]
    rates = sorted(df["rate"].unique())
    nrep = df.groupby(["arm", "rate"]).size()
    print(f"arms {arms}, rates {rates}, {len(df)} conditions, "
          f"repeats {nrep.min()}..{nrep.max()}")
    note = (f"one session, one binary; the arms differ only in "
            f"--fluidserve-force-margin. Bars are min..max over "
            f"{nrep.min()} repeats.")

    with plt.rc_context(PAPER_STYLE):
        # ---------------- headline ----------------
        fig, ax = plt.subplots(1, 3, figsize=(10.4, 3.2))
        for arm in arms:
            lab, _, _ = ARMS[arm]
            band(ax[0], df, arm, "adm", "-", f"{lab} — admitted")
            band(ax[0], df, arm, "off", ":", f"{lab} — offered")
            band(ax[1], df, arm, "goodput", "-", lab)
            band(ax[2], df, arm, "rej", "-", lab)
        ax[0].set_ylabel("SLO attainment (%), per request")
        ax[0].set_ylim(0, 105)
        ax[0].set_title("attainment, both denominators", fontsize=8.5)
        ax[0].legend(fontsize=6, loc="lower left")
        ax[1].set_ylabel("goodput (output tokens/s)")
        ax[1].set_title("token goodput", fontsize=8.5)
        ax[1].legend(fontsize=6.5, loc="lower left")
        ax[2].set_ylabel("rejected (%)")
        ax[2].set_title("rejection rate — the gap between the two curves left",
                        fontsize=8.5)
        ax[2].legend(fontsize=6.5, loc="upper left")
        for x in ax:
            x.set_xlabel("offered rate (requests/s)")
            x.set_xticks(rates)
            x.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        fig.suptitle("EXP-42 — applying the routing margin to the forced-placement "
                     f"test\n{note}", fontsize=9, y=1.10)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp42_headline.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig)
        print(f"wrote {p}")

        # ---------------- mechanism ----------------
        fig, ax = plt.subplots(1, 3, figsize=(10.4, 3.2))
        DEC = [("route", "-"), ("force", "--"), ("shed", ":")]
        for arm in arms:
            lab, c, _ = ARMS[arm]
            for k, ls in DEC:
                if f"dec_{k}" in df.columns:
                    band(ax[0], df, arm, f"dec_{k}", ls, None, mk=".")
            band(ax[1], df, arm, "chat_itl", "-", lab)
            for cl in CLASSES:
                s = df[df.arm == arm].groupby("rate")[f"{cl}_off"].mean()
                ax[2].plot(s.index, s.values, color=CLASS_COLORS[cl],
                           ls="-" if arm == arms[-1] else ":", lw=1.2)
        h = [plt.Line2D([], [], color="#666666", ls=ls, lw=1.2) for _, ls in DEC]
        h += [plt.Line2D([], [], color=ARMS[x][1], lw=1.2) for x in arms]
        ax[0].legend(h, [k for k, _ in DEC] + [ARMS[x][0] for x in arms],
                     fontsize=5.8, loc="center left")
        ax[0].set_ylabel("share of decisions (%)")
        ax[0].set_title("what the policy decided", fontsize=8.5)
        ax[1].axhline(50.0, color="#d62728", lw=0.8, ls=":")
        # Below the line and at the right: above it runs into the title, and at
        # the left into the legend.
        ax[1].annotate("chat budget 50 ms", (rates[-1], 50.0), ha="right",
                       textcoords="offset points", xytext=(-2, -9),
                       fontsize=6.5, color="#d62728")
        ax[1].set_ylabel("chat median inter-token latency (ms)")
        ax[1].set_title("the quantity the budget is a threshold on", fontsize=8.5)
        ax[1].legend(fontsize=6.5, loc="lower right")
        hc = [plt.Line2D([], [], color=CLASS_COLORS[c], lw=1.2) for c in CLASSES]
        hc += [plt.Line2D([], [], color="#666666", ls=":" if i == 0 else "-", lw=1.2)
               for i, _ in enumerate(arms)]
        # Six entries over lines that reach the bottom of the panel, so this one
        # goes under the axes rather than onto the data.
        ax[2].legend(hc, list(CLASSES) + [ARMS[x][0] for x in arms],
                     fontsize=5.8, ncol=2, loc="upper center",
                     bbox_to_anchor=(0.5, -0.22))
        ax[2].set_ylabel("SLO attainment (%), offered")
        ax[2].set_ylim(0, 105)
        ax[2].set_title("per class, offered denominator", fontsize=8.5)
        for x in ax:
            x.set_xlabel("offered rate (requests/s)")
            x.set_xticks(rates)
            x.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        fig.suptitle("EXP-42 mechanism — forced placements become rejections, and "
                     "routing resumes", fontsize=9, y=1.04)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp42_mechanism.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig)
        print(f"wrote {p}")

        # ---------------- regimes ----------------
        fig, ax = plt.subplots(figsize=(4.6, 3.4))
        for arm in arms:
            s = df[df.arm == arm]
            lab, c, m = ARMS[arm]
            ax.scatter(s["dec_route"], s["off"], color=c, marker=m, s=34,
                       edgecolor="white", linewidth=0.5, label=lab, zorder=3,
                       alpha=0.85)
            # Every condition at 15 and 30 req/s sits in the same corner at 100%
            # attainment and ~100% routing, so labelling each one there produces
            # a stack of overlapping text that says nothing. The points that
            # carry the figure are the ones away from that corner.
            for _, r in s.iterrows():
                if r["off"] > 99.5 and r["dec_route"] > 97:
                    continue
                dx = -12 if arm == arms[0] else 5
                ax.annotate(f"{r['rate']:.0f}", (r["dec_route"], r["off"]),
                            textcoords="offset points", xytext=(dx, -2),
                            fontsize=6, color=c)
        # Upper left is the only empty quadrant: nothing routes little and
        # scores well, which is the shape the figure is showing.
        ax.annotate("15 and 30 req/s, both arms, sit together at\n"
                    "route >97%, attainment 100 and are unlabelled",
                    (0.03, 0.93), xycoords="axes fraction", ha="left", va="top",
                    fontsize=6, color="#666666")
        ax.set_xlabel("share of decisions that routed (%)")
        ax.set_ylabel("SLO attainment (%), offered, per request")
        ax.set_ylim(0, 105)
        ax.grid(ls=":", lw=0.7, alpha=0.6)
        ax.legend(fontsize=6.5, loc="lower right")
        ax.set_title("every condition as one point, labelled by rate\n"
                     "45 req/s appears twice for the shipped arm, in two places",
                     fontsize=8)
        fig.tight_layout()
        p = os.path.join(a.out_dir, "exp42_regimes.png")
        fig.savefig(p, dpi=300, bbox_inches="tight"); plt.close(fig)
        print(f"wrote {p}")


if __name__ == "__main__":
    main()
