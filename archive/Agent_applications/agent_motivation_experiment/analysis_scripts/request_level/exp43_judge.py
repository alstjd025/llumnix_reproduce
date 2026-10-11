#!/usr/bin/env python3
"""EXP-43: judge candidate E, the class term in the damage estimate.

Checks the five conditions in `experiments/EXP-43_class-harm.md` §2 in the order
they were written. Two of them need quantities the standard tables do not carry:

  separation index   the mean over engines of the Herfindahl index of that
                     engine's class mix, sum over classes of (share)^2. A fleet
                     where every engine holds the same mixture reads 1/3; an
                     engine holding one class reads 1. This is what the term is
                     supposed to move, and it is measured from the scheduler's
                     own dispatch log joined to the client ids rather than from
                     the prefix-cache hit rate, which EXP-41 §6.2 showed does
                     not read out routing on this workload.
  preemptions        zero in every static condition of EXP-38 and EXP-40. This
                     term concentrates a class onto fewer engines, and EXP-41
                     measured 5,513 preemptions on the engine that collected deep
                     research once the concentration formed under a moving load.
                     A non-zero count at a static rate is a result whatever
                     happens to the score.

  python3 exp43_judge.py --runs 'results/*exp43*'
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
from exp22_fluidserve import load_run, attain, CLASSES  # noqa: E402
from exp41_engine_view import attribute_engines  # noqa: E402

ARMS = {"fsa": "candidate A only", "fsah": "A + class harm"}


def separation(run, r):
    """Mean over engines of the Herfindahl index of its class mix."""
    if not os.path.exists(os.path.join(run, "analysis", "request_engine.csv")):
        return np.nan
    try:
        j, _ = attribute_engines(run, r)
    except Exception:
        return np.nan
    if j.empty:
        return np.nan
    hs = []
    for p in sorted(j["engine_port"].unique()):
        e = j[j["engine_port"] == p]
        if len(e) < 50:
            continue
        hs.append(sum(((e["class"] == c).mean()) ** 2 for c in CLASSES))
    return float(np.mean(hs)) if hs else np.nan


def engine_side(run):
    """Preemptions summed over engines, and the busiest/least batch ratio."""
    pre, batches = 0.0, []
    for f in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        t, p, b = [], [], []
        for line in open(f):
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if not o.get("ok"):
                continue

            def pick(pref):
                for k, v in o.items():
                    if k.startswith(pref) and isinstance(v, (int, float)):
                        return float(v)
                return np.nan
            t.append(o["t"]); p.append(pick("vllm:num_preemptions_total"))
            b.append(pick("vllm:num_requests_running"))
        if len(t) < 10:
            continue
        t = np.array(t) - t[0]
        m = (t > 120) & (t < t.max() - 60)
        if m.sum() < 5:
            continue
        pre += float(np.nanmax(np.array(p)[m]) - np.nanmin(np.array(p)[m]))
        batches.append(float(np.nanmean(np.array(b)[m])))
    imb = (max(batches) / min(batches)) if batches and min(batches) > 0 else np.nan
    return pre, imb


def decisions(run):
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
            m = re.search(r"exp43r(\d)_(fsa|fsah)_m1_rpm_(\d+)", os.path.basename(d))
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
            pre, imb = engine_side(d)
            rec = dict(
                rep=int(m.group(1)), arm=m.group(2), rate=int(m.group(3)) / 60.0,
                off=attain(r, "violate_offered"), adm=attain(r, "violate_served"),
                rej=100.0 * r["rejected"].mean(), met_s=len(ok) / span,
                goodput=tok(ok) / span,
                chat_itl=pd.to_numeric(ch["itl_ms"], errors="coerce").median(),
                sep=separation(d, r), preempt=pre, imbalance=imb,
            )
            for cl in CLASSES:
                rec[f"{cl}_off"] = attain(r[r["class"] == cl], "violate_offered")
            rec.update({f"dec_{k}": v for k, v in decisions(d).items()})
            rows.append(rec)
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", default=["results/*exp43*"])
    a = ap.parse_args()
    df = collect(a.runs)
    if df.empty:
        sys.exit("no EXP-43 runs matched")
    print(f"{len(df)} conditions, arms {sorted(set(df.arm))}, reps {sorted(set(df.rep))}\n")

    print("per condition")
    print(f"{'arm':>5} {'rate':>5} {'rep':>4} {'off':>6} {'adm':>6} {'rej%':>6} {'gp':>7} "
          f"{'chatITL':>8} {'sep':>6} {'preempt':>8} {'imbal':>6} | "
          f"{'route%':>7} {'pend%':>7} {'shed%':>7} {'force%':>7}")
    for _, r in df.sort_values(["rate", "arm", "rep"]).iterrows():
        print(f"{r['arm']:>5} {r['rate']:>5.0f} {r['rep']:>4} {r['off']:>6.1f} {r['adm']:>6.1f} "
              f"{r['rej']:>6.1f} {r['goodput']:>7.0f} {r['chat_itl']:>8.1f} {r['sep']:>6.3f} "
              f"{r['preempt']:>8.0f} {r['imbalance']:>6.2f} | "
              + " ".join(f"{r.get('dec_'+k, float('nan')):>7.1f}" for k in
                         ("route", "pend", "shed", "force")))

    print("\nmean over repeats: A alone -> A + class harm")
    print(f"{'rate':>5} {'off':>18} {'sep':>16} {'chatITL':>16} {'goodput':>18} {'preempt':>14}")
    rates = sorted(df["rate"].unique())
    for rt in rates:
        b = df[(df.arm == "fsa") & (df.rate == rt)]
        t = df[(df.arm == "fsah") & (df.rate == rt)]
        if b.empty or t.empty:
            continue
        f = lambda col: (f"{b[col].mean():.1f} -> {t[col].mean():.1f}"
                         if col != "sep" else f"{b[col].mean():.3f} -> {t[col].mean():.3f}")
        print(f"{rt:>5.0f} {f('off'):>18} {f('sep'):>16} {f('chat_itl'):>16} "
              f"{f('goodput'):>18} {f('preempt'):>14}")

    print("\nper class, offered denominator")
    print(f"{'rate':>5} {'arm':>5} " + " ".join(f"{c:>14}" for c in CLASSES))
    for rt in rates:
        for arm in ("fsa", "fsah"):
            s = df[(df.arm == arm) & (df.rate == rt)]
            if not s.empty:
                print(f"{rt:>5.0f} {arm:>5} "
                      + " ".join(f"{s[c+'_off'].mean():>14.1f}" for c in CLASSES))

    print("\n" + "=" * 72)
    print("refutation conditions (EXP-43 §2), in order")
    print("=" * 72)
    b60 = df[(df.arm == "fsa") & (df.rate == 60)]
    t60 = df[(df.arm == "fsah") & (df.rate == 60)]
    if b60.empty or t60.empty:
        print("  60 req/s not complete on both arms yet; nothing to judge.")
        return

    def verdict(ok, text):
        print(f"  [{'PASS' if ok else 'FAIL'}] {text}")
        return ok

    sb, st = b60["sep"].mean(), t60["sep"].mean()
    c1 = verdict(st > sb + 0.01,
                 f"1. mechanism fired: separation index at 60 req/s {sb:.3f} -> {st:.3f}"
                 f"  (a null here says fsHarmCap is too small to decide anything)")
    ob, ot = b60["off"].mean(), t60["off"].mean()
    c2 = verdict(ot > ob, f"2. offered attainment at 60 req/s {ob:.1f} -> {ot:.1f}")
    ok3 = True
    for rt in (15.0, 30.0, 45.0):
        bb = df[(df.arm == "fsa") & (df.rate == rt)]
        tt = df[(df.arm == "fsah") & (df.rate == rt)]
        if bb.empty or tt.empty:
            continue
        tol = max(4.2, (bb["off"].max() - bb["off"].min()) if len(bb) > 1 else 0.0)
        d = tt["off"].mean() - bb["off"].mean()
        ok3 &= d > -tol
        print(f"       at {rt:.0f} req/s: {bb['off'].mean():.1f} -> {tt['off'].mean():.1f} "
              f"({d:+.1f}, tolerance -{tol:.1f})")
    c3 = verdict(ok3, "3. no regression at the lower rates")
    worst = df[df.arm == "fsah"]["preempt"].max()
    c4 = verdict(worst == 0,
                 f"4. preemptions stayed at zero: worst condition {worst:.0f} "
                 f"(EXP-38 and EXP-40 recorded 0 everywhere)")
    itl_b, itl_t = b60["chat_itl"].mean(), t60["chat_itl"].mean()
    c5 = verdict(not (c1 and not c2 and itl_t >= itl_b),
                 f"5. not concentration alone: chat inter-token latency "
                 f"{itl_b:.1f} -> {itl_t:.1f} ms")

    print()
    if not c1:
        print("  VERDICT: null. The term did not change any ordering. Record it as a")
        print("  statement about fsHarmCap rather than about the mechanism.")
    elif c2 and c3 and c4:
        print("  VERDICT: accepted.")
    elif c2 and c3 and not c4:
        print("  VERDICT: accepted on score, but it produced preemptions at a STATIC")
        print("  rate, which EXP-38 and EXP-40 never did. Candidate D (the capMem")
        print("  sharing ratio) becomes a precondition rather than an independent fix.")
    else:
        print("  VERDICT: rejected. See which condition failed above.")


if __name__ == "__main__":
    main()
