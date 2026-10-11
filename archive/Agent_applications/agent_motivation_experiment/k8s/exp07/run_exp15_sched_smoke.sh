#!/bin/bash
# EXP-15 smoke: intra-engine scheduling policy comparison on ONE condition.
#
#   RATES=1800 ./run_exp15_sched_smoke.sh        # all four policies at 30 req/s
#   RATES=2280 ./run_exp15_sched_smoke.sh srpf   # one policy at 38 req/s
#
# Pick a rate where a WAITING QUEUE actually exists: scheduling policy orders
# the waiting queue, so at 22 req/s (queue ~6) all four policies degenerated to
# admit-on-arrival and were indistinguishable. EXP-14 mix A queue by rate:
#   22 -> 7, 30 -> 618, 38 -> 1877, 47 -> 3447.
#
# Condition: mix A (1:1:1) at $RATES rpm.
# 5-min run, migration OFF for every policy (isolates the intra-engine
# scheduler; the FIFO arm is re-run here rather than reusing EXP-14, which had
# migration ON, so the comparison is apples-to-apples).
#
# The client always sends per-class SLO budgets, so the request stream is
# byte-identical across policies. Only EDF consumes them (FIFO ignores
# priority; SJF/SRPF overwrite it inside the engine).
set -uo pipefail
cd "$(dirname "$0")"
META=../../results/exp07_meta
mkdir -p "$META"
RATES=${RATES:-1800}   # rpm; 1800=30 req/s (queue ~618), 2280=38 req/s (~1877)
DURMIN=5
POLICIES=${*:-"fifo edf sjf srpf"}

check_stack() {
  local bin
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp15] ABORT: gateway is not the custom binary ($bin)"; exit 1; }
  echo "[exp15] gateway=$bin"
}

run_policy() {
  local pol=$1
  local job="bench-runner-exp15-${pol}"
  local session="exp15_${pol}"
  echo "=================================================================="
  echo "[exp15] policy=$pol rate=$((RATES/60))req/s  ($(date -u +%H:%M:%S))"
  # 1) switch the engine scheduler (migration off) and let it restart
  python3 set_engine_sched.py --policy "$pol" --migration off --restart \
    | grep -E '^\[sched\]'
  echo "[exp15] waiting for engine to come back ..."
  sleep 20
  until [ "$(kubectl -n llumnix get pod neutral-0 \
             -o jsonpath='{.status.containerStatuses[?(@.name=="vllm")].ready}' \
             2>/dev/null)" = "true" ]; do sleep 15; done
  # confirm the policy actually took effect in the live process
  kubectl -n llumnix logs neutral-0 -c vllm --tail=4000 2>/dev/null \
    | grep -ao "scheduling_policy': '[^']*'\|scheduler_cls': '[^']*'" | sort -u \
    | sed 's/^/[exp15]   /'
  # 2) run the single condition
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$RATES/" -e "s/__DURMIN__/$DURMIN/" \
      -e "s|__MIXJSON__|{\"chat\":1,\"deepresearch\":1,\"swe\":1}|" \
      runner-exp15.template.yaml | kubectl apply -f - >/dev/null
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout=60m \
    || { echo "[exp15] $pol did not complete"; kubectl -n llumnix logs "job/$job" --tail=25; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null \
    | grep -aE "Success:|rc=" | tail -2
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp15] policy=$pol DONE ($(date -u +%H:%M:%S))"
}

check_stack
for p in $POLICIES; do run_policy "$p"; done
echo "[exp15] ALL POLICIES DONE $(date -u +%H:%M:%S)"
