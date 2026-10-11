#!/bin/bash
# EXP-13 driver: searcharena deep-research workload (mid-length, ~3.2k tok in).
#
#   ./run_exp13_searcharena.sh smoke          # 2-min sanity at 2 req/s
#   ./run_exp13_searcharena.sh sweep [rates]  # rate grid (rpm), 5 min per condition
#
# Prereq: no-timeout gateway + 64Gi + admission off — the driver checks all.
set -uo pipefail
cd "$(dirname "$0")"
META=../../results/exp07_meta
mkdir -p "$META"
RATES=""      # sweep grid TBD after smoke — pass explicitly for now
DURMIN=5

check_stack() {
  local th bin mem
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp13] ABORT: admission theta=$th (want off)"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp13] ABORT: gateway is not the no-timeout binary ($bin) — run ./patch-gateway-timeout.sh"; exit 1; }
  mem=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')
  echo "[exp13] stack ok: theta off, gateway=$bin mem=$mem"
}

run_job() {  # $1 job, $2 session, $3 timeout, extra seds...
  local job=$1 session=$2 timeout=$3; shift 3
  kubectl -n llumnix delete job "$job" --ignore-not-found
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" "$@" \
      runner-exp13.template.yaml | kubectl apply -f -
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout="$timeout" \
    || { echo "[exp13] $job did not complete"; kubectl -n llumnix logs "job/$job" --tail=30; exit 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy gateway -o yaml > "$META/gateway_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found
}

case "${1:-}" in
  smoke)
    check_stack
    run_job bench-runner-exp13-smoke exp13_smoke 30m \
      -e "s/__RATES__/120/" -e "s/__DURMIN__/2/"
    ;;
  sweep)
    check_stack
    RATES="${2:-$RATES}"
    [ -n "$RATES" ] || { echo "[exp13] sweep grid TBD — pass rates (rpm), e.g. sweep 300,600,1200"; exit 1; }
    run_job bench-runner-exp13-sweep exp13_searcharena 360m \
      -e "s/__RATES__/$RATES/" -e "s/__DURMIN__/$DURMIN/"
    echo "[exp13] SWEEP DONE $(date -u +%H:%M)"
    ;;
  *)
    echo "usage: $0 {smoke|sweep [rates_rpm]}"; exit 1 ;;
esac
