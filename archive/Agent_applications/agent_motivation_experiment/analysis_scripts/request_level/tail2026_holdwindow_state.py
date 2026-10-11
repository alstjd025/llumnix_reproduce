#!/usr/bin/env python3
"""Which state the fleet settles in under each gateway hold window.

`tail2026_holdwindow.py` reports the per-instance MEAN of the gate allowance,
and that mean took the value 62.5 ms under the 35,000/500 window and 50.0 ms
under the shipped 5,000/1,000 one.  Neither is a per-token budget any class has:
chat's is 50 ms, deepresearch's is 100 ms, and the swe class is judged end to end
so its nominal per-token pace is 30,000 ms divided by its expected output
length.  62.5 is the mean of (50, 50, 50, 100) -- three instances carrying chat
and one carrying none -- so the difference the mean was showing is not a
different allowance, it is a different ARRANGEMENT of the classes over the
instances.  This script measures that arrangement instead of inferring it.

  per-instance gate     the distribution of `scheduler_fluidserve_gate_allowance_ms`
                        for each instance separately, so an instance whose gate
                        sits at deepresearch's 100 ms is visible as such rather
                        than being averaged into a value no instance held.
  class over instances  every placed request's instance, from the scheduler's
                        `[Schedule] dispatch request <id> to neutral instance
                        <id>` lines joined to `request_ids.jsonl` on the request
                        id and thence to `metrics.csv` on (task_id, call_index).
                        The join rate was 100% on all twelve runs; it is
                        recomputed and printed here rather than assumed.
  concentration         N_eff = 1 / sum_i s_i^2 over the instance shares s_i of
                        one class in one window: four instances sharing a class
                        evenly gives 4.0, one instance holding all of it gives
                        1.0, and two instances holding half each gives 2.0, so
                        the unit is instances.  Computed per 60 s window and
                        reported as the median over windows, because a class
                        that is concentrated on a DIFFERENT instance in each
                        window reads as spread out when the whole run is pooled.

  python3 tail2026_holdwindow_state.py --out <directory>
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
from exp22_fluidserve import load_run, CLASSES  # noqa: E402
from tail2026_holdwindow import NAME_RE, RATES, SETTING, RESULTS  # noqa: E402

DISPATCH_RE = re.compile(
    r"^I(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{6})\s+\d+\s+\S+\] "
    r"\[Schedule\] dispatch request ([0-9a-f-]{36}) to \S+ instance (\d+)")
WINDOW_S = 60.0


def run_dirs():
    out = []
    for d in glob.glob(os.path.join(RESULTS, "*exp8[23]r[12]_fspfx_m1_rpm_*")):
        b = os.path.basename(d)
        m = NAME_RE.match(b)
        if m and os.path.isdir(d) and "PRERUN" not in b:
            if int(m.group("rpm")) in RATES:
                out.append((d, m.group("exp"), int(m.group("rep")),
                            int(m.group("rpm"))))
    return sorted(out, key=lambda t: (t[1], t[3], t[2]))


def dispatch_pairs(run_dir, year):
    """request id -> (dispatch epoch seconds, instance id), first dispatch only."""
    p = os.path.join(run_dir, "server_metrics", "scheduler_dispatch.log")
    out = {}
    if not os.path.isfile(p):
        return out
    with open(p, errors="replace") as fh:
        for line in fh:
            if "[Schedule] dispatch request " not in line:
                continue
            m = DISPATCH_RE.match(line)
            if not m:
                continue
            mo, dy, hh, mm, ss, us, rid, inst = m.groups()
            t = datetime.datetime(year, int(mo), int(dy), int(hh), int(mm),
                                  int(ss), int(us),
                                  tzinfo=datetime.timezone.utc).timestamp()
            if rid not in out or t < out[rid][0]:
                out[rid] = (t, inst)
    return out


def placement_frame(run_dir):
    r = load_run(run_dir)
    if r is None or r.empty:
        return None, np.nan
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
                            str(d.get("request_id", "")).replace("cmpl-", "")))
    idf = pd.DataFrame(ids, columns=["task_id", "call_index", "rid"])
    if not len(idf):
        return None, np.nan
    idf["call_index"] = pd.to_numeric(idf["call_index"], errors="coerce")
    year = datetime.datetime.fromtimestamp(
        float(r["start_time"].min()), datetime.timezone.utc).year
    disp = dispatch_pairs(run_dir, year)
    idf["instance"] = [disp[x][1] if x in disp else None for x in idf["rid"]]
    m = r.merge(idf[["task_id", "call_index", "instance"]],
                on=["task_id", "call_index"], how="left")
    placed = m[~m["rejected"]]
    join = 100.0 * placed["instance"].notna().mean() if len(placed) else np.nan
    return m[m["instance"].notna()], join


def n_eff(shares):
    s = np.asarray(shares, dtype=float)
    tot = s.sum()
    if tot <= 0:
        return np.nan
    s = s / tot
    return 1.0 / np.sum(s * s)


def separation_rows(m, exp, rep, rpm):
    """Per-window instance concentration of each class, and the top-1 share.

    Both are reported: the top-1 share is what earlier records in this
    repository quote, and N_eff is the one whose unit is instances and which
    therefore distinguishes "two instances hold half each" (2.0) from "one
    instance holds half" (also 50% on top-1).
    """
    rows = []
    t0 = m["rel"].min()
    m = m.copy()
    m["win"] = ((m["rel"] - t0) // WINDOW_S).astype(int)
    for c in CLASSES:
        s = m[m["class"] == c]
        neffs, tops = [], []
        for _, g in s.groupby("win"):
            counts = g["instance"].value_counts()
            if counts.sum() < 20:
                continue
            neffs.append(n_eff(counts.values))
            tops.append(100.0 * counts.max() / counts.sum())
        if neffs:
            rows.append(dict(exp=exp, rep=rep, rpm=rpm, cls=c,
                             windows=len(neffs),
                             n_eff_p50=float(np.median(neffs)),
                             top1_pct_p50=float(np.median(tops))))
    return rows


def instance_share_rows(m, exp, rep, rpm):
    """The class composition of each instance, pooled over the analysis window.

    Pooling is legitimate here because the question is which instances carried
    chat at all -- the gate allowance is set by the presence of the tightest
    class, not by how much of it there is -- and it is answered by a share that
    does not move much within a run at a fixed arrival rate.  Where a class
    MOVES between instances, the per-window N_eff above is the measure to read.
    """
    rows = []
    tot = m.groupby("instance").size()
    order = {inst: i for i, inst in enumerate(sorted(tot.index))}
    for inst, g in m.groupby("instance"):
        n = len(g)
        row = dict(exp=exp, rep=rep, rpm=rpm, inst=order[inst], n=n)
        for c in CLASSES:
            row[c] = 100.0 * (g["class"] == c).sum() / n if n else np.nan
        rows.append(row)
    return rows


def gate_rows(run_dir, exp, rep, rpm):
    """Per-instance gate allowance, KV and iteration time, kept separate.

    The fleet mean of a quantity that differs by instance describes an instance
    that does not exist: with three instances gated at chat's 50 ms and one at
    deepresearch's 100 ms the mean is 62.5, which is no class's budget.
    """
    p = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    if not os.path.isfile(p):
        return []
    series = {}
    with open(p, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for k, v in d.items():
                for g in ("gate_allowance_ms", "observed_step_ms",
                          "obs_kv_tokens", "cap_kv_tokens"):
                    pre = "scheduler_fluidserve_" + g + "|instance="
                    if k.startswith(pre) and v is not None:
                        series.setdefault(k[len(pre):], {}).setdefault(
                            g, []).append(float(v))
    rows = []
    for i, (inst, d) in enumerate(sorted(series.items())):
        row = dict(exp=exp, rep=rep, rpm=rpm, inst=i)
        for g, vs in d.items():
            a = np.asarray(vs, dtype=float)
            a = a[np.isfinite(a) & (a >= 0)]
            if not len(a):
                continue
            row[g + "_p50"] = float(np.median(a))
            if g == "gate_allowance_ms":
                row["pct_at_50"] = float(100.0 * np.mean(np.isclose(a, 50.0, atol=0.6)))
                row["pct_at_100"] = float(100.0 * np.mean(a >= 95.0))
        rows.append(row)
    return rows


def band(g, col, fmt="%.1f"):
    v = pd.to_numeric(g[col], errors="coerce").dropna()
    if not len(v):
        return "--"
    if len(v) == 1 or abs(v.max() - v.min()) < 1e-9:
        return fmt % v.iloc[0]
    return (fmt + ".." + fmt) % (v.min(), v.max())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        RESULTS, "aggregate_analysis", "tail_2026-08-16"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    sep, shares, gates, joins = [], [], [], []
    for d, exp, rep, rpm in run_dirs():
        print("reading", os.path.basename(d), file=sys.stderr)
        m, join = placement_frame(d)
        joins.append(dict(exp=exp, rep=rep, rpm=rpm, join_pct=join))
        if m is not None and len(m):
            sep += separation_rows(m, exp, rep, rpm)
            shares += instance_share_rows(m, exp, rep, rpm)
        gates += gate_rows(d, exp, rep, rpm)

    for name, rows in [("separation", sep), ("instshare", shares),
                       ("instgate", gates), ("statejoin", joins)]:
        df = pd.DataFrame(rows)
        p = os.path.join(a.out, "19_holdwindow_%s.csv" % name)
        df.to_csv(p, index=False)
        print("wrote", p, file=sys.stderr)

    gt = pd.DataFrame(gates)
    print("\n=== gate allowance PER INSTANCE (median over samples), and how "
          "often it sat at chat's 50 ms ===")
    print("%-6s %-14s %5s %10s %10s %10s %10s %10s" % (
        "req/s", "window", "inst", "gate_p50", "%at 50ms", "%>=100ms",
        "step_p50", "obs_kv"))
    for rpm in RATES:
        for exp in ("82", "83"):
            for i in range(4):
                g = gt[(gt.rpm == rpm) & (gt.exp == exp) & (gt.inst == i)]
                if not len(g):
                    continue
                print("%-6d %-14s %5d %10s %10s %10s %10s %10s" % (
                    rpm // 60, SETTING[exp], i,
                    band(g, "gate_allowance_ms_p50"), band(g, "pct_at_50"),
                    band(g, "pct_at_100"), band(g, "observed_step_ms_p50"),
                    band(g, "obs_kv_tokens_p50", "%.0f")))

    sh = pd.DataFrame(shares)
    print("\n=== class composition of each instance, %% of its placed requests ===")
    print("%-6s %-14s %5s %9s %9s %9s %9s" % (
        "req/s", "window", "inst", "n", "chat", "deepres", "swe"))
    for rpm in RATES:
        for exp in ("82", "83"):
            for i in range(4):
                g = sh[(sh.rpm == rpm) & (sh.exp == exp) & (sh.inst == i)]
                if not len(g):
                    continue
                print("%-6d %-14s %5d %9s %9s %9s %9s" % (
                    rpm // 60, SETTING[exp], i, band(g, "n", "%.0f"),
                    band(g, "chat"), band(g, "deepresearch"), band(g, "swe")))

    sp = pd.DataFrame(sep)
    print("\n=== instances a class was spread over, per 60 s window (median) ===")
    print("%-6s %-14s %-13s %10s %12s" % (
        "req/s", "window", "class", "N_eff", "top-1 share"))
    for rpm in RATES:
        for exp in ("82", "83"):
            for c in CLASSES:
                g = sp[(sp.rpm == rpm) & (sp.exp == exp) & (sp.cls == c)]
                if not len(g):
                    continue
                print("%-6d %-14s %-13s %10s %12s" % (
                    rpm // 60, SETTING[exp], c, band(g, "n_eff_p50", "%.2f"),
                    band(g, "top1_pct_p50")))

    print("\n=== join rate onto placed requests ===")
    print(pd.DataFrame(joins).to_string(index=False))


if __name__ == "__main__":
    main()
