#!/bin/bash
# EXP-27: rate x mix sweep on the shortened swe workload.
#
#   ./run_exp27_mixsweep.sh calib [rates] [min]      polyserve only, all mixes,
#                                                    to locate where the fleet
#                                                    saturates before spending
#                                                    hours at the wrong rates
#   ./run_exp27_mixsweep.sh sweep [rates] [min] [reps]
#   ./run_exp27_mixsweep.sh arm <arm> <mix> [rates] [min]
#
# Two things changed from EXP-25 and both are in the workload, not the policy:
#
#   swe input   22,474 -> 6,812 mean tokens (shortened transcript). At the old
#               length swe carried 82.6% of the fleet's input tokens at equal
#               request counts and cost 15x a chat request, so PolyServe's
#               partition came out (2 swe / 1 chat / 1 dr) for every mix it
#               could be given. A partition that never moves cannot be shown to
#               be worse than one that moves.
#   the mix     stated as a share of INPUT TOKENS rather than of request counts.
#               m1 balanced 31/37/31, m2 chat-heavy 64/19/16, m3 swe-heavy
#               19/19/63 (chat / deep research / swe).
#
# Reproduced off-line against allocateServers, the three mixes no longer agree:
# m2 computes (1 swe / 2 chat / 1 dr) where m1 and m3 compute (2 / 1 / 1).
#
# The scheduler policy is the only thing that differs between arms. Same
# workload, same rates, same cold engine restart per condition.
set -uo pipefail
cd "$(dirname "$0")"
REPO=/home/nxclab/llumnix_reproduce
META=../../results/exp07_meta
mkdir -p "$META"

declare -A MIXCFG=(
  [m1]=/work/workload_configs/mix_short_m1_balanced.json
  [m2]=/work/workload_configs/mix_short_m2_chatheavy.json
  [m3]=/work/workload_configs/mix_short_m3_sweheavy.json
  # m1 with the agent class's 30 s end-to-end budget restated as the (ttft, tbt)
  # pair closest to FluidServe's nominal pace. For the Llumnix SLO arm only: that
  # policy has no end-to-end mode and took the default decomposition's 25 ms as a
  # literal per-token target, judging the same requests 2.3x tighter than
  # FluidServe judged them. Scoring is unaffected -- the analysis scores the agent
  # class on end-to-end 30 s whatever this file says.
  [m1f]=/work/workload_configs/mix_short_m1_slofair.json
)
HOSTWORK=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
SHORT_TRANSCRIPT="$HOSTWORK/workloads/codingagent_request_level_poisson/data/transcript_swe_short7k_mix1500.jsonl"

check_stack() {
  local th bin mig extra
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp27] ABORT: KV admission theta=$th is on"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp27] ABORT: gateway is not the host-built binary ($bin)"; exit 1; }
  mig=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='LLUMNIX_ENABLE_MIGRATION'),'unset'))")
  # Migration is per ARM in this experiment: the two Llumnix baselines get their
  # own mechanism and the two policies under test do not use it. The check still
  # exists -- it now catches an engine left in the wrong state for the arm about
  # to run, which is the failure it was always for.
  want_mig="${EXPECT_MIGRATION:-0}"
  [ "$mig" = "$want_mig" ] || { echo "[exp53] ABORT: engine migration is '$mig', want $want_mig"; exit 1; }
  extra=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='SCHED_EXTRA_ARGS'),'unset'))")
  # An engine-side scheduler confounds a control-plane comparison, so the
  # default is to refuse any. EXP-40 makes the engine scheduler the variable
  # under test, and states which one it expects: the check then still catches an
  # engine left in the wrong state, which is the failure it exists to prevent.
  # An unset EXPECT_SCHED_EXTRA_ARGS keeps the old behaviour exactly.
  want_extra="${EXPECT_SCHED_EXTRA_ARGS-}"
  [ "$extra" = "$want_extra" ] || {
    echo "[exp27] ABORT: engine has SCHED_EXTRA_ARGS='$extra', expected '${want_extra:-<empty>}'"
    exit 1; }
  # The shortened transcript is the whole point of this experiment; running it
  # against the long one would silently reproduce EXP-25 under a new name.
  [ -s "$SHORT_TRANSCRIPT" ] \
    || { echo "[exp27] ABORT: short transcript missing: $SHORT_TRANSCRIPT"; exit 1; }
  echo "[exp53] stack ok: theta off, gateway=$bin, migration=$mig, engine sched='${want_extra:-stock FIFO}'"
}

