#!/usr/bin/env bash
#
# LAYER 3 — deploy the Llumnix workload (a namespace full of pods). Re-runnable: it just
# re-applies the kustomize tree, so editing the YAML and re-running updates the deployment.
#
# This deploys the IN-REPO, ALREADY-EDITED manifests under
#   deploy/neutral/full-mode-scheduling/load-balance/
# which this project customized for Llama-3-8B on 2x B200 (see ms_dev/README.md "What was changed").
#
# Override target via env:  NS=llumnix2 MODE=neutral/lite-mode-scheduling/load-balance ./03-deploy.sh
set -uo pipefail
cd "$(dirname "$0")"; source ./lib.sh

have kubectl || die "kubectl not found. Run ./01-prepare-k3s-host.sh first."
node_ready   || die "k3s node '$NODE' not Ready. Run ./01-prepare-k3s-host.sh first."
kubectl get crd leaderworkersets.leaderworkerset.x-k8s.io >/dev/null 2>&1 \
  || die "LeaderWorkerSet CRD missing. Run ./02-cluster-prereqs.sh first."
[ -d "$DEPLOY_DIR/$MODE" ] || die "mode dir not found: $DEPLOY_DIR/$MODE"

# On a fresh clone the deploy/ YAML edits (Llama3 / 2x B200 / no-RDMA) aren't present;
# apply them from the bundled patch (idempotent — skips if already there).
ensure_deploy_patch

log "Deploying '$MODE' into namespace '$NS' (model: $MODEL_ID)"
( cd "$DEPLOY_DIR" && ./group_deploy.sh "$NS" "$MODE" ) || die "group_deploy.sh failed"

# Wait for the vLLM (LeaderWorkerSet) pod to load the model and become Ready.
# First model load is a couple of minutes (weights stream from the Lustre HF cache + torch.compile).
log "Waiting for the vLLM pod to become Ready (model load can take a few minutes)..."
if kubectl wait --for=condition=Ready pod \
     -l leaderworkerset.sigs.k8s.io/name=neutral -n "$NS" --timeout=600s 2>/dev/null; then
  log "vLLM pod is Ready."
else
  warn "vLLM pod not Ready within 10min. Current state:"
  kubectl get pods -n "$NS" -o wide
  warn "Inspect with:  kubectl logs -n $NS neutral-0 -c vllm --tail=80"
  exit 1
fi

echo
kubectl get pods -n "$NS" -o wide
echo
log "Deployed. Smoke-test with: ./04-smoke-test.sh"
