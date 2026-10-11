> Analysis date: 2026-05-28 18:10 (KST)
> Updated: 2026-06-01 14:30 (KST) — FlexPipe (EuroSys '26) 등장으로 Alibaba `cluster-trace-v2026-GenAI` 추가

# LLM Serving Trace Datasets — 상세 분석 보고서

공개된 LLM/LMM serving trace 데이터셋을 다운로드하여 실제 데이터를 분석한 결과.

---

## 1. 데이터셋 개요

| Dataset | 출처 | 기간 | Requests | 주요 특징 |
|---------|------|------|----------|----------|
| **Azure LLM 2023** | Azure (Splitwise, ISCA '24) | ~1시간 (2023-11-16) | 28K | Code + Conv 두 workload |
| **Azure LLM 2024** | Azure (DynamoLLM, HPCA '25) | 10일 (2024-05-10~19) | 수백만 | 2023의 확장판, 동일 스키마 |
| **Azure LMM 2025** | Azure (ModServe, SoCC '25) | 7일 (2024-10-15~22) | 1M | 멀티모달 (NumImages 포함) |
| **BurstGPT** | Azure OpenAI (KDD '25) | 213일 | 10.1M | Session ID, Model별 분리, 최대 규모 |
| **Mooncake/Kimi** | Kimi chatbot (FAST '25 Best Paper) | 1시간 | 35.6K | KV cache hash_ids (prefix sharing) |
| **Alibaba GenTD26** | Alibaba (SoCC '25 "Understanding Diffusion" 원본; FlexPipe EuroSys '26·Rock CLUSTER '25 재사용) | 시스템 22.8h + 요청 23일 | 26.8K reqs + 157K GPU samples | **Stable Diffusion + LoRA 서빙** (LLM 아님). 요청 + GPU/메모리/큐 매칭 |

---

## 2. Azure LLM 2023 (Splitwise)

**스키마:** `TIMESTAMP, ContextTokens, GeneratedTokens`

### Code Workload
- **8,819 requests** / 57분
- ContextTokens: mean=2048, median=1469, P95=7303, max=7437
- GeneratedTokens: mean=28, median=13, P95=90 — **코드 생성은 출력이 매우 짧음**
- Peak: 585 req/min — **bursty** (0까지 떨어졌다가 급등하는 패턴)

### Conversation Workload
- **19,366 requests** / 58분
- ContextTokens: mean=1155, median=1020, P95=4083
- GeneratedTokens: mean=211, median=129, P95=451
- Peak: 502 req/min — Code보다 안정적, bell-curve 형태

### 특징
- **매우 짧은 trace** (~1시간). 일별/주별 패턴 분석 불가
- Code vs Conv의 토큰 분포 차이가 뚜렷: Code는 input이 크고 output이 작음
- Splitwise 논문의 prefill/decode phase splitting 연구를 위한 데이터

---

## 3. Azure LLM 2024 (DynamoLLM)

> **다운로드 진행 중** — 완료 후 분석 추가 예정 (code ~660MB, conv ~1.06GB)

**스키마:** 2023과 동일 (`TIMESTAMP, ContextTokens, GeneratedTokens`)
- 10일치 데이터로 2023 대비 일별 패턴, 주중/주말 차이 분석 가능
- DynamoLLM (HPCA '25) — LLM 클러스터의 에너지 효율 최적화 논문

---

## 4. Azure LMM 2025 (ModServe)

**스키마:** `TIMESTAMP, NumImages, ContextTokens, GeneratedTokens`

- **1,000,000 requests** / 7일
- ContextTokens: mean=2911, median=1124, P95=9755, **max=148,569** (매우 긴 컨텍스트)
- GeneratedTokens: mean=187, median=98, P95=603
- **NumImages: mean=14.33, max=9,409** — 이미지 수천 장을 포함하는 요청도 존재

### Image vs Text-Only 비율
- With images: 500,000 (정확히 50.0%) — **의도적 샘플링으로 보임**
- Text only: 500,000 (50.0%)

### 주간 패턴
- 뚜렷한 **일주기(diurnal) 패턴** 존재 (UTC 기준)
- 10-19 ~ 10-20 (주말)에 traffic 감소 → **비즈니스 워크로드** 시사
- Peak: 11,971 req/hr

### 특징
- **유일한 공개 멀티모달 production trace**
- Image request는 더 bursty한 패턴 (spike가 큼)
- 50/50 image/text 비율이 인위적 → 실제 production 비율이 아닐 수 있음 (**불확실**)

---

## 5. BurstGPT (Azure OpenAI GPT)

**스키마:** `Timestamp, [Session ID], [Elapsed time], Model, Request tokens, Response tokens, Total tokens, Log Type`

> File 1-2는 6열, File 3는 8열 (Session ID, Elapsed time 추가)

### 전체 통계
- **10,144,565 requests** / 335일 (data gap: day 121~225)
- Models: ChatGPT (9.1M, 89.6%), GPT-4 (1.1M, 10.4%)
- Log Types: API log (9.7M, 95.5%), Conversation log (457K, 4.5%)
- Request tokens: mean=436, median=257, P95=1321 — **API 호출이 대부분이라 짧음**
- Response tokens: mean=72, **median=14** — 절반 이상이 14토큰 이하 (streaming/short response)

### 모델별 차이
- **ChatGPT**: request median=238, response median=14 — 짧은 API 호출 위주
- **GPT-4**: request median=616, response median=66 — 더 복잡한 작업

### 시간 패턴
- **매우 bursty**: peak 37,278 req/hr, 대부분 시간은 수천 req/hr 이하
- Day 121~225 구간에 **데이터 공백** (File 2와 File 3 사이)
- Diurnal 패턴: 낮(12~20시)에 높고 새벽(6~10시)에 낮음

### Session 분석 (File 3만 가능)
- 55,295 unique sessions
- 세션당 request: mean=4.2, median=2 — **대부분 짧은 멀티턴**
- 세션 duration: mean=10.3s, median=5s — 매우 빠른 인터랙션

### 특징
- **가장 대규모** 공개 LLM trace (10M+ requests, 7개월)
- API log이 95% — **chatbot보다 API 사용이 압도적**
- File 3의 Session ID로 멀티턴 분석 가능
- 데이터 공백(day 121~225)이 있어 연속 시계열 분석 시 주의

---

## 6. Mooncake/Kimi (Production Chatbot)

**스키마:** `timestamp (ms), input_length, output_length, hash_ids[]`

### Conversation Trace
- **12,031 requests** / 59분
- Input: mean=12,035, median=6,909, **max=126,195** — 매우 긴 컨텍스트
- Output: mean=343, median=350
- KV blocks: mean=24.0, median=14
- **KV cache reuse ratio: 36.6%**
- All requests share the same first hash_id (hash_id=0) → **단일 시스템 프롬프트**

### Tool & Agent Trace
- **23,608 requests** / 59분
- Input: mean=8,596, median=6,346
- Output: mean=182, **median=30** — 많은 tool call이 짧은 출력
- KV blocks: mean=17.4, median=13
- **KV cache reuse ratio: 55.3%** (conversation보다 높음)
- 3개의 주요 prefix: hash_id 0 (46.3%), hash_id 46 (39.0%), hash_id 74 (14.6%)

### Inter-Arrival Time
- **Median: 0ms** (!) — 대부분의 request가 동시에 도착 (batch)
- Mean: conversation 294ms, agent 150ms
- ~3000ms 주기의 burst 패턴 존재

### 특징
- **유일하게 KV cache block hash를 포함** — prefix sharing 연구에 필수
- Conversation vs Agent의 뚜렷한 특성 차이 (input 길이, output 길이, reuse ratio)
- Agent trace의 높은 reuse ratio(55%)는 tool call들이 공통 컨텍스트를 공유하기 때문
- 1시간 분량이라 시간 패턴 분석에는 한계

---

## 7. Alibaba GenTD26 (실제 다운로드·검증 완료)

> ⚠️ **이전 버전 정정**: FlexPipe 인용만 보고 LLM 서빙 트레이스로 추정했으나, 실제 다운로드 결과 **Stable Diffusion + LoRA + ControlNet 서빙 데이터셋**이었습니다. 원 공개자는 SoCC '25 "Understanding Diffusion Model Serving in Production" (Yan et al.). FlexPipe와 Rock(CLUSTER '25)이 재사용한 것입니다.

### 7.1 정체와 출처

- **공식 이름**: `GenTD26 — GenAI Serving Top-Down Dataset 2026`
- **URL**: https://github.com/alibaba/clusterdata/tree/master/cluster-trace-v2026-GenAI
- **원 논문**: Yan et al., "Understanding Diffusion Model Serving in Production: A Top-Down Analysis of Workload, Scheduling, and Resource Efficiency", **SoCC '25** (재사용: FlexPipe EuroSys '26, Rock CLUSTER '25)
- **워크로드 정체**: Stable Diffusion 이미지 생성 (TXT_2_IMG / IMG_2_IMG / INPAINTING) + multi-LoRA + ControlNet — **LLM 텍스트 서빙이 아님**
- **다운로드 위치 (로컬)**: `traces/alibaba_gentd26/`

### 7.2 데이터 구조 (3-layer top-down)

| 계층 | 파일 | rows | 시간 |
|---|---|---|---|
| **Application** | `lora_request_trace.csv` | 26,823 | **2024-11-15 → 2024-12-08 (23일)** |
| **Application (정제)** | `data_trace_processed.csv` | 68,195 | (no timestamp; 분석용 snapshot) |
| **Middleware (큐)** | `queue_size_raw_anon.csv` | 1,434 | 시스템 22.8h |
| **Middleware (큐)** | `queue_rt_raw_anon.csv` | 23,478 | 시스템 22.8h |
| **Middleware (QPS)** | `qps.csv` | 24,627 (Gen 23,513 + API 1,114) | 시스템 22.8h |
| **Middleware (latency)** | `pipeline_inference_data_anon.csv` | 34,421 | 시스템 22.8h |
| **Middleware (latency)** | `model_predict_data_anon.csv` | 6,254 | 시스템 22.8h |
| **Middleware (loading)** | `pipeline_update_latency_anon.csv` | 36,046 | 시스템 22.8h |
| **Middleware (loading)** | `basemodel_update_latency_anon.csv` | 7,531 | 시스템 22.8h |
| **Middleware (loading)** | `lora_update_latency_anon.csv` | 27,415 | 시스템 22.8h |
| **Middleware (loading)** | `controlnet_latency_data_anon.csv` | 29,221 | 시스템 22.8h |
| **Infrastructure** | `pod_gpu_duty_cycle_anon.csv` | 157,417 | 시스템 22.8h (143 unique pods) |
| **Infrastructure** | `pod_gpu_memory_used_bytes_anon.csv` | 161,413 | 시스템 22.8h |
| **Infrastructure** | `pod_memory_util_anon.csv` | 214,890 | 시스템 22.8h |

> ⚠️ **두 가지 시간축이 섞여 있음**: 시스템/인프라 metric은 2022-09-11 ~ 09-12 의 ~22.8시간 snapshot, application-level `lora_request_trace.csv`는 2024-11-15 ~ 12-08 의 23일. **두 데이터는 시간상 정렬 불가**. README의 `container_ip` MD5 매칭도 시간축이 다르면 의미 제한.

### 7.3 핵심 스키마

**`lora_request_trace.csv`** (request-level, 23일):
```
gmt_create (datetime), predict_type (TXT_2_IMG/IMG_2_IMG/INPAINTING),
predict_status, exec_time_seconds, groupId, prompt_length (chars),
negative_prompt_length, num_images_per_prompt, num_inference_steps,
checkpoint_model_version_id, num_lora (0/1/2/3/4/6)
```

**시스템 metric 공통 스키마**:
```
timestamp_anon (float, Unix-like seconds), value (float), container_ip (MD5 hash)
```

### 7.4 실측 통계 (다운로드 후 직접 계산)

**Application-level (`lora_request_trace.csv`, 26,823 req / 23일)**:
- predict_type: TXT_2_IMG 91.1% / IMG_2_IMG 8.2% / INPAINTING 0.7%
- predict_status: SUCCEED 98.4% / FAILED 1.5% / 기타 0.1%
- unique `groupId` (사용자): **4,247**
- unique base models: **86**
- num_lora 분포: 0=22,329 (83%) / 1=4,195 (16%) / 2+=299 (1%)
- exec_time_seconds — Median 23s, Mean 28.7s, P95 69s, Max 567s
- prompt_length — Median **52 chars** (이미지 생성용 짧은 프롬프트), P95 126, Max 1,288
- num_inference_steps — 대부분 30 step 단일 모드 (24,438 / 26,823 ≈ 91%), 일부 40·50 step

**Infrastructure-level (`pod_gpu_duty_cycle`, 22.8h)**:
- unique pods: **143** (FlexPipe Table 1의 C1=468 GPUs / C2=1,175 GPUs와 다름 — release는 subset)
- GPU duty cycle: **mean 7.0%, median 0%** (분포가 0에 강하게 편향, 가끔 60-100% spike)
- GPU memory: 0-50.8 GB 범위, mean 24.76 GB → A100-40GB 또는 80GB 추정

**System QPS (22.8h 누적)**:
- 0-10시간 구간이 active period, 이후 트래픽 거의 사라짐
- Generative Requests이 API Requests보다 ~20× 많음

### 7.5 LLM 서빙 연구에서의 활용 가능성

**✅ 직접 활용 가능 (LLM 서빙에도 transferable)**:
- **GPU duty cycle / GPU memory time series**: 143 pods × 22.8h 분량의 클러스터 utilization 패턴 — production GPU 사용 패턴 분석에 그대로 사용 가능
- **Pod memory util**: 컨테이너 메모리 압박 패턴
- **System QPS time series**: 서빙 시스템의 도착 패턴 변동성 (CV) 분석
- **Queue size / queue RT**: serverless inference cluster scheduling 연구에 사용

**❌ 직접 활용 불가 (Diffusion-specific)**:
- `predict_type` (TXT_2_IMG 등): LLM 개념 아님
- `num_inference_steps` (sampling step 수): Diffusion 전용
- `num_lora` (multi-LoRA): 일반 LLM과 다른 구조
- `prompt_length`: **character 단위** (LLM의 token이 아님)
- `exec_time_seconds`: 평균 23초 — 이미지 생성 파이프라인 전체. LLM처럼 토큰별 latency가 아님
- `pipeline_inference_data` latency P95 35초 / max 91초: 이미지 생성 특성

**🟡 추론을 거쳐 활용 가능**:
- LoRA loading latency: LLM의 LoRA hot-swap (S-LoRA, MuxServe) 연구에 유추 적용 가능
- Pipeline update latency: model warm-up cold-start 모델로 활용 가능 (FlexPipe도 이렇게 씀)
- Application-level arrival pattern (23일): 도착률 자체는 LLM 서빙 trace로 시뮬레이션 가능 (단, 요청 1건 = 이미지 생성 1회로 ~23초 소요, LLM 호출의 ~수백ms~수초와 시간 단위 다름)

### 7.6 FlexPipe 논문 주장과의 대조

| FlexPipe 본문 주장 | 실측 검증 결과 |
|---|---|
| "two-week analysis of GPU resources" (§3.1) | 시스템 metric은 **22.8h 만 공개**. lora_request_trace는 23일. **2주 데이터는 비공개** |
| "C1: 430 nodes / 468 GPUs; C2: 927 nodes / 1,175 GPUs" (Table 1) | 공개 데이터에는 **143 unique pods**만 있음 — release는 subset |
| "GPU subscription rate averaging 216%" (§3.1) | 공개 데이터에서 subscription rate 직접 계산은 불가 (pod-to-node mapping이 없음). 통계만 인용된 형태 |
| "0.02% probability of co-locating 4 GPUs" (§3.1) | 동일하게 직접 검증 불가 (node-level mapping 미공개) |
| "78% of tensor parallelism requests forced to degrade to pipeline parallelism" (§3.1) | parallelism strategy 컬럼이 release 데이터에 없음 — 본문에만 인용 |
| "request CV up to 7× across timeframes" (Fig.1) | lora_request_trace.csv (23일)에서 직접 계산 가능 |

→ **FlexPipe가 사용한 production-scale 통계의 상당 부분은 internal Alibaba access에서 나온 것이고, 공개 데이터셋은 그 motivation을 *재현* 하기에는 일부 제한이 있습니다.** 다만 CV 분석, 도착률 패턴, GPU 활용도 분포 같은 가시화는 공개 데이터로 직접 가능합니다.

### 7.7 시각화 (생성됨)

`traces/plots/alibaba/` 에 저장:
- `gentd26_system_22h.png` — 22.8시간 시스템 snapshot (QPS / GPU duty / GPU mem / 큐 size)
- `gentd26_lora_23day.png` — 23일 application-level 도착 패턴 (전체 + predict_type별 + num_lora별)
- `gentd26_distributions.png` — exec_time, prompt_length, inference_steps, GPU duty cycle 분포

### 7.8 라이선스

- README 상에 라이선스 명시 없음. 일반적인 Alibaba clusterdata 정책 (CC-BY [uncertain]) 추정
- 인용 문구는 SoCC '25 또는 FlexPipe BibTeX 제공 (논문에 따라 선택)

---

## 8. 데이터셋 비교 매트릭스

| Feature | Azure 2023 | Azure 2024 | Azure LMM 2025 | BurstGPT | Mooncake | **Alibaba GenTD26** |
|---------|-----------|-----------|----------------|----------|----------|------|
| **Workload type** | LLM text | LLM text | LMM (text+image) | LLM text (Azure OpenAI) | LLM chatbot/agent | **Diffusion (image gen)** |
| Duration | 1시간 | 10일 | 7일 | 335일 | 1시간 | 시스템 22.8h + 요청 23일 |
| Requests | 28K | 수백만 | 1M | 10.1M | 35.6K | 26.8K (req) + 157K (GPU samples) |
| Timestamp | O | O | O | O (상대) | O (상대, ms) | O (절대 Unix-like) |
| Prompt text | X (GDPR) | X | X | X | X | X (length만 char 단위) |
| Session/User ID | X | X | X | **O** (File3) | X | **O** (groupId, 4,247 unique) |
| Model type | X | X | X | **O** (ChatGPT/GPT-4) | X | **O** (86 base models, anonymized) |
| Log type | X | X | X | **O** (API/Conv) | X | **O** (predict_type) |
| Image count | X | X | **O** | X | X | **O** (num_images_per_prompt) |
| KV cache hash | X | X | X | X | **O** | X |
| Multi-turn | X | X | X | **O** | X | X |
| **GPU duty cycle** | X | X | X | X | X | **O** (143 pods, 22.8h) |
| **GPU mem usage** | X | X | X | X | X | **O** (per-pod time series) |
| **Queue size/RT** | X | X | X | X | X | **O** |
| **Pipeline latency** | X | X | X | X | X | **O** |
| **LoRA loading latency** | X | X | X | X | X | **O** (unique) |
| License | CC-BY | CC-BY | CC-BY | CC-BY-4.0 | Apache 2.0 | 미확인 [uncertain] |

---

## 9. 추가로 할 수 있는 분석

### 당장 가능한 것
1. **Azure 2024 도착 후**: 10일간 일별 패턴, 주중/주말 차이, peak time 분석
2. **BurstGPT session 분석**: File 3의 session 데이터로 멀티턴 대화 패턴, session 내 inter-arrival time
3. **Cross-dataset 비교**: 같은 metric(예: ContextTokens 분포)으로 데이터셋 간 직접 비교
4. **Burstiness 정량화**: CV (coefficient of variation), autocorrelation, Hurst exponent 등으로 burstiness 측정
5. **Mooncake prefix sharing 시뮬레이션**: hash_ids를 이용한 prefix cache hit rate 시뮬레이션

### 추가 데이터 확보 가능한 것
6. **Azure Function Invocation 2021**: PARD처럼 arrival pattern으로 사용 가능 (개별 timestamp 포함)
7. **Azure LLM 2023 (Splitwise)**: 이미 받음 — 2024와 비교하면 6개월간 workload 변화 추적 가능
8. **ShareGPT + BurstGPT 결합**: ShareGPT의 텍스트와 BurstGPT의 arrival을 결합하면 "realistic" workload 생성 가능

---

## 10. 주의 사항 및 불확실한 점

| 항목 | 설명 |
|------|------|
| **LMM 2025 50/50 비율** | Image와 Text-only가 정확히 50:50 — 인위적 샘플링 가능성. 실제 production 비율이 아닐 수 있음 |
| **BurstGPT 데이터 공백** | Day 121~225 (약 100일) 데이터 없음. 수집 중단 또는 별도 사유 불명 |
| **BurstGPT timestamp 기준** | 상대 시간 (초 단위, day 1부터). UTC offset 불명 — diurnal 분석 시 실제 시간대 불확실 |
| **Mooncake timestamp 단위** | ms 단위, 상대 시간. 논문 Table 2와 statistics 일치 확인 완료 |
| **Azure 2024 파일 크기** | Code ~660MB, Conv ~1.06GB — 다운로드 진행 중, 완료 후 분석 추가 예정 |
| **GenTD26 ≠ LLM 트레이스** | Stable Diffusion 서빙 데이터. LLM 연구에는 GPU/QPS/queue 시그널만 transferable; request schema는 diffusion-specific |
| **GenTD26 두 가지 시간축** | 시스템 metric은 22.8h (2022-09), application-level은 23일 (2024-11~12) — 시간 정렬 불가 |
| **GenTD26 release vs paper 차이** | FlexPipe Table 1의 C1=468 GPUs / C2=1,175 GPUs가 release에는 143 pods만 노출. fragmentation 통계는 internal access 기반, public data로 완전 재현 불가 |

---

## 11. 파일 위치

```
traces/
├── plots/                                    # 모든 시각화 PNG 파일
│   ├── azure_2023_code_rate.png
│   ├── azure_2023_conv_rate.png
│   ├── azure_2023_code_tokens.png
│   ├── azure_2023_conv_tokens.png
│   ├── azure_lmm_2025_rate.png
│   ├── azure_lmm_2025_tokens.png
│   ├── azure_lmm_2025_img_breakdown.png
│   ├── burstgpt_hourly_rate_all.png
│   ├── burstgpt_by_model.png
│   ├── burstgpt_by_logtype.png
│   ├── burstgpt_token_dist.png
│   ├── burstgpt_diurnal.png
│   ├── burstgpt_first_week.png
│   ├── mooncake_request_rate.png
│   ├── mooncake_token_dist.png
│   ├── mooncake_kvcache_blocks.png
│   ├── mooncake_interarrival.png
│   └── all_traces_comparison.png
├── AzureLLMInferenceTrace_code_2023.csv      # 313K
├── AzureLLMInferenceTrace_conv_2023.csv      # 702K
├── AzureLLMInferenceTrace_code_2024.csv      # ~660MB (다운로드 중)
├── AzureLLMInferenceTrace_conv_2024.csv      # ~1.06GB (다운로드 중)
├── AzureLMMInferenceTrace_multimodal_2025.csv # 33MB
├── burstgpt/
│   ├── BurstGPT_1.csv                        # 50MB (1.4M rows)
│   ├── BurstGPT_2.csv                        # 138MB (3.9M rows)
│   ├── BurstGPT_3.csv                        # 221MB (5.3M rows, +Session ID)
│   ├── BurstGPT_without_fails_1.csv          # 49MB
│   ├── BurstGPT_without_fails_2.csv          # 136MB
│   └── BurstGPT_without_fails_3.csv          # 140MB (5.0M rows, +Session ID)
├── mooncake/
│   ├── conversation_trace.jsonl              # 2.9MB (12K rows)
│   └── toolagent_trace.jsonl                 # 4.2MB (24K rows)
├── analyze_traces.py                         # Azure 시각화 스크립트
├── analyze_burstgpt.py                       # BurstGPT 시각화 스크립트
├── analyze_mooncake.py                       # Mooncake 시각화 스크립트
├── analyze_alibaba_gentd26.py                # GenTD26 시각화 스크립트
├── plot_burstgpt_week.py                     # BurstGPT 임의 7일/단일일 윈도우 플롯
├── plot_burstgpt_full.py                     # BurstGPT 파일별 전체 타임라인
└── alibaba_gentd26/                          # GenTD26 (Stable Diffusion serving)
    ├── lora_request_trace.csv                # 2.0MB (23일, 26.8K req)
    ├── data_trace_processed.csv              # 8.5MB (68K rows, snapshot)
    ├── qps.csv                               # 2.1MB
    ├── pod_gpu_duty_cycle_anon.csv           # 8.4MB (143 pods × 22.8h)
    ├── pod_gpu_memory_used_bytes_anon.csv    # 9.8MB
    ├── pod_memory_util_anon.csv              # 13.9MB
    ├── pipeline_inference_data_anon.csv      # 1.9MB (34K reqs)
    ├── pipeline_update_latency_anon.csv      # 1.9MB
    ├── lora_update_latency_anon.csv          # 1.5MB
    ├── controlnet_latency_data_anon.csv      # 1.6MB
    ├── basemodel_update_latency_anon.csv     # 0.4MB
    ├── model_predict_data_anon.csv           # 0.3MB
    ├── queue_rt_raw_anon.csv                 # 1.2MB
    └── queue_size_raw_anon.csv               # 28KB
```
