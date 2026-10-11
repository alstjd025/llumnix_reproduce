# EXP-07 — KV-occupancy thresholding admission (motivation: 정적 수위 임계의 한계)

**Status**: DONE (12/12 conditions, 2026-07-14; results §4) ·
**Date**: 2026-07-14 ·
**Branches**: llumnix `feat/kv-admission-threshold`, Agent_applications `feat/exp07-kv-admission`

## 1. 왜 / 가설

KV-tank 유량 분석(ANALYSIS_kv-tank-flow.md)의 대조군 실험. **"현재 KV 점유량(hot)
임계 θ 기반 admission은 suboptimal하다"**를 exp05 시나리오(chat, open-loop)에서
정량화한다. 예상 실패 모드:

1. exp05 실측: 100% attainment 구간(≤50 req/s)의 hot 수위 ≈ **36%**, 60 req/s에서
   66%, 70+에서 87%(pin). SLO를 지키는 θ는 수위를 ~40% 아래에 가둠 → 큰 utilization
   gap. (주의: "빈 HBM 낭비"가 아니라 — 나머지는 idle prefix cache가 hit ~85%를
   만드는 데 쓰임 — **안전하게 더 받을 수 있는 부하를 못 받는 gap**으로 서술)
2. 50→60→70 req/s에서 수위가 36→66→87%로 점프 — θ를 앉힐 중간 수위가 없는 절벽
   → 정적 θ의 비강건성 (θ\*가 rate마다 이동할 것).
3. exp05의 SLO 붕괴는 TBT-위반 지배 — KV 수위는 decode 밀도를 직접 제어하지 못함
   → θ가 완벽해도 TBT SLO는 못 지킴 ("잘못된 변수 제어").

## 2. 구현 (검증 완료 항목)

### Scheduler (llumnix `feat/kv-admission-threshold`, commit 3d55ea7)
- 새 scheduling metric **`kv_cache_usage_ratio`** = CMS `NumUsedGpuTokens /
  NumTotalGpuTokens` — **hot 점유율 [0,1]**, 엔진의 `vllm:kv_cache_usage_perc`와
  동일 축. (기존 `kv_cache_usage_ratio_projected`는 대기 토큰 포함이라 순수
  점유량이 아님 — 가설이 겨냥하는 신호가 아니어서 새로 추가)
- 새 플래그 **`--admission-kv-usage-threshold θ`** (default 0=off): >0이면
  neutral dispatch에 per-instance 하드 필터 추가(`notSkipWhenFallback: true`).
  4개 인스턴스 전부 hot 수위 ≥ θ → `ErrorNoAvailableEndpoint` → scheduler 429.
  **per-instance 의미론**: 여유 인스턴스가 하나라도 있으면 그쪽으로 배치(기존
  argmin selector 불변 — `all_prefills_tokens_num`, exp05와 동일 dispatch).
- 거절 전달 경로(소스 확인): scheduler 429 → gateway `SchedulerClient.Get`
  재시도 루프(기본 5s) → 클라이언트 **503** `no available inference worker`.
  즉시 거절을 위해 gateway args에 `--wait-scheduling-timeout=0s` 추가
  (`patch-gateway-fastfail.sh`; 이미지 불변, args-only).
- 빌드: 호스트에 Go 1.24 설치, cgo sdk 의존 3건 분리(ToolParser any화, lrs
  TokenEncoder 훅) 후 **CGO_ENABLED=0 정적 바이너리** `bin/scheduler-exp07`.
  이미지 빌드 없이 **hostPath로 stock 이미지에 주입** (`patch-scheduler-kvadm.sh`).

### Client (Agent_applications `feat/exp07-kv-admission`)
- `LlumnixCompletionsLLM`: 503/429 + 거절 시그니처 → `LlumnixRejectedError`
  (재시도/논스트리밍 fallback 차단), `_detect_admission_rejection`이
  `is_rejected=True, rejection_reason=KV_THRESHOLD`로 기록. 유닛 테스트 통과.
