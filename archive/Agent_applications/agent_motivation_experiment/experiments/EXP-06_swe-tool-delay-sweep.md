# EXP-06 — SWE tool-delay chain sweep (무제어 agent baseline)

**Status**: DONE (2026-07-09) · **Data**: `results/*exp06_swe_sweep_rpm_{15..300}`
· **Analysis**: `results/aggregate_analysis/exp06/{slo,ratesweep,engine_latency,kvflow,goodput}/`

## 왜

dependency 체인(전부 완료해야 goodput)이 있는 agent 워크로드에서 무제어
서빙의 request/job 양 레벨 붕괴를 측정 — EXP-09(admission)의 baseline.

## 설정

Llama-**3.1**-70B-Instruct, max-model-len 40960(체인 후반 30k 입력 수용),
`swe_bench_coding_tool_delay`: 체인 4–12콜(평균 8), SYSTEM_PROMPT 14.5k tok,
콜 입력 15k→30k 성장, tool delay Beta(평균 3s) — **부분 폐루프**(전 콜 완료
+delay 후 다음 콜 발행). rate {0.25..5.0} jobs/s 11조건 × 12 min,
warmup 0.25 jobs/s × 60s, cold restart.

## 결과

**Request-level (steady)**: 0.75 j/s 96.9% → 1.0에서 66.7% → 5.0에서 22.5%.
붕괴는 2단계 — 1.0 j/s에서 **순수 TBT 위반**(KV 64.5%)이 먼저, 1.25+부터
엔진 큐(62→957)가 쌓이며 **TTFT+TBT 동시 위반이 지배**, 3.5 j/s부터 에러
대량(1.6–2.9k).

**Job-level (제출창 60–360s)**: 완주 goodput이 ~0.8 jobs/s에서 천장,
과부하에서 0.43까지 **역행**(congestion collapse); 완주+SLO goodput은
0.75 j/s 피크(0.63/s) 후 5 j/s에서 0.05/s.

**KV/캐시**: 1.0 j/s부터 pin 발생(t=603s→235s로 단축), prefix-hit 77–90%.
hot 수위가 오를수록 hit-ratio 침식(87.6→74.0%) — idle 층 파괴가 prefill
재계산 수요를 ~2배로 만드는 양의 되먹임 (상세:
[ANALYSIS_kv-tank-flow.md](ANALYSIS_kv-tank-flow.md),
[ANALYSIS_why-not-full-kv.md](ANALYSIS_why-not-full-kv.md) E2/E3).

**"서버 throughput은 멀쩡해 보임"**: 토큰 처리량은 2.0 j/s에서 3.3k tok/s로
최고 — job goodput이 무너지는 동안. 이 벤치의 motivation 논지가 Llumnix
스택에서 재현됨.
