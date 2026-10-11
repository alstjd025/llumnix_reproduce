#!/bin/bash
# EXP-22: FluidServe vs PolyServe routing.
#
#   ./run_exp22_fluidserve.sh smoke [rpm]        # one short fixed-rate condition
#   ./run_exp22_fluidserve.sh sweep [rates] [min] # fixed-rate sweep, both arms
#   ./run_exp22_fluidserve.sh dyn                # the 1-hour dynamic trace, both arms
#   ./run_exp22_fluidserve.sh arm fluidserve dyn # a single arm
#
#   FS_PEND=false ./run_exp22_fluidserve.sh smoke 1800   # ablation: no holding
#
# Ablation switches pass through to the scheduler (FS_PEND, FS_EXTERNALITY,
# FS_FLUX, FS_ALPHA, FS_HORIZON, FS_Z). With holding off FluidServe is pure
# routing, which is what PolyServe is too, so that arm is the like-for-like
# comparison of the two routing rules.
#
# Only the SCHEDULER POLICY differs between arms. Same workload, same trace, the
# same cold engine restart per condition, and the client sends the packed
# per-request SLO in both arms.
#
#   polyserve   tier affinity + the section 4.5-4.7 admission test + least-load
#   fluidserve  headroom over a planning horizon; holds a request at the gateway
#               when no instance can take it within budget
#
# One configuration difference is deliberate and is recorded here because it is
# not a free variable: FluidServe expresses "no instance can take this yet" by
# returning no endpoint, and the gateway's hold-and-retry loop is what carries
# the request until one can. Its retry interval is therefore FluidServe's
# re-decision period, and the stock 1000 ms is far coarser than the timescale an
# instance's state changes on. set_scheduler_profiling.py sets 100 ms with a
# 12 s ceiling for the fluidserve arm and restores 1000 ms / 5 s for every other
# policy, so the polyserve arm runs exactly as it did in EXP-21.
#
# The engine is stock FIFO in both arms (no --scheduler-cls, migration off), as
# in EXP-21, so this isolates the routing policy.
set -uo pipefail
cd "$(dirname "$0")"
REPO=/home/nxclab/llumnix_reproduce
META=../../results/exp07_meta
mkdir -p "$META"

TRACE="${TRACE:-/work/traces/dynamic/canonical/dyn60_azure4d.csv}"
WCFG="${WCFG:-/work/workload_configs/mix_dyn60.json}"
SMOKE_TRACE="${SMOKE_TRACE:-/work/traces/dynamic/canonical/dyn06_azure4d_smoke.csv}"
SMOKE_WCFG="${SMOKE_WCFG:-/work/workload_configs/mix_dyn06_smoke.json}"
HOSTWORK=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment

check_stack() {
  local th bin mig extra
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp22] ABORT: KV admission theta=$th is on; it rejects requests on its"
    echo "        own and would be indistinguishable from the policy's decisions"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp22] ABORT: gateway is not the host-built binary ($bin)"; exit 1; }
  mig=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='LLUMNIX_ENABLE_MIGRATION'),'unset'))")
  [ "$mig" = "0" ] || { echo "[exp22] ABORT: engine migration is '$mig', want 0"; exit 1; }
  extra=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='SCHED_EXTRA_ARGS'),'unset'))")
  [ -z "$extra" ] || { echo "[exp22] ABORT: engine has SCHED_EXTRA_ARGS='$extra', want stock FIFO"; exit 1; }
  echo "[exp22] stack ok: theta off, gateway=$bin, migration off, engine stock FIFO"
}

