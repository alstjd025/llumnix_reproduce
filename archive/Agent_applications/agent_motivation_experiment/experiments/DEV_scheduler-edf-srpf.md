# DEV — vLLM intra-engine scheduler: FIFO → EDF / SRPF (EXP-15 prep)

**날짜**: 2026-07-21 · **상태**: planning (구현 전, 결정 대기)

## 목표

EXP-14 mix 실험을 vLLM 엔진 내부 스케줄러를 **FIFO(현재) vs EDF vs SRPF**로
바꿔가며 재실험. 가설: 과부하에서 스케줄 순서를 바꾸면 클래스별 SLO attainment
분포가 달라진다 — EDF는 deadline 임박 요청 보호, SRPF는 짧은 요청을 빨리 빼
head-of-line blocking(무거운 SWE prefill 뒤에 갇힌 chat) 완화.

FIFO baseline = EXP-14 기존 데이터. 따라서 **EDF·SRPF만 새로 실행**하고 EXP-14와
비교. 평가지표 = 우리가 만든 클래스별 차등 SLO attainment.

## 조사 결과 (2026-07-21 실측, 이미지 vllm 0.12.1.dev0)

- 두 스케줄러: **Llumnix scheduler**(inter-instance dispatch+migration) vs
  **vLLM scheduler**(intra-engine batch). 대상은 후자.
- 현재 FIFO 확정(`--scheduling-policy` 미지정 → `fcfs`).
- **vLLM "priority" 정책 네이티브 지원**: 대기열 `(priority, arrival_time)` 정렬,
  KV 포화 시 선점도 `(priority, arrival_time)` 최댓값부터. lower value = 먼저.
- **chunked prefill ON** (기본, `max_num_batched_tokens=8192`) → 긴 prefill 청크화,
  긴급 요청 prefill 삽입 가능. EDF/SRPF와 궁합 좋음.
- async-scheduling ON — priority와 호환(PP>1·speculative만 hard-fail).
- completions API `priority:int=0` 네이티브, 엔진까지 배선(`serving_completion.py:228`).
- **유일한 결손: gateway가 priority를 버림.** `neutral_forwarder.go:42`가
  `json.Marshal(CompletionRequest)`로 재직렬화 → struct 미선언 필드 drop.

## 매핑: EDF/SRPF → vLLM priority

- **EDF**: `priority = 도착_epoch_ms + 클래스_예산_ms` (절대 deadline). 이른
  deadline = 작은 값 = 먼저. arrival_time tiebreak와도 정합. (상대 deadline은
  틀림: priority=예산만 쓰면 도착 다른 요청 간 EDF 안 됨.)
- **SRPF**: `priority = 프롬프트_토큰수`. 짧은 프롬프트 = 작은 값 = 먼저.
  주의: vLLM priority는 **정적** → "shortest-prompt-first"(초기 길이)이지 진짜
  "remaining"(청크 진행에 따라 감소)이 아님. 대기열 admission에는 사실상 동일
  (running 중 prefill은 chunked로 이미 진행). 동적 remaining은 스케줄러 패치 필요.

## 구현 (3곳)

### A. Gateway: priority 필드 통과 (Go 재빌드 — 기존 shim/timeout 재빌드와 동일 절차)
- `pkg/gateway/protocol/openai_completion.go`의 `CompletionRequest`(및 응답 경로
  무관)에 `Priority *int \`json:"priority,omitempty"\`` 추가.
- forwarder는 이미 struct 전체를 marshal하므로 필드만 추가하면 자동 전달.
  neutral_forwarder + pd_vllm_kvt_forwarder(둘 다 neutral 경로일 수 있음) 확인.
- 산출물: `bin/gateway-exp10` 계열 재빌드, `patch-gateway-timeout.sh`류로 주입.

### B. 클라이언트: 요청당 priority 계산 (`LlumnixCompletionsLLM`)
- `workloads/swe_bench_coding/agent.py`의 `_payload()`에 `priority` 추가.
- 정책·클래스·예산을 RunContext로 주입(`--sched-policy {fifo,edf,srpf}` +
  클래스별 예산). mixed 워크로드는 클래스를 알고 있으므로 delegate에 예산 전달.
