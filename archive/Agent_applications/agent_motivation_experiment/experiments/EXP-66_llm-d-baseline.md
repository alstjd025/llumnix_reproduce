# EXP-66 — llm-d predicted-latency를 기준선으로

**상태**: 실행 전. 이 파일은 **결과가 나오기 전에** 쓴다.
**배포·환경 계획은 `llumnix_reproduce/ms_dev/notes/llmd-baseline.md`가 정본**이고, 여기에는
가설과 판정 규칙만 둔다. 시스템 자체의 분석은 `related-works-review.md` §12.

---

## 0. 왜 이 실험을 하는가

llm-d v0.8의 predicted-latency scheduling은 **우리와 같은 계층에서 같은 형태의 판정 조건을
쓴다** — 새 요청의 예측 TPOT를 `min(그 요청의 예산, 그 인스턴스에서 이미 처리 중인 요청 중
가장 빡빡한 예산)`과 비교한다(`prediction.go:161-173`). 심사에서 "이미 있는 것 아닌가"로
나올 가장 유력한 후보이고, 지금 우리에게는 그것을 우리 워크로드 위에서 잰 값이 하나도 없다.

**그러므로 이 실험의 목적은 우리가 이기는 것을 보이는 것이 아니라, 차이가 어디에서
나오는지를 재는 것이다.** 판정 규칙에 "우리 주장을 좁혀야 하는" 결과를 먼저 적어 둔다.

---

## 1. 가설 — 실행 전에 적는다

| # | 가설 | 근거 | 무엇이 나오면 반증인가 |
|---|---|---|---|
| **H1** | `llmd-slo`가 `llmd-base`보다 높다 | 예산을 보는 것이 값을 만든다는 것이 우리 전체 주장의 전제다. 이것이 안 나오면 이 워크로드에서 예산 인식 자체가 값이 없다는 뜻이고, 그러면 우리 결과도 다시 봐야 한다 | 55·70 req/s에서 `llmd-slo` − `llmd-base` ≤ 0 이고 반복 범위가 겹친다 |
| **H2** | `llmd-pred`와 `llmd-slo`의 차이가 `llmd-base`와 `llmd-pred`의 차이보다 크다 | 예측을 갖는 것보다 그 예측을 예산에 대고 쓰는 것이 큰 일이라는 예상. 두 arm의 점수 방향이 반대이기 때문이다(§2.2) | 두 차이의 부호가 같고 크기가 뒤바뀐다 |
| **H3** | 세 llm-d arm의 클래스당 유효 인스턴스 수가 우리보다 4.0(균등)에 가깝다 | 그들에게는 클래스 개념이 없고 prefix 선호만 있다. **다만 우리 세 클래스는 프롬프트 구조가 크게 달라서 prefix 선호만으로도 갈릴 수 있다 — 그래서 이것이 가장 불확실한 가설이다** | llm-d arm의 클래스당 유효 인스턴스 수가 우리(chat 2.62 / dr 2.62 / swe 2.45, EXP-54 한 시간)와 0.3 안으로 붙는다 |
| **H4** | `llmd-slo`의 거절률이 0이 아니다 | 세 클래스를 전부 sheddable로 표시하고 InferencePool 경로로 돌리므로 거절 조건이 성립할 수 있다 | 경로 B에서도 거절이 0건 |

**H3가 반증되면 우리 주장을 넓혀야 한다** — "클래스를 봐야 분리가 생긴다"에서 "요청을 갈라
놓는 신호가 무엇이든 하나 있어야 한다"로. 그 경우 우리 기여는 분리의 존재가 아니라 **분리가
수요를 따라간다는 것**(motivation §8의 축 1)으로 좁혀진다.

---

## 2. arm

같은 엔진 넷(neutral-0의 vLLM 8000~8003), 같은 워크로드, 같은 믹스. 다른 것은 라우터뿐이다.

| arm | 라우터 | 구성 |
|---|---|---|
| `fluidserve` | Llumnix 게이트웨이 + 우리 스케줄러 | 배포 바이너리 `c2d970ca`, `FS_CLASS_HARM=false FS_FORCE_MARGIN=false FS_OWN_BUDGET_GATE=false` (EXP-27 이후 모든 조건과 같다) |
| `llmd-base` | llm-d EPP + Envoy | llm-d 기본 구성 — queue-scorer + kv-cache-utilization-scorer + prefix-cache-scorer + no-hit-lru-scorer. 예측 없음 |
| `llmd-pred` | 같음 | predicted-latency-producer + prefix-cache-affinity-filter + latency-scorer + weighted-random-picker. **SLO 헤더 안 보냄** |
| `llmd-slo` | 같음 | 위 + slo-headroom-tier-filter + latency-slo-admitter, `streamingMode: true`. **SLO 헤더 보냄**, 세 클래스 전부 sheddable |

