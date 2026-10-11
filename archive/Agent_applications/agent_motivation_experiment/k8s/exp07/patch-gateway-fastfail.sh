#!/bin/bash
# EXP-07: make gateway rejections FAST-FAIL. On scheduler 429 (no available
# endpoint) the stock gateway retries the /schedule call every 1s for up to 5s
# (--wait-scheduling-timeout) before surfacing 503 to the client. For a clean
# admission semantic the client must see the reject immediately, so set the
# wait to 0s. Image/binary stay stock — args-only change.
#
# Usage: patch-gateway-fastfail.sh [revert]
set -e
if [ "${1:-}" = "revert" ]; then EXTRA=""; else EXTRA='
        - --wait-scheduling-timeout=0s'; fi
kubectl -n llumnix patch deploy gateway --type=strategic -p "$(cat <<PATCH
spec:
  template:
    spec:
      containers:
      - name: gateway
        args:
        - --port
        - "8089"
        - -v
        - "3"
        - --discovery-redis-status-ttl-ms
        - "60000"
        - --scheduler-endpoints
        - scheduler:8088
        - --llm-backend-discovery
        - redis
        - --scheduler-discovery
        - endpoints
        - --discovery-redis-host
        - redis
        - --discovery-redis-port
        - "6379"
        - --scheduling-policy
        - load-balance
        - --tokenizer-path
        - /hf-cache/hub/models--meta-llama--Meta-Llama-3.1-70B-Instruct/snapshots/1605565b47bb9346c5515c34102e054115b4f98b
        - --max-model-len
        - "40960"
        - --enable-full-mode-scheduling=true$EXTRA
PATCH
)"
kubectl -n llumnix rollout status deploy gateway --timeout=180s
echo "[exp07] gateway patched (fast-fail $([ "${1:-}" = revert ] && echo OFF || echo ON))"
