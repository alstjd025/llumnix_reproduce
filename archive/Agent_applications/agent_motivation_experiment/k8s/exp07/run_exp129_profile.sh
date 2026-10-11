#!/usr/bin/env bash
# EXP-111 -- measure the offline profile for a newly deployed model.
#
# Three files describe a model to the control plane and all three are properties
# of the model AND the hardware, so a model switch invalidates all of them:
#
#   ttft.json        per-STEP prefill cost vs chunk size. Measured separately and
#                    already done by ms_dev/scripts/measure_ttft_sweep.py, which
#                    talks to an idle engine directly.
#   tpot.json        decode step time over (batch size, KV tokens per request).
#   fluidserve.json  the decode step law t = c0 + c_kv*M + c_n*n, the prefill
#                    curve, and the per-class output-length survival function.
#
# The last two are built from the per-step scheduler dump plus the per-request
# metrics, which is what this script produces.
#
# WHY A SPREAD OF ARRIVAL RATES. The decode law has to separate a KV term from a
# batch-size term, and at saturation those two are collinear -- EXP-16 phase 1
# ran at one saturated rate and got a variance inflation factor around 11, which
# means the fit could not tell them apart. Low rates give many low-KV steps with
# small batches; high rates give full batches at high occupancy. The same spread
# also serves the output-length survival, because the low rates are where
# requests actually finish rather than being cut off at the end of the window.
#
# WHY THE POLICY IS load-balance. FluidServe reads the very profile this script
# is measuring, so running it here would make the measurement depend on the
# tables it is producing. load-balance takes no SLO input and no profile at all.
#
# WHY THE ENGINE SCHEDULER IS `instrumented`. llumnix_sched.InstrumentedScheduler
# has ordering identical to stock vLLM (fcfs) and adds only the per-step log, so
# what is measured is the engine's physics rather than a scheduling policy.
#
#   ./run_exp111_profile.sh                       default rates
#   RATES_RPS="10 30 60" ./run_exp111_profile.sh
set -uo pipefail
cd "$(dirname "$0")"

NS=llumnix
REPO=/home/nxclab/llumnix_reproduce
HOSTWORK=$REPO/Agent_applications/agent_motivation_experiment
MODEL=${MODEL:-Qwen/Qwen2.5-72B-Instruct}
WCFG=${WCFG:-/work/workload_configs/mix_short_m1_t75.json}
RATES_RPS=${RATES_RPS:-"10 20 30 45 60"}
DURMIN=${DURMIN:-5}
TAG=${TAG:-exp111}
JOB=bench-runner-exp111

say() { echo "[exp111] $(date -u +%H:%M:%S) $*"; }

# The whole point of this script is that the fleet was just switched, so the one
# thing it must not assume is which model is loaded.
preflight() {
  local bin
  bin=$(kubectl -n $NS get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] || { say "ABORT: gateway is not the custom binary ($bin)"; return 1; }
  python3 "$REPO/ms_dev/scripts/switch_model.py" --verify | sed 's/^/[exp111]   /'
  local ip served
  ip=$(kubectl -n $NS get pod neutral-0 -o jsonpath='{.status.podIP}')
  served=$(curl -sf -m 5 "http://$ip:8000/v1/models" | python3 -c "import json,sys;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
  [ "$served" = "$MODEL" ] || { say "ABORT: engine serves '$served', this run is for '$MODEL'"; return 1; }
  say "preflight ok: engine serves $MODEL"
}

# `--policy load-balance` is set through the profiling script so that the same
# verification of the scheduler's own startup line applies here as anywhere else.
set_policy() {
  python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy load-balance \
    | sed 's/^/[exp111]   /' || { say "ABORT: could not set load-balance"; return 1; }
}

# Readiness is NOT "four ports answer". During a restart the outgoing pod answers
# on all four for as long as it takes to terminate, and a wait written that way
# returned in ten seconds against the model that was being replaced. Ask what is
# being served.
wait_engine() {
  # Which ports to require. Read from configmap/llumnix-model rather than fixed
  # at four: waiting for four ports of an eight-instance fleet returns as soon as
  # half of it is up, and the measurement then runs against a fleet that is still
  # loading the other half.
  local EPORTS
  EPORTS=$(kubectl -n $NS get cm llumnix-model -o jsonpath='{.data.ENGINE_PORTS}' 2>/dev/null | tr ',' ' ')
  [ -n "$EPORTS" ] || EPORTS="8000 8001 8002 8003"
  local i ip ok got
  for i in $(seq 1 240); do
    ip=$(kubectl -n $NS get pod neutral-0 -o jsonpath='{.status.podIP}' 2>/dev/null)
    ok=0
    if [ -n "$ip" ]; then
      ok=1
      for p in $EPORTS; do
        got=$(curl -sf -m 3 "http://$ip:$p/v1/models" 2>/dev/null \
              | python3 -c "import json,sys;print(json.load(sys.stdin)['data'][0]['id'])" 2>/dev/null)
        [ "$got" = "$MODEL" ] || ok=0
      done
    fi
    [ "$ok" = "1" ] && { say "engine serving $MODEL on all $(echo $EPORTS | wc -w) ports after $((i*15))s"; return 0; }
    sleep 15
  done
  say "ABORT: engine did not serve $MODEL within 60 minutes"; return 1
}

wait_job() {
  local deadline=$(( ${1:-60} * 6 )) i st
  for i in $(seq 1 "$deadline"); do
    st=$(kubectl -n $NS get job "$JOB" -o jsonpath='{.status.conditions[*].type}' 2>/dev/null)
    case "$st" in
      *Complete*) return 0 ;;
      *Failed*)   say "$JOB reached condition Failed"; return 1 ;;
    esac
    sleep 10
  done
  say "$JOB did not finish in time"; return 1
}

