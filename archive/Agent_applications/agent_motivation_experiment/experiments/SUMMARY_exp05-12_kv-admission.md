# SUMMARY — EXP-05→12: KV admission & 과부하 붕괴 아크 (인사이트 · 방법 · 세팅)

**기간**: 2026-07-08 ~ 07-16 · **스택**: NXC13 8×B200, Llumnix full-mode
(gateway :8089 / scheduler :8088 / 4×TP2 vLLM). 상세는 각 EXP 문서.

## 표준 (2026-07-16 확정 — 이후 모든 실험·비교의 기준)

- **단일 모델: Llama-3.1-70B-Instruct / max-model-len 40960.** EXP-04/05는
  구 3-70B(컨텍스트 8k; SWE 30k 입력 수용 불가로 EXP-06에서 전환) 시절이라
  **legacy** — chat 정본은 EXP-12(5–120 req/s) + EXP-11 재절단(125–175).
  두 모델은 chat 거동이 크게 다름(knee 60→~55, raw 용량 ~100→~150 req/s).
- **표준 분석 창: 도착-앵커 [60s, 340s]** (warmup 60s 제외 + 본 300s − 끝
  20s). 긴 run은 `plot_slo_vs_throughput.py --steady-anchor arrival
  --steady-max-s 360`으로 재절단. 과부하엔 정상상태가 없어 창 길이가 다르면
  감쇠 궤적의 다른 지점을 평균하게 되기 때문(EXP-11의 60-포인트: 10분 창
  23.9% vs 표준 창 44.4%).
- EXP-10부터 **무-kill 스택**: gateway SSE 300s/헤더 15m 타임아웃 제거
  (custom binary, env 오버라이드) + 64Gi + client abort 전부 off — 무제어
  측정에서 요청을 죽이는 주체가 없도록.

## 스토리라인 (한 단락)

무제어 서빙의 congestion collapse를 정량화(EXP-04/05/06) → KV "수조" 가설
검증(ANALYSIS_kv-tank-flow) → 최단순 순간-점유율 θ admission이 정상상태에서
예상 밖 근사-최적(EXP-07/08) → 이유로 **TBT–KV 선형 법칙** 발견
(ANALYSIS_why-not-full-kv) → agent 체인 전이 + chain-kill 실측(EXP-09) →
폐루프 되먹임을 제거한 open-loop replay(EXP-10)에서 **throughput 자체의
붕괴**를 발견, 인프라 아티팩트 2건(gateway OOM, 300s timeout)을 제거한
대조실험으로 원인을 **큐-질량 ITL 법칙**으로 확정 → chat 심화 sweep(EXP-11)
으로 법칙의 워크로드-불변성 확인 + 붕괴 비대칭을 질량 축적 속도로 정량화 →
단일 모델·표준 창으로 전체 재정렬(EXP-12).

## 핵심 인사이트

1. **무제어 붕괴는 다단계다.** chat(3.1-70B): 50 req/s 100% → 60에서
   48.6%(TBT) → 70에서 11.2%(큐잉) — 그동안 tok/s는 19.2k로 멀쩡. SWE 체인:
   TBT 위반 → TTFT 큐잉 2단계(EXP-06). open-loop SWE(EXP-10)에선 3단계째로
   **throughput 자체가 붕괴**: peak 3,031 tok/s(λ=8) → 1,036(λ=20, 34%)
   [표준 창].
2. **영역 1 — TBT–KV 선형 법칙**: TBT ≈ 10 + 20.6 × hot-KV[Mtok], 워크로드
   불변(chat 840 tok/req와 SWE 5.5k이 batch 수 축에선 7.5× 달라도 KV 축에선
   한 곡선). decode step 시간은 매 step 읽는 KV 총량이 결정(bandwidth
   bound). → 50ms SLO = KV 예산 ~83%; **"KV를 다 쓰지 않는 게 최적"인 이유**.
3. **영역 2 — 큐-질량 ITL 법칙 (KV 포화 후)**: ITL ≈ base(KV) +
   **~3 ns/(대기 토큰·step)** × 대기열 토큰 질량(= 대기 요청 수 × 요청당
   프롬프트 토큰). SWE 실측 3.3ns(질량 13–99M, r=0.98), chat ~3.2ns(질량
   0.3–5M — 얕은 범위라 오더 확인 수준). 요청-개수 해석(78µs/req)은 chat
   반례로 기각 — 개수가 아니라 **질량**. 무-kill 대조실험으로 doomed-work
   가설 기각, preemption·prefix-hit 붕괴도 배제. 코드 레벨 메커니즘(스케줄링
   시도의 prefix-hash 재조회 추정; 블록당 50–80ns ÷ 16 tok과 정합)은 미확증
   — EXP-13 후보. 그림: `exp11_chat/itl_vs_queue_mass_law.png`.