# Migration is switched by editing the engine's env, which restarts the pod, so
# it is done once per arm rather than once per condition. ARM_MIG and ARM_WCFG
# are read by run_cell.
set_arm() {  # $1 = fluidserve | polyserve | slo | loadbalance
  local policy
  case "$1" in
    # The two policies under test. Neither uses migration, so neither gets it.
    # FS_GATE_SLACK is set from the value EXP-52 selects; 1.0 is the shipped
    # policy and is what this defaults to until that experiment says otherwise.
      # FS_PREFIX=false is explicit and must stay that way. The compiled
      # default became true at v0.2, and every results directory named
      # *_fluidserve_* that already exists was produced with it OFF. Leaving
      # this arm on the compiled default would make one arm name mean two
      # configurations depending on when it ran, which is the failure this
      # repository keeps hitting. The arm that exercises the shipped default is
      # `fspfx`, and it sets FS_PREFIX=true explicitly for the same reason.
    fluidserve)  policy=fluidserve; export FS_PREFIX=false; ARM_MIG=0; ARM_WCFG=m1
                 export FS_CLASS_HARM=false FS_FORCE_MARGIN=false \
                        FS_OWN_BUDGET_GATE=false FS_GATE_SLACK="${FS_SLACK:-1.0}" ;;
    polyserve)   policy=polyserve;  ARM_MIG=0; ARM_WCFG=m1 ;;
    # The two Llumnix baselines, each with its own migration mechanism ON. `slo`
    # is SLO-aware but NOT class-aware -- --ttft-slo and --tpot-slo are single
    # global values -- and it takes the m1f workload config for the reason
    # EXP-28 recorded: the default decomposition hands it the agent class's
    # 25 ms as a literal per-token target and it rejects 98% of that class.
    # Scoring is unaffected; the analysis judges that class end to end whatever
    # the config says. `loadbalance` is stock Llumnix with no SLO input at all.
    slo)         policy=slo;          ARM_MIG=1; ARM_WCFG=m1f ;;
    loadbalance) policy=load-balance; ARM_MIG=1; ARM_WCFG=m1 ;;
    *) echo "[exp53] unknown arm: $1" >&2; return 1 ;;
  esac
  local cur
  cur=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='LLUMNIX_ENABLE_MIGRATION'),'0'))")
  if [ "$cur" != "$ARM_MIG" ]; then
    echo "[exp53] engine migration $cur -> $ARM_MIG (restarts the engine)"
    python3 "$REPO/Agent_applications/agent_motivation_experiment/k8s/exp07/set_engine_sched.py" \
      --policy fifo --migration "$([ "$ARM_MIG" = 1 ] && echo on || echo off)" | sed 's/^/[exp53]   /' \
      || { echo "[exp53] ABORT: could not set migration"; return 1; }
  fi
  EXPECT_MIGRATION=$ARM_MIG check_stack || return 1
  echo "[exp27] switching scheduler -> $policy"
  python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy "$policy" \
    | sed 's/^/[exp27]   /'
  local pod actual i
  # Retry: the rollout returns as soon as the pod is Ready, which is before the
  # start-up line has necessarily been written and collected. A single grep here
  # aborted a condition of EXP-31 that was otherwise fine -- the policy had been
  # applied correctly and the run simply never started. Read it as many times as
  # it takes rather than treating "not yet" as "wrong".
  for i in $(seq 1 30); do
    pod=$(kubectl -n llumnix get pods -l app=scheduler \
          --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
    # From the START of the log: at -v 4 the start-up line leaves any tail window
    # within seconds.
    actual=$(kubectl -n llumnix logs "$pod" 2>/dev/null \
             | grep -am1 -ao "create scheduler with policy: [a-z-]*" | awk '{print $NF}')
    [ -n "$actual" ] && break
    sleep 2
  done
  [ -n "$actual" ] || { echo "[exp27] scheduler pod $pod reported no policy after 60s"; return 1; }
  [ "$actual" = "$policy" ] \
    || { echo "[exp27] ABORT: scheduler reports '$actual', wanted '$policy'"; return 1; }
  echo "[exp27] scheduler confirmed policy=$actual (pod $pod)"
}

