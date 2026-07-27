# FluidServe 설계서
### Multi-Instance LLM Serving을 위한 Flux 기반 통합 라우팅/Admission Controller

> **문서 목적**: 구현(Claude Code)에 전달할 상세 설계 명세.
> **표기**: `[확정]` 설계 결정 완료 · `[제안]` 검증 필요 · `[미결]` 실험이 답해야 함
> **시스템 이름**: `FluidServe` (임시)
>
> ---
>
> ⚠️ **2026-07-27 갱신 — 이 문서는 원안이고, 구현은 여러 곳에서 달라졌다.**
> 아래 [부록 B. 원안 대 구현 대조표](#부록-b-원안-대-구현-대조표-2026-07-27)를 먼저 볼 것.
> 구현·측정의 정본은 `ms_dev/notes/fluidserve-implementation.md`이고,
> 그중 **§13이 현재 상태를 자족적으로** 담고 있다.
>
> 요약: Part 3(수식)과 Part 11(한계)은 그대로 유효하며 Part 11은 실제로 일어난 일을
> 상당히 정확히 예측했다. Part 4의 C4·C6·C7과 §3.3(통합 원리)은 실측으로 반증되어
> 구현이 다르다.

---

## Part 1. 스토리라인 (논문 서사)

### 1.1 배경

Multi-instance serving이 de-facto 표준이다 (clusters → nodes → instances). 본 연구의 스코프는 **node 레벨**, 즉 한 노드 안의 여러 엔진 인스턴스를 조율하는 계층이다.

이 계층이 다루는 요청들은 이질적인 SLO를 가진다:
- **Latency-sensitive**: TTFT, TBT(TPOT)가 중요 — 인터랙티브 챗, 코딩 어시스턴트
- **E2EL-sensitive**: 전체 완료 시각이 중요 — 툴 호출, 배치 API
- **Best-effort**: starvation만 아니면 되는 — 백그라운드 작업

Load fluctuation 하에서 적절한 부하 관리가 없으면 SLO violation과 throughput loss가 동시에 발생한다.

### 1.2 기존 해법과 그 실패

| 범주 | 하는 일 | 대표 |
|---|---|---|
| Load balancing | **어디로** 보낼지 (routing at init, migration at runtime) | Llumnix, Dynamo KV-aware router |
| Load shedding | **무엇을** 처리할지 (serve / pend / reject / drop) | rate limit, queue cap, waiting-time drop |
| Isolation | SLO class별 자원 분리 | PolyServe |

**P1. 부정확한 load signal**
현행 신호들은 각기 다른 이유로 실패한다:
- *Queue length*: 과거 불균형의 적분 → lagging indicator
- *Running/inflight request 수*: 요청별 KV footprint 이질성 무시 (SWE agent 10k input vs chat 1k input)
- *KV occupancy(level)*: **drift를 보지 못함**. one-step feasibility만 체크하는 정책은 최악의 limit cycle로 수렴함이 이론적으로 증명됨 (Ao et al., Service-Induced Congestion)
- *SLI(attainment)*: 사후적 — 위반이 발생해야 관측됨

→ 결과: 잘못된 라우팅, 불필요한 rejection, bad feedback cycle

**P2. Load balancing과 load shedding의 상호 무지**
현행 시스템에서 LB와 shedding은 서로를 모른다. Shedding 결정은 전부 instance-local이고 router와 무관하다. 이를 4-튜플로 정리하면:

| 시스템 | what | when | where | how |
|---|---|---|---|---|
| vLLM watermark | 무차별 | 도착 시 | instance | block 부족 시 대기 |
| JITServe | 무차별 | 대기 후 | instance | drop |
| QoServe | violation 예정자 | in-flight | instance | pend (relegation) |
| Mooncake | 예측 위반자 | 도착 시 | instance | reject |

→ 결과: 인스턴스 A에서 reject되는 순간 인스턴스 B에 여유가 있는 낭비 발생

**P3. SLO / service tier의 다양성**
SLO tier마다 ideal operating region이 다르고, 런타임에 섞인다. 정적 thresholding은 suboptimal해지고, 명시적 인스턴스 isolation은 fragmentation과 underutilization을 초래한다.

### 1.3 우리의 접근

**핵심 관찰**: decode의 iteration time은 batch 크기가 아니라 **batch가 점유한 KV cache 크기**로 결정된다. 그리고 KV cache는 유입(prefill + decode 성장)과 유출(완료 시 해제)의 차이로 채워지는 **수조(reservoir)**로 모델링할 수 있다.

**D1. KV cache의 flux 모델링** → 인스턴스의 load와 capacity를 유연하고 정확하게 추정
**D2. Routing과 shedding의 통합** → 둘이 `headroom`이라는 **같은 양의 두 질문**이 됨
**D3. SLO class별 flow 분리 관찰 + 공유 자원 통합 관리** → 정적 파티션 없이 soft binning이 창발

### 1.4 기여

1. Multi-instance LLM serving에서 level 기반 신호의 한계를 실측으로 규명하고, flux가 leading indicator임을 보임
2. Routing과 admission을 단일 수량(`headroom`)으로 통합하는 제어 프레임워크 설계
3. 정적 파티셔닝(PolyServe) 없이 SLO 이질성을 다루는 동적 capacity 모델
4. 실제 시스템(vLLM + B200 멀티 인스턴스)에서의 구현 및 평가

### 1.5 선행 연구와의 위치

| 연구 | 계층 | 목적함수 | 행동 | 스코프 |
|---|---|---|---|---|
| Ao et al. (WAIT, SIC) | 단일 GPU, 이론 | throughput | delay, 정적 rate cap | 정상상태 |
| JITServe | 엔진 스케줄링 | goodput | 스케줄 + timeout drop | 단일 인스턴스 |
| QoServe | 엔진 스케줄링 | multi-QoS | 스케줄 + pend | 단일 인스턴스 + RR LB |
| PolyServe | provisioning | multi-SLO | 정적 파티션 + autoscale | 클러스터 |
| **FluidServe** | **node 조율** | **goodput @ attainment** | **route + pend/reject** | **멀티 인스턴스, transient** |

---

## Part 2. 시스템 개요

### 2.1 위치

```
              client requests (+ SLO tier label)
                          │
              ┌───────────▼────────────────┐
              │        FluidServe          │  ← node-level, router에 병치
              │  C1 LengthTracker          │
              │  C2 FluxEstimator          │
              │  C3 CapacityCalibrator     │
              │  C4 DecisionEngine         │
              │  C5 AdmissionTimingGuard   │
              │  C6 OuterLoopTuner         │
              │  ┌──────────────────────┐  │
              │  │ C7 DispatchQueue     │  │  ← 요청을 여기 붙잡아 둠
              │  │  (late binding)      │  │     (재배치 비용 = 0)
              │  └──┬────┬────┬────┬────┘  │
              └─────│────│────│────│───────┘
         dispatch   │    │    │    │   (얕은 엔진 큐 유지)
              ┌─────▼┐┌──▼──┐┌▼────┐┌▼────┐
              │ E0   ││ E1  ││ E2  ││ E3  │  ← vLLM instances (수정 없음)
              └──────┘└─────┘└─────┘└─────┘
```

> **핵심 원리 — 큐를 제어 가능한 곳에 둔다.**
> 엔진 큐에 요청이 쌓이면 FluidServe는 재배치도, 재정렬도, shed도 할 수 없다. 네트워킹의 bufferbloat 회피 원칙(제어할 수 없는 하류 버퍼에 큐를 쌓지 말 것)과 동일하다. 요청을 라우터에 붙잡아 두고 엔진 큐는 **얕게** 유지함으로써 결정권을 보존한다.

### 2.2 설계 원칙 `[확정]`

**원칙 1 — Engine agnostic.**
엔진 내부 정책(chunked prefill 여부, SLO-aware scheduling, eviction 정책, batch 구성)을 **알지 못한다고 가정**한다. 엔진에 대한 개입은 "요청을 보낸다 / 안 보낸다"뿐이다. 엔진이 무엇을 하든 관측된 `(occupancy → latency)` 관계에 흡수된다.

**원칙 2 — 온라인 신호는 정확한 양만 쓴다.**
`occupancy`는 측정값이 아니라 회계값(할당된 블록 수)이므로 노이즈가 없다. Variance가 큰 양(TBT)은 **오프라인 교정 곡선 안으로 격리**한다. 매 iteration의 TBT를 보고 반응하지 않는다.

**원칙 3 — 예측하지 않고 분포를 측정한다.**
Per-request output length 예측은 하지 않는다. Class별 길이 **분포**만 추적한다.

**원칙 4 — 오차는 한 방향으로만 편향시킨다.**
- outflow 과대추정 → over-admit → **eviction cascade (치명적, 회복 불가)**
- outflow 과소추정 → 약간의 underutilization (경미)

따라서 항상 보수 쪽으로 편향시킨다.

### 2.3 비목표 `[확정]`

- 엔진 내부 스케줄링 (vLLM 기본값 고정)
- Node 외부 orchestration (autoscaling, cross-node)
- 모델 품질 저하를 통한 degradation (smaller-model routing)
- Per-request output length 예측

---

## Part 3. 상태 모델 및 수식

### 3.1 기호

| 기호 | 의미 |
|---|---|
| `i` | 인스턴스 인덱스 |
| `c` | SLO class |
| `r` | 요청 |
| `k` | horizon (iteration 개수) |
| `M_i` | 현재 KV occupancy (토큰) |
| `C_i` | 물리 KV capacity (토큰) |
| `cap_i(t)` | SLO-feasible occupancy 상한 |
| `n_i` | running request 수 |
| `j_r` | 요청 r이 지금까지 생성한 토큰 수 |
| `w_r` | 요청 r의 KV footprint (input + 생성분) |
| `l₀(r)` | 요청 r의 prompt 길이 |

### 3.2 핵심 수식

**Horizon을 iteration 개수로 세는 이유** `[제안]`:
wall-clock으로 잡으면 "τ초 안에 몇 iteration이 도는가"가 iteration time에 의존하고, iteration time은 occupancy의 함수이며, occupancy는 제어 대상이다 → 순환 참조. iteration 개수로 잡으면 decode inflow가 정확히 `n_i × k`로 **결정적**이 된다.

```
inflow_i(k)  = n_i · k                          # decode 성장 (결정적, 제어 불가)
             + Σ_{admitted} l₀(r)               # admission (유일한 제어 변수)

outflow_i(k) = Σ_{r ∈ running_i} p_c(j_r, k) · w_r     # 확률적

proj_i(k)     = M_i + inflow_i(k) − outflow_i(k)
headroom_i(k) = cap_i(t) − proj_i(k)
```

**완료 확률 (조건부 생존확률)**:
```
p_c(j, k) = [ S_c(j) − S_c(j+k) ] / S_c(j)
```
`S_c(j) = P(출력 길이 > j)` = class c의 생존함수.

> **왜 평균 길이가 아닌가**: heavy-tail 분포에서 "평균 길이에 도달하면 끝난다"고 가정하면 오래된 요청의 완료를 체계적으로 **과대예측**한다. 예: 평균 250인 분포에서 j=150인 요청이 100 토큰 안에 끝날 실제 확률이 0.29인데, 평균 기반은 1.0으로 계산 → outflow 3.5배 과대추정 → over-admit → cascade. 오차가 치명적인 방향으로 발생한다.

**보수화**:
```
Var[outflow] = Σ_r p_r(1−p_r) · w_r²
outflow_safe = E[outflow] − z · √Var[outflow]
```

### 3.3 통합 원리 `[제안 — 설계의 중심 주장]`

`headroom`이 routing과 admission을 통합한다:

```
Routing   = argmax_i headroom_i(k),  단 headroom_i ≥ cost(r)
Admission = 모든 i에서 headroom_i < cost(r) 이면 shed
```

두 결정이 **같은 양에 대한 두 질문**이 된다 → P2(LB/shedding 상호 무지)가 모델 차원에서 해소.

---

## Part 4. 컴포넌트 상세

---

### C1. LengthDistributionTracker

**목적**
Class별 출력 길이 분포를 유지하여, "이미 j 토큰 생성한 요청이 앞으로 k iteration 안에 끝날 확률"을 제공한다.

**입력**
- 완료 이벤트: `(class, input_len, output_len, completed_at)`
- 진행 중 요청 스냅샷: `(class, j_r)` — **우측 절단(right-censored) 관측치**

**출력**
- `S_c(j)`: class c의 생존함수 (이산 그리드)
- `p_c(j, k)`: 조건부 완료 확률

**설계**
- **Kaplan-Meier estimator** 사용 `[제안]`
  완료된 요청만 세면 짧은 요청이 먼저 완료되어 표본에 과대 대표되는 **censoring bias**가 발생한다. 진행 중 요청을 "길이 > j"라는 절단 관측치로 반영해야 한다.
- 길이 그리드: 로그 스케일 bin 권장 (예: 1, 2, 4, ..., 32768) — heavy-tail 대응
- 시간 감쇠: EWMA 또는 sliding window로 분포 드리프트 추적
- Class 정의: 우선 SLO tier 기준. 필요 시 `(SLO tier, application/endpoint)`로 세분화 `[미결]`

**정책**
- Bin당 최소 샘플 수 `min_samples_per_bin` 미만이면 해당 class는 **fallback 분포** 사용 (전체 pooled 분포 또는 보수적 상수)
- Cold start: 초기 `warmup_requests` 동안은 보수적 fallback

**튜너블 파라미터**

| 파라미터 | 기본값(제안) | 의미 | 효과 |
|---|---|---|---|
| `length_grid_type` | log2 | 길이 bin 방식 | 세밀도 vs 샘플 밀도 |
| `distribution_window` | 5000 requests | 분포 추적 윈도우 | 클수록 안정, 드리프트 추종 느림 |
| `ewma_alpha_length` | 0.05 | 분포 갱신 감쇠율 | 클수록 최근 반영↑, 노이즈↑ |
| `min_samples_per_bin` | 30 | bin 신뢰 최소 샘플 | 낮으면 노이즈, 높으면 fallback 빈번 |
| `warmup_requests` | 500 | cold start 임계 | — |
| `use_kaplan_meier` | true | 절단 보정 사용 | false면 짧은 요청 편향 |

**예상 효과**
- Outflow 추정의 체계적 편향 제거
- Heavy-tail 워크로드(agentic)에서 특히 중요 — 평균 기반 대비 over-admit 대폭 감소

**실패 모드**
- Class 정의가 부적절하면 분포가 다봉(multimodal)이 되어 조건부 확률이 무의미 → class 세분화 필요 신호
- 워크로드 급변 시 분포가 stale → `distribution_window` 축소 또는 change detection

---

### C2. FluxEstimator

**목적**
인스턴스별 `inflow`, `outflow`, `proj`, `headroom`을 계산한다. **시스템의 핵심 상태 추정기.**

**입력**
- 엔진 metric: `M_i` (KV occupancy), `n_i` (running 수), 요청별 `j_r`, `w_r`
- C1의 `p_c(j, k)`
- C3의 `cap_i(t)`

**출력**
- `inflow_i(k)`, `outflow_i(k)`, `proj_i(k)`, `headroom_i(k)`
- 신뢰구간 정보 (`Var[outflow]`)

**설계**
```python
def estimate(instance_i, k):
    # decode inflow: 결정적
    decode_inflow = instance_i.n_running * k

    # outflow: 확률적, 보수 편향
    e_out, var_out = 0, 0
    for r in instance_i.running_requests:
        p = C1.completion_prob(r.slo_class, r.j, k)
        e_out += p * r.kv_footprint
        var_out += p * (1 - p) * r.kv_footprint ** 2
    outflow_safe = e_out - z_safety * sqrt(var_out)
    outflow_safe = max(0, outflow_safe)

    proj = instance_i.M + decode_inflow - outflow_safe
    headroom = C3.cap(instance_i) - proj
    return FluxState(decode_inflow, outflow_safe, proj, headroom)
```

**정책**
- `outflow_safe`는 0 이하로 내려가지 않도록 클램프
- Pending admission(방금 라우팅했지만 아직 엔진 metric에 반영 안 된 요청)을 **in-flight 보정**으로 더해야 함 — 그렇지 않으면 짧은 시간에 같은 인스턴스로 과다 라우팅 `[중요]`

**튜너블 파라미터**

| 파라미터 | 기본값(제안) | 의미 | 효과 |
|---|---|---|---|
| `k_horizon` | 100 iterations | 예측 지평 | **핵심 파라미터**. 짧으면 근시안(limit cycle), 길면 추정 오차 지배 |
| `z_safety` | 1.65 (≈95%) | outflow 보수화 계수 | 클수록 안전, utilization↓ |
| `metric_poll_interval` | 100 ms | 엔진 metric 폴링 주기 | 짧을수록 반응↑, 오버헤드↑ |
| `inflight_correction` | true | 미반영 admission 보정 | false면 순간 과다 라우팅 |
| `growth_estimate_mode` | `expected` \| `conservative` | 신규 요청의 k 동안 성장 추정 | conservative는 상위 분위수 사용 |

**예상 효과**
- Level 신호 대비 **선행(leading) 경보** — 넘치기 전에 감지
- Ao et al.이 증명한 one-step feasibility의 limit cycle 회피

**실패 모드**
- `k`가 너무 작으면 one-step feasibility와 동일해짐 → limit cycle
- Prefix caching 활성 시 `w_r` 가산이 성립하지 않음 → `[미결 Q5]`

---

### C3. CapacityCalibrator

**목적**
각 인스턴스의 **SLO-feasible occupancy 상한 `cap_i(t)`**를 관측으로부터 교정한다.
**iteration time을 예측하지 않는다. 오직 관측된 관계를 누적한다.** `[확정: 설계 요구사항]`

**입력**
- `(occupancy, prefill_tokens_in_batch, observed_iteration_time)` 샘플 스트림
- 현재 인스턴스에 배치된 요청들의 SLO tier

**출력**
- `cap_i(t)`: 현재 mixture 기준 SLO-feasible occupancy 상한
- 교정 곡선의 신뢰도 지표

**설계**

2단계로 분리한다:

**(a) 오프라인 교정 곡선 — 안정적, 인스턴스의 성질**
```
(occupancy_bin, prefill_tokens_bin) → iteration_time 분위수 테이블
```
예:
| occupancy bin | prefill bin | p50 | p90 | p95 | p99 | samples |
|---|---|---|---|---|---|---|
| 50–60% | 0 | 24 | 27 | 28 | 33 | 4210 |
| 60–70% | 0 | 30 | 34 | 35 | 41 | 3980 |
| 70–80% | 0 | 41 | 46 | 48 | 55 | 3110 |
| 80–90% | 0 | 60 | 68 | 71 | 84 | 1870 |

- iteration이 10~50 ms이므로 인스턴스당 **초당 20~100 샘플**이 쌓인다 → 분 단위로 bin당 수천 샘플 확보
- **variance는 여기서 흡수된다.** SLO 자체가 분위수(p95 TBT ≤ 50ms)이므로 variance는 극복 대상이 아니라 모델링 대상이다

**(b) 온라인 cap 결정 — mixture에 따라 실시간 변동**
```python
def cap(instance_i):
    tightest_tbt = min(r.slo.tbt for r in instance_i.requests if r.slo.tbt)
    if tightest_tbt is None:
        return C_i * loose_cap_ratio          # TBT 제약 없음
    # 교정 곡선에서 목표 분위수가 tightest_tbt 이하인 최대 occupancy
    return curve.max_occupancy_satisfying(
        quantile=attainment_target,           # 예: 0.90 → p90
        threshold=tightest_tbt
    )
```

> **핵심**: 곡선은 안정적이고 오래 누적하지만, `cap_i`는 "지금 누가 얹혀 있느냐"에 따라 **매 결정마다 다시 읽는다.** 이것이 PolyServe식 정적 파티셔닝과의 구조적 차이.

**TTFT / E2EL은 cap 교정에 쓰지 않는다** `[확정]`

| SLO 종류 | 관측 지연 | 제어 레버 |
|---|---|---|
| TBT/TPOT | **0** (iteration time이 곧 신호) | occupancy cap (C3) |
| TTFT | 첫 토큰 시점 | admission timing (C5) |
| E2EL/TTLT | 완료 시점 (가장 느림) | 위 둘의 파생 결과 |

TTFT는 컨트롤러가 만들어내는 **결과값**이므로 신호로 부적절하다.

**정책**
- Bin당 샘플 부족 시 → 인접 bin 보간 또는 보수적 fallback cap
- Bin 내 산포가 과대하면(`IQR / median > spread_threshold`) → 조건화 변수 추가 필요 신호를 로깅 `[미결 Q1]`
- Preemption 이벤트 관측 시 → 해당 bin을 즉시 "위험"으로 마킹하고 cap 하향

**튜너블 파라미터**

| 파라미터 | 기본값(제안) | 의미 | 효과 |
|---|---|---|---|
| `occupancy_bin_width` | 5% | occupancy bin 폭 | 좁으면 정밀, 샘플 희소 |
| `prefill_bin_edges` | [0, 512, 2048, 8192] | prefill 토큰 bin | colocated 환경에서 필수 |
| `attainment_target` | 0.90 | 목표 SLO attainment | 높이면 보수적 |
| `curve_ewma_alpha` | 0.02 | 곡선 갱신 감쇠 | 하드웨어/모델 드리프트 추종 |
| `min_samples_per_cell` | 100 | 셀 신뢰 최소 샘플 | — |
| `fallback_cap_ratio` | 0.60 | 곡선 미성숙 시 보수 cap | 낮을수록 안전 |
| `loose_cap_ratio` | 0.92 | TBT 제약 없을 때 상한 | 물리 한계 여유 |
| `spread_threshold` | 0.35 | 산포 경보 임계 | Q1 진단용 |

**예상 효과**
- Engine-agnostic 유지하면서 SLO-aware capacity 확보
- 하드웨어/모델/엔진 설정이 바뀌어도 자동 재교정

**실패 모드**
- 엔진이 내부에서 SLO-aware 스케줄링(예: dynamic chunking)을 하면 `occupancy → latency`가 함수가 아니게 됨
  → **방어 논리**: "우리는 엔진이 SLO를 *맞출 수 있는* 영역에 머물게 보장한다. 그 안에서 누구에게 무엇을 줄지는 엔진의 일" — 역할 분담을 명시하면 agnostic 주장이 오히려 강해짐 `[미결 Q6]`

---

### C4. DecisionEngine

**목적**
요청 도착 시 **어디로 보낼지 / 보내지 않을지**를 결정한다. Routing과 admission을 하나의 결정으로 통합.

**입력**
- 요청 `r`: `(slo_class, prompt_len, arrival_time, priority_hint?)`
- 모든 인스턴스의 `headroom_i(k)`, `cap_i(t)`
- C1의 class별 길이 분포

**출력**
- `Decision = ROUTE(i) | PEND(deadline) | REJECT(reason)`

**설계**

```python
def decide(request r):
    cost = r.prompt_len + growth_estimate(r.slo_class, k_horizon)

    feasible = [i for i in instances if headroom_i(k) >= cost]

    if feasible:
        scores = {}
        for i in feasible:
            ext = externality(i, r)
            scores[i] = headroom_i(k) - alpha * ext
        return ROUTE(argmax(scores))
    else:
        return shed_policy(r)
```

**Externality 항 — PolyServe와의 차별점이 사는 곳** `[제안, 검증 필요 Q4]`

문제: tight TBT 요청이 여러 인스턴스로 분산되면 **모든 인스턴스가 그 tight capacity에 bound**된다.

정량 예시 (엔진 4개, `cap_loose`=90%, `cap_tight`=55%):

| 배치 | 사용 가능 총량 |
|---|---|
| tight를 4엔진에 분산 | 4 × 55 = **220** |
| tight를 1엔진에 집중 | 55 + 3×90 = **325** |

→ **약 48% 차이.** 미미하지 않다.

```python
def externality(i, r):
    cap_before = C3.cap(i)
    cap_after  = C3.cap_if_added(i, r)     # r을 넣었다고 가정한 cap
    drop = max(0, cap_before - cap_after)
    affected = len(i.running_requests)      # 영향받는 요청 수 가중
    return drop * affected_weight(affected)
```

**예상 동작**: tight-SLO 요청의 **집중(soft binning)이 최적해로 창발**한다. 정적 파티션이 아니라 결과로 나오므로, 부하가 오르면 선명해지고 내려가면 다시 섞인다.

**Tension (정직하게 명시)**: 집중은 총 capacity를 최대화하지만 **tight tier 자신의 headroom은 줄인다.** tight 부하가 한 인스턴스 용량을 넘으면 2개로 나눠야 하며, 이는 "몇 개 인스턴스를 tight에 헌납할까"라는 bin-packing 결정이 된다.

**Shed 정책**

| tier | 행동 | 근거 |
|---|---|---|
| latency-sensitive | REJECT (즉시 실패 통보) | 지연시켜도 SLO 위반 확정 |
| E2EL-sensitive | PEND (deadline 여유 내) | 늦어도 완료가 의미 있음 |
| best-effort | PEND (starvation 하한 보장) | — |

**튜너블 파라미터**

| 파라미터 | 기본값(제안) | 의미 | 효과 |
|---|---|---|---|
| `alpha_externality` | 1.0 | externality 가중 | 0이면 순수 max-headroom, 크면 강한 집중 |
| `affected_weight_mode` | `linear` \| `sqrt` \| `const` | 영향 요청 수 가중 방식 | 집중 강도 조절 |
| `growth_estimate_quantile` | 0.5 (또는 0.75) | 신규 요청 성장 추정 분위수 | 높이면 보수적 |
| `tie_break` | `least_loaded` \| `round_robin` | 동점 처리 | — |
| `shed_policy_map` | 위 표 | tier별 행동 | 실험 변수 |
| `pend_queue_max` | 1000 | pend 큐 상한 | 초과 시 reject 전환 |
| `enable_externality` | true | ablation 스위치 | **Q4 검증용** |

**예상 효과**
- Salvageable rejection(다른 인스턴스에 여유가 있는데 reject) 제거
- Soft binning으로 fleet 총 capacity 증대

**실패 모드**
- `alpha`가 과도하면 과집중 → tight tier의 TTFT 큐잉 악화
- externality가 soft binning을 만들지 못하면 명시적 tier-affinity 항 필요 `[Q4]`

---

### C5. AdmissionTimingGuard

**목적**
PEND된 요청이 무한정 대기하지 않도록, TTFT deadline을 감시하고 강제 결정한다.

**입력**
- Pend 큐: `(request, arrival_time, slo)`
- 현재 시각, 인스턴스 상태

**출력**
- 만료 임박 요청에 대한 재평가 트리거 → ROUTE 또는 REJECT

**설계**
```
admit_deadline(r) = arrival_r + SLO_TTFT(r) - prefill_time_estimate(r) - safety_margin
```
- `prefill_time_estimate`는 C3의 교정 곡선에서 prefill_tokens 기준으로 조회 (예측이 아니라 관측 기반 조회)
- Deadline이 지나면 즉시 REJECT (조용히 실패시키지 않고 명시적 통보)

**정책**
- 재평가 주기마다 pend 큐를 deadline 순으로 스캔
- Deadline 여유 비율이 `urgent_ratio` 이하인 요청은 우선 배치 시도
- Best-effort는 deadline이 없으므로 `starvation_timeout` 기준으로 강제 승격

**튜너블 파라미터**

| 파라미터 | 기본값(제안) | 의미 | 효과 |
|---|---|---|---|
| `pend_reeval_interval` | 50 ms | 재평가 주기 | 짧으면 반응↑, CPU↑ |
| `ttft_safety_margin` | 200 ms | deadline 여유 | 크면 보수적 |
| `urgent_ratio` | 0.3 | 긴급 판정 임계 | — |
| `starvation_timeout` | 60 s | best-effort 강제 승격 | — |

**예상 효과**
- Pend가 조용한 SLO 위반으로 변질되는 것 방지
- QoServe식 relegation이 transient overload를 전제하는 것과 달리, **sustained overload에서도 pend 큐가 발산하지 않음**

---

### C6. OuterLoopTuner

**목적**
느린 타임스케일에서 안전 마진과 목표치를 조정하여 **효율(utilization)**을 높인다.
**안전은 inner loop(C2/C4 feasibility)가 담당하므로, 이 루프가 느려도 시스템이 위험해지지 않는다.**

**입력**
- 관측된 SLO attainment (class별)
- Token goodput
- Preemption / eviction 이벤트율
- Rejection율, salvageable rejection율

**출력**
- `z_safety`, `fallback_cap_ratio`, `alpha_externality` 등의 조정

**설계 — 2-timescale cascade control**

| 루프 | 주기 | 신호 | 역할 |
|---|---|---|---|
| Inner (안전) | iteration ~ 수백 ms | `headroom` feasibility | hard guard. **attainment 신호 미사용** |
| Outer (효율) | 수 초 | attainment, goodput, preemption율 | 마진 미세조정 |

**비대칭 반응 정책** `[중요]`
- **올릴 때**: additive, 느리게. attainment가 목표 이상으로 `stable_windows` 연속 유지될 때만 `z_safety`를 조금 낮춤
- **내릴 때**: 즉시, multiplicative. **preemption/eviction 이벤트 자체가 트리거**
- 근거: eviction 영역을 넘으면 우아하게 돌아오지 못하고 limit cycle로 떨어짐 (Ao et al.)

```python
def outer_loop_tick():
    if preemption_rate > preempt_threshold:
        z_safety *= backoff_factor          # 즉시 후퇴
        return
    if attainment >= attainment_target + hysteresis_band \
       and stable_count >= stable_windows:
        z_safety = max(z_min, z_safety - z_step)   # 점진적 완화
```

**튜너블 파라미터**

| 파라미터 | 기본값(제안) | 의미 | 효과 |
|---|---|---|---|
| `outer_interval` | 5 s | 외부 루프 주기 | — |
| `z_step` | 0.05 | 완화 스텝 | 크면 빠른 수렴, 진동 위험 |
| `backoff_factor` | 1.5 | 후퇴 배수 | 클수록 보수적 |
| `preempt_threshold` | 0.01 (1%) | 후퇴 트리거 | 낮으면 민감 |
| `stable_windows` | 3 | 완화 전 안정 확인 횟수 | 클수록 신중 |
| `hysteresis_band` | 0.02 | 진동 방지 대역 | — |
| `z_min` / `z_max` | 0.5 / 3.0 | 안전계수 범위 | — |

**예상 효과**
- "attainment는 SLI라 피드백이 느리다"는 문제가 구조적으로 해소 — 느린 신호는 효율 조정에만 쓰임
- Limit cycle 회피

---

### C7. DispatchQueue `[제안 — 설계 변경]`

**목적**
요청을 엔진에 즉시 밀어넣지 않고 **라우터에 붙잡아 둠으로써**, (a) 잘못된 초기 결정을 무료로 되돌리고, (b) 결정을 정보가 최대인 시점까지 미루며(late binding), (c) shed/재정렬 권한을 유지한다.

**핵심 통찰 — 되돌림 비용의 3단계**

| 요청 상태 | 되돌리는 수단 | 비용 |
|---|---|---|
| 라우터 큐에 대기 (KV 없음) | **재배치 / 재선택** | **0** |
| Prefill 완료, decoding 초기 (KV 작음) | migration | 낮음 |
| 장시간 decoding (KV 큼) | migration | 높음 |

→ C7은 첫 번째 층을 담당한다. Migration(Q3)과 **경쟁이 아니라 상보 관계**이며, 비용이 0이므로 **항상 먼저 시도되어야 한다.**

**입력**
- C4의 결정 결과 (선호 엔진 + 점수)
- 모든 인스턴스의 실시간 `headroom_i(k)`, `cap_i(t)`
- 엔진별 현재 큐 깊이

**출력**
- `DISPATCH(request, engine_i)` — 실제 엔진 전송
- 큐 상태 (대기 요청, 각 요청의 deadline 여유)

**설계 — 단일 큐 + affinity 스코어 (권장)**

두 가지 구현 방식이 있다:

| 방식 | 장점 | 단점 |
|---|---|---|
| A. per-engine 큐 | prefix cache affinity 유지 명확, 엔진별 순서 관리 쉬움 | 재배치 로직 필요 (언제/무엇을 옮기나) |
| B. **단일 큐 + dispatch 시점 binding** | "재배치" 개념 자체가 불필요(애초에 안 묶음), 항상 최신 정보로 결정 | affinity를 스코어 항으로 명시해야 함 |

**권장: B (late binding).** 요청은 엔진에 대한 *선호도*만 갖고, 실제 binding은 dispatch 순간에 결정한다. 이러면 재배치라는 별도 동작 없이 최적 배치가 자동으로 나온다. Prefix cache/세션 affinity는 스코어 항으로 반영한다.

```python
def dispatch_loop():
    while True:
        for i in instances:
            # 엔진 큐를 얕게 유지: 목표 깊이 미만일 때만 채움
            while engine_queue_depth(i) < engine_queue_target:
                candidates = [r for r in queue
                              if headroom_i(k) >= cost(r)]
                if not candidates:
                    break
                r = argmax(candidates, key=lambda r:
                        urgency(r)                       # deadline 여유
                      + affinity_weight * affinity(r, i) # prefix/세션
                      - alpha * externality(i, r))       # C4와 동일 항
                dispatch(r, i)
                queue.remove(r)
        sleep(dispatch_interval)
```

**정책**
- **엔진 큐 얕게 유지**: `engine_queue_depth(i) < engine_queue_target`일 때만 dispatch. Credit 기반 admission(Breakwater 계열)과 동일 패턴
- **Aging**: 대기 시간이 길어질수록 `urgency` 가중을 올려 starvation 방지
- **Hysteresis**: 선호 엔진이 바뀌어도 즉시 재선택하지 않고 `reassign_hysteresis` 이상의 점수 차가 있을 때만 (thrashing 방지)
- **Deadline 강제**: C5의 `admit_deadline` 도달 시 최선의 엔진으로 강제 dispatch 또는 REJECT

**새로운 tension — 엔진 큐 깊이** `[중요, 실험 필요]`

| 엔진 큐가 | 결과 |
|---|---|
| 너무 깊음 | 라우터가 제어권 상실 (현재 문제) |
| 너무 얕음 | 엔진이 좋은 batch를 구성하지 못해 throughput 손실 — vLLM은 waiting queue를 보고 chunked prefill 스케줄링을 함 |

→ `engine_queue_target`의 throughput 영향 측정이 필수 실험 항목. 이 값이 **agnostic 원칙과 성능 사이의 유일한 결합점**이다.

**튜너블 파라미터**

| 파라미터 | 기본값(제안) | 의미 | 효과 |
|---|---|---|---|
| `queue_mode` | `single_late_bind` \| `per_engine` | 큐 구조 | 위 표 참조 |
| `engine_queue_target` | 2 | 엔진에 유지할 대기 요청 수 | **핵심.** 낮으면 제어권↑ throughput↓ |
| `dispatch_interval` | 10 ms | dispatch 루프 주기 | 짧으면 반응↑, CPU↑ |
| `affinity_weight` | 0.5 | prefix/세션 affinity 가중 | 0이면 affinity 무시 |
| `reassign_hysteresis` | 0.15 | 재선택 최소 점수 차 | thrashing 방지 |
| `aging_rate` | 0.1 /s | 대기 시간당 urgency 증가 | starvation 방지 |
| `queue_max_depth` | 1000 | 라우터 큐 상한 | 초과 시 REJECT |
| `enable_late_binding` | true | **ablation 스위치** | false면 도착 즉시 binding (기존 설계) |

**예상 효과**
- 잘못된 초기 라우팅 결정의 비용이 0이 됨 → C4가 덜 보수적으로 결정 가능 (2차 효과)
- Migration 필요성 대폭 감소 (Q3의 상당 부분 해소)
- Shed 결정을 마지막 순간까지 미룰 수 있음 → 불필요한 rejection 감소
- 요청이 엔진에 갇히지 않으므로 salvageable rejection이 구조적으로 감소

**실패 모드**

| 실패 모드 | 원인 | 방어 |
|---|---|---|
| 재배치 thrashing | 점수 미세 변동에 과민 반응 | `reassign_hysteresis` |
| Starvation | 특정 요청이 계속 밀림 | aging |
| Head-of-line blocking | FIFO 큐에서 큰 요청이 뒤를 막음 | 우선순위 큐 (FIFO 금지) |
| Throughput 손실 | 엔진 큐가 너무 얕아 batch 구성 실패 | `engine_queue_target` 튜닝 |
| 큐 발산 | sustained overload에서 대기만 함 | C5 deadline + `queue_max_depth` |

---

## Part 5. 제어 플로우 (요청 단위)

**도착 경로 (arrival path)**
```
1. 요청 도착 (SLO tier label 포함)
2. C1: class 분포 조회 → cost(r) 산출
3. C5: admit_deadline(r) 계산
4. C7: DispatchQueue에 삽입 (아직 엔진에 binding하지 않음)
   4a. queue_max_depth 초과 → 즉시 REJECT
```

**Dispatch 경로 (dispatch loop, `dispatch_interval` 주기)**
```
5. C2: 모든 인스턴스의 headroom_i(k) 갱신 (in-flight 보정 포함)
6. C3: cap_i(t) 갱신 (현재 mixture 기준)
7. 각 인스턴스 i에 대해, engine_queue_depth(i) < engine_queue_target 인 동안:
   7a. feasible 후보 = { r ∈ queue : headroom_i(k) ≥ cost(r) }
   7b. 비어있지 않음 → urgency + affinity − α·externality 최대인 r 선택 → DISPATCH(r, i)
   7c. 비어있음 → 다음 인스턴스로
8. C5: deadline 임박/만료 요청 처리
   8a. 만료 임박 → 최선의 인스턴스로 강제 dispatch
   8b. 만료 → tier별 shed (REJECT 또는 계속 PEND)
```

**백그라운드**
```
9.  C6: 수 초 주기로 안전 마진(z_safety 등) 조정
10. C1/C3: 완료 이벤트와 iteration 샘플로 분포/곡선 갱신
```

> **기존 설계와의 차이**: 이전에는 도착 즉시 `ROUTE(i)`로 binding이 확정됐다. 이제는 **도착 경로와 dispatch 경로가 분리**되고, binding은 dispatch 순간에 일어난다. 그 사이 요청은 라우터 큐에 있으며 재배치 비용이 0이다.

---

## Part 6. 파라미터 카탈로그 (통합)

### 6.1 핵심 파라미터 (실험에서 스윕할 것)

| 파라미터 | 컴포넌트 | 범위 | 왜 중요한가 |
|---|---|---|---|
| `k_horizon` | C2 | 10 ~ 500 iter | **가장 중요.** 짧으면 one-step feasibility와 동일해져 limit cycle, 길면 추정 오차 지배 |
| `z_safety` | C2 | 0.5 ~ 3.0 | 안전 vs utilization의 직접 트레이드오프 |
| `attainment_target` | C3 | 0.85 ~ 0.99 | 목표 SLO 달성률 |
| `alpha_externality` | C4 | 0 ~ 5 | 0이면 순수 max-headroom (ablation baseline) |
| `engine_queue_target` | C7 | 0 ~ 32 | **agnostic 원칙과 throughput의 결합점.** 낮으면 제어권↑ batch 품질↓ |

### 6.2 구조적 스위치 (ablation용)

| 스위치 | 목적 |
|---|---|
| `enable_flux` | false면 level 기반(현행) 신호로 폴백 → **P1 검증** |
| `enable_shedding` | false면 LB only → **P2 검증** |
| `enable_routing` | false면 RR + shedding only → **P2 검증** |
| `enable_externality` | false면 tight 요청 분산 → **Q4 검증** |
| `enable_kaplan_meier` | false면 완료 요청만 사용 → censoring bias 영향 측정 |
| `outflow_mode` | `survival` \| `mean_length` → 평균 기반 대비 효과 측정 |
| `enable_late_binding` | false면 도착 즉시 엔진 binding → **C7의 가치 측정 (Q7)** |

### 6.3 필수 ablation 매트릭스 (P2 증명)

| 구성 | routing | shedding | 통합 |
|---|---|---|---|
| LB only | flux | ✗ | — |
| Shed only | RR | flux | — |
| 독립 결합 | flux | flux | ✗ (서로 모름) |
| **FluidServe** | flux | flux | ✓ (headroom 공유) |

**"독립 결합" 대비 유의미한 갭이 없으면 co-design 주장이 무너진다.** 이것이 논문의 핵심 실험.

---

## Part 7. 인터페이스 명세

### 7.1 엔진에서 읽는 metric (read-only)

| 필드 | 타입 | 출처(vLLM) | 용도 |
|---|---|---|---|
| `kv_cache_usage` | float [0,1] | `gpu_cache_usage_perc` | `M_i` |
| `num_running` | int | scheduler stats | `n_i` |
| `num_waiting` | int | scheduler stats | 진단 |
| `request_states[]` | list | per-request tracking | `j_r`, `w_r` |
| `iteration_time` | float (ms) | step latency | C3 교정 |
| `prefill_tokens_in_batch` | int | scheduler stats | C3 조건화 |
| `preemption_count` | counter | scheduler stats | 안전 트리거 |
| `finished_requests[]` | list | completion events | C1 갱신 |

> 일부 필드는 vLLM 기본 metric에 없을 수 있음 → 최소 침습적 exporter 추가 필요. **엔진 정책은 건드리지 않는다.**

### 7.2 FluidServe 외부 API

```
POST /v1/completions          # OpenAI 호환, slo_tier 확장 필드
  → 200 (routed)
  → 202 (pended, retry_after 포함)
  → 429 (rejected, reason 포함)

GET  /fluidserve/state           # 디버깅/계측
  → { instances: [{ id, M, cap, headroom, n_running, ... }],
      pend_queue_len, recent_decisions }

GET  /fluidserve/curves          # 교정 곡선 덤프
POST /fluidserve/config          # 런타임 파라미터 변경 (실험용)
```

---

## Part 8. 구현 마일스톤 (Claude Code용)

### M1. 관측 계층 (선행 — 설계 검증용)
- 엔진 metric exporter + 수집기
- 오프라인 분석 스크립트:
  - **Q1 검증**: occupancy bin별 TBT 산포 플롯 → cap 교정 가능성 판정
  - **Q3 검증**: request duration 분포 vs class mixture autocorrelation → migration 필요성 판정
  - Kaplan-Meier 생존함수 추정 및 시각화
- **산출물**: Q1/Q3에 대한 답. 이게 안 나오면 이후 설계가 흔들림

### M2. 추정기 (C1 + C2 + C3)
- 오프라인 리플레이 모드로 먼저 검증 (기존 로그 재생)
- `headroom` 예측 vs 실측 occupancy 오차 측정
- Flux 경보의 **lead time** vs level/queue 신호 비교 (동일 FP rate 기준)

### M3. 결정 엔진 (C4 + C5)
- Routing만 먼저 (shedding 비활성)
- 그다음 shedding 추가
- Externality 항은 스위치로 분리 (Q4 검증)

### M4. 제어 루프 (C6)
- Inner/outer 분리 검증
- 비대칭 반응 튜닝

### M5. 평가
- Ablation 매트릭스 (§6.3)
- Baseline 비교

---

## Part 9. 미해결 항목

| # | 질문 | 무엇이 해결하나 | 위험도 |
|---|---|---|---|
| **Q1** | occupancy 단독 binning으로 TBT 곡선이 서나? | M1의 산포 플롯 | **높음** — 안 되면 `cap_i` 자체가 무너짐 |
| **Q2** | `k_horizon` 최적값? | 후향 스윕 | 중 |
| **Q3** | Migration이 필요한가? | `T_req` vs `T_mix` 비교. **단, C7(late binding)이 "잘못된 초기 결정" 케이스를 무료로 처리하므로 남는 것은 "commit 이후 상황 변화" 케이스뿐** | 낮음~중 (C7로 하향) |
| **Q7** | `engine_queue_target`을 얼마나 얕게 가져갈 수 있나? | 값을 스윕하며 throughput/제어권 트레이드오프 측정 | **높음** — 너무 얕으면 엔진 batch 품질 저하로 throughput 손실 |
| **Q4** | externality가 soft binning을 만드나? | 시뮬레이션 후 실측 | **높음** — 안 되면 PolyServe 대비 라우팅 차별화 소멸 |
| **Q5** | Prefix caching 회계 처리 | 스코프 결정 | 중 |
| **Q6** | 엔진이 SLO-aware 스케줄링 시 곡선 붕괴 | 역할 분담 명시로 방어 | 낮음 |

> **Q1과 Q4가 가장 약한 고리.** 둘 다 기존 로그 + 짧은 시뮬레이션으로 판정 가능하므로, **본 구현 착수 전에 M1을 먼저 수행할 것.**

---

## Part 10. 계측 및 평가 지표

### 10.1 모델 검증
- `headroom` 예측 vs 실측 occupancy 오차 (MAE, 편향 방향)
- `p_c(j,k)` calibration plot (예측 확률 vs 실제 완료율)
- 교정 곡선 bin별 산포

### 10.2 신호 우월성 (P1 증명)
- Flux 경보의 **lead time** vs level/queue/attainment 신호
- 동일 false-positive rate에서의 ROC/AUC 비교

### 10.3 낭비 정량화 (P2 증명)
- **Salvageable rejection rate**: reject/preempt 시점에 다른 인스턴스에 여유가 있었던 비율
- **Headroom-at-violation**: SLO 위반 순간 fleet 총 여유

### 10.4 최종 성능
- Token goodput @ attainment target
- SLO attainment (class별)
- Rejection rate, pend latency 분포
- **PolyServe 대비**: 같은 GPU 수에서 goodput, 또는 같은 goodput에서 GPU 수 (fragmentation 비용)

### 10.5 안정성
- Limit cycle 관측 여부 (occupancy 시계열의 진동 주기/진폭)
- Preemption/eviction 이벤트율
- Transient burst 후 회복 시간 (hysteresis)

---

## 부록 A. 실패 모드 요약

| 실패 모드 | 원인 | 방어 | 담당 |
|---|---|---|---|
| Eviction cascade | outflow 과대추정 → over-admit | 보수 편향(`z_safety`), preemption 즉시 반응 | C2, C6 |
| Limit cycle | level만 보는 bang-bang 제어 | flux를 제어변수에 포함, hysteresis, 연속 제어 | C2, C6 |
| Over-rejection → throughput 붕괴 | cap 과소추정 | outer loop 점진 완화, salvageable rejection 감시 | C6 |
| Tight-SLO 전체 확산 | externality 무시 라우팅 | externality 항 | C4 |
| Stuck state | 장수명 tight 요청이 cap 고정 | migration (Q3 결과에 따라) | 미정 |
| Pend 무한 증가 | deadline 미감시 | AdmissionTimingGuard | C5 |
| 순간 과다 라우팅 | 미반영 admission | in-flight 보정 | C2 |
| Cold start 오작동 | 분포/곡선 미성숙 | 보수적 fallback | C1, C3 |
| 잘못된 초기 배치 고착 | 도착 즉시 binding | late binding (재배치 비용 0) | C7 |
| 재배치 thrashing | 점수 미세 변동에 과민 반응 | `reassign_hysteresis` | C7 |
| Starvation | 특정 요청이 계속 밀림 | aging | C7 |
| Head-of-line blocking | FIFO 큐 | 우선순위 큐 (FIFO 금지) | C7 |
| Throughput 손실 | 엔진 큐 과소 | `engine_queue_target` 튜닝 | C7 |

---

## Part 11. 가정, 한계, 실패 조건

> 이 절은 방어용이 아니라 **설계 판단의 근거**다. 아래 가정 중 무엇이 깨지는지에 따라 어떤 컴포넌트를 고쳐야 하는지가 결정된다.

### 11.1 명시적 가정

| # | 가정 | 깨지면 영향받는 곳 | 검증 방법 |
|---|---|---|---|
| A1 | KV occupancy가 capacity의 지배적 축이다 | 전체 모델 | prefill-heavy 워크로드에서 KV 여유 상태의 SLO 위반 빈도 측정 |
| A2 | `occupancy → latency`가 안정적인 함수다 | C3 → `cap_i` | bin별 산포 (Q1) |
| A3 | Class별 출력 길이 분포가 준정상적(quasi-stationary)이다 | C1 → outflow | 윈도우별 분포 KS 검정 |
| A4 | 요청 간 길이가 class 내에서 독립이다 | C1의 분산 추정 | agentic job 내 자기상관 측정 |
| A5 | 엔진들이 상호 교환 가능하다 (동일 모델/하드웨어) | C4 라우팅 | — (설계 전제) |
| A6 | 요청 하나가 capacity 대비 충분히 작다 (fluid 근사) | C2 | 요청 크기 / capacity 비율 분포 |
| A7 | 노드 전체 capacity가 대체로 수요를 감당한다 | 시스템 가치 자체 | 부하 스윕에서 sustained 구간 비율 |

### 11.2 시스템 수준 근본 한계

**L1. 메모리는 유일한 capacity 축이 아니다 (가장 중요)**
전체 모델이 KV occupancy를 capacity의 통화(currency)로 삼는다. 그러나:
- Prefill-dominant 부하는 KV를 크게 늘리지 않으면서 compute를 포화시킨다
- **특히 B200처럼 HBM이 큰 하드웨어에서는 메모리보다 compute/latency가 먼저 묶일 가능성이 높다**
- `cap_i`가 `occupancy → TBT` 곡선에서 나오므로 *부분적으로는* compute 효과를 흡수하지만, **flux 예측(토큰 유입/유출)은 compute를 전혀 모델링하지 않는다**

→ **결과**: prefill 폭주형 overload에 대해 FluidServe는 눈이 멀 수 있다.
→ **완화**: C3의 `prefill_tokens` 조건화가 1차 방어. 부족하면 flux를 (KV 토큰, compute 단위) 2차원 벡터로 확장해야 함 — **설계 확장 후보**

**L2. Fluid 근사의 이산성(lumpiness) 문제**
Fluid 모델은 "많은 작은 단위"를 전제한다. 그러나 LLM 요청은 크기 편차가 100배에 이르고(1k prompt vs 100k prompt), 요청 하나가 capacity의 수 %를 차지할 수 있다.
- 유입이 연속적이지 않고 **덩어리(lumpy)** 로 들어옴
- 결과적으로 tight-SLO 집중 문제(§C4)처럼 **bin-packing 성격**이 드러남
- Fluid 예측은 평균적으로 맞아도 개별 dispatch 순간에는 크게 틀릴 수 있음

→ **완화**: `z_safety`의 보수화, 그리고 대형 요청에 대한 별도 취급(예: 크기 임계 초과 시 전용 feasibility 검사)

**L3. 탐색-교정 결합 (self-limiting learning) — 미묘하지만 실재하는 함정**
C3는 관측된 데이터에서 `occupancy → latency` 곡선을 배우고, C6는 그 곡선을 근거로 안전 마진을 조정한다. 그런데:

> C6가 보수적으로 운영하면 → 시스템이 높은 occupancy에 도달하지 않음 → **C3가 고(高) occupancy 구간의 샘플을 영원히 못 봄** → 그 구간의 곡선이 미성숙 → 보수적 fallback 유지 → 다시 처음으로

**자기강화 하향 루프**다. 두 적응 루프가 결합되어 있어 발생한다.

→ **완화 (필수)**:
- 배포 전 **오프라인 프로파일링으로 곡선을 시딩** (부하 스윕으로 전 구간 샘플 확보)
- 또는 저부하 구간에서 의도적 탐색(occupancy를 일시적으로 밀어 올려 샘플 수집)
- 곡선의 커버리지 지표를 계측하여 미탐색 구간을 명시적으로 추적

**L4. `cap_i`의 불연속성**
`cap_i`는 인스턴스에 있는 요청 중 **가장 tight한 SLO**로 정의된다 — 즉 변하는 집합에 대한 `min`이다.
- Tight 요청이 하나 들어오면 `cap_i`가 **계단식으로 하락**, 완료되면 계단식 상승
- `headroom_i`가 불연속적으로 점프 → dispatch 결정이 chattering할 수 있음

→ **완화**: `cap_i`에 EWMA 평활 적용, 또는 tight 요청 배치 시 일정 기간 commitment(그 인스턴스의 cap을 유지) 부여

**L5. 근시안적(myopic) 결정**
C4/C7의 결정은 요청 단위 greedy이고 미래 도착에 대한 lookahead가 없다. 전역 최적 packing과 괴리가 생긴다. 특히 tight-SLO 집중 결정은 본질적으로 온라인 bin-packing이라 경쟁비(competitive ratio) 손실이 불가피하다.

→ **입장**: 최적성을 주장하지 않는다. "현행 신호 대비 개선"으로 주장 범위를 한정한다.

**L6. 노드 스코프 — capacity를 늘릴 수 없다**
Autoscaling이 out of scope이므로, 노드 전체 수요가 지속적으로 capacity를 초과하면 할 수 있는 것은 shed뿐이다. Pend 큐가 자라고 reject가 늘어난다.

→ **결과**: **FluidServe의 가치는 "capacity가 거의 충분한" 구간에 집중된다.** 이 구간을 명시적으로 규정하고 평가해야 한다 (§11.4)

**L7. Adversarial / 병리적 워크로드**
Class 조건부 추정에 의존하므로, "싸 보이지만 길게 디코딩하는" 요청을 지속적으로 보내는 클라이언트는 outflow 추정을 체계적으로 붕괴시킨다. 본 설계에는 클라이언트 단위 격리나 fairness 메커니즘이 없다.

→ **범위 밖으로 명시**하거나, per-tenant flux 회계를 확장으로 추가

**L8. 이중 스케줄링 (double scheduling)**
C7이 dispatch 순서를 정하고, 엔진도 내부에서 자체 스케줄링을 한다. 두 계층의 목적이 어긋나면 서로의 결정을 상쇄할 수 있다.

→ **방어 논리**: "우리는 엔진이 SLO를 *맞출 수 있는* 영역에 머물게 보장한다. 그 안에서 누구에게 무엇을 줄지는 엔진의 일" — 역할 분담 명시. 단, `engine_queue_target`을 극단적으로 낮추면 이 분담이 깨진다(엔진의 선택지를 없애므로)

### 11.3 컴포넌트별 실패 조건 (요약)

| 컴포넌트 | 잘 안 되는 조건 | 증상 | 진단 지표 |
|---|---|---|---|
| **C1** LengthTracker | class 내 분포가 다봉(multimodal) | 조건부 확률이 무의미 | 분포 히스토그램 다봉성 |
| | 워크로드 급변 (신규 앱 온보딩) | 분포 stale → outflow 오추정 | 윈도우 간 분포 거리 |
| | Agentic job 내 길이 상관 (A4 위반) | 분산 과소추정 → 보수화 부족 | job 내 자기상관 |
| | 신규 class cold start | fallback 지속 | class별 샘플 수 |
| **C2** FluxEstimator | 대형 요청 (A6 위반) | 개별 dispatch에서 headroom 초과 | 요청 크기/capacity 비율 |
| | `k_horizon` 과소 | one-step과 동일 → limit cycle | occupancy 진동 주기 |
| | `k_horizon` 과대 | 추정 오차 지배 → 과보수 | 예측 vs 실측 오차 |
| | Prefix caching 활성 | `w_r` 가산 불성립 → 회계 붕괴 | 예측 occupancy vs 실측 괴리 |
| | in-flight 보정 누락 | 순간 과다 dispatch | dispatch 직후 preemption |
| **C3** Calibrator | occupancy 단독으로 불충분 (A2 위반) | bin 내 산포 과대 | IQR/median |
| | 동일 총 KV, 다른 길이 구성 | 같은 bin인데 latency 다름 | 시퀀스 길이 분산 조건화 필요 |
| | 엔진 adaptive scheduling | 곡선이 함수가 아님 | 재현성 낮음 |
| | 고occupancy 구간 미탐색 (L3) | 곡선 커버리지 부족 | bin별 샘플 수 히트맵 |
| | 하드웨어 상태 변화 (thermal 등) | 곡선 stale | 예측 latency 잔차 드리프트 |
| **C4** DecisionEngine | `cap_if_added` 추정 부정확 | externality 오계산 | — |
| | `alpha` 과대 | 과집중 → tight tier TTFT 악화 | tight tier 대기 시간 |
| | `alpha` 과소 | tight 확산 → fleet capacity 손실 | 인스턴스별 cap 분포 |
| | 미래 도착 무지 (L5) | 국소 최적 | 오프라인 최적 대비 갭 |
| **C5** TimingGuard | prefill 시간 추정 오차 | deadline 오판 → 뒤늦은 reject | 추정 vs 실측 prefill |
| **C6** OuterLoopTuner | class별 요청률 낮음 | attainment 측정 노이즈 | class별 샘플 수 |
| | C3와 결합 (L3) | 자기강화 하향 루프 | occupancy 상한 정체 |
| | 워크로드 변화가 루프보다 빠름 | 추종 실패 | 마진 조정 지연 |
| **C7** DispatchQueue | `engine_queue_target` 과소 | 엔진 batch 품질 저하 → throughput↓ | GPU utilization, batch size |
| | `engine_queue_target` 과대 | 제어권 상실 (기존 문제로 회귀) | 엔진 큐 깊이 |
| | 고 RPS | dispatch 루프 자체가 병목 | 결정 지연 |
| | hysteresis 부족 | 재배치 thrashing | 재선택 횟수 |
| | aging 부족 | starvation | 대기 시간 tail |

### 11.4 FluidServe가 baseline보다 나쁠 수 있는 구간 (정직하게)

| 구간 | 왜 나쁜가 | 대응 |
|---|---|---|
| **저부하** | 모든 오버헤드(폴링, 추정, dispatch 지연)가 순손실. 라우팅 결정의 가치 없음 | 저부하 감지 시 fast-path (추정 생략, 즉시 dispatch) |
| **단일 SLO class / 균일 워크로드** | `cap_i`가 모두 같아지고 externality가 0 → 사실상 load balancing으로 축퇴. 복잡도만 추가 | 이 구간에서는 "동등하다"를 보이는 것으로 충분 |
| **지속적 극단 overload** | 어떤 정책도 대부분을 살릴 수 없음. 정교한 라우팅의 한계 이득이 0에 수렴, shedding 정책만 남음 | 이 구간은 autoscaling의 영역임을 명시 |
| **완전히 정상적(stationary)이고 예측 가능한 워크로드** | 정적 provisioning(PolyServe)이 더 적은 복잡도로 동등 성능 달성 가능 | fluctuation이 있는 워크로드에서 평가해야 공정 |
| **요청 수가 적을 때** | flux의 대수 법칙이 작동 안 함 (추정 분산 과대) → 과보수 | 최소 concurrency 조건 명시 |

> **평가 전략에의 함의**: 위 구간들을 피해가는 것이 아니라, **부하 스윕 전체를 보여주고 "어느 구간에서 이득이 나오는지"를 명시**하는 것이 정직하고 강하다. 이득 구간이 좁으면 그것 자체가 결과다.

### 11.5 한계에 대한 서술 전략 (논문용)

1. **L1(메모리 단일 축)은 선제적으로 인정**하고, "KV flux는 가장 파국적인 실패 모드(eviction cascade)를 막는 필요조건이지 SLO 보장의 충분조건이 아니다"로 범위를 한정한다.
2. **L5(근시안)에 대해 최적성을 주장하지 않는다.** "현행 신호 대비 개선"으로 주장을 좁힌다.
3. **L6(노드 스코프)를 명시**하고, autoscaling reaction lag 동안의 transient 구간이 본 시스템의 표적임을 밝힌다.
4. **L3(탐색-교정 결합)는 반드시 먼저 해결하고 서술**한다. 이걸 놓치면 시스템이 조용히 저성능으로 수렴하며, 그 원인을 찾기 어렵다.
5. §11.4의 "나쁜 구간"을 숨기지 않고 평가에 포함한다.

### 11.6 설계 확장 후보 (현 범위 밖, 한계 대응용)

| 확장 | 대응 한계 | 비용 |
|---|---|---|
| Flux를 (KV, compute) 2차원 벡터로 | L1 | 모델 복잡도 증가 |
| Migration 추가 | Stuck state (Q3) | KV 전송 비용, Llumnix 결합 |
| Per-tenant flux 회계 | L7 (adversarial) | 상태 증가 |
| 오프라인 곡선 시딩 + 능동 탐색 | L3 | 배포 전 프로파일링 시간 |
| 대형 요청 전용 경로 | L2 | 정책 분기 |


---

# 부록 B. 원안 대 구현 대조표 (2026-07-27)

원안을 쓴 뒤 EXP-22~25에서 구현·측정한 결과를 원안의 각 부분에 대응시킨 것이다.
**"다름"으로 표시된 것은 전부 측정된 이유가 있고**, 그 이유를
`fluidserve-implementation.md`의 해당 절에 적어뒀다.

## B.1 그대로 유효한 것 (구현 = 원안)

| 원안 | 상태 | 비고 |
|---|---|---|
| §3.2 `inflow = n·k`, `outflow = Σ p_c(j,k)·w_r`, `p_c = [S(j)−S(j+k)]/S(j)`, `outflow_safe = E − z√Var` | **그대로** | 코드가 이 수식 그대로다 |
| §3.2 horizon을 iteration으로 세는 논증(순환 참조 회피) | **그대로** | 코드 주석에 같은 논증이 있다 |
| 원칙 1 (engine agnostic) | **그대로** | 엔진 무수정. 요청 진행도는 step 카운터 차분으로 복원 |
| 원칙 3 (per-request 예측 안 함, 분포만) | **그대로** | 클래스 생존함수만 사용 |
| 원칙 4 (오차를 한 방향으로 편향) | **그대로** | z=1.65, prefill 비율 추정도 "비싸게 매기는 쪽"으로 편향시켰다 |
| C1 LengthDistributionTracker | **그대로** | EXP-21 10개 run, 60,042건에서 생성 |
| C2 FluxEstimator | **그대로** | |
| C3의 오프라인 시딩 (§11.2 L3의 완화책) | **그대로, 전면 채택** | 온라인 학습만으로는 L3에 걸린다는 원안 판단이 맞았다 |

## B.2 반증되거나 달라진 것

| 원안 | 구현 | 왜 | 근거 |
|---|---|---|---|
| **§3.3 통합 원리** — `Routing = argmax headroom`, `Admission = 모든 i에서 headroom < cost면 shed` | **사다리**(ROUTE/PEND/SHED/FORCE). headroom은 동률 처리로 밀림 | headroom은 인스턴스에 대해 **대칭**이라 균일 혼합이 안정한 고정점이다. 7개 구성이 전부 그 점에 앉았다(chat 라우팅 집중도 0.05~0.11, PolyServe는 1.00) | impl §7.3 |
| **C4 externality** `score = headroom − α·ext` | **α 없음.** 각 단계가 하나의 양만 쓴다 | ① externality를 측정하니 0.013 대 [−1,1] 범위라 순위에 영향이 없었다 ② 가중합은 과부하에서 붕괴한다 — 한 엔진에 5,569건이 쌓이고 나머지 셋이 27분 유휴 | impl §5, §7.2 |
| C4의 soft binning **창발** | **명시적 `classShare` 항** | 원안 §11.3이 예고한 그대로다: "externality가 soft binning을 못 만들면 명시적 tier-affinity 항 필요 `[Q4]`". **Q4의 답이 '필요하다'로 나왔다** | impl §5 |
| **C5** 별도 pend 큐 + deadline 스캔 + `urgent_ratio`/`starvation_timeout` | 게이트웨이 보유-재시도로 대체. `ttft_safety_margin`만 남음 | 기능은 같고 구조가 다르다. 다만 게이트웨이 워커 풀이 5개여서 **동시 보유가 5건으로 제한**되는 결함이 있었고, 이것이 v1~v10 전체 측정을 무효화했다 | impl §8 |
| **C6 OuterLoopTuner** | **삭제** | 원안은 "안전은 inner loop가 담당하니 이 루프가 느려도 위험하지 않다"고 했으나, 실제로는 **진동**했다(보정계수 1.24~2.35). 파라미터 7개를 쓰고 얻은 것이 없었다 | impl §7.4 |
| **C7 DispatchQueue** + `engine_queue_target` | 게이트웨이 보유로 대체 | 스케줄러에 큐를 두지 않고 게이트웨이가 들고 있다 | impl §1 D1 |
| **Part 6 파라미터 카탈로그** | **20개 → 7개** | 가중치 4개, room 하한, mismatch 항과 EWMA, 대기 판정 3개, 안전 루프 7개, 온라인 교정 3개를 삭제 | impl §7.4 |

## B.3 원안이 미리 맞춘 것 (Part 11)

| 원안 | 실제로 일어난 일 |
|---|---|
| **L1** 메모리는 유일한 capacity 축이 아니다. flux 예측은 compute를 전혀 모델링하지 않는다 → **prefill 폭주형 overload에 눈이 멀 수 있다** | impl §11~§13의 주제 전부가 이것이다. 도착 prefill을 투영에 넣는 작업을 세 번 고쳤고 아직 진행 중이다 |
| **L3** 탐색-교정 결합, 자기강화 하향 루프 | 같은 종류의 문제를 **세 번** 겪었다 — 보정계수 진동(§10), 인스턴스별 배치율 루프(§12), 거절률 루프(§13.4) |
| **L4** `cap_i`의 불연속성 → chattering | 잔여 예산을 용량 게이트로 쓴 v1~v8의 붕괴가 이것이다 |
| **L5** 근시안적 greedy, lookahead 없음 | 그대로. "최적성을 주장하지 않는다"는 입장도 그대로 유효하다 |
| §11.3 C4 행: `alpha` 과대 → 과집중 / 과소 → 확산 | v9에서 겪은 tension이 정확히 이것이고, 답은 α 조절이 아니라 **가중합을 버리는 것**이었다 |

## B.4 ⭐ §11.4가 지금 평가 조건을 규정한다

원안 §11.4는 **FluidServe가 baseline보다 나쁠 수 있는 구간**을 미리 나열했다.

| §11.4의 구간 | 우리가 측정한 것 |
|---|---|
| **정상적(stationary)이고 예측 가능한 워크로드** — "정적 provisioning(PolyServe)이 더 적은 복잡도로 동등 성능 달성 가능. **fluctuation이 있는 워크로드에서 평가해야 공정**" | **EXP-21~25가 전부 고정 rate mix A(1:1:1).** 완전히 stationary하다 |
| **저부하** — 오버헤드가 순손실 | 600 rpm |
| **지속적 극단 overload** — shedding 정책만 남고 라우팅의 한계 이득이 0에 수렴 | 3000 / 4200 rpm |

**즉 지금까지 측정한 세 rate가 전부 원안이 "불리하다"고 지목한 구간이다.**

### 그런데 지금 가진 동적 trace도 §11.4가 요구하는 조건은 아니다

원안의 논리는 "변동하는 워크로드에서는 정적 파티션이 못 따라간다"이다. 그러나 측정된
사실 둘이 이 trace에서는 그 상황이 생기지 않음을 보인다.

1. **PolyServe의 재분할은 7분 동적 trace에서 459 샘플 동안 변화 0회**였다.
2. **세 믹스(A/B/C) 전부 최적 배정이 (swe 2 / chat 1 / dr 1)로 같다.** 4대 + 3클래스
   + tier당 최소 1대 보장이면 자유로운 서버가 1대뿐이고, swe의 요청당 비용이 7~15배라
   항상 swe가 가져간다.

→ **rate와 믹스가 변해도 최적 파티션이 안 변하면, 정적 파티션이 못 따라갈 일이 없다.**

**원안이 요구하는 조건을 만들려면 최적 파티션이 실제로 움직여야 한다.** 두 방향:

- **클래스 수 > 서버 수** — 클래스 5개에 서버 4대면 tier당 최소 1대 보장 자체가
  불가능해지고, 정수 단위 배분이 반드시 손해를 낸다
- **믹스 변동을 최적 배정이 바뀔 만큼 크게** — 지금 A/B/C는 전부 (2,1,1)로 계산되므로,
  예컨대 swe 비중을 5%까지 떨어뜨리는 구간을 넣어야 (1,2,1)이나 (1,1,2)로 넘어간다

이것이 EXP-26의 설계 근거다.
