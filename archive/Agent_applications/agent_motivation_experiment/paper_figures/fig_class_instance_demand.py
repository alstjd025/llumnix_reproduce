#!/usr/bin/env python3
"""Per-class offered demand over the one-hour trace, in instances.

  class_instance_demand.pdf        3.335 x 1.45 in, stacked per class
  class_instance_demand_lines.pdf  3.335 x 1.45 in, one line per class
  class_instance_demand.csv        the values drawn, per minute
  class_instance_capacity.csv      the per-class capacity of one instance

Definition (2026-09-15, at the author's request):

    demand_c(t) = lambda_c(t) / mu_c          [instances]

lambda_c(t) is the arrival rate of class c in the trace
(`traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv`, the trace EXP-109
replayed), counted per minute. mu_c is the number of class-c requests per
second that ONE instance can complete within that class's per-token budget when
it serves class c alone. mu_c is computed from the profiled cost model, not
measured by running each class alone:

  * N concurrent requests hold N x (prompt + output/2) KV tokens on average;
    this must fit 95% of the instance's KV pool, 492,944 tokens (vLLM's
    "GPU KV cache size" at engine start-up, Llama-3.1-70B, TP=2, B200).
  * One decode iteration costs c0 + c_kv x KV + c_n x N (decode_step_law of
    deploy/profiling/llama31-70b-b200-tp2/fluidserve.json).
  * Prefill takes time away from decoding. A request's uncached prompt costs
    (prefill ms at 1,024 tokens - c0) / 1,024 ms per token; the uncached
    fraction is 1 - the fleet prefix-cache hit rate measured by the engines in
    the EXP-109 FluidServe repeat-1 run (46.2%). Spread over the request's
    output tokens, this adds N x uncached prompt x cost / output ms per token.
  * The per-token time must stay within the class's TBT budget (chat 50 ms,
    deep research 100 ms, agent 75 ms). The largest N that meets both limits
    gives mu = N / (output x per-token time).

Prompt and output lengths are the means of the requests that finished in the
EXP-109 FluidServe repeat-1 run (the handover trace table).

    python3 paper_figures/fig_class_instance_demand.py
"""
import json
import os
import sys

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
REPO = os.path.dirname(os.path.dirname(ROOT))
sys.path.insert(0, HERE)
import paper_style as ps  # noqa: E402
from fig_class_mix_hour import CLASS_COLOR_PUBUGN, CLASS_LABEL  # noqa: E402

TRACE = os.path.join(ROOT, "traces", "dynamic", "canonical",
                     "dyn60_shift_m2Am1B_b1045.csv")
PROFILE = os.path.join(REPO, "deploy", "profiling", "llama31-70b-b200-tp2",
                       "fluidserve.json")
REQ_TABLE = ("/home/nxclab/handover/two_models_hour_reqgoodput_a/trace/"
             "260831_2015_exp109r1_fsv3capgnofrct75_shift_trace.csv")
KV_POOL = 492944          # tokens per instance, vLLM start-up log
KV_SAFETY = 0.95
PREFIX_HIT = 0.462        # fleet prefix-cache hit rate, EXP-109 FluidServe r1
TBT_MS = {"chat": 50.0, "deepresearch": 100.0, "swe": 75.0}
FLEET = 4
CLASSES = ["chat", "swe", "deepresearch"]
BIN_S = 60.0
# The stacked areas use the class colours of the other hour figures. The agent
# colour there is too pale to read as a thin line, so the line version darkens
# the two light colours and separates agent from deep research by line style
# as well (their lines coincide between minutes 16 and 30).
LINE_COLOR = {"chat": "#1c9099", "swe": "#8c6bb1", "deepresearch": "#3690c0"}
LINE_STYLE = {"chat": "-", "swe": "--", "deepresearch": "-"}


def capacity(prompt, output, budget_ms, law, pre_ms_per_tok):
    """(N, mu req/s, per-token ms, KV fraction) at the largest feasible N."""
    best = None
    for n in range(1, 5000):
        kv = n * (prompt + output / 2.0)
        if kv > KV_SAFETY * KV_POOL:
            break
        step = (law["c0_ms"] + law["c_kv_ms_per_token"] * kv
                + law["c_n_ms_per_request"] * n)
        tok = step + n * prompt * (1.0 - PREFIX_HIT) * pre_ms_per_tok / output
        if tok > budget_ms:
            break
        best = (n, n / (output * tok / 1000.0), tok, kv / KV_POOL)
    return best


