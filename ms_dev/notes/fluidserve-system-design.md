# FluidServe 시스템 설계 완전판 — 코드 기준 참조 문서 (v2)

**2026-08-20 작성, 같은 날 수도코드·상태 구조·실제 상수값을 포함하도록 전면 개정.**
논문 design 섹션을 다시 정리하기 위한 기반 문서다. 배포 바이너리(EXP-64 판,
`bin/scheduler-exp07`)의 소스를 직접 읽고 만들었고, 수식과 수도코드는 코드를 그대로
옮긴 것이다. 코드와 어긋나면 코드가 맞다.

다른 문서와의 관계: 버전별 명세 [fluidserve-v0.2.md](fluidserve-v0.2.md), 설계 서사
[fluidserve-how-it-works.md](fluidserve-how-it-works.md), 사전 등록
[fluidserve-v0.1.md](fluidserve-v0.1.md). **이 문서만이 모든 변수·상수·상태 구조·입출력·
통합·코드량을 한 곳에 적는다.**

표기: `req.*`는 도착 요청, `f.*`는 인스턴스 상태(flux), `c.*`는 후보 평가 결과,
`r.*`는 상주 요청(live). 파일:행은 전부 실제 위치.

---

## §1 핵심 개념 — 넷

**① routing과 admission은 같은 질문의 서로 다른 출구다.** 모든 결정은 검사 하나로
내려진다: *"이 요청을 이 인스턴스에 놓으면, 새 요청을 포함해 그 인스턴스의 모든 요청이
각자의 SLO 예산을 지키는가."* 인스턴스별 검사 결과가 넷으로 갈린다 — **route**(통과하는
곳이 있음) / **pend**(없지만 기다릴 시간이 남음 — 게이트웨이가 붙들고 재시도) /
**force**(못 기다리지만 자기 예산은 지킴 — 피해 최소 인스턴스에 강제 배치) /
**shed**(자기 예산조차 못 지킴 — 거절). 거절은 별도 정책이 아니라 "feasible한 배치가
TTFT 예산 안에 생길 수 없다"의 이름이다.

**② 요청별 미래를 예측하지 않는다.** 대체물 셋: (a) 출력 길이는 **클래스 조건부 분포**
`E[L−j | L>j]`, (b) 상주 요청의 위태로움은 **예산 회계**(쓴 시간 대 허용 시간의 잔액 —
§4.3의 `allowanceMs`), (c) 미래 속도는 요청별이 아니라 **인스턴스당 horizon 평균 step
시간 하나**(`meanAfter`)를 예측하고 모든 상주 요청의 잔액을 그 한 값과 비교.

**③ 추정을 그 순간 이상 믿지 않는다.** 붙들린 요청은 재시도 주기(500 ms, 측정값
`recheckMs`)마다 전체 경로를 재탑승. 받아들인 요청의 예산은 이후 모든 도착의
`overIncumbents` 검사에서 반복 보호.

**④ 격리는 구성이 아니라 결과다.** 정렬의 `classShare` 항이 쏠림을 만들고 feasibility가
멈춘다. 배정이 없으므로 트래픽이 끊기면 스스로 풀린다.

**⑤ (설계 원칙) 가중합이 없다.** 결정은 사다리이고 단마다 양 하나가 정한다
(fluidserve.go:50-83 주석). 가중합 판이 과부하에서 붕괴한 실측(한 엔진에 5,569건 큐,
셋 27분 유휴)이 근거. 예외는 `affinityWeight` 하나(0~1, 정렬 점수 안).

---

## §2 컴포넌트와 각각의 상태(state)

### 2.1 전체 그림

```
클라이언트 ──요청──▶ 게이트웨이 (hold loop) ──HTTP──▶ 스케줄러 dispatch policy
                        ▲    │ 429 "no endpoint" → 500ms 후 재시도 (35s 천장)
                        │    │ 429 "admission rejected" → 즉시 클라이언트 오류
                        │    ▼ 200 + instance id
                     클라이언트                엔진 인스턴스 × N (vLLM V1)
                                                │ status (~500ms 주기) ──▶ cmsView
```

### 2.2 정책 최상위 상태 (`fluidserveDispatchPolicy`)

```go
type fluidserveDispatchPolicy struct {
    cfg      fluidserveConfig            // §7의 플래그 23개가 든 구조체 (fluidserve.go:103)
    capacity *capacityModel              // §2.3
    registry *requestRegistry            // §2.4
    prefix   *prefixIndex                // §2.5 (prefixAware=true일 때)
    lengths  *lengthModel                // §2.6

    lastObs  map[instanceID]stepObservation  // 인스턴스별 관측 이력 (§4.1)
    fluxCache map[instanceID]cachedFlux      // (stepID, registryVersion, N)이 키
}

// 인스턴스별 관측 이력 — "history trace"의 실체. 전부 EWMA이고 α는 명시.
type stepObservation struct {
    stepID, timestampMs      int64    // 직전 status의 스냅샷
    kvLogical, nDecode, pending float64
    // 아래 전부 α=0.1 (fsPrefillDutyAlpha; 유효 창 ≈ status 10개 ≈ 5초)
    prefillMsEwma float64   // 구간별 prefill에 쓴 시간(ms)의 EWMA   ┐ 나눠서
    totalMsEwma   float64   // 구간별 전체 시간(ms)의 EWMA           ┘ prefillDuty
    prefillDuty   float64   // = prefillMsEwma/totalMsEwma, [0,1] 클램프
    elapsedEwma   float64   // 구간 경과 시간 EWMA   ┐ 나눠서
    stepsEwma     float64   // 구간 step 수 EWMA     ┘ meanMs (시간가중 평균 step)
    meanMs        float64   // 이 인스턴스가 실제로 내고 있는 평균 iteration 시간
    kvSlope       float64   // 논리 KV의 변화율 (tok/ms), H2 후보용
}
```

