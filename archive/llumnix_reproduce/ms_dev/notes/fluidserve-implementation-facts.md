# FluidServe — Implementation 절을 위한 사실 모음

작성 2026-09-09. **여기에는 사실만 적는다.** 왜 FTB가 필요한지, 왜 짧은 horizon인지,
왜 클래스를 모으는지는 design 절이 끝낸 이야기이므로 반복하지 않는다.

각 수치의 출처를 옆에 적었다. `파일:행`은 실제로 읽어 확인한 것이고, 측정값은 어느 run에서
나왔는지를 밝혔다. **확인하지 않은 것은 ⚠로 표시했다.**

---

## 0. 이 절에서 강조할 것 셋

1. **엔진을 건드리지 않는다** — vLLM 소스 0행, 엔진 기동 인자에 커스텀 스케줄러 없음.
2. **런타임 정보를 싸게 얻는다** — 주기적 status pull 하나, per-token RPC 없음,
   요청 단위 상태는 스케줄러가 재구성.
3. **요청마다 모든 인스턴스에 투영해도 싸다** — 실측 결정당 **0.013 ms**.

---

## 1. vLLM 버전과 수정 범위

| | |
|---|---|
| 엔진 | vLLM **0.12.1.dev0+g4fd9d6a85.d20260305** (`patches/vllm-sched/VLLM_VERSION.txt`) |
| 이미지 | `llumnix/vllm:20260306-165123` (파드 spec에서 확인) |
| 구성 | 인스턴스 4개 × **TP=2** = B200 8장, 한 파드 안 |
| 엔진 인자 | `--max-model-len 40960`, `--gpu-memory-utilization 0.9`, `--max-num-seqs 4096` |
| 엔진 내부 스케줄러 | **`--scheduler-cls` 지정 없음** → vLLM V1 기본(AsyncScheduler) |

**FluidServe를 위해 vLLM에 넣은 코드는 0행이다.** 엔진은 stock으로 돌고, 우리가 읽는 상태는
Llumnix가 이미 발행하던 것뿐이다(§3).

⚠ **혼동하지 말 것 — `patches/vllm-sched/`에 파이썬 파일 넷이 있지만 셋은 FluidServe와 무관하다**:
- `llumnix_sched.py` — EXP-16 프로파일링용 계측 스케줄러(stock FIFO + step 로그). 프로파일을
  만들 때만 쓰고 측정 run에서는 안 쓴다.
- `deadline_sched.py`, `slo_tier.py` — QoServe(Niyama) 이식용. 별도 기준선.
- `llumnix_client_index_fix.py` — **엔진 기동 시 실행되지만 Llumnix의 버그 수정**이다
  (llumlet의 소켓 인덱스가 API server 프로세스 수여야 하는데 1로 하드코딩돼 있다).
  다섯 arm 전부에 똑같이 적용되므로 비교에 비대칭이 없다.

**게이트웨이도 거의 안 건드렸다.** 기존 Llumnix hold loop(`pkg/gateway/load-balancer/
scheduler_client.go:176-235`)를 그대로 쓰고, 더한 것은 **같은 429 본문을 "보유"와 "거절"
두 뜻으로 가르는 수십 행**뿐이다(`ErrorAdmissionRejected` 대 `ErrorNoAvailableEndpoint`).

---

## 2. 스케줄러의 위치와 통신 구조

```
클라이언트 → 게이트웨이(:8089) → 스케줄러(:8088) → [결정] → 게이트웨이가 엔진으로 전달
                  ↑                    ↑
            Redis discovery       Redis CMS (인스턴스 status)
```

- 스케줄러는 **별도 파드의 Go 프로세스**이고 요청 본문을 보지 않는다. 게이트웨이가
  `types.SchedulingRequest`(요청 id, 프롬프트 토큰 수, 토큰 id, tier, TTFT/TPOT 예산)를
  보내고 목적지 인스턴스를 돌려받는다.
- **데이터 경로에 없다.** 토큰은 게이트웨이 ↔ 엔진 사이로만 흐르고 스케줄러를 지나지 않는다.
- 인스턴스 발견과 status 전달이 **둘 다 Redis**를 지난다
  (`--llm-backend-discovery redis`, `--cms-redis-host redis`).
- 배포는 hostPath — 호스트에서 빌드한 정적 바이너리를 파드의 `/exp07bin`에 마운트한다.
  플래그 주입과 기동 줄 검증은 `ms_dev/scripts/set_scheduler_profiling.py`가 하고,
  적용 뒤 스케줄러 로그의 기동 줄을 되읽어 `verified:`를 찍는다.

