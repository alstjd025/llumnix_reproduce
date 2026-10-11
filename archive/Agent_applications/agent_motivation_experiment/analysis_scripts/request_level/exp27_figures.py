#!/usr/bin/env python3
"""EXP-27 figures: the rate sweep, what each engine was doing, and the latencies.

Three figures, each answering one question that the others cannot.

  sweep    attainment and token goodput against offered rate, one panel per mix.
           Two y axes on purpose: a policy can raise attainment by refusing work,
           and goodput is the quantity that does not let it. Putting them on one
           panel means no reading of the attainment curve is possible without the
           throughput curve in view.
  engines  per-engine decode batch, queue depth and KV occupancy over time. The
           request-level numbers cannot show a fleet that is idle in three places
           and saturated in the fourth, which is exactly the failure the static
           partition produces.
  latency  TTFT and inter-token latency distributions per class, against the
           budgets they are scored on. This is what separates "the engine is too
           slow" from "the request waited": if ITL is inside budget and TTFT is
           not, nothing was overloaded, the request was queued.

    python3 analysis_scripts/request_level/exp27_figures.py \
        --pass1 'results/*exp27r1_*' --pass2 'results/*exp27p2*' \
        --out-dir results/aggregate_analysis/exp27
"""
import argparse
import glob
import json
import os
import re
import sys
import textwrap

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import (  # noqa: E402
    CLASSES, CLASS_COLORS, PAPER_STYLE, load_run, per_request, goodput_tokens,
    total_tokens,
    arm_of,
)

