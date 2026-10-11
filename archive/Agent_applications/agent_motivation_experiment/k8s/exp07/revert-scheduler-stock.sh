#!/bin/bash
# EXP-07 cleanup: restore the stock scheduler (image binary, no admission
# filter). The exp07bin volume/mount is left in place (harmless, read-only).
set -e
kubectl -n llumnix patch deploy scheduler --type=strategic -p "$(cat <<'PATCH'
spec:
  template:
    spec:
      containers:
      - name: scheduler
        command: ["/usr/local/bin/scheduler"]
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
PATCH
)"
kubectl -n llumnix rollout status deploy scheduler --timeout=180s
echo "[exp07] scheduler reverted to stock image binary"
