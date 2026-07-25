# Niyama (Sarathi-Serve, ASPLOS'26) 분석 노트

> 작성: 2026-07-24, 대상 머신: 8× NVIDIA B200 (Blackwell, sm_100), torch 2.10 / CUDA 13.1
> 브랜치: `microsoft/sarathi-serve @ niyama_asplos2026`
> 목적: Llumnix 대체 / deadline-aware 스케줄링 재현 후보 평가 (JITServe와 비교)

## 1. Niyama가 뭔가

- **Sarathi-Serve의 deadline-aware 스케줄링 브랜치.** Sarathi-Serve 자체가 **vLLM 포크**(research prototype, feature parity 없음).
- 핵심: **SLA/deadline-aware intra-engine 스케줄러.** chunked-prefill(Sarathi) 위에 deadline 기반 요청 정렬 + 동적 prefill chunk size 선택 + eager relegation(데드라인 놓칠 요청 후순위).
- **JITServe와 같은 카테고리:** 한 엔진 *내부* 스케줄러이지, Llumnix 같은 cross-instance 오케스트레이터가 **아님.** 마이그레이션/라우터 없음.

## 2. 핵심 구현 위치

- `sarathi/core/scheduler/deadline_scheduler.py`
  - L271–277: chunk size predictor 호출 (스케줄할 토큰 수 결정)
  - L54–167: chunk size predictor 구현
  - L300: **eager relegation** (deadline miss 예측 요청 후순위)
- `sarathi/core/datatypes/sequence.py`
  - L11: `TIER_DEADLINES` — SLA tier 정의
  - L70–75: 요청을 SLA tier에 랜덤 배정
  - L79: hybrid prioritization 상수
  - L88–109: sequence comparator (hybrid prioritization 정렬)
  - L106–107: arrival time vs remaining tokens 가중치
  - **TTLT(Time-To-Last-Token) SLA**: 출력길이 `num_dec_tokens`로 예측, TTFT ≈ SLA − num_dec_tokens×TBT_SLA
- `chunk_size_predictor/predictor.py`: 이전 run 데이터로 선형회귀 fit → 처리시간 예측
- 스케줄러 레지스트리(`scheduler_registry.py`): VLLM / ORCA / FASTER_TRANSFORMER / SARATHI / SIMPLE_CHUNKING / **DEADLINE**
  - 벤치 정책 문자열: `fcfs`, `edf`, `deadline`

## 3. 실행 모델 & "멀티인스턴스"

- **오프라인 벤치 하네스 중심.** `fig7.sh` 등은 GPU 0~3에 각각 (scheduler, qps)를 배정해 **독립 replica를 병렬 실행**:
  ```
  "fcfs 1.45" "edf 2.9" "deadline 3.55"   # GPU 0
  "fcfs 1.6"  "edf 2.75" "deadline 3.7"   # GPU 1  ...
  ```
- `sarathi/benchmark/capacity_search/` — **Ray 기반 멀티-GPU capacity search** 하네스 내장 (여러 엔진 config를 GPU에 뿌려 병렬 탐색).
- 즉 여기서 "멀티인스턴스" = **독립 replica들의 병렬 실행**이지, Llumnix식 라우팅/이주가 있는 협조형 멀티인스턴스가 **아님.** 앞단 라우터는 우리가 붙여야 함(현재 레포의 gateway 재사용 가능).
- online 서버도 있음: `sarathi/entrypoints/openai/` (OpenAI 호환).

## 4. 🚨 B200 호환성 (JITServe보다 더 나쁨)

- 요구 스택: **Python 3.10, torch `2.3`, flashinfer `0.1.1+cu121torch2.3`, CUDA 12.1** (README, requirements.txt)
- setup.py는 CUDA compute capability로 커널 생성 — sm_100(Blackwell) 타겟 없음(8.6/8.9까지만 언급, A100/H100 세대).
- → **B200(sm_100)에서 torch 2.3 / cu121 flashinfer는 기동 불가.** JITServe(torch 2.5)보다도 오래됨.

## 5. 데이터셋 / 재현 대상

- Azure LLM inference traces: `AzureLLMInferenceTrace_code_1week.csv`, `..._conv_1week.csv` (blob에서 wget), + ShareGPT
- 모델: Llama-3-8B (TP1 중심), Qwen-2.5
- 재현 figure: fig7(scheduler별 capacity), fig8, fig10/11. `*_tiny.sh`로 축소 sweep 가능.
- 재현성 위해 GPU clock lock / persistence mode / seed 고정 요구.

