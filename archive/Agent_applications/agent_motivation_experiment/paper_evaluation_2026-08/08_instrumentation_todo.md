# 계측을 더해야 답할 수 있는 것 둘 — 설계와, 밤사이 하지 않은 이유

**2026-08-21. 코드를 쓰지 않았다.** 이유는 §0이다.

## 0. 왜 밤사이 하지 않았는가

두 계측 다 **새 메트릭 이름을 만드는 것**인데, 러너의 수집기
`Agent_applications/agent_motivation_experiment/llumnix_metrics.py`가 **명시적 허용 목록**으로
동작한다(101~111행에 `scheduler_fluidserve_*`가 한 줄씩 적혀 있다). 즉 **새 메트릭을 넣으려면
그 파일을 고쳐야 한다.**

**그 파일은 측정 경로다.** 러너의 `/work`가 이 실험 저장소를 hostPath로 마운트하고 조건마다
새 Job이 소스를 다시 읽으므로, **EXP-88이 도는 동안 고치면 조건마다 다른 코드로 측정된다**
(CLAUDE.md 함정 C). 그래서 **EXP-88과 EXP-89가 끝난 뒤에 한다.**

## 1. agent 클래스를 왜 거절하는지 — shed 자리의 세 항

**[B4](07_why_agent_is_refused.md)가 후보를 하나로 좁혔고**, 그것을 확인하려면 거절이 일어나는
순간의 세 항을 클래스별로 봐야 한다. 판정식은 `fluidserve.go:1742-1744`이다:

```go
if req.isE2E {
    return waited + c.prefillMs + req.expectedToks*c.meanAfter > req.budgetMs
}
```

**이미 그 자리에 `klog.V(5)` 한 줄이 있는데 `-v 4`로 도는 run에서는 안 찍힌다.** 그리고
**로그는 부하가 높으면 사라지므로**(`motivation.md` §9.2가 한 시간 PolyServe에서 86.4%를
기록했다) **메트릭이어야 한다.**

**더할 것** — tier 라벨을 붙인 histogram 셋. 수집기가 histogram의 `_sum`과 `_count`를 잡으므로
평균이 나오고, 그것으로 충분하다(지금 물음은 "`meanAfter`가 함대 pace보다 크게 큰가"이므로
분위수가 필요 없다).

| 이름 | 무엇을 담는가 | 왜 |
|---|---|---|
| `scheduler_fluidserve_shed_mean_after_ms{tier}` | 거절 시점의 `best.meanAfter` | **후보 자체.** 함대의 관측 pace(45~48 ms)와 비교한다 |
| `scheduler_fluidserve_shed_expected_toks{tier}` | 그때의 `req.expectedToks` | 다른 후보. 프로파일 값(tier 25 = 494)과 대조한다 |
| `scheduler_fluidserve_shed_slack_ms{tier}` | `budgetMs − waited − prefillMs` | 남은 예산. 셋을 합치면 판정식이 그대로 복원된다 |

**판정 규칙(실행 전에 적는다)**: `shed_mean_after_ms`의 tier 25 평균이 같은 조건의
`observed_step_ms` 중앙값(45~48 ms)의 **1.3배를 넘으면** "얹은 뒤의 pace가 원인"이 확인된다.
**1.1배 안이면 반증**이고 그때는 `expected_toks` 쪽을 본다.

**비용**: Go 약 10줄 + 수집기 3줄 + **이미 있는 조건 재사용**(새 실험 없이 3 도착률만 다시
돌리면 된다, 약 1.5시간).

## 2. 결정 하나에 얼마가 드는가 — C2

**계측이 아예 없다.** `pkg/scheduler/policy/`에 결정 시간을 재는 histogram이나 어떤 duration
지표도 없다(확인함). 리뷰어가 반드시 묻는 것이고 지금 답이 하나도 없다.

| 이름 | 무엇 |
|---|---|
| `scheduler_fluidserve_decide_us` | 요청 하나의 결정에 걸린 시간(마이크로초). histogram |
| `scheduler_fluidserve_candidates` | 그 결정에서 평가한 인스턴스 수. histogram |

**둘을 같이 내는 이유**: 정책이 결정마다 schedulable 인스턴스 전부를 평가하므로, **비용이
인스턴스 수에 어떻게 붙는지**가 질문이다. 둘을 같이 내면 이 하드웨어의 네 대에서 기울기를
추정할 수 있다.

⚠ **인스턴스를 8/16/32/64로 늘리는 실측은 이 하드웨어에서 불가능하다**(8 GPU에
Llama-3.1-70B). **결정 함수만 떼어 Go 벤치마크로 재고, 논문에 그렇게 적는다** — 클러스터에서
잰 것과 벤치마크에서 잰 것을 구분해서.

**판정 규칙(실행 전에 적는다)**: 결정 지연의 p99가 요청 E2E의 **1%를 넘으면** 설계를 다시
봐야 한다. 이 워크로드의 chat E2E가 17~19초이므로 1%는 약 170~190 ms이고, 실제 값은 그보다
서너 자릿수 작을 것으로 예상한다 — **예상이 틀리면 그 자체가 결과다.**

**비용**: Go 약 15줄 + 수집기 2줄 + 3 도착률 2반복 (약 2시간), 그리고 벤치마크는 클러스터
없이 돈다.

## 3. 순서

**둘이 같은 바이너리 변경이므로 한 번에 한다.** 배포 절차는 CLAUDE.md의 것을 그대로 따른다 —
`cp`로 덮어쓰면 `Text file busy`로 막히므로 새 이름으로 쓰고 `mv`로 넣고, **배포 뒤에 파드
안에서 `md5sum /proc/1/exe`로 확인한다.** 그것이 유일한 authority다.