ARM_C = {"polyserve": "#d62728", "slo": "#2ca02c", "fluidserve": "#1f77b4",
         # EXP-98.
         "fsboth": "#e377c2", "fsdelay": "#17becf", "fsdeadfix": "#8c564b",
         "fsinterleave": "#7f7f7f", "fsv3": "#1f77b4", "fsv3noaff": "#ff7f0e",
         "fsv3b40": "#2ca02c", "fsv3noaffb40": "#8c564b",
         # EXP-107.
         "fsv3cap": "#9467bd", "fsv3nofrc": "#e377c2", "fsv3capnofrc": "#17becf",
         "fsv3capg": "#7b3294", "fsv3capgnofrc": "#0570b0",
         # EXP-114 cap ablation: the two arms differ in FS_INSTANCE_CAP only.
         # EXP-116 (2026-09-10): fsv3capgnofrct75 moved from #d62728 to the
         # project's FluidServe blue, and the cap ablation arm to orange,
         # because the eight-instance static sweep draws this arm beside
         # polyservept75 and #d62728 is PolyServe's reserved colour. A
         # figure that gives our arm the baseline's colour misreads at a
         # glance no matter what the legend says.
         "fsv3capgnofrct75": "#1f77b4", "fsv3gnofrct75nocap": "#ff7f0e",
         "polyservept75": "#d62728", "loadbalance": "#7f7f7f",
         # EXP-116 follow-on: the first-token deadline in the feasibility test.
         "fsv3capgnofrct75dl": "#2ca02c",
         # EXP-118: the first-token estimate corrections.
         "fsv3ft": "#ff7f0e", "fsv3ftq": "#9467bd", "fsv3ftboth": "#2ca02c",
         "fsv3dl": "#8c564b", "fsv3dlftboth": "#e377c2",
         "fsv3ftml": "#17becf", "fsv3ftms85": "#7f7f7f",
         # EXP-124 (2026-09-10). The arrivals term in the KV projection: two window
         # lengths at the shipped 0.95 memory ceiling, the 0.97 ceiling alone, and the
         # pairing. Registered BEFORE the runs exist, because every one of these tables
         # is turned into an arm list as [k for k in <table> if k in <data>] and an arm
         # missing from the table is dropped from the figure with no message, leaving a
         # plausible legend over an incomplete comparison. FluidServe-family colours,
         # kept away from #d62728 (PolyServe) and #1f77b4 (the control arm these four
         # are read against, which must stay visually distinct from its own variants).
         "fsv3ah3": "#bcbd22", "fsv3ah1": "#ffbb78",
         "fsv3ms97": "#c5b0d5", "fsv3ah3ms97": "#393b79",
         # EXP-121, the halved per-token budgets. Registered before the runs
         # exist, for the reason written above this table: fig_sweep builds its
         # arm list as [a for a in ARM_C if a in set(df["arm"])], so an arm that
         # is absent here is dropped from every figure without a message and the
         # figure still renders with a plausible legend. The FluidServe arm keeps
         # the project's FluidServe blue and the PolyServe arm PolyServe's red,
         # because these two are read against fsv3capgnofrct75 and polyservept75
         # and the pairs must not swap identity between figures; the halved arms
         # are separated from the full-budget ones by line style in ARM_LS below.
         "fsv3capgnofrcc25d50s38": "#1f77b4", "polyservepc25d50s38": "#d62728",
         # EXP-123. The same two policies with the first-token budgets halved
         # as well. Same colours as the EXP-121 pair, because the policy
         # identity must not change between figures; ARM_LS separates the two
         # budget settings by line style.
         "fsv3capgnofrcc25d50s38ftc2500d5000s3500": "#1f77b4",
         "polyservepc25d50s38ftc2500d5000s3500": "#d62728",
         "fsv3capgnofrct75cc": "#2ca02c",
         "fsv3capgnofrct75ml": "#9467bd",
         "fsv3capgnofrct75ml85": "#8c564b",
         "fsnaboth": "#bcbd22",
         "fsdead": "#17becf",
         "fscorr": "#8c564b",
         "fspacecap": "#7f7f7f",

         "fluidserveflat": "#9467bd",
         # EXP-40 crosses two control planes with two engine schedulers. Hue is
         # the control plane so it means what it means everywhere else; the
         # lighter shade of the same hue is the deadline-aware engine. Reading
         # the pair of shades is reading what the engine changed.
         "slofifo": "#2ca02c", "sloqoserve": "#98df8a",
         "fluidservefifo": "#1f77b4", "fluidserveqoserve": "#aec7e8",
         # EXP-66/67/68. llm-d is brown because orange is the deep-research
         # class colour; the prefix-aware FluidServe arms keep the FluidServe
         # hue because they are the same control plane, and are separated by
         # line style below. fig_sweep builds its arm list as
         # [a for a in ARM_C if a in set(df["arm"])], so an unregistered arm is
         # dropped from the figure in silence.
         # fspfx started on the FluidServe blue because it is the same control
         # plane, which made the control and the treatment the same colour on
         # every figure that carries both -- exactly the case this comparison
         # exists for. Cyan reads as the same family and is distinguishable.
         "llmdslo": "#8c564b", "fspfx": "#17becf", "fspfxb": "#bcbd22",
         # EXP-73's three ablations of the v0.2 binary. Same control plane, so
         # they could share its hue, but this sweep exists to separate them from
         # the control, so each gets its own.
         "fspnopend": "#9467bd", "fspnoaff": "#e377c2", "fspslos": "#7f7f7f",
         # EXP-77. Deep purple, and the colour it is deliberately not: this arm
         # was first given a terracotta (#b56349), which sits in the same family
         # as llm-d's brown (#8c564b). Those two curves carry the comparison the
         # arm exists for -- routing by prefix affinity against routing by a
         # per-request latency prediction -- so they are the two that must be
         # separable at a glance, and they were the two hardest to tell apart.
         "vllmcache": "#7b3294",
         # EXP-78, the admission axis. Both are the same control plane as
         # fspfx with pieces removed, so they take shades of its cyan family
         # rather than new hues -- the figure they appear on compares them
         # against fspfx, not against the baselines.
         "fsnoshed": "#0e7c86", "fsroute": "#6bbfc6",
         # EXP-79, the independent-combination arm. Same control plane as
         # fspfx with the shed test decoupled, so it stays in the cyan family.
         "fsindep": "#1a5c7a",
         # EXP-94, the class-preference STRENGTH axis. Shades of one hue rather
         # than four hues, because these are one axis sampled at four points and
         # the figure has to read as a ladder rather than as four policies.
         "fscount": "#d62728",
         "fsw00": "#1f77b4", "fsw25": "#4c9fd4", "fsw50": "#7fbfe0", "fsw75": "#a8d5ec",
         # EXP-95, the pin ladder. A second one-axis family, kept away from the
         # weight family so the two ladders are not mistaken for one.
         "fspinc1": "#d62728", "fspinc2": "#e06666", "fspinc3": "#f0a6a6"}
