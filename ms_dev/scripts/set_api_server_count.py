#!/usr/bin/env python3
"""Set how many OpenAI API server processes each vLLM instance runs.

WHY THIS EXISTS. vLLM's three per-request timers do not share an origin:

    time_to_first_token = iteration_timestamp - arrival_time   # API server clock
    queued_time         = scheduled_ts - queued_ts             # engine-core events
    prefill_time        = first_token_ts - scheduled_ts        # engine-core events

so TTFT minus (queued + prefill) is, by construction, the time between the API
server receiving a request and the engine core queueing it, plus whatever the
front end takes to hand the first token back. On the eight-instance Llama-3.1-8B
fleet that difference is 26.3 s and 27.4 s on the two instances that sit near
1,024 concurrent sequences, and 5 ms on an instance carrying 56. Established
streams keep flowing at engine pace over the same stretch (client inter-token
p50 53.0 ms against a 55 ms step), so the delay is on the way IN, not on the way
out: one Python process cannot both stream 1,024 responses and admit new
requests.

--api-server-count runs several front-end processes over a shared socket for one
engine core (vllm/entrypoints/cli/serve.py: run_multi_api_server), which is the
only knob that addresses that segment. Raising --max-num-seqs would move the
wrong way: it lets one front end hold more streams.

This is a MEASUREMENT-PATH change to the serving stack. A run made with a
different count is not directly comparable with one made without, and the four
instance fleets never approached this limit (per-instance concurrency 144-257),
so the expectation there is no effect -- which is itself worth one condition.

    python3 ms_dev/scripts/set_api_server_count.py --show
    python3 ms_dev/scripts/set_api_server_count.py --count 4 --restart
"""
import argparse
import json
import subprocess
import sys

NS = "llumnix"
LWS = "neutral"
ANCHOR = "--gpu-memory-utilization"


# The one thing --api-server-count > 1 breaks, and the reason it is fixed here
# rather than in a code overlay.
#
# LLUMNIX_VLLM_API_SERVER_PORT is set inside the API SERVER process
# (vllm/entrypoints/openai/api_server.py: build_async_engine_client). The
# Llumlet -- which registers the instance with the CMS and is the only thing our
# scheduler ever hears from -- runs as a child of the ENGINE CORE
# (vllm/v1/engine/core.py creates VLLMLlumletProxy, which spawns it), and reads
# that variable with os.getenv.
#
#   --api-server-count 1  the `vllm serve` process sets the variable and THEN
#                         spawns the engine core, so the engine core and its
#                         Llumlet inherit it.
#   --api-server-count 4  the launcher goes through run_multi_api_server, which
#                         never calls build_async_engine_client. It spawns the
#                         engine core FIRST and the four API servers as
#                         SIBLINGS, so the engine core never sees the variable.
#                         int(os.getenv(...)) then raises and the Llumlet exits
#                         with "Can not parse api_server from envs var" --
#                         measured, once per instance, eight times on this fleet.
#                         Nothing registers, the scheduler sees no live instance,
#                         and the engine core's status pushes fail UNAVAILABLE.
#
# Exporting it in the launch shell, where $PORT is already known, makes every
# descendant inherit it. The value is the one the API servers would set
# themselves (they share a single listening socket, so there is one port), so it
# is correct at any count and harmless at 1.
PORT_ENV = "LLUMNIX_VLLM_API_SERVER_PORT=$PORT"
ENV_ANCHOR = "BLLM_KVTRANS_PORT_BASE="


def ensure_port_env(parts):
    """Idempotently put PORT_ENV on the line above the serve invocation."""
    if any(PORT_ENV in part for part in parts):
        return parts, False
    out, hits = [], 0
    for part in parts:
        lines = []
        for line in part.split("\n"):
            if ENV_ANCHOR in line and "vllm serve" not in line:
                indent = line[: len(line) - len(line.lstrip())]
                lines.append(f"{indent}{PORT_ENV} \\")
                hits += 1
            lines.append(line)
        out.append("\n".join(lines))
    if hits != 1:
        sys.exit(f"ABORT: expected exactly one {ENV_ANCHOR!r} line, found {hits}")
    return out, True


