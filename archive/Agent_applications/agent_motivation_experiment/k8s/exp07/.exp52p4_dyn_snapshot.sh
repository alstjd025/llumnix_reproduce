#!/bin/bash
# EXP-30: the dynamic trace. Rate and mix both move, on the shortened workload.
#
#   ./run_exp30_dynamic.sh smoke                 one arm, the 9-minute flat-rate
#                                                mix cycle, to prove the plumbing
#   ./run_exp30_dynamic.sh ablation [reps]       three arms x the 9-minute flat-rate
#                                                mix cycle: mix moves, rate does not
#   ./run_exp30_dynamic.sh full [reps]           three arms x the 1-hour Azure trace
#
# Why this experiment exists. Every result so far was measured at a fixed rate
# with a fixed mix, and a stationary workload cannot separate a policy that
# adapts from one that does not, because nothing has to be adapted to. Measured,
# PolyServe's partition changes once or twice in an eight-minute condition and
# every change is in the start-up transient, so on a static mix its allocation
# is simply correct and stays correct.
#
# Two traces, varying different things:
#
#   dyn09_short_mixcycle_flat   rate flat at 40 req/s, mix cycles m1-m2-m3-m1 on
#                               two-minute segments. Isolates the mix.
#   dyn60_short_m123            rate 25-75 req/s from four days of Azure minutes,
#                               mix cycles on fifteen-minute segments, one hour.
#                               Both move; this is the workload the design is for.
#
# Reading them together is the point: if the flat-rate run shows no difference
# and the hour-long one does, the effect is the rate rather than the mix, and
# the claim to make is about admission control rather than about partitioning.
#
# Arm is the inner loop and repeat the outer, as always: between-session movement
# on this workload is 0.1 points at 20 req/s and 4.6 at 80 (five PolyServe runs
# across three sessions), which is the same size as the differences under test,
# so arms measured in different sessions cannot be compared.
set -uo pipefail
cd "$(dirname "$0")"
REPO=/home/nxclab/llumnix_reproduce
HOSTWORK=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
META=../../results/exp07_meta
mkdir -p "$META"

declare -A TRACE=(
  [ablation]=/work/traces/dynamic/canonical/dyn09_short_mixcycle_flat.csv
  [full]=/work/traces/dynamic/canonical/dyn60_short_m123.csv
  [azcode]=/work/traces/dynamic/canonical/azcode_w60_m1.csv
)
# The SLO filter has no end-to-end mode and reads tbt_ms literally, so on the
# stock config it judges the agent class 2.3x tighter than FluidServe judges it
# and rejects ~98% of it (EXP-28). Each variant therefore has a "fair" config
# that restates that class as (2500, 52); scoring is unaffected because the
# analysis judges it end to end whatever the config says. Chosen by ARM, the
# same way run_exp27_mixsweep picks m1 or m1f.
declare -A WCFG=(
  [ablation]=/work/workload_configs/mix_dyn09_short_mixcycle_flat.json
  [full]=/work/workload_configs/mix_dyn60_short_m123.json
  [azcode]=/work/workload_configs/mix_azcode_w60_m1.json
)
declare -A WCFG_FAIR=(
  [full]=/work/workload_configs/mix_dyn60_short_m123_slofair.json
  [azcode]=/work/workload_configs/mix_azcode_w60_m1_slofair.json
)
SHORT_TRANSCRIPT="$HOSTWORK/results/exp10_transcript/transcript_swe_short7k_mix1500.jsonl"

check_stack() {
  local th bin mig extra
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp30] ABORT: KV admission theta=$th is on"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp30] ABORT: gateway is not the host-built binary ($bin)"; exit 1; }
  mig=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='LLUMNIX_ENABLE_MIGRATION'),'unset'))")
  [ "$mig" = "0" ] || { echo "[exp30] ABORT: engine migration is '$mig', want 0"; exit 1; }
  extra=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='SCHED_EXTRA_ARGS'),'unset'))")
  [ -z "$extra" ] || { echo "[exp30] ABORT: engine has SCHED_EXTRA_ARGS='$extra'"; exit 1; }
  [ -s "$SHORT_TRANSCRIPT" ] \
    || { echo "[exp30] ABORT: short transcript missing: $SHORT_TRANSCRIPT"; exit 1; }
  echo "[exp30] stack ok: theta off, gateway=$bin, migration off, engine stock FIFO"
}