set_arm() {  # $1 = fluidserve | polyserve | loadbalance
  local policy
  case "$1" in
      # FS_PREFIX=false is explicit and must stay that way. The compiled
      # default became true at v0.2, and every results directory named
      # *_fluidserve_* that already exists was produced with it OFF. Leaving
      # this arm on the compiled default would make one arm name mean two
      # configurations depending on when it ran, which is the failure this
      # repository keeps hitting. The arm that exercises the shipped default is
      # `fspfx`, and it sets FS_PREFIX=true explicitly for the same reason.
    fluidserve)  policy=fluidserve; export FS_PREFIX=false ;;
    polyserve)   policy=polyserve ;;
    loadbalance) policy=load-balance ;;
    *) echo "[exp22] unknown arm: $1" >&2; return 1 ;;
  esac
  echo "[exp22] switching scheduler -> $policy"
  python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy "$policy" \
    | sed 's/^/[exp22]   /'
  # Read the policy back out of the running process rather than the applied
  # spec: an apply that did not take effect would otherwise run a whole arm
  # under the wrong policy and look like a result.
  local pod actual
  pod=$(kubectl -n llumnix get pods -l app=scheduler \
        --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
  # Read from the START of the container log, not the tail: the scheduler runs
  # at -v 4 and emits hundreds of lines a second, so the start-up line is out of
  # any tail window within seconds of the pod becoming ready.
  actual=$(kubectl -n llumnix logs "$pod" 2>/dev/null \
           | grep -am1 -ao "create scheduler with policy: [a-z-]*" | awk '{print $NF}')
  if [ -z "$actual" ]; then
    echo "[exp22] scheduler pod $pod did not report a policy; last lines:"
    kubectl -n llumnix logs "$pod" --tail=15 2>/dev/null | sed 's/^/[exp22]   /'
    return 1
  fi
  [ "$actual" = "$policy" ] \
    || { echo "[exp22] ABORT: scheduler reports policy '$actual', wanted '$policy'"; return 1; }
  # The retry cadence is part of the arm, so confirm it too.
  local iv
  iv=$(kubectl -n llumnix get deploy gateway -o json \
       | python3 -c "import json,sys;a=json.load(sys.stdin)['spec']['template']['spec']['containers'][0].get('args',[]);print(a[a.index('--wait-scheduling-retry-interval')+1] if '--wait-scheduling-retry-interval' in a else 'default')")
  echo "[exp22] scheduler confirmed policy=$actual, gateway retry interval=$iv (pod $pod)"
}

run_trace_arm() {  # $1 arm, $2 trace, $3 wcfg, $4 timeout
  local arm=$1 trace=$2 wcfg=$3 timeout=$4
  local tag; tag=$(basename "${trace%.csv}")
  local job="bench-runner-exp22-${arm}" session="exp22_${arm}_${tag}"
  set_arm "$arm" || return 1
  # The workload reads the class column of the same file the runner replays; a
  # mismatch pairs classes with the wrong arrivals and produces a different mix
  # than intended, without any error. The runner checks this too, but checking
  # here costs nothing and fails before an hour of cluster time is spent.
  local planned
  planned=$(python3 -c "import json;print(json.load(open('${HOSTWORK}${wcfg#/work}'))['class_plan_file'])")
  [ "$planned" = "$trace" ] \
    || { echo "[exp22] ABORT: ${wcfg} class_plan_file=$planned but trace=$trace"; return 1; }

  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" \
      -e "s#__TRACE__#$trace#" -e "s#__WCFG__#$wcfg#" -e "s/__ARM__/$arm/" \
      runner-dyn.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp22] $arm launched $(date -u +%H:%M:%S), trace=$tag"
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout="$timeout" \
    || { echo "[exp22] $arm did not complete"; kubectl -n llumnix logs "job/$job" --tail=60; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix get deploy gateway   -o yaml > "$META/gateway_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp22] $arm DONE $(date -u +%H:%M:%S)"
}

run_rate_arm() {  # $1 arm, $2 rates, $3 durmin, $4 timeout
  local arm=$1 rates=$2 durmin=$3 timeout=$4
  local job="bench-runner-exp22-${arm}"
  local session="${SESSION_PREFIX:-exp22fix}_${arm}${SESSION_SUFFIX:-}_mixA"
  set_arm "$arm" || return 1
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rates/" -e "s/__DURMIN__/$durmin/" -e "s/__ARM__/$arm/" \
      runner-exp21.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp22] $arm launched $(date -u +%H:%M:%S), rates=$rates rpm"
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout="$timeout" \
    || { echo "[exp22] $arm did not complete"; kubectl -n llumnix logs "job/$job" --tail=60; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp22] $arm DONE $(date -u +%H:%M:%S)"
}

