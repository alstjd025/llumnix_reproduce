# KV-cache 유량(수조) 분석 — capacity control의 key로서의 KV in/out flow

**Date**: 2026-07-09 · **Data**: EXP-05 (chat, 70B, 5→100 req/s, warmup) ·
**Scripts**: `analysis_scripts/request_level/plot_kv_flows.py`, `plot_kv_tank.py` ·
**Figures**: `results/aggregate_analysis/exp05_kvflow/`

## 1. 관점 (가설)

> KV cache의 **들어오는 양(유입)과 나가는 양(유출)이 서버 capacity control의
> key**가 될 수 있다. 유입 = prefill로 KV가 만들어지는 것(연산 또는 하위 티어에서
> 로드), 유출 = in-flight 요청이 잡고 있는(locked) KV를 제외한 KV가 free/evict/
> swap-out 되는 것. HBM의 KV 공간은 **이 유량 차이로 채워지는 수조**다.

수위(usage%) 기반 제어는 후행적이다 — 수위가 찼을 때는 이미 preemption이
시작된다. 유량 관점의 기대: **유입-유출 불균형은 포화를 사전에 예보**할 수 있고,
그 리드타임이 admission control의 판단 여유가 된다.

## 2. 데이터와 방법

### 원시 카운터 (엔진별, 1s 간격 스크레이프)
| 카운터 | 의미 | 역할 |
|---|---|---|
| `vllm:kv_cache_usage_perc` | 활성(참조 중) KV 블록 비율 | **수위계** |
| `vllm:generation_tokens_total` | 생성 토큰 누적 | decode가 새로 쓴 KV |
| `vllm:prefix_cache_queries_total` | prefill 시 캐시 조회 토큰 누적 | prefill 유입 총량 |
| `vllm:prefix_cache_hits_total` | 그중 캐시 재사용 토큰 누적 | 재활용분 |
| `vllm:num_preemptions_total` | preemption 누적 | 압박(강제 배수) 신호 |

이 빌드는 **evict/swap 카운터를 노출하지 않는다** (V1은 swap 자체가 없고 CPU
offload 비활성). 유출은 직접 측정 불가 → **보존식으로 유도**한다.

### 유도식 (1초 차분)
```
inflow_new   = Δ(queries − hits) + Δ(generation)   # 새로 계산해 쓴 KV (prefill miss + decode append)
inflow_reuse = Δ(hits)                              # prefix cache에서 재활성화 (연산 0, 수위만 ↑)
outflow      = (inflow_new + inflow_reuse) − ΔU/Δt  # 보존식 잔차 = 블록 해제(완료/preempt)
   where U = usage% × pool_tokens (fleet Σ; 70B TP2 pool = 36,570 blk × 16 = 585k tok/engine)
```

### 그림
- `kvflow_rpm_*.png`: (A) 수위 시계열 + preemption 틱, (B) 3개 유량 시계열
- `kvtank_pred_rpm_*.png` (**G1**): 수위 / 순유입 / **예측 time-to-full
  (= 남은공간 ÷ 순유입) vs 실제 잔여시간** — 예보 성능 검증
- `kvtank_drain_curve.png` (**G2**): 유출율 vs 수위 산점 (전 조건, 핀 이전 틱만)
  — 수조의 배수 능력 곡선

## 3. 결과

### G1 — 유량은 포화의 선행지표다 (검증됨)
80 req/s: 순유입이 t≈65s부터 +40k tok/s로 벌어지고, 실제 핀(수위 99%)은
**t=129s**. 예측 time-to-full은 **t≈70s부터 실제 잔여시간 곡선에 수렴해 끝까지
추적** → **약 60초의 예보 리드타임**. "예측 TTF < 임계(예: 30-60s)면 유입 차단"
같은 단순 admission 규칙이 이 데이터에서 그대로 성립. (70/90/100 req/s 조건도
동일 패턴; `kvtank_pred_rpm_{4200,4800,5400,6000}.png`)

### G2 — 배수 곡선과 자연 setpoint
- 수위 ~80%까지: 배수율이 수위에 대략 비례해 증가 (수위↑ = 동시 in-flight↑ =
  초당 완료·해제↑). 수위를 올릴수록 처리량 이득이 있는 구간.
- **90%+ 구간은 churn 지배** (preempt로 뱉고 재유입하는 소용돌이; 점들이
  500-1,700k로 폭주) — 지속가능한 배수가 아님.
- → **자연 admission setpoint ≈ 수위 80-85%**: 그 이하로 유지하면 배수 이득을
  다 얻고 churn/preemption 영역을 피한다.

### 유량 크기 감각 (50 req/s, 안정 조건)
수위 ~950k/2,340k에서 평형, 유입 ≈ 유출 ≈ 60-100k tok/s, preemption 0.

## 4. 모델 정교화 (설계에 반영할 것)

1. **제어 키 = 유량 단독이 아니라 "수위 + 순유량 궤적"**. 정상상태에선 어느
   수위에서든 유입=유출이므로 유량만으론 30%와 95%를 구분 못 한다. 댐 방류
   제어처럼 수위 트렌드 외삽으로 판단해야 한다.
2. **수조는 2층이다**: locked(활성 요청의 KV — 완료 전엔 못 뺌, 억지로 빼면
   preemption=recompute) / idle-cached(prefix cache 블록 — 즉시 회수 가능하지만
   evict하면 **미래 유입이 증가**하는 비용: hit-rate ↓ → prefill 재계산 ↑).
   넘침의 실체는 locked 층만으로 바닥까지 차는 것.
3. **admission 판정의 자연스러운 정식화 = KV·초 (token-seconds)**: 요청 하나를
   admit하는 것은 (prompt KV + 예상 출력 KV) × 예상 체류시간 만큼의 수조 용적을
   커밋하는 것. "커밋된 KV·초가 예측 배수 여력 안에 드는가"가 판정식.

## 5. 한계 (정직하게)

- **포화 이후 유량 카운터는 오염**: KV가 꽉 차면 스케줄러가 대기 요청을 매 스텝
  재스케줄 시도하며 prefix 캐시를 반복 조회 → queries/hits가 실제 연산의 수백
  배로 부풂 (관측: 30-80M tok/s — 물리적으로 불가능한 수치 = thrash 검출기로만
  유효). G1 예측/G2 곡선은 핀 이전 틱만 사용.
- **유출은 잔차 추정**: 완료-해제와 preempt-해제를 구분 못 하고, idle-cached
  층의 진짜 eviction(캐시 데이터 파괴)은 관측 불가. 정밀화하려면 vLLM에 카운터
  패치 필요 (evicted_blocks_total, cached_free_blocks 게이지 등).
- 공유 prefix 블록은 refcount로 해제되므로 토큰 단위 수지는 근사다.

## 6. 후속 후보

- **G3** 유입-유출 위상도 (x=유입, y=유출, 색=수위; 대각선=균형) — 조건별 작동점
- **G4** KV·초 수지 (조건별 수요 = Σ admit된 요청의 KV×체류 / 공급 = pool×시간)
- **G5** 수위 vs prefix hit-rate 오버레이 — idle 층 침식의 2차 비용 정량화
- EXP-06(SWE 체인, tool-delay로 KV가 idle-locked 상태로 머무는 워크로드)에 동일
  분석 적용 — locked 층 동학이 chat보다 훨씬 두드러질 것으로 예상.
- vLLM 카운터 패치로 유출 분해 (완료/preempt/evict).
