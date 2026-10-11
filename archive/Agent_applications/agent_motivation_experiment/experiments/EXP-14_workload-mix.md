# EXP-14 — 워크로드 mix rate sweep (chat + deep-research + SWE)

**날짜**: 2026-07-20 · **상태**: implementing · **브랜치**: `feat/exp07-kv-admission`

## 왜

지금까지는 워크로드를 **하나씩** 돌려 각각의 붕괴 특성을 확보했다:

| 워크로드 | 입력 mean | knee | 과부하 거동 | 정본 |
|---|---|---|---|---|
| chat (sharegpt) | 674 | ~55 req/s | tok/s 평탄 19.2k | EXP-12 |
| deep-research (searcharena) | 4,055 | ~13 req/s | tok/s 평탄 ~3.5k out | EXP-13 |
| SWE (codingagent) | 22,474 | ~5 call/s | **tok/s 붕괴** peak→1/3 | EXP-10 |

실제 서빙 클러스터는 이들이 **섞여서** 들어온다. mix에서 새로 물어볼 수 있는 것:

1. **간섭(interference)**: 무거운 SWE 요청이 같은 엔진에 들어왔을 때 가벼운 chat
   요청의 latency를 얼마나 망가뜨리나? (head-of-line blocking / KV 점유 경쟁)
2. **클래스별 공정성**: 과부하에서 어떤 클래스가 먼저·더 많이 SLO를 잃나?
   단일 워크로드 knee(55 / 13 / 5)로 예측한 것과 mix에서의 실제가 다른가?
3. **혼합 비율의 영향**: 무거운 클래스 비중이 커지면 전체 용량이 어떻게 무너지나
   — 요청 비율은 선형인데 토큰 질량은 비선형이므로.

## 설계

### 워크로드: `mixed_request_level_poisson` (신규)

세 request-level 워크로드를 **위임(delegate)** 방식으로 섞는 어댑터. 자체 요청
내용이 없고, 각 클래스의 단일 워크로드 의미론(프롬프트·결정성·절대 SLO·무-abort)을
그대로 유지 → mix 결과를 단일 워크로드 baseline과 직접 비교 가능.

- 클래스 배정: 가중치 합 크기의 **블록**을 결정적으로 셔플해 반복 → 목표 비율을
  블록 단위로 정확히 실현(i.i.d. 샘플링은 5분 run에서 비율이 흔들려 "비율 비교"라는
  실험 목적을 훼손). 자세한 근거는 `mixplan.py`.
- **metrics 스키마 무변경**: 각 delegate가 평소대로 기록(`agent=="request"`).
  클래스는 **task_id 접두사**로 복원 — `sg-`=chat, `sa-`=deep-research, 나머지=SWE.
  기존 분석 스크립트가 그대로 동작하고 per-class는 groupby로 얻는다.
- SWE transcript는 1.5GB라 24프로세스에 올릴 수 없어 **균등 샘플 1500건**
  (`transcript_swe_calls_mix1500.jsonl`, 170MB, 입력 mean 22,474 = 원본과 동일)을
  쓴다.

### 비율 (요청 수 기준)

**요청 비율 ≠ 토큰 비율**임에 주의 — 입력 크기가 클래스 간 ~33× 차이. run_config에
둘 다 기록한다.

| 조건 | mix (chat:dr:swe) | 요청 비율 | 예상 입력토큰 비율 | 의도 |
|---|---|---|---|---|
| **A** | 1:1:1 | 33/33/33% | 2.5/15/82% | 기준 — 균등 요청 |
| **B** | 6:3:1 | 60/30/10% | 8.6/26/65% | 경량 우세(현실적 프로덕션 믹스) |
| **C** | 1:1:3 | 20/20/60% | 1.2/7.2/92% | 중량 우세(에이전트 부하 스트레스) |

B는 chat이 다수인 현실적 구성, C는 SWE가 토큰 질량을 지배하는 스트레스 구성.
셋 다 토큰 질량은 SWE가 지배하지만 그 정도가 65%→82%→92%로 달라진다.

### 프로토콜 / grid

exp12-13 표준 동일: 조건별 cold restart, warmup 60s + 본 **5분** + grace 60s,
표준 분석 창 **[60,340]**, θ=0, no-timeout gateway + 64Gi.

- 부하기: **12 procs × 2048 threads** (transcript 170MB × 12 ≈ 2GB).
- **`--disable-timeouts` 필수**: SWE delegate만 `job_timeout_sec = baseline×tau`
  클라이언트 abort를 걸고 chat/deep-research는 abort가 전부 꺼져 있다. 그대로 두면
  mix에서 SWE만 죽어 클래스 간 비교가 오염된다(EXP-10도 같은 이유로 이 플래그를
  썼다). 이 플래그로 세 클래스 모두 무-kill로 통일한다.
