# EXP-21 — PolyServe routing vs stock Llumnix load-balance (mix A)

## Why
EXP-17~20은 **엔진 스케줄러**를 바꾸고(FIFO/EDF/SJF/SRPF/QoServe) 라우팅은
load-balance로 고정했다. 결론은 "TTFT는 재정렬로 고쳐지지만 TBT는 배치 구성
통제로만 고쳐진다"였고, 어떤 엔진 정책도 goodput 붕괴를 막지 못했다.

그렇다면 **배치 구성을 라우팅 단계에서 통제**하면 어떻게 되는가? PolyServe
(arXiv:2507.17769)가 정확히 그 주장이다: 요청을 SLO tier로 나누고 tier마다
서버를 배타적으로 할당해, 무거운 클래스가 가벼운 클래스의 배치에 섞이지
않게 한다. 이 실험은 **엔진을 stock FIFO로 고정하고 라우팅만 바꾼다** —
EXP-17~20과 정확히 수직인 축.

구현은 `llumnix_reproduce` 쪽 P0~P3 (커밋 `ed7c3a5`, `9e0d51d`, `ecb5810`,
`fe2c122`). 설계 정본 `POLYSERVE_DESIGN_KO.md`, 진행 기록 `POLYSERVE_PROGRESS.md`.

## Hypothesis
1. tier 격리로 chat/deepresearch가 22k 토큰짜리 swe prefill을 아예 안 보게 되어
   두 클래스의 attainment가 크게 오른다.
2. swe는 엔진 4대 → 2대로 줄어 **나빠질 것이다**(용량 손실).
3. fleet 전체로는 (1)의 이득과 (2)의 손해가 상쇄되어 애매할 수 있다.

→ **2번과 3번은 틀렸다.** 아래 결과 참조.

## Exact settings
- 엔진: Llama-3.1-70B-Instruct, TP2 × 4, max-model-len 40960, chunked prefill
  (`max_num_batched_tokens=8192`), async scheduling.
  **양쪽 arm 모두 stock FIFO**(`SCHED_EXTRA_ARGS=''`), **migration OFF**,
  KV admission θ=0(off). 드라이버가 시작 전에 셋 다 검사하고 아니면 중단.
- arm은 스케줄러 라우팅 정책만 다름:
  - `loadbalance` — stock Llumnix. `all_prefills_tokens_num`(대기+진행중+inflight
    prefill 토큰)이 8192 미만인 인스턴스 중 **최솟값** 선택. (round-robin 아님)
  - `polyserve` — tier affinity + §4.5~4.7 feasibility test + least-load.
    `--polyserve-tier-decode-tokens "25:728,50:386,100:275"`.
- workload: `mixed_request_level_poisson` mix A(1:1:1), `--priority-mode deadline`
  (**양쪽 arm 모두** packed priority 전송; load-balance는 무시).
  SLO/tier 키 `workload_configs/mix_polyserve.json`:
  chat TTFT 5000/TBT 50, deepresearch 10000/100, swe 11800/25.
- 프로토콜: EXP-14 동일. rate 600/1200/1800/2400/3000 rpm, 조건당 5분,
  warmup 60s@60rpm, **조건마다 엔진 콜드 재시작**, load 12 procs × 2048 threads.
- 채점: EXP-17과 동일 규칙(`exp14_per_class_slo`) — chat TTFT≤5s & TBT≤50ms,
  deepresearch TTFT≤10s & TBT≤100ms, swe E2E≤30s. 창은 `served_rows` 표준
  (arrival-anchored [60s, min(last,360)−20s]).

## Result

### 지표 선택 주의 — fleet attainment는 쓰면 안 된다
처음 뽑았을 때 polyserve의 fleet attainment가 부하와 함께 **좋아졌다**
(79.2 → 85.1 → 90.1). 원인은 분모 구성 변화다. 3000 rpm에서 served 요청 중
swe 비중이 loadbalance 29.7% vs polyserve **10.1%** — 100%를 받는 두 클래스가
분모를 지배한 것. 워크로드는 1:1:1로 제공되므로 **클래스 등가중(equal-mix)** 이
mix-독립 비교이고, 아래는 전부 그 값이다(polyserve 3000rpm: 90.1 → **67.2**).

### 클래스별 SLO attainment (%)

