#!/bin/bash
# EXP-21: PolyServe routing vs stock Llumnix load-balance, mix A (1:1:1).
#
#   ./run_exp21_polyserve.sh smoke                 # one short condition, both arms
#   ./run_exp21_polyserve.sh sweep [rates]         # full sweep, both arms
#   ./run_exp21_polyserve.sh arm polyserve [rates] # a single arm
#
# Only the SCHEDULER POLICY differs between arms. Same workload, same rates, same
# EXP-14 protocol (cold engine restart per condition, 60s warmup at 60 rpm, then
# 5 min steady), and the client sends the packed per-request SLO in both arms --
# load-balance simply ignores it. So any difference is the routing policy.
#
#   loadbalance  stock Llumnix: least of DispatchNeutralLoadMetric
#   polyserve    tier affinity + the section 4.5-4.7 admission test + least-load
#
# The engine is stock FIFO in both arms (no --scheduler-cls, migration off), so
# this isolates routing from engine scheduling, unlike EXP-17..20 which varied
# the engine and held routing fixed.
set -uo pipefail
cd "$(dirname "$0")"
REPO=/home/nxclab/llumnix_reproduce
META=../../results/exp07_meta
mkdir -p "$META"

# Brackets the knee EXP-17 found: comfortable at 600, collapsing by 3000.
RATES="${RATES:-600,1200,1800,2400,3000}"
DURMIN="${DURMIN:-5}"

check_stack() {
  local th bin mig extra
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp21] ABORT: KV admission theta=$th is on; it would reject requests and"
    echo "        confound PolyServe's own admission test"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp21] ABORT: gateway is not the host-built binary ($bin)"; exit 1; }
  # The gateway must be the build that decodes the packed SLO, or every request
  # reaches the scheduler with no budget and PolyServe silently uses globals.
  mig=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='LLUMNIX_ENABLE_MIGRATION'),'unset'))")
  [ "$mig" = "0" ] || { echo "[exp21] ABORT: engine migration is '$mig', want 0 (it would move requests across tier boundaries)"; exit 1; }
  extra=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='SCHED_EXTRA_ARGS'),'unset'))")
  [ -z "$extra" ] || { echo "[exp21] ABORT: engine has SCHED_EXTRA_ARGS='$extra', want stock FIFO"; exit 1; }
  echo "[exp21] stack ok: theta off, gateway=$bin, migration off, engine stock FIFO"
}

set_arm() {  # $1 = loadbalance | polyserve
  local policy
  case "$1" in
    loadbalance) policy=load-balance ;;
    polyserve)   policy=polyserve ;;
    *) echo "[exp21] unknown arm: $1" >&2; return 1 ;;
  esac
  echo "[exp21] switching scheduler -> $policy"
  python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy "$policy" \
    | sed 's/^/[exp21]   /'
  # Confirm from the live process, not from the spec: an apply that silently did
  # not take effect would otherwise run a whole arm under the wrong policy.
  local pod actual
  pod=$(kubectl -n llumnix get pods -l app=scheduler \
        --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
  actual=$(kubectl -n llumnix logs "$pod" --tail=3000 2>/dev/null \
           | grep -ao "create scheduler with policy: [a-z-]*" | tail -1 | awk '{print $NF}')
  [ "$actual" = "$policy" ] \
    || { echo "[exp21] ABORT: scheduler reports policy '$actual', wanted '$policy'"; return 1; }
  echo "[exp21] scheduler confirmed running policy=$actual (pod $pod)"
}

run_arm() {  # $1 arm, $2 rates, $3 durmin, $4 timeout
  local arm=$1 rates=$2 durmin=$3 timeout=$4
  local job="bench-runner-exp21-${arm}" session="exp21_${arm}_mixA"
  set_arm "$arm" || return 1
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rates/" -e "s/__DURMIN__/$durmin/" -e "s/__ARM__/$arm/" \
      runner-exp21.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp21] $arm launched $(date -u +%H:%M:%S), rates=$rates"
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout="$timeout" \
    || { echo "[exp21] $arm did not complete"; kubectl -n llumnix logs "job/$job" --tail=40; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp21] $arm DONE $(date -u +%H:%M:%S)"
}

case "${1:-}" in
  smoke)
    check_stack
    run_arm loadbalance 600 2 40m && run_arm polyserve 600 2 40m
    ;;
  arm)
    check_stack
    run_arm "${2:?usage: arm loadbalance|polyserve [rates]}" "${3:-$RATES}" "$DURMIN" 400m
    ;;
  sweep)
    check_stack
    run_arm loadbalance "${2:-$RATES}" "$DURMIN" 400m || exit 1
    run_arm polyserve   "${2:-$RATES}" "$DURMIN" 400m || exit 1
    echo "[exp21] SWEEP DONE $(date -u +%H:%M)"
    ;;
  *)
    sed -n '2,18p' "$0"; exit 1 ;;
esac
