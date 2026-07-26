# FluidServe 구현 기록

> 설계 원안: [fluidserve-design.md](fluidserve-design.md) · 대상 스택: NXC13 k3s, Llumnix,
> 4× vLLM 인스턴스(각 TP2, Llama-3.1-70B), B200×8 · 브랜치: `feat/fluidserve`
> (Agent_applications 쪽도 같은 이름의 브랜치)
>
> 이 파일은 **설계 원안을 실제 코드로 옮기면서 내린 결정과 그 근거**를 시간순으로
> 기록한다. 원안이 `[제안]`/`[미결]`로 남겨둔 자리를 채운 내용이 대부분이다.
> 서술 규칙: 비유를 쓰지 않고 일반적인 기술 용어로 인과를 적는다.

---

## 0. 목표와 성공 기준

**최종 목표**: 1시간 동적 trace(`dyn60_azure4d`, Azure 모양 rate + 15분 단위 믹스 변화)에서
PolyServe 라우팅을 **SLO attainment와 token goodput 두 지표 모두에서** 상회한다.

**채점 규칙(고정)**

| 클래스 | 규칙 | 근거 |
|---|---|---|
| chat | 평균 TTFT ≤ 5s **및** 평균 TBT ≤ 50ms | EXP-14/17/21과 동일 |
| deepresearch | 평균 TTFT ≤ 10s **및** 평균 TBT ≤ 100ms | 〃 |
| swe | E2E ≤ 30s | 〃 (EXP-17 기준) |

- 집계는 **클래스 등가중(equal-mix)**. fleet 평균은 arm마다 served 요청의 클래스 구성이
  달라져 비교가 성립하지 않는다(EXP-21에서 확인된 문제).
- **거절/미서빙 요청은 위반으로 집계한다.** 기존 `served_rows`는 admission reject를
  분모에서 제외하는데, 그 규칙을 그대로 쓰면 요청을 거절할 수 있는 정책이 자동으로
  유리해진다. 따라서 분모는 **분석 창에 도착한 모든 요청(offered)**으로 정의하고,
  기존 정의(served 기준)는 참고값으로 병기한다.
- token goodput = SLO를 만족한 요청이 생성한 output token만 합산(EXP-17 정의 그대로).

**중간 성공 기준**: 고정 rate smoke test에서 (a) 정책이 기동하고, (b) 라우팅 결정이
설계대로 관측되며, (c) 같은 rate의 PolyServe 대비 열세가 아님.

---

## 1. 실행 환경이 강제하는 것 (설계 원안과의 차이)

원안은 라우터가 큐를 소유하는 독립 프로세스를 가정한다. 실제 스택은 다음과 같다.

```
client → gateway (요청 보유, 재시도) → POST /schedule → scheduler (인스턴스 1개 반환)
                                                          → gateway가 엔진으로 전달
```

- **스케줄러는 큐를 갖지 않는다.** 요청당 한 번 호출되어 인스턴스 하나를 고르는
  함수다(`DispatchPolicy.Schedule`). 큐를 스케줄러 안에 새로 만들면 게이트웨이와의
  프로토콜을 바꿔야 하고, 그러면 PolyServe arm과 게이트웨이 구성이 달라져 비교가
  오염된다.
- **게이트웨이에 이미 보유-재시도 경로가 있다.** `SchedulerClient.Get()`은 스케줄러가
  `ErrorNoAvailableEndpoint`(HTTP 429)를 반환하면 `wait-scheduling-retry-interval`만큼
  자고 `wait-scheduling-timeout`까지 재시도한다. 요청은 그동안 **어떤 엔진에도
  바인딩되지 않는다.**
- **완료 이벤트가 스케줄러에 오지 않는다.** full-mode에서 `/release`는 전달 실패 시에만
  호출된다(`OnPostRequest`). 정상 완료는 CMS 상태 동기화로만 반영된다.
