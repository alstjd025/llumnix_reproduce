# EXP-47 — does the second placement in one engine step see the first?

Written before the run, 2026-08-01 23:10 KST. This is **instrumentation, not a
policy change**: no decision is altered and there is no treatment arm.

## 1. The question and why it blocks everything else

§40.3 measured engine 8003 taking **3.2 times its own headroom** of new work in
one minute — 290 requests arriving where the headroom allowed about 90 — and the
cause has been open since. §41 measured the candidate mechanism: the scheduler
re-reads instance state every 500 ms and **50.4% of those windows carry more than
one placement to the same engine**, 6,863 prompt tokens per window committed by
the second and later ones.

§42 then found that the mechanism is not obviously broken. `onDispatch`
increments `dispatchVersion[instanceID]`, `flux()` invalidates its cached view
when that changes, and the CMS local account is incremented on dispatch and
decremented when the engine reports the request. **By design the second
placement in a step should see the first.** What §42 could not do is tell
whether it does, because the only evidence available was a 1 Hz gauge and the
quantity is per decision.

This blocks both remaining candidates. **C** makes deep research route, it
collects on an engine and that engine breaks — EXP-46 part 2 recorded 6,583
preemptions. **B** would make deep research shed on the premise that the freed
capacity reaches chat. Both are attempts to manage a capacity that is being
handed out; whether it is being handed out **twice** decides which of them is
even the right shape.

## 2. What was added

Two histograms, published in `commit`, which runs once per placement:

| metric | meaning |
|---|---|
| `scheduler_fluidserve_dispatch_ordinal_in_step` | how many placements this engine status step has carried. All ones means every placement gets a fresh view. |
| `scheduler_fluidserve_headroom_move_in_step` | for the **second and later** placement in a step, the change in the headroom the decision read. |

One mutex, one map lookup and two observations per placement, at up to about 40
placements per second. Both are in `llumnix_metrics.py`'s allowlist, without
which they are computed and never collected.

## 3. How to read it — written before the numbers exist

**`headroom_move_in_step` is the answer.**

- **Negative, by roughly the cost of one request.** The view moved between
  placements and the earlier one was accounted for. The design works and §40.3's
  over-admission has some other cause, which would have to be looked for
  elsewhere — the projection horizon and the arrival burst are the two remaining
  candidates in §40.5.
- **Zero, or a spike at zero.** The same capacity was offered twice. That is the
  defect, and it explains §40.3 directly: with 2.2 placements per window on the
  engine collecting deep research, roughly half the committed tokens are booked
  against a headroom that already belonged to something else.
- **Positive.** The view moved the wrong way — headroom grew between two
  placements in the same step, which would mean the projection is being reset by
  something other than the engine.

`dispatch_ordinal_in_step` gives the exposure: if the distribution is almost all
ones, whatever the second measurement says applies to very little traffic and
the finding is small whichever way it falls.

**No outcome accepts or rejects anything.** The result decides which of B and C
is worth building, or whether a third change is needed first.

## 4. Design

| | |
|---|---|
| arm | `fluidserve`, the shipped policy — the one §40.3 and §41 measured |
| rates | 45 and 60 req/s, static |
| repeats | 1 |
| duration | about 25 minutes |
| binary | `scheduler-exp47-probe` md5 `809b823b540629f6c0fc32adb3b87fdb` |

**Static rather than the dynamic trace**, because the question is about every
500 ms window and not about the ramp: §41 measured multiple placements per
window in the steady stretches too (minutes 8–16 and 50–56 both read 2.4–2.6 per
window on the engine collecting deep research). A static condition gives the same
distribution in a third of the time. If the answer is ambiguous, the hour is the
follow-up.

### One configuration deviation, noted rather than fixed mid-run

The start-up line reads `classharm=true`. Every FluidServe condition from
2026-07-28 to EXP-46 ran with it **false**, because a value written by one
ablation arm stuck in the deployment (§38); the fix made unset ablations fall
back to the compiled default, which for this flag is `true`, and this driver
calls the plain `fluidserve` arm, which does not pin it.

**Left as it is.** The quantity being measured is how many placements one engine
status step carries and whether the view moved between them. `classHarm` enters
`harmToIncumbents`, which orders candidates when none is feasible; it changes
*which* instance a forced request goes to, not how many placements go out per
step nor whether `flux()` rebuilt its view. EXP-43 also measured it as null on
the score at these rates.

The `fluidserve` arm of `run_exp27_mixsweep.sh` should pin the flag the way
`fsa`, `fsac` and `fsah` do. That edit waits until this run finishes — editing a
running script is what broke a condition of EXP-42.

## 5. Result

(to be filled in)
