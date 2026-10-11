#!/usr/bin/env python3
"""Re-score all-arrivals attainment with the per-token statistic swapped.

The headline rule for chat and deep research is "time to first token AND
per-token time", and the per-token term is currently the corrected mean
published by exp22_fluidserve.load_run as `itl_ms`. This script re-scores the
same runs with that term replaced, one at a time, by the raw client percentile
columns tbt_p50_ms .. tbt_p95_ms, so the ranking between arms can be read as a
function of where on the quantile axis the budget is applied. The swe class is
scored on end-to-end time of 30 s and is therefore identical under every
substitution; only chat and deep research move.

CAVEAT THAT THE LADDER CANNOT REMOVE. `itl_ms` is a CORRECTED mean: the client
recorded `tbt_mean_ms` at about 1/1.92 of the true per-token time because each
streamed chunk was tokenised out of context and the resulting token count was
used as the divisor, and load_run replaces it with (e2e - ttft)/(tokens - 1).
The percentile columns tbt_p50_ms .. tbt_p95_ms were never corrected, so they
carry the same downward bias. The ladder is therefore NOT an apples-to-apples
sweep of one distribution: the mean column is on the true scale and the
percentile columns are on the raw scale. Both versions are produced --
percentiles as recorded, and percentiles multiplied by 1.92 -- so the size of
that scale factor is visible rather than folded into the conclusion.

Everything else follows exp22_fluidserve: the same analysis window (60 s
warm-up, 20 s drain), the same class rules, the same treatment of rejected,
errored and run-boundary-cutoff requests. The denominator here is ALL ARRIVALS
(all_arrivals_attainment.py's third column): every request that arrived is in
the denominator and a rejection or an unfinished request is a violation.

  python3 tail2026_quantile_ladder.py --out <markdown path>
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, SLO_RULES, CLASSES, truthy  # noqa: E402
from all_arrivals_attainment import parse_dir  # noqa: E402

EXPDIR = "/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
PINNED = os.path.join(EXPDIR, "paper_experiment/static_sweep_2026-08/data")

# The status quo first, then the quantile axis in increasing order.
STATS = ["itl_ms", "tbt_p50_ms", "tbt_p75_ms", "tbt_p80_ms",
         "tbt_p85_ms", "tbt_p90_ms", "tbt_p95_ms"]
QUANTILES = STATS[1:]
SCALE = 1.92          # the client-side under-count of the per-token time
ARMS = ["fspfx", "llmdslo", "vllmcache", "slo", "polyserve"]
ARM_LABEL = {"fspfx": "FluidServe", "llmdslo": "llm-d",
             "vllmcache": "vLLM router", "slo": "Llumnix SLO",
             "polyserve": "PolyServe"}


def score(rows, tbt, subset=None):
    """All-arrivals attainment with `tbt` used as the per-token statistic.

    Reproduces load_run's miss test exactly, with only the per-token series
    substituted: a request misses if it broke its class rule, and separately a
    request that was rejected or errored is a violation. Run-boundary cutoffs
    stay in the denominator here because that is what the all-arrivals column
    is for -- an arm that stops rejecting turns its rejections into unfinished
    requests, and dropping them would hide exactly that.
    """
    if subset is not None:
        rows, tbt = rows[subset], tbt[subset]
    if not len(rows):
        return np.nan, 0
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
    met = (~violate) & (~rows["cutoff"])
    return 100.0 * met.sum() / len(rows), len(rows)


def completed(rows):
    """Requests that ran to completion, for length- and latency-derived stats.

    A truncated or failed request has a latency that is a lower bound and not a
    measurement, so it is dropped here. It is NOT dropped from the attainment
    denominator, which counts it as a violation.
    """
    bad = (truthy(rows, "is_server_terminated") | truthy(rows, "is_error")
           | truthy(rows, "is_timeout") | truthy(rows, "is_job_timeout"))
    return rows[~bad & ~rows["rejected"]]


def collect():
    recs, dists = [], []
    for d in sorted(glob.glob(os.path.join(PINNED, "*"))):
        if not os.path.isdir(d):
            continue
        rows = load_run(d)
        if rows is None or rows.empty:
            print(f"  skipped (no usable rows): {os.path.basename(d)}")
            continue
        # load_run keeps every non-job_summary row; the rule for this work is the
        # request rows, so state the filter rather than assume the two agree.
        rows = rows[rows["agent"].astype(str) == "request"].copy()
        if rows.empty:
            print(f"  skipped (no agent==request rows): {os.path.basename(d)}")
            continue
        meta = parse_dir(d)
        base = {"run": os.path.basename(d), "arm": meta["arm"],
                "rate": meta["rate"], "n": len(rows)}

        series = {"itl_ms": pd.to_numeric(rows["itl_ms"], errors="coerce")}
        for q in QUANTILES:
            series[q] = pd.to_numeric(rows[q], errors="coerce")

        for name, s in series.items():
            for scaled in (False, True):
                if name == "itl_ms" and scaled:
                    continue          # the mean is already on the true scale
                v = s * SCALE if scaled else s
                a, n = score(rows, v)
                rec = dict(base, stat=name, scaled=scaled, all_arrivals=a)
                for c in CLASSES:
                    rec[c] = score(rows, v, rows["class"] == c)[0]
                recs.append(rec)

        # Task 4: the per-token distribution itself, completed chat only.
        chat = completed(rows[rows["class"] == "chat"])
        chat = chat[pd.to_numeric(chat["tbt_p50_ms"], errors="coerce").notna()]
        if len(chat):
            p50 = pd.to_numeric(chat["tbt_p50_ms"], errors="coerce")
            p90 = pd.to_numeric(chat["tbt_p90_ms"], errors="coerce")
            itl = pd.to_numeric(chat["itl_ms"], errors="coerce")
            ratio = (p90 / p50).replace([np.inf, -np.inf], np.nan).dropna()
            row = dict(base, n_chat=len(chat))
            for label, ser in (("tbt_p50_ms", p50), ("itl_ms", itl)):
                for q in (10, 25, 50, 75, 90, 95, 99):
                    row[f"{label}_p{q}"] = ser.quantile(q / 100.0)
                row[f"{label}_max"] = ser.max()
                row[f"{label}_mean"] = ser.mean()
            row["ratio_p90p50_median"] = ratio.median()
            row["ratio_p90p50_p90"] = ratio.quantile(0.90)
            dists.append(row)
    return pd.DataFrame(recs), pd.DataFrame(dists)


def agg(df, value="all_arrivals"):
    g = df.groupby(["stat", "scaled", "arm", "rate"])[value]
    return pd.DataFrame({"mean": g.mean(), "lo": g.min(), "hi": g.max(),
                         "reps": g.size()}).reset_index()


def cell(r):
    return f"{r['mean']:.1f} [{r['lo']:.1f}..{r['hi']:.1f}]"


def ladder_table(a, arm, scaled, out):
    rates = sorted(a["rate"].unique())
    cols = ["itl_ms"] + QUANTILES
    hdr = ["rate"] + [c.replace("_ms", "").replace("tbt_", "") for c in cols]
    out.append("| " + " | ".join(hdr) + " |")
    out.append("|" + "---|" * len(hdr))
    for rate in rates:
        line = [f"{rate:.0f}"]
        for c in cols:
            s = False if c == "itl_ms" else scaled
            sel = a[(a.arm == arm) & (a.rate == rate) & (a.stat == c)
                    & (a.scaled == s)]
            line.append(cell(sel.iloc[0]) if len(sel) else "-")
        out.append("| " + " | ".join(line) + " |")


def qname(q):
    return q.replace("tbt_", "").replace("_ms", "")


def crossing(a, scaled, out):
    """Highest quantile at which FluidServe still leads, lowest at which llm-d does.

    Both are reported because the difference does not have to change sign only
    once: reporting one endpoint alone would hide a ladder that alternates.
    The mean column is not on this axis and is excluded.
    """
    out.append("| rate | mean (`itl`) lead (pp) | highest quantile where "
               "FluidServe leads | its lead (pp) | lowest quantile where llm-d "
               "leads | its lead (pp) | monotone? |")
    out.append("|---|---|---|---|---|---|---|")
    for rate in sorted(a["rate"].unique()):
        def diff(stat, sc):
            f = a[(a.arm == "fspfx") & (a.rate == rate) & (a.stat == stat)
                  & (a.scaled == sc)]
            l = a[(a.arm == "llmdslo") & (a.rate == rate) & (a.stat == stat)
                  & (a.scaled == sc)]
            if not len(f) or not len(l):
                return None
            return f.iloc[0]["mean"] - l.iloc[0]["mean"]

        ds = [(q, diff(q, scaled)) for q in QUANTILES]
        ds = [(q, d) for q, d in ds if d is not None]
        fs = [(q, d) for q, d in ds if d > 0]
        ld = [(q, d) for q, d in ds if d <= 0]
        mean_d = diff("itl_ms", False)
        # A single sign change means the crossing is a point on the axis; more
        # than one means the two endpoints do not bracket a single crossing.
        signs = [d > 0 for _, d in ds]
        flips = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i - 1])
        out.append(
            f"| {rate:.0f} | {mean_d:+.1f} "
            f"| {qname(fs[-1][0]) if fs else 'none'} "
            f"| {fs[-1][1]:+.1f} |" .replace("| nan |", "| - |")
            if fs else
            f"| {rate:.0f} | {mean_d:+.1f} | none | - |")
        tail = (f" {qname(ld[0][0])} | {ld[0][1]:+.1f} |" if ld
                else " none | - |")
        out[-1] = out[-1] + tail + (" yes |" if flips <= 1 else
                                    f" no ({flips} sign changes) |")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(
        EXPDIR, "results/aggregate_analysis/tail_2026-08-16/"
                "03_quantile_ladder.md"))
    a = ap.parse_args()

    df, dist = collect()
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    df.to_csv(a.out.replace(".md", "_perrun.csv"), index=False)
    dist.to_csv(a.out.replace(".md", "_chat_distribution.csv"), index=False)

    ag = agg(df)
    o = []
    o.append("# Quantile ladder: where the FluidServe / llm-d ranking crosses")
    o.append("")
    o.append(f"Runs: the pinned five-arm static sweep, "
             f"`paper_experiment/static_sweep_2026-08/data/` "
             f"({df['run'].nunique()} runs, 2 repeats per arm per rate).")
    o.append("Denominator: **all arrivals** — every arriving request is in the "
             "denominator, and both a rejection and a request still unfinished "
             "when the load window closed count as violations. Analysis window "
             "is exp22_fluidserve's: 60 s warm-up dropped, 20 s drain dropped.")
    o.append("Rules: chat TTFT 5 s + per-token 50 ms, deepresearch TTFT 10 s + "
             "per-token 100 ms, swe end-to-end 30 s. **swe is unaffected by the "
             "substitution** — it has no per-token term — so the movement below "
             "is chat and deepresearch only.")
    o.append("")
    o.append("## The caveat that the ladder cannot remove")
    o.append("")
    o.append("`itl_ms` is a **corrected mean**: the client recorded "
             "`tbt_mean_ms` at about **1/1.92** of the true per-token time "
             "because each streamed chunk was tokenised out of context and that "
             "token count was used as the divisor, and `load_run` replaces the "
             "column with `(e2e - ttft)/(output_tokens - 1)`. The percentile "
             "columns `tbt_p50_ms` .. `tbt_p95_ms` are the **raw client values** "
             "and were never corrected. So version A below compares a corrected "
             "mean against uncorrected percentiles and is NOT apples to apples; "
             "version B multiplies every percentile by 1.92 before the budget "
             "test so the effect of that one scale factor is visible. Whether "
             "1.92 is the right factor for a percentile (it was measured on the "
             "sum of the chunk-token estimates, not per quantile) is being "
             "checked separately; neither version should be quoted as the "
             "corrected tail until it is.")
    o.append("")

    for scaled, tag in ((False, "A — percentiles as recorded (raw)"),
                        (True, "B — percentiles x 1.92")):
        o.append(f"## Version {tag}")
        o.append("")
        o.append("Cells are `mean [min..max]` over the 2 repeats. The `itl` "
                 "column is the status quo and is identical in both versions.")
        for arm in ARMS:
            o.append("")
            o.append(f"### {ARM_LABEL[arm]} (`{arm}`) — all-arrivals "
                     f"attainment (%)")
            o.append("")
            ladder_table(ag, arm, scaled, o)
        o.append("")
        o.append(f"### Crossing quantile, version {tag[0]}")
        o.append("")
        crossing(ag, scaled, o)
        o.append("")

    o.append("## Per class, FluidServe and llm-d, rates 15 / 25 / 35")
    o.append("")
    o.append("Same denominator, restricted to the class: all arrivals OF THAT "
             "CLASS, rejection and unfinished counting as violations. swe is "
             "printed to show it does not move.")
    for scaled, tag in ((False, "raw"), (True, "x1.92")):
        for cls in CLASSES:
            o.append("")
            o.append(f"### {cls}, percentiles {tag}")
            o.append("")
            ac = agg(df, cls)
            cols = ["itl_ms"] + QUANTILES
            hdr = (["arm", "rate"] +
                   [c.replace("_ms", "").replace("tbt_", "") for c in cols])
            o.append("| " + " | ".join(hdr) + " |")
            o.append("|" + "---|" * len(hdr))
            for arm in ("fspfx", "llmdslo"):
                for rate in (15.0, 25.0, 35.0):
                    line = [ARM_LABEL[arm], f"{rate:.0f}"]
                    for c in cols:
                        s = False if c == "itl_ms" else scaled
                        sel = ac[(ac.arm == arm) & (ac.rate == rate)
                                 & (ac.stat == c) & (ac.scaled == s)]
                        line.append(cell(sel.iloc[0]) if len(sel) else "-")
                    o.append("| " + " | ".join(line) + " |")

    o.append("")
    o.append("## The per-token distribution itself — completed chat requests")
    o.append("")
    o.append("Per-repeat, not averaged. Completed only: rows with "
             "`is_server_terminated`, `is_error`, `is_timeout` or "
             "`is_job_timeout` true are dropped, as are rejected rows, because "
             "their latency is a lower bound and not a measurement. Quantiles "
             "are ACROSS requests of each per-request statistic.")
    o.append("")
    sub = dist[dist["rate"].isin([15.0, 35.0])
               & dist["arm"].isin(["fspfx", "llmdslo"])].sort_values(
                   ["arm", "rate", "run"])
    for label, col in (("raw `tbt_p50_ms` (ms)", "tbt_p50_ms"),
                       ("corrected `itl_ms` (ms)", "itl_ms")):
        o.append("")
        o.append(f"### {label}, across requests")
        o.append("")
        o.append("| arm | rate | run | n | p10 | p25 | p50 | p75 | p90 | p95 "
                 "| p99 | max | mean |")
        o.append("|" + "---|" * 13)
        for _, r in sub.iterrows():
            o.append("| " + " | ".join([
                ARM_LABEL.get(r["arm"], r["arm"]), f"{r['rate']:.0f}",
                r["run"], f"{int(r['n_chat'])}"] +
                [f"{r[f'{col}_p{q}']:.1f}" for q in (10, 25, 50, 75, 90, 95, 99)] +
                [f"{r[f'{col}_max']:.1f}", f"{r[f'{col}_mean']:.1f}"]) + " |")
    o.append("")
    o.append("### Within-request tail ratio `tbt_p90_ms / tbt_p50_ms`")
    o.append("")
    o.append("Computed per request and then summarised across requests, so a "
             "constant calibration error on the per-token time divides out.")
    o.append("")
    o.append("The last two columns are not part of that ratio: they are the "
             "observed gap between the corrected mean and the raw median over "
             "the same requests, i.e. how far apart the two scales in version A "
             "actually are on this data. If the raw columns were low by exactly "
             "the 1.92 used in version B, and if the mean and the median of a "
             "request's per-token time were the same quantity, these would read "
             "1.92; they do not, so version B over-corrects and version A "
             "under-corrects by an amount that differs by arm and by rate.")
    o.append("")
    o.append("| arm | rate | run | n | median ratio | p90 of ratio "
             "| median(itl)/median(p50) | mean(itl)/mean(p50) |")
    o.append("|---|---|---|---|---|---|---|---|")
    for _, r in sub.iterrows():
        o.append(f"| {ARM_LABEL.get(r['arm'], r['arm'])} | {r['rate']:.0f} "
                 f"| {r['run']} | {int(r['n_chat'])} "
                 f"| {r['ratio_p90p50_median']:.3f} "
                 f"| {r['ratio_p90p50_p90']:.3f} "
                 f"| {r['itl_ms_p50'] / r['tbt_p50_ms_p50']:.3f} "
                 f"| {r['itl_ms_mean'] / r['tbt_p50_ms_mean']:.3f} |")
    o.append("")

    with open(a.out, "w") as f:
        f.write("\n".join(o) + "\n")
    print(f"wrote {a.out}")
    print(f"wrote {a.out.replace('.md', '_perrun.csv')}")
    print(f"wrote {a.out.replace('.md', '_chat_distribution.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
