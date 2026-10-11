# EXP-48 — the deep research length profile was three and a half times short

Written before the run, 2026-08-02 01:05 KST. This is **an input correction, not
a policy change**: no decision rule is edited and no flag is added. What changes
is one file the policy reads.

## 1. What was wrong

`deploy/profiling/llama31-70b-b200-tp2/fluidserve.json` states the empirical
output-length distribution of each class. For deep research it said **mean 282,
p50 256**. The workload has been producing **mean 985, p50 973** since
2026-07-29.

The profile was built by `d4e8250` (2026-07-26) from the EXP-21 metrics, and the
deep research mean measured in those runs is 277.8, so **it was correct when it
was written**. `5fa82f8` in `Agent_applications` (2026-07-29 16:35 KST) then
changed `searcharena.py`'s report structure from four sections to seven and gave
each request notes from its own conversation; that commit's own message records
the effect it measured, median output 249 → 942. The profile was not rebuilt.
Run by run the transition is one day wide: 07-27 reads 149–300, 07-28 reads
274–903, and every run from 07-29 onward reads 947–984.

## 2. Why it matters — it is the release term of the KV projection

```
outflow = Σ_live  completionProb(j_i, 100) × kvTokens_i  −  z·σ
proj    = kvLogical + nDecode×100 − outflow
```

`completionProb(j, k)` is `[S(j) − S(j+k)] / S(j)`, read off the survival curve
the profile carries. With the 07-26 curve, a deep research request that has
already produced 300 tokens is given a **0.602** chance of finishing within the
next 100 iterations. It has about 650 tokens left, so the true value is near
0.1. Averaged over a resident set spread across its own lifetime the completion
rate per 100 iterations is `100 / mean length`:

| class | 07-26 profile | measured | ratio |
|---|---|---|---|
| chat | 0.237 | 0.240 | 1.0 |
| swe | 0.192 | 0.206 | 0.9 |
| **deepresearch** | **0.355** | **0.105** | **3.4** |

and deep research holds an estimated **77% of resident KV** (arrival share ×
residency: chat 77.9%×12.6 s, dr 14.3%×72.7 s, swe 7.8%×13.9 s, times per-request
footprints of 878 / 4,851 / 6,462 logical tokens). So the term that says how much
KV is about to be released is over-predicted on the class that dominates it.

**Measured consequence, §48.2 of `fluidserve-implementation.md`:** the projection
was compared against what the engines actually held one horizon (4.6 s) later,
14,676 paired samples from `260731_2313_exp45r1_fluidserve_full`. The shipped
projection under-predicts **88.5%** of the time, mean error **−101,633** tokens,
mean absolute error 106,416 — against 29,380 for using the current occupancy and
making no projection at all. The bias grows with occupancy, from −7.1% of the
actual future value on a near-empty engine to **−22.7%** on a near-full one.

## 3. What exactly changed in the file

**`classes[]` only.** `decode_step_law`, `prefill_step_law` and
`mixed_step_validation` are byte-identical to the 07-26 document, verified by
comparison after writing. They describe the engine, were fitted on the EXP-16
per-step dumps, and rebuilding them would make this two changes instead of one.
(§34.4's separate finding that the deployed `c_kv` is about 1/1.6 of the measured
value is left alone here.)

| class | mean before → after | n (07-30 … 08-01 runs) |
|---|---|---|
| chat | 422 → **428** | 1,780,160 |
| swe | 520 → **494** | 209,282 |
| deepresearch | 282 → **985** | 410,195 |

Conditional completion probabilities over the 100-iteration horizon:

| class | cp(0,100) | cp(300,100) | cp(600,100) | cp(900,100) |
|---|---|---|---|---|
| chat | 0.114 → 0.150 | 0.258 → 0.234 | 0.423 → 0.396 | 0.374 → 0.330 |
| swe | 0.000 → 0.000 | 0.151 → 0.175 | 0.619 → 0.719 | 0.558 → 0.473 |
| **deepresearch** | 0.006 → 0.005 | **0.602 → 0.001** | **0.567 → 0.058** | 0.061 → **0.317** |

The 07-26 profile is kept beside the new one as
`fluidserve-classes-20260726.json` so a reader who sees the old numbers can find
the document they came from.

### Everything in the policy this reaches

`expectedToks` is `expectedRemaining(0)`, the class mean, and it enters the
decision path **only through the end-to-end branch**, which is swe alone:

