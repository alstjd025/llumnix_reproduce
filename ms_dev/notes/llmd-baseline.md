# llm-d를 기준선으로 세우는 계획

**만든 날**: 2026-08-07. **상태**: 계획만 있고 아무것도 배포하지 않았다.
**왜 이 문서가 따로 있나**: llm-d는 앞의 일곱 편과 달리 논문이 아니라 배포된 시스템이고,
우리 워크로드를 태우려면 클라이언트·드라이버·분석 셋을 다 손봐야 한다. 그 목록과 순서를
한곳에 둔다. 시스템 자체에 대한 분석과 우리와의 차이는
[related-works-review.md §12](related-works-review.md)가 정본이고, 여기는 **어떻게 돌릴
것인가**만 다룬다.

---

## 1. 무엇을 답하려는 실험인가

| # | 질문 | 답이 나오는 방식 |
|---|---|---|
| 1 | 요청 단위 지연 예측을 **학습해서** 쓰는 라우터가, 클래스 조건부 분포만 쓰는 우리보다 잘하는가 | `llmd-slo` 대 `fluidserve`를 같은 도착률에서 비교 |
| 2 | 그 이득이 **예측** 때문인가, 아니면 **예산을 보는 것** 때문인가 | `llmd-base`(예측 없음) → `llmd-pred`(예측만) → `llmd-slo`(예측 + 예산 + 거절)의 세 단계 |
| 3 | prefix cache 선호가 **클래스 개념 없이도** 클래스를 갈라 놓는가 | 세 arm의 창별 클래스당 유효 인스턴스 수와 엔진별 prefix hit rate |
| 4 | 확률적으로 뽑는 것(`weighted-random-picker`)이 분리를 깨뜨리는가 | 같은 지표를 우리 것과 비교 |

**3번과 4번이 이 실험에서만 나오는 것이다.** 1·2번은 점수 비교이고, 3·4번은 우리
"분리를 구성하지 않고 결과로 얻는다"는 주장이 다른 설계에서도 성립하는지를 본다.

---

## 2. 지금 환경 — 전부 2026-08-07에 직접 조회한 값이다

```
노드      nxc13 단일 노드. k3s v1.35.5+k3s1, containerd 2.2.3, Ubuntu 24.04
          호스트에 docker 없음, helm 없음, cargo 없음. python 3.12.3, go는 /home/nxclab/tools/go
GPU       8장 전부 neutral-0 파드가 점유 (nvidia.com/gpu: 8)
엔진      LeaderWorkerSet `neutral`, replicas=1 size=1 → 파드 하나(neutral-0)
          그 안에서 vLLM API 서버 4개가 8000/8001/8002/8003 을 listen
          DP_SIZE_LOCAL=4, TP_SIZE=2, LLUMNIX_ENABLE_MIGRATION=0
          vLLM 0.12.1.dev0+g4fd9d6a85, 모델 meta-llama/Meta-Llama-3.1-70B-Instruct
          엔진마다 /metrics 를 자기 포트로 낸다
서비스    gateway(8089) / scheduler(8088) / redis(6379) / neutral(headless)
CRD       leaderworkersets 만 있음 — Gateway API 없음, InferencePool 없음
레지스트리 현재 이미지는 전부 Aliyun 미러. /etc/rancher/k3s/registries.yaml 없음(미러 재작성 없음).
          호스트에서 ghcr.io/quay.io 는 401 응답(= 도달함). 실제 pull 은 아직 안 해 봤다
디스크    615 GB 여유, DiskPressure=False
```

**엔진이 노출하는 지표가 llm-d 예측기가 요구하는 여섯 개를 다 덮는다** — `vllm:kv_cache_usage_perc`,
`vllm:num_requests_running`, `vllm:num_requests_waiting`, `vllm:prefix_cache_queries_total`,
`vllm:prefix_cache_hits_total`. 이름이 예전 `gpu_cache_usage_perc`에서 바뀌었는데 EPP는
`kv-cache-usage-percentage-metric` 같은 플래그로 이름을 설정할 수 있다.

### 2.1 클라이언트가 지금 무엇을 보내는가

`workloads/swe_bench_coding/agent.py`의 `LlumnixCompletionsLLM`:

- **`POST {base_url}/v1/completions`**, 스트리밍(`stream: true`)으로 SSE를 읽는다
- 본문: `model`, `prompt`(Llama-3 템플릿을 직접 렌더링한 문자열), `max_tokens`, `temperature`,
  `top_p`, `seed`, `stop`, `stream`, 그리고 `priority_mode=deadline`이면 `priority`
- **HTTP 헤더는 하나도 안 보낸다**
- 거절 판정은 `_raise_if_llumnix_rejected` — 상태코드 429/503이면서 본문에
  `no available inference worker` 또는 `rate limit exceeded`가 들어 있을 때만
- 응답 본문의 `id`(`cmpl-<uuid>`)를 `last_request_id`에 저장한다. Llumnix 스케줄러가 같은
  uuid를 배치 로그에 찍으므로 그것이 요청 → 엔진 연결의 근거다

EPP는 `/v1/completions`의 `prompt` 필드를 파싱할 수 있다
(`pkg/epp/framework/interface/requesthandling/types.go`의 `CompletionsRequest`). **API 형태는
바꿀 필요가 없다.**

### 2.2 클래스별 예산

채점 규칙은 `analysis_scripts/request_level/slo_rule_breakdown.py`:

| 클래스 | 채점 규칙 | 워크로드 설정의 분해 (`slo` 블록) |
|---|---|---|
| chat | TTFT ≤ 5.0 s, 토큰당 ≤ 50 ms | ttft_ms 5000, tbt_ms 50, out_len 386 |
| deepresearch | TTFT ≤ 10.0 s, 토큰당 ≤ 100 ms | ttft_ms 10000, tbt_ms 100, out_len 275 |
| swe | **전체 시간 ≤ 30.0 s** | ttft_ms 11800, tbt_ms 25, out_len 728 |

**swe만 전체 시간 예산인데, 워크로드 설정에 이미 분해가 들어 있다** — 11,800 ms + 25 ms ×
728 토큰 = 30,000 ms. 이 분해는 Niyama 이식을 위해 우리가 예전에 만든 것이고, llm-d의 두
헤더에 그대로 넣으면 된다. **즉 우리가 llm-d를 위해 새로 지어내는 값이 없다.** 그래도 논문에
"swe의 전체 시간 예산을 TTFT + 토큰당으로 분해해서 넣었고, 분해에 쓴 기대 출력 길이는
728 토큰"이라고 적어야 한다.

---

## 3. 무엇을 배포하는가

llm-d를 우리 클러스터에 세우는 방법이 둘이고, **거절이 되느냐 안 되느냐가 갈린다**
(근거는 related-works-review.md §12.4).

| | 경로 A — file-discovery | 경로 B — InferencePool |
|---|---|---|
| 엔드포인트 목록 | 디스크의 YAML 파일 | InferencePool 객체(라벨 선택 + targetPorts) |
| 필요한 CRD | **없음** | GIE v1.5.0 (InferencePool, InferenceObjective) |
| 게이트웨이 provider | 불필요 (Envoy를 EPP 옆에 둔다) | 불필요 (같음) |
| helm | 불필요 | 불필요 (매니페스트를 직접 쓴다) |
| **거절** | **안 된다** — priority가 0으로 고정 | **된다** — InferenceObjective로 sheddable 표시 |
| 우리 4엔진 표현 | 주소가 같고 포트만 다른 항목 4개 | `targetPorts: [8000,8001,8002,8003]` (v1.4.0부터 최대 8개, 포트마다 별개 엔드포인트) |

**결정 (2026-08-07): A와 B를 둘 다 한다.** A로 먼저 세워 배관을 확인하고(CRD를 안 건드리므로
되돌리기가 쉽다), B로 옮겨 본 실험을 돌린다. A에서 나온 결과는 **"거절하지 않는 정책"으로만**
읽고, 거절률이 들어가는 표에는 B의 값만 쓴다.

### 3.1 파드 구성

```
llmd-router 파드
  ├ envoy         :8080  클라이언트가 여기로 붙는다. ext_proc로 EPP에 물어보고
  │                      ORIGINAL_DST로 지정된 엔진에 보낸다
  └ epp           :9002(ext_proc) :9090(metrics)
                         ghcr.io/llm-d/llm-d-router-endpoint-picker
                         환경변수 TRAINING_SERVER_URL / PREDICTION_SERVER_URL

llmd-predictor-training 파드  :8000   ghcr.io/llm-d/llm-d-latency-predictor-training-server
llmd-predictor-prediction 파드 :8001  ghcr.io/llm-d/llm-d-latency-predictor-prediction-server
```

예측기 둘은 공유 볼륨이 필요 없다(예측 서버가 학습 서버에서 모델을 HTTP로 받는다). 둘 다
CPU만 쓴다. 매니페스트는 `llm-d-latency-predictor/deploy/base/`의 것을 이미지 이름만 채워
쓰면 된다.

**엔진은 건드리지 않는다.** neutral-0도, Llumnix 스케줄러·게이트웨이도 그대로 둔다. llm-d는
같은 엔진 넷을 옆에서 가리킬 뿐이다. 그래야 arm 사이에 엔진이 같다는 것이 보장된다.

### 3.2 EPP 플러그인 구성 — `llmd-slo` 하나만 돌린다 (2026-08-07 사용자 결정)

**arm은 `llmd-slo` 하나다.** 처음 계획은 `llmd-base`(예측 없음) → `llmd-pred`(예측만) →
`llmd-slo`(예측 + 예산 + 거절) 셋이었는데, 사용자 결정으로 마지막 하나만 돌린다.

⚠ **그 대신 포기하는 것을 적어 둔다**: 셋을 다 돌리면 "예측을 갖는 것"과 "그 예측을 예산에
대고 쓰는 것" 중 무엇이 값을 만드는지가 갈렸다. 하나만 돌리면 **llm-d 안에서의 그 분해는 못
한다.** 남는 것은 llm-d 전체 대 우리 전체의 비교이고, 그것이 이 실험의 주 질문이다.
`llmd-base` 구성(`deploy/llmd/epp-config-base.yaml`)은 경로 A 배관 확인에 쓰였고 파일로 남아
있으므로, 나중에 그 분해가 필요해지면 그대로 돌릴 수 있다.

| arm | 구성 | 파일 |
|---|---|---|
| `fluidserve` | 배포 바이너리 `c2d970ca`, EXP-27 이후와 같은 플래그 | — |
| **`llmd-slo`** | 상류 `predicted-latency-slo.values.yaml`의 플러그인 묶음 그대로. `streamingMode: true` | `deploy/llmd/epp-config-slo.yaml` |
| (`llmd-base`) | 예측 없는 llm-d 기본 구성. **이번에는 안 돌린다** | `deploy/llmd/epp-config-base.yaml` |

