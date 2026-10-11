#!/usr/bin/env python3
"""Is the worst per-token tail on the quiet engines because they are quiet, or
because they are the engines holding chat?

WHY THIS QUESTION HAS TO BE ANSWERED BEFORE THE PREFILL DISTURBANCE BUDGET IS
WRITTEN.  `12_batch_step_chain.md` measured that at 45 req/s the pooled
token-gap 90th percentile is worst in windows whose decode batch is 120-140
(125.5 ms) and best at batch 240-250 (72.7 ms) -- the tail is on the LESS loaded
windows.  `19_hold_window_mechanism.md` then measured that under the deployed
gateway setting one instance of four is given over to deepresearch and runs at a
batch and KV occupancy roughly twice the others.  Those two facts make batch and
class composition collinear: the high-batch windows ARE the deepresearch-only
instance, and the low-batch windows ARE the chat-carrying ones.  A budget
derived from the wrong one of the two would be derived from a confound.

  - if it is quietness: the same prefill chunk is a larger share of a shorter
    step, so the budget has to be expressed relative to the decode-only floor of
    that instance, and the re-allocation direction is counter-intuitive (send
    prefill towards the busier instance)
  - if it is the class held: the budget belongs against the tightest per-token
    allowance among the requests resident on the instance, which the policy
    already computes as `tightestAllowance` (`fluidserve.go:1018-1033`)

Inputs, all already on disk, all from the pinned EXP-82 runs:
  14_window_gaps.csv      per engine per one-second window: the reconstructed
                          step-time mean/p50/p90, with batch, KV and prefill
  15_window_features.csv  the same windows with the resident request counts per
                          class (n_chat, n_dr, n_swe)

mu_d(batch, kv) -- the decode-only floor -- is taken exactly as
`tail2026_quantile_model.py` takes it, from windows that processed almost no
prefill, so the two documents cannot disagree about what phi means.

Writes `20_quiet_or_chat.md` and its tables.  Reads only; writes only new files.
"""
import os
import sys
import numpy as np
import pandas as pd

D = ("/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
     "/results/aggregate_analysis/tail_2026-08-16")
NEAR_ZERO_PREFILL = 200.0   # tok/s below which a window is treated as decode-only
BUDGET_MS = 50.0            # chat per-token budget, workload_configs/mix_short_m1_*.json


def load():
    g = pd.read_csv(os.path.join(D, "14_window_gaps.csv"))
    f = pd.read_csv(os.path.join(D, "15_window_features.csv"))
    g["tk"] = g["t"].round(3)
    f["tk"] = f["t"].round(3)
    m = g.merge(f.drop(columns=["arm", "rep", "rate"]), on=["run", "engine", "tk"],
                suffixes=("", "_f"))
    # FluidServe only: the question is about the arm the budget would go into,
    # and llm-d reaches the engines through a different control plane.
    m = m[m["arm"] == "fspfx"].copy()
    m["n_res"] = m[["n_chat", "n_dr", "n_swe"]].sum(axis=1)
    m = m[(m["n_res"] > 0) & m["p90"].notna() & m["mean"].notna()].copy()
    m["chat_share"] = m["n_chat"] / m["n_res"]
    m["dr_share"] = m["n_dr"] / m["n_res"]
    return m.reset_index(drop=True)


