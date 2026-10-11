#!/usr/bin/env python3
"""EXP-139. Two single-column figures on what a policy does with its SLO.

FIGURE 1 -- slo_scale_pace.pdf   (CSV: slo_scale_pace_outcome.csv). The per-token time a policy actually delivers to
chat, against the per-token budget it was given. chat is the class that sets the
gate on any instance holding it, so this is the quantity the budget is meant to
control. Two reference lines: y = x is the budget itself, y = 0.90x is the pace
FluidServe's gate plans against (`fsAllowanceUtilisation = 0.90`, gateSlack 1.0).

FIGURE 2 -- slo_scale_outcome.pdf. What that buys, on the denominator this paper
uses: every arrival counts, and a rejected or unfinished request is a violation.

WHY k = 1.0 IS DRAWN SEPARATELY. Every other point was measured on 2026-09-15 in
one session. The deployed budget was measured on 2026-08-31 (EXP-108), and
session-to-session movement on this workload has been measured at up to 4.6
points, so it is drawn as a hollow marker rather than joined into the curve. The
CSV carries the session for every row.

  python3 paper_figures/fig_slo_scale_pace.py
"""
import csv, json, os, subprocess, sys
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
from paper_style import COL_W, STYLE, GRID, ARM_COLOR, save   # noqa: E402

# tag -> (label, workload file tag, run glob, session). k=1.0 has no EXP-139 run.
SCALES = [
    ("0.7", "070", "results/*exp139k070r[12]_%s_*", "2026-09-15"),
    ("0.8", "080", "results/*exp139k080r1_%s_*",    "2026-09-15"),
    ("0.9", "090", "results/*exp139k090r1_%s_*",    "2026-09-15"),
    ("1.0", None,  "results/*exp108r[12]_%s_*_rpm_2100", "2026-08-31"),
    ("1.1", "110", "results/*exp139k110r1_%s_*",    "2026-09-15"),
    ("1.2", "120", "results/*exp139k120r1_%s_*",    "2026-09-15"),
    ("1.3", "130", "results/*exp139k130r[12]_%s_*", "2026-09-15"),
]
STYLES = {                       # label -> (colour key, marker)
    "FluidServe":  ("fluidserve",  "s"),
    "llm-d":       ("llmd",        "D"),
    "PolyServe":   ("polyserve",   "o"),
    "Llumnix SLO": ("slo",         "^"),
}
ORDER = ["FluidServe", "llm-d", "PolyServe", "Llumnix SLO"]