ARM_L = {"polyserve": "PolyServe", "slo": "Llumnix SLO",
         # EXP-98.
         "fsboth": "FluidServe (per-inst. corr. + pace cap)",
         "fsinterleave": "FluidServe (+ interleave-aware first-token estimate)",
         "fsv3": "FluidServe v0.3",
         "fsv3noaff": "FluidServe v0.3 (class preference off)",
         # EXP-121. The label states the budgets rather than "halved", because a
         # relative word is only readable beside the arm it is relative to.
         "fsv3capgnofrcc25d50s38":
             "FluidServe v0.4 (per-token 25/50/38 ms)",
         "polyservepc25d50s38":
             "PolyServe (paper mech., per-token 25/50/38 ms)",
         # EXP-123. The label states both budget sets, because these arms sit on
         # figures beside columns scored at first-token 5/10/7 s and per-token
         # 50/100/75 ms and the two sets of numbers may not be read as one.
         "fsv3capgnofrcc25d50s38ftc2500d5000s3500":
             "FluidServe v0.4 (first-token 2.5/5/3.5 s, per-token 25/50/38 ms)",
         "polyservepc25d50s38ftc2500d5000s3500":
             "PolyServe (paper mech., first-token 2.5/5/3.5 s, "
             "per-token 25/50/38 ms)",
         "fsv3b40": "FluidServe v0.3 (swe budget 40 s)",
         "fsv3noaffb40": "FluidServe v0.3 (swe 40 s, preference off)",
         "fsv3cap": "FluidServe v0.3 (+ class-instance cap)",
         "fsv3nofrc": "FluidServe v0.3 (force off)",
         "fsv3capnofrc": "FluidServe v0.3 (cap, force off)",
         "fsv3capg": "FluidServe v0.3 (+ cap, guardrail form)",
         "fsv3capgnofrc": "FluidServe v0.3 (guardrail cap, force off)",
         "fsv3capgnofrct75": "FluidServe v0.4 (class-instance cap ON)",
         "fsv3capgnofrct75cc": "FluidServe v0.4 (cap, free gates allowed)",
         "fsv3capgnofrct75ml": "FluidServe v0.4 (level memory test, 0.95 pool)",
         "fsv3capgnofrct75ml85": "FluidServe v0.4 (level memory test, 0.85 pool)",
         "fsv3gnofrct75nocap": "FluidServe v0.4 (class-instance cap OFF)",
         "polyservept75": "PolyServe (paper mechanisms, per-token swe)",
         "loadbalance": "Llumnix load-balance",
         "fsv3capgnofrct75dl": "FluidServe v0.4 (+ first-token deadline in feasibility)",
         "fsv3ft": "FluidServe v0.4 (+ full-iteration queue drain)",
         "fsv3ftq": "FluidServe v0.4 (+ resident prompt waits for the queue)",
         "fsv3ftboth": "FluidServe v0.4 (+ both first-token corrections)",
         "fsv3dl": "FluidServe v0.4 (+ first-token deadline in feasibility)",
         "fsv3dlftboth": "FluidServe v0.4 (+ deadline + both corrections)",
         "fsv3ftml": "FluidServe v0.4 (+ corrections + level memory test)",
         "fsv3ftms85": "FluidServe v0.4 (+ corrections + 0.85 pool)",
         # EXP-124, the arrivals term. Registered before the runs exist: ARM_L
         # is read as a lookup with a fallback to the raw arm name, so a missing
         # entry does not abort, it prints the directory token as the legend.
         "fsv3ah3": "FluidServe v0.4 (+ arrivals term, window 3)",
         "fsv3ah1": "FluidServe v0.4 (+ arrivals term, window 1)",
         "fsv3ms97": "FluidServe v0.4 (0.97 pool, no arrivals term)",
         "fsv3ah3ms97": "FluidServe v0.4 (+ arrivals term, window 3, 0.97 pool)",
         "fsdelay": "FluidServe (+ per-inst. delay in deadline test)",
         "fsdeadfix": "FluidServe (+ delay, deadline in feasibility)",
         "fsnaboth": "FluidServe (pref. off + corr. + pace cap)",
         "fsdead": "FluidServe (+ first-token deadline)",
         "fscorr": "FluidServe (per-inst. corr.)",
         "fspacecap": "FluidServe (pace cap)",

         "fluidserve": "FluidServe (prefix acct. off)", "fluidserveflat": "FluidServe (v20 off)",
         "slofifo": "Llumnix SLO + FIFO", "sloqoserve": "Llumnix SLO + QoServe",
         "fluidservefifo": "FluidServe + FIFO",
         "fluidserveqoserve": "FluidServe + QoServe",
         "llmdslo": "llm-d", "fspfx": "FluidServe v0.2",
         "fspfxb": "FluidServe v0.2 (calibration fixed)",
         "fspnopend": "FluidServe, holding off",
         "fscount": "FluidServe, pref. by count",
         "fsw00": "FluidServe, pref. weight 0",
         "fsw25": "FluidServe, pref. weight 0.25",
         "fsw50": "FluidServe, pref. weight 0.5",
         "fsw75": "FluidServe, pref. weight 0.75",
         "fspinc1": "FluidServe, chat on 1 engine",
         "fspinc2": "FluidServe, chat on 2 engines",
         "fspinc3": "FluidServe, chat on 3 engines",
         "fspnoaff": "FluidServe, class preference off",
         "fspslos": "FluidServe, both off",
         "vllmcache": "vLLM router (cache-aware)",
         "fsnoshed": "FluidServe, rejection off",
         "fsroute": "FluidServe, rejection and holding off",
         "fsindep": "FluidServe, rejection decided independently"}
