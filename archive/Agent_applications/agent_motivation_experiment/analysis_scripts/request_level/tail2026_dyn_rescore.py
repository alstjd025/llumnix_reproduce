#!/usr/bin/env python3
"""Re-score the hour-long dynamic trace on the tail of the per-token time.

`ms_dev/notes/tail-latency.md` reports that SLO attainment is scored on the MEAN
per-token time and that re-scoring on the p90 reverses the ranking between
FluidServe v0.2 (`fspfx`) and llm-d (`llmdslo`). Every table in that document was
produced on STATIC conditions: one fixed arrival rate, one fixed class mix,
eight minutes. This script asks the same question of the hour-long trace of
EXP-71, whose arrival rate follows an Azure trace over 10.8-45.0 req/s and whose
class mix steps three times.

What is scored
--------------
The primary metric of this repository: every arrival is in the denominator, and
a rejection or a client error counts as a violation (`violate_offered` in
exp22_fluidserve.load_run). Requests still in flight when the run ended leave
both denominators, because their outcome was never determined -- which is
exactly the hazard handled below.

Four scorings of the same rule, differing only in which per-token quantity is
compared against the per-token budget:

  itl_ms      the corrected MEAN per-token time, (end-to-end - time to first
              token) / (output tokens - 1), published by load_run. This is the
              headline scoring.
  tbt_p50_ms  the client's recorded median of the inter-chunk gaps
  tbt_p90_ms  the client's recorded 90th percentile of the inter-chunk gaps
  tbt_p95_ms  the client's recorded 95th percentile

The class whose budget is end-to-end (the software-engineering class, 30 s) is
unaffected by the choice and is scored identically in all four.

The scale caveat, and what this script measures about it
--------------------------------------------------------
The mean recorded in `tbt_mean_ms` was found to be 1/1.92 of the true per-token
time on runs collected before 2026-07-30, because the client divided each
inter-chunk gap by a count of tokens obtained by tokenising the chunk out of
context; only the mean was corrected, by deriving it from the end-to-end time
instead. The percentile columns were never corrected, so a percentile column is
on the same scale as the true per-token time only if the engine emits exactly
one token per streamed chunk. That is a measurable property of each run, so this
script measures it (`stream_chunks / output_tokens` and the ratio between the
derived mean and the recorded mean) and prints it before the tables, and it also
prints every percentile scoring a second time with the samples multiplied by
1.92 so that the effect of the scale is visible either way.

The end-of-run contamination, and how it is cut
-----------------------------------------------
`attain()` drops run-boundary cutoffs from the denominator. In an arm that is
backlogged those cutoffs are precisely the requests that were going to fail, so
a window near the end of the run reads far better than the policy behaved. The
share of arrivals in each window that ended as a cutoff is therefore computed
per arm per window; the earliest window at which any arm goes above
`--cutoff-max` and stays above it for the rest of the run marks the start of the
contaminated stretch, and EVERY arm is truncated there so that the arms are
compared over one common interval.

Usage
-----
  python3 tail2026_dyn_rescore.py --out-dir <dir>
"""
import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    CLASSES, SLO_RULES, load_run, truthy,
)

REPO = "/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
PLAN = os.path.join(REPO, "traces/dynamic/canonical/dyn60_short_m123_b1045.plan.json")

# The four control planes of EXP-71, two repeats each. The directory names are
# UTC-7 and every one of these is at or after 260807_1900, which is the boundary
# the analysis rule sets: the hour-long band changed on 2026-08-09 and the runs
# of EXP-54, EXP-56 and EXP-60 are on the older 24.9-74.7 req/s band and must not
# be placed in the same table as these.
RUNS = {
    "fspfx": ["results/260808_1620_exp71r1_fspfx_fullb",
              "results/260808_1845_exp71br1_fspfx_fullb"],
    "llmdslo": ["results/260808_1741_exp71r1_llmdslo_full",
                "results/260808_2007_exp71br1_llmdslo_full"],
    "slo": ["results/260808_2133_exp71r1_slo_fullb",
            "results/260809_0057_exp71br1_slo_fullb"],
    "polyserve": ["results/260808_2245_exp71r1_polyserve_fullb",
                  "results/260809_0210_exp71br1_polyserve_fullb"],
}
ARM_LABEL = {"fspfx": "FluidServe v0.2", "llmdslo": "llm-d",
             "slo": "Llumnix SLO", "polyserve": "PolyServe"}
ARM_ORDER = ["fspfx", "llmdslo", "slo", "polyserve"]

# The per-token quantity each scoring compares against the per-token budget.
# `itl_ms` is the corrected mean; the three `tbt_*` columns are the client's raw
# recorded percentiles of the inter-chunk gaps.
SCORINGS = ["itl_ms", "tbt_p50_ms", "tbt_p90_ms", "tbt_p95_ms"]


