# EXP-10 — SWE open-loop request-level replay λ sweep (admission 없음)

**날짜**: 2026-07-15 · **상태**: done · **브랜치**: `feat/exp07-kv-admission`

## 왜 (motivation)

EXP-06/09의 SWE 워크로드는 **job-level release (closed-loop)**: 콜 n+1은 콜 n
완료 + tool-delay 후에야 발행되고, 거절/지연 1건이 체인의 미래 콜 발행 자체를
줄인다 (EXP-09 실측: mid-chain 거절 1건 = 체인 즉시 사망, 후속 콜 4.6× 억압).
즉 시스템이 받는 도착 스트림이 시스템 자신의 상태에 되먹임되는 구조라,
"무제어 붕괴 곡선"이 순수 수요 곡선이 아니다.

EXP-10은 같은 콜 내용(EXP-06/09와 동일한 SWE agent 프롬프트)을 **open-loop
request-level**로 릴리즈해 되먹임을 제거한다 — EXP-05(chat open-loop)의 SWE
버전. 목적:

1. chain 되먹임 없는 상태의 진짜 용량·붕괴 지점 (용량 추정: prefill 기준
   ~6 req/s, KV 체류 기준 ~13 req/s 사이)
2. TBT–KV 선형 법칙(ANALYSIS_why-not-full-kv)의 3번째 독립 검증점
   (chat open-loop, SWE closed-loop에 이어 SWE open-loop)
3. EXP-06/09 곡선과의 비교축 확보 — call 도착률(calls/s)로 환산해 오버레이

admission은 걸지 않는다 (θ=0, stock 의미론). θ sweep은 보류된 원안(EXP-09
문서 말미)에서 제외됐다.

## 예상

- attain 곡선은 chat(EXP-05)형 절벽을 보이되, 5.5k tok/req prefill 부하 때문에
  붕괴점은 훨씬 낮은 λ (6–13 req/s 사이 예상).
- TBT–KV 기울기 ~20.6 ms/Mtok이 유지되면 법칙의 워크로드·릴리즈방식 불변성 강화.

## 설계 (사용자 확정: 녹화 60분 / λ 11점 1–20 / 조건 8분)

### E1. Transcript 녹화

replay workload(`codingagent_request_level_poisson`)는 transcript JSONL을
literal replay하므로 녹화가 선행된다.

- `swe_bench_coding_tool_delay`, 0.5 jobs/s × 60분, θ=off, chain 4–12,
  warmup 0.25 jobs/s × 60s, cold restart 선행.
- **LOAD_PROCS=1**: 여러 proc이 같은 transcript 파일에 append하면 ~22KB
  JSONL 라인이 인터리브로 깨질 수 있어 단일 proc으로 녹화.
- 기대 ~10k+ 라인 (성공 콜만 기록). 산출:
  `results/exp10_transcript/transcript_swe_calls.jsonl` (~300MB, 커밋 금지).
- pool 크기 근거: replay pool은 소진 시 동일 바이트로 순환 → 순환 ≥2부터
  prefix-cache hit가 비현실적으로 올라 prefill 부하가 증발. 최고 λ 조건
  (20 req/s × 8분 ≈ 9.6k 요청)에서도 순환 ≤~1이 되게 8k+ 라인 확보.

### E2. λ sweep

- λ = 1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20 req/s (11조건) × 8분,
  조건별 cold restart, warmup 1 req/s(=60 rpm) × 60s.
- `--disable-timeouts`: client abort 전부 끔 (τ-timeout 포함) — 느린 요청도
  죽이지 않고 측정 (EXP-05 chat과 동일 철학, absolute-SLO 분석).
- MP 8 procs: 각 worker가 transcript를 `[shard::8]`로 나눠 갖고 독립 순환 →
  worker 간 중복 재생 없음, worker당 순환 배수는 전역과 동일.
