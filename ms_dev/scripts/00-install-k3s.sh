#!/usr/bin/env bash
#
# LAYER 0 — INSTALL k3s + NVIDIA Container Toolkit from scratch.
# These live on the container's overlay rootfs (/usr/local/bin, /etc, apt packages),
# so they are GONE when the Docker container is RECREATED (only /NHNHOME survives).
# Run this FIRST on a brand-new container, before 01.
#
#   cold start:  ./00-install-k3s.sh -> ./01-prepare-k3s-host.sh -> 02 -> 03 -> 04
#
# This script DOES NOT start k3s (INSTALL_K3S_SKIP_START=true). Starting is 01's job,
# because the container-nesting binds (containerd->xfs, /dev/kmsg, sysctls) MUST be in
# place before k3s's first start — otherwise containerd writes to the overlay rootfs and
# the big vLLM image extract fills the disk. So: 00 installs (no start), 01 binds + starts.
#
# Privilege is via `gcsudo <cmd>` (runs as root); plain sudo does NOT work on this host.
# Everything here codifies the project memory "nxc7-k3s-install-recipe" (also mirrored in
# ms_dev/notes/). It installs exactly what was verified working on 2026-06-23.
set -uo pipefail
cd "$(dirname "$0")"; source ./lib.sh

# --- pinned, verified-working versions (override via env if needed) ---
K3S_VERSION="${K3S_VERSION:-v1.35.5+k3s1}"
K3S_EXEC="${K3S_EXEC:-server --write-kubeconfig-mode 644 --disable traefik --snapshotter overlayfs}"
TOOLKIT_KEYRING=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
TOOLKIT_LIST=/etc/apt/sources.list.d/nvidia-container-toolkit.list

ensure_gcsudo || die "could not set up gcsudo (arg-preserving root escalation) — cannot install."

# ---------------------------------------------------------------------------
# 1) NVIDIA Container Toolkit (gives k3s an 'nvidia' containerd runtime; the
#    config.yaml below makes it the default so GPU pods need no runtimeClassName).
# ---------------------------------------------------------------------------
if have nvidia-ctk; then
  log "[1/4] NVIDIA Container Toolkit already installed ($(nvidia-ctk --version 2>/dev/null | head -1)) — skip"
else
  log "[1/4] installing NVIDIA Container Toolkit (apt)"
  curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
    | gcsudo gpg --dearmor -o "$TOOLKIT_KEYRING" \
    || die "failed to fetch NVIDIA gpg key (network to nvidia.github.io?)"
  curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
    | sed "s#deb https://#deb [signed-by=$TOOLKIT_KEYRING] https://#g" \
    | gcsudo tee "$TOOLKIT_LIST" >/dev/null \
    || die "failed to write toolkit apt source"
  gcsudo apt-get update -y       || die "apt-get update failed"
  gcsudo apt-get install -y nvidia-container-toolkit || die "toolkit install failed"
  have nvidia-ctk || die "nvidia-ctk still not on PATH after install"
fi

# ---------------------------------------------------------------------------
# 2) k3s config.yaml — MUST exist before k3s first starts.
#    default-runtime: nvidia        -> GPU injection without per-pod runtimeClassName
#    disable-cloud-controller       -> CRITICAL, avoids the ~12s CCM crash-restart loop
# ---------------------------------------------------------------------------
log "[2/4] writing /etc/rancher/k3s/config.yaml"
gcsudo mkdir -p /etc/rancher/k3s
gcsudo tee /etc/rancher/k3s/config.yaml >/dev/null <<'YAML'
default-runtime: nvidia
disable-cloud-controller: true
disable-helm-controller: true
disable:
  - traefik
  - metrics-server
  - servicelb
YAML

# ---------------------------------------------------------------------------
# 3) k3s itself — install WITHOUT starting (binds in 01 must come first).
# ---------------------------------------------------------------------------
if have k3s && k3s --version 2>/dev/null | grep -q "$K3S_VERSION"; then
  log "[3/4] k3s $K3S_VERSION already installed — skip"
else
  log "[3/4] installing k3s $K3S_VERSION (skip-start; 01 will start it after the binds)"
  curl -fsSL https://get.k3s.io \
    | gcsudo env INSTALL_K3S_VERSION="$K3S_VERSION" \
                 INSTALL_K3S_SKIP_START=true \
                 INSTALL_K3S_EXEC="$K3S_EXEC" sh - \
    || die "k3s install failed (network to get.k3s.io / github releases?)"
  have k3s || die "k3s still not on PATH after install"
fi

# ---------------------------------------------------------------------------
# 4) kubeconfig for this user (the in-cluster file is mode 644 via the exec flag).
# ---------------------------------------------------------------------------
log "[4/4] placing kubeconfig at ~/.kube/config"
mkdir -p "$HOME/.kube"
if [ -f /etc/rancher/k3s/k3s.yaml ]; then
  gcsudo cat /etc/rancher/k3s/k3s.yaml > "$HOME/.kube/config" 2>/dev/null \
    && chmod 600 "$HOME/.kube/config" \
    && log "kubeconfig written (will be valid once k3s starts in 01)."
else
  warn "k3s.yaml not present yet — it is generated on first k3s start. 01 / kubectl will pick it up."
fi

log "Install complete. k3s is INSTALLED but NOT started. Next: ./01-prepare-k3s-host.sh"
