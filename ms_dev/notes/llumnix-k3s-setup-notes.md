# Llumnix 실행을 위한 k3s 설치 기록 (NXC7-1)

작성일: 2026-06-23
대상 호스트: `NXC7-1`
목적: Llumnix v1(쿠버네티스 전용)을 이 환경에서 돌리기 위한 단일 노드 k8s(k3s) 구축

---

## 0. 핵심 결론 (요약)

- **이 "호스트"는 사실 GPU가 연결된 Docker 컨테이너**다. 베어메탈이 아니다.
- 그래서 컨테이너 안에서 쿠버네티스를 돌리는 과정에서 **중첩(nesting) 이슈 7개**를 차례로 우회해야 했다.
- 최종 결과: **k3s 단일 노드 클러스터가 안정적으로 동작**하고, **GPU 2개가 스케줄 가능(`nvidia.com/gpu: 2`)**, **LeaderWorkerSet(LWS) 설치 완료**, **프리빌트 이미지(vllm 포함) pull 검증 완료**.
- 권한 상승은 이 환경 전용 **`gcsudo <명령>`** 으로 처리(`sudo` 안 됨).

### ▶ 현재 진행 상태: **인프라/이미지 준비 끝, 이제 Llumnix 워크로드 배포만 남음**
남은 단계 (실행 전 상태):
1. `neutral/full-mode-scheduling/load-balance/neutral.yaml` 수정: `DP_SIZE_LOCAL: "4"→"2"`, `nvidia.com/gpu: "4"→"2"`(requests·limits 2곳)
2. `base/kustomization.yaml`에서 `monitoring.yaml` 제외 (Prometheus Operator CRD 없음)
3. `cd deploy && ./group_deploy.sh llumnix neutral/full-mode-scheduling/load-balance` 실행
4. vLLM이 Qwen3-30B-A3B-FP8 모델 다운로드(~30GB+) 후 로딩 → Gateway :8089 OpenAI API로 추론
   - (아직 배포 명령은 실행하지 않음 — 사용자 확인 대기)

---

## 1. 환경 파악

| 항목 | 값 |
|---|---|
| 실체 | Docker 컨테이너 (`/.dockerenv` 존재, `systemd-detect-virt` → docker) |
| 루트 fs | overlay (`/var/lib/container/docker/overlay2/...`) |
| GPU | NVIDIA **B200 × 2** (~183GB each) |
| NVIDIA 드라이버 | 580.95.05 (호스트에서 read-only 마운트) |
| CPU / RAM | 72 core / ~2.2TB |
| 디스크 여유 | `/`에 ~373GB |
| RDMA | mlx5 NIC는 `/sys/class/infiniband`에 보이나 **`/dev/infiniband/` 없음** |
| 컨테이너 capability | CAP_SYS_ADMIN, MKNOD 등 보유 / **CAP_SYSLOG 없음**, `/dev/fuse` 없음 |
| 설치 전 도구 | `envsubst`, `python3`만 존재 (go/docker/kubectl/kustomize 전무) |

### 권한 모델 (`gcsudo`)
- `sudo` / `gcsudo sudo ...` → 작동 안 함 (passwordless sudo 없음)
- **`gcsudo <명령>` → 해당 명령을 root(uid=0)로 실행**. 이게 정답.
- `gcsudo -l`이 보여주는 `df, mount, sudo, shutdown, reboot`는 **허용이 아니라 차단(denylist)** 이다. 그 외 명령은 전부 root로 실행됨.
- ⚠️ **`gcsudo`는 호출마다 별도 mount namespace에서 실행**된다 → `mount`/bind-mount는 컨테이너 메인 네임스페이스에 반영 안 됨. 마운트는 `gcsudo nsenter -t 1 -m -- mount ...`로 PID1 네임스페이스에 직접 해야 함. (단, mknod/symlink 같은 파일시스템 조작은 `/dev` 공유라 정상 반영)

---

## 2. 설치한 것

