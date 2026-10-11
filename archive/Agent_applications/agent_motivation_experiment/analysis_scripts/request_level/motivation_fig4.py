#!/usr/bin/env python3
"""Motivation figure 4: the two existing answers fail in opposite ways.

Figure 3 said the fleet's capacity depends on which requests are placed on which
engine. There are two established ways to decide that, and each gets it wrong
from a different direction. This figure shows both mechanisms directly, on the
same hour-long trace, from the engines' own reports.

  a static partition assigns classes to engines once, from an estimate of how
      much of each class there will be. Where the estimate is wrong, or where
      the mix later moves, the assignment is wrong AND STAYS WRONG: there is no
      quantity in the system that reacts to a request being on the wrong engine.
      Read column PolyServe: chat, which is 76.9% of the requests, is given one
      instance outright and a share of two more, and that instance holds a mean
      queue of 1,180 while the two it shares sit at 25.6% KV occupancy. Chat is
      served at 195 to 203% of the time per token its rule allows on all three.

  load balancing spreads requests to equalise load, which is the same thing as
      MAXIMISING the mixing: if every engine carries the same load it also
      carries the same class composition. Every engine then holds at least one
      request of the tightest-budget class, so every engine in the fleet has to
      run at that budget, and the classes with looser budgets get an engine
      tuned to someone else's requirement. Read column Llumnix SLO: all four
      engines within a few points of the same composition, all four delivering
      between 44 and 48 ms per token against chat's 50.

Three rows, because any one of them can be explained away on its own:

  A  what each engine was given -- the assignment
  B  what each engine delivered, as a percentage of each class's OWN budget, so
     that three different rules can share one axis and 100% means "exactly at
     the limit" for all of them
  C  what was waiting at each engine -- the queue the router built and cannot
     reach into

FluidServe is not on this figure. The claim being made is about the two existing
families and it is made entirely by their own measurements.

swe's rule is 30 seconds end to end rather than a per-token budget; row B states
it as the 62 ms per token that its mean output length makes equivalent, which is
the same conversion EXP-55 used. It is an approximation for display only and the
scoring everywhere else uses the end-to-end form.

The PolyServe column comes from EXP-57 and the Llumnix SLO column from EXP-54,
because only PolyServe reads --polyserve-tier-decode-tokens and only that arm
was re-measured when the value was corrected on 2026-08-05. Correcting it moved
the partition from two instances for swe and one for deep research to one and
two, and it changed which engine ends up overloaded -- under the stale table the
loaded engines were the ones holding chat and deep research together; under the
corrected one chat has an instance to itself and still cannot keep up. What did
not change is the shape of the failure: an assignment fixed once, one instance
saturated while others are a quarter full, and the largest class served at twice
its allowed time per token.

  python3 motivation_fig4.py <out-dir> [glob]
"""
import glob
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, load_run  # noqa: E402
from exp41_engine_view import attribute_engines, engine_series, ENG_C  # noqa: E402

# The two families, and the order they are drawn. The load-balance arm is not
# here on purpose: it is Llumnix SLO without the latency awareness rather than a
# third way of deciding the assignment, and its EXP-54 run stopped measuring the
# policy at minute 40 when the load generator exhausted its ephemeral ports.
# Each column carries its own run glob. PolyServe's tier length table was
# corrected on 2026-08-05 and that arm alone re-measured as EXP-57, so its
# EXP-54 runs are superseded; Llumnix SLO never reads that flag and keeps its
# EXP-54 runs. Globbing one session for both would have averaged the stale
# PolyServe into the corrected one without saying so.
COLS = [("polyserve", "PolyServe — assign classes to engines once",
         "results/*exp57r*_polyserve_full"),
        ("slo", "Llumnix SLO — equalise load across engines",
         "results/*exp54r[12]_slo_full")]

CLS = [("chat", "#1f77b4", 50.0, "chat  (50 ms/token)"),
       ("deepresearch", "#ff7f0e", 100.0, "deep research  (100 ms/token)"),
       ("swe", "#d62728", 62.0, "swe  (30 s end to end = 62 ms/token)")]


def collect(pattern, only=None):
    """{arm: [(run_dir, joined_requests, engine_series), ...]} over repeats."""
    out = {}
    for arm, _, _ in COLS:
        if only and arm != only:
            continue
        for d in sorted(glob.glob(pattern)):
            if not re.search(rf"_{arm}_full$", os.path.basename(d)):
                continue
            r = load_run(d)
            if r is None or r.empty:
                continue
            if not os.path.exists(os.path.join(d, "analysis", "request_engine.csv")):
                print(f"  no request_engine.csv for {d}; skipped")
                continue
            j, n = attribute_engines(d, r)
            print(f"  {os.path.basename(d)}: {len(j)}/{n} admitted attributed "
                  f"({100.0*len(j)/max(n,1):.1f}%)")
            out.setdefault(arm, []).append((d, j, engine_series(d)))
    return out


