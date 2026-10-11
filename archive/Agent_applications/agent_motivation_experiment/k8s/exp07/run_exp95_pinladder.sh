#!/bin/bash
# EXP-95 SNAPSHOT of run_exp94_wsweep.sh (itself a snapshot of run_exp92_m5.sh), taken 2026-08-24, so the original stays
# editable while this runs. Four arms added: the class-preference weight sweep.
# Nothing else changed.
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
  # EXP-92. The mix LEVEL, which this project chose (10:2:1) and has never
  # varied on the post-fix workload, is the axis class preference's effect was
  # derived to depend on: with chat at 76.9% of requests three of four instances
  # must carry it whatever the policy prefers, so preference decides the
  # composition of ONE instance. m5 puts every class at a third, which is the
  # same three workloads, the same SLO block, the same transcript and the same
  # chat pool -- only the request-count ratio differs. No profile regeneration:
  # classes[] holds per-class OUTPUT LENGTH distributions, which do not depend
  # on how many of each class arrive.
  [m5]=/work/workload_configs/mix_short_m5_equal.json
  [m4]=/work/workload_configs/mix_short_m4_chat57.json
  [schat]=/work/workload_configs/single_chat.json
  [sdr]=/work/workload_configs/single_deepresearch.json
  [sswe]=/work/workload_configs/single_swe.json
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
  # EXP-94. Clear every ablation variable before the arm sets the ones it wants.
  #
  # This driver had no such clearing, and the weight sweep is the first arm set
  # here where one arm's export would silently reconfigure the next: `export
  # FS_AFFINITY_WEIGHT=0` from the w=0 arm stays in the shell, and the control
  # arm, which sets no weight and is supposed to run at the compiled default of
  # 1.0, would then run at 0 and be identical to the treatment under a different
  # name. That is what EXP-60 lost a control condition to, one flag earlier.
  #
  # set_scheduler_profiling.py cannot catch it: it removes an ablation the
  # invocation did not ASK for, and a leaked export means this invocation did
  # ask. The clearing has to happen here.
  unset FS_PEND FS_SHED FS_AFFINITY FS_AFFINITY_WEIGHT FS_CLASS_PIN \
        FS_FLUX FS_CLASS_HARM FS_HORIZON FS_Z FS_FORCE_MARGIN \
        FS_OWN_BUDGET_GATE FS_KV_SLOPE FS_GATE_SLACK FS_PREFIX \
        FS_SHED_SIGNAL FS_ORACLE_LEN
  case "$1" in
    # Pinned, not left unset: set_scheduler_profiling.py now returns an unset
    # ablation to its compiled default, which for class-harm is true, while every
    # FluidServe condition from EXP-27 pass 2 to EXP-46 ran with it false (§38).
    # EXP-47's first run went out with classharm=true for exactly this reason.
      # FS_PREFIX=false is explicit and must stay that way. The compiled
      # default became true at v0.2, and every results directory named
      # *_fluidserve_* that already exists was produced with it OFF. Leaving
      # this arm on the compiled default would make one arm name mean two
      # configurations depending on when it ran, which is the failure this
      # repository keeps hitting. The arm that exercises the shipped default is
      # `fspfx`, and it sets FS_PREFIX=true explicitly for the same reason.
    fluidserve)  policy=fluidserve; export FS_PREFIX=false FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-67. The prefill charge for an arriving prompt becomes per instance:
    # the prompt minus the leading blocks the candidate instance is believed to
    # already hold, from an index the scheduler keeps of what it dispatched
    # where. Everything else is the `fluidserve` arm above, so the difference
    # from it is one change. See ms_dev/notes/fluidserve-prefix.md.
    #
    # fspfx-nocal drops the calibration as well, which is what corrects a charge
    # built on cache hits the engines no longer have. The two arms together
    # separate "knowing which instance holds the prefix" from "being corrected
    # when that belief is wrong".
    fspfx)       policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    # EXP-94. The class-preference STRENGTH axis. sortCandidates scores a
    # feasible instance as w*share + (1-w)*room, so w=1 orders purely by how much
    # of the arriving class the instance already holds and w=0 orders purely by
    # free space. The source comment for that function records that EXP-56
    # measured only the two endpoints and calls the values between them "the axis
    # that experiment could not sweep". These arms sweep it.
    #
    # w=0 is NOT the same as --fluidserve-enable-affinity=false. That flag also
    # removes the class term from the damage estimate used to order INFEASIBLE
    # candidates, so the existing off arm changes two things and this one changes
    # one. The pair therefore separates them, which no experiment has done.
    #
    # Everything else is pinned exactly as fspfx pins it, so the only difference
    # from the deployed configuration is the weight.
    fsw00)       policy=fluidserve; export FS_AFFINITY_WEIGHT=0    FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    fsw25)       policy=fluidserve; export FS_AFFINITY_WEIGHT=0.25 FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    fsw50)       policy=fluidserve; export FS_AFFINITY_WEIGHT=0.5  FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    fsw75)       policy=fluidserve; export FS_AFFINITY_WEIGHT=0.75 FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    # EXP-95. The pin ladder: how many engines the TIGHT class is allowed to
    # occupy. --fluidserve-class-pin filters the candidate list by instance
    # POSITION in the sorted instance-id list, and a tier absent from the string
    # is left unrestricted (fluidserve.go applyClassPin returns the candidates
    # unchanged when the arriving tier is not listed), so pinning tier 50 alone
    # constrains chat and leaves deep research and swe free to go anywhere.
    #
    # The preference on its own frees about one engine of four and never more:
    # three engines stay chat-bound in 71.9-74.1% of engine-seconds. This ladder
    # asks whether that cap is what limits the gain. fspinc3 is the instrument
    # check -- one free engine, the same structure the preference produces
    # emergently -- and it should land on fspfx if the ladder is to be read as
    # "number of chat-free engines" at all. fspinc1 is the known-bad anchor:
    # chat's produced output tokens are about 2.5 engines' worth, so one engine
    # must starve it and the pin allows no spill.
    #
    # Arm names carry no hyphen ON PURPOSE. exp22_fluidserve.arm_of matches
    # exp[0-9a-z]*_([a-z][a-z0-9]*)_ against the directory name, so an arm called
    # fspin-c1 does not match and the function falls back to returning the whole
    # directory name -- which means every figure and table would treat each
    # condition as its own arm. The pre-existing fspin-poly and fspin-demand arms
    # have that defect.
    fspinc3)     policy=fluidserve; export FS_CLASS_PIN="50:0,1,2" FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    fspinc2)     policy=fluidserve; export FS_CLASS_PIN="50:0,1"   FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    fspinc1)     policy=fluidserve; export FS_CLASS_PIN="50:0"     FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    # EXP-92. Class preference off, everything else as deployed. FS_PREFIX is
    # pinned true EXPLICITLY rather than left on the compiled default, so this
    # arm differs from fspfx in exactly one flag whatever binary it runs on.
    fsnoaff)     policy=fluidserve; export FS_AFFINITY=false FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true ;;
    # EXP-78. The admission axis, which every earlier ablation left alone.
    #
    # The paper's central sentence is that routing and admission control are one
    # decision, and until now no arm has had admission turned off, so the size of
    # its contribution is unmeasured while the claim rests on it. EXP-77 made the
    # question concrete rather than rhetorical: the vLLM router completes almost
    # the same number of requests we do in the same eight minutes (12.4k against
    # 13.0k, the fleet's capacity) and 315 to 984 of them meet their budgets
    # against our 11,774 to 12,495. The first thing a reader asks is whether that
    # gap is simply rejection, and whether any policy that rejects would show it.
    #
    # fsnoshed  removes ONLY the rejection. The request that cannot meet its own
    #           budget on the best instance available is placed there anyway
    #           instead of being refused. Holding, class preference and
    #           prefix-aware prefill accounting all stay on, so the difference
    #           from fspfx is admission and nothing else.
    # fsroute   removes rejection AND holding, which is FluidServe as pure
    #           routing -- set_scheduler_profiling.py's own header calls that
    #           combination exactly that. It is the arm that can be put beside
    #           the vLLM router and PolyServe on equal terms, all three refusing
    #           nothing, differing only in how they choose a destination.
    #
    # Both keep FS_PREFIX=true and the same three pinned settings as fspfx, so
    # the ladder subtracts cleanly against the control re-run in this session.
    #
    # SCORING. With rejection off these arms produce unfinished-at-the-window's
    # end requests instead of rejections, and attain() drops those from BOTH
    # denominators because their outcome is unknown -- which flatters exactly the
    # arms that stop refusing. The headline for this experiment is therefore the
    # aggregation that counts every arrival, scoring a rejection AND an
    # unfinished request as a miss. That is fixed here, before the run.
    fsnoshed)    policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true FS_SHED=false ;;
    fsroute)     policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true FS_SHED=false FS_PEND=false ;;
    fspfx-nocal) policy=fluidserve; export FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false FS_PREFIX=true FS_PREFIX_CALIBRATION=false ;;
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
    # The vLLM router's default cache_aware policy, ported as a baseline. The
    # five constants are left at the compile defaults, which are vllm-router
    # 0.1.15's own values, and set_scheduler_profiling.py reads the start-up line
    # back and refuses the condition unless they match those values -- the
    # SGLang gateway it was forked from ships cache_threshold 0.7 against
    # vllm-router's 0.3 and the two are different routers, so an unchecked
    # default would run as this arm under the same name. The same check requires
    # localaccount=true, without which the load signal collapses to a 500 ms poll
    # snapshot. Plan and judgement rule: ms_dev/notes/vllm-router-baseline.md.
    vllmcache)   policy=vllm-cache ;;
    loadbalance) policy=load-balance ;;
    *) echo "[exp27] unknown arm: $1" >&2; return 1 ;;
  esac
  echo "[exp27] switching scheduler -> $policy"
  # EXP-88: the status of this call was being discarded. It is a pipeline, so
  # even with pipefail nobody read the result, and the only check that follows
  # reads the POLICY NAME -- which stays correct when an ablation flag fails to
  # apply. That is exactly the case the verifier exists to catch, so the
  # condition used to run on the wrong settings while a checker sat beside it
  # (CLAUDE.md trap A). Same shape as the fix EXP-79 made to run_exp67_prefix.sh.
  if ! python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy "$policy" \
       | sed 's/^/[exp27]   /'; then
    echo "[exp27] ABORT: set_scheduler_profiling.py reported a settings mismatch"
    return 1
  fi
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
  # EXP-67 names its own jobs so that a chain waiting on "has EXP-67 finished"
  # cannot match a completed EXP-27 job left in the cluster. Finished jobs stay
  # queryable until deleted, and a wait condition that matched one of those
  # waited forever on an experiment that ended eight days earlier.
  local job="bench-runner-exp67-${arm}"
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