def main():
    prof = json.load(open(PROFILE))
    law = prof["decode_step_law"]
    pre = (prof["prefill_step_law"]["ms_at_1024"] - law["c0_ms"]) / 1024.0

    req = pd.read_csv(REQ_TABLE)
    done = req[req["outcome"].isin(["met", "missed"])]
    rows = []
    for c in CLASSES:
        d = done[done["class"] == c]
        p, o = d["input_tokens"].mean(), d["output_tokens"].mean()
        n, mu, tok, kvf = capacity(p, o, TBT_MS[c], law, pre)
        bound = "KV" if kvf > 0.94 * KV_SAFETY else "TBT"
        rows.append(dict(cls=c, prompt=p, output=o, tbt_budget_ms=TBT_MS[c],
                         concurrency=n, per_token_ms=tok, kv_fraction=kvf,
                         bound=bound, mu_req_s=mu))
        print(f"{c:13s} prompt {p:6.0f} output {o:5.0f}: N={n:4d} "
              f"per-token {tok:5.1f} ms, KV {100 * kvf:4.1f}% ({bound}-bound), "
              f"mu = {mu:5.2f} req/s per instance")
    cap = pd.DataFrame(rows).set_index("cls")
    cap.to_csv(os.path.join(HERE, "class_instance_capacity.csv"),
               float_format="%.4f")

    tr = pd.read_csv(TRACE)
    nb = int(np.ceil(tr["arrival_s"].max() / BIN_S))
    b = (tr["arrival_s"] // BIN_S).astype(int)
    lam = {c: np.bincount(b[tr["class"] == c], minlength=nb)[:nb] / BIN_S
           for c in CLASSES}
    dem = {c: lam[c] / cap.loc[c, "mu_req_s"] for c in CLASSES}
    total = sum(dem.values())
    t = np.arange(nb) + 0.5

    # check against what the fleet did: FluidServe repeat-1 rejection per minute
    rel = req["rel_s"].to_numpy()
    rb = (rel // BIN_S).astype(int)
    rb_ok = rb < nb
    n_all = np.bincount(rb[rb_ok], minlength=nb)
    n_rej = np.bincount(rb[rb_ok & (req["outcome"] == "rejected").to_numpy()],
                        minlength=nb)
    rej = np.where(n_all > 0, 100.0 * n_rej / np.maximum(n_all, 1), np.nan)
    over = total > FLEET
    ok = n_all > 100
    print(f"minutes with demand > {FLEET} instances: {int(over.sum())} of {nb}")
    print(f"FluidServe r1 rejection, minutes over {FLEET}: "
          f"{np.nanmean(rej[over & ok]):.1f}%; minutes under: "
          f"{np.nanmean(rej[~over & ok]):.1f}%; per-minute correlation "
          f"{np.corrcoef(total[ok], rej[ok])[0, 1]:.2f}")
    for lo, hi in ((0, 15), (16, 30), (31, 45), (46, 60)):
        s = slice(lo, min(hi, nb))
        print(f"  min {lo:2d}-{hi:2d}: " + ", ".join(
            f"{CLASS_LABEL[c]} {dem[c][s].mean():.2f}" for c in CLASSES)
            + f"; total {total[s].mean():.2f} inst; rejected {np.nanmean(rej[s]):.1f}%")

    out = pd.DataFrame({"minute": np.arange(nb),
                        **{f"lambda_{c}": lam[c] for c in CLASSES},
                        **{f"instances_{c}": dem[c] for c in CLASSES},
                        "instances_total": total,
                        "fluidserve_r1_rejected_pct": rej})
    out.to_csv(os.path.join(HERE, "class_instance_demand.csv"), index=False,
               float_format="%.4f")

    style = {**ps.STYLE, "xtick.labelsize": 7, "ytick.labelsize": 7,
             "axes.labelsize": 7.5, "legend.fontsize": 7}
    ymax = np.ceil(total.max() + 0.5)
    for kind in ("stack", "lines"):
        with plt.rc_context(style):
            fig, ax = plt.subplots(figsize=(ps.COL_W, 1.45))
            if kind == "stack":
                ax.stackplot(t, *[dem[c] for c in CLASSES],
                             colors=[CLASS_COLOR_PUBUGN[c] for c in CLASSES],
                             labels=[CLASS_LABEL[c] for c in CLASSES],
                             lw=0.3, edgecolor="white")
                ax.set_ylim(0, ymax)
            else:
                for c in CLASSES:
                    ax.plot(t, dem[c], color=LINE_COLOR[c], lw=1.1,
                            ls=LINE_STYLE[c], label=CLASS_LABEL[c])
                ax.set_ylim(0, np.ceil(max(d.max() for d in dem.values()) + 0.5))
            # named in the legend: no spot along the line is free of data in
            # both versions
            ax.axhline(FLEET, color="#333333", lw=0.7, ls="--",
                       label=f"Fleet ({FLEET})")
            ax.set_xlim(0, nb)
            ax.set_xticks(np.arange(0, nb + 1, 10))
            ax.set_xlabel("Time (minutes)", labelpad=1.5)
            ax.set_ylabel("Demand\n(instances)")
            ax.grid(axis="y", **ps.GRID)
            ax.set_axisbelow(True)
            ax.legend(loc="lower center", bbox_to_anchor=(0.5, 1.0), ncol=4,
                      handlelength=1.4, columnspacing=0.7, handletextpad=0.4, borderaxespad=0.2)
            fig.tight_layout(pad=0.35)
            name = ("class_instance_demand.pdf" if kind == "stack"
                    else "class_instance_demand_lines.pdf")
            ps.save(fig, os.path.join(HERE, name))
            print("wrote", name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