- **엔진은 요청별 진행도를 보고하지 않는다.** 다만 CMS 상태에 인스턴스 단위로
  `StepId`(엔진의 누적 step 카운터), `NumUsedGpuTokens`, `SchedulerRunningToDecodeRequestsNum`,
  `StepDuration`(직전 step 실행 시간), `NumScheduledPrefillTokens`가 들어 있다.

### 결정 D1 — C7(late binding)은 게이트웨이 보유-재시도로 구현한다

스케줄러가 `PEND`을 표현하는 방법은 429 반환이다. 요청은 게이트웨이에 남고, 엔진
바인딩은 **재시도가 성공하는 순간**에 일어난다. 원안이 요구한 성질(바인딩 지연,
재배치 비용 0, 얕은 엔진 큐)은 그대로 얻는다. 얻지 못하는 것은 라우터가 대기 요청
집합을 한 번에 보고 순서를 정하는 능력이다. 이는 스케줄러가 요청 ID별 최초 관측
시각을 기록해 **대기 시간을 알고 있는 상태로 매 재시도를 판정**하는 것으로 부분
대체한다(아래 C5).

재시도 간격 기본값 1000ms는 이 용도에 너무 크다. FluidServe arm은
`--wait-scheduling-retry-interval=100ms`, `--wait-scheduling-timeout`은 가장 큰 TTFT
예산에 맞춰 12s로 올린다. **PolyServe arm의 게이트웨이 설정은 건드리지 않는다** —
두 arm의 게이트웨이가 달라지므로, 이 차이를 결과 해석에 명시하고, 필요하면
"PolyServe + 같은 재시도 설정" 대조군을 추가한다.

### 결정 D2 — 요청 진행도는 `StepId` 차분으로 복원한다

연속 배칭에서 디코딩 중인 요청은 스케줄되는 매 step마다 정확히 토큰 1개를 낸다.
따라서 어떤 요청이 디코드에 진입한 뒤 엔진이 실행한 step 수가 곧 그 요청이 생성한
토큰 수다. 스케줄러는 dispatch 시점의 `StepId`를 기록해 두고

```
j_r = max(0, StepId_now − StepId_dispatch − prefillSteps(l₀))
prefillSteps(l₀) = ceil(l₀ / max_num_batched_tokens)
```

로 진행도를 복원한다. wall-clock을 쓰지 않으므로 iteration time 변동에 영향받지 않고,
CMS 폴링 지연에도 둔감하다. preemption이 일어나면 과대추정이 되는데, 이는
outflow(완료량) 과대추정 방향이므로 원안의 원칙 4(오차를 보수 쪽으로)와 반대다.
그래서 아래 D3의 대조(reconciliation)로 보정한다.

### 결정 D3 — 레지스트리는 엔진 보고값을 기준으로 대조한다

스케줄러가 유지하는 인스턴스별 요청 목록은 추정값이고, 엔진이 보고하는
`NumUsedGpuTokens`(점유 토큰)와 running 요청 수가 사실이다. 매 스케줄 호출에서:

1. 레지스트리 항목 수가 엔진의 running 수보다 많으면, **진행도가 큰 항목부터**
   제거한다(먼저 끝났을 가능성이 높은 순서).
2. 클래스 생존함수 기준 `S_c(j) < s_min`인 항목은 완료로 간주해 제거한다.
3. 레지스트리 합 `Σ(l₀+j)`와 엔진 보고 `NumUsedGpuTokens`의 비를 drift 지표로
   기록하고, outflow 계산에 쓰는 `w_r`에 그 비를 곱해 규모를 맞춘다.

이 구조에서 레지스트리의 역할은 **점유 토큰의 절대량을 맞히는 것이 아니라, 엔진이
보고한 점유량을 클래스와 진행도별로 배분하는 것**이다. 절대량은 항상 엔진 값을 쓴다.

---

## 2. 설계 원안의 재구성 3가지

리뷰에서 지적한 세 가지 문제(cap 고정, externality 무력화, 교정 통계량 불일치)를
해결하기 위해 원안의 수식을 다음과 같이 바꾼다. 컴포넌트 구성(C1~C7)과 설계 원칙
(engine agnostic, 정확한 양만 온라인 사용, 분포 측정, 보수 편향)은 유지한다.

