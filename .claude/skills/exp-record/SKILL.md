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
| `CLAUDE.md` | **rules only** — traps that will recur, judgement rules, prose convention, the resume procedure |
| `ms_dev/notes/STATUS.md` | **state only** — what is running, which section is current, which numbers must not be cited, the per-section digest of `implementation.md` |
| auto-memory | pointers and the few facts not derivable from the repo |

Two repositories, both on `feat/fluidserve`. Commit to each separately;
`Agent_applications` is gitignored by the outer repo.

### CLAUDE.md or STATUS.md — the test is whether the next experiment changes it

**Still true after the next run → `CLAUDE.md`, in one of the trap groups A–F.
Changes when the next run finishes → `STATUS.md`.**

- "changing the workload means changing the profile too" — a rule, group A
- "candidate C was rejected on the dynamic trace at −4.4" — state
- "the same quantity written in two places gets updated in one of them" — a rule
- "do not cite the PolyServe numbers from EXP-53" — state, it lifts when EXP-57 lands

`CLAUDE.md` is loaded in full at the start of every session, so a stale sentence
there is read every time. `STATUS.md` is read on demand, so it is the safe place
for anything with a shelf life.

**After a run finishes**: update `STATUS.md` §1, add a row to §3, and add a trap
to `CLAUDE.md` only if something recurred that is not already in A–F.

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

Five sections of `implementation.md` are corrections of a mechanism written from
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

Examples worth matching: `implementation.md` §24.3 (a mid-path indicator taken
as a proxy for the result, three times), §26 (two claims written without
measurement), §27 (a quantity chased for days that did not exist).

## Before a compaction

Ask what a reader with no memory of this session would need:

1. the newest section of `implementation.md`, self-contained
2. the experiment file for anything running, with its judgement rule
3. `ms_dev/notes/STATUS.md` §1 brought up to date — it is what the resume
   procedure in `CLAUDE.md` sends the next session to read
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