### 2.1 SLO 헤더 매핑 — 워크로드 설정은 `m1f`다 (2026-08-07 정정)

| 클래스 | `x-llm-d-slo-ttft-ms` | `x-llm-d-slo-tpot-ms` | 출처 |
|---|---|---|---|
| chat | 5,000 | 50 | `mix_short_m1_slofair.json`의 `slo` 블록 그대로 |
| deepresearch | 10,000 | 100 | 그대로 |
| **swe** | **2,500** | **52** | 같은 파일. 이 값은 **Llumnix SLO가 쓰는 것과 같다** |

**⚠ 처음에는 `m1`(balanced)으로 돌렸고 그것이 틀렸다.** m1의 swe는 (11,800, **25**)인데, 25는
30초를 나눈 값이 아니라 **EXP-16에서 잰 유휴 상태 디코드 ITL 중앙값**이다(`agent.py`의
`DEFAULT_TBT_MS` 주석). 부하가 걸리면 거의 항상 초과되므로, **전체 시간 예산을 표현할 수 없는
정책은 모든 인스턴스를 거부한다.** 2026-08-07 smoke에서 llm-d가 swe 클래스를 17~49% 거절했고,
`mix_short_m1_slofair.json`의 주석에 따르면 **Llumnix SLO가 먼저 같은 벽에 부딪혀 그 클래스의
98%를 거절했다.** m1f는 그것 때문에 만들어진 파일이다.

```
FluidServe의 명목 속도 = 30,000 / 520.2 (실측 출력 토큰) = 57.7 ms/token
m1f 의 분해            =  2,500 + 520.2 × 52 = 29,550 ms ≤ 30,000
```

**네 정책이 swe에 대해 받는 것** — 채점은 넷 다 전체 시간 30초로 같다.

| 정책 | 설정 | swe (ttft, 토큰당) | 그 값을 쓰나 |
|---|---|---|---|
| FluidServe | m1 | (11,800, 25) | **안 쓴다.** `--fluidserve-class-budgets`의 `25:e2e:30000`이 tier 25를 전체 시간으로 재정의 |
| PolyServe | m1 | (11,800, 25) | 쓴다 |
| Llumnix SLO | **m1f** | (2,500, 52) | 쓴다 |
| **llm-d** | **m1f** | **(2,500, 52)** | 쓴다 |

**논문에 적을 것 둘**: ① swe의 전체 시간 예산을 (TTFT, 토큰당) 쌍으로 분해해 넣었고 그것이
동등한 진술이 아니라는 것 — E2E 예산은 두 양을 맞바꿀 수 있는데 고정 쌍은 곡선 위 한 점만
고정한다. ② **EXP-53 표에 llm-d 열을 더하면 m1f 열이 둘(Llumnix SLO, llm-d), m1 열이 셋이 된다.**

### 2.2 `llmd-pred`와 `llmd-slo`가 왜 방향이 반대인가

`latency-scorer`는 headroom = 예산 − 예측으로 점수를 만든다. 예산 헤더가 없으면 headroom이
전부 음수가 되고, 음수 구간에서는 `least`가 강제되는데 그것은 **위반 폭이 가장 작은 곳**,
즉 **예측 지연이 가장 짧은 곳**이다 → 부하를 퍼뜨린다. 예산이 있으면 양수 구간에서 `least`가
**예산 경계에 가장 가까운 곳**이 된다 → 부하를 예산 한계까지 모은다.
근거는 `scorer/latency/plugin.go:181-247`.

---

## 3. 조건

```
정적 sweep : 15 / 25 / 35 / 45 / 50 / 55 / 60 / 70 req/s   ← EXP-53과 같은 여덟 개
믹스       : m1 (mix_short_m1_balanced.json) — EXP-53과 같다
arm        : llmd-slo 하나 (2026-08-07 사용자 결정)
반복       : 2회
조건 수    : 8 rate × 2 반복 = 16
조건당     : 예열 주행 3분 + 배수 대기(엔진이 빌 때까지) + 러너 60초 예열 + 본 측정 8분 ≈ 11분
소요       : 약 3시간
```

기존 arm(FluidServe, Llumnix, Llumnix SLO, PolyServe)은 EXP-53·EXP-57에 있으므로 다시 안
돌린다. 이 실험은 그 표에 열을 하나 더한다.

