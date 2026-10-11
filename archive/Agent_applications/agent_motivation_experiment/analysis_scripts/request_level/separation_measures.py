#!/usr/bin/env python3
"""How separated are the classes, measured four ways, for any set of runs.

The motivation section needs to say that fully separating the classes is bad,
fully mixing them is bad, and the good region is in between. Saying that needs a
number for "how separated", and the one used so far -- the share of a class held
by the instance holding the most of it -- has two problems that this script
exists to fix.

  1. It does not read as what it means when a system dedicates SEVERAL instances
     to one class. The static-partition baseline gives two of four instances to
     the software-engineering class, and that class then reads 50%, which is the
     same number an unpartitioned system would produce by holding half of a class
     on one instance by chance.

  2. It measures how spread a CLASS is. The mechanism that makes separation
     worth anything is the other direction: an instance holding one request of
     the tightest-budget class is held to that budget for everything else it
     might take. What matters is therefore a property of the INSTANCE.

So four quantities are produced per run. All of them are computed on the same
windows and the same attribution, so they can be read against each other.

  top1        the share of a class held by the instance holding the most of it,
              median over windows. 25% for an even spread over four instances,
              100% for one instance. This is what earlier documents call the
              class concentration and it is kept so that new numbers can be
              checked against recorded ones.

  eff_inst    the effective number of instances a class runs on, 1 / sum(p_i^2)
              where p_i is instance i's share of the class. 4.0 for an even
              spread over four instances, 1.0 for one instance, 2.0 for two
              instances holding half each. This is the quantity that reads
              correctly for a system dedicating two instances to one class.

  eff_class   the effective number of classes an instance holds, 1 / sum(q_c^2)
              where q_c is class c's share of the instance's requests. Averaged
              over instances. Its maximum is not 3.0 but the effective number of
              classes in the workload mixture, because a class that is 8% of
              arrivals cannot occupy a third of every instance; that ceiling is
              printed alongside so the reading has a scale.

  nochat      the fraction of (instance, sample time) pairs at which an instance
              held no resident request of the tightest-budget class. This is the
              mechanism variable: an instance in that state is admissible to
              classes promised a looser pace, and an instance not in that state
              is not. 0% means every instance always held one.

`nochat` is derived from the client's records -- a request is treated as
resident on its instance from when it started to when it ended -- rather than
from the scheduler's gate gauge, for one reason: the gauge exists only in our
own policy, so the gauge cannot compare us against the baselines. The two
instruments are independent and both are available for our own runs, so the
script prints the gauge value too when it can find it and the difference between
them is a check on the derivation.

  python3 separation_measures.py 'results/*exp56r?_fluidserve_m1_rpm_2700' ...
  python3 separation_measures.py --window 60 --label-from-dir 'results/*_rpm_2700'
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
from exp22_fluidserve import CLASSES, attain, load_run  # noqa: E402
from exp41_engine_view import attribute_engines  # noqa: E402

TIGHT = "chat"          # the class with the smallest per-token budget
MIN_PER_WINDOW = 40     # a share over fewer requests than this moves for
                        # reasons that have nothing to do with routing


def effective_count(counts):
    """1 / sum of squared shares: the number of equally-sized parts that would
    give the same concentration.

    Preferred to the largest share because it counts the parts. Two instances
    holding half of a class each gives 2.0, which is what a reader wants to be
    told when a system dedicates two instances to a class; the largest share
    gives 50%, which is indistinguishable from one instance holding half of an
    unpartitioned class.
    """
    n = np.asarray([c for c in counts if c > 0], dtype=float)
    if n.sum() <= 0:
        return np.nan
    p = n / n.sum()
    return 1.0 / float((p ** 2).sum())


def windows(rel_max, win, step):
    t = win / 2.0
    while t + win / 2.0 <= rel_max:
        yield t - win / 2.0, t + win / 2.0
        t += step


def class_measures(r, win, step):
    """top1 and eff_inst per class, and eff_class per instance, over windows."""
    top1 = {c: [] for c in CLASSES}
    eff_i = {c: [] for c in CLASSES}
    eff_c, n_windows = [], 0
    for t0, t1 in windows(r["rel"].max(), win, step):
        w = r[(r["rel"] >= t0) & (r["rel"] < t1)]
        if w.empty:
            continue
        n_windows += 1
        for c in CLASSES:
            sub = w[w["class"] == c]
            if len(sub) < MIN_PER_WINDOW:
                continue
            n = sub["engine_port"].value_counts()
            top1[c].append(100.0 * n.iloc[0] / n.sum())
            eff_i[c].append(effective_count(n.values))
        # The dual view: within one instance, how many classes is it holding.
        per_inst = []
        for _, g in w.groupby("engine_port"):
            if len(g) < MIN_PER_WINDOW:
                continue
            per_inst.append(effective_count(g["class"].value_counts().values))
        if per_inst:
            eff_c.append(float(np.mean(per_inst)))
    med = lambda v: float(np.median(v)) if v else np.nan  # noqa: E731
    return ({c: med(top1[c]) for c in CLASSES},
            {c: med(eff_i[c]) for c in CLASSES},
            med(eff_c), n_windows)


def identity_churn(r, win, step):
    """How often the instance holding the most of a class changes, per hour.

    This is the quantity that separates a system which re-forms its separation
    continuously from one that fixes it once. A static partition reads 0: the
    assignment is a configuration and does not move while the run lasts. It is
    reported per hour so that eight-minute conditions and one-hour traces are
    comparable.
    """
    out = {}
    dur_h = r["rel"].max() / 3600.0
    for c in CLASSES:
        prev, changes, seen = None, 0, set()
        for t0, t1 in windows(r["rel"].max(), win, step):
            sub = r[(r["rel"] >= t0) & (r["rel"] < t1) & (r["class"] == c)]
            if len(sub) < MIN_PER_WINDOW:
                continue
            top = sub["engine_port"].value_counts().index[0]
            seen.add(top)
            if prev is not None and top != prev:
                changes += 1
            prev = top
        out[c] = (changes / dur_h if dur_h > 0 else np.nan, len(seen))
    return out


def assignment_drift(r, win, step):
    """How much a class's distribution over the instances changes from one
    window to the next, averaged over classes and windows.

    For class c let p(t) be the vector of instance shares of that class in
    window t, summing to one. The drift is the total variation distance
    0.5 * sum_i |p_i(t) - p_i(t-1)|. It reads 0 when the class is distributed
    the same way it was and 1 when it has moved entirely to instances it was not
    on before.

    This is the second axis, and it is independent of the first. A static
    partition reads near zero because the assignment is a configuration. Load
    balancing also reads near zero, because the distribution is uniform in every
    window and uniform does not move. What distinguishes them is the first axis,
    the number of instances a class runs on. A system that produces its
    separation per request reads above zero on this axis while reading below
    four on the first, and that combination is what neither baseline produces.

    Normalising per class is what makes this a property of the ASSIGNMENT rather
    than of the workload. The first version of this function measured the change
    in each instance's class composition instead, and on the one-hour trace the
    load-equalising baseline read highest of all (0.103 against the static
    partition's 0.015) -- not because it moved anything, but because every
    instance carries the arrival mixture and the arrival mixture itself steps
    three times in that trace. Dividing by the class total removes that.

    The argmax-based churn below cannot serve this purpose either: when a class
    sits evenly on two instances the argmax alternates between them for reasons
    of counting noise, which is why the static partition reads 41 to 55 changes
    an hour for the one class it spreads over two instances while its assignment
    has not moved at all.
    """
    insts = sorted(r["engine_port"].unique())
    series = {c: [] for c in CLASSES}
    for t0, t1 in windows(r["rel"].max(), win, step):
        w = r[(r["rel"] >= t0) & (r["rel"] < t1)]
        for c in CLASSES:
            sub = w[w["class"] == c]
            if len(sub) < MIN_PER_WINDOW:
                continue
            n = sub["engine_port"].value_counts()
            p = np.array([n.get(i, 0) for i in insts], dtype=float)
            series[c].append(p / p.sum())

    def at_lag(k):
        d = [0.5 * float(np.abs(v[i] - v[i - k]).sum())
             for v in series.values() for i in range(k, len(v))]
        return float(np.mean(d)) if d else np.nan

    # Two lags, because the value at one lag cannot be compared across
    # configurations. The distance between two consecutive windows contains
    # counting noise, and how much depends on the distribution being measured:
    # a class sitting entirely on one instance has none, while a class spread
    # evenly over four has the most. So a static partition can read LOWER than
    # a load-equalising policy while neither has moved.
    #
    # Movement accumulates with the lag and noise does not. The ratio of the
    # distance at eight windows to the distance at one is therefore the part
    # that is comparable: near 1 means whatever the level, it is noise; above 1
    # means the distribution is going somewhere.
    return at_lag(1), at_lag(8)


def demand_implied_instances(r, n_inst):
    """How many instances each class's share of the produced output tokens
    implies, if instances were handed out in proportion to that share.

    Output tokens are used as the measure of the work a class asks for because
    decode dominates the engine time here and each decoded token costs one
    iteration slot. It is a coarse proxy -- it ignores prefill and it ignores
    that a longer request holds its memory for longer -- so it is reported as a
    reference point to read the measured allocation against, not as a target the
    system is trying to hit.
    """
    tok = r.groupby("class")["output_tokens"].sum()
    tot = tok.sum()
    if tot <= 0:
        return {c: np.nan for c in CLASSES}
    return {c: n_inst * float(tok.get(c, 0.0)) / float(tot) for c in CLASSES}


def residency_nochat(r, sample_s=5.0):
    """Fraction of (instance, sample time) pairs holding no resident request of
    the tightest class, and the mean number of instances in that state.

    A request is treated as resident on the instance it was dispatched to from
    its start to its end. That is an approximation of what the engine held --
    the client sees the request as running from when it issued it, which
    includes any time it waited at the gateway -- so the value is compared
    against the scheduler's own gate gauge wherever both exist.
    """
    need = {"rel", "engine_port", "class"}
    if not need.issubset(r.columns):
        return np.nan, np.nan
    # `latency` is in SECONDS in these records, the same unit as `rel`. The
    # end-to-end budget in SLO_RULES is 30.0 for the same reason. Dividing it by
    # a thousand made every request last a millisecond and the first reading of
    # this quantity came out at 85% for every arm.
    end = r["rel"] + pd.to_numeric(r.get("latency"), errors="coerce")
    d = pd.DataFrame({"inst": r["engine_port"], "cls": r["class"],
                      "t0": r["rel"], "t1": end}).dropna()
    if d.empty:
        return np.nan, np.nan
    insts = sorted(d["inst"].unique())
    if not insts:
        return np.nan, np.nan
    tight = d[d["cls"] == TIGHT]
    grid = np.arange(d["t0"].min(), d["t1"].max(), sample_s)
    if grid.size == 0:
        return np.nan, np.nan
    free = np.zeros(grid.size, dtype=float)
    for inst in insts:
        g = tight[tight["inst"] == inst]
        if g.empty:
            free += 1.0
            continue
        # Count of tight-class requests resident at each sample: the number
        # started minus the number finished, by binary search over sorted ends.
        started = np.searchsorted(np.sort(g["t0"].values), grid, side="right")
        ended = np.searchsorted(np.sort(g["t1"].values), grid, side="right")
        free += (started - ended) <= 0
    return 100.0 * float(free.mean()) / len(insts), float(free.mean())


def gate_nochat(run):
    """The same quantity from the scheduler's own gauge, when the run has one.

    Only our policy publishes it, so this returns None for every baseline. Chat
    is promised exactly 50.0 ms per token and the next budget up is 61.9, so a
    reading above 50.5 means the instance held no chat request whose remaining
    budget is still achievable. That last clause is why this and the
    residency-derived value above are not the same quantity: the scheduler drops
    a request from the gate once no batch composition could still meet it.
    """
    p = os.path.join(run, "server_metrics", "scheduler.jsonl")
    if not os.path.exists(p):
        return None
    vals = []
    for line in open(p):
        try:
            o = json.loads(line)
        except ValueError:
            continue
        for k, v in o.items():
            if k.startswith("scheduler_fluidserve_gate_allowance_ms|") \
                    and isinstance(v, (int, float)):
                vals.append(float(v))
    if not vals:
        return None
    v = np.asarray(vals)
    v = v[np.isfinite(v) & (v >= 0)]
    return 100.0 * float((v > 50.5).mean()) if v.size else None


def label_of(d):
    """Turn a result directory name into "arm [session] rate".

    The session tag is not always `expNNrN`: passes are named `exp53p2r1`, hour
    runs `exp56hr1`, and one-off arms `exp57unchanged`. The mix variant is not
    always `_m1` either -- the Llumnix SLO arm runs `_m1f`, the same mixture with
    the agent class's end-to-end budget restated for a policy that cannot express
    one. A regex that assumed either form fell through to the raw directory name
    for six of twelve runs and the figure then drew each of them as its own arm.
    """
    b = os.path.basename(d)
    m = re.match(r"^\d{6}_\d{4}_([a-z0-9]+)_(.+)$", b)
    if not m:
        return b
    session, rest = m.group(1), m.group(2)
    rate = ""
    rm = re.search(r"_rpm_(\d+)$", rest)
    if rm:
        rate = f" {int(rm.group(1)) / 60:.0f}rps"
        rest = rest[: rm.start()]
    else:
        rate = " hour"
    rest = re.sub(r"_(m\d+f?|full|verbatim)$", "", rest)
    return f"{rest} [{session}]{rate}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("patterns", nargs="+")
    ap.add_argument("--window", type=float, default=60.0)
    ap.add_argument("--step", type=float, default=30.0)
    ap.add_argument("--csv", default="")
    a = ap.parse_args()

    dirs = []
    for p in a.patterns:
        dirs += sorted(glob.glob(p))
    if not dirs:
        sys.exit("no runs matched")

    rows = []
    for d in dirs:
        r = load_run(d)
        if r is None or r.empty:
            print(f"  {os.path.basename(d)}: no rows")
            continue
        if not os.path.exists(os.path.join(d, "analysis", "request_engine.csv")):
            print(f"  {os.path.basename(d)}: no request_engine.csv, skipped")
            continue
        e, n = attribute_engines(d, r)
        if e is None or e.empty:
            print(f"  {os.path.basename(d)}: attribution empty, skipped")
            continue
        frac = 100.0 * len(e) / max(n, 1)
        top1, eff_i, eff_c, nw = class_measures(e, a.window, a.step)
        dem = demand_implied_instances(e, e["engine_port"].nunique())
        drift1, drift8 = assignment_drift(e, a.window, a.step)
        churn = identity_churn(e, a.window, a.step)
        nochat, nfree = residency_nochat(e)
        gate = gate_nochat(d)
        # The ceiling on eff_class: a class that is a small part of the mixture
        # cannot fill a third of an instance, so the maximum an evenly mixed
        # fleet could reach is the effective number of classes in the arrivals.
        mix_ceiling = effective_count(e["class"].value_counts().values)
        # The score belongs in the same row: every figure that reads this file
        # plots a separation measure against it, and joining two files on a run
        # name is one more place for an arm to be dropped in silence.
        served = r[~r["rejected"]]
        met = served[~served["violate_served"]]
        # The same divisor exp56_affinity.py uses, so that the goodput printed
        # here is the same number as the one in the experiment files. Using the
        # trimmed span instead gave 22,711 against that file's 20,099 for the
        # same run, which is exactly the "two quantities with one name" failure
        # this repository keeps hitting.
        dur = max(r["rel"].max(), 1.0)
        rows.append(dict(
            run=os.path.basename(d), label=label_of(d), attributed=frac,
            windows=nw,
            offered=attain(r, "violate_offered"),
            admitted=attain(served, "violate_served"),
            reject=100.0 * float(r["rejected"].mean()),
            goodput=float(pd.to_numeric(met["output_tokens"], errors="coerce")
                          .fillna(0).sum()) / dur,
            **{f"top1_{c}": top1[c] for c in CLASSES},
            top1_mean=float(np.nanmean([top1[c] for c in CLASSES])),
            drift=drift1, drift_lag8=drift8,
            drift_ratio=(drift8 / drift1 if drift1 else np.nan),
            **{f"demand_{c}": dem[c] for c in CLASSES},
            **{f"effinst_{c}": eff_i[c] for c in CLASSES},
            effinst_mean=float(np.nanmean([eff_i[c] for c in CLASSES])),
            effclass=eff_c, effclass_ceiling=mix_ceiling,
            **{f"churn_{c}": churn[c][0] for c in CLASSES},
            churn_mean=float(np.nanmean([churn[c][0] for c in CLASSES])),
            **{f"nseen_{c}": churn[c][1] for c in CLASSES},
            nochat_pct=nochat, nochat_instances=nfree, gate_nochat_pct=gate,
        ))

    if not rows:
        sys.exit("nothing measured")
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 200)

    print("\n=== the score, for reading the measures below against")
    print(df[["label", "offered", "admitted", "reject", "goodput"]].to_string(
        index=False, float_format="%.1f"))

    print("\n=== how spread is each class: largest instance's share of it (%), "
          "median over windows")
    print("    25.0 = even over four instances, 100 = one instance")
    print(df[["label", "attributed"] + [f"top1_{c}" for c in CLASSES]
             + ["top1_mean"]].to_string(index=False, float_format="%.1f"))

    print("\n=== how many instances does a class effectively run on, "
          "1/sum(share^2), median over windows")
    print("    4.0 = even over four instances, 2.0 = two instances, "
          "1.0 = one instance")
    print(df[["label"] + [f"effinst_{c}" for c in CLASSES]
             + ["effinst_mean"]].to_string(index=False, float_format="%.2f"))

    print("\n=== how many classes does an instance effectively hold, "
          "1/sum(share^2), mean over instances")
    print("    the ceiling is the effective number of classes in the arrivals, "
          "printed beside it")
    print(df[["label", "effclass", "effclass_ceiling"]].to_string(
        index=False, float_format="%.2f"))

    print("\n=== how many instances each class's share of the output tokens "
          "would imply, out of four")
    print("    a reference to read the measured allocation against, not a target")
    print(df[["label"] + [f"demand_{c}" for c in CLASSES]].to_string(
        index=False, float_format="%.2f"))

    print("\n=== how much a class's distribution over the instances changes "
          "between consecutive windows (total variation, 0 = unchanged)")
    print("    lag 1 and lag 8 windows, and their ratio: near 1 is noise, "
          "above 1 is movement")
    print(df[["label", "drift", "drift_lag8", "drift_ratio"]].to_string(
        index=False, float_format="%.3f"))

    print("\n=== how often the instance holding the most of a class changes, "
          "per hour (and how many distinct instances held it)")
    print("    0 = the assignment does not move while the run lasts")
    print(df[["label"] + [f"churn_{c}" for c in CLASSES] + ["churn_mean"]
             + [f"nseen_{c}" for c in CLASSES]].to_string(
        index=False, float_format="%.1f"))

    print(f"\n=== the mechanism variable: (instance, time) pairs holding no "
          f"resident {TIGHT} request")
    print("    derived from the client's records; the last column is the "
          "scheduler's own gauge where it exists")
    print(df[["label", "nochat_pct", "nochat_instances", "gate_nochat_pct"]]
          .to_string(index=False, float_format="%.1f"))

    if a.csv:
        df.to_csv(a.csv, index=False)
        print(f"\nwrote {a.csv}")


if __name__ == "__main__":
    main()
