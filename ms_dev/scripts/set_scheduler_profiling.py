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
    for f in files:
        if not os.path.exists(f):
            sys.exit(f"missing {f} -- run gen_profiling_from_stepdump.py first")
        with open(f) as fh:
            doc = json.load(fh)
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
    ap.add_argument("--policy", choices=["polyserve", "slo", "load-balance"])
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
    logs = kubectl("logs", f"deploy/{DEPLOY}", "--tail=400", check=False)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
