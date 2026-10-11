#!/usr/bin/env python3
"""The planning-horizon curve on two models, side by side.

The horizon was never tuned: 100 was chosen at design time from a unit argument
("decode growth is exactly one token per request per iteration") which justifies
the unit and not the value. EXP-131 swept it on Llama-3.1-70B and found the peak
at 25-50 with the deployed value 7 to 10 points below it. This draws that curve
beside the same sweep on Qwen2.5-72B (EXP-136), which is the question of whether
the finding rests on one model.

BOTH DENOMINATORS ARE DRAWN because they say different things here and the
difference is the point: admitted attainment is MONOTONE in the horizon -- the
shorter it is the more of what it accepted the system keeps -- while
all-arrivals is a U, because the loss at the short end is entirely refusal. A
figure with only one of them supports the wrong reading either way.

The two arrival rates differ (2,100 rpm on Llama, 1,344 on Qwen) because they are
set to the same multiple of each model's knee, 28.4 and 18.3 req/s. The absolute
levels are therefore not comparable and the caption has to say so; the shape and
the position of the peak are what this figure is for.

  python3 exp136_horizon_two_models.py
"""
import argparse, glob, os, sys, csv, collections

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, HERE)
for _k, _v in (("FS_SWE_TBT_MS", "75"), ("FS_SWE_TTFT_S", "7")):
    os.environ.setdefault(_k, _v)

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import all_arrivals_attainment as A
from exp22_fluidserve import PAPER_STYLE

EXCLUDED = os.path.join(REPO, "ms_dev", "notes", "excluded_runs.tsv")

# horizon -> glob per model. The deployed value has no dedicated arm: it is the
# control arm, which runs at horizon 100 by compiled default.
LLAMA = {
    1:   ["results/*exp131j*_fsv3t75h1_t75_rpm_2100"],
    10:  ["results/*exp131j*_fsv3t75h10_t75_rpm_2100"],
    25:  ["results/*exp131h*_fsv3t75h25_t75_rpm_2100"],
    50:  ["results/*exp131e*_fsv3t75h50_t75_rpm_2100"],
    100: ["results/*exp108r[12]_fsv3capgnofrct75_t75_rpm_2100",
          "results/*exp133r[12]_cum0_t75_rpm_2100"],
    250: ["results/*exp131e*_fsv3t75h250_t75_rpm_2100"],
    500: ["results/*exp131e*_fsv3t75h500_t75_rpm_2100"],
}
QWEN = {h: ["results/*exp136qr[12]_%s_t75_rpm_1344" % a] for h, a in [
    (1, "fsv3t75h1"), (10, "fsv3t75h10"), (25, "fsv3t75h25"), (50, "fsv3t75h50"),
    (100, "fsv3capgnofrct75"), (250, "fsv3t75h250"), (500, "fsv3t75h500")]}
# The projection-off arm is NOT a point on the horizon axis -- it changes a
# second thing -- so it is drawn as a separate marker at its charging window.
NOFLUX = {
    "Llama-3.1-70B": ["results/*exp131i*_fsv3t75h50noflux_t75_rpm_2100"],
    "Qwen2.5-72B":   ["results/*exp136qr[12]_fsv3t75h50noflux_t75_rpm_1344"],
}
MODELS = [("Llama-3.1-70B", LLAMA, "#1f77b4", "o", "2,100 rpm (35 req/s)"),
          ("Qwen2.5-72B",   QWEN,  "#d62728", "s", "1,344 rpm (22.4 req/s)")]


def excluded():
    out = set()
    if os.path.exists(EXCLUDED):
        for line in open(EXCLUDED):
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line.split("\t")[0])
    return out


