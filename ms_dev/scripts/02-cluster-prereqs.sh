#!/usr/bin/env bash
#
# LAYER 2 — cluster prerequisites that PERSIST in k3s state across k3s restarts
# (they only need reinstalling if the k3s datastore itself is wiped). Idempotent:
# already-installed components are detected and skipped.
#
#   - NVIDIA k8s device plugin  -> makes the node advertise nvidia.com/gpu
#   - LeaderWorkerSet (LWS) CRD + controller -> REQUIRED by every Llumnix deploy mode
#
# NOTE: k3s here is configured with default-runtime=nvidia (via /etc/rancher/k3s/config.yaml),
# so GPU pods get device injection without per-pod runtimeClassName.
set -uo pipefail
cd "$(dirname "$0")"; source ./lib.sh

have kubectl || die "kubectl not found. Run ./01-prepare-k3s-host.sh first."
node_ready   || die "k3s node '$NODE' not Ready. Run ./01-prepare-k3s-host.sh first."

# --- NVIDIA device plugin ---
if kubectl get ds -n kube-system nvidia-device-plugin-daemonset >/dev/null 2>&1; then
  log "NVIDIA device plugin already installed — skip"
else
  log "Installing NVIDIA device plugin $DEVICE_PLUGIN_VERSION"
  kubectl create -f "https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${DEVICE_PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml" \
    || die "device plugin install failed (check network to raw.githubusercontent.com)"
fi

# --- LeaderWorkerSet ---
if kubectl get crd leaderworkersets.leaderworkerset.x-k8s.io >/dev/null 2>&1; then
  log "LeaderWorkerSet already installed — skip"
else
  log "Installing LeaderWorkerSet $LWS_VERSION"
  kubectl apply --server-side -f "https://github.com/kubernetes-sigs/lws/releases/download/${LWS_VERSION}/manifests.yaml" \
    || die "LWS install failed (check network to github.com)"
fi

# --- wait until GPUs are schedulable and LWS controller is up ---
log "Waiting for node to advertise GPUs..."
for _ in $(seq 1 40); do
  gpus=$(kubectl get node "$NODE" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null)
  [ -n "${gpus:-}" ] && [ "$gpus" != "0" ] && break
  sleep 3
done
gpus=$(kubectl get node "$NODE" -o jsonpath='{.status.allocatable.nvidia\.com/gpu}' 2>/dev/null)
[ -n "${gpus:-}" ] && [ "$gpus" != "0" ] || die "node is not advertising GPUs (nvidia.com/gpu empty/0)"
log "node '$NODE' allocatable nvidia.com/gpu = $gpus"

kubectl wait --for=condition=Available deploy/lws-controller-manager -n lws-system --timeout=120s 2>/dev/null \
  || warn "LWS controller not Available yet (it may still be starting)"

log "Cluster prerequisites ready. Next: ./03-deploy.sh"