⚠ **비율의 EWMA가 아니라 EWMA의 비율**이다. 구간별 비율을 평활하면 두 오류가 생긴다는
것이 실측됐다(fluidserve.go:713-758 주석): ① 구간별 클램프가 잡음의 양의 부분만 남겨
duty를 65% 부풀렸고(실측 평균 0.099인데 평활값 0.163), ② prefill 구간(수백 ms)과 decode
구간(약 40 ms)을 등가중하면 "시간의 몫"이 아니라 "전형적 구간의 몫"이 된다(시간가중
0.32). `meanMs`도 같은 이유로 두 누적의 비율이다 — 전체 정책의 예측이 이 값으로
수렴하므로(보정이 이것을 목표로 학습) 이 정의가 앵커다.

### 2.3 capacityModel (fluidserve_capacity.go, 397행)

```go
type capacityModel struct {
    c0  float64  // step 고정 비용.        배포 프로파일 실값: 16.361 ms
    cKv float64  // KV 토큰당 비용.        실값: 1.2822e-5 ms/tok
    cN  float64  // 디코드 요청당 비용.    실값: 0.07643 ms/req   (R²=0.952, 377,838 step)
    predictor *LatencyPredictor  // prefill step 표 (ttft.json: 1024→80.1, 4096→264.2, 8192→519.7 ms)
    correction      float64  // 예측 배율. 초기 1.0, α=0.002 곱셈 갱신, [0.5, 3.0] 클램프
    prefillFraction float64  // prefill 비용 보정 κ. 초기 1.0, α=0.01, 경계는 모드별 (§4.8)
}
```

두 EWMA가 이 컴포넌트의 살아 있는 상태 전부다. α가 왜 그 값인지도 측정돼 있다:
correction은 자기 루프(더 큰 보정 → 덜 수용 → 엔진 빨라짐 → 측정 하락)에 지연이 있어
α=0.02에서는 1.2~2.4로 진동했고, α=0.002(시상수 약 1분)에서 안정된다(:53-73).

### 2.4 requestRegistry (fluidserve_registry.go, 770행)

```go
type requestRegistry struct {
    byInstance map[instanceID]map[requestID]*dispatchRecord  // 배치 기록의 정본
    arrivedMs  map[requestID]int64   // 첫 문의 시각 (재시도에도 불변; TTL 5분)
    lastSeenMs map[requestID]int64   // 직전 문의 시각 → recheckMs 측정
    dispatchVersion   map[instanceID]uint64  // 배치마다 +1 → flux 캐시 무효화
    promptTokensSince map[instanceID]float64 // κ 보정의 분모 누적 (prefix off일 때)
    chargedPrefillSince map[instanceID]float64 // 〃 (prefix on일 때 — 예측 청구량)
    promptHashes map[requestID][]uint64 // 프롬프트 블록 해시 캐시 (요청당 1회 계산)
    // 배치→첫토큰 큐 잔차의 실측 (canWait이 씀). α=0.01, 표본 50 미만이면 미사용
    delayMean, delayMeanSq float64; delaySeen int64
}

type dispatchRecord struct {       // "이 요청이 이 인스턴스에서 돌고 있다"는 믿음 하나
    id string; tier int
    promptTokens, prefillSteps int // prefillSteps = ceil(prompt/chunk)
    stepAtDispatch int64           // 배치 시점의 엔진 step — 진행도 복원의 기준
    firstSeenMs, dispatchedMs int64
    decodeStartMs int64            // 첫 토큰 증거를 본 시각 (0 = 아직 prefill)
    prefillEstMs float64           // 결정이 예측했던 prefill 비용 (잔차 분해용)
    oracleTokens int               // EXP-64 힌트 (기본 0)
    lastJ int
}
```

### 2.5 prefixIndex (fluidserve_prefix.go, 211행)

```go
// 블록(16토큰)마다 체인 FNV-1a 해시 → "이 스케줄러가 그 블록을 보낸 인스턴스" 비트마스크
type prefixEntry struct { hash uint64; mask uint32; el *list.Element /* LRU */ }
// 상태: m map[uint64]*prefixEntry (최대 500,000블록, LRU 축출), instBit map[string]uint32
```

- **체인 해시**: 블록 i의 해시가 블록 0..i−1 전체에 의존 — 같은 16토큰이 다른 앞부분
  뒤에 오면 불일치(엔진 자신의 블록 해시와 같은 이유). 꼬리의 부분 블록은 버린다(엔진이
  캐시하는 단위가 아니라서).
