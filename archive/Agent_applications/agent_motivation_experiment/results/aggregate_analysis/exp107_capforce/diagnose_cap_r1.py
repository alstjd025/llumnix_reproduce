#!/usr/bin/env python3
"""Why fsv3cap rep 1 loses: three windowed views from the run's own files.

  1. per-class ADMITTED attainment in 3-minute windows, cap vs control --
     locates the dip the author saw at minutes 15-30 and names the class.
  2. the cap's own state: per-tier lambda, raw demand-derived limit BEFORE
     the scarcity rescale (reconstructed), the published limit, and the gate
     count -- shows when the rescale, not the demand, is what binds.
  3. admitted swe misses: what their end-to-end time was spent on (wait to
     first token vs decode), and which instances served them.

All disk-only. Windows are by ARRIVAL time, 180 s.
"""
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.abspath(os.path.join(HERE, "..", ".."))
CAP = os.path.join(RESULTS, "260827_0703_exp107r1_fsv3cap_shift")
CTRL = [os.path.join(RESULTS, "260826_0705_exp104r1_fsv3_shift"),
        os.path.join(RESULTS, "260826_0931_exp104r2_fsv3_shift")]
WIN = 180.0


def classify(t):
    t = str(t)
    return "chat" if t.startswith("sg-") else (
        "deepresearch" if t.startswith("sa-") else "swe")


