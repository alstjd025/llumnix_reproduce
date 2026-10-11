#!/usr/bin/env python3
"""Re-score finished runs under the token-level cumulative deadline, including
the tolerance form: a request passes if at least a fraction q of its output
tokens each arrived by their own deadline.

WHY THIS SCRIPT EXISTS, AND WHAT IT IS NOT.
`tail2026_literature_rules.py` already scores the four rules the literature
uses (mean, cumulative deadline, block-of-ten mean, every token) on the pinned
static sweep. It is left untouched because its numbers are cited. This script
adds the two things that one cannot answer:

  1. THE TOLERANCE. The rules in the literature are all "for every i". The rule
     proposed here allows a fraction of the tokens to miss their own deadline,
     which no paper among the nine read for ms_dev/notes/slo-definitions.md
     does. Because it is a new rule, every strict rule it relaxes is computed
     beside it on the same requests, so the effect of the relaxation is a
     column difference rather than an assertion.
  2. THE AGENT CLASS. The other script skips swe: its promise was an end-to-end
     30 s budget with no per-token term. FluidServe v0.4 restated that promise
     as TTFT 7 s + 75 ms per token, so swe now has a per-token half like the
     other two classes and is scored here.

DEFINITIONS. Write a_1 <= ... <= a_N for the arrival offsets of the N output
tokens of one request, in milliseconds from the moment the client submitted it;
T for the class time-to-first-token budget and P for its per-token budget. In
every column below the composite a request must satisfy is

    a_1 <= T   AND   <the per-token rule>

so the time-to-first-token half is never traded away by the tolerance. This is
deliberate and it is the answer to a real weakness: with the deadline measured
from submission a request that beat T banks the unused time, and if the engine
then decodes faster than P it can absorb a first-token violation of about
N * (P - actual per-token time) before any deadline is missed. Keeping a_1 <= T
as its own conjunct removes that.

    mean      (a_N - a_1) / (N - 1) <= P
              The status quo in this repository, and Scorpio's rule.
    cum       a_i <= T + i * P        for every i        PolyServe's indexing.
    cumt      a_i <= T + (i-1) * P    for every i        First token due at
              exactly T, so the schedule and the TTFT budget agree at i = 1.
    q<NN>     |{i : a_i <= T + (i-1) * P}| / N >= 0.NN   The proposed rule.
    q90end    q90 AND a_N <= T + (N-1) * P               q90 with the last token
              still bound, which is what stops an unbounded overrun that is
              concentrated in the final tokens from being invisible.
    ft90      |{i : a_i <= a_1 + (i-1) * P}| / N >= 0.90 The same tolerance with
              the schedule anchored at the OBSERVED first token instead of at
              the budget. This is the variant that matches what the scheduler
              itself accounts for: for a per-token class the policy's remaining
              budget is counted from the first token, not from arrival
              (fluidserve-system-design.md section 0.3), so this column and the
              controller measure the same quantity.

DIAGNOSTICS, because the tolerance's weakness is a distribution and not a mean.
Under sustained overload the lateness of a request grows monotonically, so the
tokens that miss are the LAST ones, and "90% of tokens met" then means "the
request was on schedule for its first 90% and the rest is unmeasured". Every
run therefore also reports, for the requests that pass q90 but fail cumt:
how late the final token was, and whether the missed tokens formed a suffix.

BURSTS. Four of the five arms reach the client through the Llumnix gateway,
which hands several already-generated tokens over in the same instant; llm-d
does not. Every rule is computed twice, RAW on the recorded arrivals and DEBURST
on the reconstruction in tail2026_literature_rules.deburst, which spreads a
sub-5 ms burst evenly over the tokens it delivered and leaves the first and last
arrival alone. A cumulative deadline is a statement about when a token was
DELIVERED, so RAW is the primary and DEBURST bounds how much of any difference
between the arms is the transport.

USAGE
  python3 rescore_token_deadline.py --runs <dir>[,<dir>...]
  python3 rescore_token_deadline.py --pinned --arms fspfx,llmdslo --rates 35,45
  python3 rescore_token_deadline.py --pinned            # every pinned run
Options: --workers N (default 4, kept low so a running experiment's load
generator is not competing for cores), --max-req N (pilot), --out <dir>.
"""
import argparse
import os
import re
import sys

# The form of the agent class's promise is part of the scoring and has to be
# fixed BEFORE exp22_fluidserve is imported, because that module reads it at
# import time and prints what it is scoring against. 7 s / 75 ms is FluidServe
# v0.4 section 1.3.
os.environ.setdefault("FS_SWE_TBT_MS", "75")
os.environ.setdefault("FS_SWE_TTFT_S", "7")

