#!/usr/bin/env python3
"""What the gateway hold window actually changes: EXP-83 (5,000/1,000) vs EXP-82 (35,000/500).

THE TWO CONDITIONS.  `--wait-scheduling-timeout` is how long the gateway keeps
re-asking the scheduler for a placement before it gives up and returns an error
to the client; `--wait-scheduling-retry-interval` is how often it re-asks inside
that window.  `ms_dev/scripts/set_scheduler_profiling.py` gives 35,000 ms/500 ms
when the policy is fluidserve and 5,000 ms/1,000 ms otherwise.  EXP-83 set
`FS_GATEWAY_STOCK=1`, which forces the shipped 5,000/1,000 pair on FluidServe
too; the driver `/home/nxclab/tools/exp83.sh` changes nothing else, so the pair
of flags is the whole treatment.

NAMING HAZARD.  EXP-83's directories are `*exp83r[12]_fspfx_m1_rpm_*`, whose arm
segment is character-for-character the arm segment of an ordinary FluidServe run.
They are a DIFFERENT system.  This script separates them by the experiment number
in the session prefix and never globs `*_fspfx_m1_rpm_*` on its own.

WHAT IT PRODUCES, in the order the questions were asked.

  1. per class      arrivals, rejection rate, attainment on the offered and the
                    admitted denominator, time to first token p50/p90, end-to-end
                    p50/p90, and completed requests per second, for chat,
                    deepresearch and swe at each of the three arrival rates under
                    both settings.  Reported as min..max over the two repeats,
                    because a difference inside that spread is not a difference.

  3. held requests  the hold before placement, measured per request rather than
                    inferred.  The gateway's scheduler log records
                    `[Schedule] dispatch request <id>` with a wall-clock stamp in
                    UTC, `request_ids.jsonl` records the client's send time and
                    the same id, and both clocks are UTC, so
                    dispatch - send IS the hold.  Only placed requests appear in
                    `request_ids.jsonl`; for a request that was refused, the
                    client's `latency` is the time from send to the refusal,
                    which is the hold that ended in a refusal.  The join rate is
                    reported for every run rather than assumed, because the
                    scheduler log drops lines under load.

  4. the two knobs  scheduling calls per arrival, decisions by kind
                    (route/force/pend/shed) and the gateway's own give-up and
                    rejection counters, from `server_metrics/scheduler.jsonl`
                    and `server_metrics/gateway.jsonl`.  These are the counters
                    that say whether the ceiling ever bound at all.

  fleet state       the instance-level series the decision reads -- queued
                    prefill, observed KV, the admissible KV cap, the observed
                    iteration time and the gate allowance -- because the
                    per-request tables say what happened and these say what the
                    scheduler was looking at when it happened.

  python3 tail2026_holdwindow.py --out <directory>
"""
import argparse
import bisect
import datetime
import glob
import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, CLASSES, WARMUP_S, DRAIN_S  # noqa: E402

EXPDIR = ("/home/nxclab/llumnix_reproduce/Agent_applications/"
          "agent_motivation_experiment")
RESULTS = os.path.join(EXPDIR, "results")

# The control is EXP-82 and the treatment is EXP-83.  Both are `fspfx` on mix
# `m1`; the experiment number is the only thing in the directory name that tells
# them apart, which is why it is matched explicitly.
NAME_RE = re.compile(r"^\d{6}_\d{4}_exp(?P<exp>8[23])r(?P<rep>\d)_fspfx_m1_"
                     r"rpm_(?P<rpm>\d+)$")
RATES = [1500, 2100, 2700]          # 25, 35, 45 req/s
SETTING = {"82": "35,000 / 500", "83": "5,000 / 1,000"}

SCHED_RE = re.compile(r"^I(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{6})\s+\d+\s+"
                      r"\S+\] \[Schedule\] dispatch request ([0-9a-f-]{36})")


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


