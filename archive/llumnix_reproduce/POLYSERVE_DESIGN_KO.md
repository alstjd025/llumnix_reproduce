# PolyServe on Llumnix — 구현 설계

> 작성 2026-07-25. 대상: `arXiv:2507.17769` PolyServe: Efficient Multi-SLO Serving at Scale
> 환경: NXC13 k3s, Llumnix, 4× vLLM 인스턴스(각 TP2, Llama-3.1-70B), B200×8
> 범위: **autoscaling 제외** (사용자 지시)

---

## 0. 확정된 설계 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| SLO tier 수 | **3** (chat / deepresearch / swe) | SLO가 서로 다른 클래스 수만큼 |
| tier→서버 배치 | **동적 재분할, fleet 4대 고정** | 논문에 정적 알고리즘 없음(autoscaler 담당) → 인스턴스 수 변경 없이 그 역할만 재현 |
| within-tier 선택 | **최저부하 (least-load)** | autoscaling 없으면 packing 보상 0; TTFT ∝ 큐 깊이; 버스트/예측오차 여유 확보 |
| 부하 지표 | **SLO 차원별 바인딩** | TTFT 바인딩→predicted TTFT 최저, TPOT 바인딩→predicted TPOT 최저. Llumnix가 둘 다 이미 계산 |
| 엔진 스케줄러 | **stock FIFO** | PolyServe는 SLO-무지 엔진 가정. 라우팅 효과만 깨끗이 귀속 |
| prefix cache | **무시** | 논문이 라우팅에 안 씀. 흩뿌림에 따른 캐시 히트 저하는 PolyServe의 비용으로 기록 |
| swe SLO 표현 | **TTFT 11.8s / TPOT 25ms** | e2e 30s − 728tok×25ms. EXP-17과 동일 기준 → 5-arm과 직접 비교 |

### ⚠️ 결정의 귀결 (알고 시작할 것)
- swe의 TPOT 25ms는 **chat(50ms)·dr(100ms)보다 빡빡** → tier 우선순위가 직관과 반대로 뒤집힘.
- swe TPOT 25ms는 B200 실측 solo decode ITL 중앙값과 동일 → **배치가 조금만 커져도 위반**. PolyServe 원칙("idle 서버 즉시 배정 시 달성 가능한 SLO만 부여")의 경계선.
- 토큰 질량 chat 2.5% / dr 15% / swe 82% → 수요 비례 배분은 swe 3.3대를 요구하나 fleet이 4대뿐 → **swe tier는 구조적으로 포화**. 이는 tier 격리의 의도된 결과(swe를 희생해 chat/dr 보호).

### 상속되는 기본동작 (명시적 가정, 필요시 변경)
- **feasible 서버가 없을 때**: Llumnix 기존 2-pass fallback(strict 필터 → 완화 필터) + 게이트웨이 hold-and-retry(`WaitSchedulingTimeout` 5s / `RetryInterval` 1s)를 그대로 사용. 즉 거절하지 않고 완화 배정 또는 대기.

---

## 1. PolyServe 핵심 아이디어 → 우리 구현 매핑

| 논문 아이디어 | 우리 구현 |
|---|---|
| ① SLO tier 세분 (TPOT bin) | 3 tier (chat/dr/swe), per-request SLO를 게이트웨이가 전달 |
| ② DSLO (누적 데드라인) | **v2로 보류** (v1은 순간 threshold) |
| ③ tier별 큐 + 서버 파티션 | tier별 서버 파티션 = 동적 재분할기. 큐는 게이트웨이 hold-and-retry로 근사 |
| ④ 최고부하 라우팅 | **반전: 최저부하** (autoscaling 제외로 근거 소멸) |
| ⑤ §4.6 wait-time-aware | **채택** — 아래 admission test 3단 판정 |
| ⑥ §4.5 max-KV 미래 시뮬레이션 | **채택** — 스냅샷이 아니라 요청 생애 최대 KV로 판정 |
| ⑦ §4.7 continuous chunked prefill prediction | **채택** — prefill 간섭항 (co-location이라 필수) |
| profiling table `(batch,KV)→iter` | Llumnix `ITLData{batch_size, tokens_per_request}` **이미 동일 축** |
| 미래 iteration 시뮬레이션 | `predictTtftLatencyByChunkPrefill()` **이미 존재** |