### R1 — SLO를 순간 임계값이 아니라 **잔여 예산**으로 표현한다

원안의 `cap_i = f(min TBT among requests)`는 우리 SLO 숫자에서 다음 문제를 만든다.
swe의 tier 키는 TPOT 25ms인데, 이 값은 B200에서 측정된 단독 디코드 ITL 중앙값과
같다. 즉 배치 크기가 1을 넘는 순간 위반이므로, swe 요청 하나가 배치된 인스턴스는
그 요청이 728 토큰을 다 생성할 때까지 cap이 최저값으로 고정되고 다른 요청을 받을 수
없게 된다. 인스턴스 4대 중 일부가 이 상태로 오래 머무르면 fleet 처리량이 그만큼
줄어든다.

그런데 **채점 규칙은 순간 TBT가 아니다**. 세 클래스 모두 실제로는 누적 예산이다.

| 클래스 | 채점 규칙 | 누적 예산으로 환산 |
|---|---|---|
| chat | 평균 TBT ≤ 50ms, 평균 출력 386 | 디코드 총시간 ≤ 19.3s |
| deepresearch | 평균 TBT ≤ 100ms, 평균 출력 275 | 디코드 총시간 ≤ 27.5s |
| swe | E2E ≤ 30s, 평균 출력 728 | TTFT + 디코드 총시간 ≤ 30s |

따라서 요청 r의 **현재 허용 per-token 시간**을

```
allowance_r = (예산_r − 이미 소비한 시간) / (예상 잔여 토큰 수)
```

로 정의한다. 예산을 앞서 쓴 요청은 allowance가 줄고, 여유가 있는 요청은 늘어난다.
`cap_i`는 인스턴스에 올라간 요청들의 allowance 최솟값으로 정한다. 이렇게 하면

- swe가 초반에 빠르게 진행했다면 allowance가 25ms보다 훨씬 커져 cap을 낮추지 않는다.
  (30s − TTFT 1.5s) / 728 ≈ **39ms**가 시작 시점의 allowance이며, 앞부분이 빠를수록
  더 커진다.
- 반대로 예산을 초과하기 시작한 요청은 allowance가 0에 수렴한다. 이때는 cap을 낮추는
  대신 **그 요청을 이미 위반 확정으로 분류하고 cap 계산에서 제외**한다(아래 R1-b).
  이미 SLO를 놓친 요청 때문에 나머지 요청까지 손해를 보는 상태를 피하기 위함이다.

**R1-b 구조적 실현 불가 판정**: allowance가 그 인스턴스에서 요청 혼자 있을 때의
디코드 step 시간보다 작으면, 어떤 배치 구성으로도 만족할 수 없다. 이런 요청은
`best-effort`로 강등해 cap 최솟값 계산에서 빼고, 라우팅에서도 다른 요청을 방해하지
않는 인스턴스로 보낸다. 이것은 PolyServe가 swe에 전용 서버 2대를 주고 그 tier의
attainment가 0에 가까워지는 것을 감수한 것과 같은 판단을, **서버를 전용으로 묶지 않고**
내리는 것이다.

### R2 — capacity 모델에 **prefill 점유율 φ**를 1급 변수로 넣는다

EXP-16 측정: 디코드 step은 17~113ms, full-chunk(8192 토큰) prefill step은 519.7ms로
6~37배다. vLLM V1의 chunked prefill은 대기 중인 prefill이 있으면 매 step에 청크를
싣는다. 따라서 어떤 인스턴스의 **평균 step 시간**은

```
s_p   = min(k, ceil(P_i / chunk))          # 앞으로 k step 중 prefill을 싣는 step 수
mean_step(k) = [ (k − s_p)·t_dec(M) + s_p·t_pre(chunk) ] / k
```

이고, 디코딩 요청이 겪는 평균 TBT가 곧 이 값이다(청크가 실린 step에서도 디코딩
요청은 토큰 1개를 받으므로, 토큰 간격이 그 step 시간이 된다).

