#!/usr/bin/env bash
#
# Smoke-test the running deployment through the Gateway's OpenAI API, and show that the
# Llumnix scheduler load-balances across the two vLLM instances.
#
#   - GETs  /v1/models
#   - POSTs a single /v1/completions  (chat/completions is NOT supported by this gateway build)
#   - fires N requests, then tallies which engine (APIServer pid) served each one
#
# Usage:  ./04-smoke-test.sh [N]        (default N=10)
set -uo pipefail
cd "$(dirname "$0")"; source ./lib.sh

N="${1:-10}"
have kubectl || die "kubectl not found."
have curl    || die "curl not found."
kubectl get svc gateway -n "$NS" >/dev/null 2>&1 || die "gateway service not found in ns '$NS'. Deploy first."

log "port-forward svc/gateway -> localhost:$GATEWAY_PORT"
kubectl port-forward -n "$NS" svc/gateway "$GATEWAY_PORT:8089" >/tmp/llumnix-pf.log 2>&1 &
PF=$!
trap 'kill "$PF" 2>/dev/null' EXIT
for _ in $(seq 1 30); do
  curl -s -o /dev/null -m1 "http://localhost:$GATEWAY_PORT/v1/models" 2>/dev/null && break
  sleep 1
done
curl -s -o /dev/null -m1 "http://localhost:$GATEWAY_PORT/v1/models" 2>/dev/null \
  || die "gateway not reachable on localhost:$GATEWAY_PORT (see /tmp/llumnix-pf.log)"

echo "=== /v1/models ==="
curl -s -m5 "http://localhost:$GATEWAY_PORT/v1/models" | python3 -m json.tool 2>/dev/null \
  | grep -E '"id"|"max_model_len"' || true

echo "=== single /v1/completions ==="
curl -s -m60 "http://localhost:$GATEWAY_PORT/v1/completions" -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL_ID\",\"prompt\":\"The capital of France is\",\"max_tokens\":12}" \
  | python3 -c 'import sys,json; d=json.load(sys.stdin); print("OK ->", repr(d["choices"][0]["text"]))' 2>/dev/null \
  || die "completion request failed"

echo "=== firing $N requests for load-balance check ==="
for n in $(seq 1 "$N"); do
  curl -s -m30 "http://localhost:$GATEWAY_PORT/v1/completions" -H 'Content-Type: application/json' \
    -d "{\"model\":\"$MODEL_ID\",\"prompt\":\"Q$n: name one animal.\",\"max_tokens\":5}" >/dev/null &
done
wait
sleep 2  # let the engine logs flush

echo "=== requests served per vLLM engine (APIServer pid == one instance) ==="
kubectl logs -n "$NS" neutral-0 -c vllm --tail=400 2>/dev/null \
  | grep "Client finished request" \
  | grep -oE 'pid=[0-9]+' | sort | uniq -c \
  | awk '{printf "  engine %s served %s requests (recent window)\n", $2, $1}'

log "Smoke test done."
