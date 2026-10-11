#!/usr/bin/env python3
"""Paper figure: what the decode phase creates, and what it costs per token,
over the hour on Qwen2.5-72B.

  decode_kv_qwen.pdf   3.335 x 1.95 in, one column, width=\\columnwidth
  decode_kv_qwen_wide.pdf   7.0 x 1.95 in, `figure*`, width=\\textwidth
  decode_kv_qwen.csv   exactly the values drawn

  left axis   the rate at which decoding brings KV cache into existence, in
              GB/s: the engines' `vllm:generation_tokens_total` differenced
              between scrapes and multiplied by the model's per-token KV size,
              summed over the four engines. Repeat 1 is the solid line and the
              filled area, repeat 2 the dashed one.
  right axis  the inter-token latency the engines themselves report, as
              (sum of `vllm:inter_token_latency_seconds_sum`) / (sum of the
              matching `_count`) across the four engines, per second. That is
              the token-weighted mean, so it follows the engine producing the
              tokens rather than giving an idle engine equal weight.

⚠ THIS IS A RATE OF CREATION, NOT AN OCCUPANCY. A request releases its whole
footprint when it finishes, so the area under the curve is the volume created
and destroyed over the hour and not a level that accumulates; the physical pool
is three orders of magnitude smaller. State it as "decode creates KV at X GB/s",
never as "X TB accumulates".

PER-TOKEN KV, AND THE CHECK THAT IT IS THE RIGHT ONE. 2 (K and V) x layers x
KV heads x head dim x dtype bytes. Qwen2.5-72B-Instruct was read from the
config.json the engine actually loads (80 layers, 64 attention heads, 8 KV
heads, hidden 8192 so head dim 128, bfloat16), which gives 327,680 B = 320 KiB
per token -- identical to Llama-3.1-70B, so the two models' curves are in the
same units and can be read against each other. The exploratory version of this
figure carried a warning that the Qwen shape was assumed; it is no longer
assumed.

DATA. EXP-113 (2026-09-03), the FluidServe arm on the hour-long mix-shift trace,
both repeats: `260903_0302_exp113r1_fsv3capgnofrct75_shiftq` and
`260903_1031_exp113r2_fsv3capgnofrct75_shiftq`. ⚠ THE QWEN TRACE IS THINNED to
64% of the arrivals of the Llama one because the model is slower on this
hardware, so a number here may not be compared with the Llama panel of
`results/aggregate_analysis/decode_kv_growth/decode_vs_itl.png` without saying
so. Both series are smoothed with a 20 s box filter, as in that figure, and time
is measured from the first arrival.

    python3 paper_figures/fig_decode_kv_qwen.py
"""
import argparse
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "dkg", os.path.join(ROOT, "analysis_scripts", "request_level",
                        "decode_kv_growth.py"))
DKG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(DKG)

# One run per arm, repeat 1 of two. ONE run and not both: the two repeats of an
# arm lie on top of each other here (the FluidServe pair differed by 0.2% in the
# hour's total, 14,544 against 14,515 GB), so a second line adds a dashed curve
# nobody can separate from the first and takes the eye off the two quantities
# the figure is about.
ARM_RUNS = {
    "vllm": ("vLLM-router", "260903_0702_exp113r1_vllmcachet75_shiftq"),
    "fs": ("FluidServe", "260903_0302_exp113r1_fsv3capgnofrct75_shiftq"),
}
# Verified against the config.json the engine loads; see the docstring.
BYTES_PER_TOKEN = 2 * 80 * 8 * 128 * 2
SMOOTH_S = 20
XMAX = 60.0
# One column wide, the same 3.335 in and the same 8 pt type as
# `azure_rate_and_mix.pdf` beside it, so the two set the same size on the page.
# A 65% canvas was tried on 2026-09-08 and reverted: at 2.17 in the 8 pt type
# takes so much of the canvas that the plotted area falls to 1.4 x 0.75 in and
# the rotated right-hand axis label no longer fits beside its axis. The figure
# is made smaller by giving it less HEIGHT, not less width.
SCALE = 1.0
KV_COLOR = "#1f77b4"
ITL_COLOR = "#d62728"
FIG_H = 1.72


