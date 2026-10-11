#!/usr/bin/env python3
"""Does what an instance received over the LAST horizon predict what it receives over
the NEXT one?  This is the question the earlier correlations did NOT answer: those
compared arrivals in [t, t+H] against other quantities measured over the SAME window,
which uses information from after the decision instant and is close to an identity."""
import numpy as np, sys

def run(tag, path):
    z = np.load(path, allow_pickle=True)
    st, sinst, sval, scols = z["s_t"], z["s_inst"], z["s_val"], list(z["s_cols"])
    rt, rinst, rp, rdec = z["r_t"], z["r_inst"], z["r_prompt"], z["r_dec"]
    lo, hi = z["win"]
    pace_i = scols.index([c for c in scols if c.endswith("pace_ms")][0])
    live = set(z["live"].tolist()) if "live" in z else set(np.unique(sinst).tolist())
    keep = rdec == "route"
    rt, rinst, rp = rt[keep], rinst[keep], rp[keep]

    rows = []
    for iid in sorted(live):
        m = sinst == iid
        t = st[m]; pace = sval[m][:, pace_i]
        o = np.argsort(t); t, pace = t[o], pace[o]
        H = 100.0 * pace / 1000.0                      # horizon in seconds, per sample
        ok = np.isfinite(H) & (H > 0) & (t >= lo) & (t <= hi)
        t, H = t[ok], H[ok]
        if len(t) < 50: continue
        rm = rinst == iid
        rti = rt[rm]; ro = np.argsort(rti); rti = rti[ro]; rpi = rp[rm][ro]
        cum = np.concatenate([[0.0], np.cumsum(rpi)])
        def mass(a, b):
            return cum[np.searchsorted(rti, b, side="right")] - cum[np.searchsorted(rti, a, side="right")]
        prev = mass(t - H, t)      # what it received over the PREVIOUS horizon  (known at t)
        nxt  = mass(t, t + H)      # what it receives over the NEXT horizon       (to be predicted)
        rows.append((iid, prev, nxt))

    print(f"--- {tag}   instances={len(rows)}")
    # per instance, then pooled; report Pearson and the MAE of using prev as the estimate of nxt
    cs, maes, means = [], [], []
    for iid, prev, nxt in rows:
        f = np.isfinite(prev) & np.isfinite(nxt)
        if f.sum() < 50: continue
        c = np.corrcoef(prev[f], nxt[f])[0, 1]
        cs.append(c); maes.append(np.abs(prev[f] - nxt[f]).mean()); means.append(nxt[f].mean())
    cs = np.array(cs); maes = np.array(maes); means = np.array(means)
    print(f"    lag-1 corr(prev horizon, next horizon), per instance: "
          f"min {cs.min():+.3f}  p50 {np.median(cs):+.3f}  max {cs.max():+.3f}")
    print(f"    using prev as the estimate of next: MAE {maes.mean():,.0f} tokens "
          f"vs next-horizon mean {means.mean():,.0f}  ->  MAE/mean {maes.mean()/max(means.mean(),1):.2f}")
    # smoothing the predictor over k horizons: noise down, response slower
    for k in (1, 3, 5, 10, 20):
        cs2, m2, mu2 = [], [], []
        for iid, prev, nxt in rows:
            if len(prev) < 5 * k + 50: continue
            # box average of the previous k horizons, approximated by averaging the
            # prev series over the k most recent samples
            s = np.convolve(prev, np.ones(k) / k, mode="full")[:len(prev)]
            f = np.isfinite(s) & np.isfinite(nxt)
            if f.sum() < 50: continue
            cs2.append(np.corrcoef(s[f], nxt[f])[0, 1])
            m2.append(np.abs(s[f] - nxt[f]).mean()); mu2.append(nxt[f].mean())
        if cs2:
            print(f"      smoothed over {k:2d} horizon(s): corr p50 {np.median(cs2):+.3f}   "
                  f"MAE/mean {np.mean(m2)/max(np.mean(mu2),1):.2f}")

for tag, p in (("hour r1", "h1.npz"), ("hour r2", "h2.npz"), ("4x70B hour", "e70.npz")):
    try: run(tag, p)
    except Exception as e: print(f"--- {tag}: {type(e).__name__}: {e}")