- **조회 `hitTokens`**: 앞에서부터 연속 일치만 센다(첫 구멍에서 중단 — prefix cache의
  실제 규칙). 반환 = 일치 블록 수 × 16.
- **기록 `note`**: commit에서만 — pend/shed는 엔진에 닿지 않았으므로 기록 안 함.
- **한계와 처리**: 엔진의 eviction을 못 보므로 **hit 과대추정**. 그 오차는 κ가
  실측으로 흡수한다(§4.8). 인스턴스 33대째부터는 비트가 없어 hit 0으로 보고 — 전체
  프롬프트를 청구하는 보수 방향(이 함대는 4대).

### 2.6 lengthModel (fluidserve_profile.go, 255행)

```go
type classProfile struct {
    Name string; TpotSloMs int      // tier 키
    N int; Mean, P50, P90 float64   // 실값: swe(25) n=209k mean 494 / chat(50) n=1.78M mean 428 / dr(100) n=410k mean 985
    Grid []int; Survival []float64  // 생존함수 S(j) = P(L > j), 153 격자점
    expectedRemainingCache []float64 // E[L−j|L>j] 사전 계산
}
```

세 질의 (전부 격자 사이 선형 보간, 격자 밖 클램프):

```
survivalAt(j)        = S(j)
completionProb(j,k)  = (S(j) − S(j+k)) / S(j)          // j토큰 만든 요청이 다음 k step 안에 끝날 확률
expectedRemaining(j) = Σ_{x>j} S(x)/S(j) · Δgrid       // 표준 항등식의 이산형, 바닥 1
```

`S(j) ≤ 1e-9`(관측된 모든 길이를 넘김)이면 completionProb=1, remaining=1로 처리.
미등록 tier의 fallback은 **p90이 가장 긴 클래스**(KV를 가장 오래 든다고 가정 —
과소수용 방향). 기동 시 검증: c0, c_kv > 0, survival 단조감소, grid/survival 길이 일치 —
어기면 프로세스가 기동을 거부한다.

---

## §3 입력

### 3.1 요청 (게이트웨이 → 스케줄러, `types.SchedulingRequest`)

| 필드 | 뜻 |
|---|---|
| `Id` | 요청 식별자 |
| `PromptNumTokens`, `PromptTokenIds` | 프롬프트 길이·토큰 id 열(해시용) |
| `TpotSloMs` | **tier = 클래스 식별자 겸 토큰당 예산(ms)**. 배포: 50=chat, 100=deepresearch, 25=swe |
| `TtftSloMs` | 첫 토큰 예산(ms). 배포: chat 5,000 / dr 10,000 / swe 11,800 |
| `PredictedOutputTokens` | EXP-64 오라클 힌트 (기본 무시) |

tier→예산 변환 (`requestBudget`, registry.go:683):

```
spec = budgets.forTier(tier)              // --fluidserve-class-budgets 재정의 조회
                                          // 배포값 "25:e2e:30000" → tier 25는 E2E 30,000ms
미재정의 tier: nominalMs = tier값, isE2E = false
E2E tier:      nominalMs = totalMs / E[L|j=0], isE2E = true, budgetMs = totalMs
expectedToks = E[L|j=0]                   // 클래스 분포에서 (oracle이 켜져 있고 힌트가 오면 그 값)
```

### 3.2 엔진 status (인스턴스별, 약 500 ms 주기, cmsView)

`StepId`(iteration 카운터), `TimestampMs`, `NumUsedGpuTokens`/`NumTotalGpuTokens`(물리
KV), 디코드 배치에서 유도되는 `kvLogical`·`nDecode`(**논리** KV — 지연 법칙이 논리
토큰으로 적합됐고 prefix 공유가 물리 풀을 초과시키므로, :941-945),
`NumUncomputedTokens{AllWaitingPrefills, SchedulerRunningPrefills}` + inflight(대기
prefill 토큰), `SchedulerRunningToDecodeRequestsNum`(registry 기록 개수의 상한),
`MaxNumBatchedTokens`(청크; 없으면 8192), `Schedulable`.

### 3.3 프로파일 (`deploy/profiling/llama31-70b-b200-tp2/fluidserve.json`)

§2.3과 §2.6에 실값을 적었다. 구조: `decode_step_law{c0_ms, c_kv_ms_per_token,
c_n_ms_per_request, r2, samples, ...}`, `prefill_step_law{ms_at_1024/4096/8192}`,
`mixed_step_validation[]`(φ 구간별 예측/실측 검증표 — 모델 자신의 정확도 기록),
`classes[]{name, tpot_slo_ms, n, mean, p50/p90/p99/max, grid[153], survival[153]}`.
생성: `ms_dev/scripts/gen_fluidserve_profile.py` (워크로드가 바뀌면 `classes[]`만 재생성).

---

## §4 워크플로 — 요청 하나의 경로, 수도코드로

### 4.0 도착 (`calculateMetrics`, fluidserve.go:379)