- 실행: `k8s/exp07/run_exp10_replay.sh {record|smoke|sweep}` +
  `runner-exp10{,-record}.template.yaml`. 드라이버가 시작 전 scheduler
  admission off(θ=0)를 강제 확인.

### 분석

1. request SLO(TTFT≤5s & meanTBT≤50ms, steady [60s, dur−20s]) attain vs λ
   — exp05 파이프라인 (`slo_sliding_window.py` 등). rejected 범주는 0이어야 함.
2. λ별 ITL CDF — `plot_itl_cdf_rates.py` glob 교체.
3. 엔진 상태(KV 수위/running/prefill) + TBT–KV fit (`analyze_tbt_drivers.py`).
4. EXP-06/09 오버레이: jobs/s 축 직접 비교 불가 → 각 run의 **측정된 call
   도착률(calls/s)** 로 환산해 비교.
5. 조건별 replay_index 분포 로그 → prefix-hit 왜곡 사후 검증.

## 세팅 요약

| 항목 | 값 |
|---|---|
| 모델/서빙 | Llama-3.1-70B-Instruct, 4×TP2, max-model-len 40960, util 0.9 |
| admission | 없음 (θ=0; custom scheduler 바이너리 유지, stock 의미론) |
| gateway | stock (wait-scheduling-timeout 기본값, fast-fail 해제) |
| release | request-level open-loop Poisson (λ = req/s) |
| 측정 | 8분/조건, steady [60s, 460s], 조건별 cold restart |
| 클라 timeout | 전부 비활성 (`--disable-timeouts`) |

## 결과

**녹화(E1)**: 13,218라인 (1,743 jobs × 6 stages, 전 라인 파싱 OK, 고유
request_id 100%). 실측 입력 **평균 22.4k tok/req** (p50 21.6k, max 38.8k —
체인 누적 프롬프트), 출력 평균 566 tok. 8-proc 샤딩 기준 worker당 1,652라인
→ 최고 λ=20에서도 순환 <1 (replay_index 전부 r01 확인).

**스모크**: λ=1×2분, TTFT p50 0.34s / meanTBT p50 10.2ms. 시작 0.3초 내 2건이
`KV_THRESHOLD`로 기록 — **admission이 아니라 cold-restart 직후 엔진 등록 전
no-endpoint 503을 클라이언트가 같은 시그니처로 분류한 것** (θ=0 확인). steady
window 밖이라 분석 무영향. 모든 run 공통의 주의사항으로 남김.

### 최종 재실행 — 타임아웃 완전 제거 (λ=10/12/16/20, 정본 데이터)

"throughput을 제대로 보려면 timeout을 제거하라"는 결정에 따라 **gateway를
재빌드**해 SSE read timeout(300s 상수)과 response-header timeout(15m)을
24h로 올리고(`GATEWAY_SSE_READ_TIMEOUT` env, 커밋 ea14d62 + 빌드 shim
`patches/sglang-go-compat/`), 부하기 스레드 16k·클라 cap 4h로 올려 λ≥10을
재실행했다. **이제 요청을 죽이는 주체가 어디에도 없다** (400/600s kill 0건,
성공 요청 e2e 최대 353s+ 실측, gateway 재시작 0).

| λ | tok/s (300s-cap) | **tok/s (no-timeout)** | KVμ | runμ | waitμ | 엔진 ITLμ |
|---|---|---|---|---|---|---|
| 10 | 2,087 | **2,009** | 99% | 347 | 1,242 | 159ms |
| 12 | 1,659 | **1,699** | 99% | 344 | 1,821 | 201ms |
| 16 | 1,121 | **1,055** | 99% | 399 | 3,100 | 315ms |
| 20 | 530 | **800** | 99% | 400 | 4,236 | 390ms |

**대조실험 결론: kill을 제거해도 붕괴 곡선이 거의 그대로다** (λ=20만 530→800
소폭 회복). 즉 이전에 세운 "doomed-work(죽을 요청에 prefill 낭비)" 가설은
부차 요인이었다. 시간 분해가 진범을 지목한다: 완료 요청 기준 prefill은
1–2s로 미미, **decode 자체가 느려진다** (per-req decode 56→141s).

