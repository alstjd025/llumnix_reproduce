#!/usr/bin/env python3
"""Find numbers in the notes that should not be there any more.

WHY THIS EXISTS. One measurement gets copied by hand into several documents, a
re-measurement changes it, and the old value survives somewhere. On 2026-08-06
four corrections of exactly that shape were made in one day: the saturation
points disagreed between two documents, PolyServe's one-hour attainment was the
pre-refit 17.1 in two places, the one-hour ablation gain appeared as +2.1 in one
section and -1.8 in another, and paper-outline.md still carried the pre-refit
PolyServe row that motivation.md had already replaced.

Two checks, both deliberately narrow. A general "compare every number against a
table" pass produces mostly false positives, because prose is full of numbers
that are not measurements.

  1. RETRACTED VALUES. ms_dev/notes/retracted.tsv lists values that a
     re-measurement replaced. Any of them appearing in a document is reported.
     This is the mechanical form of the "인용하면 안 되는 수치" section that
     CLAUDE.md and STATUS.md were maintaining by hand.

  2. CITATIONS. Text may write {{key}} where key names a row of the numbers
     table (numbers.tsv, produced by collect_numbers.py). The checker renders it
     and reports a key that does not exist. Where the table says a value is a
     mean over more than one repeat, a citation that does not also state the
     range is reported -- that is this project's own rule, written in three
     places and broken again on 2026-08-06.

Existing documents are NOT rewritten to use citations. fluidserve-implementation.md
is an append-only record and its numbers are historical facts tied to a moment;
turning them into live citations would make them wrong. Only new or edited text
uses {{...}}.

  python3 ms_dev/scripts/check_numbers.py                # check the notes
  python3 ms_dev/scripts/check_numbers.py --render FILE  # substitute citations
"""
import argparse
import glob
import os
import re
import sys

REPO = "/home/nxclab/llumnix_reproduce"
RETRACTED = os.path.join(REPO, "ms_dev/notes/retracted.tsv")
NUMBERS = os.path.join(REPO, "ms_dev/notes/numbers.tsv")
# Only the documents that make CLAIMS, not the ones that record history.
#
# The experiment files and fluidserve-implementation.md are append-only records:
# a number in them is a fact about what was measured on a date, and replacing it
# would make the record wrong. The first run of this checker reported 42
# problems and nearly all of them were of that kind, including lines whose whole
# purpose is to show the correction ("27.5 -> 35.6"). Checking them trains the
# reader to ignore the output, which is worse than not checking.
DEFAULT_GLOBS = [
    "ms_dev/notes/motivation.md",
    # The second motivation draft restates most of the first one's numbers in a
    # different order, which is exactly the shape of copy that goes stale when a
    # re-measurement lands. It has to be checked too.
    "ms_dev/notes/motivation_v2.md",
    "ms_dev/notes/motivation_v3.md",
    "ms_dev/notes/paper-outline.md",
    "ms_dev/notes/STATUS.md",
    "ms_dev/notes/fluidserve-how-it-works.md",
    "ms_dev/notes/fluidserve-v0.1.md",
    "ms_dev/notes/fluidserve-v0.1.1.md",
    "ms_dev/notes/fluidserve-v0.1.2.md",
    "ms_dev/notes/fluidserve-v0.2.md",
    "ms_dev/notes/polyserve-fidelity.md",
    "ms_dev/notes/slosserve-comparison.md",
    "ms_dev/notes/why-the-routing-layer.md",
    "CLAUDE.md",
    # The figure README is a claim-making document: it states what each paper
    # figure shows, and a figure drawn from superseded data is exactly the error
    # this checker exists to find. Adding it on 2026-08-07 immediately reported
    # that motivation_throughput_vs_goodput.png carries the pre-refit PolyServe
    # row (16,363 / 3,509 / 14.4 at 70 req/s).
    "Agent_applications/agent_motivation_experiment/results/aggregate_analysis/"
    "motivation/README.md",
]
# Lines that are allowed to hold a retracted value: the ones whose job is to say
# it is retracted.
# A line whose job is to say the value is retracted, or to show the correction
# itself, keeps the old number on purpose.
EXEMPT = re.compile(r"인용하면 안|retracted|수정 전|프로파일 수정|pre-refit|"
                    r"정정|철회|이전 값|옛 값|EXP-5[34] 이전|낡은|→|->|EXP-57 이전")
