#!/usr/bin/env python3
"""EXP-101: what the decision predicted the first token would cost, against what
it cost, per instance -- and which feasibility condition refused a candidate on
its own.

Two questions this answers that no previous run could:

1. **Is the estimate the deadline test reads right?**  The scheduler now
   publishes, per instance, the sum of what the decision path predicted the
   prefill would cost (`dispatchRecord.prefillEstMs`) and the sum of what the
   request actually took from dispatch to its first token, over the same
   requests.  Their ratio is the quantity EXP-101 gates on: it was 0.69 on
   deepresearch in EXP-100 (9,005 ms predicted against 13,028 ms realised under
   a 10,000 ms budget), and a change that claims to correct the estimate has to
   move it to 1.0.  Before this counter the ratio could only be reconstructed by
   joining two logs.

2. **Did a feasibility condition refuse anything that would otherwise have been
   routed?**  `scheduler_fluidserve_infeasible_total` counts every firing of a
   term, so a candidate the gate also refused is counted under the term under
   test whether or not that term mattered.  `..._sole_total` counts only the
   candidates that ONE term refused, which is what an ablation of a single
   predicate has to read.  EXP-100 had to approach this from the share of
   decisions whose feasible set was already empty (63.9% against 61.9%).

Both are counters, so a run's value is the last sample minus the first.
"""
import argparse, glob, json, os, sys
from collections import defaultdict

PRED = "scheduler_fluidserve_placement_predicted_ms_total"
REAL = "scheduler_fluidserve_placement_realised_ms_total"
SAMP = "scheduler_fluidserve_placement_samples_total"
SOLE = "scheduler_fluidserve_infeasible_sole_total"
ALL  = "scheduler_fluidserve_infeasible_total"
DMEAN = "scheduler_fluidserve_instance_delay_mean_ms"
JOINT = "scheduler_fluidserve_placement_joint_total"


def series(path):
    """name -> {labelstring: (first, last)} over the run."""
    first, last = {}, {}
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict):
                continue
            for key, val in row.items():
                if key in ("t", "ok") or not isinstance(val, (int, float)):
                    continue
                if key not in first:
                    first[key] = val
                last[key] = val
    return first, last


def split(key):
    name, _, labels = key.partition("|")
    return name, labels


def label_value(labels, want):
    for part in labels.split(","):
        k, _, v = part.partition("=")
        if k == want:
            return v
    return ""


