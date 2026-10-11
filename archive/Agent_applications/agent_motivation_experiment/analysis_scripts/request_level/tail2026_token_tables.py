"""Markdown tables for the token-level time-between-tokens analysis.

The headline burst threshold is tau = 15 ms ("cor15"); tau = 5 ms ("cor5") is
carried alongside as a sensitivity row because the earlier tables used it.

Reads ``tail2026_tokenlevel_per_run.csv`` (written by ``tail2026_token_level.py``)
and prints every table used in ``11_token_level_tbt.md``.  Each cell carries the
two repeats as ``rep1 / rep2`` so a difference smaller than the repeat range is
visible as such.
"""
import os
import sys

import numpy as np
import pandas as pd

EXPDIR = "/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment"
CSV = os.path.join(EXPDIR, "results/aggregate_analysis/tail_2026-08-16",
                   "tail2026_tokenlevel_per_run.csv")
ARMS = ("fspfx", "llmdslo")
LABEL = {"fspfx": "FluidServe", "llmdslo": "llm-d"}
RATES = (10, 15, 20, 25, 35)
WINDOWS = (1, 2, 5, 10, 20, 50, 100, 200)


def load():
    df = pd.read_csv(CSV)
    df["arm"] = df["run"].str.extract(r"_(fspfx|llmdslo)_")
    df["rate"] = df["run"].str.extract(r"_rpm_(\d+)").astype(int) // 60
    return df.sort_values(["arm", "rate", "run"])


def cell(df, arm, rate, col, fmt="%.1f"):
    v = df[(df.arm == arm) & (df.rate == rate)][col].to_numpy()
    if not len(v) or np.all(~np.isfinite(v)):
        return "-"
    return " / ".join(fmt % x for x in v)


def table(df, cols, headers, fmts, title):
    print("\n**%s**\n" % title)
    print("| arm | req/s | " + " | ".join(headers) + " |")
    print("|---|---:|" + "---:|" * len(headers))
    for arm in ARMS:
        for rate in RATES:
            row = [cell(df, arm, rate, c, f) for c, f in zip(cols, fmts)]
            print("| %s | %d | %s |" % (LABEL[arm], rate, " | ".join(row)))


LADDER = [("p90", "p90"), ("p95", "p95"), ("p96", "p96"), ("p97", "p97"),
          ("p98", "p98"), ("p99", "p99"), ("p99_5", "p99.5"), ("p99_9", "p99.9")]


def verdict(a, b):
    """Which arm is worse at this percentile, when both repeats agree.

    a and b are the two repeats of FluidServe and of llm-d.  'FS' means
    FluidServe is the larger value in both repeats, 'llm-d' the reverse,
    'overlap' means the repeats disagree, so the repeat range covers the
    difference and there is nothing to read.
    """
    if len(a) != 2 or len(b) != 2:
        return "?"
    if a[0] > b[0] and a[1] > b[1]:
        return "FS worse"
    if a[0] < b[0] and a[1] < b[1]:
        return "llm-d worse"
    return "overlap"


def crossover(df):
    """Per rate, which arm carries the heavier tail at each percentile."""
    print("\n**Task 1d — which arm is worse at each percentile of the CORRECTED "
          "(tau = 15 ms) pooled distribution. 'overlap' = the two repeats disagree, so the "
          "repeat range covers the difference.**\n")
    print("| req/s | " + " | ".join(lbl for _, lbl in LADDER) + " |")
    print("|---:|" + "---|" * len(LADDER))
    for rate in RATES:
        cells = []
        for key, _ in LADDER:
            a = df[(df.arm == "fspfx") & (df.rate == rate)]["pool_cor15_" + key].to_numpy()
            b = df[(df.arm == "llmdslo") & (df.rate == rate)]["pool_cor15_" + key].to_numpy()
            cells.append(verdict(a, b))
        print("| %d | %s |" % (rate, " | ".join(cells)))


