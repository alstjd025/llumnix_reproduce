#!/usr/bin/env python3
"""Re-score the pinned static sweep under every per-token latency rule the
serving literature actually uses, instead of under the one this repository
happened to adopt.

WHY. The attainment figure in this repository scores the per-token half of a
class rule as `mean inter-token time over the request <= budget`. That is one of
four rules in use, and the systems this work is compared against do not all use
it. If the result only holds under the mean, the paper cannot claim it; if it
holds under all four, the choice of rule stops being an objection. This script
computes all four on the same runs, the same window and the same denominator, so
the only thing that moves between the columns is the rule.

THE FOUR RULES, stated on the arrival times of the output tokens of one request.
Write a_1 <= a_2 <= ... <= a_N for the arrival offsets of the N output tokens
measured from the moment the request was submitted, T for the class's
time-to-first-token budget and P for its per-token budget.

  RULE 1  MEAN                 (a_N - a_1) / (N - 1) <= P
          The status quo here, and Scorpio's definition: "TPOT sets an upper
          bound on the average latency for generating subsequent tokens." One
          long stall is paid for out of the slack of every other token.

  RULE 2  CUMULATIVE DEADLINE  a_i <= T + i * P   for every i = 1..N
          PolyServe's rule. The request carries a schedule of deadlines fixed at
          arrival; a token that arrives after its own deadline is a violation
          even if the request later catches up. It constrains every prefix of
          the request and not only the end, but it also lets a request that ran
          fast bank slack against a later stall, so it is neither implied by nor
          implies rule 1. The direction actually measured is reported below
          rather than assumed.
          Reported alongside is RULE 2-TIGHT, a_i <= T + (i-1) * P, which is the
          same schedule with the first token's deadline pinned at T instead of
          T + P. It is the variant that makes the last constraint coincide
          exactly with the mean condition, and it is one budget stricter
          everywhere. Both are given because the two indexings appear in the
          literature and the gap between them is one per-token budget, which at
          chat's 50 ms is a fifth of a percent of a 250-token request.

  RULE 3  BLOCK MEAN OF 10     mean of every consecutive block of 10 per-token
                               gaps <= P
          How SLOs-Serve checks the budget when tokens do not arrive one at a
          time. Blocks are non-overlapping and cut from the start of the
          request; the trailing partial block is included, and the sensitivity
          of dropping it is reported.

  RULE 4  EVERY TOKEN          a_i - a_{i-1} <= P for every i
          SLOs-Serve's stated rule. One gap over budget fails the request. This
          is the upper bound of severity and is reported as such.

Rules 1 and the TTFT half of every class rule are unchanged from
exp22_fluidserve.load_run, which this script calls rather than reimplements, so
the rule-1 column reproduces the published figure by construction.

THE swe CLASS IS HELD FIXED. Its rule is end-to-end <= 30 s and contains no
per-token term, so its score is identical under all four rules. The script
asserts that rather than stating it.

WHAT THE CLIENT ACTUALLY RECORDED, AND WHY IT MATTERS HERE.
`tbt_events.jsonl` records one entry per STREAMED CHUNK: its arrival offset from
the request's submission, its text length in characters, and the client's own
estimate of how many tokens it carried. The token estimate is known to be wrong
-- the client tokenises each chunk out of context, which splits fragments that
are one token inside the full string, and a floor of one keeps every sub-token
chunk at one. That defect is why the recorded mean per-token time was about half
the truth and why only the mean was ever corrected.

Two facts decide how much of that matters for rules 2 to 4.

  (a) The stream is one token per chunk. Over the six runs measured in
      results/aggregate_analysis/tail_2026-08-16/chunk_shape.json the median
      ratio of chunk count to the server's output_tokens is 0.9946 on every arm,
      identical to four figures between FluidServe and llm-d. So the gap between
      two chunks IS the gap between two tokens and no per-chunk token estimate
      is needed. The script recomputes this ratio for every run it reads and
      refuses to report a run whose ratio leaves [0.90, 1.10].
      Two alternative token allocations are computed on a subset as a check:
      tokens spread across chunks in proportion to each chunk's character count,
      and the client's own per-chunk estimate renormalised to the server's
      output_tokens. Both are reported; if they moved the answer the primary
      choice would be wrong.

  (b) The transport delivers tokens in bursts on four of the five arms, and not
      on the fifth. Fraction of chat inter-chunk gaps below 0.1 ms, first repeat
      at 15 req/s: FluidServe 0.068, vLLM router 0.077, Llumnix SLO 0.064,
      PolyServe 0.060, llm-d 0.0001. The four that go through the Llumnix
      gateway hand several already-generated tokens to the client in the same
      instant, preceded by one long gap; llm-d, which reaches the engines
      through its own inference gateway, does not.
      This is a property of the transport, not of the scheduling being measured,
      and it falls entirely on one side of the comparison. It leaves rule 1
      untouched (the span and the token count are both unchanged) and inflates
      rules 3 and 4 on exactly the four arms that burst, because k tokens
      produced at a steady cadence are recorded as one long gap and k-1 gaps of
      zero.
      Every rule is therefore computed twice: RAW on the recorded arrival times,
      and DEBURST on a reconstruction that treats a run of chunks separated by
      less than TAU = 5 ms as one delivery and spreads the wall-clock time that
      delivery covers evenly over the tokens in it. The reconstruction preserves
      the first and last arrival, so rule 1 is unchanged by it, which is the
      check that it is a redistribution and not a rescaling. It is a no-op on a
      stream with no sub-TAU gaps, so it cannot flatter the bursting arms
      relative to llm-d. TAU = 5 ms sits far below any real decode interval on
      this hardware (the 1st percentile of llm-d's gaps is 16.6 ms) and far
      above the burst floor (0.02 ms); the sensitivity to TAU is reported.

  Neither correction is applied to rule 2 as a matter of course, because the
  cumulative deadline depends on when a token was DELIVERED and a burst delays
  delivery for real. Both versions are reported for it too.

WHAT COUNTS IN THE DENOMINATOR. The primary metric is all arrivals: every
request that arrived inside the analysis window is in the denominator, and a
rejection, a client error and a request still unfinished when the load window
closed each count as a violation. That is all_arrivals_attainment.py's third
column, and it is used here because a policy that can refuse work must be
charged for refusing it. The offered and admitted columns are written to the
per-run CSV as well.

Usage
-----
  python3 tail2026_literature_rules.py --stage probe   # burst + chunk shape
  python3 tail2026_literature_rules.py --stage score --arms fspfx,llmdslo \
      --rates 15,25,35
  python3 tail2026_literature_rules.py --stage score      # all 80 runs
"""
import argparse
import json
import os
import re
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import SLO_RULES, load_run, attain  # noqa: E402

EXPDIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", ".."))
RESULTS = os.path.join(EXPDIR, "results")
PINNED = os.path.join(EXPDIR, "paper_experiment", "static_sweep_2026-08")
OUTDIR = os.path.join(RESULTS, "aggregate_analysis", "tail_2026-08-16")
SINCE = "260807_1900"          # directory names are UTC-7; older runs are excluded

TAU_MS = 5.0                   # burst threshold, see module docstring
BLOCK = 10                     # rule 3 block length, in per-token gaps

# One regex over the raw line beats json.loads by about 6x on these files and
# the fields are machine-written, so the shapes are fixed.
RE_OFF = re.compile(rb'"arrival_offset_ms": ([0-9.eE+-]+)')
RE_CHARS = re.compile(rb'"delta_chars": (\d+)')
RE_EST = re.compile(rb'"delta_tokens_est": (\d+)')
RE_TASK = re.compile(rb'"task_id": "([^"]*)"')
RE_OUT = re.compile(rb'"output_tokens": (\d+)')

ARM_LABEL = {"fspfx": "FluidServe", "llmdslo": "llm-d",
             "vllmcache": "vLLM router", "slo": "Llumnix SLO",
             "polyserve": "PolyServe"}


# ----------------------------------------------------------------- primitives
def deburst(off, tau=TAU_MS):
    """Arrival offsets with each sub-TAU burst spread evenly over its tokens.

    `off` is the recorded arrival offset of every chunk. A maximal run of chunks
    whose arrival gaps are all below `tau` is one delivery: the transport handed
    the client several tokens that the engine had produced at some cadence
    inside the window that ends at the last of them. The wall-clock span from
    the chunk before the delivery to its last chunk is divided evenly among the
    tokens delivered.

    The first and last elements are returned unchanged, so the request's span
    and hence its mean per-token time are identical before and after.
    """
    a = np.asarray(off, dtype=float)
    if a.size < 3:
        return a.copy()
    g = np.diff(a)
    out_g = np.empty_like(g)
    i = 0
    n = len(g)
    while i < n:
        j = i + 1
        while j < n and g[j] < tau:
            j += 1
        out_g[i:j] = (a[j] - a[i]) / (j - i)
        i = j
    return np.concatenate(([a[0]], a[0] + np.cumsum(out_g)))