`streamingMode: true`가 있어야 토큰당 시간 쪽 판정이 살아난다(기본값 false면 그 부분이 통째로
꺼진다). 우리 클라이언트는 전부 스트리밍이므로 조건은 만족한다.

#### 왜 `llmd-slo`가 우리와 방향이 같은가

`latency-scorer`는 headroom = 예산 − 예측으로 점수를 만든다. **예산 헤더가 있으면** 양수
구간에서 `least`가 **예산 경계에 가장 가까운 곳**을 고른다 → 부하를 예산 한계까지 모은다.
예산 헤더가 없으면 headroom이 전부 음수가 되고 `least`가 **예측 지연이 가장 짧은 곳**이 되어
부하를 퍼뜨린다. 근거는 `scorer/latency/plugin.go:181-247`. **그래서 `llmd-slo`가 best-fit
packing이고 우리 `room` 정렬과 방향이 같다.**

## 3.3 ⚠ swe의 SLO는 `m1f`를 쓴다 — 여기서 한 번 틀렸다 (2026-08-07)

**결론 먼저: llm-d는 `mix_short_m1_slofair.json`(m1f)로 돌린다.** Llumnix SLO와 같은 설정이고,
같은 이유로 그렇다.

### 무엇을 틀렸나

smoke를 `m1`(balanced)로 돌렸고, swe에 `x-llm-d-slo-tpot-ms: 25`가 갔다. llm-d는 네 엔진 전부
그 예산을 못 맞춘다고 판정해 **swe를 클래스별로 17~49% 거절**했다. 그것을 처음에는 HTTP 500
오류로 읽었고(§4의 4번), 원인을 밝힌 뒤에는 "llm-d가 큰 요청을 거절하는 성질"로 읽을 뻔했다.
**둘 다 틀렸다. 우리 설정이 그 정책을 무력화한 것이다.**

### 25 ms가 무엇인가 — 30초를 나눈 값이 아니다

`agent.py`의 `DEFAULT_TBT_MS = 25.0` 주석: **EXP-16에서 잰 B200의 순수 디코드 step ITL
중앙값**이다. 나눈 것은 반대쪽이다 — `11,800 = 30,000 − 25 × 728`. 즉 "엔진이 낼 수 있는
속도로 728토큰을 뽑고도 30초 안에 끝나려면 첫 토큰이 언제까지 나와야 하는가"다.
**유휴 상태의 값이므로 부하가 걸리면 거의 항상 초과된다.**

### 그래서 이미 m1f가 있었다

`mix_short_m1_slofair.json`의 `_comment`가 같은 일을 Llumnix SLO에서 겪었다고 적는다 —
(11,800, 25)를 주자 **그 클래스의 98%를 거절했다.** m1f는 30초 예산 안에 들어가면서 FluidServe의
명목 속도에 가장 가까운 쌍을 준다:

```
FluidServe의 명목 속도 = 30,000 / 520.2 (실측 출력 토큰) = 57.7 ms/token
m1f                    =  2,500 + 520.2 × 52 = 29,550 ms ≤ 30,000
```

그 파일이 한계도 같이 적는다 — **E2E 예산은 첫 토큰 시간과 토큰당 속도를 맞바꿀 수 있는데
고정 쌍은 그 곡선 위의 한 점만 고정하므로 동등한 진술이 아니고 될 수도 없다.** 그것이 시험
대상인 한계이고, m1f는 정적 쌍으로 가능한 한 기준선을 강하게 만들어 **남는 것이 설정이 아니라
한계이게** 한다.

### 네 정책이 swe를 어떻게 보는가

| 정책 | 워크로드 설정 | swe에 준 (ttft, 토큰당) | 그 값을 어떻게 쓰나 |
|---|---|---|---|
| FluidServe | m1 | (11,800, 25) | **25를 안 쓴다.** `--fluidserve-class-budgets`의 `25:e2e:30000`이 tier 25를 전체 시간 30초로 다시 정의한다 |
| PolyServe | m1 | (11,800, **25**) | 토큰당 예산으로 인스턴스를 거른다(`polyserve.go:194`) |
| Llumnix 부하분산 | m1 | — | SLO를 안 본다 |
| **Llumnix SLO** | **m1f** | (2,500, **52**) | 토큰당 예산으로 판정 |
| **llm-d** | **m1f** ← 이 결정 | (2,500, **52**) | `x-llm-d-slo-tpot-ms` 헤더 |

**채점은 넷 다 전체 시간 30초다**(`exp22_fluidserve.py`의 `SLO_RULES`). 다른 것은 정책이 받는
입력이지 판정 기준이 아니다.

### 값이 어떻게 전달되는가 (경로를 한 번 적어 둔다)

클라이언트는 OpenAI API의 `priority` 정수 한 칸에 두 예산을 포갠다
(`agent.py`의 `_priority()`): `priority = ttft_ms × 1000 + tbt_ms`.
스케줄러가 `types.DecodePackedSlo`로 푼다. m1f의 swe면 `2500 × 1000 + 52 = 2,500,052`이고
`TtftSloMs=2500, TpotSloMs=52`가 된다. **llm-d만 이 경로를 안 쓰고 HTTP 헤더로 받는데, 값은
같은 `slo_spec`에서 나오므로 같은 숫자가 간다.**

⚠ **인용할 때**: EXP-53 표에서 Llumnix SLO만 m1f이고 나머지 셋은 m1이다. llm-d 열을 그 표에
더하면 **m1f 열이 둘, m1 열이 셋**이 된다. 밝히지 않으면 오독된다.

---

## 3.4 예측기가 실제로 어떻게 동작하는가 (2026-08-07 smoke 실측)

**요약: 잘 돌고 있다.** 8분 조건에서 20,473개를 쌓아 2~3초마다 다시 학습했고, p90 목표에 대해
88~90%를 덮었다.

### 어디서 도는가

`UseNativeXGBoost`가 **기본 true**이므로 **EPP가 모델 파일을 받아 자기 프로세스 안에서
예측한다**(`latencypredictorclient/types.go:80`). 예측 서버 파드는 **예측을 하지 않고 모델을
배포만 한다** — smoke에서 31분간 CPU 2.5~2.8초만 썼다. **복제본을 늘리는 것은 의미가 없고,
예측 비용을 늘리는 것은 EPP 하나다**(평균 0.77 코어).

### 무엇을 얼마나 쌓는가

요청 하나가 표본 두 개를 만든다 — 첫 토큰에서 TTFT 하나, 종료에서 토큰당 시간 하나. 8분
조건(45 req/s)에서:

```
Initiating training with 20473 samples using xgboost for quantile 0.9
TTFT model trained on 10848 samples. Quantile Loss 6.1500, Coverage 88.10% (target 90%)
TPOT model trained on  9625 samples. Quantile Loss 0.5755, Coverage 89.90% (target 90%)
```

표본은 (KV 사용률 10% 구간 × prefix 일치율 0.25 구간 × 큐 구간)으로 나눈 통에 **통마다 최대
500개**씩 슬라이딩 윈도로 쌓인다. 그래서 20,473에서 더 늘지 않고 오래된 것이 밀려난다.

### 얼마나 자주, 얼마나 정확한가

- **재학습 주기**: 설정은 1초인데 학습 자체가 시간을 쓰므로 **실제 2~3초마다** 한 번.
- **정확도**: 목표가 p90이고 **coverage가 88.1%(TTFT)·89.9%(TPOT)** 다. **잘 맞춘 것이다.**
- **예측 대 실측**(EPP 히스토그램, 같은 run):

  | | 예측 평균 | 실측 평균 | 비 |
  |---|---|---|---|
  | TTFT | 171.3 ms | 143.2 ms | 1.20배 |
  | 토큰당 | 44.4 ms | 39.3 ms | 1.13배 |

  **조금 크게 예측하는 것이 정상이다** — p90을 맞추는 모델이므로 그 평균은 실측 평균보다 위에
  있어야 한다. 오차가 아니라 설계다.
- **예측 비용**: 요청당 **9.7 ms**(`inference_objective_request_ttft_prediction_duration_seconds`).
  33,078번 불렸다.

### 판정에 쓸 때 확인할 것

**조건마다 이 셋을 본다.** 하나라도 어긋나면 그 조건은 predicted-latency를 잰 것이 아니다.

1. 학습 서버가 표본 **0에서 출발**했는가 (드라이버가 검사한다)
2. **coverage가 90%에 가까운가** — 크게 낮으면 모델이 못 따라간 것이다
3. **예측 평균 / 실측 평균이 1.1~1.3 근처인가** — 1.0 아래면 과소예측이고, 훨씬 크면 부하를
   못 따라간 것이다

---

## 4. 우리 쪽에서 고쳐야 하는 것

전부 측정 경로에 있으므로 **실험이 도는 동안에는 손대지 않는다**(CLAUDE.md 함정 C).
지금은 EXP-63이 끝나 클러스터가 비어 있다.

| # | 파일 | 무엇 | 크기 |
|---|---|---|---|
| 1 | `workloads/swe_bench_coding/agent.py` | `LlumnixCompletionsLLM`에 `extra_headers` 인자를 추가하고 `.stream()`/`.invoke()`가 그것을 보내게 한다 | 약 10줄 |
| 2 | 같은 파일 | 헤더 값을 `slo_spec`에서 유도한다 — `x-llm-d-slo-ttft-ms` = `ttft_ms`, `x-llm-d-slo-tpot-ms` = `tbt_ms`. 클래스별 `slo_spec`은 mixed 워크로드가 이미 요청마다 넣고 있다 | 약 10줄 |
| 3 | 같은 파일 | `x-llm-d-inference-objective` 헤더(거절 대상 표시)를 보낸다. ~~`x-request-id`~~는 **불필요**하다 — Envoy가 만들고 vLLM이 completion id로 되돌려 준다(§4.1) | 약 3줄 |
| 4 | 같은 파일 | `_raise_if_llumnix_rejected`에 llm-d의 거절 본문(`no valid endpoint available to serve the request`)을 추가한다 | 2줄 |
| 5 | `llumnix_deploy.py` | `restart_llumnix`가 `deploy/scheduler deploy/gateway`를 이름으로 고정하고 있다. llm-d에서는 `deploy/llmd-router`를 재시작해야 한다 | 인자 하나 |
| 6 | `analysis_scripts/request_level/build_request_engine_map.py` | 스케줄러 배치 로그 대신 **Envoy 접근 로그**를 읽는 경로를 추가한다. 조인 키는 `metrics.csv`의 request id에서 `cmpl-`를 뗀 것 | 새 함수 |
| 7 | `k8s/exp07/run_exp66_llmd.sh` (신규) | arm 정의와 조건 루프. 기존 `run_exp27_mixsweep.sh`를 본뜨되 `set_scheduler_profiling.py` 대신 EPP ConfigMap 교체 | 신규 |

