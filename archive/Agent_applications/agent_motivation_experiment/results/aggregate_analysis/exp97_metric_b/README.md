# EXP-97 그림 — 클래스 선호의 정규화를 고쳤을 때 한 시간 믹스 이동 trace에서 무엇이 달라지나

**판정과 표의 정본은 `experiments/EXP-97_normalisation-on-the-hour.md` §5·§6.**
여기는 그림마다 **무엇을 주장하는지 / 무엇을 말하지 않는지**만 적는다.

두 디렉토리가 두 반복이다. **한쪽만 보고 판정하지 않는다** — 이 실험에서 고친 방식의
반복 폭이 총계 4.2점, 마지막 구간 11.5점이다.

| 디렉토리 | 반복 |
|---|---|
| `exp97_metric/` | 반복 1 (`exp97r1_*`, 선호 끔은 `exp93nr1`) |
| `exp97_metric_b/` | 반복 2 (`exp97br1_*`, 선호 끔은 `exp93nbr1`) |

## arm 셋을 부르는 이름

| 그림의 범례 | 문서에서 부르는 이름 | 정렬이 무엇으로 순위를 매기나 | 디렉토리 |
|---|---|---|---|
| `preference off` | **선호 끔** | 클래스 항이 없다 (`room`만) | `fsnoaff` |
| `preference, shipped` | **지금 방식** | 그 인스턴스 안에서 같은 클래스가 차지하는 **비율** | `fspfx` |
| `preference, fixed` | **고친 방식** | 그 인스턴스가 든 같은 클래스의 **개수** | `fscount` |
| `llm-d` | **llm-d** | 우리 정책이 아니다 — 자체 external processor가 예측 지연으로 고른다 | `llmdslo` |

**llm-d는 EXP-93 세션의 것을 그대로 쓰고 이 바이너리로 재측정하지 않았다.** 클래스 선호의
개수 지표를 넣은 커밋이 바꾼 것은 `pkg/scheduler/policy/fluidserve.go`와 그 테스트, 그리고
`cmd/config/config.go`의 플래그 정의 하나뿐이고 **llm-d 경로의 파일은 하나도 없다.**
`sortCandidates`가 `fluidserve.go` 밖에 나오는 유일한 곳은 그 플래그의 도움말 문자열 안이라
호출이 아니며, llm-d 드라이버는 `llmd` 네임스페이스의 세 파드만 재시작하고
`deploy/scheduler`·`deploy/gateway`를 건드리지 않는다.

## ⚠ llm-d는 요청→엔진 귀속이 원리적으로 불가능하다

그 arm은 Llumnix 스케줄러를 거치지 않으므로 `scheduler_dispatch.log`에 줄이 없다. 실제로
`request_engine.csv`를 만들면 **53,945행 전부 `engine_port`가 결측**이고 스크립트가
`llmdslo: 0 of 53,130 admitted requests attributed (0.0%)`로 명시한다.

| 무엇을 쓰나 | llm-d가 들어가나 |
|---|---|
| 엔진 자신의 Prometheus 값 — 배치·KV·큐·preemption·prefix hit | **들어간다** |
| 클라이언트 기록 — 달성률·거절률·goodput·클래스별 | **들어간다** |
| 요청→엔진 귀속 — 엔진별 클래스 구성, 분리 지표, chat 없는 인스턴스 비율 | **안 들어간다** |

**즉 분리 지표 표에 llm-d 행이 없는 것은 결함이 아니다.**

## 그림

| 파일 | 무엇을 주장하나 | 말하지 않는 것 |
|---|---|---|
| `trace_shape.png` | 세 arm이 같은 도착 과정과 같은 믹스 일정을 받았다 | arm별 차이는 여기 없다 |
| `exp97_classpreference_normalisation_shift_timeline.png` | **주 그림.** 8패널. C·D가 두 분모의 달성률, E·F가 클래스별, H가 함대 KV | 90초 창이므로 그보다 짧은 사건은 안 보인다 |
| `class_goodput_hour.png` | **손실의 위치.** 고친 방식의 chat이 **약 53~58분에만** 5,000 → 500 tok/s로 무너지고 나머지는 겹친다 | 어느 인스턴스에서 무너졌는지는 엔진 그림에서 본다 |
| `compare_rpm_hour.png` | 네 arm을 열로 놓고 배치·큐·KV·디코드를 행으로 | 행마다 y축이 열 사이에 공유된다 — 열 하나만 보고 "한가하다"고 읽으면 안 된다 |
| `exp97_shift_engine_timeline.png` | 엔진 넷을 따로 그린 시계열 | |
| `exp97_shift_engine_requests.png` | 엔진별 요청 수·클래스 구성·chat 토큰당 시간 | 스케줄러 로그 조인이므로 부하 최대 구간에서 줄이 빠질 수 있다 |
| `engine_*` / `tokens_*` / `llumnix_*` | run 하나씩의 엔진 계층·토큰 처리량·컨트롤플레인 | 전부 엔진과 게이트웨이 자신의 Prometheus 값이라 클라이언트 지표 결함에 영향받지 않는다 |
| `separation_hour.csv` | 클래스당 유효 인스턴스 수, 이동량, 최다 보유 교체 횟수, chat 없는 인스턴스 비율 | |