def collect(name):
    """(kv, itl, total GB) for one run, each series as (minutes, values).

    Step by step, because every one of them changes what the curve means:

      1. each engine's `server_metrics/engine_*.jsonl` holds one Prometheus
         scrape per line, with the scrape's unix time in `t`;
      2. `counter` interpolates each engine's CUMULATIVE
         `vllm:generation_tokens_total` onto a common 1 s grid, over the window
         all four engines cover, and sums them -- interpolating before summing
         because the four are scraped at their own times;
      3. differencing consecutive seconds turns the counter into tokens per
         second, with negatives clipped to zero so an engine restart reads as a
         gap rather than as a large negative rate;
      4. multiplying by the model's per-token KV size gives bytes per second and
         dividing by 1e9 gives GB/s;
      5. a 20 s box filter smooths it, the same filter the exploratory figure
         uses, so the two can be compared;
      6. the x axis is measured from the FIRST ARRIVAL rather than from the
         first scrape, so minute 0 is when load starts, and only 0-60 min is
         kept.

    The inter-token latency is built from the same scrapes: per engine, the
    per-second increments of `inter_token_latency_seconds_sum` and of its
    `_count`, summed across engines, and then the ratio of the two sums. That is
    the token-weighted mean, so it follows the engine actually producing tokens;
    averaging the four engines' means would give an idle engine's handful of
    tokens the same weight as a busy engine's thousands.

    The hour's total is taken from the counter's endpoints, not by integrating
    the smoothed curve, so the smoothing cannot move it.
    """
    run = os.path.join(ROOT, "results", name)
    t, cum = DKG.counter(run, "vllm:generation_tokens_total")
    if t is None:
        print(f"!! {name}: no generation counter", file=sys.stderr)
        return None, None, None
    at, _ = DKG.arrivals(run)
    t0 = at[0] if at is not None else t[0]
    d = np.diff(cum, prepend=cum[0])
    d[d < 0] = 0.0
    gbps = DKG.smooth(d * BYTES_PER_TOKEN / 1e9, SMOOTH_S)
    m = (t - t0) / 60.0
    keep = (m >= 0) & (m <= XMAX)
    kv = (m[keep], gbps[keep])
    total = (cum[-1] - cum[0]) * BYTES_PER_TOKEN / 1e9

    itl = None
    it, iv = DKG.itl_ms(run)
    if it is not None:
        sm = DKG.smooth(np.nan_to_num(iv), SMOOTH_S)
        mi = (it - t0) / 60.0
        ok = (mi >= 0) & (mi <= XMAX) & (sm > 0)
        itl = (mi[ok], sm[ok])
    return kv, itl, total


def write_csv(kv, itl, path):
    rows = []
    for a, b in zip(*kv):
        rows.append({"series": "decode_kv_gb_s", "minute": float(a),
                     "value": float(b)})
    if itl is not None:
        for a, b in zip(*itl):
            rows.append({"series": "inter_token_latency_ms",
                         "minute": float(a), "value": float(b)})
    df = pd.DataFrame(rows)
    df["smoothing_s"] = SMOOTH_S
    df["bytes_per_token"] = BYTES_PER_TOKEN
    df.to_csv(path, index=False, float_format="%.5f")
    print(f"wrote {path}  ({len(df)} rows)")


def _fitting_ylabel(fig, ax, candidates):
    """The first label whose set width fits beside its axis, measured.

    Returns the last candidate if none fits, so the caller always gets a label.
    """
    fig.canvas.draw()
    avail = ax.get_window_extent().height
    for text in candidates:
        t = ax.set_ylabel(text)
        fig.canvas.draw()
        # ⚠ THE LABEL IS ROTATED, so the length of the set type is the extent's
        # HEIGHT and its width is the thickness of one line. Comparing the width
        # instead says every label fits and the long one is then drawn off the
        # top of the canvas, which is what happened on 2026-09-08.
        if t.get_window_extent().height <= avail:
            return text
    return candidates[-1]


def _one_row_legend(fig, handles, width, band_frac, sizes=(8, 7.5, 7, 6.5, 6,
                                                           5.5, 5)):
    """Place the legend in a single row, at the largest size that fits.

    The legend is drawn at each size and its rendered width compared with the
    canvas; matplotlib will happily let a one-row legend run off the page, so
    the fit has to be measured.
    """
    labels = [h.get_label() for h in handles]
    chosen = None
    for size in sizes:
        leg = fig.legend(handles, labels, loc="lower center", ncol=len(handles),
                         bbox_to_anchor=(0.5, 1 - band_frac), frameon=False,
                         fontsize=size, columnspacing=0.8, handlelength=1.2,
                         handletextpad=0.3, borderaxespad=0.0)
        fig.canvas.draw()
        w = leg.get_window_extent().width / fig.dpi
        if w <= width * 0.95:
            chosen = size
            break
        leg.remove()
    if chosen is None:
        chosen = sizes[-1]
        fig.legend(handles, labels, loc="lower center", ncol=len(handles),
                   bbox_to_anchor=(0.5, 1 - band_frac), frameon=False,
                   fontsize=chosen, columnspacing=0.6, handlelength=1.0,
                   handletextpad=0.25, borderaxespad=0.0)
    return chosen


