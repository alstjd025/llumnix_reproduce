#!/usr/bin/env python3
"""EXP-29 - where the gap between the predicted and the measured iteration is.

FluidServe gates every placement on a predicted mean iteration time. At 80 req/s
that prediction reads about 8 ms above what the engine actually runs, the gate
sits between the two, and the result is that 84% of decisions become holds while
the fleet runs at 21% KV. Three attempts to name the cause from the two endpoints
alone were wrong, so this decomposes the prediction into its terms instead.

The prediction is assembled as

    meanStep = corr x [ dec + (sp/k) x (pre - c0) ]

and the measurement satisfies, by the definition of the duty cycle that the
prefill attribution uses,

    measured = decodeOnly + duty_instant x measured

where `dec` is the decode law at the batch in the CURRENT status and `decodeOnly`
is the same law at the batch that was running over the interval that was
measured. Because the arriving prefill is built from the duty cycle through the
same constant the model uses in reverse, the prefill term collapses:

    sp/k              = duty x pace / perChunk
    (sp/k)(pre - c0)  = duty x pace

and `pace` is the measured mean. So with dec == decodeOnly and corr == 1 the
prediction equals the measurement identically, and the gap can only come from

    (1) corr    != 1          the multiplicative correction has not settled at 1
    (2) dec     != decodeOnly the two are evaluated at different batches
    (3) pending  > arriving   effectivePrefill takes the queued branch and the
                              collapse above no longer holds

This script measures the size of each, per instance and over the run.

Usage
-----
  python3 exp29_prediction_decomposition.py results/<run-dir> [--from-min 2]
"""
import argparse
import collections
import glob
import json
import os
import sys

# Series this reads. Everything is a gauge sampled once per scheduler scrape.
PER_INSTANCE = [
    "scheduler_fluidserve_observed_step_ms",
    "scheduler_fluidserve_predicted_step_ms",
    "scheduler_fluidserve_decode_only_ms",
    "scheduler_fluidserve_decode_law_ms",
    "scheduler_fluidserve_pace_ms",
    "scheduler_fluidserve_prefill_duty",
    "scheduler_fluidserve_arriving_prefill_tokens",
    "scheduler_fluidserve_queued_prefill_tokens",
    "scheduler_fluidserve_obs_kv_tokens",
    "scheduler_fluidserve_obs_decode_batch",
    "scheduler_fluidserve_gate_allowance_ms",
]
GLOBAL = [
    "scheduler_fluidserve_capacity_correction",
    "scheduler_fluidserve_prefill_fraction",
]


def load(run_dir, from_min):
    """Read scheduler.jsonl into {series: {instance: [(t, v)]}}.

    Keys are "<name>|<label>=<value>,..." or the bare name when unlabelled.
    """
    path = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(path):
        sys.exit(f"no scheduler metrics in {run_dir}")
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    t0 = None
    for line in open(path):
        try:
            r = json.loads(line)
        except Exception:
            continue
        t = r.get("t")
        if t is None:
            continue
        if t0 is None:
            t0 = t
        # Skip the opening minutes: the correction starts at its initial value
        # and the duty cycle at zero, so an average over them describes the
        # transient rather than the state the decisions were made in.
        if (t - t0) < from_min * 60:
            continue
        for k, v in r.items():
            if not isinstance(k, str) or not k.startswith("scheduler_fluidserve_"):
                continue
            if not isinstance(v, (int, float)):
                continue
            name, _, lbl = k.partition("|")
            inst = "-"
            if lbl.startswith("instance="):
                inst = lbl.split("=", 1)[1]
            out[name][inst].append((t, v))
    return out


def mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else float("nan")


