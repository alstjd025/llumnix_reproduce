#!/usr/bin/env python3
"""Three policies side by side at one offered rate, engine layer.

`plot_ratesweep_split.py` draws six panels for ONE condition, which is the right
shape for reading a single run in detail and the wrong shape for the question
this experiment asks, which is what three policies do differently with the same
four engines. This puts one policy per column and one quantity per row, on
shared y axes, so a difference between columns is a difference between policies
and not between scales.

Four rows, chosen because each answers something the request-level table cannot:

  running batch   whether the fleet is being used evenly. PolyServe's tier
                  partition shows up here directly: at 60 req/s two of its
                  engines carry about 1,000 and 700 requests while the other two
                  carry about 20.
  engine queue    work the engine has accepted but not started. A policy with no
                  admission control puts its backlog here, where it costs the
                  requests already running; a policy that holds at the gateway
                  does not.
  KV occupancy    how close each engine is to the memory bound, which is what
                  triggers preemption and recompute.
  decode tok/s    the fleet's actual output rate, summed. Attainment says how
                  many requests met their rule; this says how much work the
                  fleet did to get there.

Prefix hit rate and queueing time are deliberately left to the per-condition
figure: they are per engine and would need a fifth and sixth row that duplicate
what the first two already show, at a third of the height.

  python3 exp38_policy_compare.py --runs 'results/*exp38r1*' \
      --out-dir results/aggregate_analysis/exp38_engines/compare
"""
import argparse
import glob
import json
import os
import re
import sys

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import PAPER_STYLE, arm_of  # noqa: E402

# An arm missing from ARM_ORDER is dropped from the figure and the script still
# writes a plausible-looking PNG with the remaining columns, so adding an arm
# here is part of running a new experiment. On 2026-08-08 an EXP-68 comparison
# was drawn with only the llm-d column because `fspfx` was not listed; the
# `arms=[...]` line the script prints at the end is what caught it, and the
# check below turns that from something to notice into something that fails.
ARM_ORDER = ["polyserve", "slo", "loadbalance", "llmdslo",
             "fluidserve", "fspfx", "fspfxb",
             # EXP-108/109. The per-token form of the agent class's promise.
             # Registered before the figures are drawn, because the check below
             # aborts on an unregistered arm and that abort is the point.
             "fsv3capgnofrct75", "fsv3capgnofrct75cc",
             "fsv3capgnofrct75ml", "fsv3capgnofrct75ml85",
             # EXP-116/118. The eight-instance static sweep and the first-token
             # estimate corrections, registered before the runs exist for the
             # reason given above.
             "fsv3capgnofrct75dl",
             "fsv3ft", "fsv3ftq", "fsv3ftboth",
             "fsv3dl", "fsv3dlftboth",
             "fsv3ftml", "fsv3ftms85",
             # EXP-124 (2026-09-10). The arrivals term in the KV projection: two window
             # lengths at the shipped 0.95 memory ceiling, the 0.97 ceiling alone, and the
             # pairing. Registered BEFORE the runs exist, because every one of these tables
             # is turned into an arm list as [k for k in <table> if k in <data>] and an arm
             # missing from the table is dropped from the figure with no message, leaving a
             # plausible legend over an incomplete comparison. FluidServe-family colours,
             # kept away from #d62728 (PolyServe) and #1f77b4 (the control arm these four
             # are read against, which must stay visually distinct from its own variants).
             "fsv3ah3", "fsv3ah1", "fsv3ms97", "fsv3ah3ms97",
             # EXP-121. The halved per-token budgets, registered before the runs
             # exist because the check below ABORTS on an arm that has runs and
             # no entry here, and that abort is the point.
             "fsv3capgnofrcc25d50s38", "polyservepc25d50s38",
             # EXP-123. The same two policies with the first-token budgets
             # halved as well, registered before the runs exist for the same
             # reason: the check below ABORTS on an arm that has runs and no
             # entry here, and that abort is what stops a "comparison" figure
             # being drawn from whichever arms happened to be registered.
             "fsv3capgnofrcc25d50s38ftc2500d5000s3500",
             "polyservepc25d50s38ftc2500d5000s3500",
             "llmdslot75", "polyservept75",
             "slot75", "vllmcachet75",
             # EXP-73's ladder. Registered before that sweep's engine-layer
             # figures are drawn rather than after, because the check below
             # aborts on an unregistered arm and the abort is the point: an arm
             # missing here used to be dropped from the figure in silence.
             "fspnopend", "fspnoaff", "fspslos", "vllmcache",
             "fsnoshed", "fsroute", "fsindep",
             # EXP-94 weight sweep and EXP-95 pin ladder.
             "fsw00", "fsw25", "fsw50", "fsw75", "fscount",
             "fspinc1", "fspinc2", "fspinc3",
             # EXP-93/97. `fsnoaff` is the class term removed entirely, which is
             # not the same arm as `fspnoaff` (that name belongs to EXP-73's
             # ladder and its runs sit in a different session).
             "fsnoaff",
             # EXP-98: the per-instance correction and the pace cap in the
             # memory predicate, separately and together.
             "fscorr", "fspacecap", "fsboth", "fsdelay", "fsdeadfix", "fsinterleave", "fsv3", "fsv3noaff", "fsv3b40", "fsv3noaffb40",
             # EXP-107: the class-instance cap and the force branch off,
             # separately and together, over the fsv3 base.
             "fsv3cap", "fsv3nofrc", "fsv3capnofrc", "fsv3capg", "fsv3capgnofrc",
             # EXP-99: the same two changes with the class preference OFF.
             "fsnaboth",
             # EXP-100.
             "fsdead",
             # EXP-114: the v0.4 candidate with the first-token deadline.
             "fsv3capgnofrct75dl"]