**Llumnix 통합 지점 여섯** (그 밖에는 손대지 않았다):

1. `scheduling_policy_registry.go`의 `case SchedulingPolicyFluidserve` — dispatch policy 구현.
   **기존 filter 파이프라인을 쓰지 않는다**: selector 하나가 전부다.
2. `verifySchedulingPolicy` 화이트리스트 — 빠지면 기동 panic이고 유닛 테스트로는 안 잡힌다.
3. `schedulingCtx`에 `fluidserveRequest` / `fluidserveFlux` 두 필드 추가.
4. 상태 원천은 **Llumnix `cmsView` 그대로** — 자체 계측 없음.
5. 배포·플래그 주입 스크립트.
6. 프로파일 생성 스크립트 `ms_dev/scripts/gen_fluidserve_profile.py`.

**다섯 기준선(FluidServe·Llumnix·Llumnix SLO·PolyServe·vLLM router)이 같은 게이트웨이와
같은 status 경로를 공유한다.** ⚠ **예외 하나**: 게이트웨이 대기 창이 FluidServe만
35,000 ms / 500 ms이고 나머지는 5,000 ms / 500 ms다. EXP-83·84가 그 차이를 측정했다.

---

## 3. 엔진이 보고하는 것과 주기 — **per-token RPC가 없다**

| | 값 | 출처 |
|---|---|---|
| status pull 주기 | **500 ms** | `--cms-pull-status-interval-ms 500` |
| metadata pull 주기 | **10,000 ms** | `--cms-pull-metadata-interval-ms 10000` |
| 전달 방식 | 엔진 → Redis → 스케줄러 (**폴링, 푸시 아님**) | |

**메시지는 Llumnix가 이미 갖고 있던 `cms.InstanceStatus` proto다** — FluidServe를 위해 필드를
추가하지 않았다. 그 메시지에서 **실제로 읽는 것은 아래뿐**이다:

| 필드 | 쓰는 곳 |
|---|---|
| `StepId`, `TimestampMs` | 구간 측정, 상주 요청의 진행 재구성, flux 캐시 키 |
| `NumUsedGpuTokens`, `NumTotalGpuTokens` | 물리 KV와 용량 → `capMem` |
| `NumUncomputedTokensAllWaitingPrefills` + `...SchedulerRunningPrefills` | 대기 prefill |
| `SchedulerRunningToDecodeRequestsNum` | 엔진이 보고한 running 수 (원장 정리에 사용) |
| `Schedulable` | 생존성 필터 |
| 디코드 배치 넷 (`decodeBatchOf`, `fluidserve.go:694-708`): `HybridSchedulerWaitingToDecodeTokensNum` + `SchedulerWaitingToDecodeTokensNum` + `SchedulerRunningToDecodeTokensNum` + `NumTokensLoadingRequests`, 그리고 대응하는 요청 수 넷 | 논리 KV와 디코드 요청 수 |
| `Metadata.MaxNumBatchedTokens` | 청크 크기 (없으면 8192로 대체) |

**요청 단위 상태는 엔진이 보고하지 않는다. 스케줄러가 재구성한다** (`fluidserve_registry.go`):

- 배치할 때 요청의 (id, tier, 프롬프트 토큰, 배치 시점 step, 예상 prefill step 수)를 원장에 적는다.
- 매 status마다 `reconcile`이 그 원장을 훑어 상주 요청마다
  `j = stepID − 배치시점step − prefillSteps`로 **지금까지 만든 토큰 수를 유도한다.**
- 엔진이 보고한 running 수보다 원장이 많으면 진행이 앞선 것부터 버린다.
  생존확률이 `fsRetireSurvival = 0.02` 아래이거나 나이가 20분을 넘으면 퇴역시킨다.

⚠ **그래서 `j`는 관측이 아니라 유도값이다.** 엔진의 전역 step 카운터와 모델이 계산한 prefill
step 수로 만들어진다. Threats에 적을 것.

**per-token 신호가 전혀 없다** — 토큰마다 오는 것은 게이트웨이 ↔ 엔진 사이의 스트림뿐이고
스케줄러는 그것을 안 본다.

---

## 4. 지연 모델의 형태

**오프라인 두 조각 + 온라인 스칼라 셋.** 학습기도, 특징 벡터도, 재학습 주기도 없다.

