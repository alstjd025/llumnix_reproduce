"""Tail comparison across all five control planes and ALL FOUR classes.

Two different quantities, and the difference is the whole point:

  WITHIN a request   take ONE request, look at the gaps between ITS OWN tokens,
                     take the p90 of those gaps. Do that for every request, then
                     report the median over requests. "Inside one answer, how
                     slow are the slow moments."
  ACROSS requests    take ONE request, average the gaps between its tokens ->
                     one number per request. Then take the p90 over requests.
                     "Which requests were slow on average."

Both are per request. They differ in which variance they see, and a system can be
good at one and bad at the other.
"""
import os, sys
import numpy as np, pandas as pd
sys.path.insert(0, "/home/nxclab/llumnix_reproduce/Agent_applications/"
                  "agent_motivation_experiment/analysis_scripts/request_level")
from exp22_fluidserve import load_run

BASE = ("/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
        "/paper_experiment/static_sweep_2026-08")
OUT = ("/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
       "/results/aggregate_analysis/tail_2026-08-16/25_tails_all_arms.csv")
ARMS = ["FluidServe", "llm-d", "vLLM router", "Llumnix SLO", "PolyServe"]
RATES = [10.0, 15.0, 20.0, 25.0, 35.0, 45.0, 55.0, 70.0]
CLASSES = ["chat", "deepresearch", "swe", "ALL"]

man = pd.read_csv(os.path.join(BASE, "manifest.tsv"), sep="\t")
rows = []
for _, m in man.iterrows():
    d = os.path.join(BASE, "data", m["run"])
    if not os.path.isdir(d):
        continue
    r = load_run(d)
    if r is None or r.empty:
        continue
    for c in CLASSES:
        s = r if c == "ALL" else r[r["class"] == c]
        live = s[~s["cutoff"]]
        if len(live) < 40:
            continue
        done = live[~live["rejected"] & ~live["errored"]]
        if len(done) < 20:
            continue
        gg = lambda col: pd.to_numeric(done.get(col), errors="coerce").dropna()
        itl = gg("itl_ms")
        win = float(r["rel"].max() - r["rel"].min())
        met = live[~live["violate_offered"]]
        rec = dict(arm=m["arm_label"], rate=float(m["req_per_s"]), cls=c,
                   n_arr=len(live), n_done=len(done),
                   rej=100.0 * float(live["rejected"].mean()),
                   goodput=(pd.to_numeric(met.get("output_tokens"), errors="coerce")
                            .fillna(0).sum() / win) if win > 0 else np.nan)
        for tag, col in (("w_p50", "tbt_p50_ms"), ("w_p90", "tbt_p90_ms"),
                         ("w_p95", "tbt_p95_ms"), ("w_max", "tbt_max_ms")):
            v = gg(col)
            rec[tag] = float(v.median()) if len(v) else np.nan
        for tag, q in (("a_p50", .50), ("a_p90", .90), ("a_p95", .95), ("a_p99", .99)):
            rec[tag] = float(itl.quantile(q)) if len(itl) else np.nan
        rows.append(rec)

df = pd.DataFrame(rows)
df.to_csv(OUT, index=False)


def cell(g, col, f="%.0f"):
    v = pd.to_numeric(g[col], errors="coerce").dropna()
    if not len(v):
        return "  --"
    if len(v) == 1 or abs(v.max() - v.min()) < 0.5:
        return f % v.mean()
    return (f + "-" + f) % (v.min(), v.max())


def panel(title, col, fmt="%.0f", note=""):
    print(f"\n########## {title}")
    if note:
        print(f"# {note}")
    for c in CLASSES:
        print(f"\n-- {c}")
        print(f"{'arm':13s}" + "".join(f"{int(r):>12d}" for r in RATES))
        for a in ARMS:
            line = f"{a:13s}"
            for rate in RATES:
                g = df[(df.arm == a) & (df.rate == rate) & (df.cls == c)]
                line += f"{cell(g, col, fmt):>12s}"
            print(line)


panel("WITHIN a request: p90 of that request's own token gaps, ms (median over requests)",
      "w_p90", note="chat budget 50 ms, deepresearch 100 ms; swe is scored end-to-end so it has no per-token budget")
panel("WITHIN a request: p50, ms", "w_p50")
panel("WITHIN a request: p95, ms", "w_p95")
panel("WITHIN a request: MAX, ms", "w_max")
panel("ACROSS requests: p90 of the per-request MEAN token gap, ms", "a_p90")
panel("ACROSS requests: p99 of the per-request MEAN token gap, ms", "a_p99")
panel("context: rejection rate, %", "rej", "%.1f")
panel("context: token goodput, output tokens/s meeting the rule", "goodput")
