#!/bin/bash
# EXP-120 SNAPSHOT of run_exp119_combo.sh, taken 2026-09-10. Adds the two arms
# that pair the corrected first-token estimate with a memory brake.
# (Original header follows.)
# EXP-119 SNAPSHOT of run_exp118_prefillfix.sh, taken 2026-09-10. Adds the two
# deadline-in-feasibility arms that complete the 2x2; nothing else changes.
# (Original header follows.)
# EXP-118 SNAPSHOT of run_exp116_sweep8.sh, taken 2026-09-10. Adds three arms
# for the first-token estimate corrections; everything else is unchanged.
#
# WHY THIS EXISTS. Every eight-instance comparison against PolyServe on record is
# ONE hour-long run per arm at ONE arrival rate (x6.20, which puts the fleet at
# 0.97 of its measured knee of 173.5 req/s). The judgement that FluidServe loses
# to PolyServe by 7.6 points of offered attainment therefore rests on a single
# measurement per condition, and this workload's repeat-to-repeat spread reaches
# 4.6 points. There is no static rate sweep of PolyServe on this fleet at all.
#
# WHAT THIS MEASURES. FluidServe, PolyServe and Llumnix load-balance across six
# arrival rates that bracket the knee (100, 130, 160, 185, 210, 250 req/s), eight
# minutes each with an engine cold restart per condition. It answers whether the
# loss is a property of the policy across the load range or a property of the one
# operating point the hour trace happens to sit at, and it gives the shape of both
# curves so that the two knees can be compared the way they were on four
# instances (FluidServe 28.0 against PolyServe 15.8 req/s there).
#
# ONLY ADDITION over the file it snapshots: [loadbalance]=t75 in ARMMIX.
# EXP-108 SNAPSHOT of run_exp106_polyserve.sh, taken 2026-08-31. It carries the
# EXP-106 fixes forward (see the three numbered items below) and adds the two
# things that experiment did not need: the FluidServe arms' environment has to be
# cleared and pinned, and the workload configuration has to move to the per-token
# form of the agent class's promise.
#
# WHAT THIS MEASURES. FluidServe v0.4 restated the agent class's SLO from an
# end-to-end 30 s budget to TTFT 7 s + 75 ms per token, because admission
# enforces an instantaneous rate while an end-to-end budget is an integral over a
# request's lifetime, and the two cannot be made to agree (v0.4 section 1.3,
# EXP-107 section 10.2). Every policy that is compared against it therefore has
# to be judged in the same form, and the three that read the promise as an input
# -- PolyServe, llm-d, Llumnix SLO -- have to be TOLD it in that form. This
# sweep re-measures the static rate curve under that form.
#
#   arms here      fsv3capgnofrct75 (FluidServe v0.4 candidate), polyservep, slo
#   arm elsewhere  llmdslo, in run_exp108_llmd.sh -- llm-d is a separate stack
#                  with its own per-condition predictor restart and pre-run
#
# ⚠ WHICH WORKLOAD FILE, AND WHY THERE ARE TWO. The per-token promise is 75 ms.
# FluidServe receives it as --fluidserve-class-budgets 25:decode:75, where 25 is
# the TIER KEY that names the class in every profile lookup and log line, so its
# workload file keeps swe tbt_ms=25 (mix_short_m1_t75.json). The other three read
# slo.<class>.tbt_ms literally as the per-token target, so theirs states 75
# (mix_short_m1_t75fair.json). Giving them the FluidServe file would judge swe
# three times tighter than it is promised, which is the defect
# polyserve-fidelity.md section 9.5 is about. ARMMIX below pairs them, and
# run_cell exports PS_MIX_CONFIG from the same key, so one table decides both.
#
# ⚠ SCORING MOVES WITH IT: FS_SWE_TBT_MS=75 for every table built from these
# runs. exp22_fluidserve.py warns on import when it is set. swe columns scored
# this way must never sit beside e2e-scored swe columns.
#
# EXP-106 SNAPSHOT of run_exp27_mixsweep.sh, taken 2026-08-31. It is a snapshot
# rather than an edit of the original because the original is on the measurement
# path of anything that reuses it, and because EXP-27's own arms must keep
# meaning what their results say they mean.
#
#   ./run_exp108_t75.sh onerep <arm> <rep> [rates] [min]   one arm, one repeat.
#                        This is the entry the chain uses, because the repeat is
#                        the outer loop and the arms are interleaved inside it.
#   ./run_exp108_t75.sh smoke [rates] [min]      one condition, SMOKE_ARM
#   ./run_exp108_t75.sh arm <arm> <mix> [rates] [min]
#
# What this measures: whether "a static class partition cannot move capacity"
# still holds once the baseline is given the mechanisms its paper has. The four
# arms are defined in set_arm below; the question, the validity checks and the
# pre-registered predictions are in
# experiments/EXP-106_polyserve-paper-fidelity.md.
#
# THREE THINGS DIFFER FROM THE EXP-27 ORIGINAL, and each is a defect the
# original carries that this experiment cannot survive:
#
#   1. set_arm now ABORTS when set_scheduler_profiling.py reports a mismatch.
#      The original piped it into sed and dropped the status, and the only check
#      after that reads the POLICY NAME, which is correct even when an ablation
#      flag failed to apply. Every mechanism this experiment turns on is such a
#      flag, so the original would run the control configuration under the
#      treatment arm's name and say nothing.
#   2. set_arm clears the PS_* variables before each arm. The original clears
#      nothing, so a switch turned on for polyservep would still be on when the
#      polyserve control ran next -- the same way an ablation flag once survived
#      61 conditions (CLAUDE.md group A).
#   3. run_cell exports PS_MIX_CONFIG from the SAME mix key the runner loads.
#      The tier table is derived from slo.<class>.tbt_ms in the workload file, so
#      deriving it from a different file than the requests were built from would
#      bin every request against a budget it does not carry, silently.
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
  # EXP-108. The per-token form of the agent class's promise (TTFT 7 s + 75 ms
  # per token). Two files for one promise: t75 keeps swe tbt_ms=25 because that
  # is the TIER KEY FluidServe's budget map is keyed by, and t75fair states 75
  # because the other policies read that field as the budget itself. Everything
  # outside the slo block is byte-identical to m1 in both.
  [t75]=/work/workload_configs/mix_short_m1_t75.json
  [t75fair]=/work/workload_configs/mix_short_m1_t75fair.json
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
SHORT_TRANSCRIPT="$HOSTWORK/workloads/codingagent_request_level_poisson/data/transcript_swe_short7k_mix1500.jsonl"

