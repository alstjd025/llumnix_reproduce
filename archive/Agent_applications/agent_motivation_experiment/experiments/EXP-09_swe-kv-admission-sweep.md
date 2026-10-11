# EXP-09 — SWE tool-delay × KV admission θ ∈ {0.3, 0.4, 0.5, 0.6}

**Status**: DONE (2026-07-15, 4θ × 11 rate = 44조건) ·
**Data**: `results/*exp09_swe_kvadm_th{0300,0400,0500,0600}_rpm_*` ·
**Analysis**: `results/aggregate_analysis/exp09/` (오버레이 2장, ITL CDF 그리드,
θ별 slo/percond) · **Baseline**: EXP-06 (동일 설정, θ=off)

## 왜

chat에서 찾은 θ\*=0.6(EXP-07/08)이 (a) prefill-heavy 체인 워크로드로
**전이되는지**, (b) request-level admission이 job 구조를 모를 때의 비용을
측정. 설정은 EXP-06과 동일 + `--admission-kv-usage-threshold`.

## 결과

**Request-level**: admitted attainment는 **모든 θ, 모든 rate에서 99.4–100%**
(수평선) — 수용분의 품질은 θ와 무관. offered attainment는 θ=0.6 > 0.5 >
0.4 > 0.3 전 구간(1.5 j/s: 89.7 vs off 39.3; 5 j/s: 74.5 vs 22.5).
**meanTBT SLO 기준 θ\* = 0.6 — chat과 동일. 전이 성립.**

**Job-level**: 완주+SLO goodput은 θ=0.6이 과부하에서 무제어의 **2–8배**
(1.5 j/s: 50.0 vs 19.0%). 그러나 **완주율만 보면 무제어보다 소폭 낮음** —
mid-chain 거절이 체인을 죽이기 때문.

**Chain-kill 증폭 (실측)**: θ=0.6/1.5 j/s에서 거절 콜 522개 전부가 해당 job의
마지막 콜(100% 체인 중단), 미발행 후속 콜 ≈ 2,416개 = 거절의 **4.6배**.
job-level 릴리즈는 거절이 미래 도착까지 줄이는 폐루프 — 측정된 rejection
rate는 수요 축소를 ~5.6배 과소표시.

**ITL CDF** (chunk 간 도착, `exp09_itl_cdf_grid.png`): θ에 따라 분포 전체가
단조 좌이동. 1.5–5 j/s에서 p95 ITL: off 615–670ms / θ0.6 172–199 /
θ0.5 95–159 / θ0.4 45–76 / **θ0.3 39–49ms**.
→ **θ\*는 SLO 정의의 함수**: per-request meanTBT≤50ms면 θ\*=0.6,
**p95 ITL≤50ms면 θ\*=0.3–0.4**. (mean이 per-token 꼬리를 흡수)
모든 θ에 300–700ms 꼬리 계단 잔존 — 15–20k prefill의 chunked-prefill
stall, admission으로 제거 불가(스케줄링 문제).

## 미실행 후속 (설계만 존재)

EXP-10(request-level replay, open-loop): 체인 폐루프 제거 시 θ\* 재검 —
`codingagent_request_level_poisson` + transcript 녹화 필요. 사용자 결정으로
보류 (대화 기록 2026-07-15).
