---
name: exp-run
description: Launch, monitor and judge a cluster experiment in llumnix_reproduce (rate sweeps, arm comparisons, trace replays). Use whenever starting a scheduler-policy measurement, before changing any experiment setting, and when reading results. Encodes the pre-flight checks, the launch pattern, and the judgement rules that have each caught a real failure.
---

# Running an experiment

Every rule here exists because it failed once. None are precautions in the
abstract.

## 1. Before changing any setting, find out what it already is and why

The single most expensive mistake made here was changing an experiment setting
after deciding it was unfair, without first checking whether the value was the
upstream default.

`--wait-scheduling-timeout` was 5000 ms for two arms and 35000 ms for
FluidServe. That looked like an asymmetry we had introduced. One grep would
have shown 5000 ms is the gateway's own default
(`cmd/gateway/app/options/config.go`), so the asymmetry was FluidServe running a
non-default value for a stated design reason and the other arms running the
system as shipped. The "fix" made the baseline a configuration nobody deploys
and would have made one sweep incomparable with every result already recorded.

**Before touching a flag, a threshold, a mix ratio or a budget:**

```bash
# is it the upstream default?
grep -rn "<flag-name>" cmd/ pkg/ --include=*.go | grep -i "default\|flags\."
# when did it become what it is, and what did the message say?
git log -S '<value>' --oneline -- <file> | head
```

If the value is upstream's, changing it changes what the baseline *is*. That is
sometimes right, and it is never right silently.

## 2. Pre-flight

`run_exp27_mixsweep.sh check_stack` covers the first four. Verify the rest by
hand or the run is void.

| Check | How | Why |
|---|---|---|
| KV admission off | `--admission-kv-usage-threshold` is 0 | a second admission layer confounds the policy |
| host-built gateway | deploy command is `/exp07bin/gateway-exp10` | the registry image lacks our changes |
| migration off | `LLUMNIX_ENABLE_MIGRATION=0` on the lws | migration moves requests behind the policy's back |
| no engine-side scheduling args | `SCHED_EXTRA_ARGS` empty | engine admission confounds the same way |
| **binary the pod is executing** | `kubectl exec <pod> -- md5sum /proc/1/exe` | reading the spec does not catch a file that failed to install; `bin/` is hostPath-mounted so overwriting needs copy-then-rename (`text file busy`) |
| **scheduler start-up line** | full `kubectl logs <pod>` with no `--tail` | the only authority on which policy and flags the *process* got. At `-v 4` it leaves a tail window in seconds |
| **session prefix is unused** | `ls -d results/*<prefix>*` | see section 5 |

## 3. Launch — two commands, never one

```bash
# 1. the chain
nohup setsid ./script.sh > <log> 2>&1 < /dev/null & disown

# 2. the watchdog, IN THE SAME TURN, with run_in_background so its exit notifies
/home/nxclab/tools/watch_experiment.sh 'exp63_profile_sensitivity.sh' \
    /home/nxclab/tools/exp63.log 'results/*exp63*' 10
```

**A launch without a watchdog is not finished.** The watchdog returns on the
first terminal state — the chain process disappearing, a new ABORT or FAILED
line, or the expected result count — and prints the log's terminal lines, the
result count and the unfinished jobs. Because it runs in the background, its
exit arrives as a notification rather than waiting to be asked for.

This exists because §4c below was written, understood, and then not acted on.
On 2026-08-07 EXP-63 died twice and each death was found hours later:

| | how it died | how long unnoticed | why the check missed it |
|---|---|---|---|
| attempt 1 | the scheduler could not parse the scaled profile, so the rollout never completed and the runner burned its 600 s timeout | 4.5 h | nothing was watching |
| attempt 2 | the chain's own verification aborted it on a rounding difference after three good conditions | 2.6 h | the check that ran six minutes later **counted successes**, found three, and read as healthy |

**Counting successes cannot detect a stop.** Ask whether the process is alive.

`setsid` and `< /dev/null` because a plain `nohup ... &` has been killed by the
harness between turns.

**Waiting for a previous run to clear:** finished Jobs stay in k8s as `Complete`
indefinitely. A wait loop on `bench-runner-exp` matches one that finished eight
days ago and never exits — this burned 35 minutes doing nothing. Match only the
jobs the script itself creates:

```bash
while kubectl -n llumnix get jobs -o name | grep -qE "bench-runner-exp(27|30|31)"; do sleep 20; done
```

**`pkill -f <name>` kills the shell that invoked it**, because that shell's
command line contains the name. Bracket a character: `pkill -f "exp34_flux[c]ount"`.

