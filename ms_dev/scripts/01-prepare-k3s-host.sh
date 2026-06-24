#!/usr/bin/env bash
#
# LAYER 1 — host workarounds that are LOST when this Docker container is RECREATED.
# Run this ONCE after a container recreation, before anything else. Then k3s comes up.
# If k3s is already healthy this script does nothing (safe to run anytime; use --force to reapply).
#
# Privilege is via `gcsudo <cmd>` (runs as root); plain sudo does NOT work on this host.
# Each gcsudo runs in its OWN mount namespace, so bind-mounts must be done in PID1's ns
# via `gcsudo nsenter -t 1 -m -- mount ...` to be visible to k3s/containerd/kubelet.
#
# Background for every step below: see ms_dev/README.md and the project memory
# "nxc7-k3s-install-recipe". This codifies that recipe; it does not invent anything new.
set -uo pipefail
cd "$(dirname "$0")"; source ./lib.sh

FORCE=0; [ "${1:-}" = "--force" ] && FORCE=1

ensure_gcsudo || die "could not set up gcsudo (arg-preserving root escalation) — cannot prepare host."
have kubectl  || warn "kubectl not found in PATH; node-readiness checks will be skipped."

if [ "$FORCE" = 0 ] && node_ready; then
  log "k3s node '$NODE' is already Ready — host workarounds are in place. Nothing to do."
  log "(Use './01-prepare-k3s-host.sh --force' to reapply the binds and restart k3s.)"
  exit 0
fi

log "Applying container-nesting workarounds (these are lost on container recreation)..."

# 1) containerd root on local xfs + overlayfs snapshotter (native snapshotter blows up disk on
#    the big vLLM image; overlay-on-overlay is rejected, overlay-on-xfs works).
if ! mountpoint -q /var/lib/rancher/k3s/agent/containerd 2>/dev/null; then
  log "  [1/6] bind containerd root -> $CONTAINERD_XFS (xfs)"
  gcsudo mkdir -p "$CONTAINERD_XFS"
  gcsudo mkdir -p /var/lib/rancher/k3s/agent/containerd
  gcsudo nsenter -t 1 -m -- mount --bind "$CONTAINERD_XFS" /var/lib/rancher/k3s/agent/containerd
else
  log "  [1/6] containerd root already bind-mounted — skip"
fi

# 2) /dev/kmsg — kubelet needs it; mknod gives EPERM (no CAP_SYSLOG), symlink to console works.
if [ ! -e /dev/kmsg ]; then
  log "  [2/6] symlink /dev/kmsg -> /dev/console"
  gcsudo ln -s /dev/console /dev/kmsg
else
  log "  [2/6] /dev/kmsg present — skip"
fi

# 3) kubelet sysctls on read-only /proc/sys — kubelet only writes when current != target,
#    so bind a file holding the target value on top of each sysctl (in PID1's mount ns).
log "  [3/6] bind target-valued sysctl files (overcommit_memory=1, panic=10)"
printf '1\n'  > /tmp/oc; gcsudo nsenter -t 1 -m -- mount --bind /tmp/oc /proc/sys/vm/overcommit_memory 2>/dev/null || true
printf '10\n' > /tmp/pn; gcsudo nsenter -t 1 -m -- mount --bind /tmp/pn /proc/sys/kernel/panic        2>/dev/null || true

# 4) kube-proxy net sysctls on read-only /proc/sys — mount a fresh writable proc and bind its
#    sys/net over /proc/sys/net (in PID1's ns) so the whole net.* tree becomes writable.
log "  [4/6] bind a fresh writable proc over /proc/sys/net"
gcsudo nsenter -t 1 -m -- sh -c \
  'mountpoint -q /proc/sys/net && grep -q " /proc/sys/net proc " /proc/mounts || { mkdir -p /run/freshproc; mount -t proc proc /run/freshproc 2>/dev/null; mount --bind /run/freshproc/sys/net /proc/sys/net; }' \
  2>/dev/null || true

# 5) disable AppArmor in containerd — host kernel has AppArmor enabled (Y) but the container's
#    securityfs is read-only, so containerd can't load the cri-containerd profile and EVERY pod
#    fails with CreateContainerError ("apparmor_parser: ... Read-only file system", exit 226).
#    This drop-in lives on the overlay rootfs (agent/etc/containerd, NOT the xfs data root), so it
#    is LOST on container recreation and must be re-created here before k3s starts. (issue #6)
APPARMOR_DROPIN=/var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/10-disable-apparmor.toml
if ! gcsudo test -f "$APPARMOR_DROPIN"; then
  log "  [5/6] disable AppArmor in containerd (drop-in)"
  gcsudo mkdir -p /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d
  gcsudo tee "$APPARMOR_DROPIN" >/dev/null <<'TOML'
[plugins.'io.containerd.cri.v1.runtime']
  disable_apparmor = true
TOML
else
  log "  [5/6] AppArmor-disable drop-in already present — skip"
fi

# 6) (re)start k3s and wait for the node, then drop the CCM "uninitialized" taint.
log "  [6/6] restart k3s and wait for node Ready"
gcsudo systemctl restart k3s

# Ensure this user's kubeconfig exists (k3s.yaml is generated on first start; on a fresh
# container 00 couldn't place it yet because k3s hadn't started). kubectl below needs it.
if [ ! -s "$HOME/.kube/config" ]; then
  mkdir -p "$HOME/.kube"
  for _ in $(seq 1 20); do [ -f /etc/rancher/k3s/k3s.yaml ] && break; sleep 1; done
  if gcsudo cat /etc/rancher/k3s/k3s.yaml > "$HOME/.kube/config" 2>/dev/null && [ -s "$HOME/.kube/config" ]; then
    chmod 600 "$HOME/.kube/config"; log "  placed kubeconfig at ~/.kube/config"
  else
    warn "  could not place ~/.kube/config (k3s.yaml not ready); kubectl may need KUBECONFIG=/etc/rancher/k3s/k3s.yaml"
  fi
fi

if have kubectl; then
  for _ in $(seq 1 60); do node_ready && break; sleep 3; done
  if node_ready; then
    kubectl taint node "$NODE" node.cloudprovider.kubernetes.io/uninitialized- 2>/dev/null || true
    log "k3s node '$NODE' is Ready."
  else
    die "k3s did not become Ready in ~3min — check: gcsudo journalctl -u k3s -n 100"
  fi
fi

log "Host prepared. Next: ./02-cluster-prereqs.sh"
