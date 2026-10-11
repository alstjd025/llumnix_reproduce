# class-instance cap + force 분기 제거 — 설계 (EXP-107)

2026-08-27. 구현 완료(바이너리 `092edd73`), 단위 테스트 전체 통과, smoke 진행 중.
정본 실험 파일: `Agent_applications/.../experiments/EXP-107_class-instance-cap.md`.
코드: `pkg/scheduler/policy/fluidserve_instancecap.go`(새 파일, 메커니즘 전체 +
파일 머리 주석에 요약), `fluidserve.go`(결정 흐름 접점), `fluidserve_capacity.go`
(`tierServiceRate`), `fluidserve_registry.go`(`noteArrival` 첫-사시 반환).

## 1. 무엇을 왜

인스턴스의 **gate**(요구 속도)는 돌고 있는 클래스들의 nominal 예산 최솟값이므로,
빡빡한 클래스의 요청 **하나**가 느슨한 인스턴스(큰 배치 = 싼 용량)를 빡빡한
인스턴스로 바꾼다. 이 전환은 배치 한 번(밀리초)인데, 역전환은 그 클래스가 한 체류
시간(chat ~21초) 동안 그 인스턴스에 하나도 안 와야 일어나고 — 라우팅이 계속
보내는 한 시작되지 않는다. 그래서 **cap이 없으면 클래스별 footprint는 현재 수요가
아니라 과거 수요의 최댓값을 따른다** (실측: 25 req/s에서 스크레이프의 72~85%가
네 인스턴스 전부 50 ms — chat의 일감 몫은 ~57%인데 footprint는 100%).

cap은 그 양을 governed variable로 만든다:

```
demandInstances[c] = ⌈ λ_c (offered, 창 평균) ÷ μ_c (그 pace에서 인스턴스 한 대의 완료율) ⌉
```

## 2. 결정 규칙 (세 상태)

클래스 c의 요청이 왔을 때, c가 gate를 정하는 인스턴스 수(gateTierCount)와
상한(instanceLimit) 비교로:

| 상태 | 규칙 |
|---|---|
| count < limit | 제한 없음 — 새 gate를 만들어도 됨 |
| count = limit | 새 gate가 될 배치만 제외 (기존 gate 인스턴스 + 더 빡빡한 gate에 얹히는 것은 허용) |
| count > limit | **c를 가장 많이 든 상위 limit대만 허용.** 나머지는 c가 이미 gate여도 제외 → 새 유입이 끊겨 한 체류 시간 안에 배수, gate 해제 |

세 번째 행이 수축 경로다 — 리뷰에서 "이미 있는 곳 자유 통행" 원안은 cold-start
동안 형성된 4/4 점유를 영원히 못 되돌린다는 것이 산수로 반증되어 이렇게 바뀌었다
(수요가 줄어도 줄어든 트래픽이 네 대에 흩뿌려지는 한 어느 한 대도 21초의 침묵을
얻지 못한다: 11 req/s ÷ 4 = 0.36초마다 한 방울).

후보 **제거**(applyClassPin과 같은 목록 제거)로 구현 — infeasible 표시였다면
FORCE 경로(최소 피해 순으로 아무 후보나 고름)가 cap을 뚫는다.

## 3. 재료의 출처 (전부 재사용)

| 양 | 출처 |
|---|---|
| λ_c, 평균 prompt | `noteArrival` 첫-사시에서만 갱신 (보류 재시도는 500ms마다 재진입하므로 세면 최대 70배 부풀음 — registry가 이미 첫 도착을 구분) |
| 창 | τ_c = 3 × 체류시간, [15s, 120s] clamp. 첫 bucket으로 warm-start (조건마다 재시작하므로 ramp-up이면 매 run 첫 수 분이 무방비) |
| μ_c | `tierServiceRate`: 배포된 mixed-step 모델(prefill 몫 포함)을 닫힌 식으로 뒤집음. **디코드 전용 식은 물리적으로 불가능한 값을 냈다** (dr 6.3 req/s vs prefill만으로의 상한 3.8) |
| gateTier | `gateAllowance`를 만드는 **같은 루프의 같은 제외 규칙**(구제불능 제외, nominal>0)에서 argmin의 tier를 기록 |
| 부호화 | 카운터 유지 없음 — 결정마다 후보 집합 위에서 새로 셈 (드리프트 불가) |

