#!/usr/bin/env python3
"""The within-request tail against llm-d, and the load that produces it.

Two panels at the full text width.

  (a) chat's token-gap distribution as a survival curve: the share of gaps
      exceeding each threshold, three arms. The crossing is the point -- llm-d is
      smoother in the middle of the distribution and rougher in the far tail than
      FluidServe with the class preference on.
  (b) the same runs cut into one-minute windows and plotted against the OUTPUT
      TOKENS PER SECOND that arm was actually sustaining in that window. llm-d
      rejects 45.6% of arrivals against our 18.9% and delivers 27.8% fewer tokens
      over the hour, so its tail is measured on a lighter fleet; putting delivered
      throughput on the x axis is what makes the two comparable.

This figure exists because the two prior passes on this dimension each had an
error that a figure caught and a table did not, and the second pass recorded
"no figure was produced" as its own largest weakness.

Data: EXP-93, six one-hour runs with byte-identical arrival times (FluidServe
preference on / off / llm-d, two repeats each). Per-request gap statistics were
extracted from tbt_events.jsonl by the analysis in
results/aggregate_analysis/exp93_affinity/a3_tail.md and a6_tail_vs_llmd.md and
are reused here rather than re-extracted, so the figure and those tables cannot
disagree by extraction.

CAPTION MUST STATE: two repeats per arm; chat only; completed requests only
(rejected, errored and run-boundary cutoffs removed); that llm-d's rejection rate
is 45.6% against FluidServe's 18.9%, which is the reason panel (b) exists; and
that the load-matched comparison behind panel (b) rests on 13 of 60 windows.
"""
import glob, os, pickle, sys
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
import numpy as np, pandas as pd          # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__)); EXPDIR = os.path.dirname(HERE)
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(EXPDIR, "analysis_scripts", "request_level"))
from paper_style import STYLE, TEXT_W, GRID, ARM_COLOR, save     # noqa: E402
from exp22_fluidserve import load_run                            # noqa: E402

GAPDIR = {"a3": "/home/nxclab/tools/a3_pass2", "a6": "/home/nxclab/tools/a6_llmd"}
ARMS = [
    ("FluidServe (pref. on)",  ARM_COLOR["fluidserve"], "-",
     [("260822_2141_exp93r1_fspfx_shift", "a3"), ("260823_0007_exp93br1_fspfx_shift", "a3")]),
    ("FluidServe (pref. off)", "#7fbfe0", "--",
     [("260823_0721_exp93nr1_fsnoaff_shift", "a3"), ("260823_0834_exp93nbr1_fsnoaff_shift", "a3")]),
    ("llm-d",                  ARM_COLOR["llmd"], "-.",
     [("260822_2303_exp93r1_llmdslo_shift", "a6"), ("260823_0129_exp93br1_llmdslo_shift", "a6")]),
]
# b0..b5 are per-request COUNTS of gaps in 0-25, 25-50, 50-100, 100-200, 200-500, >500 ms.
EDGES = [25, 50, 100, 200, 500]
KEY = ["task_id", "iteration", "call_index"]


def load(run, where):
    r = load_run(os.path.join(EXPDIR, "results", run))
    for c in KEY:
        r[c] = r[c].astype(str) if c == "task_id" else pd.to_numeric(r[c], errors="coerce")
    g = pd.read_csv(os.path.join(GAPDIR[where], run + ".csv"))
    g["task_id"] = g["task_id"].astype(str)
    m = r.merge(g.drop(columns=["start_time", "output_tokens"]), on=KEY, how="left")
    assert len(m) == len(r), f"{run}: join changed the row count"
    # Completed only: a rejected request has no stream, and a run-boundary cutoff
    # has a truncated one, so neither describes how a served request was served.
    return m[~m["rejected"] & ~m["errored"] & ~m["cutoff"]].copy()


def panel_survival(ax, data):
    for label, color, ls, runs in ARMS:
        ys = []
        for m in data[label]:
            c = m[m["class"] == "chat"]
            b = np.array([c[f"b{i}"].sum() for i in range(6)], float)
            tot = b.sum()
            # share of gaps ABOVE each edge = total over total, never a mean of
            # per-request ratios: a long request would otherwise count the same
            # as a short one.
            ys.append([100.0 * b[i + 1:].sum() / tot for i in range(5)])
        ys = np.array(ys)
        ax.plot(EDGES, ys.mean(0), ls=ls, color=color, marker="o", ms=3.0, lw=1.1, label=label)
        ax.fill_between(EDGES, ys.min(0), ys.max(0), color=color, alpha=0.18, lw=0)
    ax.set_xscale("log"); ax.set_yscale("log")
    ax.set_xticks(EDGES); ax.set_xticklabels([str(e) for e in EDGES])
    ax.set_xlabel("token gap threshold (ms)")
    ax.set_ylabel("share of chat gaps above it (%)")
    ax.yaxis.grid(True, **GRID)
    ax.legend(loc="lower left", handlelength=1.8, borderaxespad=0.3)
    ax.set_title("(a) chat token-gap survival", fontsize=8, pad=4)


def panel_load(ax, data):
    for label, color, ls, runs in ARMS:
        pts = []
        for m in data[label]:
            m = m.copy(); m["w"] = (m["rel"] // 60).astype(int)
            for _, g in m.groupby("w"):
                ch = g[(g["class"] == "chat") & g["p90"].notna()]
                if len(ch) < 20:
                    continue
                tps = pd.to_numeric(g["output_tokens"], errors="coerce").sum() / 60.0
                pts.append((tps, ch["p90"].median()))
        if not pts:
            continue
        pts = np.array(pts)
        ax.scatter(pts[:, 0], pts[:, 1], s=4, color=color, alpha=0.30, lw=0)
        # Binned median, so the eye reads the relationship rather than the cloud.
        edges = np.arange(0, pts[:, 0].max() + 2000, 2000)
        xs, ys = [], []
        for lo, hi in zip(edges[:-1], edges[1:]):
            s = pts[(pts[:, 0] >= lo) & (pts[:, 0] < hi)]
            if len(s) >= 3:
                xs.append(s[:, 0].mean()); ys.append(np.median(s[:, 1]))
        ax.plot(xs, ys, ls=ls, color=color, lw=1.4, marker="s", ms=3.0, label=label)
    ax.axhline(50, color="#555555", lw=0.6, ls=(0, (3, 2)))
    ax.text(0.99, 51, "chat budget 50 ms", transform=ax.get_yaxis_transform(),
            ha="right", va="bottom", fontsize=7, color="#555555")
    ax.set_xlabel("output tokens/s the arm was delivering in that minute")
    ax.set_ylabel("median chat request's own p90 gap (ms)")
    ax.yaxis.grid(True, **GRID)
    ax.set_title("(b) the same tail against the load that produced it", fontsize=8, pad=4)


def main():
    plt.rcParams.update(STYLE)
    data = {}
    for label, _, _, runs in ARMS:
        data[label] = [load(r, w) for r, w in runs]
        n = sum(len(x) for x in data[label])
        print(f"  {label:26s} {len(runs)} runs, {n:,} completed requests")
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_W, 2.55))
    panel_survival(axes[0], data)
    panel_load(axes[1], data)
    fig.tight_layout(pad=0.5, w_pad=1.6)
    save(fig, os.path.join(HERE, "tail_vs_llmd.pdf"))


if __name__ == "__main__":
    main()
