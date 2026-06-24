#!/usr/bin/env bash
#
# Tear down the Llumnix workload (LAYER 3). Leaves k3s, the device plugin and LWS intact,
# so you can redeploy quickly with ./03-deploy.sh.
#
#   ./99-teardown.sh           delete the '$NS' namespace
#   ./99-teardown.sh --k3s     ALSO stop k3s (LAYER 1/2 stay installed, just not running)
set -uo pipefail
cd "$(dirname "$0")"; source ./lib.sh

have kubectl || die "kubectl not found."

if kubectl get ns "$NS" >/dev/null 2>&1; then
  log "Deleting namespace '$NS' (pods, services, LWS, gateway, scheduler, redis)..."
  kubectl delete namespace "$NS" --wait=true || warn "namespace delete returned non-zero"
else
  log "namespace '$NS' not found — nothing to delete."
fi

if [ "${1:-}" = "--k3s" ]; then
  have gcsudo || die "gcsudo not found; cannot stop k3s."
  log "Stopping k3s (datastore + images preserved; re-run ./01-prepare-k3s-host.sh --force to bring it back)."
  gcsudo systemctl stop k3s || warn "systemctl stop k3s returned non-zero"
fi

log "Teardown complete."
