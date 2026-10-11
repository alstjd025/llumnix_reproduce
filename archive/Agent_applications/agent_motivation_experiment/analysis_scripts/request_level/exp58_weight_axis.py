#!/usr/bin/env python3
"""EXP-58: the class preference as a degree, read against its judgement rules.

The rules were written before the run, in
experiments/EXP-58_affinity-weight-axis.md. This script reports each of them.

  H1  the effective number of instances a class runs on falls as the weight
      rises, and the ENDPOINTS reproduce what EXP-56 measured with the switch:
      w=0 must land inside fsnoaff's range and w=1 inside fluidserve's. An
      endpoint outside its range means the weighted sum is not the rule the
      switch was, and nothing between them can be read.

  H2  offered attainment rises with separation over the reachable range, with no
      downturn at the top. If it IS refuted -- if w=1 scores below w=0.4 by more
      than the w=1 repeat spread -- that is the first evidence from inside our
      own system that too much separation hurts, and it is the most valuable
      result in the experiment rather than a failure.

  H3  the spread between repeats widens as the weight rises, because the
      preference is positive feedback and the weight is how strongly it feeds
      back.

The third panel is what the experiment exists for. Until now every point on a
separation-against-attainment plot beyond two came from a DIFFERENT system, so
nothing on that plot could be attributed to the separation. Every point here is
one binary with one number changed.

  python3 exp58_weight_axis.py [--out DIR]
"""
import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, CLASSES, attain, load_run  # noqa: E402
from exp41_engine_view import attribute_engines  # noqa: E402
from separation_measures import (  # noqa: E402
    class_measures, residency_nochat, gate_nochat,
)

# The arm name carries the weight, so the sweep can grow without editing this.
ARM = re.compile(r"_exp58r(\d)_fsw(\d{3})_m1_rpm_(\d+)$")

# What EXP-56 measured with the switch, at 45 req/s, three repeats each. The
# endpoints of this sweep have to land inside these.
EXP56 = {
    0.0: dict(name="fsnoaff", offered=(86.5, 87.0), effinst=(3.91, 3.94),
              nochat=(1.0, 1.0)),
    1.0: dict(name="fluidserve", offered=(90.4, 98.9), effinst=(1.63, 3.01),
              nochat=(4.4, 30.9)),
}


def collect(pattern):
    rows = []
    for d in sorted(glob.glob(pattern)):
        m = ARM.search(os.path.basename(d))
        if not m:
            continue
        rep, w, rpm = int(m.group(1)), int(m.group(2)) / 100.0, int(m.group(3))
        r = load_run(d)
        if r is None or r.empty:
            print(f"  {os.path.basename(d)}: no rows (still running?)")
            continue
        served = r[~r["rejected"]]
        met = served[~served["violate_served"]]
        row = dict(run=os.path.basename(d), rep=rep, w=w, rate=rpm / 60.0,
                   offered=attain(r, "violate_offered"),
                   admitted=attain(served, "violate_served"),
                   reject=100.0 * float(r["rejected"].mean()),
                   goodput=float(pd.to_numeric(met["output_tokens"], errors="coerce")
                                 .fillna(0).sum()) / max(r["rel"].max(), 1.0),
                   chat_itl=float(pd.to_numeric(
                       served[served["class"] == "chat"]["itl_ms"],
                       errors="coerce").median()))
        # Build the attribution map if it is not there. Without it the
        # separation columns are missing and the third panel -- the reason this
        # experiment exists -- comes out empty with no error.
        emap = os.path.join(d, "analysis", "request_engine.csv")
        if not os.path.exists(emap):
            here = os.path.dirname(os.path.abspath(__file__))
            rc = os.system(f"python3 {here}/build_request_engine_map.py {d} "
                           f">/dev/null 2>&1")
            if rc != 0:
                print(f"  {os.path.basename(d)}: could not build the engine map")
        if os.path.exists(emap):
            e, n = attribute_engines(d, r)
            if e is not None and not e.empty:
                top1, eff, effc, _ = class_measures(e, 60.0, 30.0)
                row["effinst"] = float(np.nanmean([eff[c] for c in CLASSES]))
                row["effinst_chat"] = eff["chat"]
                row["top1"] = float(np.nanmean([top1[c] for c in CLASSES]))
                row["nochat"], _ = residency_nochat(e)
        row["gate_nochat"] = gate_nochat(d)
        rows.append(row)
    return pd.DataFrame(rows)


