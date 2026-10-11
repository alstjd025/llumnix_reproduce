#!/usr/bin/env python3
"""Per instance, does tightening the gate to a class's promise cost any capacity?

Admission takes min(capKv, capMem): capKv is the pace ceiling and falls when the
gate tightens, capMem is the physical pool and does not. So the cost of letting a
class open a gate on an instance is

    min(capKv_now, capMem) - min(capKv_after, capMem)

and it is ZERO whenever capMem is the smaller ceiling both before and after. The
class-instance cap refuses placements to avoid that cost; where the cost is zero
it is refusing to protect capacity that was never at risk.

capKv is EXACTLY linear in the allowance -- maxKvForAllowance returns
(allowance/corr - overhead)/c_kv and the overhead term carries no allowance -- so
the value at another gate follows from the published one without a second call:

    capKv_after = capKv_now + (a_after - a_now) / (corr * c_kv)

with a = budget * fsAllowanceUtilisation. Both corr (per instance) and capKv are
published per instance, so this is arithmetic on stored series, not a model.

Two readings are not usable and are counted separately rather than dropped:
finiteOrMinusOne publishes -1 for an infinite ceiling, which is what an instance
with no live request has, and there the linear step does not apply.
"""
import argparse, glob, json, os, sys
import numpy as np

FS_ALLOWANCE_UTILISATION = 0.90
TIER_BUDGET = {"chat": 50.0, "swe": 75.0, "deepresearch": 100.0}


def load(run):
    p = os.path.join(run, "server_metrics", "scheduler.jsonl")
    rows = [json.loads(l) for l in open(p)]
    ids = sorted({k.split("instance=")[1] for k in rows[-1]
                  if k.startswith("scheduler_fluidserve_cap_kv_tokens|")})
    return rows, ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--profile", required=True,
                    help="deploy/profiling/<dir>/fluidserve.json, for c_kv")
    a = ap.parse_args()
    c_kv = json.load(open(a.profile))["decode_step_law"]["c_kv_ms_per_token"]
    rows, ids = load(a.run)
    if not ids:
        sys.exit(f"no per-instance capKv series in {a.run} -- was the run loaded?")
    # Load window: scrapes where the route counter advanced. Taking the whole run
    # would mix in the idle scrapes before and after load, where every instance is
    # empty and every ceiling is the -1 sentinel.
    rt = np.array([r.get("scheduler_fluidserve_decisions_total|decision=route", np.nan)
                   for r in rows], float)
    loaded = np.r_[False, np.diff(np.nan_to_num(rt)) > 0]
    print(f"{a.run}")
    print(f"  scrapes {len(rows):,}, loaded {loaded.sum():,}, instances {len(ids)}, "
          f"c_kv {c_kv:.3e} ms/token")
    print()
    hdr = (f"{'instance':>9s} {'gate ms':>8s} {'capKv':>13s} {'capMem':>13s} "
           f"{'binding':>8s} | " +
           " | ".join(f"{c[:4]}: cost" for c in TIER_BUDGET))
    print(hdr)
    tot = {c: [0, 0] for c in TIER_BUDGET}   # [zero-cost samples, usable samples]
    unusable = 0
    for i in ids:
        g = lambda n: np.array([r.get(f"{n}|instance={i}", np.nan) for r in rows], float)
        kv, mem, ga, corr = (g("scheduler_fluidserve_cap_kv_tokens"),
                             g("scheduler_fluidserve_cap_mem_tokens"),
                             g("scheduler_fluidserve_gate_allowance_ms"),
                             g("scheduler_fluidserve_instance_correction"))
        ok = loaded & np.isfinite(kv) & np.isfinite(mem) & np.isfinite(ga) & np.isfinite(corr)
        # -1 is the sentinel for an infinite ceiling or no gate at all.
        usable = ok & (kv > 0) & (mem > 0) & (ga > 0) & (corr > 0)
        unusable += int((ok & ~usable).sum())
        if usable.sum() < 5:
            print(f"{i[-4:]:>9s}  (fewer than 5 usable scrapes)")
            continue
        cells = []
        for c, budget in TIER_BUDGET.items():
            a_now = ga[usable] * FS_ALLOWANCE_UTILISATION
            a_after = np.minimum(ga[usable], budget) * FS_ALLOWANCE_UTILISATION
            kv_after = kv[usable] + (a_after - a_now) / (corr[usable] * c_kv)
            cost = np.minimum(kv[usable], mem[usable]) - np.minimum(kv_after, mem[usable])
            zero = cost <= 0
            tot[c][0] += int(zero.sum()); tot[c][1] += int(usable.sum())
            cells.append(f"{100*zero.mean():8.1f}%")
        print(f"{i[-4:]:>9s} {np.median(ga[usable]):8.1f} {np.median(kv[usable]):13,.0f} "
              f"{np.median(mem[usable]):13,.0f} "
              f"{'memory' if np.median(mem[usable]) < np.median(kv[usable]) else 'pace':>8s} | "
              + " | ".join(cells))
    print()
    print("fraction of loaded per-instance scrapes where opening that class's gate")
    print("would cost the instance NOTHING (so the cap would have nothing to protect):")
    for c in TIER_BUDGET:
        z, n = tot[c]
        print(f"  {c:<14s} {100*z/max(n,1):6.1f}%   ({z:,} of {n:,} samples)")
    print(f"\nunusable samples (infinite-ceiling sentinel or no gate): {unusable:,}")


if __name__ == "__main__":
    main()