이 형태의 장점은 **externality가 별도 장치 없이 같은 식에서 유도된다**는 점이다.
요청 r을 인스턴스 i에 배치하면 `P_i`가 `l₀(r)`만큼 늘어 `s_p`가 `ceil(l₀/chunk)`만큼
증가하고, 그 결과 i에 이미 올라가 있는 모든 요청의 `mean_step`이 얼마나 나빠지는지가
바로 계산된다. 원안의 `cap_if_added`를 따로 추정할 필요가 없다.

`t_dec(M)`는 EXP-11에서 확인된 KV 점유량에 대한 선형 관계를 쓴다(`t_dec = a + b·M`).
`t_pre(x)`는 P4-b에서 유휴 엔진 스윕으로 직접 측정한 `ttft.json`을 그대로 쓴다.

### R3 — 대기(PEND)와 확산(spill)을 명시적으로 비교한다

원안의 판정 순서는 `feasible = {i : headroom_i ≥ cost}` → 그 안에서 externality 반영
점수 최대. 이 순서에서는 tight 요청을 모아둔 인스턴스가 cap이 낮아 headroom이 먼저
마르므로 feasible 집합에서 가장 먼저 빠지고, 결국 tight 요청이 loose 인스턴스로
확산된다. 부하가 올라갈수록 확산이 심해져 원안이 의도한 집중이 반대로 작동한다.
(PolyServe 구현에서 admission이 모든 인스턴스에서 탈락한 뒤 fallback으로 풀려
사실상 무력화된 것과 같은 형태의 문제다.)

그래서 판정을 다음으로 바꾼다.

```
best_now   = argmin_i  degradation(i, r)         # 지금 보내면 fleet에 주는 총 손해
wait_gain  = degradation(best_now, r) − degradation(best_future, r)
if 요청 r의 TTFT 잔여 예산 > 예상 대기시간 and wait_gain > wait_threshold:
        PEND
else:   ROUTE(best_now)
```

`degradation(i, r)`은 R2의 `mean_step` 증가분을 i에 있는 요청들의 allowance 여유로
정규화한 값의 합에, r 자신이 i에서 받을 손해를 더한 것이다. `best_future`는
가장 빨리 여유가 생길 인스턴스의 예측 상태로 평가한다. 즉 **대기 자체가 하나의
선택지로 점수화**되며, 이것이 PEND(=429)의 발생 조건이다.

---

## 3. 컴포넌트 구현 명세

파일은 모두 `pkg/scheduler/policy/` 아래 `fluidserve_*.go`로 새로 만든다.
기존 파일 수정은 등록(`scheduling_policy_registry.go`), 상수(`pkg/consts`), 설정
플래그(`cmd/config/config.go`)에 한정한다. PolyServe 코드는 건드리지 않는다.

| 파일 | 컴포넌트 | 내용 |
|---|---|---|
| `fluidserve_profile.go` | C1, C3(오프라인) | 클래스 생존함수 + 디코드 step 선형모델 로딩, JSON 스키마 |
| `fluidserve_length.go` | C1 | 온라인 Kaplan-Meier 갱신, `p_c(j,k)` |
| `fluidserve_registry.go` | C2 | 인스턴스별 요청 레지스트리, `StepId` 기반 진행도, 대조 |
| `fluidserve_capacity.go` | C3 | `t_dec(M)` 온라인 적합, `mean_step(k)`, `cap_i`, allowance |
| `fluidserve_flux.go` | C2 | inflow/outflow/proj/headroom |
| `fluidserve_decide.go` | C4, C5 | degradation 점수, ROUTE/PEND 판정, 대기 마감 |
| `fluidserve_tuner.go` | C6 | z_safety 조정, 예측 잔차 감시 |
| `fluidserve.go` | 조립 | 정책 구조체, 필터/셀렉터 어댑터, 메트릭 발행 |

### 설정 플래그