run_rate() {
  # Two statements, not one: bash expands the whole `local a=$1 b=$((a*60))`
  # line before it executes the assignments, so the arithmetic sees `rps` still
  # unset and `set -u` kills the script.
  local rps=$1
  local rpm=$((rps * 60))
  local ctr="/sched-out/sched_steps_${TAG}_r${rps}.jsonl"
  local host="/opt/llumnix-sched-out/sched_steps_${TAG}_r${rps}.jsonl"
  local session="${TAG}_r${rps}"
  say "=== rate=${rps} req/s (rpm=${rpm})"

  kubectl -n $NS exec neutral-0 -c vllm -- rm -f "$ctr" 2>/dev/null || true
  # set_engine_sched.py mutates the LIVE LeaderWorkerSet rather than applying the
  # repo yaml, which is what keeps the model switch from being undone here; its
  # own docstring records that applying the yaml would downgrade the model.
  # The exit status has to be read, and a pipe hides it: piping into grep makes
  # the pipeline's status grep's. On 2026-09-03 this call failed with
  # "resourceVersion: Invalid value: 0" -- the engine was never switched into the
  # instrumented scheduler -- and the driver carried on and ran the condition on
  # a stock engine with no step log. `set -o pipefail` plus PIPESTATUS is what
  # makes the failure stop the rate instead of decorating the log.
  set -o pipefail
  python3 set_engine_sched.py --policy instrumented --step-log "$ctr" \
    --migration off --restart 2>&1 | sed 's/^/[exp111]   /'
  local rc=${PIPESTATUS[0]}
  [ "$rc" = "0" ] || { say "ABORT rate=${rps}: set_engine_sched failed (rc=$rc)"; return 1; }
  wait_engine || return 1
  # A restart that did not happen leaves the previous engine answering, and
  # wait_engine then passes in one poll. Confirm the scheduler class the engine
  # reports is the instrumented one before any load is sent.
  local cls
  cls=$(kubectl -n $NS logs neutral-0 -c vllm --tail=6000 2>/dev/null \
        | grep -ao "scheduler_cls': '[^']*'" | sort -u | tail -1)
  case "$cls" in
    *InstrumentedScheduler*) say "engine scheduler: $cls" ;;
    *) say "ABORT rate=${rps}: engine is not instrumented (scheduler_cls=${cls:-<none>})"; return 1 ;;
  esac
  kubectl -n $NS logs neutral-0 -c vllm --tail=6000 2>/dev/null \
    | grep -ao "scheduler_cls': '[^']*'\|\[instrumented\] step log -> [^ ]*" \
    | sort -u | sed 's/^/[exp111]   /'

  kubectl -n $NS delete job "$JOB" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$JOB/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rpm/" -e "s/__DURMIN__/$DURMIN/" -e "s/__ARM__/loadbalance/" \
      -e "s#__WCFG__#$WCFG#" -e "s#__MODEL__#$MODEL#" \
      runner-exp111.template.yaml | kubectl apply -f - >/dev/null
  wait_job 60 || { kubectl -n $NS logs "job/$JOB" --tail=40; return 1; }
  kubectl -n $NS logs "job/$JOB" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -2 | sed 's/^/[exp111]   /'
  kubectl -n $NS delete job "$JOB" --ignore-not-found >/dev/null

  local rundir
  rundir=$(ls -dt "$HOSTWORK"/results/*"${session}"_rpm_* 2>/dev/null | grep -v PRERUN | head -1)
  if [ -n "$rundir" ] && [ -s "$host" ]; then
    mkdir -p "$rundir/server_metrics"
    cp "$host" "$rundir/server_metrics/sched_steps.jsonl"
    say "collected into $(basename "$rundir"): step lines=$(wc -l < "$host")"
  else
    say "WARN rate=${rps}: rundir='$rundir' steplog='$host' -- no step dump collected"
  fi
}

preflight || exit 1
set_policy || exit 1
for r in $RATES_RPS; do run_rate "$r" || say "rate=$r failed; continuing"; done
say "=== EXP-111 DONE"
say "=== collected: $(ls -d "$HOSTWORK"/results/*${TAG}_r*_rpm_* 2>/dev/null | grep -vc PRERUN) result directories"
