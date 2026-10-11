#!/bin/bash
# EXP-14 driver: mixed workload (chat + deep-research + SWE) rate sweep.
#
#   ./run_exp14_mix.sh smoke                 # 2-min sanity at 2 req/s, mix A (1:1:1)
#   ./run_exp14_mix.sh sweep A|B|C [rates]   # 5-min per condition
#   ./run_exp14_mix.sh all                   # A, then B, then C (~5.3h)
#
# Mixes (request-count ratio chat:deepresearch:swe):
#   A = 1:1:1  balanced requests        (token mass ~2.5/15/82%)
#   B = 6:3:1  light-dominant, realistic (token mass ~8.6/26/65%)
#   C = 1:1:3  heavy-dominant, stress    (token mass ~1.2/7.2/92%)
#
# Prereq: no-timeout gateway + 64Gi + admission off — the driver checks all.
set -uo pipefail
cd "$(dirname "$0")"
META=../../results/exp07_meta
mkdir -p "$META"
RATES="60,120,180,240,300,360,480,600,780,960"   # 1..16 req/s
DURMIN=5

mix_json() {
  case "$1" in
    A) echo '{"chat":1,"deepresearch":1,"swe":1}' ;;
    B) echo '{"chat":6,"deepresearch":3,"swe":1}' ;;
    C) echo '{"chat":1,"deepresearch":1,"swe":3}' ;;
    *) echo "unknown mix: $1" >&2; return 1 ;;
  esac
}

check_stack() {
  local th bin mem
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp14] ABORT: admission theta=$th (want off)"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp14] ABORT: gateway is not the no-timeout binary ($bin)"; exit 1; }
  mem=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].resources.limits.memory}')
  echo "[exp14] stack ok: theta off, gateway=$bin mem=$mem"
}

run_job() {  # $1 job, $2 session, $3 timeout, $4 mixjson, $5 rates, $6 durmin
  local job=$1 session=$2 timeout=$3 mixjson=$4 rates=$5 durmin=$6
  kubectl -n llumnix delete job "$job" --ignore-not-found
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rates/" -e "s/__DURMIN__/$durmin/" \
      -e "s|__MIXJSON__|$mixjson|" \
      runner-exp14.template.yaml | kubectl apply -f -
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout="$timeout" \
    || { echo "[exp14] $job did not complete"; kubectl -n llumnix logs "job/$job" --tail=30; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy gateway -o yaml > "$META/gateway_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found
}

case "${1:-}" in
  smoke)
    check_stack
    run_job bench-runner-exp14-smoke exp14_smoke 30m "$(mix_json A)" 120 2
    ;;
  sweep)
    m="${2:?usage: sweep A|B|C [rates]}"
    check_stack
    run_job "bench-runner-exp14-$(echo "$m" | tr 'A-Z' 'a-z')" "exp14_mix${m}" 300m \
            "$(mix_json "$m")" "${3:-$RATES}" "$DURMIN"
    echo "[exp14] SWEEP $m DONE $(date -u +%H:%M)"
    ;;
  all)
    check_stack
    for m in A B C; do
      echo "[exp14] === mix $m start $(date -u +%H:%M) ==="
      run_job "bench-runner-exp14-$(echo "$m" | tr 'A-Z' 'a-z')" "exp14_mix${m}" 300m \
              "$(mix_json "$m")" "$RATES" "$DURMIN" \
        || echo "[exp14] mix $m FAILED — continuing"
      echo "[exp14] === mix $m done $(date -u +%H:%M) ==="
    done
    echo "[exp14] ALL DONE $(date -u +%H:%M)"
    ;;
  *)
    echo "usage: $0 {smoke|sweep A|B|C [rates]|all}"; exit 1 ;;
esac