```
--scheduling-policy fluidserve
--fluidserve-profile-path      /profiling/fluidserve.json   # C1+C3 시드
--fluidserve-horizon-steps     100      # k
--fluidserve-z-safety          1.65
--fluidserve-alpha-externality 1.0
--fluidserve-class-budgets     "25:e2e:30000:728,50:decode:19300:386,100:decode:27500:275"
--fluidserve-enable-pend       true     # false면 항상 즉시 배치 (ablation)
--fluidserve-enable-externality true    # ablation
--fluidserve-enable-flux       true     # false면 현재 점유량만 사용 (ablation)
```

`--fluidserve-class-budgets`의 형식은 `tpotSloMs:mode:budgetMs:expectedTokens`이며
`mode`는 `e2e`(TTFT가 예산에 포함) 또는 `decode`(TTFT 별도)다. tier 키가 TPOT SLO인
것은 PolyServe와 동일한 게이트웨이 배관(packed priority)을 그대로 쓰기 위함이다.

---

## 4. 마일스톤

| 단계 | 내용 | 상태 |
|---|---|---|
| M1 | 기존 로그에서 오프라인 프로파일 생성(`fluidserve.json`): 클래스 생존함수, `t_dec(M)` 계수, 혼합 step 검증 | 완료 |
| M2 | C1/C2/C3 구현 + 단위 테스트 | 대기 |
| M3 | C4/C5/C7 구현 + 단위 테스트 | 대기 |
| M4 | C6 구현 | 대기 |
| M5 | 빌드/배포/고정 rate smoke | 대기 |
| M6 | 1시간 동적 trace 2-arm 비교 + 분석 | 대기 |

---

## 5. 시간순 기록

### 2026-07-26 — 착수, 스택 조사

`feat/fluidserve` 브랜치를 두 저장소에 생성(llumnix_reproduce는 `feat/kv-admission-threshold`
에서, Agent_applications는 `feat/exp07-kv-admission`에서 분기).

조사에서 확인한 사실과 그로부터 나온 결정이 위 §1(D1~D3), §2(R1~R3)이다. 특히
다음 두 가지는 설계 원안이 "엔진 수정이 필요할 수 있다"고 적어둔 항목을 수정 없이
해결한다.

- `InstanceStatus.StepId`가 엔진의 누적 step 카운터를 담고 있어 요청별 진행도를
  차분으로 복원할 수 있다(D2). 원안 §7.1의 `request_states[]` 요구가 사라진다.
- `InstanceStatus.StepDuration`과 `NumScheduledPrefillTokens`가 이미 CMS 상태에 실려
  있다(`python/llumnix/status_collector/vllm_v1/status_updater.py`에서 채움). 즉
  `(점유량, 배치 내 prefill 토큰) → step 시간` 표본이 폴링 주기마다 들어오므로 C3의
  온라인 교정이 엔진 수정 없이 가능하다. 원안 §11.2의 L3(고점유 구간을 관측하지
  못해 곡선이 미성숙한 채로 남는 문제)는 오프라인 시드(EXP-16 step dump 431,440
  step)와 이 온라인 표본을 함께 써서 완화한다.

### 2026-07-26 — M1 완료: 오프라인 프로파일

생성기 `ms_dev/scripts/gen_fluidserve_profile.py`,
산출물 `deploy/profiling/llama31-70b-b200-tp2/fluidserve.json`.

**(1) 디코드 step 법칙** — EXP-16 step dump의 decode-only step 377,838건, 76개 셀
(셀당 최소 30표본, 셀 중앙값으로 적합):

```
t_dec = 16.361 ms + 1.2822e-5 · M + 7.643e-2 · n      R² = 0.952
        (M = 인스턴스가 보유한 KV 토큰 수, n = 디코딩 요청 수)
상대오차 p50 6.4% / p90 20.5%
```

기존 `tpot.json`의 독립 적합(16.427 / 1.412e-5 / 7.950e-2)과 세 계수가 모두 근접해
서로 교차검증된다. KV 항은 엔진 1대 기준 12.8 ns/token이고, EXP-11이 fleet 합계 KV에
대해 보고한 약 3 ns/token을 엔진 4대로 나눈 값과 일치한다.