case "${1:-}" in
  smoke)
    check_stack
    run_rate_arm fluidserve "${2:-1800}" 2 60m
    ;;
  smokeboth)
    check_stack
    run_rate_arm polyserve  "${2:-1800}" 2 60m && run_rate_arm fluidserve "${2:-1800}" 2 60m
    ;;
  dynsmoke)
    check_stack
    run_trace_arm fluidserve "$SMOKE_TRACE" "$SMOKE_WCFG" 60m
    ;;
  dynsmokeboth)
    # Run both arms on the 7-minute trace before spending an hour on each. The
    # trace-replay mode has never been exercised on this cluster, so this is
    # also the first check that the class plan lands on the right arrivals and
    # that the new scheduler series appear in the scrape.
    check_stack
    run_trace_arm polyserve  "$SMOKE_TRACE" "$SMOKE_WCFG" 60m || exit 1
    run_trace_arm fluidserve "$SMOKE_TRACE" "$SMOKE_WCFG" 60m || exit 1
    echo "[exp22] DYN SMOKE DONE $(date -u +%H:%M)"
    ;;
  sweep)
    # A rate sweep, which is the only condition under which the two admission
    # layers can differ. At a single moderate rate PolyServe's occupancy
    # threshold almost never fires -- 20 rejections in 23,080 requests on the
    # dynamic run -- so every comparison so far has been between two policies
    # that were both effectively admitting everything. Raising the rate is what
    # makes admission the thing being measured, and the offered denominator is
    # what makes a rejection cost what it actually costs.
    #
    #   ./run_exp22_fluidserve.sh sweep [rates] [minutes-per-rate]
    check_stack
    export SESSION_PREFIX=exp23
    RATES=${2:-600,1200,1800,2400,3000,3600}
    DUR=${3:-8}
    run_rate_arm polyserve  "$RATES" "$DUR" 240m || exit 1
    run_rate_arm fluidserve "$RATES" "$DUR" 240m || exit 1
    echo "[exp22] SWEEP DONE $(date -u +%H:%M)"
    ;;
  armsweep)
    # One arm over a rate list at a chosen duration, for when only that arm has
    # changed. The other arm's curve is reused, which is sound only while the
    # code path it exercises is untouched -- state that explicitly in the write-up
    # rather than leaving the reader to check.
    #
    #   ./run_exp22_fluidserve.sh armsweep <arm> [rates] [minutes-per-rate]
    check_stack
    export SESSION_PREFIX=${SESSION_PREFIX:-exp23}
    run_rate_arm "${2:?arm name}" "${3:-600,1200,1800,2400,3000,3600}" "${4:-8}" 240m
    echo "[exp22] ARM SWEEP DONE $(date -u +%H:%M)"
    ;;
  dyn)
    check_stack
    run_trace_arm polyserve  "$TRACE" "$WCFG" 180m || exit 1
    run_trace_arm fluidserve "$TRACE" "$WCFG" 180m || exit 1
    echo "[exp22] DYN DONE $(date -u +%H:%M)"
    ;;
  arm)
    check_stack
    case "${3:-dyn}" in
      dyn)    run_trace_arm "${2:?arm name}" "$TRACE" "$WCFG" 180m ;;
      dyn06)  run_trace_arm "${2:?arm name}" "$SMOKE_TRACE" "$SMOKE_WCFG" 90m ;;
      smoke)  run_rate_arm  "${2:?arm name}" "${4:-1800}" 2 60m ;;
      *) echo "usage: arm <name> {dyn|dyn06|smoke}"; exit 1 ;;
    esac
    ;;
  *)
    sed -n '2,26p' "$0"; exit 1 ;;
esac
