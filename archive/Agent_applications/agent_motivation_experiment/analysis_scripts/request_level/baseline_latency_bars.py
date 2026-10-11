#!/usr/bin/env python3
"""Mean first-token and mean per-token time, per class, for the baselines.

FluidServe is deliberately absent: this is a picture of what the deployed and
published systems deliver, and a motivation figure that needs the paper's own
system to make its point is not a motivation figure.

⚠ THE DENOMINATORS ARE NOT THE SAME ACROSS BARS. Each bar is the mean over the
requests that arm ADMITTED and that ran to completion, and the arms admit very
different shares -- llm-d takes 38% of chat where the vLLM router takes all of
it. Rejection is not random, so a low bar may be a low bar over an easier
population. The admitted share is printed on every bar for that reason.

⚠ The per-token time is `(e2e - ttft) / (output tokens - 1)`, the corrected
definition, taken from exp22_fluidserve.load_run's `itl_ms`. The raw
`tbt_mean_ms` column in metrics.csv is a different quantity.

⚠ swe carries the per-token promise (7 s, 75 ms). These bars cannot sit beside
anything scored against the 30 s end-to-end rule.
"""
import argparse, importlib.util, os, sys
import numpy as np, pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.family"] = "NanumBarunGothic"
plt.rcParams["axes.unicode_minus"] = False

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "paper_figures"))
import paper_style as ps  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "e22", os.path.join(HERE, "exp22_fluidserve.py"))
E = importlib.util.module_from_spec(spec); spec.loader.exec_module(E)

CLASSES = ["chat", "deepresearch", "swe"]
BUDGET = {"chat": (5000, 50), "deepresearch": (10000, 100), "swe": (7000, 75)}
COLOR = {"vLLM router": ps.ARM_COLOR["vllmrouter"], "PolyServe": ps.ARM_COLOR["polyserve"],
         "Llumnix SLO": ps.ARM_COLOR["slo"], "llm-d": ps.ARM_COLOR["llmd"]}


def stats(dirs):
    parts = []
    for d in dirs:
        r = E.load_run(d)
        if r is not None and not r.empty:
            parts.append(r)
    r = pd.concat(parts)
    out = {}
    for c in CLASSES:
        s = r[r["class"] == c]
        ok = s[(~s["rejected"]) & (~s["cutoff"]) & (s["output_tokens"] > 1)]
        if ok.empty:
            continue
        out[c] = dict(ttft=(ok["first_token_latency"] * 1000).mean(),
                      itl=ok["itl_ms"].mean(),
                      adm=100 * (1 - s["rejected"].mean()))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True, help="label|dir[,dir]")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    arms = [x.split("|") for x in a.arm]
    D = {lab: stats(dirs.split(",")) for lab, dirs in arms}
    names = [lab for lab, _ in arms]

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 3.8))
    w = 0.8 / len(names)
    for ax, key, title, unit in (
            (axes[0], "ttft", "첫 토큰 시간 (평균)", "ms"),
            (axes[1], "itl", "토큰간 시간 (평균)", "ms")):
        for j, lab in enumerate(names):
            xs, ys = [], []
            for i, c in enumerate(CLASSES):
                if c not in D[lab]:
                    continue
                xs.append(i - 0.4 + w * (j + 0.5)); ys.append(D[lab][c][key])
            b = ax.bar(xs, ys, width=w * 0.92, color=COLOR[lab], label=lab,
                       edgecolor="white", linewidth=0.5)
            for rect, c in zip(b, [c for c in CLASSES if c in D[lab]]):
                ax.annotate(f"{D[lab][c]['adm']:.0f}%",
                            (rect.get_x() + rect.get_width() / 2, rect.get_height()),
                            ha="center", va="bottom", fontsize=6.5, color="#444444",
                            xytext=(0, 1), textcoords="offset points")
        for i, c in enumerate(CLASSES):
            v = BUDGET[c][0 if key == "ttft" else 1]
            ax.hlines(v, i - 0.44, i + 0.44, color="#333333", lw=1.0, ls="--")
            ax.annotate(f"예산 {v:,}", (i + 0.44, v), fontsize=6.5, color="#333333",
                        ha="right", va="bottom")
        ax.set_xticks(range(len(CLASSES)))
        ax.set_xticklabels(["chat", "deepresearch", "swe"])
        ax.set_title(title, fontsize=10)
        ax.set_ylabel(unit)
        ax.grid(axis="y", ls=":", lw=0.5, color="#909090")
        ax.set_axisbelow(True)
    axes[0].set_yscale("log")
    axes[0].set_ylabel("ms (로그 축)")
    axes[0].legend(fontsize=8, frameon=False, ncol=4, loc="upper center",
                   bbox_to_anchor=(1.05, 1.22))
    fig.text(0.5, 0.005, "막대 위 숫자 = 그 arm이 그 클래스를 받아들인 비율. "
             "분모가 arm마다 다르므로 낮은 막대가 곧 좋은 것은 아니다.",
             ha="center", fontsize=7.5, color="#444444")
    fig.tight_layout(rect=(0, 0.035, 1, 0.90))
    fig.savefig(a.out, dpi=200)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