| rpm | req/s | eqmix LB→PS | chat LB→PS | deepresearch LB→PS | swe LB→PS |
|---|---|---|---|---|---|
| 600 | 10 | 97.9 → 99.2 | 100 → 100 | 100 → 100 | 93.8 → 97.5 |
| 1200 | 20 | 69.1 → 78.3 | 83.0 → **100** | 100 → 100 | 24.3 → 35.0 |
| 1800 | 30 | 22.4 → **69.5** | 9.3 → **100** | 55.3 → **100** | 2.5 → 8.5 |
| 2400 | 40 | 14.3 → **67.4** | 0.8 → **100** | 41.1 → **100** | 1.2 → 2.2 |
| 3000 | 50 | 11.1 → **67.2** | 0.0 → **100** | 33.4 → **100** | 0.0 → 1.7 |

**chat과 deepresearch가 전 rate에서 100%.** 그리고 **swe도 모든 rate에서
loadbalance 이상** — 가설 2가 틀렸다.

### Goodput output tokens/s — 더 결정적

| req/s | FIFO | EDF | SJF | SRPF | QoServe | **PolyServe** |
|---|---|---|---|---|---|---|
| 14 | 4,860 | 4,852 | 4,851 | 4,831 | 3,987 | — |
| 22 | 3,354 | 3,231 | 3,274 | 3,204 | 2,781 | — |
| **30** | 1,420 | 1,408 | 1,176 | 1,318 | 1,819 | **7,417** |
| 47~50 | 602 | 587 | 191 | 157 | 1,065 | **11,658** |
| 57 | 328 | 289 | 103 | — | 703 | — |

**엔진 스케줄러 5개는 전부 ~14 req/s에서 꺾여 붕괴한다. PolyServe만 단조 증가한다.**
30 req/s(6개 arm이 모두 측정된 유일한 rate)에서 FIFO 대비 5.2배, QoServe 대비 4.1배.

총 처리량도 같은 방향이고 클라이언트 측정 착시가 아니다(서버 카운터 확인):

| rpm | 처리 req/s LB→PS | 출력 tok/s LB→PS | preemption LB→PS |
|---|---|---|---|
| 1800 | 19.9 → 25.9 | 7,740 → 10,234 | 331 → 171 |
| 3000 | 13.2 → 36.2 | 5,041 → **13,467** | 503 → 261 |

loadbalance는 1800 rpm 이후 처리량이 **감소**한다(7,740→6,372→5,041) —
전형적 congestion collapse. PolyServe는 계속 오른다.

### 메커니즘 확인 — tier 격리가 실제로 걸렸다
라우팅 집중도(0=엔진에 고르게 분산, 1=한 대에 고정):

| arm | chat | deepresearch | swe |
|---|---|---|---|
| loadbalance | 0.00 | 0.00 | 0.00 |
| polyserve | 0.99 | 0.99 | **0.35** |

swe의 0.35는 **4대 중 2대에 갇힌 클래스의 이론값(0.333)** 과 일치.
재분할기가 `swe 2 / chat 1 / deepresearch 1`로 수렴했고(설계 예측과 동일),
관측 demand는 25ms=0.65 / 50ms=0.02 / 100ms=0.07 서버-초/초였다.
demand 비율 88/3/9%가 토큰 질량 82.6/2.5/15%보다 swe로 더 쏠리는데, swe의
25ms TPOT가 배치를 작게 강제해 같은 출력이 더 많은 서버-초를 먹기 때문.

600 rpm 스모크(양쪽 100% attainment, 변별력 없는 구간)의 클래스별 중앙값:

| class | TTFT p50 | TBT p50 | E2E p50 |
|---|---|---|---|
| chat | 0.153 → 0.109 | 15.9 → 9.3 | 14.18 → 8.56 |
| deepresearch | 0.215 → 0.154 | 14.5 → 9.4 | 8.63 → 5.24 |
| swe | 0.339 → 0.324 | 15.2 → 13.7 | 15.65 → 13.54 |

포화 전에도 세 클래스 모두 개선 → 이득의 원천은 **용량이 아니라 간섭 제거**다.
600 rpm에서는 아무것도 포화가 아니라(KV p90 0.09~0.125) 분리가 용량을 늘려줄
수 없다. 없앤 것은 22k 토큰 swe prefill이 chat을 decode 중인 배치에 섞이는 것 —
EXP-16/17이 엔진 쪽에서 찾아낸 바로 그 효과.

