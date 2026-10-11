#!/usr/bin/env python3
"""EXP-125: did the arrivals term in the KV projection make the loop unstable?

The treatment arm adds one term to the KV projection: the mean prompt tokens an
instance received over the last N=1 horizons, added to kvLogical + inflow -
outflow. That term feeds back on the policy's OWN placements -- an instance that
received a lot is charged more and therefore receives less -- with a measured
loop delay of 5 to 25 s against a 4 s window. A previous arrivals estimator built
on an instance's own dispatch rate left two runs of one binary eight attainment
points apart, so oscillation is the specific thing being looked for.

WHAT IS DRAWN, and why each panel is needed.

  row 1  per-instance share of admitted arrivals, per window. This is the
         quantity the feedback term acts on directly. Oscillation would appear
         as instance shares crossing each other repeatedly at a fixed period.

  row 2  how far that share vector moved, as total variation distance, at TWO
         lags. One lag cannot be read on its own: the distance between
         consecutive windows contains counting noise whose size depends on the
         distribution being measured, so a configuration that never moves can
         read higher than one that does. Movement accumulates with the lag and
         noise does not, so the RATIO lag8/lag1 is the comparable part -- near 1
         is noise, above 1 is real movement. Recorded reference values on this
         workload, for the per-CLASS distribution measured by
         separation_measures.py: static partition 5.5, ours 3.6, load balancing
         1.9. The pooled per-instance share measured here is a DIFFERENT vector
         from that per-class one and its ratio is not comparable with those
         three; both are reported so neither is mistaken for the other.

  row 3  the effective number of instances each class runs on, 1/sum(share^2)
         per window. Not the largest instance's share, which reads 50% both for
         two instances dedicated to a class and for one instance holding half of
         an unpartitioned class.

  row 4  rejection rate within the run. The open question is that rejection rose
         25.0 -> 26.6% on the hour while on static conditions the same arm
         reduced it 21.5 -> 21.0%, and whether that is the loop or the mixture
         shift is unresolved. Drawn against the segment boundaries so the two
         explanations can be told apart by eye.

ENGINE ATTRIBUTION COVERAGE IS A FILTER, NOT A FOOTNOTE. Rows 1-3 read the
scheduler's dispatch log joined to the client ids, and that log drops lines under
high load. A window whose admitted requests are only partly attributed produces a
share vector over a biased sample, and the bias is not uniform across instances.
Windows below --min-coverage are therefore dropped from rows 1-3 and the dropped
count is printed and drawn. Row 4 is client-side and is never filtered.
"""
import argparse
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, CLASSES, CLASS_COLORS, load_run  # noqa: E402
from exp41_engine_view import attribute_engines  # noqa: E402

MIN_PER_WINDOW = 40


def eff_count(counts):
    n = np.asarray([c for c in counts if c > 0], dtype=float)
    if n.sum() <= 0:
        return np.nan
    p = n / n.sum()
    return float(1.0 / np.square(p).sum())


def per_window(run_dir, win, step, cut_s, min_cov):
    """Share of admitted arrivals per instance, per window, with coverage."""
    r = load_run(run_dir)
    if cut_s is not None:
        r = r[r["rel"] < cut_s]
    e, _ = attribute_engines(run_dir, r)
    e = e[e["rel"] < cut_s] if cut_s is not None else e
    insts = sorted(int(p) for p in e["engine_port"].dropna().unique())
    adm = r[~r["rejected"]]
    tmax = float(r["rel"].max())
    rows = []
    t = 0.0
    while t + win <= tmax + 1e-9:
        w = e[(e["rel"] >= t) & (e["rel"] < t + win)]
        wa = adm[(adm["rel"] >= t) & (adm["rel"] < t + win)]
        wr = r[(r["rel"] >= t) & (r["rel"] < t + win)]
        cov = 100.0 * len(w) / len(wa) if len(wa) else np.nan
        vc = w["engine_port"].value_counts()
        cnt = np.array([float(vc.get(i, 0)) for i in insts])
        rec = dict(t_mid=t + win / 2.0, n_attributed=len(w), n_admitted=len(wa),
                   coverage=cov, n_arrivals=len(wr),
                   reject_pct=100.0 * float(wr["rejected"].mean()) if len(wr) else np.nan)
        ok = (cov >= min_cov) and cnt.sum() >= MIN_PER_WINDOW
        for i, c in zip(insts, cnt):
            rec[f"share_{i}"] = (c / cnt.sum() if (ok and cnt.sum()) else np.nan)
        for c in CLASSES:
            sub = w[w["class"] == c]
            v = sub["engine_port"].value_counts()
            rec[f"eff_{c}"] = (eff_count([v.get(i, 0) for i in insts])
                               if (ok and len(sub) >= MIN_PER_WINDOW) else np.nan)
        rec["usable"] = ok
        rows.append(rec)
        t += step
    return pd.DataFrame(rows), insts