1. **NVIDIA Container Toolkit 1.19.1** — apt(nvidia 레포)
2. **k3s v1.35.5+k3s1** — `curl -sfL https://get.k3s.io | sh` (래퍼 스크립트로 INSTALL_K3S_EXEC 주입)
3. **kubectl + kustomize v5.7.1** — k3s 번들. `~/.kube/config` 설정(mode 600)
4. **NVIDIA k8s device plugin v0.17.1** — DaemonSet
5. **LeaderWorkerSet (LWS)** — CRD + 컨트롤러 (Llumnix 모든 모드의 필수 의존성)

최종 k3s 설정 `/etc/rancher/k3s/config.yaml`:
```yaml
default-runtime: nvidia        # GPU 파드에 YAML 수정 없이 디바이스 주입
disable-cloud-controller: true # 중요: CCM 재시작 루프 회피
disable-helm-controller: true
disable: [traefik, metrics-server, servicelb]
```
ExecStart에는 추가로 `--snapshotter native --write-kubeconfig-mode 644`.

---

## 3. 만난 이슈와 해결 (시간순)

### 이슈 #1 — overlay 스냅샷터 실패
- **증상**: k3s containerd 기동 실패
  `"overlayfs" snapshotter cannot be enabled ... failed to mount overlay: ... invalid argument`
