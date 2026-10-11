# EXP-45 — Llumnix SLO on the `full` hour, and a second repeat of the candidate-A pair

Written before the run, 2026-08-01 14:20 KST.

## 1. What is run and why in this order

Three arms on the same one-hour trace, **`slo` first**, then `fluidserve` and
`fsa`. About 3.5 hours in total, with the Llumnix SLO number available after the
first 70 minutes.

`slo` is the arm the user asked for: EXP-44 dropped it, so the dynamic trace has
no baseline in the current series. `fluidserve` and `fsa` repeat EXP-44 because
its two largest findings are one run each — the −13.7 points at the first peak
and the 1,852 → 0 in preemptions — and the first peak is a transient, which is
where run-to-run variation is largest.

**These are read alongside EXP-44's runs, not only against each other.** The
session rule was relaxed on 2026-08-01 (CLAUDE.md, "결과를 볼 때 지키는 규칙"):
what matters is whether a difference exceeds the repeat spread of the quantity
being read, not whether the arms shared a session. The measured basis for that,
on this exact trace and policy: EXP-41's `fluidserve` and EXP-44's `fluidserve`
are a day apart and read 59.7 against 59.2 offered, 84.3 against 83.7 admitted,
29.1 against 29.3 rejected, 15,643 against 15,472 goodput.

**But not every quantity travels that well.** Across those same two runs the
preemption count moved 1,471 → 1,852, 26%. Aggregate scores cross sessions;
events localised to one engine do not. Each quantity is read against its own
spread.

## 2. Design

| | |
|---|---|
| arms | `slo`, `fluidserve`, `fsa`, in that order |
| trace | `full` = `dyn60_short_m123` |
| repeats | 1 here; combined with EXP-44 this gives n=2 for the two FluidServe arms |
| engine | stock FIFO, four engines, migration off, engine admission off |
| binary | `scheduler-exp42-A` md5 `d24861df81d89257f5f16dbbb70bb295` for all three |
| `--fluidserve-class-harm` | false on both FluidServe arms, set explicitly |
| workload config | `slo` gets the `m1f` fair variant, the other two get `m1` |

**`slo` takes the fair config** because the shipped SLO filter reads the agent
class's `tbt_ms 25` literally and rejected 98% of it in EXP-28. `m1f` restates
that class as (2500, 52), inside the same 30 s end-to-end budget. Scoring is
unaffected — the analysis judges that class end to end whatever the config says.
Same treatment EXP-41 gave it.

## 3. What is being asked

**Question 1 — where does each arm stand against Llumnix SLO on a moving hour?**
EXP-41 measured FluidServe +21.2 over this trace. A did not move the whole-hour
offered score (EXP-44: 59.2 → 59.7), so both FluidServe arms should sit near
that, with A well above on the admitted denominator.

**Question 2 — is EXP-44's first-peak loss real?** EXP-44 read minutes 0–15 at
70.7 for FluidServe and 57.0 with A. A second run of similar sign and size makes
it a property of the change; a run that does not reproduce it makes EXP-44's
segment table a transient and leaves the whole-hour null unexplained.

**Question 3 — do the preemptions stay at zero with A?** Baselines so far: 1,471
(EXP-41) and 1,852 (EXP-44). A: 0. A third baseline value and a second zero
would settle it against a spread that is already known to be at least 26%.

**Pre-registered so it cannot be chosen afterwards.** The FluidServe arm to
propose is the one with the higher **offered** score over the hour, reported with
its rejection rate and token goodput. If the two are within each other's repeat
spread, the tie goes to the one with fewer preemptions, because an engine at
100% KV is a failure the request-level score does not price.

## 3a. One directory to exclude

The first launch was interrupted about three and a half minutes in and relaunched.
Both attempts wrote a directory, so **`results/260731_2200_exp45r1_slo_full` is
not part of this experiment and must be excluded from every glob.** The live run
is `results/260731_2203_exp45r1_slo_full`.

The names differ only by three minutes and by nothing else, and most of the
analysis globs here are `results/*exp45r1_{arm}_{variant}`, which matches both.
Scripts that take `sorted(hits)[-1]` pick the right one by accident; scripts that
collect every match would pool an aborted three-minute run with a full hour.
Recorded here rather than deleted, the same way EXP-40's three aborted session
directories were.