**두-영역(two-regime) ITL 법칙** (전 11λ 엔진 카운터 + EXP-05 교차검증으로
확정; 초기의 "큐 개수 × 78µs" 해석은 EXP-05 반례로 **수정됨**):

- **영역 1 — KV-바운드** (KV<~92%, 큐≈0): ITL ≈ 10+20.6·KV[Mtok]
  (기존 TBT–KV 법칙; λ=1–6 구간 20→104ms가 이걸 따름).
- **영역 2 — 큐-바운드** (KV 포화 후): ITL이 대기 큐의 **요청 수가 아니라
  토큰 질량**에 비례해 증가. exp10 내부 회귀는 78µs/대기요청(r=0.98)이지만
  EXP-05는 같은 큐 개수(2.7k)에서 3.9µs/요청 — 20× 차이가 정확히 요청당
  프롬프트 크기 비(22.4k vs 840 tok, 27×)로 설명된다: **토큰 단위로는 양쪽
  모두 ~3–5 ns/(대기 토큰·step)** (exp05 4.6, exp10 1.7–3.3). 블록당
  해시룩업 ~50–80ns ÷ 16 tok/블록과 정량 일치 — 매 step 스케줄링 시도가
  대기 프롬프트의 prefix-cache 블록 해시를 재조회하는 비용이 유력 후보
  (코드/프로파일링 확증은 후속, EXP-11 후보).
- 배제된 가설: doomed-work(무-timeout 대조실험으로 곡선 불변), preemption
  (422→139로 감소), prefix-hit 붕괴(75→66%로 완만), KV 총량(99% 고정인데
  ITL 159→390ms).

**EXP-05가 무너지지 않은 이유 (tok/s ≡ running/ITL 항등식으로 분해)**:

| | EXP-05 @100req/s (1.67×용량) | EXP-10 @λ=20 (4×용량) |
|---|---|---|
| running (KV pool ÷ 요청당 고유토큰) | 2,699 (≈2.34M/0.8k) | 400 (≈2.34M/5.8k) |
| 엔진 ITLμ | 123ms | 390ms |
| tok/s = run/ITL | **22,030 (평탄)** | **800 (붕괴)** |

chat은 과부하에서 running이 1.7k→2.7k로 **자라며** ITL 상승(+40ms)을 흡수
(work-conserving 포화 평탄역). SWE 장문은 (a) 요청당 고유 KV가 커서 running이
400에 **고정**되고, (b) 대기 토큰 질량(94M vs 2.3M, 40×)이 step당 오버헤드를
키워 ITL이 3배가 되니, 곱으로 tok/s가 무너진다. 추가로 EXP-05는 최대
1.67×용량까지만 밀었는데 그 영역에선 EXP-10도 -6%뿐(λ=8) — 붕괴는 ≥2×에서
시작되는 영역이고 chat은 거기까지 간 적이 없다.

**함의**: 무제어 과부하는 GPU 물리가 아니라 **대기 큐가 엔진 스텝 루프에
부과하는 토큰-질량 비례 오버헤드**로 throughput을 파괴한다. admission
control이 지키는 것은 latency SLO만이 아니라 엔진의 유효 처리량 그 자체 —
특히 장문(agent) 워크로드에서 큐를 짧게 유지하는 것이 throughput 보존 조건.

**릴리즈 조건 감사 (no-timeout 11조건 전수)**: steady 측정 도착률 = offered
정확히 일치(λ=20 → 20.00/s; 스레드 16k 상향 후 client cap 해소), inter-arrival
CV 0.94–1.02(=Poisson), replay 순환 0(전 조건 r01), task_id 중복 0, 입력
토큰 μ 20.2–22.3k(transcript 22.4k와 일치, 조건 간 균질), gateway pending 0
(도착이 즉시 엔진 waiting에 적재 — open-loop 끝단까지 보존). **발견된 결함
1건**: `--warmup-rpm`이 poisson 모드에선 no-op이라 첫 60초도 λ 그대로 진행
(rate 모드 전용 경로). 영향 없음 — steady window가 [60s,·)라 어차피 제외되고,
전 조건 동일 거동이라 비교성 유지, open-loop 순수성엔 오히려 부합. tau=2.0이
run_config에 남지만 `--disable-timeouts`로 불활성.

