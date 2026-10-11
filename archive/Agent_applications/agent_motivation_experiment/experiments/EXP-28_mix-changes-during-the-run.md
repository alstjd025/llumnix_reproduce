# EXP-28 — 믹스가 조건 안에서 바뀔 때 정적 파티션이 무엇을 지불하는가

**상태**: 설계 완료, trace·설정 생성 완료. **EXP-27 결과를 본 뒤 실행**
**트레이스**: `traces/dynamic/canonical/dyn08_mixcycle_flat.csv`
**설정**: `workload_configs/mix_short_dyn08_cycle.json`

## 1. EXP-27이 답할 수 없는 것

EXP-27은 세 믹스를 **각각 별개의 조건**으로 측정한다. 그것이 답하는 질문은
"믹스가 다르면 PolyServe가 계산하는 파티션이 다른가"이고, 오프라인 재현으로는
답이 나와 있다(m2만 (1,2,1)). 답할 수 없는 것은 **전환의 비용**이다. 한 조건 안에
전환이 없기 때문이다.

전환 비용이 실재하는 이유는 코드에 적혀 있다.

```go
repartitionPeriod      = 10 * time.Second
repartitionStableRounds = 2   // "a server that changes tier drains its old
                              //  tier's queue first, so churn is not free"
```

- 반응까지 약 **20초**.
- 재배정 자체는 공짜지만, **이 실험들은 migration이 꺼져 있다**(`LLUMNIX_ENABLE_MIGRATION=0`).
  따라서 tier가 바뀐 서버는 **옛 tier의 in-flight 요청을 끝까지 들고 있다.** swe 요청은
  수십 초를 도므로, 재배정 직후 그 서버는 두 클래스를 동시에 서빙하는 상태가 된다.
  그 동안 새 tier의 예산은 옛 tier의 배치 비용을 함께 지불한다.

FluidServe에는 서버 단위 배정이 없으므로 이 상태 자체가 없다. **원안 §1.3 D3(정적
파티션 없는 soft binning)이 이득을 낼 수 있는 유일한 구조적 자리가 여기다.**

## 2. 설계 — 도착률은 고정, 믹스만 움직인다

```
arrival rate   40 req/s 고정 (2,400 rpm)
mix schedule   m1 -> m2 -> m3 -> m1, 세그먼트 2분
duration       8분 + warmup 60초 (warmup은 m1, 분석에서 제외)
arm            polyserve / fluidserve, 같은 세션, 반복이 바깥 루프
```

생성 결과(`dyn08_mixcycle_flat.plan.json`):

| 세그먼트 | 도착 수 | 실현 요청 비율 chat/dr/swe |
|---|---|---|
| warmup m1 | 2,422 | 0.769 / 0.154 / 0.077 |
| s0 m1 | 4,683 | 0.769 / 0.154 / 0.077 |
| s1 m2 | 4,925 | 0.930 / 0.047 / 0.023 |
| s2 m3 | 4,757 | 0.667 / 0.111 / 0.222 |
| s3 m1 | 4,859 | 0.769 / 0.154 / 0.077 |

**도착률을 고정하는 이유**: Azure 형태의 rate와 믹스를 동시에 움직이면 관측된 차이가
둘 중 무엇 때문인지 귀속되지 않는다. 1시간 동적 실행은 둘 다 움직이는 최종 형태이고,
그 결과를 해석하려면 **믹스만 움직였을 때의 답이 먼저 있어야 한다.**

**세그먼트를 2분으로 두는 이유**: PolyServe가 20초 안에 따라잡으므로, 세그먼트가 훨씬
길면 잘못된 구간의 비중이 작아져 효과가 묻힌다. 반대로 20초에 가깝게 짧으면 PolyServe가
한 번도 안정되지 못해 "따라올 수 없는 속도로 흔들었다"는 비판을 받는다. 2분은
**PolyServe가 세그먼트의 대부분을 올바른 배정으로 보내고 경계에서만 틀리는** 길이다.
지속시간 감도는 4분 세그먼트로 한 번 더 확인한다.

## 3. 무엇을 관측하는가

| 계열 | 무엇을 말하나 |
|---|---|
| `scheduler_polyserve_tier_servers` | 배정이 **언제 몇 번** 움직였나. 0회면 이 실험은 성립하지 않고, 그때는 세그먼트 간 수요 차이가 largest-remainder 경계를 못 넘었다는 뜻이다 |
| 시간축 attainment (30초 창) | 배정이 움직인 시점 **직후**에 PolyServe가 떨어지는가. 떨어지는 폭과 회복 시간이 곧 전환 비용이다 |
| 엔진별 prefix hit rate | 재배정된 서버가 두 클래스를 동시에 들고 있는 구간에서 그 엔진의 hit rate가 떨어져야 한다. **엔진이 직접 보고하는 값**이라 스케줄러의 자기 보고보다 강한 증거다 |
| preemption 횟수 | 전환 구간에 몰리는지 |
| FluidServe의 `prefill_duty` | 인스턴스별로 갈라지는지, 그리고 믹스가 바뀔 때 따라 움직이는지 |

## 4. 실행 전에 적어두는 판정 규칙

| 관측 | 결론 |
|---|---|
| 배정이 세그먼트마다 움직이고, 그 직후 PolyServe의 attainment가 떨어졌다가 회복 | **전환 비용이 측정됐다.** FluidServe가 그 구간에서 떨어지지 않으면 그것이 D3의 근거다 |
| 배정은 움직이는데 PolyServe가 안 떨어짐 | 전환 비용이 무시 가능하다는 뜻. 정적 파티션의 약점은 전환이 아니라 다른 데 있다는 결과이고, 그것도 기록할 값어치가 있다 |
| 배정이 안 움직임 | 세그먼트 간 수요 차이가 부족하다. m2/m3의 간격을 벌리거나 세그먼트를 길게 해 EWMA(α=0.5)가 충분히 따라가게 한다 |
| 둘 다 전환 구간에서 떨어짐 | 부하 자체가 전환과 무관하게 과하다. rate를 낮춰 재측정 |

## 5. 관련

- 선행: [EXP-27](EXP-27_short-swe-mix-sweep.md) — 세 믹스를 정적 조건으로 분리
- 최종 형태: 1시간 동적 trace(rate와 믹스가 동시에 움직임).
  `traces/dynamic/build_dynamic_mix_trace.py`에 m1/m2/m3를 추가해 두었으므로
  `--mix-schedule m1,m3,m2,m1`로 바로 생성된다
- 근거가 되는 코드: `pkg/scheduler/policy/polyserve_repartition.go`
  (`repartitionPeriod`, `repartitionStableRounds`와 그 주석)