```
arrivedMs, recheckMs = registry.noteArrival(id, promptTokens, now)
   // 첫 호출: arrivedMs = now 고정. 재호출: recheckMs = now − lastSeenMs (게이트웨이
   // 재시도 주기의 측정값 — 설정을 읽지 않는다. 붙들린 요청은 이 간격 안에서는 행동할
   // 수 없으므로 모든 deadline 계산이 이만큼을 미리 뺀다)
tier = request.TpotSloMs
nominalMs, expectedToks, isE2E, budgetMs = requestBudget(tier)     // §3.1
promptHashes = registry.hashesFor(id, PromptTokenIds, 16)          // 요청당 1회, 캐시
req = fluidserveRequest{...}   // 위 전부 + ttftSloMs, nowMs
for each instance view:
    observed = observeInstance(view)     // §4.1 — 측정과 보정 공급이 여기서
    f = flux(view)                        // §4.2 — 캐시 or buildFlux
    if observed ≥ 0: capacity.noteResidual(f.meanStep, observed)   // §4.8
```

### 4.1 관측 경로 (`observeInstance`, :658) — 예측의 근거가 되는 측정

```
cur = {stepID, timestampMs, kvLogical, nDecode, pending}    // 이번 status
prev = lastObs[id]                                          // 지난 status
if cur.stepID ≤ prev.stepID: return −1     // 엔진이 안 움직였으면 표본 없음 (재호출 중복 방지)
steps   = cur.stepID − prev.stepID          // 그 사이 실행된 iteration 수
elapsed = cur.timestampMs − prev.timestampMs
guard: steps > 10,000 or elapsed > 30,000ms → 버림 (재시작/정지 걸침)
guard: prev.nDecode < 1 → 버림 (놀고 있는 엔진의 step 간격은 계산 시간이 아님)

measured = elapsed / steps                 // 구간 평균 iteration 시간 — 모델이 예측하는 그 양
decodeOnly = c0 + cKv·prev.kvLogical + cN·prev.nDecode   // 구간 시작 상태의 디코드 전용 예측
prefillMs = (measured − decodeOnly) × steps  // 디코드 법칙을 넘는 시간 전부를 prefill로 귀속
notePrefill(measured, decodeOnly, steps, chunk, 분모, residual)   // κ 갱신, §4.8
// EWMA 갱신 (α=0.1): prefillMsEwma, totalMsEwma → prefillDuty
//                    elapsedEwma, stepsEwma → meanMs
//                    kvSlope ← (Δ kvLogical)/elapsed
return meanMs
```

단일 step 시각을 안 쓰는 이유: 엔진 스케줄러가 비동기라 개별 step 간격은 0 아니면 참값
둘 중 하나라 귀속 불가(:645-656). 게이지로 `decode_only_ms`·`obs_kv_tokens`·
`obs_decode_batch`를 같이 내보내 예측-실측 간극이 네 원인(법칙/배치/prefill 항/보정) 중
어디서 났는지 갈 수 있게 한다.

### 4.2 인스턴스 상태 구성 (`buildFlux`, :922)

캐시: `(엔진 stepID, registry.dispatchVersion, 인스턴스 수)`가 같으면 재사용(:310-338) —
**엔진이 안 움직였어도 내 배치가 있었으면 다시 만든다.**

```
f.kvLogical, f.nDecode = decodeBatchOf(status)
f.pendingPrefill = 대기 + 실행중 + inflight prefill 토큰
f.chunk = MaxNumBatchedTokens (기본 8192)
f.live = registry.reconcile(id, engineRunning, stepID, now)      // §4.3

// 미래 prefill: 큐(순간)와 duty 투영(흐름)의 max — 합이면 같은 프롬프트 이중 청구
horizonMs = 100 × paceMs(id)          // paceMs = 실측 meanMs, 없으면 디코드 법칙 (낙관 방향)
f.arrivingPrefill = duty × horizonMs / (prefillStepMs(chunk) − c0) × chunk
f.effectivePrefill = max(f.pendingPrefill, f.arrivingPrefill)

f.meanStep = meanStepMs(f.kvLogical, f.nDecode, f.effectivePrefill, chunk, 100)  // 지금 pace

// 두 문턱 — 상주 요청을 훑으며 (제외 규칙은 §4.3의 allowance 뒤에)
floor = c0 × correction                       // 빈 인스턴스의 step 비용
for r in f.live:
    if r.allowanceMs < floor:  r.unachievable = true; continue   // 아무도 못 구함 → 양쪽 제외
    gateAllowance    = min(gateAllowance, r.nominalMs)            // 명목 예산의 최소
    if r.allowanceMs < f.meanStep: continue    // 이미 놓치는 중 → 거절해도 못 구하므로 제외
    tightestAllowance = min(tightestAllowance, r.allowanceMs)     // 잔액의 최소

// KV 흐름 수지 (enableFlux=true; 끄면 inflow=outflow=0 = 순수 level 제어기)
inflow  = f.nDecode × 100                      // 디코드는 step당 요청당 정확히 1토큰
outflow = Σ_r  completionProb(r.j, 100) × r.kvTokens              // 기대 방출
        − 1.65 × sqrt( Σ_r p(1−p)·kvTokens² )                     // Bernoulli 합의 1.65σ 하한
f.proj  = max(0, f.kvLogical + inflow − outflow)

f.capKv  = maxKvForAllowance(gateAllowance × 0.90, nDecode, effectivePrefill, chunk, 100)
ratio    = clamp(kvPhysical / kvLogical, 0.01, 1)   // prefix 공유 배율 (실측; EXP-16에서 0.09까지 관측)
f.capMem = kvCapacity × 0.95 / ratio                // 물리 용량을 논리 단위로 환산
f.headroom = min(capKv, capMem) − proj
```