import numpy as np                                                # noqa: E402
import pandas as pd                                               # noqa: E402
from concurrent.futures import ProcessPoolExecutor                # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import SLO_RULES, load_run, attain          # noqa: E402
from tail2026_literature_rules import (                           # noqa: E402
    RESULTS, PINNED, TAU_MS, RE_OFF, RE_TASK, RE_OUT, ARM_LABEL, deburst)

QUANTILES = (0.90, 0.95, 0.99)
RULES = ["mean", "cum", "cumt"] + [f"q{int(q * 100)}" for q in QUANTILES] + \
        ["q90end", "ft90"]


def budgets_for(cls):
    """(T in ms, P in ms) for a class, or None if it has no per-token term."""
    r = SLO_RULES[cls]
    if "e2e" in r:
        return None
    return r["ttft"] * 1000.0, r["tbt"]


def class_of_task(task):
    return "chat" if task.startswith("sg-") else (
        "deepresearch" if task.startswith("sa-") else "swe")


def rule_outcomes(a, T, P):
    """Every rule's verdict for one request, plus the tolerance diagnostics."""
    n = a.size
    if n < 2:
        return None
    idx = np.arange(1, n + 1, dtype=float)
    out = {}
    out["mean"] = bool((a[-1] - a[0]) / (n - 1) <= P)
    out["cum"] = bool(not (a > T + idx * P).any())

    due = T + (idx - 1.0) * P            # the cumt schedule
    ok = a <= due
    met = float(ok.mean())
    out["cumt"] = bool(ok.all())
    out["met_frac"] = met
    for q in QUANTILES:
        out[f"q{int(q * 100)}"] = bool(met >= q)
    out["q90end"] = bool(met >= 0.90 and ok[-1])

    due_ft = a[0] + (idx - 1.0) * P      # schedule anchored at the first token
    out["ft90"] = bool((a <= due_ft).mean() >= 0.90)

    # How late the request finished relative to its own schedule, and whether
    # the tokens that missed were a suffix. Both are what decides whether the
    # tolerance is hiding an unbounded overrun or forgiving a transient stall.
    out["last_late_ms"] = float(a[-1] - due[-1])
    out["misses_are_suffix"] = bool(np.all(np.diff(ok.astype(np.int8)) <= 0))
    out["n_tokens"] = int(n)
    out["ttft_ms"] = float(a[0])
    return out


def scan_run(run, max_req=None, tau=TAU_MS):
    """Per-request verdicts for one run, RAW and DEBURST, all three classes."""
    path = os.path.join(RESULTS, run, "tbt_events.jsonl")
    rows, n_chunk, n_tok, n_short = [], 0, 0, 0
    with open(path, "rb") as f:
        for line in f:
            if b'"agent": "request"' not in line:
                continue
            mt = RE_TASK.search(line)
            if mt is None:
                continue
            task = mt.group(1).decode()
            cls = class_of_task(task)
            b = budgets_for(cls)
            if b is None:                # class has no per-token term at all
                continue
            T, P = b
            offs = RE_OFF.findall(line)
            mo = RE_OUT.search(line)
            if mo is not None:
                n_tok += int(mo.group(1))
            n_chunk += len(offs)
            if len(offs) < 2:
                n_short += 1
                continue
            a = np.array([float(x) for x in offs])
            rec = {"task_id": task, "class": cls}
            r_raw = rule_outcomes(a, T, P)
            r_deb = rule_outcomes(deburst(a, tau), T, P)
            if r_raw is None or r_deb is None:
                continue
            for k, v in r_raw.items():
                rec[f"raw_{k}"] = v
            for k, v in r_deb.items():
                rec[f"deb_{k}"] = v
            rec["sub_tau_frac"] = float((np.diff(a) < tau).mean())
            rows.append(rec)
            if max_req and len(rows) >= max_req:
                break
    ev = pd.DataFrame(rows)
    ev.attrs["chunk_over_token"] = (n_chunk / n_tok) if n_tok else np.nan
    ev.attrs["n_short"] = n_short
    return ev