과부하(ΣK > N)면 비례 몫으로 정규화. limit 바닥 1(도착이 있는 클래스는 항상 첫
gate를 열 수 있음 — 없으면 복귀 burst가 전부 거절되고 후보 목록이 비어 panic).
정수 경계 hysteresis(demand가 limit+0.05를 넘어야 상승 — swe 수요가 하필 0.995에
앉아 있어 노이즈로 K가 깜빡임). class-pin이 설정된 tier는 cap 건너뜀(더 명시적인
운영자 배정이 이김).

## 4. force → 명시적 거절

원안 설계 문서(fluidserve-design.md의 shed 정책표)가 이미 "만료된
latency-sensitive는 즉시 REJECT"였고, force는 구현 과정의 이탈이었다. 측정
(`analysis_scripts/request_level/force_fate.py`, EXP-104/105 run): 강제 배치의
자기-예산 달성 56~82%(예측이 5건 중 1건 틀림), 같은 순간 다른 인스턴스 대비
동거 요청 위반율 수 배(선택 편향은 축소 방향인데도).

`--fluidserve-enable-force=false`면 그 분기가 shed가 되고, 사유를 갈라 센다:
- `cannot_meet` — 지금 놓아도 자기 예산을 못 지킴 (자기 보호)
- `no_feasible` — 지킬 수 있지만 모든 배치가 돌고 있는 요청들을 위반시킴 (타인 보호; 이전의 force 인구)

shed 끔 + force 끔은 기동 panic (조용한 auto-on은 검증기가 요청값과 기동 줄을
비교하므로 어차피 abort하거나 조용한 반전이 됨 — 저장소 관례대로 일찍 크게).

## 5. 하이퍼파라미터 회계

새 상수 **1개**: `--fluidserve-class-instance-cap-window-mult` (기본 3.0) —
"수요 이동이 함대 몫을 얻으려면 얼마나 지속되어야 하는가"의 정의. β 여유는
올림(⌈·⌉)으로 대체, 희소 조건 θ는 v1에서 제외(저부하 비용을 재고 결정), 가중치
w_c는 인터페이스(기본 1, 운영자 정책 자리).

## 6. cap이 하지 않는 것 (논문에서 과장하면 반증당할 것들)

- **용량을 만들지 않는다** — gate와 KV가 같은 토큰 재고를 읽는 시소(EXP-93 §9.5)
  는 허용 집합 안에서 그대로. cap은 거절의 배치를 수요에 정렬시키는 수단.
- **상한 안쪽의 과잉 집중을 막지 않는다** (EXP-97의 2대 몰림 → KV 천장).
- **지속되는 진짜 홍수를 막지 않는다** — 홍수는 자기 상한을 키운다(의도된 동작).
  특정 클래스 보호는 w_c의 일.

## 7. 계측

시리즈 7개, 전부 `llumnix_metrics.py` 화이트리스트에 등록, 생성 시 0으로 사전
등록(짧은 run에서 "발화 안 함"과 "수집 안 됨"이 구분되게; limit 게이지의 유휴
sentinel은 −1): `instcap_limit / instcap_gate_count / instcap_lambda /
instcap_excluded_total / instcap_blocked_feasible_total /
instcap_empty_fallback_total {tier}` + `shed_reason_total{reason}`.
`instcap_` 접두는 기존 `cap_kv_tokens`와의 grep 혼동 방지.

## 8. 리뷰에서 잡혀 설계가 바뀐 것 (기록)

1. 수축 경로 부재 (원안은 예방만 가능, 복구 불가) → §2의 세 번째 행.
2. μ의 디코드 전용 식이 물리 상한 위반 → mixed-step 닫힌 식.
3. 빈 후보 목록 panic (limit 0 + count·필터 집합 불일치) → 바닥 1, 같은 집합
   위에서 셈, 방어적 fallback.
4. `decisions_total`에 라벨 추가는 CounterVec 불일치 panic → 별도 시리즈.
5. gateTier를 tightestAllowance(잔액) 기준으로 했다면 요청 상태 따라 잠금
   정체가 깜빡였을 것 → nominal + gateAllowance와 같은 제외 규칙.
6. force의 세 탈락 사유는 다른 종류의 피해(여유/잔액/preemption) — 민성님 판정:
   여유를 알고 깨는 것도 비일관 → 전면 REJECT.
