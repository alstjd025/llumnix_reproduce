---
name: llumnix-deploy-modes
description: Llumnix v1 deployment modes and their resource/hardware requirements
metadata: 
  node_type: memory
  type: reference
  originSessionId: 18c91790-645d-41fa-ab05-5ab03b3de80c
---

Llumnix v1 deploy modes (from `docs/source/getting_started/e2e_deploy.md`, deployed via `deploy/group_deploy.sh llumnix <dir>`):

- **Neutral** (`neutral/...`): prefill+decode combined in one vLLM pod. Simplest. No KV transfer, no RDMA needed. Default 4 GPU/pod. Sub-variants: `full-mode-scheduling/load-balance` (recommended, needs Redis CMS), `lite-mode-scheduling/load-balance`, `lite-mode-scheduling/round-robin` (simplest, no Scheduler pod).
- **PD** (`pd/full-mode-scheduling/load-balance`): prefill/decode in separate pods, KV via HybridConnector (kvt). Default 4 GPU each.
- **PD-KVS** (`pd-kvs/...`): PD + distributed KV store (Mooncake) + cache-aware sched. REQUIRES RDMA (`/dev/infiniband/`) + Mooncake-built vLLM image. 1 GPU each.
- **SLO-Aware** (`slo-aware/base` and `slo-aware/adaptive-pd`): PD + TTFT/TPOT SLO enforcement, profiling data auto-downloaded from Alibaba OSS. REQUIRES RDMA. Defaults tuned for Qwen3-32B on H20.

Full mode = white-box, Llumlet embedded in vLLM reports to CMS (Redis), `VLLM_ENABLE_LLUMNIX=1`, higher scheduling quality. Lite mode = black-box, engine-transparent, lower quality.

Change model: edit `vllm serve` line in neutral.yaml/prefill.yaml/decode.yaml + tokenizer path + modelscope download in gateway.yaml.

Custom registry/tags: `--repository`, `--gateway-tag`, `--scheduler-tag`, `--vllm-tag`, `--discovery-tag`, `--mooncake-vllm-tag` flags on group_deploy/group_update.

See [[llumnix-env-assessment]].
