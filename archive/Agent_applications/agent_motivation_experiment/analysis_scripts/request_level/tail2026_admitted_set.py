#!/usr/bin/env python3
"""Is the attainment advantage bought by admitting only the cheap requests?

The accusation under test: FluidServe's throughput and attainment advantage
comes from rejecting expensive requests and keeping cheap ones, which enlarges
the decode batch and inflates token throughput without any real scheduling
improvement.

This script builds the request-side evidence (Tasks 1-3 of the audit):

  1. what each policy ADMITS versus what was OFFERED -- counts, class shares,
     and input/output length distributions, admitted versus rejected;
  2. whether the advantage is in REQUESTS or in TOKENS -- SLO-meeting requests
     per second next to output tokens belonging to SLO-meeting requests per
     second, overall and per class;
  3. a composition-neutral score -- per-class attainment recombined with ONE
     fixed class weighting shared by every arm, so an arm cannot raise its
     score by changing which classes remain in its own denominator.

Definitions are imported from exp22_fluidserve so the scoring rule, the
analysis window and the class assignment are identical to every other figure
in this repository. Two things are added here:

  oracle length     A rejected request produces no output, so its length is not
                    observable in the run that rejected it. The same task ids
                    are replayed by every arm at every rate, so the length of a
                    rejected request is recovered as the median output length
                    of the same task id wherever it DID complete, pooled over
                    all 80 pinned runs. This makes "the offered set" a
                    measurable quantity in tokens rather than only in requests,
                    which is what the accusation is about. Coverage is
                    reported; requests with no oracle entry are excluded from
                    the oracle columns only.
  length filter     A request cut off by the run boundary, errored or timed out
                    has an output_tokens that is a LOWER BOUND on its length,
                    not its length. Every length statistic here excludes rows
                    with is_server_terminated, is_error, is_timeout or
                    is_job_timeout set, and the excluded count is reported.
                    Rejected requests have output 0 and are handled by the
                    oracle instead.

    python3 analysis_scripts/request_level/tail2026_admitted_set.py \
        --pinned paper_experiment/static_sweep_2026-08 \
        --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import collections
import os
import re
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from exp22_fluidserve import (  # noqa: E402
    CLASSES, SLO_RULES, WARMUP_S, DRAIN_S, truthy, mean_inter_token_ms,
)
from plot_per_engine_attainment import class_of  # noqa: E402

ARM_LABEL = {
    "fspfx": "FluidServe",
    "llmdslo": "llm-d",
    "vllmcache": "vLLM router",
    "slo": "Llumnix SLO",
    "polyserve": "PolyServe",
}
ARM_ORDER = ["fspfx", "llmdslo", "vllmcache", "slo", "polyserve"]
REPLAY_SUFFIX = re.compile(r"__r\d+$")


def base_task(t):
    """The prompt identity behind a replayed arrival.

    `sg-00000-t01__r01` and `sg-00000-t01__r07` are the same prompt sent twice;
    the `__rNN` suffix is the replay index the load generator appends. Stripping
    it is what lets a request that one arm rejected be given the length it had
    when another arm ran it.
    """
    return REPLAY_SUFFIX.sub("", str(t))


def load_rows(run_dir):
    """Requests inside the analysis window, with outcome flags and class.

    Same window and same scoring rule as exp22_fluidserve.load_run. The one
    difference is the row filter: `agent == "request"`, because metrics.csv
    writes each request twice, once as a `request` row and once as a
    `job_summary` row, and only the first carries the per-request timings.
    """
    p = os.path.join(run_dir, "metrics.csv")
    if not os.path.isfile(p):
        return None
    df = pd.read_csv(p, low_memory=False)
    r = df[df["agent"] == "request"].copy()
    r = r[pd.to_numeric(r.get("start_time"), errors="coerce").notna()]
    if r.empty:
        return None
    t0 = r["start_time"].min()
    r["rel"] = r["start_time"] - t0
    end = min(r["end_time"].max() - t0, r["rel"].max())
    r = r[(r["rel"] >= WARMUP_S) & (r["rel"] < end - DRAIN_S)].copy()
    if r.empty:
        return None

    r["class"] = r["task_id"].map(class_of)
    r["base"] = r["task_id"].map(base_task)
    r["rejected"] = truthy(r, "is_rejected")
    r["errored"] = truthy(r, "is_error") | truthy(r, "is_timeout")
    r["cutoff"] = truthy(r, "is_server_terminated") & ~r["rejected"] & ~r["errored"]
    # Any of the four flags means the recorded output length is a lower bound
    # on the true length rather than the length. Kept separate from `cutoff`,
    # which is the run-boundary rule the attainment denominators use.
    r["len_bad"] = (truthy(r, "is_server_terminated") | truthy(r, "is_error")
                    | truthy(r, "is_timeout") | truthy(r, "is_job_timeout"))

    ttft = pd.to_numeric(r["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(r["latency"], errors="coerce")
    tbt = mean_inter_token_ms(r, ttft, e2e)
    miss = pd.Series(False, index=r.index)
    for cname, rule in SLO_RULES.items():
        m = r["class"] == cname
        if "e2e" in rule:
            miss.loc[m] = e2e[m] > rule["e2e"]
        else:
            miss.loc[m] = (ttft[m] > rule["ttft"]) | (tbt[m] > rule["tbt"])
    miss = miss | (ttft.isna() & ~r["cutoff"])
    r["violate_served"] = miss
    r["violate_offered"] = miss | r["rejected"] | r["errored"]
    r["in_tok"] = pd.to_numeric(r["input_tokens"], errors="coerce")
    r["out_tok"] = pd.to_numeric(r["output_tokens"], errors="coerce")
    r["window_s"] = r["rel"].max() - r["rel"].min()
    return r


def q(series, name):
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return {f"{name}_n": 0, f"{name}_mean": np.nan,
                f"{name}_p50": np.nan, f"{name}_p90": np.nan}
    return {f"{name}_n": int(len(s)), f"{name}_mean": float(s.mean()),
            f"{name}_p50": float(s.quantile(0.5)),
            f"{name}_p90": float(s.quantile(0.9))}


def attain(rows, col):
    """exp22_fluidserve.attain, repeated here so this file is self-contained."""
    rows = rows[~rows["cutoff"]]
    if col == "violate_served":
        rows = rows[~rows["rejected"]]
    return 100.0 * (~rows[col]).mean() if len(rows) else np.nan


def build_oracle(all_rows):
    """Median clean output and input length per prompt, pooled over every run.

    Pooled across all arms and all rates on purpose: the quantity wanted is the
    length of the prompt, and a prompt's length is a property of the prompt, not
    of the policy that happened to serve it. Pooling also maximises the chance
    that a prompt one arm always rejected was completed somewhere.
    """
    clean = all_rows[(~all_rows["rejected"]) & (~all_rows["len_bad"])
                     & (all_rows["out_tok"] > 0)]
    g = clean.groupby("base")
    return pd.DataFrame({"oracle_out": g["out_tok"].median(),
                         "oracle_in": g["in_tok"].median(),
                         "oracle_n": g["out_tok"].size()})


def run_record(rows, oracle, run, arm, rate):
    w = float(rows["window_s"].iloc[0])
    rec = {"run": run, "arm": arm, "rate": rate, "window_s": round(w, 1)}
    rows = rows.join(oracle, on="base")

    off = rows
    adm = rows[~rows["rejected"]]
    rej = rows[rows["rejected"]]
    rec["n_offered"] = len(off)
    rec["n_admitted"] = len(adm)
    rec["n_rejected"] = len(rej)
    rec["reject_pct"] = 100.0 * len(rej) / len(off)
    rec["offered_rps"] = len(off) / w
    rec["admitted_rps"] = len(adm) / w
    # A rejection sets is_error, so the blanket flag count would read as
    # "26,066 truncated requests" for an arm that merely rejected 26,000. The
    # count that means "the recorded output length is a lower bound" is the
    # non-rejected one.
    rec["n_len_excluded"] = int((off["len_bad"] & ~off["rejected"]).sum())
    rec["n_flagged_all"] = int(off["len_bad"].sum())
    rec["n_cutoff"] = int(off["cutoff"].sum())
    rec["oracle_cov_offered"] = 100.0 * off["oracle_out"].notna().mean()
    rec["oracle_cov_rejected"] = (100.0 * rej["oracle_out"].notna().mean()
                                 if len(rej) else np.nan)

    # class composition, offered and admitted
    for c in CLASSES:
        oc = off[off["class"] == c]
        ac = adm[adm["class"] == c]
        rc = rej[rej["class"] == c]
        rec[f"off_n_{c}"] = len(oc)
        rec[f"adm_n_{c}"] = len(ac)
        rec[f"off_share_{c}"] = 100.0 * len(oc) / len(off)
        rec[f"adm_share_{c}"] = 100.0 * len(ac) / len(adm) if len(adm) else np.nan
        rec[f"rej_pct_{c}"] = 100.0 * len(rc) / len(oc) if len(oc) else np.nan

    # measured lengths, clean rows only
    adm_ok = adm[~adm["len_bad"]]
    rec.update(q(adm_ok["out_tok"], "adm_out"))
    rec.update(q(adm_ok["in_tok"], "adm_in"))
    rec.update(q(rej["in_tok"], "rej_in"))
    # Input length needs no oracle: the prompt is tokenised by the client
    # before the request is sent, so a rejected request still carries its true
    # input_tokens. This makes "is the admitted set shorter than the offered
    # set" directly measurable on the prefill dimension.
    # A rejection sets is_error, so the blanket truncation filter would remove
    # every rejected row and the "offered" input distribution would collapse
    # back to the admitted one. A rejection does not truncate a prompt -- the
    # prompt was complete and was refused -- so rejected rows are kept here and
    # only the genuinely truncated, errored or timed-out rows are dropped.
    off_in_rows = off[~(off["len_bad"] & ~off["rejected"])]
    rec["n_in_excluded"] = len(off) - len(off_in_rows)
    rec.update(q(off_in_rows["in_tok"], "off_in"))
    rec["adm_over_off_in"] = (rec["adm_in_mean"] / rec["off_in_mean"]
                              if rec["off_in_mean"] else np.nan)
    for c in CLASSES:
        rec.update(q(adm_ok[adm_ok["class"] == c]["out_tok"], f"adm_out_{c}"))
        rec.update(q(adm_ok[adm_ok["class"] == c]["in_tok"], f"adm_in_{c}"))
        rec.update(q(rej[rej["class"] == c]["in_tok"], f"rej_in_{c}"))

    # oracle lengths: the only way to compare admitted against offered in tokens
    rec.update(q(off["oracle_out"], "orc_off_out"))
    rec.update(q(adm["oracle_out"], "orc_adm_out"))
    rec.update(q(rej["oracle_out"], "orc_rej_out"))
    rec.update(q(off["oracle_in"], "orc_off_in"))
    rec.update(q(adm["oracle_in"], "orc_adm_in"))
    rec.update(q(rej["oracle_in"], "orc_rej_in"))
    for c in CLASSES:
        rec.update(q(adm[adm["class"] == c]["oracle_out"], f"orc_adm_out_{c}"))
        rec.update(q(rej[rej["class"] == c]["oracle_out"], f"orc_rej_out_{c}"))

    # work: measured on the admitted side, oracle on the offered side
    rec["adm_out_tok_ps"] = float(adm_ok["out_tok"].sum()) / w
    rec["adm_io_tok_ps"] = float((adm_ok["out_tok"] + adm_ok["in_tok"]).sum()) / w
    rec["orc_off_out_ps"] = float(off["oracle_out"].fillna(0).sum()) / w
    rec["orc_adm_out_ps"] = float(adm["oracle_out"].fillna(0).sum()) / w
    rec["orc_off_io_ps"] = float((off["oracle_out"] + off["oracle_in"]).fillna(0).sum()) / w
    rec["orc_adm_io_ps"] = float((adm["oracle_out"] + adm["oracle_in"]).fillna(0).sum()) / w
    rec["work_admit_frac_out"] = (100.0 * rec["orc_adm_out_ps"] / rec["orc_off_out_ps"]
                                  if rec["orc_off_out_ps"] > 0 else np.nan)
    rec["work_admit_frac_io"] = (100.0 * rec["orc_adm_io_ps"] / rec["orc_off_io_ps"]
                                 if rec["orc_off_io_ps"] > 0 else np.nan)
    rec["req_admit_frac"] = 100.0 * len(adm) / len(off)
    # the direct statement of the accusation: admitted work share divided by
    # admitted request share. Below 1 means the admitted set is systematically
    # shorter than the offered set.
    rec["cheapness_ratio"] = (rec["work_admit_frac_out"] / rec["req_admit_frac"]
                              if rec["req_admit_frac"] > 0 else np.nan)

    # Task 2: requests and tokens that met their SLO, per second
    met = rows[(~rows["violate_offered"]) & (~rows["cutoff"])]
    met_ok = met[~met["len_bad"]]
    rec["met_rps"] = len(met) / w
    rec["met_tok_ps"] = float(met_ok["out_tok"].sum()) / w
    rec["met_io_tok_ps"] = float((met_ok["out_tok"] + met_ok["in_tok"]).sum()) / w
    for c in CLASSES:
        rec[f"met_rps_{c}"] = len(met[met["class"] == c]) / w
        rec[f"met_tok_ps_{c}"] = float(met_ok[met_ok["class"] == c]["out_tok"].sum()) / w

    # Task 3 inputs: per-class attainment under both denominators
    rec["per_req_offered"] = attain(rows, "violate_offered")
    rec["per_req_served"] = attain(rows, "violate_served")
    for c in CLASSES:
        sub = rows[rows["class"] == c]
        rec[f"att_off_{c}"] = attain(sub, "violate_offered")
        rec[f"att_srv_{c}"] = attain(sub, "violate_served")
        srv = sub[(~sub["cutoff"]) & (~sub["rejected"])]
        rec[f"srv_n_{c}"] = len(srv)
    return rec


def agg(df, col):
    """mean, min, max over the repeats of one (arm, rate) cell."""
    s = pd.to_numeric(df[col], errors="coerce").dropna()
    if s.empty:
        return np.nan, np.nan, np.nan
    return float(s.mean()), float(s.min()), float(s.max())


def fmt(m, lo, hi, p=1):
    if np.isnan(m):
        return "-"
    if abs(hi - lo) < 10 ** (-p) / 2:
        return f"{m:.{p}f}"
    return f"{m:.{p}f} [{lo:.{p}f}..{hi:.{p}f}]"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pinned", default="paper_experiment/static_sweep_2026-08")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/tail_2026-08-16")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    man = pd.read_csv(os.path.join(a.pinned, "manifest.tsv"), sep="\t")
    data_dir = os.path.join(a.pinned, "data")

    per_run, all_rows = {}, []
    for _, m in man.iterrows():
        d = os.path.join(data_dir, m["run"])
        rows = load_rows(d)
        if rows is None or rows.empty:
            print(f"  skip {m['run']}: no usable rows")
            continue
        rows["arm"] = m["arm"]
        rows["rate"] = float(m["req_per_s"])
        per_run[m["run"]] = rows
        all_rows.append(rows[["base", "class", "rejected", "len_bad",
                              "in_tok", "out_tok"]])
    pool = pd.concat(all_rows, ignore_index=True)
    oracle = build_oracle(pool)
    print(f"oracle table: {len(oracle)} prompts, "
          f"{int(oracle['oracle_n'].sum())} clean completions pooled")

    recs = []
    for _, m in man.iterrows():
        rows = per_run.get(m["run"])
        if rows is None:
            continue
        recs.append(run_record(rows, oracle, m["run"], m["arm"],
                               float(m["req_per_s"])))
    df = pd.DataFrame(recs)
    df.to_csv(os.path.join(a.out_dir, "tail2026_per_run.csv"), index=False)

    rates = sorted(df["rate"].unique())
    arms = [x for x in ARM_ORDER if x in set(df["arm"])]

    # ---- fixed class weighting: the offered composition, shared by all arms.
    # Taken as the mean offered share over every arm at that rate, so it is one
    # vector per rate and no arm's own admission decisions can move it.
    wts = {}
    for rt in rates:
        sub = df[df["rate"] == rt]
        v = np.array([sub[f"off_share_{c}"].mean() for c in CLASSES])
        wts[rt] = v / v.sum()

    for _, r in df.iterrows():
        for den, key in (("off", "att_off"), ("srv", "att_srv")):
            vals = np.array([r[f"{key}_{c}"] for c in CLASSES], dtype=float)
            ok = ~np.isnan(vals)
            w = wts[r["rate"]].copy()
            df.loc[r.name, f"fixedw_{den}"] = float((vals[ok] * w[ok]).sum() / w[ok].sum())
            # The adversarial weighting. The offered composition is nearly the
            # same for every arm, so weighting by it cannot move an arm's score
            # relative to another's; it only proves that composition was not the
            # lever. Equal class weights are the weighting that actually tests
            # the accusation, because they take the 77% of requests that are
            # chat and give chat one third of the say instead of three quarters.
            df.loc[r.name, f"eqclass_{den}"] = float(vals[ok].mean())
    df.to_csv(os.path.join(a.out_dir, "tail2026_per_run.csv"), index=False)

    out = []
    P = out.append

    def table(title, cols, note=None, p=1):
        P("")
        P(f"**{title}**")
        if note:
            P("")
            P(note)
        P("")
        P("| rate | " + " | ".join(ARM_LABEL[x] for x in arms) + " |")
        P("|---|" + "---|" * len(arms))
        for rt in rates:
            cells = []
            for arm in arms:
                sub = df[(df["arm"] == arm) & (df["rate"] == rt)]
                if sub.empty:
                    cells.append("-")
                    continue
                cells.append(" / ".join(fmt(*agg(sub, c), p=p) for c in cols))
            P(f"| {rt:.0f} | " + " | ".join(cells) + " |")

    P("## Task 1 - what each policy admits")
    table("Rejection rate, % of arrivals in the analysis window", ["reject_pct"])
    table("Rejection rate per class, % (chat / deepresearch / swe)",
          [f"rej_pct_{c}" for c in CLASSES])
    table("Offered class composition, % of arrivals (chat / dr / swe)",
          [f"off_share_{c}" for c in CLASSES])
    table("Admitted class composition, % of admitted (chat / dr / swe)",
          [f"adm_share_{c}" for c in CLASSES])
    table("Admitted output tokens, mean / p50 / p90 (measured, clean rows only)",
          ["adm_out_mean", "adm_out_p50", "adm_out_p90"], p=0)
    table("Admitted input tokens, mean / p50 / p90 (measured, clean rows only)",
          ["adm_in_mean", "adm_in_p50", "adm_in_p90"], p=0)
    table("Rejected input tokens, mean / p50 / p90 (measured directly)",
          ["rej_in_mean", "rej_in_p50", "rej_in_p90"], p=0)
    table("Offered input tokens, mean / p50 / p90 (measured, includes rejected)",
          ["off_in_mean", "off_in_p50", "off_in_p90"], p=0)
    table("Admitted mean input length divided by offered mean input length",
          note="Below 1 means the policy kept the shorter prompts and rejected "
               "the longer ones. This needs no oracle: the client tokenises the "
               "prompt before sending, so a rejected request carries its true "
               "input_tokens.",
          cols=["adm_over_off_in"], p=3)
    table("Oracle output length, offered / admitted / rejected, mean tokens",
          ["orc_off_out_mean", "orc_adm_out_mean", "orc_rej_out_mean"], p=0)
    table("Oracle output length, offered / admitted / rejected, p90 tokens",
          ["orc_off_out_p90", "orc_adm_out_p90", "orc_rej_out_p90"], p=0)
    table("Admitted output tokens per second, measured", ["adm_out_tok_ps"], p=0)
    table("Admitted input+output tokens per second, measured", ["adm_io_tok_ps"], p=0)
    table("Share of offered REQUESTS admitted, % / share of offered output-token "
          "WORK admitted, % / ratio of the two",
          ["req_admit_frac", "work_admit_frac_out", "cheapness_ratio"], p=2)
    table("Oracle coverage, % of arrivals / % of rejected arrivals with a length",
          ["oracle_cov_offered", "oracle_cov_rejected"], p=1)
    table("Rows excluded from every length statistic because the recorded "
          "output length is a lower bound / of which run-boundary cutoffs",
          note="Rejected rows are not counted here even though a rejection also "
               "sets is_error: a rejection does not truncate anything, it "
               "produces no output at all, and those rows leave the length "
               "statistics through the admitted/rejected split instead.",
          cols=["n_len_excluded", "n_cutoff"], p=0)

    P("")
    P("## Task 2 - requests or tokens")
    table("SLO-meeting requests per second (offered denominator)", ["met_rps"], p=2)
    table("Output tokens of SLO-meeting requests per second", ["met_tok_ps"], p=0)
    table("SLO-meeting requests/s per class (chat / dr / swe)",
          [f"met_rps_{c}" for c in CLASSES], p=2)
    table("SLO-meeting output tokens/s per class (chat / dr / swe)",
          [f"met_tok_ps_{c}" for c in CLASSES], p=0)

    P("")
    P("## Task 3 - composition-neutral score")
    table("Per-class attainment, offered denominator, % (chat / dr / swe)",
          [f"att_off_{c}" for c in CLASSES])
    table("Per-class attainment, admitted denominator, % (chat / dr / swe)",
          [f"att_srv_{c}" for c in CLASSES])
    table("Per-request / offered-composition fixed weight / equal class weight, "
          "offered denominator, %",
          ["per_req_offered", "fixedw_off", "eqclass_off"])
    table("Per-request / offered-composition fixed weight / equal class weight, "
          "admitted denominator, %",
          ["per_req_served", "fixedw_srv", "eqclass_srv"])

    def advantage(title, cols, note):
        P("")
        P(f"**{title}**")
        P("")
        P(note)
        P("")
        P("| rate | " + " | ".join(ARM_LABEL[x] for x in arms if x != "fspfx") + " |")
        P("|---|" + "---|" * (len(arms) - 1))
        for rt in rates:
            us = df[(df["arm"] == "fspfx") & (df["rate"] == rt)]
            cells = []
            for arm in arms:
                if arm == "fspfx":
                    continue
                th = df[(df["arm"] == arm) & (df["rate"] == rt)]
                if us.empty or th.empty:
                    cells.append("-")
                    continue
                cells.append(" / ".join(
                    f"{us[c].mean() - th[c].mean():+.1f}" for c in cols))
            P(f"| {rt:.0f} | " + " | ".join(cells) + " |")

    advantage("FluidServe advantage in attainment points, offered denominator",
              ["per_req_offered", "fixedw_off", "eqclass_off"],
              "per-request / offered-composition fixed weight / equal class weight")
    advantage("FluidServe advantage in attainment points, admitted denominator",
              ["per_req_served", "fixedw_srv", "eqclass_srv"],
              "per-request / offered-composition fixed weight / equal class weight. "
              "This denominator removes rejections from the population, so an arm "
              "that rejects more is scored on a smaller and easier set; it is "
              "reported next to the rejection rate, never alone.")
    advantage("FluidServe advantage in SLO-meeting requests per second",
              ["met_rps"] + [f"met_rps_{c}" for c in CLASSES],
              "total / chat / deepresearch / swe")
    advantage("FluidServe advantage in SLO-meeting output tokens per second",
              ["met_tok_ps"] + [f"met_tok_ps_{c}" for c in CLASSES],
              "total / chat / deepresearch / swe. The class columns sum to the "
              "total column, so this is the decomposition that says which class "
              "the token advantage comes from.")

    txt = "\n".join(out)
    with open(os.path.join(a.out_dir, "tail2026_tables.md"), "w") as fh:
        fh.write(txt + "\n")
    print(txt)
    print(f"\nwrote {a.out_dir}/tail2026_tables.md and tail2026_per_run.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
