# PolyServe on Llumnix — 진행 기록

설계 정본은 [POLYSERVE_DESIGN_KO.md](POLYSERVE_DESIGN_KO.md), 프로파일 테이블 정본은
[deploy/profiling/README.md](deploy/profiling/README.md). 이 파일은 **무엇을 언제 왜 했는지**의
시간순 기록이다.

## 상태 요약

| 단계 | 커밋 | 상태 |
|---|---|---|
| P0 프로파일 테이블 생성 | `ed7c3a5` | 완료 (실제 Go 로더로 검증) |
| 논문 재확인 → 설계 수정 | `56617a9` | 완료 |
| P1 per-request SLO 배관 | `9e0d51d` | 완료 (26 테스트) |
| P2 PolyServe 정책 | `ecb5810` | 완료 (라이브 e2e) |
| P3 동적 재분할 | `fe2c122` | 완료 (라이브 수렴 확인) |
| P4-a 배포 + 동작 확인 | `--` | 완료 (라이브 e2e) |
| **P4-b ttft.json 재측정** | — | **진행 중** |
| P4-c rate sweep 3-arm | — | 대기 |
| dynamic chunking 검토 | — | 보류 (sweep 결과 본 뒤) |

## 확정된 결정 (사용자)

- tier 3개, tier 키 = **TPOT SLO(ms)**. chat 50 / deepresearch 100 / swe 25.
- swe SLO는 e2e 30s가 아니라 **TTFT 11.8s / TPOT 25ms** (e2e30s − 728×25ms). EXP-17과 동일 기준.
- within-tier **최저부하** (논문의 최고부하 반전 — autoscaling 없으면 packing 보상 0).
- admission test는 **§4.5+4.6+4.7 전부** 반영 (PolyServe를 제 실력으로 평가).
- fallback: **admission은 풀리고 tier 경계는 유지**.
- 출력길이는 **tier별 실측 평균** (chat 386 / dr 275 / swe 728).
- DSLO는 v1 제외 — 판정만 논문대로, 채점은 EXP-17 순간기준 유지(5-arm 직접비교).
- 엔진은 **stock FIFO** (dynamic chunking 없음). 결과 보고 재논의.

## 빌드 / 배포 방법

호스트에 go·docker가 없어 툴체인을 직접 설치했다(`/home/nxclab/tools/go`, go1.24.1 =
go.mod의 toolchain과 일치).

```bash
export PATH=/home/nxclab/tools/go/bin:$PATH
export GOPROXY="https://proxy.golang.org,direct" GOFLAGS=-mod=mod
export TMPDIR=/home/nxclab/tools/gotmp GOTMPDIR=/home/nxclab/tools/gotmp  # /tmp가 noexec

CGO_ENABLED=0 go build -buildvcs=false -o bin/scheduler-exp07 ./cmd/scheduler
go build -buildvcs=false \
  -ldflags="-extldflags '-L./lib/sglang/sgl-model-gateway/bindings/golang/lib/'" \
  -o bin/gateway-exp10 ./cmd/gateway
```

`bin/`이 스케줄러 pod의 `/exp07bin`으로 hostPath 마운트되어 있어, 바이너리를 덮어쓰고
pod를 재시작하면 반영된다. 게이트웨이의 cgo 정적 라이브러리는 Makefile이 가리키는
`target/release/`가 아니라 `.../golang/lib/`에 있다(untracked).

## 시간순 기록

### 2026-07-25 — P0~P3 구현

위 표 참조. 진행 중 발견한 것들:

- **QoServe 데이터는 프로파일링에 쓰면 안 된다**(사용자 지적). `DeadlineScheduler`가 매 스텝
  청크 크기를 바꾸므로 stock 엔진의 물리를 설명하지 못한다. 생성기가 이름으로 거부한다.
