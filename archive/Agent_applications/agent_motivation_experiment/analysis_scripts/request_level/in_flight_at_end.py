#!/usr/bin/env python3
"""Where an attainment-over-time figure stops being readable, per run.

A request still in flight when a run ends has an unknown outcome, so attain()
drops it from BOTH denominators rather than scoring it a violation. That is
right in general -- counting it as a miss would penalise the low-load conditions
where a long request cannot finish inside the run -- and it is wrong at the end
of a BACKLOGGED run, because the requests still in flight there are precisely
the slow ones. Removing them removes the failures and leaves the survivors, so
the last windows read far too high.

Measured once on EXP-54's PolyServe arm: its final 90 s window holds 3,678
arrivals of which 3,396 never finished, and the 282 that did were the fast ones,
so attainment reads 99.6% against 9.1% two minutes earlier. On the figure that
is a near-vertical rise at minute 60 that reads as the static partition
recovering, which is the opposite of what happened. The same run's FluidServe arm
never exceeds 4.5%, because it rejects rather than building a backlog -- so the
artifact appears only on the arm that is doing worst, and flatters it.

This script reports, per window, the share of arrivals that never finished, and
prints the first window where each run crosses the threshold. Cut EVERY arm at
the earliest such minute across the runs being compared, not each at its own:
an arm that rejects and an arm that does not will cross minutes apart, and that
difference is the signal rather than a reason to give them different x extents.

Usage
-----
  python3 in_flight_at_end.py <run-dir> [<run-dir> ...] [--window 60] [--max 20]
"""
import argparse
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import truthy  # noqa: E402


def per_window(run_dir, window_s):
    m = pd.read_csv(os.path.join(run_dir, "metrics.csv"), low_memory=False)
    m = m[m["agent"] != "job_summary"].copy()
    if m.empty:
        return None
    t0 = m["start_time"].min()
    m["rel"] = m["start_time"] - t0
    # "Never finished" is the run-boundary cutoff and nothing else: a rejected
    # request has a known outcome and an errored one does too. This is the same
    # definition attain() uses to drop a row from both denominators, so the
    # share reported here is exactly the share it removed.
    m["rejected"] = truthy(m, "is_rejected")
    m["errored"] = truthy(m, "is_error") | truthy(m, "is_timeout")
    m["cutoff"] = truthy(m, "is_server_terminated") & ~m["rejected"] & ~m["errored"]
    m["win"] = (m["rel"] // window_s).astype(int)
    g = m.groupby("win").agg(arrivals=("cutoff", "size"), unfinished=("cutoff", "sum"))
    g["pct"] = 100.0 * g["unfinished"] / g["arrivals"]
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--window", type=float, default=60.0, help="seconds")
    ap.add_argument("--max", type=float, default=20.0,
                    help="drop windows whose unfinished share exceeds this (%%)")
    a = ap.parse_args()

    first_bad = {}
    for d in a.runs:
        g = per_window(d, a.window)
        name = os.path.basename(d.rstrip("/"))
        if g is None:
            print(f"{name}: EMPTY")
            continue
        bad = g[g["pct"] > a.max]
        fb = int(bad.index.min()) if len(bad) else None
        first_bad[name] = fb
        tail = g.tail(6)
        print(f"\n{name}")
        print(f"  마지막 여섯 창의 미완료 비율(%): "
              + " ".join(f"{i}:{v:.1f}" for i, v in zip(tail.index, tail['pct'])))
        print(f"  {a.max:.0f}% 를 처음 넘는 창: " + (f"{fb} (분)" if fb is not None else "없음"))

    have = [v for v in first_bad.values() if v is not None]
    print("\n=== 공통 절단점 ===")
    if not have:
        print(f"  어느 run 도 {a.max:.0f}% 를 넘지 않는다 — 전 구간을 읽어도 된다.")
    else:
        cut = min(have)
        print(f"  가장 이른 창이 {cut}분이므로, 비교하는 모든 arm 을 {cut}분에서 자른다.")
        print("  arm 마다 다른 지점에서 자르면 패널이 서로 다른 구간을 보여 준다.")
        for n, v in sorted(first_bad.items()):
            print(f"    {n}: " + (f"{v}분" if v is not None else "넘지 않음"))


if __name__ == "__main__":
    main()
