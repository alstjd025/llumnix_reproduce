#!/bin/bash
# EXP-102 SNAPSHOT of run_exp101_delay.sh, taken 2026-08-26 while the EXP-101
# control condition was still running, so that driver stays untouched -- bash
# reads a script by file offset as it executes, so editing one mid-run makes it
# jump into a different block.
#
# One arm added: `fsshednoft`. Nothing else changed.
#
# EXP-101 SNAPSHOT of run_exp100_deadline.sh (itself a snapshot chain back to
# run_exp30_dynamic.sh), taken 2026-08-26 so the original stays editable while
# this runs. Two arms added: `fsdelay` and `fsdeadfix`. Nothing else changed.
#
# EXP-97 SNAPSHOT of run_exp93_shift.sh (itself a snapshot of run_exp30_dynamic.sh),
# taken 2026-08-23 so the original stays editable while this runs. One variant
# added: `shift`, the mix-shift stress trace. Nothing else changed.
#
# EXP-30: the dynamic trace. Rate and mix both move, on the shortened workload.
#
#   ./run_exp30_dynamic.sh smoke                 one arm, the 9-minute flat-rate
#                                                mix cycle, to prove the plumbing
#   ./run_exp30_dynamic.sh ablation [reps]       three arms x the 9-minute flat-rate
#                                                mix cycle: mix moves, rate does not
#   ./run_exp30_dynamic.sh full [reps]           three arms x the 1-hour Azure trace
#
# Why this experiment exists. Every result so far was measured at a fixed rate
# with a fixed mix, and a stationary workload cannot separate a policy that
# adapts from one that does not, because nothing has to be adapted to. Measured,
# PolyServe's partition changes once or twice in an eight-minute condition and
# every change is in the start-up transient, so on a static mix its allocation
# is simply correct and stays correct.
#
# Two traces, varying different things:
#
#   dyn09_short_mixcycle_flat   rate flat at 40 req/s, mix cycles m1-m2-m3-m1 on
#                               two-minute segments. Isolates the mix.
#   dyn60_short_m123            rate 25-75 req/s from four days of Azure minutes,
#                               mix cycles on fifteen-minute segments, one hour.
#                               Both move; this is the workload the design is for.
#
# Reading them together is the point: if the flat-rate run shows no difference
# and the hour-long one does, the effect is the rate rather than the mix, and
# the claim to make is about admission control rather than about partitioning.
#
# Arm is the inner loop and repeat the outer, as always: between-session movement
# on this workload is 0.1 points at 20 req/s and 4.6 at 80 (five PolyServe runs
# across three sessions), which is the same size as the differences under test,
# so arms measured in different sessions cannot be compared.
set -uo pipefail
cd "$(dirname "$0")"
REPO=/home/nxclab/llumnix_reproduce
HOSTWORK=/home/nxclab/llumnix_reproduce/Agent_applications/agent_motivation_experiment
META=../../results/exp07_meta
mkdir -p "$META"

