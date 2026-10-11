#!/bin/bash
# EXP-125 SNAPSHOT of run_exp114_hour36.sh, taken 2026-09-10. The hour driver
# with thirty-six load-generator worker processes, unchanged except for three
# additions:
#
#   fsv3ah1   The KV projection's arrivals term at N=1. Every other setting is
#             written out identically to fsv3capgnofrct75, so the pair differs
#             in exactly one named flag, --fluidserve-arrival-horizons. The arm
#             body is copied verbatim from run_exp124_arrivals.sh, which is the
#             static sweep that measured this term, so the same arm name means
#             the same configuration in both.
#
#   the workload-config table gains fsv3ah1 alongside fsv3capgnofrct75, so the
#   arm reads the same _t75.json the control does. This table has no default
#   branch on purpose: an unregistered arm leaves wcfg empty and the runner
#   refuses to start, rather than falling through to another class's budgets.
#
#   set_arm's clearing list gains the seven variable names added by EXP-114,
#   EXP-118, EXP-120 and EXP-124 that were never added to it, matching the list
#   run_exp124_arrivals.sh already carries. This changes no existing arm: every
#   arm that wants one of these sets it again in the case below, and every arm
#   that does not want one never set it. It removes the dependence on the chain
#   invoking this file once per arm.
#
# The original header follows.
#
# EXP-107 SNAPSHOT of run_exp105_swebudget.sh, taken 2026-08-27. Three arms
# added -- `fsv3cap`, `fsv3nofrc`, `fsv3capnofrc` -- and the new environment
# variables added to set_arm's clearing list. The control is EXP-104's fsv3
# runs already on disk (swe budget stays at 30 s: FS_SWE_E2E_MS unset here and
# FS_SWE_E2E_S unset in the analysis).
#
#   fsv3cap       v0.3 + the class-instance cap: per class, the number of
#                 instances whose pace gate the class sets is bounded by a
#                 demand-derived limit, and at the limit the class is placed
#                 only on its most-loaded gate holders so the surplus holders
#                 drain within one residence time.
#   fsv3nofrc     v0.3 + the force branch off: a request no instance can take
#                 within the protections, and that can no longer wait, is
#                 rejected explicitly (shed reason no_feasible) instead of
#                 being placed onto the least-damaging instance anyway.
#   fsv3capnofrc  both.
#
# ⚠ The three arms pin FS_INSTANCE_CAP / FS_FORCE / FS_CAP_WINDOW_MULT
# explicitly, and ONLY these arms do: the flags exist only in the EXP-107
# binary, and pinning them into an arm that might run on an older binary is
# the EXP-48/49 CrashLoopBackOff trap (pflag exits on an unknown flag).
#
# EXP-105 SNAPSHOT of run_exp104_v03affinity.sh, taken 2026-08-27. Two arms
# added, `fsv3b40` and `fsv3noaffb40`: v0.3 with the class preference on and off,
# with swe's end-to-end budget raised from 30 to 40 seconds.
#
# ⚠ THE ARM NAMES CARRY THE BUDGET ON PURPOSE. A results directory named `fsv3`
# would not say which budget scored it, and a run scored at 40 s cannot be
# compared with one scored at 30 s on any swe number. The budget has to move in
# TWO places at once -- the policy's --fluidserve-class-budgets, set here through
# FS_SWE_E2E_MS, and the scorer's SLO_RULES, set through FS_SWE_E2E_S -- and if
# only one moves, the scheduler and the analysis judge the same requests against
# different budgets and neither says so.
#
# EXP-104 SNAPSHOT of run_exp103_interleave.sh, taken 2026-08-26 for FluidServe
# v0.3. Two arms added (`fsv3`, `fsv3noaff`) and ONE STRUCTURAL CHANGE: every arm
# now starts from the v0.2 configuration written out explicitly.
#
# Why that change. v0.3 moves four compiled defaults -- affinity-metric to
# `count`, per-instance-correction, memory-uses-pace-cap and
# prefill-interleave-aware to true. set_scheduler_profiling.py REMOVES an
# ablation flag the invocation did not ask for, returning it to the compiled
# default, so after the flip every arm that does not name those four would
# silently acquire the new behaviour and stop meaning what its existing results
# mean. That is the failure this repository has hit twice already: one arm name
# standing for two configurations depending on when it ran.
#
# So set_arm writes the v0.2 values first and each arm overrides what it needs.
# Every arm is then immune to a default moving under it, in either direction.
#
# EXP-103 SNAPSHOT of run_exp101b_truth.sh, taken 2026-08-26. One arm added:
# `fsinterleave`. Nothing else changed.
#
# EXP-101b SNAPSHOT of run_exp101_delay.sh, taken 2026-08-26 after the control
# condition showed that the scheduler cannot observe the quantity its first-token
# test predicts. Same arms; the difference is the binary, which now logs what
# each placement predicted so the prediction can be joined offline against the
# client's first-token latency.
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
  # EXP-113. The same hour on Qwen2.5-72B, whose answers are 1.46x longer than
  # Llama-3.1-70B's on this workload, so the same offered rate is 1.5x the work.
  # Measured (EXP-112, one repeat): FluidServe's 90% crossing falls from 28.4 to
  # 18.3 req/s. Replayed unchanged, `shift` would put the hour at 1.50x capacity
  # on average with 78% of its minutes above the crossing, against 0.96x and 45%
  # on Llama -- the under-loaded half of the trace, and with it every transition
  # into and out of overload, would disappear and every arm would spend the hour
  # rejecting. `shiftq` is that trace thinned to 0.644 of its arrivals, which is
  # the measured capacity ratio, so it sits where the original sat: 0.95x mean,
  # 1.62x peak, 44% of minutes above. Thinning keeps the timeline, the burst
  # locations and the class mix (65.4 -> 65.2 / 21.5 -> 21.6 / 13.0 -> 13.2%)
  # and drops requests; stretching time instead would have made the hour 93
  # minutes and moved the mix-shift schedule with it.
  [shiftq]=/work/traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045_q064.csv
  # EXP-114. The same hour upscaled 6.20x for the eight-instance Llama-3.1-8B
  # fleet, whose knee is 173.5 req/s. The band is then 0.35x-1.61x with a mean
  # of 0.97x and 46% of minutes above the knee, which is where the Llama-70B
  # hour sat on its own fleet (0.35x-1.61x, 0.97x, 45%). Built with
  # traces/upscale_trace_keepdur.py rather than scale_trace.py, whose upscale
  # path truncates to the original request count and would have turned the
  # hour into ten minutes; the mix schedule changes every fifteen minutes and
  # the KV pool takes about 150 s to fill, so a short hour is a different
  # experiment. Class mix preserved to 0.006 percentage points.
  [shift62]=/work/traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045_x620.csv
  # EXP-114: 5.00x instead of 6.20x, because at 6.20x the load generator could
  # not drive the arm that admits -- see the x500 workload config for the numbers.
  [shift50]=/work/traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045_x500.csv
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
  # Same file as `shift`: thinning changed the arrival times only. The class
  # mix, the SLO block and the transcript are properties of this config.
  [shiftq]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045.json
  [shift62]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045.json
  [shift50]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045.json
)
declare -A WCFG_FAIR=(
  [full]=/work/workload_configs/mix_dyn60_short_m123_slofair.json
  [fullb]=/work/workload_configs/mix_dyn60_short_m123_b1045_slofair.json
  [azcode]=/work/workload_configs/mix_azcode_w60_m1_slofair.json
  [shift]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045_slofair.json
  [shiftq]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045_slofair.json
  [shift62]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045_slofair.json
  [shift50]=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045_slofair.json
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
  # EXP-109. The PolyServe mechanism switches, cleared for the same reason as the
  # FluidServe ones below: set_scheduler_profiling.py removes a switch it was not
  # asked to set, but only the shell can stop one arm's export from reaching the
  # next. run_exp107_capforce.sh does not clear these because it predates them.
  unset PS_ADMISSION_BINDS PS_STEADY_NO_PREFILL PS_PROMOTION \
        PS_PARTITION PS_PREFER_LOADED PS_KV_ADMISSION PS_MIX_CONFIG
  unset FS_PEND FS_SHED FS_AFFINITY FS_AFFINITY_WEIGHT FS_AFFINITY_METRIC FS_CLASS_PIN \
        FS_FLUX FS_CLASS_HARM FS_HORIZON FS_Z FS_FORCE_MARGIN \
        FS_OWN_BUDGET_GATE FS_KV_SLOPE FS_GATE_SLACK \
        FS_PER_INSTANCE_CORR FS_MEMORY_PACE_CAP FS_DEADLINE_FEASIBLE \
        FS_PER_INSTANCE_DELAY FS_DEADLINE_USES_DELAY \
        FS_SHED_NO_FIRST_TOKEN FS_PREFILL_INTERLEAVE FS_SWE_E2E_MS \
        FS_INSTANCE_CAP FS_CAP_WINDOW_MULT FS_FORCE FS_SWE_TBT_MS FS_PREFIX \
        FS_CAP_COST FS_MEM_LEVEL FS_MEM_SAFETY FS_PREFILL_FULLITER \
        FS_PREFILL_RESIDENTQ FS_ARRIVAL_HORIZONS FS_CHAT_TBT_MS FS_DR_TBT_MS
  # EXP-125. The seven names on the three lines above were added by EXP-114,
  # EXP-118, EXP-120 and EXP-124 and were NOT in this list when those arms were
  # written. The chain runs one arm per invocation of this file, so nothing
  # carried over in practice; clearing them here removes the dependence on that
  # fact. It changes no existing arm, because every arm that wants one of them
  # sets it again in the case below and every arm that does not want one never
  # set it. run_exp124_arrivals.sh carries the same list for the same reason.
  # Then write the v0.2 configuration out, so that no arm depends on a compiled
  # default. An arm wanting v0.3 behaviour overrides these below; an arm that
  # does not is pinned to what its existing results were measured under.
  export FS_AFFINITY_METRIC=share FS_PER_INSTANCE_CORR=false \
         FS_MEMORY_PACE_CAP=false FS_PREFILL_INTERLEAVE=false
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
    # EXP-103. fsboth plus the first-token estimate stretched by the engine's
    # measured prefill duty cycle.
    #
    # What EXP-101b measured, over the 15,219 deep research placements of one
    # mix-shift hour, joining the scheduler's placement lines to the client's
    # own first-token latency:
    #
    #   the estimate ORDERS the requests almost correctly -- Spearman +0.616
    #   against the post-dispatch time and +0.850 for waited-plus-estimate
    #   against the whole first-token time -- and its SCALE is short in the
    #   tail: realised over predicted is 0.59 at the median and 1.99 at p90,
    #   and the two p90s stand at 6,959 ms predicted against 14,332 realised.
    #
    #   So none of the 2,476 placements that took ten seconds or more was
    #   foreseen: the estimate simply never crosses the ten-second budget,
    #   0.0% of deep research placements, while 16.3% of them are late.
    #
    #   Cut by that score, the achievable operating points are real: refusing
    #   the worst 16.3% catches 69.7% of the late work and the worst 20.0%
    #   catches 84.9%, against 16.3% and 20.0% for refusing at random.
    #
    # The correction is not a constant. The estimate prices prefill STEP time,
    # so it answers how much engine prefill time the work costs rather than
    # when the first token appears, and the difference is the decode the engine
    # interleaves. The duty cycle is that share, is measured per instance, and
    # is already published: 0.43 to 0.46 on an instance carrying a queue over
    # 10,000 tokens, whose reciprocal is 2.2 to 2.3 against the measured 2.06.
    #
    # ⚠ The flag exists only in the binary md5 2b357f00, deployed 2026-08-26.
    fsinterleave) policy=fluidserve; export FS_AFFINITY_METRIC=count FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true \
                                          FS_PREFILL_INTERLEAVE=true ;;
    # EXP-104. FluidServe v0.3 as it will ship, and the same with the class
    # preference switched off.
    #
    # fsv3 names the four defaults v0.3 moves, even though they are now the
    # compiled defaults, so the arm states its configuration instead of
    # inheriting it. That is the whole point of the block above.
    #
    # fsv3noaff removes the class preference. --fluidserve-enable-affinity=false
    # takes away BOTH places it acts: the ordering of the feasible set by how
    # much of the class an instance already holds, and the class term in the
    # damage estimate. The sort falls back to free space, which is a
    # load-balancing rule, so this arm is "v0.3 with the routing fully mixed".
    #
    # It is asked again at v0.3 because the answer moved once already: on the
    # mix-shift hour the preference cost 5.2 points before EXP-98's two changes
    # and gained 1.3 after them, so it is not a property of the workload alone
    # but of what the rest of the policy is doing. And v0.3 changes the first
    # token estimate, which is what decides how long a request is held, so the
    # queue that the preference builds on one instance is now priced
    # differently.
    fsv3)        policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true ;;
    fsv3noaff)   policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_AFFINITY=false ;;
    # EXP-105. v0.3 with swe's end-to-end budget at 40 s instead of 30.
    #
    # Why. The three classes' nominal per-token budgets are chat 50 ms, swe
    # about 60 (the 30-second end-to-end budget over an expected 498 tokens) and
    # deep research 100. The first two are ten milliseconds apart, and EXP-104
    # measured what that does: isolation runs as a CHAIN in budget order, chat
    # withdraws to two instances, and then swe -- still nearly as tight as chat
    # -- drags deep research down on the remaining two, so the sort separates
    # them again and swe is the one left over. Its effective instance count in
    # the chat-77% segment is 1.53 and 2.02 against deep research's 2.90 and
    # 2.92, although deep research is twice as numerous there, and it pays with
    # 715 more rejected requests over the hour.
    #
    # At 40 s swe's nominal becomes about 80 ms, so the budgets read 50 / 80 /
    # 100 and mixing chat with swe costs 30 ms of an instance's admissible pace
    # rather than 10. If the chain is what EXP-104 says it is, the second link
    # should weaken.
    #
    # ⚠ It also makes swe EASIER: of the swe requests admitted in EXP-104, 11.1%
    # exceed 30 s and only 2.3% exceed 40. There is a third as much room to move,
    # so this is a sensitivity condition about where the budgets sit, not a
    # better-scoring configuration. Its swe numbers are not comparable with any
    # run scored at 30 s.
    fsv3b40)      policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_SWE_E2E_MS=40000 ;;
    fsv3noaffb40) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_SWE_E2E_MS=40000 FS_AFFINITY=false ;;
    # EXP-107. Each arm is the fsv3 block above plus exactly its own change,
    # with the OTHER new flag pinned to the shipped value -- the arm states its
    # configuration instead of inheriting a compiled default, the same rule the
    # block comment at the top of set_arm gives.
    fsv3cap)      policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=true ;;
    fsv3nofrc)    policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=false FS_FORCE=false ;;
    fsv3capnofrc) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false ;;
    # EXP-107 revision (binary 08f9e9ce): the cap without the scarcity rescale
    # and with the guardrail clause -- the limit is the class's own demand, and
    # it binds only while some other class's gate count is short of ITS demand.
    # Same environment as fsv3cap ON PURPOSE: the arms differ by binary, and
    # the arm name is what records which mechanism scored the run.
    fsv3capg)     policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=true ;;
    fsv3capgnofrc) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false ;;
    # EXP-107I (final): the candidate configuration with swe's end-to-end budget
    # at 40 s. The b40 suffix follows EXP-105's rule -- the budget moves in the
    # policy (FS_SWE_E2E_MS) here AND in the scorer (FS_SWE_E2E_S=40) at
    # analysis time, and the arm name records which budget scored it. Its
    # budget-matched control is EXP-105's fsv3b40 runs, NOT the 30 s arms.
    fsv3capgnofrcb40) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_E2E_MS=40000 ;;
    # EXP-107T. The adopted configuration with the agent class's SLO in
    # PER-TOKEN form: budget 25:decode:75 (tier key stays 25 -- it names the
    # class in every profile; the third field is the real budget) and TTFT
    # 7,000 ms via the _t75 workload config. Scoring: ttft<=7s AND mean
    # per-token <=75 ms; the t75 suffix records the form, per the b40 rule.
    fsv3capgnofrct75) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    # EXP-125. The KV projection's arrivals term at window N=1. The projection
    # prices what the resident set will generate and what completions release,
    # and had no term for the prompts routed in DURING the horizon; this adds
    # the mean prompt mass this instance was given per horizon over the last N
    # horizons. N is a window and not a gain -- the term is a mean per horizon,
    # so N changes how much noise is averaged away and not how much is charged.
    # Zero, the compiled default, disables the term, which is why the control
    # below emits no --fluidserve-arrival-horizons flag at all and its meaning
    # is unchanged by this arm existing.
    #
    # Copied verbatim from run_exp124_arrivals.sh, the static sweep that
    # measured it, so that the arm name means one configuration in both files.
    # Every other flag is written out identically to fsv3capgnofrct75 above, so
    # the pair differs in exactly one named setting.
    fsv3ah1) policy=fluidserve; export FS_ARRIVAL_HORIZONS=1 FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    # EXP-126. Both budget halves on the hour trace. The per-token budgets reach
    # the policy through --fluidserve-class-budgets (25:decode:38, 50:decode:25,
    # 100:decode:50 -- tier key : form : budget) and the first-token budgets
    # through the workload file wired below. The scorer is told the same six by
    # FS_*_TBT_MS / FS_*_TTFT_S at analysis time, and the chain refuses to
    # deploy if the three sources disagree.
    fsv3capgnofrcc25d50s38ftc2500d5000s3500) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=38 FS_CHAT_TBT_MS=25 FS_DR_TBT_MS=50 ;;
    # The line above plus FS_ARRIVAL_HORIZONS=1 and nothing else, so the pair
    # differs in exactly one named flag: --fluidserve-arrival-horizons.
    fsv3ah1c25d50s38ftc2500d5000s3500) policy=fluidserve; export FS_ARRIVAL_HORIZONS=1 \
                                          FS_PREFIX=true FS_FORCE_MARGIN=false \
                                          FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=38 FS_CHAT_TBT_MS=25 FS_DR_TBT_MS=50 ;;
    # PolyServe with the paper mechanisms, unchanged from polyservept75. It sets
    # no FS_ variable: it takes the promise entirely from the workload file, and
    # that file also fixes the tier boundaries it bins classes on, which the
    # halved budgets therefore move to chat 25 / swe 38 / deepresearch 50.
    polyservepc25d50s38ftc2500d5000s3500) policy=polyserve; export PS_ADMISSION_BINDS=true \
                     PS_STEADY_NO_PREFILL=true PS_PROMOTION=true \
                     PS_PARTITION=elastic PS_PREFER_LOADED=true \
                     PS_KV_ADMISSION=true ;;
    fsv3capgnofrct75cc) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_COST=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_SWE_TBT_MS=75 ;;
    # EXP-114. The memory predicate as a level test: kvLogical + cost against
    # capMem instead of proj + cost. The projection prices departures and not
    # arrivals, so it forecasts a drain the arrivals then fill -- measured, an
    # instance moves by 46,087 generated + 158,216 admitted - 204,988 released =
    # -686 over a horizon while the model predicts -104,647. That optimism is
    # 5.3% against a 5% safety factor, the two cancel, and 40.6% of routes landed
    # past the engine's measured 0.973 preemption onset. Every other flag is
    # written out identically to fsv3capgnofrct75, so the pair differs in one.
    fsv3capgnofrct75ml) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_MEM_LEVEL=true \
                                          FS_SWE_TBT_MS=75 ;;
    # The same with the occupancy target lowered from 0.95 to 0.85 of the
    # physical pool, which is the third point of the sweep: the measured
    # preemption onset is 0.973-0.998, so 0.95 already sits below it and 0.85
    # buys more distance at the cost of admissions. Two points give a slope, and
    # the slope is the exchange rate the decision needs.
    fsv3capgnofrct75ml85) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_MEM_LEVEL=true FS_MEM_SAFETY=0.85 \
                                          FS_SWE_TBT_MS=75 ;;
    # EXP-114. The control with the first-token deadline made a feasibility
    # condition. Every other flag is written out identically, so the pair differs
    # in exactly one.
    fsv3capgnofrct75dl) policy=fluidserve; export FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false \
                                          FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true \
                                          FS_MEMORY_PACE_CAP=true FS_PREFILL_INTERLEAVE=true \
                                          FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false \
                                          FS_DEADLINE_FEASIBLE=true \
                                          FS_SWE_TBT_MS=75 ;;
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
    # EXP-109. The hour trace in the per-token form of the agent class's promise.
    # The t75 suffix records the form, as the b40 suffix records a budget: these
    # three are the SAME policies as polyserve / slo / vllmcache above, on a
    # workload file that promises 75 ms per token instead of restating a 30 s
    # end-to-end budget as 52. One name covering both would make two sets of
    # numbers that cannot share a table look like repeats of each other.
    polyservept75)  policy=polyserve; export PS_ADMISSION_BINDS=true \
                     PS_STEADY_NO_PREFILL=true PS_PROMOTION=true \
                     PS_PARTITION=elastic PS_PREFER_LOADED=true \
                     PS_KV_ADMISSION=true ;;
    slot75)      policy=slo ;;
    # EXP-126. Llumnix's SLO-aware variant at the halved budgets. It takes the
    # promise entirely from the workload file -- it has no way to be told a
    # budget on the command line -- so the arm name is the only record of which
    # budgets it was scored under, and the file is the same halved one PolyServe
    # reads.
    slotc25d50s38ftc2500d5000s3500)  policy=slo ;;
    # The vLLM router reads neither budget -- its request carries an id and the
    # prompt tokens and nothing else -- so the suffix here records only which
    # file scored it, not a change in what it does. It is in this sweep because
    # its existing hour runs are on the fullb trace, not this one.
    vllmcachet75) policy=vllm-cache ;;
    # EXP-128. The vLLM router's default policy at the halved budgets. Like the
    # other three baselines it takes its promise entirely from the workload file.
    # It has no admission control at all, so its rejection rate is 0.0% by
    # construction and its failures appear as requests that never finish -- which
    # is why this arm is read on the all-arrivals denominator.
    vllmcachec25d50s38ftc2500d5000s3500) policy=vllm-cache ;;
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
  # EXP-109. Giving up is not enough: the Job keeps running, and the next arm
  # then starts a second runner on the same fleet. That happened on 2026-09-01 --
  # the vLLM router's hour was still draining at 300 minutes, the chain moved on,
  # and repeat 2's FluidServe began measuring a cluster that had another hour's
  # backlog on it. Delete the Job here so that "the driver gave up" and "the load
  # stopped" are the same event.
  echo "[wait_job] ABORT: $job did not finish within $2 minutes -- deleting it"
  echo "[wait_job] a runner that outlives its deadline means the arm did not"
  echo "[wait_job] terminate on its own; its result directory is NOT usable and"
  echo "[wait_job] the next arm must not start on top of it."
  kubectl -n llumnix logs "job/$job" --tail=20 2>/dev/null | sed 's/^/[wait_job]   /'
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  # And wait for its pod to actually go, so the next arm does not overlap it.
  for i in $(seq 1 30); do
    kubectl -n llumnix get pods -l job-name="$job" --no-headers 2>/dev/null | grep -q . || break
    sleep 10
  done
  return 1
}

