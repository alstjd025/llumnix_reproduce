#!/usr/bin/env bash
#
# Persist this whole ms_dev/ tree (scripts + README + notes + deploy patch) to /NHNHOME,
# which is the ONLY storage that survives a container RECREATION. Everything else
# (this repo clone, my Claude memory, k3s, the toolkit) lives on the ephemeral overlay
# rootfs and is wiped. Run this after editing anything under ms_dev/.
#
#   ./sync-to-nhnhome.sh            # ms_dev  -> /NHNHOME/llumnix-ms_dev
#   ./sync-to-nhnhome.sh --restore  # /NHNHOME/llumnix-ms_dev -> ms_dev (on a new container)
#
# Cold-start a new container then is just:
#   git clone https://github.com/llumnix-project/llumnix.git /home/nxclab/llumnix
#   cp -r /NHNHOME/llumnix-ms_dev /home/nxclab/llumnix/ms_dev
#   cd /home/nxclab/llumnix/ms_dev/scripts && ./00-install-k3s.sh && ./01-... && 02 && 03 && 04
set -uo pipefail
cd "$(dirname "$0")"; source ./lib.sh

BACKUP="${BACKUP:-/NHNHOME/llumnix-ms_dev}"

if [ "${1:-}" = "--restore" ]; then
  [ -d "$BACKUP" ] || die "no backup at $BACKUP"
  log "restoring $BACKUP -> $MS_DEV_DIR"
  mkdir -p "$MS_DEV_DIR"
  cp -a "$BACKUP/." "$MS_DEV_DIR/"
  log "restored. Next: cd $MS_DEV_DIR/scripts && ./00-install-k3s.sh"
  exit 0
fi

[ -d "$MS_DEV_DIR" ] || die "ms_dev not found at $MS_DEV_DIR"
log "backing up $MS_DEV_DIR -> $BACKUP"
mkdir -p "$BACKUP"
# mirror; --delete keeps the backup from accumulating stale files
if have rsync; then
  rsync -a --delete "$MS_DEV_DIR/" "$BACKUP/"
else
  rm -rf "$BACKUP"; mkdir -p "$BACKUP"; cp -a "$MS_DEV_DIR/." "$BACKUP/"
fi
log "backed up. $(find "$BACKUP" -type f | wc -l) files at $BACKUP"