### 4.3 상주 요청의 복원과 잔액 (`reconcile` registry.go:471, `liveViewLocked` :613)

```
for each dispatchRecord rec on this instance:
    if stepID < rec.stepAtDispatch: delete    // step이 뒤로 갔다 = 엔진 재시작
    if age > 20min: delete                    // 백스톱
    j = stepID − rec.stepAtDispatch − rec.prefillSteps     // 만든 토큰 수 = step 차이
    if j > 0 and rec.decodeStartMs == 0:
        rec.decodeStartMs = now               // 첫 토큰의 증거 — 이후 회계는 벽시계 기준
        notePlacementDelay(now − dispatchedMs − rec.prefillEstMs)   // 큐 잔차 표본 (§4.6)
    if survivalAt(j) < 0.02: delete           // 관측된 길이의 꼬리를 넘김 = 끝났다고 판정
if len > engineRunning: j 큰 것부터 삭제       // 엔진 카운트가 authority (끝났을 확률 최대 순)

// 잔액 — "남은 토큰 하나당 앞으로 쓸 수 있는 시간". 예산 회계이지 속도 측정이 아니다.
remaining = oracleTokens>0 ? max(1, oracle − j) : E[L−j | L>j]
토큰당 클래스:
    decodeStartMs==0 (아직 prefill): allowance = perTokMs        // 디코드 예산은 미사용
    else: spent = now − decodeStartMs                            // 실제 쓴 벽시계 시간
          total = perTokMs × (j + remaining)                     // 전체 수명에 허용된 시간
          allowance = (total − spent) / remaining                // 느리게 돌았으면 <예산, 빠르면 >예산
E2E 클래스:
    allowance = (totalMs − (now − firstSeenMs)) / remaining      // 대기·prefill·디코드가 한 계좌
allowance = max(0, allowance)
nominalMs = 클래스의 시작 예산 (tier값 또는 totalMs/E[L])         // 인스턴스 gate용 — 요청이
                                                                  // 뒤처져도 안 움직인다 (되먹임 차단)
```

### 4.4 후보 평가 (`evaluate`, :1490)

```
// 이 요청의 prefill 비용 — 인스턴스별로 다르다 (prefix)
raw = promptTokens − hitTokens(promptHashes, instance)     // 이 인스턴스가 든 연속 prefix 블록 할인
c.prefillCharge = raw × κ                                  // κ = 측정된 잔차 배율 (§4.8)

cost       = promptTokens + min(100, expectedToks)   // horizon 안의 KV 성장만 (E[L]=500이어도 100)
newKv      = f.proj + cost
newN       = f.nDecode + 1
newPending = f.effectivePrefill + c.prefillCharge
c.meanAfter = corr × [ (k−sp)·(c0 + cKv·newKv + cN·newN) + sp·(t_pre(chunk) + dec − c0) ] / k
              // k=100, sp = prefillSteps(newPending, chunk) — 1 미만이면 1로 올리지 않고
              // 부분 청크는 부분으로 (반올림 3.0ms가 판정을 뒤집은 실측이 주석에, capacity.go:303)

gate = req.nominalMs
if !ownBudgetGate and gateAllowance × gateSlack < gate: gate = gateAllowance × gateSlack
c.gateAfter = gate × 0.90

unpredictable  = isInf(meanAfter)
overGate       = meanAfter > c.gateAfter          // 약속(명목)의 보호 — 자기 예산도 min에 포함
overIncumbents = meanAfter > f.tightestAllowance  // 잔액의 보호 — 0.90 없음
overMemory     = newKv > f.capMem
overDeadline   = deadlineFeasible && (waited + c.prefillMs > ttftSloMs)   // 기본 꺼짐 (EXP-87)
c.feasible = !(unpredictable || overGate || overIncumbents || overMemory || overDeadline)
// 실패 조건은 각각 카운터로 (동시 실패도 전부): scheduler_fluidserve_infeasible_total{reason}

c.share = 이 인스턴스 상주 중 같은 tier의 비율 (빈 인스턴스 = 0)
c.harm  = harmToIncumbents(...)                    // §4.5
c.room  = (min(capKv,capMem) − newKv) / max(capMem, 1)
c.prefillMs = 자기 prefill 시간 + 앞에 쌓인 큐를 그 큐의 청크 단가로 값매긴 시간 (:2149)
c.missesTtftDeadline / c.missesOwnBudget           // §4.6
```

### 4.5 정렬 (`sortCandidates`, :1398)과 harm (:1872)

```
정렬: feasible 그룹 먼저.
  feasible끼리:  score = w·share + (1−w)·room 내림차순    // w = affinityWeight, 배포 1.0
                 동점 → room 내림차순 → 인스턴스 id (재현성)
  infeasible끼리: harm 오름차순 → room → id

harm = Σ over 살릴 수 있는 상주 r (unachievable 아님, slack = r.allowanceMs − meanBefore > 0):
           min( (meanAfter − meanBefore) / slack , 10 )
     + (classHarm이면) w × (1 − share) × 10
// 이미 예산을 넘긴 요청은 0 기여 — 무너진 인스턴스가 계속 흡수하고 멀쩡한 곳은 깨끗하게.
// 클래스 항은 그 비대칭이 "누가 부쉈는지"를 모른다는 구멍을 메움. harm은 infeasible
// 정렬에만 쓰이므로 = 강제 배치 목적지에만 작용 (45 req/s에서 결정의 약 5%)
```