## 4. Result — the three-way is clean; candidate A does not transfer to this trace

Finished 2026-08-01 17:38 KST. All three arms healthy: 4/4 engines, 50.1 req/s
delivered, no flags.

| whole hour | offered | admitted | rej% | goodput | chat / dr / swe | preemptions |
|---|---|---|---|---|---|---|
| Llumnix SLO | 38.3 | 66.3 | 42.1 | 11,353 | 26.4 / **100.0** / 59.4 | **0** |
| FluidServe | **60.3** | 85.1 | 29.1 | **15,782** | 59.9 / 84.4 / 35.1 | **1,605** (engine 8002) |
| FluidServe + A | 56.5 | **92.7** | 39.1 | 15,280 | 52.6 / 90.8 / 46.2 | **0** |

**Both FluidServe arms beat Llumnix SLO by about 20 points offered and 35–39% on
token goodput**, reproducing EXP-41's +21.2 in a session that contains all three.

### Question 1 — the first-peak loss reproduces

| segment | FluidServe → +A, EXP-44 | EXP-45 | |
|---|---|---|---|
| 0–15 | 70.7 → 57.0 (**−13.7**) | 70.7 → 56.1 (**−14.6**) | **reproduces** |
| 15–30 | 90.2 → 95.8 (+5.6) | 90.9 → 96.1 (+5.2) | reproduces |
| 30–45 | 51.8 → 60.6 (+8.8) | 54.6 → 48.1 (**−6.5**) | **sign flips** |
| 45–60 | 31.2 → 31.2 (0.0) | 32.1 → 31.0 (−1.1) | ~0 |

**The loss at the first peak is a property of the change, not a transient.** Two
runs, −13.7 and −14.6. §"EXP-44 §4" attributed it to the margin being applied
only to the branch for classes judged on time between tokens, so the admitted
mix shifts toward swe, whose prompts are 5,557 tokens.

**The 30–45 segment is not stable** — +8.8 then −6.5. Nothing should be read
from it at n=2.

### Question 2 — preemptions stay at zero with A

| run | FluidServe | with A |
|---|---|---|
| EXP-41 | 1,471 (engine 8003) | — |
| EXP-44 | 1,852 (engine 8002) | **0** |
| EXP-45 | 1,605 (engine 8002) | **0** |

Baseline 1,471–1,852 over three runs; A reads zero twice. That is outside the
26% spread by a wide margin and is **established**. Llumnix SLO also reads zero,
for a different reason: it spreads every class evenly and never concentrates
one on an engine.

### Question 3 — which FluidServe arm to propose

The pre-registered rule was: the higher **offered** score over the hour, and if
the two are within each other's repeat spread, the tie goes to fewer preemptions.

| arm | offered, two runs | spread | mean |
|---|---|---|---|
| FluidServe | 59.2, 60.3 | 1.1 | 59.8 |
| FluidServe + A | 59.7, 56.5 | 3.2 | 58.1 |

The difference of the means is 1.7 against spreads of 1.1 and 3.2, so **they are
within each other's spread and the rule sends it to the preemption count, which
selects candidate A** — zero against roughly 1,700.

**This is a weaker case for A than EXP-42 made and it should be stated that
way.** A is clearly right on static conditions (+13.1 at 60 req/s, spread ±1.2,
two repeats). On this hour it does not raise the offered score, may lower it,
and buys two other things: admitted attainment 85.1 → 92.7 with the rejection
rate reported beside it (29.1 → 39.1%), and the engine overload removed.

### What this points at

The first-peak loss has an identified mechanism and an obvious next change:
the margin is applied to one branch and not the other. **Applying it to the
end-to-end branch as well is the single change that would test it**, and it is
the same constant and the same shape as EXP-42's.

That is not what runs next. EXP-46 (candidate C) was already prepared and
started, and it targets a different window — the minutes 50–56 loss to Llumnix
SLO, which this run measures at −1.0 for FluidServe and −0.8 for A, against
EXP-41's −2.5.
