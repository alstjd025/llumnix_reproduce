---
name: nxc7-k3s-install-recipe
description: Working recipe to run k3s (single-node k8s) inside the NXC7-1 GPU Docker container for Llumnix
metadata: 
  node_type: memory
  type: project
  originSessionId: 18c91790-645d-41fa-ab05-5ab03b3de80c
---

Goal: run Llumnix v1 (k8s-only) on NXC7-1, which is a GPU Docker container ([[nxc7-is-docker-container]]). k3s works but needs several container-nesting workarounds. Privilege via `gcsudo <cmd>` ([[nxc7-sudo-constraint]]).

**Installed (2026-06-23):**
- NVIDIA Container Toolkit 1.19.1 (apt, from nvidia repo). Driver 580.95.05, 2× B200.
- k3s v1.35.5+k3s1, kubectl + kustomize v5.7.1 (bundled). kubeconfig copied to `~/.kube/config` (mode 600). containerd 2.2.3-k3s1.

**k3s config** `/etc/rancher/k3s/config.yaml`:
```
default-runtime: nvidia        # so GPU pods get device injection without editing YAMLs
disable-cloud-controller: true # CRITICAL: avoids the 12s CCM-crash restart loop
disable-helm-controller: true
disable: [traefik, metrics-server, servicelb]
```
ExecStart has `--snapshotter overlayfs --write-kubeconfig-mode 644 --disable traefik` (set via INSTALL_K3S_EXEC at install).

