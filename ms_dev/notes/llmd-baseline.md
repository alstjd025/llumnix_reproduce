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

### 3.2 EPP 플러그인 구성 셋

`EndpointPickerConfig` 파일 하나가 arm 하나다. ConfigMap 셋으로 두고 EPP를 재시작하며 바꾼다.

| arm | 플러그인 | 무엇을 재나 |
|---|---|---|
| `llmd-base` | queue-scorer + kv-cache-utilization-scorer + prefix-cache-scorer + no-hit-lru-scorer (llm-d 기본 구성) | 예측을 안 쓰는 llm-d. 사실상 부하 균등화 + prefix 선호 |
| `llmd-pred` | predicted-latency-producer + prefix-cache-affinity-filter + latency-scorer + weighted-random-picker (**SLO 헤더 없음**) | 예측만 켠 것 |
| `llmd-slo` | 위 + slo-headroom-tier-filter + latency-slo-admitter, `streamingMode: true` (**SLO 헤더 있음**) | 예측 + 예산 + 거절. `predicted-latency-slo.values.yaml`의 구성 그대로 |

`streamingMode: true`가 있어야 토큰당 시간 쪽 판정이 살아난다(기본값은 false이고, false면
그 부분이 통째로 꺼진다). 우리 클라이언트는 이미 전부 스트리밍이므로 조건은 만족한다.

#### `llmd-pred`와 `llmd-slo`의 차이 — 점수의 방향이 뒤집힌다

둘 다 같은 예측값을 쓰는데 **부하를 퍼뜨리느냐 모으느냐가 반대**가 된다. 이유는
`latency-scorer`가 headroom = 예산 − 예측으로 점수를 만들기 때문이다
(`scorer/latency/plugin.go:181-247`).

| | `llmd-pred` (SLO 헤더 없음) | `llmd-slo` (SLO 헤더 있음) |
|---|---|---|
| 예산 | 없음 → headroom = 0 − 예측 = **전부 음수** | 클래스별 예산 → headroom에 부호가 생긴다 |
| 후보 가르기 | 전부 음수라 tier 필터가 할 일이 없다 | 지킬 수 있는 무리 / 없는 무리로 갈린다 |
| 점수 | 음수 구간에서는 `least`가 강제되고, 그것은 **위반 폭이 가장 작은 것** = **예측 지연이 가장 짧은 것** → 부하를 **퍼뜨린다** | 양수 구간에서 `least`는 **예산 경계에 가장 가까운 것** → 부하를 예산 한계까지 **모은다** |
| 거절 | 없다 | `latency-slo-admitter` |

**그래서 `llmd-pred`는 "학습한 지연 추정치로 하는 부하 균등화"이고 `llmd-slo`는 "예산에
맞춘 best-fit packing"이다.** 우리 설계와 방향이 같은 것은 뒤쪽이고, 앞쪽은 Llumnix
부하 균등화의 더 똑똑한 판이다. 둘을 같이 두는 이유가 여기 있다 — **예측을 갖는 것과 그
예측을 예산에 대고 쓰는 것이 다른 일이라는 것을 이 두 arm의 차이가 직접 잰다.**

---

## 4. 우리 쪽에서 고쳐야 하는 것

전부 측정 경로에 있으므로 **실험이 도는 동안에는 손대지 않는다**(CLAUDE.md 함정 C).
지금은 EXP-63이 끝나 클러스터가 비어 있다.

| # | 파일 | 무엇 | 크기 |
|---|---|---|---|
| 1 | `workloads/swe_bench_coding/agent.py` | `LlumnixCompletionsLLM`에 `extra_headers` 인자를 추가하고 `.stream()`/`.invoke()`가 그것을 보내게 한다 | 약 10줄 |
| 2 | 같은 파일 | 헤더 값을 `slo_spec`에서 유도한다 — `x-llm-d-slo-ttft-ms` = `ttft_ms`, `x-llm-d-slo-tpot-ms` = `tbt_ms`. 클래스별 `slo_spec`은 mixed 워크로드가 이미 요청마다 넣고 있다 | 약 10줄 |
| 3 | 같은 파일 | `x-llm-d-inference-objective` 헤더(거절 대상 표시)와 `x-request-id`(엔진 연결용)를 보낸다 | 약 5줄 |
| 4 | 같은 파일 | `_raise_if_llumnix_rejected`에 llm-d의 거절 본문(`no valid endpoint available to serve the request`)을 추가한다 | 2줄 |
| 5 | `llumnix_deploy.py` | `restart_llumnix`가 `deploy/scheduler deploy/gateway`를 이름으로 고정하고 있다. llm-d에서는 `deploy/llmd-router`를 재시작해야 한다 | 인자 하나 |
| 6 | `analysis_scripts/request_level/build_request_engine_map.py` | 스케줄러 배치 로그 대신 **Envoy 접근 로그**를 읽는 경로를 추가한다 | 새 함수 |
| 7 | `k8s/exp07/run_exp66_llmd.sh` (신규) | arm 정의와 조건 루프. 기존 `run_exp27_mixsweep.sh`를 본뜨되 `set_scheduler_profiling.py` 대신 EPP ConfigMap 교체 | 신규 |

