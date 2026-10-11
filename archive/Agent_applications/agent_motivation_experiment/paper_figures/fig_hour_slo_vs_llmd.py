#!/usr/bin/env python3
"""Paper figure: Llumnix SLO against llm-d on the hour-long trace, three bars.

  hour_slo_vs_llmd.pdf   3.335 x 1.55 in, one column, width=\\columnwidth
  hour_slo_vs_llmd.csv   exactly the values drawn

  (a) tokens the engines produced per second
  (b) the share of the requests each control plane ACCEPTED that met their
      deadline
  (c) the share of arrivals it refused

WHY THE THREE PANELS ARE ONE FIGURE. Attainment on an admitted denominator
rewards refusing the work that was going to miss, so (b) alone ranks nothing:
an arm that rejects half its arrivals and serves the rest perfectly reads 100%.
(c) is what makes (b) readable, and (a) says whether the fleet was kept busy
while both happened. The three have to be read together and are therefore
drawn together.

⚠ THE TWO ARMS ANSWER THE SAME PROMISE WITH DIFFERENT MACHINERY. Both are told
the agent class's promise in its per-token form (TTFT 7 s + 75 ms per token)
through `mix_dyn60_shift_m2Am1B_b1045_t75fair.json`, because neither can express
an end-to-end budget. Llumnix SLO predicts the per-token time from an offline
table measured on this hardware; llm-d predicts it with a model it trains online
during the run. The figure compares outcomes, not predictors.

SCORING. Token i of a request is on time if it arrives within
TTFT_SLO + i x TBT_SLO of the send, i counted from zero; the request is on time
if at least 95% of its tokens are. `deadline_ladder_attainment.py` owns the rule
and this script reads its per-request verdicts. Throughput is the engines' own
`vllm:generation_tokens_total`, differenced between scrapes and summed over the
four engines, so it does not depend on what the client recorded.

DATA. EXP-109 (2026-08-31), both repeats of each arm. The bar is the mean of the
two and the error bar is their min and max, which is a floor on the spread
rather than an estimate of it.

    python3 paper_figures/fig_hour_slo_vs_llmd.py
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
from matplotlib.patches import Patch  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


HOURFIG = _load("e109", os.path.join(HERE, "fig_exp109_hour.py"))
VERDICTS = os.path.join(ROOT, "results", "aggregate_analysis", "ladder95",
                        "verdicts")

ARMS = [
    ("Llumnix SLO", ps.ARM_COLOR["slo"],
     ["260831_2346_exp109r1_slot75_shift", "260901_1856_exp109r2_slot75_shift"]),
    ("llm-d", ps.ARM_COLOR["llmd"],
     ["260831_2128_exp109r1_llmdslot75_shift",
      "260901_1637_exp109r2_llmdslot75_shift"]),
]
PANELS = [("thru", "Tokens/s", "(a) Throughput"),
          ("adm", "Attainment (%)", "(b) SLO attainment"),
          # "(c) Rejection", not "rate": centred under the third panel, the
          # longer form still ran off the right edge of the canvas at 6.5 pt,
          # and the y label already says the unit.
          ("rej", "Rejected (%)", "(c) Rejection")]
FIG_H = 1.35


def one_run(name, t_lo=None, t_hi=None):
    """Throughput, admitted attainment and rejection rate for one run.

    `t_lo`/`t_hi` are minutes from the first arrival. A request is placed by its
    ARRIVAL time, so the slice holds the requests that arrived in the interval
    including those that finished after it -- their verdict is still their own.
    Throughput is averaged over the windows whose centre falls in the interval.
    """
    v = pd.read_csv(os.path.join(VERDICTS, name + ".csv"))
    if t_lo is not None:
        rel_min = pd.to_numeric(v["rel"], errors="coerce") / 60.0
        v = v[(rel_min >= t_lo) & (rel_min < t_hi)]
    live = v[~v["cutoff"].astype(bool)]
    served = live[~live["rejected"].astype(bool)]
    out = {"adm": 100.0 * float(served["ladder_ok"].astype(bool).mean()),
           "rej": 100.0 * float(live["rejected"].astype(bool).mean())}
    s = HOURFIG.series(os.path.join(ROOT, "results", name))
    if s is None:
        sys.exit(f"no windows for {name}")
    x = np.asarray(s["x"], dtype=float)
    keep = np.ones(len(x), bool) if t_lo is None else ((x >= t_lo) & (x <= t_hi))
    out["thru"] = float(np.nanmean(np.asarray(s["thru"], dtype=float)[keep]))
    out["n"] = len(live)
    return out


def collect(t_lo=None, t_hi=None):
    data = {}
    for label, _, runs in ARMS:
        per = [one_run(r, t_lo, t_hi) for r in runs]
        data[label] = {k: (float(np.mean([p[k] for p in per])),
                           float(np.min([p[k] for p in per])),
                           float(np.max([p[k] for p in per])))
                       for k in ("thru", "adm", "rej")}
        data[label]["n_runs"] = len(per)
        data[label]["n_requests"] = int(np.sum([p["n"] for p in per]))
    return data


def write_csv(data, path):
    rows = []
    for label, _, runs in ARMS:
        d = data[label]
        row = {"arm": label, "n_repeats": d["n_runs"],
               "n_requests_total": d["n_requests"], "rule": "ladder95",
               "runs": " ".join(runs)}
        for key, name in (("thru", "throughput_tok_s"),
                          ("adm", "slo_attainment_admitted_pct"),
                          ("rej", "rejected_pct")):
            row[name], row[f"{name}_min"], row[f"{name}_max"] = d[key]
        rows.append(row)
    pd.DataFrame(rows).to_csv(path, index=False, float_format="%.3f")
    print(f"wrote {path}  ({len(rows)} rows)")


def build(data, out, width=ps.COL_W, height=FIG_H):
    labels = [l for l, _, _ in ARMS]
    colours = [c for _, c, _ in ARMS]
    with plt.rc_context(ps.STYLE):
        fig, ax = plt.subplots(1, 3, figsize=(width, height))
        x = np.arange(len(labels))
        for i, (key, ylab, title) in enumerate(PANELS):
            m = [data[l][key][0] for l in labels]
            lo = [data[l][key][0] - data[l][key][1] for l in labels]
            hi = [data[l][key][2] - data[l][key][0] for l in labels]
            ax[i].bar(x, m, 0.62, color=colours, edgecolor="white", linewidth=0.4)
            # min..max over the two repeats. A bar whose error bar is invisible
            # is one where the repeats agreed, not one measured once.
            ax[i].errorbar(x, m, yerr=[lo, hi], fmt="none", ecolor="#404040",
                           elinewidth=0.6, capsize=1.5)
            # No names under the bars: the same two names under three panels
            # is the same key written three times, and rotated it costs a third
            # of the canvas height. The colours carry it, once, in the legend.
            ax[i].set_xticks(x)
            ax[i].set_xticklabels([""] * len(labels))
            ax[i].tick_params(axis="x", length=0)
            ax[i].set_ylabel(ylab, labelpad=1.5)
            # 6.5 pt, not 8: at three panels across one column each caption
            # has about 0.8 in and "(b) SLO attainment" is 1.05 in of type at
            # 8 pt. Centred on its axes, the overflow runs off the canvas --
            # which is how "(c) Rejection rate" lost its last word.
            ax[i].set_xlabel(title, labelpad=1.5, fontsize=6.5)
            ax[i].grid(axis="y", **ps.GRID)
            ax[i].set_axisbelow(True)
            for sp in ("top", "right"):
                ax[i].spines[sp].set_visible(False)
            if key == "thru":
                ax[i].set_ylim(0, None)
                ax[i].yaxis.set_major_formatter(ps.kfmt())
            else:
                ax[i].set_ylim(0, 105)
                ax[i].set_yticks([0, 50, 100])
        handles = [Patch(facecolor=c, label=l) for l, c, _ in ARMS]
        fig.legend(handles, labels, loc="lower center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 1 - 0.16 / height), frameon=False,
                   fontsize=6.5, columnspacing=1.0, handlelength=1.0,
                   handleheight=1.0, handletextpad=0.4, borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0, 1, 1 - 0.145 / height), pad=0.3, w_pad=1.0)
        ps.save(fig, out)


def report(data):
    print(f"{'arm':13s} {'runs':>4s} {'throughput':>11s} "
          f"{'attain(adm)':>12s} {'rejected':>9s}")
    for label, _, _ in ARMS:
        d = data[label]
        print(f"{label:13s} {d['n_runs']:4d} {d['thru'][0]:11,.0f} "
              f"{d['adm'][0]:11.1f}% {d['rej'][0]:8.1f}%")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--t-lo", type=float, default=None,
                    help="minutes from the first arrival; the whole hour if "
                         "omitted")
    ap.add_argument("--t-hi", type=float, default=None)
    a = ap.parse_args()
    if (a.t_lo is None) != (a.t_hi is None):
        sys.exit("give both --t-lo and --t-hi or neither")

    data = collect(a.t_lo, a.t_hi)
    report(data)
    # The interval is in the file name. The same three bars over a different
    # slice of the same hour are different numbers, and a name that does not
    # carry the slice is a name that will be cited for the wrong one.
    tag = "" if a.t_lo is None else f"_m{a.t_lo:.0f}_{a.t_hi:.0f}"
    pdf = os.path.join(HERE, f"hour_slo_vs_llmd{tag}.pdf")
    build(data, pdf)
    build(data, os.path.join(HERE, f"hour_slo_vs_llmd{tag}_wide.pdf"),
          ps.TEXT_W, 1.60)
    write_csv(data, pdf[:-4] + ".csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
