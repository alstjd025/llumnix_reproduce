#!/usr/bin/env python3
"""Engine-side test of the accusation's mechanism.

The accusation names a mechanism, not just a correlation: admitting more short
requests makes the decode batch larger, a larger decode batch produces more
tokens per step, and the reported token throughput rises without the scheduler
having done anything useful. That mechanism is checkable on the engine series
because vLLM exports the running batch directly.

For each arm and rate this reports, over the four engines of the fleet:

  running batch      vllm:num_requests_running, p50 and p90 per engine, then
                     the mean over engines and the largest single engine. The
                     mean and the max are both printed because the fleets are
                     not symmetric -- PolyServe partitions classes onto
                     instances, so its fleet mean describes an engine that does
                     not exist.
  waiting queue      vllm:num_requests_waiting, p90. A large batch reached by
                     letting a queue build is a different state from a large
                     batch reached by admitting more work.
  KV occupancy       vllm:kv_cache_usage_perc, p50 and p90. This is the ceiling
                     the batch runs into; a mean hides how often it is touched.
  preemptions        vllm:num_preemptions_total, last minus first, over the run.
  engine tokens      vllm:generation_tokens_total, last minus first, divided by
                     the sampling span. This is the engine's own count of
                     output tokens produced and is independent of the client's
                     metrics.csv, so it is the check on the client-side
                     throughput figure.

Then the arithmetic that decides the mechanism: tokens produced per second
divided by the mean running batch is the per-sequence decode rate. If a policy
gets its throughput from a bigger batch of the same work, its batch is larger
and its per-sequence rate is not. If it gets the same throughput from a smaller
batch, the batch is not the explanation.

Percentiles are per engine over that condition's samples, then combined across
engines and across repeats; ranges are min..max over the two repeats.

    python3 analysis_scripts/request_level/tail2026_batch.py \
        --pinned paper_experiment/static_sweep_2026-08 \
        --results results \
        --out-dir results/aggregate_analysis/tail_2026-08-16
"""
import argparse
import collections
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from tail2026_admitted_set import ARM_LABEL, ARM_ORDER, fmt, agg  # noqa: E402

GAUGES = {"batch": "vllm:num_requests_running",
          "queue": "vllm:num_requests_waiting",
          "kv": "vllm:kv_cache_usage_perc"}
COUNTERS = {"gen_tok": "vllm:generation_tokens_total",
            "prompt_tok": "vllm:prompt_tokens_total",
            "preempt": "vllm:num_preemptions_total"}


def client_window(pinned_run):
    """The absolute clock window the request-side analysis uses.

    The engine sampler stamps each scrape with the same epoch clock the client
    writes into start_time, which is what makes a matched-window comparison
    possible. It is needed: the client-side rate divides tokens by the ARRIVAL
    span, and an arm that queues heavily keeps generating for minutes after the
    last arrival, so tokens generated during that drain are divided by a window
    that ended before they were produced. Restricting the engine counter to the
    same window removes that.
    """
    p = os.path.join(pinned_run, "metrics.csv")
    if not os.path.isfile(p):
        return None
    df = pd.read_csv(p, usecols=["agent", "start_time", "end_time"],
                     low_memory=False)
    r = df[df["agent"] == "request"].dropna(subset=["start_time"])
    if r.empty:
        return None
    t0 = r["start_time"].min()
    rel_end = min(r["end_time"].max() - t0, r["start_time"].max() - t0)
    return t0 + 60.0, t0 + rel_end - 20.0