# Line style per arm for the latency CDFs, where colour already encodes class.
ARM_LS = {"fluidserve": "-", "slo": "--", "polyserve": ":",
          "fsboth": "-", "fsnaboth": "--", "fsdelay": "-", "fsdeadfix": "-", "fsinterleave": "-", "fsv3": "-", "fsv3noaff": "--", "fsv3b40": "-", "fsv3noaffb40": "--", "fsdead": "-", "fscorr": "-", "fspacecap": "-",
          "fsv3cap": "-", "fsv3nofrc": "--", "fsv3capnofrc": "-.",
          "fsv3capg": "-", "fsv3capgnofrc": "-.",
          "fsv3capgnofrct75": "-", "fsv3gnofrct75nocap": "--",
          "fsv3capgnofrct75cc": "-.",
          "fsv3capgnofrct75ml": "-",
          "fsv3capgnofrct75ml85": "--",
          "polyservept75": ":", "loadbalance": "-.",
          "fsv3capgnofrct75dl": "--",
          "fsv3ft": "--", "fsv3ftq": ":", "fsv3ftboth": "-",
          "fsv3dl": "-.", "fsv3dlftboth": "-",
          "fsv3ftml": "-", "fsv3ftms85": "--",
          # EXP-124, the arrivals term.
          "fsv3ah3": "-", "fsv3ah1": "--",
          "fsv3ms97": ":", "fsv3ah3ms97": "-.",
          # EXP-121. Dashed, so that on a figure carrying both budget settings
          # the halved arm is distinguishable from the full-budget arm of the
          # same colour without reading the legend.
          "fsv3capgnofrcc25d50s38": "--", "polyservepc25d50s38": "--",
          # EXP-123. Dash-dot, so a figure carrying all three budget settings
          # separates full budgets (solid), per-token halved (dashed) and both
          # halved (dash-dot) without reading the legend.
          "fsv3capgnofrcc25d50s38ftc2500d5000s3500": "-.",
          "polyservepc25d50s38ftc2500d5000s3500": "-.",
          "fluidservefifo": "-", "fluidserveqoserve": "--",
          "slofifo": "-.", "sloqoserve": ":",
          "llmdslo": "-", "fspfx": "--", "fspfxb": "-.",
          "fspnopend": "--", "fspnoaff": ":", "fspslos": "-.",
          "vllmcache": "-", "fsnoshed": "--", "fsroute": ":", "fsindep": "-."}
MIX_TITLE = {
    "m1": "m1 balanced\n31/37/31% of input tokens",
    "m2": "m2 chat-heavy\n64/19/16%",
    "m3": "m3 agent-heavy\n19/19/63%",
}
# The rules attainment is scored against, drawn as reference lines.
TTFT_BUDGET = {"chat": 5.0, "deepresearch": 10.0, "swe": None}
ITL_BUDGET = {"chat": 50.0, "deepresearch": 100.0, "swe": None}


# The runner's condition is set in requests per MINUTE, which is what the run
# directory is named after, but every rate in the analysis and in the capacity
# arithmetic is per SECOND. Converting once here keeps the figures in the unit the
# reasoning is done in and leaves the directory names alone.
def rps(rpm):
    return np.asarray(rpm, dtype=float) / 60.0


def rpm_of(d):
    m = re.search(r"_rpm_(\d+)", os.path.basename(d))
    return int(m.group(1)) if m else None


def mix_of(d):
    """The mix key, with the "f" variant folded onto the mix it is made of.

    m1f is m1: the same three workloads at the same ratio, the same arrival
    process and the same seed. The only difference is the (ttft_ms, tbt_ms) pair
    the config declares for the agent class, which exists so that a policy with
    no end-to-end mode is told a pace comparable to the one FluidServe derives
    instead of the default decomposition's 25 ms. Scoring is on the real 30 s
    end-to-end budget in both cases, so the runs are measurements of the same
    condition and belong on the same panel.

    Before this, the pattern required "_m1_rpm_" and the m1f runs matched
    nothing, so collect() dropped them and the Llumnix SLO arm was silently
    absent from a figure that named it in the note.
    """
    # The workload-configuration key in the directory name. EXP-108 onwards
    # names it t75 (the per-token form of the agent class's promise) rather
    # than m1/m2/m3, and a key this function does not recognise makes
    # collect() drop the run with no message, so every t75 sweep drew
    # nothing until 2026-09-08.
    m = re.search(r"_(m[123]|t75)f?(?:air)?_rpm_", os.path.basename(d))
    return m.group(1) if m else None


def collect(patterns):
    rows = []
    for p in patterns:
        for d in sorted(glob.glob(p)):
            r = load_run(d)
            if r is None or r.empty:
                continue
            w = r["rel"].max() - r["rel"].min()
            if w <= 0:
                continue
            rows.append({
                "dir": d, "arm": arm_of(d), "mix": mix_of(d), "rpm": rpm_of(d),
                "attain": per_request(r, "violate_served"),
                "attain_off": per_request(r, "violate_offered"),
                "goodput": goodput_tokens(r, w),
                # Total output tokens per second, SLO or not. The pair
                # (throughput, goodput) is what separates "the engines are
                # busy" from "the work is arriving in time", and the two can
                # move in opposite directions: EXP-53 measured 20,057 and
                # 20,212 total tokens/s from two policies whose goodput was 710
                # and 9,064.
                "tokens": total_tokens(r, w),
                "rejected": 100.0 * r["rejected"].mean(),
            })
    return pd.DataFrame([r for r in rows if r["rpm"] and r["mix"]])


