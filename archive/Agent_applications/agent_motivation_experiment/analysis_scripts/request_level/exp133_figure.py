#!/usr/bin/env python3
"""EXP-133 -- the cumulative ablation staircase, in two panels.

Panel (a) is the staircase itself: what the system scores as each mechanism is
removed on top of the ones already gone. Both denominators are drawn, because
attainment on the admitted denominator alone rewards an arm for refusing the
requests that were going to miss, and refusal is one of the things moving here.

Panel (b) is the reason the experiment exists. For each step it puts the
cumulative cost beside EXP-131's leave-one-out cost of the same element. Where
the two agree, that element works alone. Where the cumulative bar is larger,
another mechanism had been covering for it and leave-one-out could not see it.
EXP-132 measured one such case directly: with the class preference off the
feasibility test still leaves 1.8 of four instances free of chat against 2.1
with it on, so most of that separation survives its removal.

Error bars are min..max over repeats. A cell with one run gets no bar, and the
note says which cells those are, because a point with no bar otherwise reads as
the most precise point on the figure.

  python3 exp133_figure.py
  python3 exp133_figure.py --runs 'results/*exp133r[12]_*' --out results/aggregate_analysis/exp133_cumulative
"""
import argparse, glob, os, sys, csv, collections

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
sys.path.insert(0, HERE)

# Pinned before the import: the budgets become module constants at import time
# and the default swe form is the end-to-end one these runs were not produced
# under. See the comment in exp132_variations.py and CLAUDE.md group A.
for _k, _v in (("FS_SWE_TBT_MS", "75"), ("FS_SWE_TTFT_S", "7")):
    os.environ.setdefault(_k, _v)

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import all_arrivals_attainment as A
from exp22_fluidserve import PAPER_STYLE

EXCLUDED = os.path.join(REPO, "ms_dev", "notes", "excluded_runs.tsv")

STEPS = [
    ("cum0", "deployed", None),
    ("cum1", "− class\npreference", (3.3, 5.6)),
    ("cum2", "− deferral", (0.0, 0.0)),
    ("cum3", "− instance\ncap", None),
    ("cum4", "− KV flux\nprojection", (-7.7, -5.5)),
    ("cum5", "− future-KV\ncharge", None),
]
CONTROL_REF = (73.6, 75.5)   # EXP-108, same cell, two repeats


def excluded():
    out = set()
    if os.path.exists(EXCLUDED):
        for line in open(EXCLUDED):
            line = line.strip()
            if line and not line.startswith("#"):
                out.add(line.split("\t")[0])
    return out


