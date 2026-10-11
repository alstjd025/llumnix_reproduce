#!/bin/bash
# EXP-16 Phase 1: per-step decode-latency instrumentation on ONE condition.
#
#   RATES=1800 ./run_exp16_perstep.sh        # mix A (1:1:1) @ 30 req/s, 5 min
#
# Goal: not to fit coefficients, but to establish that the per-step decode time
# DECOMPOSES into separable terms (T_schedule / prefill / KV / batch) and is
# therefore modellable. The engine runs InstrumentedScheduler = stock FIFO
# ordering + a per-step JSONL log (SCHED_STEP_LOG), so this isolates engine
# physics, not any scheduling policy. Migration OFF.
#
# Output: results/<run>/server_metrics/sched_steps.jsonl  (copied from the host
# writable mount /opt/llumnix-sched-out after the job completes) alongside the
# usual metrics.csv / tbt_events.jsonl / server_metrics/engine_*.jsonl so the
# engine-side step ITL can be cross-validated against client-side ITL.
set -uo pipefail
cd "$(dirname "$0")"
RATES=${RATES:-1800}          # rpm; 1800 = 30 req/s (queue ~618 in EXP-14)
DURMIN=${DURMIN:-5}
STAMP=$(date +%H%M%S)
STEPLOG_NAME="sched_steps_exp16_${STAMP}.jsonl"
STEPLOG_CTR="/sched-out/${STEPLOG_NAME}"          # path inside the container
STEPLOG_HOST="/opt/llumnix-sched-out/${STEPLOG_NAME}"
SESSION="exp16_instr_rpm_${RATES}"
JOB="bench-runner-exp16"

check_stack() {
  local bin
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp16] ABORT: gateway is not the custom binary ($bin)"; exit 1; }
  echo "[exp16] gateway=$bin"
}

check_stack

# 1) switch engine to the instrumented scheduler (FIFO order) + step logging,
#    migration off, and restart so it takes effect.
echo "[exp16] switching engine -> instrumented (steplog=${STEPLOG_CTR})"
python3 set_engine_sched.py --policy instrumented --step-log "${STEPLOG_CTR}" \
  --migration off --restart | grep -E '^\[sched\]'
echo "[exp16] waiting for engine to come back ..."
sleep 20
until [ "$(kubectl -n llumnix get pod neutral-0 \
           -o jsonpath='{.status.containerStatuses[?(@.name=="vllm")].ready}' \
           2>/dev/null)" = "true" ]; do sleep 15; done
# confirm the class + step log path actually took effect in the live process
kubectl -n llumnix logs neutral-0 -c vllm --tail=6000 2>/dev/null \
  | grep -ao "scheduler_cls': '[^']*'\|\[instrumented\] step log -> [^ ]*" \
  | sort -u | sed 's/^/[exp16]   /'

# 2) run the single condition (mix A) using the exp15 runner template.
kubectl -n llumnix delete job "$JOB" --ignore-not-found >/dev/null
sed -e "s/__JOBNAME__/$JOB/" -e "s/__SESSION__/$SESSION/" \
    -e "s/__RATES__/$RATES/" -e "s/__DURMIN__/$DURMIN/" \
    -e "s|__MIXJSON__|{\"chat\":1,\"deepresearch\":1,\"swe\":1}|" \
    runner-exp15.template.yaml | kubectl apply -f - >/dev/null
echo "[exp16] job launched ($(date -u +%H:%M:%S)); step log growing at ${STEPLOG_HOST}"
sleep 90
echo "[exp16] steplog line count after ~90s: $(wc -l < "${STEPLOG_HOST}" 2>/dev/null || echo NA)"
kubectl -n llumnix wait --for=condition=complete "job/$JOB" --timeout=60m \
  || { echo "[exp16] job did not complete"; kubectl -n llumnix logs "job/$JOB" --tail=25; }
kubectl -n llumnix logs "job/$JOB" --tail=400 2>/dev/null \
  | grep -aE "Success:|rc=" | tail -2

# 3) copy the host step log into the run's results dir for a self-contained run.
RUNDIR=$(ls -dt ../../results/*"${SESSION}" 2>/dev/null | head -1)
if [ -n "$RUNDIR" ] && [ -s "${STEPLOG_HOST}" ]; then
  mkdir -p "${RUNDIR}/server_metrics"
  cp "${STEPLOG_HOST}" "${RUNDIR}/server_metrics/sched_steps.jsonl"
  echo "[exp16] step log ($(wc -l < "${STEPLOG_HOST}") lines) -> ${RUNDIR}/server_metrics/sched_steps.jsonl"
else
  echo "[exp16] WARN: could not place step log (RUNDIR='$RUNDIR' host='${STEPLOG_HOST}')"
fi
kubectl -n llumnix delete job "$JOB" --ignore-not-found >/dev/null
echo "[exp16] DONE ($(date -u +%H:%M:%S))"