### 4.6 4-way 분기 (`selectInstance`, :1250)

```
best = 정렬 1위
1) if best.feasible: commit(best, "route"); return best

2) if enablePend and canWait(best, req): return nil    // 게이트웨이가 붙듦
   canWait (:2116):
     after = best.prefillMs + queueBound
       queueBound = delaySeen ≥ 50 ? delayMean + 1.65·sqrt(delayMeanSq − delayMean²)
                                   : 300ms 고정
       // delayMean/MeanSq: 배치→첫토큰 실측에서 그 결정의 prefillEstMs를 뺀 "큐 잔차"의
       // EWMA 쌍 (α=0.01). 프롬프트 의존 부분(prefill)은 요청별로, 비의존 부분(엔진 큐,
       // 도는 iteration의 꼬리)은 함대 실측으로 — 하나로 합치면 chat(76.9%)의 값이 되어
       // 10배 긴 프롬프트의 agent 요청을 잘못 움직인 실측이 주석에 (registry.go:247-268)
     토큰당: waited < ttftSloMs − after − recheckMs
     E2E:    waited < budgetMs − after − expectedToks×meanAfter − recheckMs

3) shedTest = shedFleetScale>0 ? missesOnFleet(전 후보 평균, scale) : best.missesOwnBudget
   missesOwnBudget (:1735):
     E2E:    waited + prefillMs + expectedToks×meanAfter > budgetMs
     토큰당: (waited + prefillMs > ttftSloMs)  or  (meanAfter > nominalMs ×(forceMargin?0.90:1))
   if enableShed and shedTest: registry.forget(id); return "admission rejected"

4) commit(best, "force")   // 못 기다리지만 자기 예산은 지킴 — harm 최소인 곳에
```

⚠ shed가 `best`(실제로 만들 배치)에 묻는 것은 의도다(:1330-1348): 다른 인스턴스가
이론상 서비스할 수 있어도 그곳을 안 쓰는 이유는 정렬이 거른 이유(멀쩡한 상주를 밀어냄)와
같으므로, "만들 의향이 있는 배치가 자기 예산을 못 지키면 그 배치는 만들 가치가 없다".

### 4.7 commit (`commit` :2220, `onDispatch` registry.go:427)

```
onDispatch: byInstance[inst][id] = dispatchRecord{ stepAtDispatch=지금 stepID,
             prefillSteps=ceil(prompt/chunk), prefillEstMs=c.prefillMs, ... }
            dispatchVersion[inst]++            // → 다음 결정의 flux 재구성
            promptTokensSince[inst] += prompt; chargedPrefillSince[inst] += charge  // κ 분모
prefix.note(hashes, inst)                      // route/force만 — pend/shed는 캐시에 안 닿음
계기: dispatch_ordinal_in_step (같은 status로 몇 건 판정했나),
     headroom_move_in_step (뒤 배치가 읽은 headroom이 움직였나 — 0이면 이중 판매 결함)
```

결정 경로 전체는 cluster-view 잠금 아래 **직렬**(:1166-1175). 동시 결정은 없고, 직전
배치는 registry를 통해 다음 결정의 live·문턱·share에 즉시 반영된다. 엔진 보고 항
(kvLogical 등)만 다음 status까지 낡는다(포화에서 status 하나당 평균 5.24건 배치 —
그 낡음을 재는 계기가 위의 둘).

### 4.8 온라인 보정 두 루프 — 정확한 갱신식

```
① step 시간 보정 (noteResidual, capacity.go:97):
   ratio = measured / predicted          // predicted는 이미 보정된 값 → 곱셈 갱신
   guard: ratio ∉ [0.2, 5] 버림
   correction *= 1 + 0.002·(ratio − 1);  clamp [0.5, 3.0]
   // α=0.002(시상수 ~1분)인 이유: 루프에 지연이 있어 α=0.02는 1.2~2.4로 진동 (실측)

② prefill 비용 κ (notePrefill, capacity.go:180):
   prefillMs = (measured − decodeOnly) × steps          // 구간의 prefill 시간 귀속
   computed  = prefillMs / (t_pre(chunk) − c0) × chunk  // → 엔진이 실제 계산한 토큰 환산
   ratio     = computed / 분모
     분모 = prefix off: 그 구간에 보낸 프롬프트 토큰 전체 (κ = "엔진이 실제 계산하는 비율",
             clamp [0.02, 1.0])
           prefix on:  그 구간의 예측 청구량 합 (κ = "예측의 잔차", clamp [0.25, 4.0] —
             1 초과 허용이 핵심: index가 eviction을 못 봐서 hit을 과대 주장하는 바로 그
             실패가 κ>1로 나타나야 하므로)
   prefillFraction += 0.01·(ratio − prefillFraction)
   // ⚠ 분모는 보정 전 양 — 자기가 곱한 값으로 나누면 κ가 참값의 √로 수렴 (EXP-67 실측)
```

