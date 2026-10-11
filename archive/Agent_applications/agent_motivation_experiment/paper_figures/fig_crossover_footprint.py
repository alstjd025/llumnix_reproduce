#!/usr/bin/env python3
"""Paper figure: the crossover footprint T*, and which ceiling actually bound.

  crossover_footprint.pdf   7.0 x 2.15 in, `figure*`, width=\\textwidth
  crossover_footprint.csv   the plotted curve and the plotted bars

WHAT IT CLAIMS. An instance has two ceilings on how much work it can hold: the
PACE ceiling, where the decode step time reaches the per-token budget, and the
MEMORY ceiling, where the KV pool fills. Which one a workload reaches first is
not a property of the workload -- it is a property of (model, tensor
parallelism, KV pool) and of the class budget, and it can be written down in
closed form from the profile tables alone. The same trace on the same
control plane therefore runs into different ceilings on different fleets, and
the figure says in advance, from three constants and a pool size, which one.

    T* = C (c_n + rho/R) / ((B - c0) s - C c_kv)

C is the instance's physical KV pool in tokens, c0/c_kv/c_n the fitted decode
step law `t = c0 + c_kv*M + c_n*n` (M is the LOGICAL KV count, the sum of the
resident requests' context lengths, which is the quantity the fit was taken
against), s the measured physical-to-logical sharing ratio prefix caching
produces, rho the uncached prompt tokens that must be prefilled per generated
token, R the prefill rate, and B the class's per-token budget. Below T* the pace
ceiling is lower; above it, memory is. The derivation is in
`analysis_scripts/request_level/crossover_footprint.py`.

PANEL (a). T* against the per-token budget, one curve per fleet, with the three
class budgets marked and each fleet's measured resident footprint drawn as a
horizontal rule in its own colour. Where a fleet's rule is BELOW its curve the
pace ceiling binds; where it is above, memory binds. The four-instance fleets
sit under their curves at chat's 50 ms per token and the eight-instance one sits
over it, on the same trace.

PANEL (b). The confirmation, from two independent places. `predicted` is the
share of instance scrapes whose measured footprint is below T* at chat's 50 ms.
`counted` is the share of the scheduler's own sole-reason infeasibility counter
(`scheduler_fluidserve_infeasible_sole_total`) attributed to the pace gate
rather than to memory. Nothing links them: one comes from the profile tables and
a pool size, the other from the scheduler's decisions.

⚠ THE TWO BARS COUNT DIFFERENT THINGS AND THE CAPTION MUST SAY SO. `predicted`
counts instance-scrapes; `counted` counts CANDIDATE EVALUATIONS at moments when
a placement was refused, so one refused request contributes one evaluation per
instance and the evaluations are concentrated in the busy moments. They agree to
1.1, 14.2 and 7.5 points on the three fleets, which is the claim -- not that they
are the same number.

⚠ WHAT IS MEASURED AND WHAT IS NOT.
  MEASURED per fleet, over the load window only (first arrival + 60 s to last
    arrival - 20 s): s, rho, the resident footprint, and both bars. Cutting to
    the load window is not cosmetic -- the collector scrapes from before the
    load starts until after it drains, and a whole-run quantile of an engine
    gauge is half idle.
  FITTED, offline, on decode-only steps: c0, c_kv, c_n. R is the prefill rate at
    the 8192-token anchor, the largest measured chunk and the one the engines
    are configured with.
  MEASURED but not from these runs: the KV pool of the 8B and 70B fleets.
  DERIVED, not measured: the Qwen2.5-72B pool. It comes from the per-GPU memory
    budget with the overhead the 8B pool implies. Every Qwen number here rests
    on that.

    python3 paper_figures/fig_crossover_footprint.py
"""
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "analysis_scripts", "request_level"))

from paper_style import TEXT_W, STYLE, GRID, kfmt, save  # noqa: E402
import crossover_footprint as cf  # noqa: E402

# ⚠ COLOUR ENCODES A FLEET HERE, NOT A POLICY, AND THIS IS THE ONLY FIGURE IN
# THIS DIRECTORY WHERE THAT IS TRUE. The three are chosen from outside both
# tables that are already spoken for -- `paper_style.ARM_COLOR`, where a colour
# means a control plane, and the class colours of `exp22_fluidserve`, where blue
# is chat, orange deep research and red the agent class -- so that no reader can
# carry a meaning across from another figure.
FLEET_COLOR = {"70B x4": "#000000", "Qwen72B x4": "#00838f",
               "8B x8": "#c2185b"}
