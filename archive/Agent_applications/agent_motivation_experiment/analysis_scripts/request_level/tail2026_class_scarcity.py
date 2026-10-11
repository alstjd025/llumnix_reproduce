#!/usr/bin/env python3
"""Can the policy tell, when a request ARRIVES, that its class has nowhere to go?

WHY THIS IS THE QUESTION.  EXP-84 measured that FluidServe authorises holds that
succeed 2.1-13.0% of the time, and that cutting them crudely with a 5,000 ms
gateway wall is worth 11-17 points.  The fix belongs in the policy, but a
per-request hold cap is not a fix -- a PEND decision already means "no feasible
destination at this instant", so "no feasible destination for the last k
re-decisions" is just `hold >= k x retry period` written differently, which is the
tuned constant the wall already is.

What a per-request timer CANNOT provide is information at ARRIVAL.  A class-level
state -- "requests of this class have been failing to find a destination for the
last W seconds" -- is available to the policy before it decides to hold at all,
and it is built from the policy's own recent decisions.  If it predicts the fate
of a newly arrived request, the policy can refuse immediately instead of holding
for five seconds first.

THE PREDICTOR, using only what the policy already knows at arrival:

    s(class, t) = of this class's requests that ARRIVED in [t - W, t), the
                  fraction that were refused or that finished having missed
                  their rule

and the question is whether s predicts the fate of the request arriving at t.
Requests still in flight at t are excluded from s, because the policy would not
know their outcome yet either -- the predictor is causal in that sense too.

Reported for each bucket of s: how many arrivals fell in it and what share of
them went on to meet their rule.  A predictor worth putting in the policy has to
separate a bucket where almost nothing succeeds from one where most things do.

Then the trade a refusal rule would make: for each threshold, how many arrivals it
would refuse, and how many of those were going to succeed.  The BENEFIT side --
capacity freed for other requests -- cannot be computed offline and needs the
experiment; this bounds only the cost.

    python3 tail2026_class_scarcity.py

Read only.  Prints; writes one CSV.
"""
import glob, os, re, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, CLASSES
from tail2026_holdwindow import RESULTS

W = 5.0                      # seconds of history the state is built from
NAME = re.compile(r"^\d{6}_\d{4}_exp(?P<exp>82|84a1)r(?P<rep>\d)_fspfx_m1_"
                  r"rpm_(?P<rpm>1500|2100|2700)$")
EDGES = [0.0, 0.05, 0.20, 0.40, 0.60, 0.80, 1.01]


def frame(d):
    r = load_run(d)
    if r is None or r.empty:
        return None
    r = r[~r["cutoff"]].copy()
    r["t"] = pd.to_numeric(r["rel"], errors="coerce")
    r["end"] = r["t"] + pd.to_numeric(r["latency"], errors="coerce").fillna(0.0)
    r["failed"] = r["violate_offered"].astype(bool)
    return r.dropna(subset=["t"]).sort_values("t").reset_index(drop=True)


rows, trade = [], []
for d in sorted(glob.glob(os.path.join(RESULTS, "*_fspfx_m1_rpm_*"))):
    b = os.path.basename(d)
    m = NAME.match(b)
    if not m:
        continue
    r = frame(d)
    if r is None or len(r) < 500:
        continue
    for c in CLASSES:
        s = r[r["class"] == c].reset_index(drop=True)
        if len(s) < 200:
            continue
        t = s["t"].to_numpy(float)
        end = s["end"].to_numpy(float)
        bad = s["failed"].to_numpy(bool)
        state = np.full(len(s), np.nan)
        for i in range(len(s)):
            # arrivals of this class in the window whose outcome was known by t[i]
            w = (t >= t[i] - W) & (t < t[i]) & (end <= t[i])
            if w.sum() >= 5:
                state[i] = float(bad[w].mean())
        ok = ~np.isnan(state)
        if ok.sum() < 100:
            continue
        cut = pd.cut(state[ok], EDGES, right=False)
        for lab, idx in pd.Series(range(int(ok.sum()))).groupby(cut, observed=True):
            j = np.flatnonzero(ok)[idx.to_numpy()]
            rows.append(dict(exp=m.group("exp"), rep=int(m.group("rep")),
                             rpm=int(m.group("rpm")), cls=c, bucket=str(lab),
                             n=len(j), met_pct=100.0 * float((~bad[j]).mean())))
        for thr in (0.2, 0.4, 0.6, 0.8):
            j = np.flatnonzero(ok & (state >= thr))
            trade.append(dict(exp=m.group("exp"), rep=int(m.group("rep")),
                              rpm=int(m.group("rpm")), cls=c, thr=thr,
                              refused_n=len(j),
                              refused_pct_of_class=100.0 * len(j) / int(ok.sum()),
                              of_those_would_have_met=(100.0 * float((~bad[j]).mean())
                                                       if len(j) else np.nan)))

df, tr = pd.DataFrame(rows), pd.DataFrame(trade)
out = os.path.join(RESULTS, "aggregate_analysis", "tail_2026-08-16",
                   "26_class_scarcity.csv")
pd.concat([df.assign(kind="bucket"), tr.assign(kind="trade")]).to_csv(out, index=False)


def band(g, col, f="%.1f"):
    v = pd.to_numeric(g[col], errors="coerce").dropna()
    if not len(v):
        return "--"
    if len(v) == 1 or abs(v.max() - v.min()) < 0.05:
        return f % v.mean()
    return (f + ".." + f) % (v.min(), v.max())


print(f"predictor: of this class's arrivals in the previous {W:.0f}s whose outcome "
      f"was already known, the fraction that failed\n")
for rpm in (1500, 2100, 2700):
    print(f"=== {rpm//60} req/s   share of arrivals that MET their rule, by the state at arrival")
    print(f"{'class':14s} " + "".join(f"{b:>16s}" for b in
          ["[0,.05)", "[.05,.2)", "[.2,.4)", "[.4,.6)", "[.6,.8)", "[.8,1]"]))
    for c in CLASSES:
        line = f"{c:14s}"
        for lab in ["[0.0, 0.05)", "[0.05, 0.2)", "[0.2, 0.4)", "[0.4, 0.6)",
                    "[0.6, 0.8)", "[0.8, 1.01)"]:
            g = df[(df.rpm == rpm) & (df.cls == c) & (df.bucket == lab)]
            if not len(g):
                line += f"{'--':>16s}"
            else:
                line += f"{band(g,'met_pct')+' ('+band(g,'n','%.0f')+')':>16s}"
        print(line)
    print()

print("=== the cost side of a refusal rule: refuse on arrival when state >= threshold")
print(f"{'class':14s} {'thr':>5s} {'% of class refused':>20s} {'of those, would have met':>26s}")
for c in CLASSES:
    for thr in (0.2, 0.4, 0.6, 0.8):
        g = tr[(tr.cls == c) & (tr.thr == thr) & (tr.rpm == 2100)]
        if not len(g):
            continue
        print(f"{c:14s} {thr:5.1f} {band(g,'refused_pct_of_class'):>20s} "
              f"{band(g,'of_those_would_have_met'):>26s}")
