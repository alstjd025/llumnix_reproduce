# EXP-13 — Search Arena deep-research 워크로드 무제어 rate sweep

**날짜**: 2026-07-20 · **상태**: running · **브랜치**: `feat/exp07-kv-admission`

## 왜

워크로드 mix 실험을 위한 **중간 길이(deep-research) 워크로드**의 단독
baseline. 아크의 기존 두 워크로드는 입력 길이의 양 극단이다 — chat
(sharegpt, 입력 mean 674 tok)과 SWE (codingagent, 입력 mean 21,789 tok).
그 사이를 **체인 구조 없이** 채우는 단발 워크로드로, mix에서 입력-길이 축을
체인-되먹임 축(SWE가 가진)과 분리해 관찰할 수 있게 한다.

새 워크로드 `searcharena_request_level_poisson`은 `lmarena-ai/search-arena-24k`
(영어-only, CC-BY-4.0)에서 deep-research **synthesis 요청**을 재구성한다:
고정 deep-research 분석가 시스템 프롬프트(~910 tok) + 검색-증강 "리서치 노트"
K개 + 실제 사용자 질문. 데이터셋 구조·재구성 근거·공개 방어 논리는
`workloads/searcharena_request_level_poisson/AGENTS.md`가 정본.

## 설계

- 워크로드: `searcharena_request_level_poisson` (기본 config: 영어-only,
  K∈[2,12] 로그균등, num_requests 60000). 입력 실측(구현체, n=400):
  **TOTAL mean 4,055 / p50 3,461 / p95 7,914 tok** — 이 중 ~910 tok은 고정
  시스템 프롬프트(엔진 prefix-cache 공유 히트), **유니크 notes+question
  tail이 mean ~3,145 tok로 실질 신규 prefill**.
- 스택: 3.1-70B 4×TP2, θ=0, no-timeout gateway(gateway-exp10, 24h) + 64Gi —
  exp10/11/12와 동일. 부하기 24 procs × 2048 threads.
- 프로토콜 = exp12 표준: 조건별 cold restart, warmup 2 req/s × 60s + 본
  **5분** + `--post-duration-grace 60`. 분석 창 = 도착-앵커 **[60s, 340s]**.
- grid (12조건, rpm): **120,180,240,300,360,480,600,780,960,1200,1800,2400**
  = **2,3,4,5,6,8,10,13,16,20,30,40 req/s**.
- 실행: `k8s/exp07/run_exp13_searcharena.sh sweep <rates>`
  (runner-exp13.template.yaml, DURMIN=5). 예상 소요 ~2.1시간.

## 용량 예측

- 이 워크로드는 **요청마다 노트 조합이 유니크** → 유니크 tail(~3.1k tok)이
  요청당 실질 prefill. (시스템 910 tok은 캐시되어 연산부담 아님 — smoke에서
  prefix-cache 히트 23.5% ≈ 910/4055 실측 확인.)
- 실측된 실연산 prefill 포화 ~25k tok/s (chat@100 ≈ SWE@10 공통) 기준,
  **raw knee ≈ 25k / 3.1k ≈ 8 req/s**, SLO knee는 그 아래 4–6 예상.
- 그래서 grid 하위 5점(2–6)이 안정, 8이 knee 근처, 상위(10–40)가 최대 5×
  과부하로 exp10급 붕괴 심도를 커버.

## 가설 / 관찰 포인트 (시작 전 기록)

1. **throughput 붕괴 여부가 핵심 질문.** 큐-질량 ITL 법칙(~3ns/tok·step)상
   질량 축적속도 = surplus × 요청당 토큰. deep-research(~3.5k/req 축적분)는
   SWE(22.4k)의 ~1/6, chat(0.82k)의 ~4배. **예측: 5분 창에서 완만한 하락
   시작만 보이고 SWE급 완전붕괴(peak→1/3)는 미도달** — 중간 워크로드가 중간
   거동을 보이면 큐-질량 법칙의 3번째 독립 검증(SWE 붕괴 / chat 평탄 사이).
2. **throughput은 total(prefill+decode) tok/s로 봐야 함.** 출력이 짧아
   (smoke 출력 mean 289, decode ~55 tps) output-only tok/s는 작게 보이지만
   prefill이 지배적이다. knee에서 total ~25k, output ~1–2k 예상.
3. **two-regime ITL**: KV<92%에선 TBT–KV 법칙 재현, 포화 후 큐-질량 기울기.
   prefix-cache 히트가 KV 수위/tok당 부하에 주는 영향도 확인.
4. cold-restart 직후 0.1초 내 KV_THRESHOLD reject 소수(θ=0인데도 뜨는
   일시 fail-closed, exp12에도 존재)는 warmup 창이라 분석 무관.