check_stack() {
  local th bin mig extra
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp108] ABORT: KV admission theta=$th is on"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp108] ABORT: gateway is not the host-built binary ($bin)"; exit 1; }
  mig=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='LLUMNIX_ENABLE_MIGRATION'),'unset'))")
  [ "$mig" = "0" ] || { echo "[exp108] ABORT: engine migration is '$mig', want 0"; exit 1; }
  extra=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='SCHED_EXTRA_ARGS'),'unset'))")
  # An engine-side scheduler confounds a control-plane comparison, so the
  # default is to refuse any. EXP-40 makes the engine scheduler the variable
  # under test, and states which one it expects: the check then still catches an
  # engine left in the wrong state, which is the failure it exists to prevent.
  # An unset EXPECT_SCHED_EXTRA_ARGS keeps the old behaviour exactly.
  want_extra="${EXPECT_SCHED_EXTRA_ARGS-}"
  [ "$extra" = "$want_extra" ] || {
    echo "[exp108] ABORT: engine has SCHED_EXTRA_ARGS='$extra', expected '${want_extra:-<empty>}'"
    exit 1; }
  # The shortened transcript is the whole point of this experiment; running it
  # against the long one would silently reproduce EXP-25 under a new name.
  [ -s "$SHORT_TRANSCRIPT" ] \
    || { echo "[exp108] ABORT: short transcript missing: $SHORT_TRANSCRIPT"; exit 1; }
  echo "[exp108] stack ok: theta off, gateway=$bin, migration off, engine sched='${want_extra:-stock FIFO}'"
}

