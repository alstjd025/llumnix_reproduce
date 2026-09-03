#!/usr/bin/env python3
"""Switch the serving fleet from one model (and fleet shape) to another.

WHY THIS EXISTS. The model identity is written in six places that have to move
together, and five of them are silent when they disagree:

  1. the LeaderWorkerSet's `llumnix.io/model` label -- what the control plane
     believes it is serving;
  2. the model argument of `vllm serve` inside the worker's start script -- what
     is actually loaded;
  3. that script's `--max-model-len` -- a value above the model's own
     max_position_embeddings makes vLLM refuse to start, and one far below it
     silently truncates long prompts;
  4. TP_SIZE and DP_SIZE_LOCAL -- how the eight GPUs are cut into instances;
  5. the gateway's `--tokenizer-path` -- the gateway counts prompt tokens for
     the scheduler with THIS tokenizer, so leaving it on the old model makes
     every prefill estimate wrong by however much the two vocabularies differ,
     with nothing in any log saying so;
  6. the gateway's `--max-model-len`, same reason as 3.

Only the second of those stops anything when it is wrong. The fifth is the one
that quietly corrupts a measurement, which is why this script sets all six from
one table and then asks the engine what it actually loaded.

The offline profile tables (ttft.json, tpot.json, fluidserve.json) are a
property of the model AND the hardware, and they are NOT switched here: they
live per model under deploy/profiling/<profile_dir>/ and are loaded into the
`llumnix-profiling` ConfigMap separately, because a newly added model does not
have them yet and measuring them is the first thing done after a switch.

    python3 ms_dev/scripts/switch_model.py --list
    python3 ms_dev/scripts/switch_model.py --to qwen25-72b-b200-tp2
    python3 ms_dev/scripts/switch_model.py --verify
"""
import argparse
import json
import subprocess
import sys
import time

NS = "llumnix"
HUB = "/hf-cache/hub"

# One row per fleet configuration. `snapshot` is the commit directory under the
# hub cache; it is pinned rather than globbed so that a second snapshot appearing
# in the shared cache cannot change which weights the gateway tokenizes with.
MODELS = {
    "llama31-70b-b200-tp2": dict(
        model="meta-llama/Meta-Llama-3.1-70B-Instruct",
        repo="models--meta-llama--Meta-Llama-3.1-70B-Instruct",
        snapshot="1605565b47bb9346c5515c34102e054115b4f98b",
        label="Meta-Llama-3.1-70B-Instruct",
        max_model_len=40960,   # the model allows 131072; this is our own ceiling
        tp=2, dp=4,
        profile_dir="llama31-70b-b200-tp2",
    ),
    "qwen25-72b-b200-tp2": dict(
        model="Qwen/Qwen2.5-72B-Instruct",
        repo="models--Qwen--Qwen2.5-72B-Instruct",
        snapshot="495f39366efef23836d0cfae4fbe635880d2be31",
        label="Qwen2.5-72B-Instruct",
        # 32768 is the model's own max_position_embeddings, not a choice. The
        # workload fits: the longest single request measured across the three
        # classes is 16,696 input + 7,830 output = 17,588 tokens (EXP-110 run at
        # 10 req/s), so there is about 1.9x headroom.
        max_model_len=32768,
        tp=2, dp=4,
        profile_dir="qwen25-72b-b200-tp2",
    ),
}


# Fields the API server owns. `kubectl apply` records whatever it is given as the
# object's last-applied-configuration, so applying a live object verbatim bakes
# resourceVersion, uid, creationTimestamp and generation into that annotation.
# The next tool that applies the same object WITHOUT them -- which is what
# set_engine_sched.py correctly does -- then produces a three-way merge whose
# patch DELETES resourceVersion, and the LeaderWorkerSet CRD rejects the update
# with "resourceVersion: Invalid value: 0: must be specified for an update".
# That is not a hypothetical: it happened on 2026-09-03 and left the engine
# running the previous scheduler while the driver carried on.
def strip_server_fields(obj):
    md = obj.get("metadata", {})
    for k in ("resourceVersion", "uid", "creationTimestamp", "generation",
              "managedFields", "selfLink"):
        md.pop(k, None)
    obj.pop("status", None)
    return obj