## 4. Monitor — the failures that look healthy

Run `analysis_scripts/request_level/run_health.py 'results/*<prefix>*'` after the
first condition and at intervals. It flags what has actually gone wrong:

- **fewer than four engines.** The pod's startup probe checks port 8000 only, so
  the pod goes Ready while the other three are still loading weights. A
  condition that starts there runs on a smaller fleet and looks entirely normal
  from outside: the rate is met, the latencies are plausible. Three of fourteen
  conditions in one sweep ran short-handed, one on a single engine for eight
  minutes. `llumnix_deploy.restart_llumnix` now polls all four ports, and the
  restart log must show `all 4 engines serving=True`.
- **rate not achieved.** The condition measured a different load than its name.
- **attainment exactly 0**, or exactly 100 where the fleet should be struggling.

A port that never came up still produces a metrics file — one record per tick
with every field `None`. Any check must require two numbers, not just "did it
increase".

## 4b. Every derived number is a place to be wrong, and it has happened three times in one week

The three below are the same mistake in three costumes: **a quantity was derived
from a text table without the source table being looked at once.** Each was
caught, two of them only because the result was implausible.

| what was written | what was true | how it happened |
|---|---|---|
| "preemptions fell 2,794 → 2" | 2,609, in line with three previous runs | `awk '{s+=$2}'` on the per-engine table read `2,609` as `2` — the thousands separator terminates the number |
| "EXP-48 part 2 finished, start the next experiment" | it had run nothing; the scheduler was in CrashLoopBackOff | `grep -q "PART 2 DONE"` matched a marker printed by an attempt that failed. **Logs are append-only: match on the COUNT, or put a per-run id in the marker** |
| "every EXP-53 condition is unhealthy" | all fourteen were fine | a watcher read `run_health` columns one position off, comparing the delivered rate against the request count |

**The rule: before a derived number is reported or acted on, print the source
rows it came from and read them.** For a check that runs unattended, run it once
against data whose answer is already known — the watcher above would have been
caught in ten seconds by running it on conditions that were known good.

And **`run_health` output is not a fixed-width table you can index blind.** A
condition still running prints a short `EMPTY` row. Require the full column
count before parsing (`NF==9`).

## 4c. A watcher that greps only for success is silent through a crash

Silence then reads as "still running", which is exactly wrong. A monitor over a
long sweep has to emit on every terminal state:

1. a condition that failed to start its engines
2. a condition that finished but not on 4/4 engines, or that `run_health` flagged
3. a delivered rate far from the target — load never applied, but the condition
   looks complete
4. **the chain script itself disappearing without its DONE marker**

**And a check that stops the run must not be more fragile than what it checks.**
Attempt 2 above was aborted by its own guard: the guard computed the wanted
profile mean from a value already rounded to one decimal (984.8 x 1.5 = 1477.2)
while the generator used the full 984.8457... (= 1477.3). The setting was exactly
right and the run was stopped anyway. A false abort costs what a missed error
costs. Compare ratios or use a tolerance, never two independently rounded
numbers, and run the guard against a case whose answer is known before arming it.

`kubectl wait --for=condition=complete` never returns on a job that FAILS; it
sits until its own `--timeout`, which was 300m here and nearly held the cluster
idle for five hours on an already-dead job. Poll for **either** terminal
condition (`wait_job` in the sweep drivers).

## 4e. An arm that rejects nothing can break the load generator instead of the system

Reported failures are attributed to the policy by default, and that default is
wrong for any arm with no admission control. In EXP-54 the Llumnix load-balance
arm saturated its four engines at minute 40; queued streams were then cut, and
because the client's server-termination keyword list does not contain
`cannot assign requested address`, each cut fell through to the generic branch
and **opened one more connection for a non-streaming retry**. That exhausted the
roughly 28,000 ephemeral ports (a closed port is held 60 s in `TIME_WAIT`), after
which every attempt failed in microseconds and the recorded attempt rate read
**170/s against a trace that offered 50** — the client spinning, not the load.
62,114 such calls; the other three arms had zero.

**On any arm whose rejection rate is 0, check two things before reading its
numbers:**

```python
df['error_msg'].value_counts()          # by kind, not just is_error.sum()
df.groupby(df.start_time//300).size()   # calls started per window
```

If the started-call rate exceeds what the trace offers, that window measures the
client. Errors concentrated in the last segments with none earlier is the
signature.