declare -A TRACE=(
  [ablation]=/work/traces/dynamic/canonical/dyn09_short_mixcycle_flat.csv
  [full]=/work/traces/dynamic/canonical/dyn60_short_m123.csv
  # EXP-71. Same hour, arrival band 25-75 -> 10-45 req/s. EXP-70 put the knee at
  # 28.1 req/s for v0.2 and 18.2 for llm-d, and on the old band 93% of the hour
  # sat above ours, so the rate never crossed it. Here it is above 47% of the
  # minutes and below the rest.
  [fullb]=/work/traces/dynamic/canonical/dyn60_short_m123_b1045.csv
  [azcode]=/work/traces/dynamic/canonical/azcode_w60_m1.csv
  # EXP-93. The mix-shift stress trace. Same arrival band and, because the
  # generator was given the same seed, the SAME ARRIVAL TIMES as fullb -- the
  # two CSVs are identical in arrival_s and differ only in the class label of
  # 46,632 of the 99,242 rows. The schedule is m2,A,m1,B, so chat goes
  # 93.0 -> 33.3 -> 76.9 -> 60.0 percent of the requests and the run reaches
  # the even region, which fullb (66.7-93.0%) never does.
  [shift]=/work/traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv
)
# The SLO filter has no end-to-end mode and reads tbt_ms literally, so on the
# stock config it judges the agent class 2.3x tighter than FluidServe judges it
# and rejects ~98% of it (EXP-28). Each variant therefore has a "fair" config
# that restates that class as (2500, 52); scoring is unaffected because the
# analysis judges it end to end whatever the config says. Chosen by ARM, the
# same way run_exp27_mixsweep picks m1 or m1f.
declare -A WCFG=(
  [ablation]=/work/workload_configs/mix_dyn09_short_mixcycle_flat.json
  [full]=/work/workload_configs/mix_dyn60_short_m123.json
  [fullb]=/work/workload_configs/mix_dyn60_short_m123_b1045.json
  [azcode]=/work/workload_configs/mix_azcode_w60_m1.json
  [shift]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045.json
)
declare -A WCFG_FAIR=(
  [full]=/work/workload_configs/mix_dyn60_short_m123_slofair.json
  [fullb]=/work/workload_configs/mix_dyn60_short_m123_b1045_slofair.json
  [azcode]=/work/workload_configs/mix_azcode_w60_m1_slofair.json
  [shift]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045_slofair.json
)
SHORT_TRANSCRIPT="$HOSTWORK/workloads/codingagent_request_level_poisson/data/transcript_swe_short7k_mix1500.jsonl"

check_stack() {
  local th bin mig extra
  th=$(kubectl -n llumnix get deploy scheduler -o yaml \
       | grep -A1 -- "--admission-kv-usage-threshold" | tail -1 | tr -dc '0-9.')
  if [ -n "$th" ] && awk -v t="$th" 'BEGIN{exit !(t>0)}'; then
    echo "[exp30] ABORT: KV admission theta=$th is on"; exit 1
  fi
  bin=$(kubectl -n llumnix get deploy gateway -o jsonpath='{.spec.template.spec.containers[0].command[0]}')
  [ "$bin" = "/exp07bin/gateway-exp10" ] \
    || { echo "[exp30] ABORT: gateway is not the host-built binary ($bin)"; exit 1; }
  mig=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='LLUMNIX_ENABLE_MIGRATION'),'unset'))")
  [ "$mig" = "0" ] || { echo "[exp30] ABORT: engine migration is '$mig', want 0"; exit 1; }
  extra=$(kubectl -n llumnix get lws neutral -o json \
        | python3 -c "import json,sys;c=[c for c in json.load(sys.stdin)['spec']['leaderWorkerTemplate']['workerTemplate']['spec']['containers'] if c['name']=='vllm'][0];print(next((e.get('value','') for e in c.get('env',[]) if e['name']=='SCHED_EXTRA_ARGS'),'unset'))")
  [ -z "$extra" ] || { echo "[exp30] ABORT: engine has SCHED_EXTRA_ARGS='$extra'"; exit 1; }
  [ -s "$SHORT_TRANSCRIPT" ] \
    || { echo "[exp30] ABORT: short transcript missing: $SHORT_TRANSCRIPT"; exit 1; }
  echo "[exp30] stack ok: theta off, gateway=$bin, migration off, engine stock FIFO"
}