ARM_LABEL = {"fsv3capgnofrct75": "FluidServe v0.4", "llmdslot75": "llm-d",
             "fsv3capgnofrct75cc": "FluidServe v0.4 (free gates)",
             "fsv3capgnofrct75ml": "FluidServe v0.4 (level memory test)",
             "fsv3capgnofrct75ml85": "FluidServe v0.4 (level memory test, 0.85 pool)",
             "fsv3capgnofrct75dl": "FluidServe (deadline)",
             # EXP-124, the arrivals term in the KV projection. The window
             # length and the memory ceiling are both in the label, because a
             # figure carrying all four puts two settings of each beside the
             # control and neither may be inferred from the position.
             "fsv3ah3": "FluidServe v0.4\n(+ arrivals term,\nwindow 3)",
             "fsv3ah1": "FluidServe v0.4\n(+ arrivals term,\nwindow 1)",
             "fsv3ms97": "FluidServe v0.4\n(0.97 pool,\nno arrivals term)",
             "fsv3ah3ms97": "FluidServe v0.4\n(+ arrivals term,\nwindow 3, 0.97 pool)",
             # EXP-121. Both labels carry the three per-token budgets, because
             # this figure puts them beside columns scored at 50 / 100 / 75 and
             # the two sets of numbers may not be read as one.
             "fsv3capgnofrcc25d50s38":
                 "FluidServe v0.4\n(per-token\n25/50/38 ms)",
             "polyservepc25d50s38":
                 "PolyServe\n(paper mech.,\nper-token\n25/50/38 ms)",
             # EXP-123. Both labels carry both budget sets, because this figure
             # puts them beside columns scored at first-token 5/10/7 s and
             # per-token 50/100/75 ms and the three sets may not be read as one.
             "fsv3capgnofrcc25d50s38ftc2500d5000s3500":
                 "FluidServe v0.4\n(first-token\n2.5/5/3.5 s,\nper-token\n25/50/38 ms)",
             "polyservepc25d50s38ftc2500d5000s3500":
                 "PolyServe\n(paper mech.,\nfirst-token\n2.5/5/3.5 s,\nper-token\n25/50/38 ms)",
             "polyservept75": "PolyServe (paper mech.)",
             "slot75": "Llumnix SLO", "vllmcachet75": "vLLM router",
             "polyserve": "PolyServe", "slo": "Llumnix SLO",
             "loadbalance": "Llumnix", "llmdslo": "llm-d",
             "fspnopend": "FluidServe\n(holding off)",
             "fspnoaff": "FluidServe\n(class pref. off)",
             "fspslos": "FluidServe\n(both off)",
             "vllmcache": "vLLM router\n(cache-aware)",
             "fsnoshed": "FluidServe\n(no rejection)",
             "fsroute": "FluidServe\n(routing only)",
             "fsindep": "FluidServe\n(independent shed)",
             "fscount": "FluidServe\n(pref. by count)",
             "fsnoaff": "FluidServe\n(class pref. off)",
             "fsboth": "FluidServe\n(per-inst. corr.\n+ pace cap)",
             "fsinterleave": "FluidServe\n(+ interleave-aware\nfirst-token estimate)",
             "fsv3": "FluidServe v0.3",
             "fsv3noaff": "FluidServe v0.3\n(class preference off)",
             "fsv3b40": "FluidServe v0.3\n(swe budget 40 s)",
             "fsv3noaffb40": "FluidServe v0.3\n(swe 40 s, pref. off)",
             "fsv3cap": "FluidServe v0.3\n(+ class-instance cap)",
             "fsv3nofrc": "FluidServe v0.3\n(force off)",
             "fsv3capnofrc": "FluidServe v0.3\n(cap, force off)",
             "fsv3capg": "FluidServe v0.3\n(+ cap, guardrail form)",
             "fsv3capgnofrc": "FluidServe v0.3\n(guardrail cap, force off)",
             "fsdelay": "FluidServe\n(+ per-inst. delay\nin deadline test)",
             "fsdeadfix": "FluidServe\n(+ delay, deadline\nin feasibility)",
             "fsnaboth": "FluidServe\n(class pref. off\n+ corr. + pace cap)",
             "fsdead": "FluidServe\n(+ first-token\ndeadline)",
             "fscorr": "FluidServe\n(per-inst. corr.)",
             "fspacecap": "FluidServe\n(pace cap)",

             "fsw00": "FluidServe\n(pref. w=0)",
             "fsw25": "FluidServe\n(pref. w=0.25)",
             "fsw50": "FluidServe\n(pref. w=0.5)",
             "fsw75": "FluidServe\n(pref. w=0.75)",
             "fspinc1": "FluidServe\n(chat on 1 engine)",
             "fspinc2": "FluidServe\n(chat on 2 engines)",
             "fspinc3": "FluidServe\n(chat on 3 engines)",
             # From v0.2 onward `fspfx` is the deployed default and is the arm the
             # paper calls FluidServe, while `fluidserve` is the ablation with
             # prefix accounting switched off -- the four drivers pin FS_PREFIX=false
             # on it so that older result directories keep one meaning. Labelling
             # `fluidserve` as plain "FluidServe" put the ablation and the system
             # under names a reader would read as the same thing.
             "fluidserve": "FluidServe\n(prefix acct. off)",
             "fspfx": "FluidServe v0.2",
             "fspfxb": "FluidServe v0.2\n(calibration fixed)"}
