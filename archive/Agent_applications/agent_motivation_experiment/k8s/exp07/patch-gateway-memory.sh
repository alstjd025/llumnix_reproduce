#!/bin/bash
# EXP-10: raise the gateway memory limit. The stock deploy caps gateway at
# 4Gi, but gateway memory scales with in-flight requests (it holds the
# connection + request body + LRS state for every request queued in the
# engines' waiting queues). SWE replay bodies are ~90KB (22k tok), so a few
# thousand engine-queued requests blow 4Gi -> OOMKilled crashloop (observed
# in EXP-10 lambda>=10 conditions). This is an experiment-infra artifact,
# not the phenomenon under study, so give the gateway real headroom.
# The host has ~2.2TB RAM; the limit is purely a k8s config choice.
#
# Usage: patch-gateway-memory.sh [<limit> [<request>]]   (default 64Gi 4Gi)
set -e
LIMIT=${1:-64Gi}
REQUEST=${2:-4Gi}
kubectl -n llumnix patch deploy gateway --type=strategic -p "$(cat <<PATCH
spec:
  template:
    spec:
      containers:
      - name: gateway
        resources:
          limits:
            cpu: "8"
            memory: $LIMIT
          requests:
            cpu: "2"
            memory: $REQUEST
PATCH
)"
kubectl -n llumnix rollout status deploy gateway --timeout=180s
echo "[exp10] gateway memory limit=$LIMIT request=$REQUEST"