def score_run(run_dir, ev, prefix):
    """Attainment of one run under every rule, on all three denominators."""
    rows = load_run(run_dir)
    if rows is None or rows.empty:
        return None
    ttft = pd.to_numeric(rows["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(rows["latency"], errors="coerce")
    evi = ev.set_index("task_id") if not ev.empty else ev

    # Throughput, goodput and the rejection rate are computed here rather than
    # in the figure script, so that one pass over the token events produces
    # everything a figure needs and the two cannot disagree about which requests
    # met their rule. The definitions are the ones the existing paper figure
    # uses (fig_motivation_throughput_goodput.collect): the span is first to
    # last arrival, throughput counts the output tokens of every request the
    # policy accepted, and goodput counts only those of requests that met the
    # rule named by the column.
    span = float(rows["rel"].max() - rows["rel"].min())
    served_all = rows[~rows["rejected"]]
    out = {"run": os.path.basename(run_dir), "n": len(rows), "span_s": span,
           "throughput_tok_s": float(pd.to_numeric(
               served_all["output_tokens"], errors="coerce").fillna(0).sum()
               / span) if span > 0 else np.nan,
           "rejected_pct": 100.0 * float(rows["rejected"].mean())}
    for cname, tag in (("chat", "chat"), ("deepresearch", "dr"), ("swe", "swe")):
        out[f"n_{tag}"] = int((rows["class"] == cname).sum())
        sub = rows[rows["class"] == cname]
        out[f"rejected_{tag}_pct"] = 100.0 * float(sub["rejected"].mean()) \
            if len(sub) else np.nan
    need = (~rows["rejected"]) & (~rows["errored"]) & (~rows["cutoff"])
    have = need & rows["task_id"].map(lambda t: (not ev.empty) and t in evi.index)
    out["n_need_stream"] = int(need.sum())
    out["n_have_stream"] = int(have.sum())
    out["stream_coverage"] = out["n_have_stream"] / out["n_need_stream"] \
        if out["n_need_stream"] else np.nan

    for rule in RULES:
        col = f"{prefix}_{rule}"
        ok = rows["task_id"].map(evi[col]) if (not ev.empty and col in evi.columns) \
            else pd.Series(np.nan, index=rows.index)
        miss = pd.Series(False, index=rows.index)
        for cname, spec in SLO_RULES.items():
            m = rows["class"] == cname
            if not m.any():
                continue
            if "e2e" in spec:
                miss.loc[m] = e2e[m] > spec["e2e"]
                continue
            pt = ok[m]
            # A request whose stream was not found falls back to the recorded
            # mean per-token time, written as `not (itl > budget)` so that a
            # request with fewer than two tokens is not turned into a violation
            # -- load_run catches those through the first-token test instead.
            fallback = ~(pd.to_numeric(rows.loc[m, "itl_ms"],
                                       errors="coerce") > spec["tbt"])
            pt = pt.where(pt.notna(), fallback)
            miss.loc[m] = (ttft[m] > spec["ttft"]) | (~pt.astype(bool))
        miss = miss | (ttft.isna() & ~rows["cutoff"])
        r = rows.copy()
        r["violate_offered"] = miss | r["rejected"] | r["errored"]
        r["violate_served"] = miss
        met = (~r["violate_offered"]) & (~r["cutoff"])
        out[f"{rule}_all"] = 100.0 * met.sum() / len(r)
        out[f"{rule}_goodput"] = float(pd.to_numeric(
            r.loc[met, "output_tokens"], errors="coerce").fillna(0).sum()
            / span) if span > 0 else np.nan
        out[f"{rule}_offered"] = attain(r, "violate_offered")
        out[f"{rule}_admitted"] = attain(r, "violate_served")
        for cname, tag in (("chat", "chat"), ("deepresearch", "dr"), ("swe", "swe")):
            sub = r[r["class"] == cname]
            if not len(sub):
                continue
            out[f"{rule}_{tag}_offered"] = attain(sub, "violate_offered")
            out[f"{rule}_{tag}_admitted"] = attain(sub, "violate_served")
    return out


def diagnostics(ev, prefix):
    """What the tolerance forgives, per class: the requests it rescues."""
    det = {}
    for cls, tag in (("chat", "chat"), ("deepresearch", "dr"), ("swe", "swe")):
        sub = ev[ev["class"] == cls]
        if sub.empty:
            continue
        cumt = sub[f"{prefix}_cumt"].astype(bool)
        q90 = sub[f"{prefix}_q90"].astype(bool)
        rescued = q90 & ~cumt
        det[f"{tag}_n"] = int(len(sub))
        det[f"{tag}_rescued_pct"] = 100.0 * float(rescued.mean())
        det[f"{tag}_met_frac_p50"] = float(sub[f"{prefix}_met_frac"].median())
        det[f"{tag}_suffix_pct"] = 100.0 * float(
            sub.loc[~cumt, f"{prefix}_misses_are_suffix"].mean()) \
            if (~cumt).any() else np.nan
        if rescued.any():
            late = sub.loc[rescued, f"{prefix}_last_late_ms"]
            det[f"{tag}_rescued_lastlate_p50_ms"] = float(late.median())
            det[f"{tag}_rescued_lastlate_p90_ms"] = float(late.quantile(0.90))
            det[f"{tag}_rescued_lastlate_max_ms"] = float(late.max())
            det[f"{tag}_rescued_ntok_p50"] = float(
                sub.loc[rescued, f"{prefix}_n_tokens"].median())
    return det


def _one(args):
    run, arm, rate, max_req = args
    try:
        ev = scan_run(run, max_req=max_req)
    except Exception as exc:                                      # noqa: BLE001
        return {"run": run, "arm": arm, "rate": rate, "error": str(exc)}, None
    cot = ev.attrs.get("chunk_over_token", np.nan)
    recs, dets = [], []
    for prefix in ("raw", "deb"):
        s = score_run(os.path.join(RESULTS, run), ev, prefix)
        if s is None:
            return {"run": run, "arm": arm, "rate": rate,
                    "error": "no usable metrics rows"}, None
        s.update({"arm": arm, "rate": rate, "prefix": prefix,
                  "chunk_over_token": cot,
                  "sub_tau_frac": float(ev["sub_tau_frac"].mean())
                  if not ev.empty else np.nan})
        recs.append(s)
        d = {"run": run, "arm": arm, "rate": rate, "prefix": prefix}
        d.update(diagnostics(ev, prefix))
        dets.append(d)
    return recs, dets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="")
    ap.add_argument("--pinned", action="store_true")
    ap.add_argument("--arms", default="")
    ap.add_argument("--rates", default="")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-req", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(
        RESULTS, "aggregate_analysis", "token_deadline_2026-08-31"))
    ap.add_argument("--tag", default="rescore")
    args = ap.parse_args()

    jobs = []
    if args.pinned:
        m = pd.read_csv(os.path.join(PINNED, "manifest.tsv"), sep="\t")
        if args.arms:
            keep = set(args.arms.split(","))
            m = m[m["arm"].isin(keep)]
        if args.rates:
            keep = {float(x) for x in args.rates.split(",")}
            m = m[m["req_per_s"].isin(keep)]
        for _, row in m.iterrows():
            if not os.path.exists(os.path.join(RESULTS, row["run"],
                                               "tbt_events.jsonl")):
                print(f"!! no per-token events, skipped: {row['run']}",
                      file=sys.stderr)
                continue
            jobs.append((row["run"], row["arm"], float(row["req_per_s"]),
                         args.max_req or None))
    for r in [x for x in args.runs.split(",") if x]:
        run = os.path.basename(r.rstrip("/"))
        # A result directory is <date>_<time>_<session>_<arm>[_<mix or tag>], so
        # the arm is the fourth field; the third is the session and naming the
        # arm after it would put two repeats of one arm under two labels.
        parts = run.split("_")
        # The arrival rate is in the directory name for a static condition and
        # absent for a trace replay; NaN marks the second case rather than
        # guessing, so a figure that needs a rate axis drops those rows loudly.
        mrate = re.search(r"_rpm_(\d+)", run)
        jobs.append((run, parts[3] if len(parts) > 3 else run,
                     int(mrate.group(1)) / 60.0 if mrate else np.nan,
                     args.max_req or None))
    if not jobs:
        sys.exit("no runs to score")

    os.makedirs(args.out, exist_ok=True)
    rows, dets = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        for recs, det in ex.map(_one, jobs):
            if isinstance(recs, dict):
                print(f"!! {recs['run']}: {recs['error']}", file=sys.stderr)
                continue
            rows.extend(recs)
            dets.extend(det)
            print(f"   scored {recs[0]['run']}", file=sys.stderr, flush=True)

    per_run = pd.DataFrame(rows)
    per_run.to_csv(os.path.join(args.out, f"{args.tag}_per_run.csv"), index=False)
    pd.DataFrame(dets).to_csv(
        os.path.join(args.out, f"{args.tag}_diagnostics.csv"), index=False)
    print(f"\nwrote {args.out}/{args.tag}_per_run.csv "
          f"({len(per_run)} rows = {len(jobs)} runs x 2 arrival treatments)")
    raw = per_run[per_run["prefix"] == "raw"]
    cols = ["run", "arm", "rate", "stream_coverage", "chunk_over_token"] + \
           [f"{r}_offered" for r in RULES]
    with pd.option_context("display.width", 200, "display.max_columns", 40):
        print(raw[cols].to_string(index=False, float_format=lambda v: f"{v:.2f}"))


if __name__ == "__main__":
    main()
