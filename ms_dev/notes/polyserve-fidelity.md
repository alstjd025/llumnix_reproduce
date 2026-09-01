# PolyServe 이식의 충실도 — 원문 대조, 그리고 "Isolation의 대표주자"로 세울 수 있는가

**작성**: 2026-08-04
**원문**: `related_works/[arxiv] PolyServe- Efficient Multi-SLO Serving at Scale.pdf`
(arXiv:2507.17769, UW + ByteDance)
**포트**: `pkg/scheduler/policy/polyserve.go`(363줄),
`pkg/scheduler/policy/polyserve_repartition.go`, 설정 주입은
`ms_dev/scripts/set_scheduler_profiling.py`
**설계 정본**: [POLYSERVE_DESIGN_KO.md](../../POLYSERVE_DESIGN_KO.md),
시간순 기록은 [POLYSERVE_PROGRESS.md](../../POLYSERVE_PROGRESS.md)
**같은 형식의 앞 문서**: [qoserve-niyama-fidelity.md](qoserve-niyama-fidelity.md)

> ⚠ **2026-08-27에 §9가 추가됐고 그것이 지금 코드의 정본이다.** 아래 §0~§8은 2026-08-04
> 시점의 대조 기록이고, **지금 무엇이 구현돼 있는지를 묻는 질문에는 §9를 읽는다.** 특히
> 다음 넷이 §9에서 뒤집혔다: §0의 "lazy promotion 미구현"(구현됐다), §1의 8번
> "거절 경로가 없는 것이 충실하다"(논문에 없는 것은 거절이고 **보류는 있다** — §4.3의
> pending과 §4.6의 pending time), §3.1의 "선택 규칙을 뒤집은 것이 정당하다"(오토스케일링을
> 되살리면 그 정당화가 사라진다), §4의 "고부하에서 admission이 무력화되는 것은 발견이다"
> (원인의 절반은 우리 쪽 결함이었다). §2의 출력 길이 문제는 2026-08-05에 고쳐졌다.

---

## 0. 세 문장 요약

**대표주자로 세울 수 있다.** 우리가 가장 충실하게 이식한 부분이 정확히 "정적 클래스
파티션이 과부하에서도 유지된다"는 성질이고, 그것이 논문에서 isolation을 만드는 메커니즘이며,
우리 측정에서 PolyServe의 결과를 만드는 것도 그 메커니즘이다(고부하에서 admission은 사실상
비활성이고 남는 것이 파티션뿐이다).

**단, 이름을 "PolyServe"로 붙이면 안 되고 "고정 fleet 위의 정적 클래스 파티션"이라고
불러야 한다.** 오토스케일링(사용자 지시로 제외)과 lazy promotion(미구현)이 빠져 있고,
within-tier 선택 규칙을 논문의 최고부하에서 최저부하로 **뒤집었다**.

**발표 전에 반드시 고쳐야 할 것이 하나 있다.** PolyServe가 쓰는 클래스별 기대 출력 길이가
2026-07-25 측정값에 멈춰 있어서 **deepresearch가 275인데 실측은 985다(3.58배 과소)**.
이것은 §48·§49에서 FluidServe를 20점 끌어올린 것과 **같은 종류의 오류이고, 지금은
기준선 쪽에만 남아 있다.** 고치지 않고 EXP-53 수치를 논문에 실으면 **수정된 시스템과
수정되지 않은 기준선을 비교하는 것이 된다.**

---

## 1. 논문의 메커니즘을 단위로 나누고, 각각이 이식되었는지

논문 §4의 구성요소를 하나씩 놓고 대조한다.

| # | 논문 메커니즘 | 이식 상태 | 어디에 |
|---|---|---|---|
| 1 | **TPOT 기준 요청 binning** (§4.2) | **충실** | tier 키가 곧 TPOT SLO(ms)다. chat 50 / deepresearch 100 / swe 25 |
| 2 | **tier별 서버 파티션 = isolation** (§4.2) | **충실. 이 포트에서 가장 중요한 부분** | `tierAffinityFilter`. 아래 §1.1 |
| 3 | **§4.5 profile-based batch formation** — 요청 생애 최대 KV로 판정 | **충실** | `polyserveIterTime{atMaxKV:true}` |
| 4 | **§4.6 wait-time-aware scheduling** — 둘째 토큰 마감이 `TTFT + TPOT` | **충실. 죽은 코드였던 것을 고쳤다** | `polyserveAdmissionFilter`의 두 번째 검사. 아래 §1.2 |
| 5 | **§4.7 continuous chunked prefill prediction** — co-location에서 prefill 청크가 iteration을 지배 | **충실** | `polyserveIterTime`의 prefill 간섭항 |
| 6 | **출력 길이를 예측하지 않고 클래스 평균을 쓴다** | **충실. 그런데 값이 낡았다** | §4가 이 문서의 핵심 문제 |
| 7 | `(batch size, KV cache size) → iteration time` 프로파일 테이블 | **충실. 축이 원래 같았다** | Llumnix `LatencyPredictor`가 이미 같은 두 축 |
| 8 | **거절 경로가 없다** — 과부하는 SLO 위반으로만 나타난다 | **충실** | admission은 fallback에서 풀리고, tier 경계는 안 풀린다 |
| 9 | **§4.3 fine-grained autoscaling** | **제외** (사용자 지시). 고정 fleet 재분할로 수렴점만 재현 | `polyserve_repartition.go`. 아래 §3.1 |
| 10 | **§4.4 lazy promotion** — 자기 tier가 꽉 차면 더 빡빡한 tier의 서버를 빌린다 | **미구현** | 아래 §3.2. **PolyServe에 불리한 방향의 결손** |
| 11 | **§2.3 DSLO** — `i`번째 토큰이 `TTFT + i·TPOT` 이전에 나오면 된다 | **판정에만 부분 반영, 채점은 순간 기준** | 아래 §3.3 |
| 12 | **PD-disaggregation** | **제외.** 우리는 co-location만 | 논문의 이득은 PD 쪽이 더 크다(1.23× 대 1.18×) |

### 1.1 isolation이 어떻게 구현되어 있는가 — 이것이 대표주자 주장의 근거다

`tierAffinityFilter`는 요청의 tier에 배정된 서버만 통과시킨다. 그리고 **Llumnix의 2-pass
fallback에서 이 필터가 풀리지 않는다.**