def judge(df, out):
    for rate in sorted(df["rate"].unique()):
        g = df[df["rate"] == rate]
        print(f"\n{'=' * 72}\n{rate:.0f} req/s\n{'=' * 72}")
        cols = ["rep", "w", "offered", "admitted", "reject", "goodput",
                "chat_itl", "effinst", "effinst_chat", "top1", "nochat",
                "gate_nochat"]
        print(g[[c for c in cols if c in g]].sort_values(["w", "rep"])
              .to_string(index=False, float_format="%.2f"))

        spec = dict(n=("offered", "size"), off=("offered", "mean"),
                    off_lo=("offered", "min"), off_hi=("offered", "max"))
        # Only aggregate what is there. Substituting another column when the
        # separation measures are missing prints the score twice under two
        # different names, which is worse than a missing column.
        for col, name in (("effinst", "eff"), ("nochat", "nc")):
            if col in g and not g[col].isna().all():
                spec[name] = (col, "mean")
        agg = g.groupby("w").agg(**spec).reset_index()
        print("\nper weight:")
        print(agg.to_string(index=False, float_format="%.2f"))

        if abs(rate - 45.0) > 1.0:
            continue

        print("\n--- H1: do the endpoints reproduce the switch?")
        for w, ref in EXP56.items():
            sub = g[g["w"] == w]
            if sub.empty:
                print(f"  w={w}: not measured yet")
                continue
            for key, col in (("offered", "offered"), ("effinst", "effinst"),
                             ("nochat", "nochat")):
                if col not in sub or sub[col].isna().all():
                    continue
                lo, hi = ref[key]
                vals = sub[col].dropna().tolist()
                # Report the distance rather than a verdict. The reference range
                # is the min and max of three draws, so a fourth landing just
                # outside it is expected and says nothing; a fourth landing far
                # outside says the weighted sum is not the rule the switch was.
                # Which of those it is depends on how far, so print how far.
                out = [v for v in vals if v < lo or v > hi]
                dist = max((min(abs(v - lo), abs(v - hi)) for v in out), default=0.0)
                width = hi - lo
                verdict = ("inside" if not out else
                           f"outside by {dist:.2f} on a range {width:.2f} wide")
                print(f"  w={w} {col:9s} {['%.2f' % v for v in vals]} against "
                      f"{ref['name']} {lo}-{hi}: {verdict}")

        print("\n--- H2: does the score turn at the top of the reachable range?")
        if len(agg) >= 2:
            top = agg.iloc[agg["w"].idxmax() if False else -1]
            prev = agg.iloc[-2]
            spread = top["off_hi"] - top["off_lo"]
            drop = prev["off"] - top["off"]
            print(f"  w={top['w']:.2f} scores {top['off']:.1f} against "
                  f"w={prev['w']:.2f} at {prev['off']:.1f}; "
                  f"difference {-drop:+.1f}, spread at the top {spread:.1f}")
            print("  -> " + ("TURNS: the highest weight is worse by more than its own "
                             "spread. First within-system evidence that too much "
                             "separation hurts." if drop > spread else
                             "no turn inside the reachable range"))

        # The knob and the thing the knob moves are not the same predictor, and
        # which of them tracks the score is the causal question this experiment
        # was built to ask. Printing both correlations answers it directly: if
        # the separation predicts the score where the weight does not, then the
        # weight is an instrument that perturbs the separation and the
        # separation is what matters.
        h = g.dropna(subset=["effinst"]) if "effinst" in g else g.iloc[0:0]
        if len(h) >= 3:
            print("\n--- what predicts the score: the knob, or the separation "
                  f"it produces? (n={len(h)})")
            for col, name in (("w", "the knob w"),
                              ("effinst", "effective instances per class"),
                              ("nochat", "chat-free instance-time")):
                if col in h and not h[col].isna().all():
                    print(f"  corr({name:32s}, offered) = "
                          f"{np.corrcoef(h[col], h['offered'])[0, 1]:+.3f}")

        print("\n--- H3: does the repeat spread widen with the weight?")
        print(agg[["w", "n", "off_lo", "off_hi"]].assign(
            spread=agg["off_hi"] - agg["off_lo"]).to_string(
            index=False, float_format="%.2f"))