⚠ **arm을 하나로 줄이면서 포기하는 것**: llm-d 안에서 "예측을 갖는 것"과 "그 예측을 예산에
대고 쓰는 것" 중 무엇이 값을 만드는지는 못 가른다. 남는 것은 llm-d 전체 대 우리 전체의
비교이고 그것이 주 질문이다. `llmd-base` 구성 파일은 남아 있으므로 나중에 돌릴 수 있다.

⚠ **맞추는 것은 도착률뿐이다.** EXP-53은 조건마다 `--warmup-rpm 60 --warmup-sec 60`
(초당 1건 × 60초 = 60건)으로 시작했고, 우리는 그 앞에 3분 예열 주행이 붙어 45 req/s에서 약
8,100건을 더 흘린 상태로 시작한다. **예열 주행은 엔진의 prefix cache도 데운다.** 크기를
모르므로 **smoke에서 본 측정 시작 시각의 엔진별 prefix hit rate를 EXP-53의 같은 시점과
비교한다.** 크면 그때 절차를 다시 손본다.

**세션이 다르므로 인용할 때 반복 간 편차를 밝힌다.**

### 3.1 조건마다 하는 일

```
① 엔진 재시작 (neutral-0 파드 삭제 → LWS가 다시 만든다) — 지금까지와 같다
② 예측기 관련 파드 셋 재시작 → 표본과 모델이 빈 상태에서 시작
③ 예열 주행 3분 — 그 조건과 같은 도착률·믹스. 결과는 버린다
④ 배수 대기 — 엔진 넷이 전부 running=0 이고 waiting=0 일 때까지 (상한 5분)
⑤ 러너의 기존 60초 예열 + 본 측정 8분
```

**②의 "셋"이 무엇이고 왜 셋 다여야 하는가.** 예측기의 상태가 조건을 넘어 새면 **뒤쪽 rate
일수록 모델이 좋아져 rate 곡선 자체가 왜곡된다.** 상태를 들고 있는 곳이 셋이다.

| 파드 | 들고 있는 것 | 재시작 안 하면 |
|---|---|---|
| 학습 서버 | 표본 버퍼와 모델(`/models`) | 표본이 조건을 넘어 쌓인다 |
| 예측 서버 ×3 | 받아 온 모델 사본(`/local_models`), 10초 주기 갱신 | 학습 서버를 새로 띄워도 **새 모델이 생길 때까지 옛 모델을 내놓는다** |
| EPP | 표본 버퍼, 인스턴스별 실행 중 요청 큐 | 앞 조건 표본이 다음 조건 초반에 섞인다 |

→ **초기화됐다는 직접 증거**: 학습 서버 로그의 `Skipping training: only N samples (< 10)`가
조건 시작 시점에 **N=0에서 출발하는지** 확인한다. 0이 아니면 그 조건은 무효다.

---

## 4. 판정 규칙 — 결과를 보기 전에 적는다

| 관측 | 결론 |
|---|---|
| `llmd-slo` < `fluidserve`, 차이가 반복 간 편차보다 크다 | 학습한 요청 단위 예측이 우리 방식보다 낫지 않다. **다음 질문은 §12.3의 차이 여섯 중 어느 것이 원인인가**이고, 그것은 이 실험이 답하지 않는다 |
| `llmd-slo` ≈ `fluidserve` (편차 안) | **우리 기여 주장을 좁혀야 한다.** 점수가 같다면 남는 것은 입력의 세기(그들은 요청 단위 학습, 우리는 클래스 조건부 분포)와 배포 비용이다 |
| `llmd-slo` > `fluidserve` | 요청 단위 예측이 값을 갖는다. EXP-64(길이 상한)가 그 상한을 재는 실험이 되고, 우리 설계에 예측기를 붙이는 것이 다음 방향 |
| `llmd-base` ≈ `llmd-pred` | 예측 자체는 값이 없고 예산을 보는 것이 값이다 (H2 지지) |
| `llmd-pred` ≈ `llmd-slo` | 반대로 예산·거절이 값이 없다 (H2 반증) |
| llm-d arm의 클래스당 유효 인스턴스 수 ≈ 4.0 | prefix 선호로는 클래스가 안 갈린다 → 우리 주장이 강해진다 (H3 지지) |
| llm-d arm의 클래스당 유효 인스턴스 수 ≈ 우리 값 | **prefix 선호만으로 같은 분리가 나온다** → 우리 주장을 넓혀야 한다 (H3 반증) |