### 논문 재확인 결과 (2026-07-25, arXiv:2507.17769 원문)

초기 분석에서 admission test를 `wait+T_iter<TPOT` 한 줄로 요약했는데, 원문을 다시 읽으니
**세 개의 독립된 메커니즘**이었다. 셋 다 반영하기로 확정.

- **§4.6 Wait-Time-Aware Scheduling** (초록의 3대 기여 중 하나). 큐잉을
  *pending time*(대기 큐) + *wait time*(배정된 서버가 현재 iteration을 끝낼 때까지)로 분해.
  적용 범위가 명시적으로 **첫/둘째 토큰**뿐 — 3번째부터는 정상상태라 iteration time만 본다.
  > "profile-based batch formation ... is only effective from the second decode token.
  > The first token, regulated by the TTFT, and the second token, regulated by TTFT + TPOT,
  > incur queueing time."
- **§4.5** 판정 기준이 현재 스냅샷이 아니다.
  > "PolyServe simulates future iterations and computes the **maximum KV cache size** as
  > requests grow in length ... Based on the largest KV cache size and current token batch
  > size, PolyServe uses the profiling table to admit the request when the predicted
  > iteration time is less than the TPOT."

  출력 길이는 논문도 예측하지 않고 "average decode length"를 쓴다 → 우리는 실측 클래스 평균
  (chat 386 / deepresearch 275 / swe 728) 사용.
- **§4.7 co-location: continuous chunked prefill prediction.** PD-분리는 prefill이 decode와
  안 섞이지만 co-location(=우리)은 섞인다.
  > "PolyServe only admits requests if the predicted chunk size can be maintained throughout
  > the prefill process. Otherwise, PolyServe will look for other lower-load machines."
- **DSLO 정의**: "the i-th token must be produced before **TTFT + i·TPOT**". 누적이라 한 스텝이
  튀어도 뒤에서 만회 가능하고, 논문의 평가 지표 자체가 DSLO attainment다. §4.5의 보수적 판정과
  짝을 이룬다(판정은 빡세게, 채점은 누적으로). 우리는 v1에서 판정만 채택하고 채점은 EXP-17과
  동일한 순간 기준을 유지 → 5-arm과 직접 비교 가능하게.

### Llumnix 현 구현과의 정확한 차이

| | 큐잉 반영 | 근거 |
|---|---|---|
| `PredictedTtft` | **있음, 주항** | `allPrefillsTokensNum` = 대기큐 + 진행중 prefill + inflight dispatch, 거기에 `- elapsedTimeMs` staleness 보정 + 큐 소진 분기 |
| `PredictedTpot` | **없음** | `predictTpotLatency(decodeReqsNum, decodeTokensNum)` 단일 조회. 단 `decodeBatchSize`가 waiting-to-decode + loading + inflight를 포함하므로 *부하*는 앞당겨 보되 *대기 시간*은 지연에 더하지 않음 |

즉 큐잉은 "시간"이 아니라 "남은 토큰"으로 들고 있다가 프로파일 테이블로 시간 환산하는 구조.
TTFT 쪽은 이미 논문과 사실상 동등하고, **빠진 것은 TPOT 쪽 3개(⑤⑥⑦)**.

---

## 2. Llumnix 기존 자산 (재사용)

| 자산 | 위치 |
|---|---|
| SLO 정책 골격 | `pkg/scheduler/policy/scheduling_policy_registry.go` `newSloDispatchFullMode` |
| 지연 예측기 | `pkg/scheduler/policy/predict_utils.go` `LatencyPredictor` (2D 보간) |
| per-instance 예측 지표 | `pkg/scheduler/policy/metrics.go` `PredictedTtft` / `PredictedTpot` |
| bin-packing selector (대조군) | `pkg/scheduler/policy/scheduling_selectors.go:188` `sloDecodeApdSelector` |
| 필터/셀렉터 플러그인 | `filters.go:57-66`, 2-pass(strict→fallback) |
| **per-request 뷰 복사** | `Schedule()`이 `toClusterViewScheduling()`로 요청마다 새 복사 → **레이스 없이 요청 컨텍스트 주입 가능** |

---

## 3. 구현 계획

### P0. 프로파일링 데이터 생성 — ✅ 완료 (커밋 `ed7c3a5`)
`GetLatencyPredictor()`는 파일 로드 실패 시 `klog.Fatalf` → 데이터 없으면 스케줄러 기동 불가.

