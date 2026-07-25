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
| P4-b ttft.json 재측정 | `--` | 완료 (직접 측정) |
| **P4-c rate sweep 3-arm** | — | **진행 중** |
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

1. ~~`ttft.json`이 잠정~~ → **해소**. P4-b에서 직접 측정으로 교체(아래 기록). 다만 청크 합산이 긴 프롬프트를 4~13% 과소예측하는 건 Llumnix 테이블 형태의 한계라 남아 있다.
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

### 2026-07-25 — P4-b `ttft.json` 직접 측정

`ms_dev/scripts/measure_ttft_sweep.py` 신규. 유휴 엔진 1대에 단일 요청을 보내 첫 스트리밍
토큰까지의 시간을 재는 방식(= 업스트림이 자기 테이블을 만든 방식). 20개 길이 × 5회.

**기존 잠정 모델이 전 구간 과대평가였다.** step-dump 앵커가 decode 18건이 같이 돌던 포화
구간에서 나온 값이라 prefill에 decode 비용이 섞여 있었다.

| 청크 | 기존 모델 | 실측 | 오차 |
|---|---|---|---|
| 256 | 35.7 ms | 26.4 ms | +35% |
| 1024 | 93.6 ms | 80.1 ms | +17% |
| 8192 | 634 ms | 519.7 ms | +22% |

그리고 **곡선이 선형이 아니다**: 64토큰까지 ~20.5ms로 평평하다가(메모리 바운드) 기울기가
0.025 → 0.063 ms/tok로 증가한다(attention의 길이 의존). 업스트림 테이블도 같은 모양.

측정 신뢰성 장치 두 가지: 반복마다 **새 랜덤 토큰 id**를 뽑아 prefix cache 히트를 원천 차단,
그리고 warm-up 라운드를 버려 CUDA graph capture를 측정에서 제외.

**남는 한계(측정 문제가 아니라 테이블 형태의 문제)**: 청크 크기만으로 인덱싱하는 테이블은
뒤 청크가 앞 청크의 KV에 attention한다는 걸 못 본다. 8192씩 합산하면 긴 프롬프트를
과소예측한다 — 12k에서 4%, 16k에서 8%, 24k에서 13%. swe 클래스(입력 ~22k)에 해당하므로
TTFT 예측 정확도의 하한으로 기록해 둔다.

**함정 기록 2**: ConfigMap을 다시 만들어도 `GetLatencyPredictor`가 `sync.Once`로 한 번만
읽으므로 프로세스 재시작 없이는 반영되지 않는다. `set_scheduler_profiling.py`에 rollout
restart를 넣었다. 또 `kubectl get pods -o jsonpath={.items[-1]...}`는 나이순이 아니므로
`--sort-by=.metadata.creationTimestamp`가 필요하다(안 그러면 종료 중인 pod을 고른다).

### 2026-07-25 — P4-c EXP-21 준비

- 워크로드 `workload_configs/mix_polyserve.json`: 모든 클래스가 `tbt_ms`를 **명시**한다.
  이게 곧 PolyServe tier 키라서, swe가 `DEFAULT_TBT_MS`를 상속하면 그 기본값이 바뀔 때
  tier가 조용히 어긋난다. 값 자체는 EXP-17 기준 그대로(swe 11800ms = 30s − 728×25ms).
- 러너 `k8s/exp07/runner-exp21.template.yaml` + 드라이버 `run_exp21_polyserve.sh`.
  드라이버가 시작 전에 스택을 검사한다: KV admission θ off(요청을 거절해서 PolyServe 자체
  admission과 뒤섞임), 엔진 migration off(tier 경계를 넘어 요청을 옮김), 엔진 stock FIFO.
  그리고 **적용된 spec이 아니라 실행 중인 스케줄러 로그에서 정책을 되읽어** 확인한다.
- 분석 `analysis_scripts/request_level/exp21_polyserve.py`. attainment만으로는
  "tier 격리가 아무 일도 안 했다"와 "tier 격리가 영향이 없었다"를 구분할 수 없어서,
  클래스별 **라우팅 집중도**(0=엔진에 고르게 분산, 1=한 대에 고정)를 같이 낸다.
  주의: 집중도는 `analysis/request_engine.csv`가 필요하므로 분석 전에 run별로
  `build_request_engine_map.py`를 돌려야 한다.

두 arm은 엔진(stock FIFO)이 같고 **스케줄러 라우팅 정책만** 다르다. EXP-17~20이 엔진
스케줄러를 바꾸고 라우팅을 고정했던 것과 정확히 수직인 축.
