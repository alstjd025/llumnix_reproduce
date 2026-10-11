# Running experiments against Llumnix (NXC13, in-cluster)

This directory holds everything to drive the agent workload against the
**Llumnix** serving stack (OSDI'24 reproduce) running in k3s on NXC13, instead
of the original SGLang-on-NXC7 path. The runner executes **as a pod inside the
cluster** so it reaches gateway/scheduler/engines over native k8s networking —
no `kubectl port-forward` (which bottlenecks and drops connections under load).

> TL;DR
> ```bash
> kubectl apply -f k8s/runner-rbac.yaml        # once
> $EDITOR k8s/runner-job.yaml                  # set LAMBDA_LIST / DURATION_MIN / ...
> kubectl -n llumnix delete job bench-runner --ignore-not-found
> kubectl apply -f k8s/runner-job.yaml         # launch
> kubectl -n llumnix logs -f job/bench-runner  # watch
> # results land on the host under results/<YYMMDD_HHMM>_<SESSION>_lambda_<λ>/
> ```

## What is different vs SGLang

The runner has an **engine profile** selected by `--engine`:

| | `--engine sglang` (default) | `--engine llumnix` |
|---|---|---|
| API | `/v1/chat/completions` (LangChain) | `/v1/completions` only (gateway rejects chat) — a Llama-3-templated completions adapter is used |
| Server control | ssh + tmux to NXC7 | k8s (`--restart-per-condition` → `kubectl` restart) |
| Server-side metrics | parse SGLang stderr | scrape Prometheus `/metrics` from every layer into `server_metrics/*.jsonl` |
| Admission (Halo) | supported | none (auto-disabled) |
| Model | Llama-3.3-70B | `meta-llama/Meta-Llama-3-8B-Instruct` (auto-default) |

The SGLang path is untouched; everything below is additive.

## Prerequisites

1. **Llumnix is deployed and healthy** in namespace `llumnix` (see the
   `llumnix_reproduce` repo `ms_dev/scripts/03-deploy.sh`). Check:
   ```bash
   kubectl -n llumnix get pods         # gateway, scheduler, redis, neutral-0 all Running
   ```
2. **RBAC applied once** (lets the runner pod restart pods + read logs):
   ```bash
   kubectl apply -f k8s/runner-rbac.yaml
   ```
3. The host **`.venv` exists** (`python -m venv .venv && .venv/bin/pip install -r
   requirements.txt`). The pod reuses it via a hostPath mount — no pip in-pod.
4. **ShareGPT is cached** on the host (offline in the pod). It lives under
   `/NHNHOME/huggingface/hub/datasets--anon8231489123--ShareGPT_Vicuna_unfiltered/`.
   If missing, pre-download once from the host:
   ```bash
   .venv/bin/python -c "from huggingface_hub import hf_hub_download as d; \
     d(repo_id='anon8231489123/ShareGPT_Vicuna_unfiltered', \
       filename='ShareGPT_V3_unfiltered_cleaned_split.json', repo_type='dataset', \
       revision='192ab2185289094fc556ec8ce5ce1e8e587154ca')"
   ```

## Run an experiment

Edit the `env:` block in [`runner-job.yaml`](runner-job.yaml), then apply.

| env var | meaning | example |
|---|---|---|
| `WORKLOAD` | workload adapter | `sharegpt_request_level_poisson` |
| `MODE` | `poisson-sweep` / `rate-sweep` / `single` | `poisson-sweep` |
| `LAMBDA_LIST` | λ values (requests/sec), comma-separated → one run each | `1,2,5,10` |
| `DURATION_MIN` | minutes per condition | `10` |
| `NUM_CONV` | ShareGPT conversations in the request pool | `500` |
| `SESSION` | result-dir suffix | `pois_sweep` |
| `RESTART_PER_CONDITION` | `1` = cold-restart engine+control plane before each λ (see below) | `1` |
| `EXTRA_ARGS` | any extra `run_experiment.py` flags | `--seed 7` |

```bash
kubectl -n llumnix delete job bench-runner --ignore-not-found
kubectl apply -f k8s/runner-job.yaml
kubectl -n llumnix logs -f job/bench-runner
```

The pod runs `run_experiment.py --engine llumnix --in-cluster ...`. `--in-cluster`
points the load generator at `http://gateway:8089` and the metrics collector at
the k8s DNS names `neutral-0.neutral` (engines :8000-8003), `scheduler` (:8088),
`gateway` (:8089) — all restart-stable.

### Per-condition isolation (`RESTART_PER_CONDITION`)

`1` cold-restarts the engine (`kubectl delete pod neutral-0` → LWS recreates it)
and the control plane (`rollout restart scheduler,gateway`) **before every λ**,
so each condition starts with empty KV/prefix cache and zeroed Prometheus
counters. Costs ~150s per restart (8-GPU model reload). Use this for rigorous,
independent per-experiment measurement.

`0` keeps the stack warm. Metrics are still per-experiment-correct: the analysis
uses **deltas** over the run window for counters, and gauges (running/KV/gateway
current) drain to 0 between conditions on their own. Cheaper; prefix cache may
carry across conditions (small for ShareGPT's distinct prompts).

## Results

Written to the host repo under `results/<YYMMDD_HHMM>_<SESSION>_lambda_<λ>/`
(pod clock is UTC), owned by the host user:

```
metrics.csv                 # per-request: TTFT, TBT, e2e latency, tokens, success/err
tbt_events.jsonl            # per-chunk inter-token detail
agent_logs/                 # per-request prompt/response
run_config.json             # engine profile + all settings (reproducibility)
server_metrics/             # per-run Prometheus time series (1 Hz)
  engine_8000.jsonl … 8003  # vllm:* per engine (running/waiting/KV/tokens/latency histos)
  scheduler.jsonl           # scheduler_* (scheduling + rescheduling counters)
  gateway.jsonl             # request_*/gateway_* (+ instance load if exposed)
  migration_events.log      # scheduler/engine log lines proving rescheduling/KV transfer
```

## Analysis

```bash
# application-side (per-request goodput / latency / throughput)
.venv/bin/python analysis_scripts/request_level/parse_request_summary.py results/<run>
.venv/bin/python analysis_scripts/request_level/summarize_lambda_sweep.py \
    --run-dirs results/*<SESSION>_lambda_* --output-dir results/aggregate_analysis/<SESSION>

# server-side (engine/scheduler/gateway time series -> CSV + migration summary)
.venv/bin/python analysis_scripts/parse_llumnix_metrics.py results/<run>
#   -> results/<run>/analysis/llumnix_server_metrics{,_summary}.csv
```

## Ad-hoc host-side runs (low λ only)

For a quick check without a pod, port-forward and run from the host `.venv`.
**This caps at ~7-8 req/s** (single port-forward); use in-cluster for real load.

```bash
kubectl -n llumnix port-forward svc/gateway 8089:8089 &
kubectl -n llumnix port-forward svc/scheduler 8088:8088 &
kubectl -n llumnix port-forward pod/neutral-0 8000 8001 8002 8003 &
.venv/bin/python run_experiment.py --engine llumnix \
  --workload sharegpt_request_level_poisson --mode single \
  --lambda-val 2 --duration-min 2 --output-dir results --session-name adhoc
# (host-side uses localhost for all layers; do NOT pass --in-cluster)
```

## Migration observability

There is **no completed-migration Prometheus metric** in this build. The numeric
signal is `scheduler_rescheduling_total` (rescheduling *decisions*) — and it is
only exposed once a rescheduling has actually happened; `parse_llumnix_metrics.py`
says so when it is absent. Ground truth ("a KV transfer occurred") lives in
`server_metrics/migration_events.log` (scheduler `rescheduling_policy.go` +
engine `rpc_server.py` lines). Under evenly-balanced gateway dispatch, migrations
rarely trigger; to force one, kill an engine (failover) or drive heterogeneous load.

## Troubleshooting

| symptom | cause / fix |
|---|---|
| `python: command not found` in pod | the image only has `python3`; the job uses the mounted `.venv` interpreter — don't change to `python` |
| pip SSL handshake failure in pod | the image's pip mirror is unusable; we deliberately **don't** pip-install (deps come from the mounted `.venv`) |
| ShareGPT download / `client has been closed` | pod has no HF egress; ensure ShareGPT is pre-cached (Prereq 4) — `HF_HUB_OFFLINE=1` is set |
| `--output-dir` error / results in odd path | always pass `--output-dir` (the runner's built-in default is a stale absolute path); the job sets `/work/results` |
| restart step times out | fixed: engine delete is `--wait=false` + poll for the new pod (UID change) Ready; raise `--restart-timeout` if model load is slow |
| high-λ runs fail with connection errors on host | that's the port-forward ceiling — run in-cluster instead |
