"""Was canWait's post-placement reserve short for the requests it held longest?

canWait allows a hold while `waited < ttftSlo - after - recheck`, where `after` is
what it reserves for everything that happens after the placement. If that reserve
is right, a request it holds and then places should reach its first token inside
its time-to-first-token budget. So every PLACED request that missed that budget is
a case where either the hold was allowed past the budget (a different defect) or
the reserve was short.

Split the overshoot:  TTFT = hold + p2ft,  where p2ft = time from the scheduler's
dispatch to the client's first token. Both are per request and already on disk.

Reported per class, per hold bucket: how many placed requests missed their TTFT
budget, and how the time divides.
"""
import datetime, glob, json, os, re, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/nxclab/llumnix_reproduce/Agent_applications/"
                  "agent_motivation_experiment/analysis_scripts/request_level")
from exp22_fluidserve import load_run
from tail2026_holdwindow import RESULTS
from tail2026_holdwindow_state import dispatch_pairs

TT = {"chat": 5000.0, "deepresearch": 10000.0}
NAME = re.compile(r"^\d{6}_\d{4}_exp(82|84a1)r\d_fspfx_m1_rpm_(2100|2700)$")
BUCKETS = [(0, 500), (500, 2000), (2000, 5000), (5000, 40000)]

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
    idf["hold_ms"] = [(disp[x][0] - s) * 1000.0 if x in disp and s is not None else np.nan
                      for x, s in zip(idf["rid"], idf["sent"])]
    m = r.merge(idf[["task_id", "call_index", "hold_ms"]], on=["task_id", "call_index"],
                how="left")
    m = m[~m["rejected"] & ~m["cutoff"] & ~m["errored"] & m["hold_ms"].notna()]
    m = m.copy()
    m["ttft"] = pd.to_numeric(m["first_token_latency"], errors="coerce") * 1000.0
    m["p2ft"] = m["ttft"] - m["hold_ms"]
    m = m[m["ttft"].notna() & (m["p2ft"] > 0)]
    for c in ("chat", "deepresearch"):
        s = m[m["class"] == c]
        if len(s) < 50:
            continue
        for lo, hi in BUCKETS:
            g = s[(s["hold_ms"] >= lo) & (s["hold_ms"] < hi)]
            if len(g) < 20:
                continue
            miss = g[g["ttft"] > TT[c]]
            rows.append(dict(rate=rate, cls=c, bucket=f"[{lo},{hi})", n=len(g),
                             miss_pct=100.0 * len(miss) / len(g),
                             hold_p50=float(g["hold_ms"].median()),
                             p2ft_p50=float(g["p2ft"].median()),
                             p2ft_p90=float(g["p2ft"].quantile(0.90)),
                             miss_hold_p50=float(miss["hold_ms"].median()) if len(miss) else np.nan,
                             miss_p2ft_p50=float(miss["p2ft"].median()) if len(miss) else np.nan))
df = pd.DataFrame(rows)

def band(g, col, f="%.0f"):
    v = pd.to_numeric(g[col], errors="coerce").dropna()
    if not len(v): return "--"
    if len(v) == 1 or abs(v.max()-v.min()) < 0.5: return f % v.mean()
    return (f+"-"+f) % (v.min(), v.max())

for rate in (35, 45):
    print(f"\n=== {rate} req/s   placed requests only, by how long they were held")
    print(f"{'class':13s} {'hold bucket':>13s} {'n':>7s} {'missed TTFT %':>14s} "
          f"{'hold p50':>10s} {'p2ft p50':>10s} {'p2ft p90':>10s}"
          f" | {'of the misses: hold p50':>24s} {'p2ft p50':>10s}")
    for c in ("chat", "deepresearch"):
        for lo, hi in BUCKETS:
            g = df[(df.rate == rate) & (df.cls == c) & (df.bucket == f"[{lo},{hi})")]
            if not len(g): continue
            print(f"{c:13s} {f'[{lo},{hi})':>13s} {band(g,'n'):>7s} {band(g,'miss_pct','%.1f'):>14s} "
                  f"{band(g,'hold_p50'):>10s} {band(g,'p2ft_p50'):>10s} {band(g,'p2ft_p90'):>10s}"
                  f" | {band(g,'miss_hold_p50'):>24s} {band(g,'miss_p2ft_p50'):>10s}")
