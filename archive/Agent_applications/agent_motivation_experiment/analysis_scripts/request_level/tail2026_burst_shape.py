#!/usr/bin/env python3
"""Burst shape of streamed-chunk arrivals, per run.

A "burst" is a maximal run of consecutive chunks whose inter-arrival gap is
below tau.  The chunk that starts it (the one reached by the last gap >= tau)
is the leader; the chunks after it are the followers.  N = 1 + followers is the
number of chunks the transport handed over together.

The test that turns "these gaps look wrong" into "these N tokens were generated
at a normal rate and delivered together":

    span = gap(leader) + sum(internal gaps)

covers exactly N chunks, so span / N must equal the request's ordinary
per-token step if the tokens were produced at a normal rate.  Equivalently the
leader gap alone must be about N * step, because the internal gaps are ~0.

Reference step per request: the median of that request's gaps that are >= tau
and are not the leader of a burst ("isolated" gaps).

Usage:
  tail2026_burst_shape.py RUNDIR [RUNDIR ...] [--tau 5.0] [--max-requests N]
                          [--class chat] [--json OUT]
"""
import argparse
import json
import os
import statistics
import sys
from collections import Counter, defaultdict


def classify(task_id: str) -> str:
    t = task_id or ""
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("dr-") or t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def quant(xs, q):
    if not xs:
        return float("nan")
    xs = sorted(xs)
    if len(xs) == 1:
        return xs[0]
    pos = q * (len(xs) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def analyse_run(path, tau, max_requests, want_class):
    ev_path = os.path.join(path, "tbt_events.jsonl")
    if not os.path.exists(ev_path):
        return None

    n_req = 0
    n_gaps = 0
    n_small = 0
    burst_n = Counter()                 # N -> count of bursts
    chunks_in_burst = 0
    # per burst size: leader_gap / step, span/N / step
    lead_ratio = defaultdict(list)
    spann_ratio = defaultdict(list)
    internal_gaps = []
    lead_gaps_all = []
    chars_in = []                       # delta_chars of follower chunks
    chars_out = []                      # delta_chars of isolated chunks
    step_all = []

    with open(ev_path) as f:
        for line in f:
            if n_req >= max_requests:
                break
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if rec.get("agent") != "request":
                continue
            if want_class and classify(rec.get("task_id", "")) != want_class:
                continue
            evs = rec.get("chunk_events") or []
            if len(evs) < 20:
                continue
            gaps = [e.get("inter_arrival_ms") for e in evs]
            chars = [e.get("delta_chars", 0) for e in evs]
            # gaps[0] is None (first chunk).  Work on indices 1..len-1.
            g = [(i, gaps[i]) for i in range(1, len(gaps)) if gaps[i] is not None]
            if len(g) < 20:
                continue
            n_req += 1

            small = [i for i, v in g if v < tau]
            smallset = set(small)
            n_gaps += len(g)
            n_small += len(small)

            # leaders: index i is a burst leader if gap[i+1] < tau and gap[i] >= tau
            idxs = [i for i, _ in g]
            gapd = dict(g)
            leaders = set()
            for i in idxs:
                if (i + 1) in smallset and i not in smallset:
                    leaders.add(i)
            # isolated gaps: >= tau and not a leader gap
            iso = [gapd[i] for i in idxs if i not in smallset and i not in leaders]
            if len(iso) < 10:
                continue
            step = statistics.median(iso)
            if not (step > 0):
                continue
            step_all.append(step)

            for i in idxs:
                if i in smallset:
                    chars_in.append(chars[i])
                elif i not in leaders:
                    chars_out.append(chars[i])

            # walk bursts
            for lead in sorted(leaders):
                j = lead + 1
                ssum = 0.0
                cnt = 0
                while j in smallset:
                    ssum += gapd[j]
                    internal_gaps.append(gapd[j])
                    cnt += 1
                    j += 1
                N = cnt + 1
                burst_n[N] += 1
                chunks_in_burst += N
                lg = gapd[lead]
                lead_gaps_all.append(lg)
                span = lg + ssum
                if N <= 12:
                    lead_ratio[N].append(lg / step)
                    spann_ratio[N].append((span / N) / step)

    if n_req == 0:
        return None

    tot_bursts = sum(burst_n.values())
    out = {
        "run": os.path.basename(path),
        "tau_ms": tau,
        "requests": n_req,
        "gaps": n_gaps,
        "frac_gap_below_tau": n_small / n_gaps if n_gaps else 0.0,
        "bursts": tot_bursts,
        "chunks_in_bursts": chunks_in_burst,
        "frac_chunks_in_bursts": chunks_in_burst / (n_gaps + n_req) if n_gaps else 0.0,
        "step_median_ms": statistics.median(step_all) if step_all else float("nan"),
        "burst_size_hist": {str(k): v for k, v in sorted(burst_n.items())},
        "burst_size_share": {
            str(k): burst_n[k] / tot_bursts for k in sorted(burst_n)
        } if tot_bursts else {},
        "internal_gap_p50": quant(internal_gaps, 0.50),
        "internal_gap_p90": quant(internal_gaps, 0.90),
        "internal_gap_p99": quant(internal_gaps, 0.99),
        "chars_follower_p50": quant(chars_in, 0.5),
        "chars_isolated_p50": quant(chars_out, 0.5),
        "chars_follower_mean": statistics.fmean(chars_in) if chars_in else float("nan"),
        "chars_isolated_mean": statistics.fmean(chars_out) if chars_out else float("nan"),
        "by_N": {},
    }
    for N in sorted(lead_ratio):
        lr = lead_ratio[N]
        sr = spann_ratio[N]
        out["by_N"][str(N)] = {
            "bursts": len(lr),
            "lead_over_step_p50": quant(lr, 0.5),
            "lead_over_step_p10": quant(lr, 0.1),
            "lead_over_step_p90": quant(lr, 0.9),
            "spanN_over_step_p50": quant(sr, 0.5),
            "spanN_over_step_p10": quant(sr, 0.1),
            "spanN_over_step_p90": quant(sr, 0.9),
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rundirs", nargs="+")
    ap.add_argument("--tau", type=float, default=5.0)
    ap.add_argument("--max-requests", type=int, default=400)
    ap.add_argument("--class", dest="cls", default="chat")
    ap.add_argument("--json", default=None)
    a = ap.parse_args()

    results = []
    for d in a.rundirs:
        r = analyse_run(d, a.tau, a.max_requests, a.cls)
        if r is None:
            print(f"# no data: {d}", file=sys.stderr)
            continue
        results.append(r)
        print(f"\n== {r['run']}  tau={a.tau}  class={a.cls}  reqs={r['requests']}")
        print(f"   gaps={r['gaps']}  below tau={r['frac_gap_below_tau']:.4f}  "
              f"bursts={r['bursts']}  chunks in bursts={r['frac_chunks_in_bursts']:.4f}")
        print(f"   step(median of isolated gaps)={r['step_median_ms']:.2f} ms   "
              f"internal gap p50/p90/p99={r['internal_gap_p50']:.3f}/"
              f"{r['internal_gap_p90']:.3f}/{r['internal_gap_p99']:.3f} ms")
        print(f"   delta_chars follower p50/mean={r['chars_follower_p50']:.1f}/"
              f"{r['chars_follower_mean']:.2f}   isolated p50/mean="
              f"{r['chars_isolated_p50']:.1f}/{r['chars_isolated_mean']:.2f}")
        if r["burst_size_share"]:
            sh = "  ".join(f"N={k}:{v*100:.1f}%" for k, v in
                           list(r["burst_size_share"].items())[:8])
            print(f"   burst size share: {sh}")
        print("     N  bursts   lead/step p10/p50/p90        (span/N)/step p10/p50/p90")
        for N, v in list(r["by_N"].items())[:8]:
            print(f"   {N:>3}  {v['bursts']:>6}   "
                  f"{v['lead_over_step_p10']:.2f}/{v['lead_over_step_p50']:.2f}/"
                  f"{v['lead_over_step_p90']:.2f}"
                  f"        {v['spanN_over_step_p10']:.2f}/"
                  f"{v['spanN_over_step_p50']:.2f}/{v['spanN_over_step_p90']:.2f}")

    if a.json:
        with open(a.json, "w") as f:
            json.dump(results, f, indent=1)
        print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
