#!/usr/bin/env python3
"""Why the 35,000 ms ceiling decides the fleet arrangement even though it cuts almost nothing.

WHAT THIS IS FOR.  EXP-84 refuted the prediction written down before it ran.  The
prediction was that the gateway RETRY PERIOD chooses whether one instance becomes
deepresearch-only, and the argument was a bound: `gateway_scheduling_gave_up_total`
is 0 in every EXP-82 run, and only 0.6-3.2% of arrivals there were held past
5,000 ms, so a 5,000 ms ceiling could touch at most that share, against an effect
of 10-19 points of attainment.  The measurement says the opposite -- with the
ceiling left at 35,000 ms and only the retry period changed, an instance is still
given over to deepresearch; with the ceiling cut to 5,000 ms and the retry period
left at 500 ms, it is not.

The bound was arithmetically right and inferentially wrong: it bounds WHICH
REQUESTS the ceiling touches, and then assumes the effect is proportional to that
share.  It is not, if the requests it touches are the ones that construct the
arrangement.  This script tests exactly that, on data that was already on disk
before EXP-84 ran:

  1. of the deepresearch requests placed on the instance that became
     deepresearch-only, what share had been held longer than a 5,000 ms ceiling
     would have allowed -- and how does that compare with the same share on the
     other three instances
  2. when the instance's gate first moves from chat's 50 ms to deepresearch's
     100 ms, what had just been placed on it
  3. how many placements a 5,000 ms ceiling would have removed, per instance

A confirmation looks like: the long-held requests are concentrated on the instance
that locks, and they arrive before it locks.  A refutation looks like: they are
spread evenly, in which case the ceiling is not selecting the requests that build
the state and something else explains EXP-84.

    python3 tail2026_ceiling_mechanism.py [--out <directory>]

Writes `22_ceiling_mechanism.md` and `22_*.csv`.  Reads only; creates only new files.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, CLASSES              # noqa: E402
from tail2026_holdwindow import RESULTS                     # noqa: E402
from tail2026_holdwindow_state import dispatch_pairs        # noqa: E402

CEILING_MS = 5000.0          # the ceiling whose effect is being explained
NAME_RE = re.compile(r"^\d{6}_\d{4}_exp(?P<cell>82|84a1)r(?P<rep>\d)"
                     r"_fspfx_m1_rpm_(?P<rpm>2100|2700)$")
CELL = {"82": "35,000 / 500", "84a1": "35,000 / 1,000"}
GATE_PRE = "scheduler_fluidserve_gate_allowance_ms|instance="
LOCK_MS = 95.0               # a gate at or above this is deepresearch's 100 ms
LOCK_WIN_S = 60.0            # a crossing counts once it holds for this long


def run_dirs():
    out = []
    for d in glob.glob(os.path.join(RESULTS, "*_fspfx_m1_rpm_*")):
        b = os.path.basename(d)
        m = NAME_RE.match(b)
        if m and os.path.isdir(d):
            out.append((d, m.group("cell"), int(m.group("rep")),
                        int(m.group("rpm"))))
    return sorted(out, key=lambda t: (t[1], t[3], t[2]))


def gate_series(run_dir):
    """{instance index: (times, gate values)} with the same index order as elsewhere."""
    raw = {}
    p = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    if not os.path.isfile(p):
        return {}
    with open(p, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            t = d.get("t")
            if t is None:
                continue
            for k, v in d.items():
                if k.startswith(GATE_PRE) and v is not None:
                    raw.setdefault(k[len(GATE_PRE):], []).append((float(t), float(v)))
    out = {}
    for i, inst in enumerate(sorted(raw)):
        a = np.asarray(sorted(raw[inst]), dtype=float)
        out[i] = (a[:, 0], a[:, 1], inst)
    return out


def lock_time(ts, gs):
    """First instant the gate is at deepresearch's 100 ms and stays there.

    "Stays" rather than "reaches" because the gauge dips whenever a chat request
    is momentarily resident, and a single sample is not the arrangement.  A
    crossing counts if the median of the following LOCK_WIN_S is also >= LOCK_MS.
    """
    over = gs >= LOCK_MS
    for i in np.flatnonzero(over):
        w = (ts >= ts[i]) & (ts < ts[i] + LOCK_WIN_S)
        if w.sum() >= 5 and float(np.median(gs[w])) >= LOCK_MS:
            return float(ts[i])
    return np.nan


def request_frame(run_dir):
    """One row per request: class, hold, instance index, outcome, send time."""
    r = load_run(run_dir)
    if r is None or r.empty:
        return None
    r = r.copy()
    r["call_index"] = pd.to_numeric(r["call_index"], errors="coerce")
    ids = []
    p = os.path.join(run_dir, "request_ids.jsonl")
    if os.path.isfile(p):
        with open(p, errors="replace") as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                ids.append((d.get("task_id"), d.get("call_index"),
                            str(d.get("request_id", "")).replace("cmpl-", ""),
                            d.get("start_time")))
    if not ids:
        return None
    idf = pd.DataFrame(ids, columns=["task_id", "call_index", "rid", "sent"])
    idf["call_index"] = pd.to_numeric(idf["call_index"], errors="coerce")
    year = datetime.datetime.fromtimestamp(
        float(r["start_time"].min()), datetime.timezone.utc).year
    disp = dispatch_pairs(run_dir, year)
    order = {inst: i for i, inst in enumerate(
        sorted({v[1] for v in disp.values()}))}
    idf["disp_t"] = [disp[x][0] if x in disp else np.nan for x in idf["rid"]]
    idf["instance"] = [order.get(disp[x][1]) if x in disp else None
                       for x in idf["rid"]]
    idf["hold_ms"] = (idf["disp_t"] - pd.to_numeric(idf["sent"],
                                                    errors="coerce")) * 1000.0
    m = r.merge(idf[["task_id", "call_index", "hold_ms", "instance", "disp_t"]],
                on=["task_id", "call_index"], how="left")
    ref = m["rejected"]
    m.loc[ref, "hold_ms"] = pd.to_numeric(m.loc[ref, "latency"],
                                          errors="coerce") * 1000.0
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        RESULTS, "aggregate_analysis", "tail_2026-08-16"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    rows, before, locks = [], [], []
    for d, cell, rep, rpm in run_dirs():
        print("reading", os.path.basename(d), file=sys.stderr)
        m = request_frame(d)
        gs = gate_series(d)
        if m is None or not gs:
            continue
        lk = {i: lock_time(t, g) for i, (t, g, _) in gs.items()}
        for i, v in lk.items():
            locks.append(dict(cell=cell, rep=rep, rpm=rpm, inst=i,
                              locked=bool(v == v),
                              lock_rel=(v - float(m["disp_t"].min())
                                        if v == v else np.nan)))
        placed = m[m["instance"].notna() & m["hold_ms"].notna()].copy()
        placed["instance"] = placed["instance"].astype(int)
        for i in sorted(placed["instance"].unique()):
            g = placed[placed["instance"] == i]
            dr = g[g["class"] == "deepresearch"]
            long_ = g[g["hold_ms"] > CEILING_MS]
            rows.append(dict(
                cell=cell, rep=rep, rpm=rpm, inst=i,
                locked=bool(lk.get(i, np.nan) == lk.get(i, np.nan)),
                placed=len(g), dr_placed=len(dr),
                dr_share_of_inst=100.0 * len(dr) / len(g) if len(g) else np.nan,
                over_ceiling=len(long_),
                pct_of_inst_over_ceiling=100.0 * len(long_) / len(g) if len(g) else np.nan,
                pct_of_dr_over_ceiling=(100.0 * (dr["hold_ms"] > CEILING_MS).mean()
                                        if len(dr) else np.nan),
                dr_share_of_over_ceiling=(100.0 * (long_["class"] == "deepresearch").mean()
                                          if len(long_) else np.nan),
                hold_p90_ms=float(g["hold_ms"].quantile(0.90)),
                dr_hold_p90_ms=float(dr["hold_ms"].quantile(0.90)) if len(dr) else np.nan))
        # What was placed on an instance in the minute before its gate locked.
        for i, v in lk.items():
            if v != v:
                continue
            g = placed[(placed["instance"] == i) & (placed["disp_t"] < v)
                       & (placed["disp_t"] >= v - LOCK_WIN_S)]
            if not len(g):
                continue
            row = dict(cell=cell, rep=rep, rpm=rpm, inst=i, n=len(g),
                       over_ceiling_pct=100.0 * float((g["hold_ms"] > CEILING_MS).mean()),
                       hold_p50_ms=float(g["hold_ms"].median()))
            for c in CLASSES:
                row[c] = 100.0 * float((g["class"] == c).mean())
            before.append(row)

    df = pd.DataFrame(rows)
    bf = pd.DataFrame(before)
    lf = pd.DataFrame(locks)
    df.to_csv(os.path.join(a.out, "22_ceiling_per_instance.csv"), index=False)
    bf.to_csv(os.path.join(a.out, "22_ceiling_before_lock.csv"), index=False)
    lf.to_csv(os.path.join(a.out, "22_ceiling_locks.csv"), index=False)

    L = []
    P = L.append
    P("# 22 — Why a ceiling that cuts 0.6-3.2% of arrivals decides the fleet arrangement")
    P("")
    P("**2026-08-17. Written from data that was on disk before EXP-84 ran, plus EXP-84's own A1 "
      "runs. No cluster command was run; only new files were created.** Script: "
      "`analysis_scripts/request_level/tail2026_ceiling_mechanism.py`. Tables: `22_*.csv`.")
    P("")
    P("EXP-84 refuted the prediction recorded in `experiments/EXP-84_gateway-two-by-two.md` §3. "
      "That prediction rested on a bound: the 5,000 ms ceiling could only touch the 0.6-3.2% of "
      "arrivals that were held longer than that in the control, so it could not be carrying an "
      "effect of 10-19 points. **The bound is correct about which requests the ceiling touches "
      "and wrong to assume the effect is proportional to their number.** This measures whether "
      "those requests are the ones that build the deepresearch-only instance.")
    P("")
    P(f"`locked` means that instance's `scheduler_fluidserve_gate_allowance_ms` reached "
      f"deepresearch's 100 ms and stayed there — the median of the following {LOCK_WIN_S:.0f} s "
      f"is also at or above {LOCK_MS:.0f} ms. A single sample is not the arrangement, because the "
      f"gauge dips whenever a chat request is momentarily resident.")
    P("")

    P("## 1. Placements held past the ceiling, per instance")
    P("")
    P("`over ceiling` counts the requests placed on that instance after a hold longer than "
      f"{CEILING_MS:,.0f} ms — the placements a 5,000 ms ceiling would have removed.")
    P("")
    P("| cell | req/s | inst | locked | placed | deepresearch % of it | **over ceiling** | **as % of its placements** | deepresearch % of those | hold p90 ms |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for (cell, rpm), g0 in df.groupby(["cell", "rpm"]):
        for i, g in g0.groupby("inst"):
            lk = "**yes**" if g["locked"].any() else "no"
            def b(c, f="%.1f"):
                v = pd.to_numeric(g[c], errors="coerce").dropna()
                if not len(v):
                    return "--"
                if len(v) == 1 or abs(v.max() - v.min()) < 1e-9:
                    return f % v.iloc[0]
                return (f + ".." + f) % (v.min(), v.max())
            P(f"| {CELL[cell]} | {rpm // 60} | {i} | {lk} | {b('placed', '%.0f')} | "
              f"{b('dr_share_of_inst')} | **{b('over_ceiling', '%.0f')}** | "
              f"**{b('pct_of_inst_over_ceiling')}** | {b('dr_share_of_over_ceiling')} | "
              f"{b('hold_p90_ms', '%.0f')} |")
    P("")

    P("## 2. The same thing as a concentration, which is the decisive form")
    P("")
    P("For each run: the share of all past-the-ceiling placements that landed on the instance "
      "that locked, against that instance's share of all placements. Equal shares mean the "
      "ceiling is not selecting the requests that build the state.")
    P("")
    P("| cell | req/s | rep | locked inst | its share of all placements | **its share of past-the-ceiling placements** | ratio |")
    P("|---|---|---|---|---|---|---|")
    conc = []
    for (cell, rpm, rep), g in df.groupby(["cell", "rpm", "rep"]):
        lockrows = g[g["locked"]]
        if not len(lockrows) or g["over_ceiling"].sum() == 0:
            continue
        i = int(lockrows["inst"].iloc[0])
        sp = 100.0 * lockrows["placed"].sum() / g["placed"].sum()
        so = 100.0 * lockrows["over_ceiling"].sum() / g["over_ceiling"].sum()
        conc.append(dict(cell=cell, rpm=rpm, rep=rep, inst=i, share_placed=sp,
                         share_over=so, ratio=so / sp if sp else np.nan))
        P(f"| {CELL[cell]} | {rpm // 60} | {rep} | {i} | {sp:.1f}% | **{so:.1f}%** | "
          f"**{so / sp:.2f}x** |")
    pd.DataFrame(conc).to_csv(os.path.join(a.out, "22_ceiling_concentration.csv"),
                              index=False)
    P("")

    P("## 3. What was placed on the instance in the minute before its gate locked")
    P("")
    P("| cell | req/s | rep | inst | placements | " + " | ".join(CLASSES)
      + " | held past ceiling | hold p50 ms |")
    P("|---|---|---|---|---|" + "---|" * (len(CLASSES) + 2))
    for _, r in bf.iterrows():
        P(f"| {CELL[r['cell']]} | {r['rpm'] // 60} | {r['rep']} | {r['inst']} | "
          f"{r['n']:.0f} | " + " | ".join(f"{r[c]:.1f}" for c in CLASSES)
          + f" | {r['over_ceiling_pct']:.1f}% | {r['hold_p50_ms']:.0f} |")
    P("")

    P("## 4. When each instance locked, in seconds from the first placement of the run")
    P("")
    P("| cell | req/s | rep | " + " | ".join(f"inst {i}" for i in range(4)) + " |")
    P("|---|---|---|" + "---|" * 4)
    for (cell, rpm, rep), g in lf.groupby(["cell", "rpm", "rep"]):
        cells = []
        for i in range(4):
            gg = g[g["inst"] == i]
            if not len(gg) or not bool(gg["locked"].iloc[0]):
                cells.append("—")
            else:
                cells.append(f"**{gg['lock_rel'].iloc[0]:.0f} s**")
        P(f"| {CELL[cell]} | {rpm // 60} | {rep} | " + " | ".join(cells) + " |")
    P("")

    out = os.path.join(a.out, "22_ceiling_mechanism.md")
    with open(out, "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    print("\nwrote " + out, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
