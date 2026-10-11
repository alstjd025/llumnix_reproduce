#!/usr/bin/env python3
"""Did the long-held requests land on instances with a deep prefill queue?

`27_waiting_selects_bad_placements.md` measured that a placed request's
post-placement time grows tenfold with how long it was held, and §4b derived from
the code that those placements are ROUTES -- feasibility turned true -- and that
feasibility has no term about when this request will see its own first token:

    c.feasible = !unpredictable && !overGate && !overIncumbents && !overMemory

all four being about per-token pace and KV. The derivation says a pace inside the
gate is compatible with a long prefill queue ahead of the request, and that the
queue is what the post-placement time is made of. THAT IS A DERIVATION, NOT A
MEASUREMENT, and this script turns it into one.

For every placed request: join its dispatch instant and instance to that
instance's own gauges at the nearest scrape, and bucket by how long the request
was held. The prediction to confirm or refute:

  the pace          `observed_step_ms` INSIDE `gate_allowance_ms` -- i.e. the
                    placement really was a route, not a violation of the gate
  the queue         `queued_prefill_tokens` HIGH, and higher the longer the hold

A refutation looks like: the queue is flat across hold buckets, in which case the
post-placement time comes from something else and §4b's mechanism is wrong.

Read only.  Prints; writes one CSV.
"""
import datetime, glob, json, os, re, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run
from tail2026_holdwindow import RESULTS
from tail2026_holdwindow_state import dispatch_pairs

GAUGES = ["queued_prefill_tokens", "arriving_prefill_tokens", "observed_step_ms",
          "gate_allowance_ms", "obs_decode_batch", "prefill_duty"]
NAME = re.compile(r"^\d{6}_\d{4}_exp(82|84a1)r\d_fspfx_m1_rpm_(2100|2700)$")
BUCKETS = [(0, 500), (500, 2000), (2000, 5000), (5000, 40000)]
TOL = 1.5          # seconds; a dispatch is matched to a scrape within this


def series(run_dir):
    """{instance id: DataFrame indexed by scrape time} for the gauges we need."""
    acc = {}
    p = os.path.join(run_dir, "server_metrics", "scheduler.jsonl")
    with open(p, errors="replace") as fh:
        for line in fh:
            try:
                q = json.loads(line)
            except ValueError:
                continue
            t = q.get("t")
            if t is None:
                continue
            for k, v in q.items():
                if "|instance=" not in k or v is None:
                    continue
                name, inst = k.split("|instance=", 1)
                name = name.replace("scheduler_fluidserve_", "")
                if name not in GAUGES:
                    continue
                acc.setdefault(inst, {}).setdefault("t", []) if False else None
                acc.setdefault(inst, []).append((float(t), name, float(v)))
    out = {}
    for inst, recs in acc.items():
        df = pd.DataFrame(recs, columns=["t", "name", "v"])
        out[inst] = df.pivot_table(index="t", columns="name", values="v",
                                   aggfunc="last").sort_index()
    return out