- 분석: `slo_sliding_window.py` — rejected를 별도 범주로(에러 제외에 안 섞임),
  `attain_pct_offered = attain/(classified+rejected)` 추가.
  `plot_slo_vs_throughput.py` — offered-attainment 라인 + rejection rate 라인
  (거절 있는 런에서만 표시; 무거절 런은 기존 그림과 동일 — exp04로 회귀 확인).

## 3. 실험 설계

고정: 현 라이브 설정 그대로 — **Llama-3.1-70B-Instruct**, TP2×4, max-model-len
40960, `sharegpt_request_level_poisson`, 8 procs × 1024 threads, warmup 20 req/s
× 60s, **5 min/조건**, 조건별 cold restart, rescheduling args exp05와 동일.

> 주의: exp05는 Llama-**3**-70B(32k)로 돌았음 → exp05 수치는 정성 참조만 하고,
> **θ=0(off) 조건 3개를 실험 내 baseline**으로 사용한다.

조건 12개 (`run_exp07.sh sweep`, ~3h):

| rate | θ (hot KV usage) | 세션명 |
|---|---|---|
| 60 req/s (3600rpm) | 0(off), 0.8, 0.6, 0.45, 0.3 | exp07_kvadm_th{0000,0800,0600,0450,0300}_rpm_3600 |
| 90 req/s (5400rpm) | 0(off), 0.8, 0.6, 0.45, 0.3 | 〃 _rpm_5400 |
| 50 req/s (3000rpm) | 0(off), 0.3 | 〃 _rpm_3000 |

θ 그리드 근거: feasible 상한 수위 36% / 붕괴 시작 66% / pin 87% (exp05 실측)
양쪽에 0.3/0.45/0.6/0.8 배치. (3.1-70B에서 수위가 다소 이동 가능 — off 조건
결과를 보고 그리드 ±0.1 조정 여지)

### 스모크 (sweep 전 필수, `run_exp07.sh smoke`)
1. θ=0.0001, 5 req/s, 2조건 중 1: 첫 요청 진행 중 usage>θ → 이후 대부분 즉시
   503 거절. 확인: client `is_rejected/KV_THRESHOLD`, scheduler 로그 "Metric
   based filter applied … kv_cache_usage_ratio", `scheduler_scheduling_failed_total`
   증가, 거절 지연(fast-fail ≤ ~수십 ms).
2. θ=0.99: 거절 0, 정상 완료 (stock-동등성).
3. 부팅 직후 CMS 미수신(MaxFloat32) 거절 폭주가 warmup에 흡수되는지 관찰.

### 측정/그림
- **goodput_offered** (reject=위반; 대표 지표) vs θ, rate별
- attain_admitted vs θ + rejection rate vs θ
- hot KV 수위 밴드 + throughput vs θ (+ prefix hit-ratio 병기)
- TBT p50/p99 vs θ — "θ가 못 지키는 SLO" 논거
- `instance_cms_kv_cache_usage_ratio_projected`/hot 시계열 — 필터가 본 신호 기록

## 4. 결과 (2026-07-14 sweep 완료; 12조건 전부 정상 수집)

분석: `analysis_scripts/request_level/plot_exp07_theta.py` →
`results/aggregate_analysis/exp07/` (summary CSV + 그림 3장).
정의: steady window [60s, dur−20s], TTFT≤5s & meanTBT≤50ms,
**offered = reject를 위반으로 카운트**, admitted = 수용된 것 중 SLO 충족.

