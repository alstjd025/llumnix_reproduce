#!/usr/bin/env bash
# Shared config + helpers for the Llumnix-on-NXC7 scripts.
# Source this from the other scripts:  source "$(dirname "$0")/lib.sh"
#
# Every value below can be overridden from the environment, e.g.
#   NS=llumnix2 MODE=neutral/lite-mode-scheduling/load-balance ./03-deploy.sh

# ---- cluster / host ----
NODE="${NODE:-$(hostname | tr 'A-Z' 'a-z')}" # k3s node name (== lowercased hostname; nxc7 here, was nxc7-1)
CONTAINERD_XFS="${CONTAINERD_XFS:-/NHNHOME/k3s-containerd}"  # containerd root on local xfs

# ---- Llumnix deployment ----
REPO_DIR="${REPO_DIR:-/home/nxclab/llumnix}"
DEPLOY_DIR="${DEPLOY_DIR:-$REPO_DIR/deploy}"
MS_DEV_DIR="${MS_DEV_DIR:-$REPO_DIR/ms_dev}"
DEPLOY_PATCH="${DEPLOY_PATCH:-$MS_DEV_DIR/llumnix-deploy.patch}"  # YAML customizations, for a fresh clone
NS="${NS:-llumnix}"                          # namespace == group name
MODE="${MODE:-neutral/full-mode-scheduling/load-balance}"  # kustomize dir under deploy/

# ---- model / serving ----
MODEL_ID="${MODEL_ID:-meta-llama/Meta-Llama-3-8B-Instruct}" # OpenAI "model" id the gateway serves
GATEWAY_PORT="${GATEWAY_PORT:-8089}"         # local port for `kubectl port-forward`

# ---- prereq versions (informational; used by 02-cluster-prereqs.sh) ----
DEVICE_PLUGIN_VERSION="${DEVICE_PLUGIN_VERSION:-v0.17.1}"
LWS_VERSION="${LWS_VERSION:-v0.9.0}"

# ---- helpers ----
log()  { printf '\033[0;36m[%s]\033[0m %s\n' "$(basename "${0:-lib}")" "$*"; }
warn() { printf '\033[0;33m[%s] WARN:\033[0m %s\n' "$(basename "${0:-lib}")" "$*" >&2; }
die()  { printf '\033[0;31m[%s] ERROR:\033[0m %s\n' "$(basename "${0:-lib}")" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# k3s up & node Ready?
node_ready() { kubectl get node "$NODE" --no-headers 2>/dev/null | grep -q ' Ready '; }

# ---- gcsudo bootstrap ----
# Privilege escalation on NXC is the setuid binary exeCTNCmd. The stock `gcsudo` is only a SHELL
# ALIAS to it, so it does NOT exist for non-interactive scripts. Worse, exeCTNCmd WORD-SPLITS its
# argv, shattering any argument that contains spaces — which breaks the k3s install
# (INSTALL_K3S_EXEC="server --flag val ...") and the /proc/sys/net workaround (sh -c '<script>').
# ensure_gcsudo installs an arg-preserving `gcsudo` wrapper on PATH (idempotent). The wrapper lives
# on the overlay rootfs, so it is re-created on every cold start. Call it before any gcsudo use.
GCSUDO_REAL="${GCSUDO_REAL:-/engrid/ensh/gpubin/exeCTNCmd}"   # real setuid escalator (persists in /engrid)
GCSUDO_WRAPPER="${GCSUDO_WRAPPER:-/usr/local/bin/gcsudo}"     # where we install the arg-preserving shim
ensure_gcsudo() {
  # Already good? (a gcsudo on PATH that PRESERVES a multi-word argument)
  if command -v gcsudo >/dev/null 2>&1 \
     && [ "$(gcsudo env _X='a b c' bash -c 'printf %s "$_X"' 2>/dev/null)" = "a b c" ]; then
    return 0
  fi
  [ -x "$GCSUDO_REAL" ] || { warn "ensure_gcsudo: real escalator not found at $GCSUDO_REAL"; return 1; }
  local tmp; tmp="$(mktemp)" || return 1
  cat > "$tmp" <<WRAP
#!/bin/bash
# arg-preserving gcsudo: $GCSUDO_REAL word-splits argv, so marshal argv (printf %q preserves
# quoting) into a temp script and run it as root via '<exe> bash <script>' — every token handed
# to the splitter is then space-free. stdin/stdout/stderr pass through (pipes + heredocs work).
set -u
EXE=$GCSUDO_REAL
if [ "\$#" -eq 0 ] || [ "\${1:-}" = "-l" ] || [ "\${1:-}" = "-h" ]; then exec "\$EXE" "\$@"; fi
f="\$(mktemp /tmp/gcsudo.XXXXXX.sh)" || exit 1
{ printf '#!/bin/bash\nexec '; printf '%q ' "\$@"; printf '\n'; } > "\$f"
chmod 755 "\$f"; "\$EXE" bash "\$f"; rc=\$?; rm -f "\$f"; exit \$rc
WRAP
  "$GCSUDO_REAL" install -m 755 "$tmp" "$GCSUDO_WRAPPER"; rm -f "$tmp"
  command -v gcsudo >/dev/null 2>&1
}

# Ensure the deploy/ YAML customizations (Llama3 / 2x B200 / no-RDMA) are present.
# On a FRESH clone the edits aren't in the tree; this applies ms_dev/llumnix-deploy.patch.
# Idempotent: if already applied (reverse-check passes), it does nothing.
ensure_deploy_patch() {
  [ -f "$DEPLOY_PATCH" ] || { warn "deploy patch not found ($DEPLOY_PATCH) — assuming deploy/ is already customized."; return 0; }
  if git -C "$REPO_DIR" apply --reverse --check "$DEPLOY_PATCH" >/dev/null 2>&1; then
    log "deploy YAML customizations already present — skip patch."
  elif git -C "$REPO_DIR" apply --check "$DEPLOY_PATCH" >/dev/null 2>&1; then
    git -C "$REPO_DIR" apply "$DEPLOY_PATCH" && log "applied deploy customizations (ms_dev/llumnix-deploy.patch)."
  else
    warn "deploy patch neither applies cleanly nor is already applied — leaving deploy/ untouched. Inspect $DEPLOY_PATCH"
  fi
}
