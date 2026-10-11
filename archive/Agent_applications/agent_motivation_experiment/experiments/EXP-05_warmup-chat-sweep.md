# EXP-05 — warmup ramp + chat 11-rate sweep (무제어 chat baseline)

**Status**: DONE (2026-07-08) · **Data**: `results/*exp05_warmup_rpm_{300..6000}`
· **Analysis**: `results/aggregate_analysis/exp05_slo/`, `exp05_kvflow/`

## 왜

EXP-04의 cold-start herd를 **warmup ramp**(20 req/s × 60s)로 제거하고,
5→100 req/s 11개 rate에서 무제어 chat의 SLO 곡선과 KV 동학의 깨끗한
baseline을 얻는다.

## 설정

Meta-Llama-**3**-70B-Instruct(주의: 이후 실험은 3.1-70B), 4×TP2,
`sharegpt_request_level_poisson`, 8 procs × 1024 threads,
rate {5,10,20,30,40,50,60,70,80,90,100} req/s, warmup 20 req/s × 60s,
5 min/조건, cold restart.

## 결과

- **Warmup 검증**: herd 소멸 — steady attain 100%가 50 req/s까지 유지
  (EXP-04 대비 저율 위반 제거), 지속 hot 엔진 없음.
- **SLO 곡선(steady)**: ≤50 req/s 100% → 60에서 82% → 70+에서 붕괴(≤20%).
- **KV 수위(steady 평균)**: 40 req/s ≈ 20%, 50 ≈ 36%, 60 ≈ 66%,
  70–100 ≈ 87–89%(pin) — 50→70 사이 수위가 점프하는 절벽. 이 실측이
  EXP-07 θ 그리드의 근거.
- **KV 유량(수조) 분석의 출생지**: 유입/유출 보존식, G1(time-to-full 예보
  리드 ~60s), G2(배수 곡선, churn 영역) — 상세는
  [ANALYSIS_kv-tank-flow.md](ANALYSIS_kv-tank-flow.md).
- 포화 후 prefix-cache 카운터 thrash(30–80M tok/s 비물리 값) 발견 —
  이후 모든 유량 분석의 필터 규칙이 됨.