def main():
    df = load()

    print("\n**Coverage and exclusions.**\n")
    print("| arm | req/s | chat request rows | excluded (flagged) | of which rejected "
          "| dropped, <3 chunks | requests used | gaps pooled |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for arm in ARMS:
        for rate in RATES:
            print("| %s | %d | %s | %s | %s | %s | %s | %s |" % (
                LABEL[arm], rate,
                cell(df, arm, rate, "chat_rows", "%d"),
                cell(df, arm, rate, "excluded", "%d"),
                cell(df, arm, rate, "rejected", "%d"),
                cell(df, arm, rate, "n_short", "%d"),
                cell(df, arm, rate, "n_requests", "%d"),
                cell(df, arm, rate, "pool_raw_n", "%d")))

    table(df,
          ["pool_raw_p1", "pool_raw_frac_sub1", "pool_raw_frac_sub5",
           "pool_raw_frac_sub16", "pool_cor15_p1", "pool_cor15_frac_sub16"],
          ["raw p1 (ms)", "raw frac <1 ms", "raw frac <5 ms", "raw frac <16 ms",
           "corrected (tau=15) p1 (ms)", "corrected frac <16 ms"],
          ["%.3f", "%.4f", "%.4f", "%.4f", "%.2f", "%.4f"],
          "The transport artifact, measured on the pooled gaps.")

    for tag, name in (("raw", "RAW (recorded chunk arrival gaps) = what the CLIENT saw"),
                      ("cor15", "CORRECTED, tau = 15 ms (headline) = what the ENGINE produced"),
                      ("cor5", "CORRECTED, tau = 5 ms (sensitivity, the earlier threshold)")):
        table(df,
              ["pool_%s_p50" % tag, "pool_%s_p75" % tag, "pool_%s_p90" % tag,
               "pool_%s_p95" % tag, "pool_%s_p99" % tag, "pool_%s_p99_9" % tag,
               "pool_%s_max" % tag, "pool_%s_mean" % tag],
              ["p50", "p75", "p90", "p95", "p99", "p99.9", "max", "(mean)"],
              ["%.1f"] * 8,
              "Task 1 — pooled over every gap, %s. ms." % name)

    table(df,
          ["pool_cor15_p90", "pool_cor15_p95", "pool_cor15_p96", "pool_cor15_p97",
           "pool_cor15_p98", "pool_cor15_p99", "pool_cor15_p99_5", "pool_cor15_p99_9"],
          ["p90", "p95", "p96", "p97", "p98", "p99", "p99.5", "p99.9"],
          ["%.1f"] * 8,
          "Task 1c — fine ladder on the CORRECTED (tau = 15 ms) pooled "
          "distribution, where the two arms cross. ms.")

    crossover(df)

    table(df,
          ["pool_raw_p90", "pool_cor2_p90", "pool_cor5_p90", "pool_cor10_p90",
           "pool_cor13_p90", "pool_cor15_p90", "pool_cor16_p90"],
          ["raw", "tau=2", "tau=5", "tau=10", "tau=13", "**tau=15**", "tau=16"],
          ["%.1f"] * 7,
          "Task 1b — pooled p90 against the burst threshold tau. ms. "
          "Below 13 ms the threshold cuts into the burst-internal population "
          "and the answer still moves with it; 13-16 ms is the stable window.")

    print("\n**Task 1b(ii) — how much the pooled p90 moves across a tau window, "
          "as a percentage of the smallest value in the window.**\n")
    print("| arm | req/s | across tau = 2, 5, 10 | across tau = 13, 15, 16 |")
    print("|---|---:|---:|---:|")
    for arm in ARMS:
        for rate in RATES:
            sub = df[(df.arm == arm) & (df.rate == rate)]
            out = []
            for grp in (["pool_cor2_p90", "pool_cor5_p90", "pool_cor10_p90"],
                        ["pool_cor13_p90", "pool_cor15_p90", "pool_cor16_p90"]):
                lo = sub[grp].min(axis=1).to_numpy()
                hi = sub[grp].max(axis=1).to_numpy()
                out.append(" / ".join("%.1f%%" % x for x in 100.0 * (hi - lo) / lo))
            print("| %s | %d | %s | %s |" % (LABEL[arm], rate, out[0], out[1]))

    table(df,
          ["vio_raw_frac_gaps", "vio_cor15_frac_gaps", "vio_cor5_frac_gaps",
           "vio_raw_frac_time", "vio_cor15_frac_time", "vio_cor5_frac_time"],
          ["gaps >50 ms: raw (client)", "**tau=15 (engine)**", "tau=5",
           "stream time in those gaps: raw (client)", "**tau=15 (engine)**", "tau=5"],
          ["%.4f"] * 6,
          "Task 2 — token-level violation of the 50 ms chat budget. fractions.")

    table(df,
          ["req_raw_mean_med", "req_raw_mean_p90", "req_raw_mean_p99",
           "req_raw_p90_med", "req_raw_p90_p90",
           "req_raw_max_med", "req_raw_max_p90", "pool_raw_p90"],
          ["mean: med", "mean: p90", "mean: p99",
           "p90: med", "p90: p90", "max: med", "max: p90", "pooled p90"],
          ["%.1f"] * 8,
          "Task 3 — three collapsing rules, RAW = client side. ms. "
          "'mean/p90/max' is the per-request statistic, then its distribution "
          "across requests.")

    for tag, name in (("cor15", "CORRECTED, tau = 15 ms (headline) = engine side"),
                      ("cor5", "CORRECTED, tau = 5 ms (sensitivity)")):
        table(df,
              ["req_%s_mean_med" % tag, "req_%s_mean_p90" % tag,
               "req_%s_mean_p99" % tag, "req_%s_p90_med" % tag,
               "req_%s_p90_p90" % tag, "req_%s_max_med" % tag,
               "req_%s_max_p90" % tag, "pool_%s_p90" % tag],
              ["mean: med", "mean: p90", "mean: p99",
               "p90: med", "p90: p90", "max: med", "max: p90", "pooled p90"],
              ["%.1f"] * 8,
              "Task 3 — three collapsing rules, %s. ms." % name)

    for pop, popname in (("fix", "fixed population: requests with >= 200 gaps"),
                         ("all", "every request with at least one full window")):
        for tag, tagname in (("raw", "RAW = client side"),
                             ("cor15", "CORRECTED tau=15 = engine side"),
                             ("cor5", "CORRECTED tau=5")):
            print("\n**Task 4 — time-scale curve, %s, %s. "
                  "Median across requests of the per-request p90 of window means, ms.**\n"
                  % (tagname, popname))
            print("| arm | req/s | " + " | ".join("W=%d" % w for w in WINDOWS) + " |")
            print("|---|---:|" + "---:|" * len(WINDOWS))
            for arm in ARMS:
                for rate in (15, 35):
                    row = [cell(df, arm, rate, "curve_%s_%d_%s" % (tag, w, pop))
                           for w in WINDOWS]
                    print("| %s | %d | %s |" % (LABEL[arm], rate, " | ".join(row)))

    for tag, tagname in (("raw", "RAW = client side"),
                         ("cor15", "CORRECTED tau=15 = engine side"),
                         ("cor5", "CORRECTED tau=5")):
        print("\n**Task 4b — the same curve as a within-request p90/p50 ratio of "
              "window means, %s, fixed population. Dimensionless.**\n" % tagname)
        print("| arm | req/s | " + " | ".join("W=%d" % w for w in WINDOWS) + " |")
        print("|---|---:|" + "---:|" * len(WINDOWS))
        for arm in ARMS:
            for rate in (15, 35):
                row = [cell(df, arm, rate, "ratio_%s_%d_fix" % (tag, w), "%.3f")
                       for w in WINDOWS]
                print("| %s | %d | %s |" % (LABEL[arm], rate, " | ".join(row)))

    for tag, tagname in (("raw", "RAW = client side"),
                         ("cor15", "CORRECTED tau=15 = engine side"),
                         ("cor5", "CORRECTED tau=5")):
        for what, whatname in (("curve", "absolute p90 of window means"),
                               ("ratio", "within-request p90/p50 of window means")):
            print("\n**Task 4d — which arm is worse at each W, %s, %s, fixed "
                  "population. 'overlap' = the two repeats disagree.**\n"
                  % (tagname, whatname))
            print("| req/s | " + " | ".join("W=%d" % w for w in WINDOWS) + " |")
            print("|---:|" + "---|" * len(WINDOWS))
            for rate in (15, 35):
                cells = []
                for w in WINDOWS:
                    col = "%s_%s_%d_fix" % (what, tag, w)
                    a = df[(df.arm == "fspfx") & (df.rate == rate)][col].to_numpy()
                    b = df[(df.arm == "llmdslo") & (df.rate == rate)][col].to_numpy()
                    cells.append(verdict(a, b))
                print("| %d | %s |" % (rate, " | ".join(cells)))

    print("\n**Task 4c — W in seconds, using each arm's own pooled median gap "
          "(corrected, tau = 15 ms).**\n")
    print("| arm | req/s | median gap (ms) | " +
          " | ".join("W=%d" % w for w in WINDOWS) + " |")
    print("|---|---:|---:|" + "---:|" * len(WINDOWS))
    for arm in ARMS:
        for rate in (15, 35):
            g = df[(df.arm == arm) & (df.rate == rate)]["pool_cor15_p50"].to_numpy()
            gm = float(np.mean(g))
            row = ["%.2f" % (w * gm / 1000.0) for w in WINDOWS]
            print("| %s | %d | %s | %s |" % (
                LABEL[arm], rate, " / ".join("%.2f" % x for x in g), " | ".join(row)))

    table(df,
          ["ngaps_p10", "ngaps_p50", "ngaps_p90", "ngaps_mean",
           "gapshare_top10pct_requests", "gapshare_bottom50pct_requests"],
          ["gaps/req p10", "p50", "p90", "mean",
           "share of pooled gaps from longest 10% of requests",
           "from shortest 50%"],
          ["%.0f", "%.0f", "%.0f", "%.1f", "%.3f", "%.3f"],
          "Task 5 — how many gaps each request contributes.")

    print("\n**Task 5b — requests bucketed by number of gaps (RAW). "
          "'med mean' is the median across requests in the bucket of that "
          "request's mean gap, ms.**\n")
    heads = [("b2_50", "2-49"), ("b50_200", "50-199"),
             ("b200_500", "200-499"), ("b500_0", ">=500")]
    print("| arm | req/s | " + " | ".join(
        "%s: reqs | %s: %% of gaps | %s: med mean" % (h, h, h)
        for _, h in heads) + " |")
    print("|---|---:|" + "---:|" * (3 * len(heads)))
    for arm in ARMS:
        for rate in RATES:
            sub = df[(df.arm == arm) & (df.rate == rate)]
            cells = []
            for key, _ in heads:
                cells.append(cell(df, arm, rate, key + "_nreq", "%d"))
                sh = (sub[key + "_ngaps"] / sub["ngaps_total"] * 100).to_numpy()
                cells.append(" / ".join("%.1f" % x for x in sh))
                cells.append(cell(df, arm, rate, key + "_med_mean"))
            print("| %s | %d | %s |" % (LABEL[arm], rate, " | ".join(cells)))


if __name__ == "__main__":
    main()