- EDF: `priority = int(time.time()*1000) + budget_ms[class]`.
  SRPF: `priority = count_tokens(prompt)`. FIFO: priority 미전송(0).

### C. 엔진: `--scheduling-policy priority` (deploy engine args)
- FIFO run은 기존 그대로, EDF/SRPF run만 `priority`로 배포(조건별 배포 전환).
- Llumnix scheduler(dispatch)는 변경 불필요 — priority는 intra-engine.

### 분석
- 재사용: `exp14_per_class_slo.py`(차등 SLO), per-engine attainment. 정책별로
  같은 그림 뽑아 FIFO(EXP-14) vs EDF vs SRPF 오버레이.

## 결정 (사용자 확정 2026-07-21)

- **정책 4개**: FIFO(기존) + **EDF** + **SJF**(정적 shortest-prompt) + **SRPF**(동적 remaining).
  사용자 정정: 정적 프롬프트-길이 우선은 SJF이고, SRPF는 remaining을 동적으로
  계속 재평가하는 것 → **셋 다 구현, SRPF는 vLLM 내부 구현.**
- **EDF deadline = 도착 + 클래스 SLO 예산 그대로** (chat+5s, dr+10s, swe+E2E 20s).
  SWE도 E2E 예산을 prefill deadline으로 직접 사용(decode 예약 안 함).
- **migration 비활성** (순수 intra-engine 스케줄러 효과 격리). FIFO baseline도
  smoke에선 migration-off로 새로 떠서 공정 비교.
- **전체 실험 보류, smoke만**: FIFO 실험 중 한 조건을 잡아 EDF/SJF/SRPF 각각
  예상 결과를 미리 세우고 smoke → 예측 대비 검증.

## EDF 동작 검증 (vLLM V1 소스 실독, 2026-07-21)

사용자 질문: "10s deadline이 chunked-prefill 중인데 5s deadline이 새로 오면 vLLM이
5s를 먼저 처리하나? priority 불변인데?"

**답: 예, 처리한다. priority 불변은 문제 없음 — EDF는 절대 deadline이라 불변이 맞고,
새 요청은 자기 priority를 live 큐에 들고 들어온다.** 정확한 메커니즘:
- 대기열 = live priority heap → 5s가 top(admission은 항상 최우선). ✓
- 매 step 선점 재평가: running 요청이 KV 성장(decode +1블록)을 못하면
  `max(running, key=(priority,arrival))` = **가장 늦은 deadline running을 선점**
  → KV 확보 → 5s waiting 승격. (scheduler.py:319)
- **정직한 caveat**: vLLM은 "waiting 위해 running 직접 선점" 경로가 없다 —
  waiting 루프는 KV full 시 `break`(scheduler.py:582). 대신 **running 성장 요구가
  선점을 유발**해 간접 확보. 우리 과부하(KV 100%, 지속 decode)에선 선점이 계속
  발생 → 5s가 빠르게 admit. **prefill-only 포화 코너케이스에선 지연 가능**(우리
  smoke엔 무관).
- running은 append 순(우선순위 정렬 아님) → priority는 admission·선점에만 작용,
  step 내 running 처리 순서엔 무영향. EDF엔 충분.
- 따라서 **LLF(laxity 동적 재계산) 불필요**. 정적 절대-deadline priority = 정확한 EDF.

## 정책 → priority 매핑 (최종)

| 정책 | priority 값 | 구현 위치 | vLLM 정책 |
|---|---|---|---|
| FIFO | (미전송) | — | fcfs |
| EDF | 도착ms + 예산ms[class] | client+gateway | priority |
| SJF | 프롬프트 토큰수(정적) | client+gateway | priority |
| SRPF | 남은 prefill 토큰(동적) | **vLLM 패치** | (custom) |

## SRPF vLLM 패치 — 설계 선택 (결정 필요)