def rule_outcomes(a, ttft_budget_ms, p_budget_ms, weights=None):
    """Pass/fail under each rule, plus where rule 2 first fails.

    `a` is the token arrival offsets in ms from submission, already one entry
    per token. `weights`, if given, is the number of tokens each entry carries
    and is used only by the alternative token allocations: an entry carrying w
    tokens has its gap divided by w for the per-gap rules, and occupies w
    positions on the cumulative-deadline axis.
    """
    n = a.size
    res = {}
    if n < 2:
        return None
    if weights is None:
        idx = np.arange(1, n + 1, dtype=float)      # 1-based token index
        gaps = np.diff(a)
    else:
        idx = np.cumsum(np.asarray(weights, dtype=float))
        w = np.asarray(weights, dtype=float)[1:]
        gaps = np.diff(a) / np.maximum(w, 1e-9)

    # rule 1, from the arrival stream rather than from metrics.csv, so that the
    # four rules are all computed on one object. The metrics.csv version is kept
    # as the headline and the two are compared in the report.
    res["r1"] = bool((a[-1] - a[0]) / max(idx[-1] - idx[0], 1e-9) <= p_budget_ms)

    # rule 2 and rule 2-tight. `idx` is the index of the LAST token an entry
    # carries, which is the strictest deadline that entry has to meet.
    d2 = ttft_budget_ms + idx * p_budget_ms
    d2t = ttft_budget_ms + (idx - 1.0) * p_budget_ms
    late2 = a > d2
    late2t = a > d2t
    res["r2"] = not bool(late2.any())
    res["r2tight"] = not bool(late2t.any())
    if late2.any():
        first = int(np.argmax(late2))
        res["r2_first_frac"] = float(idx[first] / idx[-1])
        res["r2_first_idx"] = float(idx[first])
        # Whether this request had ALREADY broken the time-to-first-token
        # conjunct. A request whose first token arrived after T fails rule 2 at
        # or near index 1 for that reason alone, and reading that as evidence of
        # a per-token failure would count one violation twice. The share is
        # reported next to the failure-position distribution so the distribution
        # can be read as per-token behaviour rather than as time-to-first-token
        # behaviour in disguise.
        res["r2_first_ttft_over"] = bool(a[0] > ttft_budget_ms)
    else:
        res["r2_first_frac"] = np.nan
        res["r2_first_idx"] = np.nan
        res["r2_first_ttft_over"] = False

    # rule 3: non-overlapping blocks of BLOCK gaps, trailing partial included.
    m = gaps.size
    nb = int(np.ceil(m / BLOCK))
    pad = nb * BLOCK - m
    gp = np.concatenate([gaps, np.full(pad, np.nan)]).reshape(nb, BLOCK)
    bm = np.nanmean(gp, axis=1)
    res["r3"] = bool(np.all(bm <= p_budget_ms))
    res["r3_nopartial"] = bool(np.all(bm[:-1] <= p_budget_ms)) if nb > 1 \
        else res["r3"]

    # rule 4
    res["r4"] = bool(gaps.max() <= p_budget_ms)

    # calibration-free stall statistics, task 4
    med = float(np.median(gaps))
    res["max_gap_ms"] = float(gaps.max())
    res["med_gap_ms"] = med
    res["n_gaps"] = int(m)
    res["n_gap_gt5x"] = int((gaps > 5.0 * med).sum()) if med > 0 else 0
    res["frac_gap_gt5x"] = res["n_gap_gt5x"] / m if m else np.nan
    return res


