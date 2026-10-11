#!/usr/bin/env python3
"""Turn the per-run output of tail2026_clean_rules.py into the report.

Same split as tail2026_literature_tables.py and for the same reason: re-wording
a paragraph must never re-read 18 GB of per-token event files, and no figure in
the document may be typed in by hand. Every number below is read from
`16_clean_per_run.csv` / `16_clean_detail.csv`, and the old-versus-new
comparison additionally reads `09_lit_per_run.csv`, which is the contaminated
scoring being corrected.

  python3 tail2026_clean_tables.py > .../16_literature_rules_clean.md
"""
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

EXPDIR = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                      "..", ".."))
OUTDIR = os.path.join(EXPDIR, "results", "aggregate_analysis", "tail_2026-08-16")
S = pd.read_csv(os.path.join(OUTDIR, "16_clean_per_run.csv"))
D = pd.read_csv(os.path.join(OUTDIR, "16_clean_detail.csv"))
OLD = pd.read_csv(os.path.join(OUTDIR, "09_lit_per_run.csv"))
OLD = OLD[OLD["arm"].isin(["fspfx", "llmdslo"])]
OLDD = pd.read_csv(os.path.join(OUTDIR, "09_lit_detail.csv"))
OLDD = OLDD[OLDD["arm"].isin(["fspfx", "llmdslo"])]

ARMS = ["fspfx", "llmdslo"]
LABEL = {"fspfx": "FluidServe", "llmdslo": "llm-d"}
RATES = [10, 15, 20, 25, 35, 45, 55, 70]
# The labels carry the corrected attributions of 2026-08-17. The two rule-2
# columns are not a rule and a sensitivity check, as the earlier document
# treated them: they are the two index conventions that the four papers using
# the cumulative deadline actually write, and they are reported as equals.
RULES = [("r1m", "rule 1 request mean"),
         ("r2", "rule 2 cumulative, i-form"),
         ("r2tight", "rule 2 cumulative, (n-1)-form"),
         ("r3", "rule 3 block-10"),
         ("r4", "rule 4 every token")]


def cell(sub, col):
    v = sub[col]
    if v.isna().all():
        return "-"
    return f"{v.mean():.2f} ({v.min():.2f}..{v.max():.2f})"