**(2) 혼합 step 모델 — 검증 결과 가법 형태로 확정**

prefill 청크를 실은 step의 비용을 두 가지로 놓고 실측 window와 비교했다.

| 형태 | 식 |
|---|---|
| 분리형 | `mean = (1−φ)·t_dec + φ·t_pre(chunk)` |
| **가법형(채택)** | `mean = (1−φ)·t_dec + φ·(t_pre(chunk) + t_dec − c0)` |

window는 **엔진별 step 사슬**을 복원한 뒤 연속 40 step으로 잡고, 측정값은
`(window가 걸친 wall time)/(step 수)`로 구했다(비동기 스케줄러의 run-ahead를
개별 step에 귀속시키지 않기 위함).

| φ | window 수 | 실측 | 가법형(비) | 분리형(비) |
|---|---|---|---|---|
| 0.0 | 4,717 | 21.5 ms | 19.9 (0.96) | 19.7 (0.95) |
| 0.2 | 2,459 | 22.1 ms | 25.0 (1.11) | 23.8 (1.06) |
| 0.4 | 1,050 | 30.1 ms | 36.5 (1.19) | 30.0 (0.99) |
| 0.6 | 36 | 124.6 ms | 109.8 (**0.90**) | 79.1 (**0.66**) |
| 0.8 | 4 | 134.6 ms | 123.2 (**0.89**) | 80.1 (**0.59**) |

가법형을 채택한 이유: prefill을 실은 step은 그 step에서 prefill GEMM과 함께 실행 중인
모든 디코딩 요청의 attention도 수행하므로 두 비용이 더해지고, 고정 오버헤드 `c0`만
한 번 계산된다. `ttft.json`은 유휴 엔진에서 측정돼 디코드 비용이 들어 있지 않으므로
분리형은 간섭이 큰 구간에서 35~40% 과소예측한다. **과소예측은 과잉 admission을 통해
preemption으로 이어지므로 가장 피해야 할 방향**이며(원안 원칙 4), 가법형은 중간
구간에서 11~19% 과대예측하는 대신 그 방향의 오차가 없다.

한계: φ ≥ 0.6 구간의 표본이 40개뿐이다. EXP-16 워크로드에서 prefill 포화 구간이
드물게 나타나기 때문이며, 이 구간의 정확도는 온라인 교정(C3)과 z_safety로 보완한다.

**(3) 클래스 출력 길이 분포** — EXP-21의 10개 run(2 arm × 5 rate) 전체에서,
에러·거절·타임아웃·run 종료 절단 요청을 제외한 60,042건.

| 클래스 | tier | n | 평균 | p50 | p90 | 최대 |
|---|---|---|---|---|---|---|
| swe | 25ms | 14,222 | 520 | 507 | 721 | 1,724 |
| chat | 50ms | 22,886 | 422 | 381 | 760 | 3,005 |
| deepresearch | 100ms | 22,934 | 282 | 256 | 452 | 2,049 |

**분포가 부하에 의존하지 않는 것을 확인했다** — run별 평균이 chat 375~451,
deepresearch 269~291, swe 499~550으로 rate가 5배 변해도 안정적이다. 따라서 전체를
합쳐 하나의 분포로 쓰는 것이 정당하고, 우측 절단(run 종료로 잘린 요청) 처리가
제대로 되고 있다는 근거도 된다.

**기록해 둘 불일치**: PolyServe arm은 `--polyserve-tier-decode-tokens "25:728,50:386,100:275"`
로 돌았는데, 위 실측은 520/422/282다. swe가 특히 크게 다르다(728 대 520). EXP-21
결과의 재현성을 위해 PolyServe arm의 설정은 **바꾸지 않고** 그대로 비교하되, 최종
결과가 이 파라미터에 의존하는지 확인이 필요하면 보정한 PolyServe arm을 추가한다.