# ---------------------------------------------------------------- task 1 ----
def per_class_rows(run_dir, exp, rep, rpm):
    """One row per class for one run, on both denominators.

    The scoring is `exp22_fluidserve.load_run`'s and is not reimplemented: a
    request violates if it broke its class rule, or was rejected, or errored, or
    produced no first token.  `cutoff` rows -- streams the run boundary
    truncated -- leave both denominators, their outcome never having been
    determined.

    Latency percentiles are taken over COMPLETED requests only.  A rejected
    request has no latency to speak of and a truncated one's latency is a lower
    bound, so including either would report a number that is not a measurement
    of what it claims to measure.  The attainment columns do not drop them:
    there they are violations, which is the whole point of the offered
    denominator.
    """
    r = load_run(run_dir)
    if r is None or r.empty:
        return []
    window_s = float(r["rel"].max() - r["rel"].min())
    out = []
    for c in CLASSES + ["ALL"]:
        s = r if c == "ALL" else r[r["class"] == c]
        if not len(s):
            continue
        live = s[~s["cutoff"]]
        served = live[~live["rejected"]]
        done = served[~served["errored"]]
        ttft = pd.to_numeric(done["first_token_latency"], errors="coerce").dropna()
        e2e = pd.to_numeric(done["latency"], errors="coerce").dropna()
        met = live[~live["violate_offered"]]
        out.append(dict(
            exp=exp, rep=rep, rpm=rpm, cls=c,
            arrivals=len(s),
            rejected_pct=100.0 * live["rejected"].mean() if len(live) else np.nan,
            attain_offered=100.0 * (~live["violate_offered"]).mean() if len(live) else np.nan,
            attain_admitted=100.0 * (~served["violate_served"]).mean() if len(served) else np.nan,
            ttft_p50=ttft.quantile(0.50) * 1000 if len(ttft) else np.nan,
            ttft_p90=ttft.quantile(0.90) * 1000 if len(ttft) else np.nan,
            e2e_p50=e2e.quantile(0.50) if len(e2e) else np.nan,
            e2e_p90=e2e.quantile(0.90) if len(e2e) else np.nan,
            met_per_s=len(met) / window_s if window_s > 0 else np.nan,
            goodput_tok_per_s=(pd.to_numeric(met.get("output_tokens"),
                                             errors="coerce").fillna(0).sum()
                               / window_s) if window_s > 0 else np.nan,
        ))
    return out


# ---------------------------------------------------------------- task 3 ----
def dispatch_times(run_dir, year):
    """First dispatch instant for every request id in the scheduler's log.

    The log stamps `I<MM><DD> HH:MM:SS.ffffff` with no year, and the process
    clock is UTC (checked against `request_ids.jsonl`, whose epochs land on the
    same second).  The year comes from the client's own first send.  A request
    can appear more than once if it is re-dispatched, and the FIRST appearance is
    the one that ends the hold, so later ones are discarded.
    """
    p = os.path.join(run_dir, "server_metrics", "scheduler_dispatch.log")
    out = {}
    if not os.path.isfile(p):
        return out
    with open(p, errors="replace") as fh:
        for line in fh:
            if "[Schedule] dispatch request " not in line:
                continue
            m = SCHED_RE.match(line)
            if not m:
                continue
            mo, dy, hh, mm, ss, us, rid = m.groups()
            t = datetime.datetime(year, int(mo), int(dy), int(hh), int(mm),
                                  int(ss), int(us),
                                  tzinfo=datetime.timezone.utc).timestamp()
            if rid not in out or t < out[rid]:
                out[rid] = t
    return out


def hold_frame(run_dir, exp, rep, rpm):
    """Per-request hold time joined to the request's fate.

    Three populations, and they come from different places because the system
    records them in different places:

      placed     `request_ids.jsonl` gives (task_id, call_index, request_id,
                 client send time) for every request that got an endpoint; the
                 scheduler log gives the instant it got one.  The difference is
                 the hold.  Whether it then met its rule comes from `load_run`.
      refused    a refused request never gets an endpoint and so is absent from
                 `request_ids.jsonl`.  Its client-side `latency` is send-to-
                 refusal, which is the hold that ended in a refusal.

    The join rate is returned, not assumed.  The scheduler runs at a verbosity
    that drops lines under load, so a placed request can be missing its dispatch
    stamp; those rows are counted and excluded rather than filled in.
    """
    r = load_run(run_dir)
    if r is None or r.empty:
        return None, {}
    key = ["task_id", "call_index"]
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
                            str(d.get("request_id", "")).replace("cmpl-", ""),
                            d.get("start_time")))
    idf = pd.DataFrame(ids, columns=["task_id", "call_index", "rid", "sent"])
    year = datetime.datetime.fromtimestamp(
        float(r["start_time"].min()), datetime.timezone.utc).year
    disp = dispatch_times(run_dir, year)

    stats = dict(exp=exp, rep=rep, rpm=rpm,
                 n_window=len(r),
                 n_placed=int((~r["rejected"]).sum()),
                 n_refused=int(r["rejected"].sum()),
                 n_ids=len(idf), n_dispatch_lines=len(disp))

    if len(idf):
        idf["hold_ms"] = [
            (disp[rid] - sent) * 1000.0 if rid in disp and pd.notna(sent) else np.nan
            for rid, sent in zip(idf["rid"], idf["sent"])]
        idf["call_index"] = pd.to_numeric(idf["call_index"], errors="coerce")
        m = r.merge(idf[key + ["hold_ms"]], on=key, how="left")
    else:
        m = r.copy()
        m["hold_ms"] = np.nan

    # A refused request's hold is the client-observed latency of the refusal.
    ref = m["rejected"]
    m.loc[ref, "hold_ms"] = pd.to_numeric(m.loc[ref, "latency"],
                                          errors="coerce") * 1000.0

    placed = m[~ref]
    stats["join_pct"] = (100.0 * placed["hold_ms"].notna().mean()
                         if len(placed) else np.nan)
    m["outcome"] = np.where(
        m["rejected"], "refused",
        np.where(m["violate_offered"], "placed_missed", "placed_met"))
    m.loc[m["cutoff"], "outcome"] = "truncated"
    return m, stats