# A runner that is still going long after an hour trace should have finished is
# the shape of the failure above, and it is invisible while it is happening: the
# Job says Running and the chain says nothing until its deadline. This prints a
# line the moment it crosses, so the log carries the warning hours before the
# deadline does.
warn_if_overrun() {  # $1 job name, $2 expected minutes
  local job=$1 want=$2
  ( while kubectl -n llumnix get job "$job" >/dev/null 2>&1; do
      local age
      age=$(kubectl -n llumnix get job "$job" -o jsonpath='{.metadata.creationTimestamp}' 2>/dev/null)
      [ -n "$age" ] || break
      local mins=$(( ( $(date -u +%s) - $(date -u -d "$age" +%s) ) / 60 ))
      if [ "$mins" -gt "$want" ]; then
        echo "[overrun] $job has been running ${mins} min, expected about ${want}."
        echo "[overrun] An hour trace that does not end is a policy with a backlog"
        echo "[overrun] it never drains. Check: kubectl -n llumnix logs job/$job --tail=5"
      fi
      sleep 600
    done ) &
}

run_cell() {  # $1 arm, $2 variant (ablation|full)
  local arm=$1 variant=$2
  local trace=${TRACE[$variant]:-} wcfg=${WCFG[$variant]:-}
  if [ "$arm" = "slo" ] && [ -n "${WCFG_FAIR[$variant]:-}" ]; then
    wcfg=${WCFG_FAIR[$variant]}
  fi
  # EXP-109. One promise, two files. FluidServe gets 75 ms through
  # --fluidserve-class-budgets 25:decode:75 and keeps tbt_ms 25 as the TIER KEY;
  # the other four read tbt_ms literally, so theirs states 75. A table rather
  # than a chain of if-statements, so that adding an arm cannot silently fall
  # through to the wrong file.
  #
  # EXP-113: the file also has to name the trace being replayed. The runner
  # refuses to start when the workload's class_plan_file and --trace-file
  # disagree -- the class of each arrival is a COLUMN of the trace, so the two
  # must be the same file -- and it caught exactly that on the first attempt
  # here, when the thinned trace was paired with the unthinned plan. The suffix
  # is therefore keyed on the variant as well as the arm.
  local qsuf=""
  [ "$variant" = "shiftq" ]  && qsuf="_q064"
  [ "$variant" = "shift62" ] && qsuf="_x620"
  [ "$variant" = "shift50" ] && qsuf="_x500"
  case "$arm" in
    fsv3capgnofrct75|fsv3capgnofrct75dl|fsv3capgnofrct75cc|fsv3capgnofrct75ml|fsv3capgnofrct75ml85|fsv3ah1)
      wcfg=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045${qsuf}_t75.json ;;
    polyservept75|slot75|vllmcachet75|llmdslot75)
      wcfg=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045${qsuf}_t75fair.json ;;
    fsv3capgnofrcc25d50s38ftc2500d5000s3500|fsv3ah1c25d50s38ftc2500d5000s3500)
      wcfg=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045${qsuf}_ftc2500d5000s3500.json ;;
    polyservepc25d50s38ftc2500d5000s3500|slotc25d50s38ftc2500d5000s3500|vllmcachec25d50s38ftc2500d5000s3500)
      wcfg=/work/workload_configs/mix_dyn60_shift_m2Am1B_b1045${qsuf}_c25d50s38ftc2500d5000s3500fair.json ;;
  esac
  # And the tier table PolyServe bins on is derived from the same file the runner
  # loads, so one key moves both.
  export PS_MIX_CONFIG="${wcfg##*/}"
  [ -n "$trace" ] || { echo "[exp30] unknown variant '$variant'"; return 1; }
  [ -s "${HOSTWORK}${trace#/work}" ] || { echo "[exp30] missing $trace"; return 1; }
  [ -s "${HOSTWORK}${wcfg#/work}" ]  || { echo "[exp30] missing $wcfg"; return 1; }
  local job="bench-runner-exp30-${arm}"
  local session="${SESSION_PREFIX:-exp30}_${arm}_${variant}"
  set_arm "$arm" || return 1
  kubectl -n llumnix delete job "$job" --ignore-not-found >/dev/null
  sed -e "s/__JOBNAME__/$job/" -e "s/__SESSION__/$session/" -e "s/__ARM__/$arm/" \
      -e "s#__TRACE__#$trace#" -e "s#__WCFG__#$wcfg#" \
      runner-exp114dyn-p36.template.yaml | kubectl apply -f - >/dev/null
  echo "[exp30] $arm/$variant launched $(date -u +%H:%M:%S)"
  warn_if_overrun "$job" 100
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
    # EXP-107: the smoke arm comes from the environment, because the smoke has
    # to exercise the arm under test (fsv3cap and its metric plumbing), not the
    # v0.2 arm this case was written for.
    check_stack
    SESSION_PREFIX="${SESSION_PREFIX_BASE:-exp30}smoke" run_cell "${SMOKE_ARM:-fluidserve}" ablation
    ;;
  onerep)
    # EXP-109. One arm, one repeat of the hour trace. This is the entry the chain
    # uses: the repeat is the outer loop and the five arms are interleaved inside
    # it, so an hour of machine drift is spread across arms instead of being
    # charged to whichever ran last, and a finished repeat leaves every arm with
    # one complete hour to read.
    check_stack
    a=${2:?usage: onerep <arm> <rep> [variant]}
    rep=${3:?rep number}
    v=${4:-shift}
    SESSION_PREFIX="${SESSION_PREFIX_BASE:-exp109}r${rep}" run_cell "$a" "$v" \
      || { echo "[exp30] rep$rep $a FAILED"; exit 1; }
    echo "[exp30] rep$rep $a DONE $(date -u +%H:%M)"
    ;;
  ablation|full|fullb|azcode|shift|shiftq|shift62|shift50)
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
