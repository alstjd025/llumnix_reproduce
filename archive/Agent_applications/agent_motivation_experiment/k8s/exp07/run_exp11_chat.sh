#!/bin/bash
# EXP-11 driver: chat deep-overload extension (exp05 continuation, 3.1-70B).
#
#   ./run_exp11_chat.sh smoke    # 2-min sanity at 110 req/s (thread/conn check)
#   ./run_exp11_chat.sh sweep    # anchors + deep grid, 10 min per condition
#
# Prereq: no-timeout gateway + 64Gi + admission off — the driver checks all.
set -uo pipefail
cd "$(dirname "$0")"
META=../../results/exp07_meta
mkdir -p "$META"
RATES="3600,5400,6600,7500,9000,10500"   # 60,90 anchors + 110,125,150,175 req/s
DURMIN=10

check_stack() {
  local th bin mem
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp11] ABORT: admission theta=$th (want off)"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp11] ABORT: gateway is not the no-timeout binary ($bin) — run ./patch-gateway-timeout.sh"; exit 1; }
  mem=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')
  echo "[exp11] stack ok: theta off, gateway=$bin mem=$mem"
}

run_job() {  # $1 job, $2 session, $3 timeout, extra seds...
  local job=$1 session=$2 timeout=$3; shift 3
  kubectl -n llumnix delete job "$job" --ignore-not-found
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" "$@" \
      runner-exp11.template.yaml | kubectl apply -f -
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout="$timeout" \
    || { echo "[exp11] $job did not complete"; kubectl -n llumnix logs "job/$job" --tail=30; exit 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy gateway -o yaml > "$META/gateway_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found
}

case "${1:-}" in
  smoke)
    check_stack
    run_job bench-runner-exp11-smoke exp11_smoke 30m \
      -e "s/__RATES__/6600/" -e "s/__DURMIN__/2/"
    ;;
  sweep)
    check_stack
    RATES="${2:-$RATES}"   # optional override, e.g. `sweep 9000,10500` for re-runs
    run_job bench-runner-exp11-sweep exp11_chat 360m \
      -e "s/__RATES__/$RATES/" -e "s/__DURMIN__/$DURMIN/"
    echo "[exp11] SWEEP DONE $(date -u +%H:%M)"
    ;;
  *)
    echo "usage: $0 {smoke|sweep}"; exit 1 ;;
esac