def per_engine(runs):
    """Composition, delivered pace as a share of budget, and queue -- per engine.

    Repeats are pooled by taking the mean over runs of each per-engine quantity.
    The engine identities are stable across repeats for these two policies:
    PolyServe assigns by port and Llumnix SLO makes all four alike, so unlike
    FluidServe there is no moving target to average over. That is checked below
    and printed, rather than assumed.
    """
    ports = sorted(ENG_C)
    comp, pace, wait, kv = {}, {}, {}, {}
    for p in ports:
        cs, ps, ws, ks = [], [], [], []
        for _, j, eng in runs:
            e = j[j["engine_port"] == p]
            if e.empty:
                continue
            cs.append({c: 100.0 * (e["class"] == c).mean() for c, _, _, _ in CLS})
            ps.append({c: 100.0 * pd.to_numeric(e[e["class"] == c]["itl_ms"],
                                                errors="coerce").median() / b
                       for c, _, b, _ in CLS})
            if p in eng:
                # The mean rather than the median: a queue that is empty for
                # most of the hour and thousands deep for part of it has a
                # median of zero, which reports the engine as untroubled at the
                # exact times it was the bottleneck.
                ws.append(float(eng[p]["wait"].mean()))
                ks.append(float(eng[p]["kv"].mean()))
        if cs:
            comp[p] = {c: float(np.mean([x[c] for x in cs])) for c, _, _, _ in CLS}
            pace[p] = {c: float(np.nanmean([x[c] for x in ps])) for c, _, _, _ in CLS}
            wait[p] = float(np.mean(ws)) if ws else np.nan
            kv[p] = float(np.mean(ks)) if ks else np.nan
    return comp, pace, wait, kv