| use | chat | deepresearch | swe |
|---|---|---|---|
| `outflow` via `completionProb` | small | **large** | small |
| `costOf` growth | none — clamped at `min(100, expectedToks)` and both values exceed 100 | none | none |
| `missesOwnBudget` end-to-end test, `deadline` | not used | not used | 520 → 494, slightly less demanding |
| `requestBudget` nominal pace for an end-to-end class | not used | not used | 30000/520 = 57.7 ms → 30000/494 = 60.7 ms |

## 4. Design

| | |
|---|---|
| arm | `fluidserve` — the shipped policy, candidates A and C both off, `FS_CLASS_HARM=false` pinned |
| part 1 | static m1, 45 and 60 req/s, 2 repeats, 8 minutes each (~55 min) |
| part 2 | `full` = `dyn60_short_m123`, 1 repeat (~70 min) |
| binary | `scheduler-exp47-probe` md5 `809b823b540629f6c0fc32adb3b87fdb`, **unchanged** |
| profile | the new `fluidserve.json`; `set_scheduler_profiling.py` recreates the ConfigMap per condition from the repository file |

**Only one profile is measured here.** The runner reinstalls the ConfigMap for
every condition from the repository file, so putting both profiles in one session
would mean editing a file on the measurement path while the sweep runs, which is
the defect that made EXP-42 lose a condition. The comparison is therefore against
recorded values from earlier sessions, which the 2026-08-01 rule allows provided
each quantity is read against its own repeat spread. The baselines available:

| quantity | old-profile `fluidserve` | n | spread |
|---|---|---|---|
| static 45 req/s offered | 99.1, 88.9 (EXP-42) | 2 | 10.2 |
| static 60 req/s offered | 35.2 (EXP-38), 36.1 (EXP-42) | 2 | 0.9 |
| static 60 req/s goodput | 12,359, 12,401, 12,638 | 3 | 279 |
| `full` whole-hour offered | 59.7 (41), 59.2 (44), 60.3 (45) | 3 | 1.1 |
| `full` preemptions | 1,471 (41), 1,852 (44), 1,605 (45) | 3 | 381 (26%) |

**Verification before the numbers are used.** The scheduler reads the profile
once at start-up (`sync.Once` in `GetLatencyPredictor`), so the ConfigMap being
right is not the same as the running process having it. After the first
condition's rollout, the mounted file inside the scheduler pod is checksummed
against the repository file. A mismatch voids the run.

## 4a. The projection is just as wrong at these static rates — added while part 1 ran

§48.2's measurement was made on the `full` hour, and part 1 is static, so the
same scoring was run on the two EXP-47 static conditions before their results
exist. It reads old runs only and touches nothing on the measurement path
(`analysis_scripts/request_level/exp48_projection_error.py`).

| run | predictor | mean error | MAE | under |
|---|---|---|---|---|
| 45 req/s | occupancy only | −634 | 34,282 | 53.6% |
| | **shipped** | **−84,826** | 91,809 | **84.3%** |
| | observed slope (H2) | +2,800 | 36,779 | 52.2% |
| 60 req/s | occupancy only | +1,730 | 35,841 | 49.7% |
| | **shipped** | **−103,411** | 112,358 | **87.0%** |
| | observed slope (H2) | +5,491 | 38,073 | 46.8% |

and the bias grows with occupancy exactly as it does on the hour: at 60 req/s,
−10.6% of the actual future value on the emptiest quarter of samples and −20.1%
on the fullest tenth.

**This is what makes part 1 a test rather than only a gate.** Static conditions
have never preempted, and the reason is visible here — occupancy tops out near
1,088k against the hour's 3,022k, so the engines never reach their memory bound.
But the admission decisions at 45 and 60 req/s are being made against a number
that is about 100,000 tokens too small in the same one-sided way, so correcting
the release term should change what gets admitted at both rates even though no
engine was in danger.

## 5. What is expected, and what would refute it

**The correction ships whatever the score does.** The deployed value states 282
for a class that measures 985; that is wrong independently of what it buys, and
leaving it in place means every later candidate is judged on an input known to be
false. What this run decides is the size and direction of the effect, and which
of the two causes in §48.4 dominates.