set_arm() {
  local policy
  # Clear every ablation variable before the arm sets the ones it wants.
  #
  # This driver loops over arms INSIDE one shell, so an `export` from one arm is
  # inherited by every arm after it. EXP-60 lost its second control condition
  # that way: the arm order is fluidserve, fspin-demand, fluidserve,
  # fspin-demand, and the third condition ran with the class pin the second one
  # had exported. Its per-class result was the pinned signature, not the
  # control's, and the log confirms three pin applications for two pinned arms.
  #
  # set_scheduler_profiling.py's own protection does not cover this: it removes
  # an ablation flag that the invocation did not ASK for, and this invocation
  # did ask, because the variable was in its environment. The same shape as the
  # flag that stuck in the deployment spec for 61 conditions (section 38), one
  # layer further out.
  #
  # run_exp27_mixsweep.sh is not affected while it is called once per condition
  # from a chain script, which is how EXP-59 ran, but it has the same structure
  # and gets the same clearing.
  unset FS_PEND FS_SHED FS_AFFINITY FS_AFFINITY_WEIGHT FS_AFFINITY_METRIC FS_CLASS_PIN \
        FS_FLUX FS_CLASS_HARM FS_HORIZON FS_Z FS_FORCE_MARGIN \
        FS_OWN_BUDGET_GATE FS_KV_SLOPE FS_GATE_SLACK \
        FS_PER_INSTANCE_CORR FS_MEMORY_PACE_CAP FS_DEADLINE_FEASIBLE \
        FS_PER_INSTANCE_DELAY FS_DEADLINE_USES_DELAY FS_SHED_NO_FIRST_TOKEN
  case "$1" in
    # EXP-44. class-harm is pinned in BOTH arms rather than left unset, because
    # set_scheduler_profiling.py now returns an unset ablation to its compiled
    # default and this flag's default is true, while every FluidServe condition
    # from EXP-27 pass 2 to EXP-43 ran with it false (implementation.md 38).
    # Without pinning, the fluidserve arm would differ from EXP-41 in two ways
    # instead of none.
      # FS_PREFIX=false is explicit and must stay that way. The compiled
      # default became true at v0.2, and every results directory named
      # *_fluidserve_* that already exists was produced with it OFF. Leaving
      # this arm on the compiled default would make one arm name mean two
      # configurations depending on when it ran, which is the failure this
      # repository keeps hitting. The arm that exercises the shipped default is
      # `fspfx`, and it sets FS_PREFIX=true explicitly for the same reason.
    fluidserve)  policy=fluidserve; export FS_PREFIX=false FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-71. The deployed default since v0.2: the prefill charge for an
    # arriving prompt is per instance. FS_PREFIX=true is set explicitly even
    # though it is now the compiled default, so the startup line states the
    # configuration rather than leaving it to be inferred, and so this arm keeps
    # meaning one thing if the default ever moves again. Everything else matches
    # the `fluidserve` arm above, which is therefore its prefix-off ablation.
    fspfx)       policy=fluidserve; export FS_PREFIX=true  FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-97. The class preference with its normalisation replaced, on the trace
    # whose class mix moves. classShare divides by the instance's OWN occupancy,
    # so it saturates at 1.0 and instances the class dominates cannot be told
    # apart; `count` divides by the largest count among the candidates instead,
    # so the instance holding most of the class wins until feasibility stops it.
    # ⚠ The flag exists only in the binary deployed for EXP-96 (md5 8faedded).
    fscount)     policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-98. Three arms over the SAME base as fscount -- the class preference
    # ranking by count, which is what EXP-97 measured and what the cluster is
    # deployed with -- so the only difference from that arm's two repeats is the
    # flag named here. Every other export matches fscount exactly; a difference
    # in any of them would put two changes in one comparison.
    #
    #   fsboth      both changes. Run first, because if neither the counters fire
    #               nor the result moves there is no reason to run the other two.
    #   fscorr      the per-instance correction only.
    #   fspacecap   the memory predicate reading min(capKv, capMem) only.
    #
    # They are separated because the two interact: capKv is
    # (allowance / correction - overhead) / cKv, so splitting the correction
    # changes the ceiling that the second flag then makes binding. Measured on
    # the chat-holding engines the correction should rise, which LOWERS capKv and
    # tightens that arm further; on the dedicated deep-research engines it should
    # fall and loosen it. Run together only, the two would be inseparable.
    fsboth)      policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true ;;
    # EXP-100. fsboth plus the first-token deadline in the feasibility test.
    #
    # The feasibility conjunction is three tests about per-token pace and one
    # about KV space. None asks when THIS request would see its own first token,
    # so an instance carrying 130,000 queued prefill tokens passes it: at deep
    # research's loose 100 ms per-token budget the queue lifts the predicted step
    # only to 86.6 ms against a 90 ms gate. The class's real constraint is its
    # 10 s first-token budget, and 99.96% of its violations are that rule.
    #
    # The test already exists and is already computed on every candidate. This
    # arm only lets the conjunction read it.
    #
    # EXP-87 measured exactly this and did not adopt it: the condition fired on
    # 1.1-8.3% of refusals but nothing moved, because deep research had one
    # instance it could go to and a blocked request returned to it as soon as
    # the queue dipped. That premise no longer holds here -- in this trace the
    # other three engines serve the class at 0.0% violation -- which is why it is
    # worth asking again rather than a repeat.
    fsdead)      policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true FS_DEADLINE_FEASIBLE=true ;;
    # EXP-101. Two arms above fsboth, which is the control they are read
    # against and which EXP-98 measured at 76.2 points admitted over two
    # repeats of this same trace.
    #
    # What they change. The first-token deadline test asks whether
    # `waited + prefillMs` exceeds the time-to-first-token budget. prefillMs is
    # the WORK and not the WAIT: it prices this prompt's own prefill given what
    # is queued, computed as though the engine did nothing else, while the
    # backlogged instance spends 45.3% of its steps on prefill and interleaves
    # decode with the rest. Measured on deepresearch over EXP-100's two repeats
    # that estimate reads 9,005 ms against a realised mean of 13,028 ms, under a
    # 10,000 ms budget -- 31% low, and low on exactly the side that lets a
    # placement through. That is why EXP-100, which put the test into the
    # feasibility conjunction unchanged, refused 46,186 candidates and moved
    # admitted attainment by 0.1 points: what it refused was already refused by
    # another condition.
    #
    #   fsdelay     the estimate is corrected. The residual between what the
    #               decision predicted and what the request realised is already
    #               accumulated by the registry and already added on the holding
    #               path in canWait; --fluidserve-deadline-uses-delay adds the
    #               same bound to the deadline test, so the two paths stop
    #               asking the same question with different quantities, and
    #               --fluidserve-per-instance-delay keeps that residual per
    #               instance. The two are one change in two parts: the bound the
    #               second flag adds is a fleet scalar unless the first is set,
    #               and the fleet scalar is what the measurement says is wrong --
    #               its p90 over the four instances of this trace is
    #               1,265 / 1,520 / 2,564 / 20,022 ms, so it understates by an
    #               order of magnitude exactly the instance that is backlogged.
    #               Only the shed path and canWait read the test in this arm.
    #   fsdeadfix   fsdelay plus --fluidserve-deadline-feasible, which is
    #               EXP-100's arm on the corrected estimate. This is the
    #               combination the design intended: a request that cannot see
    #               its first token inside its budget is refused a routed
    #               placement rather than given one and then missing.
    # EXP-102. fsboth with the first-token branch taken OUT of the shed test.
    #
    # The shed test asks whether the placement that would actually be made still
    # meets the request's own budget, and for a class judged on a per-token
    # budget that is the first-token branch OR the pace branch. For deep
    # research the pace branch never fires -- over both EXP-100 repeats 0.0% of
    # its completed requests exceeded its 100 ms per-token budget -- so its
    # 30.0% and 29.6% rejection runs entirely through the first-token estimate.
    #
    # This is the OPPOSITE direction from fsdelay, and which one is right is
    # what EXP-101's joint table decides. Both arms exist so that whichever the
    # table calls for can be run without writing a driver first.
    #
    # It does not turn shedding off. Chat and the agent class keep being judged
    # on the branch that fires for them, which is what separates this from the
    # shed-off ablation EXP-78 ran.
    #
    # ⚠ TWO STEPS ARE STILL MISSING and this arm will not work without them.
    # They were left out on purpose: both touch files the EXP-101 control
    # condition was calling while this snapshot was written, and the measurement
    # path is everything the running sweep calls, not only bin/ and workloads/.
    #
    #   1. Deploy /home/nxclab/tools/staging/scheduler-exp102 (md5 07e539b9).
    #      The flag exists in no other binary, and pflag exits on an unknown
    #      flag, so setting it against the EXP-101 binary is a CrashLoopBackOff
    #      that the driver reports as "reported no policy" -- indistinguishable
    #      from a slow rollout.
    #   2. Add to ms_dev/scripts/set_scheduler_profiling.py:
    #        FLAGS:  "FS_SHED_NO_FIRST_TOKEN": "--fluidserve-shed-ignores-first-token"
    #        checks: ("--fluidserve-shed-ignores-first-token", "shednoft")
    #      Without the first the flag is silently dropped and this arm becomes
    #      its own control; without the second nothing verifies that it applied.
    fsshednoft)  policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true \
                                          FS_SHED_NO_FIRST_TOKEN=true ;;
    fsdelay)     policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true \
                                          FS_PER_INSTANCE_DELAY=true FS_DEADLINE_USES_DELAY=true ;;
    fsdeadfix)   policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true \
                                          FS_PER_INSTANCE_DELAY=true FS_DEADLINE_USES_DELAY=true FS_DEADLINE_FEASIBLE=true ;;
    fscorr)      policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_PER_INSTANCE_CORR=true ;;
    fspacecap)   policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_MEMORY_PACE_CAP=true ;;
    fsa)         policy=fluidserve; export FS_FORCE_MARGIN=true  FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false ;;
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
    # EXP-91: FS_PREFIX pinned true EXPLICITLY. The arm was written before the
    # prefix flag existed and relied on nothing; today the compiled default is
    # true, so behaviour would be the same -- but an arm that depends on a
    # compiled default means one arm name means two configurations depending on
    # the binary, which is the failure this repository keeps hitting. The
    # comparison arm fspfx pins it true for the same reason.
    fsnoaff)     policy=fluidserve; export FS_PREFIX=true FS_AFFINITY=false FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    # EXP-60. The pinned arms, on the trace whose mix moves. EXP-59 measured
    # them at fixed arrival rate and fixed mix, where a well chosen fixed
    # allocation is right for the whole eight minutes by construction, so that
    # experiment could not show what fixing the assignment costs. This trace
    # steps the mix three times, which is the condition the claim is about.
    fspin-poly)   policy=fluidserve; export FS_CLASS_PIN="50:0;100:1,2;25:3" FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    fspin-demand) policy=fluidserve; export FS_CLASS_PIN="50:0,1;100:2;25:3"  FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false ;;
    fsac)        policy=fluidserve; export FS_FORCE_MARGIN=true  FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=true ;;
    # EXP-49 candidate H2: project from the observed rate of change of KV
    # occupancy instead of from the modelled inflow/outflow balance.
    fskv)        policy=fluidserve; export FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false FS_KV_SLOPE=true ;;
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
    slo)         policy=slo ;;
    # EXP-77. The vLLM router's default cache_aware policy. It reads the prompt
    # prefix and the queue depth and nothing else: no budget, no admission test,
    # so it always returns an instance and its rejection rate is zero by
    # construction. It therefore takes the STANDARD workload config rather than
    # the "fair" one -- the fair config exists because the SLO filter reads
    # tbt_ms as a literal per-token target, and this policy never reads the SLO
    # fields at all, so the two configs are the same input to it. Keeping it on
    # the standard config makes it directly comparable to the fspfx arm.
    #
    # The five constants are the compile defaults, which are vllm-router
    # 0.1.15's own values; set_scheduler_profiling.py reads the start-up line
    # back and refuses the condition if they differ or if localaccount is not
    # true. Plan and judgement rule: ms_dev/notes/vllm-router-baseline.md.
    vllmcache)   policy=vllm-cache ;;
    loadbalance) policy=load-balance ;;
    *) echo "[exp30] unknown arm: $1" >&2; return 1 ;;
  esac
  echo "[exp30] switching scheduler -> $policy"
  # EXP-91: the status of this call was being discarded (a pipeline nobody
  # read), and the only check that follows reads the POLICY NAME, which stays
  # correct when an ablation flag fails to apply -- exactly the case the
  # verifier exists to catch. Same fix as run_exp88_route.sh.
  if ! python3 "$REPO/ms_dev/scripts/set_scheduler_profiling.py" --policy "$policy" \
       | sed 's/^/[exp30]   /'; then
    echo "[exp30] ABORT: set_scheduler_profiling.py reported a settings mismatch"
    return 1
  fi
  local pod actual
  pod=$(kubectl -n llumnix get pods -l app=scheduler \
        --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
  actual=$(kubectl -n llumnix logs "$pod" 2>/dev/null \
           | grep -am1 -ao "create scheduler with policy: [a-z-]*" | awk '{print $NF}')
  [ -n "$actual" ] || { echo "[exp30] scheduler pod $pod reported no policy"; return 1; }
  [ "$actual" = "$policy" ] \
    || { echo "[exp30] ABORT: scheduler reports '$actual', wanted '$policy'"; return 1; }
  echo "[exp30] scheduler confirmed policy=$actual (pod $pod)"
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

run_cell() {  # $1 arm, $2 variant (ablation|full)
  local arm=$1 variant=$2
  local trace=${TRACE[$variant]:-} wcfg=${WCFG[$variant]:-}
  if [ "$arm" = "slo" ] && [ -n "${WCFG_FAIR[$variant]:-}" ]; then
    wcfg=${WCFG_FAIR[$variant]}
  fi
  [ -n "$trace" ] || { echo "[exp30] unknown variant '$variant'"; return 1; }
  [ -s "${HOSTWORK}${trace#/work}" ] || { echo "[exp30] missing $trace"; return 1; }
  [ -s "${HOSTWORK}${wcfg#/work}" ]  || { echo "[exp30] missing $wcfg"; return 1; }
  local job="bench-runner-exp30-${arm}"
  local session="${SESSION_PREFIX:-exp30}_${arm}_${variant}"
  set_arm "$arm" || return 1
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" -e "s/__ARM__/$arm/" \
      -e "s#__TRACE__#$trace#" -e "s#__WCFG__#$wcfg#" \
      runner-dyn.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp30] $arm/$variant launched $(date -u +%H:%M:%S)"
  # Poll for EITHER terminal condition. `kubectl wait --for=condition=complete`
  # never returns on a job that FAILS -- it sits until its own timeout -- so a
  # single failed condition used to block the whole chain for five hours. EXP-48
  # rep 2 lost a night that way: engine 8002 did not come back from the cold
  # restart, the runner exited 1, and the sweep waited on a job that was already
  # in state Failed.
  wait_job "$job" 300 \
    || { echo "[exp30] $arm/$variant did not complete"
         kubectl -n llumnix logs "job/$job" --tail=60; return 1; }
  kubectl -n llumnix logs "job/$job" --tail=400 2>/dev/null | grep -aE "Success:|rc=" | tail -3
  kubectl -n llumnix get deploy scheduler -o yaml > "$META/scheduler_deploy_${session}.yaml"
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  echo "[exp30] $arm/$variant DONE $(date -u +%H:%M:%S)"
}

case "${1:-}" in
  smoke)
    check_stack
    SESSION_PREFIX=exp30smoke run_cell fluidserve ablation
    ;;
  ablation|full|fullb|azcode|shift)
    check_stack
    VARIANT=$1
    REPS=${2:-2}
    for rep in $(seq 1 "$REPS"); do
      for arm in ${ARMS:-polyserve slo fluidserve}; do
        SESSION_PREFIX="${SESSION_PREFIX_BASE:-exp30}r${rep}" run_cell "$arm" "$VARIANT" \
          || echo "[exp30] rep$rep $arm/$VARIANT FAILED"
      done
      echo "[exp30] REPEAT $rep DONE $(date -u +%H:%M)"
    done
    echo "[exp30] $VARIANT ALL DONE $(date -u +%H:%M)"
    ;;
  *)
    sed -n '2,12p' "$0"; exit 1 ;;
esac