# Wait for a Job to reach either terminal state. Returns 0 on Complete and 1 on
# Failed or on the deadline expiring, so a failed condition ends the wait instead
# of holding the chain open.
wait_job() {  # $1 job name, $2 deadline in minutes
  local job=$1 deadline=$(( $2 * 60 / 10 )) i st
  for i in $(seq 1 "$deadline"); do
    st=$(kubectl -n llumnix get job "$job" -o jsonpath='{.status.conditions[*].type}' 2>/dev/null)
    case "$st" in
      *Complete*) return 0 ;;
      *Failed*)   echo "[wait_job] $job reached condition Failed"; return 1 ;;
    esac
    sleep 10
  done
  echo "[wait_job] $job did not finish within $2 minutes"
  return 1
}

run_cell() {  # $1 arm, $2 mix key (overridden by the arm), $3 rates, $4 durmin
  local arm=$1 mix=$2 rates=$3 durmin=$4
  set_arm "$arm" || return 1
  mix=${ARM_WCFG:-$mix}
  local wcfg=${MIXCFG[$mix]:-}
  [ -n "$wcfg" ] || { echo "[exp27] unknown mix '$mix'"; return 1; }
  [ -s "${HOSTWORK}${wcfg#/work}" ] || { echo "[exp27] missing $wcfg"; return 1; }
  local job="bench-runner-exp53-${arm}"
  local session="${SESSION_PREFIX:-exp53}_${arm}${SESSION_SUFFIX:-}_${mix}"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rates/" -e "s/__DURMIN__/$durmin/" -e "s/__ARM__/$arm/" \
      -e "s#__WCFG__#$wcfg#" \
      runner-exp27.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp27] $arm/$mix launched $(date -u +%H:%M:%S), rates=$rates rpm dur=${durmin}m"
  # Poll for EITHER terminal condition. `kubectl wait --for=condition=complete`
  # never returns on a job that FAILS -- it sits until its own timeout -- so a
  # single failed condition used to block the whole chain for five hours. EXP-48
  # rep 2 lost a night that way: engine 8002 did not come back from the cold
  # restart, the runner exited 1, and the sweep waited on a job that was already
  # in state Failed.
  wait_job "$job" 300 \
    || { echo "[exp27] $arm/$mix did not complete"
         kubectl -n llumnix logs "job/$job" --tail=60; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp27] $arm/$mix DONE $(date -u +%H:%M:%S)"
}

# Arm is the INNER loop and repeat the outer, as always: machine state drifts
# over hours and with the arm outside, one arm would be measured entirely in a
# different stretch of that drift.
RATES=${RATES:-900,1500,2100,2700,3000,3300,3600,4200}
DUR=${DUR:-8}
REPS=${REPS:-2}
for rep in $(seq 1 "$REPS"); do
  for arm in ${ARMS:-fluidserve polyserve slo loadbalance}; do
    echo "[exp53] === rep $rep $arm $(date -u +%H:%M:%S)"
    SESSION_PREFIX="${PREFIX:-exp53}r${rep}" run_cell "$arm" m1 "$RATES" "$DUR" \
      || echo "[exp53] rep$rep $arm FAILED"
  done
  echo "[exp53] REPEAT $rep DONE $(date -u +%H:%M)"
done
echo "[exp53] ALL DONE $(date -u +%F' '%H:%M)"