**Rejections are not retried**, so do not reach for that explanation: the
handler branches on rejection before the fallback, and the HTTPAdapter is
mounted `max_retries=0`. The string "Max retries exceeded" inside a
`ConnectionError` is requests' standard wording for a failed connection, not
evidence of a retry.

## 4d. One failed condition should not take the rest of its arm with it

The sweep drivers pass a whole rate list to one runner Job, and the runner exits
on the first condition whose engines do not come up. In EXP-53 that cost two arms
their top two rates — six conditions ran, the seventh failed, the eighth never
started. Engine restarts fail often enough to plan around: **four times in one
week**, always the same way (one of four ports never reports serving inside
1200 s).

For a sweep long enough that losing an arm matters, either run one rate per Job
or plan a top-up pass that fills only the (arm, rate) cells short of their
repeats. Do not re-run whole arms to recover two cells.

## 5. Never reuse a session prefix across a restart

Stopping a sweep and relaunching with the same `SESSION_PREFIX` leaves two sets
of directories with the same name and different settings, distinguishable only
by timestamp. It has happened twice. Bump the prefix, and if it has already
happened, record the exact directories to exclude in the experiment file
immediately — not at analysis time.

**Cleaning up an interrupted run's directory needs the runner pod.** The runner
chowns its output to the host user on its last line, so a run killed before that
leaves root-owned files that the host cannot delete:

```bash
POD=$(kubectl -n llumnix get pods -o name | grep bench-runner | head -1 | cut -d/ -f2)
kubectl -n llumnix exec "$POD" -- rm -rf /work/results/<dir>
```

## 6. Judging results

- **Never judge from one measurement per condition.** Within-session spread on
  this workload is 1.4–3.3 points and reaches 6 at 80 req/s; between sessions it
  reaches 4.6. A difference smaller than the spread is "no difference".
- **Arms must be in the same session**, repeat as the outer loop. Cross-session
  arm comparison is not a comparison.
- **Fix which statistic before comparing.** An eight-millisecond "bias" was
  chased for three days; it was the median of one distribution against the
  median of another, and on the means the same data said the opposite sign. If
  the distribution is bimodal — iteration times here are — mean and median
  disagree by 19 ms and give opposite answers.
- **Both denominators, always.** Admitted (rejections excluded) beside offered
  (rejections are violations), plus the rejection rate and token goodput. A
  policy that rejects everything scores 100% on admitted alone.
- **Four numbers, not one.** Request-level metrics miss preemptions (549 in
  eight minutes once, about 27% of two engines' time) and per-engine prefix hit
  rate, which is the engine reporting directly whether routing separated the
  classes.
- **A mid-path indicator is not the result.** Pre-registering "holds will fall"
  and then seeing results improve while holds did not has happened three times.
  Write the refutation condition before the run, and treat mid-path numbers as
  refutation only.

## 6b. Cross-check a client-side metric against what the server reports

A metric the load generator computes is a second implementation of a quantity
the system already measures, and the two can disagree without anything erroring.

`tbt_mean_ms` was recorded at 1/1.92 of the true inter-token time for every run
up to 2026-07-30, because the client divided each inter-chunk gap by a per-chunk
token estimate obtained by tokenising the chunk out of context, which
roughly doubles the count. The scheduler's `observed_step_ms` (50.5 ms) and its
model's `predicted_step_ms` (50.2 ms) agreed with each other and with the true
per-token time; only the client number (26.1 ms) was wrong, and it was the one
the SLO rule was applied to. Every attainment figure on record was judged
against roughly twice its intended per-token budget.

Before trusting a client-side latency, reconcile it three ways: the client's
own raw events (`tbt_events.jsonl` has per-chunk arrival offsets), the server's
gauge for the same thing, and a derivation from independent columns
(`(e2e - ttft) / (tokens - 1)`). If they disagree by a clean constant factor,
look for a count on one side of a division.

**Do not fix the collection code while a sweep is running.** `/work` is a
hostPath mount of the experiment repository and each condition starts a Job that
re-reads the source, so editing `workloads/` mid-sweep measures different
conditions with different code — the same rule as `bin/`. `analysis_scripts/` is
not on the measurement path and is safe to change, which is also the better
place for a scoring fix because it applies to every run already recorded.

## 7. Cost discipline

A counter computed inside the run beats an A/B arm when the effect is smaller
than the spread or when the change feeds back on itself. Measuring how often the
KV projection would have changed a decision took twenty minutes as a shadow
calculation; as an ablation arm it would have taken two hours and landed inside
a six-point spread, with the correction loop absorbing part of the effect.
