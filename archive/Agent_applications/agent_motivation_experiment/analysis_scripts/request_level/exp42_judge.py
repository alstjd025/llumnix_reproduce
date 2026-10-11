#!/usr/bin/env python3
"""EXP-42: judge candidate A against the conditions written before the run.

The five refutation conditions are in `experiments/EXP-42_force-margin.md` §2 and
are checked here in the order they are written there, because the order matters:
a change that never fired is a void run rather than a negative result, and a
change that moved the pace without moving the score is a different finding from
one that moved neither.

Everything is printed with both denominators and with the rejection rate beside
them, because the admitted view on its own rewards a policy for refusing work
and this change refuses more work on purpose.

  python3 exp42_judge.py --runs 'results/*exp42*'
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    load_run, attain, CLASSES, SLO_RULES,
)

ARMS = {"fsbase": "baseline (shipped)", "fsa": "candidate A (force margin)"}
# From EXP-38, two repeats, the numbers the conditions are written against.
EXP38 = {15: 100.0, 30: 100.0, 45: 88.5, 60: 35.2}
EXP38_GOODPUT_60 = 12359.0
EXP38_ITL_60 = 49.9


def decisions(run):
    """route/pend/shed/force counts over the steady window, from the scraper."""
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


def collect(patterns):
    rows = []
    for pat in patterns:
        for d in sorted(glob.glob(pat)):
            m = re.search(r"exp42r(\d)_(fsbase|fsa)_m1_rpm_(\d+)", os.path.basename(d))
            if not m:
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            span = r["rel"].max() - r["rel"].min()
            ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
            adm = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])]
            ch = adm[adm["class"] == "chat"]
            tok = lambda g: pd.to_numeric(g.get("output_tokens"), errors="coerce").fillna(0).sum()
            rec = dict(
                rep=int(m.group(1)), arm=m.group(2), rate=int(m.group(3)) / 60.0,
                off=attain(r, "violate_offered"), adm=attain(r, "violate_served"),
                rej=100.0 * r["rejected"].mean(),
                met_s=len(ok) / span, goodput=tok(ok) / span, total=tok(adm) / span,
                chat_itl=pd.to_numeric(ch["itl_ms"], errors="coerce").median(),
                run=os.path.basename(d),
            )
            for cl in CLASSES:
                rec[f"{cl}_off"] = attain(r[r["class"] == cl], "violate_offered")
            rec.update({f"dec_{k}": v for k, v in decisions(d).items()})
            rows.append(rec)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", default=["results/*exp42*"])
    a = ap.parse_args()
    df = collect(a.runs)
    if df.empty:
        sys.exit("no EXP-42 runs matched")
    print(f"{len(df)} conditions, arms {sorted(set(df.arm))}, "
          f"reps {sorted(set(df.rep))}, rates {sorted(set(df.rate))}\n")

    g = df.groupby(["arm", "rate"])
    print("per condition (all repeats shown; a single repeat is not a judgement)")
    print(f"{'arm':>7} {'rate':>5} {'rep':>4} {'off':>6} {'adm':>6} {'rej%':>6} {'met/s':>7} "
          f"{'goodput':>8} {'chatITL':>8} | {'route%':>7} {'pend%':>7} {'shed%':>7} {'force%':>7}")
    for _, r in df.sort_values(["rate", "arm", "rep"]).iterrows():
        print(f"{r['arm']:>7} {r['rate']:>5.0f} {r['rep']:>4} {r['off']:>6.1f} {r['adm']:>6.1f} "
              f"{r['rej']:>6.1f} {r['met_s']:>7.2f} {r['goodput']:>8.0f} {r['chat_itl']:>8.1f} | "
              + " ".join(f"{r.get('dec_'+k, float('nan')):>7.1f}" for k in
                         ("route", "pend", "shed", "force")))

    print("\nmean over repeats, and the difference that is the claim")
    print(f"{'rate':>5} {'base off':>9} {'A off':>7} {'diff':>7} | {'base ITL':>9} {'A ITL':>7} "
          f"{'diff':>7} | {'base gp':>8} {'A gp':>8} | {'base rej':>9} {'A rej':>7}")
    rates = sorted(df["rate"].unique())
    for rt in rates:
        b = df[(df.arm == "fsbase") & (df.rate == rt)]
        t = df[(df.arm == "fsa") & (df.rate == rt)]
        if b.empty or t.empty:
            continue
        print(f"{rt:>5.0f} {b['off'].mean():>9.1f} {t['off'].mean():>7.1f} "
              f"{t['off'].mean()-b['off'].mean():>+7.1f} | "
              f"{b['chat_itl'].mean():>9.1f} {t['chat_itl'].mean():>7.1f} "
              f"{t['chat_itl'].mean()-b['chat_itl'].mean():>+7.1f} | "
              f"{b['goodput'].mean():>8.0f} {t['goodput'].mean():>8.0f} | "
              f"{b['rej'].mean():>9.1f} {t['rej'].mean():>7.1f}")

    print("\nper class, offered denominator")
    print(f"{'rate':>5} {'arm':>7} " + " ".join(f"{c:>14}" for c in CLASSES))
    for rt in rates:
        for arm in ("fsbase", "fsa"):
            s = df[(df.arm == arm) & (df.rate == rt)]
            if s.empty:
                continue
            print(f"{rt:>5.0f} {arm:>7} "
                  + " ".join(f"{s[c+'_off'].mean():>14.1f}" for c in CLASSES))

    # ---- the pre-registered conditions, in the order they were written ----
    print("\n" + "=" * 72)
    print("refutation conditions (EXP-42 §2), in order")
    print("=" * 72)
    b60 = df[(df.arm == "fsbase") & (df.rate == 60)]
    t60 = df[(df.arm == "fsa") & (df.rate == 60)]
    if b60.empty or t60.empty:
        print("  60 req/s not complete on both arms yet; nothing to judge.")
        return

    def verdict(ok, text):
        print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
        return ok

    shed_b, shed_t = b60["dec_shed"].mean(), t60["dec_shed"].mean()
    c1 = verdict(shed_t > shed_b + 1.0,
                 f"1. mechanism fired: shed share at 60 req/s {shed_b:.1f}% -> {shed_t:.1f}%"
                 f"  (if not, the run is VOID, not negative)")
    itl_t = t60["chat_itl"].mean()
    c2 = verdict(itl_t < 47.0,
                 f"2. chat median inter-token latency at 60 req/s {b60['chat_itl'].mean():.1f} "
                 f"-> {itl_t:.1f} ms, target < 47.0")
    off_t = t60["off"].mean()
    c3 = verdict(off_t > 45.0,
                 f"3. offered attainment at 60 req/s {b60['off'].mean():.1f} -> {off_t:.1f}, "
                 f"target > 45.0")
    # Condition 4 was written against EXP-38's numbers, but those are from
    # another session and CLAUDE.md forbids reading a difference across one.
    # Both are printed: the pre-registered floor because it was pre-registered,
    # and the within-session difference because that is the valid comparison.
    # At 45 req/s they can disagree sharply -- that condition is bistable, and
    # this session's baseline routed 90.6% of decisions where EXP-38's routed
    # 11.2%, which is a different operating regime rather than a small offset.
    ok4 = True
    for rt, floor in ((45.0, 84.3), (30.0, 95.8), (15.0, 95.8)):
        s = df[(df.arm == "fsa") & (df.rate == rt)]
        bb = df[(df.arm == "fsbase") & (df.rate == rt)]
        if s.empty:
            continue
        within = (s["off"].mean() - bb["off"].mean()) if not bb.empty else float("nan")
        # The spread that matters is this arm's own repeat spread where it is
        # available, and the largest ever measured (4.2) where it is not.
        tol = max(4.2, (bb["off"].max() - bb["off"].min()) if len(bb) > 1 else 0.0)
        ok4 &= (s["off"].mean() >= floor) and (within > -tol or np.isnan(within))
        print(f"       at {rt:.0f} req/s: A {s['off'].mean():.1f} vs EXP-38 floor {floor}"
              f", within-session base {bb['off'].mean() if not bb.empty else float('nan'):.1f}"
              f" -> {within:+.1f} (tolerance -{tol:.1f})")
    c4 = verdict(ok4, "4. no regression at the lower rates")
    gp_t = t60["goodput"].mean()
    c5 = verdict(gp_t > b60["goodput"].mean(),
                 f"5. token goodput at 60 req/s {b60['goodput'].mean():.0f} -> {gp_t:.0f} tok/s")

    print()
    if not c1:
        print("  VERDICT: void. The change did not take effect; check the scheduler's")
        print("  start-up line for forcemargin=true before reading anything else.")
    elif c2 and c3 and c4 and c5:
        print("  VERDICT: accepted on every pre-registered condition.")
    elif c2 and not c3:
        print("  VERDICT: rejected on condition 3, and this is the informative failure.")
        print("  The pace moved and the score did not, which means the pace is not the")
        print("  binding constraint at this load and contradicts §34. Record it as such.")
    else:
        print("  VERDICT: rejected. See which condition failed above.")


if __name__ == "__main__":
    main()
