#!/usr/bin/env python3
"""EXP-16 explainer: why prefill cost is 'saturation-conditional'.

Mechanism (from data): every full-chunk step schedules the SAME ~8192 tokens
(the max_num_batched_tokens cap), so nominal work is constant. What changes is
the prefix-cache hit rate. Unsaturated: the prompt's prefix is still cached in
KV, so most of the 8192 is a cache HIT and the runner skips it -> ~20-40ms.
Saturated: the KV pool is jammed with active requests, the cached prefix is
EVICTED, the same prompt MISSES -> the full 8192 is recomputed -> ~600ms
(matches a genuine 8k prefill on 70B TP2). So it's KV pressure evicting the
prefix cache, not prefill competing with a big decode batch.

Left  : schematic — same prefill chunk = cache hit (cheap) vs cache miss (full
        recompute) depending on whether KV has room to keep the cached prefix.
Right : measured full-chunk (>=7500 prefill tok) step interval vs offered rate.
"""
import os
import numpy as np
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

PAPER_STYLE = {
    "font.family": "serif", "font.serif": ["DejaVu Serif", "Liberation Serif"],
    "font.size": 8, "axes.labelsize": 9, "axes.titlesize": 9,
    "axes.linewidth": 0.75, "legend.fontsize": 8, "legend.frameon": False,
    "xtick.direction": "in", "ytick.direction": "in",
}
# measured full-chunk (prefill>=7500) step interval p50 (ms) from the sweep
RATES = [6, 10, 13, 18, 22, 30, 40, 50]
FULLCHUNK_P50 = [20.8, 27.6, 35.7, 43.7, 82.4, 157.3, 578.0, 606.9]
DECODE_BASE = {"lo": 35, "hi": 95}   # decode-only step p50 (unsat / sat)
OUT = os.environ.get("OUT_DIR", "figs/exp16_sweep")


def timeline(ax, y, blocks, colors, labels=None, h=0.6):
    """Draw a horizontal sequence of step-blocks (width == duration ms)."""
    x = 0
    for w, c in zip(blocks, colors):
        ax.add_patch(Rectangle((x, y), w, h, facecolor=c, edgecolor="white",
                               linewidth=0.7))
        x += w
    return x


def main():
    os.makedirs(OUT, exist_ok=True)
    with plt.rc_context(PAPER_STYLE):
        fig, (axL, axR) = plt.subplots(1, 2, figsize=(11, 3.6),
                                       gridspec_kw={"width_ratios": [1.25, 1]})

        # ---- left: schematic timelines (x = milliseconds) ----
        dcol, pcol = "#1f77b4", "#d62728"
        # unsaturated: prefix cached -> the 8192-chunk step is a cache hit
        b1 = [35, 35, 35, 40, 35, 35]
        c1 = [dcol]*3 + [pcol] + [dcol]*2
        timeline(axL, 2.2, b1, c1)
        axL.text(0, 3.05, "KV has room: prefix stays cached (unsaturated)",
                 fontsize=8.5)
        axL.text(35*3+40/2, 2.5, "8192-chunk\n= cache HIT\n~40ms", ha="center",
                 va="bottom", fontsize=7, color=pcol)
        # saturated: prefix evicted -> the same 8192-chunk = full recompute
        b2 = [95, 95, 600, 95]
        c2 = [dcol, dcol, pcol, dcol]
        timeline(axL, 0.4, b2, c2)
        axL.text(0, 1.25, "KV full: cached prefix evicted (saturated)",
                 fontsize=8.5)
        axL.annotate("same 8192-chunk now a\ncache MISS -> full recompute\n~600ms",
                     xy=(95*2+600/2, 0.4), xytext=(300, -0.9),
                     ha="center", fontsize=7, color=pcol,
                     arrowprops=dict(arrowstyle="->", color=pcol, lw=0.8))
        axL.set_xlim(-20, 1000); axL.set_ylim(-1.3, 3.4)
        axL.set_xlabel("wall time (ms)  →  block width = one decode step")
        axL.set_yticks([]);
        for s in ("left", "right", "top"):
            axL.spines[s].set_visible(False)
        # legend
        axL.add_patch(Rectangle((720, 3.0), 30, 0.28, facecolor=dcol))
        axL.text(760, 3.02, "decode step", fontsize=7)
        axL.add_patch(Rectangle((720, 2.6), 30, 0.28, facecolor=pcol))
        axL.text(760, 2.62, "step carrying a\nfull prefill chunk", fontsize=7,
                 va="bottom")
        axL.set_title("The same prefill is cheap with room, a stall when full")

        # ---- right: measured full-chunk interval vs rate ----
        knee = 24
        axR.axvspan(knee, 52, color="#d62728", alpha=0.07)
        axR.text(38, 640, "saturated", color="#d62728", fontsize=8, ha="center")
        axR.text(13, 640, "unsaturated", color="#1f77b4", fontsize=8, ha="center")
        axR.plot(RATES, FULLCHUNK_P50, "o-", color="#d62728",
                 label="full-chunk (≥7500 prefill tok) step")
        axR.axhline(DECODE_BASE["hi"], ls=":", color="#7f7f7f", lw=1)
        axR.text(50, DECODE_BASE["hi"]+8, "decode-only step (~95ms)",
                 ha="right", fontsize=7, color="#7f7f7f")
        axR.set_xlabel("offered rate (req/s)")
        axR.set_ylabel("step interval p50 (ms)")
        axR.set_title("Prefill penalty appears only past the saturation knee")
        axR.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        axR.set_ylim(0, 700)
        axR.legend(loc="upper left")

        fig.tight_layout()
        p = os.path.join(OUT, "exp16_prefill_conditional.png")
        fig.savefig(p, dpi=300, bbox_inches="tight")
        print("wrote:", p)


if __name__ == "__main__":
    main()