```go
// polyserve.go:256
func (f *tierAffinityFilter) skipWhenFallback() bool { return false }
```

주석에 이유가 적혀 있다: "Isolation is the point of the tier partition, so it must survive
the fallback pass; otherwise an overloaded tier would spill onto the servers protecting the
other tiers, which is precisely what PolyServe exists to prevent."

반대로 admission 필터는 풀린다.

```go
// polyserve.go:233
func (f *polyserveAdmissionFilter) skipWhenFallback() bool { return true }
```

**즉 어떤 서버도 admission을 통과하지 못하면 요청은 거절되는 것이 아니라 자기 tier 안에서
가장 덜 찬 서버로 간다.** 이것이 PolyServe의 거동과 맞다 — 논문에 drop 경로가 없고 과부하는
SLO 위반으로 나타난다.

**이 두 줄이 "PolyServe = isolation"이라는 주장의 코드상 근거다.** 과부하가 심해질수록
admission은 풀리고 tier 경계만 남는다.

**그리고 그것이 작동한다는 것이 두 방향으로 측정됐다.**

라우팅 집중도(0 = 네 엔진에 고르게 분산, 1 = 한 대에 고정), EXP-21 스모크:

| arm | chat | deepresearch | swe |
|---|---|---|---|
| polyserve | 0.956 | 0.944 | **0.333** |
| loadbalance | 0.001 | 0.000 | 0.000 |

swe의 0.333은 **4대 중 2대에 갇힌 클래스의 이론값과 정확히 같다.** 즉 재분할이
swe 2 / chat 1 / dr 1로 수렴했고 격리가 전 구간 작동했다.

그리고 엔진이 직접 보고하는 값으로도 확인된다 — **엔진별 prefix cache hit rate가
PolyServe에서 chat 엔진 94.5% 대 agent 엔진 66.7%로 갈리는데, FluidServe는 네 엔진 전부
63~65%였다**(CLAUDE.md 판정 규칙 절). 한 엔진에 한 클래스만 있으면 프롬프트 prefix가
공유되므로 hit rate가 올라간다. 클래스가 실제로 분리되었는지를 요청 단위 지표가 아니라
엔진이 말해 주는 값이다.

### 1.2 §4.6이 처음에 죽은 코드였고, 그것을 고친 경위

논문 §4.6은 "profile-based batch formation은 두 번째 디코드 토큰부터만 유효하다. 첫 토큰은
TTFT로, 두 번째 토큰은 TTFT + TPOT로 규제되고 둘 다 큐잉 시간을 겪는다"고 적는다.

처음 구현은 `ttft + iter ≤ ttftSlo + tpotSlo`였는데, **검사 1(`ttft ≤ ttftSlo`)과
검사 3(`iter ≤ tpotSlo`)이 통과하면 이 식은 대수적으로 항상 참이라 아무것도 걸러내지
못했다.** 의미가 생기려면 `iter`를 두 개로 쪼개야 한다.

- `iterNow` — **지금** KV로 계산한 다음 iteration. co-location에서는 대기 중인 prefill
  청크가 이 값을 지배한다(우리 실측으로 full chunk 스텝이 520~634 ms, 디코드 스텝이
  17~113 ms)
- `iterMax` — 배치가 다 자란 뒤의 정상상태 iteration (§4.5)

지금 코드(`polyserve.go:208~224`):

```go
if ttft > ttftSlo                     { return reject("first token") }   // 1토큰
if ttft+iterNow > ttftSlo+tpotSlo     { return reject("second token") }  // 2토큰, §4.6
if iterMax > tpotSlo                  { return reject("steady state") }  // 3토큰~, §4.5
```

주석에 왜 두 번째가 독립적인지가 적혀 있다: "it uses the near-term iteration, which a
co-scheduled prefill chunk can blow far past TPOT even while the steady state is fine."

**이 정정은 원문을 다시 읽어서 잡았다.** 초기 요약이 §4.5·§4.6·§4.7 세 메커니즘을
`wait + T_iter < TPOT` 한 줄로 뭉갠 것이었고, 셋 다 v1에 빠져 있었다(PROGRESS §2026-07-25).

---

## 2. ⚠ 반드시 고쳐야 할 것 — 클래스별 기대 출력 길이가 낡았다

### 2.1 무엇이 틀렸나

PolyServe는 §4.5의 최대 KV를 계산할 때 각 요청이 자기 tier의 **기대 출력 길이**만큼 자란다고
가정한다. 논문도 개별 요청의 길이를 예측하지 않고 "average decode length"를 쓰므로 이
방식 자체는 충실하다. 문제는 **그 평균값이 2026-07-25에 측정된 뒤 한 번도 갱신되지
않았다는 것**이다.

`ms_dev/scripts/set_scheduler_profiling.py:58`:
```python
"--polyserve-tier-decode-tokens": "25:728,50:386,100:275",
```

| 클래스 | tier(TPOT ms) | **배포된 값** | **실측 (2026-08-02판 프로파일)** | 비율 |
|---|---|---|---|---|
| swe | 25 | 728 | **494** | 0.68배 (과대) |
| chat | 50 | 386 | **428** | 1.11배 (과소) |
| **deepresearch** | **100** | **275** | **985** | **3.58배 (과소)** |

**deepresearch의 3.58배는 §48·§49가 FluidServe에서 잡은 것과 같은 원인이다.**
`Agent_applications 5fa82f8`(2026-07-29)이 searcharena 보고서 구조를 4→7절로 늘려 그
클래스의 출력이 282 → 985 토큰이 됐는데, FluidServe 쪽 프로파일(`fluidserve.json`의
`classes[]`)만 재생성하고 **PolyServe 쪽 상수는 안 고쳤다.**

swe의 728은 그보다 더 오래된 값이다 — 22.5k 짜리 transcript를 쓰던 시절의 측정이고,
그 뒤 `build_short_transcript.py`로 7k로 줄였다.

### 2.2 이 값이 어디에 쓰이는가 — 두 곳이고, 둘째가 더 중요하다

**(1) admission 판정의 최대 KV** (`polyserve.go:123`)
```go
kv += batch * int32(m.decodeTokens.forTier(tpotSloMs))
```
dr을 275로 보면 최대 KV가 실제보다 작게 나오고, 그러면 `iterMax`가 작게 나오고, 그러면
**dr에 대해 admission이 실제보다 관대해진다.** swe는 반대로 728로 과대평가하므로
admission이 실제보다 엄격해진다.