def segments_from_plan(plan_path=PLAN):
    with open(plan_path) as f:
        plan = json.load(f)
    out = []
    for name, seg in plan.get("segments", {}).items():
        if str(name).startswith("warmup"):
            continue
        t0, t1 = seg["t_range_s"]
        out.append((name, float(t0), float(t1),
                    seg.get("realised_ratio", {})))
    out.sort(key=lambda x: x[1])
    return out


def prepare(run_dir):
    """load_run plus the raw percentile columns and the completion flags."""
    r = load_run(run_dir)
    if r is None or r.empty:
        return None
    for c in ["tbt_p50_ms", "tbt_p90_ms", "tbt_p95_ms", "tbt_mean_ms",
              "first_token_latency", "latency", "output_tokens",
              "stream_chunks"]:
        r[c] = pd.to_numeric(r.get(c), errors="coerce")
    # Rows excluded from any latency or length statistic. Attainment keeps its
    # own definition (rejection and error are violations, cutoffs leave the
    # denominator); this flag is only for the descriptive distributions.
    r["incomplete"] = (truthy(r, "is_server_terminated") | truthy(r, "is_error")
                       | truthy(r, "is_timeout") | truthy(r, "is_job_timeout"))
    return r


def violate_offered(r, col, scale=1.0):
    """The primary metric's violation flag, with `col` as the per-token time.

    Same rule as load_run: the class budget on time to first token AND on the
    per-token time, except for the end-to-end class. A request that produced no
    first token produced nothing and is a violation under any rule. A rejection
    or a client error is a violation. The comparison is NaN-safe: a request with
    one output token has no inter-token interval and is not thereby a violation,
    it is caught by the missing-first-token test or it met its budget trivially.
    """
    ttft = r["first_token_latency"]
    e2e = r["latency"]
    tbt = r[col] * scale
    miss = pd.Series(False, index=r.index)
    for cname, rule in SLO_RULES.items():
        m = r["class"] == cname
        if "e2e" in rule:
            miss.loc[m] = e2e[m] > rule["e2e"]
        else:
            miss.loc[m] = (ttft[m] > rule["ttft"]) | (tbt[m] > rule["tbt"])
    miss = miss | (ttft.isna() & ~r["cutoff"])
    return miss | r["rejected"] | r["errored"]


def attain_offered(r, col, scale=1.0):
    rows = r[~r["cutoff"]]
    if rows.empty:
        return np.nan
    return 100.0 * float((~violate_offered(rows, col, scale)).mean())


def cutoff_share(r, lo, hi):
    w = r[(r["rel"] >= lo) & (r["rel"] < hi)]
    if w.empty:
        return np.nan, 0
    return 100.0 * float(w["cutoff"].mean()), len(w)


def find_truncation(data, win, cutoff_max, t_end):
    """Start of the terminal stretch in which some arm's in-flight share stays
    above `cutoff_max`.

    Scanning backwards rather than forwards on purpose: a single window above
    the threshold in the middle of the run is a burst, not the end-of-run
    artefact, and truncating the whole comparison there would throw away most of
    the hour. The artefact is by construction terminal, so the quantity wanted
    is the earliest window from which the condition holds continuously to the
    end.
    """
    starts = np.arange(0.0, t_end, win)
    cut = t_end
    for arm, runs in data.items():
        for name, r in runs:
            t = t_end
            for s in starts[::-1]:
                share, n = cutoff_share(r, s, s + win)
                if n == 0 or np.isnan(share):
                    continue
                if share > cutoff_max:
                    t = s
                else:
                    break
            cut = min(cut, t)
    return float(cut)


def score_window(data, lo, hi, scalings):
    """One row per arm: n, and the attainment under every scoring."""
    out = []
    for arm in ARM_ORDER:
        if arm not in data:
            continue
        vals = {k: [] for k in scalings}
        ns, cuts = [], []
        for name, r in data[arm]:
            w = r[(r["rel"] >= lo) & (r["rel"] < hi)]
            if len(w) < 50:
                continue
            ns.append(len(w))
            cuts.append(100.0 * float(w["cutoff"].mean()))
            for k, (col, sc) in scalings.items():
                vals[k].append(attain_offered(w, col, sc))
        if not ns:
            continue
        row = dict(arm=arm, n=int(np.sum(ns)), runs=len(ns),
                   cutoff_pct=float(np.mean(cuts)))
        for k in scalings:
            v = [x for x in vals[k] if not np.isnan(x)]
            row[k] = float(np.mean(v)) if v else np.nan
            row[k + "_spread"] = (float(np.max(v) - np.min(v))
                                  if len(v) > 1 else 0.0)
        out.append(row)
    return pd.DataFrame(out)