def hold_summary(m, exp, rep, rpm):
    rows = []
    for c in CLASSES + ["ALL"]:
        s = m if c == "ALL" else m[m["class"] == c]
        for oc in ["placed_met", "placed_missed", "refused"]:
            h = pd.to_numeric(s.loc[s["outcome"] == oc, "hold_ms"],
                              errors="coerce").dropna()
            if not len(h):
                continue
            rows.append(dict(exp=exp, rep=rep, rpm=rpm, cls=c, outcome=oc,
                             n=len(h), p50=h.quantile(.50), p90=h.quantile(.90),
                             p99=h.quantile(.99), mx=h.max()))
    return rows


def long_hold_fate(m, exp, rep, rpm, cuts=(1000, 2000, 5000, 10000)):
    """Of the requests held at least this long, what share went on to meet?

    The point of the question: if a long hold is followed by a violation almost
    every time, then the hold produced a certain failure and the ceiling that cut
    it short was hiding a defect in the test that allowed it, not costing
    anything.  Refused requests are in the denominator -- a hold that ended in a
    refusal is a hold that delivered nothing -- and truncated ones are not,
    their outcome never having been determined.
    """
    rows = []
    s = m[m["outcome"] != "truncated"].copy()
    s["hold_ms"] = pd.to_numeric(s["hold_ms"], errors="coerce")
    s = s[s["hold_ms"].notna()]
    for c in CLASSES + ["ALL"]:
        t = s if c == "ALL" else s[s["class"] == c]
        for cut in cuts:
            u = t[t["hold_ms"] >= cut]
            if not len(u):
                rows.append(dict(exp=exp, rep=rep, rpm=rpm, cls=c, cut=cut,
                                 n=0, met_pct=np.nan, refused_pct=np.nan))
                continue
            rows.append(dict(
                exp=exp, rep=rep, rpm=rpm, cls=c, cut=cut, n=len(u),
                met_pct=100.0 * (u["outcome"] == "placed_met").mean(),
                refused_pct=100.0 * (u["outcome"] == "refused").mean()))
    return rows


# ---------------------------------------------------------------- task 4 ----
GW_KEYS = ["gateway_scheduling_gave_up_total", "gateway_scheduling_rejected_total",
           "gateway_scheduling_waited_total",
           "gateway_scheduling_wait_milliseconds_count",
           "gateway_scheduling_wait_milliseconds_sum"]
SC_KEYS = ["scheduler_scheduling_total"] + [
    "scheduler_fluidserve_decisions_total|decision=" + d
    for d in ("route", "pend", "force", "shed")] + [
    "scheduler_fluidserve_infeasible_total|reason=" + s
    for s in ("gate", "incumbents", "memory")]
GAUGES = ["obs_kv_tokens", "cap_kv_tokens", "queued_prefill_tokens",
          "arriving_prefill_tokens", "observed_step_ms", "gate_allowance_ms",
          "live_requests", "prefill_duty"]


def last_values(path, keys):
    """The final value of each counter.

    The scrape is cumulative and monotone within a run, so the last sample is
    the run total.  Keys absent from every sample were never incremented, which
    is a zero and not a missing value -- that distinction is the whole content of
    the give-up counter under the 35,000 ms ceiling.
    """
    out = {k: 0.0 for k in keys}
    seen = {k: False for k in keys}
    if not os.path.isfile(path):
        return out, seen
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for k in keys:
                if d.get(k) is not None:
                    out[k], seen[k] = float(d[k]), True
    return out, seen


