#!/bin/bash
# EXP-10 driver: SWE open-loop request-level replay (no admission).
#
#   ./run_exp10_replay.sh record   # E1: 60-min transcript recording run
#   ./run_exp10_replay.sh smoke    # 2-min replay sanity check (lambda=1)
#   ./run_exp10_replay.sh sweep    # E2: full lambda sweep (11 conditions)
#
# Prereq (E0, run once, manually): admission OFF + stock gateway --
#   ./patch-scheduler-kvadm.sh 0 && ./patch-gateway-fastfail.sh revert
# The driver refuses to start if the scheduler still has a nonzero theta.
set -uo pipefail
cd "$(dirname "$0")"
META=../../results/exp07_meta
mkdir -p "$META"
TRANSCRIPT=../../workloads/codingagent_request_level_poisson/data/transcript_swe_calls.jsonl
LAMBDAS="1,2,3,4,5,6,8,10,12,16,20"
DURMIN=8

check_admission_off() {
  local th
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp10] ABORT: scheduler admission threshold is still $th (want 0/off)"
    echo "        run: ./patch-scheduler-kvadm.sh 0"
    exit 1
  fi
  echo "[exp10] admission check ok (theta off)"
}

run_job() {  # $1 job, $2 template, $3 session, $4 timeout, extra seds...
  local job=$1 tpl=$2 session=$3 timeout=$4; shift 4
  kubectl -n llumnix delete job "$job" --ignore-not-found
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" "$@" "$tpl" \
    | kubectl apply -f -
  kubectl -n llumnix wait --for=condition=complete "job/$job" --timeout="$timeout" \
    || { echo "[exp10] $job did not complete"; kubectl -n llumnix logs "job/$job" --tail=30; exit 1; }
  kubectl -n llumnix logs "job/$job" --tail=300 2>/dev/null | grep -E "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found
}

case "${1:-}" in
  record)
    check_admission_off
    run_job bench-runner-exp10-record runner-exp10-record.template.yaml \
      exp10_record 100m
    echo "[exp10] transcript lines: $(wc -l < "$TRANSCRIPT" 2>/dev/null || echo MISSING)"
    ;;
  smoke)
    [ -s "$TRANSCRIPT" ] || { echo "[exp10] transcript missing -- run 'record' first"; exit 1; }
    check_admission_off
    run_job bench-runner-exp10-smoke runner-exp10.template.yaml \
      exp10_smoke 30m -e "s/__LAMBDAS__/1/" -e "s/__DURMIN__/2/"
    ;;
  sweep)
    [ -s "$TRANSCRIPT" ] || { echo "[exp10] transcript missing -- run 'record' first"; exit 1; }
    check_admission_off
    LAMBDAS="${2:-$LAMBDAS}"   # optional override, e.g. `sweep 10,12,16,20` for re-runs
    run_job bench-runner-exp10-sweep runner-exp10.template.yaml \
      exp10_replay 300m -e "s/__LAMBDAS__/$LAMBDAS/" -e "s/__DURMIN__/$DURMIN/"
    echo "[exp10] SWEEP DONE $(date -u +%H:%M)"
    ;;
  *)
    echo "usage: $0 {record|smoke|sweep [lambda,list]}"; exit 1 ;;
esac