### 4.1 오프라인 (프로파일 파일 둘)

`deploy/profiling/llama31-70b-b200-tp2/`

| 파일 | 형태 | 규모 |
|---|---|---|
| `fluidserve.json` | **디코드 step 법칙 계수 셋** + 클래스별 출력 길이 생존함수 | 계수 3개, 클래스 3개 × 격자 153점 |
| `ttft.json` | **청크 크기 → prefill step 비용** 표, 이중선형 보간 | 26점 (측정 20 + 외삽 6) |

```
디코드 전용 step:  t = c₀ + c_kv·KV + c_n·N
                   c₀ = 16.3612 ms, c_kv = 1.282e-5 ms/token, c_n = 0.0764286 ms/req
                   R² = 0.952  ⚠ 원시 step 377,838개가 아니라 격자 셀 76개의 중앙값에 적합
                   상대오차 중앙 6.4% / p90 20.5% (파일에 있으나 코드는 읽지 않는다)

혼합 step (계획 구간 평균):
  meanStep = corr · [ (k − s_p)·t_dec + s_p·(t_pre(chunk) + t_dec − c₀) ] / k,   k = 100
  s_p = prefillSteps(pending, chunk)   — 올림하지 않는다

역함수 (허용 점유량):
  capKv = (allowance/corr − c₀ − c_n·N − f·(t_pre − c₀)) / c_kv,   f = s_p/k ≤ 1
```

- 프로파일은 `sync.Once`로 **프로세스당 한 번** 읽는다. 파싱 실패는 `klog.Fatalf`.
- 격자 밖 조회는 대체값을 쓰지 않고 **`+Inf`를 돌려주며**, 호출자는 그것을 `unpredictable`로
  읽어 그 인스턴스를 후보에서 뺀다.
- ⚠ `tpot.json`도 같은 디렉토리에 있고 로드되지만 **FluidServe는 읽지 않는다**(PolyServe와
  Llumnix SLO가 쓴다). "프로파일 표 둘을 쓴다"고 쓰면 틀린다.

### 4.2 온라인 (스칼라 셋 + 하나, 전부 EMA)

| 값 | 범위 | 갱신 | α | 경계 |
|---|---|---|---|---|
| `corr` step 보정 | **인스턴스별** | 곱셈 EMA | 0.002 | [0.5, 3.0] |
| `κ` prefill 청구 비율 | **함대 스칼라** | 덧셈 EMA | 0.01 | [0.02,1.0] / [0.25,4.0] |
| `duty` prefill 시간 비중 | 인스턴스별 | 두 EMA의 비 | 0.1 | 하한 0.05 |
| 배치 지연 상한 | 인스턴스/함대 | mean·meanSq 각각 EMA | 0.01 | 표본 50건 미만이면 상수 300 ms |

**갱신은 status pull마다 한 번**(인스턴스당 초당 약 2회)이다. 요청마다가 아니다.

⚠ 적어 둘 것 셋: (a) `corr`의 시상수가 per-instance 모드에서 약 **4분**이다(주석의 "약 1분"은
함대 하나이던 시절 값), (b) `corr`·`κ`·`duty` 셋이 같은 잔차(`측정 − 디코드 전용 예측`)에서
나와 따로 식별되지 않는다, (c) 배치 지연 상한의 표본은 §3의 유도된 `j`에서 나온다.

---

## 5. 수요 추정 창과 히스테리시스 (class-instance cap)

`pkg/scheduler/policy/fluidserve_instancecap.go`

```
버킷:      5,000 ms마다 닫는다 (fsCapBucketMs)
버킷 값:   rate = count/경과초,  prompt = promptSum/count
EMA:       α = 버킷길이 / τ,   τ = capWindowMult(3.0) × residenceS,  clamp [15 s, 120 s]
residenceS = nominal × expectedToks / 1000        (클래스마다 다르다)
첫 버킷:   EMA 없이 그대로 대입 (warm start)
상한:      limit = ⌈λ_c / μ_c⌉,  바닥 1,  올라갈 때만 +0.05 히스테리시스 (fsCapRaiseEps)
μ_c:       tierServiceRate — 그 클래스만 서빙하는 인스턴스 하나의 정상상태 완료율
```

τ의 실제 값: chat 64 s, swe 111 s, deepresearch 120 s(상한에 걸림).

**도착은 첫 등장만 센다** — 보유된 요청이 500 ms마다 재진입하므로
`registry.noteArrival`의 첫-사시 반환으로 중복을 막는다.

