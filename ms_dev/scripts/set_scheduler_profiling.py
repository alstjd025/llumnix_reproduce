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

# Expected output length per tier, keyed by the tier's TPOT SLO.  Measured
# per-class means of the mix workload: swe 728, chat 386, deepresearch 275.
POLYSERVE_FLAGS = {
    "--polyserve-tier-decode-tokens": "25:728,50:386,100:275",
}

# How each tier's latency budget is defined, which is how the requests are
# actually scored: chat and deepresearch on the mean time between output tokens
# over the whole request, swe on end-to-end latency.  Both forms are cumulative,
# so a request that has been running ahead of its budget is not held to a
# per-token ceiling it does not need.
FLUIDSERVE_FLAGS = {
    "--fluidserve-profile-path": f"{MOUNT}/fluidserve.json",
    "--fluidserve-class-budgets": "25:e2e:30000,50:decode,100:decode",
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
    "FS_SHED": "--fluidserve-enable-shed",
    "FS_AFFINITY": "--fluidserve-enable-affinity",
    "FS_FLUX": "--fluidserve-enable-flux",
    "FS_CLASS_HARM": "--fluidserve-class-harm",
    "FS_HORIZON": "--fluidserve-horizon-steps",
    "FS_Z": "--fluidserve-z-safety",
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
# shipped. FluidServe overrides them above because its hold is a computed
# deadline rather than a fixed patience: canWait derives waits of up to twenty
# seconds from the agent class's 30 s end-to-end budget, and a 5 s ceiling
# truncates them before the scheduler's own logic can end them, which removes
# the mechanism rather than testing it. That override is part of FluidServe's
# design and has to be stated as one, not hidden.
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
                    choices=["fluidserve", "polyserve", "slo", "load-balance"])
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
    for k, v in POLYSERVE_FLAGS.items():
        args = set_flag(args, k, v)
    args = drop_flags(args, set(FLUIDSERVE_FLAGS) | set(FLUIDSERVE_ABLATIONS.values()),
                      "--fluidserve-")
    for k, v in FLUIDSERVE_FLAGS.items():
        args = set_flag(args, k, v)
    for env_key, flag in FLUIDSERVE_ABLATIONS.items():
        val = os.environ.get(env_key)
        if val:
            args = set_flag(args, flag, val)
            print(f"  ablation: {flag}={val}")
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
        ("--fluidserve-enable-flux", "flux"),
        ("--fluidserve-class-harm", "classharm"),
        ("--fluidserve-z-safety", "z"),
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


def consts_fluidserve():
    return "fluidserve"


def set_gateway(policy, timeout):
    """Point the gateway's hold-and-retry loop at the right cadence.

    Only FluidServe depends on the retry cadence: for every other policy the
    scheduler either returns an instance or the request fails, so the retry loop
    is a failure path rather than a control mechanism.  Those values are reset
    for other policies so that switching back leaves no trace of the FluidServe
    run.  The queue capacity in GATEWAY_CAPACITY is applied to every policy, for
    the reason recorded there.
    """
    want = dict(GATEWAY_FLAGS_BY_POLICY.get(policy, GATEWAY_DEFAULTS))
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