def run_engine_stats(run, window=None):
    """Per-engine gauge percentiles and counter deltas, folded to the fleet.

    The sampler writes one JSON object per scrape per engine. A counter is read
    as last minus first over the scrapes that carried it, divided by the span
    between those two scrapes, so a restart in the middle of a run would show
    up as a negative and is dropped rather than silently halving the rate.
    """
    files = sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl")))
    if not files:
        return None
    per = collections.defaultdict(list)
    for f in files:
        g = collections.defaultdict(list)
        c = collections.defaultdict(list)
        with open(f) as fh:
            for line in fh:
                try:
                    d = json.loads(line)
                except Exception:                                    # noqa: BLE001
                    continue
                t = d.get("t")
                if window is not None and t is not None:
                    if t < window[0] or t > window[1]:
                        continue
                for k, v in d.items():
                    if v is None:
                        continue
                    for name, pre in GAUGES.items():
                        if k.startswith(pre):
                            g[name].append(float(v))
                    for name, pre in COUNTERS.items():
                        if k.startswith(pre) and t is not None:
                            c[name].append((float(t), float(v)))
        for name, xs in g.items():
            if not xs:
                continue
            scale = 100.0 if name == "kv" else 1.0
            per[f"{name}_p50"].append(scale * float(np.percentile(xs, 50)))
            per[f"{name}_p90"].append(scale * float(np.percentile(xs, 90)))
            per[f"{name}_mean"].append(scale * float(np.mean(xs)))
        for name, xs in c.items():
            if len(xs) < 2:
                continue
            xs.sort()
            span = xs[-1][0] - xs[0][0]
            delta = xs[-1][1] - xs[0][1]
            if span <= 0 or delta < 0:
                continue
            per[f"{name}_total"].append(delta)
            per[f"{name}_ps"].append(delta / span)
    if not per:
        return None
    out = {}
    for k, xs in per.items():
        if k.endswith("_total") or k.endswith("_ps"):
            out[k] = float(np.sum(xs))          # fleet total
        else:
            out[k + "_fleet"] = float(np.mean(xs))
            out[k + "_max"] = float(np.max(xs))
    out["n_engines"] = len(files)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pinned", default="paper_experiment/static_sweep_2026-08")
    ap.add_argument("--results", default="results")
    ap.add_argument("--out-dir", default="results/aggregate_analysis/tail_2026-08-16")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    man = pd.read_csv(os.path.join(a.pinned, "manifest.tsv"), sep="\t")
    recs, missing = [], []
    for _, m in man.iterrows():
        d = os.path.join(a.results, m["run"])
        win = client_window(os.path.join(a.pinned, "data", m["run"]))
        s = run_engine_stats(d, win)
        if s is None:
            missing.append(m["run"])
            continue
        full = run_engine_stats(d, None) or {}
        s["gen_tok_ps_fullrun"] = full.get("gen_tok_ps", np.nan)
        s["preempt_total_fullrun"] = full.get("preempt_total", np.nan)
        s.update({"run": m["run"], "arm": m["arm"], "rate": float(m["req_per_s"])})
        recs.append(s)
    df = pd.DataFrame(recs)
    if missing:
        print(f"no engine series for {len(missing)} runs: {missing[:5]}")

    # per-sequence decode rate: fleet output tokens per second divided by the
    # fleet's mean concurrently running sequences.
    df["tok_per_seq"] = df["gen_tok_ps"] / (df["batch_mean_fleet"] * df["n_engines"])
    df.to_csv(os.path.join(a.out_dir, "tail2026_engine_per_run.csv"), index=False)

    rates = sorted(df["rate"].unique())
    arms = [x for x in ARM_ORDER if x in set(df["arm"])]
    out = []
    P = out.append
    P("## Task 4 - batch size and the mechanism")

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
                cells.append("-" if sub.empty else
                             " / ".join(fmt(*agg(sub, c), p=p) for c in cols))
            P(f"| {rt:.0f} | " + " | ".join(cells) + " |")

    table("Running batch per engine, p50 / p90, averaged over the four engines",
          ["batch_p50_fleet", "batch_p90_fleet"], p=1)
    table("Running batch on the busiest engine, p50 / p90",
          ["batch_p50_max", "batch_p90_max"], p=1)
    table("Concurrently running requests across the fleet, mean "
          "(sum over the four engines)",
          ["batch_mean_fleet"],
          note="Printed per engine; multiply by four for the fleet.", p=1)
    table("Waiting queue per engine, p90 / KV occupancy p50 / KV occupancy p90 (%)",
          ["queue_p90_fleet", "kv_p50_fleet", "kv_p90_fleet"], p=1)
    table("Engine-reported output tokens per second, fleet total, over the same "
          "analysis window as the request-side tables / over the whole run",
          ["gen_tok_ps", "gen_tok_ps_fullrun"],
          note="The two differ when an arm keeps generating after the last "
               "arrival. The client-side figure divides tokens by the arrival "
               "span, so for a heavily queued arm it charges drain-time tokens "
               "to a window that had already closed; the windowed engine "
               "counter is the one to compare across arms.", p=0)
    table("Engine-reported prompt tokens per second, fleet total",
          ["prompt_tok_ps"], p=0)
    table("Preemptions, fleet total, in the analysis window / over the whole run",
          ["preempt_total", "preempt_total_fullrun"], p=0)
    table("Output tokens per second per running sequence",
          ["tok_per_seq"],
          note="Engine output tokens per second divided by the mean number of "
               "concurrently running sequences on the fleet. A policy whose "
               "throughput comes from a larger batch of the same work has a "
               "larger batch and an unchanged or lower value here.", p=2)

    P("")
    P("**FluidServe against each baseline: batch, engine tokens/s, tokens per "
      "running sequence**")
    P("")
    P("ratio of FluidServe to the baseline, so 1.00 is identical")
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
            parts = []
            for c in ["batch_mean_fleet", "gen_tok_ps", "tok_per_seq"]:
                b = th[c].mean()
                parts.append(f"{us[c].mean() / b:.2f}" if b else "-")
            cells.append(" / ".join(parts))
        P(f"| {rt:.0f} | " + " | ".join(cells) + " |")

    txt = "\n".join(out)
    with open(os.path.join(a.out_dir, "tail2026_engine_tables.md"), "w") as fh:
        fh.write(txt + "\n")
    print(txt)
    print(f"\nwrote {a.out_dir}/tail2026_engine_tables.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