제거된 세 번째 루프도 기록해 둘 가치가 있다(:2272-2284 주석): z를 온라인 조정하는 안전
루프는 두 번 만들어 두 번 제거됐다 — 예산 대비 비교판은 과부하에서 항상 발화해 z를
천장에 고정했고, 잔차 대비 판은 상수 7개를 쓰고도 한 번도 움직이지 않았다. z는 설정값
그대로다.

---

## §5 출력

**결정**: 인스턴스 id(route/force) / 429 "no available endpoint"(pend) /
429 "admission rejected"(shed — 게이트웨이가 즉시 클라이언트 오류로 변환).

**Prometheus 시리즈** (분석의 정본 — 요청별 로그는 V(5)이고 V(4)에서 1분 안에 회전):

| 시리즈 | 내용 |
|---|---|
| `scheduler_fluidserve_decisions_total{decision}` | route/pend/shed/force |
| `scheduler_fluidserve_infeasible_total{reason}` | unpredictable/gate/incumbents/memory/deadline (동시 실패 각각) |
| `..._observed_step_ms` / `..._predicted_step_ms` | 보정의 두 입력 |
| `..._decode_only_ms` / `..._obs_kv_tokens` / `..._obs_decode_batch` | 예측-실측 간극의 원인 분해 |
| `..._raw_step_ms` / `..._obs_steps` | 구간 원시 측정 |
| `..._gate_allowance_ms{instance}` | 인스턴스가 무엇에 gate되는지 (전용 인스턴스 표식) |
| `..._headroom_tokens` / `..._cap_kv_tokens` | 용량 상태 |
| `..._flux_{evaluations,flips}_total{level}` | 투영이 결정을 바꾼 횟수 (candidate/decision/target) |
| `..._oracle_total{hint}` | EXP-64 |
| `gateway_scheduling_{waited,rejected,gave_up}_total`, `gateway_scheduling_wait_milliseconds` | 게이트웨이 쪽 |
| `dispatch_ordinal_in_step`, `headroom_move_in_step` | 이중 판매 감시 |

`scheduler_dispatch.log`의 배치 라인이 요청→엔진 귀속의 원천(부하 최고 구간 유실 가능 —
CLAUDE.md 함정 D).

---

## §6 상수 전체 표

| 상수 | 값 | 무엇 |
|---|---|---|
| `horizonSteps` | 100 iteration | 계획 지평 (디코드 성장 = 요청·step당 1토큰이 되는 단위) |
| `zSafety` | 1.65 | outflow 하한과 큐지연 상한의 σ 배수 (같은 z) |
| `fsAllowanceUtilisation` | 0.90 | gate에 곱하는 여유 |
| `fsMemorySafety` | 0.95 | 물리 KV 사용 가능 비율 |
| `fsHarmCap` | 10.0 | 상주 하나의 harm 상한 = 클래스 항 스케일 |
| `fsCorrectionAlpha` / 경계 | 0.002 / [0.5, 3.0] | step 보정 EWMA |
| `fsPrefillAlpha` / 경계 | 0.01 / [0.02,1.0] 또는 [0.25,4.0] | κ EWMA (모드별 경계) |
| `fsPrefillDutyAlpha` | 0.1 | duty·meanMs·kvSlope EWMA (유효 창 ≈ 5초) |
| `fsPlacementDelayAlpha` / 최소 표본 / 상한 | 0.01 / 50 / 60 s | 큐 잔차 실측 |
| `ttftSafetyMs` | 300 ms | 표본 <50일 때의 고정 큐 여유 |
| `fsRetireSurvival` / `fsMaxRecordAgeMs` | 0.02 / 20 min | 상주 기록 퇴역 |
| `fsArrivalTTLMs` | 5 min | 도착 기록 보존 (게이트웨이 hold 창보다 길게) |
| prefix 블록 / 용량 | 16 tok / 500,000 블록 LRU | FNV-1a 체인 해시 |
| chunk | 엔진 `MaxNumBatchedTokens` (fallback 8192) | prefill step 계산 |
| observeInstance guard | steps>10,000 / elapsed>30 s / nDecode<1 | 표본 버림 조건 |
| noteResidual guard | ratio ∉ [0.2, 5] | 〃 |
| 게이트웨이 (FluidServe만) | 천장 35,000 / 재시도 500 ms | 다른 정책은 5,000/1,000 |
| 프로파일 실값 | c0=16.36 ms, c_kv=1.282e-5, c_n=0.0764 (R²=0.952) | decode 법칙 |
| 〃 | t_pre: 1024→80.1 / 4096→264.2 / 8192→519.7 ms | prefill step |

---

## §7 플래그 (23개 전부, `--fluidserve-*`)