FLEET_LS = {"70B x4": "-", "Qwen72B x4": "--", "8B x8": (0, (4, 1, 1, 1))}
# Repeats. One run per repeat, the same directories the SLO-scale figure draws,
# named rather than globbed for the reason given there.
RUNS = {
    "70B x4": ["results/260831_2015_exp109r1_fsv3capgnofrct75_shift",
               "results/260901_1514_exp109r2_fsv3capgnofrct75_shift"],
    "Qwen72B x4": ["results/260903_0302_exp113r1_fsv3capgnofrct75_shiftq",
                   "results/260903_1031_exp113r2_fsv3capgnofrct75_shiftq"],
    "8B x8": ["results/260908_2055_exp114h62r1_fsv3capgnofrct75_shift62",
              "results/260909_0900_exp114mlr1_fsv3capgnofrct75_shift62"],
}
LABEL = {"70B x4": "4 x Llama-3.1-70B, TP=2",
         "Qwen72B x4": "4 x Qwen2.5-72B, TP=2",
         "8B x8": "8 x Llama-3.1-8B, TP=1"}
ORDER = ["70B x4", "Qwen72B x4", "8B x8"]

BUDGETS = np.linspace(30.0, 150.0, 241)     # ms per token
# The sensitivity box the caption has to state. rho and s are the two measured
# constants T* is most exposed to, and the band is their JOINT range, not one at
# a time -- one at a time understates a box by construction.
RHO_BOX = (1.0, 3.0)
S_BOX = (0.6, 1.0)
FIG_H = 2.15


def fleet_rows():
    """Everything both panels draw, per fleet, with the repeat spread."""
    # The overhead constant, calibrated on the one pool the engine reports, and
    # the check that makes it usable: it predicts the 70B pool to within 0.8%.
    f8 = cf.FLEETS["8 x Llama-3.1-8B (TP=1)"]
    overhead = (cf.GPU_BYTES * cf.GPU_UTIL - f8["params"] * cf.DTYPE_BYTES
                - f8["pool_tokens"] * f8["kv_bytes_per_token"])
    out = {}
    for short in ORDER:
        spec = next(v for v in cf.FLEETS.values() if v["short"] == short)
        C = spec["pool_tokens"] or cf.pool_from_overhead(spec, overhead)
        prof = cf.profile(spec["profile"])
        ms = [cf.measure(os.path.join(ROOT, r)) for r in RUNS[short]]
        s = [m["s_per_pool"] * C for m in ms]
        rho = [m["rho"] for m in ms]
        foot = [m["foot_p50"] for m in ms]
        tstar50 = [cf.tstar(C, prof, cf.CLASS_BUDGET_MS["chat"], si, ri)
                   for si, ri in zip(s, rho)]
        pred = [float((m["footprints"] < t).mean()) for m, t in zip(ms, tstar50)]
        cnt = [m["counted_pace_frac"] for m in ms]
        out[short] = dict(
            spec=spec, C=C, prof=prof, n_repeats=len(ms),
            s=float(np.mean(s)), s_min=min(s), s_max=max(s),
            rho=float(np.mean(rho)), rho_min=min(rho), rho_max=max(rho),
            foot=float(np.mean(foot)), foot_min=min(foot), foot_max=max(foot),
            foot_p25=float(np.mean([m["foot_p25"] for m in ms])),
            foot_p75=float(np.mean([m["foot_p75"] for m in ms])),
            n_scrape_obs=int(sum(m["n_foot"] for m in ms)),
            pred=float(np.mean(pred)) * 100, pred_min=min(pred) * 100,
            pred_max=max(pred) * 100,
            counted=float(np.mean(cnt)) * 100, counted_min=min(cnt) * 100,
            counted_max=max(cnt) * 100,
            sole_total=int(sum(m["sole_total"] for m in ms)),
            window_s=float(np.mean([m["window_s"] for m in ms])))
    return out