set_arm() {  # $1 = fluidserve | polyserve | polyservep* | slo | loadbalance
  local policy
  # Clear the PolyServe mechanism switches before every arm. Each is read from
  # the environment by set_scheduler_profiling.py, which removes a flag it was
  # not asked to set, so an arm that names none of them configures the port's
  # pre-2026-08-27 behaviour -- but only if the shell does not still carry the
  # previous arm's exports. Without this line the control arm inherits whatever
  # the treatment arm turned on and stops being a control.
  unset PS_ADMISSION_BINDS PS_STEADY_NO_PREFILL PS_PROMOTION \
        PS_PARTITION PS_PREFER_LOADED PS_KV_ADMISSION
  # And the FluidServe side, moved here from run_exp107_capforce.sh. The base
  # driver this was snapshotted from does neither of these, which is safe only
  # while every arm is a PolyServe arm: a FluidServe flag exported for one arm
  # otherwise stays exported for the next, and an arm that names none of the
  # four v0.3 defaults silently acquires whatever the binary was compiled with.
  # Both failures are silent and both have happened (CLAUDE.md group A).
  unset FS_PEND FS_SHED FS_AFFINITY FS_AFFINITY_WEIGHT FS_AFFINITY_METRIC FS_CLASS_PIN \
        FS_FLUX FS_CLASS_HARM FS_HORIZON FS_Z FS_FORCE_MARGIN \
        FS_OWN_BUDGET_GATE FS_KV_SLOPE FS_GATE_SLACK \
        FS_PER_INSTANCE_CORR FS_MEMORY_PACE_CAP FS_DEADLINE_FEASIBLE \
        FS_PER_INSTANCE_DELAY FS_DEADLINE_USES_DELAY \
        FS_SHED_NO_FIRST_TOKEN FS_PREFILL_INTERLEAVE FS_SWE_E2E_MS \
        FS_INSTANCE_CAP FS_CAP_WINDOW_MULT FS_FORCE FS_SWE_TBT_MS FS_PREFIX
  # Then write the v0.2 configuration out, so no arm depends on a compiled
  # default. An arm wanting v0.3/v0.4 behaviour overrides these below.
  export FS_AFFINITY_METRIC=share FS_PER_INSTANCE_CORR=false \
         FS_MEMORY_PACE_CAP=false FS_PREFILL_INTERLEAVE=false
  case "$1" in
    # EXP-108. The FluidServe v0.4 adopted candidate in the per-token form of the
    # agent class's promise. The name is the one the hour-trace run already on
    # disk carries (260827_2320_exp107tr1_fsv3capgnofrct75_shift), NOT a new one:
    # the static curve and the hour trace have to read as the same arm, and a
    # second name for one configuration is the failure this repository keeps
    # recording in the other direction.
    #   cap on (guardrail)  FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0
    #   force off           FS_FORCE=false -- an unplaceable request becomes an
    #                       explicit refusal instead of a placement onto the
    #                       least-damaged instance
    #   swe per-token       FS_SWE_TBT_MS=75 -> --fluidserve-class-budgets
    #                       25:decode:75; TTFT 7 s comes from the t75 workload file
    # EXP-114. The same v0.4 candidate with the class-instance cap OFF, which is
    # the v0.3 behaviour EXP-107 measured the cap against. It exists because on
    # the eight-instance fleet the cap sized chat to two instances at 95% of
    # their modelled service rate and removed 77,675 feasible placements from
    # candidate lists while four engines sat idle. Every other flag is pinned
    # identically to fsv3capgnofrct75, so the pair differs in one flag.
    # EXP-114. The adopted v0.4 configuration with the FIRST-TOKEN DEADLINE made
    # a feasibility condition (--fluidserve-deadline-feasible). Without it
    # `feasible` is pace, harm-to-incumbents and memory only, so the routing
    # decision never asks whether a placement can deliver a first token inside
    # the budget -- and on this fleet that is the promise that breaks: chat is
    # 76.9% of arrivals and 53.6% of it misses a 5 s time-to-first-token while
    # deepresearch and swe pass at 93.9% and 96.1%.
    #
    # The flag was measured twice on four 70B instances and adopted neither time
    # (EXP-87 static, EXP-100 on the hour), both times inside the control's
    # repeat spread. Those experiments targeted deepresearch, which is 15% of
    # arrivals and had somewhere else to go; EXP-100's own conclusion is that the
    # test moves which engine carries the queue rather than shrinking it. What is
    # different here is the class that breaks and how much of the fleet is idle
    # beside it, so the earlier verdict does not carry over by itself.
    fsv3capgnofrct75dl) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_DEADLINE_FEASIBLE=true \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3gnofrct75nocap) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=false FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3capgnofrct75) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3ft) policy=fluidserve; export FS_PREFILL_FULLITER=true FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3ftq) policy=fluidserve; export FS_PREFILL_RESIDENTQ=true FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3ftboth) policy=fluidserve; export FS_PREFILL_FULLITER=true FS_PREFILL_RESIDENTQ=true FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3ftml) policy=fluidserve; export FS_MEM_LEVEL=true FS_PREFILL_FULLITER=true FS_PREFILL_RESIDENTQ=true FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3ftms85) policy=fluidserve; export FS_MEM_SAFETY=0.85 FS_PREFILL_FULLITER=true FS_PREFILL_RESIDENTQ=true FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3dl) policy=fluidserve; export FS_DEADLINE_FEASIBLE=true FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    fsv3dlftboth) policy=fluidserve; export FS_DEADLINE_FEASIBLE=true FS_PREFILL_FULLITER=true FS_PREFILL_RESIDENTQ=true FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
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
    # EXP-106. The control: the port as every PolyServe condition before
    # 2026-08-27 ran it, on the balanced mix (m1). It is in the sweep for the
    # session comparison, not because its numbers are new.
    polyserve)   policy=polyserve ;;
    # The paper-fidelity arm: all six mechanisms on, on m1f. m1f matters as much
    # as the switches do -- on m1 the agent class is judged against 25 ms per
    # token, which is the idle decode step time used as a tier label, while its
    # actual objective is 30 s end to end. Once admission binds, that label
    # becomes the class's capacity, and at 7,306 KV tokens per request it allows
    # a batch of 23 where the real budget allows 186.
    polyservep)  policy=polyserve; export PS_ADMISSION_BINDS=true \
                     PS_STEADY_NO_PREFILL=true PS_PROMOTION=true \
                     PS_PARTITION=elastic PS_PREFER_LOADED=true \
                     PS_KV_ADMISSION=true ;;
    # EXP-108. The same six mechanisms as polyservep, on the per-token form of
    # the agent class's promise. The t75 suffix is not decoration: `polyservep`
    # in run_exp106_polyserve.sh runs the SAME switches on m1f, where swe is
    # promised 52 ms, and a single name covering both would make two sets of
    # numbers that cannot share a table look like repeats of each other.
    polyservept75)  policy=polyserve; export PS_ADMISSION_BINDS=true \
                     PS_STEADY_NO_PREFILL=true PS_PROMOTION=true \
                     PS_PARTITION=elastic PS_PREFER_LOADED=true \
                     PS_KV_ADMISSION=true ;;
    # Llumnix's SLO-aware variant, unchanged. It takes the promise entirely from
    # the workload file, so the t75 suffix records that the file said 75 rather
    # than the 52 its existing static-sweep column was measured under.
    slot75)      policy=slo ;;
    # One switch off each, so a difference can be attributed to that switch
    # rather than to "the new PolyServe".
    polyservep_noadm)  policy=polyserve; export PS_STEADY_NO_PREFILL=true \
                     PS_PROMOTION=true PS_PARTITION=elastic \
                     PS_PREFER_LOADED=true PS_KV_ADMISSION=true ;;
    # partition=demand is the port's own rate-times-cost allocator. prefer-loaded
    # STAYS ON: it is what the selection rule is, and turning it off at the same
    # time would change two things at once.
    polyservep_nopool) policy=polyserve; export PS_ADMISSION_BINDS=true \
                     PS_STEADY_NO_PREFILL=true PS_PROMOTION=true \
                     PS_PARTITION=demand PS_PREFER_LOADED=true \
                     PS_KV_ADMISSION=true ;;
    # Llumnix's own SLO-aware policy, as shipped apart from the neutral branch
    # that lets it run on a co-located fleet. It is NOT class-aware: --ttft-slo
    # and --tpot-slo are single global values, so every request is judged against
    # the same pair. That is the point of having it -- it separates what SLO
    # awareness buys from what per-class differentiation buys.
    slo)         policy=slo ;;
    loadbalance) policy=load-balance ;;
    *) echo "[exp108] unknown arm: $1" >&2; return 1 ;;
  esac
  echo "[exp108] switching scheduler -> $policy"
  # ABORT on a mismatch. `set -o pipefail` is on, so the status of the python
  # process survives the pipe; the original discarded it and the mismatch was
  # visible only to whoever read the log afterwards.
  if ! python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy "$policy" \
       | sed 's/^/[exp108]   /'; then
    echo "[exp108] ABORT: set_scheduler_profiling.py reported the applied flags"
    echo "         do not match what was asked for. Every mechanism under test"
    echo "         here is one of those flags, so continuing would measure the"
    echo "         control configuration under this arm's name."
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
  [ -n "$actual" ] || { echo "[exp108] scheduler pod $pod reported no policy after 60s"; return 1; }
  [ "$actual" = "$policy" ] \
    || { echo "[exp108] ABORT: scheduler reports '$actual', wanted '$policy'"; return 1; }
  echo "[exp108] scheduler confirmed policy=$actual (pod $pod)"
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
  [ -n "$wcfg" ] || { echo "[exp108] unknown mix '$mix'"; return 1; }
  [ -s "${HOSTWORK}${wcfg#/work}" ] || { echo "[exp108] missing $wcfg"; return 1; }
  # The tier table PolyServe bins on is derived from slo.<class>.tbt_ms in the
  # workload configuration, and the requests carry that same field. Export the
  # file the runner is about to load, so one key moves both: a table derived from
  # a different file than the requests were built from produces no error, only
  # requests binned against budgets they do not carry.
  export PS_MIX_CONFIG="${wcfg##*/}"
  local job="bench-runner-exp106-${arm}"
  local session="${SESSION_PREFIX:-exp106}_${arm}${SESSION_SUFFIX:-}_${mix}"
  set_arm "$arm" || return 1
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" \
      -e "s/__RATES__/$rates/" -e "s/__DURMIN__/$durmin/" -e "s/__ARM__/$arm/" \
      -e "s#__WCFG__#$wcfg#" \
      runner-exp114p36.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp108] $arm/$mix launched $(date -u +%H:%M:%S), rates=$rates rpm dur=${durmin}m"
  # Poll for EITHER terminal condition. `kubectl wait --for=condition=complete`
  # never returns on a job that FAILS -- it sits until its own timeout -- so a
  # single failed condition used to block the whole chain for five hours. EXP-48
  # rep 2 lost a night that way: engine 8002 did not come back from the cold
  # restart, the runner exited 1, and the sweep waited on a job that was already
  # in state Failed.
  wait_job "$job" 300 \
    || { echo "[exp108] $arm/$mix did not complete"
         kubectl -n llumnix logs "job/$job" --tail=60; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp108] $arm/$mix DONE $(date -u +%H:%M:%S)"
}

