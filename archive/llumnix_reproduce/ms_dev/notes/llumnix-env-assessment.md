---
name: llumnix-env-assessment
description: "Whether the current NXC7-1 host can run Llumnix v1, and what's missing"
metadata: 
  node_type: memory
  type: project
  originSessionId: 18c91790-645d-41fa-ab05-5ab03b3de80c
---

User (alstjd025) is evaluating whether Llumnix v1 (this repo, `/home/nxclab/llumnix`) can run on the current host `NXC7-1`. Assessment performed 2026-06-23.

**Llumnix v1 is 100% Kubernetes-native.** Deployment = `deploy/group_deploy.sh <group> <kustomize-dir>` which runs `kubectl create namespace` + `group_update.sh` (kustomize build | envsubst | kubectl apply). There is NO non-k8s / bare-metal path. Components ship as prebuilt container images (gateway, scheduler, vllm, discovery), default registry `llumnix-registry.cn-beijing.cr.aliyuncs.com/llumnix` (Alibaba Cloud Beijing — may be slow/blocked outside CN).

**Hardware present (good):** 3× NVIDIA B200 (~183 GB each), 72 cores, ~2.2 TB RAM. RDMA NICs present (`mlx5_0..9` in /sys/class/infiniband).

**Blockers on this host (as of check):**
- NO Kubernetes cluster, NO kubectl, NO kustomize, NO docker/containerd/podman/any container runtime, NO go. Only `envsubst` (gettext 0.21) and python3.12 are present.
- `/dev/infiniband/` is MISSING even though mlx5 devices exist in /sys → RDMA user-space device nodes not exposed. PD-KVS and SLO-aware modes require `/dev/infiniband/` (rdma_cm, uverbs0).
- Default Neutral mode wants 4 GPUs per pod (TP_SIZE=4); only 3 GPUs here → must lower TP_SIZE (1/2) to fit.

**Prereqs Llumnix needs:** k8s ≥1.26, kubectl, kustomize ≥5.0 (or `kubectl kustomize`), envsubst (have it), LeaderWorkerSet (LWS) CRD installed.

**To actually run here:** install a container runtime + a single-node k8s (k3s / kind / minikube) with NVIDIA device plugin, install LWS CRD, then deploy the simplest mode `neutral/lite-mode-scheduling/round-robin` (no scheduler) with TP_SIZE reduced to fit 3 GPUs. Avoid PD-KVS/SLO modes until /dev/infiniband is exposed. User has `gcsudo`/sudo for installs.

See [[llumnix-deploy-modes]] for mode requirements.