rows = []
for d in sorted(glob.glob(os.path.join(RESULTS, "*_fspfx_m1_rpm_*"))):
    b = os.path.basename(d)
    if not NAME.match(b):
        continue
    rate = int(b.rsplit("_", 1)[1]) // 60
    r = load_run(d).copy()
    r["call_index"] = pd.to_numeric(r["call_index"], errors="coerce")
    ids = []
    with open(os.path.join(d, "request_ids.jsonl"), errors="replace") as fh:
        for line in fh:
            try:
                q = json.loads(line)
            except ValueError:
                continue
            ids.append((q.get("task_id"), q.get("call_index"),
                        str(q.get("request_id", "")).replace("cmpl-", ""),
                        q.get("start_time")))
    idf = pd.DataFrame(ids, columns=["task_id", "call_index", "rid", "sent"])
    idf["call_index"] = pd.to_numeric(idf["call_index"], errors="coerce")
    year = datetime.datetime.fromtimestamp(float(r["start_time"].min()),
                                           datetime.timezone.utc).year
    disp = dispatch_pairs(d, year)
    idf["disp_t"] = [disp[x][0] if x in disp else np.nan for x in idf["rid"]]
    idf["inst"] = [disp[x][1] if x in disp else None for x in idf["rid"]]
    idf["hold_ms"] = (idf["disp_t"] - pd.to_numeric(idf["sent"], errors="coerce")) * 1000.0
    m = r.merge(idf[["task_id", "call_index", "disp_t", "inst", "hold_ms"]],
                on=["task_id", "call_index"], how="left")
    m = m[~m["rejected"] & ~m["cutoff"] & ~m["errored"]
          & m["disp_t"].notna() & m["inst"].notna()].copy()
    m["ttft"] = pd.to_numeric(m["first_token_latency"], errors="coerce") * 1000.0
    m["p2ft"] = m["ttft"] - m["hold_ms"]
    m = m[m["p2ft"] > 0]
    ser = series(d)
    matched = 0
    for g in GAUGES:
        m[g] = np.nan
    for inst, s in ser.items():
        sel = m["inst"] == inst
        if not sel.any() or s.empty:
            continue
        idx = np.searchsorted(s.index.to_numpy(), m.loc[sel, "disp_t"].to_numpy())
        idx = np.clip(idx, 0, len(s) - 1)
        near = s.index.to_numpy()[idx]
        ok = np.abs(near - m.loc[sel, "disp_t"].to_numpy()) <= TOL
        for g in GAUGES:
            if g in s.columns:
                vals = s[g].to_numpy()[idx]
                vals = np.where(ok, vals, np.nan)
                m.loc[sel, g] = vals
        matched += int(ok.sum())
    join = 100.0 * matched / max(len(m), 1)
    for c in ("chat", "deepresearch"):
        s = m[m["class"] == c]
        for lo, hi in BUCKETS:
            g = s[(s["hold_ms"] >= lo) & (s["hold_ms"] < hi)]
            g = g[g["queued_prefill_tokens"].notna()]
            if len(g) < 20:
                continue
            rows.append(dict(
                rate=rate, cls=c, bucket=f"[{lo},{hi})", n=len(g), join_pct=join,
                p2ft_p50=float(g["p2ft"].median()),
                queued_p50=float(g["queued_prefill_tokens"].median()),
                queued_p90=float(g["queued_prefill_tokens"].quantile(0.90)),
                arriving_p50=float(g["arriving_prefill_tokens"].median()),
                step_p50=float(g["observed_step_ms"].median()),
                gate_p50=float(g["gate_allowance_ms"].median()),
                inside_gate_pct=100.0 * float((g["observed_step_ms"]
                                               <= g["gate_allowance_ms"]).mean()),
                batch_p50=float(g["obs_decode_batch"].median()),
                duty_p50=float(g["prefill_duty"].median())))

df = pd.DataFrame(rows)
df.to_csv(os.path.join(RESULTS, "aggregate_analysis", "tail_2026-08-16",
                       "28_backlog_at_placement.csv"), index=False)


def band(g, col, f="%.0f"):
    v = pd.to_numeric(g[col], errors="coerce").dropna()
    if not len(v):
        return "--"
    if len(v) == 1 or abs(v.max() - v.min()) < 0.5:
        return f % v.mean()
    return (f + "-" + f) % (v.min(), v.max())


print(f"join of dispatch instants to the instance's own scrapes: "
      f"{df['join_pct'].min():.1f}-{df['join_pct'].max():.1f}%\n")
for rate in (35, 45):
    print(f"=== {rate} req/s   the instance's state AT THE MOMENT OF PLACEMENT")
    print(f"{'class':13s} {'hold':>13s} {'n':>7s} {'p2ft p50':>9s} "
          f"{'queued prefill p50':>19s} {'p90':>9s} {'step p50':>9s} {'gate':>6s} "
          f"{'pace inside gate':>17s} {'batch':>7s}")
    for c in ("chat", "deepresearch"):
        for lo, hi in BUCKETS:
            g = df[(df.rate == rate) & (df.cls == c) & (df.bucket == f"[{lo},{hi})")]
            if not len(g):
                continue
            print(f"{c:13s} {f'[{lo},{hi})':>13s} {band(g,'n'):>7s} {band(g,'p2ft_p50'):>9s} "
                  f"{band(g,'queued_p50'):>19s} {band(g,'queued_p90'):>9s} "
                  f"{band(g,'step_p50','%.1f'):>9s} {band(g,'gate_p50','%.0f'):>6s} "
                  f"{band(g,'inside_gate_pct','%.1f')+'%':>17s} {band(g,'batch_p50'):>7s}")
    print()