### 4.1 요청이 어느 엔진에서 처리됐는지 알아내는 방법 — 클라이언트 변경이 필요 없다

지금은 클라이언트 `metrics.csv`와 스케줄러 `scheduler_dispatch.log`를 요청 번호로 맞춰
붙인다. llm-d에는 그 로그가 없다. **대신 Envoy 접근 로그가 더 나은 것을 준다** —
`%UPSTREAM_HOST%`가 실제로 요청을 처리한 `IP:포트`이고 `%REQ(X-REQUEST-ID)%`가 요청 번호다.
한 줄에 둘 다 있으므로 로그 두 개를 맞춰 붙일 필요가 없다.

**2026-08-07에 확인한 것: 그 요청 번호가 우리 클라이언트가 이미 기록하는 값과 같다.**
Envoy가 `use_remote_address: true`이므로 들어오는 요청에 x-request-id를 새로 만들어 붙이고,
vLLM이 그 값을 자기 completion id로 쓴다. 그래서

```
접근 로그:  ...  2deda94b-b6e0-4216-a91d-b6ec12151553  200  10.42.0.150:8001  ...
응답 본문:  {"id": "cmpl-2deda94b-b6e0-4216-a91d-b6ec12151553", ...}
```

이고, 클라이언트의 `last_request_id`가 이미 그 본문 `id`를 저장한다. **`cmpl-` 접두사만 떼면
조인된다.** 실측 12건에서 100%였고 엔진 넷에 3/4/3/3으로 분산됐다.

⚠ **Envoy의 파일 접근 로그는 약 10초 버퍼링한다.** 요청을 보낸 직후에 읽으면 아직 안 쓰여
있어서 조인 성공률이 0%로 보인다(2026-08-07에 실제로 그렇게 보였다). **조건이 끝난 뒤
로그를 복사하기 전에 줄 수가 안정될 때까지 기다리거나, Envoy에 `--file-flush-interval-msec`를
낮춰 준다.** 이것을 안 하면 조건 끝부분의 요청들이 통째로 빠진다.

### 4.2 거절 대상 표시를 어떻게 정할 것인가

`latency-slo-admitter`는 priority가 음수인 요청만 거절한다. priority는 `InferenceObjective`
객체에서 오고 요청이 `x-llm-d-inference-objective` 헤더로 어느 객체를 쓸지 지정한다.

**계획: `sheddable`(priority = -1) 객체 하나를 만들고 세 클래스가 전부 그것을 쓴다.**
우리 시스템은 모든 요청을 거절할 수 있으므로 그것과 맞추는 설정이다. **이 선택을 논문에
적는다** — 표시를 안 하면 거절률이 0%로 나오는데 그것은 정책의 성질이 아니라 우리 설정의
결과다.

---

## 5. 예측기 학습을 어떻게 다룰 것인가 — 이 실험 고유의 위험

예측기는 라이브 트래픽으로 온라인 학습한다. 표본은 **요청 하나당 두 개**(첫 토큰에서 TTFT
하나, 요청 종료에서 토큰당 시간 하나)이고, 배포용 설정 기준으로 **최소 100개**가 모이면
학습을 시작하고 **1초마다** 다시 학습한다.

**문제는 우리 sweep이 조건마다 파드를 재시작한다는 것이다.** 예측기를 같이 재시작하면 매
조건이 빈 모델에서 시작하고, 예측이 없는 동안 `latency-scorer`는 KV 여유·큐 길이·prefix
일치율의 가중합으로 떨어진다(`scorer/latency/plugin.go:332-376`). **그 상태는 사실상 부하
균등화이므로, 그 구간을 포함해서 재면 predicted-latency를 잰 것이 아니다.**

### 5.1 결정 — 조건마다 초기화하되 예열 주행을 먼저 돌린다 (2026-08-07 사용자 제안)

모델을 조건 사이에 살려 두면 조건 순서가 결과에 영향을 준다. 그래서 **살려 두지 않고**,
조건마다 이렇게 한다.

```
① 엔진 재시작 (지금과 같다: neutral-0 파드 삭제 → LWS가 다시 만든다)
② 예측기 관련 파드 셋 재시작 → 모델과 표본이 빈 상태에서 시작 (어느 셋인지는 §7.2)
③ 예열 주행 3분  — 그 조건과 같은 도착률, 같은 믹스로 부하를 건다. 여기서 나온 표본으로
                   예측기가 학습한다. 이 구간의 결과는 버린다
④ 배수 대기      — 엔진 넷이 전부 num_requests_running = 0 이고
                   num_requests_waiting = 0 이 될 때까지 기다린다 (상한 5분)
⑤ 본 측정 8분
```

**③에서 표본이 모자랄 일은 없다.** 45 req/s × 180초 = 요청 약 8,100건 → 표본 약 16,200개이고
최소 학습 표본은 100개, 통마다의 상한이 500개다.

**④를 3분 고정이 아니라 "엔진이 빌 때까지"로 한 이유**: 고정 시간은 그 시간이 충분한지를
확인하지 못한다. 엔진 지표를 직접 보면 남은 요청이 실제로 0인지를 확인할 수 있고, 대개
3분보다 훨씬 빨리 끝난다. 상한 5분을 두어 엔진 하나가 멈춘 경우에 연쇄가 매달리지 않게 한다.

### 5.2 ⚠ 예열 주행은 엔진의 prefix cache도 데운다 — 그래서 모든 arm에 똑같이 준다

**이것이 이 설계에서 가장 놓치기 쉬운 것이다.** 지금 sweep은 조건마다 엔진을 재시작하므로
**모든 조건이 빈 prefix cache에서 시작**하고, 그 뒤 60초 예열(`--warmup-rpm 60
--warmup-sec 60`)을 모든 arm이 똑같이 받는다. 여기에 llm-d arm에만 3분 예열 주행을 더하면
**llm-d만 prefix cache가 더 데워진 엔진에서 측정된다.** prefix 일치율은 llm-d의 라우팅
입력이자 우리 분리 지표의 관측값이므로, 그 비대칭은 3번 질문(prefix 선호가 클래스를 갈라
놓는가)의 답을 직접 오염시킨다.

→ **예열 주행 3분 + 배수 대기를 `fluidserve`를 포함한 모든 arm에 똑같이 준다.**
예측기가 없는 arm에서는 학습할 것이 없으니 낭비처럼 보이지만, **그 3분이 만드는 것은
예측기 모델만이 아니라 엔진의 상태**이고 그것을 맞추는 것이 목적이다.

→ **그리고 조건 시작 시각의 엔진별 prefix hit rate를 기록해서 arm 사이에 실제로 비슷한지
확인한다.** 안 비슷하면 이 통제가 실패한 것이므로 3번 질문에 답하지 않는다.

⚠ **이 예열 주행 때문에 EXP-66의 `fluidserve` 값은 EXP-53·EXP-57의 `fluidserve` 값과 측정
절차가 다르다.** 같은 표에 놓을 때 그 사실을 적고, EXP-66 안에서의 arm 간 비교를 주 결과로
삼는다.

### 5.3 비용

조건당 약 4분이 늘어난다(예열 3분 + 배수 대개 1분 미만). 32조건이면 약 2.1시간이고,
전체는 약 6시간 → **약 8시간**이 된다. 한 번의 야간 실행으로 들어간다.

### 5.4 그래도 확인해야 하는 것

**예측이 언제부터 쓸 만해지는지를 매 조건에서 기록한다.** EPP가 예측과 실측을 각각
히스토그램으로 낸다 — `inference_objective_request_ttft_seconds` 대
`inference_objective_request_predicted_ttft_seconds`, 토큰당 시간도 같은 쌍.
**이 값을 안 보고 점수를 인용하지 않는다.** 3분 예열이 모자랐다면 본 측정 구간의 앞부분이
부하 균등화이고, 그것은 이 지표에 보인다.

---

## 5.5 무엇이 수집되고, 그 지표가 맞는가 (2026-08-07 검증)

§32(기록된 TBT가 실제의 1/1.92였던 것)와 같은 계열의 오류가 라우터를 바꾸면서 다시 생길 수
있으므로, **평가에 쓰는 양이 만들어지는 경로를 하나씩 확인했다.**

### 5.5.1 판정에 쓰는 양은 라우터와 무관한 곳에서 만들어진다

달성률·goodput·거절률은 전부 **클라이언트가 기록한 `metrics.csv`**에서 나오고, 그 기록을
만드는 코드(`workloads/swe_bench_coding/agent.py`의 `LlumnixCompletionsLLM`)는 arm에 상관없이
같다. 파생 TBT의 식 `(e2e − ttft) / (output_tokens − 1)`도 분석 쪽(§32의 수정)에 있고 라우터를
안 본다. **그러므로 라우터 교체가 지표 정의를 바꾸지 않는다.** 다만 그 전제가 실제로 성립하는지
셋을 쟀다.

### 5.5.2 확인 1 — Envoy가 스트리밍을 버퍼링하지 않는다

버퍼링하면 첫 토큰이 늦게 도착해 TTFT가 e2e에 가까워지고 토큰 간 간격이 0으로 무너진다.
같은 프롬프트를 엔진에 직접, 그리고 Envoy를 거쳐 보내고 청크 간격을 비교했다.

| | 청크 간격 중앙값 | `(e2e − ttft)/(n−1)` | 청크 수 |
|---|---|---|---|
| 엔진 직접 | 16.58 ms | 16.62 ms | 64 |
| Envoy 경유 | 16.32 ms | 16.41 ms | 64 |

**간격이 같고 청크 수도 같다.** Envoy는 SSE 청크를 합치지도 미루지도 않는다. 그리고 이 표는
**§32의 수정식이 새 경로에서도 실제 청크 간격과 일치한다는 것**을 같이 보여준다(16.41 대 16.32).

### 5.5.3 확인 2 — 프록시 자체의 비용이 비교를 왜곡하지 않는다

TTFT를 세 경로에서 각각 6회 쟀다(부하 없음).