def kubectl(args, inp=None, check=True):
    r = subprocess.run(["kubectl", "-n", NS] + args, input=inp,
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"kubectl {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout


def strip_server_fields(obj):
    """kubectl apply of a live object bakes resourceVersion into the
    last-applied annotation and the next tool to apply it without them gets a
    patch that DELETES resourceVersion, which the CRD rejects. Same guard as
    switch_model.py."""
    md = obj.get("metadata", {})
    for k in ("resourceVersion", "uid", "creationTimestamp", "generation",
              "managedFields", "selfLink"):
        md.pop(k, None)
    obj.pop("status", None)
    return obj


def vllm_container(lws):
    spec = lws["spec"]["leaderWorkerTemplate"]["workerTemplate"]["spec"]
    for c in spec["containers"]:
        if c["name"] == "vllm":
            return c
    sys.exit("no 'vllm' container in the LWS pod spec")


def current(script, flag):
    for line in script.split("\n"):
        s = line.strip().rstrip("\\").strip()
        if s.startswith(flag):
            return s.split()[-1]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int)
    # The engine's concurrent-sequence limit. vLLM V1's OpenAI-server default is
    # 1024 and we never set it; on the eight-instance Llama-3.1-8B fleet the two
    # loaded engines pin at exactly that, so it has to be separable from the
    # front-end bottleneck before either is acted on.
    ap.add_argument("--max-num-seqs", type=int, dest="max_num_seqs")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--restart", action="store_true")
    a = ap.parse_args()

    lws = json.loads(kubectl(["get", "lws", LWS, "-o", "json"]))
    c = vllm_container(lws)
    field = "args" if c.get("args") else "command"
    script = c[field][0] if len(c[field]) == 1 else "\n".join(c[field])

    if a.show or (a.count is None and a.max_num_seqs is None):
        print(f"  --api-server-count: {current(script, '--api-server-count') or '(unset -> 1)'}")
        print(f"  --max-num-seqs:     {current(script, '--max-num-seqs') or '(unset -> engine default)'}")
        return 0

    # Rewrite in place. The flag is written on its own line just above
    # --gpu-memory-utilization so that the serve command stays readable and the
    # anchor is one this script can find again.
    # What this call sets. A flag left as None is not touched; a flag whose value
    # means "the default" is removed rather than written, so the start script
    # always shows exactly the non-default settings.
    want = {}
    if a.count is not None:
        want["--api-server-count"] = a.count if a.count > 1 else None
    if a.max_num_seqs is not None:
        want["--max-num-seqs"] = a.max_num_seqs if a.max_num_seqs > 0 else None

    parts = c[field]
    hits = 0
    new_parts = []
    for part in parts:
        lines = []
        for line in part.split("\n"):
            s = line.strip().rstrip("\\").strip()
            if any(s.startswith(f) for f in want):
                continue                      # drop the old one, re-add below
            if s.startswith(ANCHOR):
                indent = line[: len(line) - len(line.lstrip())]
                for f, v in want.items():
                    if v is not None:
                        lines.append(f"{indent}{f} {v} \\")
                hits += 1
            lines.append(line)
        new_parts.append("\n".join(lines))
    if hits != 1:
        sys.exit(f"ABORT: expected exactly one {ANCHOR!r} line, found {hits}. "
                 f"The start script changed shape; fix this script rather than "
                 f"letting it patch the wrong line.")
    new_parts, added = ensure_port_env(new_parts)
    if added:
        print(f"  LWS patched: exported {PORT_ENV} so the engine core and its "
              f"Llumlet inherit it")
    c[field] = new_parts

    kubectl(["apply", "-f", "-"], inp=json.dumps(strip_server_fields(lws)))
    for f, v in want.items():
        print(f"  LWS patched: {f} {v if v is not None else '(removed, back to the default)'}")

    if a.restart:
        kubectl(["delete", "pod", "neutral-0", "--wait=false"], check=False)
        print("  neutral-0 deleted; LWS will recreate it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