**(2) 재분할기의 수요 추정** (`polyserve_repartition.go:119, 129, 133`)
```go
outTokens := float64(r.decodeTokens.forTier(tierTpotMs))
kvPerRequest := meanInputTokens + outTokens/2
decodeSeconds = outTokens * (float64(tierTpotMs)/1000.0) / float64(batch)
```
**이쪽이 더 중요하다.** 이 값이 tier별 요청당 server-seconds를 정하고, 그것이 곧
tier → 서버 배분을 정하며, **그 배분이 isolation 그 자체다.**

설계 문서에 기록된 라이브 수렴값:
```
PolyServe repartition: 25ms=2 50ms=1 100ms=1
  (demand 25ms=0.65 50ms=0.02 100ms=0.07, 4 live servers)
```

출력 길이가 dr에서 3.58배 올라가고 swe에서 0.68배 내려가면 이 demand 세 값이 전부
움직인다. `decodeSeconds`가 `outTokens`에 비례하고, `kvPerRequest`가 커지면
`maxBatchForTpot`이 작아져서 다시 `decodeSeconds`를 키우므로 **dr의 수요는 3.58배보다
더 오른다.**

**⚠ 그래도 배분이 (2/1/1)에서 안 바뀔 가능성이 높다** — tier가 3개이고 서버가 4대라
tier당 최소 1대를 보장하면 자유로운 서버가 1대뿐이고, swe의 요청당 비용이 여전히 가장
크기 때문이다. **그러나 이것은 추정이고 재야 한다.** §55(배포값을 파일에서 확인하지 않고
인용한 것)와 같은 실수를 반복하지 않으려면 고친 값으로 재분할기를 돌려 로그의
`PolyServe repartition:` 줄을 직접 읽어야 한다.

### 2.3 왜 이것을 고치지 않으면 논문에 실을 수 없는가

**FluidServe는 이 오류를 고쳤고 그것이 프로젝트 역사상 가장 큰 이득이었다** — 정적
60 req/s에서 offered 36.6 → 56.8, 한 시간 trace에서 59.7 → 70.2(§49). 정책 코드는 0줄
바뀌었다.

**PolyServe는 같은 오류를 그대로 안고 EXP-53에 들어갔다.** 즉 현재 표는

> 길이 프로파일이 수정된 FluidServe **대** 수정되지 않은 PolyServe

를 비교한 것이다. 이것을 그대로 실으면 **우리에게 유리한 방향의 비대칭이고, 리뷰어가
발견하면 그 표 전체의 신뢰가 무너진다.** EXP-53의 PolyServe 수치(35 req/s에서 41.1,
70에서 14.4)가 얼마나 달라질지는 모르지만, **"모른다"는 것 자체가 문제다.**

### 2.4 고치는 방법 — 코드 0줄

```python
# ms_dev/scripts/set_scheduler_profiling.py:58
"--polyserve-tier-decode-tokens": "25:494,50:428,100:985",
```

값의 출처는 `deploy/profiling/llama31-70b-b200-tp2/fluidserve.json`의 `classes[].mean`이고,
이것은 2026-08-02에 실제 run 로그에서 재생성한 것이다. **두 정책이 같은 표본에서 나온
같은 값을 쓰게 되므로, 길이 정보의 정확도가 비교에서 상쇄된다.**

**그리고 재발 방지 장치가 필요하다.** 지금은 이 값이 두 곳(FluidServe의 JSON, PolyServe의
명령줄 문자열)에 따로 있어서 한쪽만 갱신되는 것을 막을 수 없다.
`set_scheduler_profiling.py`가 `fluidserve.json`에서 읽어 두 정책에 같은 값을 넣도록
바꾸는 것이 옳다. **v0.1.1 §6.6이 FluidServe에 대해 요구한 "프로파일과 워크로드가 맞는지
확인하는 장치"를 PolyServe에도 걸어야 한다는 뜻이다.**

---

## 3. 의도적으로 다르게 한 것 셋, 그리고 각각의 방향

**"다르다"가 곧 "불충실"은 아니다. 방향이 중요하다** — PolyServe에 유리한 쪽으로 바꿨는지
불리한 쪽으로 바꿨는지를 각각 적는다.

### 3.1 within-tier 선택: 최고부하 → **최저부하로 뒤집었다** (PolyServe에 유리)

논문 §4.3~4.4의 핵심 규칙은 "SLO를 지킬 수 있는 서버 중 **가장 부하가 높은** 곳으로
보내라"이고, 목적은 부하 구배를 만들어 마지막 서버를 비워 스케일다운을 쉽게 하는 것이다.

우리는 오토스케일링을 제외했으므로 그 보상이 0이 된다. 그래서 뒤집었다
(`leastBindingLatencySelector`, `polyserve.go:315`). 각 인스턴스를
`max(predictedTtft/ttftSlo, iterMax/tpotSlo)`로 채점해 가장 작은 것을 고른다 — 즉 그
인스턴스에서 먼저 구속력을 가질 차원에서의 여유로 고른다.