def tv_lags(df, insts, lags=(1, 8)):
    """Total-variation movement of the pooled instance-share vector, per lag.

    Only window pairs whose BOTH ends survived the coverage filter contribute,
    so a gap does not turn into a spurious jump.
    """
    cols = [f"share_{i}" for i in insts]
    M = df[cols].to_numpy(dtype=float)
    out = {}
    for k in lags:
        d = []
        for i in range(k, len(M)):
            a, b = M[i], M[i - k]
            if np.isnan(a).any() or np.isnan(b).any():
                continue
            d.append(0.5 * float(np.abs(a - b).sum()))
        out[k] = (float(np.mean(d)) if d else np.nan, len(d))
    return out


def per_window_tv(df, insts, k):
    cols = [f"share_{i}" for i in insts]
    M = df[cols].to_numpy(dtype=float)
    v = np.full(len(M), np.nan)
    for i in range(k, len(M)):
        a, b = M[i], M[i - k]
        if not (np.isnan(a).any() or np.isnan(b).any()):
            v[i] = 0.5 * float(np.abs(a - b).sum())
    return v


def segments_from_trace(csv_path):
    d = pd.read_csv(csv_path, usecols=["arrival_s", "segment"])
    g = d.groupby("segment")["arrival_s"].agg(["min", "max"])
    g = g[~g.index.str.startswith("warmup")].sort_values("min")
    return [(n, float(a), float(b)) for n, (a, b) in g.iterrows()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", nargs="+", required=True,
                    help="label|colour|linestyle|run-dir, repeatable")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--window", type=float, default=60.0)
    ap.add_argument("--step", type=float, default=30.0)
    ap.add_argument("--cut-min", type=float, default=None,
                    help="drop arrivals after this many minutes, every series alike")
    ap.add_argument("--min-coverage", type=float, default=80.0)
    ap.add_argument("--trace", default=None, help="trace csv, for segment boundaries")
    ap.add_argument("--tag", default="exp125")
    ap.add_argument("--title", default="EXP-125")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    series = []
    for spec in a.series:
        p = spec.split("|")
        if len(p) != 4:
            sys.exit(f"bad series spec (want label|colour|ls|dir): {spec!r}")
        if not os.path.isdir(p[3]):
            sys.exit(f"series {p[0]!r}: not a directory: {p[3]}")
        series.append(dict(label=p[0], colour=p[1], ls=p[2], dir=p[3]))

    cut_s = a.cut_min * 60.0 if a.cut_min is not None else None
    segs = segments_from_trace(a.trace) if a.trace else []

    data, tvs, insts_all, run_reject = {}, {}, {}, {}
    print(f"\n=== windows: {a.window:.0f}s wide, {a.step:.0f}s step, "
          f"cut at {a.cut_min} min, coverage floor {a.min_coverage:.0f}%")
    for s in series:
        df, insts = per_window(s["dir"], a.window, a.step, cut_s, a.min_coverage)
        data[s["label"]] = df
        _r = load_run(s["dir"])
        if cut_s is not None:
            _r = _r[_r["rel"] < cut_s]
        run_reject[s["label"]] = 100.0 * float(_r["rejected"].mean())
        insts_all[s["label"]] = insts
        tvs[s["label"]] = tv_lags(df, insts)
        nu = int(df["usable"].sum())
        print(f"  {s['label']:<42s} {os.path.basename(s['dir'])}")
        print(f"      instances={len(insts)}  windows={len(df)}  usable={nu} "
              f"({100.0*nu/len(df):.0f}%)  coverage p05/p50={df['coverage'].quantile(.05):.0f}"
              f"/{df['coverage'].median():.0f}%")

    print("\n=== movement of the POOLED per-instance arrival share "
          "(total variation, 0 = unchanged)")
    print(f"    {'arm':<44s}{'lag1':>8s}{'lag8':>8s}{'ratio':>8s}{'n1':>6s}{'n8':>6s}")
    for s in series:
        t = tvs[s["label"]]
        m1, n1 = t[1]
        m8, n8 = t[8]
        ratio = m8 / m1 if m1 else np.nan
        print(f"    {s['label']:<44s}{m1:8.3f}{m8:8.3f}{ratio:8.2f}{n1:6d}{n8:6d}")

    # ---- figure -------------------------------------------------------
    n = len(series)
    with plt.rc_context(PAPER_STYLE):
        # Rows share a y axis across the columns. Without that a column
        # that looks calm is calm only relative to itself, and the reader
        # draws the opposite conclusion from the one the data supports.
        # They share x too: coverage gaps end some columns early and an
        # autoscaled x makes a shorter record look like a different hour.
        fig, ax = plt.subplots(4, n, figsize=(4.6 * n, 11.6), squeeze=False,
                               sharex=True)
        for row in ax:
            for A in row[1:]:
                A.sharey(row[0])

        def marks(axis):
            for _, t0, t1 in segs:
                axis.axvline(t0 / 60.0, color="0.75", lw=0.6, ls=":")

        for j, s in enumerate(series):
            lab, df, insts = s["label"], data[s["label"]], insts_all[s["label"]]
            x = df["t_mid"] / 60.0
            cm = plt.get_cmap("tab10")

            A = ax[0][j]
            for k, i in enumerate(insts):
                A.plot(x, 100 * df[f"share_{i}"], lw=0.8, color=cm(k % 10),
                       label=f"{i}")
            A.axhline(100.0 / len(insts), color="0.3", lw=0.7, ls="--")
            marks(A)
            hi = max(np.nanmax(d[[c for c in d.columns
                                 if c.startswith("share_")]].to_numpy(dtype=float))
                     for d in data.values())
            A.set_ylim(0, 100 * hi * 1.08)
            A.set_title(f"{lab}\nshare of admitted arrivals per instance (%)\n"
                        f"dashed = even split {100.0/len(insts):.1f}%; "
                        f"gaps = windows below {a.min_coverage:.0f}% attribution",
                        fontsize=7)
            if j == 0:
                A.set_ylabel("share of admitted arrivals (%)")
            A.legend(fontsize=5, ncol=4, frameon=False, loc="upper left")

            B = ax[1][j]
            B.plot(x, per_window_tv(df, insts, 1), lw=0.8, color=s["colour"],
                   label="lag 1 window")
            B.plot(x, per_window_tv(df, insts, 8), lw=0.9, color=s["colour"],
                   ls="--", alpha=0.7, label="lag 8 windows")
            marks(B)
            hib = max(np.nanmax(per_window_tv(d, insts_all[l], 8))
                      for l, d in data.items())
            B.set_ylim(0, float(hib) * 1.08)
            t = tvs[lab]
            r8 = t[8][0] / t[1][0] if t[1][0] else float("nan")
            B.set_title(f"movement of that share vector (total variation)\n"
                        f"mean lag1 {t[1][0]:.3f}, lag8 {t[8][0]:.3f}, "
                        f"ratio {r8:.2f}", fontsize=7)
            if j == 0:
                B.set_ylabel("total variation distance")
            B.legend(fontsize=6, frameon=False)

            C = ax[2][j]
            for c in CLASSES:
                C.plot(x, df[f"eff_{c}"], lw=0.8, color=CLASS_COLORS.get(c),
                       label=c)
            C.axhline(len(insts), color="0.3", lw=0.7, ls="--")
            marks(C)
            C.set_ylim(0, len(insts) + 0.6)
            med = {c: np.nanmedian(df[f"eff_{c}"]) for c in CLASSES}
            C.set_title("effective number of instances a class runs on, "
                        "1/sum(share^2)\nmedian over windows: "
                        + ", ".join(f"{c} {med[c]:.2f}" for c in CLASSES),
                        fontsize=7)
            if j == 0:
                C.set_ylabel(f"effective instances (of {len(insts)})")
            C.legend(fontsize=6, frameon=False, ncol=3)

            D = ax[3][j]
            D.plot(x, df["reject_pct"], lw=0.8, color=s["colour"])
            marks(D)
            D.set_ylim(0, 100)
            # Overlapping windows double-count arrivals, so the run figure is
            # taken from the requests themselves over the drawn span rather
            # than by averaging windows; the window mean is printed beside it
            # so a disagreement between the line and the number is visible.
            # nan windows are dropped from the weighted mean instead of
            # poisoning it -- an unguarded np.average returned nan here.
            ok = df["reject_pct"].notna() & df["n_arrivals"].gt(0)
            wmean = (float(np.average(df.loc[ok, "reject_pct"],
                                      weights=df.loc[ok, "n_arrivals"]))
                     if ok.any() else float("nan"))
            D.set_title(f"rejection rate within the run (client-side, "
                        f"unfiltered)\nover the drawn span: "
                        f"{run_reject[lab]:.1f}% of arrivals rejected "
                        f"(window-weighted mean {wmean:.1f}%)", fontsize=7)
            D.set_xlabel("minutes from first arrival")
            if j == 0:
                D.set_ylabel("rejected (% of arrivals)")

        for row in ax:
            for A in row:
                A.grid(axis="y", ls=":", lw=0.5, alpha=0.6)
        import textwrap
        fig.suptitle("\n".join(textwrap.wrap(a.title, 150)), fontsize=8)
        fig.tight_layout(rect=[0, 0, 1, 0.945])
        p = os.path.join(a.out_dir, f"{a.tag}_arrival_stability.png")
        fig.savefig(p, dpi=160)
        plt.close(fig)
        print(f"\nwrote {p}")

    out = []
    for s in series:
        df = data[s["label"]].copy()
        df.insert(0, "arm", s["label"])
        df.insert(1, "run", os.path.basename(s["dir"]))
        t = tvs[s["label"]]
        df["tv_lag1_mean"] = t[1][0]
        df["tv_lag8_mean"] = t[8][0]
        df["tv_ratio"] = t[8][0] / t[1][0] if t[1][0] else np.nan
        df["tv_lag1_window"] = per_window_tv(df, insts_all[s["label"]], 1)
        df["tv_lag8_window"] = per_window_tv(df, insts_all[s["label"]], 8)
        df["scoring"] = os.environ.get("FS_SWE_TBT_MS", "e2e30")
        df["n_repeats"] = 1
        out.append(df)
    c = os.path.join(a.out_dir, f"{a.tag}_arrival_stability.csv")
    pd.concat(out, ignore_index=True).to_csv(c, index=False)
    print(f"wrote {c}")


if __name__ == "__main__":
    main()