| rate | θ | attain_offered | attain_admitted | reject | KVμ | TBT p50/p99 (ms) |
|---|---|---|---|---|---|---|
| 60 | off | 51.1% | 51.1% | 0% | 74.9% | 49.9 / 70.9 |
| 60 | 0.8 | 89.8% | 91.2% | 1.6% | 68.6% | 45.0 / 57.7 |
| 60 | **0.6** | **95.1%** | 99.5% | 4.4% | 55.2% | 35.7 / 46.9 |
| 60 | 0.45 | 88.6% | 99.8% | 11.2% | 42.5% | 27.9 / 38.1 |
| 60 | 0.3 | 84.5% | 99.9% | 15.4% | 29.2% | 20.7 / 29.3 |
| 90 | off | **1.5%** | 1.5% | 0% | 86.4% | 62.5 / 82.7 |
| 90 | 0.8 | 59.2% | 82.4% | 28.1% | 75.0% | 46.1 / 70.3 |
| 90 | **0.6** | **65.7%** | 96.7% | 32.0% | 57.5% | 34.8 / 64.4 |
| 90 | 0.45 | 65.2% | 98.8% | 33.9% | 43.9% | 27.3 / 52.5 |
| 90 | 0.3 | 59.0% | 99.7% | 40.9% | 30.1% | 19.7 / 34.6 |
| 50 | off | 99.9% | 99.9% | 0% | 43.8% | 30.6 / 40.3 |
| 50 | 0.3 | 91.7% | 99.9% | 8.2% | 28.6% | 21.2 / 27.9 |

### 관찰 (정직하게 — 예상과 다른 부분 포함)

1. **admission 자체의 가치는 압도적으로 확인**: off 조건에서 60 req/s는 51%,
   90 req/s는 **1.5%**로 붕괴. θ=0.6은 이를 95.1% / 65.7%로 회복.
2. **정적 θ가 예상보다 선방**: 90 req/s에서 θ=0.6의 good throughput
   ≈ 0.657×90 ≈ **59 req/s** — 시스템 실효 용량(≈57–59 req/s, 60×θ0.6에서
   0.951×60=57)에 사실상 도달. deep overload에서는 offered-goodput 기준으로
   최적 근처다. θ\*의 rate 의존성도 이 그리드에선 관찰 안 됨(둘 다 0.6).
3. **가설 "KV 수위는 TBT를 제어 못 한다"는 기각**: TBT p50이 θ에 단조 반응
   (60 req/s: 49.9→20.7ms). admission이 decode 밀도를 함께 조이므로 hot-KV
   점유율이 이 워크로드에선 decode 부하의 유효한 프록시.
4. **실측된 정적 θ의 실제 결함 3가지**:
   - (a) **feasible 부하 false-positive**: 50 req/s(무개입 99.9%)에서 θ=0.3이
     멀쩡한 요청 8.2%를 거절 → 91.7%. 보수적 고정 θ는 경부하에서 순손실.
   - (b) **오프라인 튜닝 의존 + 민감도**: θ\*=0.6은 sweep으로야 알 수 있고,
     그리드 한 칸(±0.15~0.2) 어긋나면 5–10%p 손실 (60: 0.8→89.8 / 0.45→88.6).
     운영에선 workload/SLO가 바뀔 때마다 재튜닝 필요.
   - (c) **knee에서 잔여 위반**: 60×θ0.6에서도 4.9%p 위반 잔존 (TBT p99
     46.9ms — SLO 바로 아래; 신호 staleness 0.5–1s 사이로 버스트가 새어
     들어와 순간 과밀 발생). 100% 회복은 어떤 θ에서도 불가.
5. **utilization 관점**: attain-최적 θ=0.6의 hot 수위는 ~55% — SLO를 지키는
   대가로 pool 절반 가까이를 hot으로 못 씀 (단, §1 주의대로 나머지는 idle
   캐시로 유용).
6. exp05 대비 참고: 3.1-70B에서 60 req/s off 수위가 74.9%로 exp05(3-70B)의
   66%보다 높게 이동 — θ 그리드를 실측 위에 얹은 판단이 유효했음.

### flow-기반 후속(가설)의 타깃

이 데이터가 남긴 개선 여지: (a)와 (b) — **부하를 보고 스스로 동작점을 찾는
컨트롤러**(수요·공급 feedforward)라면 경부하 false-positive가 구조적으로 없고
오프라인 θ sweep이 불필요하다; (c) — 도착 시점 수요(prompt 길이)를 아는
feedforward는 staleness 구간의 버스트 누수를 줄일 수 있다. "정적 θ보다 높은
peak"가 아니라 **"튜닝 없이 / 전 부하 구간에서 θ\*-근접"**이 올바른 비교 축.
