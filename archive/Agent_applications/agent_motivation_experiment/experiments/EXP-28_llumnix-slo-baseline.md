# EXP-28 — Llumnix 자체 SLO 정책을 baseline으로

**상태**: 공정 설정 sweep 실행 중 (2026-07-29 00:38 KST 시작, ~01:30 종료)
**세션**: `exp27p5r1_slo_m1f`

## 1. 왜 이 baseline인가

지금까지 대조군이 **PolyServe(논문 기법을 우리가 이식)** 하나뿐이었다. 그래서
FluidServe의 이득이 어디서 오는지가 뭉쳐 있다.

| arm | 출처 | 클래스 구분 | 용량 판단 | 거절 |
|---|---|---|---|---|
| **Llumnix SLO** | **Llumnix 원본** | 요청별 예산 | 순간 예측 (프로파일) | 없음 (게이트웨이 타임아웃) |
| PolyServe | 논문 이식 | tier→서버 정적 배정 | 점유율 임계 | 있음 (실측 0.0%) |
| FluidServe | 우리 기여 | 클래스별 예산 | **지평 위 용량 회계** | 있음 |

Llumnix SLO만 **원본 시스템에 이미 있던 것**이라 "기존 시스템 대비"의 가장 직접적인
대조군이다. 그리고 SLO 인지 / 클래스 구분 / 지평 회계 / 거절 중 **무엇이 이득의
원천인지**를 가른다.

## 2. 이식한 것 두 가지 (명시 필요)

| | 이유 |
|---|---|
| `InferTypeNeutral` 분기 | 원본은 prefill/decode 분리 배치를 전제해 그 둘만 정의한다. `baseDispatchPolicy`가 포인터 맵이라 co-located fleet에서는 nil 역참조로 죽는다. **불가피** |
| 임계를 요청별 예산으로 | 원본은 `--ttft-slo`/`--tpot-slo` 전역 상수 하나. 혼합 fleet에서는 가장 빡빡한 클래스에 맞춰야 하므로 dr·swe가 chat의 50 ms로 판정된다. **우리에게 불리한 방향의 강화** |

예측기(`PredictedTtft`/`PredictedTpot`)와 필터 구조는 **원본 그대로**다.

## 3. ⚠ 첫 측정은 불공정했고 폐기한다

`tbt_ms = 25`는 swe가 요구하는 속도가 아니라 **E2E 30초를 (ttft, tbt)로 쪼갠
부산물**이다. FluidServe는 `25:e2e:30000`으로 예산 자체를 받아 **57.7 ms/token**
(30,000 ÷ 측정 평균 출력 520.2)로 판정하는데, Llumnix SLO는 E2E 모드가 없어 25를
하드 필터로 쓴다 — **2.3배 빡빡한 기준**이다.

그 결과 swe를 98% 거절했고, 그 용량으로 chat/dr을 지켜 60·80 req/s에서 이겼다.
**비교가 성립하지 않는다.**

## 4. 공정 설정 `m1f`

```
swe (ttft 2,500, tbt 52)      2,500 + 520.2 × 52 = 29,550 ≤ 30,000  ✓
chat·deepresearch 변경 없음
채점 변경 없음 — 분석은 swe를 무조건 E2E ≤ 30 s로 잰다
```

52는 30초 안에 들어가면서 FluidServe의 57.7에 가장 가까운 값이다.
`workload_configs/mix_short_m1_slofair.json`, 러너 믹스 키 `m1f`.

`tbt_ms`가 PolyServe의 tier key이기도 하므로 **이 파일은 Llumnix SLO 전용**이다.

## 5. 60 req/s 선행 결과 — 사전 등록한 세 경우 중 ①

| | swe 거절 | req-adm | req-off | eq-adm | eq-off | goodput |
|---|---|---|---|---|---|---|
| Llumnix SLO (25 ms) | **98.3%** | 99.9 | 92.3 | 100.0 | 67.2 | 21,973 |
| Llumnix SLO (**52 ms**) | **0.2%** | 93.2 | 88.0 | 82.2 | **79.9** | 20,495 |
| FluidServe | — | **98.9** | **89.7** | **97.0** | 78.6 | **21,468** |

**우위는 starvation 때문이었다.** swe를 받게 하자 세 지표가 떨어지고 FluidServe가
다시 앞선다. 받아들인 swe의 **절반만 30초를 지키고**(E2E ≤ 30 s: 49.5%, p50 29.9 s),
그 부담이 chat으로 번진다(chat 거절 0 → 7.2%).

정직하게: **eq-off는 79.9 대 78.6으로 동률**이다. "SLO를 알고 프로파일로 예측한다"만으로
대부분이 설명되고, 우리 추가 기제의 몫은 req-adm +5.7, eq-adm +14.8점이다.

## 6. 판정 규칙 (실행 전 등록, 60 req/s에서 ①로 확정)

| 관측 | 결론 |
|---|---|
| **① swe 거절↓ + 성능이 FluidServe 아래로** | **우위는 starvation. 비교가 이제 공정** ← 60 req/s에서 확인 |
| ② swe 거절↓인데 성능 그대로 | starvation이 원인이 아니다. FluidServe를 다시 봐야 한다 |
| ③ swe 거절이 여전히 높음 | TTFT 필터에 걸린 것. "정적 쌍은 E2E를 표현 못 한다"의 직접 증거 |

## 7. 남은 주의

**FluidServe도 지금 자기 성능을 못 내고 있다.** 80 req/s에서 예측이 실측보다 8 ms
높아 결정의 84%가 보유가 되고 dr이 예산의 85%를 대기로 쓴다
(`ms_dev/notes/fluidserve-implementation.md` §24). **둘 다 나쁜 상태에서 비교하는
것이므로, 이 표는 그 수정 뒤에 다시 만들어야 한다.**

## 8. 관련

- 선행: [EXP-27](EXP-27_short-swe-mix-sweep.md)
- 근거: `ms_dev/notes/fluidserve-implementation.md` §23(baseline), §24(v23 반증)
- v0.1 명세: `ms_dev/notes/fluidserve-v0.1.md`
