---
name: llumnix-neutral-deploy-working
description: Working Llumnix neutral-mode deployment on NXC7-1 serving Llama3-8B across 2 instances with load balancing
metadata: 
  node_type: memory
  type: project
  originSessionId: 4054beb3-8055-4269-a6cf-502f5344bbfb
---

**DONE 2026-06-23: Llumnix v1 neutral mode is deployed and serving on NXC7-1.** End-to-end verified: Gateway → Scheduler(load-balance) → 2× vLLM (Llama-3-8B-Instruct), requests distributed across both instances. Builds on [[nxc7-k3s-install-recipe]].

Mode: `neutral/full-mode-scheduling/load-balance`, namespace `llumnix`. Deployed via `cd deploy && ./group_deploy.sh llumnix neutral/full-mode-scheduling/load-balance` (default Alibaba registry + tags; images already cached). Pods: gateway, scheduler, redis, neutral-0 (2/2 = vllm + discovery). 2 vLLM engines on ports 8000/8001, DP_SIZE_LOCAL=2, TP_SIZE=1, one B200 each.

**Model = Llama-3-8B-Instruct, NOT the default Qwen3-30B-A3B-FP8.** Chosen because the weights are ALREADY on the Lustre HF cache `/NHNHOME/huggingface/hub/models--meta-llama--Meta-Llama-3-8B-Instruct` (snapshot 8afb486c1db24fe5011ec46dfbe5b5dccdb575c2) → zero download, no HF token (gated repo already resolved). Dense model also avoids the default YAML's MoE/DeepEP/NVSHMEM-IBGDA path which needs RDMA (absent here).

**Edits made to `deploy/neutral/full-mode-scheduling/load-balance/` + base (all in-repo, uncommitted):**
- `base/kustomization.yaml`: removed `monitoring.yaml` (no Prometheus-Operator CRDs).
- `neutral.yaml`: model label + `vllm serve meta-llama/Meta-Llama-3-8B-Instruct`; stripped ALL MoE/EP/DP/DeepEP/NVSHMEM flags+env → simple `--tensor-parallel-size 1 --max-model-len 8192 --gpu-memory-utilization 0.6 --async-scheduling`, one server per local GPU. Env: `VLLM_USE_MODELSCOPE=false`, `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_HUB_CACHE=/hf-cache/hub`. GPU `4→2` (requests+limits), `DP_SIZE_LOCAL 4→2`, discovery `--dp_size_local 4→2`. Added a `hf-cache` **hostPath** volume `path: /NHNHOME/huggingface` mounted RO at `/hf-cache`.
- `gateway.yaml`: deleted the modelscope `download-tokenizer` initContainer; mounted the same hostPath HF cache; `--tokenizer-path /hf-cache/hub/models--meta-llama--Meta-Llama-3-8B-Instruct/snapshots/8afb486c1db24fe5011ec46dfbe5b5dccdb575c2`; `--max-model-len 40960→8192`.

**Two container-in-container fixes that were REQUIRED (would bite any vLLM pod here):**
1. **Caps**: the stock `neutral.yaml` vllm `securityContext` adds `IPC_LOCK` + `SYS_RAWIO` → pod fails `unable to apply caps: operation not permitted` (exit 128, StartError). This Docker container's CapBnd (`a92c35fb`) has NEITHER cap (only SYS_ADMIN etc.). **Removed both caps** — dense no-RDMA inference doesn't need them. (Any future GPU pod adding caps must check `/proc/self/status` CapBnd first.)
2. **Image pull**: vllm container had `imagePullPolicy: Always` → forced re-pull of the cached 25GB image, and the Alibaba auth-token fetch is flaky (intermittent `connection reset`). **Changed to `IfNotPresent`** so the cached image is used offline. (Other components' images pulled fine.)

**Access / test:** `kubectl port-forward -n llumnix svc/gateway 8089:8089` then `curl localhost:8089/v1/completions` (OpenAI text-completion works). NOTE: `/v1/chat/completions` returns 400 "Invalid OpenAI request" — this gateway build only serves the **completions** path, not chat. `/v1/models` works.

**Teardown:** `kubectl delete namespace llumnix`.

Next possible step the user floated: join the 2nd identical B200 host as a k3s worker to get cross-node load balancing (4 GPU / 2 nodes). Live request migration / PD / PD-KVS / SLO still blocked by missing RDMA `/dev/infiniband/`.