**Prediction.** `outflow` falls by an estimated 59%, so `proj` rises, headroom
falls, and admission tightens on the engines holding deep research. Static
conditions have never recorded a preemption on this policy, so at 45 and 60 req/s
the change should be visible only as **fewer admissions**: rejection up, offered
attainment flat or slightly down. On the hour it should be visible as **fewer
preemptions**.

1. **Part 1 is a regression gate and it stops the experiment on failure.**
   Offered at 60 req/s must not fall more than **4.2 points** below 36.1 — that
   is EXP-38's largest measured repeat spread on this workload, so a larger fall
   is a real cost. At 45 req/s the same rule cannot be applied, because the two
   recorded repeats are 99.1 and 88.9; that rate is reported and not gated.
2. **Rejection rising while offered does not rise is a failure** (§33.3). Token
   goodput is reported beside both denominators for exactly this reason.
3. **Part 2, the direct test of §48.3.** Preemptions **below 1,000** confirm that
   the over-predicted release term was the dominant cause of engines filling.
   **Above 1,400** — inside the baseline's own 1,471–1,852 range — means it was
   not, and that the missing arrival term of §48.4 is what matters, which
   promotes H2 (projecting from the engine's observed rate of change of KV
   occupancy) to the next change.
4. **The refutation of §48.3 as a mechanism at all**: preemptions unchanged *and*
   offered, rejection and goodput all unchanged at both static rates. That would
   mean `outflow` never binds on a decision, and §48.2's bias, though correct
   arithmetic, does not reach admission. In that case the next thing to measure
   is which term of `feasible` actually refuses placements at saturation, not
   another projection change.

## 6. Result

### 6.1 The mechanism is confirmed, and the size was predicted correctly

Written 2026-08-02 01:20 KST, while part 1's second rate is still running. This
compares two runs of the **same binary at the same rate** where the profile is
the only difference, so nothing else can account for it.

| 45 req/s, `shipped` projection scored against the occupancy one horizon later | 07-26 profile | corrected profile |
|---|---|---|
| run | `260801_0706_exp47r1` | `260801_0857_exp48r1` |
| mean error | −84,826 | **−24,350** |
| mean absolute error | 91,809 | **44,660** |
| under-predicting | 84.3% | **69.9%** |
| bias on the fullest fifth of samples | −19.1% of the actual value | **−5.8%** |

**71% of the bias is gone and the absolute error is halved.** §48.3 predicted the
release term would fall by about 59% and therefore leave a residual near −30,000
at this rate; the measurement is −24,350, and that number was written before the
run.

The residual is what §48.4 attributes to the missing arrival term, and it is
still one-sided — 69.9% rather than the 50% an unbiased projection would give.

**One consequence for EXP-49 that has to be said before it runs.** The
alternative projection's advantage was scored on runs with the *old* profile,
where it beat the shipped one by a factor of 3.5 in absolute error. On the
corrected profile at this rate the three predictors are much closer: shipped
44,660, observed slope 39,545, current occupancy alone 38,181. **EXP-49's premise
section quotes the old-profile numbers and overstates the remaining gap**; it is
corrected there before that experiment is launched.

### 6.2 Part 1 at 60 req/s: the correction is worth about twenty points

Repeat 1, and the gate is passed by a wide margin in the direction opposite to
the one the gate was written to catch.

| 60 req/s, arm `fluidserve` | offered | admitted | rej% | goodput | chat ITL | route% |
|---|---|---|---|---|---|---|
| EXP-38, old profile | 35.2 | — | — | 12,359 | 49.9 | — |
| EXP-42 `fsbase`, old profile | 36.1 | 55.5 | 35.0 | 12,638 | 49.7 | 1.7 |
| EXP-47 rep 1, old profile, **this binary** | 38.3 | 60.0 | 35.5 | 13,250 | 49.0 | 1.8 |
| EXP-47 rep 2, old profile, **this binary** | 36.8 | 58.6 | 36.4 | 13,024 | 49.6 | 1.7 |
| **EXP-48 rep 1, corrected profile** | **56.9** | **86.0** | **33.4** | **18,636** | **44.3** | **6.2** |

**Four measurements with the 07-26 profile read 35.2, 36.1, 38.3 and 36.8 — mean
36.6, spread 3.1. The corrected profile reads 56.9.** Token goodput rises 43%
over the same binary's own two runs, chat's median inter-token latency falls 5.0
ms, and the rejection rate does not rise, so this is not the failure mode of
§33.3 where a policy scores better only by refusing more.

