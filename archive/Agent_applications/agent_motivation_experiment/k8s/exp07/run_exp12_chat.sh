#!/bin/bash
# EXP-12 driver: chat uncontrolled baseline re-sweep on the CURRENT stack
# (3.1-70B, theta=0, no-timeout gateway) — replaces the legacy 3-70B EXP-05
# as the canonical chat baseline. exp05 protocol: warmup 20 req/s x 60s +
# 5 min load per condition; analysis window [60s, 340s] arrival-anchored.
# Reuses runner-exp11.template.yaml (DURMIN placeholder).
#
#   ./run_exp12_chat.sh sweep [rates_rpm]
set -uo pipefail
cd "$(dirname "$0")"
META=../../results/exp07_meta
mkdir -p "$META"
# 5,10,20,30,40,50,60,70,80,90,100,120 req/s
RATES="300,600,1200,1800,2400,3000,3600,4200,4800,5400,6000,7200"
DURMIN=5

check_stack() {
  local th bin
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp12] ABORT: admission theta=$th (want off)"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp12] ABORT: gateway is not the no-timeout binary ($bin)"; exit 1; }
  echo "[exp12] stack ok: theta off, gateway=$bin"
}

case "${1:-}" in
  sweep)
    check_stack
    RATES="${2:-$RATES}"
    kubectl -n llumnix delete job bench-runner-exp12-sweep --ignore-not-found
    sed -e "s/__JOBNAME__/bench-runner-exp12-sweep/" -e "s/__SESSION__/exp12_chat/" \
        -e "s/__RATES__/$RATES/" -e "s/__DURMIN__/$DURMIN/" \
        runner-exp11.template.yaml | kubectl apply -f -
    kubectl -n llumnix wait --for=condition=complete job/bench-runner-exp12-sweep --timeout=360m \
      || { echo "[exp12] sweep did not complete"; kubectl -n llumnix logs job/bench-runner-exp12-sweep --tail=30; exit 1; }
    kubectl -n llumnix logs job/bench-runner-exp12-sweep --tail=600 2>/dev/null | grep -aE "Success:|rc=" | tail -3
    kubectl -n llumnix delete job bench-runner-exp12-sweep --ignore-not-found
    echo "[exp12] SWEEP DONE $(date -u +%H:%M)"
    ;;
  *)
    echo "usage: $0 sweep [rates_rpm]"; exit 1 ;;
esac