| 경로 | 1회차 | 이후 5회 중앙값 | 엔진 직접 대비 |
|---|---|---|---|
| 엔진 직접 | 29.9 ms | **23.0 ms** | — |
| Envoy (llm-d) | 3107.5 ms | **26.0 ms** | +3.0 ms |
| Llumnix 게이트웨이 | 2600.5 ms | **28.7 ms** | +5.7 ms |

**첫 요청이 비싼 것은 연결 수립 비용이고 두 프록시 모두에 걸린다.** 정상 상태에서는 Envoy가
우리 게이트웨이보다 2.7 ms 빠르므로, **프록시 비용 때문에 llm-d가 유리하거나 불리해지지
않는다.** ⚠ 이 값은 **부하가 없을 때**의 것이다. 부하가 걸리면 우리 게이트웨이는 요청을
큐에 붙드는데(PEND) 그것은 설계이지 비용이 아니고, llm-d 쪽은 `llmd-pred`·`llmd-slo`에서
예측 왕복이 더해진다. **그 비용은 `inference_extension_plugin_duration_seconds`로 잰다.**

### 5.5.4 확인 3 — 파생 TBT의 분모가 맞는가

`output_tokens`는 `count_tokens(response_text)`이고 그 함수는 **tiktoken `cl100k_base`**를
쓴다 — 모델은 Llama-3이므로 토크나이저가 다르다. 분모가 틀리면 달성률이 그 비율만큼 통째로
틀리므로 실제로 쟀다. 스트리밍에서 엔진은 토큰 하나당 청크 하나를 보내므로 **청크 개수가 실제
토큰 수**다(코드 주석의 독립 실측으로 서버 보고값의 0.991배).

| 출력 성격 | 청크 수(= 실제 토큰) | cl100k 개수 | 비 |
|---|---|---|---|
| chat 유사(대화) | 168 | 168 | **1.000** |
| deepresearch 유사(보고서) | 200 | 200 | **1.000** |
| swe 유사(코드) | 200 | 200 | **1.000** |

**세 종류 전부 정확히 일치한다.** 클래스마다 다른 배수로 틀어지는 일도 없다. 표본이 셋이므로
"어긋남을 못 찾았다"이지 "0임을 증명했다"는 아니지만, 코드 주석의 독립 실측과 방향이 같다.

### 5.5.5 arm에 따라 수집처가 달라지는 것

| 무엇 | `fluidserve` arm | llm-d arm |
|---|---|---|
| 달성률·goodput·거절률·TTFT·e2e·토큰 수 | 클라이언트 `metrics.csv` | **같다** |
| 파생 TBT | 분석에서 `(e2e−ttft)/(out−1)` | **같다** |
| 엔진별 KV·큐·preemption·prefix hit | 엔진 Prometheus (neutral-0:8000~8003) | **같다** |
| 요청이 어느 엔진에서 처리됐나 | 스케줄러 배치 로그 + 요청 번호 조인 | **Envoy 접근 로그 한 줄** (§4.1) |
| 라우터 내부 상태 | 스케줄러 Prometheus (`scheduler_fluidserve_*`) | **EPP Prometheus** — 아래 |
| 게이트웨이 큐 | `gateway_pending_requests` | 해당 없음(Envoy는 붙들지 않는다) |

**llm-d arm에서는 `scheduler_*`·`gateway_*` 계열이 존재하지 않는다.** 수집기
(`llumnix_metrics.py`)가 못 긁으면 그 대상을 건너뛰므로 run이 죽지는 않지만, **EPP를 긁는
대상을 새로 넣어야 한다.** EPP가 내는 것 중 쓸 것:

| 지표 | 무엇에 쓰나 |
|---|---|
| `inference_objective_normalized_time_per_output_token_seconds` | **llm-d 자신이 잰 토큰당 시간.** 우리 파생 TBT와 대조하는 독립 근거가 공짜로 생긴다 — §32가 요구하는 계층 간 대조가 이 arm에서는 자동이다 |
| `inference_objective_request_duration_seconds` | 요청 e2e. 클라이언트 값과 대조 |
| `inference_extension_plugin_duration_seconds` | 플러그인별 소요. **예측 왕복 비용이 여기 보인다** |
| `inference_extension_prefix_indexer_hit_ratio` | EPP가 보는 prefix 일치율 |
| `inference_pool_per_pod_queue_size`, `inference_pool_average_kv_cache_utilization` | EPP가 보는 엔진 상태. 엔진 Prometheus 값과 대조하면 EPP의 관측이 맞는지 알 수 있다 |

⚠ **`inference_objective_request_predicted_ttft_seconds` 계열은 `llmd-base`에는 없다** —
predicted-latency 플러그인이 안 실려서다. `llmd-pred`·`llmd-slo`에서 생기고, §5.4가 요구하는
예측 오차 확인이 그것으로 이루어진다.

### 5.5.6 아직 안 고친 것 — 거절이 오류로 기록된다

클라이언트의 `_raise_if_llumnix_rejected`는 상태코드 429/503이면서 본문에
`no available inference worker` 또는 `rate limit exceeded`가 있을 때만 거절로 센다. llm-d의
거절은 **503에 `no valid endpoint available to serve the request`**이므로 지금 그대로 돌리면
**거절이 거절이 아니라 오류로 집계된다.** 그러면 `admitted` 분모가 틀리고 거절률이 0으로
보인다. §4의 4번 항목이 그것이고, 단계 5 전에 반드시 넣는다.

---

## 5.6 smoke 결과 — 2026-08-07 (m1f, 45 req/s)

두 번 돌렸다. 첫 판은 `m1`이라 무효이고(§3.3), 아래는 `m1f`로 다시 돌린 것이다.
결과 디렉토리 `260807_0338_exp66smoke_llmdslo_m1f_rpm_2700`.

| 확인할 것 | m1 (무효) | **m1f** |
|---|---|---|
| swe 거절 | django 49% / matplotlib 29% | **0.1% (1건)** |
| 거절이 `is_rejected`로 | 130건만, 4,895건은 오류로 샘 | **26건 전부 집계**, Envoy 429 26건과 일치 |
| Envoy가 본 요청 | 45,847 (그중 21,660이 `/metrics`) | **21,662, 전부 `/v1/completions`** |
| HTTP 500 | 4,895 | **1** |
| 요청 → 엔진 연결 | — | **21,633 / 21,633 = 100.0%** |
| 예측기 초기화 | 확인됨 | 확인됨 (표본 0에서 출발) |
| 조건당 소요 | 28분 | **27.5분** |

### 남은 5.1%는 arm의 성질이 아니다

`server terminated during streaming` 1,103건(5.1%)이 남는데, **같은 도착률의 기존 arm과 같은
수준이다.** 클라이언트가 이 부하에서 스트림을 끊는 것이고 라우터와 무관하다.

| arm | 스트림 종료 |
|---|---|
| PolyServe (EXP-53) | 29.0% |
| Llumnix 부하분산 | 6.7% |
| Llumnix SLO | 5.5% |
| **FluidServe** | **5.2%** |
| **llm-d slo (EXP-66)** | **5.1%** |

### 눈에 띄는 것 둘

1. **엔진 사용이 고르지 않다** — 6,475 / 6,259 / 4,598 / 4,301, 최대/최소 **1.51배**. 네 엔진을
   다 쓰지만 균등하지 않다. `weighted-random-picker`와 prefix 선호가 만든 것으로 보이고,
   **§1의 질문 3·4에 답할 재료**다. 조건 하나로는 판단하지 않는다.
2. **45 req/s에서는 거절이 0.1%뿐이다.** EXP-53에서 FluidServe가 같은 도착률에서 7.3%를
   거절한 것과 대비된다. 부하가 모자란 것인지 정책 차이인지는 **60·70 req/s에서 갈린다** —
   원래 판정 기준으로 정한 구간과 같다.

### ⚠ 소요 시간 정정

조건당 **27.5분**이다(계획서에 11분으로 적었던 것은 틀렸다 — 엔진 기동만 8분이다).

```
반복 1회 (8 rate)  ≈ 3.7시간
반복 2회까지       ≈ 7.3시간
```

---

## 6. 단계와 점검 지점

각 단계는 **다음 단계로 넘어가기 전에 확인할 것**을 갖는다. 확인이 안 되면 거기서 멈춘다.

| 단계 | 하는 일 | 넘어가기 전에 확인할 것 |
|---|---|---|
| **0** | ghcr.io에서 이미지 넷을 실제로 pull 한다 (EPP, envoy, 예측기 둘) | ✅ **2026-08-07 통과.** EPP 23 MB / Envoy 33 MB / 학습 617 MB / 예측 617 MB, 합계 약 1.3 GB. 네임스페이스 `llmd`에서 확인 |
| **1** | 예측기 둘을 Deployment로 띄운다 | ✅ **2026-08-07 통과.** `deploy/llmd/predictor.yaml`. 학습 1 + 예측 3 파드 전부 Ready. **모델 동기화 링크가 실제로 돈다** — 예측 서버들이 `GET /model/{ttft,tpot}/info` 200을 받는다. 학습 루프가 1초마다 돌며 `Skipping training: only 0 samples (< 10)`을 찍는다(= 설정이 먹었고 첫 학습 문턱이 10개) |
| **2** | 경로 A(file-discovery)로 EPP + Envoy를 띄우고 `llmd-base` 구성으로 `curl` 한 번 | ✅ **2026-08-07 통과.** `deploy/llmd/{router,epp-config-base}.yaml`. 응답 200, 접근 로그에 `10.42.0.150:8001`. **file-discovery와 core-metrics-extractor 조합이 실제로 동작한다** — §10의 1번이 닫혔다 |
| **3** | 요청 → 엔진 연결 | ✅ **2026-08-07 통과, 100%.** 12건 전부 조인, 엔진 넷에 3/4/3/3으로 분산. **클라이언트 변경이 필요 없다** — §4.1 참조 |
| **4** | `llmd-slo` 구성이 예측을 실제로 내는가 | ✅ **2026-08-07 통과.** `inference_objective_request_predicted_{ttft,tpot}_seconds_count` 와 실측 `..._{ttft,tpot}_seconds_count` 가 함께 찬다. 학습 서버가 요청당 표본 2개를 받는다 |
| **5** | 경로 B로 옮긴다 (GIE CRD 설치, InferencePool + InferenceObjective) | ⚠ **절반 통과 (2026-08-07).** 파드 하나의 네 포트가 `neutral-0-rank-0`~`3` 네 엔드포인트로 잡히고, **`inference_objective_request_total{priority="-1"}` 로 sheddable 표시가 실제로 도달한다** = 거절의 전제 조건 성립. **실제 거절이 나는지는 부하가 있어야 보이므로 smoke에서 확인한다** |
| **6** | smoke 한 rate → 본 sweep | §7.1 그리고 §7의 판정 규칙 |

