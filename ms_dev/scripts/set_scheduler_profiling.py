#!/usr/bin/env python3
"""Load the latency-profiling tables into the live scheduler and switch policy.

`policy.GetLatencyPredictor()` calls klog.Fatalf when either profiling file is
missing or empty, so every SLO-aware policy (`slo`, and PolyServe later) is dead
on arrival without them.  This script installs the tables and flips the policy,
so the failure -- if any -- shows up now rather than at experiment time.

The tables ship as a ConfigMap rather than a hostPath: together they are ~80 KB,
far under the 1 MiB limit, and that keeps the whole thing inside kubectl with no
root on the node.

    # install tables + switch to the SLO policy, wait for rollout
    python3 ms_dev/scripts/set_scheduler_profiling.py --policy slo

    # FluidServe additionally needs its own profile and a gateway that retries
    # quickly, because holding a request is how it expresses "no instance can
    # take this yet"
    python3 ms_dev/scripts/set_scheduler_profiling.py --policy fluidserve

    # put the cluster back the way it was
    python3 ms_dev/scripts/set_scheduler_profiling.py --policy load-balance

    python3 ms_dev/scripts/set_scheduler_profiling.py --show
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time

NS = "llumnix"
DEPLOY = "scheduler"
CM = "llumnix-profiling"
MOUNT = "/profiling"
VOL = "profiling-data"
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TABLE_DIR = os.path.join(REPO, "deploy", "profiling", "llama31-70b-b200-tp2")

# Flags the SLO-aware policies need beyond the profiling paths.  --ttft-slo and
# --tpot-slo are only the fallback for requests that carry no SLO of their own;
# under PolyServe every request brings its own budget via the packed priority.
SLO_FLAGS = {
    "--ttft-slo": "5000",
    "--tpot-slo": "50",
    "--ttft-slo-dispatch-threshold": "0.9",
    "--tpot-slo-dispatch-threshold": "0.9",
    "--ttft-profiling-data-path": f"{MOUNT}/ttft.json",
    "--tpot-profiling-data-path": f"{MOUNT}/tpot.json",
}

# Expected output length per tier, keyed by the tier's TPOT SLO.  PolyServe uses
# this quantity in two places -- the largest KV footprint a tier's admitted set
# can reach (design document section 4.5) and the per-tier demand the
# repartitioner divides the fleet by -- so a value that no longer matches the
# workload moves the partition itself, not merely an admission threshold.
#
# It used to be a hand-written string here, "25:728,50:386,100:275", measured on
# 2026-07-25.  On 2026-07-29 Agent_applications 5fa82f8 changed the search-arena
# report structure from four sections to seven, which took the deep research
# class from 282 to 985 output tokens, and this string was not updated because
# nothing connected it to the workload.  FluidServe carried the identical defect
# in its own length profile; correcting that was the largest single improvement
# in the project's history (static 60 req/s, offered attainment 36.6 -> 56.8,
# implementation.md sections 48 and 49).  The baseline kept the defect six days
# longer only because the same quantity was written down in two places and only
# one of them was regenerated.
#
# It is derived from fluidserve.json now, which is the file both policies read,
# so the two cannot drift apart again and neither policy holds an accuracy
# advantage over the other in any comparison between them.
# Which tier each class lands in.  The tier identity IS the per-token budget the
# request carries, so this table cannot be written down here: it is whatever the
# workload configuration puts in `slo.<class>.tbt_ms`, because that is the field
# the client packs into the request and the scheduler bins on.
#
# It was hard-coded as {"swe": 25, ...} until 2026-08-27, and that was wrong in a
# way no check could see.  The agent class's real objective is end-to-end 30 s;
# 25 ms is the idle decode step time measured in EXP-16, present in the balanced
# mix only as a tier label.  FluidServe is told the real budget separately
# (--fluidserve-class-budgets 25:e2e:30000) and never reads the 25 as a rate.
# PolyServe has no such restatement and took it literally, so it judged the agent
# class against a per-token budget seven to nine times tighter than its own
# objective: at that class's KV footprint of 7,306 tokens the profiling table
# allows a batch of 23 under 25 ms and 186 under the 55.7 ms that 30 s of
# end-to-end budget actually buys.  The effect was invisible only because
# admission was relaxed on the fallback pass and changed no decision at all.
#
# Reading the workload file also ties the two together for the next change: if
# the agent class's end-to-end budget moves from 30 s to 40 s, the restated
# per-token figure in the slofair configuration moves with it and so does the
# tier, without anyone having to remember that this line exists.
# The default stays the balanced configuration, which is what every arm before
# this change ran, so an invocation that does not name a mix keeps deriving the
# tiers those runs used.  The PolyServe arm names one, and it names it from the
# same shell variable that selects the workload the runner loads -- one variable
# moving both places, because a tier table derived from a different file than the
# requests were built from is silent: the requests carry one budget, the length
# table is keyed by another, and every lookup quietly falls back to the default
# output length.
DEFAULT_MIX_CONFIG = os.path.join(
    REPO, "Agent_applications", "agent_motivation_experiment",
    "workload_configs", "mix_short_m1_balanced.json")


def tier_by_class():
    """Read the per-class tier (TPOT SLO in ms) out of the workload config."""
    path = os.environ.get("PS_MIX_CONFIG", DEFAULT_MIX_CONFIG)
    if not os.path.isabs(path):
        path = os.path.join(
            REPO, "Agent_applications", "agent_motivation_experiment",
            "workload_configs", path)
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except OSError as exc:
        sys.exit(f"cannot read the workload configuration {path}: {exc}. "
                 f"The PolyServe tier table is derived from its slo block, so "
                 f"guessing one here would reintroduce exactly the defect that "
                 f"derivation replaced.")
    slo = doc.get("slo") or {}
    out = {}
    for name in ("swe", "chat", "deepresearch"):
        entry = slo.get(name) or {}
        tbt = entry.get("tbt_ms")
        if not tbt:
            sys.exit(f"{path} has no slo.{name}.tbt_ms; without it there is no "
                     f"tier for that class and PolyServe would bin every one of "
                     f"its requests as unrestricted")
        out[name] = int(tbt)
    return out


# The five mechanisms of the paper that the first port left out or inverted, plus
# the memory predicate it never had.  Each is its own environment variable so a
# result can be attributed to one of them rather than to "the new PolyServe", and
# every one defaults to the port's earlier behaviour: an invocation that names
# none of them configures exactly what every PolyServe condition before
# 2026-08-27 ran with.
#
# Full reasoning in ms_dev/notes/polyserve-fidelity.md section 9.
POLYSERVE_ABLATIONS = {
    # Stop Llumnix's second scheduling pass from discarding the admission test
    # when nothing passes it.  Without this the test is computed, logged, and
    # then ignored, which is why the arm refused 0.0% of requests at every rate.
    "PS_ADMISSION_BINDS": "--polyserve-admission-binds",
    # Section 4.5 keeps prefill out of the steady-state iteration estimate and
    # handles it through TTFT in 4.7.  Ours charged a whole queued chunk to both
    # estimates, and a full 8,192-token chunk costs 520-634 ms against tier
    # budgets of 25-100 ms, so no server passed admission whenever any prefill
    # was queued.
    "PS_STEADY_NO_PREFILL": "--polyserve-steady-state-ignores-prefill",
    # Section 4.4.
    "PS_PROMOTION": "--polyserve-lazy-promotion",
    # "demand" (this port's own rate-times-cost allocator) or "elastic" (the idle
    # pool of section 4.3, where a tier takes a server when its requests start
    # pending and returns one when its last server runs empty).
    "PS_PARTITION": "--polyserve-partition",
    # Section 4.3's greedy rule.  Required by "elastic" rather than optional
    # alongside it: least-loaded selection keeps every server partly full, the
    # last server of a tier never runs empty, and nothing is ever released.
    "PS_PREFER_LOADED": "--polyserve-prefer-loaded",
    # Compare the batch section 4.5 simulates forward to against the instance's
    # reported KV capacity.  The port has no memory predicate at all, so the
    # fleet is driven past capacity and the engine recovers by preempting.
    "PS_KV_ADMISSION": "--polyserve-kv-admission",
}


def polyserve_flags():
    """The tier table, derived from the length profile rather than hard-coded."""
    path = os.path.join(TABLE_DIR, "fluidserve.json")
    with open(path) as fh:
        doc = json.load(fh)
    by_name = {c["name"]: c for c in doc.get("classes", [])}
    tiers = tier_by_class()
    missing = sorted(n for n in tiers if n not in by_name)
    if missing:
        sys.exit(f"{path} has no class {missing}; the PolyServe tier table "
                 f"cannot be derived and a stale hard-coded one is exactly the "
                 f"defect this replaced")
    pairs = sorted(tiers.items(), key=lambda kv: kv[1])
    return {"--polyserve-tier-decode-tokens":
            ",".join(f"{tier}:{int(round(by_name[name]['mean']))}"
                     for name, tier in pairs)}

# How each tier's latency budget is defined, which is how the requests are
# actually scored: chat and deepresearch on the mean time between output tokens
# over the whole request, swe on end-to-end latency.  Both forms are cumulative,
# so a request that has been running ahead of its budget is not held to a
# per-token ceiling it does not need.
FLUIDSERVE_FLAGS = {
    "--fluidserve-profile-path": f"{MOUNT}/fluidserve.json",
    # The swe end-to-end budget is settable from the environment because it is a
    # property of the WORKLOAD's service objective rather than of the policy, and
    # a sensitivity condition varies it. Changing it here alone is not enough --
    # the scoring rule has to move with it or the policy and the analysis judge
    # the same requests against different budgets. The scorer takes
    # FS_SWE_E2E_S (in seconds) for that, and prints a banner when it is not 30.
    #
    # EXP-107T. FS_SWE_TBT_MS switches the agent class's budget FORM: instead of
    # an end-to-end budget it gets an explicit per-token one ("25:decode:75"),
    # the same form chat and deepresearch use. The tier key stays 25 -- it names
    # the class in every profile and log -- and the third field carries the real
    # budget, because in bare decode mode the key IS the budget and 25 ms is
    # below this hardware's decode floor. Mutually exclusive with FS_SWE_E2E_MS;
    # scoring must move with it (per-token rule at the same value, stated in the
    # arm name).
    "--fluidserve-class-budgets":
        (f"25:decode:{int(float(os.environ['FS_SWE_TBT_MS']))},"
         if os.environ.get('FS_SWE_TBT_MS') else
         f"25:e2e:{int(float(os.environ.get('FS_SWE_E2E_MS', '30000')))},")
        + "50:decode,100:decode",
    "--fluidserve-horizon-steps": "100",
    "--fluidserve-z-safety": "1.65",
    "--fluidserve-enable-pend": "true",
    "--fluidserve-enable-shed": "true",
    "--fluidserve-enable-affinity": "true",
    "--fluidserve-enable-flux": "true",
}

# Ablation switches, set from the environment so an arm can turn one mechanism
# off without editing this file:
#   FS_PEND=false      place immediately instead of holding
#   FS_SHED=false      place a request that is already certain to miss instead of
#                      rejecting it, so the same requests are lost either way and
#                      the difference is what their capacity cost the others
#   FS_AFFINITY=false  among the instances that can take a request, ignore which
#                      class each is already holding and route on free space
#   FS_AFFINITY_WEIGHT=w  how strongly that preference counts against free
#   FS_AFFINITY_METRIC=share|count  what "most of this class" means. share is
#                         the shipped ratio and saturates at 1.0; count divides
#                         by the largest count among the candidates instead, so
#                         the fullest instance keeps winning. EXP-96.
#                      space, between 0 and 1.  1 is the shipped behaviour and
#                      every measurement before EXP-58; 0 is the same ordering
#                      FS_AFFINITY=false produces.  It exists so the degree of
#                      class separation can be swept rather than switched.
#   FS_CLASS_HARM=false  when nothing is feasible, stop charging a candidate for
#                      the share of it that belongs to other classes.  Separate
#                      from FS_AFFINITY because the feasible-set ordering never
#                      runs in the regime this term is for.
#   FS_FLUX=false      judge instances on current occupancy instead of projecting
# The point of each is to attribute a result to a mechanism rather than to the
# policy as a whole.  FS_PEND=false FS_SHED=false is FluidServe as pure routing,
# which is directly comparable with PolyServe.
FLUIDSERVE_ABLATIONS = {
    "FS_PEND": "--fluidserve-enable-pend",
    # EXP-79. `coupled` (shipped) or `fleet[:scale]`, the independent-combination
    # ablation. A string rather than a boolean, so it is not subject to the Go
    # bool-flag trap, but it IS subject to a typo silently becoming the control --
    # the scheduler refuses to start on an unparseable value and the check below
    # compares what it reported back.
    "FS_SHED_SIGNAL": "--fluidserve-shed-signal",
    # EXP-64. Read the client's per-request output-length hint instead of the
    # class distribution. Default false, so the control arm is unchanged.
    "FS_ORACLE_LEN": "--fluidserve-oracle-length",
    "FS_SHED": "--fluidserve-enable-shed",
    "FS_AFFINITY": "--fluidserve-enable-affinity",
    # EXP-58. Not a boolean either: a float between 0 and 1.
    "FS_AFFINITY_WEIGHT": "--fluidserve-affinity-weight",
    "FS_AFFINITY_METRIC": "--fluidserve-affinity-metric",
    "FS_PER_INSTANCE_CORR": "--fluidserve-per-instance-correction",
    "FS_MEMORY_PACE_CAP": "--fluidserve-memory-uses-pace-cap",
    "FS_PER_INSTANCE_DELAY": "--fluidserve-per-instance-delay",
    "FS_DEADLINE_USES_DELAY": "--fluidserve-deadline-uses-delay",
    "FS_SHED_NO_FIRST_TOKEN": "--fluidserve-shed-ignores-first-token",
    "FS_PREFILL_INTERLEAVE": "--fluidserve-prefill-interleave-aware",
    # EXP-59. A string, "50:0;100:1,2;25:3": tier -> the positions, in the
    # sorted list of instance ids, that the class may be placed on.
    "FS_CLASS_PIN": "--fluidserve-class-pin",
    "FS_FLUX": "--fluidserve-enable-flux",
    "FS_CLASS_HARM": "--fluidserve-class-harm",
    "FS_HORIZON": "--fluidserve-horizon-steps",
    "FS_Z": "--fluidserve-z-safety",
    # EXP-42 candidate A. Off in the shipped default, so the baseline arm needs
    # no environment variable and the treatment arm sets FS_FORCE_MARGIN=true.
    "FS_FORCE_MARGIN": "--fluidserve-force-margin",
    # EXP-46 candidate C.
    "FS_OWN_BUDGET_GATE": "--fluidserve-own-budget-gate",
    "FS_DEADLINE_FEASIBLE": "--fluidserve-deadline-feasible",
    # EXP-49 candidate H2.
    "FS_KV_SLOPE": "--fluidserve-kv-slope-projection",
    # EXP-52. Not a boolean: the value is passed through as a float, so the
    # set_flag path must not turn it into --flag=true.
    "FS_GATE_SLACK": "--fluidserve-gate-slack",
    # EXP-67. Charge an arriving prompt only for the blocks the candidate
    # instance is not already believed to hold. Off in the shipped default, so
    # the control arm needs no environment variable and the treatment arm sets
    # FS_PREFIX=true. See ms_dev/notes/fluidserve-prefix.md.
    "FS_PREFIX": "--fluidserve-prefix-aware",
    # On by default in the binary, so the ablation arm sets it to false.
    "FS_PREFIX_CALIBRATION": "--fluidserve-prefix-calibration",
    # Integers, not booleans.
    "FS_PREFIX_BLOCK_TOKENS": "--fluidserve-prefix-block-tokens",
    "FS_PREFIX_CAPACITY": "--fluidserve-prefix-capacity",
    # EXP-107. The class-instance cap: per class, bound the number of instances
    # whose pace gate the class sets at a demand-derived limit. Off in the
    # shipped default, so the control arm needs no environment variable.
    "FS_INSTANCE_CAP": "--fluidserve-class-instance-cap",
    # A float (residence-time multiple), not a boolean -- same handling as
    # FS_GATE_SLACK.
    "FS_CAP_WINDOW_MULT": "--fluidserve-class-instance-cap-window-mult",
    # EXP-107. On by default (the shipped behaviour keeps the force branch);
    # the treatment arm sets FS_FORCE=false, which turns a forced placement
    # into an explicit early rejection.
    "FS_FORCE": "--fluidserve-enable-force",
}

# The gateway holds a request and re-asks the scheduler while no instance can
# take it, so its retry interval is FluidServe's re-decision period.  The stock
# 1000 ms is far coarser than the timescale the decision moves on: at 20 ms per
# iteration an instance's state turns over completely between two retries.  The
# window has to be wider than any deadline the scheduler itself computes, so
# that a hold ends because the scheduler decided it should and not because the
# gateway lost patience.  The longest such deadline is the agent class's
# end-to-end budget of 30 s, so the ceiling is set above it; every shorter hold
# ends in a placement or a rejection well before this fires, and reaching it at
# all means the scheduler stopped answering.
GATEWAY_FLAGS_BY_POLICY = {
    "fluidserve": {
        # Matched to --cms-pull-status-interval-ms, which is how often the
        # instance state the decision reads is refreshed.  Re-deciding faster
        # than the state changes cannot reach a different answer and only costs
        # scheduling calls, so the re-decision period is the state period.
        "--wait-scheduling-retry-interval": "500ms",
        "--wait-scheduling-timeout": "35000ms",
    },
}
# These two ARE the gateway's upstream defaults (cmd/gateway/app/options/config.go:
# 1000 ms and 5000 ms), so every arm except FluidServe runs the gateway as
# shipped. FluidServe overrides them above so that the WALL NEVER BINDS and the
# hold is ended by the policy's own per-request deadline instead.
#
# The value is not tuned, and it is not 35,000 for any property of 35,000: the
# wall only has to exceed the longest hold canWait can derive, which is bounded
# by the largest class time-to-first-token budget (deepresearch, 10 s), so every
# value above about twelve seconds is the same policy. That it does not bind is
# MEASURED rather than assumed -- gateway_scheduling_gave_up_total is 0 in all
# six EXP-82 control runs and the longest hold anywhere is 9,071 ms.
#
# The justification written here until 2026-08-18 was that "canWait derives waits
# of up to twenty seconds from the agent class's 30 s end-to-end budget". EXP-84
# measured that to be arithmetically impossible: canWait already subtracts the
# decode the request still needs, so twenty seconds would require the fleet to
# deliver 20.8 ms per token against a decode floor of 25-30 ms; the derivable
# hold is 4.4-8.0 s, and swe's refusals in the control came after a median hold
# of 19 ms. That claim is retracted. The override stays, for the reason above.
# See experiments/EXP-84_gateway-two-by-two.md section 7.5b.
#
# 2026-07-29: briefly changed so that every arm got FluidServe's window, on the
# reasoning that the gateway is infrastructure and an asymmetry here is unfair.
# Reverted for two reasons. The 5 s ceiling is what Llumnix ships, so raising it
# measures a baseline nobody runs; and every result recorded from EXP-27 to
# EXP-36 used the per-policy setting, so changing it would leave one sweep
# incomparable with all of them. Running the uniform-window variant as a
# separate arm is worth doing -- it answers "what if the baseline could hold as
# long as you do" -- but as an appendix, not as the main comparison.
GATEWAY_DEFAULTS = {
    "--wait-scheduling-retry-interval": "1000ms",
    "--wait-scheduling-timeout": "5000ms",
}

# Applied to every policy, because it is a property of the gateway rather than
# of the arm and it has to be identical on both sides of a comparison.
#
# The gateway takes each request off a buffer queue with a fixed pool of worker
# goroutines, and the worker stays on the request until it has an endpoint.  For
# a policy that answers immediately that is invisible: a worker is occupied for
# under a millisecond, and five of them serve thousands of requests a second.
# For a policy that defers a placement, the worker is occupied for the whole
# hold, so five workers cap the fleet at five concurrently held requests and
# every other arrival waits for the 512-slot queue.  Measured, that pinned
# gateway_pending_requests at 515 for an entire run and drove the time to first
# token to 20-50 s while the engines ran at a third of their capacity and the
# scheduler answered in 0.1 ms.  It made the hold mechanism look like a policy
# failure when it was a pool size.
#
# The workers are waiting on the network, not computing, so the pool is sized
# for the number of requests that may be in flight rather than for the number of
# cores.  PolyServe's pending gauge is zero either way, so this changes nothing
# on that side of the comparison and both arms are run with it.
GATEWAY_CAPACITY = {
    "--wait-queue-threads": "4096",
    "--max-queue-size": "16384",
}


def kubectl(*args, check=True, stdin=None):
    r = subprocess.run(["kubectl", "-n", NS, *args], capture_output=True,
                       text=True, input=stdin)
    if check and r.returncode != 0:
        sys.exit(f"kubectl {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


def get_deploy():
    return json.loads(kubectl("get", "deploy", DEPLOY, "-o", "json"))


def set_flag(args, name, value):
    """Set a flag in an argv list, replacing any existing occurrence.

    A boolean is written as one token, --name=value, and this is not a style
    choice. Go's pflag only reads the separated form for flags that take a
    value; for a boolean, `--name false` sets the flag to TRUE and leaves
    "false" as a positional argument, which the scheduler ignores. Written that
    way, every ablation switch silently did the opposite of what it was asked.
    It cost a four-hour run in which the arm with holding and rejection turned
    off recorded 72,593 holds and 15,723 rejections, and matched the arm they
    were meant to be turned off in to within one point at every rate -- because
    it was the same configuration measured twice.
    """
    out, i = [], 0
    while i < len(args):
        cur = args[i]
        if cur == name:
            # Separated form. Drop the flag and its value, unless what follows
            # is another flag, in which case this one had no value to drop.
            i += 2 if i + 1 < len(args) and not args[i + 1].startswith("-") else 1
            continue
        if cur.startswith(name + "="):
            i += 1
            continue
        out.append(cur)
        i += 1
    if str(value).lower() in ("true", "false"):
        return out + [f"{name}={value}"]
    return out + [name, value]


def drop_flags(args, keep, prefix):
    """Remove every --prefix* flag that is not in `keep`.

    A flag the scheduler no longer defines is a start-up failure, not a warning:
    the process exits with "unknown flag" and the pod crash-loops. Applying the
    current flag set on top of an older one therefore has to remove what it does
    not set, which happened when the parameter count was cut from twenty to
    seven and the deployment kept the removed flags.
    """
    out, i, dropped = [], 0, []
    while i < len(args):
        tok = args[i]
        name = tok.split("=", 1)[0]
        if name.startswith(prefix) and name not in keep:
            dropped.append(name)
            if "=" in tok:
                i += 1
            else:
                i += 2 if i + 1 < len(args) and not args[i + 1].startswith("-") else 1
            continue
        out.append(tok)
        i += 1
    if dropped:
        print("  dropping flags no longer defined: " + " ".join(dropped))
    return out


def show():
    d = get_deploy()
    c = d["spec"]["template"]["spec"]["containers"][0]
    args = c.get("args", [])
    pol = args[args.index("--scheduling-policy") + 1] if "--scheduling-policy" in args else "?"
    print(f"policy       : {pol}")
    for f in ("--ttft-profiling-data-path", "--tpot-profiling-data-path",
              "--ttft-slo", "--tpot-slo"):
        print(f"{f:<13}: {args[args.index(f)+1] if f in args else '(unset)'}")
    mounts = [m["mountPath"] for m in c.get("volumeMounts", [])]
    print(f"mounts       : {mounts}")
    cm = subprocess.run(["kubectl", "-n", NS, "get", "cm", CM],
                        capture_output=True, text=True)
    print(f"configmap    : {'present' if cm.returncode == 0 else 'absent'}")


def install_configmap():
    files = [os.path.join(TABLE_DIR, n) for n in ("ttft.json", "tpot.json")]
    fs = os.path.join(TABLE_DIR, "fluidserve.json")
    if os.path.exists(fs):
        files.append(fs)
    for f in files:
        if not os.path.exists(f):
            sys.exit(f"missing {f} -- run gen_profiling_from_stepdump.py first")
        with open(f) as fh:
            doc = json.load(fh)
        if os.path.basename(f) == "fluidserve.json":
            if not doc.get("classes") or not doc.get("decode_step_law"):
                sys.exit(f"{f} is missing classes or decode_step_law; the policy "
                         f"would refuse to start")
            print(f"  fluidserve.json: {len(doc['classes'])} classes, "
                  f"{os.path.getsize(f):,} bytes")
            continue
        if not doc.get("results"):
            sys.exit(f"{f} has no results; GetLatencyPredictor would Fatalf")
        print(f"  {os.path.basename(f)}: {len(doc['results'])} results, "
              f"{os.path.getsize(f):,} bytes")
    # recreate so repeated runs pick up regenerated tables
    subprocess.run(["kubectl", "-n", NS, "delete", "cm", CM, "--ignore-not-found"],
                   capture_output=True, text=True)
    kubectl("create", "configmap", CM, *[f"--from-file={f}" for f in files])
    print(f"  configmap/{CM} created")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy",
                    choices=["fluidserve", "polyserve", "slo", "load-balance",
                             # The vLLM router's default cache_aware policy,
                             # ported as a baseline. Named vllm-cache rather than
                             # vllm-router because "vLLM router" is ambiguous:
                             # production-stack's default is roundrobin and this
                             # is the PyPI vllm-router package's.
                             "vllm-cache"])
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args()

    if a.show or not a.policy:
        show()
        if not a.policy:
            return 0
        print()

    install_configmap()

    d = get_deploy()
    spec = d["spec"]["template"]["spec"]
    c = spec["containers"][0]
    args = list(c.get("args", []))
    args = set_flag(args, "--scheduling-policy", a.policy)
    # The profiling paths are inert unless an SLO policy is active, so leaving
    # them set across a revert costs nothing and keeps the diff small.
    for k, v in SLO_FLAGS.items():
        args = set_flag(args, k, v)
    # An ablation this invocation does not set is REMOVED, for the same reason as
    # the FluidServe ones below: a flag written by one arm otherwise survives
    # into every condition that follows it.
    args = drop_flags(args, set(POLYSERVE_ABLATIONS.values()), "--polyserve-")
    for k, v in polyserve_flags().items():
        args = set_flag(args, k, v)
        # Printed on every invocation, not only under --policy polyserve, so
        # that a run whose tier table went stale says so in its own log rather
        # than being reconstructed from the deployment spec months later.
        print(f"  polyserve tier table (from fluidserve.json): {k}={v}")
    for env_key, flag in POLYSERVE_ABLATIONS.items():
        val = os.environ.get(env_key)
        if val:
            args = set_flag(args, flag, val)
            print(f"  polyserve mechanism: {flag}={val}")
    # An ablation flag that this invocation does NOT set is REMOVED, so the
    # scheduler falls back to its compiled default. Keeping it instead was a
    # defect: a flag written by one arm survived into every condition that ran
    # afterwards, because nothing here put it back. Measured on 2026-08-01,
    # --fluidserve-class-harm=false was written by one ablation arm on
    # 2026-07-28 10:28 (exp27p2r1_fluidserveflat_m1) and was still in the
    # deployment 61 conditions later, spanning EXP-27 pass 2 to EXP-41. Every
    # one of those ran with the class term in the damage estimate disabled,
    # which is not what any of them intended and not what the documents record.
    #
    # The start-up line always said so. What was missing was a comparison
    # between what the line said and what the arm meant, which is exactly the
    # comparison this file exists to make, so it is made for the ablations too.
    keep = set(FLUIDSERVE_FLAGS)
    for env_key, flag in FLUIDSERVE_ABLATIONS.items():
        if os.environ.get(env_key):
            keep.add(flag)
    args = drop_flags(args, keep, "--fluidserve-")
    for k, v in FLUIDSERVE_FLAGS.items():
        args = set_flag(args, k, v)
    unset = []
    for env_key, flag in FLUIDSERVE_ABLATIONS.items():
        val = os.environ.get(env_key)
        if val:
            args = set_flag(args, flag, val)
            print(f"  ablation: {flag}={val}")
        elif flag not in FLUIDSERVE_FLAGS:
            unset.append(env_key)
    if unset:
        print("  ablations left at the compiled default (env unset): "
              + " ".join(sorted(unset)))
    c["args"] = args

    vols = spec.setdefault("volumes", [])
    if not any(v["name"] == VOL for v in vols):
        vols.append({"name": VOL, "configMap": {"name": CM}})
    mounts = c.setdefault("volumeMounts", [])
    if not any(m["name"] == VOL for m in mounts):
        mounts.append({"name": VOL, "mountPath": MOUNT, "readOnly": True})

    for k in ("resourceVersion", "uid", "creationTimestamp", "generation"):
        d["metadata"].pop(k, None)
    d.pop("status", None)
    # GetLatencyPredictor reads the tables once, behind a sync.Once, so a
    # regenerated ConfigMap is invisible until the process restarts, and `apply`
    # alone restarts nothing when the args happen to be unchanged. Stamp the
    # restart annotation into the same apply rather than following up with
    # `rollout restart`: two mutations would spawn two ReplicaSets, and the
    # policy check below would then read a pod that is already being replaced.
    tmpl = d["spec"]["template"]["metadata"].setdefault("annotations", {})
    tmpl["llumnix.dev/restartedAt"] = str(time.time())
    kubectl("apply", "-f", "-", stdin=json.dumps(d))
    print(f"  scheduler -> --scheduling-policy {a.policy}")

    print("  waiting for rollout ...")
    r = subprocess.run(["kubectl", "-n", NS, "rollout", "status",
                        f"deploy/{DEPLOY}", f"--timeout={a.timeout}s"],
                       capture_output=True, text=True)
    print("  " + (r.stdout or r.stderr).strip())
    if r.returncode != 0:
        print("\n--- scheduler logs (rollout failed) ---")
        print(kubectl("logs", f"deploy/{DEPLOY}", "--tail=60", check=False))
        return 1

    time.sleep(3)
    # Address the pod by name: with an old replica still terminating,
    # `logs deploy/<name>` can pick the one that is going away.
    # Sort by creation time: list order is not age order, so without this the
    # "newest" pod can still be the one that is shutting down.
    pods = kubectl("get", "pods", "-l", f"app={DEPLOY}",
                   "--sort-by=.metadata.creationTimestamp",
                   "-o", "jsonpath={.items[-1].metadata.name}", check=False).strip()
    target = pods or f"deploy/{DEPLOY}"
    logs = kubectl("logs", target, "--tail=400", check=False)
    hits = [l for l in logs.splitlines()
            if "LatencyPredictor" in l or "profiling" in l.lower()
            or "PolyServe dispatch policy" in l or "create scheduler with policy" in l]
    fatal = [l for l in logs.splitlines() if "Failed to load" in l or "F0" == l[:2]]
    print("\n--- predictor init ---")
    for l in hits[:10]:
        print("  " + l.strip())
    if fatal:
        print("  FATAL:")
        for l in fatal[:5]:
            print("  " + l.strip())
        return 1
    if a.policy == "slo" and not hits:
        print("  WARNING: no LatencyPredictor line found; check -v level and logs")

    # Read from the START of the log for the verification, not the tail: the
    # scheduler runs at -v 4 and emits hundreds of lines a second, so the
    # start-up line it has to be checked against is out of any tail window
    # within seconds of the pod becoming ready.
    full = kubectl("logs", target, check=False)
    if verify_effective(a.policy, full, args) != 0:
        return 1

    if set_gateway(a.policy, a.timeout) != 0:
        return 1
    return 0


def verify_effective(policy, logs, applied_args):
    """Check that the scheduler is running the configuration it was given.

    Applying a flag and having it take effect are different things, and the gap
    between them is silent. A boolean written in the separated form is accepted
    by the deployment, survives a rollout, appears in `kubectl get deploy`, and
    is read as the opposite of what it says. Four hours of measurement were
    taken through exactly that, with an ablation arm that was in fact identical
    to the arm it was supposed to differ from.

    The scheduler prints what it actually resolved at start-up. That line, not
    the spec, is the authority, so it is parsed and compared here and the caller
    fails rather than proceeding.
    """
    if policy == "vllm-cache":
        return _verify_vllm_cache(logs, applied_args)
    if policy != consts_fluidserve():
        return 0
    line = next((l for l in logs.splitlines()
                 if "FluidServe dispatch policy created" in l), None)
    if not line:
        print("\n  ERROR: the scheduler never reported its FluidServe configuration.")
        print("  Without that line there is no way to tell what it is running.")
        return 1

    want = {}
    for i, tok in enumerate(applied_args):
        name, _, inline = tok.partition("=")
        if not name.startswith("--fluidserve-"):
            continue
        val = inline if inline else (
            applied_args[i + 1] if i + 1 < len(applied_args) else "")
        want[name] = val

    effective = dict(re.findall(r"(\w+)=([\w.]+)", line))
    checks = [
        ("--fluidserve-enable-pend", "pend"),
        ("--fluidserve-enable-shed", "shed"),
        ("--fluidserve-enable-affinity", "affinity"),
        ("--fluidserve-affinity-weight", "affweight"),
        ("--fluidserve-affinity-metric", "affmetric"),
    ("--fluidserve-per-instance-correction", "percorr"),
    ("--fluidserve-memory-uses-pace-cap", "pacecap"),
    ("--fluidserve-per-instance-delay", "perdelay"),
    ("--fluidserve-deadline-uses-delay", "deadlinedelay"),
    ("--fluidserve-shed-ignores-first-token", "shednoft"),
    ("--fluidserve-prefill-interleave-aware", "interleave"),
        ("--fluidserve-enable-flux", "flux"),
        ("--fluidserve-class-harm", "classharm"),
        ("--fluidserve-force-margin", "forcemargin"),
        ("--fluidserve-own-budget-gate", "ownbudgetgate"),
        ("--fluidserve-deadline-feasible", "deadlinefeasible"),
        ("--fluidserve-kv-slope-projection", "kvslope"),
        ("--fluidserve-gate-slack", "gateslack"),
        ("--fluidserve-z-safety", "z"),
        # EXP-67. The whole point of this arm is that the treatment differs from
        # the control, so a flag that did not take effect makes them the same
        # run under two names -- the failure this function exists to catch.
        ("--fluidserve-oracle-length", "oraclelen"),
        ("--fluidserve-prefix-aware", "prefix"),
        ("--fluidserve-prefix-calibration", "prefixcalib"),
        ("--fluidserve-prefix-block-tokens", "prefixblock"),
        ("--fluidserve-prefix-capacity", "prefixcap"),
        # EXP-107. The three fields sit at the END of the startup line, after
        # `budgets "..."` -- nothing may be inserted between classpin and
        # budgets, and the values here are a bool, a bare float and a bool, all
        # readable by the generic token regex above.
        ("--fluidserve-class-instance-cap", "instancecap"),
        ("--fluidserve-class-instance-cap-window-mult", "capmult"),
        ("--fluidserve-enable-force", "enableforce"),
    ]
    bad = []
    for flag, key in checks:
        if flag not in want or key not in effective:
            continue
        asked, got = want[flag].lower(), effective[key].lower()
        try:
            same = abs(float(asked) - float(got)) < 1e-6
        except ValueError:
            same = asked == got
        if not same:
            bad.append(f"{flag}: asked {asked}, scheduler reports {key}={got}")
    # The shed signal is checked separately for the same reason as the class pin
    # below: its value contains a ':' and the general token regex above stops
    # there, so `shedsignal=fleet:1.0` was read as `fleet` and compared unequal to
    # the `fleet:1.0` that was asked for. The pre-flight caught this before any
    # condition ran, which is the whole reason the pre-flight exists.
    #
    # The comparison is semantic rather than textual, because the two sides spell
    # the same setting differently by design: the Go side treats a bare `fleet` as
    # scale 1.0, so `fleet` and `fleet:1.0` are one configuration written two ways.
    # Comparing the strings would fail a run that was correct -- the mistake made
    # once already with the class pin.
    def _shed_canon(text):
        text = (text or "coupled").strip()
        if text in ("", "coupled"):
            return ("coupled", 0.0)
        if text == "fleet":
            return ("fleet", 1.0)
        if text.startswith("fleet:"):
            try:
                return ("fleet", float(text.split(":", 1)[1]))
            except ValueError:
                return ("unparseable", text)
        return ("unparseable", text)

    # The class budgets are checked separately, and were not checked at all
    # until 2026-08-27. Their value contains ':' and ',', which the general
    # token regex above does not capture, so a budget that was asked for and not
    # applied passed in silence -- and this is the one setting that has to move
    # in TWO places at once, here and in the scorer's SLO_RULES. A condition
    # whose policy uses one end-to-end budget while the analysis scores against
    # another produces numbers that look ordinary and mean nothing.
    #
    # Compared as a map rather than as a string: the Go side prints the tiers in
    # sorted order, which is the mistake already made once with the class pin,
    # where a correct setting failed a textual comparison and the condition could
    # not be run.
    def _budget_map(text):
        out = {}
        for part in (text or "").split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split(":")
            out[bits[0]] = ":".join(bits[1:])
        return out

    m = re.search(r'budgets "([^"]*)"', line)
    if m is not None and "--fluidserve-class-budgets" in want:
        asked_b = _budget_map(want["--fluidserve-class-budgets"])
        got_b = _budget_map(m.group(1))
        if asked_b != got_b:
            bad.append(f"--fluidserve-class-budgets: asked "
                       f"{want['--fluidserve-class-budgets']}, scheduler "
                       f"reports budgets \"{m.group(1)}\"")
    elif "--fluidserve-class-budgets" in want:
        bad.append("the start-up line does not report budgets; the class "
                   "budgets cannot be verified and this condition must not run")

    m = re.search(r"shedsignal=([^,]+)", line)
    got_shed = m.group(1).strip() if m else None
    if got_shed is None:
        bad.append("the start-up line does not report shedsignal; the binary "
                   "predates --fluidserve-shed-signal and this arm cannot be run "
                   "on it")
    else:
        asked_shed = want.get("--fluidserve-shed-signal", "coupled")
        if _shed_canon(asked_shed) != _shed_canon(got_shed):
            bad.append(f"--fluidserve-shed-signal: asked {asked_shed}, "
                       f"scheduler reports shedsignal={got_shed}")

    # The class pin is checked separately for two reasons. Its value contains
    # ':' ',' and ';', which the general token regex above does not capture, so
    # it would be skipped in silence -- and a pin that was asked for and not
    # applied turns the treatment arm into the control arm, which is the failure
    # this function exists to prevent. And the two sides write the same map in
    # different orders: the scheduler prints the tiers sorted so that its
    # start-up line is reproducible, while an arm writes them in whatever order
    # reads well. Comparing the strings failed a configuration that was correct.
    def _pin_canon(text):
        out = {}
        for grp in (text or "").split(";"):
            grp = grp.strip()
            if not grp or ":" not in grp:
                continue
            tier, _, lst = grp.partition(":")
            out[tier.strip()] = sorted(x.strip() for x in lst.split(",") if x.strip())
        return out

    if "--fluidserve-class-pin" in want:
        m = re.search(r"classpin=(.+?), budgets ", line)
        asked = want["--fluidserve-class-pin"]
        got = m.group(1) if m else "(absent)"
        if _pin_canon(got) != _pin_canon(asked):
            bad.append(f"--fluidserve-class-pin: asked {asked}, "
                       f"scheduler reports classpin={got}")
    elif "classpin=" in line and "classpin=off" not in line:
        m = re.search(r"classpin=(.+?), budgets ", line)
        bad.append(f"--fluidserve-class-pin was not requested but the scheduler "
                   f"reports classpin={m.group(1) if m else '?'}")

    # The horizon is printed in its own phrasing.
    m = re.search(r"horizon (\d+) steps", line)
    if m and "--fluidserve-horizon-steps" in want:
        if m.group(1) != want["--fluidserve-horizon-steps"]:
            bad.append(f"--fluidserve-horizon-steps: asked "
                       f"{want['--fluidserve-horizon-steps']}, reports {m.group(1)}")

    print("\n--- effective FluidServe configuration (read from the scheduler) ---")
    print("  " + line.split("] ", 1)[-1].strip())
    if bad:
        print("\n  ERROR: the scheduler is not running what it was asked to run:")
        for b in bad:
            print("    " + b)
        print("  A boolean must be written as --flag=value; the separated form"
              " is read as true.")
        return 1
    print("  verified: every FluidServe flag matches what was applied")
    return 0


def _verify_vllm_cache(logs, applied_args):
    """Check the vLLM router baseline against its own start-up line.

    Two things are checked, and the second is the reason this function exists
    rather than being folded into the FluidServe branch.

    The five constants, because `cache_threshold` alone gives two different
    routers -- 0.3 in vllm-router and 0.7 in the SGLang gateway it forked -- so a
    value that failed to apply produces a policy that runs, routes plausibly, and
    is not the one being compared against.

    And `localaccount`, which is not a flag this script sets. The policy's load
    signal is the engine's queue depth from the last poll plus the dispatches
    made since, and the second term is only maintained when
    --enable-instance-status-local-account is true. With it false the term is
    always zero, every decision inside one 500 ms poll interval reads the same
    depth, and they pile onto whichever instance looked emptiest at the last
    poll. That is a defect of our port rather than a property of their
    algorithm, so a run made in that state would attribute our artifact to them.
    See ms_dev/notes/vllm-router-baseline.md section 3.1.
    """
    line = next((l for l in logs.splitlines()
                 if "vLLM router cache_aware policy created" in l), None)
    if not line:
        print("\n  ERROR: the scheduler never reported its vllm-cache configuration.")
        print("  Without that line there is no way to tell what it is running.")
        return 1

    want = {}
    for i, tok in enumerate(applied_args):
        name, _, inline = tok.partition("=")
        if not name.startswith("--vllm-cache-"):
            continue
        want[name] = inline if inline else (
            applied_args[i + 1] if i + 1 < len(applied_args) else "")

    effective = dict(re.findall(r"(\w+)=([\w.]+)", line))
    # The start-up line writes the constants as "name value," prose rather than
    # name=value, so they are read by name here.
    reported = {}
    for key, pat in (("--vllm-cache-threshold", r"cacheThreshold ([\d.]+)"),
                     ("--vllm-cache-balance-abs", r"balanceAbs (\d+)"),
                     ("--vllm-cache-balance-rel", r"balanceRel ([\d.]+)"),
                     ("--vllm-cache-eviction-secs", r"evictionSecs (\d+)"),
                     ("--vllm-cache-max-tree-size", r"maxTreeSize (\d+)")):
        m = re.search(pat, line)
        if m:
            reported[key] = m.group(1)

    # What vllm-router 0.1.15 ships. The driver arm passes no --vllm-cache- flag
    # at all, so without this the constants would go unchecked in the one path
    # that is actually used, and a rebuilt binary with a different compile
    # default would run as this arm under the same name. A deliberate ablation
    # passes the flag and is compared against what it asked for instead.
    VLLM_CACHE_DEFAULTS = {
        "--vllm-cache-threshold": "0.3",
        "--vllm-cache-balance-abs": "64",
        "--vllm-cache-balance-rel": "1.5",
        "--vllm-cache-eviction-secs": "120",
        "--vllm-cache-max-tree-size": str(1 << 26),
    }

    bad = []
    for flag, dflt in VLLM_CACHE_DEFAULTS.items():
        want.setdefault(flag, dflt)
    for flag, asked in want.items():
        got = reported.get(flag)
        if got is None:
            bad.append(f"{flag}: asked {asked}, the start-up line does not report it")
            continue
        try:
            same = abs(float(asked) - float(got)) < 1e-6
        except ValueError:
            same = asked.lower() == got.lower()
        if not same:
            bad.append(f"{flag}: asked {asked}, scheduler reports {got}")

    if effective.get("localaccount", "").lower() != "true":
        bad.append(
            "localaccount is not true, so the dispatches made since the last "
            "poll are not counted and every decision inside one poll interval "
            "reads the same queue depth (vllm-router-baseline.md section 3.1)")

    print("\n--- effective vllm-cache configuration (read from the scheduler) ---")
    print("  " + line.split("] ", 1)[-1].strip())
    if bad:
        print("\n  ERROR: the scheduler is not running what it was asked to run:")
        for b in bad:
            print("    " + b)
        return 1
    print("  verified: every vllm-cache flag matches, and the load signal counts "
          "dispatches since the last poll")
    return 0


def consts_fluidserve():
    return "fluidserve"


# The two flags the gateway's hold-and-retry loop is made of, and the environment
# variable that overrides each ONE ON ITS OWN.
#
# WHY EACH FLAG NEEDS ITS OWN SWITCH.  FS_GATEWAY_STOCK below moves both values
# at once, from FluidServe's 35,000 ms / 500 ms to the shipped 5,000 ms /
# 1,000 ms, and EXP-83 ran that way.  It moved every aggregate by 10 to 19
# points, and the investigation that followed found that what actually differs
# between the two settings is which class each of the four instances ends up
# holding: under 35,000/500 one instance of the four becomes deep-research-only
# in four runs out of four, and under 5,000/1,000 in none of four.  Since both
# values moved together, that effect cannot be attributed to either of them.
# These two variables fill the other two cells of the two-by-two, each changing
# exactly one value and leaving the other at whatever the policy would take.
#
# The value is given in milliseconds, with or without the "ms" suffix.  Unset,
# nothing changes.
GATEWAY_WINDOW_ENV = {
    "FS_GATEWAY_RETRY_MS": "--wait-scheduling-retry-interval",
    "FS_GATEWAY_TIMEOUT_MS": "--wait-scheduling-timeout",
}


def gateway_want(policy, env=None):
    """The gateway flags this policy should run with, before the cluster is touched.

    Kept separate from set_gateway so that the environment overrides can be
    exercised without applying anything: the whole point of these switches is
    that one condition differs from another in exactly one value, and a switch
    that is only ever tested by running an experiment is tested after it is too
    late to find out it was wrong.
    """
    env = os.environ if env is None else env
    want = dict(GATEWAY_FLAGS_BY_POLICY.get(policy, GATEWAY_DEFAULTS))
    notes = []
    # FS_GATEWAY_STOCK=1 forces the shipped window on whatever policy is running.
    # It exists to answer the question the comment above GATEWAY_DEFAULTS already
    # said was worth answering and that nobody had run: how much of FluidServe's
    # advantage is the hold-and-retry window rather than the decisions it makes.
    # A 2026-08-17 audit measured that the 5 s ceiling BINDS on the baselines --
    # Llumnix SLO's median rejection latency is 5,065 ms, which is its wall --
    # while FluidServe's longest hold over 316,269 rejections is 9.07 s against a
    # 35 s wall, so the two arms are not merely differently configured, one of
    # them is being cut off by the configuration and the other is not.
    # Unset, nothing changes: every existing run and every future run that does
    # not set it takes the same values it took before.
    if env.get("FS_GATEWAY_STOCK") == "1":
        want = dict(GATEWAY_DEFAULTS)
        notes.append("FS_GATEWAY_STOCK=1, using the shipped window for every policy")
    # Applied after FS_GATEWAY_STOCK so the two compose predictably: the stock
    # switch sets the pair, a per-flag switch then moves one member of it.
    for name, flag in GATEWAY_WINDOW_ENV.items():
        raw = env.get(name)
        if not raw:
            continue
        raw = raw.strip()
        digits = raw[:-2] if raw.endswith("ms") else raw
        if not digits.isdigit():
            sys.exit(f"{name}={raw!r} is not a whole number of milliseconds")
        was = want.get(flag)
        want[flag] = f"{digits}ms"
        notes.append(f"{name}={want[flag]} overrides {flag} (policy default {was})")
    return want, notes


def set_gateway(policy, timeout):
    """Point the gateway's hold-and-retry loop at the right cadence.

    Only FluidServe depends on the retry cadence: for every other policy the
    scheduler either returns an instance or the request fails, so the retry loop
    is a failure path rather than a control mechanism.  Those values are reset
    for other policies so that switching back leaves no trace of the FluidServe
    run.  The queue capacity in GATEWAY_CAPACITY is applied to every policy, for
    the reason recorded there.
    """
    want, notes = gateway_want(policy)
    for n in notes:
        print(f"  gateway: {n}")
    want.update(GATEWAY_CAPACITY)
    d = json.loads(kubectl("get", "deploy", "gateway", "-o", "json"))
    c = d["spec"]["template"]["spec"]["containers"][0]
    args = list(c.get("args", []))
    before = list(args)
    for k, v in want.items():
        args = set_flag(args, k, v)
    if args == before:
        print(f"\n  gateway retry settings already {want}")
        return 0
    c["args"] = args
    for k in ("resourceVersion", "uid", "creationTimestamp", "generation"):
        d["metadata"].pop(k, None)
    d.pop("status", None)
    d["spec"]["template"]["metadata"].setdefault("annotations", {})[
        "llumnix.dev/restartedAt"] = str(time.time())
    kubectl("apply", "-f", "-", stdin=json.dumps(d))
    print(f"\n  gateway -> {want}")
    r = subprocess.run(["kubectl", "-n", NS, "rollout", "status", "deploy/gateway",
                        f"--timeout={timeout}s"], capture_output=True, text=True)
    print("  " + (r.stdout or r.stderr).strip())
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
