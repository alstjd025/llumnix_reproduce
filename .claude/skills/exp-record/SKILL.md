---
name: exp-record
description: Write up experiments, implementation decisions and corrections in llumnix_reproduce. Use when recording a result, a design change, a refuted hypothesis, or a mistake — and before a compaction. Covers where each kind of writing goes, the prose convention, and the correction discipline.
---

# Recording

## Where it goes

| Document | Holds |
|---|---|
| `ms_dev/notes/fluidserve-v0.1.md` | the self-contained spec: decision rules, what is measured versus configured, remaining constants, what v0.1 is **not** |
| `ms_dev/notes/fluidserve-implementation.md` | chronological account, numbered sections. **The end of the file is always the newest.** |
| `Agent_applications/.../experiments/EXP-NN_*.md` | one file per experiment: why, hypothesis, judgement rule, exact settings, result |
| `CLAUDE.md` | traps that will recur, and the resume procedure |
| auto-memory | pointers and the few facts not derivable from the repo |

Two repositories, both on `feat/fluidserve`. Commit to each separately;
`Agent_applications` is gitignored by the outer repo.

## Prose convention

**No metaphors, no idioms. Ordinary technical terms.** Longer sentences are
fine. Say **why and how** a state comes about.

- not: "the instance is held hostage", "it goes blind"
- but: "that instance's cap stays low until the request completes, so it cannot
  accept new work"

The test: could this sentence appear in the paper unchanged.

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

Examples worth matching: `implementation.md` §24.3 (a mid-path indicator taken
as a proxy for the result, three times), §26 (two claims written without
measurement), §27 (a quantity chased for days that did not exist).

## Before a compaction

Ask what a reader with no memory of this session would need:

1. the newest section of `implementation.md`, self-contained
2. the experiment file for anything running, with its judgement rule
3. `CLAUDE.md` resume procedure pointing at both
4. auto-memory updated — current state, open questions, what was refuted

State what is running, its expected finish, and which binary is deployed
(md5, and the fact that `md5sum /proc/1/exe` inside the pod is the only
authority).

## Commit messages

Long-form English, same convention as the documents. Say what changed, what it
was before, what measurement motivated it, and what would show it wrong. These
are read later as the record of why the code is shaped as it is, so a message
that only names the change is a message that will have to be re-derived.

End with:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```