데이터 계보: `results/exp10_oom_archive/`(gateway 4Gi OOM 오염),
`results/exp10_gwtimeout_archive/`(300s-cap), 현행 `*_exp10_replay_lambda_*`
= no-timeout 정본 (λ≤8은 최초 sweep 그대로 — kill 미발생 구간이라 유효).
λ=20 ITL CDF 라인 부재는 steady window 도착분 중 스트리밍 완주가 0건이라
표본이 없는 것(그 자체가 붕괴 증거).

### 연산 구성비 — prefill이 forward 예산을 지배 (tokens_lambda_N.png)

표준 창에서 실연산 prefill(= counted × (1−prefix-hit)) vs decode [tok/s]:

| 조건 | counted prefill | hit% | **실연산 prefill** | decode | 비 |
|---|---|---|---|---|---|
| SWE λ=10 | 84.4k | 69% | 25.9k | 2.76k | **9.4:1** |
| SWE λ=20 | 28.8k | 66% | 9.7k | 1.37k | 7.1:1 |
| chat 100req/s (exp12) | 42.5k | 42% | 24.8k | 22.2k | 1.1:1 |

- **수준(level)의 이해**: SWE는 forward 토큰의 ~90%가 prefill(chat은 ~50%).
  흥미롭게도 실연산 prefill 양은 chat@100과 SWE@10이 거의 같다(~25k/s) —
  같은 prefill 부하에서 chat은 배치 2,700으로 22k decode를 뽑고 SWE는 배치
  ~370이라 2.8k에 그친다. 즉 낮은 decode 정점은 ①KV-바운드 배치(7×)가
  주요인이고 ②prefill 점유(step 시간 상승)가 가중 요인.
- **추세(trend)의 반증**: λ=10→20에서 실연산 prefill이 25.9k→9.7k로
  **줄어드는데** decode도 2.76k→1.37k로 같이 준다 — "prefill이 예산을 먹어서
  고λ에서 더 떨어진다"는 방향이 맞지 않고, 남는 설명이 큐-질량 항.
- **그림 판독 주의**: `tokens_lambda_N.png` 오른쪽 절반(제출 종료 후 drain)의
  "prefill만 200k+/s, decode 0" 구간은 **run-종료 아티팩트**다 — 클라이언트가
  종료 이벤트로 끊기면서 "스케줄→prefill→첫 토큰→클라 단절→abort→다음"의
  prefill-abort 공회전이 백로그를 소진할 때까지 돈다(표준 창 밖; grace 도입
  이전 run들이라 길게 남음). 부하 구간의 실제 구성비는 위 표가 정본.

### 인프라 이슈 2건과 조치 (경과 기록; 위 최종 재실행의 전사)

1. **gateway OOMKilled crashloop** — 최초 sweep의 λ≥10 조건에서 gateway가
   4Gi limit에 OOM(exit 137, λ=20 중 4회 재시작). gateway 메모리는 in-flight
   요청 수에 비례(엔진 waiting queue의 요청마다 연결+~90KB 본문+LRS 상태
   유지, gateway 자체 pending 게이지는 0)하는데 이 워크로드의 큐가 수천 개라
   4Gi를 초과. **연구 대상이 아닌 인프라 artifact로 판정** →
   `patch-gateway-memory.sh`로 limit 4→64Gi 상향 후 **λ=10/12/16/20 재실행**
   (오염 run은 `results/exp10_oom_archive/`). λ≤8은 OOM 미발생으로 유효.