def build(kv, itl, out, width, itl_top=None):
    height = FIG_H * (SCALE if width < 3.0 else 1.0)
    with plt.rc_context(ps.STYLE):
        fig, ax = plt.subplots(figsize=(width, height))
        ax2 = ax.twinx()

        m, v = kv
        ax.fill_between(m, 0, v, color=KV_COLOR, alpha=0.30, lw=0)
        ax.plot(m, v, color=KV_COLOR, lw=1.0, zorder=3)
        if itl is not None:
            ax2.plot(*itl, color=ITL_COLOR, lw=0.8, alpha=0.9, zorder=2)

        ax.set_xlim(0, XMAX)
        ax.set_xticks([0, 15, 30, 45, 60])
        ax.set_xlabel("Time (minutes)", labelpad=1.5)
        ax.set_ylim(0, None)
        ax.set_ylabel("KV-cache (GB/s)", color=KV_COLOR, labelpad=1.5)
        ax.tick_params(axis="y", colors=KV_COLOR)
        # The latency axis can be pinned by the caller so that two arms drawn
        # from this script are on one scale; an arm whose latency runs away
        # would otherwise compress an arm whose latency does not.
        ax2.set_ylim(0, itl_top)
        # The label is chosen by MEASURING it rather than by guessing: a
        # rotated y label longer than its axis is tall gets clipped, and
        # "Time between Token, TBT (ms)" at 8 pt is about an inch of type. The
        # long form is used where it fits and the short one where it does not;
        # the legend spells the full name either way.
        # Measured after the layout is settled, because the axes height the
        # label has to fit beside is not known before it.
        ylab2 = "TBT (ms)"
        ax2.set_ylabel(ylab2, color=ITL_COLOR, labelpad=2.0)
        ax2.tick_params(axis="y", colors=ITL_COLOR)
        # Both spines stay: each carries an axis that a curve is read against,
        # and the colours say which curve belongs to which.
        ax.spines["top"].set_visible(False)
        ax2.spines["top"].set_visible(False)
        ax.spines["left"].set_color(KV_COLOR)
        ax.spines["right"].set_color(ITL_COLOR)
        ax2.spines["left"].set_color(KV_COLOR)
        ax2.spines["right"].set_color(ITL_COLOR)
        ax.grid(axis="both", **ps.GRID)
        ax.set_axisbelow(True)

        handles = [Line2D([], [], color=KV_COLOR, lw=1.0,
                          label="KV-cache Generation by Decode"),
                   Line2D([], [], color=ITL_COLOR, lw=0.8,
                          label="Time between Token (TBT)")]
        # ONE ROW, and the font is the largest that fits in one: the two names
        # are long, and a legend that wraps to a second row takes height from
        # the panel and reads as two groups rather than one key. The size is
        # found by measuring the drawn legend rather than assumed, so the same
        # code works on both canvases, and it is printed.
        band = 0.175
        h = fig.get_size_inches()[1]
        size = _one_row_legend(fig, handles, width, band / h)
        fig.tight_layout(rect=(0, 0, 1, 1 - (band - 0.015) / h), pad=0.3)
        ylab2 = _fitting_ylabel(fig, ax2,
                                ["Time between Token, TBT (ms)", "TBT (ms)"])
        ax2.set_ylabel(ylab2, color=ITL_COLOR, labelpad=2.0)
        fig.tight_layout(rect=(0, 0, 1, 1 - (band - 0.015) / h), pad=0.3)
        print(f"    legend on one row at {size:.1f} pt; y label "
              f"{ylab2!r}")
        ps.save(fig, out)


def report(label, kv, itl, total):
    v = kv[1][kv[1] > 0]
    print(f"{label}: decode created {total:,.0f} GB over the hour, "
          f"p10 {np.percentile(v, 10):.2f} / p90 {np.percentile(v, 90):.2f} GB/s "
          f"(p90/p10 = {np.percentile(v, 90) / np.percentile(v, 10):.1f}x)")
    if itl is not None:
        w = itl[1]
        print(f"{'':{len(label)}}  inter-token latency p10 {np.percentile(w, 10):.0f} / "
              f"p50 {np.percentile(w, 50):.0f} / p90 {np.percentile(w, 90):.0f} ms, "
              f"max {w.max():.0f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", choices=sorted(ARM_RUNS), nargs="*",
                    default=sorted(ARM_RUNS))
    ap.add_argument("--itl-top", type=float, default=None,
                    help="pin the latency axis so two arms share one scale")
    a = ap.parse_args()

    made = {}
    for key in a.arm:
        label, name = ARM_RUNS[key]
        kv, itl, total = collect(name)
        if kv is None:
            continue
        report(label, kv, itl, total)
        made[key] = (kv, itl)
        pdf = os.path.join(HERE, f"decode_kv_qwen_{key}.pdf")
        build(kv, itl, pdf, ps.COL_W * SCALE, a.itl_top)
        build(kv, itl, os.path.join(HERE, f"decode_kv_qwen_{key}_wide.pdf"),
              ps.TEXT_W, a.itl_top)
        write_csv(kv, itl, pdf[:-4] + ".csv")
    if len(made) > 1 and a.itl_top is None:
        print("\n⚠ the arms were drawn on their own latency axes; pass "
              "--itl-top to put them on one scale before showing them together")
    return 0


if __name__ == "__main__":
    sys.exit(main())
