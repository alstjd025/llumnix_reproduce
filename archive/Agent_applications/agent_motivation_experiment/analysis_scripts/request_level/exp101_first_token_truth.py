#!/usr/bin/env python3
"""What the decision predicted a placement's first token would cost, against
what it actually cost -- per request, from the run's own files.

WHY THIS IS NOT A SCHEDULER COUNTER. The scheduler cannot observe the quantity
it predicts. `reconcile` marks a request as having started producing tokens
when the engine's GLOBAL step counter passes `stepAtDispatch + prefillSteps`,
which is a modelled step count rather than an observation of that request, so a
request queued behind fifteen seconds of other work is recorded as having
started within a second. Measured on the EXP-101 control condition, the
scheduler's implied placement-to-first-token time is 314 ms of mean with ZERO
placements over ten seconds, while the truth below is 0.90 s of median and
16.20 s of p90 on deep research with 22.8% over its ten-second budget.

WHAT THIS JOINS. Three files every run already writes:

  server_metrics/scheduler_dispatch.log   the generic dispatch line gives the
      wall-clock instant of the placement; the FluidServe placement line added
      for EXP-101 gives what the decision predicted, the class, and how long the
      request had already waited.
  request_ids.jsonl                       maps that uuid to (task_id,
      call_index) and to the client's send time.
  metrics.csv                             gives the client's first-token
      latency for that request.

  post-dispatch first token = first_token_latency - (dispatch - send)

Rejected, errored, timed-out and run-boundary-truncated requests are dropped,
because a truncated first-token latency is a lower bound rather than a
measurement.

WHAT IT REPORTS. Per class: the two halves of the first-token time, and then
the two-by-two table of (the decision expected this to be slow) against (it was
slow) at the class's own first-token budget. The second column of that table is
what decides whether a first-token admission test can work at all: a test can
only refuse what it can see, and if the estimate is not large where the wait is
long then no threshold on it separates the two populations, whatever constant
it is given.
"""
import argparse, datetime, glob, json, os, re, sys
import numpy as np
import pandas as pd

DISPATCH = re.compile(
    r"^I(\d{2})(\d{2}) (\d{2}):(\d{2}):(\d{2})\.(\d{6})\s+\d+ \S+ "
    r"\[Schedule\] dispatch request ([0-9a-fA-F-]{8,}) to \S+ instance (\d+)")
# Go prints an infinite float as "+Inf" and a NaN as "NaN", and a FORCED
# placement onto an infeasible candidate is exactly where prefillMs is
# infinite. A pattern that only accepted digits would drop those lines in
# silence -- and they are the placements most likely to be late, so losing them
# would flatter the estimate in the table this script exists to build.
_NUM = r"[-+]?(?:\d+(?:\.\d+)?(?:[eE][-+]?\d+)?|Inf|NaN)"
PLACEMENT = re.compile(
    r"\[Schedule\] dispatch request (\S+) fsplacement tier=(\d+) waited=(-?\d+) "
    r"prefillest=(" + _NUM + r") prefillraw=(" + _NUM + r") prompt=(\d+) "
    r"decision=(\w+) inst=(\d+)")


def _f(text):
    """Go's float formatting into a Python float. +Inf and NaN are real
    outcomes here, not parse failures."""
    t = text.strip()
    if t.endswith("Inf"):
        return float("-inf") if t.startswith("-") else float("inf")
    if t.endswith("NaN"):
        return float("nan")
    return float(t)

# The first-token budget each class is judged on, in seconds. Same numbers the
# scoring rules use; stated here rather than imported because this script must
# run against a run scored under either workload config.
TTFT_BUDGET = {"chat": 5.0, "deepresearch": 10.0, "swe": 11.8}


def classify(task_id):
    t = str(task_id)
    if t.startswith("sg-"):
        return "chat"
    if t.startswith("sa-"):
        return "deepresearch"
    return "swe"


def read_dispatch(path, year):
    """uuid -> (dispatch epoch seconds, prediction dict or None)."""
    when, pred = {}, {}
    with open(path, errors="ignore") as fh:
        for line in fh:
            m = DISPATCH.match(line)
            if m:
                mo, d, h, mi, s, us, u, _inst = m.groups()
                if u not in when:
                    when[u] = datetime.datetime(
                        year, int(mo), int(d), int(h), int(mi), int(s),
                        int(us), tzinfo=datetime.timezone.utc).timestamp()
                continue
            m = PLACEMENT.search(line)
            if m:
                u, tier, waited, est, raw, prompt, decision, inst = m.groups()
                u = u.split("cmpl-")[-1]
                if u in pred:
                    continue
                pred[u] = dict(tier=int(tier), waited_ms=float(waited),
                               prefillest_ms=_f(est),
                               prefillraw_ms=_f(raw),
                               prompt=int(prompt), decision=decision,
                               inst=inst)
    return when, pred


