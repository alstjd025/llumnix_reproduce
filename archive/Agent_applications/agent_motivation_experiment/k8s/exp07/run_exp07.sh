#!/bin/bash
# EXP-07 driver (run on the NXC13 host): KV-occupancy-threshold admission sweep.
#
# Per condition: (1) patch the scheduler deployment with theta, (2) launch a
# single-rate runner job (which itself cold-restarts engine+control-plane via
# --restart-per-condition, picking up the patched theta), (3) wait, (4) save a
# scheduler-deployment snapshot next to the results.
#
# Usage:
#   run_exp07.sh smoke   # 2 quick validation conditions (see SMOKE below)
#   run_exp07.sh sweep   # full 12-condition sweep (~3h)
#
# Prereqs (one-time, done by the operator):
#   - bin/scheduler-exp07 built at /home/nxclab/llumnix_reproduce/bin
#   - ./patch-gateway-fastfail.sh applied (rejects fast-fail as 503)
set -euo pipefail
cd "$(dirname "$0")"

# "rpm theta" pairs. theta=0 -> admission disabled (in-experiment baseline).
SWEEP=(
  "3600 0"     "3600 0.8"   "3600 0.6"   "3600 0.45"  "3600 0.3"   # 60 req/s
  "5400 0"     "5400 0.8"   "5400 0.6"   "5400 0.45"  "5400 0.3"   # 90 req/s
  "3000 0"     "3000 0.3"                                          # 50 req/s
)
# smoke: tiny theta must reject almost everything at 5 req/s;
# theta=0.99 must reject nothing (stock-equivalent sanity).
SMOKE=(
  "300 0.0001"
  "300 0.99"
)

case "${1:-}" in
  smoke) CONDS=("${SMOKE[@]}");;
  sweep) CONDS=("${SWEEP[@]}");;
  *) echo "usage: $0 {smoke|sweep}"; exit 1;;
esac

META_DIR=../../results/exp07_meta
mkdir -p "$META_DIR"

for cond in "${CONDS[@]}"; do
  read -r RPM THETA <<<"$cond"
  TTAG=$(awk -v t="$THETA" 'BEGIN{printf "%04d", t*1000}')
  SESSION="exp07_kvadm_th${TTAG}"
  JOB="bench-runner-exp07-r${RPM}-t${TTAG}"
  echo "=== [exp07] rpm=$RPM theta=$THETA session=$SESSION ==="

  ./patch-scheduler-kvadm.sh "$THETA"

  kubectl -n llumnix delete job "$JOB" --ignore-not-found
  sed -e "s/__JOBNAME__/$JOB/" -e "s/__RPM__/$RPM/" -e "s/__SESSION__/$SESSION/" \
      runner-exp07.template.yaml | kubectl apply -f -

  # restart(~8m for 70B load) + warmup(1m) + run(5m) + teardown
  if ! kubectl -n llumnix wait --for=condition=complete "job/$JOB" --timeout=40m; then
    echo "[exp07] WARN: $JOB did not complete in time; logs:"
    kubectl -n llumnix logs "job/$JOB" --tail=40 || true
    exit 1
  fi
  kubectl -n llumnix logs "job/$JOB" --tail=15 | grep -E "SUMMARY|Success|Failed|rc=" || true

  kubectl -n llumnix get deploy scheduler -o yaml \
    > "$META_DIR/scheduler_deploy_${SESSION}_rpm_${RPM}.yaml"
  kubectl -n llumnix delete job "$JOB" --ignore-not-found
done
echo "[exp07] all conditions done"
