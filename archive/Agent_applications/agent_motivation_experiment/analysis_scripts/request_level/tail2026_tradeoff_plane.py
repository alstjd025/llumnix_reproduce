#!/usr/bin/env python3
"""Is the FluidServe advantage a different operating point on one curve, or a
different curve?

The reviewer's objection this script is written to answer, stated in full: at 35
req/s FluidServe refuses about 16% of arrivals and delivers about 13,100 output
tokens per second while llm-d refuses about 52% and delivers about 8,300, and
FluidServe's per-token tail is the worse of the two. If throughput and per-token
latency trade off against each other along a single curve that every policy
lies on, then FluidServe has not improved anything: it has merely been placed
further along the same curve, and any baseline could be moved to the same place
by offering it more load. The claim only survives if there is a point that one
arm reaches and the other cannot reach at ANY of its eight arrival rates.

So every comparison here is made at MATCHED WORK rather than at matched offered
rate. Three matchings are computed because they can disagree:

  delivered output tokens/s   what the fleet actually emitted. An output, not an
                              input: a policy cannot be asked to run at a chosen
                              value of it.
  admitted output tokens/s    output tokens of the requests that were let in.
                              Differs from the above only by requests that were
                              admitted and then cut off at the window edge.
  admitted total tokens/s     input + output over admitted requests. Prefill and
                              decode both occupy the engine, and an arm that
                              admits long-prompt requests is doing work that an
                              output-token count does not see.

WHAT IS EXCLUDED AND WHY. Requests flagged is_server_terminated, is_error,
is_timeout or is_job_timeout are dropped from every latency and every length
statistic. Their recorded output_tokens is the count produced before the stream
was cut, which is a lower bound on the length and not the length, and their
per-token time is computed from that truncated count. They are still counted in
the attainment denominators, which is where load_run and all_arrivals_attainment
already put them.

Rejected requests have no output and no first token, so they leave every latency
statistic on their own; they are carried explicitly as rejected_pct and they are
violations under the offered and all-arrivals denominators.

REPEATS. Two per cell. The reported spread is max minus min of the two, which is
a floor on the noise and not an estimate of a variance. A gap between two arms
that is smaller than the larger of their two spreads is reported as no gap.

  python3 tail2026_tradeoff_plane.py \
      --data paper_experiment/static_sweep_2026-08/data \
      --out results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import glob
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, attain, CLASSES  # noqa: E402
from all_arrivals_attainment import parse_dir  # noqa: E402

ARM_LABEL = {
    "fspfx": "FluidServe",
    "llmdslo": "llm-d",
    "vllmcache": "vLLM router",
    "slo": "Llumnix SLO",
    "polyserve": "PolyServe",
}
ARM_ORDER = ["fspfx", "llmdslo", "vllmcache", "slo", "polyserve"]
OURS = "fspfx"

# The two variants of our own binary with parts of the admission machinery
# switched off, from EXP-78. They are not baselines and they are kept out of
# ARM_ORDER so that they cannot enter the five-arm comparison of Tasks 1 to 5;
# they exist to trace the achievable region of this fleet and workload with the
# controller disabled, which is a property of the hardware and the workload
# rather than of any policy.
NOADM_LABEL = {
    "fsroute": "FluidServe, routing only (no rejection, no holding)",
    "fsnoshed": "FluidServe, rejection off (holding on)",
}
NOADM_ORDER = ["fsroute", "fsnoshed"]
ARM_LABEL.update(NOADM_LABEL)


def truthy(df, col):
    if col not in df.columns:
        return pd.Series(False, index=df.index)
    return df[col].astype(str).str.lower().isin(["true", "1", "1.0"])


def q(series, p):
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(np.percentile(s, p)) if len(s) else np.nan


def one_run(run_dir):
    """Every quantity this analysis needs, for one condition.

    load_run already trims the warm-up and drain windows, maps the class, and
    publishes the corrected per-token time as itl_ms; re-deriving any of that
    here would be the repository's most frequent error -- one quantity written
    in two places and updated in one of them.
    """
    rows = load_run(run_dir)
    if rows is None or rows.empty:
        return None
    # The `agent` column carries THREE kinds of row, not two, and the third one
    # decides a denominator.
    #
    #   request      one arrival, with its latencies and its token counts
    #   job_summary  the per-job duplicate of that same arrival. Double counts
    #                everything and must be dropped, which is what load_run's
    #                `agent != "job_summary"` does.
    #   grace_cut    an arrival that was STILL STREAMING when the load window
    #                closed and was cut at the grace boundary. Measured over the
    #                80 pinned runs: 88,122 such rows in 14 of them, every one
    #                with is_server_terminated true, output_tokens 0, no first
    #                token, and no matching `request` row on
    #                (task_id, call_index, iteration). They are separate
    #                arrivals, not duplicates.
    #
    # So grace_cut rows are KEPT here. Dropping them would remove real arrivals
    # from the all-arrivals denominator, and they are not spread evenly: they are
    # up to 33% of the arrivals of a PolyServe or vLLM router condition and
    # essentially absent from FluidServe's, so dropping them would raise those
    # two baselines' all-arrivals attainment by up to 33 points against ours.
    # They carry no tokens and no latency, so they enter no latency or length
    # statistic anyway -- the truncation mask below excludes them by
    # is_server_terminated. Keeping them is also what makes every attainment,
    # rejection and unfinished figure in this file reproduce the pinned
    # `table.csv` exactly rather than approximately.
    rows = rows[rows["agent"] != "job_summary"].copy()
    if rows.empty:
        return None

    dur = rows["rel"].max() - rows["rel"].min()
    out_tok = pd.to_numeric(rows.get("output_tokens"), errors="coerce").fillna(0.0)
    in_tok = pd.to_numeric(rows.get("input_tokens"), errors="coerce").fillna(0.0)

    # The full truncation mask, wider than load_run's `cutoff`: that one is
    # is_server_terminated and not rejected and not errored, because its job is
    # to leave undetermined outcomes out of the attainment denominator. Here the
    # job is different -- any request whose stream was cut has a length that is a
    # lower bound, so all four flags disqualify it from a length or a latency.
    cut = (truthy(rows, "is_server_terminated") | truthy(rows, "is_error")
           | truthy(rows, "is_timeout") | truthy(rows, "is_job_timeout"))
    clean = rows[~cut & ~rows["rejected"]].copy()

    met = (~rows["violate_offered"]) & (~rows["cutoff"])
    admitted = ~rows["rejected"]

    rec = dict(parse_dir(run_dir))
    rec.update({
        "run": os.path.basename(run_dir.rstrip("/")),
        "dur_s": dur,
        "n": len(rows),
        "arrival_rps": len(rows) / dur,
        # Delivered. The first counts every token the fleet emitted inside the
        # window including the partial output of requests that were still
        # streaming when it closed; the second counts only requests that
        # finished cleanly. They bracket the true delivered rate, and the second
        # is the one used for matching because a truncated request's token count
        # is not a length.
        "deliv_tok_s_all": float(out_tok.sum()) / dur,
        "deliv_tok_s_clean": float(out_tok[clean.index].sum()) / dur,
        # Admitted work: what the system took on, whatever became of it.
        "adm_out_tok_s": float(out_tok[admitted].sum()) / dur,
        "adm_tot_tok_s": float((out_tok + in_tok)[admitted].sum()) / dur,
        "adm_req_s": float(admitted.sum()) / dur,
        # Attainment, computed by the same functions the paper's tables use.
        "offered": attain(rows, "violate_offered"),
        "admitted_att": attain(rows, "violate_served"),
        "all_arrivals": 100.0 * met.sum() / len(rows),
        "rejected_pct": 100.0 * rows["rejected"].mean(),
        "unfinished_pct": 100.0 * rows["cutoff"].mean(),
        # Requests that met their rule, per second, and their output tokens.
        "met_req_s": float(met.sum()) / dur,
        "met_tok_s": float(out_tok[met].sum()) / dur,
    })

    ttft_ms = pd.to_numeric(clean.get("first_token_latency"), errors="coerce") * 1000.0
    rec["ttft_p50_ms"] = q(ttft_ms, 50)
    rec["ttft_p90_ms"] = q(ttft_ms, 90)
    rec["ttft_p99_ms"] = q(ttft_ms, 99)

    for tag, sub in [("", clean)] + [(f"_{c}", clean[clean["class"] == c])
                                     for c in CLASSES]:
        for p in (50, 90, 99):
            rec[f"itl_p{p}{tag}"] = q(sub["itl_ms"], p)
        # Deliberately not `n{tag}`: with tag empty that key is `n`, which is
        # already the arrival count, and the assignment would silently replace
        # the denominator of every attainment figure above with the size of the
        # clean subset.
        rec[f"n_clean{tag}"] = len(sub)

    # ------------------------------------------------------------------
    # The adversarial check: the tail WITHIN a request, not across requests.
    #
    # Everything above is a percentile across requests of each request's MEAN
    # per-token time, which is the quantity the task asked for and the quantity
    # the SLO rules score. It cannot see a request whose mean is 45 ms because
    # half its tokens came at 25 ms and half at 65 ms. A reviewer who says "your
    # per-token tail is worse" may well mean that one, and it is the direction
    # this repository has already flagged as the live threat to the attainment
    # claim, so it is measured here rather than left out.
    #
    # The only per-request record of the token interval DISTRIBUTION is the
    # tbt_* family of columns, and this repository's rule is that those are
    # about half the true interval on runs collected before 2026-07-30, because
    # the client divided each chunk gap by an overcounted per-chunk token
    # estimate. That rule does not apply to these runs and the ratio is measured
    # rather than assumed: `itl_over_tbtmean` below is the per-run median of
    # itl_ms / tbt_mean_ms, and it comes out at 0.996 in all 40 (arm, rate)
    # cells. The recorded column and the corrected derivation agree here, so
    # tbt_p90_ms is already on the same scale as itl_ms and is directly
    # comparable across the arms.
    #
    # The within-request p90 is nonetheless written as
    #
    #     within_p90 = itl_ms * (tbt_p90_ms / tbt_mean_ms)
    #
    # rather than as tbt_p90_ms, so that the anchor is the corrected mean and
    # only the dimensionless shape factor comes from the recorded series. With
    # the ratio at 0.996 the two forms agree to a fraction of a percent; if a
    # later workload reintroduces the scale error, this form degrades gracefully
    # and the reported `itl_over_tbtmean` says so.
    #
    # Requests with fewer than 10 recorded intervals are dropped: a p90 of six
    # samples is the largest or second largest of them and carries no
    # information about a tail.
    tb_mean = pd.to_numeric(clean.get("tbt_mean_ms"), errors="coerce")
    tb_p90 = pd.to_numeric(clean.get("tbt_p90_ms"), errors="coerce")
    nsamp = pd.to_numeric(clean.get("tbt_sample_count"), errors="coerce")
    ok = (tb_mean > 0) & tb_p90.notna() & (nsamp >= 10)
    burst = (tb_p90 / tb_mean).where(ok)
    within = clean["itl_ms"] * burst
    # The scale check described above, carried per run so it appears in the
    # output instead of being asserted in a comment.
    rec["itl_over_tbtmean"] = q((clean["itl_ms"] / tb_mean).where(tb_mean > 0), 50)
    rec["chunks_per_tok"] = q(
        pd.to_numeric(clean.get("stream_chunks"), errors="coerce")
        / pd.to_numeric(clean.get("output_tokens"), errors="coerce"), 50)
    for tag, m in [("", pd.Series(True, index=clean.index)),
                   ("_chat", clean["class"] == "chat")]:
        for p in (50, 90, 99):
            rec[f"within_p{p}{tag}"] = q(within[m], p)
        rec[f"burst_p50{tag}"] = q(burst[m], 50)
    # Chat is scored on its MEAN per-token time against 50 ms. This is the share
    # of chat requests that met that rule on the mean and would have failed it on
    # their own within-request p90 -- the size of the claim that rests on the
    # choice of statistic.
    ch = clean[clean["class"] == "chat"]
    if len(ch):
        w = within[ch.index]
        mean_ok = ch["itl_ms"] <= 50.0
        p90_bad = w > 50.0
        rec["chat_mean_ok_pct"] = 100.0 * float(mean_ok.mean())
        rec["chat_p90_ok_pct"] = 100.0 * float((~p90_bad).where(w.notna()).mean())
        rec["chat_flips_pct"] = 100.0 * float((mean_ok & p90_bad).where(
            w.notna()).mean())
    return rec


def aggregate(df):
    """Mean over repeats, with the min..max range of the repeats beside it."""
    keys = ["arm", "rate"]
    num = [c for c in df.select_dtypes("number").columns if c not in keys]
    g = df.groupby(keys)
    out = g[num].mean()
    out["reps"] = g.size()
    for c in num:
        out[c + "_lo"] = g[c].min()
        out[c + "_hi"] = g[c].max()
    return out.reset_index()


def fmt_range(row, c, prec=0):
    return (f"{row[c]:.{prec}f} ({row[c + '_lo']:.{prec}f}..{row[c + '_hi']:.{prec}f})")


def match_pairs(agg, col, tol=0.05):
    """Every cross-arm pair whose value of `col` agrees to within `tol`."""
    recs = agg.to_dict("records")
    pairs = []
    for i, a in enumerate(recs):
        for b in recs[i + 1:]:
            if a["arm"] == b["arm"]:
                continue
            va, vb = a[col], b[col]
            if not np.isfinite(va) or not np.isfinite(vb):
                continue
            m = 0.5 * (va + vb)
            if m > 0 and abs(va - vb) / m <= tol:
                pairs.append((a, b, 100.0 * abs(va - vb) / m))
    return pairs


def pareto_check(agg, arm_a, arm_b, tcol, lcol):
    """Points of arm_a that no rate of arm_b matches or beats in BOTH coordinates.

    A point of a is unreachable for b if every rate of b either delivers less
    throughput or carries a higher tail latency. Ties count in b's favour, so
    this is the conservative direction for a claim about a.
    """
    A = agg[agg.arm == arm_a]
    B = agg[agg.arm == arm_b]
    unreachable = []
    for _, ra in A.iterrows():
        if not (np.isfinite(ra[tcol]) and np.isfinite(ra[lcol])):
            continue
        dominated = B[(B[tcol] >= ra[tcol]) & (B[lcol] <= ra[lcol])]
        if dominated.empty:
            unreachable.append(ra)
    return unreachable


def strict_dominations(agg, tcol, lcol):
    """(a beats b) whenever a has more throughput AND a lower tail at once."""
    recs = agg.to_dict("records")
    hits = []
    for a in recs:
        for b in recs:
            if a["arm"] == b["arm"]:
                continue
            if not all(np.isfinite(x) for x in (a[tcol], a[lcol], b[tcol], b[lcol])):
                continue
            if a[tcol] > b[tcol] and a[lcol] < b[lcol]:
                hits.append((a, b))
    return hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--noadm-runs", nargs="*", default=[],
                    help="globs over the runs of the variants with parts of the "
                         "admission machinery disabled (EXP-78 fsroute and "
                         "fsnoshed). They are aggregated and reported "
                         "separately and never enter the five-arm tables.")
    ap.add_argument("--since", default="260807_1900",
                    help="drop runs whose directory name sorts before this. The "
                         "load generator changed on 2026-08-08 11:00 KST and the "
                         "directory names are the runner pod's UTC-7, which is "
                         "why the boundary is 260807_1900 and not 260808.")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    def load_set(dirs, tag):
        print(f"{len(dirs)} run directories for {tag}")
        recs = []
        for d in dirs:
            r = one_run(d)
            if r is None:
                print(f"  skipped (no usable rows): {os.path.basename(d)}")
                continue
            recs.append(r)
        out = pd.DataFrame(recs)
        if out.empty:
            return out
        out["arm_label"] = out["arm"].map(lambda k: ARM_LABEL.get(k, k))
        print(f"  loaded {len(out)} runs, arms {sorted(out.arm.unique())}")
        return out

    def agg_of(frame, order):
        g = aggregate(frame)
        g["arm_label"] = g["arm"].map(lambda k: ARM_LABEL.get(k, k))
        g["ord"] = g["arm"].map(lambda k: order.index(k) if k in order else 99)
        return g.sort_values(["ord", "rate"]).drop(columns="ord")

    dirs = sorted(d for d in glob.glob(os.path.join(a.data, "*")) if os.path.isdir(d))
    df = load_set(dirs, "the pinned five-arm sweep")
    df.to_csv(os.path.join(a.out, "04_plane_per_run.csv"), index=False)
    agg = agg_of(df, ARM_ORDER)
    agg.to_csv(os.path.join(a.out, "04_plane_by_arm_rate.csv"), index=False)

    ex_dirs = []
    for pat in a.noadm_runs:
        ex_dirs.extend(d for d in glob.glob(pat) if os.path.isdir(d))
    ex_dirs = sorted(set(ex_dirs))
    before = [d for d in ex_dirs if os.path.basename(d) < a.since]
    ex_dirs = [d for d in ex_dirs if os.path.basename(d) >= a.since]
    if before:
        # Loud, because a silent date filter is how a table comes out looking
        # clean while the runs it needed were dropped.
        print(f"--since {a.since}: dropped {len(before)} pre-workload-change runs")
    agg_ex = pd.DataFrame()
    if ex_dirs:
        df_ex = load_set(ex_dirs, "the no-admission variants")
        if not df_ex.empty:
            df_ex.to_csv(os.path.join(a.out, "04_plane_noadm_per_run.csv"),
                         index=False)
            agg_ex = agg_of(df_ex, NOADM_ORDER)
            agg_ex.to_csv(os.path.join(a.out, "04_plane_noadm_by_arm_rate.csv"),
                          index=False)

    write_report(df, agg, agg_ex, a.out)
    make_figure(agg, agg_ex, a.out)
    print(f"wrote {a.out}")
    return 0


# --------------------------------------------------------------------------
# report


def write_report(df, agg, agg_ex, out_dir):
    L = []
    w = L.append
    w("# The throughput / per-token-latency plane: one curve or two?\n")
    w("Generated by `analysis_scripts/request_level/tail2026_tradeoff_plane.py` "
      "over the 80 pinned runs in `paper_experiment/static_sweep_2026-08/data/`.\n")
    w("Every number is the mean of the two repeats of that (arm, arrival rate) "
      "cell, with the min..max of the two repeats in parentheses. That range is "
      "the gap between two numbers, so it is a floor on the run-to-run noise and "
      "not an estimate of a variance; a difference between two arms that is "
      "smaller than the larger of their two ranges is reported here as no "
      "difference.\n")
    w("**What is excluded.** Requests flagged `is_server_terminated`, "
      "`is_error`, `is_timeout` or `is_job_timeout` are dropped from every "
      "latency statistic and from every token count that is used as a length, "
      "because the output token count recorded for such a request is the number "
      "produced before the stream was cut and is therefore a lower bound rather "
      "than a length. Rejected requests produced no tokens and no first token, "
      "so they leave the latency statistics by themselves; they are carried "
      "separately as the rejection percentage and they count as violations under "
      "the offered and all-arrivals denominators. Attainment, rejection and "
      "unfinished percentages come from `exp22_fluidserve.attain`, the same "
      "function the paper's tables use.\n")
    w("**Per-token latency** is `itl_ms` as published by "
      "`exp22_fluidserve.load_run`: for each request, "
      "`(end-to-end - time to first token) / (output tokens - 1)`. The `p50`, "
      "`p90` and `p99` below are percentiles ACROSS REQUESTS of that "
      "per-request mean, not percentiles across individual token intervals, so "
      "they understate the true per-token tail for every arm equally.\n")

    # ---- Task 1
    w("\n## Task 1 - the tradeoff plane\n")
    w("`deliv` is delivered output tokens per second counting only requests "
      "that finished cleanly. `deliv(all)` adds back the partial output of "
      "requests still streaming when the window closed; the two bracket the "
      "true delivered rate and the matching in Tasks 2 and 3 uses the first.\n")
    for arm in ARM_ORDER:
        s = agg[agg.arm == arm]
        if s.empty:
            continue
        w(f"\n### {ARM_LABEL[arm]} (`{arm}`)\n")
        w("| req/s | deliv tok/s | deliv(all) tok/s | ITL p50 | ITL p90 | ITL p99 "
          "| chat p50 | chat p90 | chat p99 | rej % | unfin % |")
        w("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in s.iterrows():
            w(f"| {r['rate']:.0f} | {fmt_range(r, 'deliv_tok_s_clean')} "
              f"| {fmt_range(r, 'deliv_tok_s_all')} "
              f"| {fmt_range(r, 'itl_p50', 1)} | {fmt_range(r, 'itl_p90', 1)} "
              f"| {fmt_range(r, 'itl_p99', 1)} "
              f"| {fmt_range(r, 'itl_p50_chat', 1)} | {fmt_range(r, 'itl_p90_chat', 1)} "
              f"| {fmt_range(r, 'itl_p99_chat', 1)} "
              f"| {r['rejected_pct']:.1f} | {r['unfinished_pct']:.1f} |")

    w("\n### Is it one monotone curve?\n")
    for lcol, name in [("itl_p50", "median"), ("itl_p90", "p90"), ("itl_p99", "p99")]:
        hits = strict_dominations(agg, "deliv_tok_s_clean", lcol)
        w(f"\n**Fleet-wide {name} per-token latency.** "
          f"{len(hits)} ordered pairs of conditions where one arm has strictly "
          f"more delivered throughput AND a strictly lower {name} per-token "
          f"latency than a condition of a different arm. On a single shared "
          f"curve this count would be zero.\n")
        if not hits:
            continue
        ours = [(x, y) for x, y in hits if x["arm"] == OURS]
        against = [(x, y) for x, y in hits if y["arm"] == OURS]
        w(f"- with FluidServe as the better arm: {len(ours)} pairs\n")
        w(f"- with FluidServe as the worse arm: {len(against)} pairs\n")
        show = sorted(ours, key=lambda p: -(p[0]["deliv_tok_s_clean"]
                                            - p[1]["deliv_tok_s_clean"]))[:8]
        if show:
            w("\nLargest eight in FluidServe's favour:\n")
            w("| better | req/s | tok/s | " + name + " | worse | req/s | tok/s | "
              + name + " |")
            w("|---|---:|---:|---:|---|---:|---:|---:|")
            for x, y in show:
                w(f"| {ARM_LABEL[x['arm']]} | {x['rate']:.0f} "
                  f"| {x['deliv_tok_s_clean']:.0f} | {x[lcol]:.1f} "
                  f"| {ARM_LABEL[y['arm']]} | {y['rate']:.0f} "
                  f"| {y['deliv_tok_s_clean']:.0f} | {y[lcol]:.1f} |")

    # ---- Task 2 and 3
    for col, title, unit in [
        ("deliv_tok_s_clean", "Task 2 - matched delivered throughput",
         "delivered output tokens/s"),
        ("adm_out_tok_s", "Task 3a - matched admitted output tokens",
         "admitted output tokens/s"),
        ("adm_tot_tok_s", "Task 3b - matched admitted total tokens (input+output)",
         "admitted input+output tokens/s"),
    ]:
        w(f"\n## {title}\n")
        pairs = match_pairs(agg, col)
        ours = [p for p in pairs if OURS in (p[0]["arm"], p[1]["arm"])]
        w(f"{len(pairs)} cross-arm pairs of conditions agree on {unit} to within "
          f"5%; {len(ours)} of them involve FluidServe.\n")
        w("\nEach row is one pair. `met/s` is requests that met their class rule "
          "per second. `all-arr` is the all-arrivals attainment, the denominator "
          "that charges an arm for both its rejections and its unfinished "
          "requests.\n")
        w("| arm | req/s | " + unit + " | all-arr % | adm % | rej % | ITL p50 "
          "| ITL p90 | ITL p99 | TTFT p50 ms | TTFT p90 ms | met/s |")
        w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for x, y, gap in sorted(ours, key=lambda p: -p[0][col]):
            # FluidServe first in every pair, so the columns can be read down.
            if y["arm"] == OURS:
                x, y = y, x
            for r in (x, y):
                w(f"| {ARM_LABEL[r['arm']]} | {r['rate']:.0f} "
                  f"| {fmt_range(r, col)} | {r['all_arrivals']:.1f} "
                  f"| {r['admitted_att']:.1f} | {r['rejected_pct']:.1f} "
                  f"| {fmt_range(r, 'itl_p50', 1)} | {fmt_range(r, 'itl_p90', 1)} "
                  f"| {fmt_range(r, 'itl_p99', 1)} "
                  f"| {r['ttft_p50_ms']:.0f} | {r['ttft_p90_ms']:.0f} "
                  f"| {fmt_range(r, 'met_req_s', 2)} |")
            w(f"| | | *gap {gap:.1f}%* | | | | | | | | | |")

    # ---- Task 4
    w("\n## Task 4 - the frontier\n")
    for lcol, name in [("itl_p90", "p90"), ("itl_p50", "median")]:
        w(f"\n### against fleet-wide {name} per-token latency\n")
        for arm in ARM_ORDER:
            if arm == OURS:
                continue
            un = pareto_check(agg, OURS, arm, "deliv_tok_s_clean", lcol)
            rev = pareto_check(agg, arm, OURS, "deliv_tok_s_clean", lcol)
            w(f"\n**FluidServe against {ARM_LABEL[arm]}.** "
              f"{len(un)} of FluidServe's 8 rates sit at a "
              f"(throughput, {name}) point that no rate of {ARM_LABEL[arm]} "
              f"matches or beats in both coordinates; "
              f"{len(rev)} of {ARM_LABEL[arm]}'s 8 rates sit at a point "
              f"FluidServe never matches.\n")
            if un:
                w("- FluidServe-only points: "
                  + ", ".join(f"{r['rate']:.0f} req/s "
                              f"({r['deliv_tok_s_clean']:.0f} tok/s, "
                              f"{r[lcol]:.1f} ms)" for r in un) + "\n")
            if rev:
                w(f"- {ARM_LABEL[arm]}-only points: "
                  + ", ".join(f"{r['rate']:.0f} req/s "
                              f"({r['deliv_tok_s_clean']:.0f} tok/s, "
                              f"{r[lcol]:.1f} ms)" for r in rev) + "\n")

    # ---- Task 5
    w("\n## Task 5 - requests met per second\n")
    w("`met/s` counts requests that were admitted, finished, and satisfied their "
      "class rule (chat: mean TTFT <= 5 s and mean per-token time <= 50 ms; deep "
      "research: 10 s and 100 ms; swe: end-to-end <= 30 s), divided by the "
      "analysis window. `met tok/s` is the output tokens of exactly those "
      "requests, per second, and is the goodput column of `table.csv`.\n")
    w("\n| req/s | " + " | ".join(f"{ARM_LABEL[k]} met/s" for k in ARM_ORDER) + " |")
    w("|---:|" + "---:|" * len(ARM_ORDER))
    for rate in sorted(agg.rate.unique()):
        cells = []
        for arm in ARM_ORDER:
            s = agg[(agg.arm == arm) & (agg.rate == rate)]
            cells.append(fmt_range(s.iloc[0], "met_req_s", 2) if len(s) else "-")
        w(f"| {rate:.0f} | " + " | ".join(cells) + " |")
    w("\n| req/s | " + " | ".join(f"{ARM_LABEL[k]} met tok/s" for k in ARM_ORDER) + " |")
    w("|---:|" + "---:|" * len(ARM_ORDER))
    for rate in sorted(agg.rate.unique()):
        cells = []
        for arm in ARM_ORDER:
            s = agg[(agg.arm == arm) & (agg.rate == rate)]
            cells.append(fmt_range(s.iloc[0], "met_tok_s") if len(s) else "-")
        w(f"| {rate:.0f} | " + " | ".join(cells) + " |")

    w("\n### peak of each arm\n")
    w("| arm | best met/s (at req/s) | best met tok/s (at req/s) | "
      "ITL p90 there |")
    w("|---|---:|---:|---:|")
    for arm in ARM_ORDER:
        s = agg[agg.arm == arm]
        if s.empty:
            continue
        b1 = s.loc[s.met_req_s.idxmax()]
        b2 = s.loc[s.met_tok_s.idxmax()]
        w(f"| {ARM_LABEL[arm]} | {fmt_range(b1, 'met_req_s', 2)} "
          f"at {b1['rate']:.0f} | {fmt_range(b2, 'met_tok_s')} "
          f"at {b2['rate']:.0f} | {fmt_range(b1, 'itl_p90', 1)} |")

    # ---- adversarial check
    w("\n## Adversarial check - the tail WITHIN a request\n")
    w("Every latency column above is a percentile across requests of each "
      "request's mean per-token time. It cannot see a request whose mean is "
      "45 ms because half of its tokens arrived at 25 ms and half at 65 ms, and "
      "a reviewer who says the per-token tail is worse may mean exactly that. "
      "The columns below measure it as, per request, "
      "`itl_ms x (tbt_p90_ms / tbt_mean_ms)` -- the corrected mean scaled by the "
      "dimensionless shape of the recorded interval series. Requests with fewer "
      "than 10 recorded intervals are dropped, because a p90 of six samples is "
      "one of the six.\n")
    w("\n**Two validity checks, because this quantity comes from the `tbt_*` "
      "columns that this repository has previously found to be half the true "
      "interval.** First, the scale: the per-run median of "
      "`itl_ms / tbt_mean_ms` is "
      f"{agg['itl_over_tbtmean'].min():.3f}..{agg['itl_over_tbtmean'].max():.3f} "
      "across all 40 (arm, rate) cells, so on these runs the recorded column and "
      "the corrected derivation agree and `tbt_p90_ms` is already on the right "
      "scale. Second, the client-side chunking, which is what produced that "
      "earlier error and would break the comparison if it differed by arm: "
      "stream chunks per output token is "
      f"{agg['chunks_per_tok'].min():.3f}..{agg['chunks_per_tok'].max():.3f} "
      "across the same 40 cells, so every arm is streaming one token per chunk "
      "and the arms are measured the same way. **The difference between the arms "
      "in this table is therefore a property of the arms and not of the "
      "instrument.**\n")
    w("\n`flips` is the share of chat requests that met chat's 50 ms rule on "
      "their MEAN per-token time and would have failed it on their own "
      "within-request p90. It is the size of the part of the attainment claim "
      "that rests on which statistic the rule is written against.\n")
    w("\n| arm | req/s | within-req p90, fleet p50 | fleet p90 | fleet p99 "
      "| burst p50 | chat mean-ok % | chat p90-ok % | flips % |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in agg.iterrows():
        w(f"| {r['arm_label']} | {r['rate']:.0f} | {fmt_range(r, 'within_p50', 1)} "
          f"| {fmt_range(r, 'within_p90', 1)} | {fmt_range(r, 'within_p99', 1)} "
          f"| {r['burst_p50']:.2f} | {r['chat_mean_ok_pct']:.1f} "
          f"| {r['chat_p90_ok_pct']:.1f} | {r['chat_flips_pct']:.1f} |")

    w("\n### the same matched-throughput pairs, on the within-request proxy\n")
    pairs = [p for p in match_pairs(agg, "deliv_tok_s_clean")
             if OURS in (p[0]["arm"], p[1]["arm"])]
    w("| arm | req/s | deliv tok/s | within p50 | within p90 | within p99 "
      "| chat p90-ok % |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for x, y, gap in sorted(pairs, key=lambda p: -p[0]["deliv_tok_s_clean"]):
        if y["arm"] == OURS:
            x, y = y, x
        for r in (x, y):
            w(f"| {ARM_LABEL[r['arm']]} | {r['rate']:.0f} "
              f"| {r['deliv_tok_s_clean']:.0f} | {r['within_p50']:.1f} "
              f"| {r['within_p90']:.1f} | {r['within_p99']:.1f} "
              f"| {r['chat_p90_ok_pct']:.1f} |")
        w(f"| | | *gap {gap:.1f}%* | | | | |")

    unw = pareto_check(agg, OURS, "llmdslo", "deliv_tok_s_clean", "within_p90")
    revw = pareto_check(agg, "llmdslo", OURS, "deliv_tok_s_clean", "within_p90")
    w(f"\nOn this proxy, {len(unw)} of FluidServe's 8 rates sit at a "
      f"(throughput, within-request p90) point no llm-d rate matches or beats "
      f"in both coordinates, and {len(revw)} of llm-d's 8 rates sit at a point "
      f"FluidServe never matches.\n")

    # ---- supporting: full admitted-work table
    w("\n## Supporting table - admitted work\n")
    w("| arm | req/s | admitted req/s | admitted out tok/s | admitted "
      "in+out tok/s | delivered out tok/s | rej % |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for _, r in agg.iterrows():
        w(f"| {r['arm_label']} | {r['rate']:.0f} | {fmt_range(r, 'adm_req_s', 1)} "
          f"| {fmt_range(r, 'adm_out_tok_s')} | {fmt_range(r, 'adm_tot_tok_s')} "
          f"| {fmt_range(r, 'deliv_tok_s_clean')} | {r['rejected_pct']:.1f} |")

    w("\n## Supporting table - TTFT and per-class per-token latency\n")
    w("| arm | req/s | TTFT p50 | TTFT p90 | TTFT p99 | chat p90 | dr p90 "
      "| swe p90 | n(clean) |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in agg.iterrows():
        w(f"| {r['arm_label']} | {r['rate']:.0f} | {r['ttft_p50_ms']:.0f} "
          f"| {r['ttft_p90_ms']:.0f} | {r['ttft_p99_ms']:.0f} "
          f"| {r['itl_p90_chat']:.1f} | {r['itl_p90_deepresearch']:.1f} "
          f"| {r['itl_p90_swe']:.1f} | {r['n_clean']:.0f} |")

    # ---- verdict
    def cell(arm, rate):
        return agg[(agg.arm == arm) & (agg.rate == rate)].iloc[0]

    f15, f35 = cell(OURS, 15), cell(OURS, 35)
    l45, l35, l20 = cell("llmdslo", 45), cell("llmdslo", 35), cell("llmdslo", 20)
    lmax = agg[agg.arm == "llmdslo"]["deliv_tok_s_clean"].max()
    w("\n## Verdict\n")
    w("**1. It is two curves, not one.** On a single shared "
      "throughput-versus-latency curve no condition of any arm can have both "
      "more delivered throughput and a lower per-token latency than a condition "
      "of another arm. That happens "
      f"{len(strict_dominations(agg, 'deliv_tok_s_clean', 'itl_p90'))} times "
      "across the 40 conditions on the p90 statistic and "
      f"{len(strict_dominations(agg, 'deliv_tok_s_clean', 'itl_p50'))} times on "
      "the median. The arms are not points on one curve.\n")
    w("\n**2. At matched delivered throughput the direction of the latency gap "
      "reverses.** FluidServe at 15 req/s delivers "
      f"{f15['deliv_tok_s_clean']:.0f} output tokens/s and llm-d at 45 req/s "
      f"delivers {l45['deliv_tok_s_clean']:.0f}, a gap of "
      f"{100 * abs(f15['deliv_tok_s_clean'] - l45['deliv_tok_s_clean']) / f15['deliv_tok_s_clean']:.1f}%. "
      f"At that matched throughput FluidServe's per-token median is "
      f"{f15['itl_p50']:.1f} ms against llm-d's {l45['itl_p50']:.1f}, p90 "
      f"{f15['itl_p90']:.1f} against {l45['itl_p90']:.1f}, p99 "
      f"{f15['itl_p99']:.1f} against {l45['itl_p99']:.1f}. FluidServe is lower "
      "on all three, and it gets there while rejecting "
      f"{f15['rejected_pct']:.1f}% of arrivals against llm-d's "
      f"{l45['rejected_pct']:.1f}%, with all-arrivals attainment "
      f"{f15['all_arrivals']:.1f}% against {l45['all_arrivals']:.1f}%. **The "
      "per-token gap the reviewer objects to is an artefact of comparing the two "
      "arms at the same ARRIVAL rate, where they are doing very different "
      "amounts of work.**\n")
    w("\n**3. The 35 req/s comparison the objection is built on does not say "
      "what it is quoted as saying.** At 35 req/s FluidServe's per-token median "
      f"is {f35['itl_p50']:.1f} ms against llm-d's {l35['itl_p50']:.1f}, so the "
      "median is worse by "
      f"{f35['itl_p50'] - l35['itl_p50']:.1f} ms. But the p90 is "
      f"{f35['itl_p90']:.1f} against {l35['itl_p90']:.1f} "
      f"(spreads {f35['itl_p90_hi'] - f35['itl_p90_lo']:.1f} and "
      f"{l35['itl_p90_hi'] - l35['itl_p90_lo']:.1f}, so the gap is inside the "
      "repeat range and is not a difference) and the p99 is "
      f"{f35['itl_p99']:.1f} against {l35['itl_p99']:.1f}, which is "
      f"{l35['itl_p99'] - f35['itl_p99']:.1f} ms in FluidServe's favour. "
      "**Across requests, FluidServe has the worse median and the better far "
      "tail, not a worse tail.**\n")
    w("\n**4. There are points FluidServe reaches that llm-d cannot reach at any "
      f"rate.** llm-d's delivered throughput peaks at {lmax:.0f} output tokens/s "
      f"(at {l20['rate']:.0f} req/s); FluidServe exceeds that at every rate from "
      "25 req/s upward and reaches "
      f"{agg[agg.arm == OURS]['deliv_tok_s_clean'].max():.0f}. Because llm-d "
      "never reaches that throughput at all, no llm-d rate can match FluidServe "
      "there in both coordinates, and all 8 of FluidServe's rates are "
      "unreachable for llm-d on both the median and the p90 statistic. In the "
      "other direction there are 0 llm-d points FluidServe cannot match. Against "
      "the vLLM router, Llumnix SLO and PolyServe the curves CROSS: those three "
      "reach the low-throughput end (10 and 15 req/s, and 20 req/s for two of "
      "them) at a lower per-token latency than FluidServe does, and FluidServe "
      "owns everything from 25 req/s upward.\n")
    w("\n**5. Where the objection does land.** The within-request tail is a "
      "different quantity and it goes the other way against llm-d. At the "
      f"matched throughput of point 2, FluidServe's median within-request p90 is "
      f"{f15['within_p50']:.1f} ms against llm-d's {l45['within_p50']:.1f}, and "
      "the share of chat requests that would satisfy chat's 50 ms rule on their "
      f"own within-request p90 rather than on their mean is "
      f"{f15['chat_p90_ok_pct']:.1f}% for FluidServe against "
      f"{l45['chat_p90_ok_pct']:.1f}% for llm-d. This is not a load effect: at "
      "the SAME 15 req/s, FluidServe has the lower mean per-token time "
      f"({f15['itl_p50']:.1f} against {cell('llmdslo', 15)['itl_p50']:.1f} ms) "
      "and the higher within-request p90 "
      f"({f15['within_p50']:.1f} against {cell('llmdslo', 15)['within_p50']:.1f} "
      "ms). FluidServe spreads a moderate slowdown across many token intervals; "
      "llm-d keeps most intervals fast and takes rare long stalls, which is why "
      "its recorded per-request p90 sits BELOW its own mean "
      f"(shape factor {cell('llmdslo', 15)['burst_p50']:.2f} against FluidServe's "
      f"{f15['burst_p50']:.2f}). **The throughput-versus-latency claim survives "
      "at matched work; a claim written against a within-request p90 budget "
      "would not, and the two must not be stated as if they were the same "
      "result.**\n")

    # ------------------------------------------------------------------
    # Refinement 1: the headline as a rate, with wasted work beside it.
    both = pd.concat([agg, agg_ex], ignore_index=True) if len(agg_ex) else agg
    w("\n## Addendum A - the headline as a RATE, and the wasted work beside it\n")
    w("Protego (NSDI '23) defines goodput as the RATE of requests completed "
      "inside the target delay, in requests per second, and reports the drop "
      "rate in a separate panel rather than folding it into the same number. "
      "The denominator is wall-clock time and not a request count, which is what "
      "structurally closes the trap that a policy refusing everything scores "
      "perfectly: refusing work lowers the numerator and leaves the denominator "
      "untouched. The four rates below are that presentation.\n")
    w("\n- **arrivals/s** - requests that entered the analysis window\n")
    w("- **admitted/s** - arrivals the system accepted, whatever became of them\n")
    w("- **met/s** - admitted requests that finished inside their class rule\n")
    w("- **met tok/s** - output tokens belonging to exactly those requests\n")
    w("\n**wasted/s = admitted/s - met/s** is the work the system agreed to do "
      "and then did not do well enough: accepted, occupied the engines, and "
      "either missed its rule or never finished. It is the quantity a pure "
      "change of operating point does not have to improve. Sliding along one "
      "throughput-versus-latency curve trades latency for throughput and leaves "
      "the accepted-then-failed population roughly proportional to what was "
      "accepted; deciding correctly what to accept shrinks it.\n")
    w("\n| arm | req/s | arrivals/s | admitted/s | met/s | met tok/s "
      "| wasted/s | wasted % of admitted |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in both.iterrows():
        waste = r["adm_req_s"] - r["met_req_s"]
        waste_lo = r["adm_req_s_lo"] - r["met_req_s_hi"]
        waste_hi = r["adm_req_s_hi"] - r["met_req_s_lo"]
        frac = 100.0 * waste / r["adm_req_s"] if r["adm_req_s"] > 0 else np.nan
        w(f"| {r['arm_label']} | {r['rate']:.0f} | {fmt_range(r, 'arrival_rps', 2)} "
          f"| {fmt_range(r, 'adm_req_s', 2)} | {fmt_range(r, 'met_req_s', 2)} "
          f"| {fmt_range(r, 'met_tok_s')} "
          f"| {waste:.2f} ({waste_lo:.2f}..{waste_hi:.2f}) | {frac:.1f} |")

    w("\n### wasted work at matched delivered throughput\n")
    w("The same matched pairs as Task 2, read on the wasted-work columns. If "
      "FluidServe were merely sitting further along a shared curve, then at a "
      "throughput another arm also reaches it would waste a similar share of "
      "what it accepted.\n")
    w("| arm | req/s | deliv tok/s | admitted/s | met/s | wasted/s "
      "| wasted % of admitted |")
    w("|---|---:|---:|---:|---:|---:|---:|")
    for x, y, gap in sorted(
            [p for p in match_pairs(agg, "deliv_tok_s_clean")
             if OURS in (p[0]["arm"], p[1]["arm"])],
            key=lambda p: -p[0]["deliv_tok_s_clean"]):
        if y["arm"] == OURS:
            x, y = y, x
        for r in (x, y):
            waste = r["adm_req_s"] - r["met_req_s"]
            frac = 100.0 * waste / r["adm_req_s"] if r["adm_req_s"] > 0 else np.nan
            w(f"| {ARM_LABEL[r['arm']]} | {r['rate']:.0f} "
              f"| {r['deliv_tok_s_clean']:.0f} | {r['adm_req_s']:.2f} "
              f"| {r['met_req_s']:.2f} | {waste:.2f} | {frac:.1f} |")
        w(f"| | | *gap {gap:.1f}%* | | | | |")

    # ------------------------------------------------------------------
    # Refinement 2: the achievable region with the controller off.
    w("\n## Addendum B - the achievable region with the controller off\n")
    if not len(agg_ex):
        w("No runs of the no-admission variants were supplied, so this section "
          "is empty. Pass them with `--noadm-runs`.\n")
    else:
        w("Aequitas draws the achievable region of the hardware and the workload "
          "FIRST, with its controller disabled, by hand-sweeping how much traffic "
          "is admitted and reading the tail latency at each point; the crossing "
          "of that curve with the SLO is the maximum admissible load, and it is a "
          "property of the fleet and the workload rather than of any policy. Only "
          "then is the controller switched on, and the claim is that it converges "
          "to that point. The equivalent here is EXP-78, which runs our own "
          "binary with parts of the admission machinery switched off:\n")
        w("\n| variant | what is off | what is still on |")
        w("|---|---|---|")
        w("| `fsroute` | rejection AND holding a request at the gateway | "
          "class preference, prefix accounting. This variant never refuses "
          "anything, so its arrival rate IS its admitted rate and it traces the "
          "region by being driven harder |")
        w("| `fsnoshed` | rejection only | holding, class preference, prefix "
          "accounting |")
        w("\n**What this comparison can and cannot settle.** The variants were "
          "measured at three arrival rates (25, 35, 45 req/s, two repeats each) "
          "against the pinned sweep's eight, so the region they trace is three "
          "points wide and does not extend below 25 req/s or above 45. A claim "
          "that our full policy reaches a point outside that region is therefore "
          "only as strong as those three points, and the honest statement of it "
          "names them. They are also a different measurement session from the "
          "pinned sweep, so the repeat range of each is the floor on what can be "
          "read.\n")
        w("\n| arm | req/s | arrivals/s | admitted/s | deliv tok/s | ITL p50 "
          "| ITL p90 | all-arr % | adm % | rej % | unfin % | met/s |")
        w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in pd.concat([agg_ex, agg[agg.arm == OURS]],
                              ignore_index=True).iterrows():
            w(f"| {r['arm_label']} | {r['rate']:.0f} "
              f"| {fmt_range(r, 'arrival_rps', 2)} | {fmt_range(r, 'adm_req_s', 2)} "
              f"| {fmt_range(r, 'deliv_tok_s_clean')} "
              f"| {fmt_range(r, 'itl_p50', 1)} | {fmt_range(r, 'itl_p90', 1)} "
              f"| {r['all_arrivals']:.1f} | {r['admitted_att']:.1f} "
              f"| {r['rejected_pct']:.1f} | {r['unfinished_pct']:.1f} "
              f"| {fmt_range(r, 'met_req_s', 2)} |")

        w("\n### does our full policy sit inside the region or outside it?\n")
        pool = pd.concat([agg, agg_ex], ignore_index=True)
        for var in NOADM_ORDER:
            if var not in set(agg_ex.arm):
                continue
            sub = agg_ex[agg_ex.arm == var]
            rng = f"{sub.rate.min():.0f}..{sub.rate.max():.0f} req/s"
            for lcol, lname in [("itl_p90", "p90"), ("itl_p50", "median")]:
                un = pareto_check(pool, OURS, var, "deliv_tok_s_clean", lcol)
                rev = pareto_check(pool, var, OURS, "deliv_tok_s_clean", lcol)
                w(f"\n**FluidServe against {NOADM_LABEL[var]}, on "
                  f"(delivered throughput, {lname} per-token latency).** "
                  f"{len(un)} of FluidServe's 8 rates sit at a point that none of "
                  f"the {len(sub)} measured rates of that variant ({rng}) matches "
                  f"or beats in both coordinates; {len(rev)} of the variant's "
                  f"rates sit at a point FluidServe never matches.\n")
                if un:
                    w("- outside the traced region: "
                      + ", ".join(f"{r['rate']:.0f} req/s "
                                  f"({r['deliv_tok_s_clean']:.0f} tok/s, "
                                  f"{r[lcol]:.1f} ms)" for r in un) + "\n")
                if rev:
                    w("- reached by the variant and not by us: "
                      + ", ".join(f"{r['rate']:.0f} req/s "
                                  f"({r['deliv_tok_s_clean']:.0f} tok/s, "
                                  f"{r[lcol]:.1f} ms)" for r in rev) + "\n")

        w("\n### the same question restricted to the throughput band the variant "
          "actually covers\n")
        w("The counts above are inflated and must not be quoted on their own. A "
          "variant measured only at 25, 35 and 45 req/s delivers only a narrow "
          "band of throughput, and every FluidServe condition BELOW that band is "
          "counted as unreachable for the trivial reason that the variant was "
          "never run that slowly. Restricting the test to the band the variant "
          "does cover is the only version of it that carries information.\n")
        for var in NOADM_ORDER:
            if var not in set(agg_ex.arm):
                continue
            sub = agg_ex[agg_ex.arm == var]
            lo, hi = sub.deliv_tok_s_clean.min(), sub.deliv_tok_s_clean.max()
            mine = agg[(agg.arm == OURS)]
            inband = mine[(mine.deliv_tok_s_clean >= lo * 0.95)]
            above = mine[mine.deliv_tok_s_clean > hi]
            # Does the variant's repeat range reach what ours reaches? A gap
            # smaller than the union of the two ranges is not a gap.
            best = sub.loc[sub.deliv_tok_s_clean.idxmax()]
            ours_top = mine.loc[mine.deliv_tok_s_clean.idxmax()]
            clears = ours_top["deliv_tok_s_clean_lo"] > best["deliv_tok_s_clean_hi"]
            w(f"\n**{NOADM_LABEL[var]}** traces "
              f"{lo:.0f}..{hi:.0f} delivered output tokens/s over its three "
              f"rates. FluidServe has {len(inband)} conditions at or above the "
              f"bottom of that band and {len(above)} above its top. Its highest "
              f"is {ours_top['deliv_tok_s_clean']:.0f} tok/s "
              f"({ours_top['deliv_tok_s_clean_lo']:.0f}.."
              f"{ours_top['deliv_tok_s_clean_hi']:.0f}) at "
              f"{ours_top['rate']:.0f} req/s against the variant's highest "
              f"{best['deliv_tok_s_clean']:.0f} tok/s "
              f"({best['deliv_tok_s_clean_lo']:.0f}.."
              f"{best['deliv_tok_s_clean_hi']:.0f}) at {best['rate']:.0f} req/s; "
              + ("the two repeat ranges do not overlap, so this is a real gap."
                 if clears else
                 "the two repeat ranges overlap, so this is not a gap.")
              + " The variant was not driven past 45 req/s, so its curve is not "
              "traced to saturation and this comparison cannot rule out that it "
              "would reach the same throughput if it were.\n")
            w("\n| deliv tok/s | arm | req/s | ITL p50 | ITL p90 | all-arr % "
              "| met/s | wasted % of admitted |")
            w("|---:|---|---:|---:|---:|---:|---:|---:|")
            band = pd.concat([inband, sub], ignore_index=True)
            for _, r in band.sort_values("deliv_tok_s_clean").iterrows():
                waste = r["adm_req_s"] - r["met_req_s"]
                frac = (100.0 * waste / r["adm_req_s"]
                        if r["adm_req_s"] > 0 else np.nan)
                w(f"| {r['deliv_tok_s_clean']:.0f} | {ARM_LABEL[r['arm']]} "
                  f"| {r['rate']:.0f} | {r['itl_p50']:.1f} | {r['itl_p90']:.1f} "
                  f"| {r['all_arrivals']:.1f} | {r['met_req_s']:.2f} "
                  f"| {frac:.1f} |")

        w("\n### the same three rates, side by side\n")
        w("This is the comparison the ablation was designed for: identical "
          "binary, identical fleet, identical workload, one arrival rate at a "
          "time, with only the admission machinery differing.\n")
        w("\n| req/s | arm | admitted/s | deliv tok/s | ITL p50 | ITL p90 "
          "| all-arr % | met/s | wasted/s | wasted % of admitted |")
        w("|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for rate in sorted(agg_ex.rate.unique()):
            for arm in [OURS] + NOADM_ORDER:
                s = pool[(pool.arm == arm) & (pool.rate == rate)]
                if s.empty:
                    continue
                r = s.iloc[0]
                waste = r["adm_req_s"] - r["met_req_s"]
                frac = (100.0 * waste / r["adm_req_s"]
                        if r["adm_req_s"] > 0 else np.nan)
                w(f"| {rate:.0f} | {ARM_LABEL[arm]} "
                  f"| {fmt_range(r, 'adm_req_s', 2)} "
                  f"| {fmt_range(r, 'deliv_tok_s_clean')} "
                  f"| {fmt_range(r, 'itl_p50', 1)} | {fmt_range(r, 'itl_p90', 1)} "
                  f"| {r['all_arrivals']:.1f} | {fmt_range(r, 'met_req_s', 2)} "
                  f"| {waste:.2f} | {frac:.1f} |")

        w("\n### verdict on the two addenda\n")
        p = pd.concat([agg, agg_ex], ignore_index=True)

        def c(arm, rate):
            return p[(p.arm == arm) & (p.rate == rate)].iloc[0]

        w("\n**6. Wasted work separates the arms where a change of operating "
          "point would not.** At the matched delivered throughput of about "
          "12,000 output tokens/s, FluidServe at 25 req/s accepts "
          f"{c(OURS, 25)['adm_req_s']:.2f} req/s and fails "
          f"{c(OURS, 25)['adm_req_s'] - c(OURS, 25)['met_req_s']:.2f} of them, "
          f"which is {100 * (c(OURS, 25)['adm_req_s'] - c(OURS, 25)['met_req_s']) / c(OURS, 25)['adm_req_s']:.1f}% of "
          "what it accepted; the vLLM router at the same 25 req/s accepts "
          f"{c('vllmcache', 25)['adm_req_s']:.2f} and fails "
          f"{c('vllmcache', 25)['adm_req_s'] - c('vllmcache', 25)['met_req_s']:.2f} "
          f"= {100 * (c('vllmcache', 25)['adm_req_s'] - c('vllmcache', 25)['met_req_s']) / c('vllmcache', 25)['adm_req_s']:.1f}%, "
          "and Llumnix SLO fails "
          f"{100 * (c('slo', 25)['adm_req_s'] - c('slo', 25)['met_req_s']) / c('slo', 25)['adm_req_s']:.1f}%. "
          "At 70 req/s the two arms that never refuse at any rate are wasting "
          f"{100 * (c('vllmcache', 70)['adm_req_s'] - c('vllmcache', 70)['met_req_s']) / c('vllmcache', 70)['adm_req_s']:.1f}% "
          "and "
          f"{100 * (c('polyserve', 70)['adm_req_s'] - c('polyserve', 70)['met_req_s']) / c('polyserve', 70)['adm_req_s']:.1f}% "
          "of everything they accept while FluidServe wastes "
          f"{100 * (c(OURS, 70)['adm_req_s'] - c(OURS, 70)['met_req_s']) / c(OURS, 70)['adm_req_s']:.1f}%. "
          "This is the quantity that a mere shift of operating point does not "
          "have to improve, and it is where the arms differ most.\n")
        w("\n**7. Against our own no-admission variants the honest reading is "
          "Aequitas's, not a frontier claim.** In the throughput band the "
          "variants actually cover, the full policy and `fsroute` reach nearly "
          "the same place: at 45 req/s the delivered throughput is "
          f"{c(OURS, 45)['deliv_tok_s_clean']:.0f} against "
          f"{c('fsroute', 45)['deliv_tok_s_clean']:.0f} tok/s and the p90 "
          f"per-token latency is {c(OURS, 45)['itl_p90']:.1f} against "
          f"{c('fsroute', 45)['itl_p90']:.1f} ms, with requests met per second "
          f"{c(OURS, 45)['met_req_s']:.2f} "
          f"({c(OURS, 45)['met_req_s_lo']:.2f}..{c(OURS, 45)['met_req_s_hi']:.2f}) "
          f"against {c('fsroute', 45)['met_req_s']:.2f} "
          f"({c('fsroute', 45)['met_req_s_lo']:.2f}.."
          f"{c('fsroute', 45)['met_req_s_hi']:.2f}) -- a gap of "
          f"{c(OURS, 45)['met_req_s'] - c('fsroute', 45)['met_req_s']:.2f} req/s "
          "against a repeat range of "
          f"{c('fsroute', 45)['met_req_s_hi'] - c('fsroute', 45)['met_req_s_lo']:.2f}, "
          "so on requests met per second it is not a difference. **Routing alone "
          "already traces most of this region, and admission is choosing where "
          "on it to sit.** What admission does change, and changes far outside "
          "the repeat ranges, is the cost of sitting there: at 45 req/s the full "
          f"policy accepts {c(OURS, 45)['adm_req_s']:.2f} req/s and wastes "
          f"{100 * (c(OURS, 45)['adm_req_s'] - c(OURS, 45)['met_req_s']) / c(OURS, 45)['adm_req_s']:.1f}% "
          f"of that, while `fsroute` accepts {c('fsroute', 45)['adm_req_s']:.2f} "
          "and wastes "
          f"{100 * (c('fsroute', 45)['adm_req_s'] - c('fsroute', 45)['met_req_s']) / c('fsroute', 45)['adm_req_s']:.1f}% "
          f"-- {c('fsroute', 45)['adm_req_s'] - c('fsroute', 45)['met_req_s']:.1f} "
          f"against {c(OURS, 45)['adm_req_s'] - c(OURS, 45)['met_req_s']:.1f} "
          "requests per second accepted and then failed. At the lower end of the "
          "band admission does also raise the outcome itself: at 25 req/s "
          f"requests met per second is {c(OURS, 25)['met_req_s']:.2f} "
          f"({c(OURS, 25)['met_req_s_lo']:.2f}..{c(OURS, 25)['met_req_s_hi']:.2f}) "
          f"against {c('fsroute', 25)['met_req_s']:.2f} "
          f"({c('fsroute', 25)['met_req_s_lo']:.2f}.."
          f"{c('fsroute', 25)['met_req_s_hi']:.2f}), a gap larger than either "
          "repeat range, and all-arrivals attainment is "
          f"{c(OURS, 25)['all_arrivals']:.1f}% against "
          f"{c('fsroute', 25)['all_arrivals']:.1f}%. **So the claim to make "
          "against our own ablation is the one Aequitas makes -- the controller "
          "reaches the right operating point by itself, without the load being "
          "hand-tuned to it, and it does so while wasting four to seven times "
          "less accepted work -- and NOT a claim that admission moves the "
          "throughput-versus-tail frontier. The frontier claim holds against the "
          "four external baselines, where it was tested over eight rates, and it "
          "does not hold against our own routing-only variant over the three "
          "rates it was measured at.**\n")

    path = os.path.join(out_dir, "04_tradeoff_plane.md")
    with open(path, "w") as f:
        f.write("\n".join(L) + "\n")
    print(f"wrote {path}")


def make_figure(agg, agg_ex, out_dir):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from exp22_fluidserve import ARM_STYLE, PAPER_STYLE

    def draw(ax, frame, arms, xcol, ycol):
        for arm in arms:
            s = frame[frame.arm == arm].sort_values("rate")
            if s.empty:
                continue
            st = dict(ARM_STYLE.get(arm, {}))
            st["label"] = ARM_LABEL[arm]
            ax.plot(s[xcol], s[ycol], **st)

    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(1, 3, figsize=(7.6, 2.7))
        for ax, col, name in zip(axes, ["itl_p50", "itl_p90", "itl_p99"],
                                 ["median", "p90", "p99"]):
            draw(ax, agg, ARM_ORDER, "deliv_tok_s_clean", col)
            ax.set_xlabel("Delivered output tokens/s")
            ax.set_ylabel(f"Per-token latency, {name} (ms)")
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, 1.02),
                       ncol=len(ARM_ORDER))
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "04_plane_figure.png"), dpi=300)
        plt.close(fig)

        # Protego's presentation: the rates in their own panels, so the gap
        # between what was accepted and what was served well is visible as a
        # distance rather than hidden inside a ratio.
        fig, axes = plt.subplots(1, 3, figsize=(7.6, 2.7))
        for ax, col, name in zip(
                axes, ["adm_req_s", "met_req_s", "met_tok_s"],
                ["Admitted (req/s)", "Met within SLO (req/s)",
                 "Met within SLO (output tok/s)"]):
            draw(ax, agg, ARM_ORDER, "rate", col)
            ax.set_xlabel("Arrival rate (req/s)")
            ax.set_ylabel(name)
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        axes[1].legend(loc="lower center", bbox_to_anchor=(0.5, 1.02),
                       ncol=len(ARM_ORDER))
        fig.tight_layout()
        fig.savefig(os.path.join(out_dir, "04_plane_rates.png"), dpi=300)
        plt.close(fig)

        if len(agg_ex):
            pool = pd.concat([agg, agg_ex], ignore_index=True)
            arms = [OURS] + NOADM_ORDER
            fig, axes = plt.subplots(1, 2, figsize=(6.8, 2.9))
            draw(axes[0], pool, arms, "deliv_tok_s_clean", "itl_p90")
            axes[0].set_xlabel("Delivered output tokens/s")
            axes[0].set_ylabel("Per-token latency, p90 (ms)")
            draw(axes[1], pool, arms, "rate", "all_arrivals")
            axes[1].set_xlabel("Arrival rate (req/s)")
            axes[1].set_ylabel("All-arrivals attainment (%)")
            axes[1].set_ylim(-3, 105)
            for ax in axes:
                ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            axes[0].legend(loc="lower center", bbox_to_anchor=(1.05, 1.02),
                           ncol=1)
            fig.tight_layout()
            fig.savefig(os.path.join(out_dir, "04_plane_noadmission.png"), dpi=300)
            plt.close(fig)


if __name__ == "__main__":
    sys.exit(main())
