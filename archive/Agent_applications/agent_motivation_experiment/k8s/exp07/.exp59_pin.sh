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
  # EXP-55. One class only. With a single-class workload no routing decision can
  # differentiate anything, so a fleet of four under load-balance is four
  # replicas of the same single-engine experiment and the per-engine series are
  # read directly. What is being located is where each class saturates and WHICH
  # resource saturates first -- pace, KV, or the end-to-end budget.
  [schat]=/work/workload_configs/single_chat.json
  [sdr]=/work/workload_configs/single_deepresearch.json
  [sswe]=/work/workload_configs/single_swe.json
)
HOSTWORK=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
SHORT_TRANSCRIPT="$HOSTWORK/results/exp10_transcript/transcript_swe_short7k_mix1500.jsonl"

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
  [ "$mig" = "0" ] || { echo "[exp27] ABORT: engine migration is '$mig', want 0"; exit 1; }
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
  echo "[exp27] stack ok: theta off, gateway=$bin, migration off, engine sched='${want_extra:-stock FIFO}'"
}

set_arm() {  # $1 = fluidserve | polyserve | slo | loadbalance
  local policy
  case "$1" in
    # Pinned, not left unset: set_scheduler_profiling.py now returns an unset
    # ablation to its compiled default, which for class-harm is true, while every
    # FluidServe condition from EXP-27 pass 2 to EXP-46 ran with it false (§38).
    # EXP-47's first run went out with classharm=true for exactly this reason.
    fluidserve)  policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-42. Both are the FluidServe policy from the SAME binary; they differ
    # only in whether the forced-placement test applies the allowance margin
    # that routing already applies. Set explicitly in both directions so the
    # scheduler's start-up line carries a positive confirmation either way,
    # rather than the treatment arm being the only one that can be checked.
    # FS_CLASS_HARM is pinned to false in BOTH arms, not because false is the
    # intended setting but because every FluidServe condition from 2026-07-28
    # 10:28 to EXP-41 ran that way (the flag stuck in the deployment), and a
    # baseline that silently differs from EXP-38 would make this comparison
    # incomparable with everything it is being read against. Turning it back on
    # is its own experiment, EXP-43, run one change at a time.
    fsa)         policy=fluidserve; export FS_FORCE_MARGIN=true  FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false ;;
    fsbase)      policy=fluidserve; export FS_FORCE_MARGIN=false FS_CLASS_HARM=false ;;
    # EXP-56. The class-preference ablation. --fluidserve-enable-affinity=false
    # removes BOTH places a class preference acts: the ordering of the feasible
    # set by classShare, and the class term in the damage estimate. Sorting falls
    # back to free space, which is a load-balancing rule, so this arm is
    # "FluidServe with everything else identical and the routing fully mixed".
    #
    # It is the only clean test of the claim in section 58.3 that the separation
    # is what keeps chat's per-token time inside budget. Both repeats of EXP-54
    # separated, so there was no unseparated run to compare against.
    #
    # FS_CLASS_HARM stays pinned false for the same reason every other arm pins
    # it: the baseline it is read against ran that way. Turning it on is EXP-43.
    fsnoaff)     policy=fluidserve; export FS_AFFINITY=false FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-58. The same preference as a DEGREE instead of a switch. The feasible
    # set is ordered by w*share + (1-w)*room, so w=1 is the `fluidserve` arm
    # above (share decides, free space only breaks ties) and w=0 is the
    # `fsnoaff` arm (free space alone). The values between them are the axis
    # EXP-56 could not sweep, and they exist for one reason: with only two
    # settings, every point on a concentration-against-attainment plot beyond
    # those two has to come from a DIFFERENT system, and then nothing can be
    # attributed to the concentration. These arms put five points of varying
    # concentration inside one binary.
    #
    # The spacing is not uniform. The ordering flips where w*dShare equals
    # (1-w)*dRoom, and dShare between two instances at 45 req/s is of order 0.5
    # while dRoom is of order 0.1, which puts the crossing near w=0.17. Uniform
    # spacing would spend three of five points in the region where the class
    # preference already decides everything.
    #
    # FS_CLASS_HARM stays pinned false as in every other arm, which also means
    # the weight acts in exactly one place here: the class term in the damage
    # estimate is off, so these arms differ from `fluidserve` and `fsnoaff` in
    # the feasible-set ordering and in nothing else.
    fsw000)      policy=fluidserve; export FS_AFFINITY_WEIGHT=0.0  FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    fsw005)      policy=fluidserve; export FS_AFFINITY_WEIGHT=0.05 FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    fsw015)      policy=fluidserve; export FS_AFFINITY_WEIGHT=0.15 FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    fsw040)      policy=fluidserve; export FS_AFFINITY_WEIGHT=0.4  FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    fsw100)      policy=fluidserve; export FS_AFFINITY_WEIGHT=1.0  FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-59. Pin each class to a fixed set of instances and change nothing
    # else. This is the fully-separated end of the axis measured as an ablation
    # of this policy rather than as a comparison against a different system.
    # Positions index the sorted list of instance ids: 0..3 for the four
    # engines. Tier 50 is chat, 100 is deepresearch, 25 is swe.
    #
    # fspin-poly is the allocation PolyServe's own repartitioner settled on
    # after its length table was corrected (chat 1, dr 2, swe 1). fspin-demand
    # is proportional to each class's share of the produced output tokens
    # (2.50 / 1.11 / 0.20 of four instances), rounded to integers. Having both
    # separates "the assignment is fixed" from "the assignment it chose is
    # wrong", which no measurement so far can tell apart.
    #
    # Hyphens, not underscores: the arm name becomes the Kubernetes Job name and
    # an underscore is rejected there, after the driver has already printed
    # "launched" and gone on to wait for a job that was never created.
    fspin-poly)   policy=fluidserve; export FS_CLASS_PIN="50:0;100:1,2;25:3" FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    fspin-demand) policy=fluidserve; export FS_CLASS_PIN="50:0,1;100:2;25:3"  FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-43. Candidate A is in the baseline now, so both arms carry it and the
    # single difference is the class term in the damage estimate. Its compiled
    # default is true; it has been false in every deployment since 2026-07-28
    # through a flag that stuck in the spec, so this is the first measurement of
    # the policy as designed.
    fsah)        policy=fluidserve; export FS_FORCE_MARGIN=true  FS_CLASS_HARM=true ;;
    # EXP-46. Candidate A is in both arms; the single difference is C, which
    # judges an arriving request's pace against its own class budget instead of
    # the tightest nominal budget on the instance.
    fsac)        policy=fluidserve; export FS_FORCE_MARGIN=true  FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=true ;;
    # EXP-49 candidate H2. The projection of an instance's KV occupancy is built
    # from the rate that occupancy is observed to be moving at, instead of from
    # a modelled balance of the resident set's growth against what completions
    # are expected to release. A and C are off in both arms so the flag is the
    # only difference from the `fluidserve` arm.
    fskv)        policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_KV_SLOPE=true ;;
    # EXP-50. Candidate C on its own -- the arriving request is judged against
    # its OWN class budget rather than against the tightest nominal budget on the
    # instance. The existing fsac arm carries candidate A as well; this one does
    # not, so the difference from `fluidserve` is one change.
    fsc)         policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=true ;;
    # EXP-52. Two points on the gate-slack axis between the shipped policy
    # (slack 1, the instance minimum binds as promised) and candidate C (slack
    # at or above the largest class-budget ratio, which for this workload is
    # 100/50 = 2, so the instance minimum never binds). At 1.111 the gate for a
    # loose-budget request on a chat-carrying instance lands on exactly chat's
    # 50 ms rather than the 45 ms margin below it; at 1.4 it lands on 63 ms.
    fsg11)       policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_GATE_SLACK=1.111 ;;
    fsg14)       policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_GATE_SLACK=1.4 ;;
    polyserve)   policy=polyserve ;;
    # Llumnix's own SLO-aware policy, as shipped apart from the neutral branch
    # that lets it run on a co-located fleet. It is NOT class-aware: --ttft-slo
    # and --tpot-slo are single global values, so every request is judged against
    # the same pair. That is the point of having it -- it separates what SLO
    # awareness buys from what per-class differentiation buys.
    slo)         policy=slo ;;
    loadbalance) policy=load-balance ;;
    *) echo "[exp27] unknown arm: $1" >&2; return 1 ;;
  esac
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

