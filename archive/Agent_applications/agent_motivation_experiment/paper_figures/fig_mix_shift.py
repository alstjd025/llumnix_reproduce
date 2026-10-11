#!/usr/bin/env python3
"""The class mix moves capacity, and the arrival rate does not report it.

Two panels at the full text width:

  (a) attainment over the hour, both policies, with the mix segments shaded and
      labelled by chat's share of the requests. The arrival process is identical
      in both arms and in both traces -- the generator was given the same seed --
      so every step in this panel is the mix moving, not the load.
  (b) the same runs re-read as a rate sweep. Each 60 s window is one observation
      at the rate it saw, so the chat-heavy segment and the even one can be
      compared AT THE SAME ARRIVAL RATE. The vertical distance between a solid
      and a dashed line of the same colour is the capacity the mix moved.

Panel (b) is the claim; panel (a) is what makes it legible. The two segments'
median arrival rates differ by 4.7% (23.6 against 24.7 req/s), so the separation
in (b) cannot be load.

Data: EXP-93, four runs (2 arms x 2 repeats) on
traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045. Judgement rules and the full
table: experiments/EXP-93_mix-shift-stress.md sections 4 and 8.

CAPTION MUST STATE: two repeats per arm; the offered denominator (every arrival
counts, a rejection is a violation); that this is a stress test whose mix range
is wider than the Azure data supports; and that the agent class's prompt reuse
is inflated inside the even segment, which makes that segment score BETTER than
realistic reuse would allow and therefore understates the gap.
"""
import glob
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np               # noqa: E402
import pandas as pd              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXPDIR = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(EXPDIR, "analysis_scripts", "request_level"))
from paper_style import STYLE, TEXT_W, GRID, ARM_COLOR, save   # noqa: E402
# Imported, never reimplemented: a correction to the loader has to reach the
# paper figure, and the segment logic has to be the one the tables were made
# with. RATE_EDGES too, so the bins on this figure are the bins in the table.
from exp93_mix_shift import (                                   # noqa: E402
    read_plan, tag_segments, windows, RATE_EDGES,
)
from exp22_fluidserve import load_run, arm_of                    # noqa: E402

PLAN = os.path.join(EXPDIR, "traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.plan.json")
RUNS = {"fspfx": "results/*exp93*_fspfx_shift", "llmdslo": "results/*exp93*_llmdslo_shift"}
LABEL = {"fspfx": "FluidServe", "llmdslo": "llm-d"}
COLOR = {"fspfx": ARM_COLOR["fluidserve"], "llmdslo": ARM_COLOR["llmd"]}


def collect():
    segs = read_plan(PLAN)
    out = {}
    for arm, pat in RUNS.items():
        frames = []
        for d in sorted(glob.glob(os.path.join(EXPDIR, pat))):
            if "PRERUN" in d:          # llm-d predictor warm-up, not a measurement
                continue
            r = load_run(d)
            if r is None or r.empty:
                print(f"  SKIP {os.path.basename(d)}: no usable rows", file=sys.stderr)
                continue
            assert arm_of(d) == arm, f"{d} is not arm {arm}"
            frames.append(windows(tag_segments(r, segs)))
        if not frames:
            sys.exit(f"no runs matched {pat} -- an arm silently missing is how a "
                     f"figure comes out plausible and wrong")
        out[arm] = (pd.concat(frames, ignore_index=True), len(frames))
        print(f"  {arm}: {len(frames)} runs, {len(out[arm][0])} windows")
    return segs, out