def state_series(path):
    """Fleet-state gauges, summarised per instance and then over instances.

    Occupancy and queue depth have a ceiling and a skew, so the median and the
    90th percentile are both reported: an average hides how often the quantity
    is at its ceiling, which is the thing that matters for it.
    """
    per = {g: [] for g in GAUGES}
    if not os.path.isfile(path):
        return {}
    with open(path, errors="replace") as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            for g in GAUGES:
                pre = "scheduler_fluidserve_" + g + "|"
                vs = [v for k, v in d.items() if k.startswith(pre) and v is not None]
                if vs:
                    per[g].append(float(np.mean(vs)))
    out = {}
    for g, vs in per.items():
        if vs:
            a = np.asarray(vs, dtype=float)
            out[g + "_p50"] = float(np.median(a))
            out[g + "_p90"] = float(np.percentile(a, 90))
    return out


def counter_row(run_dir, exp, rep, rpm, arrivals):
    sm = os.path.join(run_dir, "server_metrics")
    gw, gseen = last_values(os.path.join(sm, "gateway.jsonl"), GW_KEYS)
    sc, _ = last_values(os.path.join(sm, "scheduler.jsonl"), SC_KEYS)
    route = sc["scheduler_fluidserve_decisions_total|decision=route"]
    pend = sc["scheduler_fluidserve_decisions_total|decision=pend"]
    force = sc["scheduler_fluidserve_decisions_total|decision=force"]
    shed = sc["scheduler_fluidserve_decisions_total|decision=shed"]
    placed = route + force
    calls = placed + pend + shed
    waitn = gw["gateway_scheduling_wait_milliseconds_count"]
    row = dict(exp=exp, rep=rep, rpm=rpm, arrivals=arrivals,
               route=route, force=force, pend=pend, shed=shed,
               placed=placed, calls=calls,
               gave_up=gw["gateway_scheduling_gave_up_total"],
               gw_rejected=gw["gateway_scheduling_rejected_total"],
               waited=gw["gateway_scheduling_waited_total"],
               mean_wait_ms=(gw["gateway_scheduling_wait_milliseconds_sum"] / waitn
                             if waitn else np.nan),
               calls_per_arrival=calls / arrivals if arrivals else np.nan,
               pend_per_arrival=pend / arrivals if arrivals else np.nan,
               held_request_seconds=np.nan,
               infeas_gate=sc["scheduler_fluidserve_infeasible_total|reason=gate"],
               infeas_incumbents=sc["scheduler_fluidserve_infeasible_total|reason=incumbents"],
               infeas_memory=sc["scheduler_fluidserve_infeasible_total|reason=memory"])
    # Each pend decision holds the request for one retry period, so the pend
    # count times that period is the total request-time spent held at the
    # gateway.  It is the one quantity that puts the two settings' holding on the
    # same scale despite their different cadences.
    period = 1.0 if exp == "83" else 0.5
    row["held_request_seconds"] = pend * period
    row.update(state_series(os.path.join(sm, "scheduler.jsonl")))
    return row


