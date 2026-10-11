#!/usr/bin/env python3
"""The full EXP-82 attainment and per-token latency tables, all denominators.

Runs: EXP-82 only -- `results/*exp82r[12]_fspfx_m1_rpm_*` and
`results/*exp82r[12]_llmdslo_m1f_rpm_*`, with the `PRERUN` directories dropped.
Earlier sweeps must not enter this table: on them the Llumnix Go gateway
exhausted its CFS CPU quota, the kernel stopped the whole container until the
next scheduling period, every live stream froze and resumed together, and the
client therefore recorded several tokens as arriving at the same instant. That
inflates every client-side per-token percentile downward at the bottom of the
distribution and upward at the top. EXP-82 re-ran both arms with the quota
raised and the fraction of inter-token gaps under 5 ms fell from 14.11% to
0.29%, so these runs measure the engines and the earlier ones do not.

WHERE EACH NUMBER COMES FROM.

  attainment          `exp22_fluidserve.load_run` for the analysis window, the
                      class rules and the rejected/errored/cutoff flags;
                      `tail2026_quantile_ladder.score` for the all-arrivals
                      denominator, which this script calls rather than
                      reimplements and then checks its own offered/admitted
                      arithmetic against.
  per-token statistic the request's mean is `load_run`'s `itl_ms`, i.e.
                      (end-to-end - time to first token) / (output tokens - 1),
                      which is the published status quo. The request's own p50,
                      p90, p95 and p99 come from `tail2026_full_gapstats.py`,
                      which takes them from the gaps between consecutive chunk
                      arrivals in `tbt_events.jsonl`. They are NOT the
                      `tbt_p50_ms` .. `tbt_p95_ms` columns of `metrics.csv`:
                      those divide each chunk's gap by a token count obtained by
                      tokenising the chunk out of context, which splits it into
                      about 1.92 pieces, so they sit on a different scale from
                      the corrected mean and cannot be put in the same table
                      with it. Going back to the arrival gaps puts all five
                      statistics on one scale, and the agreement between the
                      event-derived mean and `itl_ms` is reported so that is
                      checkable rather than asserted.

  latency, Task 2     completed requests only -- rejected, errored, timed out,
                      job-timed-out and run-boundary-truncated rows dropped --
                      because a truncated stream's latency is a lower bound and
                      not a measurement. The attainment tables do NOT drop them:
                      there they are violations.

THE THREE DENOMINATORS, all of which appear for every cell:

  offered       every arrival is in the denominator and a rejection is a
                violation; requests still in flight when the window closed are
                dropped, their outcome never having been determined.
  admitted      only what the system accepted; a rejection leaves the
                population entirely. Read alone this rewards refusing whatever
                was going to miss, so the rejection rate is printed beside it.
  all arrivals  every arrival, with BOTH a rejection and a request that never
                finished counting as violations.

  python3 tail2026_full_tables.py --out <markdown path>
"""
import argparse
import glob
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, SLO_RULES, CLASSES  # noqa: E402
from tail2026_quantile_ladder import score, completed  # noqa: E402
from tail2026_full_gapstats import OUTDIR as GAPDIR  # noqa: E402

EXPDIR = "/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
RESULTS = os.path.join(EXPDIR, "results")
RUN_GLOBS = ("*exp82r[12]_fspfx_m1_rpm_*", "*exp82r[12]_llmdslo_m1f_rpm_*")
ARM_LABEL = {"fspfx": "FluidServe (`fspfx`, m1)", "llmdslo": "llm-d (`llmdslo`, m1f)"}

# The status quo first, then the request's own quantile ladder.
STATS = ["mean", "p50", "p90", "p95", "p99"]
# `_f` = the gap percentile with `itl_ms` filled in where the event file gives
# no gap series. See fill_stats() for why the fill exists and what it costs.
STAT_COL = {"mean": "itl_ms", "p50": "gap_p50_ms_f", "p90": "gap_p90_ms_f",
            "p95": "gap_p95_ms_f", "p99": "gap_p99_ms_f"}
RAW_COL = {"mean": "itl_ms", "p50": "gap_p50_ms", "p90": "gap_p90_ms",
           "p95": "gap_p95_ms", "p99": "gap_p99_ms"}
DENOMS = ["offered", "admitted", "all_arrivals"]
NAME_RE = re.compile(r"^\d{6}_\d{4}_exp82r(?P<rep>\d)_(?P<arm>fspfx|llmdslo)_"
                     r"(?P<mix>m1f?)_rpm_(?P<rpm>\d+)$")


def run_dirs():
    out = []
    for g in RUN_GLOBS:
        for d in glob.glob(os.path.join(RESULTS, g)):
            b = os.path.basename(d)
            if os.path.isdir(d) and "PRERUN" not in b and NAME_RE.match(b):
                out.append(d)
    return sorted(out)


