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
| admission test `wait+T_iter<TPOT` | `metricBasedFilter` 변형 — threshold를 요청별 SLO로 |
| profiling table `(batch,KV)→iter` | Llumnix `ITLData{batch_size, tokens_per_request}` **이미 동일 축** |
| 미래 iteration 시뮬레이션 | `predictTtftLatencyByChunkPrefill()` **이미 존재** |

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

### P0. 프로파일링 데이터 생성 — **blocker**
`GetLatencyPredictor()`는 파일 로드 실패 시 `klog.Fatalf` → 데이터 없으면 스케줄러 기동 불가. 레포에 파일 없음.

생성 소스: **EXP-16 `sched_steps.jsonl`** (B200, Llama-3.1-70B TP2 실측)

- `ttft_profiling.json` — `TtftData{results:[{tokens_num, p50, ...}]}`
  ← prefill 스텝의 `(prefill_tokens_step → interval_ms)` 집계
- `tpot_profiling.json` — `ITLData{results:[{batch_size, tokens_per_request, p50, ...}]}`
  ← pure-decode 스텝의 `(n_decode, kv_tokens/n_decode → interval_ms)` 집계

검증: 기존 `--scheduling-policy slo`로 스케줄러 기동 성공 확인.

### P1. per-request SLO 배관
1. `pkg/types/scheduling_request.go` `SchedulingRequest`에 `TtftSloMs`, `TpotSloMs` 추가
2. 게이트웨이가 채움 — QoServe 때 뚫어둔 `priority` 패킹(`slo_ms*1000+tbt_ms`) 재사용
3. `schedulingCtx`(per-instance, 요청마다 새로 만들어짐)에 요청 SLO 필드 추가
4. `DispatchPolicy.schedule(request, view)`에서 각 인스턴스뷰에 주입
   → **필터/셀렉터 인터페이스 변경 불필요** (현재 시그니처는 request를 안 받음)

### P2. PolyServe 정책
1. `consts.SchedulingPolicyPolyserve` 등록, `newDispatchPolicyInternal` 분기 추가
2. `perRequestSloFilter` — 전역 `p.TpotSlo` 대신 `schedulingCtx`의 요청 SLO를 threshold로 (admission test)
3. `tierAffinityFilter` — 요청 tier의 현재 배정 서버만 통과
4. `leastBindingLatencySelector` — 요청의 바인딩 차원에서 predicted latency 최소 인스턴스 선택
5. 대조군은 기존 `sloDecodeApdSelector`(bin-packing) 그대로 → **논문의 "최고부하" 주장 직접 검증 가능**

### P3. 동적 재분할기 (tier → 서버)
논문에 없음(autoscaler 역할). PolyServe 스케일 규칙의 수렴점을 fleet 고정으로 재현.

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
- **DSLO 누적 데드라인** — 예측오차 흡수. v1은 순간 threshold.
- **pull 기반 라우터 큐 소유** — 게이트웨이 재설계 필요. v1은 hold-and-retry로 근사.
- **autoscaling** — 지시에 따라 제외.
- **PD-disaggregation** — 논문 이득의 상당 부분이 여기서 나오나(1.23× vs co-location 1.18×) 우리는 co-location. 상한이 낮음을 감안.

## 5. 알려진 리스크
1. **P0 프로파일 데이터 품질** — EXP-16은 mixA 부하 중 관측치라 (batch, tokens/req) 격자가 성길 수 있음. 보간 예측기의 외삽 구간 주의.
2. **swe tier 구조적 포화** — 설계상 예상되나, swe attainment가 0에 수렴하면 fleet 평균이 낮아 보일 수 있음. 클래스별로 반드시 분리 보고.
3. **논문은 시뮬레이션 전용 평가** — 실엔진의 iteration 변동이 admission 예측을 얼마나 흔드는지 미검증. 이걸 실측하는 것 자체가 우리 기여.
4. **필터가 request를 못 받는 구조** — schedulingCtx 주입으로 우회하나, 향후 Llumnix 업스트림 병합 시 마찰 가능.