V1 스케줄러(`vllm/v1/core/sched/scheduler.py` + `request_queue.py`)에서 대기열은
`PriorityRequestQueue`(heap). **대기 요청은 prefill 미시작 → remaining=전체 길이라
대기열 정렬만으론 SJF와 동일.** 차이는 chunked prefill 진행 중 요청 처리에서만:

- **S1 (admission-only, ~SJF)**: 대기열을 remaining으로 keying(=프롬프트 길이).
  거의 SJF와 동일. 가장 싸지만 SJF와 구분 안 됨 → 의미 없음.
- **S2 (running-prefill 재정렬, 비선점)**: 매 step token budget을 remaining 적은
  running-prefill부터 배정(거의 끝난 것 먼저 완료 → decode 진입 → 평균지연↓).
  중간 난이도. running set 순회 순서 패치.
- **S3 (선점형 SRPT)**: 훨씬 짧은 신규 요청이 진행 중 긴 prefill을 선점. 교과서
  SRPF에 가장 근접, 가장 침습적(선점+재개 로직).

vLLM 스케줄러는 Python이라 hostPath로 패치 파일 주입 가능(Go gateway와 달리
재빌드 불필요). 권장: **S2** — SJF와 명확히 구분되면서 선점의 복잡도/불안정성 회피.

## 구현 (4곳)

### A. Gateway (Go 재빌드): `CompletionRequest`에 `Priority *int` 추가
`pkg/gateway/protocol/openai_completion.go` — forwarder가 struct 전체 marshal하므로
필드 추가만으로 전달. builder-gateway.yaml류 인클러스터 CGO 빌드 후 주입.

### B. 클라이언트: `LlumnixCompletionsLLM._payload()`에 priority
`--sched-policy {fifo,edf,sjf,srpf}` + 클래스별 예산을 RunContext로. EDF/SJF는
client가 priority 계산, SRPF는 priority 미사용(엔진이 remaining으로 자체 정렬).

### C. 엔진 deploy: 정책별 배포
- EDF/SJF: `--scheduling-policy priority`.
- SRPF: 패치된 스케줄러 + custom policy 플래그(S2). migration off.
- FIFO: 기존 + migration off.

### D. vLLM SRPF 패치 (S2, Python hostPath 주입)
`request_queue.py`/`scheduler.py` 사본을 패치해 running-prefill 토큰 배정을
remaining 오름차순으로. 조건별로 원본/패치 스왑.

## Smoke 계획 + 예측 (예측 먼저, 검증)

**조건**: mix A(1:1:1) @ **22 req/s** (knee 직후 cliff, KV 100%, 대기열 형성 →
재정렬 여지 최대. FIFO에선 fleet~45%, 차등SLO chat 43/dr 100/swe 2). 4정책 각 1회.

**예측** (차등 SLO: chat 5s/50ms, dr 10s/100ms, swe E2E 20s):
- **공통**: EDF/SJF/SRPF 모두 짧은·긴급 요청(chat)을 prefill 우선 → **chat·dr
  attainment↑, swe↓**(이미 ~2%라 바닥). fleet(차등)↑.
- **SJF**: chat<dr<swe 크기순 → chat 최대 보호, **SWE 기아 위험**(chat/dr 계속
  도착하면 swe 영원히 대기). 완료 처리량 최대.
- **EDF**: deadline-aging → 오래 기다린 dr/swe의 deadline 임박 시 승격 →
  **SJF보다 기아 덜함**(swe가 가끔 순번 받음), chat은 SJF보다 미세하게↓.
- **SRPF(S2)**: 대기 admission은 SJF와 동일하나 거의 끝난 prefill을 먼저 완료 →
  평균지연 최소, **near-done SWE는 SJF보다 살아남음**.
- 요약 예상: chat attainment FIFO < EDF ≲ SRPF ≈ SJF; swe attainment SJF <
  SRPF ≲ EDF < FIFO; fleet(차등) FIFO < EDF ≲ SRPF ≲ SJF.

## 결과 (smoke 2026-07-22, mix A @ 22 req/s, 4정책, migration off)

차등 SLO(chat 5s/50ms, dr 10s/100ms, swe E2E 20s), 표준 창 [60,340].

