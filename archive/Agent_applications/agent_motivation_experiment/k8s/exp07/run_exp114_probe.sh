#!/usr/bin/env bash
# EXP-114 step 2+3 -- does the measurement see all eight engines, and how fast can
# the load generator actually offer requests to them?
#
# WHY THIS EXISTS AT ALL. Eight instances of an 8B model are roughly ten times the
# fleet throughput of four instances of a 70B one, which puts the knee somewhere
# near 200-300 req/s. The highest attempt rate this load generator has ever
# reached is 267.7/s (260707_0813_exp02cal_rpm_24000), and it was already
# producing connection-level failures for 9.5% of requests there. If the client
# tops out below the fleet's knee then everything measured above that point is a
# property of the client, not of the policy -- which is what happened in EXP-54,
# where a saturated arm exhausted ephemeral ports and its "170 req/s" was the
# rate at which the client was failing and retrying, not the trace's arrival rate.
#
# So this measures the client and the fleet together, at a policy that has no
# admission control of its own (load-balance never rejects), and reports:
#   - realised attempts per second vs. what the rate asked for
#   - error_msg by kind, connection-level failures separated out
#   - whether all eight engine_*.jsonl series exist
#
# WHY load-balance. It takes no SLO input and reads no profile, so nothing here
# depends on the profile tables that have not been measured for this model yet.
#
#   ./run_exp114_probe.sh check           one 2-minute condition, verify instrumentation
#   ./run_exp114_probe.sh ladder          the rate ladder
#   RATES_RPS="20 60 120 200 300" ./run_exp114_probe.sh ladder
set -uo pipefail
cd "$(dirname "$0")"

NS=llumnix
REPO=/home/nxclab/llumnix_reproduce
HOSTWORK=$REPO/Agent_applications/agent_motivation_experiment
MODEL=${MODEL:-meta-llama/Llama-3.1-8B-Instruct}
WCFG=${WCFG:-/work/workload_configs/mix_short_m1_t75.json}
DURMIN=${DURMIN:-3}
TAG=${TAG:-exp114probe}
JOB=bench-runner-exp114

say() { echo "[exp114] $(date -u +%H:%M:%S) $*"; }

preflight() {
  local bin
  bin=$(kubectl -n $NS get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] || { say "ABORT: gateway is not the custom binary ($bin)"; return 1; }
  python3 "$REPO/ms_dev/scripts/switch_model.py" --verify llama31-8b-b200-tp1 \
    | sed 's/^/[exp114]   /'
  local rc=${PIPESTATUS[0]}
  [ "$rc" = "0" ] || { say "ABORT: fleet is not llama31-8b-b200-tp1 (rc=$rc)"; return 1; }
  EPORTS=$(kubectl -n $NS get cm llumnix-model -o jsonpath='{.data.ENGINE_PORTS}')
  say "preflight ok: $MODEL on ports $EPORTS"
}

set_policy() {
  set -o pipefail
  python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy load-balance \
    2>&1 | sed 's/^/[exp114]   /'
  local rc=${PIPESTATUS[0]}
  [ "$rc" = "0" ] || { say "ABORT: could not set load-balance (rc=$rc)"; return 1; }
}

wait_job() {
  local deadline=$(( ${1:-30} * 6 )) i st
  for i in $(seq 1 "$deadline"); do
    st=$(kubectl -n $NS get job "$JOB" -o jsonpath='{.status.conditions[*].type}' 2>/dev/null)
    case "$st" in
      *Complete*) return 0 ;;
      *Failed*)   say "$JOB Failed"; return 1 ;;
    esac
    sleep 10
  done
  say "$JOB did not finish in time"; return 1
}

run_rates() {
  local rpm_list=$1 session=$2
  kubectl -n $NS delete job "$JOB" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$JOB/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rpm_list/" -e "s/__DURMIN__/$DURMIN/" -e "s/__ARM__/loadbalance/" \
      -e "s#__WCFG__#$WCFG#" \
      runner-exp114.template.yaml | kubectl apply -f - >/dev/null
  wait_job 90 || { kubectl -n $NS logs "job/$JOB" --tail=60; return 1; }
  kubectl -n $NS logs "job/$JOB" --tail=800 2>/dev/null \
    | grep -aE "engine ports:|Success:|rc=" | sed 's/^/[exp114]   /'
  kubectl -n $NS delete job "$JOB" --ignore-not-found >/dev/null
}

report() {
  local session=$1
  for d in $(ls -dt "$HOSTWORK"/results/*"${session}"_rpm_* 2>/dev/null | grep -v PRERUN); do
    "$HOSTWORK/.venv/bin/python" "$HOSTWORK/analysis_scripts/request_level/exp114_probe_report.py" "$d" \
      | sed 's/^/[exp114]   /'
  done
}

case "${1:-check}" in
  check)
    preflight || exit 1
    set_policy || exit 1
    DURMIN=2 run_rates 1200 "${TAG}chk"
    report "${TAG}chk"
    ;;
  ladder)
    preflight || exit 1
    set_policy || exit 1
    RATES_RPS=${RATES_RPS:-"20 60 120 200 300"}
    RPMS=""
    for r in $RATES_RPS; do RPMS="${RPMS:+$RPMS,}$((r * 60))"; done
    # A rate list is comma-separated; passing it space-separated makes the runner
    # die in forty seconds with "could not convert string to float" while the
    # chain reports DONE (trap B).
    case "$RPMS" in *[!0-9,]*) say "ABORT: bad rate list '$RPMS'"; exit 1 ;; esac
    say "ladder rpm list: $RPMS"
    run_rates "$RPMS" "${TAG}lad"
    report "${TAG}lad"
    ;;
  *) say "usage: $0 {check|ladder}"; exit 2 ;;
esac
say "=== EXP-114 PROBE DONE"