# ------------------------------------------------------------------ main ----
def band(g, col, fmt="%.1f"):
    """min..max over the repeats, which is the only spread two runs support."""
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

    cls_rows, cnt_rows, hold_rows, fate_rows, join_rows = [], [], [], [], []
    for d, exp, rep, rpm in run_dirs():
        print("reading", os.path.basename(d), file=sys.stderr)
        rows = per_class_rows(d, exp, rep, rpm)
        cls_rows += rows
        arrivals = next((r["arrivals"] for r in rows if r["cls"] == "ALL"), 0)
        cnt_rows.append(counter_row(d, exp, rep, rpm, arrivals))
        m, st = hold_frame(d, exp, rep, rpm)
        join_rows.append(st)
        if m is not None:
            hold_rows += hold_summary(m, exp, rep, rpm)
            fate_rows += long_hold_fate(m, exp, rep, rpm)

    for name, rows in [("perclass", cls_rows), ("counters", cnt_rows),
                       ("holds", hold_rows), ("longhold", fate_rows),
                       ("join", join_rows)]:
        df = pd.DataFrame(rows)
        p = os.path.join(a.out, "19_holdwindow_%s.csv" % name)
        df.to_csv(p, index=False)
        print("wrote", p, file=sys.stderr)

    # A compact console view so the tables can be read without opening the CSVs.
    pc = pd.DataFrame(cls_rows)
    for rpm in RATES:
        print("\n=== %d req/s ===" % (rpm // 60))
        print("%-13s %-14s %8s %8s %8s %8s %9s %9s %9s %9s %9s" % (
            "class", "window", "arr", "rej%", "off%", "adm%", "ttft50",
            "ttft90", "e2e50", "e2e90", "met/s"))
        for c in CLASSES + ["ALL"]:
            for exp in ("82", "83"):
                g = pc[(pc.rpm == rpm) & (pc.cls == c) & (pc.exp == exp)]
                if not len(g):
                    continue
                print("%-13s %-14s %8s %8s %8s %8s %9s %9s %9s %9s %9s" % (
                    c, SETTING[exp], band(g, "arrivals", "%.0f"),
                    band(g, "rejected_pct"), band(g, "attain_offered"),
                    band(g, "attain_admitted"), band(g, "ttft_p50", "%.0f"),
                    band(g, "ttft_p90", "%.0f"), band(g, "e2e_p50", "%.1f"),
                    band(g, "e2e_p90", "%.1f"), band(g, "met_per_s", "%.1f")))

    cn = pd.DataFrame(cnt_rows)
    print("\n=== decisions and calls ===")
    print("%-6s %-14s %8s %8s %8s %8s %8s %8s %8s %8s" % (
        "req/s", "window", "placed", "force", "pend", "shed", "gaveup",
        "calls/req", "held_s", "meanwait"))
    for rpm in RATES:
        for exp in ("82", "83"):
            g = cn[(cn.rpm == rpm) & (cn.exp == exp)]
            if not len(g):
                continue
            print("%-6d %-14s %8s %8s %8s %8s %8s %8s %8s %8s" % (
                rpm // 60, SETTING[exp], band(g, "placed", "%.0f"),
                band(g, "force", "%.0f"), band(g, "pend", "%.0f"),
                band(g, "shed", "%.0f"), band(g, "gave_up", "%.0f"),
                band(g, "calls_per_arrival", "%.2f"),
                band(g, "held_request_seconds", "%.0f"),
                band(g, "mean_wait_ms", "%.0f")))

    print("\n=== fleet state the decision reads (per-instance mean, over samples) ===")
    print("%-6s %-14s %10s %10s %10s %10s %10s %10s" % (
        "req/s", "window", "queuedpf", "obs_kv", "cap_kv", "step_ms",
        "gate_ms", "live"))
    for rpm in RATES:
        for exp in ("82", "83"):
            g = cn[(cn.rpm == rpm) & (cn.exp == exp)]
            if not len(g):
                continue
            print("%-6d %-14s %10s %10s %10s %10s %10s %10s" % (
                rpm // 60, SETTING[exp],
                band(g, "queued_prefill_tokens_p50", "%.0f"),
                band(g, "obs_kv_tokens_p50", "%.0f"),
                band(g, "cap_kv_tokens_p50", "%.0f"),
                band(g, "observed_step_ms_p50", "%.1f"),
                band(g, "gate_allowance_ms_p50", "%.1f"),
                band(g, "live_requests_p50", "%.0f")))

    hd = pd.DataFrame(hold_rows)
    print("\n=== hold before placement or refusal, ms ===")
    print("%-6s %-14s %-13s %-14s %7s %8s %8s %8s" % (
        "req/s", "window", "class", "outcome", "n", "p50", "p90", "max"))
    for rpm in RATES:
        for exp in ("82", "83"):
            for c in CLASSES:
                for oc in ("placed_met", "placed_missed", "refused"):
                    g = hd[(hd.rpm == rpm) & (hd.exp == exp) & (hd.cls == c)
                           & (hd.outcome == oc)]
                    if not len(g):
                        continue
                    print("%-6d %-14s %-13s %-14s %7s %8s %8s %8s" % (
                        rpm // 60, SETTING[exp], c, oc, band(g, "n", "%.0f"),
                        band(g, "p50", "%.0f"), band(g, "p90", "%.0f"),
                        band(g, "mx", "%.0f")))

    ft = pd.DataFrame(fate_rows)
    print("\n=== of requests held at least X, what share met their rule ===")
    print("%-6s %-14s %-13s %6s %8s %8s %9s" % (
        "req/s", "window", "class", "cut", "n", "met%", "refused%"))
    for rpm in RATES:
        for exp in ("82", "83"):
            for c in CLASSES + ["ALL"]:
                for cut in (1000, 2000, 5000, 10000):
                    g = ft[(ft.rpm == rpm) & (ft.exp == exp) & (ft.cls == c)
                           & (ft.cut == cut)]
                    if not len(g) or pd.to_numeric(g["n"]).max() == 0:
                        continue
                    print("%-6d %-14s %-13s %6d %8s %8s %9s" % (
                        rpm // 60, SETTING[exp], c, cut, band(g, "n", "%.0f"),
                        band(g, "met_pct"), band(g, "refused_pct")))

    jn = pd.DataFrame(join_rows)
    print("\n=== join rate of the dispatch log onto placed requests ===")
    print(jn.to_string(index=False))


if __name__ == "__main__":
    main()
