# EXP-08 — θ=0.6 고정 전체 rate sweep (EXP-05 시나리오 + KV admission)

**Status**: DONE (11/11, 2026-07-14) · **Follows**: EXP-07 ·
**Data**: `results/*exp08_kvadm_th0600_rpm_{300..6000}` ·
**Analysis**: `results/aggregate_analysis/exp08/` (`plot_exp08_overlay.py`,
slo_sliding_window, plot_slo_vs_throughput, percond/ 엔진 상태 플롯)

## 목적

EXP-07에서 찾은 θ\*=0.6을 고정하고 exp05와 동일한 11-rate sweep(5→100 req/s,
chat, 5min/조건, warmup 20req/s×60s, 조건별 cold restart, Llama-3.1-70B)을
돌려 **"admission ON일 때 SLO-vs-rate 곡선 전체"**를 무제어 대비로 얻는다.

## 결과 (steady window; offered = reject를 위반으로 카운트)

| rate | admitted% | offered% | reject% | KVμ | queμ | tok/s |
|---|---|---|---|---|---|---|
| 5–40 | 100 | 100 | 0 | 1.8–24.5% | ≤6 | 2.4k–15.8k |
| 50 | 99.9 | 99.9 | 0 | 44.1% | 7 | 18.5k |
| 60 | 99.5 | 95.0 | 4.5 | 54.9% | 17 | 20.8k |
| 70 | 99.2 | 84.7 | 14.6 | 56.4% | 22 | 22.1k |
| 80 | 98.3 | 76.0 | 22.6 | 57.0% | 30 | 22.8k |
| 90 | 96.8 | 67.6 | 30.1 | 57.5% | 35 | 23.3k |
| 100 | 96.2 | 59.6 | 38.0 | 58.1% | 52 | 23.2k |

### 관찰

1. **용량 클램프로 동작**: 70–100 req/s의 good-throughput(offered%×rate)이
   59.3/60.8/60.8/59.6 ≈ **상수 ~60 req/s**. offered 곡선이 이론 상한
   `min(1, 60/rate)`에 전 구간 밀착 (`exp08_overlay_vs_rate.png`의 점선).
   무제어 baseline(EXP-07 θ=off: 60→51%, 90→1.5%)과 극명한 대비.
2. **feasible 구간 무손실**: ≤50 req/s에서 steady 거절 0, attainment 99.9–100%
   — θ=0.6은 경부하 false-positive가 없음 (θ=0.3의 8% 거절과 대조).
   full-run 카운트에는 저율에도 거절이 보이는데(5req/s에 55건) 전부 t<60s
   cold-start(CMS 미수신 MaxFloat32 + fill-up) 구간 — warmup이 흡수.
3. **과부하 정상상태가 안정**: KV 수위가 55–58%에 고정(θ 바로 아래 setpoint),
   엔진 waiting 큐 17–52로 억제(무제어에선 수천), preemption 없이 tok/s
   22–23k 포화 유지. admitted attainment는 96–99.5%로 완만히 침식
   (100 req/s에서 3.8%p 위반 — staleness 0.5–1s 사이 버스트 누수).
4. **재현성**: EXP-07 θ=0.6 점(60: 95.1 / 90: 65.7)이 EXP-08 곡선(95.0 / 67.6)
   위에 그대로 얹힘 — cross-run 편차 ≤2%p.

### 해석 / 남는 것

정상상태 open-loop chat에서는 순간-점유율 임계 하나로 사실상 이상적 용량
클램프가 구현된다(EXP-07 §4의 결론 재확인·확장). 정적 θ의 남은 결함은
(a) θ\*=0.6 자체가 오프라인 sweep 산물이라는 것, (b) 과부하 admitted 침식
(96.2%에서 멈추지 않고 rate에 따라 계속 내려갈 것), (c) 이 설정이 신호에
가장 유리한 조건이라는 것. 다음 단계는 신호가 구조적으로 불리해지는 설정 —
**SWE tool-delay(체인·idle-locked KV) 재실험** 또는 **burst 도착** — 에서
같은 θ=0.6이 유지되는지 보는 것.