**단계 3이 가장 중요한 점검이다.** 여기서 요청 → 엔진 연결이 100%가 아니면 3·4번 질문에
답할 수 없고, 그러면 이 실험의 고유한 값이 없어진다.

---

## 7. 본 실험 설계 — 실행 전에 판정 규칙을 적는다

```
정적 sweep : 15 / 25 / 35 / 45 / 50 / 55 / 60 / 70 req/s  (= EXP-53과 같은 여덟 개)
믹스       : m1 (mix_short_m1_balanced.json) — EXP-53과 같다
arm        : llmd-slo
반복       : 2회
조건 수    : 8 rate × 2 반복 = 16
소요       : 조건당 약 15분(§5.1) → 약 4시간
```

**rate를 EXP-53과 같게 맞추는 이유는 그 표에 이미 FluidServe·PolyServe·Llumnix SLO·Llumnix가
있기 때문이다.** 그래서 이 실험은 그 표에 열을 하나 더하는 형태가 된다.

**맞추는 것은 도착률뿐이다 (2026-08-07 사용자 결정).** 한때 예열 주행 뒤에 엔진을 한 번 더
재시작해서 EXP-53과 **절차까지** 같게 만드는 안을 적었는데, 요구된 것은 도착률 일치이므로
그 단계는 뺀다. 조건마다 엔진 재시작 한 번(약 4분 × 16조건 ≈ 1시간)을 아낀다.

```
① 엔진 재시작 + 예측기 3종 재시작 (§7.2)
② 예열 주행 3분 (같은 도착률·믹스) — 예측기가 학습한다. 결과는 버린다
③ 배수 대기 — 엔진 넷이 전부 running=0, waiting=0 일 때까지 (상한 5분)
④ 러너의 기존 60초 예열 + 본 측정 8분
```

⚠ **그래서 남는 비대칭을 밝혀 둔다.** EXP-53은 조건마다 `--warmup-rpm 60 --warmup-sec 60`,
즉 **초당 1건 × 60초 = 60건**으로 측정을 시작했다(`run_config.json`으로 확인). 우리는 그 앞에
3분 예열 주행이 붙으므로 45 req/s에서 **약 8,100건**을 더 흘린 상태로 시작한다. 예열 주행은
예측기 모델만이 아니라 **엔진의 prefix cache**도 데우고, prefix 일치율은 llm-d의 라우팅 입력
이자 우리 분리 지표의 관측값이다.

**크기를 모르므로 smoke에서 잰다** — 본 측정 시작 시각의 엔진별 prefix hit rate를 기록해
EXP-53 run의 같은 시점 값과 비교한다. 작으면 그대로 가고, 크면 그때 엔진 2차 재시작을 다시
넣는다. **추측으로 정하지 않는다.**

### 7.2 무엇을 재시작해야 초기화인가 — 셋 다여야 한다

예측기의 상태가 조건을 넘어 새면 뒤쪽 rate일수록 모델이 좋아져서 rate 곡선 자체가 왜곡된다.
**상태를 들고 있는 곳이 셋이고 하나라도 빠지면 샌다.**

| 파드 | 들고 있는 것 | 재시작 안 하면 |
|---|---|---|
| **학습 서버** (`llmd-training-server`) | 표본 버퍼와 만들어진 모델(`/models`, emptyDir) | 표본이 조건을 넘어 계속 쌓인다 |
| **예측 서버** ×3 (`llmd-prediction-server`) | 학습 서버에서 받아 온 모델 사본(`/local_models`), 10초마다 갱신 | 학습 서버를 새로 띄워도 **새 모델이 생길 때까지 옛 모델을 계속 내놓는다** |
| **EPP** (`llmd-router`) | 학습 표본을 모아 보내는 버퍼, 인스턴스별 실행 중 요청 큐 | 앞 조건의 표본이 다음 조건 초반에 섞인다 |

→ **드라이버가 셋을 다 재시작하고, 초기화됐다는 직접 증거를 확인한다**: 학습 서버 로그의
`Skipping training: only N samples (< 10)`가 조건 시작 시점에 **N=0에서 출발하는지** 본다.
0이 아니면 앞 조건이 남아 있는 것이므로 그 조건은 무효다.

**판정 규칙 (결과를 보기 전에 적는다):**

| 관측 | 결론 |
|---|---|
| `llmd-slo`가 EXP-53의 FluidServe보다 반복 간 편차보다 크게 낮다 | 학습한 요청 단위 예측이 우리 방식보다 낫지 않다. **어느 차이가 원인인지는 이 실험이 답하지 않는다**(related-works-review §12.3의 여섯) |
| `llmd-slo` ≈ FluidServe (편차 안) | **우리 기여 주장을 좁혀야 한다.** 남는 것은 입력의 세기와 배포 비용이다 |
| `llmd-slo` > FluidServe | 요청 단위 예측이 값을 갖는다. EXP-64가 그 상한을 재는 실험이 되고, 우리 설계에 예측기를 붙이는 것이 다음 방향 |
| `llmd-slo`가 Llumnix SLO보다 높고 PolyServe보다 높다 | 예산 인식 라우팅이 이 워크로드에서 값을 갖는다는 것을 우리 것 말고도 하나 더 확인한 셈 |
| llm-d의 클래스당 유효 인스턴스 수 ≈ 4.0 | prefix 선호로는 클래스가 안 갈린다 → 우리 주장이 강해진다 |
| llm-d의 클래스당 유효 인스턴스 수 ≈ 우리 값 | **prefix 선호만으로 같은 분리가 나온다** → 주장을 "클래스를 봐야 한다"에서 "요청을 갈라 놓는 신호가 하나 있어야 한다"로 넓혀야 한다 |

⚠ **45 req/s는 반복 간 편차가 10점을 넘는 구간이므로 그 하나로 판정하지 않는다.**
55와 70 req/s가 판정의 기준이다.

⚠ **점수를 인용하기 전에 §5.4의 예측 오차를 본다.**

⚠ **EXP-53과 세션이 다르다.** 총계 지표는 세션을 잘 건너가지만 엔진별 사건은 안 건너간다
(CLAUDE.md의 반복 규칙). 인용할 때 밝힌다.

### 7.3 그 전에 smoke — 한 rate

**본 sweep 전에 45 req/s 한 조건을 돌린다.** 확인할 것:

1. `metrics.csv`가 정상이고 거절이 `is_rejected`로 집계되는가(오류가 아니라)
2. 요청 → 엔진 연결이 100%인가
3. 예측 오차 히스토그램이 채워지고 예열 3분이 충분한가
4. 엔진 넷에 다 분산되는가
5. 조건당 소요가 계산대로인가(약 11분)
6. **예측기 셋이 실제로 초기화되는가** — 학습 서버가 N=0에서 출발하는가 (§7.2)
7. **본 측정 시작 시각의 엔진별 prefix hit rate가 EXP-53의 같은 시점과 얼마나 다른가** —
   위의 비대칭 크기를 여기서 처음 잰다

**거절이 0건이면 45 req/s가 거절이 나올 부하가 아닌 것일 수 있다** — 그 경우 60이나 70에서
한 번 더 본다. 거절이 어느 부하에서도 0이면 그때 멈추고 원인을 찾는다.

## 8. 파일을 어디에 두는가

| 경로 | 무엇 | git |
|---|---|---|
| `lib/llm-d/` | 상류 저장소 클론 넷(llm-d-router, llm-d, gateway-api-inference-extension, llm-d-latency-predictor) | 추적 안 함. `lib/sglang`과 같은 방식 |
| `deploy/llmd/` | 우리가 쓴 매니페스트. `predictor.yaml`(작성됨), EPP+Envoy와 arm별 ConfigMap은 아직 | **추적한다** |
| `ms_dev/notes/llmd-baseline.md` | 이 문서 | 추적 |
| `Agent_applications/.../experiments/EXP-66_llm-d-baseline.md` | 실험 기록. 실행 **전에** 가설과 판정 규칙을 적는다 | 추적(별도 저장소) |
| `Agent_applications/.../k8s/exp07/run_exp66_llmd.sh` | 드라이버 | 추적(별도 저장소) |
| `/home/nxclab/tools/exp66_llmd.sh` | 연쇄 스크립트 | 추적 안 함 |

상류 클론을 `lib/llm-d/`에 두는 이유: `lib/sglang`이 이미 그 자리에 있고 같은 성격이다.
llm-d 저장소들은 각자 `go.mod`를 가진 별도 모듈이므로 `go build ./cmd/scheduler` 같은
패키지 지정 빌드에는 영향이 없다.

---

## 9. 기존 환경에 무엇이 영향을 받는가 — 시작 전에 확인한 것

**질문은 "지금 쿠버네티스·도커 환경을 끄고 하는 것인가, FluidServe 쪽이 달라지거나 코드가
유실될 여지가 있는가"였다. 답부터: 아무것도 끄지 않고, 유실 위험이 하나 있었는데 막았다.**

### 9.1 끄는 것은 없다

- **호스트에 docker가 아예 없다.** k3s가 containerd를 직접 쓴다. 끌 docker가 없다.
- **Llumnix 스택(스케줄러·게이트웨이·redis)을 내리지 않는다.** llm-d는 같은 네임스페이스에
  파드를 **더 얹는** 것이고, Service 이름과 포트가 겹치지 않는다(llm-d는 자기 Service를
  쓰고, gateway 8089 / scheduler 8088 / redis 6379는 그대로다).
- **엔진(neutral-0)을 바꾸지 않는다.** llm-d는 같은 vLLM 넷을 IP:포트로 가리킬 뿐이다.
  그래야 arm 사이에 엔진이 같다는 것이 보장된다.
- **부하는 한 번에 하나의 라우터에만 간다.** 러너의 `--server-base-url`이 어디를 가리키냐로
  정해지고, 안 쓰는 쪽은 요청을 안 받으므로 아무 일도 하지 않는다.

### 9.2 자원은 충분하다 (2026-08-07 조회)

```
CPU     72 코어 중 요청 38.2 (53%)   → llm-d 파드 넷은 전부 CPU 전용, 여유로 들어간다
메모리  2,373 GiB 중 요청 263 (11%)
GPU     8/8 전부 neutral-0            → llm-d는 GPU를 안 쓴다
파드    110 상한, 지금 8개
디스크  615 GB 여유, DiskPressure=False
```

### 9.3 유실 위험이 하나 있었다 — 막았다