def collect():
    rows = []
    for klabel, tag, glob_pat, session in SCALES:
        env = dict(os.environ)
        if tag is None:                         # EXP-108 ran the standard budgets
            env["FS_SWE_TBT_MS"] = "75"
        else:
            slo = json.load(open(os.path.join(
                ROOT, "workload_configs", f"mix_short_m1_k{tag}fair.json")))["slo"]
            env.update({
                "FS_CHAT_TTFT_S": f'{slo["chat"]["ttft_ms"]/1000:g}',
                "FS_DR_TTFT_S": f'{slo["deepresearch"]["ttft_ms"]/1000:g}',
                "FS_SWE_TTFT_S": f'{slo["swe"]["ttft_ms"]/1000:g}',
                "FS_CHAT_TBT_MS": str(slo["chat"]["tbt_ms"]),
                "FS_DR_TBT_MS": str(slo["deepresearch"]["tbt_ms"]),
                "FS_SWE_TBT_MS": str(slo["swe"]["tbt_ms"]),
            })
        r = subprocess.run([sys.executable, os.path.join(HERE, "_fig_slo_scale_pace_collect.py"),
                            klabel, glob_pat, session],
                           env=env, capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit(f"collector failed at k={klabel}:\n{r.stderr}")
        rows.extend(json.loads(r.stdout.strip().splitlines()[-1]))
    return rows


def series(rows, arm, insession=True):
    out = [r for r in rows if r["arm"] == arm
           and ((r["session"] == "2026-09-15") == insession)]
    return sorted(out, key=lambda r: r["k"])


def fig_pace(rows, path):
    fig, ax = plt.subplots(figsize=(COL_W, 2.30))
    lo, hi = 32, 68
    # The two reference lines go in the LEGEND, not in inline annotations: at
    # this width an annotation on either line lands on top of a policy curve.
    ax.plot([lo, hi], [lo, hi], color="#666666", ls="-", lw=0.7, zorder=1,
            label="budget")
    ax.plot([lo, hi], [0.9 * lo, 0.9 * hi], color="#666666", ls="--", lw=0.7,
            zorder=1, label=r"0.90 $\times$ budget")
    for arm in ORDER:
        ck, mk = STYLES[arm]
        s = [r for r in series(rows, arm) if r["pace_ms_per_token"]]
        if s:
            ax.plot([r["chat_budget_ms"] for r in s], [r["pace_ms_per_token"] for r in s],
                    color=ARM_COLOR[ck], marker=mk, label=arm, zorder=3)
        for r in series(rows, arm, insession=False):
            if r["pace_ms_per_token"]:
                ax.plot([r["chat_budget_ms"]], [r["pace_ms_per_token"]],
                        color=ARM_COLOR[ck], marker=mk, mfc="white", mew=0.9,
                        ls="none", zorder=3)
    ax.set_xlabel("chat per-token budget (ms)")
    ax.set_ylabel("delivered per-token\ntime, chat (ms)", linespacing=0.95)
    ax.set_xlim(lo, hi); ax.set_ylim(25, 112)
    ax.set_xticks([35, 40, 45, 50, 55, 60, 65])
    ax.set_yticks([30, 40, 50, 60, 70, 80])
    ax.yaxis.grid(True, **GRID); ax.set_axisbelow(True)
    ax.legend(loc="upper left", ncol=2, handlelength=1.3, borderpad=0.2,
              labelspacing=0.25, handletextpad=0.5, columnspacing=1.0)
    fig.tight_layout(rect=(0, 0, 1, 1), pad=0.25)
    save(fig, path)


def fig_outcome(rows, path):
    fig, ax = plt.subplots(figsize=(COL_W, 2.30))
    for arm in ORDER:
        ck, mk = STYLES[arm]
        s = series(rows, arm)
        if s:
            x = [r["k"] for r in s]
            y = [r["attainment_pct"] for r in s]
            lo = [r["attainment_pct"] - r["attainment_min"] for r in s]
            hi = [r["attainment_max"] - r["attainment_pct"] for r in s]
            ax.errorbar(x, y, yerr=[lo, hi], color=ARM_COLOR[ck], marker=mk,
                        label=arm, capsize=1.5, elinewidth=0.7, zorder=3)
        for r in series(rows, arm, insession=False):
            ax.plot([r["k"]], [r["attainment_pct"]], color=ARM_COLOR[ck], marker=mk,
                    mfc="white", mew=0.9, ls="none", zorder=3)
    ax.axvline(1.0, color="#666666", ls=":", lw=0.7, zorder=1)
    # Above the data and below the legend: at y=2 this label lands on the
    # Llumnix SLO curve, which sits at 6.6-6.7 over the right half of the axis.
    ax.annotate("deployed budget", (1.0, 93), textcoords="offset points",
                xytext=(3, 0), ha="left", va="center", fontsize=7, color="#666666")
    ax.set_xlabel(r"SLO scale $k$   (every budget $\times\ k$)")
    ax.set_ylabel("SLO attainment (%),\nevery arrival", linespacing=0.95)
    ax.set_xlim(0.65, 1.35); ax.set_ylim(0, 118)
    ax.set_xticks([0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.3])
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.yaxis.grid(True, **GRID); ax.set_axisbelow(True)
    ax.legend(loc="upper left", ncol=2, handlelength=1.3, borderpad=0.2,
              labelspacing=0.25, handletextpad=0.5, columnspacing=1.0)
    fig.tight_layout(rect=(0, 0, 1, 1), pad=0.25)
    save(fig, path)


BATCH_LO, BATCH_HI = 150.0, 1070.0


def _area(batch):
    if not batch:
        return 8.0
    t = (batch - BATCH_LO) / (BATCH_HI - BATCH_LO)
    return 8.0 + max(0.0, min(1.0, t)) * 54.0


def fig_plane(rows, path):
    """Figure 1. What a policy spends, against what it gets for it.

    x  the share of the per-token budget the fleet actually consumes, measured as
       the delivered chat per-token time over chat's budget. chat is the class
       that sets the gate on any instance holding it, so this is the budget that
       binds. x > 1 is a fleet running slower than the promise it was given.
    y  token goodput: output tokens per second from requests that met their SLO.
       Request goodput is not drawn because at a fixed arrival rate it is the
       attainment rescaled (measured: within 0.06 req/s on every cell).
    area  fleet decode batch, i.e. how much of the engines the policy occupies.

    The point of the plane is that three of the four policies TRAVERSE it as the
    budget is scaled, and one does not.
    """
    fig, ax = plt.subplots(figsize=(COL_W, 2.55))
    ax.axvspan(1.0, 1.75, color="#f0f0f0", zorder=0)
    ax.axvline(1.0, color="#666666", ls="-", lw=0.7, zorder=1)
    ax.axvline(0.90, color="#666666", ls="--", lw=0.7, zorder=1)
    for arm in ORDER:
        ck, mk = STYLES[arm]
        s = [r for r in series(rows, arm) if r["pace_ms_per_token"]]
        if not s:
            continue
        x = [r["pace_ms_per_token"] / r["chat_budget_ms"] for r in s]
        y = [r["goodput_tok_s"] / 1000.0 for r in s]
        # matplotlib's `s` is an AREA in points^2. Map the measured batch range
        # onto 8..62 pt^2, which is 3.2 to 8.9 pt across -- readable at 3.335 in
        # without any marker swallowing its neighbours. Squaring a linear size
        # here is what made the first draft unreadable.
        sz = [_area(r["batch_p50"]) for r in s]
        ax.plot(x, y, color=ARM_COLOR[ck], lw=0.8, zorder=2, alpha=0.8)
        ax.scatter(x, y, s=sz, color=ARM_COLOR[ck], marker=mk,
                   zorder=3, linewidths=0, label=arm)
    ax.annotate("over budget", (1.02, 0.35), fontsize=7, color="#666666",
                ha="left", va="bottom")
    # Each curve is parameterised by k, and without these the reader cannot tell
    # that FluidServe's four points are four budgets rather than one measurement.
    # FluidServe's four budgets land on top of each other, which is the result;
    # the label has to say so, and it is set in the clear area between llm-d's
    # arc and PolyServe's descent with a leader back to the cluster.
    ax.annotate(r"$k$ = 0.7 … 1.3", xy=(0.884, 11.75), xytext=(0.945, 9.1),
                ha="left", va="center", fontsize=6.5,
                color=ARM_COLOR["fluidserve"],
                arrowprops=dict(arrowstyle="-", lw=0.5,
                                color=ARM_COLOR["fluidserve"],
                                shrinkA=1, shrinkB=2))
    ax.annotate(r"$k$=1.3", (0.862, 13.77), textcoords="offset points",
                xytext=(-7, 0), ha="right", va="center", fontsize=6.5,
                color=ARM_COLOR["polyserve"])
    ax.annotate(r"$k$=0.7", (1.41, 3.70), textcoords="offset points",
                xytext=(7, -1), ha="left", va="center", fontsize=6.5,
                color=ARM_COLOR["polyserve"])
    ax.annotate(r"$k$=1.3", (1.20, 1.54), textcoords="offset points",
                xytext=(0, 7), ha="center", va="bottom", fontsize=6.5,
                color=ARM_COLOR["slo"])
    ax.set_xlabel("share of the per-token budget consumed")
    ax.set_ylabel("token goodput (k tok/s)")
    ax.set_xlim(0.68, 1.72); ax.set_ylim(0, 18.5)
    ax.set_xticks([0.7, 0.9, 1.1, 1.3, 1.5, 1.7])
    ax.set_yticks([0, 4, 8, 12])
    ax.yaxis.grid(True, **GRID); ax.set_axisbelow(True)
    for b, xx in ((200, 1.34), (1000, 1.50)):
        ax.scatter([xx], [15.0], s=_area(b), color="#666666", marker="o",
                   linewidths=0, zorder=3)
        ax.annotate(f"{b}", (xx, 15.0), textcoords="offset points",
                    xytext=(0, -9), ha="center", va="top", fontsize=6,
                    color="#666666")
    ax.annotate("fleet batch", (1.42, 16.3), fontsize=6.5, color="#666666",
                ha="center", va="bottom")
    ax.legend(loc="upper left", ncol=1, handlelength=1.0, borderpad=0.2,
              labelspacing=0.2, handletextpad=0.4, scatterpoints=1,
              markerscale=0.8, fontsize=7)
    fig.tight_layout(rect=(0, 0, 1, 1), pad=0.25)
    save(fig, path)


def fig_capacity(rows, path):
    """Figure 1, in the form MOONCAKE's Figure 1 uses.

    Mooncake (FAST '25) sweeps the TBT SLO on x, plots "request capacity ratio"
    on y, and reads the improvement as a percentage at fixed SLO thresholds. Its
    metric is defined as "the proportion of effective requests among all
    requests", where an effective request met both its TTFT and its TBT
    threshold, and its scheduler rejects a request it cannot serve -- so a
    rejection counts against the denominator. That is the quantity this
    repository already computes as attainment on every arrival, which is why the
    axis is labelled with their name rather than a new one.

    TWO THINGS DIFFER FROM THEIRS AND BOTH ARE IN THE CAPTION. Their x spans 10x
    (100 to 1000 ms) and is logarithmic; ours spans 1.86x and is linear. Their
    curves do not cross, so one annotation carries the whole result; ours do, so
    the gap is annotated at two budgets and the sign of it changes.

    x is chat's per-token budget because chat is the class that sets the gate on
    any instance holding it. All three classes scale together; the caption gives
    the other two.
    """
    fig, ax = plt.subplots(figsize=(COL_W, 2.45))
    for arm in ORDER:
        ck, mk = STYLES[arm]
        s = series(rows, arm)
        if not s:
            continue
        x = [r["chat_budget_ms"] for r in s]
        y = [r["attainment_pct"] for r in s]
        lo = [r["attainment_pct"] - r["attainment_min"] for r in s]
        hi = [r["attainment_max"] - r["attainment_pct"] for r in s]
        ax.errorbar(x, y, yerr=[lo, hi], color=ARM_COLOR[ck], marker=mk,
                    label=arm, capsize=1.5, elinewidth=0.7, zorder=3)
        for r in series(rows, arm, insession=False):
            ax.plot([r["chat_budget_ms"]], [r["attainment_pct"]], color=ARM_COLOR[ck],
                    marker=mk, mfc="white", mew=0.9, ls="none", zorder=3)

    def at(arm, budget):
        for r in series(rows, arm):
            if abs(r["chat_budget_ms"] - budget) < 1e-6:
                return r["attainment_pct"]
        return None

    # Two budgets, not one: the gap narrows as the promise loosens and then
    # changes sign, which a single annotation would hide.
    for budget in (45.0, 65.0):
        ours = at("FluidServe", budget)
        rival, rname = max(((at(a, budget), a) for a in ORDER[1:]
                            if at(a, budget) is not None))
        ax.annotate("", xy=(budget, ours), xytext=(budget, rival),
                    arrowprops=dict(arrowstyle="<->", lw=0.6, color="#333333",
                                    shrinkA=1.5, shrinkB=1.5))
        pct = 100.0 * (ours - rival) / rival
        # The right-hand annotation must sit LEFT of its arrow or it leaves the
        # canvas, which `bbox_inches="tight"` is not available to rescue here.
        side = -5 if budget >= 60 else 4
        ax.annotate(f"{pct:+.0f}%", ((budget), (ours + rival) / 2),
                    textcoords="offset points", xytext=(side, 0),
                    ha=("right" if side < 0 else "left"),
                    va="center", fontsize=7, color="#333333")
    # The note that all three classes scale together belongs in the caption:
    # at 3.335 in it runs off the canvas here.
    ax.set_xlabel("chat per-token budget (ms)")
    ax.set_ylabel("effective request capacity (%)")
    ax.set_xlim(32, 68); ax.set_ylim(0, 118)
    ax.set_xticks([35, 40, 45, 50, 55, 60, 65])
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.yaxis.grid(True, **GRID); ax.set_axisbelow(True)
    ax.legend(loc="upper left", ncol=2, handlelength=1.3, borderpad=0.2,
              labelspacing=0.25, handletextpad=0.5, columnspacing=1.0)
    fig.tight_layout(rect=(0, 0, 1, 1), pad=0.25)
    save(fig, path)


TP_LO, TP_HI = 4900.0, 15200.0


def _tp_area(tp):
    if not tp:
        return 8.0
    t = (tp - TP_LO) / (TP_HI - TP_LO)
    return 8.0 + max(0.0, min(1.0, t)) * 54.0


def fig_conversion(rows, path):
    """How much of the computation a policy performs turns out to be useful.

    y = token goodput / token throughput. The denominator is every output token
    the engines produced in the window; the numerator is the tokens from requests
    that met their SLO. A rejected request contributes to neither. A request that
    ran and missed contributes its whole output to the denominator and nothing to
    the numerator, so the ratio is the share of GPU work that was not wasted.

    ⚠ THE RATIO ALONE REWARDS REFUSING. llm-d converts 94.4% at the tightest
    budget, close to FluidServe's 97.7%, while producing 4,931 tok/s against
    12,202 -- it rejects 79% of arrivals and therefore only computes what it can
    already serve. The marker area is token throughput for exactly this reason:
    a high point with a small marker is a policy that kept its hands clean by
    doing little. The pair (area, height) multiplies to goodput.
    """
    fig, ax = plt.subplots(figsize=(COL_W, 2.45))
    for arm in ORDER:
        ck, mk = STYLES[arm]
        s = [r for r in series(rows, arm) if r.get("conversion_pct")]
        if not s:
            continue
        x = [r["chat_budget_ms"] for r in s]
        y = [r["conversion_pct"] for r in s]
        ax.plot(x, y, color=ARM_COLOR[ck], lw=0.9, zorder=2, alpha=0.8)
        ax.scatter(x, y, s=[_tp_area(r["throughput_tok_s"]) for r in s],
                   color=ARM_COLOR[ck], marker=mk, zorder=3, linewidths=0,
                   label=arm)
        for r in series(rows, arm, insession=False):
            if r.get("conversion_pct"):
                ax.scatter([r["chat_budget_ms"]], [r["conversion_pct"]],
                           s=_tp_area(r["throughput_tok_s"]), facecolors="white",
                           edgecolors=ARM_COLOR[ck], marker=mk, zorder=3,
                           linewidths=0.9)
    # Upper right: the only band this plot leaves empty. At y=26 the key sat on
    # the Llumnix SLO curve and on PolyServe's flat stretch.
    for tp, xx in ((5000, 59.5), (15000, 65.0)):
        ax.scatter([xx], [112.0], s=_tp_area(tp), color="#666666", marker="o",
                   linewidths=0, zorder=3)
        ax.annotate(f"{tp//1000}k", (xx, 112.0), textcoords="offset points",
                    xytext=(0, -9), ha="center", va="top", fontsize=6,
                    color="#666666")
    ax.annotate("token throughput", (62.2, 119.0), fontsize=6.5, color="#666666",
                ha="center", va="bottom")
    ax.set_xlabel("chat per-token budget (ms)")
    ax.set_ylabel("share of computed tokens\nthat met an SLO (%)", linespacing=0.95)
    ax.set_xlim(32, 68); ax.set_ylim(0, 128)
    ax.set_xticks([35, 40, 45, 50, 55, 60, 65])
    ax.set_yticks([0, 25, 50, 75, 100])
    ax.yaxis.grid(True, **GRID); ax.set_axisbelow(True)
    ax.legend(loc="upper left", ncol=2, handlelength=1.0, borderpad=0.2,
              labelspacing=0.2, handletextpad=0.4, columnspacing=0.8,
              scatterpoints=1, markerscale=0.8, fontsize=7)
    fig.tight_layout(rect=(0, 0, 1, 1), pad=0.25)
    save(fig, path)


def write_csv(rows, path):
    cols = ["k", "arm", "session", "n_repeats", "chat_budget_ms",
            "budget_consumed", "throughput_tok_s", "conversion_pct", "goodput_tok_s", "goodput_min", "goodput_max",
            "batch_p50", "attainment_pct", "attainment_min", "attainment_max",
            "pace_ms_per_token", "pace_min", "pace_max"]
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        for r in sorted(rows, key=lambda r: (r["arm"], r["k"])):
            r = dict(r)
            r["budget_consumed"] = (r["pace_ms_per_token"] / r["chat_budget_ms"]
                                    if r.get("pace_ms_per_token") else None)
            w.writerow({c: r.get(c) for c in cols})
    print(f"wrote {path}  ({len(rows)} rows)")


def main():
    plt.rcParams.update(STYLE)
    rows = collect()
    write_csv(rows, os.path.join(HERE, "slo_scale_pace_outcome.csv"))
    fig_conversion(rows, os.path.join(HERE, "token_conversion.pdf"))
    fig_capacity(rows, os.path.join(HERE, "effective_capacity.pdf"))
    fig_plane(rows, os.path.join(HERE, "slo_scale_plane.pdf"))
    fig_pace(rows, os.path.join(HERE, "slo_scale_pace.pdf"))
    fig_outcome(rows, os.path.join(HERE, "slo_scale_outcome.pdf"))


if __name__ == "__main__":
    main()