def score(pats, skip):
    rs = []
    for p in pats:
        for d in sorted(set(glob.glob(p))):
            b = os.path.basename(d.rstrip("/"))
            if "PRERUN" in b or b in skip:
                continue
            r = A.one_run(d)
            if r:
                rs.append(r)
    return rs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="results/aggregate_analysis/exp136_horizon")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    skip = excluded()

    data = {}
    for name, table, _, _, _ in MODELS:
        for h, pats in table.items():
            rs = score(pats, skip)
            if rs:
                data[(name, h)] = rs
    nf = {name: score(pats, skip) for name, pats in NOFLUX.items()}

    singles = [(n, h) for (n, h), rs in data.items() if len(rs) < 2]

    with plt.rc_context(PAPER_STYLE):
        fig, (ax, ax2) = plt.subplots(1, 2, figsize=(9.4, 4.2))
        for key, axis, lab in [("all_arrivals", ax, "all arrivals"),
                               ("admitted", ax2, "admitted")]:
            for name, table, colour, marker, rate in MODELS:
                hs = sorted(h for h in table if (name, h) in data)
                m = np.array([np.mean([r[key] for r in data[(name, h)]]) for h in hs])
                lo = np.array([min(r[key] for r in data[(name, h)]) for h in hs])
                hi = np.array([max(r[key] for r in data[(name, h)]) for h in hs])
                axis.errorbar(hs, m, yerr=[m - lo, hi - m], color=colour, marker=marker,
                              lw=1.6, ms=5, capsize=3, label="%s, %s" % (name, rate))
                if nf.get(name):
                    v = [r[key] for r in nf[name]]
                    axis.errorbar([50], [np.mean(v)],
                                  yerr=[[np.mean(v) - min(v)], [max(v) - np.mean(v)]],
                                  color=colour, marker="*", ms=11, lw=0, capsize=3,
                                  markeredgecolor="white", markeredgewidth=0.5,
                                  label="%s, KV projection off" % name.split("-")[0])
            axis.set_xscale("log")
            axis.set_xticks([1, 10, 25, 50, 100, 250, 500])
            axis.set_xticklabels(["1", "10", "25", "50", "100", "250", "500"])
            axis.axvline(100, color="k", ls=":", lw=0.9)
            axis.set_xlabel("planning horizon (engine iterations)")
            axis.set_ylabel("SLO attainment (%%), %s" % lab)
            axis.grid(axis="y", ls=":", alpha=0.6)
        ax.annotate("deployed", xy=(100, ax.get_ylim()[0]), xytext=(108, ax.get_ylim()[0] + 3),
                    fontsize=7, color="k")
        ax.set_title("(a) every arrival counted: a U with its peak at 25-50", fontsize=9)
        ax2.set_title("(b) of what was accepted: monotone in the horizon", fontsize=9)
        ax.legend(frameon=False, fontsize=7, loc="lower center")

        note = ("Static, 8 min, t75 scoring (swe: TTFT 7 s AND mean 75 ms/token), four instances "
                "x TP=2. The two arrival rates are set to the same multiple of each model's knee "
                "(28.4 and 18.3 req/s), so the SHAPE and the position of the peak are comparable "
                "and the absolute levels are not. Bars are min..max over 2 repeats; "
                + ("cells with one run and therefore no bar: "
                   + ", ".join("%s h=%d" % (n, h) for n, h in sorted(singles, key=lambda x: x[1]))
                   + ". " if singles else "every cell has 2 repeats. ")
                + "The star is not a point on this axis: it keeps the charging window at 50 and "
                  "also removes the inflow/outflow projection, so it differs in two things.")
        fig.tight_layout(rect=[0, 0.16, 1, 1])
        fig.text(0.5, 0.02, "\n".join(__import__("textwrap").wrap(note, 124)),
                 ha="center", va="bottom", fontsize=6.5)
        png = os.path.join(a.out, "exp136_horizon_two_models.png")
        fig.savefig(png, dpi=200)
        print("wrote", png)

    csvp = os.path.join(a.out, "exp136_horizon_two_models.csv")
    with open(csvp, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["model", "arrival_rate_rpm", "horizon", "projection", "n_repeats",
                    "all_arrivals", "all_arrivals_min", "all_arrivals_max",
                    "admitted", "admitted_min", "admitted_max",
                    "rejected_pct", "goodput_tok_s", "scoring_rule"])
        for name, table, _, _, rate in MODELS:
            rpm = 2100 if name.startswith("Llama") else 1344
            for h in sorted(table):
                rs = data.get((name, h))
                if not rs:
                    continue
                g = lambda k: [r[k] for r in rs]
                w.writerow([name, rpm, h, "on", len(rs),
                            round(np.mean(g("all_arrivals")), 2), round(min(g("all_arrivals")), 2),
                            round(max(g("all_arrivals")), 2),
                            round(np.mean(g("admitted")), 2), round(min(g("admitted")), 2),
                            round(max(g("admitted")), 2),
                            round(np.mean(g("rejected_pct")), 2), round(np.mean(g("goodput_tok_s")), 1),
                            "swe per-token: ttft<=7s AND mean<=75ms"])
            rs = nf.get(name)
            if rs:
                g = lambda k: [r[k] for r in rs]
                w.writerow([name, rpm, 50, "off", len(rs),
                            round(np.mean(g("all_arrivals")), 2), round(min(g("all_arrivals")), 2),
                            round(max(g("all_arrivals")), 2),
                            round(np.mean(g("admitted")), 2), round(min(g("admitted")), 2),
                            round(max(g("admitted")), 2),
                            round(np.mean(g("rejected_pct")), 2), round(np.mean(g("goodput_tok_s")), 1),
                            "swe per-token: ttft<=7s AND mean<=75ms"])
    print("wrote", csvp)


if __name__ == "__main__":
    main()
