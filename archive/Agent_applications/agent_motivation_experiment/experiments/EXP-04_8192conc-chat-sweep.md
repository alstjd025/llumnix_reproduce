# EXP-04 — chat rate sweep @ client concurrency 8192 (70B)

**Status**: DONE (2026-07-08) · **Data**: `results/exp04_final_8192conc/`
(canonical merged set; superseded 2048-conc runs in
`results/superseded_exp04_2048conc/`) · **Analysis**:
`results/aggregate_analysis/exp04_slo/`

## 왜

EXP-02/03에서 60 req/s 이상이 클라이언트 동시성 한도(2048)에 눌려 있었음을
발견(`--load-threads` sentinel 버그: 명시한 1024가 MP 모드에서 256으로 리셋
→ 8×256=2048). 버그 수정 후 8 procs × 1024 threads = **8192** 동시성으로
5→120 req/s를 다시 측정.

## 설정

Meta-Llama-**3**-70B-Instruct, 4×TP2 util 0.9, `sharegpt_request_level_poisson`,
rate-sweep 8조건 (5/10/20/40/60/80/100/120 req/s), 5 min/조건, cold restart,
**warmup 없음**(그 결과로 EXP-05가 생김).

## 결과 (full-run attain, TTFT≤5s & meanTBT≤50ms)

| req/s | 5 | 10 | 20 | 40 | 60 | 80 | 100 | 120 |
|---|---|---|---|---|---|---|---|---|
| attain | 100 | 95.7 | 100 | 91.9 | 76.9 | **3.0** | 0.8 | 0.6 |

- 60→80 req/s 사이 **절벽** (76.9→3.0%) — 무제어 chat의 congestion collapse
  첫 정량화. 이 절벽이 이후 admission 실험(EXP-07/08)의 무대가 됨.
- 저율 조건의 산발적 위반(10 req/s 95.7 등)은 **cold-start thundering herd**:
  빈 fleet + prefill-only dispatch 신호로 초기 러시가 한 엔진에 몰림
  (피해 엔진 TTFT 49s). staggered-readiness 가설은 데이터로 기각(전 엔진
  t=0 정상, 희생 엔진 ≠ 첫 ready 엔진) → 해결책은 **warmup ramp** (EXP-05).
- 여기서 SLO 분석 파이프라인 확정: 도착 기준 TTFT, 60s sliding window,
  에러/타임아웃/런종료컷 제외·별도 보고 (`slo_sliding_window.py`).
