#!/bin/bash
# EXP-16 Phase 3 (option A): KV-vs-batch-count decoupling by workload variety.
#
# Phase 2 could not separate the KV-read term from the batch-count term because
# the mix workload has ~constant per-request context (KV proportional to count).
# Fix: run SINGLE workloads with very different context lengths and pool the
# per-step data — short-context (chat) fills the high-count/low-KV region, long-
# context (swe) fills the low-count/high-KV region, so the (KV, count) plane is
# populated OFF-DIAGONAL and the regression can identify a (KV) vs b (count).
#
#   ./run_exp16_decouple.sh
#
# Engine = InstrumentedScheduler (FIFO + per-step log), migration OFF, cold
# restart per condition. Each condition writes its own step log.
set -uo pipefail
cd "$(dirname "$0")"
DURMIN=${DURMIN:-5}
JOB="bench-runner-exp16dec"
# "tag mixjson rps" — chat=short ctx, swe=long ctx
CONDS=(
  "chat {\"chat\":1} 30"
  "chat {\"chat\":1} 60"
  "swe  {\"swe\":1}  6"
  "swe  {\"swe\":1}  13"
  "swe  {\"swe\":1}  22"
)

check_stack() {
  local bin
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp16dec] ABORT: gateway not custom binary ($bin)"; exit 1; }
}

run_cond() {
  local tag=$1 mix=$2 rps=$3
  local rpm=$((rps * 60))
  local steplog_ctr="/sched-out/sched_steps_exp16dec_${tag}_r${rps}.jsonl"
  local steplog_host="/opt/llumnix-sched-out/sched_steps_exp16dec_${tag}_r${rps}.jsonl"
  local session="exp16dec_${tag}_rpm_${rpm}"
  echo "=================================================================="
  echo "[exp16dec] tag=$tag rate=${rps}req/s mix=$mix  ($(date -u +%H:%M:%S))"
  kubectl -n llumnix exec neutral-0 -c vllm -- rm -f "$steplog_ctr" 2>/dev/null || true
  python3 set_engine_sched.py --policy instrumented --step-log "$steplog_ctr" \
    --migration off --restart | grep -E '^\[sched\]'
  echo "[exp16dec] waiting for engine ..."
  sleep 20
  until [ "$(kubectl -n llumnix get pod neutral-0 \
             -o jsonpath='{.status.containerStatuses[?(@.name=="vllm")].ready}' \
             2>/dev/null)" = "true" ]; do sleep 15; done
  kubectl -n llumnix delete job "$JOB" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$JOB/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rpm/" -e "s/__DURMIN__/$DURMIN/" \
      -e "s|__MIXJSON__|$mix|" \
      runner-exp16.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp16dec] job launched $tag/$rps ($(date -u +%H:%M:%S))"
  kubectl -n llumnix wait --for=condition=complete "job/$JOB" --timeout=60m \
    || { echo "[exp16dec] $tag/$rps did not complete"; kubectl -n llumnix logs "job/$JOB" --tail=25; }
  kubectl -n llumnix logs "job/$JOB" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -2
  local rundir
  rundir=$(ls -dt ../../results/*"exp16dec_${tag}_rpm_${rpm}_rpm_${rpm}" 2>/dev/null | head -1)
  if [ -n "$rundir" ] && [ -s "$steplog_host" ]; then
    mkdir -p "$rundir/server_metrics"
    cp "$steplog_host" "$rundir/server_metrics/sched_steps.jsonl"
    echo "[exp16dec] steplog ($(wc -l < "$steplog_host") lines) -> $rundir/server_metrics/sched_steps.jsonl"
  else
    echo "[exp16dec] WARN $tag/$rps: rundir='$rundir' host='$steplog_host'"
  fi
  kubectl -n llumnix delete job "$JOB" --ignore-not-found >/dev/null
  echo "[exp16dec] $tag/$rps DONE ($(date -u +%H:%M:%S))"
}

check_stack
for c in "${CONDS[@]}"; do run_cond $c; done
echo "[exp16dec] ALL DONE ($(date -u +%H:%M:%S))"