⚠ **45 req/s는 반복 간 편차가 10점을 넘는 구간이므로 그 하나로 판정하지 않는다.**
55와 70 req/s가 판정의 기준이다.

⚠ **`llmd-pred`·`llmd-slo`의 점수를 인용하기 전에 예측 오차를 본다.** EPP가 예측과 실측을
히스토그램으로 낸다(`inference_objective_request_ttft_seconds` 대
`..._predicted_ttft_seconds`, 토큰당 시간도 같은 쌍). **3분 예열이 모자랐다면 본 측정 구간의
앞부분이 부하 균등화이고, 그것은 이 지표에 보인다.** 안 보고 점수를 쓰면 안 된다.

⚠ **arm 사이에 조건 시작 시각의 엔진별 prefix hit rate가 비슷한지 확인한다.** 안 비슷하면
③④의 통제가 실패한 것이므로 **H3에 답하지 않는다.**

---

## 5. 실행 전에 통과해야 하는 점검

| 단계 | 확인할 것 | 상태 |
|---|---|---|
| 0 | ghcr.io에서 이미지 넷을 받는다 | ✅ **2026-08-07 통과.** EPP 23 MB / Envoy 33 MB / 학습 617 MB / 예측 617 MB, 네임스페이스 `llmd`에서 확인 |
| 1 | 예측기 둘이 뜨고 예측 서버가 학습 서버에서 모델을 받는다 | ✅ **2026-08-07 통과.** 학습 1 + 예측 3 파드 Ready, 예측 서버가 `GET /model/{ttft,tpot}/info` 200. 학습 루프 1초 주기 확인 |
| 2 | 경로 A(file-discovery)로 EPP + Envoy가 뜨고 `curl` 한 번이 엔진에 닿는다 | ✅ **2026-08-07 통과.** 응답 200, 접근 로그에 처리 엔진이 찍힌다 |
| 3 | **요청 → 엔진 연결이 100%다** (Envoy 접근 로그의 `%UPSTREAM_HOST%`) | ✅ **2026-08-07 통과. smoke 실부하에서 21,633/21,633 = 100.0%.** (사전 확인은 12/12) 엔진 넷에 3/4/3/3. **클라이언트 변경 불필요** — Envoy가 붙인 x-request-id를 vLLM이 completion id로 되돌려 주고 클라이언트가 그것을 이미 기록한다 |
| 4 | 헤더 전송을 껐을 때 `fluidserve` arm의 요청이 지금까지와 같다 | |
| 5 | 경로 B에서 **거절이 0이 아니다** | ✅ **통과.** m1f smoke에서 `ADMISSION_REJECTED` 26건 + `KV_THRESHOLD` 1건, Envoy 429 26건과 일치. 다만 45 req/s에서는 0.1%뿐이라 **거절의 크기는 60·70 req/s에서 본다**. (이전 절반 상태: 네 포트가 네 엔드포인트로 잡히고 `priority="-1"` 이 요청에 실제로 붙는다(전제 조건 성립). 실제 거절은 부하가 있어야 보이므로 smoke에서 확인) |

**단계 3이 가장 중요하다.** 여기서 100%가 안 나오면 H3에 답할 수 없고, 그러면 이 실험의
고유한 값(3·4번 질문)이 없어진다.

**단계 5가 0이면 멈춘다.** 거절이 안 되는 상태로 돌리면 `llmd-slo`가 아니라 `llmd-pred`에
tier 필터만 붙인 것을 재는 것이 된다.

---

## 6. 기록

| | |
|---|---|
| 배포·환경 계획 | `llumnix_reproduce/ms_dev/notes/llmd-baseline.md` |
| 시스템 분석 | `llumnix_reproduce/ms_dev/notes/related-works-review.md` §12 |
| 드라이버 | `k8s/exp07/run_exp66_llmd.sh` (아직 없음) |
| 연쇄 스크립트 | `/home/nxclab/tools/exp66_llmd.sh` (아직 없음) |
| 감시 | `/home/nxclab/tools/watch_experiment.sh` — 실험을 걸면 같은 턴에 건다 |


---

## 7. 결과 — rep 1 (2026-08-07 22:56 KST)

**정본은 `llumnix_reproduce/ms_dev/notes/llmd-baseline.md` §9.9·§9.10이다.** 여기에는 요약만.

offered 달성률, `exp22_fluidserve.py`, 산출물 `results/aggregate_analysis/exp66r1/`:

