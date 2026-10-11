#!/usr/bin/env python3
"""Paper figure: the share of conversation requests in the Azure trace window.

  azure_conv_share.pdf   3.335 x 1.40 in, `figure`, width=\\columnwidth
  azure_conv_share.csv   the values drawn

The same four-day window and the same ten-minute bins as `azure_mix_only.pdf`
(`fig_azure_rate_and_mix.py`, imported rather than recomputed), drawn as the
plain share instead of the deviation from the average (2026-09-14, at the
author's request):

    conversation share = conversation requests / (conversation + code) x 100

The dashed rule is the window's average share, weighted by requests (all
conversation requests in the window over all requests), which is the reference
`azure_mix_only.pdf` measures its deviation from. The lowest and highest
ten-minute shares and the average are written above the 100% line.

⚠ AZURE HAS TWO REQUEST TYPES, conversation and code, and nothing that
corresponds to our deep research class. This is evidence that the composition of
a real deployment's arrivals moves, not evidence for the three-class mix the
experiments use.
⚠ The two source traces cover different calendar weeks and are summed by index,
so the hour of day lines up and the calendar date does not.

    python3 paper_figures/fig_azure_conv_share.py
"""
import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "az", os.path.join(HERE, "fig_azure_rate_and_mix.py"))
AZ = importlib.util.module_from_spec(spec)
spec.loader.exec_module(AZ)

COLOR, ALPHA = AZ.C_MIX_ONLY, AZ.C_MIX_ONLY_ALPHA    # the colour of azure_mix_only.pdf
FIG_H = AZ.FIG_H_MIX


ZOOM_MIN = 60          # length of the zoomed window, minutes
SMOOTH_MIN = 5        # smoothing used only to CHOOSE the window


def most_volatile_hour(sh_min):
    """Start minute of the 60-minute window with the largest range of the share.

    The share is taken per minute and smoothed over 5 minutes before the range
    is measured, so that a single noisy minute cannot select the window; the
    panel then draws the unsmoothed per-minute share. At the smallest per-minute
    count in the window (1,568 requests) one minute's share has a binomial
    standard deviation of about 1.2 points, which is the noise floor of that
    line. The largest start-to-end change over an hour picks almost the same
    stretch (minutes 1001-1061 against 1010-1069), so the choice does not hinge
    on the definition.
    """
    s5 = pd.Series(sh_min).rolling(SMOOTH_MIN, center=True,
                                   min_periods=SMOOTH_MIN).mean()
    r = s5.rolling(ZOOM_MIN, min_periods=ZOOM_MIN)
    rng = (r.max() - r.min()).to_numpy()
    end = int(np.nanargmax(rng))
    return end - ZOOM_MIN + 1, float(rng[end])


def zoom_figure(conv, code, hb, share, avg, hours_end):
    """The four-day share beside the one hour in which it moved most.

      azure_conv_share_zoom.pdf   3.335 x 1.40 in, `figure`
    """
    sh_min = 100.0 * conv / (conv + code)
    start, rng = most_volatile_hour(sh_min)
    seg = sh_min[start:start + ZOOM_MIN]
    t_min = np.arange(ZOOM_MIN)
    print(f"zoom: minutes {start}-{start + ZOOM_MIN} of the window "
          f"(hour {start / 60:.2f}-{(start + ZOOM_MIN) / 60:.2f}); smoothed range "
          f"{rng:.1f} points; per-minute share {seg.min():.1f}-{seg.max():.1f}%, "
          f"{seg[0]:.1f}% at its start and {seg[-1]:.1f}% at its end")
    style = {**AZ.STYLE, "xtick.labelsize": 7, "ytick.labelsize": 7,
             "axes.labelsize": 7.5}
    with plt.rc_context(style):
        fig, (a0, a1) = plt.subplots(
            1, 2, figsize=(ps.COL_W, FIG_H),
            gridspec_kw=dict(width_ratios=[2.2, 1.0]), sharey=True)
        a0.plot(hb, share, color=COLOR, lw=0.6)
        a0.fill_between(hb, share, color=COLOR, alpha=ALPHA, lw=0)
        a0.axhline(avg, color="#333333", lw=0.6, ls="--")
        # the hour drawn on the right, marked where it sits in the four days
        a0.axvspan(start / 60.0, (start + ZOOM_MIN) / 60.0, color="#555555",
                   alpha=0.25, lw=0, zorder=0)
        a0.set_xlim(0, hours_end)
        a0.set_xticks(np.arange(0, hours_end + 1, 24))
        a0.set_xlabel("Time (hours)", labelpad=1.5)
        a0.set_ylabel("Conversation\nshare (%)")
        a0.set_ylim(0, 100)
        a0.set_yticks([0, 25, 50, 75, 100])

        a1.plot(t_min, seg, color=COLOR, lw=0.7)
        a1.fill_between(t_min, seg, color=COLOR, alpha=ALPHA, lw=0)
        a1.axhline(avg, color="#333333", lw=0.6, ls="--")
        a1.set_xlim(0, ZOOM_MIN - 1)
        a1.set_xticks([0, 20, 40, 59])
        a1.set_xticklabels(["0", "20", "40", "60"])
        a1.set_xlabel("Time (minutes)", labelpad=1.5)
        for a in (a0, a1):
            a.grid(axis="both", **AZ.GRID)
            a.set_axisbelow(True)
        fig.tight_layout(pad=0.35, w_pad=0.8)
        AZ.check_ylabel(fig, a0, "conversation share (zoom)")
        rend = fig.canvas.get_renderer()
        fig.canvas.draw()
        gap = (a1.get_tightbbox(rend).x0 - a0.get_tightbbox(rend).x1) / fig.dpi
        print(f"  the two panels' ink is {gap:+.3f} in apart"
              + ("  ⚠ OVERLAP" if gap < 0 else ""))
        ps.save(fig, os.path.join(HERE, "azure_conv_share_zoom.pdf"))
    pd.DataFrame({"minute_of_window": start + t_min,
                  "minute_in_zoom": t_min,
                  "conversation_share_pct": seg,
                  "total_requests": (conv + code)[start:start + ZOOM_MIN]}
                 ).to_csv(os.path.join(HERE, "azure_conv_share_zoom.csv"),
                          index=False, float_format="%.4f")


