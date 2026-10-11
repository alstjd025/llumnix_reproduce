#!/usr/bin/env python3
"""EXP-16 one-figure summary: the 3 factors of decode step time.

Panel 1 (KV, main)     : decode-only step time vs batch KV, several rates
                         overlaid -> they collapse onto one curve => step time
                         is set by KV occupancy, rate-independent. (~30 ms/Mtok)
Panel 2 (count, weak)  : from the decoupling runs (chat=short ctx, swe=long ctx)
                         interval vs decode batch size. chat has HIGH count but
                         LOW interval, swe the opposite => count is a weak
                         secondary factor (regression a_KV ~5x b_count).
Panel 3 (prefill)      : mean prefill time / request vs rate (vLLM counter) —
                         cheap until the saturation knee, then explodes.
Panel 4 (why panel 3)  : the two saturation mechanisms behind panel 3 — prefix
                         cache-hit rate collapsing and preemptions switching on
                         as KV fills. (T_schedule stays ~2ms throughout: ruled out.)

Sources: sweep runs (*exp16_instr_rpm_*) for panels 1/3/4, decouple runs
(*exp16dec_*) for panel 2. All from existing results — no new experiment.
"""
import glob
import json
import os
import re

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt

import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from plot_exp16_perstep import load_steps, busy_window  # noqa: E402
from plot_exp16_saturation_mechanism import collect as sat_collect  # noqa: E402

PAPER_STYLE = {
    "font.family": "serif", "font.serif": ["DejaVu Serif", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 8.5,
    "axes.linewidth": 0.75, "legend.fontsize": 7, "legend.frameon": False,
    "xtick.direction": "in", "ytick.direction": "in", "lines.markersize": 4.0,
}
KV_BINS = np.array([0, .5, 1, 1.5, 2, 2.5, 3, 3.5, 4, 4.5, 5, 6])


def decode_only(path):
    w = busy_window(load_steps(path))
    return w[w["prefill_tokens_step"] == 0]


def sweep_runs():
    out = {}
    for d in sorted(glob.glob("results/*exp16_instr_rpm_*"),
                    key=lambda x: int(re.search(r"rpm_(\d+)", x).group(1))):
        rps = int(re.search(r"rpm_(\d+)", os.path.basename(d)).group(1)) // 60
        f = os.path.join(d, "server_metrics", "sched_steps.jsonl")
        if os.path.exists(f):
            out[rps] = decode_only(f)
    return out


def decouple_runs():
    out = {}
    for d in sorted(glob.glob("results/*exp16dec_*")):
        m = re.search(r"exp16dec_([a-z]+)_rpm_(\d+)", os.path.basename(d))
        f = os.path.join(d, "server_metrics", "sched_steps.jsonl")
        if m and os.path.exists(f):
            out.setdefault(m.group(1), []).append(decode_only(f))
    return {k: pd.concat(v, ignore_index=True) for k, v in out.items()}


def binned(ax, x, y, color, label, bins=None, nb=12):
    d = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(d) < 10:
        return
    if bins is not None:
        q = pd.cut(d["x"], bins)
    else:
        q = pd.qcut(d["x"], min(nb, d["x"].nunique()), duplicates="drop")
    g = d.groupby(q, observed=True).agg(x=("x", "mean"), y=("y", "median"))
    ax.plot(g["x"], g["y"], "o-", color=color, label=label, zorder=5)


def main():
    out_dir = "figs/exp16_sweep"
    os.makedirs(out_dir, exist_ok=True)
    sw = sweep_runs()
    dc = decouple_runs()
    sat = sat_collect("results")
    rates = sorted(sat)

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(2, 2, figsize=(9.6, 7.0))

        # Panel 1: KV (main) — collapse across rates
        cmap = plt.cm.viridis(np.linspace(0, .85, len([6, 13, 22, 30, 40])))
        for rps, c in zip([6, 13, 22, 30, 40], cmap):
            if rps in sw:
                binned(ax[0, 0], sw[rps]["kv_tokens"] / 1e6,
                       sw[rps]["interval_ms"], c, f"{rps} req/s", bins=KV_BINS)
        ax[0, 0].set_xlabel("batch KV occupancy (Mtok)")
        ax[0, 0].set_ylabel("decode step time p50 (ms)")
        ax[0, 0].set_title("Factor 1: KV cache size — MAIN driver\n"
                           "(all rates collapse → set by KV, not rate; ~30 ms/Mtok)")
        ax[0, 0].legend(title="offered rate", ncol=2)

        # Panel 2: batch count (weak) — decouple
        for wl, col in (("chat", "#1f77b4"), ("swe", "#d62728")):
            if wl in dc:
                lbl = f"{wl} ({'short' if wl=='chat' else 'long'} ctx)"
                binned(ax[0, 1], dc[wl]["n_decode"], dc[wl]["interval_ms"],
                       col, lbl)
        ax[0, 1].set_xlabel("decode batch size (# requests)")
        ax[0, 1].set_ylabel("decode step time p50 (ms)")
        ax[0, 1].set_title("Factor 2: batch count — WEAK secondary\n"
                           "(chat: many reqs yet fast; regression a_KV ≈ 5× b_count)")
        ax[0, 1].legend()

        # Panel 3: prefill cost vs rate
        pf = [sat[r]["prefill_ms"] for r in rates]
        ax[1, 0].axvspan(24, 52, color="#d62728", alpha=0.06)
        ax[1, 0].plot(rates, pf, "o-", color="#d62728")
        for r, y in zip(rates, pf):
            ax[1, 0].annotate(f"{y:.0f}", (r, y), fontsize=6,
                              textcoords="offset points", xytext=(0, 5))
        ax[1, 0].set_xlabel("offered rate (req/s)")
        ax[1, 0].set_ylabel("mean prefill time / request (ms)")
        ax[1, 0].set_title("Factor 3: prefill contention — cheap, then EXPLODES\n"
                           "(vLLM counter; flat ~150ms → 829ms past the knee)")

        # Panel 4: why panel 3 — the two saturation mechanisms
        kv = [sat[r]["kv"] for r in rates]
        hit = [sat[r]["hit"] for r in rates]
        pre = [sat[r]["preempt"] for r in rates]
        ax[1, 1].plot(rates, kv, "o-", color="#9467bd", label="KV occupancy %")
        ax[1, 1].plot(rates, hit, "s-", color="#1f77b4",
                      label="prefix-cache hit %")
        ax[1, 1].set_ylabel("percent"); ax[1, 1].set_ylim(0, 100)
        ax[1, 1].set_xlabel("offered rate (req/s)")
        axb = ax[1, 1].twinx()
        axb.plot(rates, pre, "^--", color="#d62728", label="preemptions/s")
        axb.set_ylabel("preemptions / s", color="#d62728")
        axb.tick_params(axis="y", colors="#d62728")
        h1, l1 = ax[1, 1].get_legend_handles_labels()
        h2, l2 = axb.get_legend_handles_labels()
        ax[1, 1].legend(h1 + h2, l1 + l2, loc="center left")
        ax[1, 1].set_title("Factor 3 — WHY it explodes: KV fills →\n"
                           "cache evicted (hit 90→60%) + preemption switches on")

        for a in ax.flat:
            a.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        fig.suptitle("What determines decode step time (ITL)  —  T_schedule (4th factor) "
                     "stays ~2ms throughout, ruled out", fontsize=9, y=1.005)
        fig.tight_layout()
        p = os.path.join(out_dir, "exp16_three_factors.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print("wrote:", p)


if __name__ == "__main__":
    main()
