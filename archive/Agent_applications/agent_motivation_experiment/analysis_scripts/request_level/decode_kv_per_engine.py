#!/usr/bin/env python3
"""One panel per engine: the KV that engine's decode phase creates, and the
inter-token latency it delivers, over the same hour.

Small multiples rather than one pooled panel, because this policy makes the
engines asymmetric on purpose and a fleet aggregate then describes an engine
that did not run. Measured on EXP-109 repeat 1, one engine produced 40% of the
fleet's generated tokens while holding the flattest latency of the four.

Both series come from the engine's own counters:
  vllm:generation_tokens_total          -> KV created, at bytes-per-token
  vllm:inter_token_latency_seconds_{sum,count} -> mean ITL per interval

⚠ The KV figure is a volume created and destroyed over the hour, not a level:
a request releases its whole footprint when it finishes. Write "creates KV at
X GB/s", never "X GB accumulates".
"""
import argparse, csv, glob, json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "NanumBarunGothic"
plt.rcParams["axes.unicode_minus"] = False

# 2 (K and V) x 80 layers x 8 KV heads x 128 head_dim x 2 bytes (bf16).
# ⚠ Qwen2.5-72B is entered as the same shape and is NOT verified against the
# engine's config; verify before this figure goes in the paper.
BYTES_PER_TOKEN = {"llama": 2 * 80 * 8 * 128 * 2, "qwen": 2 * 80 * 8 * 128 * 2}


def first_arrival(run):
    """Unix second of the first request the client sent, so 0 on the x axis is
    the moment load starts rather than the moment the collector started."""
    best = None
    with open(os.path.join(run, "metrics.csv")) as fh:
        for row in csv.DictReader(fh):
            if row.get("agent") == "job_summary":
                continue
            try:
                t = float(row["start_time"])
            except (TypeError, ValueError):
                continue
            best = t if best is None else min(best, t)
    return best


def engine_series(path):
    ts, gen, sm, ct = [], [], [], []
    for line in open(path):
        try:
            r = json.loads(line)
        except Exception:
            continue
        g = next((x for k, x in r.items()
                  if k.startswith("vllm:generation_tokens_total")), None)
        a = next((x for k, x in r.items()
                  if k.startswith("vllm:inter_token_latency_seconds_sum")), None)
        b = next((x for k, x in r.items()
                  if k.startswith("vllm:inter_token_latency_seconds_count")), None)
        if g is None or a is None or b is None:
            continue
        ts.append(r["t"]); gen.append(g); sm.append(a); ct.append(b)
    if len(ts) < 2:
        return None
    t = np.array(ts)
    grid = np.arange(t[0], t[-1], 1.0)
    out = {}
    for key, raw in (("gen", gen), ("sum", sm), ("cnt", ct)):
        d = np.diff(np.interp(grid, t, np.array(raw, float)), prepend=np.nan)
        d = np.nan_to_num(d); d[d < 0] = 0
        out[key] = d
    out["t"] = grid
    out["gen_total"] = gen[-1] - gen[0]
    return out


def smooth(x, k):
    return np.convolve(x, np.ones(k) / k, mode="same") if k > 1 else x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--col", action="append", required=True,
                    help="label|model|run_dir")
    ap.add_argument("--out", required=True)
    ap.add_argument("--smooth-s", type=int, default=20)
    ap.add_argument("--budget-ms", type=float, default=50.0)
    ap.add_argument("--itl-max", type=float, default=110.0,
                    help="clip the latency axis; a single sub-second spike "
                         "otherwise flattens the whole hour")
    a = ap.parse_args()

    cols = [c.split("|") for c in a.col]
    names = sorted({os.path.basename(f).replace("engine_", "").replace(".jsonl", "")
                    for _, _, run in cols
                    for f in glob.glob(os.path.join(run, "server_metrics",
                                                    "engine_*.jsonl"))})
    fig, axes = plt.subplots(len(names), len(cols),
                             figsize=(5.6 * len(cols), 1.75 * len(names)),
                             sharex=True, sharey="col", squeeze=False)

    for ci, (label, model, run) in enumerate(cols):
        bpt = BYTES_PER_TOKEN[model]
        t0 = first_arrival(run)
        for ri, name in enumerate(names):
            ax = axes[ri][ci]
            f = os.path.join(run, "server_metrics", f"engine_{name}.jsonl")
            if not os.path.exists(f):
                ax.set_axis_off(); continue
            s = engine_series(f)
            if s is None:
                ax.set_axis_off(); continue
            m = (s["t"] - t0) / 60.0
            keep = m >= 0
            gbps = smooth(s["gen"] * bpt / 1e9, a.smooth_s)
            ax.fill_between(m[keep], 0, gbps[keep], color="#1f77b4", alpha=0.35, lw=0)
            ax.plot(m[keep], gbps[keep], color="#1f77b4", lw=0.9, zorder=3)

            ax2 = ax.twinx()
            sd, cd = smooth(s["sum"], a.smooth_s), smooth(s["cnt"], a.smooth_s)
            itl = np.divide(sd, cd, out=np.full_like(sd, np.nan), where=cd > 0) * 1000
            ok = keep & ~np.isnan(itl)
            ax2.plot(m[ok], itl[ok], color="#d62728", lw=0.8, alpha=0.9, zorder=2)
            ax2.axhline(a.budget_ms, color="#555555", lw=0.7, ls="--", zorder=1)
            ax2.set_ylim(0, a.itl_max)
            ax2.tick_params(axis="y", labelsize=7, colors="#d62728")
            if ci == len(cols) - 1:
                ax2.set_ylabel("ITL (ms)", fontsize=8, color="#d62728")

            v = itl[ok]; v = v[v > 0]
            over = 100.0 * np.mean(v > a.budget_ms) if len(v) else 0.0
            ax.text(0.015, 0.90,
                    f"엔진 {name}   생성 {s['gen_total']*bpt/1e9:,.0f} GB   "
                    f"ITL 중앙 {np.median(v):.0f} ms   예산 초과 {over:.0f}% 시간",
                    transform=ax.transAxes, fontsize=7.5, va="top", color="#222222")
            ax.set_xlim(0, 62)
            ax.set_ylim(0, None)
            ax.grid(axis="both", ls=":", lw=0.5, color="#909090")
            ax.set_axisbelow(True)
            ax.tick_params(labelsize=7)
            if ri == 0:
                ax.set_title(label, fontsize=10)
            if ri == len(names) - 1:
                ax.set_xlabel("부하 시작 후 경과 시간 (분)", fontsize=8)
            if ci == 0:
                ax.set_ylabel("KV 생성\n(GB/s)", fontsize=8)
    fig.tight_layout(h_pad=0.5)
    fig.savefig(a.out, dpi=200)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