set_arm() {
  local policy
  case "$1" in
    # EXP-44. class-harm is pinned in BOTH arms rather than left unset, because
    # set_scheduler_profiling.py now returns an unset ablation to its compiled
    # default and this flag's default is true, while every FluidServe condition
    # from EXP-27 pass 2 to EXP-43 ran with it false (implementation.md 38).
    # Without pinning, the fluidserve arm would differ from EXP-41 in two ways
    # instead of none.
    fluidserve)  policy=fluidserve; export FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false ;;
    fsa)         policy=fluidserve; export FS_FORCE_MARGIN=true  FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false ;;
    fsac)        policy=fluidserve; export FS_FORCE_MARGIN=true  FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=true ;;
    # EXP-49 candidate H2: project from the observed rate of change of KV
    # occupancy instead of from the modelled inflow/outflow balance.
    fskv)        policy=fluidserve; export FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false FS_KV_SLOPE=true ;;
    # EXP-50. Candidate C on its own -- the arriving request is judged against
    # its OWN class budget rather than against the tightest nominal budget on the
    # instance. The existing fsac arm carries candidate A as well; this one does
    # not, so the difference from `fluidserve` is one change.
    fsc)         policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=true ;;
    # EXP-52. Two points on the gate-slack axis between the shipped policy
    # (slack 1, the instance minimum binds as promised) and candidate C (slack
    # at or above the largest class-budget ratio, which for this workload is
    # 100/50 = 2, so the instance minimum never binds). At 1.111 the gate for a
    # loose-budget request on a chat-carrying instance lands on exactly chat's
    # 50 ms rather than the 45 ms margin below it; at 1.4 it lands on 63 ms.
    fsg11)       policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_GATE_SLACK=1.111 ;;
    fsg14)       policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_GATE_SLACK=1.4 ;;
    polyserve)   policy=polyserve ;;
    slo)         policy=slo ;;
    loadbalance) policy=load-balance ;;
    *) echo "[exp30] unknown arm: $1" >&2; return 1 ;;
  esac
  echo "[exp30] switching scheduler -> $policy"
  python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy "$policy" \
    | sed 's/^/[exp30]   /'
  local pod actual
  pod=$(kubectl -n llumnix get pods -l app=scheduler \
        --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
  actual=$(kubectl -n llumnix logs "$pod" 2>/dev/null \
           | grep -am1 -ao "create scheduler with policy: [a-z-]*" | awk '{print $NF}')
  [ -n "$actual" ] || { echo "[exp30] scheduler pod $pod reported no policy"; return 1; }
  [ "$actual" = "$policy" ] \
    || { echo "[exp30] ABORT: scheduler reports '$actual', wanted '$policy'"; return 1; }
  echo "[exp30] scheduler confirmed policy=$actual (pod $pod)"
}

# Wait for a Job to reach either terminal state. Returns 0 on Complete and 1 on
# Failed or on the deadline expiring, so a failed condition ends the wait instead
# of holding the chain open.
wait_job() {  # $1 job name, $2 deadline in minutes
  local job=$1 deadline=$(( $2 * 60 / 10 )) i st
  for i in $(seq 1 "$deadline"); do
    st=$(kubectl -n llumnix get job "$job" -o jsonpath='{.status.conditions[*].type}' 2>/dev/null)
    case "$st" in
      *Complete*) return 0 ;;
      *Failed*)   echo "[wait_job] $job reached condition Failed"; return 1 ;;
    esac
    sleep 10
  done
  echo "[wait_job] $job did not finish within $2 minutes"
  return 1
}

run_cell() {  # $1 arm, $2 variant (ablation|full)
  local arm=$1 variant=$2
  local trace=${TRACE[$variant]:-} wcfg=${WCFG[$variant]:-}
  if [ "$arm" = "slo" ] && [ -n "${WCFG_FAIR[$variant]:-}" ]; then
    wcfg=${WCFG_FAIR[$variant]}
  fi
  [ -n "$trace" ] || { echo "[exp30] unknown variant '$variant'"; return 1; }
  [ -s "${HOSTWORK}${trace#/work}" ] || { echo "[exp30] missing $trace"; return 1; }
  [ -s "${HOSTWORK}${wcfg#/work}" ]  || { echo "[exp30] missing $wcfg"; return 1; }
  local job="bench-runner-exp30-${arm}"
  local session="${SESSION_PREFIX:-exp30}_${arm}_${variant}"
  set_arm "$arm" || return 1
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" -e "s/__ARM__/$arm/" \
      -e "s#__TRACE__#$trace#" -e "s#__WCFG__#$wcfg#" \
      runner-dyn.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp30] $arm/$variant launched $(date -u +%H:%M:%S)"
  # Poll for EITHER terminal condition. `kubectl wait --for=condition=complete`
  # never returns on a job that FAILS -- it sits until its own timeout -- so a
  # single failed condition used to block the whole chain for five hours. EXP-48
  # rep 2 lost a night that way: engine 8002 did not come back from the cold
  # restart, the runner exited 1, and the sweep waited on a job that was already
  # in state Failed.
  wait_job "$job" 300 \
    || { echo "[exp30] $arm/$variant did not complete"
         kubectl -n llumnix logs "job/$job" --tail=60; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp30] $arm/$variant DONE $(date -u +%H:%M:%S)"
}

case "${1:-}" in
  smoke)
    check_stack
    SESSION_PREFIX=exp30smoke run_cell fluidserve ablation
    ;;
  ablation|full|azcode)
    check_stack
    VARIANT=$1
    REPS=${2:-2}
    for rep in $(seq 1 "$REPS"); do
      for arm in ${ARMS:-polyserve slo fluidserve}; do
        SESSION_PREFIX="${SESSION_PREFIX_BASE:-exp30}r${rep}" run_cell "$arm" "$VARIANT" \
          || echo "[exp30] rep$rep $arm/$VARIANT FAILED"
      done
      echo "[exp30] REPEAT $rep DONE $(date -u +%H:%M)"
    done
    echo "[exp30] $VARIANT ALL DONE $(date -u +%H:%M)"
    ;;
  *)
    sed -n '2,12p' "$0"; exit 1 ;;
esac