4. **붕괴 비대칭은 산수다**: 질량 축적 속도 = (초과 수요) × (요청당 토큰).
   SWE 22.4k tok/req vs chat 0.82k → 같은 surplus에서 **27× 빨리** 붕괴역
   진입. chat이 붕괴 질량(수십 Mtok)에 도달하려면 ~300 req/s × 9만+ 동시
   연결이 필요해 사실상 불가. 항등식 tok/s = running/ITL에서 chat은 running
   성장(→2,800)이 ITL 상승을 흡수하고, SWE는 running이 KV로 400에 고정돼
   (요청당 고유 KV ~5.8k tok) ITL 상승이 그대로 throughput을 깎는다.
   **admission이 지키는 것은 latency만이 아니라 엔진 throughput 그 자체.**
5. **θ\* = 0.6 (meanTBT≤50ms 기준, chat·SWE 공통)** — 정적 순간-점유율
   임계가 정상상태 근사-최적(EXP-07/08/09), 재현 편차 ≤2%p. 남은 결함 4개:
   (a) 오프라인 sweep 산물(자동 동작점 탐색 없음), (b) **job-무지** —
   mid-chain 거절 1건 = 체인 사망 + 후속 콜 4.6× 억압, (c) **SLO-정의 의존**
   — p95 ITL≤50ms 기준이면 θ\*=0.3–0.4, (d) 300–700ms prefill-stall 꼬리는
   θ로 제거 불가.
6. **릴리즈 방식은 knee를 못 옮기고 초과분만 가른다** (EXP-10 vs EXP-06,
   측정 call 도착률 축): 양쪽 다 ~5 calls/s에서 67%로 꺾임. closed-loop은
   chain 되먹임이 도착을 자기억제해 attain ~22% 바닥을 만들고(수요 은폐),
   open-loop은 0%까지 관통 — **job-level 실험의 붕괴 지표는 실수요 대비
   과소표시**다.
7. **수조(tank) 관점**: 순유량 외삽 예보(~60s 리드), hot/idle 2층 구조,
   hit-침식 되먹임(87.6→74% → prefill 수요 ~2×). 인사이트 2와 결합하면
   setpoint = `(SLO−절편)/기울기`의 튜닝-무관 θ 예측 가설이 성립(미실증).
8. **방법론 교훈 — 인프라가 결과를 오염시킨다.** 이번 아크에서 실측으로
   적발·수정한 것들: gateway 4Gi OOM(무제어 측정을 crash로 오염), SSE 300s
   컴파일타임 상수(모든 장기 대기를 400으로 절단), 분석 창 길이 불일치(같은
   조건이 23.9% vs 44.4%), 모델 전환 미동기(용량 100 vs 150 오판), 부하기
   스레드 상한(도착률 클램프), poisson 모드 warmup no-op. **무제어 baseline은
   "죽이는 주체가 없는 스택 + 공통 창 + 단일 모델"에서만 성립한다.**

## 실험 방식 (공통 방법론)

| 항목 | 정의 |
|---|---|
| SLO | TTFT ≤ 5s (도착 기준, gateway 큐 포함) AND per-request meanTBT ≤ 50ms |
| 표준 창 | 도착-앵커 [60s, 340s]; 긴 run은 `--steady-max-s 360` 재절단 |
| 제외 범주 | error/timeout/run-end-cut(+grace_cut) 제외·별도 보고; rejected는 offered 뷰에서 위반 카운트; (구)gateway-timeout kill은 TTFT 위반으로 재분류 |
| job goodput | 제출창 [60,360)s job만 분류(우측 절단 방지) |
| 조건 독립성 | 조건마다 engine+control-plane cold restart (`--restart-per-condition`) |
| warmup | chat 20 req/s×60s(rate 모드), SWE job 0.25 jobs/s×60s. poisson 모드는 warmup **no-op**(EXP-10은 warmup 없음 — 창이 [60s,·)라 무영향, 문서화) |
| drain | `--post-duration-grace 60`: 종료 후 60s 대기 → 미완료분을 synthetic `grace_cut` 행으로 기록(도착 계수 보존) 후 스레드 방기 |

## 세팅 요약