ENGINE_COLORS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728"]

E_RUN = "vllm:num_requests_running"
E_WAIT = "vllm:num_requests_waiting"
E_KV = "vllm:kv_cache_usage_perc"
E_GEN = "vllm:generation_tokens_total"


def series(run):
    """Per-engine (t, running, waiting, kv%) plus the fleet decode rate.

    The generation counter is cumulative, so the rate is its difference over the
    sampling interval rather than the value itself. Ticks where the collector
    failed carry ok=false and are skipped; a skipped tick widens the interval it
    sits in rather than producing a spike, because the difference is divided by
    the actual elapsed time.
    """
    eng, gen_t, gen_v = {}, [], []
    for path in sorted(glob.glob(os.path.join(run, "server_metrics", "engine_*.jsonl"))):
        t, running, wait, kv, gen = [], [], [], [], []
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
                return np.nan

            t.append(rec["t"])
            running.append(pick(E_RUN))
            wait.append(pick(E_WAIT))
            kv.append(pick(E_KV) * 100)
            gen.append(pick(E_GEN))
        if not t:
            continue
        name = os.path.basename(path).replace("engine_", "").replace(".jsonl", "")
        t = np.array(t, dtype=float)
        eng[name] = (t - t[0], np.array(running, dtype=float),
                     np.array(wait, dtype=float), np.array(kv, dtype=float))
        gen_t.append(t - t[0])
        gen_v.append(np.array(gen, dtype=float))
    if not eng:
        return None
    # Fleet decode rate: interpolate each engine's cumulative counter onto a
    # common grid before differencing, because the four collectors do not tick
    # together and differencing per engine then summing would alias.
    grid = np.arange(0, min(x.max() for x in gen_t), 2.0)
    tot = np.zeros_like(grid)
    for tt, vv in zip(gen_t, gen_v):
        ok = np.isfinite(vv)
        if ok.sum() < 2:
            continue
        tot += np.interp(grid, tt[ok], vv[ok])
    rate = np.diff(tot) / np.diff(grid)
    return eng, grid[1:], rate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", default=["results/*exp38r1*"])
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--title", default="EXP-38")
    ap.add_argument("--tag", default=None,
                    help="Force every run into one condition cell under this "
                         "name, for arms whose directories differ in something "
                         "other than the rate.")
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    cells = {}
    for pat in a.runs:
        for d in sorted(glob.glob(pat)):
            # A rate sweep names the rate in the directory; a dynamic trace has
            # no single rate, so all its arms go into one cell keyed by the
            # variant. Without this branch every hour-long run was skipped and
            # the side-by-side figure existed only for sweeps.
            m = re.search(r"_rpm_(\d+)", os.path.basename(d))
            # The fallback keys a dynamic-trace run by the last token of its
            # directory name, which is the variant. That is right when the arms
            # share a driver and therefore a variant, and wrong when they do
            # not: EXP-71 compares `_fspfx_fullb` against `_llmdslo_full` and
            # got two one-column figures instead of one two-column figure,
            # which is the whole point of this script. --tag forces the runs
            # into one cell and names the file.
            if a.tag:
                key = a.tag
            else:
                key = int(m.group(1)) if m else os.path.basename(d).rsplit("_", 1)[-1]
            cells.setdefault(key, {})[arm_of(d)] = d

    for rpm in sorted(cells, key=str):
        arms = [x for x in ARM_ORDER if x in cells[rpm]]
        unknown = sorted(set(cells[rpm]) - set(ARM_ORDER))
        if unknown:
            sys.exit(f"{rpm}: these arms have runs but are not in ARM_ORDER, so "
                     f"they would be dropped from the figure without a message: "
                     f"{unknown}. Add them to ARM_ORDER and ARM_LABEL.")
        if not arms:
            continue
        loaded = {x: series(cells[rpm][x]) for x in arms}
        arms = [x for x in arms if loaded[x] is not None]
        if not arms:
            print(f"{rpm} rpm: no engine metrics")
            continue
        with plt.rc_context(PAPER_STYLE):
            fig, ax = plt.subplots(4, len(arms), figsize=(3.6 * len(arms), 8.4),
                                   sharex=True)
            ax = np.atleast_2d(ax)
            if ax.shape[0] != 4:
                ax = ax.T
            # Shared limits per row, set from the widest arm, so a column that
            # looks calm is calm and not merely rescaled.
            lim = [0.0, 0.0, 105.0, 0.0]
            for x in arms:
                eng, gt, gr = loaded[x]
                for _, (_, rn, wt, _) in eng.items():
                    lim[0] = max(lim[0], np.nanmax(rn))
                    lim[1] = max(lim[1], np.nanmax(wt))
                lim[3] = max(lim[3], np.nanmax(gr) if len(gr) else 0.0)
            for j, x in enumerate(arms):
                eng, gt, gr = loaded[x]
                for i, (name, (t, rn, wt, kv)) in enumerate(sorted(eng.items())):
                    c = ENGINE_COLORS[i % len(ENGINE_COLORS)]
                    ax[0][j].plot(t, rn, color=c, lw=0.8, label=f"engine {name}")
                    ax[1][j].plot(t, wt, color=c, lw=0.8)
                    ax[2][j].plot(t, kv, color=c, lw=0.8)
                ax[3][j].plot(gt, gr, color="#2ca02c", lw=0.8)
                ax[0][j].set_title(ARM_LABEL[x])
                for i, hi in enumerate(lim):
                    ax[i][j].set_ylim(0, hi * 1.05 if hi else 1)
                    ax[i][j].grid(axis="y", ls=":", lw=0.7, alpha=0.6)
                ax[3][j].set_xlabel("time (s)")
            for i, lab in enumerate(["running batch\n(requests)",
                                     "engine queue\n(requests)",
                                     "KV occupancy (%)",
                                     "fleet decode\n(tokens/s)"]):
                ax[i][0].set_ylabel(lab)
            ax[0][0].legend(fontsize=6, loc="upper left")
            where = (f"at {rpm/60:.0f} req/s" if isinstance(rpm, int)
                     else f"on the hour-long dynamic trace ({rpm})")
            # The fleet size is counted from the series actually drawn. It was
            # a literal "four" until 2026-09-11, by which time this script was
            # being run on eight-instance fleets and the subtitle contradicted
            # its own legend: a caption number has to come from the aggregate
            # the panels are drawn from.
            # series() returns (eng, grid, rate); the engine count is the
            # size of the per-engine dict, not of that 3-tuple.
            nengine = sorted({len(v[0]) for v in loaded.values() if v})
            fleet = (f"the same {nengine[0]} engines" if len(nengine) == 1
                     else f"engine counts {nengine} — NOT the same fleet")
            fig.suptitle(f"{a.title} engine layer {where} — "
                         f"{fleet}, {len(arms)} policies\n"
                         f"rows share a y axis across columns", fontsize=9)
            fig.tight_layout(rect=(0, 0, 1, 0.96))
            p = os.path.join(a.out_dir, f"compare_rpm_{rpm}.png")
            fig.savefig(p, dpi=300)
            plt.close(fig)
            print(f"wrote {p}  arms={arms}")


if __name__ == "__main__":
    main()
