#!/usr/bin/env python3
"""EXP-139. One curve that carries both performance axes at once.

THE PROBLEM THIS SOLVES. A policy has to be judged on two things that are
normally two figures: how much it delivers, and how close it runs to the latency
budget it was given. Encoding the second as marker size does not read.

THE DEVICE. Plot the CDF of, per request, the worse of its two terms against its own budget,

    max( first-token time / first-token budget ,
         per-token time   / per-token budget   )

so all three classes share one axis, and because a request meets its SLO exactly
when both terms are under 1, the SLO is the single vertical line x = 1. Then ONE
curve carries three readings:

  * the HEIGHT AT x = 1 is the share of every arrival that met its SLO -- the
    outcome axis. Checked against the scored table on all eight cells: the two
    agree within 0.3 points, the residual being requests of one output token,
    for which a per-token time is undefined;
  * WHERE THE CURVE RISES is how much of the budget the policy actually spends
    -- the adherence axis. A curve that stands up just left of 1 is using the
    budget and no more; one that stands up at 0.5 is leaving half of it;
  * the PLATEAU is 1 minus the share that never produced a per-token time at
    all, which is rejection plus the requests cut off at the window's end.

Arrivals with no per-token time are counted in the denominator and contribute no
x value, which is what makes the plateau mean that.

  python3 paper_figures/fig_budget_cdf.py
"""
import csv, json, os, subprocess, sys
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from paper_style import COL_W, TEXT_W, STYLE, GRID, ARM_COLOR, save   # noqa: E402

PANELS = [("0.9", "090", "results/*exp139k090r1_%s_*", "tight: $k$ = 0.9"),
          ("1.3", "130", "results/*exp139k130r1_%s_*", "loose: $k$ = 1.3")]
STYLES = {"FluidServe": ("fluidserve", "-"), "llm-d": ("llmd", "--"),
          "PolyServe": ("polyserve", "-."), "Llumnix SLO": ("slo", (0, (1, 1)))}
ORDER = ["FluidServe", "llm-d", "PolyServe", "Llumnix SLO"]


def collect():
    out = {}
    for klabel, tag, pat, _ in PANELS:
        slo = json.load(open(os.path.join(
            ROOT, "workload_configs", f"mix_short_m1_k{tag}fair.json")))["slo"]
        env = dict(os.environ, **{
            "FS_CHAT_TTFT_S": f'{slo["chat"]["ttft_ms"]/1000:g}',
            "FS_DR_TTFT_S": f'{slo["deepresearch"]["ttft_ms"]/1000:g}',
            "FS_SWE_TTFT_S": f'{slo["swe"]["ttft_ms"]/1000:g}',
            "FS_CHAT_TBT_MS": str(slo["chat"]["tbt_ms"]),
            "FS_DR_TBT_MS": str(slo["deepresearch"]["tbt_ms"]),
            "FS_SWE_TBT_MS": str(slo["swe"]["tbt_ms"])})
        r = subprocess.run([sys.executable, os.path.join(HERE, "_norm_cdf_collect.py"),
                            klabel, pat], env=env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"collector failed at k={klabel}:\n{r.stderr}")
        out[klabel] = json.loads(r.stdout.strip().splitlines()[-1])
    return out


def draw(ax, rows, title):
    ax.axvspan(1.0, 2.0, color="#f0f0f0", zorder=0)
    ax.axvline(1.0, color="#666666", lw=0.7, zorder=1)
    for arm in ORDER:
        rec = next((r for r in rows if r["arm"] == arm), None)
        if not rec or not rec["x"]:
            continue
        ck, ls = STYLES[arm]
        x = np.asarray(rec["x"])
        y = np.arange(1, len(x) + 1) / rec["n_arrivals"] * 100.0
        x = np.concatenate([[0.0], x, [2.0]])
        y = np.concatenate([[0.0], y, [y[-1]]])
        ax.step(x, y, where="post", color=ARM_COLOR[ck], ls=ls, lw=1.1,
                label=arm, zorder=3)
    ax.set_xlim(0.25, 1.85); ax.set_ylim(0, 100)
    ax.set_xticks([0.5, 1.0, 1.5])
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.yaxis.grid(True, **GRID); ax.set_axisbelow(True)
    ax.set_title(title, pad=3)


def main():
    plt.rcParams.update(STYLE)
    data = collect()
    fig, axes = plt.subplots(1, 2, figsize=(TEXT_W / 2 + 0.55, 2.25), sharey=True)
    for ax, (klabel, _, _, title) in zip(axes, PANELS):
        draw(ax, data[klabel], title)
    axes[0].set_ylabel("share of every arrival (%)")
    axes[0].annotate("over budget", (1.03, 4), fontsize=6.5, color="#666666",
                     ha="left", va="bottom")
    # The axis is the WORSE of the two terms now, not the per-token one; a label
    # naming only the pace would misstate what x = 1 means.
    fig.supxlabel("delivered / budget, worse of first-token and per-token",
                  y=0.115, fontsize=8)
    # Below the panels, one row. Inside either panel it lands on a curve: the
    # upper left of the loose panel is where FluidServe and PolyServe both
    # plateau, and the lower right is where Llumnix SLO is still rising.
    h, l = axes[0].get_legend_handles_labels()
    fig.legend(h, l, loc="lower center", ncol=4, handlelength=1.6,
               borderpad=0.2, labelspacing=0.2, handletextpad=0.5,
               columnspacing=1.1, fontsize=7.5, bbox_to_anchor=(0.5, -0.012))
    fig.tight_layout(rect=(0, 0.155, 1, 1), pad=0.25)
    save(fig, os.path.join(HERE, "budget_cdf.pdf"))

    with open(os.path.join(HERE, "budget_cdf.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["k", "arm", "n_arrivals", "quantile_of_arrivals_pct",
                    "normalised_per_token"])
        for klabel, rows in data.items():
            for rec in rows:
                x = np.asarray(rec["x"])
                if not len(x):
                    continue
                for q in range(5, 100, 5):
                    i = int(round(q / 100.0 * rec["n_arrivals"])) - 1
                    if 0 <= i < len(x):
                        w.writerow([klabel, rec["arm"], rec["n_arrivals"], q,
                                    round(float(x[i]), 4)])
    print("wrote budget_cdf.csv")


if __name__ == "__main__":
    main()