run_cell() {  # $1 arm, $2 mix key, $3 rates, $4 durmin
  local arm=$1 mix=$2 rates=$3 durmin=$4
  local wcfg=${MIXCFG[$mix]:-}
  [ -n "$wcfg" ] || { echo "[exp27] unknown mix '$mix'"; return 1; }
  [ -s "${HOSTWORK}${wcfg#/work}" ] || { echo "[exp27] missing $wcfg"; return 1; }
  local job="bench-runner-exp27-${arm}"
  local session="${SESSION_PREFIX:-exp27}_${arm}${SESSION_SUFFIX:-}_${mix}"
  set_arm "$arm" || return 1
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

case "${1:-}" in
  calib)
    # Where does this workload saturate? The shortened swe changes the fleet's
    # capacity, so the EXP-25 rate list is no longer the right one and guessing
    # it from the demand model is not good enough -- that model puts chat's
    # capacity well below what the fleet actually delivers. One arm, short
    # conditions, all three mixes.
    check_stack
    export SESSION_PREFIX=exp27cal
    for mix in m1 m2 m3; do
      run_cell polyserve "$mix" "${2:-600,1200,2400,3600}" "${3:-4}" || exit 1
    done
    echo "[exp27] CALIB DONE $(date -u +%H:%M)"
    ;;
  sweep)
    check_stack
    RATES=${2:-600,1200,2400}
    DUR=${3:-8}
    REPS=${4:-1}
    for rep in $(seq 1 "$REPS"); do
      # Repeat is the OUTER loop. Machine state drifts over hours, and with the
      # arm outside, one arm would be measured entirely in a different stretch
      # of that drift and the drift would read as an arm effect. EXP-24 measured
      # 5 points of between-session movement against 0.2 within a session.
      for mix in m1 m2 m3; do
        for arm in polyserve fluidserve; do
          SESSION_PREFIX="exp27r${rep}" run_cell "$arm" "$mix" "$RATES" "$DUR" \
            || echo "[exp27] rep$rep $arm/$mix FAILED"
        done
      done
      echo "[exp27] REPEAT $rep DONE $(date -u +%H:%M)"
    done
    echo "[exp27] SWEEP DONE $(date -u +%H:%M)"
    ;;
  arm)
    check_stack
    run_cell "${2:?arm}" "${3:?mix key m1|m2|m3}" "${4:-1200}" "${5:-4}"
    ;;
  *)
    sed -n '2,30p' "$0"; exit 1 ;;
esac