def report(run):
    path = os.path.join(run, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(path):
        print(f"  no scheduler.jsonl in {run}")
        return None
    first, last = series(path)

    per_inst = defaultdict(lambda: [0.0, 0.0, 0.0])
    gauges = {}
    sole, allr, joint = defaultdict(float), defaultdict(float), defaultdict(float)
    # Whether the counter EXISTS in this run, as distinct from being zero. The
    # old binary does not publish it, and a table of zeroes reads as "no
    # condition refused anything on its own", which is the opposite claim.
    sole_present = any(split(k)[0] == SOLE for k in last)
    for key, hi in last.items():
        name, labels = split(key)
        lo = first.get(key, 0.0)
        delta = hi - lo
        if name in (PRED, REAL, SAMP):
            inst = label_value(labels, "instance")
            idx = (PRED, REAL, SAMP).index(name)
            per_inst[inst][idx] += delta
        elif name == DMEAN:
            gauges[label_value(labels, "instance")] = hi
        elif name == SOLE:
            sole[label_value(labels, "reason")] += delta
        elif name == ALL:
            allr[label_value(labels, "reason")] += delta
        elif name == JOINT:
            joint[(label_value(labels, "tier"),
                   label_value(labels, "cell"))] += delta

    print(f"\n{os.path.basename(run)}")
    if not per_inst:
        print("  the placement-delay counters are absent from this run "
              "(binary predates EXP-101, or the metric is not whitelisted "
              "in llumnix_metrics.py)")
    else:
        print("  what the decision predicted the first token would cost, "
              "against what it cost")
        print(f"  {'instance':>20} {'samples':>9} {'pred ms':>9} {'real ms':>9} "
              f"{'real/pred':>10} {'EWMA ms':>9}")
        tp = tr = ts = 0.0
        for inst in sorted(per_inst, key=lambda i: -per_inst[i][2]):
            p, r, s = per_inst[inst]
            tp, tr, ts = tp + p, tr + r, ts + s
            ratio = (r / p) if p > 0 else float("nan")
            mp = p / s if s else float("nan")
            mr = r / s if s else float("nan")
            print(f"  {inst[-18:]:>20} {s:9.0f} {mp:9.1f} {mr:9.1f} "
                  f"{ratio:10.3f} {gauges.get(inst, float('nan')):9.1f}")
        ratio = (tr / tp) if tp > 0 else float("nan")
        print(f"  {'fleet':>20} {ts:9.0f} {tp/ts if ts else 0:9.1f} "
              f"{tr/ts if ts else 0:9.1f} {ratio:10.3f}")
        print(f"  gate: real/pred must reach 1.0 +/- 0.1 -- "
              f"{'PASS' if abs(ratio - 1.0) <= 0.1 else 'FAIL'} at {ratio:.3f}")

    if joint:
        # The tier is the class's per-token budget in milliseconds, which is how
        # the scheduler names a class everywhere else.
        names = {"25": "swe", "50": "chat", "100": "deepresearch"}
        print("  did the decision expect a slow first token where the first "
              "token was in fact slow, at the 10 s line")
        print(f"  {'class':>14}{'placed':>9}{'was late':>10}{'foreseen':>10}"
              f"{'seen/late':>11}{'false alarm':>13}")
        tiers = sorted({t for t, _ in joint})
        for tier in tiers:
            tp = joint.get((tier, "slow_slow"), 0.0)
            fn = joint.get((tier, "fast_slow"), 0.0)
            fp = joint.get((tier, "slow_fast"), 0.0)
            tn = joint.get((tier, "fast_fast"), 0.0)
            n = tp + fn + fp + tn
            late = tp + fn
            flagged = tp + fp
            recall = (100.0 * tp / late) if late else float("nan")
            alarm = (100.0 * fp / flagged) if flagged else float("nan")
            print(f"  {names.get(tier, tier):>14}{n:9.0f}{late:10.0f}"
                  f"{tp:10.0f}{recall:10.1f}%{alarm:12.1f}%")
        print("  seen/late is how much of the late work a first-token test "
              "could refuse at all; false alarm is what refusing on it costs.")
        print("  A test can only refuse what it can see: near zero on the "
              "first column and no threshold on this estimate separates the "
              "two populations, whatever constant it is given.")

    if allr and not sole_present:
        print("  refusals by condition (the sole-condition counter is ABSENT "
              "from this run, so the second question cannot be asked of it)")
        print(f"  {'condition':>14} {'fired':>12}")
        for reason in sorted(allr, key=lambda r: -allr[r]):
            print(f"  {reason:>14} {allr[reason]:12.0f}")
    elif allr:
        print("  refusals by condition: every firing, and the firings where "
              "that condition was the ONLY one refusing the candidate")
        print(f"  {'condition':>14} {'fired':>12} {'sole':>12} {'sole share':>11}")
        for reason in sorted(allr, key=lambda r: -allr[r]):
            s = sole.get(reason, 0.0)
            share = (100.0 * s / allr[reason]) if allr[reason] else float("nan")
            print(f"  {reason:>14} {allr[reason]:12.0f} {s:12.0f} {share:10.1f}%")
    return per_inst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True,
                    help="run directories or globs")
    args = ap.parse_args()
    runs = []
    for pat in args.runs:
        hits = sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]
        if not hits:
            print(f"no run matches {pat}", file=sys.stderr)
        runs += [h for h in hits if "PRERUN" not in h]
    if not runs:
        sys.exit("no runs")
    for run in runs:
        report(run)


if __name__ == "__main__":
    main()