**배포 중인 스케줄러 바이너리 `bin/scheduler-exp07`(md5 `c2d970ca`)의 사본이 어디에도
없었다.** `bin-backup/`에 이름이 가장 가까운 `scheduler-exp07.pre-exp59`는 `cf74fe98`,
즉 **그 앞 판**이다. 소스에서 다시 빌드할 수는 있지만 지금 배포된 것과 바이트가 같다는
보장은 없다.

→ **2026-08-07에 백업했다**: `bin-backup/scheduler-exp07.c2d970ca-pre-exp66`,
`bin-backup/gateway-exp10.0efb664a-pre-exp66`. 둘 다 md5로 대조했다. 기존 파일은 하나도
덮어쓰지 않았다.

### 9.4 FluidServe 쪽이 달라지는 것 — 둘 있고 둘 다 통제한다

**(1) 클라이언트가 공유된다.** `make_llm`과 `LlumnixCompletionsLLM`은 세 워크로드
(sharegpt=chat, searcharena=deepresearch, codingagent=swe)가 전부 쓰고, arm과 무관하게 같은
코드다. 여기에 llm-d용 헤더를 그냥 넣으면 **FluidServe arm의 요청도 달라진다.**

→ **헤더 전송을 기본 꺼짐으로 두고 llm-d arm에서만 켠다.** 꺼져 있으면 지금까지와
바이트 단위로 같은 요청이 나가야 하고, 그것을 smoke에서 대조한다.

**(2) 예열 주행이 추가된다**(§5.1). 이건 통제할 수 없고 통제해서도 안 된다 — 모든 arm에
똑같이 줘야 엔진 상태가 같아지기 때문이다(§5.2).

→ **그래서 EXP-66의 `fluidserve` 값은 EXP-53·EXP-57의 값과 측정 절차가 다르다.**
같은 표에 놓을 때 그 사실을 적는다.

### 9.5 나머지 변경은 기존 경로를 안 건드리는 형태로 한다

| 파일 | 어떻게 |
|---|---|
| `llumnix_deploy.py` | `restart_llumnix`에 재시작 대상 이름을 **인자로** 받게 한다. 기본값은 지금 그대로 `deploy/scheduler deploy/gateway` |
| `build_request_engine_map.py` | 스케줄러 로그를 읽는 기존 함수는 그대로 두고 Envoy 로그용 함수를 **추가**한다 |
| GIE CRD (경로 B) | 릴리스 번들 전체가 아니라 **CRD yaml만** 적용한다. 번들에는 우리가 원하지 않는 Deployment·RBAC·네임스페이스가 들어 있다. 새 API 그룹이므로 `leaderworkersets`와 겹치지 않는다 |
| `lib/llm-d/` | **git 서브모듈로 만들지 않는다.** `lib/sglang`은 서브모듈(`.gitmodules`에 있다)이라 커밋이 인덱스에 박히는데, llm-d 클론은 참고용이므로 평범한 클론 + `.gitignore` 한 줄로 둔다 |

### 9.6 지금 커밋 안 된 것

`docs/claude-md-restructure` 브랜치에 **문서 13개가 수정됨 + `llmd-baseline.md` 신규**
상태로 남아 있다(용어 정리와 이 계획서). **클러스터를 건드리기 전에 커밋하는 것을 권한다** —
실험 중에 뭔가 잘못돼 되돌릴 때 오늘 쓴 것이 같이 날아가지 않게.

---

## 9.7 결과를 읽는 틀 — 차이를 만들 수 있는 축 여덟 (2026-08-07)

결과를 "우리가 이겼다/졌다"로 읽으면 아무것도 배우지 못한다. **여덟 축 중 어디서 갈렸는지**를
묻는다. 1~4는 사용자가 정리한 것이고 5~8은 소스를 읽으며 더한 것이다. 확인 상태를 같이 적는다 —
**코드로 확인한 것과 아직 관측이 아닌 것을 섞지 않는다.**

| # | 축 | FluidServe | llm-d | 상태 | 어떻게 재나 |
|---|---|---|---|---|---|
| 1 | 예측의 정확도와 **보수성** | 인스턴스의 평균 step 시간·KV, 해석적 모델, **평균**을 맞춘다 | 요청×인스턴스의 TTFT·토큰당, XGBoost, **p90**을 맞춘다 | 코드 확인 | 양쪽 예측/실측 지표. **coverage가 정확도와 보수성을 갈라 준다** |
| 2 | 붙들기(PEND) | TTFT 예산을 소진하며 기다린다 | 도착 즉시 결정 | 코드 확인 | 거절률 대 달성률 |
| 3 | **요청을 가르는 신호** | **클래스**(`share`). **접두사는 안 본다**(§9.8) | **접두사**(블록 해시 일치율) | **코드 확인** | 클래스당 유효 인스턴스 수, 엔진별 prefix hit rate |
| 4 | **못 지키는 요청 제외** | `allowanceMs < floor`면 `unachievable`로 표시하고 **게이트와 damage 추정에서 뺀다**(`fluidserve.go:944-953`) | **개념이 없다.** 실행 중 요청 큐에서 빠지는 것은 EOS와 TTL뿐이고, 그 최솟값이 계속 `podMinTPOTSLO`가 된다 | **코드 확인. 되먹임은 아직 관측 아님** | **거절률의 시간 곡선.** 부하가 일정한데 단조 증가하면 상태가 누적되는 것이다 |
| 5 | 메모리 제약 | `newKv ≤ capMem` | **없다.** KV 사용률은 예측기의 입력일 뿐 제약이 아니다 | 코드 확인 | `vllm:num_preemptions_total` |
| 6 | 요청 자신의 미래 | `E[L−j \| L>j]`로 남은 토큰을 더한다 | 스케줄링 시점 예측을 `generatedTokenCounts = 1`로 부른다 | 코드 확인 | 긴 클래스(deepresearch)에서 차이가 커야 한다 |
| 7 | 거절의 예외 조항 | 없다 | **셋 있다** — 놀고 있는 인스턴스가 있거나 / KV 2% 미만이 있거나 / 예측이 없으면 무조건 통과 | 코드 확인 | 부하-거절 곡선의 모양. 낮은 부하에서 거의 안 거절하다 갑자기 꺾이는가 |
| 8 | best-fit packing | `room` 정렬 | `latency-scorer`의 기본 `least` | **같다** | — (차이가 아니라 일치. 논문에 적는다) |

**4번의 되먹임이 성립할 조건은 갖춰져 있다.** 예산이 빡빡한 요청이 들어가면 그 인스턴스의
`podMinTPOTSLO`가 그 값으로 고정되고, **그 요청이 이미 예산을 넘겨 못 지키게 되어도 큐에
남아** 새 요청을 계속 그 기준으로 판정한다. 우리 `unachievable` 주석이 같은 상황을 그대로
서술한다 — "그 요청에 도움도 안 되면서 다른 요청을 못 받게 된다". **다만 이것은 코드 독해이지
관측이 아니다. rep 1의 거절률 시간 곡선이 첫 증거가 된다.**

⚠ **5번과 4번이 같은 방향으로 작용한다.** 메모리 제약이 없으면 KV를 넘겨 preemption이 나고,
preemption은 요청 단위 지표에 안 보이면서 엔진 시간을 크게 먹는다. 두 축을 같이 본다.

## 9.8 우리는 prefix caching을 어디까지 보는가 (2026-08-07)

**"안 본다"는 정확하지 않다. 두 곳에서 보고, 라우팅에서만 안 본다.**

| 어디 | 어떻게 | 무엇을 아는가 |
|---|---|---|
| KV 용량 모델 | `kvPhysical / kvLogical` 비율을 **측정**해서 쓴다 | 접두사 공유로 물리 KV가 논리보다 작다는 것 (`fluidserve.go:1032`) |
| prefill 비용 | `prefillFraction` — "도착 토큰 중 엔진이 **실제로 계산하는** 비중"을 관측에서 지수이동평균(α=0.02)으로 추정 | 그 엔진이 평균적으로 얼마나 아끼는가 |
| **라우팅 결정** | **안 본다.** `sortCandidates`는 `w·share + (1−w)·room`뿐이다 | — |

**한계는 `prefillFraction`이 인스턴스마다 하나인 스칼라이고 요청과 무관하다는 것이다.**
라우팅에 필요한 양은 `(요청, 인스턴스)` 쌍마다 다르다 — 같은 요청이 엔진 A에는 접두사가 90%
있고 B에는 0%일 때 prefill 실계산이 500 대 5,000 토큰으로 갈리는데, 인스턴스별 평균은 그 둘을
구분하지 못한다.

**넣으려면 부품은 다 있다.** `ttft.json`이 이미 `t_pre(chunk)` 표를 갖고 있고
(1024→80.1 ms, 4096→264.2 ms, 8192→519.7 ms), 접두사 해시 테이블은
`pkg/scheduler/policy/prefix_cache_utils.go`에 있으며(다른 정책이 쓴다), 엔진은
`vllm:prefix_cache_hits_total`을 낸다. 바꿀 것은 chunk를 정하는 방식뿐이다:

```
지금:  prefill 비용 = t_pre(프롬프트 토큰 × prefillFraction)            ← 인스턴스 평균
가능:  prefill 비용 = t_pre(프롬프트 토큰 × (1 − 그 인스턴스의 접두사 일치율))
```

⚠ **그런데 넣으면 우리 주장 하나가 흔들린다.** "분리를 구성하지 않고 결과로 얻는다"의 기전은
클래스 선호(`share`)인데, **접두사 선호를 넣으면 분리를 만드는 신호가 둘이 되어 어느 쪽이 만든
분리인지 갈리지 않는다.** 그리고 우리 세 클래스는 프롬프트 구조가 크게 달라 접두사만으로도
갈릴 가능성이 있다.

→ **순서**: ① EXP-66의 H3이 먼저 답한다(접두사 선호만으로 클래스가 갈리는가). 갈리면 접두사
항을 넣는 것은 주장을 흐리는 일이다. ② 안 갈리면 `share`와 직교하는 항이므로 별개로 넣고
ablation으로 기여를 가를 수 있다. ③ **어느 쪽이든 "라우팅에는 접두사를 안 쓰고 용량 모델에는
측정으로 흡수한다"를 논문에 적는다** — 지금 어디에도 없고 심사에서 반드시 나온다.

---

## 9.9 rep 1 결과 (2026-08-07 22:56 KST 완료) — **llm-d가 앞선다**

8조건 전부 완료. 채점은 표준 채점기 `exp22_fluidserve.py`, 산출물은
`results/aggregate_analysis/exp66r1/`. **offered 기준**(도착한 모든 요청이 분모, 거절은 위반).