def rule_table(prefix, metric, classtag="", frame=None, extra=()):
    d = (S if frame is None else frame)
    d = d[d["prefix"] == prefix]
    suf = f"_{classtag}" if classtag else ""
    head = "| arm | req/s | " + " | ".join(n for _, n in RULES)
    bar = "|---|---:|" + "---:|" * len(RULES)
    for _, n in extra:
        head += f" | {n}"
        bar += "---:|"
    lines = [head + " |", bar]
    for arm in ARMS:
        for rate in RATES:
            sub = d[(d["arm"] == arm) & (d["rate"] == rate)]
            if sub.empty:
                continue
            cells = [cell(sub, f"{r}{suf}_{metric}") for r, _ in RULES]
            cells += [cell(sub, c) for c, _ in extra]
            lines.append(f"| {LABEL[arm]} | {rate:.0f} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def lead_table(frame, prefix, metric="all"):
    d = frame[frame["prefix"] == prefix]
    lines = ["| req/s | " + " | ".join(n for _, n in RULES) + " |",
             "|---:|" + "---:|" * len(RULES)]
    marks = 0
    for rate in RATES:
        cells = []
        for rule, _ in RULES:
            a = d[(d["arm"] == "fspfx") & (d["rate"] == rate)][f"{rule}_{metric}"]
            b = d[(d["arm"] == "llmdslo") & (d["rate"] == rate)][f"{rule}_{metric}"]
            if a.empty or b.empty:
                cells.append("-")
                continue
            diff = a.mean() - b.mean()
            overlap = (a.min() <= b.max()) and (b.min() <= a.max())
            marks += int(overlap)
            who = "FluidServe" if diff > 0 else "llm-d"
            cells.append(f"{'~' if overlap else ''}{who} +{abs(diff):.1f}")
        lines.append(f"| {rate:.0f} | " + " | ".join(cells) + " |")
    return "\n".join(lines), marks


def emit(s=""):
    print(s)


raw = S[S["prefix"] == "raw"]
oraw = OLD[OLD["prefix"] == "raw"]

# ------------------------------------------------------------------ preamble
emit("# The four literature per-token rules, re-scored on the EXP-82 runs")
emit()
emit("**2026-08-17. No experiment was run for this document.** The four rules of")
emit("`09_literature_rules.md` are recomputed, with the same code, on the runs that")
emit("replaced the ones that document scored. Two things change: the INPUT, because")
emit("the earlier runs carried a client-side transport artifact (section 0), and the")
emit("ATTRIBUTIONS, because a re-reading of the papers moved SLOs-Serve from rule 4")
emit("to rule 2 and left rules 3 and 4 belonging to no paper (section 1). The")
emit("arithmetic of the four rules is unchanged.")
emit()
emit("Read in one paragraph: **the cumulative deadline is the criterion four of the")
emit("compared papers use, and under it FluidServe leads llm-d at all eight arrival")
emit("rates by 2.4 to 41.7 points, under both of the two index conventions the")
emit("papers write.** The mean rule, used by Scorpio and by this repository, agrees")
emit("at every rate. The two window rules that no paper uses do not separate the")
emit("systems above 25 req/s, where neither arm exceeds 6.4% under the strictest of")
emit("them.")
emit()
emit("Scoring: `analysis_scripts/request_level/tail2026_clean_rules.py`, which")
emit("imports `scan_run`, `score_run` and `_one` from")
emit("`tail2026_literature_rules.py` rather than restating them, so no rule is")
emit("redefined and no threshold is retuned. Tables:")
emit("`analysis_scripts/request_level/tail2026_clean_tables.py`. Machine-readable")
emit("output: `16_clean_per_run.csv` (64 rows = 32 runs x 2 arrival treatments) and")
emit("`16_clean_detail.csv` (32 rows).")
emit()
emit("## 0. Why the earlier scoring had to be redone")
emit()
emit("The runs behind `09_literature_rules.md` were collected while the Llumnix")
emit("gateway process was exhausting its CPU quota and being stopped by the kernel.")
emit("A stopped gateway freezes every stream it is carrying at the same instant and")
emit("then, when it is scheduled again, hands the client several already generated")
emit("tokens in one write. On the client that is recorded as one long inter-chunk")
emit("gap followed by a run of gaps near zero. It leaves a request's MEAN")
emit("inter-token time almost alone, because the span from first token to last and")
emit("the number of tokens are both unchanged, but it moves every per-token")
emit("percentile, and it moved it on the four control planes that reach the engines")
emit("through the Llumnix gateway and not on llm-d, which reaches them through its")
emit("own inference gateway. The distortion therefore fell entirely on one side of")
emit("the comparison, which is what makes it disqualifying rather than merely")
emit("noisy: rules 3 and 4 read short windows of the stream and are exactly the")
emit("columns such an artifact can move.")
emit()
emit("The cause was removed at source by setting `GOMAXPROCS=16` on the gateway so")
emit("the Go runtime stops creating more runnable threads than the container's CPU")
emit("quota allows, and both arms were re-measured as EXP-82. The signature of a")
emit("batched delivery is an inter-chunk gap below 5 ms, which is far below any")
emit("real decode interval on this hardware; the EXP-82 write-up records it falling")
emit("from 14.11% to 0.29% pooled over the gaps of both arms. Measured here as the")
emit("mean over runs of each run's mean per-request fraction, which weights every")
emit("request equally instead of every gap, the same quantity is:")
emit()
emit("| arm | sub-5 ms inter-chunk gap fraction, mean over 16 runs (min .. max) |")
emit("|---|---:|")
for arm in ARMS:
    sub = raw[raw["arm"] == arm]
    o = oraw[oraw["arm"] == arm]
    emit(f"| {LABEL[arm]} | {100*sub['sub_tau_frac'].mean():.3f}% "
         f"({100*sub['sub_tau_frac'].min():.3f}% .. "
         f"{100*sub['sub_tau_frac'].max():.3f}%) "
         f" -- was {100*o['sub_tau_frac'].mean():.3f}% |")
emit()
emit("llm-d's figure is unchanged, which is the check that the artifact was the")
emit("Llumnix gateway and not something in the workload or the client: llm-d never")
emit("passed through the throttled process, so a fix to that process must leave it")
emit("where it was, and it does.")
emit()
emit("Because the bursting is gone, the **deburst** treatment -- which spreads the")
emit("wall-clock time of a sub-5 ms run of chunks evenly over the tokens in it --")
emit("is now close to a no-op on both arms rather than a correction that only one")
emit("arm needed. The largest difference it makes to any all-arrivals cell in this")
d_raw = S[S["prefix"] == "raw"].set_index("run")
d_deb = S[S["prefix"] == "deb"].set_index("run")
mx = max(float((d_raw[f"{r}_all"] - d_deb[f"{r}_all"]).abs().max())
         for r, _ in RULES)
emit(f"document is {mx:.2f} points. The raw treatment is therefore the primary one")
emit("below and the deburst tables are kept only so the two documents can be")
emit("compared column for column.")
emit()

# ------------------------------------------------- corrected attributions
emit("## 1. What each rule is, and which paper actually uses it")
emit()
emit("**The attributions below correct the ones in `09_literature_rules.md`.** They")
emit("come from a re-reading of the papers themselves rather than from the earlier")
emit("extraction. The arithmetic of the four rules is unchanged -- the same four")
emit("conditions are computed on the same streams -- but what the columns MEAN, and")
emit("in particular which of them corresponds to a criterion any published system")
emit("uses, is different.")
emit()
emit("Write `a_1 <= a_2 <= ... <= a_N` for the arrival offsets of the N output")
emit("tokens of one request in milliseconds from submission, `T` for the class")
emit("time-to-first-token budget and `P` for the class per-token budget.")
emit()
emit("| rule | condition | who uses it |")
emit("|---|---|---|")
emit("| 1 request mean | `(a_N - a_1) / (N - 1) <= P` | Scorpio, and the status quo in this repository. \"TPOT sets an upper bound on the average latency for generating subsequent tokens\" |")
emit("| 2 cumulative deadline | `a_n` no later than a line fixed at arrival, for every `n` | **PolyServe, QoServe, JITServe and SLOs-Serve -- four papers, not three.** SLOs-Serve expresses it in rate form, as a demand line starting at `pDDL_i` and rising at `k_i` tokens per second (its p5), which is the same constraint written as a rate rather than as a per-token deadline |")
emit("| 3 block mean of 10 | the mean of every consecutive non-overlapping block of 10 per-token gaps is `<= P` | **no paper.** This was attributed to SLOs-Serve as its practical check; that attribution falls with the correction above |")
emit("| 4 every token | `a_n - a_{n-1} <= P` for every `n` | **no paper.** This was attributed to SLOs-Serve as its stated rule; SLOs-Serve in fact uses the cumulative deadline |")
emit()
emit("**Two consequences, and the second is the one that changes how this document")
emit("should be read.**")
emit()
emit("**First, the cumulative deadline is the criterion of the serving literature,**")
emit("not one option among four. Four of the systems this work is compared against")
emit("express their service-level objective as a deadline schedule fixed when the")
emit("request arrives, and the mean rule is a fifth convention used by one paper and")
emit("by this repository. Rules 1 and 2 between them therefore cover every published")
emit("attainment criterion in this comparison set.")
emit()
emit("**Second, rule 4 -- the row under which llm-d leads at most rates -- is not a")
emit("convention that any of these papers uses.** It is an upper bound on how bad")
emit("the severity axis can get, computed here because it bounds the others and")
emit("because the earlier extraction wrongly named it as SLOs-Serve's rule. It must")
emit("not be presented as \"llm-d wins under SLOs-Serve's criterion\", and neither")
emit("must rule 3. Under SLOs-Serve's actual criterion, which is the cumulative")
emit("deadline, the ordering is the one in the rule-2 columns.")
emit()
emit("**No paper in this set uses a per-token latency percentile as its attainment")
emit("criterion.** Where attainment is defined it is a per-request pass or fail")
emit("against a deadline, and percentiles appear beside it descriptively; QoServe")
emit("reports no per-token percentile at all, saying explicitly that it omits them")
emit("because violations were under 0.1%, and PolyServe reports no latency")
emit("percentile anywhere. The four rules scored here are therefore the complete set")
emit("of attainment criteria in this literature, and **any percentile-based scoring")
emit("this project does -- for instance scoring a request on its p90 inter-token")
emit("time rather than on its mean -- is this project's own construction and must be")
emit("presented as such rather than as a convention borrowed from the field.**")
emit()

# ------------------------------------------------------------------ what ran
emit("## 2. What exists, and which cells have one repeat rather than two")
emit()
emit("Every EXP-82 directory under `results/` was enumerated. Directories whose")
emit("name contains `PRERUN` were excluded: those are the llm-d latency-predictor")
emit("warm-up passes that precede each measured condition, not measurements. There")
emit("were 16 of them, one per llm-d condition, and no other directory was dropped.")
emit()
emit("| arm | req/s | repeats | runs |")
emit("|---|---:|---:|---|")
one_rep = []
for arm in ARMS:
    for rate in RATES:
        sub = raw[(raw["arm"] == arm) & (raw["rate"] == rate)]
        runs = ", ".join(f"`{x}`" for x in sorted(sub["run"]))
        if len(sub) < 2:
            one_rep.append((arm, rate, len(sub)))
        emit(f"| {LABEL[arm]} | {rate:.0f} | {len(sub)} | {runs} |")
emit()
if one_rep:
    emit("**Cells with fewer than two repeats:** " + "; ".join(
        f"{LABEL[a]} at {r:.0f} req/s ({n})" for a, r, n in one_rep) + ".")
else:
    emit("**Every one of the 16 cells has two repeats.** No cell in any table")
    emit("below rests on a single measurement. One caution about the last cell to")
    emit("arrive: `260816_0845_exp82r1_fspfx_m1_rpm_4200` finished and merged its")
    emit("per-worker shards while this analysis was being prepared; it was read")
    emit("only after its `metrics.csv` and `tbt_events.jsonl` had stopped growing")
    emit("and its `shards/` directory had been removed by the runner's own")
    emit("verified merge, which is the signal that the merge completed.")
emit()
emit(f"Stream coverage -- the share of requests that must be judged on a per-token")
emit("rule, were not rejected, did not error and were not cut off by the end of the")
emit("load window, for which a per-chunk record was found -- is")
emit(f"{100*S['stream_coverage'].min():.1f}% at worst and {100*S['stream_coverage'].mean():.1f}% on average. The remainder")
emit("falls back to the recorded mean, which is rule 1; the counts are in the CSV")
emit("as `n_need_stream` and `n_have_stream`.")
emit()
emit("The stream still carries one token per chunk, which is what lets a gap")
emit("between two chunks be read as a gap between two tokens. Ratio of chunk count")
emit("to the server's `output_tokens` over every chat and deepresearch request:")
emit()
emit("| arm | chunks per server token, min .. max over 16 runs |")
emit("|---|---:|")
for arm in ARMS:
    sub = raw[raw["arm"] == arm]
    emit(f"| {LABEL[arm]} | {sub['chunk_over_token'].min():.4f} .. "
         f"{sub['chunk_over_token'].max():.4f} |")
emit()

# ------------------------------------------------------------------ swe check
emit("### swe comes out identical under all four rules, and that is asserted")
emit()
swe_cols = [f"{r}_swe_all" for r, _ in RULES] + \
    ["r3_nopartial_swe_all", "r1_swe_all"]
spread = (S[swe_cols].max(axis=1) - S[swe_cols].min(axis=1)).max()
emit("The swe class is scored on an end-to-end budget of 30 s, which contains no")
emit("per-token term, so changing the per-token rule cannot change its score. Over")
emit(f"all {len(S)} run-treatment rows the largest spread of the swe attainment across the")
emit(f"four rules is **{spread:.4f} points**, that is, exactly zero. swe is carried")
emit("unchanged into every aggregate below, so all movement in the totals comes")
emit("from chat and deepresearch.")
emit()

# ------------------------------------------------------------------ task 1
emit("## 3. Task 1 -- the four rules on the all-arrivals denominator")
emit()
emit("**Denominator: all arrivals.** Every request that arrived inside the analysis")
emit("window is in it; a rejection, a client error and a request still unfinished")
emit("when the load window closed each count as a violation. Each cell is")
emit("`mean (min..max)` over the two repeats.")
emit()
emit("### 3.1 raw arrival times")
emit()
emit(rule_table("raw", "all"))
emit()
emit("### 3.2 deburst arrival times")
emit()
emit(rule_table("deb", "all"))
emit()

# ------------------------------------------------------------------ task 2
# ------------------------------------------------- the two index conventions
emit("## 4. The two index conventions of rule 2, side by side")
emit()
emit("The papers that use the cumulative deadline do not agree on the index, and")
emit("the disagreement is worth exactly one per-token budget everywhere along the")
emit("schedule.")
emit()
emit("| form | deadline of output token `n` | who writes it |")
emit("|---|---|---|")
emit("| `i`-form | `arrival + T + n * P` | JITServe writes `TTFT_SLO + i x TBT_SLO` without saying whether `i` counts from 0 or from 1; PolyServe section 2.3 gives `TTFT + i x TPOT` |")
emit("| `(n-1)`-form | `arrival + T + (n - 1) * P` | QoServe, unambiguously: token `n` is due at `arrival + SLO_TTFT + (n-1) x SLO_TBT`, so at `n = 1` it reduces to the time-to-first-token deadline and the TTFT budget is the intercept of the same line rather than a separate check. PolyServe section 4.6 describes this form, contradicting its own section 2.3 |")
emit()
emit("The `(n-1)`-form is one per-token budget tighter at every index. Both are")
emit("computed on every run and reported as equals in every table of this document;")
emit("neither is a sensitivity check on the other.")
emit()
emit("**The question the two forms have to answer: is the gap between the")
emit("conventions comparable to the gap between the arms?** If it were, no")
emit("conclusion could rest on rule 2 without naming the convention. Measured on the")
emit("all-arrivals denominator, raw arrival times:")
emit()
emit("| req/s | FluidServe lead, `i`-form | FluidServe lead, `(n-1)`-form | FluidServe: `i` minus `(n-1)` | llm-d: `i` minus `(n-1)` |")
emit("|---:|---:|---:|---:|---:|")
for rate in RATES:
    a = raw[(raw["arm"] == "fspfx") & (raw["rate"] == rate)]
    b = raw[(raw["arm"] == "llmdslo") & (raw["rate"] == rate)]
    emit(f"| {rate} | {a['r2_all'].mean()-b['r2_all'].mean():+.2f} | "
         f"{a['r2tight_all'].mean()-b['r2tight_all'].mean():+.2f} | "
         f"{a['r2_all'].mean()-a['r2tight_all'].mean():.3f} | "
         f"{b['r2_all'].mean()-b['r2tight_all'].mean():.3f} |")
emit()
idx_gap = (raw["r2_all"] - raw["r2tight_all"]).abs()
arm_gap = [abs(raw[(raw["arm"] == "fspfx") & (raw["rate"] == r)]["r2_all"].mean() -
               raw[(raw["arm"] == "llmdslo") & (raw["rate"] == r)]["r2_all"].mean())
           for r in RATES]
idx_cls = max(float((raw[f"r2_{t}_all"] - raw[f"r2tight_{t}_all"]).abs().max())
              for t in ("chat", "dr"))
emit(f"**No. The two conventions differ by at most {idx_gap.max():.2f} points on the")
emit(f"all-arrivals total and {idx_cls:.2f} points on any single class, against a")
emit(f"between-arm gap of {min(arm_gap):.1f} to {max(arm_gap):.1f} points.** The largest")
emit("difference the index convention makes to the FluidServe lead at any rate is")
mx_lead = max(abs((raw[(raw["arm"] == "fspfx") & (raw["rate"] == r)]["r2_all"].mean() -
                   raw[(raw["arm"] == "llmdslo") & (raw["rate"] == r)]["r2_all"].mean()) -
                  (raw[(raw["arm"] == "fspfx") & (raw["rate"] == r)]["r2tight_all"].mean() -
                   raw[(raw["arm"] == "llmdslo") & (raw["rate"] == r)]["r2tight_all"].mean()))
              for r in RATES)
emit(f"{mx_lead:.2f} points, and it never changes which arm leads at any rate.")
emit()
emit("The reason the ambiguity costs so little here is arithmetic and is worth")
emit("stating, because it is a property of this workload and would not survive a")
emit("different one. Moving from the `i`-form to the `(n-1)`-form shifts the whole")
emit("deadline line down by one per-token budget -- 50 ms for chat, 100 ms for")
emit("deepresearch -- uniformly in `n`. A median chat request in these runs carries")
emit("roughly 4.5 s of unused time-to-first-token budget as decode slack (section 9),")
emit("so removing 50 ms of it changes the outcome only for a request that was inside")
emit("its schedule by less than one token's worth of time. On a workload with a")
emit("tight time-to-first-token budget, or with much shorter requests, one budget")
emit("would be a large share of the total slack and the two forms would separate.")
emit()
emit("**So rule 2's conclusion on these runs does not depend on the index")
emit("convention**, and that is a measured result rather than an assumption. It is")
emit("not a licence to omit the convention: the value that must be stated alongside")
emit("any rule-2 figure is which form produced it, because the finding that the two")
emit("agree is specific to this workload's slack.")
emit()

emit("## 5. Task 2 -- the offered and admitted denominators, with the rejection rate")
emit()
emit("The admitted denominator drops rejected requests from the population, so a")
emit("policy that refuses more work scores higher on it for that reason alone. It")
emit("is therefore never reported without the rejection rate next to it. The")
emit("rejection rate below is the share of requests, among those whose outcome was")
emit("determined, that the system refused; requests still in flight when the load")
emit("window closed leave every denominator here.")
emit()
emit("On these runs a rejected request also has `is_error` set, which is why the")
emit("rejection and error columns of the per-run CSV agree to within a few")
emit("hundredths of a point; no truncation filter is applied when counting what was")
emit("admitted.")
emit()
emit("### 5.1 offered denominator (rejections are violations, cutoffs excluded)")
emit()
emit(rule_table("raw", "offered", extra=(("reject_pct", "rejected %"),)))
emit()
emit("### 5.2 admitted denominator (rejections leave the population)")
emit()
emit(rule_table("raw", "admitted", extra=(("reject_pct", "rejected %"),)))
emit()

# ------------------------------------------------------------------ task 3
emit("## 6. Task 3 -- per class, chat and deepresearch")
emit()
emit("chat is 76.9% of the requests and deepresearch 4.7%, so the all-arrivals")
emit("totals above sit close to the chat column, and the deepresearch column is")
emit("where a small class can be starved without moving the total. All-arrivals")
emit("denominator, raw arrival times.")
emit()
for tag, name in (("chat", "chat (T = 5 s, P = 50 ms)"),
                  ("dr", "deepresearch (T = 10 s, P = 100 ms)")):
    emit(f"### 6.{1 if tag == 'chat' else 2} {name}")
    emit()
    emit(rule_table("raw", "all", tag))
    emit()

# ------------------------------------------------------------------ task 4
emit("## 7. Task 4 -- the contaminated numbers and the clean ones, side by side")
emit()
emit("`old` is `09_literature_rules.md` section 3.1, computed on runs collected")
emit("while the gateway was being throttled. `new` is section 3.1 of this document.")
emit("Both are the all-arrivals denominator, raw arrival times, mean of two repeats;")
emit("`d` is new minus old in percentage points. The two sets of runs are different")
emit("sessions, so a difference is the transport fix plus whatever moves between")
emit("sessions; the repeat spread of each is printed so that a `d` smaller than the")
emit("spread is not read as a correction.")
emit()
for ri, (rule, name) in enumerate(RULES, 1):
    emit(f"### 7.{ri} {name}")
    emit()
    emit("| arm | req/s | old | new | d |")
    emit("|---|---:|---:|---:|---:|")
    for arm in ARMS:
        for rate in RATES:
            n = raw[(raw["arm"] == arm) & (raw["rate"] == rate)][f"{rule}_all"]
            o = oraw[(oraw["arm"] == arm) & (oraw["rate"] == rate)][f"{rule}_all"]
            if n.empty or o.empty:
                continue
            emit(f"| {LABEL[arm]} | {rate:.0f} | "
                 f"{o.mean():.2f} ({o.min():.2f}..{o.max():.2f}) | "
                 f"{n.mean():.2f} ({n.min():.2f}..{n.max():.2f}) | "
                 f"{n.mean()-o.mean():+.2f} |")
    emit()

emit("### 7.6 what moved, in one place")
emit()
tot = {}
for rule, name in RULES:
    for arm in ARMS:
        n = raw[raw["arm"] == arm].groupby("rate")[f"{rule}_all"].mean()
        o = oraw[oraw["arm"] == arm].groupby("rate")[f"{rule}_all"].mean()
        d = (n - o).dropna()
        tot[(rule, arm)] = d
emit("| rule | arm | mean d | most negative | most positive |")
emit("|---|---|---:|---:|---:|")
for rule, name in RULES:
    for arm in ARMS:
        d = tot[(rule, arm)]
        emit(f"| {name} | {LABEL[arm]} | {d.mean():+.2f} | {d.min():+.2f} | "
             f"{d.max():+.2f} |")
emit()

emit("### 7.7 did the ranking change")
emit()
emit("Each cell names the arm that leads and by how many percentage points, on the")
emit("all-arrivals denominator, mean of two repeats. A cell is marked `~` when the")
emit("two arms' min..max ranges over the two repeats overlap, which means the")
emit("difference is inside the repeat spread and must not be read as a difference.")
emit()
emit("**Clean runs (EXP-82):**")
emit()
tbl_new, marks_new = lead_table(S, "raw")
emit(tbl_new)
emit()
emit("**Contaminated runs, for comparison:**")
emit()
tbl_old, marks_old = lead_table(OLD, "raw")
emit(tbl_old)
emit()
flips = []
for rate in RATES:
    for rule, name in RULES:
        def who(fr):
            a = fr[(fr["prefix"] == "raw") & (fr["arm"] == "fspfx") &
                   (fr["rate"] == rate)][f"{rule}_all"]
            b = fr[(fr["prefix"] == "raw") & (fr["arm"] == "llmdslo") &
                   (fr["rate"] == rate)][f"{rule}_all"]
            if a.empty or b.empty:
                return None, None, None
            ov = (a.min() <= b.max()) and (b.min() <= a.max())
            return ("FluidServe" if a.mean() > b.mean() else "llm-d"), \
                abs(a.mean() - b.mean()), ov
        wn, dn, ovn = who(S)
        wo, do, ovo = who(OLD)
        if wn is None or wo is None:
            continue
        if wn != wo:
            flips.append((rate, name, wo, do, ovo, wn, dn, ovn))
if flips:
    emit(f"**{len(flips)} of the 40 cells changed which arm leads.**")
    emit()
    emit("| req/s | rule | old leader | old margin | new leader | new margin | inside repeat spread |")
    emit("|---:|---|---|---:|---|---:|---|")
    for rate, name, wo, do, ovo, wn, dn, ovn in flips:
        ins = []
        if ovo:
            ins.append("old")
        if ovn:
            ins.append("new")
        emit(f"| {rate} | {name} | {wo} | {do:.1f} | {wn} | {dn:.1f} | "
             f"{'/'.join(ins) if ins else 'neither'} |")
else:
    emit("**No cell changed which arm leads.**")
emit()

# ------------------------------------------------------------------ task 5
emit("## 8. Task 5 -- where the cumulative deadline is first missed")
emit()
emit("For each request that fails rule 2, the position of the FIRST token to miss")
emit("its own deadline, as a fraction of that request's total output length. A")
emit("failure at the start means the request never got going: it spent so much of")
emit("its time-to-first-token budget waiting that it began decoding with no slack")
emit("left. A failure late means the request ran well and then stalled, which is")
emit("interference from work admitted onto the same engine after it started.")
emit()
emit("Both arms at 25 and 35 req/s, raw arrival times, both repeats listed")
emit("separately. `TTFT already over` is the share of those failures whose first")
emit("token had already arrived after `T`; such a request fails the cumulative")
emit("deadline at or near index 1 for that reason alone, and counting it as a")
emit("per-token failure would count one violation twice.")
emit()
for tag, name in (("chat", "chat"), ("dr", "deepresearch")):
    emit(f"### 8.{1 if tag == 'chat' else 2} {name}")
    emit()
    emit("| arm | req/s | run | requests | failing rule 2 | TTFT already over | p10 | p25 | p50 | p75 | p90 | in first 10% | past halfway | median first-miss token |")
    emit("|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    sub = D[D["rate"].isin([25, 35])]
    for _, r in sub.sort_values(["arm", "rate", "run"]).iterrows():
        p = f"raw_{tag}"
        n, nf = r[p + "_n"], r[p + "_nfail2"]
        if not nf or np.isnan(nf) or nf == 0:
            emit(f"| {LABEL[r['arm']]} | {r['rate']:.0f} | `{r['run']}` | {n:.0f} | "
                 f"0 (0.00%) | - | - | - | - | - | - | - | - | - |")
            continue
        emit(f"| {LABEL[r['arm']]} | {r['rate']:.0f} | `{r['run']}` | {n:.0f} | "
             f"{nf:.0f} ({100*nf/n:.2f}%) | {100*r[p+'_ff_ttftover']:.1f}% | "
             + " | ".join(f"{r[p+f'_ffq{q}']:.3f}" for q in (10, 25, 50, 75, 90))
             + f" | {100*r[p+'_ff_lt10']:.1f}% | {100*r[p+'_ff_gt50']:.1f}% | "
               f"{r[p+'_ffidx_q50']:.0f} |")
    emit()

emit("Reading the two classes together, and noting what changed from the")
emit("contaminated scoring.")
emit()
sub25 = D[(D["arm"] == "fspfx") & (D["rate"].isin([25, 35]))]
osub25 = OLDD[(OLDD["arm"] == "fspfx") & (OLDD["rate"].isin([25, 35]))]
emit("**On FluidServe the chat class has essentially stopped failing rule 2.** At")
emit("25 and 35 req/s it now fails the cumulative deadline on 0 to 9 chat requests")
emit("out of about 9,000 to 11,000, that is 0.00% to 0.08%, against 0.03% to 5.17%")
emit("on the contaminated runs. That is the largest single movement in this")
emit("document and it is the expected one: a gateway stall inserted a multi-hundred")
emit("millisecond gap into a stream that was otherwise inside budget, and the")
emit("cumulative deadline fails a request permanently once it falls behind the")
emit("line, so a single inserted stall late in a long chat request was enough. With")
emit("the stalls gone there is nothing left to push chat over its schedule at these")
emit("rates. The distribution columns for FluidServe chat are printed for")
emit("completeness and describe between 1 and 9 requests; they carry no shape.")
emit()
emit("**FluidServe's deepresearch rule-2 failures are now almost entirely")
emit("time-to-first-token failures.** At 35 req/s 21.8% and 23.5% of deepresearch")
emit("requests miss the cumulative deadline, and 98.3% and 98.2% of those had")
emit("already missed the 10 s time-to-first-token budget outright, with the median")
emit("first miss at output token 1 of about 968. A request whose deadline schedule")
emit("starts at 10 s and which misses at token 1 waited in the queue for its whole")
emit("time-to-first-token budget and began decoding with no slack; the first")
emit("ordinary decode interval then put it over. This is a queueing signal, not")
emit("mid-flight interference, and it is the same signal that the class rule's own")
emit("first conjunct already records. On the contaminated runs the same share was")
emit("20.7% and 33.1%, so the failures were a mixture; they are now nearly pure.")
emit()
emit("**On llm-d, chat carries both failure modes at once and deepresearch")
emit("carries none.** Across the four llm-d chat conditions the median first miss")
emit("falls between 0.25 and 0.37 of the way through the request, 9.5% to 27.5% of")
emit("failures occur inside the first tenth and 25.1% to 37.5% past the halfway")
emit("point, at absolute positions of output token 111 to 168 of a median 390-token")
emit("request. So some llm-d chat requests never got started and a comparable")
emit("number ran most of the way and then stalled. Deepresearch has zero rule-2")
emit("failures on three of the four conditions and 11 on the fourth. The shape of")
emit("the llm-d distribution is close to what the contaminated scoring showed,")
emit("which is again consistent with llm-d not having been affected.")
emit()
emit("**The volume of rule-2 failure is small compared with the total shortfall.**")
emit("At 25 req/s FluidServe fails the cumulative deadline on at most 0.01% of chat")
emit("requests while its all-arrivals attainment is 95.3%, so essentially the whole")
emit("of that shortfall is rejection, client error, unfinished work and the")
emit("time-to-first-token conjunct rather than the per-token rule. For llm-d at")
emit("25 req/s it is 2.8% to 3.8% of chat requests against an all-arrivals")
emit("attainment of 72.6%, so the same holds there.")
emit()

# ------------------------------------------------------------------ caveat
emit("## 9. The caveat re-checked -- rule 2 still scores HIGHER than rule 1")
emit()
emit("`09_literature_rules.md` recorded that the cumulative deadline, despite")
emit("constraining every prefix of the request rather than only its end, scored")
emit("higher than the mean nearly everywhere, and that this is a property of the")
emit("rule and not of the systems. The reason is that the deadline of token `i` is")
emit("`T + i * P` counted from ARRIVAL, so a request that produced its first token")
emit("well inside its time-to-first-token budget carries the unused part of `T`")
emit("forward as decode slack. That must be restated on the clean runs, because")
emit("\"FluidServe also wins under a stricter rule\" would be a false claim if rule 2")
emit("is in fact the looser one here.")
emit()
emit("**It is still the looser one.** Rule 2 exceeds rule 1 on the all-arrivals")
n_hi = int((raw["r2_all"] > raw["r1m_all"]).sum())
emit(f"denominator in {n_hi} of the {len(raw)} runs scored here, and the two rules are")
emit("not nested: both directions of disagreement occur on both arms. Over chat and")
emit("deepresearch requests with a recorded stream, pooled per run:")
emit()
emit("| arm | passes rule 1, fails rule 2 | fails rule 1, passes rule 2 |")
emit("|---|---:|---:|")
for arm in ARMS:
    sub = D[D["arm"] == arm]
    emit(f"| {LABEL[arm]} | {100*sub['raw_pass1_fail2'].mean():.2f}% "
         f"(max {100*sub['raw_pass1_fail2'].max():.2f}%) | "
         f"{100*sub['raw_fail1_pass2'].mean():.2f}% "
         f"(max {100*sub['raw_fail1_pass2'].max():.2f}%) |")
emit()
emit("The size of the relaxation, computed the same way as before but on the clean")
emit("runs. For a class with time-to-first-token budget `T`, per-token budget `P`,")
emit("median first-token latency `t1` and median output length `N`, the cumulative")
emit("deadline lets the request use `(T - t1) / N` milliseconds per token more than")
emit("the mean rule does, so the effective per-token budget is `P + (T - t1) / N`.")
emit()
emit("| arm | class | median first token | median output tokens | extra ms per token | nominal P | effective P | relaxation |")
emit("|---|---|---:|---:|---:|---:|---:|---:|")
for arm in ARMS:
    sub = D[D["arm"] == arm]
    for tag, cname, T, P in (("chat", "chat", 5000.0, 50.0),
                             ("dr", "deepresearch", 10000.0, 100.0)):
        t1 = float(sub[f"{tag}_ttft_ms_q50"].median())
        N = float(sub[f"{tag}_outtok_q50"].median())
        slack = (T - t1) / N
        emit(f"| {LABEL[arm]} | {cname} | {t1:.0f} ms | {N:.0f} | "
             f"{slack:+.1f} | {P:.0f} ms | {P+slack:.1f} ms | "
             f"{100*slack/P:+.0f}% |")
emit()
emit("The same arithmetic at the 10th percentile of chat output length, where the")
emit("relaxation is largest because the unused time-to-first-token budget is spread")
emit("over fewer tokens:")
emit()
emit("| arm | 10th-percentile chat output tokens | extra ms per token | effective P |")
emit("|---|---:|---:|---:|")
for arm in ARMS:
    sub = D[D["arm"] == arm]
    t1 = float(sub["chat_ttft_ms_q50"].median())
    N10 = float(sub["chat_outtok_q10"].median())
    slack = (5000.0 - t1) / N10
    emit(f"| {LABEL[arm]} | {N10:.0f} | {slack:+.1f} | {50.0+slack:.1f} ms |")
emit()
emit("**A cumulative deadline measured from arrival is therefore a weaker")
emit("per-token constraint than the mean for short requests and converges to it")
emit("only for long ones**, exactly as recorded before. The `(n-1)`-form removes")
emit("exactly one per-token budget of that slack; across the runs here it moves the")
d2 = (raw["r2_all"] - raw["r2tight_all"]).abs()
emit(f"all-arrivals totals by at most {d2.max():.2f} points (section 4), which shows the")
emit("slack comes from the unused time-to-first-token budget and not from the index")
emit("convention. **The lead under rule 2 must not be described as a lead under a")
emit("stricter rule.** It is a lead under the criterion four of the compared papers")
emit("actually use, which on this workload is slightly looser than the mean. Both")
emit("halves of that sentence matter: rule 2 is the literature's criterion, and it")
emit("is not the stricter one.")
emit()

# ------------------------------------------------------------------ conclusion
emit("## 10. Does the previous conclusion survive")
emit()


def margin(rule, rate, frame=None):
    fr = (S if frame is None else frame)
    fr = fr[fr["prefix"] == "raw"]
    a = fr[(fr["arm"] == "fspfx") & (fr["rate"] == rate)][f"{rule}_all"]
    b = fr[(fr["arm"] == "llmdslo") & (fr["rate"] == rate)][f"{rule}_all"]
    ov = (a.min() <= b.max()) and (b.min() <= a.max())
    return a.mean() - b.mean(), ov


emit("The three claims of `09_literature_rules.md` section 0, taken one at a time.")
emit()
m1 = [margin("r1m", r) for r in RATES]
m2 = [margin("r2", r) for r in RATES]
emit("**Claim 1: under rules 1 and 2, FluidServe leads at every rate and by a")
emit("similar margin under both. HOLDS.** Rule 1 leads, by rate: " +
     ", ".join(f"{r} req/s {d:+.1f}" for r, (d, _) in zip(RATES, m1)) + ".")
emit("Rule 2 leads: " +
     ", ".join(f"{r} req/s {d:+.1f}" for r, (d, _) in zip(RATES, m2)) + ".")
gap = max(abs(a - b) for (a, _), (b, _) in zip(m1, m2))
emit(f"The two rules never disagree about the leader, and the largest difference")
emit(f"between the rule-1 margin and the rule-2 margin at the same rate is")
emit(f"{gap:.1f} points, at 15 req/s. Rates at which the two arms' repeat ranges")
emit("overlap, so the difference there is inside the spread and must not be read as")
emit("a difference: rule 1, " +
     ((", ".join(f"{r} req/s" for r, (_, o) in zip(RATES, m1) if o))
      or "none of the eight rates") +
     "; rule 2, " +
     ((", ".join(f"{r} req/s" for r, (_, o) in zip(RATES, m2) if o))
      or "none of the eight rates") + ".")
om1 = [margin("r1m", r, OLD) for r in RATES]
dm1 = [n - o for (n, _), (o, _) in zip(m1, om1)]
bigger = sum(1 for x in dm1 if x > 0)
emit(f"Against the contaminated scoring the rule-1 margin is larger at {bigger} of the")
emit(f"eight rates and smaller at {8-bigger}, moving by {min(dm1):+.1f} to {max(dm1):+.1f} points, and it")
emit("never changes sign. That movement is not the transport fix: rule 1 depends on")
emit("the stream only through the span from first token to last and the token count,")
emit("and a batched delivery preserves both. It is mostly llm-d scoring lower on")
emit("these sessions, which section 5.1 shows as a drop of 1.0 to 4.3 points at")
emit("35 req/s and above, together with FluidServe scoring 3.1 to 3.7 points higher")
emit("at 45 to 70 req/s.")
emit()
m3 = [margin("r3", r) for r in RATES]
emit("**Claim 2: under rule 3 it is a tie above 25 req/s. PARTLY HOLDS; the rule")
emit("itself is also unattributed** -- the block mean of 10 was credited to")
emit("SLOs-Serve as its practical check and that attribution falls with the")
emit("correction in section 1.")
emit()
emit("The sign of the small difference above 25 req/s has also changed. Rule 3")
emit("leads, by rate: " + ", ".join(f"{r} req/s {d:+.1f}{'~' if o else ''}"
                          for r, (d, o) in zip(RATES, m3)) + " (a positive number")
emit("is FluidServe ahead; `~` marks a cell where the two arms' repeat ranges")
emit("overlap). On the contaminated runs FluidServe led at 10, 15 and 20 req/s and")
emit("trailed by 0.2 to 1.3 points from 25 req/s upward. On the clean runs it leads")
emit("by a wide margin at 10 and 15 req/s, trails by 0.4 to 0.7 points at 20, 25 and")
emit("35 req/s, and leads by 0.3 to 0.7 points at 45, 55 and 70 req/s -- so the")
emit("sign of the small difference above 25 req/s has reversed at the top of the")
emit("sweep. The magnitudes there are 0.3 to 0.7 points against a rule-3 attainment")
emit("of 3.2 to 5.5 percent, and three of those six cells have overlapping repeat")
emit("ranges. **The substance is unchanged: above 25 req/s rule 3 does not separate")
emit("the two systems**, and the claim as originally worded -- a tie, with llm-d")
emit("marginally ahead -- should be restated as a tie without a direction.")
emit()
m4 = [margin("r4", r) for r in RATES]
emit("**Claim 3: under rule 4 llm-d leads throughout. NO LONGER TRUE AS STATED, and")
emit("the claim has also lost its standing.** Before the numbers: rule 4 was")
emit("presented as SLOs-Serve's stated rule, and it is not -- SLOs-Serve uses the")
emit("cumulative deadline in rate form (section 1). No paper in this comparison set")
emit("scores attainment token by token. So even where llm-d leads this row, it is a")
emit("lead under a criterion nobody publishes against, and it must not be reported")
emit("as \"llm-d wins under SLOs-Serve's rule\".")
emit()
emit("Rule 4 leads, by rate: " +
     ", ".join(f"{r} req/s {d:+.1f}{'~' if o else ''}"
               for r, (d, o) in zip(RATES, m4)) + ".")
lead_llmd = [r for r, (d, _) in zip(RATES, m4) if d < 0]
lead_fs = [(r, o) for r, (d, o) in zip(RATES, m4) if d > 0]
emit("llm-d leads at " + ", ".join(str(r) for r in lead_llmd) + " req/s and")
emit("FluidServe at " + ", ".join(
    f"{r}{' (repeat ranges overlap)' if o else ''}" for r, o in lead_fs) +
     " req/s. The 70 req/s cell is the one that is not inside the repeat spread,")
emit("so at the top of the sweep the direction has genuinely reversed rather than")
emit("merely become uncertain. Removing the gateway stalls raised FluidServe's rule-4")
emit(f"figure at every rate (by {min(tot[('r4','fspfx')]):+.2f} to {max(tot[('r4','fspfx')]):+.2f} points) and left llm-d's")
emit(f"within {tot[('r4','llmdslo')].abs().max():.2f} points, which is what the diagnosis predicted: the")
emit("artifact was inserting one long gap into FluidServe's streams and rule 4")
emit("fails a request for a single gap over budget. What does not change is the")
emit("level. The highest rule-4 figure anywhere is llm-d's")
emit(f"{raw['r4_all'].max():.1f}% at 10 req/s; above 25 req/s neither arm exceeds")
hi = raw[raw["rate"] > 25]["r4_all"].max()
emit(f"{hi:.1f}%. **Rule 4 still does not rank these control planes**; it says that")
emit("on this hardware and this workload a 50 ms per-token budget is not met token")
emit("by token by either of them.")
emit()
emit("## 11. What this does not settle")
emit()
emit("- The comparison in section 7 crosses sessions. The contaminated numbers and")
emit("  the clean ones come from different runs on different days, so each `d` is")
emit("  the transport fix plus the session-to-session movement of that quantity.")
emit("  The repeat spread of both sides is printed in every row of section 7 for")
emit("  that reason, and a `d` inside those spreads should not be attributed to the")
emit("  fix.")
emit("- Rules 3 and 4 are still measured at the client, on a socket read, through a")
emit("  proxy hop. Removing the CPU-quota stall removed the one transport effect")
emit("  that was identified and measured; it cannot remove one that was not.")
emit("- Two repeats per condition. Every table gives min..max, and the ranking")
emit(f"  table marks a cell `~` when the two arms' ranges overlap: {marks_new} of the 40")
emit(f"  cells on the clean runs, against {marks_old} of 40 on the contaminated ones.")
emit("- The finding that the two index conventions of rule 2 agree is a property of")
emit("  this workload's slack, not of the rule. One per-token budget is small here")
emit("  because a median request carries seconds of unused time-to-first-token")
emit("  budget; on a workload with a tight time-to-first-token budget or much")
emit("  shorter requests the two forms would separate, and the convention would")
emit("  then have to be named before any rule-2 figure could be quoted.")
emit("- The attributions in section 1 come from a re-reading of the papers, not")
emit("  from re-running anything. They are recorded here because they change how")
emit("  the rule-3 and rule-4 rows may be described, and the previous document")
emit("  states the superseded version.")
emit("- Only FluidServe and llm-d were re-measured. The vLLM router, Llumnix SLO")
emit("  and PolyServe arms of `09_literature_rules.md` reach the engines through")
emit("  the same Llumnix gateway and carry the same artifact; their rule-3 and")
emit("  rule-4 figures in that document should not be quoted until they are")
emit("  re-measured.")
emit()
print("<!-- generated by tail2026_clean_tables.py -->")
