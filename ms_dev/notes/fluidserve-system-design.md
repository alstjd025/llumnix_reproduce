# FluidServe 시스템 설계 완전판 — 코드 기준 참조 문서 (v3)

**2026-08-26 개정, FluidServe v0.3 기준.** 배포 바이너리 **`d867351a`**
(`bin/scheduler-exp07`, 파드 안 `md5sum /proc/1/exe`로 검증)의 소스를 직접 읽고 만들었고,
수식과 수도코드는 코드를 그대로 옮긴 것이다. **코드와 어긋나면 코드가 맞다.**

**이 문서의 용도**: 논문 design 섹션의 재료. 논문에 다 들어가지는 않지만, **논문이 필요로
할 만한 모든 것이 여기 있어야 한다** — 변수·상수·상태 구조·입출력·수식·통합 지점·코드량,
그리고 **각 선택을 정하게 만든 측정과, 검토했다가 버린 대안**. 결론만 적힌 곳이 있으면
그것은 이 문서의 결함이다.

다른 문서와의 관계: 버전별 명세 [fluidserve-v0.3.md](fluidserve-v0.3.md)(무엇이 언제
바뀌었나), 설계 서사 [fluidserve-how-it-works.md](fluidserve-how-it-works.md)(처음
이해할 때), 사전 등록 [fluidserve-v0.1.md](fluidserve-v0.1.md)(무엇을 미리 정했나).
**이 문서만이 전부를 한 곳에 적는다.**

표기: `req.*`는 도착 요청, `f.*`는 인스턴스 상태(flux), `c.*`는 후보 평가 결과,
`r.*`는 상주 요청(live). 파일:행은 전부 실제 위치.

**v3.2(2026-08-28)에서 바뀐 것**: §1에 **일곱 번째 핵심 개념**(예측 거리를 결정이 필요로
하는 만큼만 잡는다)이 생겼다. 코드에 있는 세 거리, 거리를 정한 이유가 정확도만이 아니라
셋이라는 것, 먼 거리에서 분포를 쓰는 이유, 그리고 **"먼 예측이 부정확해서 비용이 난다"를
EXP-64·EXP-90이 반증한다는 것**(총계는 안 움직이고 클래스 배분이 움직인다). 그 절이
**빈칸 둘**을 스스로 적어 둔다 — v0.3에서 재측정되지 않은 KV 투영 오차, 그리고 EXP-64가
물었던 통로가 v0.3에서 굵어졌다는 것.

**v3.1(2026-08-27)에서 바뀐 것**: **§0이 새로 생겼다** — 이 문서를 실제로 읽으며 나온 질문
다섯을 수식 없이 먼저 답하는 절이고, 상세 절로 가는 안내다. 그리고 그 질문들이 가리킨 자리
넷을 보강했다: §4.2에 **두 제외 규칙의 범위가 다른 이유**, §4.4에 **`unpredictable`의 정체와
비교 두 변의 비대칭**, §4.6에 **첫토큰 마감 검사가 3단에서는 지금도 돈다는 것**, §9.1에
**같은 양을 건드린 두 변경의 이름을 가르는 표**.

**v2(2026-08-20)에서 바뀐 것**: §1에 개념 하나 추가(모형의 두 절반이 같은 엔진을 같은
방식으로 봐야 한다), §2.3·§4.2·§4.4·§4.5에 v0.3의 네 변경, §4.6에 **`canWait`가 쓰는
큐 지연이 관측이 아니라 모델이라는 발견**, §4.7에 배치별 예측 로그, §5에 새 시리즈 일곱,
§7 플래그 23 → 30개, §9 전면 개정(옛 §9.1이 두 번 측정되어 답이 나왔다).

---

## §0 먼저 읽는 절 — 결정 하나를 높은 수준에서

**이 절에는 수식이 없다.** 아래 §1~§9가 코드를 그대로 옮긴 참조이고, 이 절은 그것을 읽기
전에 **무엇이 무엇을 지키는지**를 잡아 두기 위한 것이다. 각 항목 끝에 상세 절을 가리킨다.

이 절은 2026-08-27에 이 문서를 실제로 읽으면서 나온 질문들로 만들었다. **되물어야 했던
자리가 곧 이 문서가 설명을 빠뜨린 자리**이므로, 그 질문들을 그대로 §0.6에 남겨 둔다.

### 0.1 요청 하나가 도착하면 무슨 일이 일어나는가

스케줄러는 **인스턴스마다 "이 요청을 여기 놓으면 어떻게 되는가"를 따로 계산하고**, 그
결과를 모아 **네 갈래 중 하나**를 고른다. 갈래의 순서가 곧 정책의 우선순위다.

1. **route** — 조건을 전부 만족하는 인스턴스가 있으면 그중 하나에 배치한다.
2. **pend** — 아무 데도 없지만 **이 요청의 예산에 아직 기다릴 시간이 남았으면** 게이트웨이가
   붙들고 있게 하고, 잠시 뒤 다시 묻는다.
3. **shed** — 기다릴 시간도 없고 **어디에 놓아도 자기 예산을 못 지키면** 거절한다.
4. **force** — 기다릴 수는 없지만 자기 예산은 지킬 수 있으면, **피해가 가장 작은 곳**에
   억지로 놓는다.

**이 순서가 "routing과 admission이 같은 결정"이라는 말의 실체다.** 거절은 별도의 관문이
아니라 **배치할 곳을 찾는 데 실패한 결과**이고, 같은 후보 평가를 읽는다. → §4.6

### 0.2 인스턴스 하나를 받아들일지 정하는 조건은 다섯이고, 각각 다른 것을 지킨다

`feasible`은 **다섯 조건이 전부 만족되어야 참**인 논리곱이다. 다섯이 서로 대체하지 않는다.

| 조건 | 무엇을 지키나 | 한 문장 |
|---|---|---|
| `unpredictable` | **모름** | 이 인스턴스의 지금 상태에 대해 성능 표가 값을 못 주면 받지 않는다 |
| `overGate` | **약속** | 이 인스턴스 위 요청들이 **약속받은** 토큰당 속도를 넘기지 않는다 |
| `overIncumbents` | **잔액** | 이미 받아들인 요청들이 **아직 지킬 수 있는** 속도를 넘기지 않는다 |
| `overMemory` | **공간** | 이 배치가 KV에 들어가는가 |
| `overDeadline` | **이 요청의 첫 토큰** | 앞에 쌓인 prefill 줄을 감안해도 첫 토큰이 제때 나오는가 (**기본 꺼짐**, §0.6) |

**`overGate`와 `overIncumbents`는 겹치는 조건이 아니다.** 하나는 클래스가 처음 약속받은
값을, 다른 하나는 그 요청이 지금까지 얼마나 잘 달렸는지에 따라 남은 값을 본다. 실측에서
어느 쪽이 더 빡빡한지가 상황에 따라 바뀐다 — 어떤 구간에서는 약속이 50.0인데 잔액은
68.3~72.7이었고(약속이 이겼다), 반대 경우도 있다. → §4.4

### 0.3 두 예산 — "약속"과 "잔액"

이 문서에서 가장 헷갈리는 자리다. **인스턴스마다 예산이 두 개** 있고, 둘 다 **그 인스턴스가
지금 들고 있는 요청들에서** 만들어진다.

| | 무엇의 최솟값인가 | 요청이 뒤처지면 |
|---|---|---|
| **약속** (`gateAllowance`) | 상주 요청들이 **클래스로서 약속받은** 토큰당 예산 (chat 50 ms, dr 100 ms) | **안 움직인다** |
| **잔액** (`tightestAllowance`) | 상주 요청들이 **지금 남은 예산 ÷ 앞으로 만들 토큰 수** | **움직인다** |

**약속이 안 움직이는 것은 되먹임을 끊기 위해서다.** 뒤처진 요청의 명목 예산을 따라 내리면,
뒤처짐이 인스턴스의 게이트를 낮추고, 낮아진 게이트가 더 많은 요청을 막고, 그래서 더
뒤처진다. **잔액만 움직이고 약속은 고정이다.**

**"잔액"은 속도 측정이 아니라 예산 회계다.** *이 요청이 약속받은 총 시간 가운데 아직 안 쓴
것을, 앞으로 만들 것으로 기대되는 토큰 수로 나눈 값*이다. 그러므로 **잔액이 크다 = 지금까지
약속보다 빨리 달렸다 = 앞으로 좀 느려도 된다**이고, 작으면 반대다.

⚠ **"앞으로 만들 토큰 수"는 planning horizon이 아니라 그 요청의 기대 잔여 길이 전체다.**
100 iteration이라는 값이 이 시스템에 있지만 잔액 계산에는 안 들어간다(§0.5). → §4.3

⚠ **클래스마다 시계가 다르다.** 토큰당 예산 클래스(chat, dr)는 **첫 토큰이 나온 시각**부터
쓴 시간을 세고, 전체 시간 예산 클래스(swe)는 **도착한 시각**부터 센다 — 후자는 게이트웨이
대기와 prefill이 같은 계좌에서 나간다. → §4.3

### 0.4 상주 요청은 세 상태로 갈린다 — "느린 것"과 "불가능한 것"은 다르다

잔액이 작다고 다 같은 상태가 아니다. **인스턴스를 완전히 비워도 못 맞추는 요청**과 **지금
이 배치에서 뒤처지고 있을 뿐인 요청**은 정책이 다르게 다뤄야 한다. 코드가 셋으로 나눈다.

| 상태 | 무엇인가 | **약속**에 들어가나 | **잔액**에 들어가나 |
|---|---|---|---|
| **① 달성 불가** | 남은 잔액이 **텅 빈 인스턴스의 iteration 하나 비용**보다도 작다. 어떤 배치 구성으로도 못 맞춘다 | **아니오** | **아니오** |
| **② 이미 뒤처짐** | 원리적으로는 가능한데 **이 인스턴스가 지금 내는 속도**로는 못 맞추고 있다 | **예** | **아니오** |
| **③ 정상** | 지금 속도로 예산 안에 있다 | 예 | 예 |

**①을 빼는 이유**: 그 요청을 잔액에 넣으면 인스턴스가 **모든 클래스에 대해 영원히 닫힌다.**
그 요청은 어차피 못 구하는데 다른 요청들만 못 받게 된다.

**②를 빼는 이유는 다르다**: 새 도착을 거절해도 그 요청은 안 구해진다. 배치는 지금 속도로
계속 돌고 상주 요청이 끝나야만 줄어들기 때문이다. 실측으로, 이 규칙이 없을 때 인스턴스가
**잔액 28 ms에 실제 전달 32 ms인 상태로 앉아 수용 가능한 점유의 3분의 1만 들고 일을
거절했다.**

**⚠ ②가 약속에는 계속 들어가는 것이 중요하다.** 뒤처진 chat 요청은 *"내 남은 예산으로 남을
막을"* 권한은 잃지만, *"이 인스턴스는 chat이 있는 곳"*이라는 표시는 유지한다. 약속은 그
요청이 얼마나 뒤처졌는지와 무관한 **클래스의 성질**이기 때문이다. → §4.2

### 0.5 무엇이 "새 요청이 들어간 뒤"이고 무엇이 "지금"인가

판정은 **두 종류의 양을 비교**하는데, 한쪽에만 도착한 요청이 들어 있다. 이것을 섞으면
판정 조건을 잘못 읽게 된다.

| | 도착 요청이 들어 있나 | 100 iteration이 들어가나 |
|---|---|---|
| **약속·잔액** (인스턴스 쪽 문턱) | **아니오.** 지금 그 인스턴스에 있는 요청들만 | **아니오** |
| **배치 뒤 예상 속도** (`meanAfter`) | **예.** 디코드 배치 +1, prefill 큐에 이 프롬프트, KV에 이 요청 | **예** |

> **판정을 문장으로 읽으면**: *"지금 여기 있는 요청 중 가장 여유 없는 것이 토큰당 40 ms를
> 남겼는데, 이 요청을 얹으면 이 인스턴스가 **앞으로 100 iteration 동안** 토큰당 평균 몇
> ms를 낼 것인가. 그게 40을 넘으면 아직 살릴 수 있는 요청을 내가 죽이는 것이므로 안 받는다."*

**왜 거리를 이렇게 나눴는지는 §1의 일곱 번째 개념에 있다** — 그리고 거기에 측정 하나가
붙어 있다: **먼 거리 예측을 정확하게 만들어도 총계는 안 움직이고, 대신 클래스 사이의 배분이
움직인다.**

**100 iteration(planning horizon)이 들어가는 곳은 둘뿐이고 둘 다 오른쪽 열이다**:
① 배치 뒤 예상 속도는 **앞으로 100 step의 평균**이고(그중 몇 step이 prefill 청크를 싣는지가
그 평균을 정한다), ② 비교에 쓰는 KV도 **지금 점유가 아니라 100 step 뒤의 투영 점유**다.
**잔액에는 안 들어간다.** → §4.2, §4.4

