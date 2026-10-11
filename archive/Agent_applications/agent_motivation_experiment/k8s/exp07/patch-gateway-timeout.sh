#!/bin/bash
# EXP-10: run the host-built gateway binary (feat/kv-admission-threshold +
# patches/sglang-go-compat shim, built in-cluster by builder-gateway.yaml)
# inside the stock gateway image via hostPath, with the SSE read timeout
# and response-header timeout raised so an intentionally unbounded queue
# can be measured without the gateway killing >5min-queued requests.
#
# Usage: patch-gateway-timeout.sh [<sse_timeout> [<hdr_timeout>]]   (default 24h 24h)
#        patch-gateway-timeout.sh revert     # back to stock image binary
# Args below mirror the stock gateway args (no fast-fail). The 64Gi memory
# limit patch lives on separate fields and is untouched.
set -e
if [ "${1:-}" = "revert" ]; then
  kubectl -n llumnix patch deploy gateway --type=json -p '[
    {"op": "remove", "path": "/spec/template/spec/containers/0/command"},
    {"op": "remove", "path": "/spec/template/spec/containers/0/env"}
  ]' || true
  kubectl -n llumnix rollout status deploy gateway --timeout=180s
  echo "[exp10] gateway reverted to stock binary"
  exit 0
fi
SSE_TO=${1:-24h}
HDR_TO=${2:-24h}
kubectl -n llumnix patch deploy gateway --type=strategic -p "$(cat <<PATCH
spec:
  template:
    spec:
      containers:
      - name: gateway
        command: ["/exp07bin/gateway-exp10"]
        env:
        - name: GATEWAY_SSE_READ_TIMEOUT
          value: "$SSE_TO"
        - name: GATEWAY_RESPONSE_HEADER_TIMEOUT
          value: "$HDR_TO"
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
        - --enable-full-mode-scheduling=true
        volumeMounts:
        - name: exp07bin
          mountPath: /exp07bin
          readOnly: true
      volumes:
      - name: exp07bin
        hostPath:
          path: /home/nxclab/llumnix_reproduce/bin
          type: Directory
PATCH
)"
kubectl -n llumnix rollout status deploy gateway --timeout=180s
echo "[exp10] gateway patched: binary=/exp07bin/gateway-exp10 sse=$SSE_TO hdr=$HDR_TO"
