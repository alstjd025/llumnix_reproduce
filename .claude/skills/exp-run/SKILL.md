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

## 3. Launch

```bash
nohup setsid ./script.sh > <log> 2>&1 < /dev/null & disown
```

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