## 이득의 출처는 tier 파티션이지 admission test가 아니다
admission 거부 사유가 스모크에서 **148건 전부 "steady state"**(`iterMax > tpotSlo`)
였다. `iterMax`에 §4.7 prefill 간섭항이 들어가므로 대기 prefill이 있는 인스턴스는
iterMax≈650ms가 되어 모든 tier(25/50/100ms)에서 탈락하고, 전부 탈락하면 fallback
으로 admission이 풀린다. 즉 **admission은 고부하에서 사실상 무력화됐는데도** 위
결과가 나왔다. 논문의 해법인 dynamic chunking(§4.7)은 admission을 되살리는
작업이므로, 이 결과 기준으로는 우선순위가 낮다.

거절은 사실상 없다: 503이 polyserve 45/45,300(0.10%), loadbalance 20/45,300
(0.04%). 둘 다 게이트웨이가 hold-and-retry 창을 소진하고 포기한 것이지 정책이
요청을 거절한 것이 아니다. (클라이언트가 이걸 `KV_THRESHOLD`로 라벨링하는데
θ는 꺼져 있다 — llumnix 429/503에 붙는 레거시 기본 이름이라 오해 소지 있음.)

## Baseline 재현성 (부수 성과)
EXP-21 `loadbalance`는 EXP-17 `fifo` arm과 같은 구성(엔진 stock FIFO + 라우팅
load-balance)이다. 두 grid가 겹치는 1800 rpm에서:

| run | n | eqmix | chat | dr | swe |
|---|---|---|---|---|---|
| EXP-14 mixA (= EXP-17 fifo) | 5,569 | 22.5 | 9.5 | 55.4 | 2.7 |
| EXP-21 loadbalance | 5,567 | 22.4 | 9.3 | 55.3 | 2.5 |

5일 간격 + 스케줄러/게이트웨이 바이너리 전면 재빌드를 사이에 두고 모든 클래스가
0.2%p 이내 재현. 두 실험을 한 그래프에 올릴 근거이자 스택 재현성 확인.

## 한계 / 해석 주의
1. **채점 기준이 논문과 다르다.** 논문은 DSLO(누적, i번째 토큰 < TTFT+i·TPOT)로
   채점하고 우리는 순간 기준을 쓴다(EXP-17과 직접 비교하기 위해). 우리 수치가
   논문보다 낮게 나오는 게 정상.
2. **rate grid가 EXP-17과 다르다.** 겹치는 건 1800 rpm 하나. 선 그래프에서는
   각 arm이 자기 표본점을 가지면 되므로 비교는 유효하지만, 특정 rate 대조는
   1800에서만 직접적이다.
3. **`ttft.json` 청크 합산 한계.** 청크 크기만으로 인덱싱하는 테이블은 뒤 청크가
   앞 청크 KV에 attention하는 걸 못 봐서 긴 프롬프트를 12k에서 4%, 24k에서 13%
   과소예측한다. swe(입력 ~22k)의 TTFT 예측 정확도 하한.
4. **tier가 토큰 질량과 정렬되어 있다**(swe = 가장 빡빡한 TPOT 25ms이면서 토큰의
   82%). 이 정렬이 PolyServe에 유리하게 작용했을 수 있다. mix B/C에서 재확인 필요.
5. swe의 절대 attainment는 두 arm 모두 고부하에서 사실상 0이다. PolyServe가
   swe를 "구한" 것이 아니라 **다른 클래스를 swe로부터 구한** 것이다.

## 산출물
- 6-arm 비교: `results/aggregate_analysis/exp21_6arm/`
  (`exp17_fleet_attainment.png` = 등가중, `exp17_per_class.png`,
  `exp17_goodput_vs_rate.png`, `exp17_goodput_tokens_over_time.png`)
- 2-arm 상세: `results/aggregate_analysis/exp21_polyserve/`
  (attainment/per-class/mechanism + engine composition/load, latency CDF, time series)
- arm별 표준 세트: `results/aggregate_analysis/exp21_engines/{loadbalance,polyserve}/`
  (`slo_vs_throughput_{full,steady}`, `kv_vs_throughput`, `inflight_vs_throughput`,
  `per_class_attainment_*`, `per_engine_attainment_*`, `persplit/{engine,llumnix,tokens}_rpm_*`)
- 드라이버 `k8s/exp07/run_exp21_polyserve.sh`, 러너 `runner-exp21.template.yaml`
- 분석 `analysis_scripts/request_level/exp21_{polyserve,engines,smoke_latency}.py`
  및 `exp17_{qoserve_vs_fifo,goodput_tokens}.py`(polyserve arm 추가)