# Which mix each arm runs on. The control keeps m1 because that is what its
# existing numbers were taken on; the paper arms take m1f for the reason in
# set_arm. This is a table rather than a flag so that no invocation can pair an
# arm with the wrong file by forgetting an argument.
declare -A ARMMIX=(
  # EXP-106 form (agent class promised 30 s end-to-end, restated as 52 ms/token
  # for the policies that read the field literally). Kept so that experiment can
  # still be re-run from this file.
  [polyserve]=m1
  [polyservep]=m1f
  [polyservep_noadm]=m1f
  [polyservep_nopool]=m1f
  # EXP-108 form (agent class promised TTFT 7 s + 75 ms/token). FluidServe takes
  # the file whose swe tbt_ms is the tier key 25; the other two take the file
  # that states 75, which is what they read as the budget.
  [fsv3capgnofrct75]=t75
  [fsv3gnofrct75nocap]=t75
  [fsv3capgnofrct75dl]=t75
  [polyservept75]=t75fair
  [slot75]=t75fair
  # EXP-116 (2026-09-10). Llumnix load-balance, added so the eight-instance static
  # sweep carries the arm that never rejects. It reads no SLO at all -- neither the
  # scheduler policy nor the stock engine consumes slo.<class>.tbt_ms -- so the
  # field cannot change its decisions, and it gets the same file as the arm it is
  # read against so that the request stream is identical.
  [loadbalance]=t75
  # EXP-118 (2026-09-10). The first-token estimate corrections, one arm per
  # flag and one with both, so that the two can be read apart. The control is
  # fsv3capgnofrct75 in the same session.
  [fsv3ft]=t75
  [fsv3ftq]=t75
  [fsv3ftboth]=t75
  # EXP-119. The other half of the 2x2: the first-token deadline in the
  # feasibility test, with and without the estimate corrections.
  [fsv3dl]=t75
  [fsv3dlftboth]=t75
  # EXP-120. The corrected first-token estimate PLUS a memory brake, which is
  # the pairing EXP-118 showed is required: correcting the estimate removes the
  # over-prediction that was accidentally braking admissions, so something has
  # to brake them on purpose.
  [fsv3ftml]=t75
  [fsv3ftms85]=t75
)