def budgets_for(cls):
    r = SLO_RULES[cls]
    if "e2e" in r:
        return None
    return r["ttft"] * 1000.0, r["tbt"]


# ------------------------------------------------------------------ streaming
def scan_run(run, want_variants=False, tau=TAU_MS, max_req=None):
    """Per-request rule outcomes for one run, from its tbt_events.jsonl.

    Returns a DataFrame keyed by task_id. Only chat and deepresearch appear:
    swe is scored end-to-end and has no per-token term at all.
    """
    path = os.path.join(RESULTS, run, "tbt_events.jsonl")
    rows = []
    n_chunk = 0
    n_tok = 0
    with open(path, "rb") as f:
        for line in f:
            if b'"agent": "request"' not in line:
                continue
            mt = RE_TASK.search(line)
            if mt is None:
                continue
            task = mt.group(1).decode()
            cls = "chat" if task.startswith("sg-") else (
                "deepresearch" if task.startswith("sa-") else "swe")
            if cls == "swe":
                continue
            b = budgets_for(cls)
            offs = RE_OFF.findall(line)
            if len(offs) < 2:
                continue
            a = np.array([float(x) for x in offs])
            mo = RE_OUT.search(line)
            srv_tok = int(mo.group(1)) if mo else 0
            n_chunk += len(a)
            n_tok += srv_tok
            ad = deburst(a, tau)
            raw = rule_outcomes(a, b[0], b[1])
            deb = rule_outcomes(ad, b[0], b[1])
            if raw is None or deb is None:
                continue
            rec = {"task_id": task, "class": cls, "n_chunks": len(a),
                   "srv_tokens": srv_tok,
                   "sub_tau_frac": float((np.diff(a) < tau).mean())}
            for k, v in raw.items():
                rec["raw_" + k] = v
            for k, v in deb.items():
                rec["deb_" + k] = v
            if want_variants:
                ch = np.array([float(x) for x in RE_CHARS.findall(line)])
                es = np.array([float(x) for x in RE_EST.findall(line)])
                if len(ch) == len(a) and ch.sum() > 0 and srv_tok > 1:
                    w = ch / ch.sum() * srv_tok
                    v = rule_outcomes(a, b[0], b[1], weights=w)
                    for k in ("r1", "r2", "r3", "r4"):
                        rec["chr_" + k] = v[k]
                if len(es) == len(a) and es.sum() > 0 and srv_tok > 1:
                    w = es / es.sum() * srv_tok
                    v = rule_outcomes(a, b[0], b[1], weights=w)
                    for k in ("r1", "r2", "r3", "r4"):
                        rec["est_" + k] = v[k]
            rows.append(rec)
            if max_req and len(rows) >= max_req:
                break
    df = pd.DataFrame(rows)
    df.attrs["chunk_over_token"] = (n_chunk / n_tok) if n_tok else np.nan
    return df