def main(out, pattern=None):
    data = {}
    for arm, _, pat in COLS:
        got = collect(pattern.format(arm=arm) if pattern else pat, only=arm)
        if arm in got:
            data[arm] = got[arm]
    cols = [(a, t) for a, t, _ in COLS if a in data]
    if not cols:
        sys.exit(f"no runs matched {pattern}")

    stats = {a: per_engine(data[a]) for a, _ in cols}
    ports = sorted(ENG_C)

    for a, title in cols:
        comp, pace, wait, kv = stats[a]
        print(f"\n{title}   ({len(data[a])} repeat(s))")
        print(f"  {'port':>6}" + "".join(f"{c[:4]+' %':>9}" for c, _, _, _ in CLS)
              + "".join(f"{c[:4]+' pace':>11}" for c, _, _, _ in CLS)
              + f"{'queue':>9}{'KV %':>8}")
        for p in ports:
            if p not in comp:
                continue
            print(f"  {p:>6}"
                  + "".join(f"{comp[p][c]:>9.1f}" for c, _, _, _ in CLS)
                  + "".join(f"{pace[p][c]:>11.1f}" for c, _, _, _ in CLS)
                  + f"{wait[p]:>9.1f}{kv[p]:>8.1f}")
        # A ratio whose denominator is a fraction of a request is arithmetically
        # true and useless to quote, so the values are given instead whenever
        # the shallowest queue is below one request.
        w = [wait[p] for p in ports if p in wait and wait[p] == wait[p]]
        k_ = [kv[p] for p in ports if p in kv and kv[p] == kv[p]]
        if w and min(w) >= 1:
            print(f"  engine queue, deepest / shallowest: {max(w)/min(w):.1f}x")
        elif w:
            print(f"  engine queues: " + ", ".join(f"{x:,.1f}" for x in sorted(w))
                  + "  (a ratio would be division by a fraction of a request)")
        if k_:
            print(f"  KV occupancy across the four engines: "
                  f"{min(k_):.1f}% to {max(k_):.1f}%")

    os.makedirs(out, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(3, len(cols), figsize=(4.9 * len(cols), 6.9),
                               squeeze=False)
        # The caption is five lines and is generated, so its height is not
        # known when the layout is set; top leaves room for the longest it
        # produces rather than for the one it produced today.
        fig.subplots_adjust(left=0.115, right=0.985, top=0.815, bottom=0.105,
                            hspace=0.42, wspace=0.16)
        xs = np.arange(len(ports))
        for j, (a, title) in enumerate(cols):
            comp, pace, wait, kv = stats[a]
            have = [p for p in ports if p in comp]
            hx = np.arange(len(have))

            # ---- A: what each engine was given
            bot = np.zeros(len(have))
            for c, col, _, lab in CLS:
                v = np.array([comp[p][c] for p in have])
                ax[0][j].bar(hx, v, bottom=bot, color=col, width=0.66,
                             label=lab if j == 0 else None)
                for i, (vv, bb) in enumerate(zip(v, bot)):
                    if vv > 11:
                        ax[0][j].annotate(f"{vv:.0f}", (i, bb + vv / 2),
                                          ha="center", va="center", fontsize=6.5,
                                          color="white", weight="bold")
                bot = bot + v
            ax[0][j].set_ylim(0, 100)
            ax[0][j].set_title(title, fontsize=8.5)

            # ---- B: what each engine delivered, against each class's own rule
            w = 0.26
            for i, (c, col, _, _) in enumerate(CLS):
                v = np.array([pace[p][c] for p in have])
                ax[1][j].bar(hx + (i - 1) * w, v, width=w, color=col)
                for xi, vv in zip(hx + (i - 1) * w, v):
                    if vv == vv:
                        # A white patch behind the number: several of these sit
                        # within a few points of the 100% line and the dashes
                        # were running through the digits.
                        ax[1][j].annotate(
                            f"{vv:.0f}", (xi, vv + 3), ha="center", va="bottom",
                            fontsize=6, color=col, zorder=6,
                            bbox=dict(boxstyle="square,pad=0.08", fc="white",
                                      ec="none", alpha=0.85))
            ax[1][j].axhline(100, color="#333333", ls="--", lw=1.0)
            ax[1][j].set_ylim(0, 260)

            # ---- C: what was waiting at each engine, and how full it was
            v = np.array([wait[p] for p in have])
            ax[2][j].bar(hx, np.maximum(v, 0.05),
                         color=[ENG_C[p] for p in have], width=0.66)
            for xi, vv in zip(hx, v):
                ax[2][j].annotate(f"{vv:,.0f}" if vv >= 1 else f"{vv:.1f}",
                                  (xi, max(vv, 0.05) * 1.35), ha="center",
                                  va="bottom", fontsize=6.5, color="#333333")
            ax[2][j].set_yscale("log")
            ax[2][j].set_ylim(0.03, 8000)

            for i in range(3):
                ax[i][j].set_xticks(hx)
                # The bottom row carries the occupancy each engine ran at,
                # because a queue alone cannot distinguish "nothing was offered
                # to this engine" from "this engine kept up with what it was
                # offered", and those are opposite conclusions.
                ax[i][j].set_xticklabels(
                    [f"engine\n{p}" + (f"\nKV {kv[p]:.0f}%" if i == 2 else "")
                     for p in have], fontsize=6.5)
                ax[i][j].grid(axis="y", ls=":", lw=0.7, alpha=0.6)
                ax[i][j].set_axisbelow(True)

        # The row tag lives in the y label rather than inside the panel: row A's
        # bars fill the axes from 0 to 100 and left no free corner for it.
        ax[0][0].set_ylabel("A. what it was given\nshare of its requests (%)")
        ax[1][0].set_ylabel("B. what it delivered\n% of that class's budget")
        ax[2][0].set_ylabel("C. what was waiting there\nrequests, mean over the hour")
        for j in range(len(cols)):
            ax[1][j].annotate("100% is exactly at the limit", (0.99, 1.02),
                              xycoords="axes fraction", ha="right", va="bottom",
                              fontsize=6.5, color="#333333")
            ax[2][j].annotate("log scale", (0.99, 1.02), xycoords="axes fraction",
                              ha="right", va="bottom", fontsize=6.5,
                              color="#333333")
        h, l = ax[0][0].get_legend_handles_labels()
        fig.legend(h, l, loc="lower center", ncol=3, fontsize=7,
                   columnspacing=1.6, bbox_to_anchor=(0.5, 0.005))

        # Computed, not written by hand. The hand-written version described the
        # PolyServe column measured before its tier table was corrected and was
        # still on the figure after that column was redrawn from EXP-57.
        def col(a):
            comp, pace, wait, kv = stats[a]
            ps = [p for p in ports if p in comp]
            chat = [pace[p]["chat"] for p in ps if pace[p]["chat"] == pace[p]["chat"]]
            dr = [pace[p]["deepresearch"] for p in ps
                  if pace[p]["deepresearch"] == pace[p]["deepresearch"]]
            return dict(kv_lo=min(kv[p] for p in ps), kv_hi=max(kv[p] for p in ps),
                        q_hi=max(wait[p] for p in ps),
                        q_lo=min(wait[p] for p in ps),
                        chat_lo=min(chat) if chat else float("nan"),
                        chat_hi=max(chat) if chat else float("nan"),
                        dr_lo=min(dr) if dr else float("nan"))
        lines = ["The two established ways of deciding where a request goes."]
        if "polyserve" in stats:
            c = col("polyserve")
            lines.append(
                f"Left: the assignment is fixed once, so one instance carries a "
                f"mean queue of {c['q_hi']:,.0f} while another sits at "
                f"{c['kv_lo']:.0f}% occupancy,\nand chat is served at "
                f"{c['chat_lo']:.0f}-{c['chat_hi']:.0f}% of the time per token "
                f"its rule allows.")
        if "slo" in stats:
            c = col("slo")
            lines.append(
                f"Right: all four instances get the same mix, so all four are "
                f"held to chat's budget at {c['chat_lo']:.0f}-{c['chat_hi']:.0f}% "
                f"of it,\nwhile deep research gets {c['dr_lo']:.0f}% of a budget "
                f"it could spend in full and every instance sits at "
                f"{c['kv_lo']:.0f}% occupancy.")
        lines.append("Both leave most of the fleet unused, for opposite reasons.")
        fig.suptitle("\n".join(lines), fontsize=8.5, y=0.995)
        p = os.path.join(out, "motivation_two_failures.png")
        fig.savefig(p, dpi=300)
        print(f"\nwrote {p}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1
         else "results/aggregate_analysis/motivation",
         sys.argv[2] if len(sys.argv) > 2 else None)
