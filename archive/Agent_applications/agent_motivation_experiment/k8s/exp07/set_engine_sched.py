#!/usr/bin/env python3
r"""Switch the vLLM intra-engine scheduling policy (and migration) on the live
Llumnix engine LeaderWorkerSet, idempotently.

Policies (see llumnix_reproduce/patches/vllm-sched/README.md):
  fifo : stock vLLM (fcfs)
  edf  : --scheduling-policy priority; the CLIENT supplies
         priority = arrival_ms + SLO_ms (absolute deadline) and the gateway
         forwards it. No custom scheduler class.
  sjf  : --scheduling-policy priority --scheduler-cls llumnix_sched.SJFScheduler
  srpf : --scheduling-policy priority --scheduler-cls llumnix_sched.SRPFScheduler

IMPORTANT — why we edit the LIVE object instead of `kubectl apply`-ing the repo
yaml: the checked-in deploy yaml has drifted from what is running (repo says
Meta-Llama-3-70B / max-model-len 8192; live runs Llama-3.1-70B-Instruct /
40960, changed live at EXP-06 and never committed). Applying the repo file
would silently downgrade the model and invalidate the experiment. So we read
the live LWS, mutate only what we need, and apply that back.

What it mutates on the vllm container:
  - adds a hostPath volume + mount exposing patches/vllm-sched at /opt/llumnix-sched
  - PYTHONPATH=/opt/llumnix-sched            (so --scheduler-cls can resolve)
  - SCHED_EXTRA_ARGS="<per-policy vllm serve flags>"
  - LLUMNIX_ENABLE_MIGRATION=0|1
  - injects `${SCHED_EXTRA_ARGS} \` into the `vllm serve` command once

Usage:
  python3 set_engine_sched.py --policy srpf --migration off [--restart]
  python3 set_engine_sched.py --show
"""
import argparse
import json
import subprocess
import sys

NS = "llumnix"
LWS = "neutral"
MOUNT_PATH = "/opt/llumnix-sched"
HOST_PATH = "/home/nxclab/llumnix_reproduce/patches/vllm-sched"
VOL_NAME = "llumnix-sched"
# writable mount for per-step instrumentation output (InstrumentedScheduler)
OUT_MOUNT_PATH = "/sched-out"
OUT_HOST_PATH = "/opt/llumnix-sched-out"
OUT_VOL_NAME = "llumnix-sched-out"
ANCHOR = "--async-scheduling"          # inject our args right after this flag
INJECT = "  ${SCHED_EXTRA_ARGS} \\\n"

POLICY_ARGS = {
    "fifo": "",
    "edf": "--scheduling-policy priority",
    "sjf": "--scheduling-policy priority --scheduler-cls llumnix_sched.SJFScheduler",
    "srpf": "--scheduling-policy priority --scheduler-cls llumnix_sched.SRPFScheduler",
    # Niyama deadline-aware scheduler (deadline_sched.py). The client sends
    # priority = slo_ms*1000 + tbt_ms (run_experiment.py --priority-mode deadline).
    "deadline": "--scheduling-policy priority --scheduler-cls deadline_sched.DeadlineScheduler",
    # verification only: EDF + logs the priorities it receives
    "edf-debug": "--scheduling-policy priority --scheduler-cls llumnix_sched.EDFDebugScheduler",
    # FIFO ordering (no policy change) + per-step decode-latency instrumentation.
    # Requires SCHED_STEP_LOG env (set via --step-log) to actually write.
    "instrumented": "--scheduler-cls llumnix_sched.InstrumentedScheduler",
}


def kubectl(args, **kw):
    return subprocess.run(["kubectl", "-n", NS] + args, capture_output=True,
                          text=True, **kw)


def get_lws():
    r = kubectl(["get", "lws", LWS, "-o", "json"])
    if r.returncode != 0:
        sys.exit(f"failed to read lws/{LWS}: {r.stderr.strip()}")
    return json.loads(r.stdout)


def vllm_container(spec):
    for c in spec["containers"]:
        if c["name"] == "vllm":
            return c
    sys.exit("no 'vllm' container in the LWS pod spec")


def set_env(container, name, value):
    for e in container.setdefault("env", []):
        if e.get("name") == name:
            e["value"] = value
            e.pop("valueFrom", None)
            return
    container["env"].append({"name": name, "value": value})