**⚠ 비대칭 하나**: 도착 요청은 예상 속도에는 들어가지만 잔액에는 안 들어간다. 그래서
**도착 요청 자신이 자기 잔액을 못 지키는지는 이 조건이 안 본다.** 그것은 게이트가 명목
기준으로 보고, 잔액 기준으로는 사다리 3단(shed)의 검사가 본다. → §4.6

### 0.6 실제로 되물은 것 다섯

**Q. 잔액 최솟값 하나만 본다는 것이, 약속과 잔액 중 작은 쪽을 취한다는 뜻인가?**
아니다. **두 조건은 따로 계산되어 논리곱으로 묶인다.** 결과만 보면 둘 중 작은 쪽이
구속하지만, 하나로 합치면 안 되는 이유가 둘이다 — **여유 계수 0.90이 약속 쪽에만 붙고**(약속이 50 ms면
게이트는 45.0이다 — 같은 양이 아니다), **어느 쪽이 막았는지를 따로 세기 때문이다**(그 계수가
실제로 게이트가 25~70 req/s에서 71~82%, 메모리가 **35 req/s 위에서** 17~20%라는 것을 알려
주었다).
그리고 "최솟값 하나"라는 말은 **요청마다 여유를 계산해 부호를 보는 것이 아니라, 상주
요청들의 잔액 중 최소를 뽑아 한 번 비교한다**는 뜻이다. 요청별 여유를 실제로 계산하는 곳은
판정이 아니라 **정렬**(harm)이다. → §4.4, §4.5

**Q. `unpredictable`은 무엇이고, 처음 보는 클래스는 거절되는가?**
**성능 표 조회가 실패한 경우**다. 이 인스턴스의 지금 prefill 큐 크기에 대해 prefill step
비용을 못 얻으면 값이 무한대가 되고, 그 인스턴스는 후보에서 빠진다. 실패했을 때 디코드
바닥값으로 대신하면 **prefill step 비용을 한 자릿수 과소평가**하게 되므로, 추정하는 대신
거부한다 — **모르는 것을 괜찮다고 하지 않겠다**는 조건이다.
**요청의 클래스와는 무관하고, 처음 보는 클래스는 거절되지 않는다.** 프로파일에 없는 tier는
**tier 키 숫자 자체를 토큰당 ms 예산으로 읽고**, 길이 분포는 fallback을 쓴다. → §4.4

**Q. 첫 토큰 마감 조건이 꺼져 있다는 것이, 그 검사를 안 한다는 뜻인가?**
아니다. **그 검사는 지금도 사다리 3단(shed)에서 돈다.** 꺼져 있는 것은 **그것을 1단(route)의
판정 조건에 넣는 것**이다. 즉 지금 정책은 *"첫 토큰이 늦을 것 같다"를 이유로 배치를 막지는
않지만, 거절할지 정할 때는 본다.* → §4.6, §9.1

**Q. 그런데 첫 토큰 쪽을 고쳐서 deepresearch가 크게 좋아지지 않았나?**
좋아졌다. **다만 그것은 다른 변경이다.** 같은 양에 대해 두 가지를 했고 결과가 반대였다.
- **추정값 자체를 고친 것** — prefill 대기 항을 엔진이 실제로 prefill에 쓰는 시간 비율로
  나눈다. **채택했고 v0.3 기본값이다.** deepresearch admitted 83.7 → 96.5/94.7.
- **그 추정을 1단 판정에 넣은 것** — **반증됐고 기본값 꺼짐이다.**
그리고 채택된 쪽의 **효과 경로도 판정이 아니었다** — 보류가 55.0% → 48.8%/44.8%(두 반복)로
줄어 **큐가 쌓일 시간을 안 준 것**이 실제 메커니즘이다. "실제로 늦은 것 중 결정이 늦을
것으로 본 비율"은 0.0% → 1.1%/2.2%로 거의 그대로다. → §4.6의 표, §9.1

**Q. 이 시스템이 스스로 관측하지 못하는 것이 있는가?**
있고, 그것이 이 문서에서 가장 조심해야 하는 대목이다. **엔진은 요청별 진행을 보고하지
않으므로, 스케줄러가 "이 요청이 첫 토큰을 냈다"고 판단하는 것은 관측이 아니라 모델이다.**
따라서 **첫 토큰과 관련된 양을 스케줄러 쪽 값으로 재면 모델과 모델을 비교하게 된다.**
진짜 값은 배치 로그와 클라이언트 기록을 요청 단위로 붙여야 나온다. → §4.3, §9.2

---

## §1 핵심 개념 — 일곱

이 절은 "무엇을 했나"가 아니라 **"왜 이 형태여야 했나"**를 적는다. 각 개념 밑에 그것을
정하게 만든 측정과, 그 자리에서 검토했다가 버린 대안을 같이 둔다.

### ① routing과 admission은 같은 질문의 서로 다른 출구다

모든 결정은 검사 하나로 내려진다:

> **"이 요청을 이 인스턴스에 놓으면, 새 요청을 포함해 그 인스턴스의 모든 요청이 각자의
> SLO 예산을 지키는가."**

인스턴스별 검사 결과가 넷으로 갈린다:

| 출구 | 언제 | 무엇이 일어나나 |
|---|---|---|
| **route** | 통과하는 인스턴스가 있다 | 그중 정렬 1위에 배치 |
| **pend** | 없지만 기다릴 시간이 남았다 | 배치하지 않고 게이트웨이가 붙들어 재시도 |
| **shed** | 못 기다리고 자기 예산도 못 지킨다 | 거절 |
| **force** | 못 기다리지만 자기 예산은 지킨다 | 피해 최소 인스턴스에 강제 배치 |

**의도**: 거절은 별도의 admission 모듈이 아니라 **"feasible한 배치가 TTFT 예산 안에 생길
수 없다"의 이름**이다. 라우팅과 admission을 따로 두면 둘이 같은 상태를 두 번 추정하게
되고, 그 두 추정이 어긋나는 순간을 아무도 못 본다.

**버린 대안**: 라우팅 뒤에 별도 admission 필터를 두는 구성. Llumnix의 기존 filter
파이프라인이 그 형태인데, 그것을 쓰지 않는다 — **보유(pend)와 거절(shed)이 선택지이므로
모든 인스턴스를 한 자리에서 비교해야 한다.** 필터는 인스턴스를 하나씩 떨어뜨리므로
"아무 데도 못 가지만 기다리면 갈 수 있다"를 표현할 수 없다.

### ② 요청별 미래를 예측하지 않는다

요청 하나하나의 남은 출력 길이나 완료 시각을 예측하지 않는다. 대체물이 셋이다:

| 무엇 | 대체물 | 어디 |
|---|---|---|
| 출력 길이 | **클래스 조건부 분포** `E[L−j | L>j]` | §2.6 |
| 상주 요청이 얼마나 위태로운가 | **예산 회계** — 쓴 시간 대 허용 시간의 잔액 | §4.3 `allowanceMs` |
| 미래 속도 | 요청별이 아니라 **인스턴스당 horizon 평균 step 시간 하나**(`meanAfter`) | §4.4 |

**의도**: 요청별 예측기를 두면 학습·초기화·드리프트가 전부 시스템의 일부가 된다.
llm-d의 predicted-latency scheduling이 그 형태이고, **조건마다 예측기가 초기화되는
위험**을 실험 설계에서 따로 다뤄야 했다(`llmd-baseline.md`). 우리는 클래스 하나에 분포
하나를 두고 그 클래스의 모든 요청에 같은 값을 쓴다 — **틀리지만 틀리는 방식이 고정**이고,
그 편향은 온라인 보정 두 루프(§4.8)가 흡수한다.

**대가**: 같은 클래스 안의 긴 요청과 짧은 요청을 구분하지 못한다. EXP-64가 클라이언트에게
요청별 길이 힌트를 받아 그 대가를 쟀다(`--fluidserve-oracle-length`).

### ③ 추정을 그 순간 이상 믿지 않는다

- 붙들린 요청은 **재시도 주기마다 전체 경로를 재탑승**한다. 그 주기는 설정을 읽지 않고
  **측정한다**(`recheckMs = now − lastSeenMs`) — 설정과 실제가 어긋나도 계산이 맞는다.
- 받아들인 요청의 예산은 이후 **모든 도착의 `overIncumbents` 검사에서 반복 보호**된다.
  한 번 통과했다고 끝이 아니라, 뒤에 오는 요청이 그것을 밀어내려 할 때마다 다시 확인된다.

**의도**: 이 시스템의 모든 추정은 500 ms짜리 status 스냅샷 위에서 만들어진다. 그
스냅샷의 유효기간을 넘겨 믿지 않는 것이 **추정의 정확도를 올리는 것보다 싸다.**

### ④ 격리는 구성이 아니라 결과다

어떤 인스턴스도 어떤 클래스에 **배정되지 않는다.** 정렬의 클래스 항(§4.5)이 쏠림을
만들고 feasibility가 그것을 멈춘다. 배정 표가 없으므로 **트래픽이 끊기면 스스로 풀린다.**

**왜 격리가 필요한가**: 인스턴스의 허용 점유량은 **그 위에 있는 가장 빡빡한 예산**이
정한다. 클래스를 섞으면 양쪽이 손해다 — 빡빡한 요청 하나가 인스턴스 전체를 끌어내리고,
느슨한 요청은 자기 예산이 주는 만큼의 자리를 못 받는다.

**버린 대안**: 정적 파티션(PolyServe 형태). 고정 fleet 위의 정적 클래스 파티션은 믹스가
움직이는 워크로드에서 **재분할 자체가 비용**이고, 우리 측정에서는 한 시간 trace의 믹스
변화를 따라가지 못했다(`polyserve-fidelity.md`). **⚠ 그러나 우리 쪽의 대가도 실측됐다** —
§9.5, 어느 인스턴스가 그 역할을 맡는지가 run마다 다르다.

### ⑤ 가중합이 없다

결정은 **사다리**이고 단마다 양 하나가 정한다(fluidserve.go:50-83 주석). 여러 신호를
가중합해 하나의 점수로 만들지 않는다.

**근거**: 가중합 판이 과부하에서 붕괴한 실측이 있다 — **한 엔진에 5,569건이 큐에 쌓이고
나머지 셋이 27분간 유휴**였다. 가중합은 어느 항이 그 결정을 만들었는지 사후에 복원할 수
없으므로 그 붕괴를 진단할 수도 없었다.

**예외 하나**: `affinityWeight`(0~1, 정렬 점수 안). 클래스 선호를 스위치가 아니라
**정도로 바꿀 수 있게** 남겨 둔 것이고, 두 양이 모두 0~1의 비율이라 스케일 상수가
필요 없다.

### ⑥ (v0.3에서 추가) 모형의 두 절반이 같은 엔진을 같은 방식으로 봐야 한다

이 정책에는 시간 모형이 둘 있다:

| 모형 | 무엇을 예측하나 | 어디 |
|---|---|---|
| `meanStepMs` | 앞으로 horizon 동안의 **평균 iteration 시간** | §4.2, §4.4 |
| `prefillEstimateMs` | 요청이 배치된 뒤 **첫 토큰까지의 시간** | §4.4, §4.6 |

**v0.2까지 둘이 어긋나 있었다.** `meanStepMs`는 prefill step과 decode step이 섞이는 것을
명시적으로 계산하는데(`((k−s_p)·t_dec + s_p·(t_pre+t_dec−c0))/k`), `prefillEstimateMs`는
**prefill step 시간만 청구**했다. 그래서 그 값이 답하는 질문이 **"이 작업이 엔진의 prefill
시간을 얼마나 먹는가"**였지 **"첫 토큰이 벽시계로 언제 나오는가"**가 아니었다.

**차이가 정확히 엔진의 prefill duty(엔진이 자기 시간 중 prefill에 쓰는 몫)의 역수다.**

**실측 (EXP-101b, 78,869건 전량 조인)**: 예측이 요청들을 **결과와 거의 같은 순서로
늘어놓는데**(Spearman +0.616 배치→첫토큰, **+0.850** 기다린 시간 포함 대 전체 첫토큰 시간)
**눈금이 꼬리에서 2배 모자랐다**(실제/예측 p50 0.59, p90 **1.99**, p95 2.21; p90끼리
6,959 ms 대 14,332 ms = **2.06배**). 그리고 큐가 쌓인 인스턴스의 실측 duty가
**0.43~0.46**이므로 역수가 2.2~2.3 — **맞는다.**

**이것이 v0.3의 유일한 새 코드다**(§4.4). 상수를 고른 것이 아니라 **빠진 항을 넣은
것**이고, 그 항의 크기는 이미 관측·발행되던 값이다.

**일반화**: 같은 물리 현상을 두 곳에서 모형화하면 **두 곳이 같은 근사를 쓰는지 확인해야
한다.** 이 저장소가 반복해서 걸린 함정 — 같은 이름의 두 양 — 의 모형 판이다.

---

