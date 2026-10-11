#!/usr/bin/env python3
"""Per-segment scoring of a mix-shift hour, as a figure, with its CSV beside it.

WHY PER SEGMENT. The trace steps its class mixture every fifteen minutes and its
arrival rate with it. A whole-run mean of a run whose workload changes cannot
answer what the run was for, and on this pair it actively misleads: the arrivals
term gains offered attainment in the middle segments and loses it in the last
one, so the run mean reports a small gain and neither half.

WHY THE CUT. A request still in flight when the run ends is dropped from BOTH
attainment denominators, and at the end of a backlogged run those are exactly the
slow ones, so the final windows keep only the survivors and read far too high.
in_flight_at_end.py puts the first window above 20% at minute 60 for all three
runs here, so every arm is cut at the same minute and the last segment is scored
over 47-60 min rather than 47-61. The uncut value is printed beside the cut one
so the size of the artifact is visible rather than asserted.

BOTH DENOMINATORS. Admitted alone rewards refusing the requests that were going
to miss, which is the axis under test, so offered and admitted are drawn as
separate panels and the rejection rate that connects them gets a panel of its own.

  python3 exp125_segment_figure.py --series 'label|colour|dir' ... \\
      --trace <trace.csv> --out-dir <dir>
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, CLASSES, load_run, attain  # noqa: E402


def segments_from_trace(csv_path):
    d = pd.read_csv(csv_path, usecols=["arrival_s", "class", "segment"])
    out = []
    for name, g in d.groupby("segment"):
        if str(name).startswith("warmup"):
            continue
        mix = (g["class"].value_counts(normalize=True) * 100).round(0)
        out.append((str(name), float(g["arrival_s"].min()),
                    float(g["arrival_s"].max()),
                    ", ".join(f"{c} {mix.get(c,0):.0f}%" for c in CLASSES),
                    len(g) / (g["arrival_s"].max() - g["arrival_s"].min())))
    return sorted(out, key=lambda x: x[1])


def score(r, t0, t1):
    """The same four aggregates the experiment's own driver reports.

    goodput uses the window span as its divisor, which is what exp30_dynamic.py
    uses, so the number printed here is the same quantity as the one in the
    experiment file rather than a second thing with the same name.
    """
    w = r[(r["rel"] >= t0) & (r["rel"] < t1)]
    if w.empty:
        return None
    served = w[~w["rejected"]]
    met = served[~served["violate_served"]]
    tok = pd.to_numeric(met["output_tokens"], errors="coerce").fillna(0).sum()
    return dict(n=len(w),
                offered=attain(w, "violate_offered"),
                admitted=attain(served, "violate_served"),
                reject=100.0 * float(w["rejected"].mean()),
                goodput=float(tok) / (t1 - t0),
                **{f"adm_{c}": attain(served[served["class"] == c],
                                      "violate_served") for c in CLASSES})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", required=True,
                    help="label|colour|run-dir, repeatable")
    ap.add_argument("--trace", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--settle", type=float, default=60.0,
                    help="seconds after each boundary excluded from the segment")
    ap.add_argument("--cut-min", type=float, default=60.0)
    ap.add_argument("--tag", default="exp125")
    ap.add_argument("--title", default="EXP-125")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    series = []
    for spec in a.series:
        p = spec.split("|")
        if len(p) != 3:
            sys.exit(f"bad series spec (want label|colour|dir): {spec!r}")
        if not os.path.isdir(p[2]):
            sys.exit(f"series {p[0]!r}: not a directory: {p[2]}")
        series.append(dict(label=p[0], colour=p[1], dir=p[2]))

    segs = segments_from_trace(a.trace)
    cut = a.cut_min * 60.0
    print(f"\n=== segments from {os.path.basename(a.trace)}, "
          f"settle {a.settle:.0f}s dropped, cut at {a.cut_min:.0f} min")
    for n, t0, t1, mix, rate in segs:
        e = min(t1, cut)
        print(f"  {n:<8s} {t0/60:5.1f}-{t1/60:5.1f} min -> scored "
              f"{(t0+a.settle)/60:5.1f}-{e/60:5.1f}  {rate:6.1f} req/s  {mix}")

    rows = []
    for s in series:
        r = load_run(s["dir"])
        for n, t0, t1, mix, rate in segs:
            v = score(r, t0 + a.settle, min(t1, cut))
            vu = score(r, t0 + a.settle, t1)
            if v is None:
                continue
            rows.append(dict(arm=s["label"], run=os.path.basename(s["dir"]),
                             segment=n, mix=mix, trace_rate_req_s=rate,
                             t0_min=(t0 + a.settle) / 60.0,
                             t1_min=min(t1, cut) / 60.0, **v,
                             offered_uncut=vu["offered"],
                             admitted_uncut=vu["admitted"],
                             n_repeats=1,
                             scoring=("swe per-token 7s/75ms"
                                      if os.environ.get("FS_SWE_TBT_MS")
                                      else "swe e2e 30s")))
        v = score(r, 0.0, cut)
        rows.append(dict(arm=s["label"], run=os.path.basename(s["dir"]),
                         segment="whole (cut)", mix="all", trace_rate_req_s=np.nan,
                         t0_min=0.0, t1_min=a.cut_min, **v,
                         offered_uncut=score(r, 0.0, r["rel"].max())["offered"],
                         admitted_uncut=score(r, 0.0, r["rel"].max())["admitted"],
                         n_repeats=1,
                         scoring=("swe per-token 7s/75ms"
                                  if os.environ.get("FS_SWE_TBT_MS")
                                  else "swe e2e 30s")))
    df = pd.DataFrame(rows)

    print("\n=== scored (cut at 60 min); *_uncut is the same window run to the "
          "segment end, which the in-flight artifact inflates")
    pd.set_option("display.width", 220)
    print(df[["arm", "segment", "n", "offered", "offered_uncut", "admitted",
              "admitted_uncut", "reject", "goodput"]].to_string(
                  index=False, float_format="%.1f"))

    order = [s[0] for s in segs] + ["whole (cut)"]
    def _mixshort(mix):
        # "chat 93%, deepresearch 5%, swe 2%" is wider than the bar group
        # and collides with its neighbours, so it is abbreviated to the
        # three shares in class order with a key in the axis label.
        return "/".join(t.split()[-1].rstrip("%") for t in mix.split(", "))
    labels = {n: f"{n}\n{_mixshort(mix)}\n{rate:.0f} req/s"
              for n, _, _, mix, rate in segs}
    labels["whole (cut)"] = "whole run\n(cut at 60 min)"
    panels = [("offered", "SLO attainment (%), per request\nOFFERED denominator "
               "(a rejection is a violation)", (0, 105)),
              ("admitted", "SLO attainment (%), per request\nADMITTED "
               "denominator (rejections leave the population)", (0, 105)),
              ("reject", "rejected (% of arrivals)", None),
              ("goodput", "goodput (output tokens/s)", None)]

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(2, 2, figsize=(11.0, 8.6))
        ax = ax.ravel()
        w = 0.8 / len(series)
        x = np.arange(len(order))
        for k, (col, ylab, ylim) in enumerate(panels):
            A = ax[k]
            for j, s in enumerate(series):
                sub = df[df["arm"] == s["label"]].set_index("segment")
                v = [sub[col].get(o, np.nan) for o in order]
                A.bar(x + j * w - 0.4 + w / 2, v, width=w * 0.92,
                      color=s["colour"], label=s["label"])
                for xi, vi in zip(x + j * w - 0.4 + w / 2, v):
                    if not np.isnan(vi):
                        A.text(xi, vi, f"{vi:.0f}" if col == "goodput"
                               else f"{vi:.1f}", ha="center", va="bottom",
                               fontsize=5, rotation=90)
            A.set_xticks(x)
            A.set_xticklabels([labels[o] for o in order], fontsize=6)
            A.set_xlabel("mixture segment; second line is chat/deepresearch/swe "
                         "as % of arrivals, third is the trace's offered rate",
                         fontsize=6)
            A.set_ylabel(ylab, fontsize=7)
            if ylim:
                A.set_ylim(*ylim)
            else:
                A.set_ylim(0, A.get_ylim()[1] * 1.18)
            A.grid(axis="y", ls=":", lw=0.5, alpha=0.6)
            A.axvline(len(order) - 1.5, color="0.5", lw=0.8)
        # Caption numbers computed from the same table the panels are drawn from.
        c = df[df["arm"] == series[0]["label"]].set_index("segment")
        t = df[df["arm"] == series[1]["label"]].set_index("segment") \
            if len(series) > 1 else c
        s1, s3 = segs[1][0], segs[3][0]
        sub = (f"{series[1]['label']} minus {series[0]['label']}, offered: "
               f"{t.loc[s1,'offered']-c.loc[s1,'offered']:+.1f} in {s1}, "
               f"{t.loc[s3,'offered']-c.loc[s3,'offered']:+.1f} in {s3}, "
               f"{t.loc['whole (cut)','offered']-c.loc['whole (cut)','offered']:+.1f} "
               f"over the whole run -- the run mean is the average of a gain and "
               f"a loss and reports neither. Rejection "
               f"{t.loc[s1,'reject']-c.loc[s1,'reject']:+.1f} pp in {s1} and "
               f"{t.loc[s3,'reject']-c.loc[s3,'reject']:+.1f} pp in {s3}. "
               f"ONE REPEAT PER ARM: no error bars, so every bar reads as the "
               f"most precise point on the figure.")
        ax[0].legend(fontsize=6, frameon=False, ncol=len(series),
                     loc="lower left")
        import textwrap
        # bbox_inches="tight" is NOT used, so an unwrapped title is
        # simply clipped by the canvas rather than widening it; both
        # halves are wrapped to the same width.
        head = "\n".join(textwrap.wrap(a.title, 155))
        fig.suptitle(head + "\n" + "\n".join(textwrap.wrap(sub, 155)),
                     fontsize=7.0)
        fig.tight_layout(rect=[0, 0, 1, 0.845])
        p = os.path.join(a.out_dir, f"{a.tag}_segments.png")
        fig.savefig(p, dpi=160)
        plt.close(fig)
        print(f"\nwrote {p}")
    c = os.path.join(a.out_dir, f"{a.tag}_segments.csv")
    df.to_csv(c, index=False)
    print(f"wrote {c}")


if __name__ == "__main__":
    main()