def ensure_mount(spec, container):
    vols = spec.setdefault("volumes", [])
    if not any(v.get("name") == VOL_NAME for v in vols):
        vols.append({"name": VOL_NAME,
                     "hostPath": {"path": HOST_PATH, "type": "Directory"}})
    mounts = container.setdefault("volumeMounts", [])
    if not any(m.get("name") == VOL_NAME for m in mounts):
        mounts.append({"name": VOL_NAME, "mountPath": MOUNT_PATH,
                       "readOnly": True})
    # writable output mount so InstrumentedScheduler can drop sched_steps.jsonl
    # straight onto the host (no collector, survives pod restart via append).
    if not any(v.get("name") == OUT_VOL_NAME for v in vols):
        vols.append({"name": OUT_VOL_NAME,
                     "hostPath": {"path": OUT_HOST_PATH,
                                  "type": "DirectoryOrCreate"}})
    if not any(m.get("name") == OUT_VOL_NAME for m in mounts):
        mounts.append({"name": OUT_VOL_NAME, "mountPath": OUT_MOUNT_PATH})


def ensure_injected(container):
    """Insert `${SCHED_EXTRA_ARGS} \\` into the vllm serve command, once."""
    args = container.get("args") or []
    if not args:
        sys.exit("vllm container has no args (unexpected)")
    script = args[0]
    if "SCHED_EXTRA_ARGS" in script:
        return False                      # already injected
    if ANCHOR not in script:
        sys.exit(f"anchor {ANCHOR!r} not found in the launch script; "
                 "the deployment changed — inspect before re-running")
    # keep the original indentation of the anchor line
    out_lines = []
    injected = False
    for line in script.splitlines(keepends=True):
        out_lines.append(line)
        if not injected and ANCHOR in line:
            indent = line[: len(line) - len(line.lstrip())]
            out_lines.append(f"{indent}${{SCHED_EXTRA_ARGS}} \\\n")
            injected = True
    container["args"][0] = "".join(out_lines)
    return True


def show():
    obj = get_lws()
    c = vllm_container(obj["spec"]["leaderWorkerTemplate"]["workerTemplate"]["spec"])
    env = {e["name"]: e.get("value") for e in c.get("env", [])}
    print("policy args :", repr(env.get("SCHED_EXTRA_ARGS")))
    print("PYTHONPATH  :", env.get("PYTHONPATH"))
    print("step log    :", repr(env.get("SCHED_STEP_LOG")))
    print("migration   :", env.get("LLUMNIX_ENABLE_MIGRATION"))
    print("mount       :", any(m.get("name") == VOL_NAME
                               for m in c.get("volumeMounts", [])))
    print("injected    :", "SCHED_EXTRA_ARGS" in (c.get("args") or [""])[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", choices=sorted(POLICY_ARGS))
    ap.add_argument("--migration", choices=["on", "off"])
    ap.add_argument("--step-log", metavar="PATH",
                    help="for --policy instrumented: container path under "
                         f"{OUT_MOUNT_PATH} to write sched_steps.jsonl "
                         "(host: %s). Empty/unset disables logging."
                         % OUT_HOST_PATH)
    ap.add_argument("--restart", action="store_true",
                    help="delete neutral-0 so the change takes effect now")
    ap.add_argument("--show", action="store_true")
    a = ap.parse_args()

    if a.show:
        show()
        return
    if not a.policy:
        ap.error("--policy is required (or use --show)")

    obj = get_lws()
    spec = obj["spec"]["leaderWorkerTemplate"]["workerTemplate"]["spec"]
    c = vllm_container(spec)

    ensure_mount(spec, c)
    set_env(c, "PYTHONPATH", MOUNT_PATH)
    set_env(c, "SCHED_EXTRA_ARGS", POLICY_ARGS[a.policy])
    # SCHED_STEP_LOG gates the per-step logging in InstrumentedScheduler.
    # Set it for the instrumented policy (default filename if --step-log
    # omitted); clear it otherwise so no stale log path leaks across policies.
    if a.policy == "instrumented":
        set_env(c, "SCHED_STEP_LOG",
                a.step_log or f"{OUT_MOUNT_PATH}/sched_steps.jsonl")
    else:
        set_env(c, "SCHED_STEP_LOG", "")
    if a.migration:
        set_env(c, "LLUMNIX_ENABLE_MIGRATION", "1" if a.migration == "on" else "0")
    newly = ensure_injected(c)

    # strip server-managed fields so apply is clean
    obj["metadata"].pop("resourceVersion", None)
    obj["metadata"].pop("uid", None)
    obj["metadata"].pop("creationTimestamp", None)
    obj["metadata"].pop("generation", None)
    obj.pop("status", None)

    r = subprocess.run(["kubectl", "-n", NS, "apply", "-f", "-"],
                       input=json.dumps(obj), capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"apply failed: {r.stderr.strip()}")
    print(f"[sched] policy={a.policy} args={POLICY_ARGS[a.policy]!r} "
          f"migration={a.migration or 'unchanged'} "
          f"(command injection: {'added' if newly else 'already present'})")
    print(r.stdout.strip())

    if a.restart:
        subprocess.run(["kubectl", "-n", NS, "delete", "pod", "neutral-0",
                        "--ignore-not-found"], check=False)
        print("[sched] neutral-0 deleted; LWS will recreate it")


if __name__ == "__main__":
    main()