Per class, offered denominator, against EXP-47's two runs on the same binary:
chat **29.9 → 55.1**, deep research **82.8 → 85.0**, swe **28.7 → 21.9**. Almost
all of it is chat.

**The binary is the same as EXP-47's** (`809b823b`), which is the comparison that
matters: EXP-42's `fsbase` used `d24861df`, and while the difference between the
two is candidate C behind a flag that is off plus the placement probe, "should
change nothing" is a weaker statement than "did not change". EXP-47's arm differs
in one other way — it ran `classharm=true` where this pins false — but EXP-42's
`fsbase` had `classharm=false` and read 36.1, so all four old-profile numbers
agree across both settings of that flag and it is not what moved.

**One repeat.** Repeat 2 is the confirmation and is running.

At 45 req/s: 91.1, against EXP-47's 88.9 and 99.6 and EXP-42's 99.1 and 88.9.
That rate has a repeat spread of over 10 points and nothing is readable there,
which is why it was not gated.

Preemptions are zero at both static rates and peak KV occupancy is 58%, as on
every static condition ever run. **The preemption question belongs to part 2.**

### 6.3 Repeat 2 was lost twice, to two different faults

The first attempt at repeat 2 produced no data. `llumnix_deploy.restart_llumnix`
cold-restarts the engine pod before each condition, and engine 8002 did not
report serving within its 1200 s deadline while the other three did — its API
server never printed `Application startup complete`. The runner exited before
generating any load, so **no result directory was written and there is nothing to
exclude.**

The failure cost time in a way worth fixing rather than only noting: the sweep
driver waited with `kubectl wait --for=condition=complete`, which **never returns
on a job that reaches condition Failed** — it sits until its own `--timeout`,
five hours here. Both drivers now poll for either terminal condition (`wait_job`)
and return non-zero on Failed, so a dead condition ends the wait instead of
holding the chain open.

**The second attempt was lost to an edit of mine, and the cause is worth stating
plainly.** While part 1 was running I added the `fskv` arm for EXP-49 to both
sweep drivers and, for the same reason the other ablations are pinned, made the
baseline arms pin `FS_KV_SLOPE=false` as well. But
`--fluidserve-kv-slope-projection` exists only in the EXP-49 binary, which is not
deployed; pflag rejects an unknown flag and the scheduler exits at start-up, so
the deployment went into CrashLoopBackOff and every condition after that failed
at the step that reads the policy from the start-up line. The driver reported it
as `scheduler pod ... reported no policy`, which is what a slow rollout also
looks like, and that is why it took a second failure to identify.

The pin is removed from the baseline arms. With the flag unset,
`set_scheduler_profiling.py` leaves it off the command line entirely, which works
with both binaries, and once the EXP-49 binary is deployed the compiled default
is `false` and the start-up line still says so positively. **No data is affected**
— both attempts died before generating load.

Two rules this run cost, and both were already in `CLAUDE.md` in weaker forms:
a flag pinned in a driver must exist in the binary that driver will run against,
and **the measurement path is not only `bin/` and `workloads/` — it is anything
the running sweep invokes**, which includes the driver scripts' arm definitions
and `set_scheduler_profiling.py`. Editing them while a sweep is in progress is
the same class of mistake as editing a running shell script, and the snapshot
pattern does not protect against it because the snapshot is taken once at the
start of the chain and the arm definitions inside it are read per condition.

### 6.4 Repeat 2 reproduces the static result to a tenth of a point

| 60 req/s | offered | admitted | rej% | goodput | chat ITL | route% |
|---|---|---|---|---|---|---|
| rep 1 | 56.9 | 86.0 | 33.4 | 18,636 | 44.3 | 6.2 |
| rep 2 | **56.8** | 87.4 | 34.5 | 18,752 | 44.1 | 5.8 |

**Spread 0.1 points**, against the four old-profile measurements at 35.2, 36.1,
38.3, 36.8. At 45 req/s the two repeats read 91.1 and 98.9, which is the same
10-point spread that rate has always had and is why it was not gated. Both
conditions pass `run_health` at 4/4 engines and 59.9 req/s delivered.

### 6.5 The hour: +10.4 points offered, and the minutes-50-56 loss is gone