def kubectl(args, inp=None, check=True):
    r = subprocess.run(["kubectl", "-n", NS] + args, input=inp,
                       capture_output=True, text=True)
    if check and r.returncode != 0:
        sys.exit(f"kubectl {' '.join(args)} failed:\n{r.stderr}")
    return r.stdout


def current():
    """What the fleet is serving right now, asked of each component separately."""
    out = {}
    lws = json.loads(kubectl(["get", "lws", "neutral", "-o", "json"]))
    lt = lws["spec"]["leaderWorkerTemplate"]
    wt = lt["workerTemplate"]
    out["label"] = wt["metadata"]["labels"].get("llumnix.io/model")
    c = [c for c in wt["spec"]["containers"] if c["name"] == "vllm"][0]
    script = "\n".join(c.get("command", []) + c.get("args", []))
    for line in script.split("\n"):
        s = line.strip().rstrip("\\").strip()
        if s.endswith("-Instruct") and "/" in s and not s.startswith("#"):
            out["serve_model"] = s
        if s.startswith("--max-model-len"):
            out["engine_max_len"] = s.split()[-1]
    env = {e["name"]: e.get("value", "") for e in c.get("env", [])}
    out["tp"], out["dp"] = env.get("TP_SIZE"), env.get("DP_SIZE_LOCAL")
    gw = json.loads(kubectl(["get", "deploy", "gateway", "-o", "json"]))
    gc = gw["spec"]["template"]["spec"]["containers"][0]
    argv = gc.get("command", []) + gc.get("args", [])
    for i, a in enumerate(argv):
        if a == "--tokenizer-path":
            out["tokenizer"] = argv[i + 1]
        if a == "--max-model-len":
            out["gw_max_len"] = argv[i + 1]
    raw = kubectl(["get", "cm", "llumnix-model", "-o", "json"], check=False)
    try:
        out["runner_model"] = json.loads(raw)["data"]["MODEL_ID"]
    except Exception:
        out["runner_model"] = None
    return out


def switch(key):
    m = MODELS[key]
    tok = f"{HUB}/{m['repo']}/snapshots/{m['snapshot']}"

    lws = json.loads(kubectl(["get", "lws", "neutral", "-o", "json"]))
    lt = lws["spec"]["leaderWorkerTemplate"]
    wt = lt["workerTemplate"]
    wt["metadata"]["labels"]["llumnix.io/model"] = m["label"]
    c = [c for c in wt["spec"]["containers"] if c["name"] == "vllm"][0]

    # The start script is a single heredoc-sized string, and which field holds
    # it is not fixed: this deployment has command=["/bin/bash","-c"] with the
    # script in args[0], but a container that inlines everything in `command` is
    # equally valid. Rewriting both fields costs nothing and removes a guess that
    # was wrong the first time it was made.
    hits = {"model": 0, "maxlen": 0}
    for field in ("command", "args"):
        if not c.get(field):
            continue
        new = []
        for part in c[field]:
            lines = []
            for line in part.split("\n"):
                s = line.strip().rstrip("\\").strip()
                if s.endswith("-Instruct") and "/" in s and not s.startswith("#"):
                    line = line.replace(s, m["model"]); hits["model"] += 1
                elif s.startswith("--max-model-len"):
                    line = line.replace(s.split()[-1], str(m["max_model_len"]))
                    hits["maxlen"] += 1
                lines.append(line)
            new.append("\n".join(lines))
        c[field] = new
    # A replacement that matched nothing leaves the old model running under the
    # new label, which is the single worst outcome this script can produce.
    if hits["model"] != 1 or hits["maxlen"] != 1:
        sys.exit(f"ABORT: expected exactly one model line and one --max-model-len, "
                 f"found {hits}. The start script's shape changed; fix this script "
                 f"rather than letting it patch the wrong line.")
    for e in c.get("env", []):
        if e["name"] == "TP_SIZE":
            e["value"] = str(m["tp"])
        if e["name"] == "DP_SIZE_LOCAL":
            e["value"] = str(m["dp"])
    kubectl(["apply", "-f", "-"], inp=json.dumps(strip_server_fields(lws)))
    print(f"  LWS patched: {m['model']}, max_model_len={m['max_model_len']}, "
          f"TP={m['tp']} x DP={m['dp']}")

    gw = json.loads(kubectl(["get", "deploy", "gateway", "-o", "json"]))
    gc = gw["spec"]["template"]["spec"]["containers"][0]
    fld = "command" if any(a.startswith("--tokenizer-path") or a == "--tokenizer-path"
                           for a in gc.get("command", [])) else "args"
    argv = gc[fld]
    for i, a in enumerate(argv):
        if a == "--tokenizer-path":
            argv[i + 1] = tok
        if a == "--max-model-len":
            argv[i + 1] = str(m["max_model_len"])
    kubectl(["apply", "-f", "-"], inp=json.dumps(strip_server_fields(gw)))
    print(f"  gateway patched: tokenizer={tok}")

    # The load generator names the model in every request body, and that name
    # was hardcoded in fifteen runner templates. Rather than fifteen places that
    # can each be forgotten, the templates read it from this ConfigMap, so the
    # switch is the single source of truth and a template whose ConfigMap is
    # missing fails to start the pod instead of quietly asking a live engine for
    # a model it does not serve.
    cm = {"apiVersion": "v1", "kind": "ConfigMap",
          "metadata": {"name": "llumnix-model", "namespace": NS},
          "data": {"MODEL_ID": m["model"], "MAX_MODEL_LEN": str(m["max_model_len"]),
                   "PROFILE_DIR": m["profile_dir"]}}
    kubectl(["apply", "-f", "-"], inp=json.dumps(cm))
    print(f"  configmap/llumnix-model set: MODEL_ID={m['model']}")

    kubectl(["delete", "pod", "neutral-0", "--wait=false"], check=False)
    kubectl(["rollout", "restart", "deploy/gateway"])
    print("  engine pod deleted and gateway restarted; the engine takes about "
          "8 minutes to serve")