### ⑦ 예측 거리를 결정이 필요로 하는 만큼만 잡는다

예측은 피할 수 없다. 그런데 **얼마나 먼 미래를 보느냐**가 정확도를 정하고, **가장 가까운
순간이 가장 정확하지만 그것만으로는 결정을 못 내린다.** 그래서 이 시스템은 하나의 예측
거리를 고르지 않고 **양마다 그 결정이 필요로 하는 거리를 따로 잡는다.**

**실제로 있는 거리 셋**:

| 거리 | 무엇을 예측하나 | 어느 판정이 쓰나 |
|---|---|---|
| **1 step** | 텅 빈 인스턴스의 iteration 하나 비용(`c0 × 보정`) | **달성 불가 판정** — 인스턴스를 비워도 못 맞추는 요청의 식별 |
| **100 step** (planning horizon) | 그 창의 **평균** 토큰당 시간, 그리고 창 끝의 **투영 KV 점유** | **게이트 · 잔액 · 메모리** — `feasible`의 네 조건 전부 |
| **요청의 남은 생애** | 앞으로 만들 토큰 수, 그것에 예측 속도를 곱한 완료 시각 | **잔액 계산**, 사다리의 **pend · shed** |

**갈리는 선이 "속도 판정"과 "예산 회계" 사이다.** *이 인스턴스가 어떤 상태가 되는가*는 100
step으로 보고, *이 요청이 약속을 지키는가*는 요청이 끝날 때까지 본다.

**그리고 필요한 것보다 자세히도 예측하지 않는다.** 100 step짜리도 step별 예보가 아니라
**그 창의 평균**이다 — 앞으로 100 step 중 몇 개가 prefill 청크를 싣는지(`s_p`)만 추정하고
나머지는 평균으로 접는다.

#### 거리를 정한 이유가 정확도만은 아니다 — 코드에 셋이 있다

- **정확도** — 위의 기본 축.
- **회계의 일관성.** `costOf`가 도착 요청의 KV를 `prompt + min(100, 기대 출력 길이)`로
  자른다. 주석의 이유는 *"horizon이 다른 모든 요청의 방출을 세는 창이기도 하기 때문"*이다.
  **들어오는 것과 나가는 것을 같은 창에서 세기 위해서**이지 정확도 때문이 아니다.
- **되먹임 차단.** `nominalMs`는 **아예 예측하지 않고 고정**이다. 뒤처진 요청의 명목 예산을
  따라 내리면 뒤처짐이 게이트를 낮추고, 낮아진 게이트가 더 많은 요청을 막아 더 뒤처진다.
  **거리가 0인 것이 정확도 때문이 아니라 그 양이 결정의 입력이자 출력이 되는 것을 막기
  위해서다.**

#### 먼 거리에서는 점 추정 대신 분포를 쓴다

긴 거리 양은 셋 다 클래스 분포에서 나온다 — 잔여 길이 `E[L−j | L>j]`, 완료 확률
`completionProb(j, k)`. 그리고 **방출 추정에는 1.65σ 하한이 붙는다**
(`outflow = Σ p·kv − 1.65·√(Σ p(1−p)·kv²)`). 방출을 적게 잡아 점유를 크게 보는 **보수적
방향**이다. **분포를 알고 있으니 꼬리를 결정에 넣을 수 있고, 점 추정으로는 못 하는 것이다.**

#### ⚠ 측정이 순진한 버전을 반증한다 — "먼 예측이 부정확해서 비용이 난다"는 성립하지 않는다

그 서술이 맞다면 **정확한 길이를 주면 좋아져야 한다.** 두 실험이 길이 축을 양방향으로
흔들었고 **총계는 어느 쪽으로도 안 움직였다.**

