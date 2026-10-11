#!/usr/bin/env python3
"""How much of the arm difference in token gaps is accounted for by batch size alone.

Each arm's gaps are already binned by the running batch of the engine that
produced them (`12_gap_hist_<run>.npz`). Write the pooled gap distribution of an
arm as a mixture over batch buckets,

    F_arm(g) = sum_b  w_arm(b) * F_arm(g | b)

where w_arm(b) is the share of that arm's tokens produced at batch b and
F_arm(.|b) is its gap distribution at that batch. Substituting one arm's weights
into the other arm's conditional distributions gives the distribution that arm
would have shown had it operated at the other's batch sizes with its own
per-batch behaviour unchanged. The quantile is then taken of the substituted
MIXTURE, not averaged across buckets: a quantile of a mixture is not the mixture
of the quantiles, and doing it the second way would silently understate the
tail.

Buckets where the donor arm has no mass cannot be evaluated -- our arm spends
much of its time at batch sizes llm-d never reaches -- so the uncovered weight
is reported next to every counterfactual. A counterfactual computed on 60% of
the weight is not a counterfactual; the number is printed so the reader can see
which rows are which.

    python3 tail2026_reweight.py --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import csv
import glob
import os

import numpy as np

MIN_COVER = 0.80


def quantile(h, edges, q):
    tot = h.sum()
    if tot <= 0:
        return np.nan
    i = min(int(np.searchsorted(np.cumsum(h), q * tot)), len(edges) - 2)
    hi = edges[i + 1] if np.isfinite(edges[i + 1]) else edges[i] + 2.0
    return 0.5 * (edges[i] + hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()

    per = {}
    edges = bedges = None
    for f in sorted(glob.glob(os.path.join(a.out_dir, "12_gap_hist_*.npz"))):
        z = np.load(f)
        if edges is None:
            edges, bedges = z["edges"], z["batch_edges"]
        name = os.path.basename(f)[len("12_gap_hist_"):-4]
        arm = "fspfx" if "fspfx" in name else "llmdslo"
        rate = int(name.rsplit("_", 1)[1]) / 60.0
        rep = 2 if "exp82r2" in name else 1
        per.setdefault((arm, rate, rep), 0)
        per[(arm, rate, rep)] = per[(arm, rate, rep)] + z["hist"].astype(np.float64)

    rows = []
    rates = sorted({k[1] for k in per})
    for rate in rates:
        for src, donor in (("fspfx", "llmdslo"), ("llmdslo", "fspfx")):
            hs = [v for k, v in per.items() if k[0] == src and k[1] == rate]
            hd = [v for k, v in per.items() if k[0] == donor and k[1] == rate]
            if not hs or not hd:
                continue
            H_s = sum(hs)
            H_d = sum(hd)
            w_s = H_s.sum(axis=1)
            w_s = w_s / w_s.sum()
            # Donor's conditional gap distribution per batch bucket.
            cover = float(w_s[H_d.sum(axis=1) > 0].sum())
            mix = np.zeros(H_s.shape[1])
            for b in range(H_s.shape[0]):
                dsum = H_d[b].sum()
                if dsum <= 0 or w_s[b] <= 0:
                    continue
                mix += w_s[b] * H_d[b] / dsum
            row = {"rate": rate, "src": src, "donor": donor,
                   "cover": round(cover, 4),
                   "n_src": int(H_s.sum()), "n_donor": int(H_d.sum())}
            for q in (0.5, 0.9, 0.95, 0.99):
                lab = f"p{int(q*100)}" if q != 0.999 else "p999"
                row[f"src_{lab}"] = quantile(H_s.sum(axis=0), edges, q)
                row[f"donor_{lab}"] = quantile(H_d.sum(axis=0), edges, q)
                row[f"donor_at_src_batch_{lab}"] = quantile(mix, edges, q) if cover >= MIN_COVER else np.nan
            rows.append(row)

    out = os.path.join(a.out_dir, "16_reweight.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {out}")

    for q in ("p50", "p90", "p99"):
        print(f"\n=== {q} of pooled token gaps (ms). "
              "'explained' = share of the arm gap that moving to the other arm's "
              "batch distribution reproduces")
        print(f"{'rate':>5} {'ours':>7} {'llmd':>7} {'gap':>7} | "
              f"{'llmd@ourB':>10} {'cov':>5} {'expl':>6} | "
              f"{'ours@llmdB':>11} {'cov':>5} {'expl':>6}")
        for rate in rates:
            f = [r for r in rows if r["rate"] == rate and r["src"] == "fspfx"]
            l = [r for r in rows if r["rate"] == rate and r["src"] == "llmdslo"]
            if not f or not l:
                continue
            f, l = f[0], l[0]
            ours, llmd = f[f"src_{q}"], f[f"donor_{q}"]
            d = ours - llmd
            a1 = f[f"donor_at_src_batch_{q}"]      # llm-d evaluated at our batches
            a2 = l[f"donor_at_src_batch_{q}"]      # us evaluated at llm-d's batches
            e1 = (a1 - llmd) / d * 100 if (d and np.isfinite(a1)) else np.nan
            e2 = (ours - a2) / d * 100 if (d and np.isfinite(a2)) else np.nan
            print(f"{rate:5.0f} {ours:7.1f} {llmd:7.1f} {d:+7.1f} | "
                  f"{a1:10.1f} {f['cover']:5.2f} {e1:5.0f}% | "
                  f"{a2:11.1f} {l['cover']:5.2f} {e2:5.0f}%")


if __name__ == "__main__":
    main()
