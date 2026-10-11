# FluidServe 구현 — 논문 구성이 실제로 돌았던 그대로

작성 2026-10-11. 이 문서 하나로 제3자가 **논문(NSDI '27 Fall #515)의 수치를 만든 스케줄러가 요청 하나에 대해
무엇을 계산했는지**를 코드 수준에서 이해할 수 있도록 쓴다. 옛 문서(`archive/`)의 수치는 날짜가 지났을 수 있으므로
**코드를 기준으로 삼았다.** `파일:행`은 이 문서를 쓰면서 실제로 열어 확인한 것만 적었고, 확인하지 못한 것은 ⚠로
표시했다. 행 번호는 현재 브랜치(`revision/reorg`, HEAD `4c3798f`)의 파일 기준이다.

---

## 0. 기준: 어느 바이너리, 어느 소스, 어느 설정인가

| 항목 | 값 |
|---|---|
| 논문 수치를 만든 바이너리 | `bin/scheduler-exp07` md5 `6dc9f035` (= `/home/nxclab/tools/staging/scheduler-exp107t`, 2026-08-28 15:03 빌드). EXP-108(정적 도착률 sweep)과 EXP-109(한 시간 동적 trace)가 이것으로 돌았다 |
| 그 바이너리의 소스 | ⚠ 커밋과 정확히 일치하는지는 확인 불가. 빌드 시각이 커밋 `07baa1d`(2026-08-28 15:24, swe에 명시적 토큰당 예산을 주는 문법 추가) 직전이므로 그 커밋 내용으로 추정한다 |
| 제출 시점 배포 바이너리 | md5 `9e8f68b6`(= `tools/staging/scheduler-usec`). 결정 시간 마이크로초 계측(EXP-140)이 추가된 판. 논문의 오버헤드 수치(192 µs)만 이것으로 쟀다 |
| 논문 구성의 arm 이름 | `fsv3capgnofrct75` — `Agent_applications/agent_motivation_experiment/k8s/exp07/run_exp108_t75.sh:201-206`과 `run_exp109_hour.sh:442-446`에 같은 정의 |
| 2026-10-10에 다시 읽은 기동 줄 (`6dc9f035`) | `horizon 100 steps, z=1.65, ttft margin 300ms, pend=true, shed=true, affinity=true, affweight=1.00, affmetric=count, percorr=true, pacecap=true, perdelay=false, deadlinedelay=false, shednoft=false, interleave=true, flux=true, classharm=false, deadlinefeasible=false, forcemargin=false, ownbudgetgate=false, kvslope=false, gateslack=1.000, shedsignal=coupled, oraclelen=false, prefix=true, prefixcalib=true, prefixblock=16, prefixcap=500000, classpin=off, budgets "25:decode:75,50:decode,100:decode", instancecap=true, capmult=3.0, enableforce=false` |

**현재 소스와 논문 바이너리의 관계.** `git diff 07baa1d HEAD -- pkg/scheduler/policy/fluidserve*.go`에서 주석과
계측(카운터·로그 줄·히스토그램)을 뺀 코드 변경을 전부 읽었다. 2026-09-08 이후 추가된 결정 로직(용량 비용을 보는
상한 예외, 메모리 판정의 수준 검사, 안전계수 설정, prefill 대기열 비용 대체식, prefix 전부 적중 시 대기열 항 유지,
도착 항, 청구 창 분리, 상주 요청 보호 조건 끄기, 길이 힌트의 투영 반영)은 **모두 플래그 뒤에 있고 기본값이 이전
동작을 그대로 재현한다.** 프로파일 세 파일(`deploy/profiling/llama31-70b-b200-tp2/`)은 `07baa1d` 이후 바뀌지 않았다.
따라서 **현재 소스를 같은 환경변수로 배포하면 논문 바이너리와 같은 결정 경로가 된다**(판단 근거는 diff 정독이며
동일 입력에 대한 실행 대조는 하지 않았다 ⚠). 2026-10-10 재구축 후 `6dc9f035`로 다시 잰 값이 논문 값과 반복 간 편차 안에서 일치했다(수치는 `evidence.md` §8).

---

## 1. 한 문단 요약

FluidServe는 Llumnix 스케줄러 안에서 돌아가는 routing·admission 정책이다. 게이트웨이가 요청을 보낼 때마다
(보류된 요청은 500 ms마다 다시 보낸다) 스케줄러는 네 인스턴스 각각에 대해, 엔진이 500 ms마다 보고하는 상태(KV
점유량, decode 요청 수, 대기 prefill 토큰 수, step 카운터)와 스케줄러가 스스로 기록한 상주 요청 장부(어느 요청이
어느 인스턴스에서 몇 토큰째인지)를 합쳐 **앞으로 100 iteration 동안의 KV 점유량과 평균 iteration 시간**을
투영한다. 그 투영에 이 요청을 더했을 때 (1) 평균 iteration 시간이 이 요청과 상주 요청들의 토큰당 예산 안에
있고 (2) KV가 물리 용량과 속도 상한 안에 있으면 그 인스턴스를 **수용 가능(feasible)**으로 본다. 수용 가능한
인스턴스가 있으면 같은 클래스 요청을 가장 많이 가진 곳으로 보낸다. 없으면 TTFT 예산 안에서 첫 토큰을 낼 시간이
남았는지를 계산해 남았으면 게이트웨이에 **보류(pend)**시키고, 남지 않았으면 **거절**한다. 이와 별도로 클래스마다
최근 도착률에서 필요한 인스턴스 수를 계산해, 한 클래스가 그 수보다 많은 인스턴스의 속도 상한을 정하고 있고 다른
클래스가 모자랄 때에는 그 클래스의 후보 인스턴스를 줄인다. 입력의 출처는 요청의 프롬프트 길이·토큰 id·TTFT·토큰당
예산, 엔진 상태, 오프라인 프로파일 세 파일, 그리고 실행 중에 갱신되는 보정값 몇 개다.

---

## 2. 구성 요소 지도

```
클라이언트 ──HTTP──> 게이트웨이(:8089) ──SchedulingRequest──> 스케줄러(:8088, 별도 파드, Go)
                         │   ↑ 503(보류: 재시도 / 거절: 즉시 반환)          │
                         │   └─────────── 목적지 인스턴스 ──────────────────┘
                         └──토큰 스트림──> vLLM 인스턴스 4대 (수정 없음, migration 끔)
    인스턴스 상태: 엔진(llumlet) → Redis CMS → 스케줄러가 500 ms마다 pull
```

- 스케줄러는 데이터 경로에 없다. 결정은 클러스터 뷰 lock 하나 아래에서 직렬로 처리된다
  (`--allow-concurrent-scheduling` 기본 false, `pkg/consts/consts.go:215`, `pkg/scheduler/policy/scheduling_policy.go:246-251`).
- 상태 pull 주기 500 ms는 매니페스트 값이다(`deploy/neutral/full-mode-scheduling/load-balance/scheduler.yaml:64-65`).
  컴파일 기본값은 50 ms다(`pkg/consts/consts.go:183`). 같은 매니페스트가 스케줄러를 `-v 4`로 띄운다(`:48-49`).
- 거절과 보류는 같은 503이지만 본문으로 구분된다. 게이트웨이는 "admission rejected"면 즉시 클라이언트에 돌려주고,
  "no available endpoint"면 재시도 간격마다 다시 묻는다(`pkg/gateway/load-balancer/scheduler_client.go:164-165, 205-215`,
  `pkg/scheduler/policy/scheduling_policy.go:295-306`).
- 정책 등록: `pkg/scheduler/policy/scheduling_policy_registry.go:30-31` → `newFluidserveDispatchFullMode`
  (`fluidserve.go:3172-3338`). 필터는 장애 도메인·스케줄 가능 여부·상태 staleness 검사뿐이고(부하 기준으로 인스턴스를 빼는 필터는 없다) 결정
  전체가 selector 하나에 있다(`fluidserve.go:3278-3299`).

### 2.1 파일과 행 수 (`pkg/scheduler/policy/`, `wc -l`)

| 파일 | 담당 | 현재 | `07baa1d`(논문 바이너리 추정 소스) |
|---|---|---|---|
| `fluidserve.go` | 설정 구조체, 엔진 관측, 인스턴스 상태 구성, 후보 평가, 정렬, 결정 사다리, commit, 기동 | 3,466 | 3,056 |
| `fluidserve_registry.go` | 상주 요청 장부, 진행 토큰 수 복원, 잔여 허용치, 클래스 예산, 첫 토큰 지연 통계 | 934 | 930 |
| `fluidserve_capacity.go` | iteration 시간 모델, 온라인 보정, KV 상한 역산, 클래스별 서비스율 | 602 | 550 |
| `fluidserve_instancecap.go` | 클래스별 인스턴스 상한 | 457 | 412 |
| `fluidserve_profile.go` | 출력 길이 분포(생존함수), 조건부 잔여 길이 | 255 | 255 |
| `fluidserve_prefix.go` | 스케줄러 자체 prefix 색인 | 211 | 211 |
| **비테스트 합계** | | **5,925** | **5,414** |
| 주석·빈 줄 제외 합계 | | 2,942 | 2,735 |
| `fluidserve*_test.go` 합계 | | 3,354 | 2,846 |

**논문의 "5.6K line Go scheduler"**는 위 여섯 비테스트 파일의 **주석·빈 줄을 포함한** 합계를 2026-09-09 커밋
`b6c36eb` 시점에 센 값 5,614와 일치한다(`archive/llumnix_reproduce/ms_dev/notes/fluidserve-implementation-facts.md`
"코드 규모" 표; 같은 커밋을 다시 세어 5,614 확인). 논문 수치를 만든 소스로 세면 5,414, 현재 5,925다. 주석이 많은
코드라 실행 코드만 세면 약 2.7~2.9K이다. 이 수에는 플래그 정의(`cmd/config/config.go`), 배포 스크립트, 게이트웨이의
거절 구분 코드가 들어 있지 않다.

---

## 3. 입력

### 3.1 요청에서 읽는 필드 (`pkg/types/scheduling_request.go:40-73`)

| 필드 | 쓰는 곳 |
|---|---|
| `Id` | 장부 키, 첫 관측 시각, 재시도 간격 측정(`fluidserve_registry.go:413-451`) |
| `PromptNumTokens` | KV 청구, prefill 비용, 클래스별 평균 프롬프트 |
| `PromptTokenIds` | prefix 색인의 16토큰 블록 해시(`fluidserve.go:592-596`, `fluidserve_prefix.go:94-113`) |
| `TtftSloMs` | 보류 가능 여부(첫 토큰 마감) |
| `TpotSloMs` | **tier 키**: 클래스 식별자 겸 예산 조회 키. 이 문서에서 "tier"는 요청이 실어 오는 이 정수값을 말한다 |
| `PredictedOutputTokens` | 길이 힌트. 논문 구성에서는 읽지 않는다(`oraclelen=false`) |

SLO는 OpenAI API의 `priority` 정수 하나에 `ttft_ms×1000 + tpot_ms`로 넣어 보내고 게이트웨이가 푼다
(`scheduling_request.go:94-113`). 워크로드 파일의 swe는 `tbt_ms: 25`(tier 키)를 보내고 실제 예산 75 ms는 스케줄러의
`--fluidserve-class-budgets` 셋째 필드에서 온다(`fluidserve_registry.go:98-115`). 논문 워크로드
(`workload_configs/mix_short_m1_t75.json`, `mix_dyn60_shift_m2Am1B_b1045_t75.json`)의 TTFT는 chat 5,000 / swe 7,000 /
deepresearch 10,000 ms다. **요청에 별도의 "workload class" 필드는 없다**(7절 참조).

### 3.2 엔진 상태 (인스턴스별, 500 ms마다 갱신)

| 양 | 필드 | 쓰는 곳 |
|---|---|---|
| decode 배치의 KV(논리 토큰)와 요청 수 | 대기·실행 중 decode 토큰/요청 수, loading 요청, 스케줄러가 보냈지만 아직 보고되지 않은 decode 요청의 합(`fluidserve.go:751-765`) | iteration 시간 모델, 투영 |
| 대기 prefill 토큰 | 대기 + 실행 중 prefill의 미계산 토큰 + 보냈지만 미보고 prefill(`fluidserve.go:1154-1157`) | prefill 간섭, 첫 토큰 추정 |
| 물리 KV 사용량과 총량 | `NumUsedGpuTokens`, `NumTotalGpuTokens`(`:1140-1141`) | 물리 용량 |
| step 카운터와 타임스탬프 | `StepId`, `TimestampMs` | 실측 iteration 시간, 진행 토큰 수 복원 |
| 실행 중 decode 요청 수 | `SchedulerRunningToDecodeRequestsNum`(`:1159`) | 장부 정리(완료 추론) |
| chunk 크기 | `Metadata.MaxNumBatchedTokens`(없으면 8,192; `:1143-1147`), metadata는 10초마다 pull | prefill step 수 |

엔진은 요청별 진행을 보고하지 않는다. 진행 토큰 수는 3.4의 장부가 step 카운터로 복원한다.

### 3.3 프로파일 세 파일 (`deploy/profiling/llama31-70b-b200-tp2/`, ConfigMap으로 `/profiling`에 마운트)

| 파일 | 정책이 읽는 것 | 내용 |
|---|---|---|
| `fluidserve.json` | `decode_step_law`와 `classes[]`만(`fluidserve_profile.go:24-33`) | decode-only iteration 시간 `t_dec(kv, n) = c0 + c_kv·kv + c_n·n`, c0 = 16.361 ms, c_kv = 1.2822e-5 ms/토큰, c_n = 0.07643 ms/요청(EXP-16 step 기록 377,838개, R² = 0.952). `classes[]`는 tier별 출력 길이 생존함수(격자 153점): swe(tier 25) n=209,282, 평균 494, p50 486, p90 645 / chat(tier 50) n=1,780,160, 평균 428, p50 389, p90 766 / deepresearch(tier 100) n=410,195, 평균 985, p50 973, p90 1,246. `classes[]`는 2026-07-30~08-01 run들의 `metrics.csv`에서 2026-08-02에 다시 만든 것이다. 같은 파일의 `prefill_step_law`·`mixed_step_validation`은 Go가 읽지 않는다 |
| `ttft.json` | `predictPrefillStepLatency`(`predict_utils.go:286-296`) | 빈 엔진에서 한 요청씩 잰 **prefill step 하나의 시간**(p50) 대 chunk 토큰 수, 26점 보간. 예: 1,024토큰 80.1 ms, 4,096토큰 264.2 ms, 8,192토큰 519.7 ms |
| `tpot.json` | **FluidServe 결정은 읽지 않는다.** 다만 `GetLatencyPredictor`가 두 파일을 모두 요구하므로 없으면 기동 실패(`predict_utils.go:198-235`) | 배치 크기 × 요청당 토큰 격자의 decode step 시간(247점) |

### 3.4 실행 중 추정하는 양

| 양 | 정의와 갱신 | 위치 |
|---|---|---|
| 상주 요청 장부 | 배치할 때 기록(`onDispatch`), 상태를 읽을 때마다 정리(`reconcile`) | `fluidserve_registry.go:474-647` |
| 진행 토큰 수 j | `j = StepId − 배치 시 StepId − ceil(prompt/chunk)` (음수면 0) — **그 요청을 관측한 값이 아니라 엔진 전역 step 수에서 계산한 값** | `fluidserve_registry.go:546-549` |
| decode 시작 시각 | j가 처음 0보다 커진 시각 | `:550-555` |
| 완료 추론 | 장부 수가 엔진의 실행 중 decode 수보다 많으면 j가 큰 것부터 제거, 생존확률 0.02 미만이면 제거, 20분 넘으면 제거 | `:622-640` |
| 인스턴스별 실측 iteration 시간 `pace_i` | 상태 두 개 사이의 경과 시간 EWMA ÷ step 수 EWMA(α = 0.1) | `fluidserve.go:1047-1055` |
| 인스턴스별 prefill duty | (실측 − decode-only 예측)×step 수의 EWMA ÷ 총 시간 EWMA, 0~1로 자름 | `fluidserve.go:1001-1021` |
| iteration 시간 보정 계수 `corr_i` | `corr ← corr × (1 + 0.002 × (실측/예측 − 1))`, 0.5~3.0, 인스턴스별(논문 구성 `percorr=true`). 새 인스턴스는 함대 값에서 시작 | `fluidserve_capacity.go:117-161`, 호출 `fluidserve.go:623` |
| prefill 비율 κ | 엔진이 실제로 계산한 prefill 토큰 ÷ 스케줄러가 청구한 토큰의 EWMA(α = 0.01). prefix 색인이 켜져 있으면 색인 예측의 잔차가 되어 0.25~4.0으로 자른다. 시작값 1.0 | `fluidserve_capacity.go:233-274, 300`, 호출 `fluidserve.go:930-936` |
| 배치→첫 토큰 잔차 | (decode 시작 시각 − 배치 시각) − 예측 prefill 시간의 평균·제곱평균 EWMA(α = 0.01), 함대 하나(논문 구성 `perdelay=false`) | `fluidserve_registry.go:679-743` |
| prefix 색인 | 인스턴스별로 보낸 프롬프트 블록 해시(체인 FNV-1a, 16토큰, LRU 500,000블록). 엔진의 eviction은 보지 못한다 | `fluidserve_prefix.go:37-198` |
| 클래스별 도착률 λ_c와 평균 프롬프트 | 첫 관측만 센다. 5초 버킷, EWMA 시간상수 3 × 체류시간(15~120초로 자름) | `fluidserve_instancecap.go:137-192` |

KV 단위는 **논리 토큰**(요청별 계산된 토큰의 합)이다. prefix 공유 때문에 물리 블록 수보다 크다. 물리 용량은
`물리/논리` 비로 나눠 논리 단위로 바꾼다(4.2).

---

## 4. 결정 경로 — 요청 하나가 거치는 단계

기호: h = 100(계획 horizon, iteration 단위), B_q = 요청 q의 토큰당 예산(chat 50, swe 75, deepresearch 100 ms),
0.9 = `fsAllowanceUtilisation`(`fluidserve.go:92`), z = 1.65.

### 4.1 도착 처리 (`calculateMetrics`, `fluidserve.go:541-732`)

1. 장부에 첫 관측 시각을 남기고, 보류된 요청이면 직전 호출과의 간격 `recheckMs`(실측 재시도 주기)를 얻는다(`:555-556`).
2. 첫 관측일 때만 클래스 도착률에 더한다(`:559-564`).
3. 예산을 정한다(`:565`, `fluidserve_registry.go:850-862`): 토큰당 예산 B_q, 기대 출력 길이 E[L_c] = 생존함수의 j=0
   조건부 잔여 길이.
4. prefix 해시를 계산하거나 캐시에서 꺼낸다(`:592-596`).
5. 인스턴스마다 엔진 상태로 실측 iteration 시간을 갱신하고(`observeInstance`, `:866-1065`), 보정 계수를 갱신하고
   (`:623`), 인스턴스 상태를 만든다(`flux`, `:617`). 인스턴스 상태는 엔진 step 번호·이 인스턴스로의 배치 횟수·인스턴스
   수가 그대로면 캐시를 재사용한다(`:472-501`).

### 4.2 인스턴스 상태 구성 (`buildFlux`, `fluidserve.go:1130-1370`)

- **대기 prefill의 유효량**: `horizonMs = h × pace_i`, `arriving = duty_i × horizonMs / (t_pre(chunk) − c0) × chunk`,
  `effectivePrefill = max(pendingPrefill, arriving)` (`:1216-1218`, `:1103-1114`). 대기열에 보이는 양과 최근 prefill
  비중에서 환산한 양 중 큰 쪽이다.
- **현재 평균 iteration 시간** `meanStep = meanStepMs(kv, n, effectivePrefill)` (`:1224-1225`). `meanStepMs`는
  (`fluidserve_capacity.go:392-416`)
  `s_p = prefillSteps(P, chunk)`(P ≤ 0이면 0, P ≤ chunk면 1, 아니면 P/chunk; h로 자름),
  `mean = corr_i × [(h − s_p)·t_dec + s_p·(t_pre(min(P, chunk)) + t_dec − c0)] / h`.
- **상주 요청 분류** (`:1227-1264`): 잔여 허용치(4.3)가 빈 인스턴스의 iteration 시간 `c0 × corr_i`보다 작으면
  "달성 불가"로 표시하고 이후 모든 판정에서 뺀다. 나머지 중
  - `gateAllowance` = 명목 예산의 최솟값(그 값을 정한 tier = `gateTier`),
  - `tightestAllowance` = 잔여 허용치의 최솟값. 단 잔여 허용치가 **현재** `meanStep`보다 이미 작은 요청은 뺀다.
- **KV 투영** (`:1266-1333`):
  `inflow = n_decode × h`;
  `outflow = max(0, Σ_r q_r·kv_r − z·sqrt(Σ_r q_r(1−q_r)·kv_r²))`, `q_r = (S_c(j_r) − S_c(j_r+h)) / S_c(j_r)`
  (`expectedOutflow`, `:1382-1408`; `completionProb`, `fluidserve_profile.go:89-105`), `kv_r = prompt_r + j_r`;
  `proj = max(0, kvLogical + inflow − outflow)`. 도착 항은 논문 구성에서 0이다.
- **속도에서 나온 KV 상한** `capKv = (0.9·gateAllowance / corr_i − c0 − c_n·n − f·(t_pre − c0)) / c_kv`,
  `f = min(1, s_p/h)` (`:1335-1337`, `maxKvForAllowance` `fluidserve_capacity.go:431-460`). 상주 요청이 없으면 무한대.
- **물리 KV 상한** `capMem = kvCapacity × 0.95 / ratio`, `ratio = 물리/논리`(둘 다 1,000 초과일 때만, 0.01~1로 자름)
  (`:1350-1365`).
- `headroom = min(capKv, capMem) − proj` (`:1367-1368`).

### 4.3 상주 요청의 잔여 허용치 (`liveViewLocked`, `fluidserve_registry.go:776-818`)

잔여 길이 `R_r = E[L_c − j | L_c > j]`(`expectedRemaining`, `fluidserve_profile.go:110-137`; 최소 1). 토큰당 예산
형식(세 클래스 모두)에서 decode 시작 전이면 허용치 = B_r, 시작 후면
`allowance_r = (B_r·(j_r + R_r) − (now − decodeStart_r)) / R_r` (0 미만이면 0). 명목 예산은 B_r 그대로다
(`:823-833`).

### 4.4 후보 평가 (`evaluate`, `fluidserve.go:1990-2289`)

1. **이 요청의 청구** (`:1994`, `costOf` `:1975-1988`): `cost = prompt + min(h, E[L_c])`.
2. **prefill 청구** (`:2012`, `prefillChargeFor` `:1953-1968`): `charge = (prompt − hit_i) × κ`. hit_i는 이 인스턴스의
   prefix 색인에서 앞에서부터 연속으로 맞은 블록 수 × 16(`fluidserve_prefix.go:139-163`).
3. 넣은 뒤 상태: `newPending = effectivePrefill + charge`, `newN = n + 1`, `newKv = proj + cost` (`:2013-2015`).
4. `meanAfter = meanStepMs(newKv, newN, newPending)` (`:2045`).
5. **판정 조건** (`:2128-2183`):
   - 예측 불가: `meanAfter`가 무한대(프로파일 보간 실패).
   - **게이트**: `meanAfter > 0.9 × min(B_q, gateAllowance × gateSlack)`, gateSlack = 1. 즉 이 요청과 상주 요청의
     **명목** 예산 중 가장 빡빡한 값의 90%.
   - **상주 요청 보호**: `meanAfter > tightestAllowance`(잔여 허용치, 0.9를 곱하지 않음).
   - **메모리**: `newKv > min(capKv, capMem)` (`pacecap=true`이므로 속도 상한도 본다, `:2165-2169`).
   - 첫 토큰 마감은 **계산만 하고 판정에 넣지 않는다**(`deadlinefeasible=false`, `:2180-2181`).
   - `feasible = 위 넷이 모두 거짓`.
6. **첫 토큰 추정** `prefillMs = own + queued` (`prefillEstimateParts`, `:2787-2896`):
   `own = prefillSteps(charge)·t_pre(min(prompt, chunk))`; `queued = prefillSteps(E)·t_pre(min(E, chunk))`, E =
   effectivePrefill. `interleave=true`이므로 대기열이 보이고(`pendingPrefill > 0`) duty > 0.05이면
   `queued = max(prefillSteps(pend)·t_pre(min(pend, chunk)) / duty, prefillSteps(arriving)·t_pre(min(arriving, chunk)))`
   (`queuedDrainMs`, `:2949-2993`). **charge가 0이면(prompt 전부 prefix 적중) 대기열과 관계없이 0을 돌려준다**(`:2805-2826`).
7. 거절 사유 판정용 `missesOwnBudget`: 첫 토큰 `waited + prefillMs > TTFT` 또는 `meanAfter > B_q` (`:2352-2388`).
8. 정렬용 값: 같은 tier 상주 요청 수 `sameCount`, 비율 `share`(`:2257-2260`); 피해 `harm = Σ_r min(10, (meanAfter −
   meanStep)/(allowance_r − meanStep))` (달성 불가·이미 초과 요청 제외, 클래스 항은 `classharm=false`라 없음;
   `:2489-2552`); 여유 `room = (min(capKv, capMem) − newKv) / capMem` (`:2269`).
9. 같은 판정을 투영 없이(현재 KV로) 한 결과를 세는 계측(`:2274-2286`). 결정에는 쓰지 않는다.

### 4.5 후보 걸러내기 — 클래스별 인스턴스 상한 (`applyInstanceCap`, `fluidserve_instancecap.go:242-457`)

평가 뒤, 정렬 전에 실행된다(`fluidserve.go:1564`). **후보를 표시하는 것이 아니라 목록에서 제거**하므로 보류·거절
판정도 남은 후보만 본다.

1. 클래스 c의 인스턴스당 서비스율 μ_c (`tierServiceRate`, `fluidserve_capacity.go:542-593`):
   `a = 0.9·B_c / corr_fleet`, 체류시간 `T = (chunks·t_pre + E[L_c]·B_c)/1000` 초, `kvPerReq = prompt_c + E[L_c]/2`,
   `d = c_kv·kvPerReq + c_n`, `extra = chunks·(t_pre − c0)`,
   `batch = (a − c0) / (d + extra·a/(1000·T))`, `batch ≤ capMem/kvPerReq`, `μ_c = batch / T`.
   (보정 계수는 인스턴스별이 아니라 **함대 값**을 쓴다, `:549`.)
2. `limit_c = ceil(λ_c / μ_c)`, 최소 1, 직전 값보다 올릴 때는 수요가 직전 값 + 0.05를 넘어야 한다(`:221-234`); 인스턴스
   수로 자른다(`:325-329`).
3. 이 클래스가 게이트를 정하는 인스턴스(`gateTier == c`) 수가 limit 미만이면 아무것도 거르지 않는다(`:351-355`).
4. limit 이상이어도, **다른 클래스 중 게이트 인스턴스 수가 자기 limit보다 적은 클래스가 없으면** 거르지 않는다(`:366-385`).
5. 위 둘을 모두 지나면 남기는 후보는 (a) 이 클래스 요청이 가장 많은 게이트 인스턴스 limit개, (b) 이미 이 클래스 예산
   이하의 게이트를 가진 다른 인스턴스("riding": 놓아도 게이트가 안 바뀌는 곳). 빈 인스턴스는 제거된다(`:392-444`).
6. 첫 버킷(약 5초)이 끝나기 전에는 작동하지 않는다(`:289-296`).

이 앞의 클래스 고정 필터(`applyClassPin`, `fluidserve.go:2578-2618`)는 논문 구성에서 꺼져 있다.

### 4.6 정렬 (`sortCandidates`, `fluidserve.go:1862-1908`)

수용 가능 후보가 앞. 수용 가능 후보끼리는 `score = w·(sameCount/최대 sameCount) + (1−w)·room`, w = 1이므로 **같은
클래스 상주 요청 수가 많은 순**, 같으면 room이 큰 순, 그다음 인스턴스 id. 수용 불가 후보끼리는 harm이 작은 순,
room, id.

### 4.7 결정 사다리 (`selectInstance`, `fluidserve.go:1515-1710`)

1. 1위가 수용 가능 → **route**(`:1617-1621`).
2. 아니면 `canWait`(`:2743-2774`)이 참이면 **보류**(`:1626-1634`): 1위(가장 피해가 작은 수용 불가 후보) 기준으로
   `after = prefillMs + bound`, `bound = 잔차 평균 + 1.65 × 잔차 표준편차`(표본 50개 미만이면 300 ms 고정 여유);
   `waited < TTFT_q − after − recheckMs`이면 보류. 보류는 스케줄러에 대기열을 두지 않는다. 게이트웨이가 요청을 쥐고
   500 ms마다 다시 묻고, 매번 4.1부터 전체 경로를 다시 탄다. 보류된 요청들 사이에 순서는 없다.
3. 아니면 **거절**(`:1654-1706`). `shed=true`이고 1위에서 `missesOwnBudget`이면 사유 "cannot_meet", 아니면
   `enableforce=false`이므로 사유 "no_feasible". 둘 다 즉시 거절이고 사유는 카운터 라벨일 뿐이다.
4. 강제 배치(`:1708`)는 논문 구성에서 도달하지 않는다. shed와 force가 모두 꺼지면 기동 시 panic한다(`:3247-3258`).

### 4.8 배치 후 (`commit`, `fluidserve.go:3068-3145`)

장부 기록(`onDispatch`: 배치 횟수 증가 → 해당 인스턴스 상태 캐시 무효화, 청구 토큰 누적), prefix 색인 갱신
(`:3092-3094`), 배치 로그 한 줄(`fsplacement`).

---

## 5. 플래그 전체와 논문 구성의 값

값의 출처: `set_scheduler_profiling.py`가 항상 쓰는 값(`ms_dev/scripts/set_scheduler_profiling.py:251-296`), arm의
환경변수(드라이버의 `set_arm`이 먼저 FS_* 변수를 모두 unset하고 `FS_AFFINITY_METRIC=share FS_PER_INSTANCE_CORR=false
FS_MEMORY_PACE_CAP=false FS_PREFILL_INTERLEAVE=false`를 깐 뒤 arm이 넷 모두를 다시 덮어쓴다,
`run_exp108_t75.sh:177-206`). 설정되지 않은 플래그는 스크립트가 명령줄에서 지워 컴파일 기본값으로 돌아간다
(`set_scheduler_profiling.py:699-725`). **★ = 논문 값이 현재 컴파일 기본값과 다르다.** "없음"은 `6dc9f035`에 그 플래그가
없었다는 뜻이고, 그 경우 동작은 현재 기본값과 같다(0절).

| 플래그 `--fluidserve-…` | 컴파일 기본값 (`cmd/config/config.go`) | 논문 값 | 무엇을 바꾸나 |
|---|---|---|---|
| ★ `profile-path` | `""` (:459) | `/profiling/fluidserve.json` | 길이 분포·decode 법칙 파일. 비면 기동 실패 |
| ★ `class-budgets` | `""` (:462) | `25:decode:75,50:decode,100:decode` | tier별 예산 형식과 값. 비면 기동 시 panic(`fluidserve.go:3176-3179`) |
| `horizon-steps` | 100 (:469, `consts.go:236`) | 100 | 계획 horizon. 4.2·4.4의 다섯 곳 이상에서 읽힌다(8.2) |
| `charge-horizon` | 0 (:475) | 없음 | 청구 창만 따로 정하기. 0 = horizon과 같음 |
| `z-safety` | 1.65 (:495, `consts.go:238`) | 1.65 | 방출 하한과 첫 토큰 잔차 상한의 표준편차 배수 |
| `ttft-safety-ms` | 300 (:499, `consts.go:241`) | 300 | 잔차 표본 50개 전 고정 여유 |
| `enable-pend` | true (:503) | true | 보류 |
| `enable-shed` | true (:507) | true | 자기 예산을 못 지킬 요청 거절 |
| `shed-signal` | `coupled` (:533) | coupled | 거절 판정을 선택된 후보로 할지 후보 평균으로 할지 |
| `oracle-length` | false (:547) | false | 요청별 길이 힌트 사용 |
| `oracle-flux` | false (:551) | 없음 | 힌트를 투영에도 사용 |
| `enable-affinity` | true (:568) | true | 클래스 선호 전체 |
| `affinity-weight` | 1.0 (:573) | 1.0 | 선호 대 여유의 가중 |
| `affinity-metric` | `count` (:687) | count | 선호를 개수로 볼지 비율로 볼지 |
| `per-instance-correction` | true (:583-584) | true | 보정 계수를 인스턴스별로 |
| `memory-uses-pace-cap` | true (:599-600) | true | 메모리 판정에 `min(capKv, capMem)` |
| `per-instance-delay` | false (:615-616) | false | 첫 토큰 잔차를 인스턴스별로 |
| `deadline-uses-delay` | false (:629-630) | false | 첫 토큰 마감 판정에 잔차 상한 더하기 |
| `shed-ignores-first-token` | false (:650-651) | false | 거절 판정에서 첫 토큰 항 빼기 |
| `prefill-interleave-aware` | true (:664-665) | true | 첫 토큰 추정의 대기열 항을 duty로 나눔 |
| `prefix-aware` | true (:724) | true | prefix 색인으로 인스턴스별 prefill 청구 |
| `prefix-calibration` | true (:733) | true | 청구에 κ 곱하기 |
| `prefix-block-tokens` | 16 (:740) | 16 | 색인 블록 크기 |
| `prefix-capacity` | 500000 (:746) | 500000 | 색인 블록 수 상한 |
| `class-pin` | `""` (:753) | off | 클래스를 고정 인스턴스에 묶기 |
| `enable-flux` | true (:764) | true | KV 투영(끄면 inflow·outflow = 0) |
| `force-margin` | false (:767) | false | 강제 배치 판정에 0.9 적용 |
| `own-budget-gate` | false (:776) | false | 게이트에서 인스턴스 최솟값 빼기 |
| `incumbent-balance` | true (:784) | 없음 | 상주 요청 보호 조건 |
| `mem-level-test` | false (:801) | 없음 | 메모리 판정을 현재 KV로 |
| `memory-safety` | 0 (= 0.95) (:809) | 없음 | 물리 용량 비율 |
| `prefill-full-iteration` | false (:815) | 없음 | 대기열 비용 대체식 |
| `prefill-resident-queue` | false (:826) | 없음 | prefix 전부 적중 시에도 대기열 항 유지 |
| `arrival-horizons` | 0 (:833) | 없음 | 투영에 도착 항 |
| `deadline-feasible` | false (:846) | false | 첫 토큰 마감을 수용 가능 판정에 넣기 |
| `kv-slope-projection` | false (:861) | false | 투영을 관측 KV 변화율로 |
| `gate-slack` | 1.0 (:874) | 1.000 | 인스턴스 최솟값 게이트의 완화 배수 |
| ★ `class-harm` | true (:909) | **false** | 수용 불가 후보 정렬에 다른 클래스 비율 항 |
| ★ `class-instance-cap` | false (:923) | **true** | 4.5의 클래스별 인스턴스 상한 |
| `cap-costs-capacity` | false (:929) | 없음 | 게이트를 조여도 용량 손실이 없으면 상한 예외 |
| `class-instance-cap-window-mult` | 3.0 (:941-942) | 3.0 | 상한 수요 추정의 시간상수(체류시간 배수) |
| ★ `enable-force` | true (:948) | **false** | 강제 배치 분기(끄면 명시적 거절) |

정책 밖이지만 논문 구성을 재현하려면 같이 필요한 값: 스케줄러 `--scheduling-policy fluidserve`,
`--ttft-profiling-data-path`/`--tpot-profiling-data-path`(기본 `""`, `config.go:401, 404`), 상태 pull 500 ms;
게이트웨이 `--wait-scheduling-retry-interval 500ms`(기본 1000 ms), `--wait-scheduling-timeout 35000ms`(기본 5000 ms),
`--wait-queue-threads 4096`(기본 5), `--max-queue-size 16384`(기본 512) (`cmd/gateway/app/options/config.go:66-72`,
`set_scheduler_profiling.py:463-528`). 게이트웨이 기본값으로는 동시에 보류할 수 있는 요청이 5건으로 묶이고
보류가 5초에서 끊긴다.

---

## 6. 결정에 영향을 주는 상수

| 상수 | 값 | 위치 | 근거로 적힌 것 |
|---|---|---|---|
| `fsAllowanceUtilisation` | 0.90 | `fluidserve.go:92` | 게이트·`capKv`·상한의 서비스율에 곱하는 여유 |
| `fsMemorySafety` | 0.95 | `fluidserve.go:88` | 엔진이 풀이 차기 전에 preemption을 시작한다는 주석 |
| `fsHarmCap` | 10 | `fluidserve.go:96` | 상주 요청 하나의 피해 상한 |
| `fsMinPrefillDuty` | 0.05 | `fluidserve.go:828` | duty로 나누기를 멈추는 하한 |
| `fsPrefillDutyAlpha` | 0.1 | `fluidserve.go:847` | duty·pace EWMA 가중 |
| 관측 무효 조건 | step > 10,000 또는 경과 > 30 s, decode 요청 < 1 | `fluidserve.go:903-914` | 재시작·유휴 구간 배제 |
| 물리/논리 비 | 하한 0.01, 상한 1, 두 값 > 1,000일 때만 | `fluidserve.go:1350-1359` | |
| `fsCorrectionAlpha`, 범위 | 0.002, [0.5, 3.0], 비율 0.2~5 밖 표본 버림 | `fluidserve_capacity.go:89-93, 124` | 시간상수 약 1분 |
| prefill 비율 범위·가중 | [0.02, 1.0] / 잔차 모드 [0.25, 4.0], α 0.01, 시작 1.0 | `fluidserve_capacity.go:184-198, 300` | |
| `fsRetireSurvival` | 0.02 | `fluidserve_registry.go:192` | 생존확률이 이보다 작으면 장부에서 제거 |
| `fsMaxRecordAgeMs` | 20분 | `fluidserve_registry.go:195` | |
| `fsPlacementDelayAlpha`, 최소 표본, 상한 | 0.01, 50, 60 s | `fluidserve_registry.go:207-214` | |
| `fsCapBucketMs` | 5,000 ms | `fluidserve_instancecap.go:55` | 논문의 "5-second window" |
| `fsCapTauMinS`/`MaxS` | 15 / 120 s | `fluidserve_instancecap.go:62-63` | 시간상수 = 3 × 체류시간을 자르는 범위. 체류시간 = B_c·E[L_c]: chat 21.4 s → τ 64 s, swe 37.0 s → 111 s, deepresearch 98.5 s → 120 s(잘림) |
| `fsCapRaiseEps` | 0.05 | `fluidserve_instancecap.go:68` | 상한 증가 이력 |
| chunk 기본값 | 8,192 (상태에 없을 때), 서비스율 계산 2,048 | `fluidserve.go:1143`, `fluidserve_capacity.go:559` | |
| 프로파일 상수 | c0, c_kv, c_n, 생존함수, prefill 표 | 3.3 | 오프라인 측정 |
| 미지 tier의 길이 분포 | p90이 가장 긴 클래스 | `fluidserve_profile.go:168-191` | |
| prefix 인스턴스 비트 | 최대 32 | `fluidserve_prefix.go:122` | |

---

## 7. 논문 서술과 코드의 대응

"일치"는 서술이 코드와 같은 계산을 말한다는 뜻이다. **차이가 있는 항목은 revision에서 본문 또는 코드 중 하나를 고쳐야
한다.**

| 논문 요소 (3~5절) | 코드 | 대응 |
|---|---|---|
| 요청 등록: workload class와 SLO를 함께 싣는다. 5절은 "workload class(길이 통계)와 SLO class(TBT)를 의도적으로 분리한다"고 쓴다 | 요청에는 TTFT와 tier(TBT 필드) 둘뿐이다. 길이 분포(`lengths.forTier(tier)`)와 예산(`budgets.forTier(tier)`)이 **같은 tier 키**로 조회된다(`fluidserve.go:565`, `fluidserve_registry.go:784-787`). 길이 분포를 tier와 다른 키로 고르는 경로가 없다 | **차이.** 구현은 분리하지 않는다. "OpenAI 인터페이스를 확장했다"도 실제로는 `priority` 정수에 두 값을 넣은 것이다 |
| 진행에 따라 다듬는 잔여 길이 R̂ = E[L_c − j | L_c > j] (식 1). "완료된 요청에서 경험적 분포 L_c를 유지한다" | 식은 `expectedRemaining`으로 구현(`fluidserve_profile.go:110-137`). 분포는 **오프라인 파일**(이전 run들의 출력 길이로 2026-08-02 생성)이고 실행 중 완료 요청으로 갱신하지 않는다 | 식은 일치, **분포의 출처는 차이** |
| 진행 토큰 수 재구성 "admission 기록과 관측된 엔진 step으로" | `j = 전역 StepId 차 − ceil(prompt/chunk)`(`fluidserve_registry.go:546`). 엔진 대기열에서 기다린 시간도 진행으로 센다 | 서술은 맞으나 그 추정이 요청 자체의 관측이 아니라는 한계를 안 적었다(8.4) |
| Receding-horizon 투영, h = 100 iteration | h = 100, 상태를 읽을 때마다 새로 계산(캐시는 상태·배치가 바뀌면 무효) | 일치 |
| 식 2: K̂ = K + K_in − K_out | `proj = kvLogical + n·h − outflow`, outflow는 **평균 − 1.65 표준편차**로 낮춘 방출. K_in의 "새로 받을 요청"은 평가 중인 그 요청(`cost`)뿐이고 horizon 동안 다른 배치의 도착 항은 없다(`arrival-horizons=0`) | 대체로 일치. **z = 1.65 하한은 논문에 없다** |
| Prefill 간섭: 대기 prefill + 이 요청의 prefill(prefix 재사용분 할인) + 최근 관측된 prefill 활동 | `max(대기열, duty 환산량) + (prompt − 색인 적중) × κ` | 일치. κ(색인 예측의 잔차 보정)와 "합이 아니라 큰 쪽"은 서술에 없다 |
| 평균 decode 시간 τ̂: "가벼운 오프라인 프로파일이 이 양들을 iteration 시간으로 매핑" | 오프라인 법칙 × **인스턴스별 온라인 보정 계수**(`fluidserve_capacity.go:392-416`) | **보정 계수가 논문에 없다.** 논문 구성에서 켜져 있고, 플래그 도움말은 한 시간 trace 2반복에서 이것만 켜면 요청 단위 admitted 68.9 → 75.8, 메모리 판정 변경과 함께 켜면 76.2라고 적었다(EXP-98; `config.go:583-614`) |
| 런타임 TBT 한계 τ_rt (식 3), "명목보다 빡빡할 때만 적용" | `allowance_r`(4.3)이 식 3과 같다. 판정은 게이트(명목 최솟값 × 0.9)와 상주 요청 보호(잔여 최솟값, 0.9 없음)의 **두 조건**이다 | 식은 일치. 결합 방식이 다르다(아래 줄) |
| 알고리즘 1의 TBT 조건 `τ̂ > min(B_q, min_r min(B_r, τ_rt_r))` | (1) **0.9 여유**가 명목 쪽에만 곱해진다. (2) 잔여 허용치가 빈 인스턴스 iteration 시간보다 작은 요청("달성 불가")은 두 조건 모두에서, 현재 속도로 이미 초과 중인 요청은 보호 조건에서 뺀다(`fluidserve.go:1231-1264`) | **차이.** 0.9 여유와 두 가지 제외가 서술에 없다 |
| 알고리즘 1의 KV 조건: "horizon만이 아니라 남은 생성 전체에 걸친 **최댓값** K̂max(q)" | `proj + cost > min(capKv, capMem)`. proj와 cost 모두 **h로 잘린** 값이고 남은 생성 전체의 최댓값을 계산하는 코드가 없다. 비교 대상에 속도에서 나온 `capKv`도 들어간다 | **차이.** 논문이 서술한 최댓값 계산은 구현되어 있지 않다 |
| 알고리즘 1의 TTFT 조건 `w_q + D̂FT > F_q` → 수용 불가 | 계산은 하지만(`fluidserve.go:2180`) `deadline-feasible=false`라 **수용 가능 판정에 들어가지 않는다.** TTFT 추정은 보류 여부(`canWait`)와 거절 사유 라벨에만 쓰인다 | **차이.** 논문 구성에서 수용 가능 판정은 TBT·KV 두 종류뿐이다. 옛 기록은 이 조건을 켜도 결과가 움직이지 않았다고 적었다(EXP-87, EXP-100) |
| SLO affinity: 같은 TBT 상주 요청 수가 많은 곳 | `count` 지표, w = 1, 동점은 여유(room), 그다음 id | 일치 |
| Demand-aware spread control: 클래스별 도착률을 "5초 창"으로 재서 프로파일된 인스턴스 성능과 비교, 충분하면 현재 군집 유지, 아니면 확장 허용 | 5초는 **버킷** 길이이고 실제 평활은 EWMA 시간상수 15~120 s(4.5). 기본은 제한 없음이고, 게이트 인스턴스 수가 limit 이상이면서 **다른 클래스가 자기 limit보다 모자랄 때만** 후보를 줄인다. "riding" 예외, 후보 **제거**(보류·거절 판정에도 영향), 서비스율 계산 식은 서술에 없다 | **부분 일치.** 동작 조건이 서술보다 좁고, 창 길이 서술은 실제 평활 시간과 다르다 |
| Pend: TTFT 예산이 남는 동안 대기, 갱신된 상태로 재평가 (그림 7의 "Pending queue") | 대기열은 스케줄러에 없고 게이트웨이가 요청을 쥐고 500 ms마다 다시 묻는다. 여유 판단에 실측 잔차의 평균 + 1.65 표준편차와 재시도 간격을 뺀다 | 기능은 일치. "queue"라는 표현과 달리 순서가 없다 |
| Reject: 더 기다리면 TTFT를 못 지킬 때 | 강제 배치를 끈 상태에서 그대로 성립 | 일치 |
| "192 µs of scheduling overhead per incoming request" | EXP-140: **결정(스케줄링 호출) 하나**의 평균 192 µs, 그중 정책 169 µs. `-v 0`, 바이너리 `9e8f68b6`, 35 req/s 8분 1회 측정. 도착 요청 하나는 보류 재시도 때문에 1.4~1.8회 결정된다(EXP-139). 성능 실험은 모두 `-v 4`로 돌았고 그 설정의 결정 시간은 320 µs다 | **표현 차이.** "요청당"이 아니라 "결정당"이고 로깅 설정이 성능 실험과 다르다. 옛 문서의 0.013 ms는 밀리초 내림값을 평균한 것이라 철회됐다 |
| "5.6K line Go scheduler" | 2.1절 | 주석 포함 행 수이며 시점이 논문 바이너리 소스와 다르다 |
| 논문에 없는데 논문 구성에서 켜져 있는 것 | 인스턴스별 보정 계수, prefix 색인과 κ 보정, 0.9 여유, z = 1.65 하한, 달성 불가 요청 제외, 첫 토큰 대기열 항의 duty 나눗셈, swe tier 키 25와 실제 예산 75의 분리, 상한의 다른 클래스 부족 조건 | revision에서 본문 또는 부록에 적을지 결정해야 한다 |
| 그림 16·17의 "FluidServe w/o Affinity" | 그림 출처는 EXP-138 arm `noaffnocaphour`(`run_exp138_noaffnocap_hour.sh:560`): **클래스 선호와 클래스별 인스턴스 상한을 둘 다 끈 것** | **차이.** 캡션은 affinity 하나를 뺀 것처럼 읽힌다. `--fluidserve-enable-affinity=false`만으로는 상한이 남는다(상한은 선호 플래그와 관계없이 `classCount`를 읽는다, `fluidserve_instancecap.go:340-344`) |
| 그림 15의 horizon 민감도 | horizon을 바꾸면 방출 창, 증가량, 청구 창, prefill 비중의 분모, duty 환산의 시간 길이가 **함께** 바뀐다(8.2) | 해석 시 주의. 그림은 "투영 길이"만 바꾼 것으로 읽힌다 |
| 표 1의 응답 길이 | ⚠ 프로파일의 실측 출력 길이는 swe p50 486 / p90 645, deepresearch p50 973 / p90 1,246인데 표 1은 SWE-agent 971 / 1,233, Search-arena 517 / 648이다. 두 행의 값이 서로 바뀐 것과 매우 가깝다. 표 1을 만든 스크립트를 이 저장소에서 찾지 못했다(`paper_figures/README.md`) | ⚠ 확인 필요 |

---

## 8. 알려진 결함과 미해결

### 8.1 컴파일 기본값으로는 논문 구성이 재현되지 않는다
- **무엇**: `class-harm`(true), `class-instance-cap`(false), `enable-force`(true)가 논문 값과 반대이고,
  `class-budgets`·`profile-path`·프로파일 경로는 비어 있어 그대로면 기동하지 않는다. 게이트웨이 기본값(재시도 1 s,
  천장 5 s, 워커 5개)은 보류를 사실상 막는다.
- **근거**: 5절 표의 ★ 행. v0.4 명세가 "채택 결정의 기록이지 배포된 기본값의 기록이 아니다"라고 적었다
  (`archive/llumnix_reproduce/ms_dev/notes/fluidserve-v0.4.md` "기본값이 아직 안 옮겨졌다" 절). 이후 v0.3의 네 값과
  prefix는 기본값이 옮겨졌지만 위 셋은 아니다.
- **왜 중요한가**: 공개 artifact로 내면 기본 배포가 논문과 다른 정책이 된다. 기본값을 옮기면 FS_* 를 명시하지 않은
  옛 arm의 뜻이 바뀌므로, 옛 드라이버에 현재 값을 먼저 박고 옮겨야 한다.

### 8.2 ablation 플래그 하나가 여러 메커니즘을 함께 바꾼다
- **horizon**: `horizonSteps`가 `fluidserve.go:1216`(duty 환산 시간), `:1224`·`:2045`(평균 iteration 시간의 prefill 비중
  분모), `:1266`(증가량), `:1281`(방출 창), `:1336`(`capKv`), `costOf`(`:1982-1986`, 청구 창)에서 읽힌다. 누적 ablation
  (EXP-133, 정적 35 req/s, 2반복)의 마지막 단계가 horizon을 1로 내려 "선청구만 끄려" 했는데, 도착 요청의 프롬프트가
  대기 prefill에 들어가 `s_p ≥ 1`이 항상 성립하므로 모든 후보의 예상 iteration 시간이 prefill 한 번의 비용이 되어
  달성률 39.9~40.0, 거절 약 60%가 나왔다. 그 −44점은 선청구의 몫이 아니다. 청구 창을 분리하는 플래그가 그 뒤 추가됐다
  (`config.go:475-494`). 논문 그림 15도 같은 플래그를 움직였다.
- **affinity**: 끄면 정렬 가중과 피해의 클래스 항(논문 구성에서는 이미 꺼짐)이 같이 0이 되지만 상한은 남는다. 논문의
  "w/o Affinity"는 상한까지 끈 arm이다(7절).
- **flux**: 끄면 inflow·outflow만 0이 되고 `cost`의 h 청구와 `capKv`의 horizon은 그대로다(`fluidserve.go:1320-1324`).
- **pend**: 끄면 강제 배치가 꺼진 논문 구성에서는 첫 판정에서 수용 불가인 요청이 모두 즉시 거절된다.
- **왜 중요한가**: ablation 표의 각 행이 "그 메커니즘 하나"의 효과라고 서술하면 사실과 다르다.

### 8.3 정적 고부하에서 인스턴스 상한과 보류가 점수를 깎는다
- **무엇**: 정적 35 req/s(8분, t75)에서 상한 **하나만** 끄면(EXP-134, 2반복) 모든 도착 대비 달성률 75.3 / 73.3 → 85.8 /
  85.6, 거절 22.0 / 23.4% → 11.9 / 12.1%, admitted는 그대로(99.2 / 98.5 → 99.1 / 99.2), goodput +10.4~12.9%. 같은 변경을
  한 시간 trace에서 하면(EXP-135, 2반복) 총점은 차이 없음(78.5 / 77.8 → 78.5 / 78.4)이고 deepresearch만 −2.0~−3.4점.
  보류는 선호를 끈 뒤 누적으로 빼면 +3.3~+6.1점(EXP-133 두 번째 단계), 혼자 빼면 차이 없음(EXP-131 leave-one-out,
  모든 도착 대비 74.1(2반복) 대 대조 73.6~75.5).
- **근거**: `archive/Agent_applications/agent_motivation_experiment/experiments/EXP-134_instance-cap-alone.md` 결과 표,
  `EXP-135_instance-cap-hour.md`, `EXP-133_cumulative-ablation.md`, `EXP-131_leave-one-out-ablation.md` 5절,
  요약은 `archive/llumnix_reproduce/ms_dev/notes/STATUS.md` 2026-09-14 절.
- **왜 중요한가**: 논문은 상한을 이득으로 서술한다. 상한의 비용은 도착률의 함수이고, 정당화하는 근거는 한 시간 trace의
  클래스별 값(deepresearch)뿐이다. 보류의 "3~6점"은 선호가 꺼진 조건부 값이므로 단독 효과로 인용하면 안 된다.

### 8.4 스케줄러는 자기가 예측하는 양을 관측하지 못한다
- **무엇**: 진행 토큰 수와 decode 시작 시각이 엔진 전역 step 카운터에서 계산된다. 엔진 대기열 뒤에서 기다리는 요청도
  1초 안에 "decode 시작"으로 표시된다. 그래서 첫 토큰 잔차 통계(`canWait`의 bound)는 모델 대 모델의 차이다.
- **근거**: `fluidserve_registry.go:546-560`; `commit` 주석(`fluidserve.go:3099-3119`)이 EXP-101에서 스케줄러가 계산한
  배치→첫 토큰 평균 314 ms(10초 초과 0건) 대 클라이언트 실측 deepresearch 중앙값 0.90 s, p90 16.20 s(77,920건)를 적었다.
- **왜 중요한가**: 논문의 "진행 추적"과 "TTFT 마감 판단"이 이 추정 위에 있다. 리뷰어가 정확도를 물으면 위 수치가 답이다.

### 8.5 KV 투영이 도착을 빼고 방출만 넣어 낙관적이다
- **무엇**: horizon 동안 다른 요청이 들어오는 양이 투영에 없다. 코드 주석은 EXP-114에서 proj가 한 horizon 뒤 실측보다
  중앙값 81,240토큰(상주 집합의 5.3%) 낮고 부호가 86~98% 표본에서 틀렸으며, 0.95 안전계수와 크기가 비슷해 서로
  상쇄되고, route의 40.6%가 엔진의 preemption 시작점(점유율 0.973) 너머 인스턴스에 갔다고 적었다
  (`fluidserve.go:2016-2039`). ⚠ 함대 구성이 주석에 명시되어 있지 않다(`config.go:355-360`은 8인스턴스 함대의 시작점을 말한다).
- **왜 중요한가**: 논문 식 2의 정확성 주장과 "메모리 판정이 preemption을 막는다"는 서술의 근거가 약하다.

### 8.6 첫 토큰 추정의 대기열 항
- **무엇**: duty로 나누는 대기열 항이 무조건적 duty(prefill이 없는 구간 포함)를 쓰고, 0.05 하한을 넘는 순간 추정이
  불연속으로 뛴다. 요약된 예측/실측 비는 8인스턴스 1.85, 4인스턴스 3.34(대체식에서 0.50, 0.84). prefix 전부 적중이면
  대기열이 있어도 0 ms를 낸다 — 8인스턴스 한 시간 run 넷에서 배치의 1.50~1.95%, 4인스턴스 70B 함대에서는 그 swe
  배치가 예산 초과 0.0%.
- **근거**: `fluidserve.go:2805-2826`, `:2903-2948` 주석(EXP-117, 한 시간 run 여섯, 두 함대).
- **왜 중요한가**: 논문 구성(4인스턴스)에서 이 추정은 보류 여부를 정한다. 대체식은 플래그로만 있고 측정된 논문 구성은
  옛 식이다.

### 8.7 두 보정 루프가 같은 잔차를 나눠 갖는다
- **무엇**: 보정 계수는 "예측과 실측의 차이"를, duty는 "decode 법칙이 설명하지 못한 시간"을 흡수한다. decode 법칙 오차가
  두 곳에 동시에 들어가고 duty는 첫 토큰 추정에 곱해진다. 분리하려면 엔진이 prefill 시간을 따로 보고해야 한다.
- **근거**: `archive/llumnix_reproduce/ms_dev/notes/fluidserve-system-design.md` "알려진 결함" 절 9.4.

### 8.8 어느 인스턴스가 어느 클래스를 맡는지는 run마다 다르다
- **무엇**: 클래스 선호는 배정 표가 없으므로 모이는 인스턴스가 run마다, 한 run 안에서도 바뀐다. 같은 설정 두 반복에서
  preemption이 한 run은 두 엔진에 369 / 275회, 다른 run은 한 엔진에 936회(나머지 0)였다.
- **근거**: 같은 문서 9.3. 또 같은 셀·같은 설정에서 완료 1,000건당 preemption이 EXP-108(2026-08-31) 16.7 / 15.2, 누적
  ablation 대조군(2026-09-14) 45.1로, 점수는 세션을 건너가고 preemption은 건너가지 않는다(STATUS.md 2026-09-14 절).
- **왜 중요한가**: 엔진 단위 그림 하나를 정책의 성질로 서술하면 안 된다. preemption은 세션 간 비교에 쓰지 않는다.

### 8.9 분리의 대부분은 선호가 아니라 판정 조건이 만든다
- **무엇**: 한 시간 trace에서 네 인스턴스 중 chat을 하나도 들지 않은 인스턴스 수(창별 중앙값)가 선호를 켜면 2.1
  (대조군 2반복), 끄면 1.8(1회)이다. 분리의 약 86%가 수용 가능 판정 혼자서 생긴다는 뜻이다(EXP-132, 한 시간 trace
  변형 비교; `EXP-132_fluidserve-variations-hour.md` 540~556행 부근).
- **왜 중요한가**: 논문이 affinity를 분리의 원인으로 서술하는 강도를 조정해야 할 수 있다.

### 8.10 오버헤드 측정 조건
- **무엇**: 192 µs는 `-v 0`, 계측판 바이너리, 1회 측정이다. 동시 요청에 대한 기울기(+10~12 µs / 100건)는 `-v 4`에서 쟀다.
  결정 수는 도착보다 빨리 늘어 10 → 70 req/s에서 도착 7배, 결정 22배였다.
- **근거**: `archive/Agent_applications/agent_motivation_experiment/experiments/EXP-140_control-plane-overhead.md` 5~7절.

### 8.11 그 밖에
- `tpot.json`은 결정에 쓰이지 않지만 없으면 기동 실패. `fluidserve.json`의 `prefill_step_law`도 읽지 않는다(3.3).
- 클래스별 인스턴스 상한의 서비스율은 인스턴스별 보정이 켜져 있어도 함대 보정을 쓴다(`fluidserve_capacity.go:549`).
- 길이 분포가 같은 워크로드 생성기의 이전 run 출력에서 만들어졌다. 워크로드를 바꾸면 다시 만들어야 하고, 한 번 빠뜨려
  deepresearch 평균이 282(실측 985)로 남은 적이 있다(`fluidserve.json`의 `classes_note`).
- 스케줄러 매니페스트는 rescheduling 루프(`neutral_load`)를 켜 두지만(`deploy/neutral/full-mode-scheduling/load-balance/scheduler.yaml:73-75`) 드라이버가 엔진
  migration이 꺼져 있음(`LLUMNIX_ENABLE_MIGRATION=0`)을 확인하고 시작한다(`run_exp108_t75.sh:141-142`). ⚠ 루프가 실제로
  아무 명령도 내지 않았는지는 확인하지 않았다.
- SLO를 넣는 `priority` 필드를 vLLM은 우선순위로 읽는다(`scheduling_request.go:92-95` 주석). 엔진이 우선순위 스케줄링으로
  설정되어 있지 않으면 무시된다고 보지만 ⚠ 엔진 인자에서 확인하지 않았다.

---

## 9. 이 문서가 다루지 않는 것과 기록 위치

- **기준선**(PolyServe, Llumnix SLO, llm-d, vLLM router 이식)의 구현: `pkg/scheduler/policy/polyserve*.go`, `vllmcache.go`,
  이식 대조는 `archive/llumnix_reproduce/ms_dev/notes/polyserve-fidelity.md`.
- **실험 절차와 재현**: `project/reproduce.md`. **revision 할 일 목록**: `project/revision.md`.
- **채점 규칙**(`ladder95`, 분모 정의): `Agent_applications/agent_motivation_experiment/analysis_scripts/request_level/deadline_ladder_attainment.py`.
- **설계가 어떻게 여기까지 왔는지**: `archive/llumnix_reproduce/ms_dev/notes/fluidserve-v0.4.md`(마지막 명세),
  `fluidserve-system-design.md`(2026-08-28 코드 기준 참조), `fluidserve-implementation-facts.md`(⚠ 그 문서의 결정당
  0.013 ms는 철회된 값), `class-instance-cap-design.md`(상한 설계), `fluidserve-prefix.md`(prefix 색인),
  `prefill-budget-design.md`(미구현 제안), `fluidserve-implementation.md`(2026-08-07까지의 시간순 기록), `STATUS.md`
  (제출 시점 상태와 누적 ablation).
- **각 실험의 설계·판정 규칙**: `archive/Agent_applications/agent_motivation_experiment/experiments/EXP-NN_*.md`.
- **엔진(vLLM) 쪽**: 수정 없음. 엔진 기동 때 적용되는 `patches/vllm-sched/llumnix_client_index_fix.py`는 Llumnix의
  소켓 인덱스 버그 수정이며 모든 arm에 같다.