**이 변경은 PolyServe를 강하게 만든다. 그리고 그것이 독립적으로 확인됐다.**
"Simple is Better"(OSDI '26, `related-works-review.md` §8.3)가 PolyServe를 재구현해
비교했는데, PolyServe가 16대 중 인스턴스 0~8을 채우고 9~15를 비워 두는 반면 자기들은
고르게 편다고 적고, **바로 그 이유로 TTFT/TPOT에서 PolyServe를 이긴다**고 서술한다
(그들 Figure 26·28).

> **논문에 쓸 문장**: "우리 포트는 논문의 부하 구배 규칙을 최저부하로 대체했다. 오토스케일링을
> 범위에서 제외했으므로 구배의 보상이 없기 때문이다. 독립적인 후속 연구가 이 구배 규칙이
> 지연 측면에서 손해라는 것을 측정했으므로(Simple is Better §6.2), 이 대체는 기준선을
> 약화시키지 않고 강화한다."

### 3.2 lazy promotion (§4.4)이 없다 (PolyServe에 **불리**)

논문 §4.4: 낮은 tier(느슨한 SLO)가 꽉 찼을 때만, 그 요청들이 더 빡빡한 tier의 서버를
점유할 수 있게 한다. eager promotion과 비교한 세 경우 논증이 논문에 있다.

**우리 `tierAffinityFilter`에는 promotion 경로가 없다.** `allows(tier, instanceID)`는
그 tier에 배정된 집합만 통과시키고, fallback에서도 안 풀린다.

**이것은 PolyServe의 이용률을 낮추는 방향의 결손이다.** 우리 설정에서 tier의 빡빡함
순서는 swe(25ms) < chat(50ms) < dr(100ms)이므로, promotion이 있으면 chat과 dr이 자기
서버가 꽉 찼을 때 swe의 2대를 빌릴 수 있다. 지금은 못 빌린다.

**고칠 것인가.** 두 선택지가 있고 어느 쪽이든 명시해야 한다.

1. **구현한다** — `tierAffinityFilter`에 "자기 tier의 모든 서버가 admission을 통과하지
   못했으면 더 빡빡한 tier의 서버도 후보에 넣는다"를 추가한다. 약 30줄. 다만 **"자기
   tier가 꽉 찼다"를 판정하려면 필터가 다른 인스턴스의 결과를 봐야 하는데, Llumnix의
   필터 인터페이스는 인스턴스 하나씩만 본다.** 2-pass 구조를 3-pass로 늘리거나
   selector 쪽으로 옮겨야 한다. 구조 변경이 생기므로 30줄보다 커질 수 있다.
2. **범위 밖으로 명시한다** — "lazy promotion은 오토스케일링 빈도를 줄이기 위한 메커니즘이고
   (논문 §4.4의 세 경우 논증이 전부 스케일업/다운 시점에 대한 것이다), 오토스케일링을
   제외한 우리 설정에서는 그 동기가 사라진다"고 적는다.

**2가 방어 가능하지만 완전하지는 않다.** promotion은 "자기 tier가 꽉 찼을 때 빌린다"는
이용률 메커니즘이고, 그 성질 자체는 fleet이 고정이어도 성립하기 때문이다. **선택은 사용자
몫이고, 어느 쪽이든 논문에 한 문장으로 적는다.**

### 3.3 DSLO를 채점에 쓰지 않는다 (PolyServe에 **불리**하되, 비교의 공정성 문제는 아니다)

논문의 SLO 정의는 누적 마감이다 — `i`번째 토큰이 `TTFT + i·TPOT` 이전에 나오면 된다.
그리고 이것은 **채점 규칙이면서 동시에 오차 흡수 메커니즘이다.** 논문 §4.5가 명시한다:
"prediction errors absorbed by the deadline-based SLO", "일부 iteration이 예측 오류로
TPOT을 넘어도 덜 붐비는 다른 사이클이 지연을 벌충할 수 있다."

우리는 순간 기준으로 채점한다(TTFT 임계 + 평균 TBT, swe는 e2e). 이유는 EXP-17의 5-arm과
직접 비교하기 위해서였다.

**이것은 PolyServe에게 불리하다.** 그러나 **비교의 공정성 문제는 아니다** — 네 arm을 전부
같은 규칙으로 채점하기 때문이다. 정확한 서술은 이것이다:

> 모든 정책을 하나의 채점 규칙으로 비교한다. PolyServe의 메커니즘들은 누적 마감 규칙을 전제로
> 설계되었으므로, 순간 기준 채점에서는 논문이 보고한 것보다 낮게 나온다. 이는 이식의
> 결함이 아니라 비교 설정의 한계이며, 누적 마감으로 채점하면 네 정책이 모두 올라간다.

### 3.4 iteration 시간 추정에 prefill 간섭항을 넣었다 (논문에는 없다)

**논문의 테이블에는 prefill 항이 없다.** §4.5가 그 이유를 명시한다.

> "iteration time은 GEMM과 collective communication(배치 크기에 의존), decode attention
> (KV 크기에 의존), prefill attention(prefill 길이에 의존)의 합이다. **시퀀스 길이가 10K
> 미만인 일반적인 워크로드에서는 chunked prefill을 고려할 때 prefill 시간이 나머지보다
> 훨씬 작다. 따라서 PolyServe는 배치 크기와 KV 크기만 고려한다.**"

**그 가정이 우리 워크로드에서 성립하지 않는다.** 우리 프롬프트는 chat 약 666 /
deepresearch 약 4,074~4,639 / swe 약 5,930 토큰이고(swe의 원래 transcript는 약 22k였다),
실측으로 **full chunk(8192) prefill 스텝이 520 ms인데 디코드 스텝은 17~113 ms다** — 5배에서
30배다. 그래서 prefill 항을 빼면 TBT 예측이 실제와 무관해진다.

그래서 `polyserveIterTime`에 항을 하나 더했다(`polyserve.go:139~154`): 대기 중인 prefill이
있으면 그 청크의 비용을 iteration에 더한다.

**논문이 이 문제를 다루지 않는 것은 아니다. 다른 자리에서 다르게 다룬다.** §4.7의
co-location 절이 "continuous chunked prefill prediction"으로, **청크 크기가 prefill이
끝날 때까지 유지될 수 있을 때만 수용한다**는 조건을 건다. 즉 논문은 iteration 추정을
prefill과 분리해 두고 별도 조건으로 막는 반면, 우리는 iteration 추정 안으로 접어 넣었다.

**방향은 보수적이지만, 그 결과가 §4다** — 대기 prefill이 있는 인스턴스는 `iterMax`가
수백 ms가 되어 25/50/100 ms 어느 tier에서도 통과하지 못하고, 전부 탈락하면 fallback이
admission을 푼다.

**⚠ 논문 문자 그대로(prefill 항 없이) 구현하면 admission이 더 관대해져서 실제로 무언가를
걸러냈을 수 있다. 재보지 않았다.** 이것은 §2의 출력 길이와는 성격이 다른 departure다 —
출력 길이는 명백한 오류이고, 이쪽은 근거 있는 선택이되 결과가 크다. **논문에 적을 때는
"우리 시퀀스 길이에서 논문의 prefill 무시 가정이 성립하지 않아 항을 추가했고, 그 결과
admission이 고부하에서 비활성화된다"까지 한 문장으로 적는다.**

---

## 4. 고부하에서 admission이 사실상 비활성이다 — 결함인가 발견인가

EXP-21 스모크에서 admission 거부 148건이 **전부** `iterMax > tpotSlo`(steady state)였다.
원인은 `iterMax`에 §4.7의 prefill 간섭항이 들어가기 때문이다 — 대기 중인 prefill이 있으면
전체 청크 비용(우리 실측 520~634 ms)이 더해져서 25/50/100 ms 어느 tier에서도 통과하지
못한다. 그리고 **모든 인스턴스가 탈락하면 fallback pass가 admission 필터를 풀어서 사실상
tier 안의 최저부하 선택으로 퇴화한다.**

**이것을 결함으로 볼 것인가.** 세 가지를 따져야 한다.

1. **논문의 해법은 동적 청킹이고 그것은 엔진 쪽 메커니즘이다.** 우리는 엔진을 stock FIFO로
   고정했다(라우팅 효과만 깨끗이 귀속하기 위해). 그러므로 이것은 **"co-location + 고정
   8192 청크에서는 PolyServe의 admission이 무력화된다"**는 조건부 관찰이지 이식의 실수가
   아니다.
2. **논문 자체에도 과부하에서 admission이 갈 곳이 없다.** PolyServe에는 drop 경로가 없고,
   과부하는 오토스케일링이 흡수한다. **fleet이 고정이면 admission은 필연적으로 퇴화한다** —
   거절할 수도 없고 서버를 늘릴 수도 없으면 남는 행동이 없기 때문이다.
3. **그래서 이것이 오히려 "isolation의 대표주자" 주장을 지지한다.** 우리 측정에서
   PolyServe의 결과를 만든 것은 admission이 아니라 파티션이다. EXP-21 기록에 그렇게 적혀
   있다: "admission은 고부하에서 사실상 무력화된 상태였는데도 위 결과가 나왔다. 이득의
   출처는 tier 파티션이다."

**결론: 발견이고, 논문에 그렇게 적을 수 있다.** 다만 **"PolyServe의 admission control이
작동하지 않는다"가 아니라 "고정 fleet · co-location · 고정 청크 조건에서 무력화된다"**로
범위를 좁혀야 한다.

---

## 5. 발표 전에 확인해야 할 것 하나 — migration이 tier 경계를 넘었는가

implementation.md §57.2가 기록한 미해결 항목이다.

> 스케줄러의 재배치 루프가 엔진의 `LLUMNIX_ENABLE_MIGRATION`과 무관하게 돌아서,
> migration을 끈 arm(**PolyServe, sweep 전체 90회**)에 재배치 쌍이 생기고 켠
> arm(Llumnix SLO, 0회)에는 안 생긴다. 그 결정이 실제 KV 전송이 됐는지는 확인되지 않았다.

**PolyServe에게는 이것이 다른 arm보다 심각하다.** 재배치가 실제 KV 전송이 되었다면 요청이
**tier 경계를 넘어 다른 tier의 서버로 옮겨졌을 수 있고, 그것은 isolation이 깨졌다는
뜻이다.** `tierAffinityFilter`는 **신규 dispatch만** 게이트하고 재배치 경로는 안 본다
(설계상 그렇다 — 재배정 비용 0을 위해 진행 중 요청은 건드리지 않는 전제였다).

**확인 방법**: `request_engine.csv`에서 한 요청이 두 개 이상의 엔진에 나타나는지를 보고,
나타난다면 그 두 엔진이 같은 tier에 배정되어 있었는지를 `scheduler_polyserve_tier_servers`
게이지와 대조한다. 재배치 90회가 전부 같은 tier 안이었으면 문제없고, tier를 넘었으면
**그 run의 격리 주장이 무효다.**

→ **이것을 확인하기 전에는 "PolyServe arm에서 클래스가 완전히 격리되었다"고 쓰면 안 된다.**

---

## 6. 그래서 "Isolation의 대표주자"로 세울 수 있는가

**세울 수 있다. 근거 넷.**

1. **대표하려는 성질이 우리가 가장 충실히 이식한 부분이다.** tier 파티션과 그것이 fallback에서
   풀리지 않는다는 것(§1.1). isolation은 바로 이 두 줄이다.
2. **그것이 작동했다는 것이 서로 독립인 두 계측으로 확인됐다** — 라우팅 집중도(chat 0.956 /
   dr 0.944 / swe 0.333 = 2/4 엔진의 이론값)와 엔진이 보고하는 prefix hit rate(chat 엔진
   94.5% 대 agent 엔진 66.7%).
3. **우리 측정에서 PolyServe의 결과를 만든 메커니즘이 실제로 파티션이다**(§4). admission이
   고부하에서 비활성이므로 남는 것이 파티션뿐이고, 그것이 EXP-21에서 엔진 스케줄러 5종을
   압도했으며(등가중 30 req/s에서 PolyServe 69.5 대 QoServe 46.1 대 FIFO 22.5) EXP-53에서
   FluidServe에 졌다.
4. **논문 자신의 지연 측면 약점(부하 구배)을 우리가 제거했다**(§3.1). 즉 **isolation을
   가장 유리한 조건에서 대표시키고 있다.**

**단, 이름과 주장 범위를 이렇게 좁혀야 한다.**

> ⚠ **아래 PolyServe 값은 EXP-57 이전(길이 프로파일 수정 전)이다.** 정본은 `ms_dev/notes/numbers.tsv`의 `exp57.*.polyserve.*`이고, 낡은 값 목록은 `ms_dev/notes/retracted.tsv`다. 표 전체를 다시 만들기 전까지 이 열은 인용하지 않는다.

| 쓰면 안 되는 것 | 써야 하는 것 |
|---|---|
| "PolyServe" | **"고정 fleet 위의 정적 클래스 파티션 (PolyServe §4.2의 이식)"** |
| "PolyServe를 재현했다" | "PolyServe의 tier 파티션과 세 admission 검사를 이식했고, 오토스케일링·lazy promotion·PD-분리·DSLO 채점은 제외했다" |
| "PolyServe는 45 req/s에서 27.5점이다" | "정적 클래스 파티션은 고정 fleet 4대·co-location·순간 기준 채점에서 45 req/s에 27.5점이다" |

**그리고 §2의 출력 길이를 고치기 전의 수치는 인용하지 않는다.**

---

## 7. 할 일 (우선순위 순)

| # | 할 일 | 비용 | 왜 |
|---|---|---|---|
| **1** | **`set_scheduler_profiling.py`의 tier decode tokens를 `25:494,50:428,100:985`로 고치고 EXP-53의 PolyServe arm을 다시 돌린다** | 코드 0줄. sweep 8 rate × 2반복 = 16조건, 약 3시간 | **§2. 고치지 않으면 수정된 시스템 대 수정되지 않은 기준선의 비교다** |
| **2** | 재분할 배분이 (2/1/1)에서 바뀌는지 확인한다 | 1의 부산물. 스케줄러 로그의 `PolyServe repartition:` 줄 | **배분이 곧 isolation이므로, 바뀌면 §1.1의 집중도 수치도 다시 재야 한다** |
| **3** | 재배치 90회가 tier를 넘었는지 확인한다 | 분석 스크립트. `request_engine.csv` + `scheduler_polyserve_tier_servers` | **§5. 넘었으면 격리 주장이 무효다** |
| 4 | tier decode tokens를 `fluidserve.json`에서 읽도록 배관을 바꾼다 | `set_scheduler_profiling.py` 수정 | 재발 방지. 지금은 같은 값이 두 곳에 따로 있다 |
| 5 | lazy promotion을 구현하거나 범위 밖으로 명시한다 | 구현 시 필터 인터페이스 변경 필요 | **§3.2. 지금은 PolyServe에 불리한 결손이 하나 남아 있다** |

**1과 2는 논문에 표를 싣기 전 필수다. 3은 격리를 주장하기 전 필수다.**

---

## 8. 관련 문서

| | |
|---|---|
| [POLYSERVE_DESIGN_KO.md](../../POLYSERVE_DESIGN_KO.md) | 이식 설계의 정본. §0의 결정표, §1의 논문→구현 매핑, §4의 범위 밖 항목 |
| [POLYSERVE_PROGRESS.md](../../POLYSERVE_PROGRESS.md) | 시간순 기록. §4.6이 죽은 코드였던 것, `ttft.json` 재측정, EXP-21 결과 |
| [qoserve-niyama-fidelity.md](qoserve-niyama-fidelity.md) | 같은 형식의 QoServe 이식 대조. §2의 엔진 큐 깊이 표 |
| [related-works-review.md](related-works-review.md) | 일곱 편 검토. **§8.3이 "Simple is Better"의 PolyServe 재구현 비교**(§3.1의 근거), §4.2가 PolyServe 리뷰 |
| [deploy/profiling/README.md](../../deploy/profiling/README.md) | `ttft.json`·`tpot.json`의 출처와 신뢰도 |
| implementation.md §57 | EXP-53 네 정책 sweep. §57.2가 migration 미해결 항목 |
| `Agent_applications/.../experiments/EXP-21_polyserve-routing.md` | PolyServe가 엔진 스케줄러 5종을 압도한 실험의 정본 |
| `related_works/[arxiv] PolyServe*.pdf` | 원문 |

---

## 9. 2026-08-27 — 논문의 메커니즘 여섯을 되살렸다 (구현 완료, 미측정)

**⚠ 이 절이 §3.1·§3.2·§4를 대체한다.** 그 세 절은 "왜 다르게 했는가"의 기록으로 남기되,
**지금 코드의 동작을 설명하는 절로 읽으면 안 된다.** 무엇이 바뀌었는지는 아래 표에 있다.

**아직 아무것도 측정하지 않았다.** 여섯 스위치의 기본값이 전부 종전 동작이므로,
스위치를 명시하지 않은 실행은 2026-08-27 이전의 모든 PolyServe 조건과 같은 결정을 내린다.
사전 등록한 판정 규칙은 `experiments/EXP-106_polyserve-paper-fidelity.md`에 있다.

### 9.1 무엇이 잘못돼 있었나 — 결함 둘이 서로를 가리고 있었다

**admission 검사 셋(§4.5·§4.6)이 결정을 하나도 바꾸지 못했다.** 원인이 둘이고, 둘이 동시에
작동해서 어느 쪽도 증상으로 드러나지 않았다.

1. **`polyserveAdmissionFilter.skipWhenFallback()`이 true였다.** Llumnix는 1차 필터에서
   남는 인스턴스가 0개면 relaxable 필터를 전부 빼고 2차 pass를 돈다. admission이 relaxable로
   등록돼 있었으므로, **아무 서버도 통과하지 못하면 요청은 거절되는 것이 아니라 자기 tier
   안에서 가장 덜 찬 서버로 그냥 배치됐다.** §1.1은 이것을 "논문에 drop 경로가 없으니 충실"
   이라고 적었는데, 논문에 없는 것은 **거절**이지 **보류**가 아니다 — §4.3이 "requests start
   pending for one SLO tier"라고 적고 §4.6이 "time in the pending queue (pending time)"을
   TTFT 회계에 넣는다. 보류가 논문의 메커니즘인데 우리는 그것을 강제 배치로 바꿔 놓았다.
2. **iteration 추정에 대기 prefill 청크 비용이 들어갔다.** §4.5는 반대로 적는다 —
   *"PolyServe only considers batch size and KV cache size"* — 그리고 prefill은 §4.7에서
   TTFT 쪽으로 따로 다룬다. 우리는 full chunk(8,192 토큰) 비용 520~634 ms를 두 추정 모두에
   더했고, tier 예산이 25/50/100 ms이므로 **대기 중인 prefill이 조금이라도 있으면 어느
   서버도 통과할 수 없었다.**

**둘을 합치면 지나치게 엄격해서 아무도 통과하지 못하고, 아무도 통과하지 못하니까 필터가
통째로 꺼진다.** 그래서 EXP-71 한 시간 trace에서 이 arm의 거절률이 **0.0%**이고, 같은 run에서
총 출력이 11,145 tok/s로 FluidServe의 11,501의 **96.9%**인데 offered 달성률은 **11.2점**이다.
토큰은 거의 같은 양을 만드는데 예산 안에 도착하는 것이 없다.

**그 위에 세 가지가 더 없거나 반대였다.** lazy promotion(§4.4)이 없고, tier 안 선택이 논문의
"가장 부하 높은 서버"의 반대이며, 재분할이 논문에 없는 수요 모형(도착률 × 프로파일 비용)으로
돌아간다. 그리고 **KV 용량을 보는 판정 조건이 아예 없어서** 함대가 용량을 넘겨 구동되고
vLLM이 recompute preemption으로 수습한다(실측: 3,000 rpm 8분에 549회).

### 9.2 무엇을 바꿨나

| 스위치 (기본값 = 종전 동작) | 논문 | 무엇이 달라지나 |
|---|---|---|
| `--polyserve-admission-binds` | §4.3·§4.6 | 아무 서버도 받아들이지 않으면 강제 배치하지 않는다. 요청은 게이트웨이에서 보류되고(= 논문의 pending queue), **자기 TTFT 예산이 아무리 기다려도 못 맞추는 상태가 되면** 거절된다 |
| `--polyserve-steady-state-ignores-prefill` | §4.5 | 정상상태 추정에서 prefill 항을 뺀다. 근거리 추정(§4.6의 wait time)에는 남긴다 — co-location에서 "서버가 지금 iteration을 끝내기를 기다리는 시간"이 바로 그 청크이기 때문 |
| `--polyserve-lazy-promotion` | §4.4 | 자기 tier의 모든 서버가 거부했을 때만 **더 빡빡한 tier**의 서버에 시도한다. **손님은 호스트 tier의 예산으로 판정한다** |
| `--polyserve-partition=elastic` | §4.3 | 수요 계산을 없애고 idle pool로 바꾼다. **보류가 생기면 +1, 그 tier의 마지막 서버가 비면 −1** |
| `--polyserve-prefer-loaded` | §4.3 | admission을 통과한 것 중 **가장 부하가 높은** 서버를 고른다 |
| `--polyserve-kv-admission` | (논문 시뮬레이터가 모델링하는 것) | §4.5가 이미 계산하는 최대 KV를 인스턴스가 보고하는 총 KV 용량과 비교한다. **고를 문턱값이 없다** |

**구조도 바뀌었다.** tier affinity와 admission을 필터에서 빼고 **결정 사다리를 selector 안으로
옮겼다**(`polyserveSelector`). 필터로는 표현할 수 없기 때문이다 — 사다리의 네 단 중 셋이
**"이 tier의 모든 서버가 거부했다"**는 tier 전체에 대한 사실을 필요로 하는데
`singleInstanceFilter`는 인스턴스를 하나씩만 본다. Llumnix의 2-pass가 그 자리를 대신하고
있었고, 그러면서 admission을 모든 결정에서 없앴다. FluidServe가 같은 이유로 이미 같은 모양을
쓴다.

**사다리는 논문 Figure 5의 순서다**: ③ 자기 tier 안에서 greedy → ④ promotion → ⑤ pool에서
scale-up → 그밖에는 pending queue.

### 9.3 두 스위치가 서로 의존한다

**`elastic`은 `prefer-loaded` 없이 쓰면 동작하지 않는다.** 논문이 서버를 tier에서 빼는 조건이
*"마지막 서버에 그 tier의 요청이 하나도 없다"* 하나뿐인데, 가장 부하가 **낮은** 서버를 고르면
모든 서버가 항상 조금씩 차 있어서 그 상태가 만들어지지 않는다. 그러면 배분은 처음 도달한 값에
굳고 −1이 한 번도 일어나지 않는다. **치명적이지는 않아서(그 조합도 정당한 ablation이다)
기동 시 경고만 찍고 진행한다** — 결과를 idle pool에 대한 증거로 읽지 않게 하기 위해서다.

§3.1이 "오토스케일링을 제외했으므로 부하 순서 규칙의 보상이 0이라 뒤집었다"고 적었는데,
**오토스케일링을 되살리면 그 정당화가 사라진다.**

### 9.4 우리 규모에서 예상되는 것 — 서버 4대에 tier 3개

**pool은 실행 시작 몇 초 만에 비고 그 뒤로는 대부분 0대다.** 그러므로 이 규모에서 이용률을
만드는 것은 오토스케일링이 아니라 **promotion**이다. 논문은 수십~수백 대를 시뮬레이션하므로
이 비율이 반대이고, 이것은 논문에 범위로 적어야 한다.

**그리고 규모 때문에 논문에 없는 규칙을 하나 넣었다**: 스케줄러가 본 적 있는 tier가 서버를
0대 들고 있으면 가장 많이 든 tier에서 한 대를 가져온다. 넣지 않으면 시작 몇 초의 순서로
진 tier가 **남은 한 시간 내내 모든 요청을 보류**한다. 논문은 pool이 제약이 되지 않는 규모라
이 규칙이 필요 없다. **논문에 명시해야 하는 이탈이다.**

### 9.5 swe의 예산이 틀려 있었다 — 이것이 admission보다 먼저다

**PolyServe는 swe를 토큰당 25 ms로 판정하고 있었다.** swe의 진짜 목표는 전체 시간 30초이고
채점도 항상 그렇게 한다. 25는 `mix_short_m1_balanced.json`의 `slo.swe.tbt_ms`에 적힌 값으로
**EXP-16에서 잰 유휴 상태 디코드 ITL 중앙값**이고, FluidServe는 그것을 tier 이름으로만 쓰고
`--fluidserve-class-budgets 25:e2e:30000`으로 예산을 다시 정의한다. **PolyServe에는 그
재정의가 없어서 곧이곧대로 읽었다.**

프로파일 표(`deploy/profiling/llama31-70b-b200-tp2/tpot.json`)를 직접 풀면 그 차이가 나온다.
swe의 요청당 KV는 입력 6,812 + 출력 494 = **7,306 토큰**이다.

| 주는 예산 | 그 예산을 지키며 담을 수 있는 최대 배치 |
|---|---|
| **25 ms (지금까지)** | **23** |
| 52 ms (`mix_short_m1_slofair.json`의 값) | 163 |
| (30,000 − 2,500) / 494 = 55.7 ms | 186 |
| (40,000 − 2,500) / 494 = 75.9 ms | 260 |

**7~9배다.** admission이 fallback에서 풀려 있었기 때문에 지금까지는 결과에 나타나지 않았지만,
구속력을 갖게 하는 순간 이 값이 그대로 swe의 수용량이 된다. **고치지 않고 `admission-binds`를
켜면 "수정된 우리 시스템 대 잘못된 예산을 받은 기준선"을 비교하게 된다.**

**고치는 방법은 저장소에 이미 있다.** `mix_short_m1_slofair.json`이 정확히 이 상황을 위해
만들어진 파일이고(그 파일의 `_comment`가 그렇게 적는다), **Llumnix SLO는 이미 그것으로 돈다.**
PolyServe만 balanced로 남아 있었다.

**그리고 그 결과가 그 자체로 결과다.** m1f에서 tier가 **chat 50 / swe 52 / dr 100**이 된다.
**PolyServe는 토큰당 예산으로 요청을 나누는 시스템인데, 전체 시간 예산을 가진 클래스를 속도로
환산하면 그 값이 chat 위에 겹친다.** 즉 이 워크로드의 세 클래스를 PolyServe의 분류 축으로는
셋으로 나눌 수 없다. 이식의 결함이 아니라 그 설계의 성질이므로 그대로 측정해서 적는다.

**재발 방지**: tier 표를 워크로드 설정의 `slo.<class>.tbt_ms`에서 유도하도록 바꿨다
(`set_scheduler_profiling.py`의 `tier_by_class()`). 그 필드가 클라이언트가 요청에 실어 보내는
값이고 스케줄러가 binning하는 값이므로, **같은 양이 두 곳에 따로 적혀 한쪽만 갱신되는 것**을
막는다. EXP-105가 swe를 40초로 올리면 그 파일 하나만 고치면 tier도 같이 움직인다.

### 9.6 새로 나오는 계측

| 시리즈 | 무엇 |
|---|---|
| `scheduler_polyserve_placement_total{stage}` | 요청이 사다리의 어느 단에서 처리됐나 — `own_tier` / `promotion` / `scale_up` / `forced` / `pending` / `refused` |
| `scheduler_polyserve_refused_total{stage,reason}` | 서버가 왜 거부했나 — `first_token` / `second_token` / `steady_state` / `memory` |
| `scheduler_polyserve_scale_total{event}` | `from_pool` / `from_tier` / `to_pool` |
| `scheduler_polyserve_pool_servers` | idle pool의 서버 수 |

**이것 없이는 admission이 한 번도 구속하지 않은 run과 구속했는데 promotion이 전부 흡수한 run이
요청 단위 지표에서 완전히 같아 보인다.**

⚠ **Go에 메트릭을 추가하는 것만으로는 수집되지 않는다** — `llumnix_metrics.py`의 시리즈 이름
목록에도 넣어야 한다(EXP-96에서 이것 때문에 카운터 한 벌을 통째로 잃었다). 그 편집은
`/home/nxclab/tools/staging/polyserve-paper/`에 대기 중이고, EXP-105가 끝난 뒤 `apply.sh`가
넣는다.

### 9.8 측정 결과 — 여섯 중 둘이 결정을 거의 바꾸지 않는다 (2026-09-01, EXP-108)

**§9.2가 "논문의 메커니즘 여섯을 되살렸다"고 적었고 그것은 맞다 — 코드는 돌고 카운터도
나온다. 그러나 그중 둘은 켜져 있어도 결정을 거의 내리지 않는다.** EXP-108의 16조건
(2반복 × 여덟 도착률, 워크로드는 per-token 형태의 `t75fair`)에서 잰 값이다.

**작동을 확인한 것 넷**:

- **admission이 구속한다** — `placement_total{stage="forced"}`가 **16/16 조건에서 0**이다.
  §9.1이 적은 결함(아무도 통과 못 하면 거절이 아니라 tier 안 최저부하로 강제 배치)이
  없어졌다.
- **거절이 생긴다** — 35 req/s 이상에서 472~13,245건. 종전 이식은 여덟 도착률 전부 0.0%였다.
- **promotion이 이용률을 만든다** — 16/16에서 `promotion` > `scale_up`이고 비율이 **50~380배**
  다(10 req/s에서 768 대 3). §9.4가 우리 규모에서 그럴 것이라고 적은 그대로다.
- **KV 판정이 거부를 지배한다** — 16/16에서 과반, 79.9~100%.

**결정을 거의 바꾸지 않는 것 둘**:

- **§4.6의 첫 토큰·둘째 토큰 검사.** 45~70 req/s에서 거부 사유의 분포가 memory 79.9~84.6%,
  steady_state(§4.5) 15.0~20.1%, **first_token 0.0~0.3%, second_token 0.0~0.2%** 다. 30만 건
  중 수백 건이다. **논문이 admission의 핵심으로 제시하는 시간 예산 검사가 이 워크로드에서는
  발화하지 않고, 실질적으로 결정을 내리는 것은 논문 본문에 없고 시뮬레이터가 모델링하는 KV
  용량 판정이다.** 이식의 결함이 아니라 측정 결과로 읽는다 — 이 함대에서는 서버가 시간 예산을
  어기기 전에 KV가 먼저 찬다.
- **§4.3의 elastic scale-up.** 16조건 전부에서 `scale_up`이 **2~6건**, 8분에 다섯 번이다.
  구현돼 있고 켜져 있지만 우리 규모에서는 거의 발화하지 않는다.

**§9.4의 예상 하나는 부분 반증됐다.** "pool은 실행 시작 몇 초 만에 비고 그 뒤로는 대부분
0대"라고 적었는데, 20 req/s 이상 12조건은 중앙값 0이 맞지만 **10·15 req/s 네 조건은 1~2대가
남는다.** 그 조건에서 엔진 하나가 8분 내내 토큰을 0개 만들었다.
**그리고 이것은 우리 주장의 직접 증거이기도 하다** — 부하는 chat tier에 있는데 서버는 pool에
있다. 정적 클래스 파티션이 용량을 옮기지 못한다는 것을 그 정책 자신의 카운터가 보고한다.

**논문에 적을 때의 문장**: "PolyServe에 논문의 메커니즘을 전부 주었다"가 아니라 **"논문의
메커니즘 여섯을 구현해 켰고, 그중 admission의 구속력·정상상태 추정·promotion·KV 판정 넷이
결정을 내렸으며, §4.6의 두 검사와 §4.3의 elastic scale-up은 이 규모·이 워크로드에서 거의
발화하지 않았다"**로 적는다.

---

### 9.7 §7의 할 일 표는 이렇게 바뀐다

1·2·4번(tier decode tokens를 프로파일에서 유도)은 2026-08-05에 끝났다. 3번(재배치가 tier를
넘었는가)은 그대로 남아 있다. **5번(lazy promotion)은 이 절이 구현으로 닫았고, 대신 §9.4의
"서버를 0대 든 tier에 한 대를 준다"는 규칙이 새 이탈로 추가됐다.**
