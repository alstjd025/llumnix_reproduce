#!/usr/bin/env python3
"""Paper figure: how long one decode iteration takes on the engine that carries
the tightest per-token budget, for three baselines.

  decode_iteration_cdf.pdf   7.0 x 1.95 in, `figure*`, width=\\textwidth
  decode_iteration_cdf.csv   the curves drawn, same basename

  (a) the one-hour dynamic trace        (b) the static sweep at 35 req/s

WHAT THE FIGURE CLAIMS. The three baselines sit at different points of one axis
-- how conservatively the control plane decides that another request fits -- and
that decision shows up directly in the engine's decode iteration time. Read
against the chat class's 50 ms per-token budget, the vertical line:

  llm-d          decides conservatively, holds a smaller batch, and spends most
                 of its iterations WELL INSIDE the budget: 72.7% of windows on
                 the hour trace and 70.3% at 35 req/s are at or under 50 ms,
                 with medians of 36.5 and 37.3 ms. Capacity is left unused.
  Llumnix SLO    decides aggressively and lands ON the budget on the hour trace
                 (median 53.4 ms, 46.4% of windows inside) and well past it at
                 35 req/s (median 74.4 ms, 32.2% inside).
  PolyServe      partitions by class, and where its chat tier lands depends on
                 how much of the traffic that tier is given.

⚠ POLYSERVE IS NOT ALWAYS BETWEEN THE OTHER TWO, AND THE TWO PANELS DISAGREE
ABOUT IT. At 35 req/s its chat-tier engine sits between them, median 63.3 ms.
On the hour trace it is the WORST of the three by a wide margin -- median
114.2 ms, 2.3x the budget, with only 12.4% of windows inside it -- because the
partition put 57,724 of the hour's 99,240 requests on that one engine while the
deep-research tier engine ran at a median of 31.0 ms. The axis the figure is
about is how conservatively a control plane decides; a static partition does not
sit on that axis at all, and which side of the other two it lands on is decided
by the class mix it was configured for. Say this rather than calling it a middle
point.

⚠ READ THE MEDIAN, NOT THE MEAN, AND THE FIGURE SAYS WHY. Every arm here has a
long right tail, so its mean iteration time is above 50 ms even when most of its
iterations are below: llm-d's median is 37.3 ms and its mean is 58.5 ms in panel
(b). The claim this figure supports is about where the MASS is -- llm-d puts
70.3% of its iterations under the budget and Llumnix SLO puts 26.6% -- and a
sentence about mean iteration time would be false for all three arms. A CDF is
drawn rather than a bar for exactly this reason.

THE QUANTITY, AND THE CHECK THAT IT IS THE ONE IT IS NAMED AFTER. Each point is
one 1-second scrape window of one engine, and its value is
`delta(vllm:inter_token_latency_seconds_sum) / delta(..._count)`: the mean time
between two consecutive output tokens the ENGINE ITSELF recorded in that window.
In continuous batching every running request advances one token per decode step,
so this is the decode iteration time -- but only if nothing else is inserted
between two tokens, and prefill steps are inserted. The independent derivation
`running_batch * delta(t) / delta(generation_tokens)` uses no latency histogram
at all and agrees: medians 37.3 vs 39.1, 74.4 vs 73.1 and 63.3 vs 62.9 ms for
the three arms in panel (b), correlation 0.93 to 0.96 window by window. The two
names therefore denote one quantity here, which is not something to assume --
this repository has twice found two quantities sharing a name.

WHICH ENGINE, AND WHY THAT ONE. An engine's admissible pace is set by the
tightest per-token budget among the requests on it, so the engine to look at is
one holding chat, whose 50 ms budget is the tightest of the three classes. The
share of each engine's requests that were chat, from joining the client's
request ids with the scheduler's dispatch log:

  panel (a), hour trace   PolyServe    engine 8001 is 100.0% chat (the chat tier)
                          Llumnix SLO  all four are 65.3-69.9% chat; 8003 drawn
  panel (b), 35 req/s     PolyServe    8003 is 98.8% chat (8000 is 97.5%)
                          Llumnix SLO  all four are 33.1-73.4% chat; 8003 drawn

⚠ llm-d CANNOT BE ATTRIBUTED THIS WAY and is not. It routes through its own
inference gateway, so our scheduler's dispatch log carries almost nothing for
it. What stands in for the attribution is the SHADED BAND: for every arm it is
the envelope of all four engines' curves, and llm-d's four engines agree within
3.8 ms of median (37.3, 37.4, 38.9, 41.1 ms in panel (b)), so which one is drawn
does not carry the claim. Read the band before reading the difference between
two solid curves.

DATA. Panel (a): the one-hour dynamic trace, EXP-71b (2026-08-08/09), one run per
arm. Panel (b): EXP-108 (2026-08-31) at 35 req/s, repeat 1 of two. NEITHER PANEL
SCORES ANYTHING against a latency rule, so the agent class's promise -- which
differs between these two experiments -- does not enter: the only budget drawn is
chat's 50 ms per output token, and that is identical in every workload
configuration this repository has used.

    python3 paper_figures/fig_decode_iteration_cdf.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
from paper_style import TEXT_W, STYLE, GRID, ARM_COLOR, save  # noqa: E402

CHAT_BUDGET_MS = 50.0
PORTS = [8000, 8001, 8002, 8003]

# (panel, arm label, colour, run directory, the engine drawn and why)
PANELS = [
    ("(a) One-hour dynamic trace", [
        ("PolyServe", ARM_COLOR["polyserve"],
         "260809_0210_exp71br1_polyserve_fullb", 8001),
        ("Llumnix SLO", ARM_COLOR["slo"],
         "260809_0057_exp71br1_slo_fullb", 8003),
        ("llm-d", ARM_COLOR["llmd"],
         "260808_2007_exp71br1_llmdslo_full", 8000),
    ]),
    ("(b) Static sweep, 35 req/s", [
        ("PolyServe", ARM_COLOR["polyserve"],
         "260831_0805_exp108r1_polyservept75_t75fair_rpm_2100", 8003),
        ("Llumnix SLO", ARM_COLOR["slo"],
         "260831_0956_exp108r1_slot75_t75fair_rpm_2100", 8003),
        ("llm-d", ARM_COLOR["llmd"],
         "260831_0548_exp108r1_llmdslot75_t75fair_rpm_2100", 8000),
    ]),
]
GRID_MS = np.logspace(np.log10(12.0), np.log10(600.0), 100)
FIG_H = 1.95


def windows(run, port):
    """Mean inter-token latency in each 1 s scrape window of one engine, in ms.

    A window is kept only when the engine's inter-token counter advanced in it;
    a window in which the engine produced no output token has no iteration time
    to report and is not a zero."""
    path = os.path.join(ROOT, "results", run, "server_metrics",
                        f"engine_{port}.jsonl")
    if not os.path.exists(path):
        return np.array([])
    s, c = [], []
    with open(path) as fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            gs = next((d[k] for k in d
                       if k.startswith("vllm:inter_token_latency_seconds_sum")
                       and d[k] is not None), None)
            gc = next((d[k] for k in d
                       if k.startswith("vllm:inter_token_latency_seconds_count")
                       and d[k] is not None), None)
            if gs is None or gc is None:
                continue
            s.append(float(gs))
            c.append(float(gc))
    if len(s) < 2:
        return np.array([])
    ds, dc = np.diff(np.array(s)), np.diff(np.array(c))
    m = dc > 0
    return 1000.0 * ds[m] / dc[m]


def cdf_on_grid(v):
    return np.searchsorted(np.sort(v), GRID_MS, side="right") / len(v)


def collect():
    rows = []
    for panel, arms in PANELS:
        for label, _, run, drawn in arms:
            for port in PORTS:
                v = windows(run, port)
                if v.size == 0:
                    continue
                rows.append(dict(panel=panel, arm=label, run=run, engine=port,
                                 role="drawn" if port == drawn else "envelope",
                                 v=v))
    return rows


def build(rows, out_pdf):
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, 2, figsize=(TEXT_W, FIG_H), sharey=True)
        handles, labels = [], []
        for i, (panel, arms) in enumerate(PANELS):
            for label, colour, run, drawn in arms:
                sel = [r for r in rows if r["panel"] == panel
                       and r["arm"] == label]
                if not sel:
                    continue
                cur = np.vstack([cdf_on_grid(r["v"]) for r in sel])
                ax[i].fill_between(GRID_MS, cur.min(axis=0), cur.max(axis=0),
                                   color=colour, alpha=0.15, lw=0)
                v = next(r["v"] for r in sel if r["engine"] == drawn)
                h, = ax[i].plot(GRID_MS, cdf_on_grid(v), color=colour, lw=1.3)
                if i == 0:
                    handles.append(h)
                    labels.append(label)
            ax[i].axvline(CHAT_BUDGET_MS, color="#333333", ls="--", lw=0.9)
            ax[i].text(CHAT_BUDGET_MS * 1.06, 0.045, "chat budget\n50 ms/token",
                       fontsize=6.0, color="#333333", va="bottom", ha="left")
            ax[i].set_xscale("log")
            ax[i].set_xlim(12, 600)
            ax[i].set_xticks([20, 50, 100, 200, 500])
            ax[i].set_xticklabels(["20", "50", "100", "200", "500"])
            ax[i].minorticks_off()
            ax[i].set_ylim(0, 1.0)
            ax[i].set_yticks([0, 0.25, 0.5, 0.75, 1.0])
            ax[i].grid(axis="both", **GRID)
            ax[i].set_axisbelow(True)
            ax[i].set_xlabel(f"Decode iteration time (ms)\n{panel}",
                             labelpad=1.5, linespacing=1.6)
        ax[0].set_ylabel("CDF over 1 s windows")
        grey = plt.Line2D([], [], color="#999999", lw=4, alpha=0.35)
        fig.legend(handles + [grey], labels + ["all four engines"],
                   loc="lower center", ncol=4, bbox_to_anchor=(0.5, 0.875),
                   frameon=False, columnspacing=1.2, handlelength=1.6,
                   handletextpad=0.4)
        fig.tight_layout(rect=(0, 0, 1, 0.878), w_pad=2.0, pad=0.25)
        save(fig, out_pdf)


def write_csv(rows, out_csv):
    """The curves themselves, on the grid they are drawn on."""
    out = []
    for r in rows:
        c = cdf_on_grid(r["v"])
        for x, y in zip(GRID_MS, c):
            out.append(dict(panel=r["panel"], arm=r["arm"], run=r["run"],
                            engine=r["engine"], role=r["role"],
                            n_windows=len(r["v"]), itl_ms=x, cdf=y))
    df = pd.DataFrame(out)
    df.to_csv(out_csv, index=False, float_format="%.5f")
    print(f"wrote {out_csv}  ({len(df)} rows = "
          f"{df.groupby(['panel','arm','engine']).ngroups} curves x "
          f"{len(GRID_MS)} grid points)")


def report(rows):
    print(f"\n{'panel':30s} {'arm':12s} {'eng':>5s} {'role':9s} {'n':>5s} "
          f"{'p10':>6s} {'p50':>6s} {'p90':>7s} {'mean':>7s} {'<=50ms %':>9s}")
    for r in rows:
        v = r["v"]
        print(f"{r['panel'][:30]:30s} {r['arm']:12s} {r['engine']:5d} "
              f"{r['role']:9s} {len(v):5d} {np.percentile(v,10):6.1f} "
              f"{np.median(v):6.1f} {np.percentile(v,90):7.1f} {v.mean():7.1f} "
              f"{100*(v<=CHAT_BUDGET_MS).mean():9.1f}")


def main():
    rows = collect()
    if not rows:
        sys.exit("no engine windows found")
    base = os.path.join(HERE, "decode_iteration_cdf")
    build(rows, base + ".pdf")
    write_csv(rows, base + ".csv")
    report(rows)


if __name__ == "__main__":
    main()
