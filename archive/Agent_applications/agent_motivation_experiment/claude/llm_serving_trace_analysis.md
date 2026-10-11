> Analysis date: 2026-05-28 22:30 (KST)

# LLM Serving 논문 Trace 사용 현황 분석

30편의 related work 논문에서 **실제 LLM request/agent trace**를 사용했는지, 어떤 trace를 썼는지, 공개 여부 및 링크를 정리한 분석 문서.

## 용어 정의

| 구분 | 설명 |
|------|------|
| **Real production trace** | 실제 운영 서비스에서 수집된 trace (timestamp + token 수 등 포함) |
| **Real dataset (length only)** | 실제 대화 데이터에서 input/output 길이 분포만 추출, arrival은 Poisson 등 합성 |
| **Synthetic only** | 길이 분포와 arrival 모두 합성 |

---

## 1. 논문별 상세 분석

### Real Production Trace 사용 논문

| # | 논문 | Venue | Trace 이름 | Trace 내용 | Arrival | Section |
|---|------|-------|-----------|-----------|---------|---------|
| 16 | **Mooncake** | FAST '25 | Kimi Conversation trace, Kimi Tool&Agent trace | 1시간 분량 실제 Kimi 챗봇 production trace. timestamp(ms), input/output length, KV cache block hash IDs 포함 | **Real (production)** | §5.2.1, Table 2 |
| 25 | **KunServe** | EuroSys '26 | BurstGPT | 실제 Azure OpenAI GPU 클러스터 trace. timestamp, session ID, model, token counts | **Real** (TraceUpscaler로 스케일링) | §5.1 |
| 28 | **eLLM** | EuroSys '26 | Azure LLM Inference 2024 | Azure production LLM invocation trace. timestamp, context/generated tokens | **Real** | §6.1 |
| 22 | **Kairos** | Arxiv '25 | Splitwise trace (Azure LLM 2023) | Azure LLM inference trace의 inter-arrival distribution을 비례 스케일링하여 사용 | **Real (scaled)** | §2.2.1, §7.1 |
| 23 | **Justitia** | Arxiv '25 | Mooncake production traces | Mooncake에서 공개한 Kimi production trace의 arrival time 사용 | **Real (time-windowed)** | §V.A |
| 26 | **PARD** | EuroSys '26 | Wikipedia, Twitter, Azure Function traces | 웹/클라우드 서비스의 실제 arrival trace (LLM 전용은 아님) | **Real** | §5.1 |

### Real Production Trace + Synthetic 혼용 논문

| # | 논문 | Venue | Real Trace | Synthetic | Section |
|---|------|-------|-----------|-----------|---------|
| 27 | **AdaGen** | EuroSys '26 | Azure LLM 2024 (real timestamp) | BurstGPT, Openchat (Poisson arrival) | §6.1, Table 1 |
| 29 | **MuxWise** | ASPLOS '26 | Mooncake Conversation + Tool&Agent (real arrival) | ShareGPT, LooGLE, OpenThoughts (Poisson) | §4.1, Table 1 |
| 7 | **VTC/Fairness** | OSDI '24 | LMSYS Chatbot Arena (real prompts, re-scaled timestamps) | Synthetic workloads (deterministic/Poisson) | §5.1, §5.3 |
| 9 | **MuxServe** | ICLR '24 | ChatLMSYS trace (real traffic distribution) | ShareGPT (Poisson) | §4.2, §4.3 |

### Real Dataset (길이 분포만) 사용 논문

| # | 논문 | Venue | Dataset(s) | Arrival | Section |
|---|------|-------|-----------|---------|---------|
| 2 | **vLLM** | SOSP '23 | ShareGPT, Alpaca | Poisson | §6.1 |
| 3 | **Parrot** | OSDI '24 | ShareGPT, Arxiv-March, Bing Copilot 길이분포, MetaGPT | Poisson | §8.1-8.4 |
| 4 | **DistServe** | OSDI '24 | ShareGPT, HumanEval, LongBench | Poisson | §6.1, Table 1 |
| 5 | **Sarathi-Serve** | OSDI '24 | openchat_sharegpt4, arxiv_summarization | Poisson | §5, Table 2 |
| 6 | **Llumnix** | OSDI '24 | ShareGPT (GPT4), BurstGPT (lengths only) | Poisson/Gamma | §6.1, Table 1 |
| 8 | **SGLang** | NeurIPS '24 | ShareGPT, MMLU, HellaSwag, ReAct/generative agent traces | Batch/offline | §6.1 |
| 10 | **CachedAttention** | ATC '24 | ShareGPT (multi-turn conversation 구조 활용) | Poisson | §4.1 |
| 11 | **SSJF** | Arxiv '24 | ShareGPT, Alpaca | Poisson | §Evaluation |
| 13 | **NanoFlow** | OSDI '25 | ShareGPT | Synthetic (varying rates) | §6 |
| 15 | **PrefillOnly** | SOSP '25 | Fingpt, Spam-t5, depression detection | Application datasets | §7 |
| 17 | **ARES** | Arxiv '25 | ShareGPT, Alpaca | Synthetic | §6.1, Table 2 |
| 19 | **Block** | Arxiv '25 | ShareGPT, BurstGPT (lengths) | Poisson | §6.1 |
| 24 | **AdaServe** | EuroSys '26 | ShareGPT, LongBench | Synthetic patterns | §5.1 |

### Synthetic Only / Benchmark Only 논문

