#!/usr/bin/env python3
"""Per-instance class binding on the hour trace, one panel per mix segment.

This is motivation_static45/build_per_engine.py adapted to a trace whose mix
moves. It cannot be applied unchanged, and the reason is a mistake this
repository has already made once: pooling a whole run to describe where a class
sits reads a moving assignment as a spread-out one (EXP-54 recorded "the class
separation does not reproduce" from exactly that, when windowing showed 99-100%
concentration that simply moved between engines).

Here the mix itself moves -- chat runs 93.0, 33.3, 76.9 and 60.0 percent of the
requests across the four segments -- so a bar pooled over the hour describes no
segment. Each segment therefore gets its own column.

Within a segment the assignment is stable, and the figure carries the evidence
rather than asking to be trusted: the whisker on each chat bar is the range of
that engine's chat share over three-minute windows inside the segment. A short
whisker means the bar is what the engine held throughout, not an average of
different states.

Output: per_engine_segments.csv and per_instance_binding_segments.{pdf,png}.
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
EXP_ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(EXP_ROOT, "analysis_scripts", "request_level"))
RESULTS = os.path.join(EXP_ROOT, "results")

from exp22_fluidserve import CLASSES, load_run  # noqa: E402

PLAN = os.path.join(EXP_ROOT, "traces", "dynamic", "canonical",
                    "dyn60_shift_m2Am1B_b1045.plan.json")
CHAT_BUDGET_MS = 50.0
SAMPLE_S = 5.0
WIN_S = 180.0          # window for the stability whisker
CLASS_C = {"chat": "#1f77b4", "deepresearch": "#ff7f0e", "swe": "#d62728"}

# Row label carries what the arm IS, not the directory name. The three FluidServe
# rows are a chain: each adds one change to the row above it.
#
# llm-d is attributed from Envoy's access log, not the scheduler's dispatch log.
# It does not pass through the Llumnix scheduler at all, so build_request_engine_map
# finds nothing for it; llmd_engine_map matches client rows to Envoy's
# %UPSTREAM_HOST% by (start time, duration) and reaches 99.94% here.
#
# PolyServe, Llumnix SLO and the vLLM router have never been run on THIS trace,
# so they cannot be added without six more hour-long runs.
RUNS = [
    ("FluidServe\ndeployed (share)",                 1, "260824_0647_exp97r1_fspfx_shift"),
    ("FluidServe\ndeployed (share)",                 2, "260824_0945_exp97br1_fspfx_shift"),
    ("FluidServe\n+ normalisation fixed",            1, "260824_0754_exp97r1_fscount_shift"),
    ("FluidServe\n+ normalisation fixed",            2, "260824_1058_exp97br1_fscount_shift"),
    ("FluidServe\n+ per-inst. corr. + pace cap",     1, "260825_0133_exp98r1_fsboth_shift"),
    ("FluidServe\n+ per-inst. corr. + pace cap",     2, "260825_0246_exp98r2_fsboth_shift"),
    ("llm-d",                                        1, "260822_2303_exp93r1_llmdslo_shift"),
    ("llm-d",                                        2, "260823_0129_exp93br1_llmdslo_shift"),
]
ARM_ORDER = ["FluidServe\ndeployed (share)",
             "FluidServe\n+ normalisation fixed",
             "FluidServe\n+ per-inst. corr. + pace cap",
             "llm-d"]


def segments():
    p = json.load(open(PLAN))
    segs = [(n, float(s["t_range_s"][0]), float(s["t_range_s"][1]),
             100.0 * s["realised_ratio"].get("chat", float("nan")))
            for n, s in p["segments"].items() if n != "warmup"]
    return sorted(segs, key=lambda s: s[1])


def attributed(run_dir):
    """Client records joined to the engine the scheduler dispatched to."""
    r = load_run(run_dir)
    if r is None or r.empty:
        sys.exit(f"{run_dir}: load_run produced no rows")
    g = pd.read_csv(os.path.join(run_dir, "analysis", "request_engine.csv"))
    key = (["request_id"] if "request_id" in r.columns and "request_id" in g.columns
           else ["task_id", "call_index"])
    if key != ["request_id"]:
        r["task_id"] = r["task_id"].astype(str)
        g["task_id"] = g["task_id"].astype(str)
    m = r.merge(g[key + ["engine_port"]].dropna(), on=key, how="left")
    m = m[m["engine_port"].notna()].copy()
    m["engine_port"] = m["engine_port"].astype(float).astype(int)
    m["rel2"] = m["start_time"] - r["start_time"].min()
    return m


def nochat_in(e, ports, t0, t1):
    """% of sample instants inside [t0,t1) at which the engine held no chat.

    Residency is the client's [start, end] on the dispatched engine, the same
    approximation separation_measures.residency_nochat documents, kept per
    engine. `latency` is in seconds, the same unit as rel2.
    """
    end = e["rel2"] + pd.to_numeric(e["latency"], errors="coerce")
    d = pd.DataFrame({"inst": e["engine_port"], "cls": e["class"],
                      "t0": e["rel2"], "t1": end}).dropna()
    grid = np.arange(t0, t1, SAMPLE_S)
    out = {}
    for p in ports:
        g = d[(d["inst"] == p) & (d["cls"] == "chat")]
        if g.empty or grid.size == 0:
            out[p] = 100.0
            continue
        started = np.searchsorted(np.sort(g["t0"].values), grid, side="right")
        ended = np.searchsorted(np.sort(g["t1"].values), grid, side="right")
        out[p] = 100.0 * float(((started - ended) <= 0).mean())
    return out


def collect():
    segs = segments()
    rows = []
    for arm, rep, dname in RUNS:
        run_dir = os.path.join(RESULTS, dname)
        if not os.path.isdir(run_dir):
            print(f"  MISSING {dname} -- skipped")
            continue
        e = attributed(run_dir)
        ports = sorted(e["engine_port"].unique())
        for sname, t0, t1, chatpct in segs:
            w = e[(e["rel2"] >= t0) & (e["rel2"] < t1) & ~e["rejected"] & ~e["cutoff"]]
            nc = nochat_in(e, ports, t0, t1)
            for p in ports:
                g = w[w["engine_port"] == p]
                # Stability of the composition inside the segment: the chat
                # share recomputed on each three-minute window. A wide range
                # means the pooled bar is an average of different states and
                # must not be read as one.
                shares = []
                for a in np.arange(t0, t1, WIN_S):
                    ww = g[(g["rel2"] >= a) & (g["rel2"] < a + WIN_S)]
                    if len(ww) >= 20:
                        shares.append(100.0 * (ww["class"] == "chat").mean())
                itl = pd.to_numeric(g[g["class"] == "chat"]["itl_ms"],
                                    errors="coerce").dropna()
                row = dict(arm=arm, repeat=rep, run=dname, segment=sname,
                           seg_chat_pct=round(chatpct, 1), engine_port=p,
                           n=len(g), nochat_pct=round(nc[p], 2),
                           chat_itl_med_ms=round(float(itl.median()), 2) if len(itl) else np.nan,
                           chat_share_pct=round(100.0 * (g["class"] == "chat").mean(), 2) if len(g) else np.nan,
                           chat_share_win_lo=round(min(shares), 2) if shares else np.nan,
                           chat_share_win_hi=round(max(shares), 2) if shares else np.nan,
                           n_windows=len(shares))
                for c in CLASSES:
                    row[f"n_{c}"] = int((g["class"] == c).sum())
                rows.append(row)
        print(f"  {arm} rep{rep} [{dname}]: {len(e):,} attributed over {len(ports)} engines")
    return pd.DataFrame(rows), segs


def draw(df, segs, out_base):
    arms = [a for a in ARM_ORDER if a in set(df["arm"])]
    ncols = len(segs)
    fig, axes = plt.subplots(2 * len(arms), ncols,
                             figsize=(3.1 * ncols, 2.15 * len(arms)),
                             squeeze=False)
    for ai, arm in enumerate(arms):
        for si, (sname, t0, t1, chatpct) in enumerate(segs):
            sub = df[(df["arm"] == arm) & (df["segment"] == sname)]
            ports = sorted(sub["engine_port"].unique())
            x = np.arange(len(ports))

            # --- composition, repeats summed
            ax = axes[2 * ai][si]
            bottom = np.zeros(len(ports))
            for c in CLASSES:
                v = np.array([sub[sub["engine_port"] == p][f"n_{c}"].sum() for p in ports], float)
                ax.bar(x, v, bottom=bottom, color=CLASS_C[c], width=0.62,
                       label=c if (ai == 0 and si == 0) else None)
                bottom += v
            # whisker: the chat share's window range, drawn on the chat block
            for xi, p in enumerate(ports):
                r = sub[sub["engine_port"] == p]
                tot = float(r[[f"n_{c}" for c in CLASSES]].sum(axis=1).sum())
                lo, hi = r["chat_share_win_lo"].min(), r["chat_share_win_hi"].max()
                if np.isfinite(lo) and np.isfinite(hi) and tot > 0:
                    ax.plot([xi, xi], [tot * lo / 100.0, tot * hi / 100.0],
                            color="k", lw=1.1, solid_capstyle="butt", zorder=5)
                nc = r["nochat_pct"].mean()
                if np.isfinite(nc):
                    ax.text(xi, bottom[xi], f"{nc:.0f}", ha="center", va="bottom",
                            fontsize=6.0)
            ax.set_xticks(x); ax.set_xticklabels([str(p)[-1] for p in ports], fontsize=7)
            ax.tick_params(labelsize=7)
            ax.set_ylim(0, bottom.max() * 1.20 if bottom.max() else 1)
            if si == 0:
                ax.set_ylabel(f"{arm}\n(requests)", fontsize=6.6)
            if ai == 0:
                ax.set_title(f"{sname}  (chat {chatpct:.0f}%)", fontsize=8)

            # --- chat ITL, one point per repeat
            ax2 = axes[2 * ai + 1][si]
            ax2.axhline(CHAT_BUDGET_MS, color="k", ls="--", lw=0.8)
            for xi, p in enumerate(ports):
                r = sub[sub["engine_port"] == p]
                for _, rr in r.iterrows():
                    if np.isfinite(rr["chat_itl_med_ms"]):
                        ax2.plot(xi, rr["chat_itl_med_ms"], "o", ms=4.2,
                                 color=CLASS_C["chat"], alpha=0.85)
            ax2.set_xticks(x); ax2.set_xticklabels([str(p)[-1] for p in ports], fontsize=7)
            ax2.set_xlim(-0.6, len(ports) - 0.4)
            ax2.set_ylim(38, 56)
            ax2.tick_params(labelsize=7)
            if si == 0:
                ax2.set_ylabel("chat ITL\nmedian (ms)", fontsize=7)
            if ai == len(arms) - 1:
                ax2.set_xlabel("engine (last digit of port)", fontsize=7)
    # Everything the reader has to decode is in the legend, not only the colours:
    # the black rule and the number above each bar are quantities too, and a
    # caption-only explanation is one the reader has to hold in their head while
    # looking somewhere else.
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    h = [Patch(facecolor=CLASS_C[c], label=c) for c in CLASSES]
    h += [Line2D([0], [0], color="k", lw=1.4,
                 label="| chat share range over 3-min windows (short = steady)"),
          Line2D([0], [0], color="none",
                 label="12  = % of TIME that engine held no chat"),
          Line2D([0], [0], marker="o", color="none",
                 markerfacecolor=CLASS_C["chat"], markersize=5,
                 label="lower panels: chat median ms/token, one point per repeat")]
    fig.legend(handles=h, fontsize=7, ncol=3, frameon=False,
               loc="upper center", bbox_to_anchor=(0.5, 0.955))
    fig.suptitle(
        "Per-instance class binding by mix segment. One column per segment because the "
        "mix moves -- chat is 93, 33, 77 and 60 percent of arrivals across them -- so a "
        "bar pooled over the hour would describe no segment. Bars are both repeats summed. "
        "Lower panels: chat median inter-token time per repeat against its 50 ms budget "
        "(note the axis starts at 38 ms, not 0). The no-chat number is a share of TIME "
        "while the bar is a count of REQUESTS, so an engine can hold most of the chat "
        "requests and still be chat-free half the time if those requests are short.",
        fontsize=7.2, y=0.998, wrap=True)
    fig.tight_layout(rect=(0, 0, 1, 0.905))
    for ext in ("pdf", "png"):
        fig.savefig(f"{out_base}.{ext}", dpi=200)
    print(f"wrote {out_base}.pdf / .png")


def main():
    df, segs = collect()
    if df.empty:
        sys.exit("no rows")
    out = os.path.join(HERE, "per_engine_segments.csv")
    df.to_csv(out, index=False)
    print(f"wrote {out}")
    draw(df, segs, os.path.join(HERE, "per_instance_binding_segments"))


if __name__ == "__main__":
    main()