# ---------------------------------------------------------------- sweep figure
def fig_sweep(df, out, title):
    mixes = [m for m in ("m1", "m2", "m3", "t75") if m in set(df["mix"])]
    arms = [a for a in ARM_C if a in set(df["arm"])]
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(1, len(mixes), figsize=(3.6 * len(mixes), 3.4),
                                 sharey=True)
        axes = np.atleast_1d(axes)
        for ax, mix in zip(axes, mixes):
            ax2 = ax.twinx()
            for a in arms:
                # Column names avoid `at`, `iat`, `loc`: those are DataFrame
                # indexer attributes, so `d.at` silently returns the indexer
                # rather than the column and the arithmetic fails far from here.
                d = df[(df.arm == a) & (df.mix == mix)].groupby("rpm").agg(
                    sloA=("attain", "mean"), sloLo=("attain", "min"),
                    sloHi=("attain", "max"), gp=("goodput", "mean"),
                    gpLo=("goodput", "min"), gpHi=("goodput", "max"),
                ).reset_index().sort_values("rpm")
                if d.empty:
                    continue
                c = ARM_C[a]
                ax.errorbar(rps(d.rpm), d.sloA,
                            yerr=[d.sloA - d.sloLo, d.sloHi - d.sloA],
                            color=c, ls="-", marker="o", capsize=2)
                ax2.errorbar(rps(d.rpm), d.gp,
                             yerr=[d.gp - d.gpLo, d.gpHi - d.gp],
                             color=c, ls="--", marker="s", ms=3.5, alpha=0.75,
                             capsize=2)
            ax.set_title(MIX_TITLE.get(mix, mix))
            ax.set_xlabel("offered rate (requests/s)")
            ax.set_xticks(rps(sorted(df.rpm.unique())))
            ax.set_ylim(0, 105)
            ax2.set_ylim(0, 19000)
            ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
            if ax is axes[0]:
                ax.set_ylabel("SLO attainment (%), admitted, per request")
            if ax is axes[-1]:
                ax2.set_ylabel("goodput (output tokens/s)")
            else:
                ax2.set_yticklabels([])
        handles, labels = [], []
        for a in arms:
            handles.append(plt.Line2D([], [], color=ARM_C[a], ls="-", marker="o"))
            labels.append(f"{ARM_L[a]} — attainment (left)")
            handles.append(plt.Line2D([], [], color=ARM_C[a], ls="--", marker="s",
                                      ms=3.5, alpha=0.75))
            labels.append(f"{ARM_L[a]} — goodput (right)")
        fig.legend(handles, labels, loc="lower center", ncol=2,
                   bbox_to_anchor=(0.5, 1.0), frameon=False)
        fig.suptitle(title, y=1.16, fontsize=9)
        fig.tight_layout()
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"wrote {out}")


# --------------------------------------------------------------- engine figure
E_RUN = "vllm:num_requests_running"
E_WAIT = "vllm:num_requests_waiting"
E_KV = "vllm:kv_cache_usage_perc"


def engine_series(run):
    out = {}
    for path in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        t, run_, wait, kv = [], [], [], []
        for line in open(path):
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not rec.get("ok"):
                continue
            def pick(pref):
                for k, v in rec.items():
                    if k.startswith(pref) and isinstance(v, (int, float)):
                        return float(v)
                return 0.0
            t.append(rec["t"]); run_.append(pick(E_RUN))
            wait.append(pick(E_WAIT)); kv.append(pick(E_KV) * 100)
        if t:
            name = os.path.basename(path).replace("engine_", "").replace(".jsonl", "")
            t0 = t[0]
            out[name] = (np.array(t) - t0, np.array(run_), np.array(wait), np.array(kv))
    return out


def fig_engines(runs, out, title):
    """runs: list of (label, run_dir). One column per run, three rows."""
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(3, len(runs), figsize=(4.2 * len(runs), 6.2),
                                 sharex=True)
        axes = np.atleast_2d(axes)
        if axes.shape[0] != 3:
            axes = axes.T
        cols = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]
        for j, (label, run) in enumerate(runs):
            eng = engine_series(run)
            for i, (name, ser) in enumerate(sorted(eng.items())):
                t, r, w, kv = ser
                c = cols[i % len(cols)]
                axes[0][j].plot(t / 60, r, color=c, lw=1.0, label=f"engine {name}")
                axes[1][j].plot(t / 60, w, color=c, lw=1.0)
                axes[2][j].plot(t / 60, kv, color=c, lw=1.0)
            axes[0][j].set_title(label)
            axes[2][j].set_xlabel("time in condition (min)")
            for ax in axes[:, j]:
                ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        axes[0][0].set_ylabel("requests decoding")
        axes[1][0].set_ylabel("requests queued")
        axes[2][0].set_ylabel("KV pool used (%)")
        axes[2][0].set_ylim(0, 105)
        # A shared y range per row makes the columns comparable at a glance;
        # without it the idle arm is autoscaled up and looks as busy as the
        # saturated one.
        for row in range(3):
            lo = min(ax.get_ylim()[0] for ax in axes[row])
            hi = max(ax.get_ylim()[1] for ax in axes[row])
            for ax in axes[row]:
                ax.set_ylim(lo, hi)
        axes[0][0].legend(fontsize=6, ncol=2, loc="upper left")
        fig.suptitle(title, y=1.0, fontsize=9)
        fig.tight_layout()
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"wrote {out}")