| | 무엇을 했나 | 총계 결과 |
|---|---|---|
| **EXP-64** (2026-08-12, v0.2) | 요청마다 **정확한 출력 길이**를 줬다 | 네 도착률에서 −0.3 / **−0.2** / +0.6 / −2.0. 전부 사전 문턱 3점 안, 부호가 두 번 뒤집힘 |
| **EXP-90** (2026-08-21) | 표본을 줄이고(9분/2분/**12초**), 실제로 겪은 **3.4배 드리프트**를 넣었다 | **12초짜리 프로파일도** 손실이 잡음 폭 안. 오프라인 오차 곡선이 결과로 이어지지 않는다 |

**그런데 EXP-90이 총계가 가리는 것을 보여 준다 — 클래스 배분은 크게 바뀐다.** 45 req/s에서
낡은 프로파일을 넣으면 **deepresearch −7.5점**, chat +5.5, swe +4.8이다. **틀린 프로파일의
주인인 클래스가 값을 치른다** — dr을 3.4배 짧게 보면 과잉 수용하고 받아들인 dr이 더 많이
실패한다. **dr이 요청의 15.4%라 총계가 이 거래를 가린다.**

**기제도 이름이 붙었다**: 길이 분포가 결정에 닿는 통로는 **메모리 판정 하나**다(분포 →
방출 → 투영 → `overMemory`). 낡은 프로파일을 넣자 45 req/s에서 **메모리 사유가 19.2% →
0.1~0.2%로 사라지고** 게이트가 93%로 옮겨 갔다.

> **그러므로 쓸 수 있는 문장은 이것이다**: 긴 거리 예측의 정밀도는 **총계 용량을 정하지
> 않고 클래스 사이의 배분을 정한다.** 이 설계가 기대는 것은 길이 분포의 정밀도가 아니라
> **클래스 수준의 예산 구조와 스스로 보정되는 시간 모델**이다. 런타임에 갱신되지 않는
> 유일한 입력이 갱신을 필요로 하지 않는 것으로 측정됐다 — **단, 총계로 채점할 때이고,
> 클래스별로 채점하면 낡은 프로파일은 그 클래스에 비용을 물린다.**

**그리고 이 형태가 "우리는 예측을 잘한다"보다 방어된다**: 후자는 *"그래서 부정확해서 얼마를
잃는가"*를 부르고 우리 답이 "총계로는 0"이라 주장이 약해진다. 앞의 형태는 **정밀도가 무엇을
정하고 무엇을 안 정하는지를 갈라 놓고 둘 다 측정으로 뒷받침한다.**

#### ⚠ 이 절의 빈칸 둘 — 논문에 쓰기 전에 채워야 한다

1. **KV 투영 오차가 v0.3에서 다시 측정되지 않았다.** 알려진 값(**88.5% 과소예측, 절대오차가
   무투영의 3.6배**)은 **2026-07-31 run 하나**이고 그 뒤 넷이 바뀌었다 — 길이 프로파일
   재생성(EXP-48), 워크로드 자체의 변경(2026-08-08), **투영이 비교되는 천장이 `capMem`에서
   `min(capKv, capMem)`로 낮아진 것**(v0.3), 보류율이 55.0% → 44.8~48.8%로 바뀐 것(v0.3).
   **재분석만으로 된다** — `projected_kv_tokens`와 `obs_kv_tokens`가 인스턴스별로 발행되므로
   `exp48_projection_error.py`가 시각 t의 예측을 t+horizon의 실측과 짝지어 준다.
2. **EXP-64가 v0.2에서 돌았고, 그 실험이 물었던 통로가 그 뒤에 굵어졌다.** 그때 메모리 판정의
   천장은 `capMem` 하나였고, 코드 주석이 그것에 대해 *"물리 이용률 95% 아래에서는 아무것도
   거절하지 않는다"*고 적는다. **길이 정보가 결정에 닿는 유일한 통로가 거의 구속하지 않는
   상태에서 "정확한 길이를 줘도 차이가 없다"를 측정한 것이다.** 반증하는 측정은 없지만
   **재확인이 필요하고, 한 시간 trace용 오라클 표가 없어서 제작이 선행한다.**
   ⚠ EXP-64 자신이 적어 둔 단서도 같이 읽는다: 처리군의 반복 폭이 세 도착률에서 대조군의
   2~5배였고, H3의 유효성 조건이 반복 2에서 깨져(**swe 유효 인스턴스 2.64 → 3.54**)
   *"admission만 쟀다"*고 쓸 수 없다.

---

## §2 컴포넌트와 각각의 상태(state)

### 2.1 전체 그림

```
클라이언트 ──요청──▶ 게이트웨이 (hold loop) ──HTTP──▶ 스케줄러 dispatch policy
                        ▲    │ 429 "no endpoint" → 500ms 후 재시도 (35s 천장)
                        │    │ 429 "admission rejected" → 즉시 클라이언트 오류
                        │    ▼ 200 + instance id
                     클라이언트                엔진 인스턴스 × N (vLLM V1)
                                                │ status (~500ms 주기) ──▶ cmsView
```

**두 429가 다른 뜻인 것이 이 설계의 통합 지점 전부다.** 게이트웨이는 Llumnix의 기존 hold
loop를 그대로 쓰고, 더한 것은 **같은 429 본문을 두 의미로 가르는 수십 행**뿐이다
(§8). pend는 재시도, shed는 즉시 클라이언트 오류.

**⚠ 게이트웨이 천장이 FluidServe만 다르다**: 35,000 ms 천장에 500 ms 재시도. 다른
정책은 5,000/1,000이다. **보유가 선택지인 정책만 그 창이 필요하고**, EXP-83·84가 그
차이를 측정했다 — 우리 수치가 보수적인 방향이다(천장이 길수록 거절이 늦게 일어나
offered 분모에서 불리하다).

### 2.2 정책 최상위 상태 (`fluidserveDispatchPolicy`, fluidserve.go)

| 필드 | 무엇 | 수명 |
|---|---|---|
| `cfg` | 플래그 30개가 굳은 설정 구조체 | 프로세스 |
| `capacity` | 지연 모델 + 온라인 보정 둘 (§2.3) | 프로세스 |
| `registry` | 어느 요청이 어느 인스턴스에 있고 얼마나 갔나 (§2.4) | 프로세스 |
| `lengths` | 클래스별 출력 길이 분포 (§2.6) | 프로세스, 읽기 전용 |
| `prefix` | prefix 블록 → 인스턴스 index (§2.5) | 프로세스 |
| `lastObs[id]` | 인스턴스별 지난 status와 거기서 유도한 EWMA 다섯 | status 하나 |
| `fluxCache[id]` | 인스턴스 상태의 캐시 (§4.2) | stepID·dispatchVersion이 같은 동안 |
| `shedIDs[id]` | 거절한 요청 id (재도착 시 같은 결정을 반복하지 않기 위해) | `fsArrivalTTLMs` |
| `lastProbe[id]` | 같은 status로 몇 번째 배치인지, 그때 headroom (이중 판매 감시) | 다음 status |

**`lastObs`가 담는 EWMA 다섯**(전부 `fsPrefillDutyAlpha = 0.1`, 유효 창 약 5초):
`prefillMsEwma`/`totalMsEwma`(그 비가 `prefillDuty`), `elapsedEwma`/`stepsEwma`(그 비가
`meanMs`), `kvSlope`.

**⚠ 비가 아니라 두 시간을 따로 평활하는 이유**(fluidserve.go:654-666 주석): 원하는 양이
**엔진 시간의 몫**이므로, 세 배 긴 구간은 그 몫에 대한 세 배의 증거다. 비를 먼저 만들고
평활하면 구간 길이가 무시된다. 같은 이유로 `meanMs`도 "구간별 per-iteration 시간의 평균"이
아니라 **총 시간 / 총 iteration**이다 — 실측으로 셋이 갈린다: 엔진 자신의 토큰당 지연
**42.6 ms**, 구간 동일가중 평균 58.7, 중앙값 38.7 (80 req/s, 천만 토큰).

### 2.3 capacityModel (fluidserve_capacity.go, 469행)

**하는 일**: 프로파일의 계수로 iteration 시간을 예측하고, 실측과의 차이를 두 개의 보정
계수로 흡수한다.

| 상태 | 무엇 | v0.3 변경 |
|---|---|---|
| `decode_step_law` | `c0 + c_kv·kvLogical + c_n·nDecode` (프로파일, 읽기 전용) | — |
| `prefill_step_law` | 청크 크기별 prefill step 시간 표 (프로파일) | — |
| `correction` | 예측 대 실측의 곱셈 보정 — **함대 하나** | **인스턴스별로 쪼갬** |
| `corrections[id]` | (v0.3) **인스턴스별 보정 계수** | **신규** |
| `prefillFraction` (κ) | prefill 비용의 잔차 배율 | 함대 하나(그대로) |

**왜 인스턴스별로 쪼갰나 (v0.3, EXP-98)**: 실측하면 **chat을 든 엔진은 예측의 1.07~1.15배**로
돌고 **deepresearch 전용 엔진은 0.76~0.96배**로 돈다. 함대 계수 하나는 **부호가 반대인 두
오차를 평균**해 0.975~0.997을 내고, **개별 인스턴스에서는 −24%~+15% 틀린다.** 그리고 그
예측을 feasibility 네 조건 중 셋이 읽는다.

**쪼개는 대가**: 계수 하나당 표본이 4분의 1이 되고, 되먹임 고리가 짧아진다 — 인스턴스별
계수는 **그 인스턴스 자신의 배치로 되돌아온다**(함대 계수는 넷에 희석된다). 그래서 α를
올리지 않았다.

**처음 보는 인스턴스는 함대 값에서 시작한다**(`corrLocked`) — 한 표본으로 만든 계수로
초기 결정을 내리지 않기 위해서. `perInstance=false`면 함대 값이 그대로 쓰인다.

**효과**: 이것 하나만으로 요청 단위 admitted **68.9 → 75.8** (EXP-98, 2반복).

### 2.4 requestRegistry (fluidserve_registry.go, 908행)

**하는 일**: 엔진이 요청별 진행을 보고하지 않는 상태에서, **어느 요청이 어느 인스턴스에
살아 있고 얼마나 갔는지를 스케줄러 쪽에서 복원**한다.

| 상태 | 무엇 |
|---|---|
| `byInstance[inst][id]` | `dispatchRecord` — 배치 시각·그때 stepID·prefill step 수·예측한 prefill 시간·프롬프트 길이·tier |
| `arrivals[id]` | 첫 도착 시각과 마지막으로 본 시각 (`recheckMs`의 원천) |
| `dispatchVersion[inst]` | 배치가 있을 때마다 증가 — flux 캐시 무효화 |
| `promptTokensSince`, `chargedPrefillSince` | κ 갱신의 분모 |
| `delayMean`, `delayMeanSq`, `delaySeen` | 배치→첫토큰 잔차의 EWMA 쌍 (함대) |
| `delayByInst[inst]` | (v0.3에 추가, 기본 미사용) 같은 것의 인스턴스별 판 |

**⚠ 이 컴포넌트의 가장 중요한 한계** (2026-08-26, EXP-101에서 발견):
**"요청이 토큰을 내기 시작했다"는 판정이 관측이 아니라 모델이다.** §4.3과 §9.2를 볼 것.

### 2.5 prefixIndex (fluidserve_prefix.go, 211행)

프롬프트를 16토큰 블록으로 잘라 FNV-1a 체인 해시를 만들고, **블록 → 그 블록을 가진
인스턴스 집합**을 LRU 500,000 블록으로 유지한다. 배치가 일어날 때만 갱신한다
(`route`/`force` — pend·shed는 엔진 캐시에 닿지 않으므로).

**의도**: 같은 프롬프트가 이미 있는 인스턴스는 그 요청의 prefill을 덜 한다. 그것을
**청구액에 반영**하면(§4.4의 `hitTokens`) 첫토큰 추정과 KV 성장 추정이 둘 다 인스턴스의
함수가 된다.

**⚠ 알려진 결함**: index는 엔진의 **eviction을 보지 못한다.** 엔진이 블록을 버려도 index는
계속 hit을 주장한다. 그 실패가 κ > 1로 나타나야 하므로 prefix 모드의 κ 상한이 4.0이다
(§4.8).

### 2.6 lengthModel (fluidserve_profile.go, 255행)

클래스(tier)마다 출력 길이의 경험 분포를 격자로 들고, 셋을 답한다:

| 질문 | 함수 | 어디 쓰나 |
|---|---|---|
| j개를 냈는데 앞으로 몇 개 더 내나 | `E[L−j | L>j]` | 잔액 계산의 분모 (§4.3) |
| j개를 냈는데 horizon 안에 끝날 확률 | `completionProb(j, 100)` | outflow (§4.2) |
| j개를 냈는데 아직 살아 있을 확률 | `survivalAt(j)` | 상주 기록 퇴역 (§4.3) |

**⚠ 워크로드를 바꾸면 이 파일도 바꿔야 한다.** 이 분포는
`deploy/profiling/.../fluidserve.json`의 `classes[]`에서 오고, 워크로드 생성기가 바뀌면
낡는다 — 실제로 deepresearch 출력이 282 → 985 토큰이 되는 동안 그대로여서 `outflow`가
그 클래스에서 **3.4배 과대예측**된 적이 있다(CLAUDE.md 함정 A).

---

## §3 입력

### 3.1 요청 (게이트웨이 → 스케줄러, `types.SchedulingRequest`)

| 필드 | 무엇 | 어떻게 쓰나 |
|---|---|---|
| `Id` | 요청 uuid | registry 키, 배치 로그 |
| `PromptNumTokens` | 프롬프트 길이 | prefill 비용, KV 성장 |
| `PromptTokenIds` | 토큰 id 배열 | prefix 해시 (요청당 1회, 캐시) |
| `TpotSloMs` | **tier** — 클래스 식별자 겸 토큰당 예산 | 예산 조회, 정렬의 클래스 항 |
| `TtftSloMs` | 첫 토큰 예산 | `canWait`, shed 판정 |

**tier가 클래스 이름이자 예산인 것이 설계 결정이다.** 게이트웨이가 클래스를 따로 실어
보내지 않아도 되고, 새 클래스는 새 tier 값 하나로 생긴다.

**⚠ tier 값이 곧 토큰당 예산인 것은 `--fluidserve-class-budgets`가 재정의하지 않는
한에서다.** 배포 설정은 `25:e2e:30000`으로 **tier 25(swe)를 전체 시간 30초 예산으로
다시 정의**한다 — 그 클래스의 진짜 제약이 토큰당이 아니라 E2E이기 때문이다. 그래서
**25는 tier 이름일 뿐 토큰당 예산이 아니다.**

### 3.2 엔진 status (인스턴스별, 약 500 ms 주기, cmsView)

| 필드 | 무엇 |
|---|---|
| `StepId` | 엔진의 전역 iteration 카운터 — 시간 측정과 상주 복원의 축 |
| `NumUsedGpuTokens` / `NumTotalGpuTokens` | **물리** KV 점유/용량 |
| 디코드 배치 수, 대기·실행중·inflight prefill 토큰 | `nDecode`, `pendingPrefill` |
| `MaxNumBatchedTokens` | 청크 크기 (기본 8192) |

**논리 KV와 물리 KV를 구분한다.** 지연 모델은 **논리 KV**(각 요청이 계산한 토큰의 합)로
적합됐고 — 엔진 자신의 디코드 회계가 보고하는 양이며 prefix 공유가 물리 풀을 넘어 부풀리는
그 양이다 — 메모리 한계는 **물리**다. §4.2의 `ratio`가 둘을 잇는다.

### 3.3 프로파일 (`deploy/profiling/llama31-70b-b200-tp2/fluidserve.json`)

| 블록 | 무엇 | 실값 |
|---|---|---|
| `decode_step_law` | `c0 + c_kv·kv + c_n·n` | c0=16.36 ms, c_kv=1.282e-5, c_n=0.0764, R²=0.952 |
| `prefill_step_law` | 청크별 step 시간 | 1024→80.1 / 4096→264.2 / 8192→519.7 ms |
| `classes[]` | 클래스별 출력 길이 분포 | §2.6 |

**⚠ 셋의 성질이 다르다.** `decode_step_law`와 `prefill_step_law`는 **엔진의 성질**이고
`classes[]`는 **워크로드의 성질**이다. 워크로드를 바꿔 재생성할 때는 `classes[]`만
갈아끼운다 — 같이 바꾸면 한 번에 두 가지를 바꾸는 것이 된다.

**⚠ 파일을 못 읽으면 기동이 거부된다**, 값이 이상해도 마찬가지다. "JSON만 바꾸는 변경"으로
스케줄러가 기동 중 죽어 4시간 반을 잃은 적이 있다(CLAUDE.md 함정 A).

---

## §4 워크플로 — 요청 하나의 경로, 수도코드로

### 4.0 도착 (`calculateMetrics`, fluidserve.go:451)

```
arrivedMs, recheckMs = registry.noteArrival(id, promptTokens, now)
   // 첫 호출: arrivedMs = now 고정. 재호출: recheckMs = now − lastSeenMs
tier = request.TpotSloMs
nominalMs, expectedToks, isE2E, budgetMs = requestBudget(tier)     // §3.1
promptHashes = registry.hashesFor(id, PromptTokenIds, 16)          // 요청당 1회, 캐시
req = fluidserveRequest{...}   // 위 전부 + ttftSloMs, nowMs
for each instance view:
    observed = observeInstance(view)     // §4.1
    f = flux(view)                        // §4.2 — 캐시 or buildFlux
    if observed ≥ 0: capacity.noteResidual(f.id, f.meanStep, observed)   // §4.8
```

**`recheckMs`를 설정이 아니라 측정에서 얻는 것이 의도다.** 붙들린 요청은 그 간격 안에서는
아무 행동도 할 수 없으므로, **모든 deadline 계산이 이만큼을 미리 뺀다.** 설정값을 읽으면
게이트웨이 설정이 바뀌었을 때 조용히 틀리는데, 측정하면 안 틀린다. (게이트웨이 재시도
주기를 run 자신의 데이터에서 복원하는 검사도 이 성질 위에 있다 — CLAUDE.md 함정 A.)

### 4.1 관측 경로 (`observeInstance`, :758) — 예측의 근거가 되는 측정

```
cur = {stepID, timestampMs, kvLogical, nDecode, pending}    // 이번 status
prev = lastObs[id]                                          // 지난 status
if cur.stepID ≤ prev.stepID: return −1     // 엔진이 안 움직였으면 표본 없음
steps   = cur.stepID − prev.stepID          // 그 사이 실행된 iteration 수
elapsed = cur.timestampMs − prev.timestampMs
guard: steps > 10,000 or elapsed > 30,000ms → 버림 (재시작/정지 걸침)
guard: prev.nDecode < 1 → 버림 (놀고 있는 엔진의 step 간격은 계산 시간이 아님)

measured   = elapsed / steps                              // 구간 평균 iteration 시간
decodeOnly = c0 + cKv·prev.kvLogical + cN·prev.nDecode    // 구간 시작 상태의 디코드 전용 예측
prefillMs  = (measured − decodeOnly) × steps              // 법칙을 넘는 시간 전부를 prefill로 귀속
notePrefill(...)                                          // κ 갱신, §4.8
EWMA 갱신 (α=0.1): prefillMsEwma/totalMsEwma → prefillDuty
                   elapsedEwma/stepsEwma     → meanMs
                   kvSlope ← (Δ kvLogical)/elapsed
return meanMs
```

**단일 step 시각을 안 쓰는 이유**(:645-656): 엔진 스케줄러가 비동기라 개별 step 간격은
**0 아니면 참값** 둘 중 하나로 나와 귀속이 불가능하다. 구간 평균만이 의미가 있다.

**`prefillDuty`의 정의가 중요하다**: "디코드 법칙이 설명하지 못하는 시간" / "전체 시간".
즉 **잔차를 prefill로 귀속한 것**이지 엔진이 보고한 prefill 시간이 아니다. 그래서 이 값은
디코드 법칙이 틀린 만큼도 함께 담는다 — 그것이 §4.8의 `correction`이 흡수하려는 그 양과
겹친다. **두 보정이 같은 잔차를 나눠 갖는다는 것이 알려진 미해결**이다(§9.4).

**게이지를 같이 내보내는 이유**: `decode_only_ms`·`obs_kv_tokens`·`obs_decode_batch`를
따로 발행해, 예측-실측 간극이 **네 원인**(디코드 법칙 / 배치 크기 / prefill 항 / 보정
계수) 중 어디서 났는지 사후에 가를 수 있게 한다.

### 4.2 인스턴스 상태 구성 (`buildFlux`, :1022)

**캐시 조건**: `(엔진 stepID, registry.dispatchVersion, 인스턴스 수)`가 같으면 재사용.
**엔진이 안 움직였어도 내 배치가 있었으면 다시 만든다** — 그렇지 않으면 같은 자리를 두 번
판다.

```
f.kvLogical, f.nDecode = decodeBatchOf(status)
f.pendingPrefill = 대기 + 실행중 + inflight prefill 토큰
f.chunk = MaxNumBatchedTokens (기본 8192)
f.live = registry.reconcile(id, engineRunning, stepID, now)      // §4.3

// 미래 prefill: 큐(순간)와 duty 투영(흐름)의 max
horizonMs = 100 × paceMs(id)          // paceMs = 실측 meanMs, 없으면 디코드 법칙(낙관 방향)
f.arrivingPrefill  = duty × horizonMs / (prefillStepMs(chunk) − c0) × chunk
f.effectivePrefill = max(f.pendingPrefill, f.arrivingPrefill)

f.meanStep = meanStepMs(f.id, f.kvLogical, f.nDecode, f.effectivePrefill, chunk, 100)

// 두 문턱 — 상주 요청을 훑으며
floor = c0 × correction(f.id)                 // 빈 인스턴스의 step 비용
for r in f.live:
    if r.allowanceMs < floor:  r.unachievable = true; continue   // 아무도 못 구함 → 양쪽 제외
    gateAllowance = min(gateAllowance, r.nominalMs)               // 명목 예산의 최소
    if r.allowanceMs < f.meanStep: continue    // 이미 놓치는 중 → 거절해도 못 구하므로 제외
    tightestAllowance = min(tightestAllowance, r.allowanceMs)     // 잔액의 최소

// KV 흐름 수지 (enableFlux=true; 끄면 inflow=outflow=0 = 순수 level 제어기)
inflow  = f.nDecode × 100                      // 디코드는 step당 요청당 정확히 1토큰
outflow = Σ_r  completionProb(r.j, 100) × r.kvTokens              // 기대 방출
        − 1.65 × sqrt( Σ_r p(1−p)·kvTokens² )                     // Bernoulli 합의 1.65σ 하한
f.proj  = max(0, f.kvLogical + inflow − outflow)

f.capKv  = maxKvForAllowance(f.id, gateAllowance × 0.90, nDecode, effectivePrefill, chunk, 100)
ratio    = clamp(kvPhysical / kvLogical, 0.01, 1)   // prefix 공유 배율 (실측 0.09까지)
f.capMem = kvCapacity × 0.95 / ratio                // 물리 용량을 논리 단위로 환산
f.headroom = min(capKv, capMem) − proj
```

**`max`이지 `+`가 아닌 이유**(:1066-1072 주석): 큐와 duty는 **겹치는 일**을 서술한다 —
지금 큐에 있는 것이 곧 엔진이 다음에 prefill하는 것으로 관측된다. 더하면 같은 프롬프트를
두 번 청구한다. 그리고 **각자가 상대가 놓치는 경우를 덮는다**: 막 도착한 버스트는 duty에
나타나기 전에 큐에 먼저 보이고, 상태 조회 사이에 엔진이 흡수해 버리는 꾸준한 흐름은 큐가
0인데 duty에 보인다.

**실측으로 어느 쪽이 이기나** (mix-shift 한 시간, 큐 > 10,000 토큰인 구간):
가장 밀린 엔진에서 **큐 p50 111,971 토큰 대 duty 항 50,632**로 **86.1%가 큐**다.
한가한 구간에서는 duty 쪽이 이긴다.

**`gateAllowance`와 `tightestAllowance`가 다른 양인 것이 중요하다.**
- `gateAllowance` = 상주 중인 요청들의 **명목 예산**(시작 예산)의 최소. 요청이 뒤처져도
  **안 움직인다** — 되먹임을 차단하려고 일부러 그렇게 뒀다. 인스턴스가 무엇에 gate되는지를
  나타내는 값이고, 100이면 **그 인스턴스에 chat이 하나도 디코드 중이지 않다**는 뜻이다.
- `tightestAllowance` = 상주 중인 요청들의 **잔액**의 최소. 이건 움직인다.

**제외 규칙 둘이 각각 다른 것을 막는다**:
- `allowanceMs < floor`(빈 인스턴스의 step 비용): **아무도 못 구하는 요청**. 이 요청을
  gate에 넣으면 인스턴스 전체가 영원히 막힌다.
- `allowanceMs < meanStep`(지금 속도): **이미 놓치는 중인 요청**. 새 도착을 거절해도 그
  요청은 못 구하므로, 그것 때문에 인스턴스를 막으면 손해만 는다.

**⚠ 두 제외의 범위가 다르다. 순서가 그것을 만든다.** `floor` 검사는 `continue`가 `gateAllowance`
갱신보다 **앞에** 있어서 **양쪽에서 다 빠지고**, `meanStep` 검사는 **뒤에** 있어서 **잔액에서만
빠진다.** 그래서 **이미 뒤처진 요청은 잔액에는 못 들어가지만 약속에는 계속 들어간다.**

의도한 것이다. 잔액은 *"내가 아직 지킬 수 있는 것을 남이 깨뜨리지 못하게 한다"*는 권리이고
이미 놓치고 있는 요청에는 그 권리가 없다. 반면 약속은 *"이 인스턴스에는 이 클래스가 있다"*는
사실의 표시이고, **그 사실은 요청이 얼마나 뒤처졌는지와 무관하다.** **⚠ 코드가 적어 둔 위험은 잔액 쪽이다**(:1896~1901 주석): 잔액이 이미 예산을 지난 요청을
제외하므로 **한 클래스가 실패하기 시작하는 바로 그 순간에 잔액의 보호가 약해진다.** EXP-46의
수용 조건이 *"chat이 그 이유로 나빠지지 않을 것"*이었다. **뒤처진 요청이 약속에는 남아 있는
것이 그 위험을 제한하는 장치**다 — 잔액이 그 요청을 놓아 주어도 게이트는 여전히 그 클래스의
예산에 묶여 있다.

**세 상태의 요약은 §0.4에 있다.**

### 4.3 상주 요청의 복원과 잔액 (`reconcile` registry.go:493, `liveViewLocked` :751)

```
for each dispatchRecord rec on this instance:
    if stepID < rec.stepAtDispatch: delete    // step이 뒤로 갔다 = 엔진 재시작
    if age > 20min: delete                    // 백스톱
    j = stepID − rec.stepAtDispatch − rec.prefillSteps     // 만든 토큰 수 = step 차이
    if j > 0 and rec.decodeStartMs == 0:
        rec.decodeStartMs = now               // ⚠ "첫 토큰"의 증거 — 아래 경고를 볼 것
        notePlacementDelay(inst, now − dispatchedMs − rec.prefillEstMs)
    if survivalAt(j) < 0.02: delete           // 관측된 길이의 꼬리를 넘김 = 끝났다고 판정
if len > engineRunning: j 큰 것부터 삭제       // 엔진 카운트가 authority

// 잔액 — "남은 토큰 하나당 앞으로 쓸 수 있는 시간". 예산 회계이지 속도 측정이 아니다.
remaining = oracleTokens>0 ? max(1, oracle − j) : E[L−j | L>j]
토큰당 클래스:
    decodeStartMs==0 (아직 prefill): allowance = perTokMs        // 디코드 예산은 미사용
    else: spent = now − decodeStartMs
          total = perTokMs × (j + remaining)
          allowance = (total − spent) / remaining
E2E 클래스:
    allowance = (totalMs − (now − firstSeenMs)) / remaining      // 대기·prefill·디코드가 한 계좌
allowance = max(0, allowance)
nominalMs = 클래스의 시작 예산 (tier값 또는 totalMs/E[L])
```

**⚠⚠ 이 절의 가장 중요한 사실 (2026-08-26, EXP-101):
`j`도 `decodeStartMs`도 그 요청에 대한 관측이 아니다.**

`j = stepID − stepAtDispatch − prefillSteps`에서 **`stepID`는 엔진의 전역 iteration
카운터**이고 **`prefillSteps`는 모델이 계산한 값**(`ceil(prompt/chunk)`)이다. 엔진은
요청별 진행을 보고하지 않으므로 이것이 유일하게 가능한 복원이지만, **앞에 15초어치 다른
일이 쌓여 있어도 엔진이 자기 step을 몇 번 돌면 조건이 만족된다.**

**실측 (EXP-101 대조 조건)**: 스케줄러가 본 배치→첫토큰이 **평균 314 ms이고 10초 이상이
0건/77,867**인데, 같은 run의 클라이언트 기록으로는 **deepresearch의 22.8%가 10초를
넘었다.** 네 인스턴스에서 297~325 ms로 거의 같게 나온 것이 증거다 — 진짜 큐라면
인스턴스마다 크게 다르고, 실제로 크게 다르다.

**따라올 결과 셋**:
1. **`decodeStartMs` 기준 잔액은 실제보다 이른 시각에서 시작한다** → `spent`가 크게 잡혀
   `allowance`가 작게 나온다. **보수적인 방향**이라 안전하지만 정확하지 않다.
2. **`notePlacementDelay`가 쌓는 잔차도 관측이 아니다** — `realised`가 위의 그 값이다.
   따라서 **`canWait`가 쓰는 "측정된 큐 지연"은 측정된 것이 아니다**(§4.6, §9.2).
3. **첫토큰 관련 양을 스케줄러 쪽 값으로 재려고 하면 모델 대 모델을 비교하게 된다.**
   진짜 값은 **배치 로그 + `request_ids.jsonl` + `metrics.csv`를 요청 단위로 조인**해서만
   나온다(`analysis_scripts/request_level/exp101_first_token_truth.py`, 매칭률 100%).

**`nominalMs`가 안 움직이는 이유**(되먹임 차단): 요청이 뒤처졌다고 그 요청의 명목 예산을
낮추면, **뒤처짐이 인스턴스의 gate를 낮추고 그것이 더 많은 요청을 막아 더 뒤처지게** 한다.
잔액은 움직이고 명목은 안 움직인다.

### 4.4 후보 평가 (`evaluate`, :1757)

```
// 이 요청의 prefill 비용 — 인스턴스별로 다르다 (prefix)
raw = promptTokens − hitTokens(promptHashes, instance)   // 이 인스턴스가 든 연속 prefix 블록 할인
c.prefillCharge = raw × κ                                // κ = 측정된 잔차 배율 (§4.8)

cost       = promptTokens + min(100, expectedToks)   // horizon 안의 KV 성장만 (E[L]=500이어도 100)
newKv      = f.proj + cost
newN       = f.nDecode + 1
newPending = f.effectivePrefill + c.prefillCharge
c.meanAfter = corr(f.id) × [ (k−sp)·(c0 + cKv·newKv + cN·newN)
                           + sp·(t_pre(chunk) + dec − c0) ] / k
              // k=100, sp = prefillSteps(newPending, chunk)

gate = req.nominalMs
if !ownBudgetGate and gateAllowance × gateSlack < gate: gate = gateAllowance × gateSlack
c.gateAfter = gate × 0.90

unpredictable  = isInf(meanAfter)
overGate       = meanAfter > c.gateAfter          // 약속(명목)의 보호
overIncumbents = meanAfter > f.tightestAllowance  // 잔액의 보호 — 0.90 없음
memLimit       = memoryUsesPaceCap ? min(f.capKv, f.capMem) : f.capMem      // ← v0.3
overMemory     = newKv > memLimit
overDeadline   = deadlineFeasible && (waited + c.prefillMs > ttftSloMs)     // 기본 꺼짐
c.feasible = !(unpredictable || overGate || overIncumbents || overMemory || overDeadline)

c.share/c.sameCount = 이 인스턴스 상주 중 같은 tier의 비율 / 개수
c.harm  = harmToIncumbents(...)                    // §4.5
c.room  = (min(capKv,capMem) − newKv) / max(capMem, 1)
c.prefillMs = prefillEstimateMs(req, f)            // 아래
c.missesTtftDeadline / c.missesOwnBudget           // §4.6
```

**`cost`가 `min(100, expectedToks)`인 이유**: horizon 안에 실제로 생길 KV만 센다.
평균 출력이 500 토큰이어도 100 iteration 안에는 100개만 생긴다. 전체 길이를 미리 청구하면
긴 클래스가 자기 자리를 영원히 못 받는다.

**`unpredictable`이 무엇인가**: `meanAfter`가 무한대가 되는 경로는 하나뿐이다 —
`prefillStepMs`가 성능 표에서 값을 못 얻으면(조회 오류, 또는 0 이하·비유한 값)
`math.Inf(1)`을 돌려주고, 그것이 `meanStepMs`를 통해 `meanAfter`로 전파된다. 실패했을 때
디코드 바닥값으로 대신하면 **prefill step 비용을 한 자릿수 과소평가**하므로 추정하지 않고
거부한다(`fluidserve_capacity.go:336~348`의 주석이 그렇게 적는다).
**요청의 클래스와는 무관하다** — 처음 보는 tier는 예산 표에서
`budgetSpec{mode: decode, perTokMs: float64(tier)}`로, 길이 표에서 `fallback` 프로파일로
떨어져 **정상 라우팅된다**(`fluidserve_registry.go:110`, `fluidserve_profile.go:186`).

**⚠ 비교의 두 변이 대칭이 아니다**:
- `f.tightestAllowance`와 `f.gateAllowance`는 **이미 최솟값**이고, **`f.live`(지금 이
  인스턴스에 있는 요청들)에서만** 만들어진다. **도착 요청은 안 들어 있다.**
- `c.meanAfter`에는 **도착 요청이 들어 있다** — `newN = f.nDecode + 1`,
  `newPending = f.effectivePrefill + c.prefillCharge`, `newKv = f.proj + cost`.
- 그러므로 판정은 **요청마다 여유의 부호를 보는 것이 아니라, 상주 요청들의 최솟값 하나를
  뽑아 도착 요청이 들어간 예상 속도와 한 번 비교**하는 것이다. 요청별 여유
  (`allowanceMs − meanBefore`)를 실제로 계산하는 곳은 판정이 아니라 **정렬**이다(§4.5의 harm).
- **planning horizon(100 iteration)은 오른쪽 변에만 들어간다** — `meanAfter`가 앞으로 100
  step의 평균이고, `f.proj`도 100 step 뒤의 투영 점유다. **잔액에는 안 들어간다.** §0.5.

**세 판정 조건이 각각 다른 것을 지킨다**:
- `overGate` — **약속의 보호**. 인스턴스 위 가장 빡빡한 **명목** 예산(과 자기 예산의 min)을
  0.90의 여유와 함께 지킨다. 이것이 클래스 격리를 만드는 항이다.
- `overIncumbents` — **잔액의 보호**. 이미 받아들인 요청들이 아직 지킬 수 있는 속도를
  지킨다. 여기에 0.90이 없는 것은 의도다 — 여유를 두 번 적용하면 같은 보호를 이중으로
  건다.
- `overMemory` — **공간의 보호**.

#### v0.3 변경 ①: 메모리 판정이 속도 상한도 본다

`capMem = kvCapacity × 0.95 / ratio`인데 `ratio = kvPhysical / kvLogical`이므로
**`kvCapacity`가 약분되어 `0.95 × kvLogical / (물리 이용률)`이 된다.** 즉 **물리 이용률이
정확히 95%일 때 현재 논리 점유량과 같아지고, 그 아래에서는 아무것도 거절하지 않는다.**
그리고 95%에 닿을 무렵에는 preemption이 임박해 있다.

`capKv`(그 인스턴스가 약속한 속도를 지킬 수 있는 점유량)는 **이미 계산되고 이미 큐에 쌓인
prefill을 포함**하는데, v0.2까지는 정렬의 `room` 항에만 닿았고 **그 항의 가중치는 선호
세기 1.0에서 0**이었다. 즉 계산해 놓고 결정에 안 쓰고 있었다.

**⚠ 이것 하나만 켜면 대조군보다 나쁘다** (EXP-98: 67.8 대 68.9). **틀린 보정이 틀린
상한을 만들기 때문이다** — `capKv`는 `(allowance/correction − overhead)/c_kv` 형태라
보정 계수가 그 안에 들어간다. 그래서 **§2.3의 인스턴스별 보정과 함께여야 76.2**가 된다.
v0.3에서 둘이 함께 기본값이 된 이유다.

**빈 인스턴스는 상주 요청이 없어 `gateAllowance`가 무한대이고 따라서 `capKv`도 무한대**라,
min이 자연스럽게 `capMem`으로 떨어진다.

#### v0.3 변경 ②: 첫토큰 추정이 엔진의 prefill duty를 반영한다 (`prefillEstimateMs`, :2497)

```
charge, _ = prefillChargeFor(req, f.id)               // prefix 할인 + κ
steps     = prefillSteps(charge, chunk)
per       = prefillStepMs(min(promptTokens, chunk))

queued    = prefillSteps(f.effectivePrefill, chunk) × prefillStepMs(min(effectivePrefill, chunk))

if prefillInterleaveAware and duty(f.id) > 0.05 and f.pendingPrefill > 0:      // ← v0.3
    pend     = prefillSteps(f.pendingPrefill, chunk) × prefillStepMs(...) / duty(f.id)
    arriving = prefillSteps(f.arrivingPrefill, chunk) × prefillStepMs(...)     // 나누지 않음
    queued   = max(pend, arriving)

return steps×per + queued
```

**왜 관측된 큐만 나누나**: `arrivingPrefill`은 **앞에 쌓인 일이 아니라 `duty × horizon`을
토큰으로 환산한 처리율 대리값**이다. duty로 나누면 **horizon 자체가 돌아와** 아무것도
추정하지 않는 값이 된다. 두 항을 따로 값 매기고 큰 것을 취한다 — §4.2에서 큐와 duty를
합칠 때 쓰는 그 max를 한 단계 뒤에서 한 번 더 하는 것이다.

**왜 요청 자신의 prefill은 안 나누나**: chat은 추정값이 거의 전부 자기 prefill인데
실측이 **754 ms 예측 대 370 ms 실제**로 이미 두 배 **과대**예측이다. 늘리면 부호가 반대인
오차를 키운다.

**`per`와 `perQueued`가 다른 프롬프트에서 오는 것도 의도다**(:2480-2495 주석): 앞에 쌓인
일은 **그 일의 청크 크기**로 값 매긴다. 개수는 엔진의 청크 단위인데 단가를 666토큰짜리
chat 프롬프트의 것으로 매기면 **여덟 배 과소평가**된다. 실측으로 4,800 rpm에서 11,478
토큰의 prefill이 728 ms의 엔진 시간인데 84를 돌려준 적이 있다.

### 4.5 정렬 (`sortCandidates`, :1639)과 harm (:2210)

```
정렬: feasible 그룹 먼저.
  feasible끼리:  score = w·classTerm + (1−w)·room 내림차순    // w = affinityWeight, 배포 1.0
                 동점 → room 내림차순 → 인스턴스 id (재현성)
  infeasible끼리: harm 오름차순 → room → id

classTerm = metric=="count" ? sameCount / max(모든 후보의 sameCount)     // ← v0.3 기본
                            : share (= 그 인스턴스 상주 중 같은 tier의 비율)
```

#### v0.3 변경 ③: 클래스 항이 비율이 아니라 개수다

**`share`의 결함**: 그 인스턴스 **안에서의** 비율이라 1.0에서 포화한다. **100건 중 20건이
chat인 인스턴스(0.20)가 1건 중 1건이 chat인 인스턴스(1.00)보다 낮은 점수를 받는다** —
**모으려는 규칙이 오히려 비어 있는 인스턴스를 고른다.**

`count`는 **후보 중 가장 많이 든 개수로 나눈다.** 그래서 이미 가장 많이 든 인스턴스가
1.0을 받고, **feasibility가 멈출 때까지 계속 이긴다.** 격리를 "결과로 얻는다"는 개념 ④가
실제로 작동하려면 이 형태여야 한다.

**실측 (EXP-97, 2반복)**: 동점이 **4.6% → 0.05%**, chat을 든 엔진이 **4대 → 2대**,
최다 보유 엔진이 바뀌는 횟수가 시간당 **17.8 → 7.9**. **다만 그 시점에는 최고 부하
구간에서 6.1점을 잃었고**, 그 손실이 사라진 것은 §2.3·§4.4의 두 변경이 들어간
뒤다(EXP-98: 68.9 → 76.2).

```
harm = Σ over 살릴 수 있는 상주 r (unachievable 아님, slack = r.allowanceMs − meanBefore > 0):
           min( (meanAfter − meanBefore) / slack , 10 )
     + (classHarm이면) w × (1 − share) × 10
```

**이미 예산을 넘긴 요청은 0을 기여한다.** 그래서 **무너진 인스턴스가 계속 흡수하고 멀쩡한
곳은 깨끗하게 남는다** — 강제 배치를 어디로 보낼지 정할 때 이것이 옳은 방향이다.
클래스 항은 그 비대칭이 "누가 부쉈는지"를 모른다는 구멍을 메운다.

**harm은 infeasible 정렬에만 쓰인다** = **강제 배치 목적지에만 작용한다**(45 req/s에서
결정의 약 5%). feasible 후보의 순서에는 영향이 없다.

**⚠ `class-harm`의 컴파일 기본값이 true인데 EXP-27 pass 2(2026-07-28) 이후 모든 조건이
false로 돌았다.** 즉 **기본값 그대로의 배포는 한 번도 측정된 적 없는 설정**이다.
v0.3에서 이것을 옮기지 않기로 했으므로 **모든 arm이 `FS_CLASS_HARM=false`를 명시로
계속 박는다**(fluidserve-v0.3.md §2).

### 4.6 4-way 분기 (`selectInstance`)

```
best = 정렬 1위
1) if best.feasible: commit(best, "route"); return best

2) if enablePend and canWait(best, req): return nil    // 게이트웨이가 붙듦
   canWait (:2464):
     after = best.prefillMs + queueBound
       queueBound = delaySeen ≥ 50 ? delayMean + 1.65·sqrt(delayMeanSq − delayMean²)
                                   : 300ms 고정
     토큰당: waited < ttftSloMs − after − recheckMs
     E2E:    waited < budgetMs − after − expectedToks×meanAfter − recheckMs

3) shedTest = shedFleetScale>0 ? missesOnFleet(전 후보 평균, scale) : best.missesOwnBudget
   missesOwnBudget (:2073):
     E2E:    waited + prefillMs + expectedToks×meanAfter > budgetMs
     토큰당: (shedIgnoresFirstToken 아니고 waited + prefillMs > ttftSloMs)
             or (meanAfter > nominalMs × (forceMargin ? 0.90 : 1))
   if enableShed and shedTest: registry.forget(id); 배치 로그 한 줄; return "admission rejected"

4) commit(best, "force")   // 못 기다리지만 자기 예산은 지킴 — harm 최소인 곳에
```

**⚠ `queueBound`가 신뢰할 수 없다** (§4.3의 경고): `delayMean`은 `realised − prefillEstMs`의
EWMA인데 그 `realised`가 관측이 아니라 모델이다. **따라서 "측정된 큐 지연"은 측정된 것이
아니다.** 현재 이 항은 크기가 작아(실측 함대 424 ms, 표준편차 111) 결정을 크게 바꾸지
않지만, **이 항을 키우는 방향의 변경(`--fluidserve-per-instance-delay`,
`--fluidserve-deadline-uses-delay`)은 근거가 없으므로 둘 다 기본값 false다**(§9.2).

**⚠ 첫토큰 마감 검사는 꺼져 있지 않다 — 3단에 있다.** 위 수도코드의 `missesOwnBudget`
안에서 `waited + prefillMs > ttftSloMs`가 지금도 평가된다
(`--fluidserve-shed-ignores-first-token`이 그것만 끄는 ablation이다). 기본값이 꺼져 있는
것은 **같은 검사를 1단(`feasible`)의 판정 조건에 넣는 것**
(`--fluidserve-deadline-feasible`)이다. 즉 **지금 정책은 "첫 토큰이 늦겠다"를 이유로 배치를
막지는 않지만, 거절할지 정할 때는 본다.** 1단에 넣는 것이 왜 무효였는지는 §9.1.

**⚠ shed가 `best`(실제로 만들 배치)에 묻는 것은 의도다**: 다른 인스턴스가 이론상 서비스할
수 있어도 그곳을 안 쓰는 이유는 정렬이 거른 이유(멀쩡한 상주를 밀어냄)와 같으므로,
**"만들 의향이 있는 배치가 자기 예산을 못 지키면 그 배치는 만들 가치가 없다".**
`--fluidserve-shed-signal fleet[:scale]`이 그 축의 ablation이다.

**v0.3의 이득이 이 사다리의 어느 단에서 나오나 (EXP-103)**: **2단(`canWait`)이다.**
첫토큰 추정이 커지자 "더 기다리면 예산을 못 지킨다"로 판정되어 요청이 그 자리에서
route나 shed로 갈린다.

| | route | pend | shed | force | 같은 도착에 대한 총 결정 수 |
|---|---|---|---|---|---|
| v0.2 계열 | 34.1% | **55.0%** | 9.0% | 2.0% | 220,481 |
| v0.3 r1 | 39.6% | **48.8%** | 10.3% | 1.3% | 193,748 |
| v0.3 r2 | 42.8% | **44.8%** | 11.0% | 1.5% | 179,631 |

**보류가 줄어드는 것이 이득의 경로다.** 3단(shed)에서 늦을 요청을 골라낸 것이 아니다 —
"실제로 늦은 것 중 결정이 늦을 것으로 본 비율"은 **0.0% → 1.1%/2.2%로 거의 그대로**다.
**큐가 쌓일 시간을 안 주는 것**이 실제 메커니즘이다.

### 4.7 commit (`commit` :2618, `onDispatch` registry.go:449)

```
onDispatch: byInstance[inst][id] = dispatchRecord{ stepAtDispatch=지금 stepID,
             prefillSteps=ceil(prompt/chunk), prefillEstMs=c.prefillMs, ... }
            dispatchVersion[inst]++            // → 다음 결정의 flux 재구성
            promptTokensSince[inst] += prompt; chargedPrefillSince[inst] += charge  // κ 분모
prefix.note(hashes, inst)                      // route/force만 — pend/shed는 캐시에 안 닿음
계기: dispatch_ordinal_in_step, headroom_move_in_step
배치 로그 한 줄 (v0.3에 추가):
  "[Schedule] dispatch request <uuid> fsplacement tier= waited= prefillest= prefillraw=
   prompt= decision= inst="
```

**배치 로그 줄을 넣은 이유** (2026-08-26): §4.3의 경고 때문에 **스케줄러에게 첫토큰 시간을
물을 수 없다.** 그래서 **결정이 예측한 값을 로그에 남기고, 클라이언트 기록과 오프라인으로
조인**한다. 앞머리를 기존 dispatch 줄과 맞춰 `llumnix_metrics.py`의 원본 필터에 걸리게
했고, `build_request_engine_map.py`의 정규식은 uuid 뒤에 `to ... instance <숫자>`를
요구하므로 **이 줄을 무시한다**(귀속 조인은 기존 줄을 계속 읽어야 하므로 그게 맞다).
**shed 결정에도 같은 줄을 남긴다** — 배치된 것만 보면 "예측이 예산에 못 닿는다"와
"닿는 것은 이미 거절돼서 파일에 없다"를 구분할 수 없다.

**결정 경로 전체는 cluster-view 잠금 아래 직렬**이다. 동시 결정은 없고, 직전 배치는
registry를 통해 다음 결정의 live·문턱·클래스 항에 **즉시** 반영된다. 엔진 보고 항
(`kvLogical` 등)만 다음 status까지 낡는다 — 포화에서 **status 하나당 평균 5.24건**을
판정하므로, 그 낡음을 재는 계기가 위의 둘이다. `headroom_move_in_step`이 **0이면 같은
자리를 두 번 판 것**이다.

### 4.8 온라인 보정 두 루프 — 정확한 갱신식

```
① step 시간 보정 (noteResidual(inst, predicted, measured), capacity.go:97):
   ratio = measured / predicted          // predicted는 이미 보정된 값 → 곱셈 갱신
   guard: ratio ∉ [0.2, 5] 버림
   correction[inst] *= 1 + 0.002·(ratio − 1);  clamp [0.5, 3.0]      // v0.3: 인스턴스별
   // α=0.002(시상수 ~1분)인 이유: 루프에 지연이 있어 α=0.02는 1.2~2.4로 진동 (실측)

② prefill 비용 κ (notePrefill, capacity.go:180):
   prefillMs = (measured − decodeOnly) × steps          // 구간의 prefill 시간 귀속
   computed  = prefillMs / (t_pre(chunk) − c0) × chunk  // → 엔진이 실제 계산한 토큰 환산
   ratio     = computed / 분모
     분모 = prefix off: 그 구간에 보낸 프롬프트 토큰 전체 (κ = "엔진이 실제 계산하는 비율",
             clamp [0.02, 1.0])
           prefix on:  그 구간의 예측 청구량 합 (κ = "예측의 잔차", clamp [0.25, 4.0])
   prefillFraction += 0.01·(ratio − prefillFraction)
```

**κ의 상한이 모드마다 다른 것이 핵심이다.** prefix on일 때 **1을 넘길 수 있어야 한다** —
index가 eviction을 못 봐서 hit을 과대 주장하는 바로 그 실패가 **κ > 1로 나타나야**
하기 때문이다(§2.5).

**⚠ 분모는 보정 전 양이어야 한다.** `charge = raw × κ`로 쓰면서 `κ ← computed / Σ charge`로
갱신하면 κ가 자기 측정의 입력이 되어 **평형이 κ² = computed/raw**가 되고, **κ가 참값이
아니라 참값의 제곱근으로 수렴**한다. 실측에서 κ가 0.53~0.59로 앉았고 역산한 참값이
0.28~0.35였다(EXP-67) — 필요한 것의 1.6~1.9배를 물리고 있었다.

**제거된 세 번째 루프**(:2272-2284 주석): z를 온라인 조정하는 안전 루프를 두 번 만들어
두 번 제거했다 — 예산 대비 비교판은 과부하에서 항상 발화해 z를 천장에 고정했고, 잔차 대비
판은 **상수 7개를 쓰고도 한 번도 움직이지 않았다.** z는 설정값 그대로다.

---

## §5 출력

**결정**: 인스턴스 id(route/force) / 429 "no available endpoint"(pend) /
429 "admission rejected"(shed — 게이트웨이가 즉시 클라이언트 오류로 변환).

**Prometheus 시리즈** (분석의 정본 — 요청별 로그는 V(5)이고 V(4)에서 1분 안에 회전한다):

| 시리즈 | 내용 |
|---|---|
| `..._decisions_total{decision}` | route/pend/shed/force |
| `..._infeasible_total{reason}` | unpredictable/gate/incumbents/memory/**pace_kv**/deadline (동시 실패 각각) |
| **`..._infeasible_sole_total{reason}`** | (v0.3) **그 조건 하나만 걸려서 거절된 후보 수** |
| `..._observed_step_ms` / `..._predicted_step_ms` | 보정의 두 입력 |
| **`..._instance_correction{instance}`** | (v0.3) 인스턴스별 보정 계수 |
| `..._decode_only_ms` / `..._obs_kv_tokens` / `..._obs_decode_batch` | 예측-실측 간극의 원인 분해 |
| `..._raw_step_ms` / `..._obs_steps` | 구간 원시 측정 |
| `..._prefill_duty{instance}` | 엔진이 prefill에 쓰는 시간 몫 (v0.3의 보정이 읽는 값) |
| `..._queued_prefill_tokens` / `..._arriving_prefill_tokens` | `effectivePrefill`의 두 입력 |
| `..._gate_allowance_ms{instance}` | 인스턴스가 무엇에 gate되는지 (전용 인스턴스 표식) |
| `..._headroom_tokens` / `..._cap_kv_tokens` / `..._projected_kv_tokens` | 용량 상태 |
| `..._flux_{evaluations,flips}_total{level}` | 투영이 결정을 바꾼 횟수 (candidate/decision/target) |
| **`..._placement_{predicted,realised}_ms_total{instance}`, `..._placement_samples_total`** | (v0.3) ⚠ **둘 다 모델이다** — §4.3 |
| **`..._placement_joint_total{tier,cell}`** | (v0.3) 예측과 결과의 2×2 표 ⚠ 같은 이유로 신뢰 불가 |
| `gateway_scheduling_{waited,rejected,gave_up}_total`, `..._wait_milliseconds` | 게이트웨이 쪽 |
| `dispatch_ordinal_in_step`, `headroom_move_in_step` | 이중 판매 감시 |

**⚠ 새 메트릭을 추가하면 `llumnix_metrics.py`의 화이트리스트에도 넣어야 한다.** 시리즈
이름 목록으로 거르므로 **없으면 결과 디렉토리에 저장되지 않는다** — EXP-96이 그렇게
카운터 셋을 통째로 잃었다.

**로그** (`scheduler_dispatch.log`, 러너가 원본에서 grep해 저장):
- `[Schedule] dispatch request <uuid> to <x> instance <id> for prefill` — **요청→엔진 귀속의
  원천.** 부하 최고 구간에서 유실될 수 있다(CLAUDE.md 함정 D).
- `[Schedule] dispatch request <uuid> fsplacement ...` — (v0.3) **결정이 예측한 값.**
  귀속 정규식은 이 줄을 무시한다.

**첫토큰의 진짜 값을 재는 법** (스케줄러가 못 재므로):
```
배치→첫토큰 = metrics.csv 의 first_token_latency − (배치 로그의 배치 시각 − request_ids.jsonl 의 전송 시각)
```
`analysis_scripts/request_level/exp101_first_token_truth.py`가 그 조인을 하고 매칭률은 100%다.

---

## §6 상수 전체 표

| 상수 | 값 | 무엇 | 왜 그 값인가 |
|---|---|---|---|
| `horizonSteps` | 100 iteration | 계획 지평 | 디코드 성장이 "요청·step당 1토큰"이 되는 단위 |
| `zSafety` | 1.65 | outflow 하한과 큐지연 상한의 σ 배수 | 단측 95% |
| `fsAllowanceUtilisation` | 0.90 | gate에 곱하는 여유 | — |
| `fsMemorySafety` | 0.95 | 물리 KV 사용 가능 비율 | — |
| `fsHarmCap` | 10.0 | 상주 하나의 harm 상한 = 클래스 항 스케일 | — |
| `fsCorrectionAlpha` / 경계 | 0.002 / [0.5, 3.0] | step 보정 EWMA | α=0.02는 1.2~2.4로 진동(실측) |
| `fsPrefillAlpha` / 경계 | 0.01 / [0.02,1.0] 또는 [0.25,4.0] | κ EWMA | 상한이 모드마다 다른 이유는 §4.8 |
| `fsPrefillDutyAlpha` | 0.1 | duty·meanMs·kvSlope EWMA | 유효 창 ≈ 5초 |
| **`fsMinPrefillDuty`** | **0.05** | (v0.3) 이 아래면 첫토큰 추정을 안 늘림 | 스무 배 넘는 배수는 보정이 아니다. 실측 duty는 큐가 있으면 0.43~0.46, 없으면 0.16~0.29 |
| `fsPlacementDelayAlpha` / 최소 표본 / 상한 | 0.01 / 50 / 60 s | 큐 잔차 | ⚠ 이 잔차가 관측이 아님(§4.3) |
| `fsSlowFirstTokenMs` | 10,000 ms | 2×2 표의 경계 | deepresearch의 TTFT 예산 |
| `ttftSafetyMs` | 300 ms | 표본 <50일 때의 고정 큐 여유 | — |
| `fsRetireSurvival` / `fsMaxRecordAgeMs` | 0.02 / 20 min | 상주 기록 퇴역 | — |
| `fsArrivalTTLMs` | 5 min | 도착 기록 보존 | 게이트웨이 hold 창(35 s)보다 충분히 길게 |
| `fsOfferedWindowMs` / `fsOfferedRateAlpha` | 1,000 ms / 0.05 | 도착률 계기 | 텔레메트리 전용 |
| prefix 블록 / 용량 | 16 tok / 500,000 블록 LRU | FNV-1a 체인 해시 | — |
| chunk | 엔진 `MaxNumBatchedTokens` (fallback 8192) | prefill step 계산 | 설정이 아니라 엔진이 보고 |
| `observeInstance` guard | steps>10,000 / elapsed>30 s / nDecode<1 | 표본 버림 | 재시작·정지·유휴 걸침 |
| `noteResidual` guard | ratio ∉ [0.2, 5] | 〃 | — |
| 게이트웨이 (FluidServe만) | 천장 35,000 / 재시도 500 ms | 보유가 선택지인 정책만 필요 | 다른 정책은 5,000/1,000 |
| 프로파일 실값 | c0=16.36 ms, c_kv=1.282e-5, c_n=0.0764 (R²=0.952) | decode 법칙 | 측정 |
| 〃 | t_pre: 1024→80.1 / 4096→264.2 / 8192→519.7 ms | prefill step | 측정 |

---

## §7 플래그 (30개 전부, `--fluidserve-*`)

**★ = v0.3에서 기본값이 바뀐 것.**

| 플래그 | 기본값 | 무엇 |
|---|---|---|
| `profile-path` | "" (필수) | §3.3 파일. 없거나 깨지면 기동 거부 |
| `class-budgets` | "" (배포 `25:e2e:30000`) | tier 예산 형태 재정의 |
| `horizon-steps` | 100 | 지평 (≤0이면 기동 거부) |
| `z-safety` | 1.65 | σ 배수 |
| `ttft-safety-ms` | 300 | 큐 여유 폴백 |
| `enable-pend` / `enable-shed` | true / true | 보유 / 거절 |
| `shed-signal` | `coupled` | 거절이 best를 보나 함대 평균을 보나 (`fleet[:scale]`) |
| `oracle-length` | false | 요청별 출력 길이 힌트 (EXP-64) |
| `enable-affinity` / `affinity-weight` | true / 1.0 | 클래스 선호와 세기 w |
| ★ `affinity-metric` | **`count`** (v0.2: `share`) | 클래스 항의 정의 — §4.5 |
| `prefix-aware` | true (v0.2부터) | 인스턴스별 prefill 비용 |
| `prefix-calibration` | true | κ 적용 |
| `prefix-block-tokens` / `prefix-capacity` | 16 / 500,000 | index 크기 |
| `class-pin` | "" | 정적 고정 (ablation 전용; 파싱 실패 시 기동 거부) |
| `enable-flux` | true | 흐름 수지 (끄면 순수 level 제어기) |
| `class-harm` | true ⚠ | harm의 클래스 항. **모든 실험이 false로 돌았다** — §4.5 |
| `force-margin` | false | force 판정에도 0.90 |
| `own-budget-gate` | false | gate에서 인스턴스 min 제거 |
| `gate-slack` | 1.0 (<1이면 기동 거부) | 인스턴스 min을 몇 배까지 넘나 |
| ★ `per-instance-correction` | **true** (v0.2: false) | step 보정을 인스턴스별로 — §2.3 |
| ★ `memory-uses-pace-cap` | **true** (v0.2: false) | 메모리 판정이 `min(capKv,capMem)` — §4.4 |
| ★ `prefill-interleave-aware` | **true** (v0.2: 없었음) | 첫토큰 추정이 duty를 반영 — §4.4 |
| `deadline-feasible` | false | feasibility에 TTFT 항 (EXP-87·100에서 두 번 측정, 둘 다 무효과) |
| `per-instance-delay` | false | 큐 지연을 인스턴스별로 ⚠ 근거 없음(§9.2) |
| `deadline-uses-delay` | false | 마감 판정에 큐 지연을 더함 ⚠ 근거 없음(§9.2) |
| `shed-ignores-first-token` | false | shed 판정에서 첫토큰 항 제거 ⚠ 근거가 철회됨(§9.2) |
| `kv-slope-projection` | false | 투영을 실측 기울기로 (후보 H2) |

**잘못된 값이 조용히 대조군이 되는 것을 막는 장치가 셋** — `class-budgets`·`class-pin`·
`shed-signal`의 파싱 실패는 **panic**이다. bool 플래그를 `--flag false`로 써서 pflag가
그것을 **true로** 설정하는 바람에 ablation 4시간을 잃은 뒤의 규칙이다.

**⚠ 기본값을 옮기면 그 플래그를 명시하지 않은 arm의 뜻이 바뀐다.**
`set_scheduler_profiling.py`가 이번 호출이 요구하지 않은 ablation 플래그를 **제거**해
컴파일 기본값으로 되돌리기 때문이다. v0.3에서는 드라이버의 `set_arm`이 **v0.2 값을 먼저
써 놓고** 각 arm이 필요한 것만 덮어쓰게 고쳤다(`run_exp104_v03affinity.sh`).

---

## §8 코드량과 통합

| 부분 | 행 수 |
|---|---|
| `fluidserve.go` (결정 경로·관측·selector) | 2,964 |
| `fluidserve_registry.go` (기록·회계·예산·큐 잔차) | 908 |
| `fluidserve_capacity.go` (지연 모델·보정 둘) | 469 |
| `fluidserve_profile.go` (길이 분포) | 255 |
| `fluidserve_prefix.go` (prefix index) | 211 |
| **정책 합계** | **4,807** |
| 단위 테스트 | 2,572 |
| 플래그 정의 (config.go) | 약 260 |

**엔진(vLLM)은 한 줄도 바꾸지 않았다.** 게이트웨이는 기존 Llumnix hold loop
(`scheduler_client.go:176-235`)를 재사용하고, 더한 것은 같은 429 본문을 두 의미로 가르는
수십 행뿐이다.

**통합 지점 여섯**:
1. `scheduling_policy_registry.go`의 `case SchedulingPolicyFluidserve` — Llumnix dispatch
   policy 인터페이스 구현. **기존 filter 파이프라인은 안 쓴다**: 보유·거절이 선택지라
   모든 인스턴스를 한 자리에서 비교해야 하므로 selector 하나가 전부다(§1 ①).
2. `verifySchedulingPolicy` 화이트리스트 — 빠지면 기동 panic이고 유닛 테스트로는 안 잡힌다.
3. `schedulingCtx`에 `fluidserveRequest`/`fluidserveFlux` 필드 추가.
4. 상태 원천은 **Llumnix cmsView 그대로** — 자체 계측 없음.
5. 배포는 hostPath `bin/` + `set_scheduler_profiling.py`(플래그 주입·기동 줄 검증 `verified:`).
6. 프로파일 생성 `gen_fluidserve_profile.py`.

**기준선 다섯**(FluidServe·Llumnix·Llumnix SLO·PolyServe·vLLM router)이 **같은
게이트웨이·같은 status 경로를 공유한다** — evaluation의 "제어 평면만 다르다"가 구조에서
나온다. **예외 하나**: 게이트웨이 대기 창이 FluidServe만 35,000/500이고, EXP-83·84가
그것을 측정했다(우리 수치가 보수적인 방향).

---

## §9 알려진 결함과 열린 축

**v2(2026-08-20)의 §9.1이 두 번 측정되어 답이 나왔으므로 이 절을 전면 개정했다.**

### 9.1 첫토큰 마감을 feasibility에 넣는 것은 답이 아니었다 — 그런데 다른 것이 답이었다

**v2가 적었던 결함**: `waited + prefillMs > ttftSloMs`가 계산되고 코드에 있는데
`if best.feasible { route }`가 먼저 끝나 도달하지 않는다. 전용 인스턴스의 느슨한
gate(100 ms)가 그곳을 항상 feasible하게 보이게 하고, prefill 줄을 아무도 묻지 않는다.

**두 번 켜 봤고 두 번 다 결과가 안 움직였다**:
- EXP-87 — 조건이 거절의 1.1~8.3%에서 발화했으나 무효과.
- EXP-100 — 완주한 두 반복에서 **60,440건과 61,402건**(전체 거절 사유 발화의 4.4%·4.6%)
  발화, deepresearch admitted가 **80.5 → 80.6**.

**왜 안 움직였나** (EXP-101b가 답): 그 판정이 읽는 추정값이 **10초 예산에 닿지 않는다.**
배치된 deepresearch 15,219건 중 예측이 10,000 ms를 넘은 것이 **0.0%**인데 실제로는
**16.3%가 넘었다.** 즉 조건이 거절한 후보는 **이미 다른 조건에 걸려 있던 것들**이다.

**⚠ 두 변경이 같은 양을 건드려서 서로 헷갈린다. 이름을 갈라 둔다.**

| | 무엇을 했나 | 결과 | 지금 |
|---|---|---|---|
| **추정값을 고쳤다** (`--fluidserve-prefill-interleave-aware`) | 대기 prefill 항을 엔진의 prefill duty로 나눈다 — 그 항이 "엔진의 prefill 시간을 얼마나 먹는가"에 답하고 "첫 토큰이 언제 나오는가"에 답하지 않고 있었다 | dr admitted **83.7 → 96.5/94.7** | **기본 켬 (v0.3)** |
| **그 추정을 1단 판정에 넣었다** (`--fluidserve-deadline-feasible`) | `feasible`이 첫토큰 마감으로 route를 막게 한다 | **반증** | **기본 꺼짐** |

**진짜 원인은 추정값의 눈금이었고, 그것을 고치자(v0.3) 효과가 났다.** 다만 **경로가
예상과 달랐다** — 판정 조건이 늦을 요청을 골라낸 것이 아니라(**본 비율 0.0% → 1.1%/2.2%**)
`canWait`가 보류를 그만두게 해서 **큐가 쌓이는 상태에 도달하지 않았다**(§4.6).

**남은 것**: 받아들인 deepresearch의 **3.5%/5.3%가 여전히 TTFT로 깨진다**(토큰당 예산은
0.01~0.03%). 그 실패는 **50~58분(도착률 45.0 req/s = 정적 무릎 28.0의 1.6배)에 몰려
있고 deepresearch를 든 한두 엔진에만 있다.** **용량 한계인지 정책이 아직 고칠 수 있는
것인지 못 가렸다** — 정적 sweep이 그것을 가른다.

### 9.2 ⚠ 스케줄러는 자기가 예측하는 양을 관측하지 못한다

**§4.3의 그 사실이 이 시스템의 구조적 한계다.** 엔진은 요청별 진행을 보고하지 않으므로,
`reconcile`이 "이 요청이 토큰을 내기 시작했다"를 **엔진의 전역 step 카운터가 모델이 계산한
prefill step 수를 지났는가**로 판정한다. **그 요청에 대한 관측이 아니다.**

**따라서**:
- **`decodeStartMs` 기준 잔액**은 실제보다 이른 시각에서 시작한다(보수적 방향).
- **`canWait`의 `queueBound`는 측정된 값이 아니다.** 그 위에 세운 두 플래그
  (`per-instance-delay`, `deadline-uses-delay`)는 **근거가 없어 기본값 false**다.
- **`shed-ignores-first-token`의 근거도 철회됐다** — "추정값이 4.9배 과대예측한다"가
  모델 대 모델의 비였다.
- **첫토큰 관련 양을 스케줄러 쪽 값으로 재려고 하면 항상 모델 대 모델을 비교하게 된다.**

**고칠 방법 둘**(둘 다 미구현):
- **엔진이 요청별 첫토큰 시각을 보고**하게 한다 — vLLM에 손대야 하고, "엔진을 안 고쳤다"는
  §8의 성질을 잃는다.
- **엔진의 집계 TTFT 히스토그램**(`vllm:time_to_first_token_seconds`)을 되먹임으로 쓴다 —
  요청별은 아니지만 인스턴스별 보정 계수는 만들 수 있다. **이 방향이 §2.3의 보정 루프와
  같은 형태다.**

### 9.3 ⚠ 어느 인스턴스가 그 역할을 맡는지가 run마다 다르다 — 논문에 반드시 밝힌다

**클래스 선호는 "이 클래스를 이 인스턴스에 둔다"고 정하지 않는다**(§1 ④). 결과로 한두
엔진에 모이는데, **어느 엔진이 그 역할을 맡는지는 run마다 다르고 한 run 안에서도 옮겨
다닌다.**

**v0.3 두 반복이 그 예다**: r1은 두 엔진(8002, 8003)에 나눠 쌓이고
(대기 prefill p90 33,250 / 22,613, preemption 369 / 275), **r2는 8002 하나에 몰린다**
(p90 54,030, preemption 936, **나머지 셋은 0**). 함대 합계는 656 대 936으로 비슷한데
**모양이 다르다.**

**그래서 결과를 낼 때**:
- **엔진 단위 그림은 한 run만 보여주면 안 된다.** 우연히 고른 run의 모양이 그 정책의
  성질처럼 읽힌다.
- **집중도는 창별로 재고 어느 엔진이 그 창에서 최다인지도 같이 낸다.** 전 구간 풀링은
  옮겨 다니는 대상을 "퍼져 있다"로 잘못 읽는다(CLAUDE.md 함정 E).
- **총계 지표는 세션을 잘 건너가지만 특정 엔진에서 일어나는 사건은 안 건너간다.**
  같은 설정의 두 run에서 preemption이 26% 움직인 기록이 이미 있다.

**무엇이 그 역할을 정하는지 모른다.** 초기 몇 초의 도착 순서일 가능성이 크지만
측정하지 않았다.

### 9.4 두 보정 루프가 같은 잔차를 나눠 갖는다

`correction`(§4.8 ①)은 "예측한 iteration 시간이 실측과 다른 만큼"을 흡수하고,
`prefillDuty`(§4.1)는 "디코드 법칙이 설명하지 못하는 시간"을 prefill로 귀속한다.
**둘의 정의가 겹친다** — 디코드 법칙이 틀리면 그 오차가 duty에도 들어간다.

v0.3에서 duty가 첫토큰 추정에 곱해지면서 **이 겹침이 결정에 닿는 경로가 하나 늘었다.**
분리되지 않았고, 분리하려면 엔진이 prefill 시간을 따로 보고해야 한다.

### 9.5 gate 축이 양 끝에서 반대로 실패한다

`gateSlack`이 1이면 **인스턴스 위 가장 빡빡한 명목 예산이 그 인스턴스 전체를 gate**하고,
크면 그 보호가 사라진다. 양 끝이 각각 다른 방식으로 실패하는 것이 둘 다 측정됐다
(fluidserve-v0.2.md §6.1). **지금 가장 큰 미해결 설계 축이다.**

특히: **인스턴스 위에 chat이 하나라도 있으면 그 인스턴스의 허용 속도가 chat 예산인
50 ms가 되므로, 포화 구간에서는 모든 인스턴스가 50 ms로 고정되어 route 결정이 거의 나오지
않는다.** 그래서 클래스가 섞이는 것이 되돌릴 수 없는 상태가 될 수 있다.

### 9.6 게이트웨이 천장이 함대 배치를 부작용으로 정한다 (EXP-84)

정책은 클래스가 모이는 것을 선호하지만, **그 선호가 만드는 상태 — 한 클래스가 함대의
얼마를 자기 예산으로 gate하는가 — 를 평가하는 변수가 없다.** 그 상태를 실제로 정하는 것은
게이트웨이의 대기 천장이고, 그것은 정책 밖에 있다.

### 9.7 요청 안 p90은 라우팅으로 닿지 않는다 (EXP-85)

chat 자신의 prefill만으로 요청 안 토큰당 시간의 변동계수 φ > 0.10이다(필요: < 0.10).
시간 배치를 바꿔도 안 된다. **남는 수단은 admission 양뿐이다.** 판정은 평균·누적
deadline(문헌 관행)으로 하고 분위수는 별도 표로 낸다.

### 9.8 swe의 E2E 실패가 손대지지 않았다

받아들인 swe의 **약 20~23%가 30초 E2E 예산을 넘긴다.** v0.3의 어느 변경도 이것을 거의
움직이지 않았다(23.49% → 22.67% / 19.72%). E2E 클래스는 대기·prefill·디코드가 한 계좌라
다른 성질이고, **이 시스템의 다음 축일 수 있다.**

### 9.9 `class-harm` 기본값이 측정된 적 없다

컴파일 기본값이 true인데 **EXP-27 pass 2 이후 모든 조건이 false로 돌았다.** v0.3에서
옮기지 않기로 했으므로 **모든 arm이 명시로 false를 계속 박아야 한다**(§4.5).

### 9.10 정적 도착률 sweep에서 v0.3이 확인되지 않았다

v0.3의 근거는 **mix-shift 한 시간 trace뿐**이다. 정적 sweep은 §9.1의 "50~58분 실패가
용량인가 정책인가"도 같이 답한다.