def curves(d):
    """T* over the budget axis, at the measured constants and over the box."""
    C, prof = d["C"], d["prof"]
    line = cf.tstar(C, prof, BUDGETS, d["s"], d["rho"])
    corners = [cf.tstar(C, prof, BUDGETS, s, r)
               for s in S_BOX for r in RHO_BOX]
    return line, np.min(corners, axis=0), np.max(corners, axis=0)


def build(F, out_pdf):
    with plt.rc_context(STYLE):
        fig, ax = plt.subplots(1, 2, figsize=(TEXT_W, FIG_H),
                               gridspec_kw=dict(width_ratios=[1.55, 1.0]))
        # --- (a) the crossover curve -------------------------------------
        handles, labels = [], []
        for short in ORDER:
            d = F[short]
            c = FLEET_COLOR[short]
            line, lo, hi = curves(d)
            ax[0].fill_between(BUDGETS, lo, hi, color=c, alpha=0.10, lw=0)
            h, = ax[0].plot(BUDGETS, line, color=c, ls=FLEET_LS[short], lw=1.1)
            # The measured resident footprint. Its position relative to the
            # curve of the SAME colour is the whole panel: below the curve the
            # pace ceiling binds, above it memory does.
            ax[0].axhline(d["foot"], color=c, lw=0.7, ls=(0, (1, 1.4)),
                          alpha=0.9)
            handles.append(h)
            labels.append(LABEL[short])
        pretty = {"chat": "chat", "swe": "swe", "deepresearch": "deep research"}
        for name, b in cf.CLASS_BUDGET_MS.items():
            ax[0].axvline(b, color="#888888", lw=0.5, ls=":", zorder=0)
            ax[0].annotate(pretty[name], xy=(b, 26000), fontsize=6,
                           color="#555555", ha="center", va="bottom")
        ax[0].set_yscale("log")
        ax[0].set_ylim(600, 40000)
        ax[0].set_yticks([1000, 3000, 10000, 30000])
        ax[0].yaxis.set_major_formatter(kfmt())
        ax[0].set_xlim(BUDGETS[0], BUDGETS[-1])
        ax[0].set_ylabel("Crossover footprint $T^*$\n(KV tokens per request)")
        ax[0].set_xlabel("Per-token budget $B$ (ms/token)\n"
                         "(a) where the two ceilings meet", labelpad=1.5,
                         linespacing=1.6)
        ax[0].grid(axis="both", **GRID)
        ax[0].set_axisbelow(True)
        # The two regions the curve separates. Placed where no curve or band
        # runs: the curves fall from left to right, so the space above them is
        # on the right and the space below them is on the left.
        ax[0].annotate("pace ceiling binds", xy=(122, 13000), fontsize=6.5,
                       color="#555555", ha="center", va="center")
        ax[0].annotate("memory ceiling binds", xy=(52, 780), fontsize=6.5,
                       color="#555555", ha="center", va="center")
        # The three horizontal rules sit within 1k of each other because the
        # three fleets replay the SAME trace; one label serves all three, and
        # the right edge is the only stretch where the curves have fallen well
        # below them.
        ax[0].annotate("measured resident footprint", xy=(148, 5400),
                       fontsize=6, color="#555555", ha="right", va="bottom")

        # --- (b) predicted against counted --------------------------------
        x = np.arange(len(ORDER))
        w = 0.34
        for j, key in enumerate(("pred", "counted")):
            vals = [F[s][key] for s in ORDER]
            err = np.array([[F[s][key] - F[s][key + "_min"] for s in ORDER],
                            [F[s][key + "_max"] - F[s][key] for s in ORDER]])
            ax[1].bar(x + (j - 0.5) * w, vals, w,
                      color=[FLEET_COLOR[s] for s in ORDER],
                      alpha=1.0 if j == 0 else 0.35,
                      edgecolor=[FLEET_COLOR[s] for s in ORDER], lw=0.6,
                      yerr=err, error_kw=dict(lw=0.6, capsize=1.5,
                                              ecolor="#444444"))
        ax[1].set_xticks(x)
        ax[1].set_xticklabels([s.replace(" x", "\nx") for s in ORDER])
        ax[1].set_ylim(0, 112)
        ax[1].set_yticks([0, 25, 50, 75, 100])
        ax[1].set_ylabel("Pace binds (%)")
        ax[1].set_xlabel("(b) predicted (solid) vs counted (faded)",
                         labelpad=1.5)
        ax[1].grid(axis="y", **GRID)
        ax[1].set_axisbelow(True)
        for i, s in enumerate(ORDER):
            for j, key in enumerate(("pred", "counted")):
                ax[1].annotate(f"{F[s][key]:.1f}",
                               xy=(i + (j - 0.5) * w, F[s][key] + 2.5),
                               fontsize=6, ha="center", va="bottom",
                               color="#333333")

        fig.legend(handles, labels, loc="lower center", ncol=3,
                   bbox_to_anchor=(0.5, 0.895), frameon=False,
                   columnspacing=1.4, handlelength=2.2, handletextpad=0.5,
                   borderaxespad=0.0)
        fig.tight_layout(rect=(0, 0.0, 1, 0.90), w_pad=1.8, pad=0.25)
        save(fig, out_pdf)


