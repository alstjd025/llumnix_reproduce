#!/usr/bin/env python3
"""Figure and consolidated tables for the batch / step-time chain.

Panel A  median token gap against running batch, one line per arm. This is the
         curve the chain predicts, and the panel that decides whether it is one
         curve or two.
Panel B  the same for the p90 of the token gap.
Panel C  the same median, but with KV occupancy held in a narrow band, because
         the engine-side means show that step time is a function of two things
         and the batch count alone does not measure the work in the batch.
Panel D  the exceedance curve of the token gap, both arms, showing where the
         mass that sets the p99 sits.

Every point is a token-weighted aggregate over both repeats where two exist; the
repeats are drawn as separate faint lines so the reader can see the spread that
any claimed difference has to beat.

    python3 tail2026_batch_figs.py --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import glob
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

C = {"fspfx": "#1f77b4", "llmdslo": "#d62728"}
L = {"fspfx": "FluidServe (ours)", "llmdslo": "llm-d"}


def load(out_dir):
    per = {}
    edges = bedges = None
    files = (sorted(glob.glob(os.path.join(out_dir, "12_gap_hist_*.npz")))
             + sorted(glob.glob(os.path.join(out_dir, "extra", "12_gap_hist_*.npz"))))
    for f in files:
        z = np.load(f)
        if edges is None:
            edges, bedges = z["edges"], z["batch_edges"]
        n = os.path.basename(f)[len("12_gap_hist_"):-4]
        arm = "fspfx" if "fspfx" in n else "llmdslo"
        rate = int(n.rsplit("_", 1)[1]) / 60.0
        rep = 2 if "exp82r2" in n else 1
        per[(arm, rate, rep)] = z["hist"].astype(np.float64)
    return per, edges, bedges


def quant(h, edges, q):
    t = h.sum()
    if t < 2000:
        return np.nan
    i = min(int(np.searchsorted(np.cumsum(h), q * t)), len(edges) - 2)
    hi = edges[i + 1] if np.isfinite(edges[i + 1]) else edges[i] + 2.0
    return 0.5 * (edges[i] + hi)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    per, edges, bedges = load(a.out_dir)
    centres = 0.5 * (bedges[:-1] + bedges[1:])

    fig, ax = plt.subplots(2, 2, figsize=(12.5, 9.0))

    for k, (q, axx, ttl) in enumerate((
            (0.5, ax[0][0], "A. median token gap vs running batch"),
            (0.9, ax[0][1], "B. p90 of token gap vs running batch"))):
        for arm in ("fspfx", "llmdslo"):
            tot = sum(v for kk, v in per.items() if kk[0] == arm)
            y = [quant(tot[b], edges, q) for b in range(len(centres))]
            axx.plot(centres, y, "-o", ms=4, color=C[arm], label=L[arm], zorder=3)
            for kk, v in per.items():
                if kk[0] != arm:
                    continue
                yy = [quant(v[b], edges, q) for b in range(len(centres))]
                axx.plot(centres, yy, "-", lw=0.6, alpha=0.35, color=C[arm], zorder=1)
        axx.set_xlabel("running requests on the engine that produced the token")
        axx.set_ylabel("token gap (ms)")
        axx.set_title(ttl, fontsize=10)
        axx.grid(alpha=0.3)
        axx.legend(fontsize=8)
        axx.set_xlim(0, 320)
    ax[0][0].set_ylim(15, 45)
    ax[0][1].set_ylim(15, 200)

    # Panel C: same median, KV held in 30-50 %.
    bk = {}
    files = (sorted(glob.glob(os.path.join(a.out_dir, "13_bk_hist_*.npz")))
             + sorted(glob.glob(os.path.join(a.out_dir, "extra", "13_bk_hist_*.npz"))))
    for f in files:
        z = np.load(f)
        n = os.path.basename(f)[len("13_bk_hist_"):-4]
        arm = "fspfx" if "fspfx" in n else "llmdslo"
        bk.setdefault(arm, 0)
        bk[arm] = bk[arm] + z["hist"].astype(np.float64)
        E2, BE2, KE2 = z["edges"], z["batch_edges"], z["kv_edges"]
    kcen = 0.5 * (BE2[:-1] + BE2[1:])
    for arm, h in bk.items():
        band = h[:, 3:5, :].sum(axis=1)      # KV 30-50 %
        y = [quant(band[b], E2, 0.5) for b in range(len(kcen))]
        ax[1][0].plot(kcen, y, "-o", ms=4, color=C[arm], label=L[arm])
        y9 = [quant(band[b], E2, 0.9) for b in range(len(kcen))]
        ax[1][0].plot(kcen, y9, "--s", ms=3, color=C[arm], alpha=0.6,
                      label=L[arm] + ", p90")
    ax[1][0].set_xlabel("running requests on the engine")
    ax[1][0].set_ylabel("token gap (ms)")
    ax[1][0].set_title("C. KV occupancy held at 30-50 %: solid = median, dashed = p90",
                       fontsize=10)
    ax[1][0].grid(alpha=0.3)
    ax[1][0].legend(fontsize=7)
    ax[1][0].set_xlim(0, 320)

    # Panel D: exceedance.
    for arm in ("fspfx", "llmdslo"):
        tot = sum(v for kk, v in per.items() if kk[0] == arm).sum(axis=0)
        cc = 1.0 - np.cumsum(tot) / tot.sum()
        x = edges[1:]
        m = (x > 20) & (x < 800) & np.isfinite(x)
        ax[1][1].semilogy(x[m], cc[m], color=C[arm], label=L[arm])
    ax[1][1].axvspan(490, 570, color="grey", alpha=0.2)
    ax[1][1].text(530, 2e-1, "capped prefill step\n(both arms)", ha="center", fontsize=7)
    ax[1][1].axhline(0.01, color="k", lw=0.6, ls=":")
    ax[1][1].text(60, 0.011, "p99", fontsize=7)
    ax[1][1].set_xlabel("token gap (ms)")
    ax[1][1].set_ylabel("fraction of tokens with a longer gap")
    ax[1][1].set_title("D. exceedance of the token gap, all rates pooled", fontsize=10)
    ax[1][1].grid(alpha=0.3)
    ax[1][1].legend(fontsize=8)

    fig.suptitle("Token gap against the running batch of the engine that produced it "
                 "(EXP-82, gateway CPU-quota defect fixed)", fontsize=11)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    out = os.path.join(a.out_dir, "12_batch_step_chain.png")
    fig.savefig(out, dpi=130)
    print("wrote", out)


if __name__ == "__main__":
    main()