산출물 `deploy/profiling/llama31-70b-b200-tp2/{ttft,tpot}.json`.
출처·신뢰도·함정은 **`deploy/profiling/README.md`가 정본**. 요약만:

- 소스는 EXP-16 `sched_steps.jsonl` 431,440 step (13 run, stock FIFO `InstrumentedScheduler`).
  생성기가 QoServe/deadline run은 이름으로 거부한다 — `DeadlineScheduler`는 매 스텝 청크를
  바꾸므로 stock 엔진의 물리를 설명하지 못한다.
- `tpot.json` 신뢰도 **양호**: decode-only 377,838 step, 247칸 중 78칸 실측.
- `ttft.json` **잠정**: 비동기 스케줄링 때문에 `interval_ms`가 bimodal이라 스텝별 귀속 불가.
  지속 포화 구간은 full-chunk에서만 생겨 **실측점이 1개**(12,917 tok/s, 교차검증 13,300).
  → **P4 전에 유휴 엔진 프롬프트길이 스윕으로 교체**(업스트림도 그 방식).
- 그리드는 **완전한 데카르트 곱**이어야 한다. `InterpolationPredictor`가 marginal 축으로
  bounding box를 잡고 4모서리를 전부 요구하며, 범위 밖 질의는 +Inf가 되어 해당 인스턴스가
  모든 SLO 필터에서 조용히 탈락한다.

검증됨: `./bin/scheduler-exp07 --scheduling-policy slo` →
`Initialized LatencyPredictor with 26 TTFT points and 247 TPOT points`, Fatalf 없음.

### P1. per-request SLO 배관
1. `pkg/types/scheduling_request.go` `SchedulingRequest`에 `TtftSloMs`, `TpotSloMs` 추가
2. 게이트웨이가 채움 — QoServe 때 뚫어둔 `priority` 패킹(`slo_ms*1000+tbt_ms`) 재사용
3. `schedulingCtx`(per-instance, 요청마다 새로 만들어짐)에 요청 SLO 필드 추가
4. `DispatchPolicy.schedule(request, view)`에서 각 인스턴스뷰에 주입
   → **필터/셀렉터 인터페이스 변경 불필요** (현재 시그니처는 request를 안 받음)

### P2. PolyServe 정책
1. `consts.SchedulingPolicyPolyserve` 등록, `newDispatchPolicyInternal` 분기 추가
2. `tierAffinityFilter` — 요청 tier의 현재 배정 서버만 통과
3. `polyserveAdmissionFilter` — **3단 판정** (§4.5~4.7 전부 반영, 사용자 확정 2026-07-25).
   전역 `p.TtftSlo`/`p.TpotSlo` 대신 `schedulingCtx`의 요청별 SLO를 threshold로:

   | 단계 | 논문 | 판정식 | 재료 |
   |---|---|---|---|
   | 1토큰 | TTFT | `PredictedTtft ≤ TTFT_slo` | 이미 큐잉 포함(대기+진행+inflight) — 그대로 사용 |
   | 2토큰 | §4.6 TTFT+TPOT | `PredictedTtft + T_iter ≤ TTFT_slo + TPOT_slo` | wait 항 = `PredictedTtft`. 추가 비용 거의 없음 |
   | 3토큰~ | §4.5 i·TPOT | `T_iter(batch, KV_max) ≤ TPOT_slo` | **스냅샷 아님**. `KV_max = KV_now + batch × 남은평균출력` |

   여기서 `T_iter`는 §4.7 co-location 보정 포함:
   `T_iter = tpot(batch, KV_max) + [대기 prefill 있으면] ttft(chunk)`.
   우리 실측상 full-chunk 스텝이 634 ms로 decode 스텝(17~113 ms)의 6~37배라, 이 항을 빼면
   TBT 예측이 무의미해진다. `predictTpotLatency`는 prefill 인자를 아예 받지 않으므로 신규 항.
   남은평균출력은 논문과 같이 예측하지 않고 실측 클래스 평균(chat 386 / dr 275 / swe 728) 사용.
4. `leastBindingLatencySelector` — 요청의 바인딩 차원에서 predicted latency 최소 인스턴스 선택
5. 대조군은 기존 `sloDecodeApdSelector`(bin-packing) 그대로 → **논문의 "최고부하" 주장 직접 검증 가능**