| whole hour, `full` trace | offered | admitted | rej% | goodput | total tok/s | chat / dr / swe |
|---|---|---|---|---|---|---|
| EXP-41, old profile | 59.7 | 84.3 | 29.1 | 15,643 | 18,312 | 59.3 / 83.7 / 35.1 |
| EXP-44, old profile | 59.2 | 83.7 | 29.3 | 15,472 | 18,305 | 58.7 / 83.3 / 34.3 |
| EXP-45, old profile | 60.3 | 85.1 | 29.1 | 15,782 | 18,318 | 59.9 / 84.4 / 35.1 |
| **EXP-48, corrected** | **70.2** | **95.7** | **26.6** | **17,998** | 18,909 | **73.3** / 81.9 / 32.3 |

**+10.4 points offered against a repeat spread of 1.1 over three baseline runs.**
Admitted attainment of 95.7 is the highest recorded on this trace by any arm, the
rejection rate falls rather than rises, and token goodput is up 14.6%. Almost all
of the gain is chat, +14.0; deep research gives up 1.9 and swe 2.5.

By segment, and every one of the four improves:

| segment | old (EXP-45) | corrected | Llumnix SLO |
|---|---|---|---|
| 0–15 (m1) | 70.7 | **73.5** | 43.0 |
| 15–30 (m2) | 90.9 | **93.4** | 66.8 |
| 30–45 (m3) | 54.6 | **67.8** | 30.0 |
| 45–60 (m1) | 32.1 | **50.3** | 19.1 |

**Minutes 50–56 — the stretch FluidServe had lost to Llumnix SLO since EXP-41 and
which §44 spent a day decomposing — now reads 35.9 against Llumnix SLO's 17.3**,
where the old profile read 16.5. Within that window chat goes from 3.8 to 36.1.
The mechanism §44 identified was that deep research was being held and then shed
while the fleet it could not enter was inside its budget; the corrected release
term changes what the fleet looks like to the admission test, and the window
reverses. Deep research inside that window falls from 79.4 to 49.7, so this is a
trade rather than a free gain, but chat is 76.9% of the requests.

Against EXP-45's three-arm session, FluidServe's margin over Llumnix SLO on this
trace goes from **+21.2 to +31.9 points offered**.

### 6.6 Preemptions rose. That is the pre-registered outcome that promotes H2

| `full` hour | preemptions | where |
|---|---|---|
| EXP-41, old profile | 1,471 | engine 8003 |
| EXP-44, old profile | 1,852 | engine 8002 |
| EXP-45, old profile | 1,605 | engine 8002 |
| **EXP-48, corrected** | **2,776** | 8002 (2,079) and 8000 (697) |

Judgement rule 3 was written as: below 1,000 confirms §48.3 as the dominant
cause of engines filling; **above 1,400 means it was not, and that §48.4's
missing arrival term is what matters, which promotes H2.** The measurement is
2,776, above the whole baseline range, so **the rule fires for H2.**

The direction is consistent with everything else here rather than surprising.
The corrected profile makes the policy admit more work — total output rises from
18,312 to 18,909 tokens/s and the offered score by 10.4 points — so the fleet
carries more, and two engines reach 100% KV occupancy instead of one. **Removing
the over-predicted release term fixed how much the projection is wrong by;
it did not give the projection the term that accounts for the requests the
scheduler places during the horizon it is projecting over.**

## 7. Verdict

**Accepted, and it is the largest single improvement recorded on this workload.**
It is also not a policy change: no decision rule was edited and no flag added.
At a static 60 req/s the offered score goes from a four-run mean of 36.6 to 56.8
and 56.9, and on the hour from a three-run mean of 59.7 to 70.2, in both cases
with the rejection rate falling. For comparison, candidate A was worth +13.1
statically and nothing on the hour, and candidate C +23.7 statically and was
rejected on the hour.

**What it does not fix is preemption**, and the pre-registered reading of that is
recorded above: EXP-49 (candidate H2) is next, and its premise section is already
restated on these numbers rather than on the old-profile ones.

**One thing to carry forward.** Every FluidServe result from 2026-07-29 onward
was measured with a length profile that was wrong about the class holding roughly
77% of resident KV. Comparisons *within* each of those experiments stand, because
every arm shared the profile. But the absolute numbers in EXP-42 through EXP-47
describe a policy running on a false input, and candidate C in particular was
rejected for a preemption count produced partly by that input. **C is worth
re-testing on the corrected profile once H2 is settled.**

