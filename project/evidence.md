# 논문 수치의 근거 — evidence.md

제출본(NSDI '27 Fall #515, `ms_dev/submitted_version/nsdi27fall-paper515.pdf`, 2026-09-23)이 기대는 수치를 전부
한 곳에 적는다. 각 수치마다 **어느 실험의 어느 run 집합에서 나왔고, 반복이 몇 번이고, 어떤 채점 규칙으로 셌고,
옆에 무엇을 같이 적어야 하는지**를 둔다. 작성 2026-10-11. 확인한 파일은 각 줄에 적었다.

표기: **✓** 파일에서 같은 값을 확인함 · **✗** 파일의 값과 다름(맞는 값을 같은 칸에 적음) · **⚠** 출처 파일을
찾지 못했거나 일부만 확인함.

---

## 1. 사용법

- `project/` 안에서 **수치는 이 파일에만 적는다.** 다른 문서는 이 파일의 절을 가리키고 수치를 옮겨 적지 않는다.
  옮겨 적으면 재측정 뒤 낡은 사본이 남는다(옛 기록에서 같은 오류를 여러 번 고쳤다).
- 재측정으로 값이 바뀌면 **행을 지우지 않는다.** 값 칸을 새 값으로 바꾸고, 행 끝의 "대체됨" 칸에
  `옛 값 → 새 값, 날짜, 이유, 새 run 이름`을 적는다. 인용을 금지해야 하는 옛 값이면 §7에도 한 줄 넣는다.
- 그림을 다시 그리면 `Agent_applications/agent_motivation_experiment/paper_figures/<이름>.csv`가 그려진 값의
  정본이다. 이 파일의 값과 CSV가 다르면 CSV를 열어 이 파일을 고친다.
- 근거 문서는 2026-10-11에 `archive/`로 옮겨졌다. 아래에서 `archive/EXP-NNN`은
  `archive/Agent_applications/agent_motivation_experiment/experiments/EXP-NNN_*.md`, `archive/README(그림)`은
  `archive/Agent_applications/agent_motivation_experiment/paper_figures/README.md`, `archive/STATUS`는
  `archive/llumnix_reproduce/ms_dev/notes/STATUS.md`의 줄임이다.

---

## 2. 모든 실험에 공통인 조건

| 항목 | 값 | 근거 |
|---|---|---|
| 기본 함대 | B200 8장 한 노드, Llama-3.1-70B-Instruct 인스턴스 4대, 각 TP=2, 엔진은 수정하지 않은 vLLM, migration 끔 | archive/EXP-108, `fig_exp131_horizon.py` 머리말 |
| 둘째 모델 | Qwen2.5-72B 4대(한 시간 trace와 지평 sweep만) | archive/EXP-113, archive/EXP-136 |
| 셋째 함대 | Llama-3.1-8B-Instruct 8대, 각 TP=1, **모든 예산을 절반으로**(chat 2.5 s / 25 ms, deepresearch 5 s / 50 ms, swe 3.5 s / 38 ms — 37.5가 아니라 38인 것은 플래그가 정수로 파싱되기 때문) | archive/EXP-126 §2 |
| 클래스 구성(정적 sweep) | 믹스 `m1`: 요청 수 기준 chat 76.9 % / deepresearch 15.4 % / swe 7.7 % | archive/README(그림) "The workload" 절 |
| 정적 sweep | 요청 단위 Poisson 도착, 도착률 10·15·20·25·35·45·55·70 req/s, 조건당 8분, 조건마다 엔진과 제어평면 재시작, 2반복 | archive/EXP-108 §4 |
| 한 시간 trace | `dyn60_shift_m2Am1B_b1045`: Azure LLM Inference 2024(대화+코드) 4일 창의 도착 **순서**를 순위 변환으로 10.8~45.0 req/s 대역에 옮긴 것. **클래스 구성은 Azure에서 온 것이 아니라 합성 일정이다** — 15분마다 m2 → A → m1 → B, chat이 요청의 93.0 → 33.3 → 76.9 → 60.0 %. 한 시간 전체로는 chat 65.4 %(98,240건 중 64,292건) | archive/EXP-109 §1, archive/README(그림) `fig_azure_trace_shape.py` 절, `class_latency_outcome_rows.csv` |
| Qwen용 trace | 위 trace를 도착만 0.644배로 솎아낸 것(7.0~29.6 req/s, 63,437건). 0.644는 두 모델의 용량 비(18.3/28.4 req/s) | archive/EXP-113 §1 |
| 8B용 trace | 위 trace를 6.20배로 늘린 것(67~279 req/s, 615,228건) | archive/EXP-126 §2, archive/EXP-128 §5 |

**arm 이름**(결과 디렉토리 이름에 들어가는 문자열)과 논문 이름의 대응:

| 논문 | arm | 무엇인가 |
|---|---|---|
| FluidServe | `fsv3capgnofrct75` | v0.4 채택 구성: 클래스별 인스턴스 상한(논문의 demand-aware spread control) 켬, 마지막 수단 강제 배치 끔, swe를 토큰당 75 ms로 약속 |
| FluidServe w/o Affinity | `noaffnocaphour` | 위에서 **클래스 선호와 클래스별 인스턴스 상한을 둘 다** 끈 것(EXP-138, 기록 파일 없음, §4.13) |
| llm-d | `llmdslot75` | llm-d 상류 `predicted-latency-slo` 구성, 별도 스택(Envoy + EPP) |
| PolyServe | `polyservept75` | 정적 클래스 분할에 원 논문의 메커니즘 여섯을 켠 이식 |
| Llumnix* | `slot75` | Llumnix의 SLO 필터 정책(`--scheduling-policy slo`)에 요청별 SLO를 실어 보내도록 이 저장소가 고친 것(커밋 `9e0d51d`, 2026-07-25 "carry a per-request SLO from the client to the scheduling filters") |
| vLLM* | `vllmcachet75` (정적 sweep은 `vllmcache`) | PyPI `vllm-router` 0.1.15의 기본 정책 `cache_aware`(SGLang model gateway의 fork)를 이식한 것. admission control 없음 |

근거: archive/EXP-108 §3, archive/EXP-109 §2, `archive/llumnix_reproduce/ms_dev/notes/vllm-router-baseline.md` §1.

---

## 3. 채점 규칙

### 3.1 논문 주 지표: `ladder95` (2026-09-03 고정)

규칙을 가진 코드는 하나다: `Agent_applications/agent_motivation_experiment/analysis_scripts/request_level/deadline_ladder_attainment.py`
(`--frac` 기본값 0.95). 그림 스크립트는 이 스크립트가 쓴 요청 단위 판정 파일(`results/aggregate_analysis/ladder95/verdicts/`)을
읽기만 한다.

```
토큰 i(0부터)가 제때  : 보낸 시각 + TTFT_SLO + i × TBT_SLO 안에 도착  (첫 토큰의 마감은 정확히 TTFT_SLO)
요청이 제때           : 그 요청 토큰의 95 % 이상이 제때
token goodput         : 자기 마감을 지킨 토큰 수 ÷ 분석 창(초)
request goodput       : 제때인 요청 수 ÷ 분석 창(초)   (거절·미완은 제때가 아니다)
```

- 분모 셋: **admitted**(받아들여 끝난 요청), **offered**(도착 전부, 거절은 위반), **모든 도착**(미완까지 위반).
  분석 창은 `exp22_fluidserve.py`의 `load_run`: 첫 도착 뒤 60초(warmup)와 마지막 도착 전 20초(drain)를 자른다.
- **첫 토큰에 별도 조건이 없다.** 출력이 긴 요청은 뒤 토큰이 예산보다 빠르면 첫 토큰의 지각이 흡수된다. 첫 토큰
  성능을 주장하려면 TTFT 분포를 따로 인용한다(archive/README(그림) 맨 앞 절).
- 이 규칙은 앞선 규칙(관용 90 %, 요청 단위 goodput, 첫 토큰 별도 조건)과 **같은 표에 넣지 않는다.**
- ✗ **논문 §6.1 Metrics의 문장과 다르다.** 논문은 "A request contributes to request goodput if its final token meets
  the corresponding deadline"이라 쓰는데, 코드는 마지막 토큰이 아니라 **토큰의 95 % 이상**이다. 개정 때 문장을 코드에 맞춘다.

### 3.2 다른 규칙을 쓰는 그림 — 섞으면 안 된다

`exp22_fluidserve.py`의 `SLO_RULES`(“평균 규칙”): 요청이 제때 = TTFT ≤ 예산 **그리고** 평균 토큰당 시간
`(e2e − ttft)/(출력 토큰 − 1)` ≤ 예산. goodput = 규칙을 지킨 **요청의 출력 토큰 전량**.
**그림 15(지평 sweep)와 그림 18(SLO 배수)은 이 규칙이다.** 같은 run이 두 규칙에서 다른 값을 낸다 — EXP-108
35 req/s FluidServe 반복 1이 평균 규칙 13,365 tok/s, ladder95 13,932 tok/s(`exp131_horizon_goodput.csv`,
`/home/nxclab/handover/motivation_4panel_t75_withfs_col/exp108_paper_ladder95.csv`). 논문은 모든 그림이 §6.1의
정의를 따른다고 읽히게 쓰여 있으므로 ✗, 개정 때 두 그림의 캡션에 규칙을 적는다.

### 3.3 클래스 예산 (논문 Table 2)

| 클래스 | TTFT | 토큰당 | 비고 |
|---|---|---|---|
| Chat (ShareGPT) | 5 s | 50 ms | |
| Agent (SWE) | 7 s | 75 ms | ✓ 원래 약속은 **전체 시간 30초**였고 FluidServe v0.4가 "7초 + 토큰당 75 ms"로 바꿨다. EXP-108부터 **모든 arm에** 이 형태를 알렸다(FluidServe는 `--fluidserve-class-budgets 25:decode:75`, 나머지 셋은 `mix_short_m1_t75fair.json`의 `slo.swe.tbt_ms=75`). 채점은 환경변수 `FS_SWE_TBT_MS=75`. **30초 규칙으로 채점한 수치와 같은 표에 넣지 않는다** |
| Deep Research (Search Arena) | 10 s | 100 ms | |

근거: archive/EXP-108 §2, archive/README(그림) `fig_motivation_tg_4panel` 절. 8B 함대는 전부 절반(§2 표).

---

## 4. 그림·표별 근거

### 4.1 그림 1 — 점 하나가 정책 하나: token goodput 대 admitted 달성률, 거절률 표기

- **주장(캡션)**: FluidServe만 token goodput과 요청 달성률을 함께 높게 유지하고 거절률은 다른 정책과 같거나 낮다.
  Azure trace, Llama-3.1-70B 4대.
- **출처**: ⚠ **그린 스크립트와 CSV를 저장소에서 찾지 못했다.** PDF 첫 쪽의 벡터 좌표를 축 눈금으로 환산해
  점의 값을 읽고(축: x 0~16k, y 0~100 %) EXP-109(한 시간 trace, 다섯 arm × 2반복, ladder95) 반복 2와 대조했다.

| 정책 | PDF에서 읽은 점 (goodput tok/s, admitted %) | EXP-109 반복 2 (반복 1) | 그림의 거절률 | EXP-109 거절률 반복 2 (반복 1) |
|---|---|---|---|---|
| FluidServe | 약 11,460, 약 100 (별 모양이라 중심이 ±1 % 흔들림) | 11,587 / 100.0 (11,637 / 99.9) | 21 % | 20.7 % (19.9 %) ✓ |
| llm-d | 약 8,490, 97.9 | 8,571 / 98.0 (7,925 / 97.9) | 42 % | 42.5 % (49.1 %) ✓(반복 2) |
| PolyServe | 약 8,530, 65.1 | 8,646 / 65.2 (8,518 / 67.1) | 18 % | 17.9 % (22.5 %) ✓(반복 2) |
| Llumnix* | 약 6,810, 47.1 | 6,827 / 47.0 (6,829 / 46.1) | 39 % | 39.9 % (39.6 %) ✗ 반올림하면 40 % |
| vLLM* | 약 2,820, 24.2 | 2,689 / 45.4 (2,726 / 45.2) | 0 % | 0.0 % ✓ |

- **판단**: 넷(FluidServe, llm-d, PolyServe, Llumnix*)의 점은 **EXP-109 반복 2와 1~1.5 % 안에서 맞는다**(x가 일관되게
  1 % 남짓 작다 — 분석 구간을 59.25분에서 자른 값일 수 있으나 확인 못 함). ⚠ **vLLM\*의 y(약 24 %)는 admitted 45.4 %와
  다르다.** 그 arm은 도착의 52 %가 한 시간 안에 끝나지 않았는데(`class_latency_outcome_rows.csv`: 미완 51.7~52.3 %),
  미완을 위반으로 세면 21.6~21.8 %라 24 %와도 맞지 않는다. ⚠ Llumnix*의 39 %는 어느 반복의 반올림과도 다르다.
- **같이 적을 것**: vLLM*은 거절 0 %인 대신 **도착의 52 %가 끝나지 않았다**(EXP-109 §5 ⑤). 반복 하나의 점이다.
  **개정 때 할 일**: 그림을 EXP-109 CSV에서 그리는 스크립트를 만들고(두 반복을 다 표시), vLLM* 점의 정의를 정한다.

### 4.2 표 1 — 워크로드 길이 통계

- **출처**: archive/README(그림) "Length distributions" 절. 게이트웨이에서 잰 토큰 수, **EXP-53(네 정책 정적 sweep,
  2026-08-03 전후)의 15 req/s 네 arm 합산(arm마다 run 1개), 완료된 요청만**(27,560건). 그 도착률에서는 아무 arm도 거절하지 않아
  길이가 포화에 잘리지 않았고, 네 arm의 클래스별 중앙값이 2 % 안에서 같다.

| 클래스 (README) | n | 입력 중앙값 / p90 / 표준편차 | 출력 중앙값 / p90 / 표준편차 |
|---|---|---|---|
| chat | 21,178 | 556 / 1,580 / 589 | 384 / 775 / 395 |
| deep research | 4,225 | 3,993 / 7,519 / 2,411 | 971 / 1,233 / 274 |
| agent (swe) | 2,157 | 5,725 / 7,787 / 1,416 | 517 / 648 / 119 |

- ✗ **논문 표 1은 SWE-agent와 Search-arena 두 행이 서로 바뀌어 있다.** 논문은 SWE-agent 행에 3,993 / 7,519 / 2,411 /
  971 / 1,233 / 274를, Search-arena 행에 5,725 / … / 119를 적었는데, README에서 그 값들은 각각 **deep research**와
  **agent**의 것이다. 그림 6(b)의 분포(EXP-108 10 req/s, 다른 모집단)도 같은 방향을 가리킨다: 출력 중앙값 Search Arena
  969, SWE-bench 489(`length_refinement_distribution.csv`). chat 행은 맞다 ✓.
- **같이 적을 것**: agent 클래스의 입력은 SWE-bench Lite의 동시성 1 실행 기록 하나를 그대로 다시 보낸 것이라 입력
  분포가 그 기록으로 고정된다. 평균은 입력 677 / 4,328 / 5,788, 출력 445 / 973 / 531(chat / deep research / agent).

### 4.3 표 2 — 클래스별 SLO

§3.3. ✓ 값은 코드(`exp22_fluidserve.py`의 `SLO_RULES`, `FS_SWE_TBT_MS=75`)와 워크로드 설정과 같다. 8B 함대는 절반이고
논문 §6.1이 그렇게 적는다 ✓.

### 4.4 그림 3 — Azure trace의 도착률과 클래스 구성 편차

- **스크립트/값**: `fig_arrival_and_mix.py`, `arrival_and_mix.csv`(패널 a 5,760점 = 1분 간격 4일, 패널 b 576점 = 10분 구간).
- **데이터**: Azure LLM Inference 2024 대화+코드 trace의 분당 요청 수를 인덱스로 더한 것, `dyn60_azure4d.plan.json`의
  창(6시부터 4일, 5,760분).
- **값**: (a) 최대/최소 = 1.000/0.17328 = **5.77배** → 논문 "5.8×" ✓. p95/p5는 3.37배(archive/README(그림)
  `fig_azure_trace_shape.py` 절 — 강건한 쪽을 원하면 이 값). (b) 구성 편차 최대 **40.2 %** → 논문 "up to 40%" ✓.
- **(b)가 무엇인가**: 그 10분 구간의 클래스 구성이 창 전체 평균 구성과 같아지려면 **클래스를 바꿔야 하는 도착의 비율**
  (total variation distance). 0은 "한 클래스"가 아니라 "평균 구성"이다.
- **같이 적을 것 / 말하면 안 되는 것**:
  - Azure trace의 클래스는 **대화와 코드 둘뿐**이다. 우리 세 클래스 구성(10:2:1 등)의 근거가 아니다.
  - **우리가 재생하는 trace의 도착률 폭은 이 그림의 5.8배가 아니라 설계한 값**(10.8~45.0 req/s, 순위 변환)이고,
    **클래스 구성 일정은 합성**이다(§2). 논문 §3 "for experiments, we use the production-derived trace in Figure 3"는
    재생 trace의 구성도 Azure에서 온 것처럼 읽힌다 ⚠.
  - 도착률과 코드 비중의 상관은 +0.57(지연 0), 최대 +0.63 — "둘이 서로를 예측하지 못한다"를 말하려면 이 값을 적는다.
  - ⚠ `fig_arrival_and_mix.py`와 `fig_azure_rate_and_mix.py`의 주석 두 곳에 "15.6x"가 남아 있는데 CSV의 값은 5.77배다.
    15.6배를 인용하지 않는다.

### 4.5 그림 4 — llm-d와 PolyServe의 인스턴스별 클래스 구성 (33~36분)

- **스크립트/값**: `fig_class_mix_hour.py`(선택지로 두 arm, 33~36분 확대, 배치 크기 높이, 달성률 선),
  `class_mix_hour_abs_2arms_pl_zoom_all4_batch_att.csv`(CSV에는 **인스턴스별 달성률 선만** 있다. 띠의 클래스 구성은
  `build_class_mix_tables.py`의 표에서 온다).
- **run**: EXP-109 반복 1 — `260831_2128_exp109r1_llmdslot75_shift`, `260831_2232_exp109r1_polyservept75_shift`. n=1.
- **달성률 선**: admitted 분모, ladder95, 90초 창을 30초마다(CSV `window_s`, `step_s`).

| 인스턴스(순위, 포트) | llm-d 평균 (최소~최대) | PolyServe 평균 (최소~최대) |
|---|---|---|
| 1 | 8002: 99.6 (97.1~100) | 8000: 53.0 (27.1~100) |
| 2 | 8003: 100.0 | 8001: 38.4 (27.9~63.8) |
| 3 | 8001: 98.7 (91.3~100) | 8002: 98.3 (88.2~100) |
| 4 | 8000: 100.0 | 8003: 100.0 |

- **본문 주장과 대조**: "llm-d가 chat을 네 대 전부에 퍼뜨린다" ✓ — 한 시간 동안 인스턴스별 chat 비중 65.5 / 62.5 / 60.4 /
  59.9 %. "PolyServe가 chat을 1~2번에 가둔다" ✓ — 94.6 / 84.1 / 0 / 0 %, preemption 0회(archive/EXP-109 §5 ④, 반복 1).
- **같이 적을 것**: 3분 확대 구간 하나, 반복 하나다. 인스턴스 순서는 포트가 아니라 **한 시간 동안 chat을 많이 든 순서**다.
  달성률은 admitted 분모라 거절이 안 보인다(그 한 시간 거절률 llm-d 49.0 %, PolyServe 22.5 %, 반복 1).

### 4.6 그림 5 — 한 인스턴스의 KV·배치·토큰당 시간·달성률 (12분)

- **스크립트/값**: `fig_engine_tbt.py`, `engine_tbt.csv`.
- **run**: EXP-109 반복 1의 **Llumnix\***(`260831_2346_exp109r1_slot75_shift`), 엔진 8002, 35~47분. n=1.
- **값**: KV 사용률 23.2~100 %, 실행 중 요청 수 59~392(패널 a는 각자 창 안 최소~최대로 정규화), 엔진이 보고한 토큰당
  시간 25.0~378.6 ms(축은 150 ms에서 자르고 넘은 초는 그림 위에 적는다), admitted 달성률 13.3~100 %.
- **같이 적을 것**: 패널 a는 **모양만** 보여준다(정규화). 달성률은 admitted 분모이고 **그 12분 동안 이 arm은 도착의
  28.3 %를 거절했다**(스크립트 머리말). 캡션의 "with Llumnix"는 Llumnix의 SLO 필터 정책(Llumnix*)이다.

### 4.7 그림 6 — 출력 길이 추정 오차와 클래스별 길이 분포

- **스크립트/값**: `fig_length_refinement.py --weighted`, `length_refinement.csv`, `length_refinement_distribution.csv`.
- **모집단**: EXP-108(정적 sweep. 이 그림은 출력 길이만 쓰므로 채점 규칙과 무관하다) **10 req/s, 네 arm × 2반복 합산, 완료 요청만, 34,598건.**
  그 도착률에서는 아무 arm도 거절하지 않는다.
- **무엇을 재나**: j 토큰을 낸 요청의 최종 길이를 "j + j에서 아직 돌던 요청들의 **남은 길이 중앙값**"으로 추정하고
  `|추정 − 실제| / 실제`의 중앙값을 낸다. 합친 분포로 추정한 것(Aggregated)과 자기 클래스 분포로 추정한 것(Workload-level,
  클래스별 오차를 j에서 살아 있는 요청 수로 가중 평균).

| j (낸 토큰) | 0 | 200 | 400 | 600 | 800 | 1000 |
|---|---|---|---|---|---|---|
| Aggregated 오차 % | 43.8 | 34.6 | 26.2 | 20.0 | 12.4 | 9.0 |
| Workload-level 오차 % | 35.6 | 24.9 | 16.9 | 13.5 | 11.6 | 9.7 |
| 살아 있는 요청 수 | 34,598 | 27,511 | 19,797 | 11,227 | 6,393 | 3,241 |

- (b) 출력 중앙값: 합침 458, ShareGPT 384, Search Arena 969, SWE-bench 489; p90/p10 비 12.0 / 12.4 / 1.8 / 2.2.
  `--weighted`는 각 클래스 곡선에 도착 비중을 곱해 셋의 합이 회색(합침) 곡선이 되게 한 판이다.
- **같이 적을 것 / 말하면 안 되는 것**:
  - **우리 구현이 아니다.** 그림의 추정기는 남은 길이의 **중앙값**이고, 논문 식 (1)과 구현은 조건부 **평균**
    `E[L − j | L > j]`를 쓴다. 그림은 "그런 재평가가 가진 정보의 양"이지 우리 코드의 정확도가 아니다.
  - 곡선은 **살아남은 요청 위에서** 계산되어 j가 커질수록 모집단이 작아지고 꼬리가 길어진다. 살아 있는 요청이
    200건 미만이면 그리지 않는다(agent 클래스는 j=600에서 끝난다).
  - 각 요청의 정확한 길이를 정책에 알려 줘도 35 req/s 점수는 −0.2점만 움직였다(EXP-64, 길이 오라클 실험).

### 4.8 그림 11·12 — 한 시간 trace의 token goodput 시계열과 request goodput 막대

- **스크립트/값**: `fig_hour_goodput_split.py` → `hour_token_goodput_3panel.pdf`(그림 11), `hour_request_goodput_3panel.pdf`
  (그림 12), `hour_goodput_split.csv`(**request goodput만** 있다. 그림 11의 시계열 값은 CSV로 남기지 않았다 ⚠).
- **run**: 패널마다 다섯 arm × 2반복. 선과 막대는 **반복 2**, 막대 위 가는 표시가 두 반복의 범위.
  - (a) Llama-3.1-70B 4대: EXP-109, `results/*exp109r[12]_<arm>_shift`, 채점 구간 59.25분.
  - (b) Qwen2.5-72B 4대: EXP-113, `results/*exp113r[12]_<arm>_shiftq`, 58.75분.
  - (c) Llama-3.1-8B 8대, **절반 예산**: EXP-126(FluidServe·PolyServe), EXP-126b(llm-d·Llumnix*), EXP-128(vLLM*),
    `results/*exp126h62r[12]_<arm>c25d50s38ftc2500d5000s3500_shift62`, 60분 전체. 판정 파일은 `ladder95_halved/` 따로.
- **채점**: ladder95(절반 예산 패널은 절반 예산으로).

| request goodput (r/s), 반복 1 / 반복 2 | vLLM* | Llumnix* | PolyServe | llm-d | FluidServe |
|---|---|---|---|---|---|
| (a) Llama-70B | 6.03 / 5.95 | 7.65 / 7.76 | 14.08 / 14.53 | 13.56 / 15.33 | 21.71 / 21.52 |
| (b) Qwen-72B | 4.24 / 4.24 | 5.25 / 5.27 | 10.49 / 10.54 | 9.56 / 9.95 | 14.19 / 14.17 |
| (c) Llama-8B 절반 예산 | 37.52 / 36.67 | 34.17 / 33.19 | 92.78 / 92.72 | 106.40 / 111.20 | 123.67 / 123.61 |

- **두 반복 평균으로 낸 FluidServe 배수**(이 CSV로 계산): (a) vLLM* 3.61, Llumnix* 2.81, PolyServe 1.51, llm-d 1.50 ·
  (b) 3.34, 2.70, 1.35, 1.45 · (c) 3.33, 3.67, 1.33, 1.14. → 논문 "1.14–3.67×" ✓(최소 8B 대 llm-d, 최대 8B 대 Llumnix*),
  "llm-d 대비 1.14–1.50×" ✓, "PolyServe 대비 1.33–1.51×" ✓. 그린 반복 2만으로 계산하면 1.11–3.72×가 되므로
  **논문 배수는 두 반복 평균이다.**
- **token goodput(그림 11) 확인 가능한 부분**: (a)는 EXP-109 표(ladder95, 전 구간)로 FluidServe 11,637 / 11,587,
  llm-d 7,925 / 8,571, PolyServe 8,518 / 8,646, Llumnix* 6,829 / 6,827, vLLM* 2,726 / 2,689 tok/s. 평균 배수는 llm-d 1.41 ✓,
  PolyServe 1.35(논문 상한 1.36과 0.01 차이), Llumnix* 1.70, vLLM* 4.29. (b)는 반복 1만 기록에 있다(FluidServe 12,266,
  PolyServe 9,612, llm-d 9,254, Llumnix* 7,860, vLLM* 2,907). ⚠ **논문 "1.10–4.04×", "llm-d 대비 1.10–1.41×", "PolyServe
  대비 1.20–1.36×"의 전체 범위는 재현하지 못했다** — (b) 반복 2와 (c)의 ladder95 한 시간 token goodput 표가 이 기계에 없다
  (`results/aggregate_analysis/ladder95/`가 복원되지 않았다). 그림 구간(59.25분)으로 자른 값을 썼을 가능성이 있다.
- **같이 적을 것**:
  - (c)는 다른 실험이다: 다른 모델, 8대, trace 6.20배, **예산 절반**. 그 함대에서 표준 예산으로는 **PolyServe가 앞섰다**
    (EXP-125, 반복 1, 평균 규칙: PolyServe 76.0 대 FluidServe 68.2 offered). 절반 예산을 택한 이유가 "작은 모델의 높은
    처리율"이라는 논문 문장은 그 측정을 말하지 않는다(archive/EXP-126 §1·§6.4). ⚠ 개정에서 가장 공격받기 쉬운 곳.
  - (c)의 **vLLM\*은 도착의 59.4 %가 `client connection exhausted`로 끝났다** — 부하 생성기의 연결이 고갈된 것이고 정책을
    잰 것이 아니다. Llumnix*도 거절이 아닌 오류가 11.0 %다(archive/EXP-128 §5.1·§5.3).
  - (b)의 vLLM*은 부하 생성기가 밀려 51~54분 도착이 다른 arm의 절반이었고 분석 창이 149초 길다(archive/EXP-113 §3 ①).
  - 채점 구간이 패널마다 다르다(59.25 / 58.75 / 60분).

### 4.9 그림 13 — 한 시간 trace(Llama-70B)의 클래스별 TTFT·TBT·결과

- **스크립트/값**: `fig_class_latency_outcome_rows.py`, `class_latency_outcome_rows.csv`. EXP-109 다섯 arm × **2반복 평균**.
- **결과(도착 대비 %, 평균, 괄호는 두 반복 범위)**:

| arm | chat 제때 / 거절 | deepresearch 제때 / 거절 | swe 제때 / 거절 | 전체 제때 / 거절 / 미완 |
|---|---|---|---|---|
| FluidServe | 84.2 (83.6~84.8) / 15.7 | 68.6 (68.0~69.1) / 30.6 | 73.7 (73.0~74.4) / 25.9 | 79.5 / 20.3 / 0.2 |
| llm-d | 52.9 (47.6~58.1) / 46.1 | 56.5 / 41.8 | 47.8 / 50.1 | 53.0 / 45.7 / 0.2 |
| PolyServe | 49.5 / 9.5 (놓침 40.9) | 45.6 / 53.3 | **80.2** (79.3~81.1) / 19.1 | 52.7 / 20.2 / 0.2 |
| Llumnix* | 30.0 / 46.1 | 27.1 / 7.4 (놓침 63.8) | 18.7 / 59.2 | 27.9 / 39.5 / 0.5 |
| vLLM* | 30.0 / 0 (미완 50.1) | 6.8 / 0 | 4.6 / 0 | 21.7 / 0 / 52.0 |

- **지연(완료 요청, P50 / P95)**: FluidServe TBT chat 43.5 / 45.8, swe 70.7 / 76.0, deepresearch 71.2 / 75.3 ms; TTFT chat
  0.42 / 1.47, swe 1.47 / 3.88, deepresearch 1.72 / 5.36 s. 축 밖 표기(논문에 찍힌 수): vLLM* TTFT P95 chat 1.3k s, swe P50 393
  / P95 1.7k s, deepresearch P50 367 / P95 1.5k s, Llumnix* deepresearch TTFT 17.2 / 23.9 s, swe P95 14.7 s; vLLM* TBT chat P95 171,
  swe P50 150 / P95 469, deepresearch P95 312 ms — 전부 CSV 평균과 ✓.
- **본문 주장과 대조**:
  - "llm-d rejects 34.8–45.7%" — 70B 45.7 %(49.0과 42.4의 평균) ✓, Qwen 43.5 %(44.4/42.5, 평균 규칙 표) 범위 안,
    8B 33.2~36.0 %(평균 34.6 %, EXP-128 표) ⚠ 하한 34.8을 ladder95 표로 확인하지 못함.
  - "PolyServe performs best for the Agent workload" ✓(80.2 대 73.7).
  - "FluidServe achieves 1.51× higher SLO-compliant request throughput in the Llama-3.1-70B setting" ✓ — PolyServe 대비
    request goodput 두 반복 평균 21.61/14.30.
  - "99.9–100% of its admitted requests satisfy their SLOs across all three configurations" — 70B 99.9 / 100.0 ✓, Qwen 99.9(반복 1) ✓,
    8B ⚠ ladder95 절반 예산 값이 기록에 없다(평균 규칙 표는 99.5~99.6, archive/EXP-128 §5.1).
  - "class-wise TBT stays close to each workload's SLO target" — chat 43.5/50, swe 70.7/75는 맞지만 **deepresearch는
    71.2/100 ms**다. deepresearch가 swe와 같은 인스턴스에 앉아 75 ms 기준을 따르기 때문이다. 문장을 좁혀야 한다 ⚠.
- **같이 적을 것**: 수염은 반복 폭이 아니라 **요청 분포의 P95**다. 지연은 **끝난 요청 위에서** 재므로 arm마다 모집단이
  다르다(절반을 거절한 arm은 그 이유만으로 빨라 보인다) — (c)와 같이 읽어야 한다. 규칙선은 명목 약속이지 채점 규칙이
  아니다(P95가 선 위여도 5 %가 실패한 것이 아니다).

### 4.10 그림 14 — 정적 rate sweep, 다섯 정책, 네 패널

- **스크립트/값**: `fig_motivation_tg_4panel.py`(`_withfs_col` 판), `motivation_throughput_vs_goodput_4panel_t75_withfs.csv`
  (arm × 도착률마다 평균, 최소, 최대, 반복 수). 이 그림만 따로 다시 그리는 묶음이 `/home/nxclab/handover/motivation_4panel_t75_withfs_col/`.
- **run**: EXP-108(2026-08-31, 네 arm × 8 도착률 × 2반복 = 64 run) `results/*exp108r[12]_<arm>_t75[fair]_rpm_<rpm>`.
  **예외 둘**: llm-d 10 req/s 칸은 EXP-108 두 반복이 거절 0.0 % 대 42.0 %로 갈려 **EXP-110 반복 3·5로 대체**(사용자 결정
  2026-09-02, EXP-110이 그 칸을 세 번 더 재서 세 번 다 거절 0 %), vLLM*은 EXP-108에 없어 **EXP-77(2026-08-10)**
  `results/*exp77r[12]_vllmcache_m1_rpm_*`를 같은 규칙으로 다시 채점(70 req/s는 반복 1개).
- **채점**: ladder95.

| 도착률 (req/s) | 25 | 35 | 45 | 55 | 70 |
|---|---|---|---|---|---|
| FluidServe goodput (tok/s) | 12,296 | 13,796 (13,661~13,932) | 15,503 | 14,861 (13,865~15,856) | 15,355 (14,559~16,150) |
| llm-d | 9,074 | 8,815 (8,339~9,290) | 9,565 (8,405~10,726) | 8,081 | 7,428 |
| PolyServe | 10,469 | 9,746 | 8,738 | 8,502 | 7,580 |
| Llumnix* | 10,244 | 6,565 | 4,990 | 5,411 | 5,156 |
| vLLM* | 12,444 | 3,005 | 1,456 | 1,068 | 658 (n=1) |
| FluidServe admitted % / 거절 % | 100.0 / 2.6 | 99.8 / 22.7 | 99.9 / 30.4 | 99.8 / 47.6 | 99.8 / 56.8 |

- **배수**: 35~70 req/s에서 admission이 있는 세 기준선(PolyServe, Llumnix*, llm-d) 대비 FluidServe는 **1.42(35, PolyServe)
  ~ 3.11(45, Llumnix\*)배** → 논문 "1.42–3.11×" ✓, 그때 admitted 99.75~99.91 % → "99.8–99.9%" ✓. 25 req/s에서는
  1.17~1.36배, 10~20 req/s에서는 0.99~1.10배(모두 100 % 근처라 천장이다).
- ✗/⚠ **서론 문장의 범위 설정**: 서론은 "Compared with vLLM, Llumnix, llm-d, and PolyServe … 1.42–3.11× on synthetic"이라 쓰는데
  이 범위는 **vLLM\*을 빼고 35 req/s 이상만** 본 것이다(vLLM* 대비는 35~70에서 4.6~23.3배). 본문 §6.2는
  "admission-enabled baselines"라고 바르게 적었다.
- **같이 적을 것**: vLLM*만 다른 세션이고 그 사이 2026-08-28에 장비를 초기화·복원했다(세션 간 이동은 최대 4.6점으로 쟀지만
  그 측정은 복원을 사이에 두지 않는다). vLLM*은 어느 도착률에서도 거절하지 않는다((d)에 그 곡선이 없는 이유). llm-d의
  반복 폭이 크다(35 req/s goodput 8,339~9,290, 45 req/s 8,405~10,726). 관용을 90 %로 낮춰도 FluidServe +0.0점, PolyServe +2.6점
  (최대 5.2) — 관용이 우리에게 유리한 잣대가 아니다.
- ⚠ archive/README(그림)의 이 그림 절에 손으로 옮긴 표(예: FluidServe 35 req/s 13,336)는 **CSV와 다르다**(13,796). 그 표는
  낡았다. CSV만 인용한다.

### 4.11 그림 15 — 예측 지평(horizon) sweep

- **스크립트/값**: `fig_exp131_horizon.py`, `exp131_horizon_goodput.csv`.
- **run**: 정적 8분, 지평마다 2반복.
  - (a) Llama-70B, **35 req/s**: h=1·10 EXP-131j, h=25 EXP-131h, h=50·250·500 EXP-131e
    (`results/*exp131[ehj]r[12]_fsv3t75h<H>_t75_rpm_2100`), **h=100은 EXP-108의 FluidServe 두 run**(대조군 재사용).
  - (b) Qwen-72B, **22.4 req/s**(1,344 rpm, 두 모델의 용량 대비 같은 위치를 노린 값): EXP-136 `results/*exp136qr[12]_*_rpm_1344`.
- **채점**: ✗ **평균 규칙**(§3.2), ladder95가 아니다.

| h | 1 | 10 | 25 | 50 | 100 (배포값) | 250 | 500 |
|---|---|---|---|---|---|---|---|
| Llama goodput (tok/s) | 6,149 | 12,858 | **14,394** | 14,328 | 13,246 | 10,121 | 8,282 |
| Llama 모든 도착 % / admitted % / 거절 % | 34.8 / 100 / 65.1 | 74.8 / 100 / 24.4 | 82.8 / 99.96 / 15.3 | 82.6 / 99.6 / 15.1 | 74.5 / 98.7 / 22.2 | 59.3 / 88.5 / 30.9 | 49.2 / 79.8 / 35.5 |
| Qwen goodput (tok/s) | 6,721 | 13,267 | **13,878** | 13,741 | 13,237 | 10,029 | 7,588 |

- **같이 적을 것 / 말하면 안 되는 것**:
  - **배포값 h=100은 최적이 아니다.** 두 모델 모두 h=25~50이 더 높다(Llama +1,082~+1,148 tok/s, all-arrivals +8점). 100은
    설계 단계에서 정했고 흔들지 않았으므로 **평가 전체의 FluidServe 수치는 이 축에서 하한**이다(archive/EXP-131 §8.3).
  - **원인 서술이 기록과 다르다** ⚠. 논문은 긴 지평의 손실을 "KV-cache growth 같은 실행 동태의 불확실성"으로 설명하지만,
    기록이 측정한 기전은 ① 짧은 쪽: 대기 중인 prefill 청크 수 ÷ h가 1에서 포화해 모든 iteration을 prefill을 나르는 최악의
    step으로 보아 속도 판정이 과도하게 비관적이 된다 ② 긴 쪽: 도착 요청의 미래 KV 선청구 `min(h, 기대 출력)`이 출력 전체에
    가까워져 도착 하나가 과도하게 비싸 보인다. **그리고 KV 투영 항(inflow − outflow)을 떼면 오히려 좋아졌다**: 청구 창
    50에서 투영 켬 81.5~83.8 대 끔 83.8~87.5(archive/EXP-131 §8.1·§8.2). "투영이 있어야 낫다"는 반증된 상태다.
  - 35 req/s(Qwen은 22.4 req/s) 한 점이다. Qwen은 "용량 대비 같은 위치"에 맞췄는데도 거절률이 낮게 나와(14.2 % 대 22.3 %)
    두 모델이 같은 압력이 아니었다(archive/EXP-136 §7.5).

### 4.12 그림 16 — 클래스 선호를 끈 것과 기준선의 KV·배치·달성률

- **스크립트/값**: `fig_kv_batch_attain.py`, `kv_batch_attain_4arms.csv`(run마다 한 행, 엔진별 중앙값 포함).
- **run**: 한 시간 trace, Llama-70B. FluidServe·llm-d·PolyServe는 EXP-109 반복 1·2, **FluidServe w/o Affinity는 EXP-138
  반복 1개**(`260914_0539_exp138r1_noaffnocaphour_shift`).
- **측정**: KV·배치는 엔진의 Prometheus 게이지를 초 단위로 맞춰 함대 값(배치 = 네 엔진 실행 요청 합, KV = 네 풀 평균)을 만들고
  분석 창의 중앙값. 달성률은 ladder95 admitted.

| arm | KV 중앙값 % | 배치 중앙값 | admitted % | 거절 % | token goodput | request goodput |
|---|---|---|---|---|---|---|
| FluidServe (r1 / r2) | 67.1 / 67.4 | 685 / 681 | 99.95 / 99.96 | 19.9 / 20.6 | 11,636 / 11,586 | 21.89 / 21.70 |
| **w/o Affinity (r1만)** | 46.8 | 521 | 99.999 | **16.2** | **11,682** | **22.94** |
| llm-d | 40.0 / 40.2 | 342 / 358 | 97.9 / 98.0 | 49.0 / 42.4 | 7,925 / 8,570 | 13.66 / 15.41 |
| PolyServe | 48.1 / 48.3 | 601 / 608 | 67.1 / 65.2 | 22.5 / 17.8 | 8,517 / 8,646 | 14.22 / 14.66 |

- **본문 주장과 대조**: "Removing affinity substantially reduces both cluster KV-cache occupancy and decode batch size" ✓
  (67 → 47 %, 683 → 521). "w/o affinity still maintains nearly 100%" ✓.
- **반드시 같이 적을 것** ✗/⚠:
  - 논문의 "w/o Affinity"는 **클래스 선호와 클래스별 인스턴스 상한을 둘 다 끈 것**이다(EXP-138 드라이버 머리말).
    논문은 선호만 뺀 것처럼 읽힌다.
  - **같은 CSV에서 w/o Affinity의 goodput이 더 높고 거절이 더 낮다**(token 11,682 대 11,586~11,636, request 22.94 대
    21.70~21.89, 거절 16.2 % 대 19.9~20.6 %). 반복 1개라 차이를 판정할 수는 없지만, **"affinity가 안전을 더 높은 이용률로
    바꾼다"를 goodput 이득으로 읽게 하면 안 된다** — 이 run에서 높은 KV·배치는 더 많은 goodput으로 이어지지 않았다.
  - 선호만 끈 한 시간 run(EXP-132, 반복 1, 상한 켬)은 모든 도착 76.3 대 대조군 78.5/77.8로 −1.4~−2.2점, 판정 문턱 아래
    (archive/EXP-132 §6.2). 정적 35 req/s에서는 상한이 켜져 있을 때만 선호가 값을 한다: 상한 켬 73.3~75.5 → 선호 끔
    69.9~71.4, 상한 끔 85.6~85.8 → 둘 다 끔 85.6~85.7(EXP-138 드라이버 머리말이 인용한 EXP-137 결과; EXP-137 파일의 결과 절은
    비어 있다 ⚠).
  - admitted 달성률은 거절을 벌하지 않는다. 거절률을 캡션에 적는다(스크립트 머리말).

### 4.13 그림 17 — FluidServe와 w/o Affinity의 인스턴스별 클래스 구성·배치·TBT

- **스크립트/값**: `fig_class_mix_hour.py --preset exp138 …`, `class_mix_hour_abs_exp138_batch_tbt.csv`(엔진이 보고한 토큰당
  시간 선만, 120초 평활). 띠는 클래스 구성 표에서 온다.
- **run**: FluidServe = EXP-109 반복 1(`260831_2015_exp109r1_fsv3capgnofrct75_shift`), w/o Affinity = EXP-138 반복 1. 각각 n=1.
- **EXP-138이 무엇인가(기록 파일 없음)**: 드라이버 `Agent_applications/agent_motivation_experiment/k8s/exp07/run_exp138_noaffnocap_hour.sh`
  머리말 — 2026-09-14, `run_exp135_nocap_hour.sh`의 스냅샷에 arm 하나(`noaffnocaphour`: 배포 구성에서
  `--fluidserve-enable-affinity=false`와 `--fluidserve-class-instance-cap=false`)를 더했다. 나머지 세 모서리는 다시 돌리지
  않았다(대조군 EXP-109 2반복, 선호만 끔 EXP-132 1반복, 상한만 끔 EXP-135 2반복). 디스크에 있는 것은 **반복 1 하나**다
  (`kv_batch_attain_4arms.csv`, 이 그림의 CSV). 드라이버는 "클래스별로 읽으라"고 적었는데 그 클래스별 결과는 어디에도 적혀
  있지 않다 ⚠.
- **값(인스턴스별 토큰당 시간, 한 시간 중앙값 / p90, ms)**: FluidServe 44.0/45.1, 44.8/74.1, 70.2/74.4, 71.3/74.8 — **두 대는
  chat 속도(약 44 ms), 두 대는 swe 속도(약 70~75 ms)**. w/o Affinity는 네 대 전부 44.8~44.9 / 45.5~45.6 — chat이 네 대에 다 있다.
- **같이 적을 것**: 반복 하나씩이다. w/o는 상한까지 끈 것이다. 분리의 대부분은 선호가 아니라 판정 조건이 만든다는 측정이
  있다 — chat 없는 인스턴스 수가 선호 켬 2.1/4, 끔 1.8/4(상한은 둘 다 켬), "분리의 86 %를 판정 조건이 혼자 만든다"
  (archive/STATUS 2026-09-14 절, EXP-132). 한 시간 동안 FluidServe chat의 유효 인스턴스 수(`1/Σs²`, 60초 창 중앙값)는 1.64,
  llm-d는 3.45(EXP-109 반복 1, `fig_class_mix_hour.py` 머리말).

### 4.14 그림 18 — SLO 배수 0.7~1.3에서의 token·request goodput (35 req/s)

- **스크립트/값**: `fig_exp139_slo_scale.py`, `exp139_slo_scale_goodput.csv`(run마다 한 행).
- **run**: k = 0.7·0.9·1.1·1.3은 EXP-139(2026-09-14 한 세션) `results/*exp139k<K>r<R>_*`; **k = 1.0은 EXP-108**(2026-08-31,
  다른 세션) `results/*exp108r<R>_*_rpm_2100`. 여섯 예산(세 클래스 × TTFT·토큰당)을 정책 입력과 채점 양쪽에서 k배.
- **채점**: ✗ **평균 규칙**(`all_arrivals_attainment.one_run`, §3.2). 거절·미완은 위반.
- **반복 수**: k=0.7 전 arm 2; **k=0.9·1.1 전 arm 1**; k=1.0 전 arm 2; k=1.3 FluidServe·llm-d 2, **PolyServe·Llumnix\* 1**.

| k | FluidServe tok/s | llm-d | PolyServe | Llumnix* |
|---|---|---|---|---|
| 0.7 | 11,923 / 11,841 | 4,657 / 6,222 | 3,846 / 3,799 | 6,125 / 6,130 |
| 0.9 | 13,796 | 5,867 | 3,676 | 4,249 |
| 1.0 | 13,365 / 13,126 | 8,490 / 7,789 | 3,961 / 4,032 | 3,237 / 3,280 |
| 1.1 | 13,501 | 10,718 | 6,425 | 1,547 |
| 1.3 | 13,692 / 13,588 | 9,241 / 8,640 | **13,738** | 1,540 |

- **같이 적을 것 / 말하면 안 되는 것**:
  - k=1.3에서 PolyServe는 FluidServe에 "근접"한 것이 아니라 **같거나 조금 높다**(13,738 대 13,588~13,692, PolyServe 반복 1개).
    request goodput도 29.30 대 28.35~28.64. FluidServe의 admitted가 k=1.3에서 95.0 %로 내려간다.
  - PolyServe는 예산에 적응하지 않는다: 예산을 넓혀도 지연 분포가 세 분위수 전부 0.5 ms 안에서 같고, 달성률 상승은 합격선이
    고정된 분포를 지나간 결과다. FluidServe는 실측 토큰당 시간 ÷ 예산이 0.87~0.90으로 예산을 따라간다(archive/STATUS 제출 시점
    절, EXP-139 §8.6).
  - k=1.0은 다른 세션(세션 간 이동 최대 4.6점). llm-d 반복 차이가 최대 11.7점(k=0.7: 18.5 대 30.2 모든 도착).
  - request goodput은 모든 도착 달성률 × 35 req/s라 달성률이 주지 않는 정보가 없다.
  - Llumnix*는 예산이 풀릴수록 나빠진다(21.6 → 6.7 %): 예산이 유일한 제동이라 배치가 커지고 KV가 넘친다(archive/STATUS).

---

## 5. 본문 수치

| 위치 | 논문 문장 | 근거 | 판정 |
|---|---|---|---|
| 초록 | "up to 3.67× the request goodput on production-derived traces" | §4.8, 8B 절반 예산 대 Llumnix* | ✓ (두 반복 평균) |
| 초록 | "up to 3.11× the token goodput on synthetic workloads" | §4.10, 45 req/s 대 Llumnix* | ✓ |
| 그림 1 | 거절률 llm-d 42 %, FluidServe 21 %, PolyServe 18 %, Llumnix 39 %, vLLM 0 % | §4.1, EXP-109 반복 2 | ⚠ 스크립트 없음, Llumnix 39 %는 39.9 % |
| 그림 1 | 축 값(점 위치) | §4.1 | ⚠ 네 점은 반복 2와 맞음, vLLM*의 y 24 % 불일치 |
| 서론 | "1.42–3.11× on synthetic workloads" (vLLM 포함 문장) | §4.10 | ✗ 범위는 vLLM* 제외, 35 req/s 이상 |
| 서론 | "1.10–4.04× on the Azure trace" | §4.8 | ⚠ 70B만 확인(1.41~4.29), 전체 범위 미확인 |
| 서론 | "99.8–100% SLO attainment among admitted requests" | §4.9, §4.10 | ✓ 정적 99.75~99.91, 70B 한 시간 99.9~100.0 (8B ⚠) |
| 그림 3 | "5.8×", "about 40%" | §4.4 | ✓ 5.77배, 40.2 % |
| §3 | 8×B200, Llama-3.1-70B-Instruct, vLLM 4대 | §2 | ✓ (각 TP=2를 적는 것이 좋다) |
| §3 | "we use the production-derived trace in Figure 3" | §4.4 | ⚠ 재생 trace의 도착률 대역과 클래스 구성은 설계한 것 |
| §3.1 | llm-d가 chat을 네 대에, PolyServe가 1~2번에 | §4.5 | ✓ |
| §3.2 | 배치·KV가 몇 분 안에 크게 흔들린다(그림 5) | §4.6 | ✓ KV 23~100 %, 배치 59~392 |
| §3.2 | "PolyServe uses the average decode length" | PolyServe 이식의 `--polyserve-tier-decode-tokens`(`50:428,75:494,100:985`, 클래스 평균 출력 길이) | ✓ (archive/EXP-108 §5.1) |
| §4.2.1 각주 1 | "h = 100 iterations" | `pkg/consts/consts.go` `DefaultFluidserveHorizonSteps = 100` | ✓ (최적이 아님, §4.11) |
| §4.4 각주 2 | "a 5-second window" | `fluidserve_instancecap.go`: `fsCapBucketMs = 5000`, 평활 상수 = 3.0 × 클래스 체류 시간, 15~120초로 제한 | ⚠ 5초는 집계 단위이고 실제로 반응하는 시간은 15~120초 |
| §5 | "5.6K line Go scheduler" | `fluidserve-implementation-facts.md` §7: 정책 6개 파일 5,614행(테스트 3,069행 제외, 2026-09-09) | ✓ 당시 기준. 지금 저장소는 5,925행 |
| §5 | "192 µs of scheduling overhead per incoming request" | EXP-140: 결정 전체 192 µs(정책 169 µs, 공유 인프라 23 µs), 스케줄러 `-v 0`, 35 req/s | ✓ |
| §5 | "nearly constant as the number of concurrently served requests increases" | EXP-140: 동시 요청 100건당 +10~12 µs, 800건에서 가변항 +80~96 µs(기저의 약 30 %) | ⚠ 기울기는 `-v 4`에서 잰 값이고 `-v 0`에서 다시 재지 않았다 |
| §6.1 | Llama-8B 8대는 TTFT·TBT를 절반 | §2 | ✓ (선택 이유는 §4.8 참조) |
| §6.1 | "Llumnix*: We extend it to support heterogeneous SLOs" | 커밋 `9e0d51d` | ✓ |
| §6.1 | "this variant outperforms the original Llumnix in our experiments" | EXP-53(정적, 프로파일 수정 전, 평균 규칙, swe 30초): 45 req/s offered Llumnix SLO 52.4 대 Llumnix 32.1 | ⚠ 논문 규칙(ladder95, swe 75 ms)으로는 Llumnix 부하 균등화 arm을 잰 적이 없다 |
| §6.1 | vLLM*: "built-in cluster scheduler of vLLM; balances instance load and prefix-cache locality" | `vllm-router-baseline.md` §1·§2 | ⚠ 정확히는 PyPI `vllm-router` 0.1.15의 기본 `cache_aware`(SGLang model gateway fork) |
| §6.2 | "improving it by 1.10–4.04×" (token, 한 시간) | §4.8 | ⚠ |
| §6.2 | request goodput "1.14–3.67×" | §4.8 | ✓ |
| §6.2 | token "1.10–1.41× over llm-d", "1.20–1.36× over PolyServe" | §4.8 | ⚠ 70B 값(1.41, 1.35)만 확인 |
| §6.2 | request "1.14–1.50×" (llm-d), "1.33–1.51×" (PolyServe) | §4.8 | ✓ |
| §6.2 | "99.9–100% of its admitted requests" (세 함대) | §4.9 | ⚠ 8B 미확인 |
| §6.2 | "rejecting 34.8–45.7% of offered requests" (llm-d) | §4.9 | ⚠ 상한 45.7 ✓, 하한 34.8 미확인(34.6 근처) |
| §6.2 | "1.51× higher SLO-compliant request throughput" (70B) | §4.9 | ✓ PolyServe 대비 |
| §6.2 | "its class-wise TBT stays close to each workload's SLO target" | §4.9 | ⚠ deepresearch는 71/100 ms |
| §6.2 | "improving token goodput by 1.42–3.11× across all admission-enabled baselines", "99.8–99.9%" | §4.10 | ✓ (35~70 req/s) |
| §6.3 | 지평의 두 실패 방향(짧으면 보수적, 길면 불확실성) | §4.11 | ⚠ 기전 서술이 기록과 다름 |
| §6.4 | affinity 제거 시 KV·배치 감소, 거의 100 % 유지 | §4.12 | ✓ 다만 w/o는 상한까지 끈 것이고 goodput은 더 높다 |
| §6.4 | 그림 17의 확장·수축 서술 | §4.13 | ⚠ 반복 1, 분리의 대부분은 판정 조건 |
| §6.5 | 1.3배에서 PolyServe가 근접 | §4.14 | ✗ 같거나 높다(반복 1) |
| §8 | "our results (Figure 13) show no pathological starvation" | §4.9 | ✓ 다만 FluidServe는 deepresearch 30.6 %, swe 25.9 %를 거절(chat 15.7 %) |

---

## 6. 같이 적어야 오독되지 않는 한계

1. **총계의 우위는 대부분 chat에서 온다.** 정적 믹스 m1에서 chat이 요청의 76.9 %, 한 시간 trace 전체로 65.4 %다.
   논문 규칙(EXP-108, ladder95)의 정적 35 req/s 클래스별 offered: FluidServe chat 75.1~77.4 / deepresearch 84.7~84.8 /
   swe 72.5~72.6, llm-d 32.3~41.5 / 64.5~68.2 / 63.5~80.4, PolyServe 38.1~39.1 / 51.0~53.0 / **90.9~94.2**
   (`exp108_paper_ladder95.csv`). 즉 **swe에서는 PolyServe가 35~70 req/s 전부에서 앞서고**(45 req/s 82.4~82.7 대 55.1~56.9),
   llm-d와는 35 req/s와 70 req/s에서 범위가 겹친다.
   ⚠ 옛 기록(`archive/Agent_applications/agent_motivation_experiment/paper_evaluation_2026-08/05_per_class.md`)의
   "deepresearch는 45 req/s부터 llm-d에 진다"는 **swe를 30초로 채점하고 FluidServe v0.2로 돈 옛 sweep**의 결과다. 논문 규칙의
   EXP-108에서는 deepresearch가 55 req/s까지 앞서고 70 req/s에서 겹친다(33.7~48.3 대 37.6~38.4). 개정에서 클래스별 표를
   싣는다면 EXP-108로 다시 만든다.
2. **PolyServe가 swe를 이긴다**(한 시간 trace 80.2 대 73.7, §4.9). 정적 분할은 지켜 줄 클래스를 배포 시점에 고정하고 그 대가를
   나머지 클래스가 낸다 — 같은 run에서 PolyServe의 chat은 받아들인 것의 약 41 %를 놓친다.
3. **클래스별 인스턴스 상한(논문의 demand-aware spread control)은 정적 35 req/s에서 10~12점을 잃게 하고 한 시간 trace에서는
   총점이 중립이다.** 상한 하나만 끈 정적 run(EXP-134, 2반복): 모든 도착 73.3/75.3 → 85.8/85.6, 거절 22.0/23.4 % → 11.9/12.1 %,
   goodput 13,259/12,990 → 14,668/14,633, admitted 변화 없음. 한 시간(EXP-135, 2반복): 모든 도착 78.5/77.8 → 78.5/78.4로 차이 없음,
   deepresearch만 68.0/69.1 → 66.0/65.7(−2.0~−3.4). 상한은 **용량을 대가로 deepresearch를 보호**하고 그 대가는 용량이 구속할 때만
   청구된다(archive/STATUS 2026-09-14 절). 논문은 이 메커니즘의 ablation을 싣지 않았다.
4. **배포 구성의 네 요소를 다 빼면 정적 35 req/s에서 약 9.5점 좋아진다**(누적 ablation EXP-133, 2반복: 74.3 → 83.8, 거절 22.7 →
   14.1 %, goodput 13,125 → 14,124). 배포 구성이 그 셀에서 손해를 보고 있다는 뜻이고, 상한을 채택한 근거가 한 시간 trace의
   재분배였으므로 그 셀이 재는 양은 아니다.
5. **지평 100은 최적이 아니고, KV 투영 항은 값을 증명하지 못했다**(§4.11). 논문의 "receding-horizon projection" 기여를 개정에서
   어떻게 쓸지 정해야 한다.
6. **8B 함대의 예산 절반**(§4.8): 표준 예산에서는 PolyServe가 앞섰다(EXP-125, 반복 1).
7. **preemption 횟수는 세션을 건너 비교하지 않는다.** 같은 셀·같은 설정에서 완료 1,000건당 preemption이 EXP-108(08-31) 16.7 / 15.2,
   EXP-133 `cum0`(09-14) 45.1이었다. 점수는 세션을 건너가고(73.3~75.3 대 73.6~75.5) preemption은 안 건너간다. 가장 유력한 원인은
   2026-09-04에 엔진 인자에 추가된 `--api-server-count 4`이고 확정하지 못했다(archive/STATUS).
8. **반복이 하나뿐인 칸**: 그림 1(EXP-109 반복 2만), 그림 4·5(EXP-109 반복 1), 그림 16·17의 w/o Affinity(EXP-138), 그림 17의
   FluidServe(EXP-109 반복 1), 그림 18의 k=0.9·1.1 전부와 k=1.3의 PolyServe·Llumnix*, 그림 14의 vLLM* 70 req/s.
9. **기준선 쪽 주의**: llm-d는 조건마다 예측기를 새로 학습해서 반복 폭이 크다(정적 10 req/s에서 거절 0 % 대 42 %, 한 시간 70B에서
   offered 49.9 대 56.3). PolyServe의 시간 기반 admission 검사(원 논문 §4.6)는 거부의 0.0~0.3 %만 차지하고 실제 거부는 KV 용량
   판정(79.9~100 %)이 만든다 — "메커니즘을 전부 주었다"가 아니라 이 분해로 적는다(archive/EXP-108 §8.3).
10. **모든 성능 실험은 스케줄러 `-v 4`에서 돌았다.** 로깅이 결정마다 약 127 µs를 썼지만 네 정책이 같은 조건이었다. 오버헤드
    수치 192 µs만 `-v 0`에서 따로 쟀다(archive/EXP-140 §8).
11. **한 시간 trace는 admission이 있는 넷의 곡선을 59.2분까지만 그린다.** 마지막 2분은 아직 돌던 요청이 admitted 분모에서 빠져
    잘 하는 arm을 부풀린다(마지막 다섯 창: llm-d admitted 96.4 대 모든 도착 28.4, FluidServe 98.5 대 70.0). 같은 arm의 세션 간
    이동은 FluidServe offered 3.8점으로 쟀다(archive/README(그림) `exp109_hour` 절, archive/EXP-109 §8).

---

## 7. 인용 금지 수치

| 값 | 무엇이었나 | 대신 쓸 값 | 출처 |
|---|---|---|---|
| 0.013 ms (결정당) | `fluidserve-implementation-facts.md` §8의 결정 비용. 밀리초로 내림한 히스토그램을 평균으로 읽은 것 | 192 µs (정책 169 µs) | archive/INDEX.md, archive/EXP-140 §2 |
| 9~15 µs | 같은 히스토그램의 `sum/count`. 실제로는 "1 ms를 넘은 결정의 비율"이다(11~19배 과소) | 192 µs | archive/EXP-140 §2 |
| 290~320 µs | 스케줄러 `-v 4`에서 잰 결정 비용(로깅 때문에 40 % 과대) | 192 µs | archive/EXP-140 §6 |
| EXP-108 llm-d 10 req/s 두 반복(`260831_0409…`, `260831_1252…`) | 거절 0.0 % 대 39.9~42.0 %로 갈린 칸 | EXP-110 반복 3·5 | archive/STATUS, `excluded_runs.tsv` |
| "선청구의 몫 −44점" | 누적 ablation `cum5`(지평 1). 지평이 속도 추정의 prefill 비중 분모에도 들어가 혼입됐다 | 없음(분리 측정 안 함) | archive/STATUS 2026-09-14 절 |
| 세션을 건넌 preemption 비교 | §6 7번 | 같은 세션 안에서만 | archive/STATUS |
| EXP-56 "정적 45 req/s에서 클래스 선호 +7.5점" | 길이 프로파일 수정 전, 배포 구성이 아님 | 정적 35 req/s +1.9~+5.6(상한 켬), 45 req/s +9.1~+11.4 | archive/STATUS, EXP-137 머리말 |
| EXP-53·EXP-54의 PolyServe 값 (16,363 / 3,509 / 26.7 / 27.5 / 17.1 / 3,192) | PolyServe 길이 프로파일 수정 전 | 20,212 / 9,064 / 26.5 / 35.6 / 18.9 / 5,632 | `archive/llumnix_reproduce/ms_dev/notes/retracted.tsv` |
| 1.50배, 18.7 req/s | FluidServe 대 llm-d의 90 % 용량 비, llm-d 90 % 유지 도착률(반복 1회) | 1.40배, 20.3 req/s | retracted.tsv |
| "gate 92~96 %, memory 0 %" | infeasible 사유 비율(워크로드 수정 전) | 게이트 84~98 %(10~20 req/s)·71~82 %(25~70), 메모리 17~20 %(35 req/s 위) | retracted.tsv |
| 11,549 / 11,399 | EXP-104 요약표의 선호 켬/끔 한 시간 goodput. 어느 정의로도 재현 안 됨 | 11,126·11,224 / 10,906·10,920 | retracted.tsv |
| 15.6배 | 그림 3 스크립트 주석에 남은 도착률 폭 | 5.77배(최대/최소), 3.37배(p95/p5) | §4.4 |
| README(그림)의 그림 14 손 표 | CSV와 다른 낡은 값 | `motivation_throughput_vs_goodput_4panel_t75_withfs.csv` | §4.10 |
| e2e 30초로 채점한 모든 swe·총점 수치(`static_sweep_2026-08`, EXP-53, EXP-71 등) | swe 약속 형태가 다르다 | EXP-108 이후 t75 수치 | archive/EXP-108 §3, §8.5 |
| 평균 규칙 수치와 ladder95 수치를 한 표에 | 규칙이 2·3위를 바꾼다(EXP-109: llm-d 54.0 > PolyServe 30.8 → 56.3 ≈ 53.6) | 한 규칙만 | archive/EXP-109 §5 ③ |

---

## 8. 2026-10-10 재현 확인 (측정 완료, 그대로 옮긴다)

규칙 ladder95. 함대 Llama-3.1-70B 4대 × TP=2.

**정적 — EXP-108과 같은 조건, 35 req/s, 8분**

| arm | 양 | 재현 반복 1 / 반복 2 | 비교 기준 (EXP-108) |
|---|---|---|---|
| FluidServe `fsv3capgnofrct75` | 모든 도착 달성률 | 77.5 / 76.1 | 75.4 (범위 74.6~76.2) |
| | admitted | 99.7 / 99.9 | 99.8 |
| | 거절률 | 22.3 / 23.8 % | 22.7 % |
| | token goodput | 13,818 / 13,643 tok/s | 13,796 |
| PolyServe `polyservept75` | 모든 도착 달성률 | 45.3 / 42.5 | 43.7 (43.3~44.1) |
| | admitted | 50.7 / 48.0 | 50.3 |
| | 거절률 | 10.6 / 11.5 % | 10.5~10.8 % |
| | token goodput | 9,818 / 9,475 | 9,746 |

**한 시간 mix-shift trace — FluidServe, 반복 1**

| 양 | 재현 | EXP-109 (반복 1 / 반복 2) |
|---|---|---|
| 도착 수 | 98,243 | 98,240 / 98,211 |
| 모든 도착 달성률 | 79.3 | 80.0 / 79.3 |
| admitted | 99.9 | 99.9 / 100.0 |
| token goodput | 11,591 tok/s | 11,637 / 11,587 |
| 클래스별 chat / deepresearch / swe | 83.6 / 70.1 / 72.9 | 84.8·83.7 / 68.6·69.7 / 74.5·73.1 |

run(이 기계의 `Agent_applications/agent_motivation_experiment/results/`에 있다): `261010_0038_rpro1010r1_*`,
`261010_0053_rpro1010r2_*`, `261010_0218_rpro1010pr1_*`, `261010_0238_rpro1010pr2_*`, `261010_0253_rpro1010hr1_*`.
같은 디렉토리의 `261010_0021_rpro1010bsmoke_*`는 연기 시험이라 표에 넣지 않았다.
⚠ "비교 기준 75.4 / 43.7"은 이 문서에서 다시 계산하지 않았다. 같은 run의 offered(미완을 분모에서 뺀 값)는
`exp108_paper_ladder95.csv`에서 FluidServe 78.1 / 76.3, PolyServe 44.5 / 45.3이다.

---

## 9. 데이터 위치 — 그림을 다시 그리려면

- **이 기계에는 위 다섯 재현 run밖에 없다.** 2026-10-06 컨테이너 초기화로 run 디렉토리가 전부 사라졌다. 사본:
  - 맥 외장 SSD `/Volumes/volume1/nxc13_backup_2026-09-23/`: run 1,841개 전부. 단 **`tbt_events.jsonl`(토큰별 도착 시각)과
    `agent_logs`는 빠져 있다.**
  - Lustre `/NHNHOME/storage/home_backup_2026-08-28/`: `results_priority/`(2026-08-28 백업, 1,296개 — **그 날짜 뒤의 EXP-108~140은
    여기 없다**), `results_archive_2026-10-05/`(tbt·agent_logs, zstd), `tbt_archive_2026-09-10/`(tbt 140개).
  - 근거: `archive/llumnix_reproduce/ms_dev/scripts/clean_backup_scripts/README.md` §1.
- **값만 필요하면 run이 필요 없다**: `paper_figures/*.csv`가 그려진 값 전부다(그림 11 시계열과 그림 1 제외).
- **그림 스크립트를 그대로 다시 돌리려면** ladder95 판정 캐시 `results/aggregate_analysis/ladder95/`(와 `ladder95_halved/`)가
  필요한데 **이 기계에 없다.** 맥 SSD의 사본에서 복원하거나, tbt를 Lustre에서 풀어 `deadline_ladder_attainment.py`로 다시 채점한다.
  다른 규칙이나 다른 SLO 배수로 다시 채점할 때도 tbt가 필요하다.

| 그림·표 | 필요한 run (결과 디렉토리 이름 패턴) | tbt 필요 |
|---|---|---|
| 그림 1 | `*exp109r2_*_shift` 다섯 arm (스크립트 없음) | 판정 캐시가 없으면 예 |
| 표 1 | EXP-53 15 req/s 네 arm, 각 1 run (`*exp53r*_rpm_900`) | 아니오 |
| 그림 3 | run 불필요. `traces/azure/plots/_minute_{conv,code}2024.csv`, `traces/dynamic/canonical/dyn60_azure4d.plan.json` | 아니오 |
| 그림 4 | `260831_2128_exp109r1_llmdslot75_shift`, `260831_2232_exp109r1_polyservept75_shift` (+ `scheduler_dispatch.log` 기반 클래스 구성 표) | 달성률 선에 판정 캐시 |
| 그림 5 | `260831_2346_exp109r1_slot75_shift` (엔진 8002 Prometheus) | 달성률 선에 판정 캐시 |
| 그림 6 | `*exp108r[12]_*_rpm_600` 여덟 run | 아니오 |
| 그림 11·12 | `*exp109r[12]_*_shift`, `*exp113r[12]_*_shiftq`, `*exp126h62r[12]_*_shift62` (각 10 run) | 판정 캐시 |
| 그림 13 | `*exp109r[12]_*_shift` 10 run | 판정 캐시 |
| 그림 14 | `*exp108r[12]_*_t75*_rpm_*` 64 run, EXP-110 llm-d 10 req/s 반복 3·5, `*exp77r[12]_vllmcache_m1_rpm_*` 15 run | 판정 캐시 (`/home/nxclab/handover/motivation_4panel_t75_withfs_col/`에 채점된 표가 있어 이 그림은 바로 그릴 수 있다) |
| 그림 15 | `*exp131[ehj]r[12]_fsv3t75h*_rpm_2100` 12 run, `*exp108r[12]_fsv3capgnofrct75_t75_rpm_2100` 2 run, `*exp136qr[12]_*_rpm_1344` 14 run | 아니오(평균 규칙) |
| 그림 16 | `*exp109r[12]_{fsv3capgnofrct75,llmdslot75,polyservept75}_shift`, `260914_0539_exp138r1_noaffnocaphour_shift` | 달성률에 판정 캐시 |
| 그림 17 | `260831_2015_exp109r1_fsv3capgnofrct75_shift`, `260914_0539_exp138r1_noaffnocaphour_shift` | 아니오 |
| 그림 18 | `*exp139k*r[12]_*` 22 run, `*exp108r[12]_*_rpm_2100` 8 run | 아니오(평균 규칙) |