| req/s | **llm-d rep1 (n=1)** | FluidServe EXP-53 (n=2, 범위) | 차이 | Llumnix SLO | PolyServe(EXP-57) |
|---|---|---|---|---|---|
| 15 | 100.0 | — | | | |
| 25 | 97.8 | — | | | |
| 35 | 95.3 | — | | | |
| 45 | 91.5 | 90.2 (90.0~90.5) | +1.3 | 52.4 | 27.5 |
| 50 | 89.5 | — | | | |
| **55** | **84.3** | 66.4 (66.3~66.4) | **+17.9** | 31.0 | 31.5 |
| **60** | **76.9** | — | | | |
| **70** | **66.1** | 51.9 (51.5~52.2) | **+14.2** | 21.4 | 24.0 |

goodput 70 req/s: **22,308 대 17,990 (+24%)**. EXP-53 값은 `numbers.tsv`에서 인용했다.

**판정 규칙(§7)이 지목하는 결론**: "요청 단위 예측이 값을 갖는다. EXP-64가 그 상한을 재는
실험이 되고, 우리 설계에 예측기를 붙이는 것이 다음 방향."

**그림**: `results/aggregate_analysis/exp66r1/`. 정본은 그 디렉토리의 `README.md`이고,
그림마다 무엇을 주장하고 무엇을 말하지 않는지가 거기 있다. 다시 만드는 명령은
`bash analysis_scripts/redraw_static_sweep_llmd.sh`이고, **EXP-53의 그림은 건드리지 않는다**
(llm-d는 반복 1회·다른 세션·예열 주행이라 정본 비교에 아직 넣지 않는다).

### ⚠ 아직 결론이 아닌 이유 넷

1. **반복 1회다.** 우리 규칙이 조건당 1회로 판정하지 않는 것이고 EXP-53 열은 2회다. rep 2 필요.
2. ~~**예열 주행 3분이 llm-d에만 있다.**~~ → **2026-08-08에 재고 해제했다(§9.11).** cold
   restart 직후 예열 주행의 첫 1분이 이미 prefix hit 96.2%라서, 예열 주행이 더해 주는 것이
   없다. llm-d의 93.5% 대 FluidServe의 75.1%는 예열이 아니라 라우팅이 만든 차이다.
3. **세션이 다르다** (08-02 대 08-07).
4. **m1f 대 m1** — llm-d·Llumnix SLO는 m1f, FluidServe·PolyServe는 m1.

### 격차의 대부분이 swe다 — 그런데 swe만 입력이 다르다

| 55 req/s | chat | dr | **swe** |
|---|---|---|---|
| llm-d | 76.9 | 91.3 | **84.7** |
| FluidServe | 66.1 | 86.4 | **30.7** |

**swe는 두 시스템이 다른 예산을 받는 유일한 클래스다**(llm-d는 m1f의 (2,500, 52), 우리는
`25:e2e:30000`). 채점은 둘 다 30초 E2E로 같지만 정책이 받는 입력이 다르고, **그 클래스에서
격차가 난다.** 정책 차이인지 예산 해석 차이인지 이 표로는 못 가른다.
→ 가르려면 **FluidServe를 m1f로 한 번 돌리는 arm**이 필요하다. 우리 정책은 tier 25를
`e2e:30000`으로 재정의하므로 값이 바뀌어도 동작이 같아야 하는데, **그것이 확인된 적이 없다.**

## 9.10 축 4·5·7 검사 결과 (rep 1 데이터, 2026-08-07)

§9.7의 여덟 축 중 rep 1로 바로 검사할 수 있는 셋을 봤다. **둘은 지지되지 않았다.**

### 축 4 — 못 지키는 요청이 쌓여 되먹임을 만드는가 → **지지되지 않는다**

부하가 일정한 조건 안에서 분당 거절률:

```
3,300 rpm:  0.3 →  7.6 → 16.5 →  9.7 → 11.4 →  2.4 →  7.7 →  8.9 %
3,600 rpm:  0.3 → 15.1 → 28.7 → 14.0 →  8.6 →  5.1 → 11.8 → 28.6 %
4,200 rpm: 11.0 → 19.2 → 23.8 → 34.7 → 19.3 → 24.9 → 35.3 → 26.9 %
```

**단조 증가가 아니라 진동한다.** 상태가 누적되면 되돌아올 수 없는데 되돌아온다 — 못 지키는
요청도 EOS로 끝나면 큐에서 빠지므로 기준이 풀린다. **코드 구조상 되먹임의 조건은 맞지만
영구적이지 않다.**

⚠ **진동 자체가 새 관측이다.** 부하가 일정한데 거절률이 3배 넘게 오르내린다. 원인이
`unachievable` 제외의 부재인지 `weighted-random-picker`의 무작위성인지 이 데이터로는 못 가른다.

### 축 5 — 메모리 제약이 없어 preemption이 느는가 → **지지되지 않는다**

| arm | 3,300 rpm | 4,200 rpm |
|---|---|---|
| llm-d | **0** | **0** |
| FluidServe (EXP-53) | 0 | — |
| Llumnix SLO (EXP-53) | 0 | 0 |
| PolyServe (EXP-53) | **3,408** | — |

