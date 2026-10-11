#!/usr/bin/env python3
"""The Aequitas test: is the rejected class also worse off, or only smaller?

Aequitas (SIGCOMM '22) answers the same objection this analysis is testing. An
admission controller that demotes or sheds part of the traffic invites the reply
that its headline number was bought by sacrificing that traffic. Aequitas closes
it by showing the outcome is not zero-sum across quality-of-service classes: the
class that was demoted also ends up better off than it was without the
controller. The measurement that supports that claim is per class and over the
requests that were actually served, not over the aggregate.

TASK 6 does exactly that comparison here. For every arm, rate and class it
describes the experience of the requests that were admitted AND completed:
end-to-end latency at p50/p90/p99, time to first token at p50/p90, per-token
time, the share that met their own class rule, and how many of them there were.
The question it answers is: for the class FluidServe rejects hardest, is a
request that FluidServe admits better off than a request llm-d admits? And
symmetrically for chat, the class llm-d rejects hardest.

  Per-token time is reported twice. The absolute figure is derived as
  (end-to-end - time to first token) / (output tokens - 1), because the recorded
  tbt_mean_ms column is about half the true value on runs collected before
  2026-07-30. Next to it is tbt_p90_ms / tbt_p50_ms, a ratio of two columns that
  carry the same calibration error, so the error cancels and the ratio is
  trustworthy whatever the absolute figures do. It measures burstiness within a
  single request's decode.

TASK 7 does the size-bucket half. Aequitas also closes the "you only kept the
cheap ones" charge by normalising by request size and reporting per size bucket.
Here the class is held fixed at chat and the requests are bucketed into quartiles
of the OFFERED output-length distribution, so composition cannot move between
arms. Length for a rejected request comes from the oracle table built in
tail2026_admitted_set: the same prompt completed under some other arm. If an
arm's admission rate falls monotonically with output length inside one class,
that is length-selective admission operating within a class, which is a sharper
version of the accusation than any cross-class comparison.

Rows carrying is_server_terminated, is_error, is_timeout or is_job_timeout are
excluded from every latency and length statistic, because their recorded values
are lower bounds rather than measurements; the counts are printed. Repeats are
averaged and ranges are min..max over the two.

    python3 analysis_scripts/request_level/tail2026_pareto.py \
        --pinned paper_experiment/static_sweep_2026-08 \
        --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from exp22_fluidserve import CLASSES  # noqa: E402
from tail2026_admitted_set import (  # noqa: E402
    ARM_LABEL, ARM_ORDER, load_rows, build_oracle, fmt, agg,
)

CLASS_LABEL = {"chat": "chat", "deepresearch": "deepresearch", "swe": "swe"}


def enrich(r):
    """Latency columns the Task 6 tables need, on the rows where they are real."""
    r = r.copy()
    r["ttft_s"] = pd.to_numeric(r["first_token_latency"], errors="coerce")
    r["e2e_s"] = pd.to_numeric(r["latency"], errors="coerce")
    # Derived rather than read: the recorded tbt_mean_ms column is roughly half
    # the true per-token time on every run collected before 2026-07-30.
    r["itl_ms"] = ((r["e2e_s"] - r["ttft_s"]) * 1000.0
                   / (r["out_tok"] - 1.0).where(r["out_tok"] > 1.0))
    p90 = pd.to_numeric(r.get("tbt_p90_ms"), errors="coerce")
    p50 = pd.to_numeric(r.get("tbt_p50_ms"), errors="coerce")
    # A ratio of two columns carrying the same calibration error: the error
    # cancels, so this is usable even though the absolute columns are not.
    r["tbt_ratio"] = (p90 / p50.where(p50 > 0))
    return r


def served(rows):
    """Admitted and completed: the population whose experience is measurable."""
    return rows[(~rows["rejected"]) & (~rows["len_bad"])]


def class_record(sub, window_s, n_offered_class):
    if sub.empty:
        return {}
    def qq(s, p):
        s = pd.to_numeric(s, errors="coerce").dropna()
        return float(s.quantile(p)) if len(s) else np.nan
    return {
        "n": len(sub),
        "rps": len(sub) / window_s,
        "admit_pct": 100.0 * len(sub) / n_offered_class if n_offered_class else np.nan,
        "e2e_p50": qq(sub["e2e_s"], 0.5),
        "e2e_p90": qq(sub["e2e_s"], 0.9),
        "e2e_p99": qq(sub["e2e_s"], 0.99),
        "ttft_p50": qq(sub["ttft_s"], 0.5),
        "ttft_p90": qq(sub["ttft_s"], 0.9),
        "itl_p50": qq(sub["itl_ms"], 0.5),
        "itl_mean": float(pd.to_numeric(sub["itl_ms"], errors="coerce").mean()),
        "tbt_ratio_p50": qq(sub["tbt_ratio"], 0.5),
        "met_pct": 100.0 * float((~sub["violate_served"]).mean()),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pinned", default="paper_experiment/static_sweep_2026-08")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/tail_2026-08-16")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    man = pd.read_csv(os.path.join(a.pinned, "manifest.tsv"), sep="\t")
    runs, pool = {}, []
    for _, m in man.iterrows():
        r = load_rows(os.path.join(a.pinned, "data", m["run"]))
        if r is None or r.empty:
            continue
        r = enrich(r)
        runs[m["run"]] = (r, m["arm"], float(m["req_per_s"]))
        pool.append(r[["base", "class", "rejected", "len_bad", "in_tok", "out_tok"]])
    oracle = build_oracle(pd.concat(pool, ignore_index=True))

    # ---------------- Task 6 -------------------------------------------------
    recs = []
    for run, (r, arm, rate) in runs.items():
        w = float(r["window_s"].iloc[0])
        s = served(r)
        rec = {"run": run, "arm": arm, "rate": rate,
               "n_excluded": int((r["len_bad"] & ~r["rejected"]).sum())}
        for c in CLASSES:
            n_off = int((r["class"] == c).sum())
            for k, v in class_record(s[s["class"] == c], w, n_off).items():
                rec[f"{c}_{k}"] = v
        recs.append(rec)
    t6 = pd.DataFrame(recs)
    t6.to_csv(os.path.join(a.out_dir, "tail2026_pareto_per_run.csv"), index=False)

    rates = sorted(t6["rate"].unique())
    arms = [x for x in ARM_ORDER if x in set(t6["arm"])]
    out = []
    P = out.append

    def table(df, title, cols, note=None, p=1, rate_list=None):
        P("")
        P(f"**{title}**")
        if note:
            P("")
            P(note)
        P("")
        P("| rate | " + " | ".join(ARM_LABEL[x] for x in arms) + " |")
        P("|---|" + "---|" * len(arms))
        for rt in (rate_list or rates):
            cells = []
            for arm in arms:
                sub = df[(df["arm"] == arm) & (df["rate"] == rt)]
                cells.append("-" if sub.empty else
                             " / ".join(fmt(*agg(sub, c), p=p) for c in cols))
            P(f"| {rt:.0f} | " + " | ".join(cells) + " |")

    P("## Task 6 - the Aequitas Pareto test, per class")
    P("")
    P("Every figure below is over the requests that were ADMITTED AND COMPLETED "
      "under that arm. A rejected request is not in this population, and neither "
      "is one whose stream was cut by the run boundary. The population is "
      "therefore different for every arm, which is the point: the question is "
      "whether the requests an arm did serve were served well, next to how many "
      "of them there were.")
    for c in CLASSES:
        P("")
        P(f"### {CLASS_LABEL[c]}")
        table(t6, f"{c}: admitted and completed, count per second / % of that "
                  f"class's arrivals",
              [f"{c}_rps", f"{c}_admit_pct"], p=2)
        table(t6, f"{c}: end-to-end latency of those requests, p50 / p90 / p99 (s)",
              [f"{c}_e2e_p50", f"{c}_e2e_p90", f"{c}_e2e_p99"], p=2)
        table(t6, f"{c}: time to first token of those requests, p50 / p90 (s)",
              [f"{c}_ttft_p50", f"{c}_ttft_p90"], p=2)
        table(t6, f"{c}: per-token time of those requests, p50 / mean (ms), "
                  f"derived from (e2e - ttft) / (output tokens - 1)",
              [f"{c}_itl_p50", f"{c}_itl_mean"], p=1)
        table(t6, f"{c}: within-request decode burstiness, median of "
                  f"tbt_p90_ms / tbt_p50_ms",
              note="A ratio of two columns that carry the same calibration "
                   "error, so the error cancels. 1.0 would mean a perfectly even "
                   "decode; larger means the request's own token stream stalled.",
              cols=[f"{c}_tbt_ratio_p50"], p=2)
        table(t6, f"{c}: share of those admitted-and-completed requests that met "
                  f"the class rule (%)",
              [f"{c}_met_pct"], p=1)

    # ---------------- Task 7 -------------------------------------------------
    P("")
    P("## Task 7 - size buckets inside the chat class")
    P("")
    P("Class composition is held fixed by looking only at chat. Every chat "
      "arrival, admitted or rejected, is given an output length from the oracle "
      "table (the same prompt's median completed length pooled over all 80 runs) "
      "and placed in a quartile of the OFFERED chat length distribution at that "
      "rate. The quartile edges are computed once per rate from the arrivals "
      "pooled over all arms, so the buckets mean the same thing for every arm. "
      "If admission rate falls monotonically with bucket inside this one class, "
      "the policy is selecting on length rather than on class.")

    bucket_rates = [25.0, 35.0]
    b_recs = []
    for rt in bucket_rates:
        chat_all = []
        for run, (r, arm, rate) in runs.items():
            if rate != rt:
                continue
            j = r[r["class"] == "chat"].join(oracle, on="base")
            chat_all.append(j["oracle_out"])
        if not chat_all:
            continue
        allv = pd.concat(chat_all).dropna()
        edges = [allv.quantile(x) for x in (0.25, 0.5, 0.75)]
        for run, (r, arm, rate) in runs.items():
            if rate != rt:
                continue
            w = float(r["window_s"].iloc[0])
            j = r[r["class"] == "chat"].join(oracle, on="base")
            j = j[j["oracle_out"].notna()]
            j["bucket"] = np.digitize(j["oracle_out"], edges)
            for b in range(4):
                sub = j[j["bucket"] == b]
                if sub.empty:
                    continue
                adm = sub[~sub["rejected"]]
                ok = adm[~adm["len_bad"]]
                b_recs.append({
                    "run": run, "arm": arm, "rate": rate, "bucket": b,
                    "n_offered": len(sub),
                    "len_p50": float(sub["oracle_out"].median()),
                    "admit_pct": 100.0 * len(adm) / len(sub),
                    "met_pct": (100.0 * float((~ok["violate_served"]).mean())
                                if len(ok) else np.nan),
                    "itl_p50": (float(ok["itl_ms"].quantile(0.5))
                                if len(ok) else np.nan),
                    "itl_mean": float(pd.to_numeric(ok["itl_ms"],
                                                    errors="coerce").mean())
                               if len(ok) else np.nan,
                    "tbt_ratio_p50": (float(ok["tbt_ratio"].quantile(0.5))
                                      if len(ok) else np.nan),
                    "ttft_p90": (float(ok["ttft_s"].quantile(0.9))
                                 if len(ok) else np.nan),
                })
    t7 = pd.DataFrame(b_recs)
    t7.to_csv(os.path.join(a.out_dir, "tail2026_buckets_per_run.csv"), index=False)

    for rt in bucket_rates:
        sub = t7[t7["rate"] == rt]
        if sub.empty:
            continue
        med = sub.groupby("bucket")["len_p50"].mean().round(0).to_dict()
        P("")
        P(f"**Chat at {rt:.0f} req/s. Bucket median output length: "
          + ", ".join(f"Q{b + 1} {med.get(b, float('nan')):.0f} tok"
                      for b in range(4)) + "**")
        for metric, label, p in (("admit_pct", "admission rate, %", 1),
                                 ("met_pct", "SLO attainment among admitted, %", 1),
                                 ("itl_p50", "per-token time p50, ms", 1),
                                 ("itl_mean", "per-token time mean, ms", 1),
                                 ("tbt_ratio_p50",
                                  "within-request tbt_p90/tbt_p50", 2),
                                 ("ttft_p90", "time to first token p90, s", 2)):
            P("")
            P(f"{label}, by output-length quartile Q1..Q4")
            P("")
            P("| arm | Q1 | Q2 | Q3 | Q4 |")
            P("|---|---|---|---|---|")
            for arm in arms:
                cells = []
                for b in range(4):
                    cell = sub[(sub["arm"] == arm) & (sub["bucket"] == b)]
                    cells.append("-" if cell.empty else
                                 fmt(*agg(cell, metric), p=p))
                P(f"| {ARM_LABEL[arm]} | " + " | ".join(cells) + " |")

    txt = "\n".join(out)
    with open(os.path.join(a.out_dir, "tail2026_pareto_tables.md"), "w") as fh:
        fh.write(txt + "\n")
    print(txt)
    print(f"\nwrote {a.out_dir}/tail2026_pareto_tables.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