def write_csv(F, path):
    """The curve and the bars, from the same objects the panels were drawn from.

    One row per (fleet, budget). The panel (b) values and every measured input
    are constant within a fleet and repeat down its rows rather than living in a
    second file, because a join on a fleet name is one more place for a row to
    go missing in silence.
    """
    rows = []
    for short in ORDER:
        d = F[short]
        line, lo, hi = curves(d)
        for b, t, l, h in zip(BUDGETS, line, lo, hi):
            rows.append(dict(
                fleet=LABEL[short], fleet_short=short, budget_ms_per_token=b,
                crossover_footprint_tokens=t,
                crossover_footprint_tokens_min=l,
                crossover_footprint_tokens_max=h,
                resident_footprint_tokens=d["foot"],
                resident_footprint_tokens_min=d["foot_min"],
                resident_footprint_tokens_max=d["foot_max"],
                resident_footprint_p25=d["foot_p25"],
                resident_footprint_p75=d["foot_p75"],
                pace_binding_predicted_pct=d["pred"],
                pace_binding_predicted_pct_min=d["pred_min"],
                pace_binding_predicted_pct_max=d["pred_max"],
                pace_binding_counted_pct=d["counted"],
                pace_binding_counted_pct_min=d["counted_min"],
                pace_binding_counted_pct_max=d["counted_max"],
                kv_pool_tokens=d["C"], kv_pool_source=d["spec"]["pool_source"],
                sharing_ratio_s=d["s"], sharing_ratio_s_min=d["s_min"],
                sharing_ratio_s_max=d["s_max"],
                rho_prefill_per_output_token=d["rho"],
                rho_min=d["rho_min"], rho_max=d["rho_max"],
                c0_ms=d["prof"]["c0"], c_kv_ms_per_token=d["prof"]["c_kv"],
                c_n_ms_per_request=d["prof"]["c_n"],
                prefill_rate_tok_per_ms=d["prof"]["R"],
                n_repeats=d["n_repeats"], n_instance_scrapes=d["n_scrape_obs"],
                n_sole_infeasible_evals=d["sole_total"],
                load_window_s=d["window_s"],
                band_definition=(f"rho in [{RHO_BOX[0]}, {RHO_BOX[1]}] x "
                                 f"s in [{S_BOX[0]}, {S_BOX[1]}], joint"),
                scoring_rule=("no request scoring: T* is a fleet property; "
                              "class budgets chat 50 / swe 75 / "
                              "deepresearch 100 ms per token")))
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, float_format="%.6g")
    print(f"wrote {path}  ({len(df)} rows = {df['fleet'].nunique()} fleets x "
          f"{df['budget_ms_per_token'].nunique()} budgets)")


def main():
    os.chdir(ROOT)
    F = fleet_rows()
    for short in ORDER:
        d = F[short]
        t50 = cf.tstar(d["C"], d["prof"], 50.0, d["s"], d["rho"])
        print(f"{LABEL[short]:26s} C={d['C']:>9,.0f} ({d['spec']['pool_source']})"
              f"  s={d['s']:.3f}  rho={d['rho']:.3f}  footprint={d['foot']:,.0f}"
              f"  T*(50ms)={t50:,.0f}  pace: pred {d['pred']:.1f}% / "
              f"counted {d['counted']:.1f}%  n={d['n_repeats']}")
    build(F, os.path.join(HERE, "crossover_footprint.pdf"))
    write_csv(F, os.path.join(HERE, "crossover_footprint.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