**⚠️ SNAPSHOTTER UPDATE (2026-06-23, supersedes earlier `native` choice):** `native` snapshotter copies every layer fully (no sharing) → pulling the large multi-layer `vllm` image (≈25GB, 64 layers) amplified to ~350GB during extract and filled the 400GB overlay rootfs → pod Evicted (`no space left on device`). Fixes tried: fuse-overlayfs is DEAD (can mknod `/dev/fuse` c 10 229 but OPENING it is blocked by the container's cgroup-v2 device policy → `open /dev/fuse: Operation not permitted`, unfixable from inside). **Working fix = relocate containerd root to the local xfs `/NHNHOME` (800G) and use the `overlayfs` snapshotter** (overlay-on-overlay only failed because the container ROOT is overlay; overlay-on-**xfs** works, and whiteout `mknod c 0 0` succeeds on xfs but fails on the overlay rootfs). overlayfs shares layers → no amplification. Storage available: `/NHNHOME` xfs 800G (local nvme), `/NHNHOME/{storage,huggingface,share}` are Lustre 60TB.

**Container workarounds that MUST be in place before k3s starts (lost on container recreation):**
1. Relocate containerd root to xfs + overlayfs snapshotter:
   `gcsudo mkdir -p /NHNHOME/k3s-containerd; gcsudo rm -rf /var/lib/rancher/k3s/agent/containerd; gcsudo mkdir -p /var/lib/rancher/k3s/agent/containerd`
   `gcsudo nsenter -t 1 -m -- mount --bind /NHNHOME/k3s-containerd /var/lib/rancher/k3s/agent/containerd`
   then run k3s with `--snapshotter overlayfs`. (bind-mount lives in PID1 ns, survives k3s restart, not container recreation)
2. `gcsudo ln -s /dev/console /dev/kmsg` — kubelet needs /dev/kmsg; mknod'd node gives EPERM (no CAP_SYSLOG), symlink to console works. (persists in /dev tmpfs across k3s restarts)
3. kubelet sysctls on read-only /proc/sys — bind files into **PID1 mount ns** (gcsudo isolates its own ns!):
   `printf '1\n'>/tmp/oc; gcsudo nsenter -t 1 -m -- mount --bind /tmp/oc /proc/sys/vm/overcommit_memory`
   `printf '10\n'>/tmp/pn; gcsudo nsenter -t 1 -m -- mount --bind /tmp/pn /proc/sys/kernel/panic`
4. kube-proxy net sysctls on read-only /proc/sys — bind a fresh writable proc over /proc/sys/net in PID1 ns:
   `gcsudo nsenter -t 1 -m -- sh -c 'mkdir -p /run/freshproc; mount -t proc proc /run/freshproc; mount --bind /run/freshproc/sys/net /proc/sys/net'`
5. After node registers, CCM is disabled so the node carries `node.cloudprovider.kubernetes.io/uninitialized:NoSchedule` — remove it:
   `kubectl taint node nxc7-1 node.cloudprovider.kubernetes.io/uninitialized-`

Result: node `nxc7-1` Ready/control-plane, NRestarts=0 stable. The bind-mounts (3,4) live in PID1's mount ns and survive `systemctl restart k3s` but NOT container recreation.

**COMPLETED 2026-06-23 — cluster fully ready:**
- NVIDIA k8s device plugin v0.17.1 deployed (`kube-system` ds `nvidia-device-plugin-daemonset`). Node advertises `nvidia.com/gpu: 2` (capacity=allocatable=2). End-to-end verified: a pod requesting `nvidia.com/gpu:1` ran `nvidia-smi` and saw the B200. (Benign NVML warnings `result=11` re persistenced/MPS socket; GPU itself works.)
- LWS installed (`lws-system`, 2 controller replicas Running, CRD `leaderworkersets.leaderworkerset.x-k8s.io`).
- containerd apparmor disabled via drop-in `config-v3.toml.d/10-disable-apparmor.toml` (`disable_apparmor = true`) — 6th container fix: securityfs ro blocked apparmor profile load (`exit 226`). Drop-ins ARE honored (containerd reads them via `imports`; they don't appear in generated config.toml).
- Toolchain verified: `kubectl kustomize` builds these Llumnix modes cleanly → neutral/full-mode-scheduling/load-balance, neutral/lite-mode-scheduling/load-balance, pd/full-mode-scheduling/load-balance.

**Prebuilt image pull VERIFIED (2026-06-23, overlayfs on xfs):** Alibaba Beijing registry reachable from here (Cloudflare-fronted, ~8MiB/s). `gateway` (92MB) and the big `vllm:20260306-165123` image both pull+run via a pod (kubelet/CRI). vllm used only ~30G on xfs (no native amplification) and is cached for deploy. Note: `k3s ctr images pull` manual command is finicky (defaults to overlayfs snapshotter / needs `--platform`); use a throwaway pod to pre-pull/verify instead.

**CURRENT STATUS (2026-06-23): infra + images ready; ONLY the Llumnix workload deploy remains (not yet run, awaiting user OK).** Exact remaining steps: (1) edit `neutral/full-mode-scheduling/load-balance/neutral.yaml` → `DP_SIZE_LOCAL: "4"→"2"` and `nvidia.com/gpu: "4"→"2"` (requests+limits); (2) drop `monitoring.yaml` from `deploy/base/kustomization.yaml`; (3) `cd deploy && ./group_deploy.sh llumnix neutral/full-mode-scheduling/load-balance`; (4) vLLM downloads Qwen3-30B-A3B-FP8 then serves via Gateway :8089 (OpenAI API).

**Deploy-time gotchas for Llumnix (workloads not yet deployed):**
- Only 2 GPUs here. Default neutral/pd want TP_SIZE=4 → must lower TP_SIZE to 1 or 2 in neutral.yaml/prefill.yaml/decode.yaml.
- Default images come from `llumnix-registry.cn-beijing.cr.aliyuncs.com/llumnix` (Alibaba Beijing) — verify pull works from Korea or mirror; pass `--repository/--*-tag` to group_deploy.sh.
- `neutral/lite-mode-scheduling/round-robin/gateway.yaml` has a YAML indentation BUG at line 44 (`pip3 install packaging` under-indented vs the `- |` block scalar) → kustomize fails. The other modes are fine. Fix indentation if using round-robin.
- load-balance/full modes emit PodMonitor/ServiceMonitor (monitoring.coreos.com) — need Prometheus-Operator CRDs at apply time, else those objects fail (pods still run).
- PD-KVS / SLO-aware need RDMA `/dev/infiniband/` which is absent here ([[nxc7-is-docker-container]]).

See [[llumnix-env-assessment]], [[llumnix-deploy-modes]].