# ------------------------------------------------------------------- scoring
# "r1m" is rule 1 computed the way this repository already computes it, from
# metrics.csv's (latency - first_token_latency) / (output_tokens - 1). It is the
# published column and it is carried here so the table has a row that reproduces
# the pinned result exactly. "r1" is the same rule computed from the chunk
# arrival stream instead, which uses the client's chunk count as the token count
# and so differs by the 0.7% by which chunks and server tokens disagree. The two
# are reported next to each other and the difference is the size of that
# disagreement, not a third rule.
RULE_COLS = ["r1m", "r1", "r2", "r2tight", "r3", "r3_nopartial", "r4"]


def score_run(run_dir, ev, prefix):
    """Attainment of one run under every rule, on all three denominators.

    The composite for chat and deepresearch is `TTFT within budget AND the
    per-token rule holds`, exactly as load_run builds it for rule 1; only the
    second conjunct changes between the columns. swe keeps its end-to-end rule
    and is therefore identical in every column, which is asserted.
    """
    rows = load_run(run_dir)
    if rows is None or rows.empty:
        return None
    ttft = pd.to_numeric(rows["first_token_latency"], errors="coerce")
    e2e = pd.to_numeric(rows["latency"], errors="coerce")

    ev = ev.set_index("task_id")
    joined = rows["task_id"].map(lambda t: t in ev.index)

    out = {"run": os.path.basename(run_dir), "n": len(rows),
           "n_chat": int((rows["class"] == "chat").sum()),
           "n_dr": int((rows["class"] == "deepresearch").sum()),
           "n_swe": int((rows["class"] == "swe").sum())}

    # coverage: of the requests that must be judged on a per-token rule and
    # actually produced a stream, how many were found in tbt_events
    need = rows["class"].isin(["chat", "deepresearch"]) & \
        (~rows["rejected"]) & (~rows["errored"]) & (~rows["cutoff"])
    out["n_need_stream"] = int(need.sum())
    out["n_have_stream"] = int((need & joined).sum())
    out["stream_coverage"] = out["n_have_stream"] / out["n_need_stream"] \
        if out["n_need_stream"] else np.nan

    for rule in RULE_COLS:
        col = f"{prefix}_{rule}"
        if rule == "r1m":
            ok = pd.Series(np.nan, index=rows.index)   # everything falls back
        elif col in ev.columns:
            ok = rows["task_id"].map(ev[col])
        else:
            ok = pd.Series(np.nan, index=rows.index)
        miss = pd.Series(False, index=rows.index)
        for cname, spec in SLO_RULES.items():
            m = rows["class"] == cname
            if "e2e" in spec:
                miss.loc[m] = e2e[m] > spec["e2e"]
            else:
                pt = ok[m]
                # A request judged on a per-token rule whose stream was not
                # found falls back to the recorded mean, and the count of those
                # is reported so the fallback cannot hide. The fallback is
                # written as `not (itl > budget)` and not as `itl <= budget`
                # because load_run treats a request with no measurable
                # inter-token interval -- one output token or none -- as not
                # having broken the per-token half of the rule; it is caught by
                # the missing-first-token test instead. Writing it the other way
                # turns those requests into violations and the r1m column stops
                # reproducing the published figure.
                fallback = ~(pd.to_numeric(rows.loc[m, "itl_ms"],
                                           errors="coerce") > spec["tbt"])
                pt = pt.where(pt.notna(), fallback)
                miss.loc[m] = (ttft[m] > spec["ttft"]) | (~pt.astype(bool))
        miss = miss | (ttft.isna() & ~rows["cutoff"])
        v_off = miss | rows["rejected"] | rows["errored"]
        r = rows.copy()
        # The names have to be exactly these two: attain() decides whether a
        # rejected request leaves the denominator by comparing the column name
        # against "violate_served", so a differently named column would compute
        # the offered figure and label it admitted.
        r["violate_offered"] = v_off
        r["violate_served"] = miss
        met = (~v_off) & (~r["cutoff"])
        out[f"{rule}_all"] = 100.0 * met.sum() / len(r)
        out[f"{rule}_offered"] = attain(r, "violate_offered")
        out[f"{rule}_admitted"] = attain(r, "violate_served")
        for cname, tag in (("chat", "chat"), ("deepresearch", "dr"),
                           ("swe", "swe")):
            sub = r[r["class"] == cname]
            out[f"{rule}_{tag}_all"] = 100.0 * (
                (~sub["violate_offered"]) & (~sub["cutoff"])).sum() / len(sub) \
                if len(sub) else np.nan
            out[f"{rule}_{tag}_offered"] = attain(sub, "violate_offered")
            out[f"{rule}_{tag}_admitted"] = attain(sub, "violate_served")
    return out