### P3. 동적 재분할기 (tier → 서버) — ✅ 완료
논문에 없음(autoscaler 역할). PolyServe 스케일 규칙의 수렴점을 fleet 고정으로 재현.
구현 `pkg/scheduler/policy/polyserve_repartition.go`.

**라이브 실측으로 예측값에 수렴 확인** (하드코딩 아님, 관측 demand에서 유도):
```
PolyServe repartition: 25ms=2 50ms=1 100ms=1
  (demand 25ms=0.65 50ms=0.02 100ms=0.07, 4 live servers)
```
demand 비율 88/3/9%가 토큰질량 비율(82.6/2.5/15%)보다 swe로 더 쏠리는데, swe의 25ms TPOT가
배치를 작게 강제해 같은 출력이 더 많은 서버-초를 먹기 때문. 이게 "빡빡한 tier가 요청당 서버를
더 받는다"는 메커니즘이고 테스트로 고정해둠.

```
주기 T(예 10s)마다:
  for each tier:
    demand_T = 관측 arrival_rate_T × 요청당 서버-초 비용
             (prefill: in_tokens / prefill_tput
              decode : out_tokens / B_max(TPOT_T))     # B_max는 프로파일 테이블에서
  servers_T = max(1, round(N × demand_T / Σdemand))    # 합이 N 되도록 반올림 보정
  히스테리시스: 직전 배정 대비 변화가 임계 미만이면 유지 (thrash 방지)
```

- **재배정 비용 0**: tier 라벨은 *신규 dispatch*만 게이트. 진행 중 요청은 그대로 완료 → 마이그레이션 불필요.
- 최소 1대 보장(없으면 해당 tier 영구 기아).
- 우리 mixA에 적용 시 수렴 예상: **swe 2 / chat 1 / dr 1** (정적 선택지와 같은 값이지만 *유도된* 값이고, mix B/C에서 자동으로 달라짐).

### P4. 실험
EXP-14 mixA 동일 프로토콜(rate-sweep 480~3420 rpm, 본 5분, warmup 60s, 조건별 콜드 재시작).

| arm | 라우팅 | 엔진 |
|---|---|---|
| baseline | round-robin (현재 neutral) | FIFO |
| slo-binpack | Llumnix 기존 `slo` 정책 (최고부하) | FIFO |
| **polyserve** | tier 파티션 + 최저부하 | FIFO |

→ EXP-17~20의 5-arm(FIFO/EDF/SJF/SRPF/QoServe)과 **동일 SLO 기준**으로 판정하므로 직접 비교 가능.

---

## 4. 범위 밖 (v2 후보)
- **DSLO 누적 데드라인** (`i번째 토큰 < TTFT + i·TPOT`) — 예측오차 흡수. v1은 판정만 논문대로
  가져오고 **채점은 EXP-17과 동일한 순간 기준 유지** → 5-arm과 직접 비교 가능하게 하려는 의도.
  주의: 논문의 평가 지표가 DSLO attainment이므로, 순간 기준으로 채점하면 PolyServe가 논문보다
  낮게 나온다. 결과 해석 시 반드시 명시할 것.
- **pull 기반 라우터 큐 소유** — 게이트웨이 재설계 필요. v1은 hold-and-retry로 근사.
- **autoscaling** — 지시에 따라 제외.
- **PD-disaggregation** — 논문 이득의 상당 부분이 여기서 나오나(1.23× vs co-location 1.18×) 우리는 co-location. 상한이 낮음을 감안.

## 5. 알려진 리스크
1. **P0 프로파일 데이터 품질** — EXP-16은 mixA 부하 중 관측치라 (batch, tokens/req) 격자가 성길 수 있음. 보간 예측기의 외삽 구간 주의.
2. **swe tier 구조적 포화** — 설계상 예상되나, swe attainment가 0에 수렴하면 fleet 평균이 낮아 보일 수 있음. 클래스별로 반드시 분리 보고.
3. **논문은 시뮬레이션 전용 평가** — 실엔진의 iteration 변동이 admission 예측을 얼마나 흔드는지 미검증. 이걸 실측하는 것 자체가 우리 기여.
4. **필터가 request를 못 받는 구조** — schedulingCtx 주입으로 우회하나, 향후 Llumnix 업스트림 병합 시 마찰 가능.