## 6. JITServe vs Niyama 비교 (우리 목적 기준)

| | JITServe (NSDI'26) | Niyama / Sarathi-Serve (ASPLOS'26) |
|---|---|---|
| 유형 | intra-engine SLO 스케줄러 | intra-engine deadline 스케줄러 (chunked-prefill 기반) |
| 예측 | QRF 출력길이 예측 + 그래프매칭(ToT/DeepResearch) | 선형회귀 chunk-size/처리시간 예측 |
| 정책 비교 | jitserve/fcfs/srtf/ltr/autellix | **fcfs/edf/deadline** ← 우리 EDF 작업과 직결 |
| 멀티인스턴스 | 없음 (라우터 직접 구현 필요) | 독립 replica 병렬 + Ray capacity_search (라우터는 없음) |
| 베이스 | vendored vLLM (2024-10, torch 2.5) | vLLM 포크 (torch 2.3, flashinfer cu121) |
| B200 기동 | 어려움 (sm_100 커널 없음) | **더 어려움** (더 옛 스택) |
| 워크로드 | ToT/DeepResearch 그래프 | Azure code/conv + ShareGPT |

## 7. 시사점 (우리 연구와의 관련성)

- 우리 브랜치 `feat/kv-admission-threshold`는 EDF/SJF/SRPF gateway 스케줄러 + KV admission threshold 실험 중.
  Niyama의 **fcfs/edf/deadline** 비교 + eager relegation + deadline-aware chunk sizing은 **직접적으로 관련**된 선행연구.
- 단, 두 시스템 모두 **Llumnix식 cross-instance 대체가 아니라 single-engine 스케줄러**. B200 기동이 공통 blocker.
- 실현 경로(둘 공통): (A) 스케줄러 로직만 최신 Blackwell 지원 vLLM에 포팅, 또는 (B) 구세대 GPU/CUDA12 컨테이너 확보 후 원본 재현.

## 8. 상태

- 클론 완료: `sarathi-serve-niyama/` (`niyama_asplos2026` 브랜치)
- 아직 빌드/실행 시도 안 함. 다음 결정: 포팅 vs 구세대 GPU vs 아이디어만 차용.

---

# 9. 옵션1 vs 옵션2 정밀 작업 분석 (2026-07-24)

## 사전 사실: 우리 베이스는 이미 B200 modern vLLM
- 현재 실험 스택 = **vLLM V1 `0.12.1.dev0` (2026-03) + `--scheduler-cls` 훅**, `AsyncScheduler` 서브클래싱.
- 이미 보유: **FIFO / EDF / SJF / SRPF** (`patches/vllm-sched/llumnix_sched.py`).
  - EDF = 게이트웨이가 `priority = arrival_ms + SLO_ms`(절대 데드라인) 주입 → 커스텀 클래스 불필요.
  - SJF = `priority = num_prompt_tokens`, SRPF = SJF + 매 스텝 `running`을 remaining prefill로 재정렬.
- **`InstrumentedScheduler`가 이미 per-step으로 `kv_tokens / prefill_tokens_step / n_decode / interval_ms`를 로깅** (EXP-16). → Niyama 배치시간 예측기의 피처와 사실상 동일.

