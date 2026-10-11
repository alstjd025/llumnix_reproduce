# 왜 KV cache를 다 안 쓰는 것이 이 SLO에서 최적인가 — 다각도 분석

**Date**: 2026-07-15 · **Data**: exp06(SWE, 무제어) + exp07/08(chat, θ sweep) —
20 s steady 윈도우 663개(chat 285 / SWE 378) ·
**Script**: `analysis_scripts/request_level/analyze_tbt_drivers.py` ·
**Figures**: `results/aggregate_analysis/why_not_full_kv/`

## 질문

EXP-07/08에서 SLO-최적 admission θ\*가 hot KV 수위 ~55–60%에 앉았다.
왜 pool을 다 쓰지 않는 것이 최적인가? 후보 가설: (a) decode batch가 클수록
TBT가 커진다, (b) prefill queuing/chunked-prefill 간섭.

## TL;DR

**KV 수위는 자원 게이지가 아니라 decode step latency의 1차 결정변수다.**
attention은 매 decode step마다 hot KV 전체를 읽으므로 step 시간이 hot KV
총 토큰에 거의 선형이고(측정: `TBT ≈ 10 + 20.6 × KV[Mtok]` ms), TBT SLO
50 ms는 그대로 **KV 예산 ≈ 1.95 Mtok = pool의 83%** (윈도우-중앙값 기준
상한)로 번역된다. 실전 θ\*(0.5–0.6)는 그 아래인데, 꼬리 분산·prefill 간섭·
수위 요동·90%+ 불안정성의 4중 마진 때문이다. 즉 "남는 KV"는 낭비가 아니라
**latency 예산의 물리적 잔여**이고, 그마저 idle prefix cache(hit 85–90%)가
쓰고 있다.

## 증거

### E1 — 축을 바꾸면 두 워크로드가 하나의 곡선으로 붕괴한다 (`tbt_vs_kv_tokens.png`)

TBT p50이 50 ms를 넘는 지점:

| 축 | chat | SWE | 비율 |
|---|---|---|---|
| fleet running (batch 폭) | ~2,480 | ~330 | **7.5×** |
| **hot KV 토큰** | ~2.0 Mtok | ~1.9 Mtok | **~1×** |

KV축 bin별 TBT p50 (chat / SWE): 0–21%: 14.6/12.3 · 21–38%: 20.5/20.7 ·
38–56%: 29.9/28.8 · 56–73%: 35.2/37.1 · 73–90%: 46.0/47.7 · 90%+: 62.2/61.6 ms
— **bin마다 1–2 ms 이내로 일치**. 요청당 KV footprint가 840 vs 5,544 tok으로
6.6× 다른 두 워크로드가 KV축에서만 겹친다는 것이 식별 근거다: decode step
시간을 정하는 것은 "몇 개의 요청"이 아니라 **"step마다 읽는 KV 총량"**
(attention KV-read, memory-bandwidth bound; GEMM의 per-seq 오버헤드는 부차적).

→ 가설 (a)는 절반만 맞다: batch가 아니라 batch가 담고 있는 토큰 총량.
→ 부수 소득: **occupancy-threshold admission(EXP-07/08)이 예상 밖으로 잘
작동한 이유** — 이 신호는 단순 잔량 게이지가 아니라 TBT의 준-인과 변수였다.

### E2 — prefill 간섭은 SWE에서만 유의한 2차 항이다 (`tbt_vs_prefill.png`)

running을 고정한 부분효과 (같은 batch에서 prefill 유입만 다를 때):

| | prefill Q1 | prefill Q4 | corr |
|---|---|---|---|
| SWE (running 250–450) | 45k tok/s → 32.7 ms | 162k tok/s → **52.7 ms** | **+0.62** |
| chat (running 1500–1800) | 99k → 35.8 ms | 176k → 35.0 ms | −0.44 |

SWE는 같은 KV/batch에서 prefill 폭주가 **+20 ms**를 얹는다(20k-토큰 프롬프트가
chunked-prefill 8192 예산으로 step 시간을 잠식). chat은 프롬프트가 짧고 hit가
높아 효과 없음. → 가설 (b)는 **워크로드 조건부로 성립**: SWE의 유효 KV 예산은
E1의 83%에서 prefill 간섭분만큼 더 깎인다 — **SWE θ\*가 chat(0.6)보다 낮아야
할 이유**이고, 지금 돌고 있는 exp09(θ=0.3/0.4/0.5)의 사전 예측이다.

### E3 — 90%+ 영역은 별개의 파멸 메커니즘 (연속 악화가 아니라 상전이)

90%+ bin에서 TBT 62 ms는 시작일 뿐: (i) decode append가 매 step 새 블록을
요구하므로 free≈0이면 preemption-recompute 연쇄(exp05 churn, TTFT 49 s),
(ii) idle 캐시 파괴 → hit 87.6→74% → prefill 재계산 ~2× → 수요 자기증폭
(ANALYSIS_kv-tank-flow.md), (iii) waiting queue 형성 → TTFT 위반 가세.
median TBT가 아직 견딜 수 있는 수위에서도 이 불안정성 때문에 상한이 당겨진다.

### E4 — "안 쓰는 공간"은 실제로는 쓰이고 있다

hot 55%일 때 나머지 45%는 빈 메모리가 아니라 idle prefix cache다(chat hit
~90%, SWE ~85%). eviction은 미래 prefill 수요를 키우므로(E3-ii) 이 층을
hot으로 바꾸는 것은 공짜가 아니다. "full KV utilization"은 이 SLO에서
달성 목표가 아니라 비용이다.

## 왜 실전 θ\*(0.5–0.6)는 예산(83%)보다 낮은가

1. 예산 83%는 **윈도우 p50** 기준 — SLO attainment는 요청별 mean-TBT가 (거의)
   전부 50 ms 아래여야 하므로 꼬리 마진 필요 (exp07 θ=0.8: p50 45 ms인데
   admitted attainment 91%).
2. 수위는 요동한다 (run 내 p10–p90 폭 ±10–15%p) — 평균이 예산 아래라도
   피크가 예산을 침범.
3. SWE는 prefill 간섭이 예산을 추가 잠식 (E2).
4. E3의 상전이 영역에 안전거리.

## 함의 / 후속

- **TBT SLO ⇒ KV 수위 예산**의 선형 번역이 성립하므로, 수조(tank) 모델의
  setpoint는 자의적 튜닝값이 아니라 `(SLO−절편)/기울기`로 계산 가능한 양이다
  — flow 제어의 목표 수위를 원리적으로 정할 수 있다.
- 기울기(20.6 ms/Mtok)와 절편(10 ms)은 HW(HBM BW)·모델 크기의 함수 — 다른
  HW/모델에서 재측정하면 θ\*를 오프라인 sweep 없이 예측할 수 있다는 가설.
- 한계: 관측 상관 기반(식별 근거는 두 워크로드의 collapse 단일성뿐), 동일
  HW·모델·TP 구성, 윈도우 p50 요약. per-step 계측(엔진 iteration 로그)으로
  기울기를 직접 검증하는 것이 다음 정밀화 단계.