# -------------------------------------------------------------- latency figure
def fig_latency(cells, out, title):
    """cells: list of (row_label, {arm: run_dir}). TTFT and ITL CDFs per class."""
    with plt.rc_context(PAPER_STYLE):
        fig, axes = plt.subplots(len(cells), 2, figsize=(7.6, 3.0 * len(cells)))
        axes = np.atleast_2d(axes)
        for i, (row_label, arms) in enumerate(cells):
            for arm, run in arms.items():
                r = load_run(run)
                if r is None:
                    continue
                # Admitted requests only: a rejected one has no latency to plot,
                # and counting it as infinite would hide the shape of what was
                # actually served. The rejection rate is in the sweep figure.
                r = r[(~r["cutoff"]) & (~r["rejected"])]
                # One line style per arm. This was solid for FluidServe and
                # dashed for everything else, which drew the Llumnix SLO arm and
                # PolyServe on top of each other in the same style while the
                # legend named only one of them.
                ls = ARM_LS.get(arm, "--")
                for c in CLASSES:
                    s = r[r["class"] == c]
                    for j, (col, budget) in enumerate(
                            [("first_token_latency", TTFT_BUDGET[c]),
                             ("itl_ms", ITL_BUDGET[c])]):
                        v = pd.to_numeric(s[col], errors="coerce").dropna()
                        if v.empty:
                            continue
                        v = np.sort(v.values)
                        y = np.arange(1, len(v) + 1) / len(v) * 100
                        axes[i][j].plot(v, y, color=CLASS_COLORS[c], ls=ls, lw=1.2)
                        if budget:
                            axes[i][j].axvline(budget, color=CLASS_COLORS[c],
                                               lw=0.6, alpha=0.45)
            axes[i][0].set_xscale("log")
            axes[i][0].set_xlabel("time to first token (s)")
            axes[i][1].set_xlabel("mean time between tokens (ms)")
            axes[i][1].set_xlim(0, 120)
            for ax in axes[i]:
                ax.set_ylabel(f"{row_label}\npercentile")
                ax.set_ylim(0, 100)
                ax.grid(ls=":", lw=0.7, alpha=0.6)
        handles = [plt.Line2D([], [], color=CLASS_COLORS[c], lw=1.2) for c in CLASSES]
        labels = list(CLASSES)
        drawn = [x for x in ARM_LS if any(x in arms for _, arms in cells)]
        handles += [plt.Line2D([], [], color="#666666", ls=ARM_LS[x], lw=1.2)
                    for x in drawn]
        handles += [plt.Line2D([], [], color="#666666", lw=0.6, alpha=0.45)]
        labels += [ARM_L[x] for x in drawn] + ["SLO budget"]
        # Legend below the axes, title above. Both were above and collided:
        # seven entries wrap or run the full width, and no vertical offset
        # separates them reliably at every figure height.
        fig.legend(handles, labels, loc="upper center", ncol=len(labels),
                   bbox_to_anchor=(0.5, 0.0), frameon=False, fontsize=7)
        fig.suptitle(title, y=1.02, fontsize=9)
        fig.tight_layout()
        fig.savefig(out, dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"wrote {out}")