- **초기 grid(1–16 req/s)는 용량을 과소평가**했다: mix A를 돌려보니 16 req/s에서도
  전 클래스 100%, KV peak 38%, TTFT 0.6s로 knee 근처도 못 갔다. 원인은 **prefix
  cache** — "input_tokens"는 캐시된 부분까지 포함하지만 실연산(KV 점유)은 uncached
  tail뿐이라, 요청당 입력토큰으로 추정한 용량이 크게 낮았다. KV가 진짜 부하 신호.
- **재설계(2026-07-20)**: KV를 부하 지표로 삼아 knee(≈KV 90%)를 추정하고 비율별
  grid를 knee 중심으로 배치. mix A는 KV가 선형(0.024/(req/s))이라 knee≈37 req/s.
  캐시성 큰 chat 비중이 큰 B는 더 높고, SWE 토큰 지분 큰 C는 더 낮다.

  | 비율 | 추정 knee | grid (req/s) |
  |---|---|---|
  | C (1:1:3) | ~19 | 4, 8, 13, 18, 24, 31, 40 |
  | A (1:1:1) | ~37 | 8, 14, 22, 30, 38, 47, 57 |
  | B (6:3:1) | ~70 | 14, 26, 40, 55, 70, 85, 100 |

  각 7조건, knee의 ~0.2×–1.4× 범위를 덮어 pre-knee·전이·과부하를 포착.