def fmt(df, cols, out):
    hdr = f"{'arm':<16}{'n':>8}{'inflt%':>8}" + "".join(f"{c:>14}" for c in cols)
    out.append(hdr)
    out.append("-" * len(hdr))
    for _, row in df.iterrows():
        line = (f"{ARM_LABEL.get(row['arm'], row['arm']):<16}{row['n']:8d}"
                f"{row['cutoff_pct']:8.1f}")
        for c in cols:
            v = row[c]
            s = "   n/a" if (isinstance(v, float) and np.isnan(v)) else f"{v:.1f}"
            sp = row.get(c + "_spread", np.nan)
            if not (isinstance(sp, float) and np.isnan(sp)):
                s = f"{s} ({sp:.1f})"
            line += f"{s:>14}"
        out.append(line)
    out.append("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.join(
        REPO, "results/aggregate_analysis/tail_2026-08-16"))
    ap.add_argument("--window", type=float, default=60.0)
    ap.add_argument("--cutoff-max", type=float, default=20.0)
    ap.add_argument("--settle", type=float, default=90.0,
                    help="seconds after a mix boundary counted as transition")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    data = {}
    for arm, dirs in RUNS.items():
        for d in dirs:
            p = os.path.join(REPO, d)
            r = prepare(p)
            if r is None:
                print(f"  {d}: no rows", file=sys.stderr)
                continue
            data.setdefault(arm, []).append((os.path.basename(p), r))

    out = []
    out.append("## A. what the recorded percentile columns are on")
    out.append("")
    out.append("One streamed chunk per output token is what makes a recorded")
    out.append("inter-chunk percentile a per-token percentile. Measured per run")
    out.append("on completed requests only (no rejection, error, timeout or")
    out.append("run-boundary cutoff), with output tokens > 1.")
    out.append("")
    hdr = (f"{'run':<40}{'chunks/token p50':>18}{'derived/recorded mean p50':>28}"
           f"{'n':>9}")
    out.append(hdr)
    out.append("-" * len(hdr))
    chunk_ratios = []
    for arm in ARM_ORDER:
        for name, r in data.get(arm, []):
            c = r[~r["incomplete"] & ~r["rejected"] & (r["output_tokens"] > 1)]
            cpt = (c["stream_chunks"] / c["output_tokens"]).median()
            ratio = (c["itl_ms"] / c["tbt_mean_ms"]).median()
            chunk_ratios.append(cpt)
            out.append(f"{name:<40}{cpt:18.4f}{ratio:28.4f}{len(c):9d}")
    out.append("")
    out.append(f"chunks per token over the eight runs: median "
               f"{np.median(chunk_ratios):.4f}, "
               f"min {np.min(chunk_ratios):.4f}, max {np.max(chunk_ratios):.4f}")
    out.append("")

    # The scorings. Each is (column, multiplier).
    scalings = {c: (c, 1.0) for c in SCORINGS}
    for c in SCORINGS[1:]:
        scalings[c + "_x1.92"] = (c, 1.92)
    cols = SCORINGS + [c + "_x1.92" for c in SCORINGS[1:]]

    t_end = min(min(r["rel"].max() for _, r in runs) for runs in data.values())
    cut = find_truncation(data, a.window, a.cutoff_max, t_end)

    out.append("## B. the end-of-run cut")
    out.append("")
    out.append(f"analysis window ends at {t_end:.0f} s (the shortest arm); "
               f"{a.window:.0f} s windows; a window is contaminated when more "
               f"than {a.cutoff_max:.0f}% of the requests that arrived in it "
               f"were still in flight when the run ended.")
    out.append(f"the terminal contaminated stretch begins at **{cut:.0f} s "
               f"({cut/60:.1f} min)** and EVERY arm is cut there.")
    out.append("")
    hdr = f"{'window (min)':<16}" + "".join(
        f"{ARM_LABEL[k]:>18}" for k in ARM_ORDER if k in data)
    out.append(hdr)
    out.append("-" * len(hdr))
    for s in np.arange(0.0, t_end, a.window):
        cells = []
        for arm in ARM_ORDER:
            if arm not in data:
                continue
            v = [cutoff_share(r, s, s + a.window)[0] for _, r in data[arm]]
            v = [x for x in v if not np.isnan(x)]
            cells.append(f"{np.mean(v):18.1f}" if v else f"{'n/a':>18}")
        if any(c.strip() != "n/a" for c in cells):
            out.append(f"{s/60:>6.1f}-{(s+a.window)/60:<9.1f}" + "".join(cells))
    out.append("")

    def table(title, note, lo, hi):
        out.append(f"### {title}")
        out.append("")
        if note:
            out.append(note)
            out.append("")
        df = score_window(data, lo, hi, scalings)
        fmt(df, cols, out)
        return df

    out.append("## C. whole trace, re-scored")
    out.append("")
    out.append("Primary metric: every arrival in the denominator, rejection and "
               "error are violations, run-boundary cutoffs leave it. Value is "
               "the mean of the two repeats, the bracket is their spread.")
    out.append("")
    whole = table(f"whole measured trace, 60 s to {cut:.0f} s",
                  "", 60.0, cut)

    segs = segments_from_plan()
    out.append("## D. per segment")
    out.append("")
    out.append("Segments come from the trace's own plan file. Each is scored "
               "twice: the whole segment, and the segment with the first "
               f"{a.settle:.0f} s after its boundary removed, which is the "
               "steady stretch of that mix.")
    out.append("")
    seg_tables = {}
    for name, t0, t1, ratio in segs:
        if t0 >= cut:
            out.append(f"### {name} ({t0/60:.0f}-{t1/60:.0f} min) — DROPPED, "
                       f"entirely inside the contaminated stretch")
            out.append("")
            continue
        hi = min(t1, cut)
        mix = ", ".join(f"{k} {100*v:.0f}%" for k, v in ratio.items())
        seg_tables[name] = table(
            f"{name}  {t0/60:.0f}-{hi/60:.1f} min  (mix: {mix})",
            f"whole segment, {t0/60:.0f} to {hi/60:.1f} min", t0, hi)
        lo2 = t0 + a.settle if t0 > 60.0 else t0
        if hi - lo2 > 120:
            seg_tables[name + "_steady"] = table(
                f"{name} steady  {lo2/60:.1f}-{hi/60:.1f} min",
                f"first {a.settle:.0f} s after the boundary removed", lo2, hi)

    out.append("## E. transitions only")
    out.append("")
    out.append(f"The {a.settle:.0f} s after each mix boundary, pooled over the "
               f"boundaries and over the two repeats. Compare against the "
               f"steady tables above.")
    out.append("")
    bounds = [t0 for _, t0, _, _ in segs[1:] if t0 + a.settle <= cut]
    trans_rows = []
    for arm in ARM_ORDER:
        if arm not in data:
            continue
        vals = {k: [] for k in scalings}
        ns, cuts = [], []
        for name, r in data[arm]:
            w = pd.concat([r[(r["rel"] >= b) & (r["rel"] < b + a.settle)]
                           for b in bounds])
            if len(w) < 50:
                continue
            ns.append(len(w))
            cuts.append(100.0 * float(w["cutoff"].mean()))
            for k, (col, sc) in scalings.items():
                vals[k].append(attain_offered(w, col, sc))
        row = dict(arm=arm, n=int(np.sum(ns)), runs=len(ns),
                   cutoff_pct=float(np.mean(cuts)))
        for k in scalings:
            v = [x for x in vals[k] if not np.isnan(x)]
            row[k] = float(np.mean(v)) if v else np.nan
            row[k + "_spread"] = float(np.max(v) - np.min(v)) if len(v) > 1 else 0.0
        trans_rows.append(row)
    trans = pd.DataFrame(trans_rows)
    fmt(trans, cols, out)

    out.append("## F. the completed-request per-token distribution")
    out.append("")
    out.append("Completed requests only. `itl_ms` is the corrected mean per "
               "request; the percentile columns are the client's raw recorded "
               "percentiles per request, and the value shown is the median over "
               "requests of that per-request percentile. Chat only, because "
               "chat is the tightest per-token budget (50 ms) and 78% of the "
               "arrivals.")
    out.append("")
    hdr = (f"{'arm':<16}{'n':>9}{'itl mean':>11}{'itl p90':>10}{'p50 col':>10}"
           f"{'p90 col':>10}{'p95 col':>10}")
    out.append(hdr)
    out.append("-" * len(hdr))
    for arm in ARM_ORDER:
        if arm not in data:
            continue
        acc = []
        for name, r in data[arm]:
            c = r[(r["rel"] < cut) & ~r["incomplete"] & ~r["rejected"]
                  & (r["class"] == "chat") & (r["output_tokens"] > 1)]
            acc.append(c)
        c = pd.concat(acc)
        out.append(f"{ARM_LABEL[arm]:<16}{len(c):9d}"
                   f"{c['itl_ms'].mean():11.1f}{c['itl_ms'].quantile(.90):10.1f}"
                   f"{c['tbt_p50_ms'].median():10.1f}"
                   f"{c['tbt_p90_ms'].median():10.1f}"
                   f"{c['tbt_p95_ms'].median():10.1f}")
    out.append("")

    text = "\n".join(out)
    print(text)
    p = os.path.join(a.out_dir, "tail2026_dyn_rescore.txt")
    with open(p, "w") as f:
        f.write(text + "\n")
    print(f"\nwrote {p}", file=sys.stderr)


if __name__ == "__main__":
    main()