### 4.1 요청이 어느 엔진에서 처리됐는지 알아내는 방법

지금은 클라이언트 `metrics.csv`와 스케줄러 `scheduler_dispatch.log`를 요청 번호로 맞춰
붙인다. llm-d에는 그 로그가 없다. **대신 Envoy 접근 로그가 더 나은 것을 준다** —
`%UPSTREAM_HOST%`가 실제로 요청을 처리한 `IP:포트`이고 `%REQ(X-REQUEST-ID)%`가 요청 번호다.
한 줄에 둘 다 있으므로 **로그 두 개를 맞춰 붙일 필요가 없고, PolyServe 한 시간 조건에서
86.4%만 덮였던 문제가 구조적으로 안 생긴다.**

조건: 클라이언트가 `x-request-id`를 직접 만들어 보내고 그 값을 자기 기록에도 남겨야 한다
(위 표의 4번 항목).

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
② 예측기 파드 둘 재시작  → 모델이 빈 상태에서 시작
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

## 6. 단계와 점검 지점

각 단계는 **다음 단계로 넘어가기 전에 확인할 것**을 갖는다. 확인이 안 되면 거기서 멈춘다.

| 단계 | 하는 일 | 넘어가기 전에 확인할 것 |
|---|---|---|
| **0** | ghcr.io에서 이미지 넷을 실제로 pull 한다 (EPP, envoy, 예측기 둘) | `crictl images`에 넷이 보인다. 안 되면 여기서 계획이 바뀐다 |
| **1** | 예측기 둘을 Deployment로 띄운다 | 학습 서버 `/`에 응답, 예측 서버가 모델을 받아 온다 |
| **2** | 경로 A(file-discovery)로 EPP + Envoy를 띄우고 `llmd-base` 구성으로 `curl` 한 번 | 응답이 오고, Envoy 접근 로그에 `UPSTREAM_HOST`가 엔진 넷 중 하나로 찍힌다 |
| **3** | 클라이언트 변경(4장의 1~4번)을 넣고 짧은 smoke — 8분 45 req/s 한 조건 | `metrics.csv`가 정상, 요청 → 엔진 연결이 **100%**, 엔진 넷에 다 분산됨 |
| **4** | `llmd-pred`, `llmd-slo` 구성으로 각각 smoke | EPP 로그에 예측값이 찍히고, 예측 대 실측 히스토그램이 채워진다 |
| **5** | 경로 B로 옮긴다 (GIE CRD 설치, InferencePool + InferenceObjective) | `llmd-slo`에서 **거절이 0이 아니다.** 0이면 표시가 안 먹은 것이므로 멈춘다 |
| **6** | 본 실험 | 아래 판정 규칙 |

**단계 3이 가장 중요한 점검이다.** 여기서 요청 → 엔진 연결이 100%가 아니면 3·4번 질문에
답할 수 없고, 그러면 이 실험의 고유한 값이 없어진다.

---

## 7. 본 실험 설계 — 실행 전에 판정 규칙을 적는다

```
정적 sweep : 35 / 45 / 55 / 70 req/s, 8분, 반복 2회
arm        : fluidserve / llmd-base / llmd-pred / llmd-slo
조건 수    : 4 arm × 4 rate × 2 반복 = 32조건
소요       : 약 6시간
믹스       : m1 (mix_short_m1_balanced.json) — 지금까지의 정적 조건과 같은 것
```

기존 arm(Llumnix, Llumnix SLO, PolyServe)은 이미 EXP-53·EXP-57에 있으므로 다시 안 돌린다.
**단 세션이 다르므로, 인용할 때 반복 편차를 밝히고 비교한다**(CLAUDE.md의 반복 규칙).

**판정 규칙 (결과를 보기 전에 적는다):**