## Niyama DeadlineScheduler 기여 분해 (이식 단위)
1. **SLA tier + 데드라인 모델** (`sequence.py`): `TIER_DEADLINES=[6,600,1800]`, tier 랜덤배정, TTFT vs TBT 분해, TTLT: `ttft_deadline = tier_deadline − num_decode_tokens×TBT_SLA`. → 순수 파이썬, 이식 쉬움.
2. **비교자 `__lt__`**: fcfs/edf/srpf/**hybrid**(`arrival + ttft_deadline + k×remaining_tokens`). → EDF/SRPF는 이미 있음, hybrid만 신규.
3. **슬랙 기반 running 정렬** `_sorting_key`: decode는 `-slack` 우선, prefill은 `ttft_deadline`. slack = `TBT_deadline − now`.
4. **동적 청크 사이징** `_get_prefill_size_by_slack` + `_predict_batch_time`: 토큰 탐색공간에서 min-slack 안에 들어가는 최대 prefill 토큰을 이진탐색, 선형 배치시간모델 사용. ← **제일 어려운 부분**, Sarathi가 스케줄러에서 직접 `prefill_token_limit`을 정하는 청크드-프리필 구조에 강결합.
5. **Eager relegation** (`drop` 플래그): `now + remaining/throughput > deadline`이면 drop++ 후 큐 후순위.
6. **배치시간 예측기** (`chunk_size_predictor/predictor.py`): 오프라인 선형회귀. 계수가 **A100 Llama3-8B 하드코딩** → B200 재프로파일 필요.

## 옵션 1 — 아이디어 차용 (현재 llumnix_sched.py 확장, B200 그대로)
예상 작업:
- [쉬움] `DeadlineScheduler(AsyncScheduler)` 신규 클래스. 게이트웨이가 tier/deadline 메타를 주입(이미 priority 포워딩 경로 있음).
- [쉬움] SLA tier + TTFT/TBT 데드라인 계산, hybrid 비교자 → 파이썬 이식.
- [중간] 매 스텝 slack 기반 `running` 재정렬(SRPF에서 이미 `self.running.sort` 패턴 보유).
- [중간] eager relegation을 vLLM V1의 waiting/preempt 의미로 매핑.
- [어려움/불확실] 동적 청크 사이징: vLLM V1은 청킹을 엔진 내부에서 함. 서브클래스에서 `max_num_batched_tokens`/`long_prefill_token_threshold`/스텝 토큰버짓을 slack 기반으로 동적 조정하려면 `schedule()` 오버라이드 + V1 sched 내부(이미 `vendor-reference/sched/` 사본 보유) 학습 필요.
- [중간] 배치시간 예측기: **EXP-16 InstrumentedScheduler 로그를 그대로 재사용해 B200에서 선형모델 fit** (큰 시너지).
- **B200 blocker 없음, 새 vLLM 빌드 없음, 배포=플래그 변경.**
- 한계: 논문 아티팩트 "재현"이 아니라 "우리 스택에서 Niyama-style deadline 스케줄링". fidelity는 근사.
- 규모 감: 스케줄러 코어 ~1–2일, 동적 청킹+검증 ~수일. 리스크 낮음.

## 옵션 2 — 충실 포팅
### 2a. Sarathi/Niyama 엔진을 B200에 빌드
- 의존성 벽: **torch 2.3 / flashinfer 0.1.1+cu121 / Python 3.10** → sm_100 미지원.
- 필요 작업: torch→2.5+/flashinfer→cu12x-blackwell 승급, `csrc/`(pos_encoding·layernorm·activation·moe 커스텀 커널) sm_100 재빌드, attention은 flashinfer 의존 → flashinfer 블랙웰 빌드 확보, vLLM-fork API drift 수정.
- 리스크 **높음**(런타임 커널/누머릭 안정성). Sarathi는 feature parity 없는 연구 프로토타입이라 최신 스택 승급 자체가 미지수.
### 2b. DeadlineScheduler `_schedule()`를 vLLM V1로 충실 재구현
- Sarathi `BaseScheduler`(plain list waiting/running, 명시적 `block_manager.allocate/append_slot`, per-step `prefill_token_limit`, preemption 없음) ↔ vLLM V1 `AsyncScheduler`(KVCacheManager, prefix cache, 연속배칭, priority queue, preemption 내장) — **실행모델이 근본적으로 다름**.
- 비교자/슬랙/relegation은 개념적으로 이식되나, 동적 청킹은 V1 토큰버짓 모델로 재표현해야 함.
- 논문 figure(fig7/8/10/11)는 Sarathi 벤치 하네스 + Azure trace 기반 → 엔진 없이는 그대로 안 돎. "figure 재현"하려면 하네스도 재배관하거나 우리 하네스에서 비교.
- 결국 옵션1로 수렴하되 더 엄밀(실측 프로파일 + 검증). 규모 감: ~1–2주+.

## 결론 (권고)
- **옵션1이 현실적.** 우리 베이스가 이미 B200 modern vLLM이고 EDF/SJF/SRPF + per-step 계측(EXP-16)까지 있어 Niyama의 tier-deadline·hybrid·slack·relegation·동적청킹을 순차 이식 가능. 배치시간 예측기는 기존 로그 재사용.
- **옵션2a(엔진 빌드)는 비권장** — CUDA/의존성 벽이 연구 가치 대비 과도.
- 옵션2b는 사실상 옵션1의 엄밀판. "논문 수치 재현"이 반드시 필요할 때만.