## Smoke 검증 (2026-07-20, run 260720_0051, 2 req/s × 2분)

358/360 성공. 입력 mean 4,101(예측 4,055 일치), **출력 mean 289 / p90 470**
(80-tok 프롬프트 시절 181에서 상승), **prefix-cache 히트 23.5%**(설계값
910/4055=22.4% 일치 → 시스템 블록이 캐시 서빙됨을 실증), TTFT 1s, KV peak
5%. server_metrics(엔진4+scheduler+gateway+migration log) 정상 수집.

## 결과 (2026-07-20 완료; 표준 창 [60,340], 12조건)

SLO 규칙 = TTFT≤5s(도착-앵커) AND meanTBT≤50ms; 에러/타임아웃/run-end-cut 제외.

| req/s | SLO attain | out tok/s | TTFTμ | e2e p95 | KVμ | runΣ | queΣ | 종료컷% |
|---|---|---|---|---|---|---|---|---|
| 2–10 | **100%** | 615→2,892 | <1s | 12–28s | 2→23% | 13→135 | ~4 | 0.3–8% |
| **13** | **86.9%** ← knee | 3,498 | 1.1s | 52s | 54% | 326 | 5 | 8.2% |
| 16 | **0.9%** ← 절벽 | 3,533 | 13.0s | 90s | 90% | 549 | 202 | 22% |
| 20 | 1.0% | 3,577 | 34.2s | 117s | 91% | 561 | 695 | 31% |
| 30 | 0.0% | 3,580 | 64.9s | 168s | 91% | 563 | 2,017 | 32% |
| 40 | 0.0% | 3,492 | 78.5s | 199s | 92% | 564 | 3,356 | 32% |

### 핵심 발견

1. **SLO knee ≈ 13 req/s, 그 위는 절벽** (86.9% → 0.9% between 13→16). KV가
   13에서 54%→16에서 90%로 급등하며 대기열이 0→202로 터지는 지점과 일치.
   예측(SLO knee 4–6)보다 훨씬 높았음 — prefix-cache로 요청당 실연산 prefill이
   유니크 ~3.1k뿐이라 raw 용량이 컸다(smoke·전 조건에서 prefix-hit ~22% 확인).

2. **가설 검증: throughput은 붕괴하지 않고 포화-평탄** (EXP-13의 핵심 질문).
   output tok/s는 16 req/s부터 ~3.5k에서 평탄(40까지 −1%), prefill은 fleet Σ
   ~60–70k tok/s로 평탄(engine_rpm_960.png 패널 D). **SWE(EXP-10)의 붕괴
   (peak 3,031→1,036, −66%)와 질적으로 다르고 chat(EXP-12)의 평탄에 가깝다.**
   → 중간 워크로드가 붕괴 축에서 chat-side 거동을 보임. 큐-질량 ITL 법칙의 3번째
   데이터포인트: 질량 축적속도(surplus×요청당 토큰)가 SWE의 ~1/6이라 5분 창에서
   throughput 파괴에 도달하지 못함(가설대로).

3. **전형적 큐-바운드 과부하** (engine_rpm_960.png): running은 KV-cap에서
   ~150/engine 고정, waiting은 0→130 선형 증가, 큐 대기시간 0→27초 선형 램프,
   KV 100% peg. throughput 안정한데 goodput만 붕괴 = 본 프로젝트 모티브의 교과서적
   실증.

4. **중간 워크로드 확인(decode 축)**: output tok/s 포화값 ~3.5k = chat 19k와
   SWE 2.5k **사이**(SWE 쪽). 반면 total(prefill+decode)은 ~60k로 chat 19k보다
   높음 — deep-research가 prefill-heavy(입력:출력 ≈ 13:1)라 토큰 처리량은 크지만
   decode 여력이 작다. 입력 4.1k가 SWE 영역에 가까운 것과 일관.

### 산출물

`results/aggregate_analysis/exp13_searcharena/`:
- `slo_vs_throughput_steady.png` (표준 창 SLO attain + output tok/s — 정본 곡선),
  `slo_vs_throughput_full.png`, `kv_vs_throughput.png`, `inflight_vs_throughput.png`
- `persplit/engine_rpm_*.png` (엔진별 running/waiting/KV/prefill·decode/hit/queue),
  `tokens_rpm_*.png`, `llumnix_rpm_*.png` (제어-평면)
- `summary/lambda_summary.csv` (12조건 원표), `summary/*.png`

### 예측 대비

- knee 위치: **빗나감**(예측 4–6 → 실측 13) — prefix-cache 효과 과소평가.
- 붕괴 여부: **적중**(예측 "SWE급 붕괴 미도달, chat-side" → 실측 포화-평탄).
- decode 중간값: **적중**(chat–SWE 사이).