def find(pattern):
    hits = sorted(glob.glob(pattern))
    return hits[0] if hits else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pass1", nargs="+", default=["results/*exp27r1_*"])
    ap.add_argument("--pass2", nargs="+", default=["results/*exp27p2*"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--runs", nargs="+", default=None,
                    help="produce the standard set for an arbitrary sweep and "
                         "stop, instead of the EXP-27 pass layout below")
    ap.add_argument("--title", default="")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    if a.runs:
        # The EXP-27 layout below hard-codes that experiment's pass globs, which
        # made every later sweep either edit this file or go without figures.
        # This branch takes a glob and produces the four things the figure-set
        # convention asks for: attainment on both denominators, goodput, what
        # each engine was doing, and latency against the budgets it is scored on.
        d = collect(a.runs)
        if d.empty:
            sys.exit(f"no runs matched {a.runs}")
        rates = sorted(set(d["rpm"]))
        arms = sorted(set(d["arm"]))
        print(f"drawing arms {arms} at {rates} rpm from {len(d)} runs")
        fig_split(d, os.path.join(a.out_dir, "sweep"),
                  a.title or "attainment and goodput", a.note)
        # Engines and latency are per condition, so use the highest rate at
        # which every arm has a run: that is where the policies differ most and
        # where a missing arm would be most misleading.
        for rpm in reversed(rates):
            cell = {}
            for arm in arms:
                m = d[(d.arm == arm) & (d.rpm == rpm)]
                if not m.empty:
                    cell[arm] = m.iloc[0]["dir"]
            if len(cell) == len(arms):
                fig_engines([(f"{arm}, {rpm} rpm", run) for arm, run in cell.items()],
                            os.path.join(a.out_dir, f"engines_{rpm}.png"),
                            f"What each of the four engines was doing ({rpm} rpm)")
                fig_latency([(f"{rpm} rpm", cell)],
                            os.path.join(a.out_dir, f"latency_{rpm}.png"),
                            "Latency of ADMITTED requests against the budgets "
                            "they are scored on")
                break
        return 0

    d1 = collect(a.pass1)
    fig_sweep(d1, os.path.join(a.out_dir, "exp27_sweep_pass1.png"),
              "EXP-27 pass 1 (v19+v20, one run per condition): "
              "three mixes, four engines, 8 min per condition")

    # pass 3 (polyserve, fluidserve) and pass 5 (the Llumnix SLO baseline at the
    # FAIR setting) ran in different sessions, so this is a cross-session
    # comparison and the figure has to say by how much that matters.
    #
    # The size is measurable without any modelling, because PolyServe's code did
    # not change across any of these passes: five runs of PolyServe on m1 spanning
    # three sessions read, per request on the admitted denominator,
    #    20 req/s  99.9 100.0 100.0 100.0 100.0   range 0.1
    #    40 req/s  53.7  54.3  55.0  55.1  55.7   range 2.0
    #    80 req/s  32.1  32.6  33.0  33.1  36.7   range 4.6
    # The movement grows with rate because at 20 req/s every placement is feasible
    # for every policy and the order requests arrive in cannot change the outcome,
    # whereas at 80 req/s every policy is deciding at its own feasibility boundary
    # and a shift in which requests coincide flips individual decisions, each of
    # which changes the state the next decision reads.
    #
    # pass 4 (the Llumnix SLO baseline at the 25 ms/token setting) is deliberately
    # NOT drawn: that setting judged the agent class 2.3x tighter than FluidServe
    # judged it and rejected 98% of it, so its curve is of a policy that was
    # answering a different question.
    d3 = collect(["results/*exp27p3*", "results/*exp27p5*"])
    if not d3.empty:
        note = ("m1 balanced, four engines, 8 min per condition, "
                "two repeats (bars = min..max)")
        if "slo" in set(d3["arm"]):
            note += ("\nLlumnix SLO: fair setting, one run, separate session; "
                     "cross-session movement here is 0.1 pt at 20 to 4.6 pt at 80")
        fig_split(d3, os.path.join(a.out_dir, "exp27_pass3"),
                  "EXP-27 (v22: re-decision reserve + queued-prefill price)", note)

    d2 = collect(a.pass2)
    if not d2.empty:
        fig_sweep(d2[d2.arm != "fluidserveflat"],
                  os.path.join(a.out_dir, "exp27_sweep_pass2_m1.png"),
                  "EXP-27 pass 2 (v21, two repeats, bars = min..max): m1 balanced")

    p = {arm: find(f"results/*exp27r1_{arm}_m1_rpm_2400") for arm in
         ("polyserve", "fluidserve")}
    q = {arm: find(f"results/*exp27r1_{arm}_m1_rpm_4800") for arm in
         ("polyserve", "fluidserve")}
    if all(p.values()):
        fig_engines([("PolyServe, m1, 2400 rpm", p["polyserve"]),
                     ("FluidServe, m1, 2400 rpm", p["fluidserve"])],
                    os.path.join(a.out_dir, "exp27_engines_m1_2400.png"),
                    "What each of the four engines was doing (m1, 2400 rpm)")
    if all(p.values()) and all(q.values()):
        fig_latency([("m1, 2400 rpm", p), ("m1, 4800 rpm", q)],
                    os.path.join(a.out_dir, "exp27_latency_m1.png"),
                    "Latency of ADMITTED requests against the budgets they are "
                    "scored on")
    return 0



def _titled(ax, title, note, width=64, note_width=78):
    """Set a title, wrapping it so the canvas is not stretched by one long line.

    `savefig(bbox_inches="tight")` crops the canvas to the ink, so a title given
    as a single unwrapped string makes the saved image as wide as that string
    rather than as wide as the axes. An EXP-68 figure came out 7,334 px wide with
    the plot occupying about a seventh of it, because the caveat note was one
    283-character line. Wrapping is what keeps the axes the widest thing on the
    canvas; the width is in characters because the title font size is fixed by
    PAPER_STYLE.
    """
    txt = textwrap.fill(title, width)
    if note:
        txt += "\n" + textwrap.fill(note, note_width)
    ax.set_title(txt, fontsize=7.5)


def fig_split(df, out_prefix, title, note=""):
    """Attainment and goodput as two separate figures.

    The dual-axis version keeps the two in view together, which is the right
    default because a policy can raise attainment by refusing work. Split, each
    gets its own scale and can be read precisely, so the attainment panel carries
    BOTH denominators and the rejection rate is annotated on the points that have
    one: the gap between the solid and faint lines is exactly what the rejections
    cost, and separating the figures must not separate that.
    """
    arms = [a for a in ARM_C if a in set(df["arm"])]

    def agg(a):
        return df[df.arm == a].groupby("rpm").agg(
            sloA=("attain", "mean"), sloALo=("attain", "min"),
            sloAHi=("attain", "max"),
            sloO=("attain_off", "mean"), sloOLo=("attain_off", "min"),
            sloOHi=("attain_off", "max"),
            gp=("goodput", "mean"), gpLo=("goodput", "min"),
            gpHi=("goodput", "max"), rej=("rejected", "mean"),
            tk=("tokens", "mean"), tkLo=("tokens", "min"),
            tkHi=("tokens", "max"),
        ).reset_index().sort_values("rpm")

    with plt.rc_context(PAPER_STYLE):
        fig, ax = plt.subplots(figsize=(4.6, 3.4))
        for a in arms:
            d, c = agg(a), ARM_C[a]
            ax.errorbar(rps(d.rpm), d.sloA,
                        yerr=[d.sloA - d.sloALo, d.sloAHi - d.sloA],
                        color=c, ls="-", marker="o", capsize=2,
                        label=f"{ARM_L[a]} — admitted")
            ax.errorbar(rps(d.rpm), d.sloO,
                        yerr=[d.sloO - d.sloOLo, d.sloOHi - d.sloO],
                        color=c, ls=":", marker="^", ms=3.5, alpha=0.6, capsize=2,
                        label=f"{ARM_L[a]} — offered")
        # The rejection rate is not annotated on the points. With three arms the
        # labels collide at exactly the rates where the arms are closest, which is
        # where the figure has to be readable. The gap between an arm's solid line
        # (admitted) and its dotted line (offered) already IS the rejection cost,
        # measured on the same axis, so the information is in the figure without
        # the text; the numbers are in the table.
        ax.set_xlabel("offered rate (requests/s)")
        ax.set_ylabel("SLO attainment (%), per request")
        ax.set_xticks(rps(sorted(df.rpm.unique())))
        ax.set_ylim(0, 105)
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(loc="lower left", fontsize=7)
        _titled(ax, title, note)
        fig.tight_layout()
        fig.savefig(f"{out_prefix}_attainment.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

        fig, ax = plt.subplots(figsize=(4.6, 3.4))
        for a in arms:
            d, c = agg(a), ARM_C[a]
            ax.errorbar(rps(d.rpm), d.gp, yerr=[d.gp - d.gpLo, d.gpHi - d.gp],
                        color=c, ls="-", marker="s", capsize=2, label=ARM_L[a])
        ax.set_xlabel("offered rate (requests/s)")
        # Short label, definition in the title: the long form overflows the axes
        # box at this figure width and the leading characters are clipped.
        ax.set_ylabel("goodput (output tokens/s)")
        ax.set_xticks(rps(sorted(df.rpm.unique())))
        ax.set_ylim(0, None)
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        ax.legend(loc="upper left", fontsize=7)
        _titled(ax, "Token goodput — output tokens/s from requests that met "
                    "their SLO. " + title, note)
        fig.tight_layout()
        fig.savefig(f"{out_prefix}_goodput.png", dpi=300, bbox_inches="tight")
        plt.close(fig)

        # Throughput beside goodput on one axis. They are the same unit and the
        # gap between them IS the wasted work, so here the second line earns its
        # place on the same axes rather than in a second figure: what is being
        # read is the distance, not either value to three digits. Solid is every
        # output token the fleet produced; dotted is the part that arrived
        # inside its own latency rule.
        fig, ax = plt.subplots(figsize=(4.6, 3.4))
        for a_ in arms:
            d, c = agg(a_), ARM_C[a_]
            ax.errorbar(rps(d.rpm), d.tk, yerr=[d.tk - d.tkLo, d.tkHi - d.tk],
                        color=c, ls="-", marker="o", capsize=2,
                        label=f"{ARM_L[a_]} — all output")
            ax.errorbar(rps(d.rpm), d.gp, yerr=[d.gp - d.gpLo, d.gpHi - d.gp],
                        color=c, ls=":", marker="^", ms=3.5, alpha=0.6, capsize=2,
                        label=f"{ARM_L[a_]} — met its rule")
        ax.set_xlabel("offered rate (requests/s)")
        ax.set_ylabel("output tokens/s")
        ax.set_xticks(rps(sorted(df.rpm.unique())))
        ax.set_ylim(0, None)
        ax.grid(axis="y", ls=":", lw=0.7, alpha=0.6)
        # Below the axes, not inside them. With four arms this legend is eight
        # entries, and inside the axes it covered 20-25 req/s on the upper left
        # -- which is exactly where llm-d's total output peaks before turning
        # down, the single most informative feature on the figure.
        ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=2,
                  fontsize=7, frameon=False)
        _titled(ax, "Throughput and goodput — the gap is output the fleet "
                    "produced that arrived too late. " + title, note)
        fig.tight_layout()
        fig.savefig(f"{out_prefix}_throughput.png", dpi=300, bbox_inches="tight")
        plt.close(fig)
    print(f"wrote {out_prefix}_attainment.png, {out_prefix}_goodput.png "
          f"and {out_prefix}_throughput.png")
if __name__ == "__main__":
    sys.exit(main())