def verify(expect=None):
    cur = current()
    print("  as configured:")
    for k in ("label", "serve_model", "engine_max_len", "tp", "dp", "tokenizer",
              "gw_max_len", "runner_model"):
        print(f"    {k:<15} {cur.get(k)}")
    ip = kubectl(["get", "pod", "neutral-0", "-o", "jsonpath={.status.podIP}"], check=False).strip()
    served = None
    if ip:
        r = subprocess.run(["curl", "-sf", "-m", "5", f"http://{ip}:8000/v1/models"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            try:
                d = json.loads(r.stdout)["data"][0]
                served = d["id"]
                print(f"  engine reports:  {served}  max_model_len={d.get('max_model_len')}")
            except Exception:
                pass
    if served is None:
        print("  engine reports:  (not serving yet)")
    ok = True
    if expect:
        m = MODELS[expect]
        if cur.get("serve_model") != m["model"]:
            print(f"  MISMATCH serve_model: {cur.get('serve_model')} != {m['model']}"); ok = False
        if m["repo"] not in (cur.get("tokenizer") or ""):
            print(f"  MISMATCH tokenizer does not belong to {m['model']}"); ok = False
        if cur.get("runner_model") != m["model"]:
            print(f"  MISMATCH configmap/llumnix-model says {cur.get('runner_model')} "
                  f"-- the load generator would name that model in every request"); ok = False
        if served is not None and served != m["model"]:
            print(f"  MISMATCH engine serves {served}"); ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--to", choices=sorted(MODELS))
    ap.add_argument("--verify", nargs="?", const="", default=None)
    a = ap.parse_args()
    if a.list:
        for k, m in MODELS.items():
            print(f"  {k:<24} {m['model']:<40} len={m['max_model_len']:<6} "
                  f"TP={m['tp']} x DP={m['dp']}  profile={m['profile_dir']}")
        sys.exit(0)
    if a.to:
        print(f"switching to {a.to}")
        switch(a.to)
        sys.exit(verify(a.to))
    if a.verify is not None:
        sys.exit(verify(a.verify or None))
    ap.print_help()
