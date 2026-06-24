---
name: nxc7-is-docker-container
description: "NXC7-1 \"host\" is actually a GPU Docker container — affects how k8s/containers can run"
metadata: 
  node_type: memory
  type: project
  originSessionId: 18c91790-645d-41fa-ab05-5ab03b3de80c
---

The Llumnix "host" `NXC7-1` is itself a **Docker container** (verified 2026-06-23), not a bare host:
- `/.dockerenv` present, `systemd-detect-virt` → docker, `/proc/1/cgroup` shows docker scope.
- Root `/` is an **overlay** fs on `/var/lib/container/docker/overlay2/...` (Docker-managed).
- It has systemd (PID1), 2× B200 GPUs already passed through (nvidia-smi works), NVIDIA driver 580.95.05 mounted read-only from host.
- Lustre + xfs mounts present (`/NHNHOME`, `/engrid`, huggingface cache at `/NHNHOME/huggingface`). 373G free on `/`.

**Container capabilities (CapBnd 0xa92c35fb):** HAS CAP_SYS_ADMIN, MKNOD, SYS_CHROOT, NET_ADMIN, SETFCAP, SYS_PTRACE, etc. So mounts and nested containerization ARE possible. NO `/dev/fuse` (so fuse-overlayfs unusable).

**Consequence for running k8s here (k3s):** containerd's default `overlayfs` snapshotter FAILS — nested overlay-on-overlay mount returns "invalid argument". Must run k3s with **`--snapshotter=native`** (plain dir copy, no overlay mount; uses more disk but works). fuse-overlayfs not an option (no /dev/fuse).

**Second k3s blocker — `/dev/kmsg` missing:** kubelet fails with `failed to create kubelet: open /dev/kmsg: no such file or directory`, which makes k3s graceful-shutdown in a restart loop (systemd "Deactivated successfully", restart counter climbing). Fix: `gcsudo mknod -m 644 /dev/kmsg c 1 11` (we have CAP_MKNOD; node ended up readable). This lives in /dev tmpfs so it survives k3s restarts but NOT a container recreation — recreate it if the container is rebuilt. Also harmless errors in this container: `modprobe br_netfilter/overlay` fail, sysctl writes to /proc/sys fail (read-only) — k3s tolerates these.

**Third k3s blocker — kubelet sysctl writes to read-only /proc/sys:** kubelet ContainerManager fails: `Failed to start ContainerManager: open /proc/sys/vm/overcommit_memory: read-only file system, open /proc/sys/kernel/panic: read-only file system`. `/proc/sys` is a Docker-locked ro mount (can't remount rw even with CAP_SYS_ADMIN). kubelet only writes a sysctl when current != desired, so the fix is to bind-mount files with the desired values over the two mismatched ones (others already matched): want vm/overcommit_memory=1, kernel/panic=10 (panic_on_oops already 1, panic_on_oom 0, keys/* already correct).

**CRITICAL gcsudo quirk:** `gcsudo <cmd>` runs each command in its OWN private mount namespace, so `mount`/bind-mounts done via plain `gcsudo` DO NOT persist to the container's main namespace where k3s runs. Must mount into PID 1's namespace: `gcsudo nsenter -t 1 -m -- mount --bind <srcfile> /proc/sys/...`. (Filesystem ops like mknod/ln on /dev DO persist since /dev is shared.) These bind mounts are lost on container recreation — redo before starting k3s.

Full k3s-in-this-container recipe: native snapshotter + `/dev/kmsg`→`/dev/console` symlink + nsenter bind-mounts for the 2 sysctls, then restart k3s.

Use [[nxc7-sudo-constraint]] (gcsudo) for privileged ops. See [[llumnix-env-assessment]].