| 플래그 | 기본값 | 무엇 |
|---|---|---|
| `profile-path` | "" (필수) | §3.3 파일. 없거나 깨지면 기동 거부 |
| `class-budgets` | "" (배포 `25:e2e:30000`) | tier 예산 형태 재정의 |
| `horizon-steps` | 100 | 지평 |
| `z-safety` | 1.65 | σ 배수 |
| `ttft-safety-ms` | 300 | 큐 여유 폴백 |
| `enable-pend` / `enable-shed` | true / true | 보유/거절 |
| `shed-signal` | coupled | 거절이 best를 보나 함대 평균을 보나 (fleet[:scale]) |
| `oracle-length` | false | EXP-64 힌트 |
| `enable-affinity` / `affinity-weight` | true / 1.0 | 클래스 선호와 세기 w |
| `prefix-aware` | true (v0.2) | 인스턴스별 prefill 비용 |
| `prefix-calibration` | true | κ 적용 |
| `prefix-block-tokens` / `prefix-capacity` | 16 / 500,000 | index 크기 |
| `class-pin` | "" | 정적 고정 (ablation 전용; 파싱 실패 시 기동 거부) |
| `enable-flux` | true | 흐름 수지 (끄면 level 제어기) |
| `force-margin` | false | force 판정에도 0.90 |
| `own-budget-gate` | false | gate에서 인스턴스 min 제거 |
| `deadline-feasible` | false | feasibility에 TTFT 항 (EXP-87) |
| `kv-slope-projection` | false | 투영을 실측 기울기로 (H2) |
| `gate-slack` | 1.0 (<1이면 기동 거부) | 인스턴스 min을 몇 배까지 넘나 |
| `class-harm` | true | harm의 클래스 항 |

잘못된 값이 조용히 대조군이 되는 것을 막는 장치가 세 곳(budgets/pin/shed-signal 파싱
실패 = panic) — bool 플래그 함정으로 ablation 4시간을 잃은 뒤의 규칙.

---

## §8 코드량과 통합

| 부분 | 행 수 |
|---|---|
| `fluidserve.go` (결정 경로·관측·selector) | 2,515 |
| `fluidserve_registry.go` (기록·회계·예산·큐 잔차) | 770 |
| `fluidserve_capacity.go` (지연 모델·보정 둘) | 397 |
| `fluidserve_profile.go` (길이 분포) | 255 |
| `fluidserve_prefix.go` (prefix index) | 211 |
| **정책 합계** | **4,148** |
| 단위 테스트 | 2,336 |
| 플래그 정의 (config.go) | 약 200 |

**엔진(vLLM)은 한 줄도 바꾸지 않았다.** 게이트웨이는 기존 Llumnix hold loop
(scheduler_client.go:176-235)를 재사용하고, 더한 것은 같은 429 본문을 두 의미(pend용
"no available endpoint" / shed용 "admission rejected")로 가르는 수십 행뿐이다.

통합 지점 여섯: ① `scheduling_policy_registry.go`의 `case SchedulingPolicyFluidserve`
(Llumnix dispatch policy 인터페이스 구현 — 기존 filter 파이프라인은 안 씀: 보유·거절이
선택지라 모든 인스턴스와 한 자리에서 비교해야 하므로 selector 하나가 전부), ②
`verifySchedulingPolicy` 화이트리스트(빠지면 기동 panic), ③ `schedulingCtx`에
`fluidserveRequest`/`fluidserveFlux` 필드 추가, ④ 상태 원천은 Llumnix cmsView 그대로
(자체 계측 없음), ⑤ 배포는 hostPath `bin/` + `set_scheduler_profiling.py`(플래그
주입·기동 줄 검증 `verified:`), ⑥ 프로파일 생성 `gen_fluidserve_profile.py`.

기준선 다섯(FluidServe·Llumnix·Llumnix SLO·PolyServe·vLLM router)이 같은 게이트웨이·
같은 status 경로를 공유한다 — evaluation의 "제어 평면만 다르다"가 구조에서 나온다
(예외: 게이트웨이 대기 창은 FluidServe만 35,000/500 — EXP-83·84가 측정, 우리 수치가
보수적인 방향).

---

## §9 알려진 결함과 열린 축

1. **feasibility에 TTFT 항이 없다.** `waited + prefillMs > ttftSloMs`는 계산되고
   코드에 있는데 `if best.feasible { route }`가 먼저 끝나 도달하지 않는다. 전용
   인스턴스의 느슨한 gate(100 ms)가 그곳을 항상 feasible하게 보이게 하고 prefill 줄
   (실측 76k~88k 토큰 ≈ 11초)을 아무도 묻지 않아 deepresearch 첫 토큰 12.6~13.2초
   (예산 10초). 스위치는 `--fluidserve-deadline-feasible` (EXP-87 후보). 정본
   `28_backlog_at_placement.md`.
2. **gate 축.** `gateSlack`의 양 끝이 반대로 실패하는 것이 둘 다 측정됨
   (fluidserve-v0.2.md §6.1).
3. **게이트웨이 천장이 함대 배치를 부작용으로 정한다** (EXP-84). 정책은 클래스가 모이는
   것을 선호하지만 그 선호가 만드는 상태(한 클래스가 함대의 얼마를 자기 예산으로 gate하는
   가)를 평가하는 변수가 없다. 논문 미해결 항목.
4. **요청 안 p90은 라우팅으로 닿지 않는다** — chat 자신의 prefill만으로 φ > 0.10
   (필요: < 0.10), 시간 배치를 바꿔도 안 됨(EXP-85). 남는 수단은 admission 양. 판정은
   평균·누적 deadline(문헌 관행), 분위수는 별도 표.