def panel_time(ax, segs, data):
    # The offered rate first and underneath, in grey, on its own axis. It is the
    # control: it is byte-identical in both arms and in both traces, so showing
    # it is what lets the reader see that the LEVEL shifts at the segment
    # boundaries while the OSCILLATION inside a segment is the rate.
    rax = ax.twinx()
    any_sl = next(iter(data.values()))[0]
    g = any_sl.groupby(any_sl["t"].round(-1))["offered_rps"].mean().sort_index()
    rax.fill_between(g.index / 60, 0, g.values, color="#000000", alpha=0.07, lw=0)
    rax.set_ylim(0, 130); rax.set_yticks([0, 20, 40])
    rax.set_ylabel("req/s", labelpad=1)
    rax.tick_params(axis="y", direction="in", length=2.5, width=0.6)

    for i, sv in enumerate(segs):
        if i:
            ax.axvline(sv["t0"] / 60, color="#555555", lw=0.5, ls=(0, (3, 2)))
        # Chat's share is the quantity that moves; the segment names would send
        # the reader to look up what m2 and B mean.
        ax.text((sv["t0"] + sv["t1"]) / 120, 104, f"{100*sv['target']['chat']:.0f}",
                ha="center", va="bottom", fontsize=7)
    ax.text(0.0, 104, "chat %:", transform=ax.get_yaxis_transform(),
            ha="right", va="bottom", fontsize=7)

    for arm, (sl, n) in data.items():
        g = sl.groupby(sl["t"].round(-1))["per_request"].mean().sort_index()
        ax.plot(g.index / 60, g.rolling(3, center=True, min_periods=1).mean(),
                color=COLOR[arm], lw=1.0, label=f"{LABEL[arm]} (n={n})", zorder=3)
    ax.set_xlim(0, 61); ax.set_ylim(0, 118)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xlabel("time into the hour (min)")
    ax.set_ylabel("SLO attainment (%)")
    ax.yaxis.grid(True, **GRID)
    ax.set_zorder(rax.get_zorder() + 1); ax.patch.set_visible(False)
    ax.legend(loc="lower left", handlelength=1.3, borderaxespad=0.3)
    ax.set_title("(a) one hour, identical arrivals", fontsize=8, pad=12)


def panel_rate(ax, segs, data):
    heavy = max(segs, key=lambda s: s["target"]["chat"])["name"]
    even = min(segs, key=lambda s: abs(s["target"]["chat"] - 1 / 3))["name"]
    for arm, (sl, _) in data.items():
        sl = sl[sl["pure"] >= 1.0]      # a window holding two mixes observes neither
        for seg, ls, mk in ((heavy, "-", "s"), (even, "--", "o")):
            w = sl[sl["segment"] == seg]
            xs, ys = [], []
            for lo, hi in zip(RATE_EDGES[:-1], RATE_EDGES[1:]):
                b = w[(w.offered_rps >= lo) & (w.offered_rps < hi)]
                if len(b) < 3:
                    continue
                xs.append(b.offered_rps.mean()); ys.append(b.per_request.mean())
            if xs:
                ax.plot(xs, ys, ls=ls, marker=mk, color=COLOR[arm], lw=1.1, ms=3.0)
    ax.set_xlim(9, 41); ax.set_ylim(0, 112)
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.set_xlabel("offered rate (req/s)")
    ax.set_ylabel("SLO attainment (%)")
    ax.yaxis.grid(True, **GRID)
    # The line style carries the segment and the colour carries the policy, so
    # the key names the style once instead of naming four lines.
    ax.plot([], [], ls="-", marker="s", color="#444444", lw=1.1, ms=3.0, label="chat 93%")
    ax.plot([], [], ls="--", marker="o", color="#444444", lw=1.1, ms=3.0, label="even mix")
    ax.legend(loc="lower left", handlelength=1.6, borderaxespad=0.3)
    ax.set_title("(b) the same rate under two mixes", fontsize=8, pad=12)


def main():
    plt.rcParams.update(STYLE)
    segs, data = collect()
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_W, 2.35))
    panel_time(axes[0], segs, data)
    panel_rate(axes[1], segs, data)
    fig.tight_layout(pad=0.5, w_pad=1.6)
    save(fig, os.path.join(HERE, "mix_shift.pdf"))


if __name__ == "__main__":
    main()