| EXP | 워크로드 | 모델 | rate | θ | 조건 | 비고 |
|---|---|---|---|---|---|---|
| [04](EXP-04_8192conc-chat-sweep.md) | chat | 3-70B | 5–120 req/s | – | 8 | **legacy** |
| [05](EXP-05_warmup-chat-sweep.md) | chat | 3-70B | 5–100 req/s | – | 11 | **legacy** → EXP-12로 대체 |
| [06](EXP-06_swe-tool-delay-sweep.md) | SWE chain | 3.1-70B | 0.25–5 jobs/s | – | 11 | |
| [07](EXP-07_kv-threshold-admission.md) | chat | 3.1-70B | 50/60/90 req/s | 0.3–0.8+off | 12 | θ\*=0.6 발견 |
| [08](EXP-08_kv-threshold-full-sweep.md) | chat | 3.1-70B | 5–100 req/s | 0.6 | 11 | 용량 클램프 밀착 |
| [09](EXP-09_swe-kv-admission-sweep.md) | SWE chain | 3.1-70B | 0.25–5 jobs/s | 0.3–0.6 | 44 | chain-kill 4.6× |
| [10](EXP-10_swe-request-level-replay.md) | SWE **open-loop replay** | 3.1-70B | λ=1–20 req/s | off | 11 | 무-kill 스택; transcript 13.2k콜 |
| [11](EXP-11_chat-deep-overload.md) | chat | 3.1-70B | 60–175 req/s | off | 6 | 법칙 교차검증 (10분 run) |
| [12](EXP-12_chat-baseline-31.md) | chat | 3.1-70B | 5–120 req/s | off | 12 | **canonical chat baseline** |

공통: 4×TP2 util 0.9, chunked prefill 8192, prefix caching ON, in-cluster
runner. 부하기: chat 24 procs×2048 thr(EXP-11/12; 이전 8×1024), SWE replay
8×2048, `--disable-timeouts`(EXP-10) / sharegpt 무-abort 설계(chat).

## 구현 (장치들)

- **admission** — llumnix `feat/kv-admission-threshold`: scheduler 메트릭
  `kv_cache_usage_ratio` + `--admission-kv-usage-threshold θ` per-instance
  하드 필터(전 인스턴스 ≥θ → 429 → gateway 503), gateway fast-fail 옵션.
  CGO-프리 정적 scheduler 바이너리 hostPath 주입
  (`k8s/exp07/patch-scheduler-kvadm.sh`).
- **무-kill gateway** — SSE read/헤더 타임아웃 env 오버라이드(ea14d62) +
  `patches/sglang-go-compat/` shim(공개 sglang 서브모듈에 없는 vendor Go
  wrapper를 stock 이미지의 FFI 정적 라이브러리에 직접 바인딩해 재작성) +
  컨테이너 내 CGO 빌드(`builder-gateway.yaml`, glibc 2.35 일치) →
  `patch-gateway-timeout.sh`. 메모리 4→64Gi(`patch-gateway-memory.sh`;
  gateway 메모리는 in-flight 비례 — 대기 요청마다 연결+본문+LRS 상태).
- **부하기** — `--post-duration-grace`(bounded drain + synthetic 행 +
  중복 방지 kill-switch), open-loop replay
  (`codingagent_request_level_poisson` + `--record-transcript`, MP 샤딩
  `[k::N]`으로 중복 재생 방지), 24-proc 지원.
- **분석** — `--steady-anchor arrival` / `--steady-max-s`(표준 창),
  `--rate-key/--rate-div`(lambda\_ 디렉토리), `gw_timeout_mask` 재분류,
  `plot_exp10_overlay.py`(call-축 오버레이), law/ITL-CDF 그림들.

## 그림/스크립트 포인터

`results/aggregate_analysis/` — **`exp12_chat/exp12_unified_curve.png`**
(정본 chat 곡선 5–175), **`exp11_chat/itl_vs_queue_mass_law.png`**(두-영역
법칙, chat+SWE 한 축), `exp10_slo/`(window340/ 표준표,
exp10_vs_exp06_callrate, ITL CDF, oom_vs_fixed 비교), `exp10_plots/`
(조건별 엔진/latency 시계열), `exp0{7,8}/`(admission),
`why_not_full_kv/`(TBT–KV 법칙). 생성 스크립트는
`analysis_scripts/request_level/` 일괄.

## 열린 질문 / 다음 후보

- **EXP-13 후보**: ~3ns/tok·step의 코드 위치 확증 — 엔진 프로파일링(vLLM
  스케줄러/llumlet의 대기열 처리 경로).
- flow-기반 컨트롤러: 정상상태는 θ가 근사-최적이므로 무대는 burst/transient;
  인사이트 2+7 결합의 튜닝-무관 setpoint 계산 가설 실증.
- job-aware admission: 체인 첫 콜만 거절(인사이트 5(b)의 직접 해법).
- chat 붕괴역 실증은 async 부하기(9만+ 연결) 없이는 불가 — 필요 시 결정.
- vLLM 카운터 패치로 유출 분해(완료/preempt/evict), per-step 계측으로 두
  법칙 직접 검증.