- **`interval_ms`는 bimodal**. 비동기 스케줄러가 한 스텝 앞서 달리거나(~0.5ms) 블로킹하거나
  (실제 forward)라서 스텝별 귀속이 불가능. decode는 긴 균질 구간이라 중앙값이 곧 스텝 시간이지만,
  prefill은 지속 포화 구간이 full-chunk에서만 생겨 **실측점이 1개**뿐. → P4-b에서 재측정.
- **초기 논문 요약이 틀렸었다.** `admission test = wait+T_iter<TPOT` 한 줄이 §4.5/4.6/4.7
  세 메커니즘을 뭉갠 것이었고 셋 다 v1에 빠져 있었다. 원문 재확인 후 전부 반영.
- **§4.6 판정식이 죽은 코드였다.** `ttft + iter ≤ TTFT+TPOT`는 검사 1·3이 통과하면 대수적으로
  항상 참. iter를 `iterNow`(현재 KV, prefill 청크 지배)와 `iterMax`(최대 KV)로 쪼개야 의미가 생긴다.
- **기존 `slo` 정책은 우리 클러스터에서 애초에 동작 불가.** Prefill/Decode만 정의하는데
  `baseDispatchPolicy`가 포인터 맵이라 neutral 요청에 nil 역참조.
- **`verifySchedulingPolicy` 화이트리스트** 누락 시 기동 panic. 유닛 테스트로는 안 잡히고
  라이브 스모크가 잡았다.

### 알려진 리스크 (P4 결과 해석 시 반드시 짚을 것)

1. **`ttft.json`이 잠정**(실측점 1개). P4-b에서 교체 예정.
2. **고부하에서 admission이 무력화될 가능성.** `iterMax`에 prefill 간섭항이 들어가므로
   대기 prefill이 있는 인스턴스는 iterMax≈650ms가 되어 모든 tier(25/50/100ms)에서 탈락한다.
   전부 탈락하면 fallback으로 admission이 풀려 사실상 least-load로 퇴화한다.
   논문의 해법은 dynamic chunking(§4.7)인데 우리는 엔진 stock FIFO로 확정해 제외했다.
   "co-location + 고정 8192 청크에서는 admission control이 무력"이 정당한 발견일 수 있으나,
   결과가 밋밋하면 dynamic chunking 허용을 재논의한다.
3. **채점 기준이 논문과 다름**(순간 기준 vs DSLO 누적). 우리 수치가 논문보다 낮게 나오는 게 정상.
4. 선재 버그 잔존: `pkg/cms/cms_read_client_test.go`가 `NewCMSReadClient` 인자 초과로 vet 실패
   (내 작업 아님, 미수정).

### 2026-07-25 — P4-a 배포 및 전체 동작 확인

바이너리를 새로 빌드해 `bin/`에 설치(기존본은 `/home/nxclab/tools/bin-backup/`에 백업),
게이트웨이 재시작 → `set_scheduler_profiling.py --policy polyserve`로 ConfigMap 주입 및 정책 전환.

엔진 상태 확인: `LLUMNIX_ENABLE_MIGRATION=0`, `SCHED_EXTRA_ARGS=''` → migration 꺼짐,
엔진은 stock FIFO. 설계와 일치하고, migration이 꺼져 있어 재스케줄링이 tier 격리를 깨지 않는다.

게이트웨이로 실제 추론 요청을 흘려 전 구간 확인:

| 클라이언트 `priority` | tier | maxKV 증가분 | 기대 출력길이 |
|---|---|---|---|
| `5000050` (chat) | 50ms | 12 → 398 = +386 | 386 |
| `11800025` (swe) | 25ms | 11 → 739 = +728 | 728 |

즉 packed priority → 게이트웨이 디코드 → `SchedulingRequest` → `schedulingCtx` →
tier별 출력길이까지 전부 이어진다.

**함정 기록**: `kubectl logs deploy/<name>`은 Terminating 중인 **옛 pod**을 고를 수 있다.
롤아웃 직후 로그를 볼 때는 반드시 pod 이름을 직접 지정할 것. 이것 때문에 "요청이 스케줄러에
안 온다"고 잠깐 오진했다.