def decode_reference(m):
    """mu_d(batch, kv), the window mean where almost no prefill was processed."""
    q = m[m["prompt_tok_s"] < NEAR_ZERO_PREFILL].copy()
    q["bb"] = (q["batch"] // 20).astype(int)
    q["kb"] = (q["kv"] * 20).round().astype(int)
    tab = q.groupby(["bb", "kb"])["mean"].agg(["median", "size"])
    tab = tab[tab["size"] >= 20]["median"]
    tabb = q.groupby("bb")["mean"].agg(["median", "size"])
    tabb = tabb[tabb["size"] >= 20]["median"]
    gmed = float(q["mean"].median())
    bb = (m["batch"] // 20).astype(int)
    kb = (m["kv"] * 20).round().astype(int)
    mu = pd.Series(pd.MultiIndex.from_arrays([bb, kb]).map(tab), index=m.index)
    mu = mu.fillna(pd.Series(bb.map(tabb), index=m.index)).fillna(gmed)
    return mu.astype(float), len(q)


def med(s):
    return float(np.median(s)) if len(s) else float("nan")


def fmt(x, n=1):
    return "n/a" if x != x else f"{x:,.{n}f}"


def table(df, rows, cols, val, agg=med, minn=30):
    """A rows x cols table of `agg(val)` with the cell count beside it."""
    out = []
    for r, gr in df.groupby(rows, observed=True):
        line = {rows: r}
        for c, gc in gr.groupby(cols, observed=True):
            line[c] = (agg(gc[val]), len(gc)) if len(gc) >= minn else (float("nan"), len(gc))
        out.append(line)
    return out


def main():
    m = load()
    mu, n_ref = decode_reference(m)
    m["mu_d"] = mu
    # phi: the share of window step time that is not the decode-only floor.
    m["phi"] = (1.0 - m["mu_d"] / m["mean"]).clip(lower=0.0, upper=1.0)
    m["excess_ms"] = (m["mean"] - m["mu_d"]).clip(lower=0.0)

    rates = [35.0, 45.0]
    m = m[m["rate"].isin(rates)].copy()

    L = []
    P = L.append
    P("# 20 — Is the worst tail on the quiet engines because they are quiet, or "
      "because they hold chat?")
    P("")
    P("**2026-08-17. Written from data already on disk; no cluster command was run and no "
      "existing file was changed or deleted.** Script: "
      "`analysis_scripts/request_level/tail2026_quiet_or_chat.py`. Tables: `20_*.csv` in this "
      "directory.")
    P("")
    P("This closes the question `ms_dev/notes/four-factor-plan.md` §5 marks as blocking factor C "
      "(the prefill disturbance budget): the budget has to be derived from something, and the two "
      "candidates — the instance's decode-only floor, or the tightest per-token allowance among "
      "the requests living on it — are told apart only by this measurement.")
    P("")
    P(f"**Population.** FluidServe (`fspfx`) windows from the pinned EXP-82 runs at 35 and "
      f"45 req/s, two repeats each: **{len(m):,} engine-seconds** over "
      f"{m['run'].nunique()} runs. A window is one Prometheus scrape interval on one engine; "
      f"`p90` is the 90th percentile of the step times reconstructed within it. "
      f"`mu_d(batch, KV)` is the decode-only floor, taken from the "
      f"{n_ref:,} windows across all rates that processed under "
      f"{NEAR_ZERO_PREFILL:.0f} prefill tokens per second, exactly as "
      f"`tail2026_quantile_model.py` takes it. `phi = 1 - mu_d/mean` is the share of window step "
      f"time that is not that floor.")
    P("")

    # ---------------------------------------------------------------- 1
    P("## 1. The observation reproduces, and batch and class composition are collinear")
    P("")
    P("Windows binned by decode batch. `chat share` is the share of the requests resident on that "
      "engine in that window that are chat; `prefill` is the prefill tokens the engine processed "
      "per second of that window.")
    P("")
    edges = [0, 60, 100, 140, 180, 220, 260, 10 ** 9]
    m["bbin"] = pd.cut(m["batch"], edges, right=False)
    P("| batch | windows | step mean | **step p90** | mu_d | **phi** | chat share | dr share | prefill tok/s |")
    P("|---|---|---|---|---|---|---|---|---|")
    rows = []
    for b, g in m.groupby("bbin", observed=True):
        if len(g) < 30:
            continue
        rows.append(dict(batch=str(b), n=len(g), mean=med(g["mean"]), p90=med(g["p90"]),
                         mu_d=med(g["mu_d"]), phi=med(g["phi"]),
                         chat=med(g["chat_share"]), dr=med(g["dr_share"]),
                         prefill=med(g["prompt_tok_s"])))
        P(f"| {b} | {len(g):,} | {fmt(rows[-1]['mean'])} | **{fmt(rows[-1]['p90'])}** | "
          f"{fmt(rows[-1]['mu_d'])} | **{rows[-1]['phi']:.3f}** | {rows[-1]['chat']:.3f} | "
          f"{rows[-1]['dr']:.3f} | {fmt(rows[-1]['prefill'], 0)} |")
    pd.DataFrame(rows).to_csv(os.path.join(D, "20_by_batch.csv"), index=False)
    P("")
    c_bc = float(m[["batch", "chat_share"]].corr().iloc[0, 1])
    c_bp = float(m[["batch", "prompt_tok_s"]].corr().iloc[0, 1])
    c_cp = float(m[["chat_share", "prompt_tok_s"]].corr().iloc[0, 1])
    P(f"Correlation across these windows: batch with chat share **{c_bc:+.3f}**, batch with "
      f"prefill rate **{c_bp:+.3f}**, chat share with prefill rate **{c_cp:+.3f}**. "
      "So the three move together and none of them can be read on its own.")
    P("")

    # ---------------------------------------------------------------- 2
    P("## 2. Holding batch fixed, does the class composition change the tail?")
    P("")
    P("Within each batch bin the windows are split into three equal groups by chat share, and the "
      "median step p90 of each group is reported with the number of windows behind it. If the "
      "tail were a property of holding chat, the p90 would rise across a row.")
    P("")
    # Rank first, so that a bin in which many windows share the same chat share
    # still splits into three equal groups instead of failing on tied edges.
    m["cs3"] = m.groupby("bbin", observed=True)["chat_share"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 3,
                          labels=["low chat", "mid", "high chat"]))
    P("| batch | low chat | mid | high chat | chat share low → high |")
    P("|---|---|---|---|---|")
    rows = []
    for b, g in m.groupby("bbin", observed=True):
        if len(g) < 90:
            continue
        cells, shares = {}, {}
        for c, gc in g.groupby("cs3", observed=True):
            cells[str(c)] = (med(gc["p90"]), len(gc))
            shares[str(c)] = med(gc["chat_share"])
        if len(cells) < 3:
            continue
        f_ = lambda k: (f"{fmt(cells[k][0])} ({cells[k][1]:,})" if k in cells else "—")
        P(f"| {b} | {f_('low chat')} | {f_('mid')} | {f_('high chat')} | "
          f"{shares.get('low chat', float('nan')):.2f} → {shares.get('high chat', float('nan')):.2f} |")
        rows.append(dict(batch=str(b), **{f"p90_{k}": v[0] for k, v in cells.items()},
                         **{f"n_{k}": v[1] for k, v in cells.items()},
                         **{f"chat_{k}": v for k, v in shares.items()}))
    pd.DataFrame(rows).to_csv(os.path.join(D, "20_batch_x_chat.csv"), index=False)
    P("")

    # ---------------------------------------------------------------- 3
    P("## 3. Holding batch fixed, does the prefill the window actually processed change the tail?")
    P("")
    P("The same split, by prefill tokens per second instead of by chat share.")
    P("")
    m["pf3"] = m.groupby("bbin", observed=True)["prompt_tok_s"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 3,
                          labels=["low prefill", "mid", "high prefill"]))
    P("| batch | low prefill | mid | high prefill | prefill tok/s low → high |")
    P("|---|---|---|---|---|")
    rows = []
    for b, g in m.groupby("bbin", observed=True):
        if len(g) < 90:
            continue
        cells, lv = {}, {}
        for c, gc in g.groupby("pf3", observed=True):
            cells[str(c)] = (med(gc["p90"]), len(gc))
            lv[str(c)] = med(gc["prompt_tok_s"])
        if len(cells) < 3:
            continue
        f_ = lambda k: (f"{fmt(cells[k][0])} ({cells[k][1]:,})" if k in cells else "—")
        P(f"| {b} | {f_('low prefill')} | {f_('mid')} | {f_('high prefill')} | "
          f"{fmt(lv.get('low prefill', float('nan')), 0)} → "
          f"{fmt(lv.get('high prefill', float('nan')), 0)} |")
        rows.append(dict(batch=str(b), **{f"p90_{k}": v[0] for k, v in cells.items()},
                         **{f"n_{k}": v[1] for k, v in cells.items()},
                         **{f"pf_{k}": v for k, v in lv.items()}))
    pd.DataFrame(rows).to_csv(os.path.join(D, "20_batch_x_prefill.csv"), index=False)
    P("")

    # ---------------------------------------------------------------- 4
    P("## 4. The decisive split: windows that processed no prefill at all")
    P("")
    P("If quietness itself made the tail, a quiet window would have a bad tail even with no "
      "prefill in it. If the prefill is what makes it, a window with no prefill should sit at its "
      "decode floor whatever its batch is.")
    P("")
    quiet = m[m["prompt_tok_s"] < NEAR_ZERO_PREFILL]
    busy = m[m["prompt_tok_s"] >= NEAR_ZERO_PREFILL]
    P("| batch | prefill | windows | step p50 | **step p90** | p90/p50 | over 50 ms budget |")
    P("|---|---|---|---|---|---|---|")
    rows = []
    for b in m["bbin"].cat.categories:
        for lbl, part in (("none", quiet), ("some", busy)):
            g = part[part["bbin"] == b]
            if len(g) < 30:
                continue
            r = dict(batch=str(b), prefill=lbl, n=len(g), p50=med(g["p50"]), p90=med(g["p90"]),
                     ratio=med(g["p90"]) / med(g["p50"]),
                     over=100.0 * float((g["p90"] > BUDGET_MS).mean()))
            rows.append(r)
            P(f"| {b} | {lbl} | {len(g):,} | {fmt(r['p50'])} | **{fmt(r['p90'])}** | "
              f"{r['ratio']:.2f} | {r['over']:.1f}% |")
    pd.DataFrame(rows).to_csv(os.path.join(D, "20_prefill_presence.csv"), index=False)
    P("")

    # ---------------------------------------------------------------- 5
    P("## 5. How much of the tail each candidate explains on its own and with the other held")
    P("")
    P("Squared Spearman correlation with the window step p90, over the same windows. `partial` "
      "holds the other two by ranking within batch bin and chat-share tercile.")
    P("")

    def sp2(a, b):
        return float(pd.Series(a).corr(pd.Series(b), method="spearman")) ** 2

    P("| candidate | alone | within batch bin |")
    P("|---|---|---|")
    rows = []
    for name, col in (("decode batch", "batch"), ("chat share", "chat_share"),
                      ("deepresearch share", "dr_share"),
                      ("prefill tok/s", "prompt_tok_s"),
                      ("phi = 1 - mu_d/mean", "phi"),
                      ("excess over decode floor (ms)", "excess_ms")):
        alone = sp2(m[col], m["p90"])
        within = []
        for b, g in m.groupby("bbin", observed=True):
            if len(g) >= 200 and g[col].nunique() > 5:
                within.append((len(g), sp2(g[col], g["p90"])))
        wm = (sum(n * v for n, v in within) / sum(n for n, _ in within)) if within else float("nan")
        rows.append(dict(candidate=name, alone=alone, within_batch=wm))
        P(f"| {name} | {alone:.3f} | {wm:.3f} |")
    pd.DataFrame(rows).to_csv(os.path.join(D, "20_variance_share.csv"), index=False)
    P("")

    # ---------------------------------------------------------------- 6
    P("## 6. The same question asked per instance rather than per window")
    P("")
    P("Per run and engine: whether that engine was the deepresearch-only one under this setting "
      "(median chat share below 0.05), and what its windows look like.")
    P("")
    inst = m.groupby(["run", "rate", "engine"]).agg(
        n=("p90", "size"), chat=("chat_share", "median"), batch=("batch", "median"),
        kv=("kv", "median"), p50=("p50", "median"), p90=("p90", "median"),
        mu_d=("mu_d", "median"), phi=("phi", "median"),
        prefill=("prompt_tok_s", "median"),
        over=("p90", lambda s: 100.0 * float((s > BUDGET_MS).mean()))).reset_index()
    inst["dedicated"] = inst["chat"] < 0.05
    inst.to_csv(os.path.join(D, "20_per_instance.csv"), index=False)
    P("| rate | kind | instances | chat share | batch | KV | mu_d | **step p90** | phi | prefill tok/s | windows over 50 ms |")
    P("|---|---|---|---|---|---|---|---|---|---|---|")
    for rate, gr in inst.groupby("rate"):
        for ded, g in gr.groupby("dedicated"):
            kind = "**deepresearch-only**" if ded else "carries chat"
            P(f"| {rate:.0f} | {kind} | {len(g)} | {med(g['chat']):.3f} | {fmt(med(g['batch']),0)} | "
              f"{med(g['kv']):.3f} | {fmt(med(g['mu_d']))} | **{fmt(med(g['p90']))}** | "
              f"{med(g['phi']):.3f} | {fmt(med(g['prefill']),0)} | {med(g['over']):.1f}% |")
    P("")

    # ---------------------------------------------------------------- 7
    P("## 7. Against the budget that instance is actually held to")
    P("")
    P("Sections 1-6 score every window against chat's 50 ms, which is the tightest budget in the "
      "workload but not the one every instance is held to. The policy's own quantity is the "
      "tightest nominal per-token allowance among the requests resident on the instance "
      "(`fluidserve.go:1018-1033`): chat 50 ms, swe 62.5 ms (30,000 ms over the profile's 480 "
      "expected tokens), deepresearch 100 ms. An instance with no chat on it is held to 100 ms, "
      "which is why it is allowed to fill to twice the KV. Recomputing the same windows against "
      "that per-window budget:")
    P("")
    m["budget_ms"] = np.where(m["n_chat"] > 0, 50.0,
                              np.where(m["n_swe"] > 0, 62.5, 100.0))
    m["over_own"] = m["p90"] > m["budget_ms"]
    m["headroom_ms"] = m["budget_ms"] - m["p90"]
    inst2 = m.groupby(["run", "rate", "engine"]).agg(
        n=("p90", "size"), chat=("chat_share", "median"),
        budget=("budget_ms", "median"), p90=("p90", "median"),
        over50=("p90", lambda s: 100.0 * float((s > BUDGET_MS).mean())),
        over_own=("over_own", lambda s: 100.0 * float(s.mean())),
        headroom=("headroom_ms", "median"),
        floor=("mu_d", "median"), phi=("phi", "median"),
        prefill=("prompt_tok_s", "median")).reset_index()
    inst2["dedicated"] = inst2["chat"] < 0.05
    inst2.to_csv(os.path.join(D, "20_per_instance_ownbudget.csv"), index=False)
    P("| rate | kind | instances | own budget | **step p90** | over 50 ms | **over its own budget** | median headroom | decode floor | prefill tok/s |")
    P("|---|---|---|---|---|---|---|---|---|---|")
    for rate, gr in inst2.groupby("rate"):
        for ded, g in gr.groupby("dedicated"):
            kind = "**deepresearch-only**" if ded else "carries chat"
            P(f"| {rate:.0f} | {kind} | {len(g)} | {med(g['budget']):.1f} | "
              f"**{fmt(med(g['p90']))}** | {med(g['over50']):.1f}% | "
              f"**{med(g['over_own']):.1f}%** | {fmt(med(g['headroom']))} | "
              f"{fmt(med(g['floor']))} | {fmt(med(g['prefill']), 0)} |")
    P("")
    P("**How much disturbance each instance can absorb, and how much it is being given.** "
      "The headroom is the budget minus the decode-only floor: what is left for prefill to use "
      "before the per-token rule is broken. Prefill is what uses it.")
    P("")
    P("| rate | kind | budget | decode floor | headroom for prefill | prefill given, tok/s | phi |")
    P("|---|---|---|---|---|---|---|")
    for rate, gr in inst2.groupby("rate"):
        for ded, g in gr.groupby("dedicated"):
            kind = "**deepresearch-only**" if ded else "carries chat"
            room = med(g["budget"]) - med(g["floor"])
            P(f"| {rate:.0f} | {kind} | {med(g['budget']):.1f} | {fmt(med(g['floor']))} | "
              f"**{fmt(room)} ms** | {fmt(med(g['prefill']), 0)} | {med(g['phi']):.3f} |")
    P("")

    with open(os.path.join(D, "20_quiet_or_chat.md"), "w") as fh:
        fh.write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    sys.exit(main())
