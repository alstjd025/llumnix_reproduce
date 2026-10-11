#!/usr/bin/env python3
"""The gateway hold-window two-by-two: which of the two values chooses the fleet arrangement.

EXP-83 changed `--wait-scheduling-timeout` from 35,000 to 5,000 ms and
`--wait-scheduling-retry-interval` from 500 to 1,000 ms together, and every
aggregate moved 10 to 19 points.  `19_hold_window_mechanism.md` then found what
differs between the two settings: under 35,000/500 one instance of four is given
over to deepresearch, gated at that class's 100 ms per token instead of chat's
50, filled to roughly twice the KV and running 69-74 ms per iteration; under
5,000/1,000 no instance does that.  Because both values moved at once, neither
can be given the credit.  EXP-84 runs each value on its own and completes the
grid:

    cell     ceiling      retry     experiment
    ----     -------      -----     ----------
    A0       35,000 ms    500 ms    EXP-82   (the deployed baseline)
    A1       35,000 ms  1,000 ms    EXP-84a1 (retry period alone)
    A2        5,000 ms    500 ms    EXP-84a2 (ceiling alone)
    A3        5,000 ms  1,000 ms    EXP-83   (both, the gateway as shipped)

The judgement quantity is the arrangement, not attainment.  Attainment is what
the arrangement causes; asking which cell scored higher would confuse the effect
with the thing that produced it.  The five markers of a deepresearch-only
instance were written down before the run in
`experiments/EXP-84_gateway-two-by-two.md` §4.1 and are each reported separately
here, so a cell that matches some of them but not all is visible as such rather
than being rounded to yes or no.

Nothing is reimplemented: the scoring, the placement join, the per-instance gate
series and the concentration measure are imported from the scripts that produced
`19_*`, so the two documents cannot disagree about what any column means.  The
one thing that cannot be imported is the conversion from pend decisions to
request-seconds held, because that multiplies by the retry period and the
imported version has the two EXP-83-era periods written into it.

    python3 tail2026_gateway_2x2.py [--out <directory>]

Writes `21_gateway_2x2.md` and `21_*.csv`.  Reads only; creates only new files.
"""
import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import CLASSES                                # noqa: E402
from tail2026_holdwindow import (RESULTS, RATES, per_class_rows,     # noqa: E402
                                 counter_row, band)
from tail2026_holdwindow_state import (placement_frame, gate_rows,   # noqa: E402
                                       instance_share_rows, separation_rows)

# cell -> (label, ceiling ms, retry ms, order)
CELL = {
    "82":   ("35,000 / 500",   35000,  500, 0),
    "84a1": ("35,000 / 1,000", 35000, 1000, 1),
    "84a2": ("5,000 / 500",     5000,  500, 2),
    "83":   ("5,000 / 1,000",   5000, 1000, 3),
}
ORDER = sorted(CELL, key=lambda c: CELL[c][3])
NAME_RE = re.compile(r"^\d{6}_\d{4}_exp(?P<cell>82|83|84a1|84a2)r(?P<rep>\d)"
                     r"_fspfx_m1_rpm_(?P<rpm>\d+)$")

# The five markers of a deepresearch-only instance, pre-registered in EXP-84 §4.1
# from the EXP-82 measurement.  Each is reported on its own below; DEDICATED is
# the conjunction, and a cell where the markers disagree is a result in itself.
MARK = {
    "gate_allowance_ms_p50": (">=", 95.0),
    "pct_at_100":            (">=", 50.0),
    "obs_kv_tokens_p50":     (">=", 600000.0),
    "observed_step_ms_p50":  (">=", 60.0),
    "chat":                  ("<",  5.0),
}
JUDGE_RATES = [2100, 2700]   # 35 and 45 req/s; 25 is the negative control


def run_dirs():
    out = []
    for d in glob.glob(os.path.join(RESULTS, "*_fspfx_m1_rpm_*")):
        b = os.path.basename(d)
        m = NAME_RE.match(b)
        if m and os.path.isdir(d) and int(m.group("rpm")) in RATES:
            out.append((d, m.group("cell"), int(m.group("rep")),
                        int(m.group("rpm"))))
    return sorted(out, key=lambda t: (CELL[t[1]][3], t[3], t[2]))


