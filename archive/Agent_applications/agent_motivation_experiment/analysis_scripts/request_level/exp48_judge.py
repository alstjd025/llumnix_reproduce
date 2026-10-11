#!/usr/bin/env python3
"""EXP-48 and EXP-49: judge a static sweep against the conditions written before it.

EXP-48 corrects the deep research length profile and EXP-49 replaces the modelled
KV projection with the instance's observed rate of change. Both are judged on the
same static gate, so one script serves them: the arm names and the recorded
baselines are the only thing that differs, and both are arguments.

The baselines EXP-48 is read against come from earlier sessions, which the
2026-08-01 rule allows provided each quantity is read against its own repeat
spread. They are printed with that spread beside them so a difference smaller
than it is visibly not a difference.

  python3 exp48_judge.py --runs 'results/*exp48*' --tag exp48
  python3 exp48_judge.py --runs 'results/*exp49*' --tag exp49
"""
import argparse
import glob
import json
import os
import re
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, attain, CLASSES  # noqa: E402

# The shipped policy with the 2026-07-26 profile, from the runs named beside each
# figure. spread is max - min over the repeats listed.
BASELINE = {
    45: dict(off=[99.1, 88.9], src="EXP-42 fsbase, two repeats"),
    60: dict(off=[35.2, 36.1], goodput=[12359, 12401, 12638], itl=[49.9, 49.9],
             src="EXP-38 and EXP-42 fsbase"),
}
GATE_DROP = 4.2   # EXP-38's largest measured repeat spread on this workload


def decisions(run):
    """route/pend/shed/force shares over the steady window, from the scraper."""
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
            m = re.match(r"scheduler_fluidserve_infeasible_total\|reason=(\w+)", k)
            if m:
                rec["nf_" + m.group(1)] = v
        rows.append(rec)
    if len(rows) < 5:
        return {}
    d = pd.DataFrame(rows).fillna(0)
    d["t"] -= d["t"].min()
    d = d[(d["t"] > 120) & (d["t"] < d["t"].max() - 60)]
    if len(d) < 3:
        return {}
    out = {}
    cols = [c for c in ("route", "pend", "shed", "force") if c in d.columns]
    tot = {c: float(d[c].iloc[-1] - d[c].iloc[0]) for c in cols}
    n = max(sum(tot.values()), 1.0)
    for c in ("route", "pend", "shed", "force"):
        out[c] = 100.0 * tot.get(c, 0.0) / n
    # Which term of `feasible` refused placements, when the binary carries the
    # counter. Shares of all refusals, and they can sum above 100 because two
    # conditions can fail on the same candidate.
    nf = {c[3:]: float(d[c].iloc[-1] - d[c].iloc[0])
          for c in d.columns if c.startswith("nf_")}
    tot_nf = max(sum(nf.values()), 1.0)
    for k, v in nf.items():
        out["nf_" + k] = 100.0 * v / tot_nf
    return out