def build(run):
    ids_path = os.path.join(run, "request_ids.jsonl")
    log_path = os.path.join(run, "server_metrics", "scheduler_dispatch.log")
    for p in (ids_path, log_path, os.path.join(run, "metrics.csv")):
        if not os.path.isfile(p):
            print(f"  missing {p}")
            return None
    ids = [json.loads(l) for l in open(ids_path, encoding="utf-8") if l.strip()]
    if not ids:
        print("  request_ids.jsonl is empty")
        return None
    year = datetime.datetime.fromtimestamp(
        ids[0]["start_time"], datetime.timezone.utc).year
    when, pred = read_dispatch(log_path, year)

    rows = []
    for r in ids:
        u = r["request_id"].split("cmpl-")[-1]
        if u not in when:
            continue
        row = dict(task_id=r["task_id"], call_index=r["call_index"],
                   wait_s=when[u] - r["start_time"])
        row.update({k: v for k, v in pred.get(u, {}).items()})
        rows.append(row)
    if not rows:
        print("  no request matched a dispatch line")
        return None
    dp = pd.DataFrame(rows)

    df = pd.read_csv(os.path.join(run, "metrics.csv"), low_memory=False)
    df = df[df.agent == "request"]
    j = df.merge(dp, on=["task_id", "call_index"], how="inner")
    # A truncated first-token latency is a lower bound, not a measurement.
    keep = ~(j.is_rejected.astype(bool) | j.is_error.astype(bool)
             | j.is_server_terminated.astype(bool) | j.is_timeout.astype(bool))
    j = j[keep].copy()
    j["cls"] = j.task_id.map(classify)
    j["post_s"] = j.first_token_latency - j.wait_s
    return j


def report(run):
    print(f"\n{os.path.basename(run)}")
    j = build(run)
    if j is None or j.empty:
        return
    have_pred = "prefillest_ms" in j.columns and j.prefillest_ms.notna().any()
    print(f"  {len(j):,} completed placements joined"
          + ("" if have_pred else
             "  -- NO prediction on the dispatch lines; this run predates the "
             "FluidServe placement line, so only the split below is available"))

    print("\n  the first-token time, split at the placement (seconds)")
    print(f"  {'class':>14}{'n':>8}{'arr>disp p50':>14}{'p90':>8}"
          f"{'disp>tok p50':>14}{'p90':>8}{'total p90':>11}{'over budget':>13}")
    for cls, g in j.groupby("cls"):
        budget = TTFT_BUDGET.get(cls, np.nan)
        over = 100.0 * (g.first_token_latency > budget).mean()
        print(f"  {cls:>14}{len(g):8d}{g.wait_s.median():14.2f}"
              f"{g.wait_s.quantile(.9):8.2f}{g.post_s.median():14.2f}"
              f"{g.post_s.quantile(.9):8.2f}"
              f"{g.first_token_latency.quantile(.9):11.2f}{over:12.1f}%")

    if not have_pred:
        return
    print("\n  did the decision expect a slow first token where it was slow")
    print(f"  {'class':>14}{'placed':>8}{'was late':>10}{'foreseen':>10}"
          f"{'seen/late':>11}{'false alarm':>13}{'pred p50':>10}{'pred p90':>10}")
    for cls, g in j.groupby("cls"):
        budget_ms = 1000.0 * TTFT_BUDGET.get(cls, np.nan)
        # The decision's own test: waited + prefillest against the budget. An
        # infinite estimate counts as "expected late", which is what the
        # decision path itself does -- missesTtftDeadline returns true outright
        # on an infinite prefillMs.
        expected_late = (g.waited_ms + g.prefillest_ms) >= budget_ms
        was_late = (1000.0 * g.first_token_latency) >= budget_ms
        tp = int((expected_late & was_late).sum())
        fp = int((expected_late & ~was_late).sum())
        fn = int((~expected_late & was_late).sum())
        late, flagged = tp + fn, tp + fp
        recall = 100.0 * tp / late if late else np.nan
        alarm = 100.0 * fp / flagged if flagged else np.nan
        print(f"  {cls:>14}{len(g):8d}{late:10d}{tp:10d}{recall:10.1f}%"
              f"{alarm:12.1f}%{g.prefillest_ms.median():10.0f}"
              f"{g.prefillest_ms.quantile(.9):10.0f}")
    print("  seen/late is how much of the late work the test could refuse at "
          "all; false alarm is what refusing on it costs.")

    # Rank correlation, which is the threshold-free version of the same
    # question: a test built on this estimate can only work if the estimate
    # ORDERS the requests the way the outcome does.
    print("\n  rank correlation between the prediction and the outcome")
    for cls, g in j.groupby("cls"):
        n_all = len(g)
        g = g[np.isfinite(g.prefillest_ms) & np.isfinite(g.post_s)]
        dropped = n_all - len(g)
        if len(g) < 100:
            continue
        rho = g.prefillest_ms.corr(g.post_s, method="spearman")
        rho_tot = (g.waited_ms + g.prefillest_ms).corr(
            1000.0 * g.first_token_latency, method="spearman")
        note = f"   [{dropped} non-finite predictions excluded]" if dropped else ""
        print(f"  {cls:>14}  prediction vs post-dispatch time {rho:+.3f}   "
              f"(waited+prediction) vs whole first-token time {rho_tot:+.3f}{note}")
    print("  Near zero means the estimate carries no ordering information about "
          "the outcome, and then no threshold on it separates the two "
          "populations whatever constant it is given.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    a = ap.parse_args()
    runs = []
    for pat in a.runs:
        hits = sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]
        if not hits:
            print(f"no run matches {pat}", file=sys.stderr)
        runs += [h for h in hits if "PRERUN" not in h]
    if not runs:
        sys.exit("no runs")
    for run in runs:
        report(run)


if __name__ == "__main__":
    main()