def held_seconds(cell, pend):
    """Request-seconds held at the gateway: one retry period per pend decision.

    This is the only form that puts two different cadences on the same scale --
    a cell that re-asks twice as often accumulates twice the pend count for the
    same amount of waiting.
    """
    return pend * CELL[cell][2] / 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        RESULTS, "aggregate_analysis", "tail_2026-08-16"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    percls, gates, shares, sep, counters, joins = [], [], [], [], [], []
    for d, cell, rep, rpm in run_dirs():
        print("reading", os.path.basename(d), file=sys.stderr)
        pc = per_class_rows(d, cell, rep, rpm)
        percls += pc
        arrivals = next((r["arrivals"] for r in pc if r["cls"] == "ALL"), 0)
        cr = counter_row(d, cell, rep, rpm, arrivals)
        cr["held_request_seconds"] = held_seconds(cell, cr["pend"])
        counters.append(cr)
        m, join = placement_frame(d)
        joins.append(dict(cell=cell, rep=rep, rpm=rpm, join_pct=join))
        if m is not None and len(m):
            shares += instance_share_rows(m, cell, rep, rpm)
            sep += separation_rows(m, cell, rep, rpm)
        gates += gate_rows(d, cell, rep, rpm)

    pc = pd.DataFrame(percls).rename(columns={"exp": "cell"})
    gt = pd.DataFrame(gates).rename(columns={"exp": "cell"})
    sh = pd.DataFrame(shares).rename(columns={"exp": "cell"})
    sp = pd.DataFrame(sep).rename(columns={"exp": "cell"})
    ct = pd.DataFrame(counters).rename(columns={"exp": "cell"})
    jn = pd.DataFrame(joins)
    for name, df in [("perclass", pc), ("instgate", gt), ("instshare", sh),
                     ("separation", sp), ("counters", ct), ("join", jn)]:
        df.to_csv(os.path.join(a.out, "21_gw2x2_%s.csv" % name), index=False)

    # The gate table and the class-composition table are per (cell, rep, rpm,
    # inst) and have to be read together, because "gated at 100 ms" and "has no
    # chat on it" are two measurements of the same state from two sources -- the
    # scheduler's own gauge and the dispatch log.
    inst = gt.merge(sh[["cell", "rep", "rpm", "inst", "chat", "deepresearch",
                        "swe", "n"]],
                    on=["cell", "rep", "rpm", "inst"], how="outer")
    for col, (op, thr) in MARK.items():
        v = pd.to_numeric(inst.get(col), errors="coerce")
        inst["m_" + col] = (v >= thr) if op == ">=" else (v < thr)
    inst["dedicated"] = inst[["m_" + c for c in MARK]].all(axis=1)
    inst["markers_met"] = inst[["m_" + c for c in MARK]].sum(axis=1)
    inst.to_csv(os.path.join(a.out, "21_gw2x2_instances.csv"), index=False)

    L = []
    P = L.append
    P("# 21 — The gateway hold-window two-by-two: which value chooses the fleet arrangement")
    P("")
    P("**2026-08-17. EXP-84 filled the two missing cells; EXP-82 and EXP-83 are the two that "
      "already existed.** Script: `analysis_scripts/request_level/tail2026_gateway_2x2.py`. "
      "Tables: `21_gw2x2_*.csv`. Plan and pre-registered judgement rules: "
      "`experiments/EXP-84_gateway-two-by-two.md`.")
    P("")
    P("| cell | ceiling | retry | experiment | runs found |")
    P("|---|---|---|---|---|")
    for c in ORDER:
        lab, ceil, retry, _ = CELL[c]
        n = len(pc[(pc.cell == c) & (pc.cls == "ALL")])
        P(f"| {lab} | {ceil:,} ms | {retry:,} ms | "
          f"{'EXP-84' + c[2:] if c.startswith('84') else 'EXP-' + c} | {n} |")
    P("")
    P("Every number is reported as min..max over the repeats of that cell and arrival rate. "
      "**A difference smaller than that band is not a difference.** Two repeats per cell.")
    P("")
    jb = jn["join_pct"].dropna()
    if len(jb):
        P(f"**Join quality.** The per-request instance attribution joins the client's request ids "
          f"to the scheduler's dispatch log; the join rate onto placed requests is "
          f"{jb.min():.1f}..{jb.max():.1f}% across all {len(jn)} runs, so no conclusion here "
          f"rests on assuming the missing rows resemble the present ones.")
        P("")

    # ---------------------------------------------------------------- 1
    P("## 1. The judgement: did an instance become deepresearch-only?")
    P("")
    P("The five markers, each measured separately, at the two arrival rates the judgement was "
      "pre-registered on (35 and 45 req/s). `dedicated` requires all five.")
    P("")
    P("| cell | req/s | instances judged | **dedicated** | gate p50 >= 95 ms | at >=100 ms for >=50% | KV >= 600k | step >= 60 ms | chat < 5% |")
    P("|---|---|---|---|---|---|---|---|---|")
    jrows = []
    for c in ORDER:
        for rpm in JUDGE_RATES:
            g = inst[(inst.cell == c) & (inst.rpm == rpm)]
            if not len(g):
                continue
            row = dict(cell=c, setting=CELL[c][0], rpm=rpm, n_inst=len(g),
                       dedicated=int(g["dedicated"].sum()))
            for k in MARK:
                row["mark_" + k] = int(g["m_" + k].sum())
            jrows.append(row)
            P(f"| {CELL[c][0]} | {rpm // 60} | {len(g)} | **{row['dedicated']}** | "
              + " | ".join(str(row["mark_" + k]) for k in MARK) + " |")
    pd.DataFrame(jrows).to_csv(os.path.join(a.out, "21_gw2x2_judgement.csv"), index=False)
    P("")
    P("Counts are instances, four per run and two runs per cell and rate, so **8 is every "
      "instance and 2 is one instance in each of the two repeats.**")
    P("")

    # ---------------------------------------------------------------- 2
    P("## 2. The same thing per instance, so a partial state is visible")
    P("")
    P("| cell | req/s | inst | gate p50 | % at >=100 ms | step p50 | observed KV | chat % | deepresearch % | markers met |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for c in ORDER:
        for rpm in RATES:
            for i in range(4):
                g = inst[(inst.cell == c) & (inst.rpm == rpm) & (inst.inst == i)]
                if not len(g):
                    continue
                mk = band(g, "markers_met", "%.0f")
                flag = " **←**" if g["dedicated"].any() else ""
                P(f"| {CELL[c][0]} | {rpm // 60} | {i} | "
                  f"{band(g, 'gate_allowance_ms_p50')} | {band(g, 'pct_at_100')} | "
                  f"{band(g, 'observed_step_ms_p50')} | "
                  f"{band(g, 'obs_kv_tokens_p50', '%.0f')} | {band(g, 'chat')} | "
                  f"{band(g, 'deepresearch')} | {mk}{flag} |")
    P("")

    # ---------------------------------------------------------------- 3
    P("## 3. Instances a class is effectively spread over, per 60 s window")
    P("")
    P("`N_eff = 1/sum(share^2)`: 4.0 is an even spread over four instances, 1.0 is all on one, "
      "2.0 is half each on two. ⚠ In EXP-82 and EXP-83 this measure did **not** separate the two "
      "settings for deepresearch (3.46..3.48 against 3.47..3.68) — deepresearch is spread under "
      "both, and what differs is whether one instance is **exclusively** deepresearch. It is "
      "reported because it is the measure earlier records quote, not because it is the judgement.")
    P("")
    P("| cell | req/s | " + " | ".join(CLASSES) + " |")
    P("|---|---|" + "---|" * len(CLASSES))
    for c in ORDER:
        for rpm in RATES:
            cells = []
            for cl in CLASSES:
                g = sp[(sp.cell == c) & (sp.rpm == rpm) & (sp.cls == cl)]
                cells.append(band(g, "n_eff_p50", "%.2f") if len(g) else "--")
            if all(x == "--" for x in cells):
                continue
            P(f"| {CELL[c][0]} | {rpm // 60} | " + " | ".join(cells) + " |")
    P("")

    # ---------------------------------------------------------------- 4
    P("## 4. What the gateway and the policy counted")
    P("")
    P("`gave up` is the gateway's own count of requests cut off by the ceiling; it is the direct "
      "test of whether the ceiling binds at all. `held_s` is pend decisions times that cell's "
      "retry period, the one form that puts different cadences on the same scale.")
    P("")
    P("| cell | req/s | placed | force | pend | shed | **gave up** | calls/arrival | held_s | mean wait ms |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for c in ORDER:
        for rpm in RATES:
            g = ct[(ct.cell == c) & (ct.rpm == rpm)]
            if not len(g):
                continue
            P(f"| {CELL[c][0]} | {rpm // 60} | {band(g, 'placed', '%.0f')} | "
              f"{band(g, 'force', '%.0f')} | {band(g, 'pend', '%.0f')} | "
              f"{band(g, 'shed', '%.0f')} | **{band(g, 'gave_up', '%.0f')}** | "
              f"{band(g, 'calls_per_arrival', '%.2f')} | "
              f"{band(g, 'held_request_seconds', '%.0f')} | "
              f"{band(g, 'mean_wait_ms', '%.0f')} |")
    P("")

    # ---------------------------------------------------------------- 5
    P("## 5. What it did to the requests — per class, both denominators")
    P("")
    P("⚠ **The admitted column cannot be read without the rejection rate beside it**, and both "
      "are printed for that reason: in EXP-83 deepresearch's admitted attainment rose to "
      "99.9..100.0 precisely because its rejection rate rose to 67.8..68.5%.")
    P("")
    for rpm in RATES:
        P(f"### {rpm // 60} req/s")
        P("")
        P("| class | cell | arrivals | rej % | offered % | admitted % | ttft p50 | ttft p90 | e2e p90 | goodput tok/s |")
        P("|---|---|---|---|---|---|---|---|---|---|")
        for cl in CLASSES + ["ALL"]:
            for c in ORDER:
                g = pc[(pc.cell == c) & (pc.rpm == rpm) & (pc.cls == cl)]
                if not len(g):
                    continue
                P(f"| {cl} | {CELL[c][0]} | {band(g, 'arrivals', '%.0f')} | "
                  f"{band(g, 'rejected_pct')} | **{band(g, 'attain_offered')}** | "
                  f"{band(g, 'attain_admitted')} | {band(g, 'ttft_p50', '%.0f')} | "
                  f"{band(g, 'ttft_p90', '%.0f')} | {band(g, 'e2e_p90', '%.1f')} | "
                  f"{band(g, 'goodput_tok_per_s', '%.0f')} |")
        P("")

    out = os.path.join(a.out, "21_gateway_2x2.md")
    with open(out, "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))
    print("\nwrote " + out, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