2. **gateway 300s SSE-read-timeout** — OOM 제거 후 드러난 스택 속성. 첫
   토큰이 300초(forwarder `ReadTimeout`, 컴파일타임 상수) 내에 안 오면
   gateway가 abort+400, 클라 non-stream fallback까지 합쳐 ~600s에 오류로
   표면화. **스택 속성으로 수용하기로 결정** (exp05/06도 같은 상한 아래였고
   이 워크로드가 처음 발현시킨 것). 분석에서 해당 시그니처(400+latency≥295s)
   를 error 제외가 아닌 **TTFT 위반**으로 재분류 (`gw_timeout_mask`,
   `viol_gw_timeout` 컬럼) — 300초를 기다린 요청은 offered 관점에서 위반.

### 표준 창 [60s,340s] 재절단 (2026-07-16 표준화 이후의 정본 표)

> 실험 간 비교 표준 창(`--steady-max-s 360`, exp05 관례)으로 재절단한 수치.
> 8분 전체 창 대비 고λ tok/s가 높게 나오는 것은 과부하 감쇠가 진행 중이라
> 관찰이 짧을수록 궤적의 이른(덜 감쇠된) 구간을 평균하기 때문 — 어느 쪽도
> 인위가 아니며, **비교에는 이 표준 창을 쓴다**. 출력물:
> `results/aggregate_analysis/exp10_slo/window340/`.

| λ (req/s) | attain | tok/s | KVμ | runμ | waitμ |
|---|---|---|---|---|---|
| 1–4 | 100% | 615–2,255 | 6–35% | ≤105 | ~0 |
| 5 | 79.6% | 2,645 | 68% | 220 | 3 |
| 6 | 16.7% | 2,847 | 90% | 310 | 34 |
| 8 | 1.5% | **3,031 (peak)** | 99% | 354 | 358 |
| 10 | 0% | 2,471 | 99% | 369 | 838 |
| 12 | 0% | 2,136 | 99% | 359 | 1,253 |
| 16 | 0% | 1,292 | 99% | 412 | 2,238 |
| 20 | 0% | 1,036 (peak의 34%) | 99% | 421 | 3,083 |

### λ sweep — 전체 8분 창 (도착 앵커 [60s, 마지막 도착−20s]; 세부 서사용)

> λ≥10은 제출 종료 후 gateway-timeout abort가 큐를 비우는 **drain 꼬리
> ~10분**이 붙는다. 완료-앵커 window로 집계하면 이 준-유휴 구간이 평균을
> 희석하므로(초기 보고에서 tok/s 269, KV 43% 같은 왜곡 발생) 요약은 전부
> 도착-앵커(`--steady-anchor arrival`)로 계산한다. attain은 원래 도착 기준
> 분류라 영향 없음.

| λ (req/s) | steady attain | tok/s | KVμ | runμ | waitμ | 비고 |
|---|---|---|---|---|---|---|
| 1–4 | **100%** | 628–2,247 | 6–38% | ≤108 | ~0 | 위반은 warmup/drain 구간뿐 |
| 5 | **66.9%** | 2,627 | 73% | 232 | 3 | TBT 위반 개시 (598/707이 tbt-only) |
| 6 | 14.4% | **2,863 (peak)** | 92% | 310 | 81 | ITL p50=49.6ms — 50ms 교차점 |
| 8 | 1.5% | 2,701 | 99% | 341 | 559 | TTFT 큐잉 합류 (both 1,508) |
| 10 | 0% | 2,087 | 99% | 343 | 1,198 | gw_timeout 사망 개시 (1,228건) |
| 12 | 0% | 1,659 | 99% | 357 | 1,858 | 도착의 73%가 gw_timeout 사망 |
| 16 | 0% | 1,121 | 98% | 381 | 3,073 | 85% 사망 |
| 20 | 0% | 530 | 71% | 277 | 5,598 | 90% 사망; 스케줄→즉사 회전 극단화 |