| policy | chat | deepr | swe | fleet | out tok/s | 대기열Σ(mean) |
|---|---|---|---|---|---|---|
| FIFO | 40.8 | 100.0 | 1.9 | 50.0 | 7,342 | 6.2 |
| EDF | 42.0 | 100.0 | 1.9 | 50.2 | 7,273 | 6.3 |
| SJF | 41.8 | 98.7 | 1.9 | 49.8 | 7,185 | 6.3 |
| SRPF | 37.4 | 99.3 | 1.7 | 48.4 | 7,205 | 6.1 |

### 예측 대비: **빗나감 — 네 정책이 구별되지 않음**

예측은 "chat↑(EDF/SJF/SRPF), swe 기아, fleet↑"였으나 **전 지표가 노이즈 수준**
(chat 37–42, fleet 48–50, out tok/s 7.2–7.3k). 정책은 실제로 적용됐다(엔진 로그에
`scheduler_cls: llumnix_sched.{SJF,SRPF}Scheduler`, `scheduling_policy: priority`
확인) — **구현 문제가 아니라 조건 선택 문제.**

### 원인 (진단 완료)

1. **재정렬할 큐가 없었다.** 22 req/s에서 **대기열 = 6.2**(4엔진 합, max 145),
   running = 703. 스케줄링 정책은 *waiting queue*의 순서를 정하는데, 큐가 사실상
   비어 있으면 FIFO/EDF/SJF/SRPF가 모두 "도착 즉시 admit"으로 수렴한다.
   EXP-14 mix A 기록을 다시 보면 queμ는 22→7, 30→618, 38→1877, 47→3447로,
   **22는 KV는 100%인데 큐는 아직 안 쌓인 지점**이었다. cliff 민감도만 보고 고른
   것이 실수.
2. **이 지점의 SLO 실패는 admission이 아니라 decode-side다.** chat TTFT는
   0.49–0.57s로 5s 예산 대비 여유가 크다 → chat 위반은 전부 **TBT>50ms**.
   TBT는 같은 배치에서 동시에 도는 running 703개가 만드는 것이라 **admission
   순서를 바꿔도 못 고친다**. SWE도 TTFT 0.86s인데 E2E 52s(예산 20s) — 역시
   decode 시간이 지배.

요약: **"KV 포화 = 스케줄러가 일할 거리가 있다"가 아니었다.** 엔진이 전부 admit해
거대한 배치로 같이 느려지는 구간이라 순서 정책의 지렛대가 없었다.

## 결과 2 (2026-07-22, 큐가 실재하는 지점 30·38 req/s)

| rate | policy | chat | deepr | swe | fleet | out tok/s | 대기열Σ | TTFT chat | TTFT swe |
|---|---|---|---|---|---|---|---|---|---|
| **30** | FIFO | 9.9 | 55.6 | 0.9 | 23.4 | 7,713 | 631 | **14.11s** | 9.61s |
| | EDF | 9.0 | **64.1** | 1.1 | **26.5** | 7,696 | 593 | 8.31s | 11.52s |
| | SJF | 10.1 | 49.0 | 1.1 | 23.0 | 7,459 | **304** | **1.27s** | 1.85s |
| | SRPF | 10.7 | 48.2 | 1.1 | 23.0 | 7,426 | 312 | 1.21s | 1.78s |
| **38** | FIFO | 0.7 | 44.0 | 0.0 | 15.6 | 7,007 | 1,859 | **28.26s** | 18.17s |
| | EDF | 0.8 | 44.1 | 0.0 | **16.1** | 6,984 | 1,769 | 19.45s | 18.65s |
| | SJF | 3.2 | 23.1 | 0.0 | 11.3 | 7,252 | **817** | **1.52s** | 2.08s |
| | SRPF | 3.1 | 22.9 | 0.0 | 11.1 | 7,270 | 814 | 1.54s | 1.97s |

### 핵심 발견 — 정책은 크게 작동하지만, SLO는 다른 축이 지배한다