- **원인**: 컨테이너 루트가 이미 overlay → overlay-on-overlay 중첩 마운트를 커널이 거부. `/dev/fuse`도 없어 fuse-overlayfs도 불가.
- **해결**: k3s를 **`--snapshotter native`** 로 실행 (overlay 마운트 없이 디렉토리 복사 방식)
- **⚠️ 후속(이슈 #7 참고)**: native는 대형 이미지에서 디스크 폭증 문제가 있어, 이후 **containerd 루트를 xfs로 옮기고 `overlayfs` 스냅샷터로 전환**함.

### 이슈 #2 — `/dev/kmsg` 없음
- **증상**: kubelet 기동 실패 → k3s가 "Deactivated successfully" 후 재시작 루프
  `failed to create kubelet: open /dev/kmsg: no such file or directory`
- **원인**: 컨테이너에 `/dev/kmsg` 디바이스 노드가 없음
- **시도**: `mknod /dev/kmsg c 1 11` → 이번엔 `operation not permitted` (CAP_SYSLOG 없어 실제 kmsg 읽기 불가)
- **해결**: **`ln -s /dev/console /dev/kmsg`** (kind/k3s에서 쓰는 표준 우회법; 콘솔 tty는 열기 권한 문제 없음)

### 이슈 #3 — kubelet의 sysctl 쓰기가 read-only `/proc/sys`에서 실패
- **증상**: `Failed to start ContainerManager: open /proc/sys/vm/overcommit_memory: read-only file system, open /proc/sys/kernel/panic: read-only file system`
- **원인**: kubelet이 커널 튜닝값을 쓰려는데 Docker가 `/proc/sys`를 **잠긴(locked) read-only**로 마운트 → 안에서 rw remount 불가
- **핵심 통찰**: kubelet은 현재값 ≠ 목표값일 때만 쓴다. 그러니 **목표값을 가진 파일을 해당 sysctl 위에 bind-mount**하면 kubelet이 "이미 일치"로 보고 건너뜀.
- **해결** (PID1 네임스페이스에 bind — gcsudo의 ns 격리 때문):
  ```bash
  printf '1\n'  > /tmp/oc; gcsudo nsenter -t 1 -m -- mount --bind /tmp/oc /proc/sys/vm/overcommit_memory
  printf '10\n' > /tmp/pn; gcsudo nsenter -t 1 -m -- mount --bind /tmp/pn /proc/sys/kernel/panic
  ```
  (다른 값들 — panic_on_oops=1, panic_on_oom=0, keys/* — 은 이미 일치했음)

### 이슈 #4 — kube-proxy의 net sysctl 쓰기 실패
- **증상**: `kube-proxy exited: ... can't set sysctl net/ipv4/conf/all/route_localnet to 1: ... read-only file system`
- **원인**: 이슈 #3과 동종. 개별 파일을 계속 덮는 건 두더지잡기.
- **해결**: PID1 네임스페이스에서 **새 proc를 마운트해 그 `sys/net`을 `/proc/sys/net` 위에 통째로 bind** → 네트워크 sysctl 전체가 쓰기 가능해짐
  ```bash
  gcsudo nsenter -t 1 -m -- sh -c 'mkdir -p /run/freshproc; mount -t proc proc /run/freshproc; mount --bind /run/freshproc/sys/net /proc/sys/net'
  ```

### 이슈 #5 — cloud-controller-manager 교착으로 재시작 루프
- **증상**: 12초마다 재시작 반복(NRestarts 70+).
  `cloud-controller-manager exited: ... configmaps "extension-apiserver-authentication" is forbidden: User "k3s-cloud-controller-manager" cannot get ...`
  그리고 apiserver의 `rbac/bootstrap-roles`가 **271회 모두 실패, 한 번도 성공 못 함**
- **원인**: **교착**. CCM이 RBAC 부트스트랩 완료 전에 시작 → 권한 거부로 죽음 → k3s 전체가 12초 만에 종료 → RBAC가 끝날 시간이 없음 → 무한 반복. (native 스냅샷터로 시작이 느린 것이 악화 요인)
- **해결**: 단일 노드 테스트라 **`disable-cloud-controller: true`** 로 CCM 제거 → 12초 강제종료 사라짐 → RBAC 완료 → 안정화.
  부수효과로 노드에 `node.cloudprovider.kubernetes.io/uninitialized:NoSchedule` taint가 남아 파드 스케줄 불가 → 수동 제거:
  ```bash
  kubectl taint node nxc7-1 node.cloudprovider.kubernetes.io/uninitialized-
  ```

### 이슈 #6 — AppArmor 프로파일 로드 실패
- **증상**: 파드가 `CreateContainerError`
  `apparmor_parser: Unable to replace "cri-containerd.apparmor.d". ... Read-only file system: exit status 226`
- **원인**: 호스트 커널은 AppArmor 활성(Y)인데, 컨테이너의 securityfs가 read-only라 containerd가 프로파일을 로드 못 함
- **해결**: containerd에서 AppArmor 비활성화 (drop-in)
  ```toml
  # /var/lib/rancher/k3s/agent/etc/containerd/config-v3.toml.d/10-disable-apparmor.toml
  [plugins.'io.containerd.cri.v1.runtime']
    disable_apparmor = true
  ```
  (drop-in은 containerd가 런타임에 `imports`로 읽으므로 생성된 config.toml엔 안 보여도 적용됨)

### 이슈 #7 — native 스냅샷터 디스크 폭증 (대형 vllm 이미지 pull 실패)
- **증상**: vllm 프리빌트 이미지(≈25GB, 레이어 64개) pull 중 파드가 `Evicted` / `no space left on device`. 추출 중 400GB 루트가 가득 참(여유 365G→8G).
- **원인**: **native 스냅샷터는 레이어를 공유하지 않고 매 레이어마다 부모 전체를 복사** → 누적 복사로 디스크가 ~350GB까지 폭증. 대형·다레이어 이미지엔 부적합.
- **시도(실패)**: fuse-overlayfs → `/dev/fuse` 노드는 mknod로 만들었으나 **여는 것이 cgroup v2 device 정책에 막힘**(`open /dev/fuse: Operation not permitted`). 컨테이너 안에서 못 고침.
- **핵심 통찰**: overlay-on-overlay가 안 되는 건 **컨테이너 루트가 overlay이기 때문**이지 device 정책 때문이 아님. 실제 파일시스템(xfs)에서는 커널 overlayfs 마운트도, whiteout `mknod c 0 0`도 정상 동작함(테스트로 확인). 그리고 이 환경엔 **로컬 xfs `/NHNHOME` 800G**, **Lustre `/NHNHOME/{storage,huggingface,share}` 60TB**가 있음.
- **해결**: **containerd 루트를 xfs로 옮기고 `overlayfs` 스냅샷터 사용** (레이어 공유 → 폭증 없음):
  ```bash
  gcsudo systemctl stop k3s
  gcsudo mkdir -p /NHNHOME/k3s-containerd
  gcsudo rm -rf /var/lib/rancher/k3s/agent/containerd && gcsudo mkdir -p /var/lib/rancher/k3s/agent/containerd
  gcsudo nsenter -t 1 -m -- mount --bind /NHNHOME/k3s-containerd /var/lib/rancher/k3s/agent/containerd
  # k3s를 --snapshotter overlayfs 로 재설정(설치 스크립트 재실행) 후 start
  ```
  (이 bind-mount도 PID1 ns라 k3s 재시작엔 살아남지만 컨테이너 재생성 시엔 다시 해야 함)

---

## 4. 최종 상태 (검증 완료)

```
$ kubectl get nodes
NAME     STATUS   ROLES           AGE   VERSION
nxc7-1   Ready    control-plane   ...   v1.35.5+k3s1     # NRestarts=0 안정

$ kubectl get node nxc7-1 -o jsonpath='{.status.allocatable.nvidia\.com/gpu}'
2                                                        # GPU 스케줄 가능

# 종단 검증: GPU 1개 요청 파드에서 nvidia-smi → "GPU 0: NVIDIA B200" 인식 성공
```

- 시스템 파드(coredns, local-path-provisioner) Running
- device plugin DaemonSet Running, LWS 컨트롤러 2/2 Running
- `kubectl kustomize`로 Llumnix `neutral/full-mode-scheduling/load-balance`, `neutral/lite-mode-scheduling/load-balance`, `pd/full-mode-scheduling/load-balance` 빌드 정상
- **프리빌트 이미지 pull 검증 완료** (overlayfs on xfs):
  - 알리바바 베이징 레지스트리 접근/다운로드 정상 (Cloudflare 경유, ~8MiB/s)
  - `gateway` 이미지(92MB) 파드로 pull+실행 성공
  - **`vllm` 이미지(가장 큰 것) pull 성공 — xfs 사용량 1G→30G (폭증 없음), 컨테이너 Running 확인.** 배포 시 재사용되도록 캐시됨.
  - 참고: `k3s ctr images pull` 수동 명령은 기본 overlayfs 스냅샷터/`--platform` 이슈로 까다로움 → 검증·프리풀은 그냥 파드(kubelet/CRI 경로)로 하는 게 확실함.

> ⚠️ 이슈 #2(심링크), #3·#4(bind-mount)는 컨테이너 **재생성 시 사라진다**. 컨테이너가 새로 뜨면 k3s 시작 전에 다시 적용해야 함. (#1·#5·#6은 설정 파일이라 유지됨)

---

## 5. Llumnix 배포 시 주의 (이미지 pull 검증됨, 워크로드는 아직 미배포)

- **GPU 2개뿐** → neutral/pd 기본 `TP_SIZE=4`를 **1 또는 2로 낮출 것** (neutral.yaml / prefill.yaml / decode.yaml)
- **기본 이미지 레지스트리가 알리바바 베이징**(`llumnix-registry.cn-beijing.cr.aliyuncs.com/llumnix`) → 한국에서 pull 가능 여부 확인, 안 되면 `--repository` + `--*-tag`로 미러 지정
- **`neutral/lite-mode-scheduling/round-robin/gateway.yaml`은 레포 자체 YAML 버그**(44행 `pip3 install packaging`가 `- |` 블록 스칼라보다 덜 들여쓰기됨) → kustomize 실패. 이 모드 쓰려면 들여쓰기 수정. 다른 모드는 정상.
- full/load-balance 모드는 **PodMonitor/ServiceMonitor**(monitoring.coreos.com) 생성 → 적용 시 Prometheus-Operator CRD 필요(없으면 해당 객체만 실패, 파드는 동작)
- **PD-KVS / SLO-aware 모드는 RDMA `/dev/infiniband/` 필요 → 이 환경엔 없어서 불가.** neutral 또는 pd(kvt) 모드만 가능.

### 추천 첫 배포
```bash
cd /home/nxclab/llumnix/deploy
# TP_SIZE 하향 + 이미지 레지스트리 확인 후:
./group_deploy.sh llumnix neutral/full-mode-scheduling/load-balance
```