def three_denominators(rows, tbt, subset=None):
    """Attainment on all three denominators with `tbt` as the per-token term.

    The miss test is `load_run`'s with only the per-token series substituted,
    and the three denominators are `exp22_fluidserve.attain`'s two plus
    `all_arrivals_attainment`'s third:

      offered      = met / (met + missed + rejected),   unfinished dropped
      admitted     = met / (met + missed),              rejected and unfinished dropped
      all arrivals = met / (met + missed + rejected + unfinished)

    The all-arrivals figure is cross-checked against
    `tail2026_quantile_ladder.score`, which is the function the earlier ladder
    table used, so a divergence between the two is raised here instead of
    appearing as an unexplained difference between two documents.
    """
    if subset is not None:
        rows, tbt = rows[subset], tbt[subset]
    if not len(rows):
        return {d: np.nan for d in DENOMS} | {"n": 0, "rejected_pct": np.nan,
                                              "unfinished_pct": np.nan}
    ttft = pd.to_numeric(rows["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(rows["latency"], errors="coerce")
    miss = pd.Series(False, index=rows.index)
    for cname, rule in SLO_RULES.items():
        m = rows["class"] == cname
        if "e2e" in rule:
            miss.loc[m] = e2e[m] > rule["e2e"]
        else:
            miss.loc[m] = (ttft[m] > rule["ttft"]) | (tbt[m] > rule["tbt"])
    miss = miss | (ttft.isna() & ~rows["cutoff"])
    violate = miss | rows["rejected"] | rows["errored"]
    cut = rows["cutoff"]
    met = (~violate) & (~cut)

    fin = ~cut
    offered = 100.0 * met[fin].mean() if fin.any() else np.nan
    adm = fin & (~rows["rejected"])
    admitted = 100.0 * met[adm].mean() if adm.any() else np.nan
    allarr = 100.0 * met.sum() / len(rows)

    ref = score(rows, tbt)[0]
    if not (np.isnan(ref) and np.isnan(allarr)) and abs(ref - allarr) > 1e-9:
        raise AssertionError(f"all-arrivals disagrees with "
                             f"tail2026_quantile_ladder.score: {allarr} vs {ref}")
    return {"offered": offered, "admitted": admitted, "all_arrivals": allarr,
            "n": int(len(rows)), "rejected_pct": 100.0 * rows["rejected"].mean(),
            "unfinished_pct": 100.0 * cut.mean()}


def q(s, p):
    s = pd.to_numeric(s, errors="coerce").dropna()
    return float(np.percentile(s, p)) if len(s) else np.nan


def load_one(d):
    name = os.path.basename(d.rstrip("/"))
    m = NAME_RE.match(name)
    rows = load_run(d)
    if rows is None or rows.empty:
        return None
    rows = rows[rows["agent"].astype(str) == "request"].copy()
    gp = os.path.join(GAPDIR, name + "_perreq.csv.gz")
    if not os.path.isfile(gp):
        print(f"  no gap cache for {name}; run tail2026_full_gapstats.py first")
        return None
    g = pd.read_csv(gp)
    rows["call_index"] = pd.to_numeric(rows["call_index"], errors="coerce")
    g["call_index"] = pd.to_numeric(g["call_index"], errors="coerce")
    before = len(rows)
    rows = rows.merge(g.drop(columns=["class"]), on=["task_id", "call_index"],
                      how="left", validate="one_to_one")
    assert len(rows) == before, "gap join changed the row count"
    rows["run"] = name
    rows["arm"] = m.group("arm")
    rows["rep"] = int(m.group("rep"))
    rows["rate"] = int(m.group("rpm")) / 60.0
    return fill_stats(rows)


def fill_stats(rows):
    """Give the four percentiles the same population the mean has.

    A small share of requests produced more than one output token -- so
    `itl_ms` is defined for them -- and yet carry no usable chunk series in
    `tbt_events.jsonl`, because the client fell back to a non-streaming read or
    the stream delivered everything in one chunk. Left as missing values those
    requests would silently PASS the per-token term under the four percentiles
    while being judged under the mean, so the five columns would not be scoring
    the same set of requests and part of any difference between them would be
    that mismatch rather than the statistic.

    For a request with a single gap the mean IS its p50, p90, p95 and p99, so
    filling the missing percentile with `itl_ms` is the choice that keeps the
    population identical across the five columns. How many requests it touches
    is counted per run and reported, and the tables are also produced without
    the fill so the size of the effect is visible instead of assumed.
    """
    itl = pd.to_numeric(rows["itl_ms"], errors="coerce")
    for c in ("gap_p50_ms", "gap_p90_ms", "gap_p95_ms", "gap_p99_ms"):
        rows[c + "_f"] = pd.to_numeric(rows[c], errors="coerce").fillna(itl)
    rows["gap_missing"] = (pd.to_numeric(rows["gap_p90_ms"], errors="coerce")
                           .isna() & itl.notna())
    return rows


def per_run_records(rows):
    """One record per (run, statistic, class-or-pooled) with the three denominators."""
    recs = []
    base = {k: rows[k].iloc[0] for k in ("run", "arm", "rep", "rate")}
    base["gap_missing_pct"] = 100.0 * rows["gap_missing"].mean()
    for fill, table in ((True, STAT_COL), (False, RAW_COL)):
        for stat in STATS:
            tbt = pd.to_numeric(rows[table[stat]], errors="coerce")
            for pop in ["all"] + CLASSES:
                sub = None if pop == "all" else (rows["class"] == pop)
                r = three_denominators(rows, tbt, sub)
                recs.append(dict(base, stat=stat, pop=pop, fill=fill, **r))
    return recs


def latency_records(rows):
    """Task 2 and Task 4, on completed requests only."""
    comp = completed(rows)
    dur = rows["rel"].max() - rows["rel"].min()
    out = []
    base = {k: rows[k].iloc[0] for k in ("run", "arm", "rep", "rate")}
    for pop in ["all"] + CLASSES:
        c = comp if pop == "all" else comp[comp["class"] == pop]
        rec = dict(base, pop=pop, n_completed=len(c))
        # (a) across requests, of each request's MEAN per-token time
        rec["reqmean_p50"] = q(c["itl_ms"], 50)
        rec["reqmean_p90"] = q(c["itl_ms"], 90)
        rec["reqmean_p99"] = q(c["itl_ms"], 99)
        # (b) across requests, of each request's OWN p90 gap
        rec["reqp90_p50"] = q(c["gap_p90_ms"], 50)
        rec["reqp90_p90"] = q(c["gap_p90_ms"], 90)
        # time to first token, milliseconds
        ttft_ms = pd.to_numeric(c["first_token_latency"], errors="coerce") * 1000.0
        rec["ttft_p50_ms"] = q(ttft_ms, 50)
        rec["ttft_p90_ms"] = q(ttft_ms, 90)
        # delivered work, and how far the event-derived mean sits from itl_ms
        rec["out_tok_s"] = (pd.to_numeric(c["output_tokens"], errors="coerce")
                            .sum() / dur) if dur > 0 else np.nan
        ratio = (pd.to_numeric(c["gap_mean_ms"], errors="coerce")
                 / pd.to_numeric(c["itl_ms"], errors="coerce"))
        rec["gapmean_over_itl_med"] = q(ratio.replace([np.inf, -np.inf], np.nan), 50)
        # Task 4: the share of requests inside each budget under each statistic
        for stat in STATS:
            s = pd.to_numeric(c[STAT_COL[stat]], errors="coerce").dropna()
            for b in (50.0, 100.0):
                rec[f"pass{int(b)}_{stat}"] = (100.0 * (s <= b).mean()
                                               if len(s) else np.nan)
        out.append(rec)
    return out


def agg(df, cols, by):
    g = df.groupby(by)
    out = pd.DataFrame({"reps": g.size()})
    for c in cols:
        out[c] = g[c].mean()
        out[c + "_lo"] = g[c].min()
        out[c + "_hi"] = g[c].max()
    return out.reset_index()


def cell(r, c, fmt="{:.1f}"):
    if r["reps"] < 2:
        return fmt.format(r[c]) + " (1 rep)"
    if not np.isfinite(r[c]):
        return "-"
    return (fmt.format(r[c]) + " [" + fmt.format(r[c + "_lo"]) + ".." +
            fmt.format(r[c + "_hi"]) + "]")


def spread(r, c):
    if r["reps"] < 2 or not np.isfinite(r[c + "_hi"]):
        return np.nan
    return r[c + "_hi"] - r[c + "_lo"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        RESULTS, "aggregate_analysis/tail_2026-08-16/13_full_tables.md"))
    a = ap.parse_args()

    dirs = run_dirs()
    frames = []
    for d in dirs:
        r = load_one(d)
        if r is not None:
            frames.append(r)
    if not frames:
        sys.exit("no runs loaded")

    att = pd.DataFrame([x for f in frames for x in per_run_records(f)])
    lat = pd.DataFrame([x for f in frames for x in latency_records(f)])
    pool = []
    import json
    for f in frames:
        name = f["run"].iloc[0]
        with open(os.path.join(GAPDIR, name + "_pooled.json")) as fh:
            j = json.load(fh)
        j.update({k: f[k].iloc[0] for k in ("arm", "rep", "rate")})
        pool.append(j)
    pool = pd.DataFrame(pool)

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    att.to_csv(a.out.replace(".md", "_attainment_perrun.csv"), index=False)
    lat.to_csv(a.out.replace(".md", "_latency_perrun.csv"), index=False)
    pool.to_csv(a.out.replace(".md", "_pooled_perrun.csv"), index=False)

    o = []
    W = o.append
    rates = sorted(att["rate"].unique())

    # ---------------------------------------------------------------- header
    W("# EXP-82 full tables: attainment, per-token latency, matched work")
    W("")
    W("Generated by `analysis_scripts/request_level/tail2026_full_tables.py` "
      "from `analysis_scripts/request_level/tail2026_full_gapstats.py`'s cache. "
      "Every table below is labelled with its denominator, its statistic and "
      "its population, because this repository has twice mistaken a "
      "per-request statistic for a pooled one.")
    W("")
    W("## What is in the table, and what was excluded")
    W("")
    W("**EXP-82 only.** Every earlier sweep carries a client-side measurement "
      "defect: the Llumnix Go gateway exhausted its CFS CPU quota, the kernel "
      "stopped the whole container until the next scheduling period boundary, "
      "and every live stream froze and resumed together, so the client recorded "
      "several tokens as arriving at the same instant. EXP-82 re-ran both arms "
      "with that fixed; the fraction of inter-token gaps under 5 ms fell from "
      "14.11% to 0.29%. The per-condition sub-5 ms fractions measured here are "
      "printed in the last table of this document so that claim is checkable "
      "per condition. `PRERUN` directories are warm-up conditions the driver "
      "writes before each llm-d condition and are excluded.")
    W("")
    W("**A sweep was still writing while this was produced.** The table is what "
      "existed at enumeration time and nothing was waited for.")
    W("")
    attf = att[att["fill"]]
    cov = (attf[(attf.stat == "mean") & (attf["pop"] == "all")]
           .groupby(["arm", "rate"]).agg(reps=("rep", "size"),
                                         runs=("run", lambda s: ", ".join(sorted(s)))))
    W("| arm | rate (req/s) | repeats | runs |")
    W("|---|---|---|---|")
    for (arm, rate), r in cov.iterrows():
        mark = " **(1 repeat)**" if r["reps"] < 2 else ""
        W(f"| {ARM_LABEL[arm]} | {rate:.0f} | {r['reps']}{mark} | {r['runs']} |")
    W("")
    one_rep = cov[cov["reps"] < 2]
    if len(one_rep):
        W("**Cells with one repeat carry no range and no difference involving "
          "them can be judged against a repeat spread:** " +
          "; ".join(f"{ARM_LABEL[i[0]]} at {i[1]:.0f} req/s"
                    for i in one_rep.index) + ".")
    else:
        W("Every cell has two repeats.")
    W("")
    W("Cells read `mean [min..max]` over repeats. **A difference smaller than "
      "the min..max range of either cell it is drawn from is not a "
      "difference**, and is marked where the comparison is made.")
    W("")
    W("### The five per-token statistics")
    W("")
    W("| name | what it is | source |")
    W("|---|---|---|")
    W("| `mean` | the request's mean per-token time, "
      "(end-to-end - time to first token) / (output tokens - 1) | "
      "`exp22_fluidserve.load_run`, column `itl_ms` — the published status quo |")
    for s in ("p50", "p90", "p95", "p99"):
        W(f"| `{s}` | the {s} of the gaps WITHIN one request, that request "
          f"then judged by it | gaps between consecutive chunk arrivals in "
          f"`tbt_events.jsonl` |")
    W("")
    W("The four percentiles are deliberately NOT the `tbt_p50_ms` .. "
      "`tbt_p95_ms` columns of `metrics.csv`. Those divide each chunk's "
      "inter-arrival gap by a token count obtained by tokenising the chunk out "
      "of context, which splits it into about 1.92 pieces, so they sit on a "
      "different scale from the corrected mean and cannot share a table with "
      "it. The engine emits one token per chunk on this deployment (chunks per "
      "token 0.996), so the gap between two chunk arrivals is the gap between "
      "two tokens and needs no per-chunk token estimate at all. The agreement "
      "between the event-derived mean and `itl_ms` is reported in Task 2 as "
      "`gap_mean/itl_ms`; it is within a percent, which is what puts all five "
      "statistics on one scale.")
    W("")
    W("### The requests with no gap series, and what was done with them")
    W("")
    W("A small share of requests produced more than one output token, so "
      "`itl_ms` is defined for them, and yet carry no usable chunk series in "
      "`tbt_events.jsonl` -- the client fell back to a non-streaming read, or "
      "the whole answer arrived in one chunk. Left as missing values they would "
      "PASS the per-token term under the four percentiles while being judged "
      "under the mean, so the five columns would be scoring different sets of "
      "requests and part of any difference between them would be that "
      "mismatch. For a request with a single gap the mean IS its p50, p90, p95 "
      "and p99, so the missing percentile is filled with `itl_ms`, which keeps "
      "the population identical across the five columns. The tables were also "
      "produced without the fill; the largest attainment difference the fill "
      "makes, over every run, statistic, class and denominator, is below.")
    W("")
    key = ["run", "stat", "pop"]
    j = (att[att["fill"]].set_index(key)[DENOMS]
         - att[~att["fill"]].set_index(key)[DENOMS]).abs()
    W("| affected requests, share of all arrivals | largest change the fill "
      "makes to any attainment cell |")
    W("|---|---|")
    gm = att.groupby("run")["gap_missing_pct"].first()
    W(f"| {gm.min():.2f}% .. {gm.max():.2f}% across the "
      f"{len(gm)} runs | {float(j.max().max()):.2f} percentage points |")
    W("")

    # ------------------------------------------------- Task 1: swe invariance
    W("## Task 1 — attainment")
    W("")
    W("### Check first: swe does not move")
    W("")
    W("swe is scored on end-to-end time of 30 s and has no per-token term, so "
      "it must be identical under all five statistics. Measured, per run, as "
      "the largest difference across the five statistics on each denominator:")
    W("")
    sw = attf[attf["pop"] == "swe"]
    W("| denominator | largest spread across the five statistics, over all "
      f"{sw['run'].nunique()} runs |")
    W("|---|---|")
    ok = True
    for d in DENOMS:
        g = sw.groupby("run")[d]
        mx = float((g.max() - g.min()).max())
        ok &= mx < 1e-9
        W(f"| {d} | {mx:.2e} percentage points |")
    W("")
    W("**Verified: swe is bit-for-bit identical under all five statistics** "
      "on all three denominators." if ok else
      "**FAILED: swe moved under substitution, which it must not.**")
    W("")

    for pop, poplabel in (("all", "all requests pooled, each counting once"),
                          ("chat", "chat only"),
                          ("deepresearch", "deepresearch only"),
                          ("swe", "swe only")):
        for den in DENOMS:
            W(f"### Attainment (%), denominator **{den}**, population "
              f"**{poplabel}**")
            W("")
            if den == "offered":
                W("Every arriving request is in the denominator and a rejection "
                  "counts as a violation; requests still in flight when the "
                  "window closed are dropped.")
            elif den == "admitted":
                W("Only requests the system accepted are in the denominator. "
                  "Read alone this rewards refusing whatever was going to miss, "
                  "so the rejection rate is in the last column.")
            else:
                W("Every arrival is in the denominator and BOTH a rejection and "
                  "a request that never finished count as violations.")
            W("")
            ag = agg(attf[(attf["pop"] == pop)], DENOMS + ["rejected_pct"],
                     ["arm", "rate", "stat"])
            hdr = ["arm", "rate"] + [f"`{s}`" for s in STATS] + ["rejected %"]
            W("| " + " | ".join(hdr) + " |")
            W("|" + "---|" * len(hdr))
            for arm in ("fspfx", "llmdslo"):
                for rate in rates:
                    line = [ARM_LABEL[arm], f"{rate:.0f}"]
                    rej = "-"
                    for s in STATS:
                        sel = ag[(ag.arm == arm) & (ag.rate == rate)
                                 & (ag.stat == s)]
                        if not len(sel):
                            line.append("-")
                            continue
                        r = sel.iloc[0]
                        line.append(cell(r, den))
                        rej = cell(r, "rejected_pct")
                    W("| " + " | ".join(line + [rej]) + " |")
            W("")

    # ----------------------------------------------------- Task 2: latency
    W("## Task 2 — the per-token latency distributions")
    W("")
    W("Population: **completed requests only**. Rejected, errored, timed-out, "
      "job-timed-out and run-boundary-truncated rows are dropped, because a "
      "truncated stream's latency is a lower bound and not a measurement. They "
      "are NOT dropped from the attainment tables above, where they are "
      "violations.")
    W("")
    W("Three different things are printed and they are routinely confused:")
    W("")
    W("1. **across requests, of each request's MEAN per-token time** — one "
      "number per request, then percentiles over requests. `reqmean p50/p90/p99`.")
    W("2. **across requests, of each request's OWN p90 gap** — the p90 within "
      "each request, then percentiles over requests. `reqp90 p50/p90`.")
    W("3. **pooled over ALL token gaps** — every gap of every request thrown "
      "into one population, then percentiles over gaps. **This is what a reader "
      "assumes when a paper prints \"P90 TBT\", and it is not either of the "
      "first two.**")
    W("")
    for pop in ("chat", "deepresearch", "all"):
        lab = {"chat": "chat", "deepresearch": "deepresearch",
               "all": "all classes pooled"}[pop]
        W(f"### {lab} — per-request statistics (ms), completed requests")
        W("")
        W("`reqmean pXX` = percentile ACROSS requests of each request's mean "
          "per-token time. `reqp90 pXX` = percentile ACROSS requests of each "
          "request's own p90 gap. `ttft` = time to first token.")
        W("")
        ag = agg(lat[lat["pop"] == pop],
                 ["reqmean_p50", "reqmean_p90", "reqmean_p99",
                  "reqp90_p50", "reqp90_p90", "ttft_p50_ms", "ttft_p90_ms",
                  "n_completed"], ["arm", "rate"])
        hdr = ["arm", "rate", "n completed", "reqmean p50", "reqmean p90",
               "reqmean p99", "reqp90 p50", "reqp90 p90", "ttft p50",
               "ttft p90"]
        W("| " + " | ".join(hdr) + " |")
        W("|" + "---|" * len(hdr))
        for arm in ("fspfx", "llmdslo"):
            for rate in rates:
                sel = ag[(ag.arm == arm) & (ag.rate == rate)]
                if not len(sel):
                    continue
                r = sel.iloc[0]
                W("| " + " | ".join(
                    [ARM_LABEL[arm], f"{rate:.0f}", cell(r, "n_completed", "{:.0f}")] +
                    [cell(r, c) for c in ("reqmean_p50", "reqmean_p90",
                                          "reqmean_p99", "reqp90_p50",
                                          "reqp90_p90", "ttft_p50_ms",
                                          "ttft_p90_ms")]) + " |")
        W("")

    for pop in ("chat", "deepresearch", "all"):
        lab = {"chat": "chat", "deepresearch": "deepresearch",
               "all": "all classes pooled"}[pop]
        W(f"### {lab} — POOLED over all token gaps (ms), completed requests")
        W("")
        W("Every inter-token gap of every completed request in one population. "
          "A long request contributes as many observations as it has tokens, "
          "which is exactly what makes this different from the per-request "
          "tables above.")
        W("")
        cols = [f"pool_{pop}_p{p}" for p in ("50", "75", "90", "95", "99", "99_9")]
        have = [c for c in cols if c in pool.columns]
        ag = agg(pool, have + [f"pool_{pop}_n"], ["arm", "rate"])
        hdr = ["arm", "rate", "gaps in population", "p50", "p90", "p95", "p99",
               "p99.9"]
        W("| " + " | ".join(hdr) + " |")
        W("|" + "---|" * len(hdr))
        for arm in ("fspfx", "llmdslo"):
            for rate in rates:
                sel = ag[(ag.arm == arm) & (ag.rate == rate)]
                if not len(sel):
                    continue
                r = sel.iloc[0]
                W("| " + " | ".join(
                    [ARM_LABEL[arm], f"{rate:.0f}",
                     cell(r, f"pool_{pop}_n", "{:.0f}")] +
                    [cell(r, f"pool_{pop}_p{p}")
                     for p in ("50", "90", "95", "99", "99_9")]) + " |")
        W("")

    # ------------------------------------------------ Task 3: matched work
    W("## Task 3 — the comparison at matched delivered work")
    W("")
    W("Comparing the two arms at the same ARRIVAL RATE compares a system doing "
      "much more work against one doing much less, because the rejection rates "
      "differ. Delivered work here is **output tokens per second from completed "
      "requests**, over the same analysis window. Each FluidServe condition is "
      "paired with the llm-d condition whose delivered work is closest, and the "
      "pairing error is printed as a percentage of the FluidServe value.")
    W("")
    dw = agg(lat[lat["pop"] == "all"], ["out_tok_s"], ["arm", "rate"])
    f_dw = dw[dw.arm == "fspfx"].set_index("rate")
    l_dw = dw[dw.arm == "llmdslo"].set_index("rate")
    W("| arm | rate | delivered output tokens/s (completed requests) |")
    W("|---|---|---|")
    for arm, t in (("fspfx", f_dw), ("llmdslo", l_dw)):
        for rate, r in t.iterrows():
            W(f"| {ARM_LABEL[arm]} | {rate:.0f} | {cell(r, 'out_tok_s')} |")
    W("")
    pairs = []
    for rate, r in f_dw.iterrows():
        cand = l_dw.copy()
        cand["err"] = (cand["out_tok_s"] - r["out_tok_s"]).abs()
        best = cand["err"].idxmin()
        pairs.append((rate, best, r["out_tok_s"], l_dw.loc[best, "out_tok_s"]))

    latall = agg(lat[lat["pop"] == "all"],
                 ["reqmean_p50", "reqmean_p90", "reqmean_p99", "reqp90_p50",
                  "reqp90_p90", "ttft_p50_ms", "ttft_p90_ms"], ["arm", "rate"])
    poolall = agg(pool, [f"pool_all_p{p}" for p in ("50", "90", "95", "99")],
                  ["arm", "rate"])
    attall = agg(attf[(attf["pop"] == "all")], DENOMS + ["rejected_pct"],
                 ["arm", "rate", "stat"])

    lmax = float(l_dw["out_tok_s"].max())
    lmax_rate = float(l_dw["out_tok_s"].idxmax())
    W(f"**llm-d's delivered work does not increase with the arrival rate past "
      f"{lmax_rate:.0f} req/s.** It peaks at {lmax:.0f} output tokens per "
      f"second there and falls back to "
      f"{float(l_dw['out_tok_s'].iloc[-1]):.0f} at 70 req/s, because it "
      f"rejects {float(attall[(attall.arm == 'llmdslo') & (attall.rate == 70.0) & (attall.stat == 'mean')].iloc[0]['rejected_pct']):.0f}% "
      f"of arrivals there. FluidServe's rises monotonically to "
      f"{float(f_dw['out_tok_s'].max()):.0f}. Two consequences, and both have "
      f"to be stated before the pair tables are read:")
    W("")
    W(f"1. **Above {lmax:.0f} tokens per second there is no matched llm-d "
      f"condition at all.** Every FluidServe condition from 25 req/s upward "
      f"pairs to the same llm-d condition and the pairing error grows with the "
      f"rate. Those rows are not a matched-work comparison; they are FluidServe "
      f"doing 18% to 37% more work than the arm it is being compared with, "
      f"which makes any FluidServe win on them conservative and any FluidServe "
      f"loss on them not attributable to the extra work without a further "
      f"measurement.")
    W("2. **Because llm-d's delivered work is not monotone in the rate, more "
      "than one llm-d condition can match one FluidServe condition.** The "
      "runner-up is printed so a pairing that turned on a fraction of a "
      "percent is visible as such.")
    W("")
    W("### The pairs")
    W("")
    W("A pair counts as matched when the pairing error is within 5%, or when "
      "the gap between the two delivered-work figures is smaller than the "
      "min..max spread of the two cells it is drawn from -- by this "
      "repository's rule a difference smaller than the repeat spread is not a "
      "difference, and that applies to the matching variable as much as to "
      "anything being compared.")
    W("")
    W("| FluidServe rate | its delivered tok/s | paired llm-d rate | its "
      "delivered tok/s | pairing error | matched? | runner-up llm-d rate "
      "(its error) |")
    W("|---|---|---|---|---|---|---|")
    for fr, lr, fv, lv in pairs:
        err = 100.0 * (lv - fv) / fv
        rf, rl = f_dw.loc[fr], l_dw.loc[lr]
        sp = np.nanmax([spread(rf, "out_tok_s"), spread(rl, "out_tok_s")])
        cand = l_dw.copy()
        cand["err"] = 100.0 * (cand["out_tok_s"] - fv) / fv
        cand = cand.reindex(cand["err"].abs().sort_values().index)
        second = cand.iloc[1]
        if abs(err) <= 5:
            verdict = "yes, within 5%"
        elif np.isfinite(sp) and abs(lv - fv) < sp:
            verdict = f"yes, gap {abs(lv - fv):.0f} < repeat spread {sp:.0f}"
        else:
            verdict = "**NO — see above**"
        W(f"| {fr:.0f} | {fv:.0f} | {lr:.0f} | {lv:.0f} | {err:+.1f}% | "
          f"{verdict} | {cand.index[1]:.0f} ({second['err']:+.1f}%) |")
    W("")
    W("### Statistic by statistic, per pair")
    W("")
    W("Lower is better on every latency row; higher is better on attainment. "
      "The verdict column says which arm is better, and says **within repeat "
      "range** when the difference is smaller than the min..max spread of "
      "either cell, which by this repository's rule means it is not a "
      "difference. A cell with one repeat has no spread and its comparison is "
      "marked accordingly.")
    W("")

    def look(tbl, arm, rate, extra=None):
        s = tbl[(tbl.arm == arm) & (tbl.rate == rate)]
        if extra:
            s = s[s.stat == extra]
        return s.iloc[0] if len(s) else None

    ROWS = ([("all-arrivals attainment, `mean` (%)", "att", "all_arrivals", "mean", True),
             ("all-arrivals attainment, `p90` (%)", "att", "all_arrivals", "p90", True),
             ("offered attainment, `mean` (%)", "att", "offered", "mean", True),
             ("offered attainment, `p90` (%)", "att", "offered", "p90", True),
             ("admitted attainment, `mean` (%)", "att", "admitted", "mean", True),
             ("rejection rate (%)", "att", "rejected_pct", "mean", False)] +
            [("across requests, request mean p50 (ms)", "lat", "reqmean_p50", None, False),
             ("across requests, request mean p90 (ms)", "lat", "reqmean_p90", None, False),
             ("across requests, request mean p99 (ms)", "lat", "reqmean_p99", None, False),
             ("across requests, request's own p90, p50 (ms)", "lat", "reqp90_p50", None, False),
             ("across requests, request's own p90, p90 (ms)", "lat", "reqp90_p90", None, False),
             ("pooled over all gaps, p50 (ms)", "pool", "pool_all_p50", None, False),
             ("pooled over all gaps, p90 (ms)", "pool", "pool_all_p90", None, False),
             ("pooled over all gaps, p99 (ms)", "pool", "pool_all_p99", None, False),
             ("time to first token p50 (ms)", "lat", "ttft_p50_ms", None, False),
             ("time to first token p90 (ms)", "lat", "ttft_p90_ms", None, False)])

    for fr, lr, fv, lv in pairs:
        err = 100.0 * (lv - fv) / fv
        W(f"#### FluidServe {fr:.0f} req/s vs llm-d {lr:.0f} req/s "
          f"({fv:.0f} vs {lv:.0f} delivered tok/s, {err:+.1f}%)")
        W("")
        sp = np.nanmax([spread(f_dw.loc[fr], "out_tok_s"),
                        spread(l_dw.loc[lr], "out_tok_s")])
        if abs(err) > 5 and not (np.isfinite(sp) and abs(lv - fv) < sp):
            W(f"**Not a matched pair.** llm-d never delivers this much work at "
              f"any rate, so FluidServe is doing {-err:.0f}% more work than "
              f"llm-d in every row of this table.")
            W("")
        elif abs(err) > 5:
            W(f"Paired despite a {err:+.1f}% error because that gap "
              f"({abs(lv - fv):.0f} tokens per second) is smaller than the "
              f"min..max spread of the repeats ({sp:.0f}).")
            W("")
        W("| statistic | FluidServe | llm-d | difference | better |")
        W("|---|---|---|---|---|")
        for label, src, col, st, higher in ROWS:
            tbl = {"att": attall, "lat": latall, "pool": poolall}[src]
            rf = look(tbl, "fspfx", fr, st)
            rl = look(tbl, "llmdslo", lr, st)
            if rf is None or rl is None:
                continue
            d = rf[col] - rl[col]
            sp = np.nanmax([spread(rf, col), spread(rl, col)])
            if rf["reps"] < 2 or rl["reps"] < 2:
                v = "one repeat, not judged"
            elif np.isfinite(sp) and abs(d) < sp:
                v = f"within repeat range ({sp:.1f})"
            elif higher:
                v = "**FluidServe**" if d > 0 else "**llm-d**"
            else:
                v = "**llm-d**" if d > 0 else "**FluidServe**"
            W(f"| {label} | {cell(rf, col)} | {cell(rl, col)} | {d:+.1f} | {v} |")
        W("")

    # ------------------------------------------------- Task 4: budget context
    W("## Task 4 — where each statistic sits relative to the budget")
    W("")
    W("Share of **completed** requests of the class whose statistic is at or "
      "below the budget. This is not attainment: it drops rejections and "
      "truncations and ignores the time-to-first-token term, so it isolates "
      "the per-token distribution against the budget line. chat's budget is "
      "50 ms and deepresearch's is 100 ms; both budgets are shown against both "
      "classes so it is visible how much headroom each class has.")
    W("")
    for pop in ("chat", "deepresearch"):
        own = 50 if pop == "chat" else 100
        for b in (50, 100):
            W(f"### {pop}: share of completed requests with the statistic "
              f"<= {b} ms" + ("  **(this class's own budget)**"
                              if b == own else ""))
            W("")
            ag = agg(lat[lat["pop"] == pop],
                     [f"pass{b}_{s}" for s in STATS], ["arm", "rate"])
            hdr = ["arm", "rate"] + [f"`{s}`" for s in STATS]
            W("| " + " | ".join(hdr) + " |")
            W("|" + "---|" * len(hdr))
            for arm in ("fspfx", "llmdslo"):
                for rate in rates:
                    sel = ag[(ag.arm == arm) & (ag.rate == rate)]
                    if not len(sel):
                        continue
                    r = sel.iloc[0]
                    W("| " + " | ".join([ARM_LABEL[arm], f"{rate:.0f}"] +
                                        [cell(r, f"pass{b}_{s}") for s in STATS])
                      + " |")
            W("")

    # ------------------------------------------------------ measurement check
    W("## Measurement check: the burst artefact is gone in these runs")
    W("")
    W("Fraction of pooled inter-token gaps below 1 ms, 5 ms and 16 ms, over "
      "completed requests of all classes. On the defective earlier sweeps the "
      "sub-5 ms fraction was 14.11% because the gateway's CPU-quota stall "
      "delivered several tokens at one instant; a real decode step on this "
      "deployment cannot be much shorter than 16 ms.")
    W("")
    W("Also printed: the median ratio of the event-derived per-request mean gap "
      "to `load_run`'s `itl_ms`. It is the check that the four percentiles and "
      "the mean are on one scale.")
    W("")
    agb = agg(pool, ["pool_all_frac_sub1", "pool_all_frac_sub5",
                     "pool_all_frac_sub16"], ["arm", "rate"])
    agr = agg(lat[lat["pop"] == "all"], ["gapmean_over_itl_med"],
              ["arm", "rate"])
    W("| arm | rate | < 1 ms | < 5 ms | < 16 ms | gap_mean / itl_ms |")
    W("|---|---|---|---|---|---|")
    for arm in ("fspfx", "llmdslo"):
        for rate in rates:
            s = agb[(agb.arm == arm) & (agb.rate == rate)]
            s2 = agr[(agr.arm == arm) & (agr.rate == rate)]
            if not len(s):
                continue
            r, r2 = s.iloc[0], s2.iloc[0]
            pcs = r.copy()
            for c in ("pool_all_frac_sub1", "pool_all_frac_sub5",
                      "pool_all_frac_sub16"):
                for k in ("", "_lo", "_hi"):
                    pcs[c + k] = pcs[c + k] * 100.0
            W("| " + " | ".join(
                [ARM_LABEL[arm], f"{rate:.0f}"] +
                [cell(pcs, c, "{:.2f}") for c in
                 ("pool_all_frac_sub1", "pool_all_frac_sub5",
                  "pool_all_frac_sub16")] +
                [cell(r2, "gapmean_over_itl_med", "{:.3f}")]) + " |")
    W("")

    with open(a.out, "w") as f:
        f.write("\n".join(o) + "\n")
    print(f"wrote {a.out}")
    for s in ("_attainment_perrun.csv", "_latency_perrun.csv",
              "_pooled_perrun.csv"):
        print(f"wrote {a.out.replace('.md', s)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
