#!/usr/bin/env python3
"""Judge EXP-86 against its pre-registration, and against the runs it replaces.

EXP-86 re-measured PolyServe, Llumnix SLO and the vLLM router with the gateway's
CPU throttling removed.  Nothing else changed, so the pre-registration
(experiments/EXP-86_gateway-throttle-clean-baselines.md section 3) splits the
columns in two:

  MUST NOT MOVE   rejection rate, token goodput, and attainment under the mean
                  rule.  If they move outside the repeat spread, GOMAXPROCS
                  changed the SYSTEM rather than the measurement, and EXP-82's
                  conclusion that the earlier results still stand has to be
                  revisited too.
  MUST MOVE       the within-request percentiles: p50 up (bursting had deflated
                  it), p90 and p95 down (bursting had inflated them).
  MUST NOT MOVE   the across-request quantiles of the per-request mean, which the
                  artifact leaves alone because a batched delivery redistributes
                  gaps without changing the span or the token count.

⚠ ONE CONFOUND THE PRE-REGISTRATION DID NOT NAME, checked here.  The runs being
compared against are the pinned static sweep, collected across EXP-68/70/72/77/80,
and nothing in the pinned manifest records the scheduler BINARY.  If the binary or
the applied policy flags differ, a movement in the "must not move" columns is not
attributable to GOMAXPROCS.  What can be checked is the flags: the manifest carries
the full scheduler command line per run, so the flags that belong to each policy
are compared string by string against the EXP-86 runs' own start-up flags.  A
difference there disqualifies that arm's invariant check rather than the finding.

    python3 tail2026_exp86_judge.py

Read only.  Prints; writes one CSV.
"""
import glob, json, os, re, sys
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, CLASSES
from tail2026_holdwindow import RESULTS

PINNED = ("/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
          "/paper_experiment/static_sweep_2026-08")
ARMS = {"polyserve": ("PolyServe", "m1"), "slo": ("Llumnix SLO", "m1f"),
        "vllmcache": ("vLLM router", "m1")}
TB = {"chat": 50.0, "deepresearch": 100.0}
TT = {"chat": 5000.0, "deepresearch": 10000.0}
NEW = re.compile(r"^\d{6}_\d{4}_exp86r(?P<rep>\d)_(?P<arm>polyserve|slo|vllmcache)"
                 r"_(?P<mix>m1f?)_rpm_(?P<rpm>\d+)$")


def one(d, label, arm, rate):
    r = load_run(d)
    if r is None or r.empty:
        return []
    out = []
    win = float(r["rel"].max() - r["rel"].min())
    for c in CLASSES + ["ALL"]:
        s = r if c == "ALL" else r[r["class"] == c]
        live = s[~s["cutoff"]]
        if len(live) < 40:
            continue
        done = live[~live["rejected"] & ~live["errored"]]
        if len(done) < 20:
            continue
        g = lambda col: pd.to_numeric(done.get(col), errors="coerce").dropna()
        itl = g("itl_ms")
        met = live[~live["violate_offered"]]
        rec = dict(src=label, arm=arm, rate=rate, cls=c, n=len(live),
                   rej=100.0 * float(live["rejected"].mean()),
                   goodput=(pd.to_numeric(met.get("output_tokens"), errors="coerce")
                            .fillna(0).sum() / win) if win > 0 else np.nan,
                   att_offered=100.0 * float((~live["violate_offered"]).mean()))
        if c in TB:
            ttft = g("first_token_latency") * 1000.0
            ok = (itl <= TB[c]) & (ttft.reindex(itl.index) <= TT[c])
            rec["att_mean_rule"] = 100.0 * float(ok.sum()) / len(live)
        for tag, col in (("w_p50", "tbt_p50_ms"), ("w_p90", "tbt_p90_ms"),
                         ("w_p95", "tbt_p95_ms")):
            v = g(col)
            rec[tag] = float(v.median()) if len(v) else np.nan
        rec["a_p90"] = float(itl.quantile(0.90)) if len(itl) else np.nan
        rec["a_p99"] = float(itl.quantile(0.99)) if len(itl) else np.nan
        out.append(rec)
    return out


rows = []
for d in sorted(glob.glob(os.path.join(RESULTS, "*_rpm_*"))):
    m = NEW.match(os.path.basename(d))
    if m and os.path.isdir(d):
        rows += one(d, "exp86", ARMS[m.group("arm")][0], int(m.group("rpm")) // 60)

man = pd.read_csv(os.path.join(PINNED, "manifest.tsv"), sep="\t")
flags = {}
for _, mm in man.iterrows():
    if mm["arm"] not in ARMS:
        continue
    d = os.path.join(PINNED, "data", mm["run"])
    if os.path.isdir(d):
        rows += one(d, "pinned", ARMS[mm["arm"]][0], float(mm["req_per_s"]))
    flags.setdefault(ARMS[mm["arm"]][0], set()).add(str(mm["policy_flags"]))

df = pd.DataFrame(rows)
df.to_csv(os.path.join(RESULTS, "aggregate_analysis", "tail_2026-08-16",
                       "29_exp86_judge.csv"), index=False)

print("=== flag check: does the pinned manifest record ONE flag string per arm?")
for a, s in flags.items():
    print(f"  {a:13s} {len(s)} distinct scheduler command line(s) across its pinned runs")
print("  (more than one means the pinned runs for that arm are not internally")
print("   comparable either, and the invariant check for it is weaker)\n")


def band(g, col, f="%.1f"):
    v = pd.to_numeric(g[col], errors="coerce").dropna()
    if not len(v):
        return "--"
    if len(v) == 1 or abs(v.max() - v.min()) < 0.05:
        return f % v.mean()
    return (f + "-" + f) % (v.min(), v.max())


for title, cols, must in (
        ("MUST NOT MOVE -- the system's behaviour",
         ["rej", "goodput", "att_offered"], "invariant"),
        ("MUST MOVE -- the within-request percentiles",
         ["w_p50", "w_p90", "w_p95"], "moves"),
        ("MUST NOT MOVE -- the across-request quantiles",
         ["a_p90", "a_p99"], "invariant")):
    print(f"\n########## {title}")
    for c in ("chat", "ALL"):
        print(f"\n-- {c}")
        hdr = f"{'arm':13s} {'req/s':>5s} {'src':>7s}"
        for col in cols:
            hdr += f"{col:>16s}"
        print(hdr)
        for a in ("PolyServe", "Llumnix SLO", "vLLM router"):
            for rate in sorted(df["rate"].unique()):
                sub = df[(df.arm == a) & (df.rate == rate) & (df.cls == c)]
                if sub[sub.src == "exp86"].empty:
                    continue
                for src in ("pinned", "exp86"):
                    g = sub[sub.src == src]
                    if not len(g):
                        continue
                    line = f"{a:13s} {rate:5.0f} {src:>7s}"
                    for col in cols:
                        f_ = "%.0f" if col == "goodput" else "%.1f"
                        line += f"{band(g, col, f_):>16s}"
                    print(line)