def collect(patterns, tag):
    pat = re.compile(rf"{tag}r(\d)_(\w+?)_m1_rpm_(\d+)")
    rows = []
    for p in patterns:
        for d in sorted(glob.glob(p)):
            m = pat.search(os.path.basename(d))
            if not m:
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            span = r["rel"].max() - r["rel"].min()
            ok = r[(~r["violate_offered"]) & (~r["cutoff"])]
            adm = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])]
            ch = adm[adm["class"] == "chat"]

            def tok(g):
                return pd.to_numeric(g.get("output_tokens"),
                                     errors="coerce").fillna(0).sum()

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
            rec.update(decisions(d))
            rows.append(rec)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", default=["results/*exp48*"])
    ap.add_argument("--tag", default="exp48")
    a = ap.parse_args()
    df = collect(a.runs, a.tag)
    if df.empty:
        sys.exit(f"no {a.tag} conditions matched {a.runs}")

    print(f"{len(df)} conditions, arms {sorted(set(df.arm))}, "
          f"reps {sorted(set(df.rep))}, rates {sorted(set(df.rate))}\n")

    print("per condition -- every repeat shown, because one repeat is not a judgement")
    print(f"{'arm':>12} {'rate':>5} {'rep':>4} {'off':>6} {'adm':>6} {'rej%':>6} "
          f"{'met/s':>7} {'goodput':>8} {'chatITL':>8} | "
          f"{'route%':>7} {'pend%':>7} {'shed%':>7} {'force%':>7}")
    for _, r in df.sort_values(["rate", "arm", "rep"]).iterrows():
        print(f"{r['arm']:>12} {r['rate']:>5.0f} {r['rep']:>4} {r['off']:>6.1f} "
              f"{r['adm']:>6.1f} {r['rej']:>6.1f} {r['met_s']:>7.2f} "
              f"{r['goodput']:>8.0f} {r['chat_itl']:>8.1f} | "
              + " ".join(f"{r.get(k, float('nan')):>7.1f}"
                         for k in ("route", "pend", "shed", "force")))

    nfcols = [c for c in df.columns if c.startswith("nf_")]
    if nfcols:
        print("\nwhich term of `feasible` refused the placement (% of refusals; "
              "sums above 100 when two fail together)")
        print(f"{'arm':>12} {'rate':>5} {'rep':>4} "
              + " ".join(f"{c[3:]:>13}" for c in nfcols))
        for _, r in df.sort_values(["rate", "arm", "rep"]).iterrows():
            print(f"{r['arm']:>12} {r['rate']:>5.0f} {r['rep']:>4} "
                  + " ".join(f"{r.get(c, float('nan')):>13.1f}" for c in nfcols))

    print("\nper class, offered denominator")
    print(f"{'arm':>12} {'rate':>5} " + " ".join(f"{c:>14}" for c in CLASSES))
    for rt in sorted(df["rate"].unique()):
        for arm in sorted(df["arm"].unique()):
            s = df[(df.arm == arm) & (df.rate == rt)]
            if s.empty:
                continue
            print(f"{arm:>12} {rt:>5.0f} "
                  + " ".join(f"{s[c+'_off'].mean():>14.1f}" for c in CLASSES))

    print("\n" + "=" * 74)
    print("the pre-registered static gate")
    print("=" * 74)
    for rt in sorted(df["rate"].unique()):
        base = BASELINE.get(int(rt))
        s = df[df.rate == rt]
        if base is None:
            print(f"  {rt:.0f} req/s: no recorded baseline")
            continue
        b = base["off"]
        spread = max(b) - min(b)
        got = s["off"].mean()
        line = (f"  {rt:.0f} req/s offered: {got:.1f} against "
                f"{sum(b)/len(b):.1f} ({', '.join(f'{x:.1f}' for x in b)}, "
                f"spread {spread:.1f}) from {base['src']}")
        if int(rt) == 60:
            ok = got >= sum(b) / len(b) - GATE_DROP
            print(f"  [{'PASS' if ok else 'FAIL'}]{line[2:]}")
            print(f"         gate is a fall of no more than {GATE_DROP} points")
        else:
            print(f"  [ report ]{line[2:]}")
            print(f"         not gated: the two recorded repeats differ by "
                  f"{spread:.1f} points, so nothing is readable at this rate")

    s60 = df[df.rate == 60]
    if not s60.empty and 60 in BASELINE:
        b = BASELINE[60]
        gp, rej = s60["goodput"].mean(), s60["rej"].mean()
        gpb = sum(b["goodput"]) / len(b["goodput"])
        print(f"\n  rejection {rej:.1f}%, token goodput {gp:.0f} against "
              f"{gpb:.0f} (spread {max(b['goodput'])-min(b['goodput']):.0f})")
        print(f"  [{'FAIL' if (rej > 40 and gp < gpb) else 'PASS'}] "
              f"rejection must not rise while goodput does not")
    return 0


if __name__ == "__main__":
    sys.exit(main())