| # | 논문 | Venue | Workload 설명 | Section |
|---|------|-------|-------------|---------|
| 1 | **Orca** | OSDI '22 | 완전 합성 (당시 공개 trace 없음을 명시) | §6 |
| 12 | **Ayo** | ASPLOS '25 | QA benchmark datasets (web_question, HotpotQA 등) + Poisson | §7 |
| 14 | **Pie** | SOSP '25 | Agentic workflow benchmarks (ReACT, CodeACT, Swarm) | §7.1 |
| 18 | **Predictable** | Arxiv '25 | 완전 합성 (T1/T2/T3 workload types) | §3.1, §4.0.1 |
| 20 | **Sherlock** | Arxiv '25 | Agent task benchmarks (CoTCollection, OMEGA, LiveCodeBench) — serving trace 아님 | §8 |
| 21 | **CONTINUUM** | Arxiv '25 | SWE-Bench, BFCL V4 + 고정 JPS rates | §6.1 |
| 30 | **Autellix** | NSDI '26 | ShareGPT, BFCL, LATS/HotpotQA + Poisson | §6.1 |

---

## 2. 공개 Trace 데이터셋 목록

### System-level Traces (timestamp + token counts, 텍스트 없음)

| Dataset | 출처 | 공개 여부 | 포함 정보 | License | 링크 |
|---------|------|----------|----------|---------|------|
| **BurstGPT** | Azure OpenAI (GPT-3.5/4) | **공개** | timestamp, session ID, model, request/response tokens, log type | CC-BY-4.0 | https://github.com/HPMLL/BurstGPT |
| **Azure LLM Inference 2024** | Azure production | **공개** | timestamp, context tokens, generated tokens (Code + Conversation 두 trace) | CC-BY | https://github.com/Azure/AzurePublicDataset → AzureLLMInferenceDataset2024 |
| **Azure LLM Inference 2023** | Azure production (Splitwise) | **공개** | 위와 유사 | CC-BY | https://github.com/Azure/AzurePublicDataset → AzureLLMInferenceDataset2023 |
| **Mooncake/Kimi Traces** | Kimi chatbot (Moonshot AI) | **공개** | timestamp(ms), input/output length, KV cache block hash IDs | Apache 2.0 | https://github.com/kvcache-ai/Mooncake/tree/main/FAST25-release/traces |

### Conversation Datasets (텍스트 포함, timestamp 없음)

| Dataset | 출처 | 공개 여부 | 포함 정보 | License | 링크 |
|---------|------|----------|----------|---------|------|
| **ShareGPT** | ChatGPT 사용자 공유 대화 | **공개 (free)** | 실제 대화 텍스트 (~53K conversations), timestamp 없음 | Apache 2.0 | https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered |
| **LMSYS-Chat-1M** | Chatbot Arena | **공개 (gated)** | 실제 대화 텍스트 (1M conversations, PII 제거), 25개 LLM, timestamp 없음 | Non-commercial | https://huggingface.co/datasets/lmsys/lmsys-chat-1m |

### 비공개 Trace

| Dataset | 논문 | 상태 |
|---------|------|------|
| **Parrot production trace** | Parrot (OSDI '24) | 코드만 공개 (https://github.com/microsoft/ParrotServe), trace 데이터 미공개 |
| **ChatLMSYS trace** | MuxServe (ICLR '24) | 공개 여부 불명. LMSYS-Chat-1M과는 별개 (서빙 시스템 관점 trace) |

---

## 3. 주요 관찰

### Trace 사용 패턴
- **30편 중 실제 production trace(arrival timestamp 포함)를 사용한 논문은 10편** (Mooncake, KunServe, eLLM, Kairos, Justitia, PARD, AdaGen, MuxWise, VTC/Fairness, MuxServe)
- 나머지 20편은 ShareGPT 등의 길이 분포 + Poisson arrival 조합이 지배적
- **Mooncake Kimi trace**가 최근 논문들에서 가장 많이 재사용됨 (Justitia, MuxWise가 직접 참조)
- **ShareGPT**는 30편 중 약 20편에서 사용 — 사실상 표준 벤치마크 dataset

### 공개 Trace의 한계
- **System-level traces** (BurstGPT, Azure, Mooncake): timestamp + token 수만 포함. 실제 프롬프트 텍스트 없음 → prefix sharing, semantic 분석 불가
- **Conversation datasets** (ShareGPT, LMSYS): 텍스트 포함하나 timestamp 없음 → arrival pattern 분석 불가
- **두 종류를 결합**해야 현실적인 workload를 재현할 수 있으나, 이를 체계적으로 수행한 논문은 드묾

### Agent/Tool Trace
- **Mooncake Tool&Agent trace**: 유일하게 공개된 실제 production agent trace (Kimi의 tool 사용 워크플로우)
- Pie, CONTINUUM, Autellix, Sherlock 등은 agent 워크플로우를 평가하지만 모두 benchmark/synthetic workload 사용

---

## 4. 불확실한 사항

| 항목 | 설명 |
|------|------|
| **SSJF (Paper #11) 상세** | ShareGPT + Alpaca + Poisson으로 확인되나, 상세 section 번호 미확인 |
| **ChatLMSYS trace 공개 여부** | MuxServe가 사용한 ChatLMSYS trace의 정확한 공개 경로 미확인. LMSYS-Chat-1M과는 형태가 다를 수 있음 |
| **Azure LLM 2025** | AzurePublicDataset repo에 2025 버전도 존재한다는 보고가 있으나, 상세 미확인 |

## 5. 해결된 사항

| 항목 | 결과 |
|------|------|
| **Kairos ref [41]** | 원문 확인 결과 **Splitwise** (Patel et al., ISCA '24) — Azure LLM Inference Dataset 2023에 해당. Parrot [35]은 baseline 시스템으로만 인용됨 |