⚠ **문서 정정 필요**: 다른 문서가 "새 상수 1개"라고 적는데, 튜닝 가능한 플래그는 1개이지만
하드코딩 상수는 넷(`fsCapBucketMs`, `fsCapTauMinS`, `fsCapTauMaxS`, `fsCapRaiseEps`) + 바닥 1이다.

---

## 6. 보유 요청의 재평가 주기

| | 값 |
|---|---|
| 게이트웨이 재시도 간격 | **500 ms** (`--wait-scheduling-retry-interval`) |
| 게이트웨이 천장 | **35,000 ms** (`--wait-scheduling-timeout`) — ⚠ FluidServe arm만; 다른 arm은 5,000 |
| 게이트웨이 워커 / 큐 | 4,096 / 16,384 (`--wait-queue-threads` / `--max-queue-size`) |

**재시도 주기를 설정에서 읽지 않고 측정한다.** `noteArrival`이 `recheckMs = now − 마지막에 본 시각`을
돌려주고, 판정이 그 값을 쓴다. 설정과 실제가 어긋나도 계산이 맞는다.

**보유된 요청은 전체 결정 경로를 다시 탄다** — 인스턴스 상태를 새로 읽고, 후보를 다시 평가하고,
다시 route/pend/shed를 정한다. 재시도 사이에 상태를 들고 있지 않다.
단 두 가지는 한 번만 한다: 프롬프트 해싱(두 번째 호출부터 캐시)과 cap의 도착 계수.

---

## 7. 코드 규모

| 부분 | 행 수 |
|---|---|
| `fluidserve.go` (결정 경로·관측·selector) | 3,159 |
| `fluidserve_registry.go` (원장·회계·예산) | 930 |
| `fluidserve_capacity.go` (지연 모델·보정·`tierServiceRate`) | 602 |
| `fluidserve_instancecap.go` (cap 메커니즘 전체) | 457 |
| `fluidserve_profile.go` (길이 분포) | 255 |
| `fluidserve_prefix.go` (prefix index) | 211 |
| **정책 합계** | **5,614** |
| 단위 테스트 | 3,069 |
| 플래그 정의 (`cmd/config/config.go`) | 34개 |
| **vLLM 수정** | **0행** |
| 게이트웨이 수정 | 429를 두 뜻으로 가르는 수십 행 |

---

## 8. 결정 하나의 비용 — 실측

`request_full_mode_schedule_duration_milliseconds` (스케줄러가 발행, 러너가 수집).
**결정 전체를 감싼다** — 인스턴스 상태 구성, 후보 넷 평가, 정렬, 사다리까지.

| run | 결정 수 | 총 스케줄링 시간 | **결정당 평균** |
|---|---|---|---|
| **FluidServe, 35 req/s, 8분** | 31,207 | 0.4 s | **0.013 ms** |
| **FluidServe, 한 시간 trace** | 207,729 | 2.1 s | **0.010 ms** |
| PolyServe, 35 req/s | 31,845 | 1.9 s | 0.061 ms |
| Llumnix SLO, 35 req/s | 59,585 | 5.6 s | 0.093 ms |

(EXP-108 반복 1 / EXP-109 반복 1. 반복 2 미확인 ⚠)

**왜 싼가 — 구현상의 이유 넷:**

1. **인스턴스 상태를 캐시한다.** `instanceFlux`(상주 요청 목록, 각자의 잔액, 게이트, 투영 KV)는
   키 셋으로 캐시된다 — `(엔진 stepID, 그 인스턴스의 배치 횟수, 인스턴스 수)`
   (`fluidserve.go:411-441`). 그래서 **엔진이 새 status를 보고했거나 그 인스턴스에 배치가
   일어났을 때만** 재구성한다. 상주 요청 전체를 훑는 `reconcile`이 요청마다 도는 것이 아니라
   **status 주기(500 ms)마다 한 번** 돈다.
2. **계획 구간이 고정 100 iteration이다.** 시뮬레이션이 아니라 닫힌 형태의 산술 몇 줄이다.
3. **긴 거리 양은 미리 계산해 둔다.** `expectedRemaining(j) = E[L−j | L>j]`는 격자 153점에
   대해 **로딩 시 사전계산**되고 조회는 이진 탐색 + 선형 보간이다(`fluidserve_profile.go:47-50`이
   그 이유를 적는다 — 결정 경로가 모든 인스턴스의 모든 상주 요청에 대해 이것을 부른다).