| 관측 | 결론 |
|---|---|
| `llmd-slo`가 `fluidserve`보다 반복 편차보다 크게 낮다 | 예측을 학습하는 것이 우리 방식보다 낫지 않다. §12.3의 차이 여섯 중 어느 것이 원인인지가 다음 질문 |
| `llmd-slo` ≈ `fluidserve` (편차 안) | **우리 기여 주장을 좁혀야 한다.** 점수가 같다면 남는 것은 입력의 세기(그들은 요청 단위 학습, 우리는 클래스 분포)와 배포 비용이다 |
| `llmd-slo` > `fluidserve` | 요청 단위 예측이 값을 갖는다. EXP-64(길이 오라클)가 그 상한을 재는 실험이 되고, 우리 설계에 예측기를 붙이는 것이 다음 방향 |
| `llmd-base` ≈ `llmd-pred` | 예측 자체는 값이 없고 예산을 보는 것이 값이다 |
| `llmd-pred` ≈ `llmd-slo` | 반대로 예산·거절이 값이 없다 |
| 세 llm-d arm의 클래스당 유효 인스턴스 수가 4.0 근처 | prefix 선호로는 클래스가 안 갈린다 → **분리가 클래스를 보는 데서만 나온다는 우리 주장이 강해진다** |
| 세 llm-d arm의 클래스당 유효 인스턴스 수가 우리와 비슷 | **prefix 선호만으로 같은 분리가 나온다** → 우리 주장을 "클래스를 봐야 한다"에서 "무엇이든 요청을 갈라 놓는 신호가 있어야 한다"로 넓혀야 한다 |

⚠ **45 req/s는 반복 사이의 편차가 10점을 넘는 구간이므로 그 하나로 판정하지 않는다.**
55와 70 req/s가 판정의 기준이다.

⚠ **`llmd-slo`의 점수를 인용하기 전에 5장의 예측 오차 지표를 본다.** 예측이 수렴하지 않은
조건이 섞여 있으면 그 조건은 predicted-latency를 잰 것이 아니다.

---

## 8. 파일을 어디에 두는가

| 경로 | 무엇 | git |
|---|---|---|
| `lib/llm-d/` | 상류 저장소 클론 넷(llm-d-router, llm-d, gateway-api-inference-extension, llm-d-latency-predictor) | 추적 안 함. `lib/sglang`과 같은 방식 |
| `deploy/llmd/` | 우리가 쓴 매니페스트 — EPP+Envoy 파드, 예측기 둘, ConfigMap 셋(arm 하나당 하나) | **추적한다** |
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

## 10. 아직 모르는 것

1. **file-discovery와 predicted-latency 플러그인의 조합을 배포한 예가 없다.** 두 가이드가
   별개다. 같은 `EndpointPickerConfig` 형식이라 합쳐질 것으로 보이지만 검증은 우리가 한다.
   `dataLayer` 블록(discovery + metrics-source + extractor)을 직접 써야 한다.
2. **엔드포인트 주소가 리터럴 IPv4여야 한다** — 호스트명을 해석하지 않는다. `neutral-0`의
   파드 IP는 재시작마다 바뀌므로 드라이버가 조건마다 다시 써야 한다(`watchFile: true`가
   그것을 받는다). 경로 B로 가면 이 문제가 없어진다.
3. **ghcr.io에서 실제로 pull 되는지 안 해 봤다.** 단계 0이 그것이다.
4. **EPP가 요구하는 지표 이름을 우리 vLLM에 맞게 설정해야 한다** — `kv_cache_usage_perc`.
   플래그 이름과 기본값을 EPP `--help`로 확인한다.
5. **예측기의 수렴 시간을 모른다.** 5장이 그것을 재는 방법이고, 재기 전에는 점수를 인용하지
   않는다.
6. **Envoy 접근 로그의 형식을 아직 안 정했다.** `%UPSTREAM_HOST%`와 `%REQ(X-REQUEST-ID)%`를
   넣는 것까지는 정했고, 파일로 뺄지 표준 출력으로 둘지는 단계 2에서 정한다. 한 시간 조건은
   요청이 십만 건대이므로 크기를 먼저 계산한다.

---

## 11. 관련 문서

| | |
|---|---|
| [related-works-review.md §12](related-works-review.md) | **llm-d 시스템 분석의 정본** — 판정 조건이 우리와 같은 형태라는 것, 남는 차이 여섯, 거절이 배포 형태에 달려 있다는 것, 예측기가 무엇을 학습하는가 |
| [STATUS.md](STATUS.md) | 지금 상태 |
| [motivation.md](motivation.md) §8 | 분리를 재는 지표(클래스당 유효 인스턴스 수)의 정의 |
| `CLAUDE.md` 함정 A~F | 설정 확인, 실험 중 금지 사항, 결과 읽는 규칙 |