def main():
    conv, code, _plan = AZ.source_window()
    nb = len(conv) // AZ.BIN
    cb = conv[:nb * AZ.BIN].reshape(nb, AZ.BIN).sum(1)
    kb = code[:nb * AZ.BIN].reshape(nb, AZ.BIN).sum(1)
    share = 100.0 * cb / (cb + kb)
    hb = (np.arange(nb) * AZ.BIN + AZ.BIN / 2) / 60.0
    hours_end = len(conv) / 60.0
    avg = 100.0 * cb.sum() / (cb.sum() + kb.sum())
    print(f"{nb} bins of {AZ.BIN} min over {hours_end / 24:.1f} days")
    print(f"conversation share: min {share.min():.1f}%  median {np.median(share):.1f}%  "
          f"max {share.max():.1f}%  request-weighted average {avg:.1f}%  "
          f"(mean of bins {share.mean():.1f}%)")

    with plt.rc_context(AZ.STYLE):
        fig, ax = plt.subplots(figsize=(ps.COL_W, FIG_H))
        ax.plot(hb, share, color=COLOR, lw=0.6)
        ax.fill_between(hb, share, color=COLOR, alpha=ALPHA, lw=0)
        ax.axhline(avg, color="#333333", lw=0.6, ls="--")
        # NO ARROW. `AZ.span` puts its label 82% of the way up the arrow, which
        # suits the mixture deviation (a low series with a few peaks) and not
        # this one: the conversation share sits near the top for most of the
        # window, and the label landed 15.9 points inside the curve. The range
        # and the average are written in the headroom above 100% instead, and
        # `check_overlap` still measures the result.
        t = ax.text(0.015, 0.965,
                    f"range {share.min():.0f}-{share.max():.0f}%,  "
                    f"dashed: average {avg:.0f}%",
                    transform=ax.transAxes, ha="left", va="top", fontsize=6.5,
                    color="#333333")
        ax.set_ylabel("Conversation\nshare (%)")
        ax.set_ylim(0, 122)
        ax.set_yticks([0, 25, 50, 75, 100])
        ax.set_xlim(0, hours_end)
        ax.set_xticks(np.arange(0, hours_end + 1, 24))
        ax.set_xlabel("Time (hours)")
        ax.grid(axis="both", **AZ.GRID)
        ax.set_axisbelow(True)
        fig.tight_layout(pad=0.35)
        AZ.check_overlap(fig, ax, t, hb, share, "conversation share")
        AZ.check_ylabel(fig, ax, "conversation share")
        ps.save(fig, os.path.join(HERE, "azure_conv_share.pdf"))

    zoom_figure(conv, code, hb, share, avg, hours_end)

    out = os.path.join(HERE, "azure_conv_share.csv")
    pd.DataFrame({"hour_bin_centre": hb, "conversation_share_pct": share,
                  "conversation_requests": cb, "code_requests": kb,
                  "window_average_share_pct": avg,
                  "bin_minutes": AZ.BIN}).to_csv(out, index=False,
                                                 float_format="%.4f")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