def figure(df, out):
    have = df.dropna(subset=["effinst"]) if "effinst" in df else df.iloc[0:0]
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(11.0, 3.2))
        for rate, mk in zip(sorted(df["rate"].unique()), ["o", "s", "^"]):
            g = df[df["rate"] == rate].sort_values("w")
            a = g.groupby("w")["offered"].agg(["mean", "min", "max"]).reset_index()
            ax[0].errorbar(a["w"], a["mean"],
                           yerr=[a["mean"] - a["min"], a["max"] - a["mean"]],
                           marker=mk, capsize=2, label=f"{rate:.0f} req/s")
            if not have.empty:
                h = have[have["rate"] == rate].sort_values("w")
                b = h.groupby("w")["effinst"].agg(["mean", "min", "max"]).reset_index()
                ax[1].errorbar(b["w"], b["mean"],
                               yerr=[b["mean"] - b["min"], b["max"] - b["mean"]],
                               marker=mk, capsize=2, label=f"{rate:.0f} req/s")
                ax[2].scatter(h["effinst"], h["offered"], marker=mk, s=28,
                              edgecolor="white", linewidth=0.5,
                              label=f"{rate:.0f} req/s", zorder=3)
        ax[0].set_xlabel("class preference weight w")
        ax[0].set_ylabel("SLO attainment (%), offered")
        ax[0].set_title("A. the score against the knob\n"
                        "bars are min..max over repeats", fontsize=7)
        ax[1].set_xlabel("class preference weight w")
        ax[1].set_ylabel("effective instances per class (of 4)")
        ax[1].set_ylim(0.8, 4.2)
        ax[1].set_title("B. the knob moves the separation\n"
                        "4.0 = every class on every instance", fontsize=7)
        ax[2].set_xlabel("effective instances per class (of 4)")
        ax[2].set_ylabel("SLO attainment (%), offered")
        ax[2].set_xlim(0.8, 4.2)
        ax[2].set_title("C. and this curve is one system\n"
                        "one binary, one number changed", fontsize=7)
        for k in range(3):
            ax[k].grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            ax[k].legend(fontsize=7)
        fig.suptitle("EXP-58 — the class preference as a degree. "
                     "w=1 is the shipped ordering, w=0 is the preference off.",
                     fontsize=8.5, y=1.04)
        fig.tight_layout()
        p = os.path.join(out, "exp58_weight_axis.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        plt.close(fig)
        print(f"\nwrote {p}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pattern", default="results/*exp58r?_fsw*_m1_rpm_*")
    ap.add_argument("--out", default="results/aggregate_analysis/exp58")
    ap.add_argument("--csv", default="")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    df = collect(a.pattern)
    if df.empty:
        sys.exit("no EXP-58 runs matched")
    print(f"{len(df)} conditions, weights {sorted(df['w'].unique())}, "
          f"rates {sorted(df['rate'].unique())}")
    judge(df, a.out)
    figure(df, a.out)
    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"wrote {a.csv}")


if __name__ == "__main__":
    main()