def series_mean(d, name, inst=None):
    if name not in d:
        return float("nan")
    if inst is None:
        return mean([v for per in d[name].values() for _, v in per])
    return mean([v for _, v in d[name].get(inst, [])])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir")
    ap.add_argument("--from-min", type=float, default=2.0,
                    help="drop this many minutes of transient (default 2)")
    a = ap.parse_args()

    d = load(a.run_dir, a.from_min)
    missing = [s for s in PER_INSTANCE + GLOBAL if s not in d]
    if missing:
        print("MISSING SERIES (binary predates the instrumentation?):")
        for m in missing:
            print("   ", m)
        if "scheduler_fluidserve_decode_only_ms" in missing:
            sys.exit("cannot decompose without decode_only_ms")

    corr = series_mean(d, "scheduler_fluidserve_capacity_correction")
    pfrac = series_mean(d, "scheduler_fluidserve_prefill_fraction")
    print(f"\n{os.path.basename(a.run_dir)}   (first {a.from_min:g} min dropped)")
    print(f"correction = {corr:.4f}    prefill_fraction = {pfrac:.4f}\n")

    # Aligned by (instance, timestamp) rather than averaged series by series.
    # The duty cycle the measurement implies is a RATIO, (obs - decOnly)/obs, and
    # a ratio of the two means is not the mean of the ratios whenever the
    # iteration time is volatile -- which at this rate it is. Computing it from
    # run means made the smoothed duty look like it lagged by 0.13 when most of
    # that was the two averages not being the same quantity.
    per = collections.defaultdict(dict)   # (inst, t) -> {short name: value}
    SHORT = {
        "scheduler_fluidserve_observed_step_ms": "obs",
        "scheduler_fluidserve_predicted_step_ms": "pred",
        "scheduler_fluidserve_decode_law_ms": "dec",
        "scheduler_fluidserve_decode_only_ms": "deco",
        "scheduler_fluidserve_prefill_duty": "duty",
        "scheduler_fluidserve_pace_ms": "pace",
        "scheduler_fluidserve_arriving_prefill_tokens": "arr",
        "scheduler_fluidserve_queued_prefill_tokens": "que",
        "scheduler_fluidserve_gate_allowance_ms": "gate",
    }
    for name, short in SHORT.items():
        for inst, pts in d.get(name, {}).items():
            for t, v in pts:
                per[(inst, t)][short] = v
                per[(inst, t)]["_t"] = t

    rows = [r for r in per.values()
            if all(k in r for k in ("obs", "pred", "dec", "deco", "duty", "pace"))
            and r["obs"] > 0]
    if not rows:
        sys.exit("no aligned samples: the series do not share timestamps")
    for r in rows:
        r["duty_i"] = (r["obs"] - r["deco"]) / r["obs"]

    insts = sorted(d["scheduler_fluidserve_observed_step_ms"].keys())
    hdr = (f"{'instance':>10}{'obs':>8}{'pred':>8}{'gap':>7}"
           f"{'dec':>8}{'decOnly':>9}{'duty_s':>8}{'duty_i':>8}"
           f"{'arriv':>9}{'queued':>9}{'gate':>7}{'n':>6}")
    print(hdr)
    print("-" * len(hdr))
    agg = collections.defaultdict(list)
    for i in insts:
        sub = [r for (inst, _), r in per.items() if inst == i and "duty_i" in r]
        if not sub:
            continue
        g = lambda k: mean([r[k] for r in sub if k in r])
        print(f"{i[-8:]:>10}{g('obs'):8.2f}{g('pred'):8.2f}{g('pred')-g('obs'):7.2f}"
              f"{g('dec'):8.2f}{g('deco'):9.2f}{g('duty'):8.3f}{g('duty_i'):8.3f}"
              f"{g('arr'):9.0f}{g('que'):9.0f}{g('gate'):7.1f}{len(sub):6d}")
    for k in ("obs", "pred", "dec", "deco", "duty", "duty_i", "pace", "arr", "que"):
        agg[k] = [r[k] for r in rows if k in r]

    # Per-sample decomposition, reported as a median as well as a mean. The
    # iteration time is heavy-tailed at this rate -- a few very long intervals
    # pull the mean far above the typical sample -- and the two statistics
    # disagree about the SIGN of the gap, so reporting one alone would answer
    # the question either way depending on which was picked.
    corrs = dict(d.get("scheduler_fluidserve_capacity_correction", {}).get("-", []))
    def corr_at(t):
        return corrs.get(t, corr)

    for r in rows:
        c = corr_at(r.get("_t", None)) if "_t" in r else corr
        r["_corr"] = c
        # Does the assembly reproduce the published prediction? If this residual
        # is not near zero the model of how meanStep is built is wrong, and every
        # component below is a decomposition of the wrong quantity.
        r["asm"] = c * (r["dec"] + r["duty"] * r["pace"])
        r["asm_err"] = r["pred"] - r["asm"]
        r["gap"] = r["pred"] - r["obs"]
        r["c1"] = (c - 1) * r["dec"]
        r["c2"] = r["dec"] - r["deco"]
        r["c3"] = r["obs"] * (c * r["duty"] - r["duty_i"])
        r["resid"] = r["gap"] - r["c1"] - r["c2"] - r["c3"]

    def med(k):
        v = sorted(r[k] for r in rows if k in r)
        n = len(v)
        return (v[n // 2] if n % 2 else 0.5 * (v[n // 2 - 1] + v[n // 2])) if n else float("nan")

    print(f"\n{'':>28}{'mean':>9}{'median':>9}")
    for label, k in (("observed", "obs"), ("predicted", "pred"),
                     ("GAP (pred - obs)", "gap"),
                     ("assembly check (pred-asm)", "asm_err")):
        print(f"{label:>28}{mean([r[k] for r in rows]):9.2f}{med(k):9.2f}")

    print(f"\ndecomposition of the gap{'':>4}{'mean':>9}{'median':>9}")
    for label, k in (("(1) correction", "c1"), ("(2) evaluation batch", "c2"),
                     ("(3) duty cycle", "c3"), ("    residual", "resid")):
        print(f"{label:>28}{mean([r[k] for r in rows]):9.2f}{med(k):9.2f}")

    print(f"\n{'duty smoothed':>28}{mean([r['duty'] for r in rows]):9.3f}"
          f"{med('duty'):9.3f}")
    print(f"{'duty instantaneous':>28}{mean([r['duty_i'] for r in rows]):9.3f}"
          f"{med('duty_i'):9.3f}")
    print(f"{'correction':>28}{corr:9.4f}")
    print(f"{'arriving prefill (tok)':>28}{mean([r['arr'] for r in rows]):9.0f}")
    print(f"{'queued prefill (tok)':>28}{mean([r['que'] for r in rows]):9.0f}")
    nq = sum(1 for r in rows if r.get("que", 0) > r.get("arr", 0))
    print(f"{'samples on QUEUED branch':>28}{nq:9d}  of {len(rows)}")

    print("\nverdict (EXP-29 section 4 rule: a component >= 6 ms names the cause)")
    if abs(med("asm_err")) > 1.5:
        print(f"  !! assembly check off by {med('asm_err'):+.2f} ms at the median: "
              f"the model of how the prediction is built is wrong, and the "
              f"components below decompose the wrong quantity")
    named = False
    for label, k in (("correction loop", "c1"), ("evaluation batch", "c2"),
                     ("duty-cycle definition", "c3")):
        if abs(med(k)) >= 6.0 or abs(mean([r[k] for r in rows])) >= 6.0:
            print(f"  -> {label}: mean {mean([r[k] for r in rows]):+.2f} ms, "
                  f"median {med(k):+.2f} ms")
            named = True
    if abs(med("resid")) > 1.5:
        print(f"  !! residual {med('resid'):+.2f} ms at the median: the "
              f"decomposition does not close")
    if not named:
        print("  -> no component reaches 6 ms on either statistic")


if __name__ == "__main__":
    main()
