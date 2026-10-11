#!/usr/bin/env python3
"""Per-segment scoring for a mix-shift trace, with the separation test printed.

WHY PER SEGMENT. This trace changes its class mix and its arrival rate every
fifteen minutes, and the four segments straddle the measured static knee of
28.0 req/s: three sit below it at 24.4 to 26.8 and the last sits above it at
33.4. A whole-run mean therefore averages two regimes, and measured on EXP-104
the two cancel -- the class preference gains 2.0 and 2.9 points of offered
attainment in the two middle segments and loses 1.2 in the last one, which
comes out as +0.9 overall and is then correctly discarded as inside the repeat
spread. Both halves of that are real and the average shows neither.

WHAT IT PRINTS. For each segment and each metric, both repeats of each arm, so
the reader can see whether the two arms' values overlap. A difference whose two
arms overlap is not read, whatever its size: the arms differ by one flag and
everything else about the two runs is a repeat of each other.
"""
import argparse, sys, os
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from exp22_fluidserve import load_run, attain

# Segment boundaries in minutes from the first arrival, and what the trace
# offers in each. Taken from the trace CSV's own `segment` column.
SEGMENTS = [
    ("s0_m2", 1, 16, "24.4 req/s, chat 93%"),
    ("s1_A", 16, 31, "25.0 req/s, 33/33/33"),
    ("s2_m1", 31, 46, "26.8 req/s, chat 77%"),
    ("s3_B", 46, 61, "33.4 req/s, chat 60%  <- above the 28.0 knee"),
]
METRICS = [("adm", "admitted"), ("off", "offered"), ("rej", "reject %"),
           ("off_chat", "chat"), ("off_dr", "deepresearch"), ("off_swe", "swe")]


def classify(t):
    t = str(t)
    return "chat" if t.startswith("sg-") else ("dr" if t.startswith("sa-") else "swe")


def score(run):
    df = load_run(run)
    t0 = df.start_time.min()
    df = df.assign(min=(df.start_time - t0) / 60.0, cls=df.task_id.map(classify))
    out = {}
    for name, lo, hi, _ in SEGMENTS:
        g = df[(df["min"] >= lo) & (df["min"] < hi)]
        if len(g) < 500:
            continue
        rec = {"adm": attain(g, "violate_served"), "off": attain(g, "violate_offered"),
               "rej": 100.0 * g.is_rejected.astype(bool).mean(), "n": len(g)}
        for c, gg in g.groupby("cls"):
            rec["off_" + ("dr" if c == "dr" else c)] = attain(gg, "violate_offered")
        out[name] = rec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arm", action="append", required=True,
                    metavar="LABEL=DIR", help="repeat as many times as there are runs")
    a = ap.parse_args()
    by = {}
    for spec in a.arm:
        label, _, d = spec.partition("=")
        by.setdefault(label, []).append(score(d))
    labels = list(by)
    if len(labels) != 2:
        print(f"two arms expected, got {labels}", file=sys.stderr)

    for name, lo, hi, what in SEGMENTS:
        print(f"\n{name}  minutes {lo}-{hi}  ({what})")
        n = np.mean([r[name]["n"] for r in by[labels[0]] if name in r])
        print(f"  {n:,.0f} arrivals")
        print(f"  {'metric':>14}" + "".join(f"{l+' repeats':>22}" for l in labels)
              + f"{'diff':>8}  separated")
        for key, lab in METRICS:
            vals = {}
            for l in labels:
                v = sorted(r[name][key] for r in by[l] if name in r and key in r[name])
                vals[l] = v
            if any(not v for v in vals.values()):
                continue
            a0, b0 = vals[labels[0]], vals[labels[1]]
            sep = a0[0] > b0[-1] or b0[0] > a0[-1]
            d = np.mean(a0) - np.mean(b0)
            cells = "".join(f"{', '.join(f'{x:.1f}' for x in vals[l]):>22}" for l in labels)
            print(f"  {lab:>14}{cells}{d:>+8.1f}  {'yes' if sep else 'NO -- not read'}")
    print("\n  separated = the two arms' repeat values do not overlap. A difference "
          "whose\n  arms overlap is not read, whatever its size.")


if __name__ == "__main__":
    main()
