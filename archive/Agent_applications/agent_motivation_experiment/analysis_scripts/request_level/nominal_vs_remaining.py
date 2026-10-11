#!/usr/bin/env python3
"""Hidden debt / hidden credit: how often does the nominal-budget view of an
instance give a different admit/refuse verdict from the remaining-budget view?

The observable (paper section 3.2). At a window midpoint, an instance's realized
pace (the engine's own inter-token-latency histogram delta over the window) is
compared against two thresholds built from the SAME residents:

  nominal_min    min over resident chat/deepresearch requests of their CLASS
                 budget (50 / 100 ms) -- the best information available to a
                 scheduler that does not track residents individually (llm-d's
                 podMinTPOTSLO is exactly this quantity).
  remaining_min  min over the same residents of what each one still has left
                 per remaining token: (budget*(out-1) - decode_time_spent) /
                 tokens_remaining -- the accounting view, which exists only if
                 residents are tracked individually.

Verdict divergence:
  B  hidden debt    pace <= nominal_min but pace > remaining_min: the nominal
                    view admits while some running request can no longer afford
                    even the current pace (it ran slow and spent its budget).
  A' hidden credit  pace > nominal_min but pace <= min over SAVABLE residents
                    (those whose remaining allowance >= pace): the nominal view
                    refuses while every request that can still be saved could
                    afford this pace. The savable-only min mirrors the policy's
                    exclusion rule in buildFlux; the RAW min is also reported
                    and is ~0 because it is dragged down by the debtors --
                    which is itself the measured justification for exclusion.

Measurement choices:
  runs        hour-trace runs (nonstationary load/mix -- static fixed-rate runs
              sit at steady state where credit and debt barely form; see
              twin_windows README for that negative result)
  residents   admitted, completed with >=2 output tokens, decode phase only
              ([start+ttft, start+e2e]); token progress j(t) linear between
              first and last token; tokens_remaining >= 10 (drops
              near-completion artifacts); swe excluded from both mins (its
              budget form differs across arm configs); chat+dr ~= 92% of
              arrivals
  remaining   uses the ACTUAL final output length (an oracle measurement of
              real credit/debt, not any policy's estimate)
  pace        engine vllm:inter_token_latency histogram delta over the same
              5 s window, >=50 tokens
  caveat      this is a diagnostic reading of one instant, not a replay of
              either policy's full predicate.

Usage: nominal_vs_remaining.py <run_dir> [<run_dir> ...]
"""
import csv, json, sys
from collections import defaultdict

W = 5.0
BUD = {"chat": 50.0, "deepresearch": 100.0}


def class_of(t):
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def hist_at(ser, t):
    lo, hi = 0, len(ser)
    while lo < hi:
        m = (lo + hi) // 2
        if ser[m][0] <= t:
            lo = m + 1
        else:
            hi = m
    return ser[lo - 1] if lo else None


def analyze(run):
    req2port = {}
    with open(f"{run}/analysis/request_engine.csv") as f:
        for row in csv.DictReader(f):
            req2port[(row["task_id"], row["call_index"])] = row["engine_port"]
    itl = defaultdict(list)
    for port in ("8000", "8001", "8002", "8003"):
        try:
            fh = open(f"{run}/server_metrics/engine_{port}.jsonl")
        except FileNotFoundError:
            continue
        with fh:
            for line in fh:
                d = json.loads(line)
                s = c = None
                for k, v in d.items():
                    if k.startswith("vllm:inter_token_latency_seconds_sum"):
                        s = float(v)
                    elif k.startswith("vllm:inter_token_latency_seconds_count"):
                        c = float(v)
                if s is not None and c is not None:
                    itl[port].append((d["t"], s, c))
    reqs = defaultdict(list)
    starts = []
    with open(f"{run}/metrics.csv") as f:
        for row in csv.DictReader(f):
            if row.get("agent") != "request":
                continue
            if row.get("is_rejected", "").strip().lower() in ("true", "1"):
                continue
            try:
                s = float(row["start_time"])
                lat = float(row["latency"])
                ttft = float(row["first_token_latency"])
                out = float(row["output_tokens"])
            except (ValueError, KeyError):
                continue
            if out < 2 or lat <= ttft:
                continue
            starts.append(s)
            cls = class_of(row["task_id"])
            if cls not in BUD:
                continue
            p = req2port.get((row["task_id"], row["call_index"]))
            if p:
                reqs[p].append((s + ttft, s + lat, out - 1, BUD[cls]))
    t0, t1 = min(starts) + 60, max(starts) - 20
    tot = nA = nAx = nB = 0
    for port in itl:
        iser = sorted(itl[port])
        rser = sorted(reqs.get(port, []))
        w = t0
        while w + W <= t1:
            a = hist_at(iser, w)
            b = hist_at(iser, w + W)
            mid = w + W / 2
            res = [r for r in rser if r[0] <= mid < r[1]]
            if a and b and b[2] - a[2] >= 50 and res:
                allows = []
                for (ds, de, ntok, bud) in res:
                    j = ntok * (mid - ds) / (de - ds)
                    rem = ntok - j
                    if rem < 10:
                        continue
                    allows.append(((bud * ntok - (mid - ds) * 1000.0)
                                   / max(rem, 1.0), bud))
                if allows:
                    tot += 1
                    pace = (b[1] - a[1]) / (b[2] - a[2]) * 1000.0
                    nom = min(x[1] for x in allows)
                    remmin = min(x[0] for x in allows)
                    okN = pace <= nom
                    if not okN and pace <= remmin:
                        nA += 1
                    elif okN and pace > remmin:
                        nB += 1
                    sav = [x[0] for x in allows if x[0] >= pace]
                    if not okN and sav and pace <= min(sav):
                        nAx += 1
            w += W
    print(f"{run}\n  {tot} engine-windows | hidden credit raw {100*nA/tot:.1f}% "
          f"| hidden credit savable-only {100*nAx/tot:.1f}% "
          f"| hidden debt {100*nB/tot:.1f}%")


if __name__ == "__main__":
    for r in sys.argv[1:]:
        analyze(r)
