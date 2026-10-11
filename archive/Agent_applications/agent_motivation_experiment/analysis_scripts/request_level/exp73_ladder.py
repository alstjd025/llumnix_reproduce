#!/usr/bin/env python3
"""EXP-73 - what holding a request costs, what the class preference costs, and
whether the two mechanisms help each other.

Four arms of one binary, each one flag away from the deployed default:

  fspfx       the control, FluidServe v0.2 as deployed
  fspnopend   --fluidserve-enable-pend=false      (no holding at the gateway)
  fspnoaff    --fluidserve-enable-affinity=false  (no class preference)
  fspslos     both off

The 2x2 is the point. If the loss with both off is LARGER than the sum of the
two single losses, the two mechanisms help each other and the co-design claim
has a measured basis. If it is SMALLER, they partly do the same work and the
claim has to be weakened.

The judgement rule was written before the run, in
experiments/EXP-73_attribution-ladder.md section 3, and this script computes
exactly the quantities it names. It is not adjusted to the result:

  - the reference rates are 25 and 35 req/s. 45 is reported but not judged on,
    because the repeat spread there reaches 8.5 points on this workload.
  - a loss counts only if it is larger than the repeat spread of the control at
    that rate. With two repeats the spread is the difference of two points, not
    an estimate of a variance, so it is used as a floor and nothing finer is
    read off it.

It also checks the thing EXP-25 failed to check and lost four hours to: that the
flag took effect at all. With holding off the PEND counter must be absent; with
the preference off the effective instances per class must rise towards four.

  python3 exp73_ladder.py [--runs 'results/*exp73*'] [--out <dir>]
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
from exp22_fluidserve import load_run, attain  # noqa: E402
from exp41_engine_view import attribute_engines  # noqa: E402

ARMS = ["fspfx", "fspnopend", "fspnoaff", "fspslos"]
LABEL = {
    "fspfx": "control (v0.2)",
    "fspnopend": "holding off",
    "fspnoaff": "class preference off",
    "fspslos": "both off",
}
DEC = "scheduler_fluidserve_decisions_total"
INF = "scheduler_fluidserve_infeasible_total"
JUDGE_RATES = (25.0, 35.0)


def scheduler_counters(run):
    """Decision and infeasibility counters, as run totals.

    The counters are cumulative and the scheduler is not restarted between the
    warm-up and the measured window on every path, so the run total is the last
    sample minus the first.
    """
    p = os.path.join(run, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(p):
        return None
    first, last = {}, {}
    for line in open(p):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        if not o.get("ok"):
            continue
        for k, v in o.items():
            if not isinstance(v, (int, float)):
                continue
            if k.startswith(DEC + "|") or k.startswith(INF + "|"):
                first.setdefault(k, float(v))
                last[k] = float(v)
    dec, inf = {}, {}
    for k in last:
        tag = k.split("=")[-1]
        (dec if k.startswith(DEC) else inf)[tag] = last[k] - first[k]
    return dict(dec=dec, inf=inf)


def effective_instances(run, r):
    """Effective number of instances a class ran on, per window, median.

    1/sum(share^2) over a 60-second window. Four instances holding an equal
    share reads 4.0, one instance reads 1.0, two instances holding half each
    reads 2.0, so the unit is instances. Computed per window rather than pooled
    over the run because a concentration whose location moves reads as no
    concentration at all when summed over a whole run.
    """
    if not os.path.exists(os.path.join(run, "analysis", "request_engine.csv")):
        return {}
    try:
        j, _ = attribute_engines(run, r)
    except Exception:
        return {}
    if j.empty:
        return {}
    WIN, STEP = 60.0, 30.0
    out = {}
    for cls in ("chat", "deepresearch", "swe"):
        vals = []
        t, tmax = 0.0, j["rel"].max()
        while t + WIN <= tmax:
            w = j[(j["rel"] >= t) & (j["rel"] < t + WIN) & (j["class"] == cls)]
            if len(w) >= 40:
                s = w["engine_port"].value_counts(normalize=True).values
                vals.append(1.0 / float((s ** 2).sum()))
            t += STEP
        if vals:
            out["neff_" + cls] = float(np.median(vals))
    if out:
        out["neff"] = float(np.mean(list(out.values())))
    return out


def summarise(run):
    r = load_run(run)
    if r is None or r.empty:
        return None
    rej = r["rejected"] if "rejected" in r else pd.Series(False, index=r.index)
    served = r[~rej]
    dur = r["rel"].max()
    met = served[~served["violate_served"]]
    row = dict(
        offered=attain(r, "violate_offered"),
        admitted=attain(served, "violate_served"),
        reject=100.0 * rej.mean(),
        goodput=met["output_tokens"].sum() / dur,
        total_out=served["output_tokens"].sum() / dur,
        n=len(r),
    )
    for cls in ("chat", "deepresearch", "swe"):
        c = r[r["class"] == cls]
        row["off_" + cls] = attain(c, "violate_offered") if len(c) else np.nan

    s = scheduler_counters(run)
    if s:
        tot = sum(s["dec"].values())
        row["decisions"] = tot
        for d in ("route", "pend", "shed", "force"):
            # A decision that never happened and a counter that does not exist
            # are different things, and the difference is the flag check: with
            # holding off the scheduler never registers a `pend` label at all.
            row[d] = (100.0 * s["dec"].get(d, 0.0) / tot) if tot else np.nan
            row["has_" + d] = d in s["dec"]
        itot = sum(s["inf"].values())
        for d in ("gate", "memory", "incumbents", "unpredictable"):
            row["inf_" + d] = (100.0 * s["inf"].get(d, 0.0) / itot
                               if itot else np.nan)
    row.update(effective_instances(run, r))
    return row


def collect(pattern):
    rows = []
    for d in sorted(glob.glob(pattern)):
        m = re.search(r"_exp73r(\d)_([a-z]+)_m1f?_rpm_(\d+)$", os.path.basename(d))
        if not m:
            continue
        arm = m.group(2)
        if arm not in ARMS:
            # The arm table above is what every figure and every judgement is
            # built from. An unregistered arm would be dropped in silence, so
            # this stops instead.
            sys.exit(f"unregistered arm {arm!r} in {d}\n"
                     f"register it in ARMS/LABEL before scoring")
        v = summarise(d)
        if v:
            rows.append(dict(rep=int(m.group(1)), arm=arm,
                             rate=int(m.group(3)) / 60.0, run=os.path.basename(d),
                             **v))
    return pd.DataFrame(rows)


def fmt(x, nd=1):
    return "—" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x:.{nd}f}"


def col(frame, name):
    """A column that may be absent because the per-run analysis has not run yet.

    `request_engine.csv` is written by a later analysis step, so a condition
    scored while the sweep is still going has no engine columns at all. Missing
    is reported as missing rather than as zero.
    """
    if name in frame.columns:
        return pd.to_numeric(frame[name], errors="coerce")
    return pd.Series(np.nan, index=frame.index, dtype=float)


def main(pattern, out):
    df = collect(pattern)
    if df.empty:
        sys.exit(f"no EXP-73 runs matched {pattern}")
    os.makedirs(out, exist_ok=True)
    df.to_csv(os.path.join(out, "exp73_conditions.csv"), index=False)

    rates = sorted(df["rate"].unique())
    present = [a for a in ARMS if a in set(df["arm"])]
    print(f"{len(df)} conditions, {len(present)}/{len(ARMS)} arms, "
          f"rates {', '.join(f'{r:.0f}' for r in rates)} req/s\n")

    # ---- did the flags take effect -------------------------------------
    print("=== flag check (what EXP-25 skipped) ===")
    print(f"{'arm':<24}{'PEND counter':<16}{'pend %':>8}{'decisions':>12}"
          f"{'eff. instances/class':>22}")
    for a in present:
        s = df[df["arm"] == a]
        has = bool(s["has_pend"].all()) if "has_pend" in s.columns else False
        print(f"{LABEL[a]:<24}"
              f"{('present' if has else 'ABSENT'):<16}"
              f"{fmt(col(s, 'pend').mean()):>8}"
              f"{fmt(col(s, 'decisions').mean(), 0):>12}"
              f"{fmt(col(s, 'neff').mean(), 2):>22}")
    print()

    # ---- the table -----------------------------------------------------
    for metric, nd in (("offered", 1), ("reject", 1), ("goodput", 0)):
        print(f"=== {metric} (mean over repeats; parentheses = spread) ===")
        head = f"{'arm':<24}" + "".join(f"{r:>18.0f}" for r in rates)
        print(head)
        for a in present:
            cells = ""
            for r in rates:
                s = df[(df["arm"] == a) & (df["rate"] == r)][metric]
                if s.empty:
                    cells += f"{'—':>18}"
                elif len(s) == 1:
                    cells += f"{s.iloc[0]:>18.{nd}f}"
                else:
                    cells += (f"{s.mean():.{nd}f} "
                              f"({s.max() - s.min():.{nd}f})").rjust(18)
            print(f"{LABEL[a]:<24}{cells}")
        print()

    # ---- the pre-registered judgement ----------------------------------
    print("=== judgement (rule written before the run, EXP-73 section 3) ===")
    ctrl = "fspfx"
    verdict = []
    for r in rates:
        c = df[(df["arm"] == ctrl) & (df["rate"] == r)]["offered"]
        if c.empty:
            continue
        spread = (c.max() - c.min()) if len(c) > 1 else float("nan")
        base = c.mean()
        judged = r in JUDGE_RATES
        print(f"\n  {r:.0f} req/s   control {base:.1f}"
              f"   repeat spread {fmt(spread)}"
              f"{'' if judged else '   (reported, not judged on)'}")
        losses = {}
        for a in ("fspnopend", "fspnoaff", "fspslos"):
            s = df[(df["arm"] == a) & (df["rate"] == r)]["offered"]
            if s.empty:
                continue
            losses[a] = base - s.mean()
            mark = ""
            if len(c) > 1 and not np.isnan(spread):
                mark = ("  > spread" if losses[a] > spread
                        else "  within spread")
            print(f"    {LABEL[a]:<24} {s.mean():6.1f}"
                  f"   loss {losses[a]:+6.1f}{mark}")
        if "fspslos" in losses and "fspnopend" in losses and "fspnoaff" in losses:
            add = losses["fspnopend"] + losses["fspnoaff"]
            rel = "larger than" if losses["fspslos"] > add else "smaller than"
            print(f"    both-off loss {losses['fspslos']:+.1f} is {rel} the sum "
                  f"of the two single losses ({add:+.1f})")
            if judged:
                verdict.append((r, losses, spread, add))

    if verdict:
        print("\n  reading the rule at the two reference rates:")
        for r, losses, spread, add in verdict:
            pend_counts = (not np.isnan(spread)) and losses["fspnopend"] > spread
            print(f"    {r:.0f} req/s: holding "
                  f"{'has a measured value' if pend_counts else 'is within the repeat spread'}"
                  f"; the two mechanisms "
                  f"{'help each other' if losses['fspslos'] > add else 'partly do the same work'}")
    print(f"\nwrote {os.path.join(out, 'exp73_conditions.csv')}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="results/*exp73*")
    ap.add_argument("--out", default="results/aggregate_analysis/exp73")
    a = ap.parse_args()
    main(a.runs, a.out)