## 그림에서 읽어야 하는 것 셋

**① 정렬 수정은 작동한다** — 최다 보유 인스턴스 교체가 chat에서 시간당 58.3/53.4(선호 끔) →
17.8/7.9(지금 방식) → **7.9/5.9(고친 방식)**이고, chat을 든 적 있는 인스턴스가 4 → 3 → **2**곳이다.

**②' 함대 불균형이 그 차이를 한 수로 요약한다** — 창별로 잰 "가장 바쁜 엔진 / 가장 한가한
엔진"의 평균이 선호 끔 **1.33배**, llm-d **1.79배**, 지금 방식 **4.95배**, 고친 방식 **34.99배**다
(반복 1). 고친 방식은 한 엔진에서 preemption 1,172회에 KV 최대 100%, 큐 6.1인데 다른 두
엔진은 preemption 0에 큐 0.3이다.

**② 그 대가는 메모리다** — 주 그림 패널 H에서 고친 방식의 함대 KV가 48~60분에 **70~77.5%**이고
지금 방식은 60~65%다. 같은 구간에서 패널 B의 거절률이 50%까지 오르고 패널 D의 admitted
달성률이 40%로 떨어진다 — **더 거절하고도 받아들인 것을 못 지켰다.**

**③ 그 구간이 trace의 최고 부하다** — 패널 A에서 48~60분이 35~46 req/s다.

## ⚠ chat 없는 인스턴스 비율의 두 열은 다른 양이다

`separation_hour.csv`의 `nochat_pct`(클라이언트 기록)와 `gate_nochat_pct`(스케줄러 게이지)가
**반복 1의 고친 방식에서 12.4 대 42.5로 30점 벌어지고 반복 2에서는 36.2 대 36.6으로 일치한다.**

클라이언트 기반은 요청이 dispatch된 인스턴스에 시작부터 종료까지 상주한다고 보고, 스케줄러
게이지는 **남은 예산으로 더 이상 달성할 수 없게 된 요청을 게이트에서 뺀다.** 반복 1이 chat
토큰당 초과가 41.9%였던 run이고 반복 2는 18.9%였으므로, **불일치의 크기가 예산 초과의 크기를
따라간다. 결함이 아니라 이 실험이 재려던 기제의 결과다.**

## 다시 만들려면

```bash
cd Agent_applications/agent_motivation_experiment
bash analysis_scripts/redraw_hour_trace_exp97.sh results/aggregate_analysis/exp97_metric      # 반복 1
bash analysis_scripts/redraw_hour_trace_exp97.sh results/aggregate_analysis/exp97_metric_b b  # 반복 2
```

⚠ **2026-08-25에 고친 것 셋.**

1. **`fsnoaff`를 `exp38_policy_compare.py`의 `ARM_ORDER`·`ARM_LABEL`에 등록했다.** 등록 전에는
   그 스크립트가 종료하며 이유를 찍는다(조용히 빠지지 않는다). `fspnoaff`와 다른 arm이다 —
   그 이름은 EXP-73 사다리의 것이고 다른 세션에 있다.
2. **`fscount`와 `fsnoaff`를 `exp41_engine_view.py`의 `ARMS`에도 등록했다.** 이쪽에는 종료
   검사가 없어서 **run 넷을 준 실행이 arm 둘짜리 그림을 내고 아무 말도 안 했다.**
3. **엔진 계층 그림이 run을 glob으로 찾지 않고 이름으로 받는다**(`--run ARM=DIR`). 네 arm이
   세 세션에서 오므로 패턴 하나로 지목할 수 없고, 게다가 `*r1_fspfx_shift*`가 `exp97r1`과
   `exp97br1`을 **둘 다** 매칭해 `sorted()[-1]`이 뒤엣것을 골랐다 — **반복 1을 그리라고 했는데
   반복 2의 엔진 데이터가 들어갔다.**

⚠ 세션 태그가 arm마다 다르다: 선호 끔은 `exp93nr1`/`exp93nbr1`, llm-d는 `exp93r1`/`exp93br1`,
우리 둘은 `exp97r1`/`exp97br1`이다.
