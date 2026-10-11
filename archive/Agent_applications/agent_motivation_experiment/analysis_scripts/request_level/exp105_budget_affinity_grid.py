#!/usr/bin/env python3
"""The swe end-to-end budget crossed with the class preference, per segment and
per class.

WHY THIS SHAPE. Two things changed between EXP-104 and EXP-105 and they are not
independent: the class preference decides which instances a class lands on, and
the swe budget decides how far that class's per-token pace sits from chat's. A
2x2 is the only layout in which the second can be read at all, because "the
preference is worth X" is a different number under each budget.

WHY PER SEGMENT. The trace changes its mix and its arrival rate every fifteen
minutes and the four segments straddle the measured static knee of 28.0 req/s:
three sit below it and the last sits above. A whole-run mean averages two
regimes and shows neither.

WHY BOTH DENOMINATORS. A rejection leaves the admitted denominator, so admitted
attainment alone rewards a policy for refusing the requests it was going to
miss. Offered counts every arrival and scores a rejection as a violation. The
rejection rate and the token goodput are printed beside them because neither
denominator is interpretable without knowing how much work was turned away.

THE SCORING RULE IS PART OF THE ARM. A run whose policy was given a 40 s
end-to-end budget for the agent class is scored against 40 s, because that is
what the system promised. The consequence is that the swe rows cannot be
compared ACROSS the two budget blocks -- a wider budget is an easier exam as
well as a different policy. What is comparable across blocks is the preference's
ON-minus-OFF difference, which is measured within one budget and therefore
within one scoring rule. The `--common-yardstick` block re-scores the 40 s runs
at 30 s as well, which separates "the policy got better" from "the exam got
easier".
"""
import argparse, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import exp22_fluidserve as fs
from exp22_fluidserve import attain, goodput_tokens, total_tokens

SEGMENTS = [
    ("s0", 1, 16, "24.4 req/s, chat 93%"),
    ("s1", 16, 31, "25.0 req/s, 33/33/33 (even mix)"),
    ("s2", 31, 46, "26.8 req/s, chat 77%"),
    ("s3", 46, 61, "33.4 req/s, chat 60%  <- the only segment above the 28.0 knee"),
]
CLASSES = ["chat", "deepresearch", "swe", "ALL"]
METRICS = [
    ("arrivals", "arrivals", "{:,.0f}"),
    ("reject", "reject %", "{:.1f}"),
    ("off", "attain offered", "{:.1f}"),
    ("adm", "attain admitted", "{:.1f}"),
    ("good", "goodput tok/s", "{:,.0f}"),
    ("out", "output tok/s", "{:,.0f}"),
]


def classify(t):
    t = str(t)
    return "chat" if t.startswith("sg-") else ("deepresearch" if t.startswith("sa-") else "swe")


def score_run(run_dir, swe_e2e_s):
    """One run, scored against the end-to-end budget its policy was given."""
    fs.SLO_RULES["swe"] = {"e2e": float(swe_e2e_s)}
    df = fs.load_run(run_dir)
    t0 = df.start_time.min()
    df = df.assign(minute=(df.start_time - t0) / 60.0, cls=df.task_id.map(classify))
    out = {}
    for name, lo, hi, _ in SEGMENTS:
        seg = df[(df["minute"] >= lo) & (df["minute"] < hi)]
        window = (hi - lo) * 60.0
        for cls in CLASSES:
            g = seg if cls == "ALL" else seg[seg["cls"] == cls]
            if not len(g):
                continue
            out[(name, cls)] = {
                "arrivals": float(len(g)),
                "reject": 100.0 * g.rejected.astype(bool).mean(),
                "off": attain(g, "violate_offered"),
                "adm": attain(g, "violate_served"),
                "good": goodput_tokens(g, window),
                "out": total_tokens(g, window),
            }
    return out


def cell(values, fmt):
    """Every repeat, joined -- not a mean. With one repeat per arm the spread is
    unknown, and printing a single number as if it were an estimate is how a
    difference smaller than the repeat spread gets read as a result."""
    vals = [v for v in values if v is not None and not (isinstance(v, float) and np.isnan(v))]
    if not vals:
        return "-"
    return " / ".join(fmt.format(v) for v in vals)


def table(scored, order, title):
    print(f"\n{'=' * 100}\n{title}\n{'=' * 100}")
    width = max(18, max(len(a) for a in order) + 2)
    for name, lo, hi, what in SEGMENTS:
        print(f"\n--- {name}  minutes {lo}-{hi}  ({what})")
        head = f"{'class':<14}{'metric':<17}" + "".join(f"{a:>{width}}" for a in order)
        print(head)
        for cls in CLASSES:
            first = True
            for key, label, fmt in METRICS:
                row = f"{cls if first else '':<14}{label:<17}"
                first = False
                any_val = False
                for arm in order:
                    vals = [r.get((name, cls), {}).get(key) for r in scored[arm]]
                    txt = cell(vals, fmt)
                    any_val = any_val or txt != "-"
                    row += f"{txt:>{width}}"
                if any_val:
                    print(row)
            print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True,
                    metavar="LABEL:SWE_E2E_S=DIR",
                    help="repeat once per run; the budget is the one the POLICY was given")
    ap.add_argument("--common-yardstick", type=float, default=None,
                    help="also re-score every run against this one budget, so that "
                         "'the policy got better' and 'the exam got easier' can be "
                         "told apart")
    a = ap.parse_args()

    arms, order, budget = {}, [], {}
    for spec in a.arm:
        head, _, d = spec.partition("=")
        label, _, secs = head.partition(":")
        if not secs:
            sys.exit(f"{spec}: give the budget as LABEL:SECONDS=DIR, because a run "
                     f"scored against the wrong one looks completely normal")
        if label not in arms:
            arms[label], order = [], order + [label]
        budget[label] = float(secs)
        arms[label].append(d)

    scored = {lab: [score_run(d, budget[lab]) for d in dirs] for lab, dirs in arms.items()}
    table(scored, order,
          "Each arm scored against the end-to-end budget its own policy was given\n"
          + "  ".join(f"{lab}: swe e2e {budget[lab]:.0f}s, n={len(arms[lab])}" for lab in order))

    if a.common_yardstick:
        y = a.common_yardstick
        scored2 = {lab: [score_run(d, y) for d in dirs] for lab, dirs in arms.items()}
        table(scored2, order,
              f"The same runs, every one re-scored at {y:.0f}s\n"
              f"  The policy still ran with the budget above; only the exam is held "
              f"fixed here, so a 40 s arm judged at 30 s is being asked to meet a "
              f"deadline it was never told about.")

    print("\nnotes")
    print("  - every repeat is printed, separated by ' / '. A difference smaller than")
    print("    an arm's own spread between its repeats is not a result.")
    print("  - goodput counts a request's whole output only if it met its rule.")
    print("  - reject % and the offered denominator are the pair to read together:")
    print("    admitted attainment alone rewards refusing what would have missed.")


if __name__ == "__main__":
    main()
