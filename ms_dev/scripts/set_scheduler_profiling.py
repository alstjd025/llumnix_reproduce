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
    "--fluidserve-alpha-externality": "1.0",
    "--fluidserve-enable-pend": "true",
    "--fluidserve-enable-externality": "true",
    "--fluidserve-enable-flux": "true",
}

# Ablation switches, set from the environment so an arm can turn one mechanism
# off without editing this file:
#   FS_PEND=false          place immediately instead of holding
#   FS_EXTERNALITY=false   ignore what a placement costs the requests already there
#   FS_FLUX=false          judge instances on current occupancy instead of projecting
# The point of each is to attribute a result to a mechanism rather than to the
# policy as a whole; with holding off, for instance, FluidServe is pure routing
# and therefore directly comparable with PolyServe, which never refuses either.
FLUIDSERVE_ABLATIONS = {
    "FS_PEND": "--fluidserve-enable-pend",
    "FS_EXTERNALITY": "--fluidserve-enable-externality",
    "FS_FLUX": "--fluidserve-enable-flux",
    "FS_ALPHA": "--fluidserve-alpha-externality",
    "FS_HORIZON": "--fluidserve-horizon-steps",
    "FS_Z": "--fluidserve-z-safety",
}

# The gateway holds a request and re-asks the scheduler while no instance can
# take it, so its retry interval is FluidServe's re-decision period.  The stock
# 1000 ms is far coarser than the timescale the decision moves on: at 20 ms per
# iteration an instance's state turns over completely between two retries.  The
# window is widened to cover the largest time-to-first-token budget in the
# workload (11.8 s for the agent class) so that holding is bounded by the
# request's own budget rather than by the gateway giving up first.
GATEWAY_FLAGS_BY_POLICY = {
    "fluidserve": {
        "--wait-scheduling-retry-interval": "100ms",
        "--wait-scheduling-timeout": "12000ms",
    },
}
GATEWAY_DEFAULTS = {
    "--wait-scheduling-retry-interval": "1000ms",
    "--wait-scheduling-timeout": "5000ms",
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
    """Set --name value in an argv list, replacing any existing occurrence."""
    out, i = [], 0
    while i < len(args):
        if args[i] == name:
            i += 2                      # drop the old flag and its value
            continue
        out.append(args[i])
        i += 1
    return out + [name, value]


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

    if set_gateway(a.policy, a.timeout) != 0:
        return 1
    return 0


def set_gateway(policy, timeout):
    """Point the gateway's hold-and-retry loop at the right cadence.

    Only FluidServe depends on this: for every other policy the scheduler either
    returns an instance or the request fails, so the retry loop is a failure
    path rather than a control mechanism.  The values are reset for those
    policies so that switching back leaves no trace of the FluidServe run.
    """
    want = GATEWAY_FLAGS_BY_POLICY.get(policy, GATEWAY_DEFAULTS)
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