4. **prefill 표 조회가 26점 이중선형**이고, 디코드는 **계수 셋의 곱셈 두 번**이다.

**후보마다 새로 하는 것은 이것뿐이다**(`evaluate`, 인스턴스당):
`cost` 덧셈 → prefix 적중 조회 → `meanStepMs` 한 번 → 다섯 판정 비교.

**엔진 보고가 비동기다** — 결정 경로가 엔진을 기다리지 않는다. 마지막 status 스냅샷을 읽고
바로 답한다. 그 스냅샷이 최대 500 ms 낡았다는 것이 대가이고, 시스템은 그 이상 믿지 않는다.

---

## 9. 결정에 관여하는 상수 전부

**⚠ 옛 문서의 "결정 상수 9개, 밀리초 상수는 하나"는 지금 틀리다.** 채택 구성 기준 아래가 전부다.

| 상수 | 값 | 차원 |
|---|---|---|
| `horizonSteps` | 100 | iteration |
| `zSafety` | 1.65 | σ 배수 |
| `fsAllowanceUtilisation` | 0.90 | 비율 ⚠ **근거가 문서에 없다** |
| `fsMemorySafety` | 0.95 | 비율 |
| `ttftSafetyMs` | 300 | **ms** |
| `gateSlack` | 1.0 | 비율 |
| `affinityWeight` / `affinityMetric` | 1.0 / count | 비율 / 선택 |
| `fsHarmCap` | 10.0 | 스케일 |
| `fsCorrectionAlpha` / 경계 | 0.002 / [0.5,3.0] | EMA 이득 |
| `fsPrefillAlpha` / 경계 | 0.01 / [0.02,1]·[0.25,4] | EMA 이득 |
| `fsPrefillDutyAlpha` / `fsMinPrefillDuty` | 0.1 / 0.05 | EMA 이득 / 하한 |
| `fsPlacementDelayAlpha` / 최소표본 / 표본상한 | 0.01 / 50 / **60,000 ms** | |
| `fsRetireSurvival` | 0.02 | 생존확률 |
| `fsMaxRecordAgeMs` / `fsArrivalTTLMs` | **20분 / 5분** | 시간 |
| cap 다섯 | 5,000 ms / 3.0 / 15 s / 120 s / 0.05 | |
| prefix 블록 / 용량 | 16 토큰 / 500,000 노드 | |
| chunk 대체값 | 8192 ⚠ `tierServiceRate`만 2048 | 토큰 |

**시간 차원 상수가 아홉이다**: 300 ms, 1,000 ms, 5,000 ms, 10,000 ms, 15 s, 60,000 ms, 120 s, 5분, 20분.

---

## 10. 이 절에 넣지 않을 것

- **설계 논거의 반복** — 왜 forward-looking인지, 왜 짧은 horizon인지, 왜 모으는지.
- **Fault tolerance** — 특별히 구현한 것이 없다. 스케줄러 재시작 시 온라인 적합값이 전부
  초기화되는 것(⚠ `corr`은 8분 조건의 절반을 수렴에 쓴다)은 **threats에 적고 여기 적지 않는다.**
- **migration** — 우리 arm에서 켜지 않았고, 켠 arm에서도 완료된 이동이 0건이다.

---

## 11. 아직 확인 안 한 것 (쓰기 전에 채울 것)

1. ⚠ **`max_num_batched_tokens = 8192`의 출처.** 엔진이 metadata로 보고하는 값인데 기동 인자에
   명시가 없어 vLLM 기본값인지 이미지 설정인지 확인하지 않았다.
2. ⚠ **prefix caching이 켜져 있는지** — 엔진 인자에 명시가 없어 vLLM 기본값에 의존한다.
   측정된 엔진 prefix hit rate가 28.9%이므로 켜져 있는 것은 맞지만 설정 경로를 확인 안 했다.
3. ⚠ **결정당 비용의 반복 2** — 위 표는 반복 1만이다.
4. ⚠ **스케줄러 파드의 CPU/메모리 요청·한도** — 파드 spec에서 안 읽었다. "중앙 스케줄러가
   비싸지 않다"를 주장하려면 몇 코어에서 그 수치가 나왔는지 적어야 한다.
5. ⚠ **flux 캐시 적중률** — 재구성이 status 주기마다 한 번이라는 것은 코드에서 나오지만,
   실제 적중률은 안 쟀다. `scheduler_fluidserve_flux_evaluations_total`이 발행되므로 잴 수 있다.