def collect(pattern):
    skip = excluded()
    by = collections.defaultdict(list)
    for d in sorted(glob.glob(pattern)):
        b = os.path.basename(d.rstrip("/"))
        if "PRERUN" in b or b in skip:
            continue
        for arm, _, _ in STEPS:
            if "_%s_" % arm in b:
                r = A.one_run(d)
                if r is not None:
                    r["_run"] = b
                    by[arm].append(r)
                break
    return by


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="results/*exp133r[12]_*")
    ap.add_argument("--out", default="results/aggregate_analysis/exp133_cumulative")
    a = ap.parse_args()

    by = collect(a.runs)
    if not by:
        sys.exit("no runs matched %r" % a.runs)
    os.makedirs(a.out, exist_ok=True)

    have = [(arm, lab, l1) for arm, lab, l1 in STEPS if by.get(arm)]
    x = np.arange(len(have))

    def band(arm, key):
        v = [r[key] for r in by[arm]]
        return float(np.mean(v)), min(v), max(v), len(v)

    with plt.rc_context(PAPER_STYLE):
        fig, (ax, ax2) = plt.subplots(1, 2, figsize=(9.0, 4.4))

        for key, colour, marker, label in [
                ("all_arrivals", "#1f77b4", "o", "all arrivals"),
                ("admitted", "#7f7f7f", "s", "admitted")]:
            m = np.array([band(arm, key)[0] for arm, _, _ in have])
            lo = np.array([band(arm, key)[1] for arm, _, _ in have])
            hi = np.array([band(arm, key)[2] for arm, _, _ in have])
            ax.errorbar(x, m, yerr=[m - lo, hi - m], color=colour, marker=marker,
                        lw=1.6, ms=5, capsize=3, label=label)

        ax.axhspan(CONTROL_REF[0], CONTROL_REF[1], color="#1f77b4", alpha=0.10, lw=0)
        # Right-aligned and inside the band, so it cannot collide with the
        # legend (lower left) or with the x tick labels.
        ax.text(0.98, (CONTROL_REF[0] + CONTROL_REF[1]) / 2,
                "EXP-108 control, same cell", ha="right", va="center",
                transform=ax.get_yaxis_transform(), fontsize=6.5, color="#1f77b4")
        ax.set_xticks(x)
        ax.set_xticklabels([lab for _, lab, _ in have], fontsize=7)
        ax.set_ylabel("SLO attainment (%), per request")
        ax.set_title("(a) each step removes one more mechanism", fontsize=9)
        ax.grid(axis="y", ls=":", alpha=0.6)
        ax.legend(frameon=False, fontsize=8, loc="lower left")

        # (b) cumulative step against EXP-131's leave-one-out for the same element.
        w = 0.38
        cum_c, l1_c = [], []
        labs = []
        for i in range(1, len(have)):
            arm, lab, l1 = have[i]
            prev = band(have[i - 1][0], "all_arrivals")[0]
            cur = band(arm, "all_arrivals")[0]
            cum_c.append(prev - cur)                     # positive = removing it cost this much
            l1_c.append(np.mean(l1) if l1 else np.nan)
            labs.append(lab)
        xi = np.arange(len(labs))
        # The last step is CONFOUNDED and is drawn so. Moving the planning horizon
        # to 1 was meant to remove the arriving request's future-KV charge, but the
        # same number is the denominator of the prefill fraction in meanStepMs, and
        # the pace is evaluated with the arriving prompt already in pendingPrefill,
        # so sp >= 1 always and a horizon of 1 makes every candidate cost a whole
        # prefill iteration. Most of that bar is the pace estimate, not the charge.
        # It is also four times the next largest, so the axis is clipped to keep
        # the others readable and the bar is labelled with its value.
        conf = [i for i, l in enumerate(labs) if "future-KV" in l]
        colours = ["#bbbbbb" if i in conf else "#1f77b4" for i in range(len(labs))]
        hatches = ["//" if i in conf else "" for i in range(len(labs))]
        bars = ax2.bar(xi - w / 2, cum_c, w, color=colours,
                       label="cumulative step (EXP-133)")
        for b, h in zip(bars, hatches):
            if h:
                b.set_hatch(h)
                b.set_edgecolor("#777777")
        ax2.bar(xi + w / 2, l1_c, w, color="#d62728", alpha=0.85,
                label="leave-one-out (EXP-131)")
        if conf:
            finite = [c for i, c in enumerate(cum_c) if i not in conf]
            top = max(finite + [0]) * 1.9 + 2
            bot = min(finite + [0]) * 1.6 - 2
            ax2.set_ylim(bot, top)
            for i in conf:
                ax2.annotate("%.0f\nconfounded:\nthe horizon also sets\nthe pace estimate"
                             % cum_c[i],
                             xy=(xi[i] - w / 2, top), ha="center", va="top",
                             fontsize=6, color="#555555")
        for k, v in enumerate(l1_c):
            if np.isnan(v):
                ax2.text(xi[k] + w / 2, 0.15, "not measured\nalone", ha="center",
                         va="bottom", fontsize=5.5, color="#d62728", rotation=90)
        ax2.axhline(0, color="k", lw=0.8)
        ax2.set_xticks(xi)
        ax2.set_xticklabels(labs, fontsize=7)
        ax2.set_ylabel("attainment lost when removed (points)")
        ax2.set_title("(b) what leave-one-out could not see", fontsize=9)
        ax2.grid(axis="y", ls=":", alpha=0.6)
        ax2.legend(frameon=False, fontsize=8)

        ns = {arm: band(arm, "all_arrivals")[3] for arm, _, _ in have}
        single = [arm for arm, n in ns.items() if n == 1]
        note = ("Static 35 req/s, 8 min, Llama-3.1-70B 4x TP=2, swe scored per-token "
                "(TTFT 7 s + 75 ms/token). Bars are min..max over repeats; "
                + ("cells with ONE run and therefore no bar: %s. " % ", ".join(single)
                   if single else "every cell has 2 repeats. ")
                + "Panel (b) compares the drop caused by removing an element once "
                  "everything above it is already gone against removing it alone.")
        # Reserve the bottom strip for the note with a rect, rather than letting
        # bbox_inches="tight" widen the canvas to fit the text -- an unwrapped
        # note once produced a 7,334 px figure whose axes were a seventh of it.
        fig.tight_layout(rect=[0, 0.16, 1, 1])
        fig.text(0.5, 0.02, "\n".join(__import__("textwrap").wrap(note, 118)),
                 ha="center", va="bottom", fontsize=6.5)
        png = os.path.join(a.out, "exp133_cumulative_staircase.png")
        fig.savefig(png, dpi=200)
        print("wrote", png)

    csvp = os.path.join(a.out, "exp133_cumulative_staircase.csv")
    with open(csvp, "w", newline="") as fh:
        w_ = csv.writer(fh)
        w_.writerow(["arm", "removed_cumulative", "n_repeats",
                     "all_arrivals", "all_arrivals_min", "all_arrivals_max",
                     "admitted", "admitted_min", "admitted_max",
                     "rejected_pct", "goodput_tok_s",
                     "cumulative_step_points", "leave_one_out_points",
                     "scoring_rule"])
        prev = None
        for arm, lab, l1 in have:
            aa = band(arm, "all_arrivals")
            ad = band(arm, "admitted")
            rj = band(arm, "rejected_pct")
            gp = band(arm, "goodput_tok_s")
            step = "" if prev is None else round(prev - aa[0], 2)
            w_.writerow([arm, lab.replace("\n", " "), aa[3],
                         round(aa[0], 2), round(aa[1], 2), round(aa[2], 2),
                         round(ad[0], 2), round(ad[1], 2), round(ad[2], 2),
                         round(rj[0], 2), round(gp[0], 1),
                         step, "" if l1 is None else round(float(np.mean(l1)), 2),
                         "swe per-token: ttft<=7s AND mean<=75ms"])
            prev = aa[0]
    print("wrote", csvp, "(%d rows)" % len(have))


if __name__ == "__main__":
    main()