def load(run):
    df = pd.read_csv(os.path.join(run, "metrics.csv"), low_memory=False)
    df = df[df.agent != "job_summary"].copy()
    df["cls"] = df.task_id.map(classify)
    t0 = df.start_time.min()
    df["min"] = (df.start_time - t0) / 60.0
    df["win"] = ((df.start_time - t0) // WIN).astype(int)
    rej = df.is_rejected.astype(bool)
    cut = (df.is_server_terminated.astype(bool) | df.is_timeout.astype(bool)
           | df.is_job_timeout.astype(bool)) & ~rej
    err = df.is_error.astype(bool) & ~rej & ~cut
    df["state"] = np.where(rej, "rej", np.where(cut | err, "cut", "served"))
    per = (df.latency - df.first_token_latency) / \
        (df.output_tokens - 1).clip(lower=1) * 1e3
    met = pd.Series(False, index=df.index)
    for cls, (tb, kb) in {"chat": (5.0, 50.0), "deepresearch": (10.0, 100.0)}.items():
        m = (df.cls == cls) & (df.state == "served")
        met.loc[m] = (df.first_token_latency[m] <= tb) & \
            ((df.output_tokens[m] < 2) | (per[m] <= kb))
    m = (df.cls == "swe") & (df.state == "served")
    met.loc[m] = df.latency[m] <= 30.0
    df["met"] = met
    return df


def admitted_windows(df):
    out = {}
    for cls, g in df[df.state == "served"].groupby("cls"):
        w = g.groupby("win").agg(att=("met", "mean"), n=("met", "size"))
        out[cls] = (w.index * WIN / 60.0, 100 * w.att.values, w.n.values)
    return out


def main():
    cap = load(CAP)
    ctrls = [load(c) for c in CTRL]

    # ---- 1. windowed admitted attainment -------------------------------
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    for ax, cls in zip(axes, ["chat", "deepresearch", "swe"]):
        for i, cdf in enumerate(ctrls):
            x, y, _ = admitted_windows(cdf)[cls]
            ax.plot(x, y, color="#1f77b4", alpha=0.55 if i else 0.9, lw=1.2,
                    label=f"control rep {i+1}" if True else None)
        x, y, _ = admitted_windows(cap)[cls]
        ax.plot(x, y, color="#9467bd", lw=1.8, label="+instance cap r1")
        ax.axhline(95, color="k", ls=":", lw=0.8)
        for b in (15, 30, 45):
            ax.axvline(b, color="gray", ls="--", lw=0.6)
        ax.set_ylabel(f"{cls}\nadmitted att (%)")
        ax.set_ylim(0, 105)
        if cls == "chat":
            ax.legend(fontsize=7, ncol=3)
    axes[-1].set_xlabel("minute (segment bounds dashed; 95% dotted)")
    fig.suptitle("fsv3cap r1: admitted attainment per 3-min window vs control")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "diag_admitted_windows.png"), dpi=140)

    # ---- 2. cap state series -------------------------------------------
    rows = []
    with open(os.path.join(CAP, "server_metrics", "scheduler.jsonl")) as f:
        for line in f:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = rec.get("t")
            for k, v in rec.items():
                if isinstance(v, (int, float)) and "instcap_" in k:
                    name, tier = k.split("|")
                    rows.append((t, name.split("instcap_")[1], tier.split("=")[1], v))
    s = pd.DataFrame(rows, columns=["t", "series", "tier", "v"])
    t0 = s.t.min()
    s["min"] = (s.t - t0) / 60.0
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True)
    tiers = [("50", "chat", "#1f77b4"), ("100", "deepresearch", "#ff7f0e"),
             ("25", "swe", "#2ca02c")]
    for ax, (tier, name, color) in zip(axes, tiers):
        g = s[s.tier == tier]
        lam = g[g.series == "lambda"]
        lim = g[g.series == "limit"]
        cnt = g[g.series == "gate_count"]
        ax.plot(lam["min"], lam.v, color=color, lw=1.0, label="lambda (req/s)")
        ax.step(lim["min"], lim.v, color="k", lw=1.4, label="published limit")
        ax.step(cnt["min"], cnt.v, color="gray", lw=1.0, ls="--", label="gate count")
        ax.set_ylabel(name)
        for b in (15, 30, 45):
            ax.axvline(b, color="gray", ls="--", lw=0.6)
        if tier == "50":
            ax.legend(fontsize=7, ncol=3)
    axes[-1].set_xlabel("minute")
    fig.suptitle("fsv3cap r1: per-tier arrival estimate, published limit, gate count")
    fig.tight_layout()
    fig.savefig(os.path.join(HERE, "diag_cap_state.png"), dpi=140)

    # ---- 3. what admitted swe misses spent their time on ----------------
    print("=== swe admitted misses, by segment (cap r1 vs control r1) ===")
    for label, df in [("cap r1", cap)] + [(f"ctrl r{i+1}", c) for i, c in enumerate(ctrls)]:
        g = df[(df.cls == "swe") & (df.state == "served")].copy()
        g["seg"] = np.clip((g["min"] // 15).astype(int), 0, 3)
        for seg, gg in g.groupby("seg"):
            miss = gg[~gg.met]
            if len(gg) == 0:
                continue
            ttft_med = miss.first_token_latency.median() if len(miss) else float("nan")
            dec_med = (miss.latency - miss.first_token_latency).median() if len(miss) else float("nan")
            print(f"  {label:8s} s{seg}: served {len(gg):5d}  missed {len(miss):5d} "
                  f"({100*len(miss)/len(gg):5.1f}%)  miss median: ttft {ttft_med:6.1f}s "
                  f"decode {dec_med:6.1f}s  out_tok {miss.output_tokens.median() if len(miss) else float('nan'):6.0f}")

    # engine placement of missed swe (cap r1 only)
    re_path = os.path.join(CAP, "analysis", "request_engine.csv")
    if os.path.isfile(re_path):
        re = pd.read_csv(re_path)
        g = cap[(cap.cls == "swe") & (cap.state == "served")]
        if "task_id" in re.columns and "engine" in re.columns:
            j = g.merge(re[["task_id", "call_index", "engine"]].drop_duplicates(),
                        on=["task_id", "call_index"], how="left")
            print("\n=== cap r1: served swe by engine, miss rate ===")
            for eng, gg in j.groupby(j.engine.fillna("unknown")):
                print(f"  engine {eng}: served {len(gg):5d}  missed {100*(~gg.met).mean():5.1f}%")

    print("\nwrote diag_admitted_windows.png, diag_cap_state.png")


if __name__ == "__main__":
    main()