case "${1:-}" in
  smoke)
    # One condition, before anything long. It answers the section 4.1 questions:
    # does the start-up line carry all six switches, does the tier table read 52
    # (which is the proof that m1f was the file the table came from, since m1
    # gives 25), are the new counter series actually stored in the run directory,
    # and is the rejection rate no longer pinned at 0.0%.
    check_stack
    export SESSION_PREFIX="${SESSION_PREFIX_BASE:-exp106}smoke"
    arm=${SMOKE_ARM:-polyservep}
    run_cell "$arm" "${ARMMIX[$arm]}" "${2:-2100}" "${3:-4}" || exit 1
    echo "[exp108] SMOKE DONE $(date -u +%H:%M)"
    ;;
  onerep)
    # One arm, one repeat, all eight rates. This is the entry the chain uses:
    # the repeat is the outer loop and the arms are interleaved inside it, so
    # that a drift over the nineteen hours is spread across arms instead of
    # being charged to whichever arm happened to run last -- and so that when a
    # repeat finishes, every arm has one complete rate curve to look at.
    check_stack
    a=${2:?usage: onerep <arm> <rep> [rates] [min]}
    rep=${3:?rep number}
    RATES=${4:-600,900,1200,1500,2100,2700,3300,4200}
    DUR=${5:-8}
    # Guard the argument before it reaches the runner: --rate-list splits on
    # commas, and a space-separated list dies on the first float conversion
    # forty seconds in, after which the chain reports DONE having run nothing.
    case "$RATES" in *[!0-9,]*) echo "[exp108] ABORT: rates must be comma-separated rpm: '$RATES'"; exit 1 ;; esac
    [ -n "${ARMMIX[$a]:-}" ] || { echo "[exp108] ABORT: arm '$a' has no mix in ARMMIX"; exit 1; }
    SESSION_PREFIX="${SESSION_PREFIX_BASE:-exp114cp36}r${rep}" \
      run_cell "$a" "${ARMMIX[$a]}" "$RATES" "$DUR" \
        || { echo "[exp108] rep$rep $a FAILED"; exit 1; }
    echo "[exp108] rep$rep $a DONE $(date -u +%H:%M)"
    ;;
  arm)
    check_stack
    run_cell "${2:?arm}" "${3:?mix key m1|m2|m3}" "${4:-1200}" "${5:-4}"
    ;;
  *)
    sed -n '2,30p' "$0"; exit 1 ;;
esac