llm-d에 KV 제약이 없는 것은 코드 사실이지만 **이 워크로드에서 preemption을 만들지 않는다.**
KV보다 시간이 먼저 바닥나기 때문이고(motivation.md의 "KV는 55%인데 chat 예산의 89~96%에 붙어
있다"와 같은 상황), PolyServe의 3,408은 정적 파티션이 한 엔진에 몰아넣는 별개 현상이다.
→ **KV가 병목인 워크로드에서는 다를 수 있으나 여기서는 아니다.**

### 축 7 — 거절의 예외 조항 셋 때문에 낮은 부하에서 안 거절하는가 → **지지된다**

| req/s | 15 | 25 | 35 | 45 | 50 | 55 | 60 | 70 |
|---|---|---|---|---|---|---|---|---|
| llm-d 거절률 | 0% | 0% | 0.03% | **0.5%** | 1.7% | 7.5% | 14.5% | 29.3% |

**45까지 사실상 0이다가 50부터 꺾인다.** "놀고 있는 인스턴스가 하나라도 있으면 무조건 통과"와
일치한다 — 낮은 부하에서는 항상 빈 엔진이 있어 거절 조건이 성립하지 않는다.
**FluidServe는 같은 45 req/s에서 이미 7.3%를 거절한다.** 두 시스템이 거절을 시작하는 지점이
다르다.

### 그래서 남은 것

**rep 1의 격차(55에서 +17.9, 70에서 +14.2)는 축 4·5·7로 설명되지 않는다.** 남은 후보는
축 1(예측의 정확도와 보수성), 2(붙들기), 3(가르는 신호), 6(요청 자신의 미래)이고,
**그중 3번이 EXP-66의 원래 질문**이다. rep 2가 끝나면 **클래스당 유효 인스턴스 수와 엔진별
prefix hit rate**로 축 3에 답한다.
→ **답했다. §9.11이다** (rep 2를 기다리지 않고 rep 1 데이터로 잴 수 있는 양이었다).

---

## 9.11 축 3에 답한다 — 격차의 원인은 클래스 분리가 아니라 **prefix cache 재사용**이다 (2026-08-08)

**결론 두 줄.**
① **llm-d는 클래스를 거의 분리하지 않는다.** 부하가 높을수록 오히려 더 퍼진다.
② **엔진이 보고하는 prefix cache hit rate가 llm-d 93.5%, FluidServe 75.1%, Llumnix SLO
79.9%다.** 그만큼 prefill에서 실제로 계산해야 하는 토큰이 줄고, 그 시간이 decode로 간다.

### 먼저 배관 — llm-d는 엔진 귀속을 어디서 얻는가

`build_request_engine_map.py`는 쓸 수 없다. 그것은 클라이언트의 `request_ids.jsonl`과
**Llumnix 스케줄러의 dispatch 로그**를 요청 uuid로 붙이는데, llm-d에서는 Llumnix 스케줄러가
요청 경로에 아예 없고 `scheduler_dispatch.log`에 이 run의 줄이 하나도 없다. 엔진 이름은
**Envoy access log의 `%UPSTREAM_HOST%`**에 있고, 그것이 `<pod-ip>:<port>`라서 엔진 파드 위
vLLM API 서버 넷 중 하나를 그대로 지목한다.

붙이는 키는 식별자가 아니라 **시각과 소요 시간**이다 — 양쪽이 공유하는 식별자가 없기
때문이다(Envoy는 자기 `x-request-id`를 만들고 클라이언트는 vLLM의 `cmpl-<uuid>`를 적는데
후자는 응답 본문 안이라 Envoy가 못 본다). 새 스크립트가
`analysis_scripts/request_level/llmd_engine_map.py`다.

**이것이 충분한지를 가정하지 않고 쟀다.** 도착 간격 평균이 14 ms인 가장 빽빽한 조건
(70 req/s)에서 짝지어진 쌍의 시작 시각 차이 중앙값이 **1.6 ms**, 소요 시간 차이 중앙값이
**3.0 ms**로, 소요 시간 자체의 폭(17~30초)보다 두 자릿수 작다. 비용이 작은 쌍부터 순서대로
집어 양쪽 모두 재사용을 금지하는 방식으로 1:1을 만들었고, **여덟 조건 전부 99.88~99.97%가
매칭됐다.** 거절된 요청은 양쪽에서 뺀다(Envoy는 upstream을 `-`로 적고 클라이언트는
`is_rejected`로 적는다).

### 축 3 — 클래스 분리는 llm-d 쪽이 **더 약하다**

클래스당 유효 인스턴스 수(`1/Σ s_i²`, 창별 중앙값. 4.0 = 네 대에 고르게, 1.0 = 한 대):

| arm, rate | chat | deepresearch | swe | 평균 |
|---|---|---|---|---|
| llm-d 15 req/s | 2.97 | 2.76 | 2.57 | 2.77 |
| llm-d 45 req/s | 3.63 | 3.41 | 3.31 | 3.45 |
| llm-d 70 req/s | 3.93 | 3.79 | 3.52 | 3.75 |
| FluidServe 45 req/s | 3.27 | 3.39 | 3.37 | 3.34 |
| FluidServe 70 req/s | 2.67 | 2.40 | 2.15 | 2.41 |

**두 시스템이 부하에 대해 반대로 움직인다.** llm-d는 한가할 때 2.6~3.0대에 몰아넣고
(`headroomSelectionStrategy: least`가 **예산 경계에 가장 가까운 후보를 고르는 best-fit
packing**이므로 의도한 동작이다) 부하가 오르면 네 대로 퍼진다 — 여유가 남은 엔드포인트가
없어지면 몰아넣기가 성립하지 않기 때문이다. FluidServe는 반대로 퍼진 상태에서 시작해
모여든다(클래스 선호가 클래스끼리 경쟁하기 시작해야 작동할 것이 생긴다).
**점수 격차가 가장 큰 55~70 req/s에서 llm-d가 가장 덜 분리한다.** 그러므로 llm-d의 점수를
만드는 것은 클래스 분리가 아니다.

### 그러면 무엇인가 — 엔진 계층의 총계 (70 req/s, Prometheus, 클라이언트와 독립)

| arm | decode tok/s | prompt tok/s | prefix hit | 평균 배치 | KV | preemption |
|---|---|---|---|---|---|---|
| **llm-d** | **20,661** | 70,533 | **93.5%** | 238 | **22.2%** | 0 |
| FluidServe | 16,562 | 49,292 | 75.1% | 219 | 35.9% | 60 |
| Llumnix SLO | 11,990 | 65,807 | 79.9% | 158 | 36.0% | 0 |
| PolyServe | 16,087 | 87,015 | 47.9% | 334 | 36.3% | 344 |

`prompt_tokens_total`은 캐시로 채운 토큰까지 세므로, **실제로 계산한 prefill 토큰**은
`prompt × (1 − hit)`이다: llm-d 4,585 tok/s 대 FluidServe 12,274 tok/s로 **llm-d가 2.7분의
1만 계산한다.** 그 시간이 decode로 가서 20,661 대 16,562(+25%)가 되고, 캐시 블록을 공유하는
만큼 KV 점유도 22.2% 대 35.9%로 낮다. **요청 단위 지표에는 이 중 어느 것도 안 보인다.**

### ⚠ 그런데 이것이 예열 주행 때문인가 → **아니다. 쟀다**

이것이 §9.9의 caveat ②였다. 조건마다 엔진을 cold restart한 직후의 3분 예열 주행과, 그
뒤의 8분 측정 구간을 1분 단위로 나눠 hit rate를 보면(70 req/s):

```
예열 주행   96.2  93.6  94.4  93.6  93.8
측정 구간   95.5  94.1  94.1  93.0  93.0  92.9  94.3  93.4  93.1  93.3
```

**cold engine에서 시작한 예열 주행의 첫 1분이 이미 96.2%다.** 즉 예열 주행이 더해 주는 것이
없고, 측정 구간의 첫 1분이 스스로 만들었을 상태와 같다. 같은 열 개 창에서 FluidServe는
84.4 89.2 79.7 76.1 79.9 74.2 72.0 76.9 71.7 78.1, Llumnix SLO는 79.8 79.2 81.3 79.9 80.6
79.3 80.4 79.3 76.7 81.0이다.
→ **§9.9의 인용 금지 이유 넷 중 ②는 해제된다.** ①(반복 1회) ③(다른 세션) ④(m1 대 m1f)는
그대로다.

### 워크로드가 같다는 것도 확인했다

`mix_short_m1_balanced.json`과 `mix_short_m1_slofair.json`을 키 단위로 비교하면 **`slo`
블록 하나만 다르다**(swe의 `ttft_ms` 11,800 → 2,500, `tbt_ms` 25 → 52). 클래스 비율,
task 집합, 도착 과정은 동일하므로 위의 prefix cache 비교는 같은 요청 흐름에 대한 것이다.

### 남은 것

`prefix-cache-affinity-filter`와 예측기 특징의 `prefix_cache_score`가 라우팅에 직접
들어가고, 우리는 그 자리에 클래스 선호를 넣는다(§9.8). **그래서 축 3의 답은 "가르는 신호가
클래스냐 prefix냐"이고, 이 워크로드에서는 prefix 쪽이 훨씬 크게 이긴다.** 이것이
**측정 가능한 설계 항목**이라는 것이 EXP-66이 준 것이고, 다음에 답해야 하는 것은
**우리 판정 조건 안에 prefix 재사용을 넣으면 얼마를 회수하는가**이다.

---

## 9.12 llm-d의 예측기는 정확히 무엇을 예측하는가 (2026-08-08, 소스 확인)

사용자 질문: "각 request가 얼마나 더 run 할지, 새 request의 TTFT·TBT가 어떨지, 전에 돌던
request들의 TTFT·TBT가 어떨지까지 전부 예측하나?" — **셋 중 하나만 예측한다.**

`pkg/epp/framework/plugins/requestcontrol/dataproducer/predictedlatency/prediction.go`의
`generatePredictions`가 정본이다. 후보 엔드포인트마다 **한 쌍**을 낸다:
**지금 도착한 요청 하나를 그 엔드포인트에 놓았을 때의 (TTFT, TPOT)**.

특징은 열한 개이고 전부 **그 엔드포인트의 현재 상태 + 그 요청의 입력**이다
(`latencypredictorclient/types.go:PredictionRequest`):

`kv_cache_percentage`, `input_token_length`, `num_request_waiting`,
`num_request_running`, `num_tokens_generated`, `prefix_cache_score`,
`encoder_input_size`, `encoder_matched_size`, `pod_type`,
`prefill_tokens_in_flight`, `decode_tokens_in_flight`.

| 질문 | 답 | 근거 |
|---|---|---|
| 각 요청이 앞으로 얼마나 더 도는지 예측하는가 | **아니다.** 출력 길이 추정이 아예 없다. 스케줄링 시점 호출은 `generatedTokenCounts[i] = 1`로 고정이다 | `prediction.go:71` |
| 새 요청의 TTFT·TPOT를 예측하는가 | **그렇다. 이것 하나만 한다** | `prediction.go:86` |
| 이미 돌던 요청들의 TTFT·TPOT가 어떻게 되는지 예측하는가 | **아니다.** 기존 요청은 두 경로로만 들어온다 — (a) 현재 상태 특징(KV·running·waiting·in-flight)에 **집계된 값**으로, (b) `podMinTPOTSLO` = **그 엔진에서 돌고 있는 요청들의 TPOT 예산 중 가장 빡빡한 것**으로. (b)는 예측이 아니라 **문턱값**이다 | `prediction.go:161-173`, `plugin.go:493-501` |

판정 조건은 따라서 이렇게 읽힌다:

```
pred.TPOT(새 요청 | 이 엔진의 현재 상태) < min(새 요청의 TPOT 예산,
                                            그 엔진 위 가장 빡빡한 TPOT 예산) × buffer
```

**기존 요청이 무엇을 잃는지는 묻지 않고, 새 요청이 가장 빡빡한 예산을 지키는지를 대신
묻는다.** decode에서 토큰당 시간이 대체로 배치 전체의 성질이라 이 대체가 완전히 틀린 것은
아니지만, **우리 `overIncumbents`가 재는 양과는 다른 양이다**(related-works-review §12.2의
차이 2번).

**그리고 이 조건은 기본 구성에서 꺼져 있다** — `streamingMode`가 기본 `false`이고 false면
TPOT 쪽이 통째로 `TPOTValid=true, Headroom=0`으로 중화되어 판정에 TTFT만 남는다
(`prediction.go:111-115`). EXP-66은 `epp-config-slo.yaml`에서 `streamingMode: true`를
명시했으므로 켜진 상태로 돌았다.

### 그래서 "예측기가 너무 좋은가"에 대한 답

**아니다. 예측 자체는 특별히 좋지 않다.** rep 1에서 잰 값은 p90 목표 대비 coverage
88.1%/89.9%, 예측/실측 비 1.20배/1.13배로 **13~20% 보수적으로 과대예측**한다. 예측 하나에
9.7 ms가 걸리고 2~3초마다 재학습한다.
**격차를 만든 것은 예측의 정확도가 아니라 §9.11의 prefix cache 재사용이다** — 그것은
예측기가 아니라 `prefix-cache-affinity-filter`와 `prefix_cache_score`가 만든다.

---

## 10. 아직 모르는 것

1. ~~file-discovery와 predicted-latency 플러그인의 조합~~ — **절반 닫혔다 (2026-08-07)**:
   file-discovery + `core-metrics-extractor` + 기본 scorer 묶음(`llmd-base`)이 실제로 동작하고
   요청이 엔진에 닿는다. **아직 안 해 본 것은 여기에 `predicted-latency-producer`를 얹는
   것**이고 그것이 단계 4다.
2. **엔드포인트 주소가 리터럴 IPv4여야 한다** — 호스트명을 해석하지 않는다. `neutral-0`의
   파드 IP는 재시작마다 바뀌므로 드라이버가 조건마다 다시 써야 한다(`watchFile: true`가
   그것을 받는다). 경로 B로 가면 이 문제가 없어진다.
3. ~~ghcr.io에서 실제로 pull 되는지~~ — **닫혔다 (2026-08-07)**: 넷 다 받아졌다.
4. ~~EPP가 요구하는 지표 이름~~ — **닫혔다 (2026-08-07)**: EPP의 내장 vLLM 설정이 이미
   `vllm:kv_cache_usage_perc` · `vllm:num_requests_waiting` · `vllm:num_requests_running`을
   쓰고, 그것이 우리 vLLM 0.12.1.dev0이 내는 이름이다. 재정의가 필요 없다
   (`datalayer/extractor/metrics/factories.go`의 `defaultEngineConfigs`).
5. **예측기의 수렴 시간을 모른다.** 5장이 그것을 재는 방법이고, 재기 전에는 점수를 인용하지
   않는다.
6. ~~Envoy 접근 로그의 형식~~ — **닫혔다 (2026-08-07)**: 탭으로 구분한 일곱 칸(시각 /
   요청 번호 / 응답 코드 / **처리한 엔진** / 소요 ms / 응답 플래그 / 경로)을 hostPath
   `/home/nxclab/tools/llmd-envoy/envoy_access.log`에 쓴다. 한 줄이 약 120바이트이므로 한 시간
   조건(요청 십수만 건)이 20 MB 안쪽이다. **남은 것은 버퍼링 대기를 드라이버에 넣는 것**(§4.1).

---

## 11. 관련 문서

| | |
|---|---|
| [related-works-review.md §12](related-works-review.md) | **llm-d 시스템 분석의 정본** — 판정 조건이 우리와 같은 형태라는 것, 남는 차이 여섯, 거절이 배포 형태에 달려 있다는 것, 예측기가 무엇을 학습하는가 |
| [STATUS.md](STATUS.md) | 지금 상태 |
| [motivation.md](motivation.md) §8 | 분리를 재는 지표(클래스당 유효 인스턴스 수)의 정의 |
| `CLAUDE.md` 함정 A~F | 설정 확인, 실험 중 금지 사항, 결과 읽는 규칙 |