# --------------------------------------------------------------------- driver
def manifest():
    m = pd.read_csv(os.path.join(PINNED, "manifest.tsv"), sep="\t")
    m = m[m["run"] >= SINCE]
    return m


def _one(args):
    run, arm, rate, variants = args
    try:
        ev = scan_run(run, want_variants=variants)
    except Exception as e:                                       # noqa: BLE001
        return {"run": run, "arm": arm, "rate": rate, "error": str(e)}
    cot = ev.attrs.get("chunk_over_token", np.nan)
    recs = {}
    for prefix in ("raw", "deb"):
        s = score_run(os.path.join(RESULTS, run), ev, prefix)
        if s is None:
            return {"run": run, "arm": arm, "rate": rate,
                    "error": "no usable metrics rows"}
        s.update({"arm": arm, "rate": rate, "prefix": prefix,
                  "chunk_over_token": cot,
                  "sub_tau_frac": float(ev["sub_tau_frac"].mean())})
        recs[prefix] = s
    # per-request material for tasks 3 and 4, kept aggregated so nothing large
    # is written
    det = {"run": run, "arm": arm, "rate": rate,
           "chunk_over_token": cot}
    for pfx in ("raw", "deb"):
        for cls in ("chat", "deepresearch"):
            sub = ev[ev["class"] == cls]
            if sub.empty:
                continue
            ff = sub[f"{pfx}_r2_first_frac"].dropna()
            tag = f"{pfx}_{'chat' if cls == 'chat' else 'dr'}"
            det[f"{tag}_n"] = len(sub)
            det[f"{tag}_nfail2"] = len(ff)
            for q in (10, 25, 50, 75, 90):
                det[f"{tag}_ffq{q}"] = float(np.percentile(ff, q)) \
                    if len(ff) else np.nan
            det[f"{tag}_ff_lt10"] = float((ff < 0.10).mean()) if len(ff) else np.nan
            det[f"{tag}_ff_gt50"] = float((ff > 0.50).mean()) if len(ff) else np.nan
            # Deciles of the position, so the shape can be read rather than
            # summarised: a queueing failure piles into the first bin and a
            # mid-flight interference failure spreads over the rest.
            if len(ff):
                h, _ = np.histogram(ff, bins=np.linspace(0.0, 1.0, 11))
                for k in range(10):
                    det[f"{tag}_ffdec{k}"] = float(h[k] / len(ff))
            det[f"{tag}_ffidx_q50"] = float(sub[f"{pfx}_r2_first_idx"].median())
            fail2 = sub[sub[f"{pfx}_r2_first_frac"].notna()]
            det[f"{tag}_ff_ttftover"] = float(
                fail2[f"{pfx}_r2_first_ttft_over"].mean()) if len(fail2) else np.nan
            mg = sub[f"{pfx}_max_gap_ms"]
            for q in (10, 25, 50, 75, 90, 99):
                det[f"{tag}_maxgap_q{q}"] = float(np.percentile(mg, q))
            # The share of requests whose worst gap is inside the class budget
            # is rule 4 restated, and the share inside two and five budgets says
            # how far outside the rest sit.
            pb = SLO_RULES["chat" if cls == "chat" else "deepresearch"]["tbt"]
            for k in (1, 2, 5, 10):
                det[f"{tag}_maxgap_le{k}b"] = float((mg <= k * pb).mean())
            det[f"{tag}_medgap_q50"] = float(sub[f"{pfx}_med_gap_ms"].median())
            det[f"{tag}_n5x_mean"] = float(sub[f"{pfx}_n_gap_gt5x"].mean())
            det[f"{tag}_n5x_q50"] = float(sub[f"{pfx}_n_gap_gt5x"].median())
            det[f"{tag}_n5x_q90"] = float(np.percentile(sub[f"{pfx}_n_gap_gt5x"], 90))
            det[f"{tag}_frac_any5x"] = float((sub[f"{pfx}_n_gap_gt5x"] > 0).mean())
            det[f"{tag}_frac5x"] = float(sub[f"{pfx}_frac_gap_gt5x"].mean())
    for k in ("chr", "est"):
        for rule in ("r1", "r2", "r3", "r4"):
            c = f"{k}_{rule}"
            if c in ev.columns:
                det[f"var_{c}_pass"] = float(ev[c].dropna().mean())
                det[f"var_raw_{rule}_pass_sameset"] = float(
                    ev.loc[ev[c].notna(), f"raw_{rule}"].mean())
    # logical relation between rules 1 and 2, measured rather than assumed
    for pfx in ("raw", "deb"):
        a1 = ev[f"{pfx}_r1"].astype(bool)
        a2 = ev[f"{pfx}_r2"].astype(bool)
        det[f"{pfx}_pass1_fail2"] = float((a1 & ~a2).mean())
        det[f"{pfx}_fail1_pass2"] = float((~a1 & a2).mean())
    return {"score": list(recs.values()), "detail": det}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="score", choices=["score", "probe"])
    ap.add_argument("--arms", default=None)
    ap.add_argument("--rates", default=None)
    ap.add_argument("--variants", action="store_true",
                    help="also compute the two alternative token allocations")
    ap.add_argument("--workers", type=int, default=10)
    ap.add_argument("--tag", default="lit")
    a = ap.parse_args()

    m = manifest()
    if a.arms:
        m = m[m["arm"].isin(a.arms.split(","))]
    if a.rates:
        want = [float(x) for x in a.rates.split(",")]
        m = m[m["req_per_s"].isin(want)]
    jobs = [(r.run, r.arm, r.req_per_s, a.variants)
            for r in m.itertuples()]
    print(f"{len(jobs)} runs, {a.workers} workers, variants={a.variants}")

    scores, details, errs = [], [], []
    with ProcessPoolExecutor(max_workers=a.workers) as ex:
        for i, res in enumerate(ex.map(_one, jobs), 1):
            if "error" in res:
                errs.append(res)
                print(f"  [{i}/{len(jobs)}] FAILED {res['run']}: {res['error']}")
                continue
            scores.extend(res["score"])
            details.append(res["detail"])
            print(f"  [{i}/{len(jobs)}] {res['detail']['run']}")

    os.makedirs(OUTDIR, exist_ok=True)
    sc = pd.DataFrame(scores)
    dt = pd.DataFrame(details)
    sc.to_csv(os.path.join(OUTDIR, f"09_{a.tag}_per_run.csv"), index=False)
    dt.to_csv(os.path.join(OUTDIR, f"09_{a.tag}_detail.csv"), index=False)
    if errs:
        pd.DataFrame(errs).to_csv(os.path.join(OUTDIR, f"09_{a.tag}_errors.csv"),
                                  index=False)
    print(f"\nwrote {OUTDIR}/09_{a.tag}_per_run.csv  ({len(sc)} rows)")
    print(f"wrote {OUTDIR}/09_{a.tag}_detail.csv  ({len(dt)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