| req/s | 15 | 25 | 35 | 45 | 50 | 55 | 60 | 70 |
|---|---|---|---|---|---|---|---|---|
| llm-d rep1 | 100.0 | 97.8 | 95.3 | 91.5 | 89.5 | 84.3 | 76.9 | 66.1 |
| 거절률 | 0% | 0% | 0.03% | 0.5% | 1.7% | 7.5% | 14.5% | 29.3% |

EXP-53 FluidServe(n=2)와의 차이: 45에서 +1.3, **55에서 +17.9, 70에서 +14.2.**
goodput 70에서 22,308 대 17,990.

**판정 규칙 §4가 지목하는 결론은 "요청 단위 예측이 값을 갖는다"이지만, 반복 1회이고
방법론적 비대칭 넷이 남아 있어 아직 결론이 아니다**(§9.9의 목록).

**축 검사**(§9.10): 4·5는 지지되지 않고 7만 지지된다. **격차는 아직 설명되지 않았다.**

**rep 2**: 2026-08-07 23:24 KST 시작, ≈02:10 KST 종료 예정.

---

## 8. 그림과 축 3의 답 (2026-08-08)

그림은 `results/aggregate_analysis/exp66r1/`이고 **정본은 그 디렉토리의 `README.md`**이다.
다시 만드는 명령:

```bash
bash analysis_scripts/redraw_static_sweep_llmd.sh
```

`redraw_static_sweep.sh`(EXP-53/57의 정본 그림)는 건드리지 않는다 — llm-d는 반복 1회이고
세션이 다르고 예열 주행이 있어 아직 그 비교에 넣지 않는다.

### 8.1 채점 재확인

rpm 4200 조건을 원 `metrics.csv`에서 손으로 다시 계산해 equal-mix offered 66.1015를 얻었고
요약표의 66.101473과 일치한다. 창(도착 60~522초, 69.81 req/s), 클래스별 개수 합(24,795 +
4,970 + 2,484 = 32,249 = n), 스트리밍 폴백(여덟 조건 전부 0~3건)을 함께 확인했다.

**보고상의 주의 하나**: llm-d의 거절은 클라이언트가 `is_rejected`와 `is_error`를 둘 다
세우므로 `exp22_summary.csv`의 `rejected`와 `errored` 열이 같은 요청을 두 번 보고한다.
`violate_offered`는 OR이라 점수에 영향이 없고, **진짜 오류는 0건**이다. 거절 사유가
`KV_THRESHOLD`로 찍히는 것은 Llumnix용 라벨이 llm-d 거절 본문에 붙은 것이다.

### 8.2 엔진 귀속 — Envoy에서 얻는다

llm-d는 Llumnix 스케줄러를 안 거치므로 `scheduler_dispatch.log`가 비어 있다. 엔진 이름은
Envoy access log의 `%UPSTREAM_HOST%`(= `<pod-ip>:<port>`)에 있다. 새 스크립트가
`analysis_scripts/request_level/llmd_engine_map.py`이고, 시각과 소요 시간으로 1:1 매칭한다.
**여덟 조건 전부 99.88~99.97% 매칭**, 70 req/s에서 시작 시각 차이 중앙값 1.6 ms.

### 8.3 축 3 — 클래스 분리가 아니라 prefix cache다

클래스당 유효 인스턴스 수(창별 중앙값): llm-d는 15 req/s의 2.77에서 70 req/s의 3.75로
**퍼지고**, FluidServe는 45 req/s의 3.34에서 70 req/s의 2.41로 **모인다**. 점수 격차가 가장
큰 구간에서 llm-d가 가장 덜 분리한다.

70 req/s 엔진 총계: prefix hit **93.5%**(llm-d) 대 75.1%(FluidServe) 대 79.9%(Llumnix SLO).
실제 계산하는 prefill 토큰이 4,585 대 12,274 tok/s이고 decode가 20,661 대 16,562 tok/s다.
**예열 주행 때문이 아니다** — cold restart 직후 예열 주행의 첫 1분이 이미 96.2%다.

### 8.4 부수적으로 고친 것 — migration 카운터

`exp53_compare.py`의 migration 열이 `Generate rescheduling pairs`만 세고 있어서, migration을
끈 엔진에서도 스케줄러가 결정한 pair가 그대로 찍혔다(llm-d 125건). 실패 호출을 따로 세니
**llm-d 125건 전부 실패, PolyServe 77건 전부 실패, 실제로 움직인 것은 Llumnix load-balance의
23건뿐**이다. 결정·실패·성공을 셋 다 출력하도록 고쳤다.
