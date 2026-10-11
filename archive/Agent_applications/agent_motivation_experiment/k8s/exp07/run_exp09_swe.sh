#!/bin/bash
# EXP-09 driver: SWE tool-delay full sweeps (exp06 scenario) at multiple
# KV-admission thetas, run sequentially. Designed for unattended overnight
# runs: launch with nohup, progress goes to results/exp09_driver.log.
#
#   nohup ./run_exp09_swe.sh 0.5 0.4 0.3 > ../../results/exp09_driver.log 2>&1 &
#
# If a job named bench-runner-exp09 (the standalone theta=0.6 run) is active
# when this starts, it is waited on and archived first.
set -uo pipefail
cd "$(dirname "$0")"
META=../../results/exp07_meta
mkdir -p "$META"

finish_job() {  # $1 job name, $2 snapshot suffix
  kubectl -n llumnix logs "job/$1" --tail=300 2>/dev/null | grep -E "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_$2.yaml"
  kubectl -n llumnix delete job "$1" --ignore-not-found
}

# 0) drain a pre-existing standalone exp09 job (theta=0.6) if present
if kubectl -n llumnix get job bench-runner-exp09 >/dev/null 2>&1; then
  echo "=== [exp09] waiting for pre-existing bench-runner-exp09 (theta=0.6) ==="
  kubectl -n llumnix wait --for=condition=complete job/bench-runner-exp09 --timeout=300m \
    || { echo "[exp09] pre-existing job did not complete"; exit 1; }
  finish_job bench-runner-exp09 exp09_th0600
fi

for TH in "$@"; do
  TTAG=$(awk -v t="$TH" 'BEGIN{printf "%04d", t*1000}')
  JOB="bench-runner-exp09-th${TTAG}"
  echo "=== [exp09] theta=$TH session=exp09_swe_kvadm_th${TTAG} $(date -u +%H:%M) ==="
  ./patch-scheduler-kvadm.sh "$TH"
  kubectl -n llumnix delete job "$JOB" --ignore-not-found
  sed -e "s/__JOBNAME__/$JOB/" -e "s/__SESSION__/exp09_swe_kvadm_th${TTAG}/" \
      runner-exp09.template.yaml | kubectl apply -f -
  kubectl -n llumnix wait --for=condition=complete "job/$JOB" --timeout=300m \
    || { echo "[exp09] $JOB did not complete"; kubectl -n llumnix logs "job/$JOB" --tail=30; exit 1; }
  finish_job "$JOB" "exp09_th${TTAG}"
done
echo "[exp09] ALL DONE $(date -u +%H:%M)"
