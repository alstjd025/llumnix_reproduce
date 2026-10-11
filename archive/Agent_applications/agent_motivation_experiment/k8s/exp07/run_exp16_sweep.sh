#!/bin/bash
# EXP-16 Phase 2: per-step decode-latency instrumentation, RATE SWEEP.
#
#   ./run_exp16_sweep.sh                 # default 6 10 13 18 22 40 50 req/s
#   RATES_RPS="6 22 50" ./run_exp16_sweep.sh
#
# Spreads scheduling steps across the KV-occupancy axis so the KV / prefill /
# batch terms decouple (Phase 1 @30 req/s was saturated -> KV & batch collinear,
# VIF~11). Unsaturated rates (6-22) give many low-KV fast-decode steps; 40/50
# push deep into saturation. Engine = InstrumentedScheduler (stock FIFO order +
# per-step JSONL log), migration OFF, cold engine restart per rate.
#
# Each rate writes its own step log on the host writable mount, copied into that
# rate's results dir as server_metrics/sched_steps.jsonl.
set -uo pipefail
cd "$(dirname "$0")"
RATES_RPS=${RATES_RPS:-"6 10 13 18 22 40 50"}
DURMIN=${DURMIN:-5}
JOB="bench-runner-exp16"

check_stack() {
  local bin
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp16] ABORT: gateway is not the custom binary ($bin)"; exit 1; }
  echo "[exp16] gateway=$bin"
}

run_rate() {
  local rps=$1
  local rpm=$((rps * 60))
  local steplog_ctr="/sched-out/sched_steps_exp16_r${rps}.jsonl"
  local steplog_host="/opt/llumnix-sched-out/sched_steps_exp16_r${rps}.jsonl"
  local session="exp16_instr_rpm_${rpm}"
  echo "=================================================================="
  echo "[exp16] rate=${rps}req/s (rpm=${rpm})  ($(date -u +%H:%M:%S))"
  # fresh step log for this rate (root-owned from a prior run -> remove via pod)
  kubectl -n llumnix exec neutral-0 -c vllm -- rm -f "$steplog_ctr" 2>/dev/null || true

  # 1) cold-restart engine into instrumented with this rate's step log
  python3 set_engine_sched.py --policy instrumented --step-log "$steplog_ctr" \
    --migration off --restart | grep -E '^\[sched\]'
  echo "[exp16] waiting for engine ..."
  sleep 20
  until [ "$(kubectl -n llumnix get pod neutral-0 \
             -o jsonpath='{.status.containerStatuses[?(@.name=="vllm")].ready}' \
             2>/dev/null)" = "true" ]; do sleep 15; done
  kubectl -n llumnix logs neutral-0 -c vllm --tail=6000 2>/dev/null \
    | grep -ao "scheduler_cls': '[^']*'\|\[instrumented\] step log -> [^ ]*" \
    | sort -u | sed 's/^/[exp16]   /'

  # 2) run one condition at this rate
  kubectl -n llumnix delete job "$JOB" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$JOB/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rpm/" -e "s/__DURMIN__/$DURMIN/" \
      -e "s|__MIXJSON__|{\"chat\":1,\"deepresearch\":1,\"swe\":1}|" \
      runner-exp16.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp16] job launched rate=${rps} ($(date -u +%H:%M:%S))"
  kubectl -n llumnix wait --for=condition=complete "job/$JOB" --timeout=60m \
    || { echo "[exp16] rate=${rps} did not complete"; kubectl -n llumnix logs "job/$JOB" --tail=25; }
  kubectl -n llumnix logs "job/$JOB" --tail=400 2>/dev/null \
    | grep -aE "Success:|rc=" | tail -2

  # 3) place the step log into the run dir
  local rundir
  rundir=$(ls -dt ../../results/*"exp16_instr_rpm_${rpm}_rpm_${rpm}" 2>/dev/null | head -1)
  if [ -n "$rundir" ] && [ -s "$steplog_host" ]; then
    mkdir -p "$rundir/server_metrics"
    cp "$steplog_host" "$rundir/server_metrics/sched_steps.jsonl"
    echo "[exp16] steplog ($(wc -l < "$steplog_host") lines) -> $rundir/server_metrics/sched_steps.jsonl"
  else
    echo "[exp16] WARN rate=${rps}: rundir='$rundir' host='$steplog_host'"
  fi
  kubectl -n llumnix delete job "$JOB" --ignore-not-found >/dev/null
  echo "[exp16] rate=${rps} DONE ($(date -u +%H:%M:%S))"
}

check_stack
for r in $RATES_RPS; do run_rate "$r"; done
echo "[exp16] ALL RATES DONE ($(date -u +%H:%M:%S))"
