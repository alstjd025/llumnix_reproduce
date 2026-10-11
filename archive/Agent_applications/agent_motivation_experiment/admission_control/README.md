# Admission Control Study — maximize goodput while preserving throughput

**Branch**: `feat/admission-control` · **Started**: 2026-07-09 · **Status**: design

> Working log for the admission-control (AC) study. Every phase (예상/목적/방법/
> 구현/결과) is recorded here + `design.md` so context survives across sessions.
> Parent experiments: `../experiments/EXP-03_70b-kv-saturation.md` (EXP-03/04),
> EXP-05 (warmup sweep, in progress).

## 목적 (Goal)

Find an admission-control policy that keeps **output-token throughput near the
system ceiling** (~22.4k tok/s on 70B 4×TP2) while keeping **SLO attainment
(goodput) maximal** — instead of the observed no-AC behavior where throughput
stays high past saturation but attainment collapses 77% → 0% (offered 60 → 80
req/s, SLO = TTFT≤5s & meanTBT≤50ms).

## 문제 정의 (from measured data, EXP-04 final)

| offered | out tok/s | SLO attain (steady) | 상태 |
|---|---|---|---|
| 40 | 16.9k | 100% | 여유 |
| 60 | 22.3k | 77%  | 임계 (TBT 표류로 첫 균열) |
| 80 | 22.0k | ~0%  | 과부하: TTFT 큐잉 폭발 (선형 15→100s) |

관측된 실패 모드 (AC가 막아야 할 것):
1. **큐잉형 SLO 붕괴**: λ>μ이면 초과분이 무한 대기 → 모든 요청의 TTFT가 함께
   폭발 (fail-slow). AC 없는 시스템은 "전원이 늦는" 최악의 공평성.
2. **TBT 표류**: 임계 부하에서 배치가 커지며 meanTBT가 SLO(50ms) 바를 서서히
   넘음 — 60 req/s에서 위반의 100%가 TBT-only.
3. **콜드스타트 herd**: (EXP-05의 warmup ramp로 별도 완화 중)

핵심 통찰: **admit한 요청만 SLO 분모에 들어간다**. 초과분을 빠르게 reject하면
(fail-fast) admit된 요청은 SLO를 지키고, 시스템은 용량만큼 처리 → goodput
(attain된 req/s) = min(λ, SLO-용량) 이 이론 최적. AC의 목표는 이 경계를 가능한
한 정확히, 가능한 한 이르게 (큐에 쌓이기 전에) 판정하는 것.

## 후보 정책 (단순한 것부터; 출처)

| # | 정책 | 신호 | 출처/유사 시스템 |
|---|---|---|---|
| P0 | No AC (baseline) | — | 현 상태 |
| P1 | 동시성 상한 (in-flight cap) | 시스템 내 요청 수 N | vLLM router max-concurrency, 고전 큐 제한 |
| P2 | 큐 길이 제한 | gateway pending + engine waiting | Llumnix `max-queue-size`(512, 현재 sleep-재시도라 사실상 무한대기), Dynamo router queue depth |
| P3 | 토큰 rate limit | 최근 admit된 (in+out 예상) tok/s | TPM 스타일 rate limiter |
| P4 | KV-capacity 기반 | fleet KV usage (또는 projected) < θ | Llumnix `kv_cache_usage_ratio_projected`, Halo KV_CAP |
| P5 | TTFT 예측 기반 | 예상 대기 = 앞선 대기 prefill 토큰 / prefill 처리율 → predicted TTFT < 5s | QLM/SLO-aware admission 계열 |
| P6 | TBT 예측 기반 | per-engine running batch < B* (ITL(B*)=50ms, 실측 곡선에서 적합) | Andes/SLO-aware batch 제어 계열 |
| P7 | P5∧P6 결합 (SLO-aware) | 둘 다 통과 시 admit | Halo request-level admission과 동형 |

## 평가 방법 (2단계)

**Stage A — 시뮬레이션** (주 수단, 빠른 정책 비교):
- 실측 캘리브레이션: EXP-04/05의 (input_tokens, output_tokens) 실분포 replay,
  엔진 모델 = {KV pool 585k tok/engine, max_num_seqs 1024, prefill 처리율·
  decode step time = 실측 곡선 적합(ITL vs batch, prefill tok/s)}.
- 각 정책 × 각 offered rate(5..120)에서: attained/s, violated/s, rejected/s,
  out tok/s, reject된 요청의 "낭비 없음" 확인.
- 산출: goodput-vs-rate 곡선 (정책별), throughput 보존율, 최적 파라미터 스윕
  (N, θ, B* 등).
- 한계 명시: 시뮬은 dispatch/herd/prefix-cache 동학을 단순화 — 우승 정책은
  Stage B로 검증.

**Stage B — 라이브 plug-in** (우승 정책 1-2개 검증):
- 구현 지점: **러너 측 admission gate** (외부 router와 동일한 제어 지점).
  `run_experiment.py`의 submit 경로에 `AdmissionPolicy.decide(state) -> admit|reject`
  훅; state는 `llumnix_metrics` collector가 이미 1s 주기로 긁는 라이브 메트릭
  (engine running/waiting/KV, gateway current/pending) 공유. reject는
  metrics.csv의 기존 `is_rejected/rejection_reason` 스키마 사용 (스키마 변경 0).
- 비교: P0 vs 우승 정책, 60/80/100 req/s, warmup ramp 포함, 조건별 콜드재시작.

## 성공 기준

- 과부하(80-100 req/s 제공)에서: **attained req/s ≥ 0.9 × (60 req/s 조건의
  attained)** (즉 goodput이 용량 수준 유지) AND **out tok/s ≥ 0.9 × 천장**.
- 임계(60)에서: attain% ≥ P0의 77% (AC가 임계 부하를 해치지 않아야 함).

## 진행 로그

- 2026-07-09: 폴더/브랜치 생성, 설계 문서 작성. EXP-05(11-rate, warmup 20req/s
  ×60s) 진행 중 — 이 데이터가 시뮬 캘리브레이션 + P0 baseline이 된다.
- (다음) EXP-05 분석 → 시뮬레이터 구현 → 정책 스윕 → 우승 정책 라이브 검증.
