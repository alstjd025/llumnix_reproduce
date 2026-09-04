#!/usr/bin/env python3
"""Size the Llumnix gateway's CPU allocation.

WHY THIS EXISTS. The gateway is ONE pod that relays every token of the whole
fleet as server-sent events. On the eight-instance Llama-3.1-8B fleet it became
the fleet's capacity limit, and it did so identically for two different
scheduling policies -- which is how it was found:

    offered attainment      FluidServe          Llumnix load-balance
    130 req/s               90.4 / 74.1         99.8 / 99.5
    160 req/s               12.6 / 13.0         19.9 / 19.3

Both collapse in the same interval. Over the same ladder the engines' own
per-token time stayed flat at 29.6-37.6 ms (inside chat's 50 ms budget) and
preemption was 0.03 per thousand generated tokens, so the engines were not the
limit. What broke was DELIVERY: the client received 100% of the tokens the
engines generated up to 69,415 tok/s and 71% at 78,481 tok/s.

At the last good point the gateway was using 7.22 of its 8 CPUs (90%) and was
throttled by the kernel in 22.2% of CFS periods, while 30.5 cores sat free on
the node. So it is the LIMIT that binds, not contention with the engine pod --
which is what makes raising the limit the right fix rather than raising the
CFS weight.

SIZING. 7.22 cores relayed about 70,000 tok/s, i.e. ~9,700 tok/s per core. 24
cores covers roughly 230,000 tok/s, past anything eight Llama-3.1-8B instances
can produce, and fits in the 28-33 cores measured free under load. GOMAXPROCS is
set EQUAL to the limit: more runnable OS threads than the cgroup quota is exactly
what produces CFS throttling, and it is why this deployment already carried
GOMAXPROCS=16 against a limit of 8 (the earlier fix, from 72).

⚠ THIS IS A MEASUREMENT-PATH CHANGE. Every number measured before it was taken
with an 8-CPU gateway, and a run made after it is not directly comparable with
one made before. Record it beside any number that crosses the boundary.

    python3 ms_dev/scripts/set_gateway_cpu.py --show
    python3 ms_dev/scripts/set_gateway_cpu.py --limit 24 --request 8 --restart
"""
import argparse
import json
import subprocess
import sys

NS = "llumnix"


def kubectl(args, inp=None):
    r = subprocess.run(["kubectl", "-n", NS] + args, input=inp,
                       capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"kubectl {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--request", type=int)
    # Left settable rather than derived, because "equal to the limit" is a
    # decision about CFS behaviour and not an arithmetic identity.
    ap.add_argument("--gomaxprocs", type=int)
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--restart", action="store_true")
    a = ap.parse_args()

    d = json.loads(kubectl(["get", "deploy", "gateway", "-o", "json"]))
    c = d["spec"]["template"]["spec"]["containers"][0]
    res = c.setdefault("resources", {})
    env = c.setdefault("env", [])

    def gomax():
        for e in env:
            if e.get("name") == "GOMAXPROCS":
                return e.get("value")
        return None

    if a.show or (a.limit is None and a.request is None and a.gomaxprocs is None):
        print(f"  replicas:        {d['spec'].get('replicas')}")
        print(f"  requests.cpu:    {res.get('requests', {}).get('cpu')}")
        print(f"  limits.cpu:      {res.get('limits', {}).get('cpu')}")
        print(f"  GOMAXPROCS:      {gomax()}")
        return 0

    if a.limit is not None:
        res.setdefault("limits", {})["cpu"] = str(a.limit)
    if a.request is not None:
        res.setdefault("requests", {})["cpu"] = str(a.request)
    gm = a.gomaxprocs if a.gomaxprocs is not None else a.limit
    if gm is not None:
        for e in env:
            if e.get("name") == "GOMAXPROCS":
                e["value"] = str(gm)
                break
        else:
            env.append({"name": "GOMAXPROCS", "value": str(gm)})

    # A request larger than the limit is rejected by the API server, but the
    # error names the field rather than the intent, so say it here.
    rq = int(res.get("requests", {}).get("cpu", 0))
    lm = int(res.get("limits", {}).get("cpu", 0))
    if rq > lm:
        sys.exit(f"ABORT: requests.cpu {rq} exceeds limits.cpu {lm}")

    for k in ("resourceVersion", "uid", "creationTimestamp", "generation",
              "managedFields", "selfLink"):
        d["metadata"].pop(k, None)
    d.pop("status", None)
    kubectl(["apply", "-f", "-"], inp=json.dumps(d))
    print(f"  gateway -> requests.cpu={res['requests']['cpu']} "
          f"limits.cpu={res['limits']['cpu']} GOMAXPROCS={gomax()}")

    if a.restart:
        r = subprocess.run(["kubectl", "-n", NS, "rollout", "status",
                            "deploy/gateway", "--timeout=180s"],
                           capture_output=True, text=True)
        print("  " + (r.stdout or r.stderr).strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