- 소요: 조건당 ~10.5분 × 21 = **~3.7시간**.
- 초기 mix A(1–16) run은 pre-knee 저rate 데이터·귀속 검증용으로 보존
  (results/*exp14_mixA_rpm_{60..960}); 재실행 A grid가 knee~과부하를 덮는다.

### 새로 붙는 계측: per-request → 엔진 귀속

EXP-13에서 불가능했던 "엔진별 SLO attainment"을 이번엔 측정한다. 구현 상세는
[DEV_request-engine-attribution.md](DEV_request-engine-attribution.md):
클라가 응답 `id`(uuid)를 `request_ids.jsonl` sidecar에 기록 + 수집기가 스케줄러의
`[Schedule] dispatch request <uuid> to ... instance <id>` 라인을
`server_metrics/scheduler_dispatch.log`로 스트리밍 → `build_request_engine_map.py`가
조인. mix에서는 이게 특히 의미 있다: **같은 엔진에 무거운 요청이 몰렸을 때 그
엔진의 가벼운 요청이 어떻게 되는지**를 직접 볼 수 있다.

## 가설

1. **간섭 가설**: mix에서 chat의 SLO knee는 단독(55 req/s)보다 훨씬 낮은
   전체-rate에서 무너진다. chat 요청 자체는 가볍지만 같은 배치/KV를 SWE와 공유하므로
   — 즉 **클래스별 knee는 자기 특성이 아니라 공유 자원 상태가 결정**한다.
2. **질량 지배 가설**: 전체 knee는 요청 비율이 아니라 **토큰 질량 비율**을 따라간다.
   A/B/C의 knee(req/s)는 SWE 토큰 지분(82/65/92%)의 역순 ≈ B > A > C 로 예상.
3. **붕괴 전염 가설**: SWE가 단독에서 보인 throughput 붕괴가 mix에서도 나타나되,
   SWE 지분이 클수록(C) 강하게 나타난다. A/B에서는 EXP-13처럼 평탄에 가까울 것.

## 결과 (2026-07-21 완료; 표준 창 [60,340], 비율별 grid)

SLO 규칙 = TTFT≤5s(도착-앵커) AND meanTBT≤50ms; 에러/컷 제외. 클래스는
task_id 접두사(sg-/sa-/그외). 정본 그림: `results/aggregate_analysis/exp14_mix/`.

### 세 비율의 fleet SLO attainment (knee 이동)

| req/s | C(1:1:3) | A(1:1:1) | B(6:3:1) |
|---|---|---|---|
| 8/8/14 | 100 | 100 | 100 |
| 13/14/26 | 89 | 100 | 100 |
| 18/22/40 | 20 | 45 | 45 |
| 24/30/55 | 6 | 10 | 7 |
| 31/38/70 | 2 | 3 | 2 |
| 40/47/85 | 0 | 1 | 0.6 |
| —/57/100 | — | 0 | 0.2 |

**SLO knee: C ≈ 13 < A ≈ 20 < B ≈ 38 req/s** — `exp14_fleet_attainment.png`에
세 곡선이 깔끔히 분리. (각 열의 세 rate는 C/A/B grid가 다르므로 나란히 배치용;
정확 knee는 각 곡선 참조.)

### 핵심 발견 (가설 3개 모두 확증)

1. **간섭(H1) 확증 — 클래스는 함께 무너진다.** 각 mix에서 chat/deepresearch/swe
   attainment이 모든 rate에서 거의 동일: C@13 = 88/91/89, A@22 = 43/50/44,
   B@40 = 46/44/41. **가벼운 chat(단독 knee 55 req/s)이 자기 특성이 아니라
   공유 KV·큐 상태 때문에 mix knee에서 같이 붕괴.** 메커니즘: 무거운 SWE가 채운
   큐 뒤에서 chat TTFT가 튐(C@18에서 chat TTFT 0.3s→5.5s = head-of-line).
   `exp14_per_class_mix{A,B,C}.png`. (미세하게 deep-research가 심과부하에서 몇 %p
   높음 — 출력이 짧아 더 빨리 빠짐.)

2. **질량 지배(H2) 확증 — knee는 요청 비율이 아니라 토큰 질량을 따른다.** SWE
   토큰 지분 C(92%) > A(82%) > B(65%)의 역순으로 knee C < A < B. 요청 비율로는
   B가 chat 60%라 "가벼워" 보이지만 실제 용량을 정하는 건 SWE 토큰 질량.

3. **붕괴 전염(H3) 확증 — throughput 붕괴 강도가 SWE 지분에 비례.** peak 후
   심과부하 output tok/s:
   - C: 5,758(@18) → 2,957(@40) = **−49%**
   - A: 7,709(@30) → 4,601(@57) = **−40%**
   - B: 12,625(@55) → 10,042(@100) = **−20%**

   SWE 단독(EXP-10)의 throughput 붕괴가 mix에서 **용량 가중으로 재현**된다
   (chat/deep-research 단독은 평탄이었음, EXP-12/13). B의 peak가 최고(12.6k)인
   것은 60% chat = decode 친화적이기 때문.

### 계측: per-request → 엔진 귀속 (신규, EXP-13 미해결분 해결)

전 조건에서 client request-id sidecar ↔ scheduler dispatch 로그 조인 성공
(매칭률 대부분 100%, 최저 92.3%). 이로써 **엔진별 SLO attainment**을 직접 계산
(`per_engine_attainment_mix*.png`). mix A knee(22 req/s)에서 4엔진
42.5/40.0/48.6/49.8% — spread ~10%p로 로드밸런싱 대칭 확인, straggler 없음.
구현: [DEV_request-engine-attribution.md](DEV_request-engine-attribution.md).

### 산출물

`results/aggregate_analysis/exp14_mix/`: `exp14_fleet_attainment.png`(정본 3-비율
곡선), `exp14_per_class_mix{A,B,C}.png`, `per_engine_attainment_mix{A,B,C}.png`,
`per_class_attainment_mix{A,B,C}.png`, `exp14_summary.csv`(전 조건 원표).
아카이브: `results/exp14_mixA_grid1_archive/`(초기 1–16 grid).

### 후속: 클래스별 차등 SLO (재실험 없이 재분석)

같은 run들을 **클래스마다 다른 SLO**로 재채점(`exp14_per_class_slo.py`,
`results/aggregate_analysis/exp14_mix_slo_differentiated/`):
chat TTFT≤5s&TBT≤50ms / deepresearch TTFT≤10s&TBT≤100ms / **swe E2E≤20s**.

- **핵심 반전**: 균일 SLO에선 세 클래스가 *함께* 붕괴(H1 간섭)했지만, 차등 SLO에선
  **클래스가 갈라진다** — 간섭이 latency를 함께 끌어올려도 *위반 여부*는 각 클래스의
  예산이 정한다. mix A@30 req/s: deepresearch 55% / chat 10% / swe 1%
  (`exp14_per_class_slo_mixA.png`). 느슨한 예산(deep-research)이 공유 열화를 더
  오래 흡수 → **"보호할 클래스를 SLO로 고를 수 있다".**
- 순서는 클래스 특성이 아니라 **SLO 예산 대비 healthy latency의 여유**가 결정:
  deep-research(TTFT 0.3s vs 예산 10s = 큰 여유) > chat > swe.
- **swe E2E≤20s 주의**: SWE healthy(저부하) E2E가 이미 12–19s라 20s는 ~1.3×로
  빡빡함 → SWE는 저rate에서도 100% 못 찍음(mixC@8 62%, mixA@8 78%). 100% 근처를
  원하면 30s(≈2×) 필요. 20s는 사용자 선택(빡센 배경-에이전트 예산 시나리오).
- 산출물: `exp14_fleet_attainment_slo.png`(3비율 fleet),
  `exp14_per_class_slo_mix{A,B,C}.png`, `per_engine_attainment_slo_mix{A,B,C}.png`,
  `exp14_summary_slo.csv`.

### 방법론 메모

- 초기 grid(1–16)는 prefix-cache 때문에 용량을 과소평가 → KV 기반 비율별 grid로
  재설계(위 설계 절). "input_tokens ≠ 실연산" — 캐시 감안 시 KV가 정직한 부하 신호.
- `--disable-timeouts`로 3클래스 무-kill 통일(SWE만 abort 걸리는 오염 제거).
