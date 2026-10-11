#!/usr/bin/env python3
"""EXP-55: where each class saturates on the same engines, and what saturates.

One class at a time, so no routing decision can differentiate anything and the
four engines are four replicas of the same single-class experiment. The point is
not that the knees sit at different request rates -- the classes have different
prompt sizes, so that is arithmetic. The point is that a DIFFERENT RESOURCE runs
out first in each case, and the three resources are measured in different units.
"""
import glob, json, os, re, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np, pandas as pd
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run, attain

CLS = {"schat": ("chat", "#1f77b4", 50.0, 649),
       "sdr": ("deepresearch", "#ff7f0e", 100.0, 4376),
       "sswe": ("swe", "#d62728", 62.0, 6219)}


def engine_p90(run):
    ks = {"kv": "vllm:kv_cache_usage_perc", "run": "vllm:num_requests_running",
          "wait": "vllm:num_requests_waiting"}
    acc = {k: [] for k in ks}
    for f in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        for l in open(f):
            try:
                o = json.loads(l)
            except ValueError:
                continue
            if not o.get("ok"):
                continue
            for k, p in ks.items():
                v = [vv for kk, vv in o.items()
                     if kk.startswith(p) and isinstance(vv, (int, float))]
                if v:
                    acc[k].append(float(v[0]))
    return {k: (np.percentile(v, 90) if v else np.nan) for k, v in acc.items()}


def main(pattern, out):
    rows = []
    for d in sorted(glob.glob(pattern)):
        m = re.search(r"_(schat|sdr|sswe)_rpm_(\d+)", os.path.basename(d))
        if not m:
            continue
        r = load_run(d)
        if r is None or r.empty:
            continue
        name, _, budget, intok = CLS[m.group(1)]
        rate = int(m.group(2)) / 60.0
        a = r[(~r["rejected"]) & (~r["errored"]) & (~r["cutoff"])]
        e = engine_p90(d)
        rows.append(dict(cls=name, key=m.group(1), rate=rate,
                         intok=intok * rate / 1000.0,
                         off=attain(r, "violate_offered"),
                         itl=pd.to_numeric(a["itl_ms"], errors="coerce").median(),
                         pace_frac=pd.to_numeric(a["itl_ms"], errors="coerce").median() / budget,
                         kv=e["kv"] * 100, batch=e["run"], wait=e["wait"]))
    df = pd.DataFrame(rows)
    os.makedirs(out, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(7.4, 2.5))
        for k, (name, c, _, _) in CLS.items():
            g = df[df.key == k].sort_values("rate")
            ax[0].plot(g.rate, g.off, color=c, marker="o", ms=3, label=name)
            ax[1].plot(g.intok, g.off, color=c, marker="o", ms=3, label=name)
            ax[2].plot(g.kv, g.pace_frac * 100, color=c, marker="o", ms=3, label=name)
        ax[0].set_xlabel("request rate (req/s)")
        ax[1].set_xlabel("input tokens/s (thousands)")
        for a_ in ax[:2]:
            a_.set_ylim(0, 105)
            a_.set_ylabel("SLO attainment (%)")
        # The third panel is the claim: at the knee, each class is against a
        # different wall. x is memory, y is how much of its own per-token budget
        # the delivered pace uses. A class that fails in the top-left is
        # pace-bound with memory to spare; one that fails on the right is
        # memory-bound while still inside its budget.
        ax[2].axhline(100, color="#888888", ls="--", lw=0.8)
        ax[2].axvline(100, color="#888888", ls="--", lw=0.8)
        ax[2].set_xlabel("KV occupancy (%, p90)")
        ax[2].set_ylabel("delivered pace\n(% of the class budget)")
        ax[2].set_xlim(0, 110)
        ax[2].set_ylim(0, 220)
        for a_ in ax:
            a_.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax[1].legend(loc="lower center", bbox_to_anchor=(0.5, 1.02), ncol=3,
                     fontsize=7, columnspacing=1.2)
        fig.savefig(os.path.join(out, "knees.png"), dpi=300, bbox_inches="tight")
    print(df.to_string(index=False, float_format=lambda x: f"{x:.1f}"))
    print(f"\nwrote {out}/knees.png")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results/*exp55r1*",
         sys.argv[2] if len(sys.argv) > 2 else "results/aggregate_analysis/exp55")