1. **SJF/SRPF의 큐·TTFT 효과는 거대하다.** TTFT chat 14.1s→**1.27s**(30, 11×),
   28.3s→**1.52s**(38, 19×), 대기열 631→304 / 1859→817(반감). 22k SWE prefill 뒤에
   갇혀 있던 짧은 요청이 즉시 prefill되는 것 — 설계 의도대로 작동.
2. **그런데 SLO attainment는 거의 안 오른다.** chat은 9.9→10.1(30), 0.7→3.2(38).
   이유: chat SLO는 TTFT≤5s **AND** TBT≤50ms인데, **TTFT는 이미 고쳐졌고 남은
   병목이 TBT**다. TBT는 running 배치 크기가 만드는 것이라 admission 순서로는
   못 고친다. (22 req/s 결과의 교훈과 동일 — 실패가 decode-side다.)
3. **SJF/SRPF는 오히려 fleet을 악화시킨다**(38에서 15.6→11.3). 큐를 빨리 비운다는
   건 **더 많은 요청을 배치에 밀어 넣는다**는 뜻 → 배치가 커져 TBT가 나빠짐 →
   TBT 예산이 있는 deepresearch가 44.0→23.1로 급락. **처리량-지연 tradeoff가
   SLO 축에서 역효과**로 나타남.
4. **EDF만 fleet을 개선**했다(30: 23.4→**26.5**, dr 55.6→**64.1**). 크기가 아니라
   deadline으로 정렬하니, 예산이 큰 dr이 마감 임박 시 승격되어 보호받음. 38에서는
   16.1 vs 15.6으로 차이 소멸(이미 과붕괴).
5. **SJF ≈ SRPF** (모든 지표에서 1%p 이내). 예상대로 **동적 remaining 재정렬(S2)의
   추가 이득은 미미** — 대기열 순서가 지배적이고, 대기 요청의 remaining은 곧 전체
   프롬프트 길이라 SJF와 같기 때문.
6. swe는 전 정책에서 0~1%. E2E 90–113s로 20s 예산과 5배 격차라 스케줄링으로
   메울 수 없음.

### 예측 대비 채점

| 예측 | 결과 |
|---|---|
| chat attainment: FIFO < EDF ≲ SRPF ≈ SJF | **빗나감** (attainment는 거의 불변). 단 **TTFT는 예측대로 대폭 개선** |
| swe 기아: SJF < SRPF ≲ EDF < FIFO | **빗나감** — swe는 어느 정책에서도 ~0%, 기아 차이 관측 불가 |
| fleet: FIFO < EDF ≲ SRPF ≲ SJF | **부분 적중** — EDF>FIFO는 맞음(30). **SJF/SRPF<FIFO로 역전**(예측 반대) |
| SJF ≈ SRPF (동적 이득 작음) | **적중** |

**한 줄 결론**: intra-engine 스케줄링은 **큐·TTFT를 극적으로 바꾸지만**, 이 워크로드의
SLO 실패는 **decode-side(TBT/E2E)** 라 admission 재정렬로는 goodput이 안 오르고,
오히려 배치를 키워 역효과가 날 수 있다. **deadline 기반(EDF)만이 goodput을
개선**했다. → 진짜 지렛대는 admission 순서가 아니라 **배치에 얼마나 넣을지(동시성
제어/admission control)** 쪽임을 시사 (EXP-05~09 KV admission 아크와 연결).

### 다음 (조건 재선정)

스케줄링 정책이 의미를 가지려면 **대기열이 실재하는 지점**이어야 한다:
- mix A **30 req/s**(queμ 618) 또는 **38 req/s**(1877). 38이 지렛대가 더 크지만
  FIFO attain이 이미 3%라 개선 여지가 작을 수 있어, **30을 1순위**로 권장.
- 그리고 chat 위반이 TBT-지배인 점을 감안하면, admission 재정렬로 개선되는 건
  주로 **TTFT/E2E**다. 즉 **SWE E2E(20s)와 대기 시간이 큰 지점**에서 효과가
  드러날 가능성이 높다.

인프라(정책 전환기·gateway priority·커스텀 스케줄러)는 전부 검증 완료라 조건만
바꿔 재실행하면 된다.