- **붕괴 3단계**: (1) λ=5–6 TBT 위반(KV 수위), (2) λ=8+ TTFT 큐잉,
  (3) λ≥10 대기 300s 초과분이 gateway timeout으로 사망하며 attain 0%.
- **토큰 처리량 collapse의 메커니즘 — 엔진이 놀아서가 아니라 "죽을 일"을
  해서다.** 부하 중 KV 98–99%, running 340–380으로 엔진은 가득 차 있는데
  tok/s는 2,863→530으로 내려간다: (a) FCFS가 가장 오래 기다린(=300s 사망에
  가장 근접한) 요청부터 스케줄 → 22k-tok prefill 도중/직후 gateway abort →
  용량 증발 (엔진 로그에 RUNNING 상태 abort 다수 실측); (b) prefill 홍수가
  chunked-prefill step 예산을 잠식해 decode 기아 (엔진 ITL 400–800ms);
  (c) prefix-cache hit 하락. 시간분해로 보면 λ=12 엔진은 초반 60–120s에
  4,789 tok/s를 내다가 큐가 늙으며 매분 단조 감소 — 8분 평균 1,659는
  아직 감쇠 중인 값으로 **상한**이다.
- **drain 꼬리**: 제출이 끝나면 새 도착이 없으니 waiting 전체가 순차적으로
  300s를 넘겨 사망 → 엔진 running≈0 상태로 ~10분간 abort만 처리
  (`engine_lambda_12.png`의 490s 이후 구간).
- **TBT–KV 법칙 3번째 검증점** (run-레벨, pool 2.34Mtok): λ=4: 예측 28.0 vs
  실측 29.8ms · λ=5: 45.4 vs 44.4 · λ=6: 53.9 vs 49.6 — open-loop SWE에서도
  기울기 유지.
- 주의: λ=20의 측정 도착률은 13.5 calls/s로 offered보다 낮음 — 체류시간
  ~600s × 8,192 부하기 스레드 상한(8192/600≈13.7)에 걸린 client-side cap.
  λ≤16은 미해당(측정=offered). attain 결론엔 무영향.

### EXP-06 오버레이 (측정 call 도착률 축) — 핵심 발견

`exp10_vs_exp06_callrate.png`:

1. **knee는 릴리즈 방식과 무관하게 동일**: attain 67%가 되는 지점이 양쪽 모두
   ~5 calls/s (exp10 λ=5→66.9%, exp06 1 jobs/s=5.34 calls/s→66.7%).
   용량은 용량 — 도착 과정이 바꾸지 못한다.
2. **초과 수요에서만 갈라진다**: closed-loop(EXP-06)은 chain 되먹임이
   도착률을 자기억제 (5 jobs/s 제안에도 실측 11.5 calls/s에서 포화, attain
   바닥 ~22%). open-loop은 제안 수요가 그대로 도착해 attain 0%로 떨어지고
   토큰 처리량도 peak의 10%로 붕괴. **즉 EXP-06의 "부드러운 바닥"은 시스템이
   견딘 게 아니라 폐루프가 수요를 숨긴 것** — job-level 실험의 rejection/붕괴
   지표는 실수요 대비 과소표시라는 EXP-09 §chain-kill 관찰의 sweep 전체 버전.

### 산출물

`results/aggregate_analysis/exp10_slo/`: slo_summary.csv, slo_sliding_grid.png,
slo_vs_throughput_{steady,full}.png, kv/inflight_vs_throughput.png,
exp10_itl_cdf_rates.png + exp10_itl_percentiles.csv,
exp10_vs_exp06_callrate.{png,csv}. 생성:
`plot_slo_vs_throughput.py / slo_sliding_window.py / plot_itl_cdf_rates.py`
(공통 `--rate-key lambda_ --rate-div 1` 옵션 추가), `plot_exp10_overlay.py`.
transcript: `results/exp10_transcript/transcript_swe_calls.jsonl` (1.5GB,
커밋 금지 — 재현 시 E1 재녹화).
