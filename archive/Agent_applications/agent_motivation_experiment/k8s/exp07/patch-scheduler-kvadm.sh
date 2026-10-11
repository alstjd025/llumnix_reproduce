#!/bin/bash
# EXP-07: run the host-built scheduler binary (feat/kv-admission-threshold,
# static CGO_ENABLED=0 build at /home/nxclab/llumnix_reproduce/bin) inside the
# stock scheduler image via hostPath, with the KV-occupancy admission filter.
#
# Usage: patch-scheduler-kvadm.sh <theta>
#   theta = hot-KV usage ratio threshold in [0,1]; 0 = admission disabled
#           (stock-equivalent code path, serves as the in-experiment baseline)
#
# Args below mirror the live exp05/exp06 scheduler args exactly; the only
# addition is --admission-kv-usage-threshold.
set -e
THETA=${1:?usage: patch-scheduler-kvadm.sh <theta 0..1>}
kubectl -n llumnix patch deploy scheduler --type=strategic -p "$(cat <<PATCH
spec:
  template:
    spec:
      containers:
      - name: scheduler
        command: ["/exp07bin/scheduler-exp07"]
        args:
        - --port
        - "8088"
        - -v
        - "4"
        - --host
        - 0.0.0.0
        - --llm-backend-discovery
        - redis
        - --discovery-redis-host
        - redis
        - --discovery-redis-port
        - "6379"
        - --scheduling-policy
        - load-balance
        - --cms-redis-host
        - redis
        - --cms-redis-port
        - "6379"
        - --cms-pull-status-interval-ms
        - "500"
        - --cms-pull-metadata-interval-ms
        - "10000"
        - --enable-full-mode-scheduling=true
        - --colocated-rescheduling-mode=true
        - --rescheduling-policies
        - neutral_load,neutral_failover
        - --rescheduling-neutral-load-threshold
        - "0.003"
        - --rescheduling-load-balance-threshold
        - "0.1"
        - --rescheduling-interval-ms
        - "500"
        - --admission-kv-usage-threshold
        - "$THETA"
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
kubectl -n llumnix rollout status deploy scheduler --timeout=180s
echo "[exp07] scheduler patched: admission-kv-usage-threshold=$THETA"
