---
name: exp-record
description: Write up experiments, implementation decisions and corrections in llumnix_reproduce. Use when recording a result, a design change, a refuted hypothesis, or a mistake — and before a compaction. Covers where each kind of writing goes, the prose convention, and the correction discipline.
---

# Recording

## Where it goes (since the 2026-10-11 reorganisation)

| Document | Holds |
|---|---|
| `project/revision.md` | **state and decisions.** Its `## 지금 상태` section is injected at session start by the hook, so it must stay short and current. Below it: the revision issue list and every decision with its reason |
| `project/system.md` | the implementation as it stands: decision path with file:line, the flag set of the configuration the paper reports, known defects. Change it in the same commit as a code change |
| `project/evidence.md` | **the one place a number is written down**: paper claim -> experiment -> runs and repeat count -> value, with what must be said beside it. Other documents point here instead of copying |
| `project/reproduce.md` | how to rebuild the environment and the last reproduction check |
| `Agent_applications/agent_motivation_experiment/experiments/EXP-NN_*.md` | one file per **new** experiment (EXP-141 onward): why, hypothesis, judgement rule, exact settings, result |
| `CLAUDE.md` | **rules only** — work rules for this phase, prose convention, judgement rules |
| `archive/` | everything before 2026-10-11, unchanged. Read-only. Open `archive/INDEX.md` first |

Two repositories, both on `revision/reorg` at the time of writing. Commit to each
separately; `Agent_applications` is gitignored by the outer repo.

### CLAUDE.md or revision.md — the test is whether the next experiment changes it

**Still true after the next run → `CLAUDE.md` (or, for an experiment-operation
trap, `.claude/skills/exp-run/traps.md`). Changes when the next run finishes →
`project/revision.md`.**

- "changing the workload means changing the profile too" — a rule
- "candidate C was rejected on the dynamic trace at −4.4" — state, and the number goes in `evidence.md`
- "the same quantity written in two places gets updated in one of them" — a rule
- "do not cite the PolyServe numbers from EXP-53" — state, it lifts when the re-measurement lands

`CLAUDE.md` is loaded in full at the start of every session, so a stale sentence
there is read every time. Keep it short.

**After a run finishes**: write the result into its experiment file, move any
number the paper or a decision depends on into `evidence.md`, and bring the
`## 지금 상태` section of `revision.md` up to date.

## Prose convention

The convention is in `CLAUDE.md` under 서술 규칙, which is always loaded — do not
restate it here, and do not write from this file's summary of it. In short: no
metaphors, ordinary technical terms used in the systems literature, nothing
abbreviated away, and the context of a number written beside the number.

## Write the judgement rule before the run

Every experiment file needs, **before** any result:

1. what is expected and on what grounds
2. **what would refute it** — a number and a direction
3. for a code change: the three conditions it must pass — justified by what the
   quantity means rather than by the number it produces, no regression at other
   rates, and holding on other mixes

Five estimator changes were each correct by definition and each failed to
improve anything. What made that legible rather than confusing was that the
refutation condition was written down first every time.

## Before writing a mechanism, read the code that produces the quantity

Five sections of the old `fluidserve-implementation.md` (now under `archive/llumnix_reproduce/ms_dev/notes/`) are corrections of a mechanism written from
the shape of a number rather than from what makes it. The pattern is always the
same: **two quantities carried the same name and were not the same quantity.**

- §32 — the client's `tbt_mean_ms` was 1/1.92 of the real inter-token latency,
  because it divided by tokens counted out of context.
- §40 — a "collapse in sharing" was a per-minute mean divided by another
  per-minute mean during a ramp.
- §42 — "the in-flight account is empty" was judged from a gauge's magnitude
  without reading the code that increments it.
- §45 — "candidate A holds preemptions at zero" treated a two-state outcome as a
  distribution and read its spread.
- §55 — "the deployed `c_kv` is 1/1.6 of measured" compared a coefficient fitted
  on decode-only steps against one fitted on samples that include prefill, and
  quoted a "deployed" value that was not in the file.

**So: open the file, read the function, and check that both sides of a
comparison were built the same way. A matching name is not evidence.**

## Correction discipline

Corrections are a large fraction of what gets recorded here, and they are the
most valuable part. Do them plainly.

- **State the wrong claim, the correct one, and how the error was possible.**
  "The 8 ms was the median of one distribution against the median of another"
  is more useful than "the 8 ms was wrong".
- **Correct in place and record the correction.** Leave the retracted number
  with a note saying why it was retracted, so a reader who saw the old figure
  can find out what happened to it.
- **When a result is withdrawn, say what the withdrawal costs.** If a headline
  claim rested on it, say so in the same paragraph.
- Do not enumerate mistakes as a list of failings and do not apologise in the
  documents. Say what is true now and how it came to be known.

Examples worth matching (in the archived `fluidserve-implementation.md`): §24.3 (a mid-path indicator taken
as a proxy for the result, three times), §26 (two claims written without
measurement), §27 (a quantity chased for days that did not exist).

## Before a compaction

Ask what a reader with no memory of this session would need:

1. the `## 지금 상태` section of `project/revision.md` brought up to date — the
   session-start hook injects exactly that section
2. the experiment file for anything running, with its judgement rule
3. any decision taken in the session written into `revision.md` with its reason

State what is running, its expected finish, and which binary is deployed
(md5, and the fact that `md5sum /proc/1/exe` inside the pod is the only
authority).

## Commit messages

Long-form English, same convention as the documents. Say what changed, what it
was before, what measurement motivated it, and what would show it wrong. These
are read later as the record of why the code is shaped as it is, so a message
that only names the change is a message that will have to be re-derived.

End with the attribution line the session's instructions give.

## Before you record a number, check it against the one table

One measurement copied by hand into several documents is how this project's most
frequent error happened (four corrections of that shape on 2026-08-06 alone).
From 2026-10-11 the rule is simple: **a number is written in `project/evidence.md`
and nowhere else in `project/`**; other documents point to its row. When a
re-measurement replaces a value, keep the old value in that row's "replaced"
column with the reason, in the same commit.

`ms_dev/data/excluded_runs.tsv` lists runs that exist and are valid but must not
enter an aggregate, with the reason; analysis scripts read it, so add a run there
rather than narrowing a glob by hand.

The previous machinery (`numbers.tsv`, `retracted.tsv`, `collect_numbers.py`,
`check_numbers.py`) is in `archive/llumnix_reproduce/ms_dev/`. Its retracted
list is still the reference for any number taken from an archived document.

Do not convert archived documents. They are append-only records; a number in
them is a fact about a date.
