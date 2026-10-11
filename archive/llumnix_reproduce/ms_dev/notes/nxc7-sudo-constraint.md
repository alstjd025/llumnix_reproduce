---
name: nxc7-sudo-constraint
description: Privilege model on host NXC7-1 — use gcsudo (not sudo) to run commands as root
metadata: 
  node_type: memory
  type: project
  originSessionId: 18c91790-645d-41fa-ab05-5ab03b3de80c
---

On host `NXC7-1`, privilege escalation is via **`gcsudo <command>`**, NOT `sudo` (verified 2026-06-23).

- `sudo`/`gcsudo sudo ...` do NOT work (no passwordless sudo; `gcsudo sudo <x>` is rejected).
- **`gcsudo <command>` runs `<command>` as root (uid=0).** Confirmed: `gcsudo id` → uid=0(root); `gcsudo apt-get ...` works as root.
- `gcsudo -l` output is a **DENYLIST, not an allowlist**: the listed commands `df`, `mount`, `sudo`, `shutdown`, `reboot` are the ones BLOCKED ("This command is not allow"). Everything else runs as root.
- So Claude CAN do privileged installs: prefix with `gcsudo` and avoid the 5 blocked commands. Use `gcsudo systemctl ...`, `gcsudo apt-get ...`, `gcsudo sh installer.sh`, etc. For disk usage use something other than `df`.

Relates to [[llumnix-env-assessment]].