# A retracted number can collide with an unrelated quantity that happens to have
# the same digits: "8분 27.5%" is deepresearch's attainment over an eight-minute
# window and has nothing to do with PolyServe's 27.5 at 45 req/s. Suppress those
# individually and say why, rather than loosening the pattern above, which would
# also stop catching the real ones.
SUPPRESS = re.compile(r"numbers-ok:")


def load_retracted():
    rows = []
    if not os.path.exists(RETRACTED):
        return rows
    for line in open(RETRACTED):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        rows.append((parts[0].strip(), parts[1].strip(), parts[2].strip()))
    return rows


def load_numbers():
    table = {}
    if not os.path.exists(NUMBERS):
        return table
    for line in open(NUMBERS):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        f = line.split("\t")
        if len(f) < 5 or f[0] == "key":
            continue
        table[f[0]] = dict(value=f[1], n=int(f[2]), lo=f[3], hi=f[4],
                           note=f[5] if len(f) > 5 else "")
    return table


def check_retracted(paths, rows):
    bad = 0
    for p in paths:
        try:
            lines = open(p, encoding="utf-8").read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        # A warning line exempts the table that follows it, not only itself.
        # The natural way to mark a stale column is a blockquote above the
        # table; requiring the marker on every row would make the table
        # unreadable, and putting it only on the header would leave the rows
        # reported.
        block = False
        for i, line in enumerate(lines, 1):
            if SUPPRESS.search(line):
                continue
            if EXEMPT.search(line):
                block = True
                continue
            if block:
                if not line.strip() or line.lstrip().startswith(("|", ">")):
                    continue
                block = False
            for old, what, new in rows:
                # Word-ish boundary so 26.7 does not match 126.75.
                if re.search(r"(?<![\d.,])" + re.escape(old) + r"(?![\d.,])", line):
                    rel = os.path.relpath(p, REPO)
                    print(f"  {rel}:{i}: retracted value {old} ({what}) -> use {new}")
                    print(f"      {line.strip()[:110]}")
                    bad += 1
    return bad


CITE = re.compile(r"\{\{([A-Za-z0-9_.\-]+)\}\}")
# A citation is "accompanied by its range" if the same line mentions two more
# numbers or the words for a range. Crude on purpose: the point is to make the
# author look, not to parse the sentence.
HAS_RANGE = re.compile(r"~|–|—|\bto\b|반복|spread|min|max|±")


def check_citations(paths, table):
    bad = 0
    for p in paths:
        try:
            lines = open(p, encoding="utf-8").read().splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for i, line in enumerate(lines, 1):
            for key in CITE.findall(line):
                rel = os.path.relpath(p, REPO)
                if key not in table:
                    print(f"  {rel}:{i}: unknown key {{{{{key}}}}}")
                    bad += 1
                    continue
                row = table[key]
                if row["n"] > 1 and not HAS_RANGE.search(line):
                    print(f"  {rel}:{i}: {{{{{key}}}}} is a mean over {row['n']} "
                          f"repeats ({row['lo']}..{row['hi']}) and the line does "
                          f"not state the range")
                    bad += 1
    return bad


def render(path, table):
    text = open(path, encoding="utf-8").read()
    missing = []

    def sub(m):
        k = m.group(1)
        if k not in table:
            missing.append(k)
            return m.group(0)
        return table[k]["value"]
    out = CITE.sub(sub, text)
    sys.stdout.write(out)
    for k in missing:
        print(f"# unknown key: {k}", file=sys.stderr)
    return 1 if missing else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--render", default="")
    ap.add_argument("paths", nargs="*")
    a = ap.parse_args()

    table = load_numbers()
    if a.render:
        sys.exit(render(a.render, table))

    paths = a.paths
    if not paths:
        for g in DEFAULT_GLOBS:
            paths += sorted(glob.glob(os.path.join(REPO, g)))
    rows = load_retracted()
    print(f"checking {len(paths)} files against {len(rows)} retracted values "
          f"and {len(table)} keys")
    bad = check_retracted(paths, rows)
    bad += check_citations(paths, table)
    if bad:
        print(f"\n{bad} problem(s).")
        print("A retracted value on a line that is ABOUT the retraction is fine —"
              " add a word from the exempt list to that line, or fix the number.")
    else:
        print("clean")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
