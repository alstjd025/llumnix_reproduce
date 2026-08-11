# 출판 논문의 글쓰기 표본 분석 — related_works/ 4편

작성 2026-08-11. 목적: FluidServe 논문(§2 Background, §3 Motivation은 `motivation_v3.md`가
논증의 정본)을 쓸 때 문장·절 구성을 흉내 낼 수 있도록, peer-reviewed venue에 실린 논문
4편의 글쓰기를 문단 단위로 분해한 것. 내용(기술적 주장) 분석은
`related-works-review.md`가 정본이고, 이 문서는 **형식**만 다룬다.

분석 대상 (전부 `related_works/`의 PDF):

| 논문 | venue | 아래에서 부르는 이름 | 페이지 표기 |
|---|---|---|---|
| QoServe: Breaking the Silos of LLM Inference Serving | ASPLOS '26 | QoServe | 인쇄 페이지 1492–1499 = PDF 1–8쪽 |
| AdaGen: Workload-Adaptive Cluster Scheduler for Latency-Optimal LLM Inference Serving | EuroSys '26 | AdaGen | 인쇄 1111–1118 = PDF 1–8쪽 |
| JITServe: SLO-aware LLM Serving with Imprecise Request Information | NSDI '26 | JITServe | 인쇄 825–831 = PDF 2–8쪽 (PDF 1쪽은 USENIX 표지) |
| Simple is Better: Multiplication May Be All You Need for LLM Request Scheduling | OSDI '26 | LMetric (시스템 이름) | 인쇄 55–64 = PDF 2–11쪽 (PDF 1쪽은 표지) |

읽은 범위: 논문마다 앞 7–8페이지 (abstract, intro, background/motivation, design 앞부분).
LMetric은 characterization 절(§4)과 설계 절(§5) 앞부분까지 추가로 읽었다(PDF 9–11쪽).
제외: SLOs-Serve와 PolyServe는 arXiv, scorpio.pdf는 1쪽에 "Preprint. Under review." 표기가
있는 arXiv 판이라 출판본이 아니므로 표본에서 뺐다.

주의: LMetric이 우리와 가장 가까운 논문이다 — cluster 앞단의 global scheduler(routing)
정책이 주제이고, admission 없이 routing만 다루지만, "두 목표(KV cache 재사용과 load
balancing)가 충돌한다"는 긴장 구도와 "기존 결합 방식들을 하나씩 측정으로 해부한다"는
motivation 구성이 우리 논문이 해야 하는 일과 같은 형태다.

---

## 1. 섹션 구성 — 네 논문의 절 골격

### QoServe (ASPLOS '26)

1. Introduction — 2. **Background and Motivation** (2.1 LLM Inference / 2.2 Production
Deployment Landscape / 2.3 Deployment Challenges / 2.4 Analysis of Multi-SLA scheduling
policies / 마지막에 굵은 글씨 "Summary." 문단) — 3. QoServe: Design and Implementation
(3.1 Overview부터) — 4. Evaluation.

- background와 motivation이 **한 절로 합쳐져** 있다. 2.1이 순수 background(prefill/decode,
  지표 정의), 2.2–2.3이 정성적 문제 제기(siloed 배포의 비효율), 2.4가 **측정 기반
  motivation**(기존 정책 4종을 그림 하나로 비교)이다. 즉 절 안에서
  정의 → 현황 → 문제 → 측정 순으로 내려간다 (p.1494–1495, PDF 3–4쪽).
- §2 끝의 "Summary." 문단이 절 전체를 한 문단으로 닫는다: "In this paper, we address
  these critical infrastructure challenges by introducing a QoS-aware serving framework,
  QoServe." (p.1495, PDF 4쪽)
- Evaluation 첫머리가 번호 붙은 질문 목록이다: "Our evaluation aims to answer the
  following questions." 다음에 질문 5개, 각 질문에 해당 소절 번호를 괄호로 단다
  (p.1498, PDF 7쪽).

### AdaGen (EuroSys '26)

1. Introduction — 2. **Background and Motivation** (2.1 Preliminaries / 2.2 Shortcomings
of Existing Cluster Schedulers) — 3. **Insights and Challenges** (3.1 Selective Distributed
Execution across Instances / 3.2 Length- and Distribution-Awareness / 3.3 Challenges) —
4. System Design of AdaGen (4.1 Overview부터) — 5 구현, 6 평가.

- motivation을 **두 절로 쪼갠** 유일한 표본이다. §2.2는 "기존 것이 부족하다"를 보이고
  (반례 → exhaustive search 실험), §3은 "그러면 무엇이 필요한가"를 insight 단위로 쌓는다.
  §3.3이 challenge를 (i)(ii)(iii)로 나눠 §4의 설계 단계와 1:1 대응시킨다 (p.1116–1117,
  PDF 6–7쪽).
- 소절 제목은 전부 명사구다 ("Shortcomings of Existing Cluster Schedulers"). 대신 각
  소절이 **회색 박스의 "Observation N." / "Key Takeaway:" 문장**으로 끝난다 — 주장문을
  제목이 아니라 박스에 둔다 (p.1114, 1116, PDF 4·6쪽).

### JITServe (NSDI '26)

1. Introduction — 2. **Motivation** (2.1 Characterizing LLM Serving Requests /
2.2 Challenges and Limitations of Existing Solutions) — 3. JITServe Overview —
4. JITServe Design (4.1–4.3) — 5 구현, 6 평가.

- 절 이름이 "Background and Motivation"이 아니라 그냥 **"Motivation"**이고, background
  (LLM inference가 어떻게 도는지)는 intro와 §3 Overview에 흡수했다. §2는 처음부터 끝까지
  자기 측정(요청 수백만 건 분석 + 사용자 설문 550명)으로 채워져 있다 (p.826–828, PDF 3–5쪽).
- §2가 도출한 요구 조건 3개(Generalizability / Goodput Efficiency / Deployability)를
  §2 끝에 bullet로 적고, §4 첫 문장이 그것을 **그대로 되풀이하며 설계 소절에 대응**시킨다:
  "SLO-aware LLM serving must address three fundamental challenges: (1) Generalizability:
  proactively estimating and refining request information during execution to meet diverse
  SLOs (§4.1), which informs (2) Goodput Efficiency: scheduling individual requests and
  their batch compositions to maximize service goodput (§4.2), and (3) Deployability:
  adapting to diverse deployment considerations, such as fairness and strong scaling
  capabilities (§4.3)." (p.829, PDF 6쪽)
- 측정 방법의 세부는 본문에 없다: "Detailed methodology and analysis are provided in
  Appendix A." (p.826, PDF 3쪽) — motivation 절은 결과와 주장만 싣는다.

### LMetric (OSDI '26)

1. Introduction — 2. LLM Serving and Scheduling (background) — 3. The Analysis Framework —
4. **Characterizing LLM Request Scheduling** (4.1 Characterization Methodology /
4.2 Load-balancing Alone is Insufficient for LLM / 4.3 KV$-awareness vs. Load balancing:
The Trade-off / 4.4 The Case of Linear Combination / 4.5 The Case of Filter-based
Combination / 4.6 The Case of Simulation-based Combination) — 5. **Simple Multiplication
May Be All You Need** (5.1 The Choice of the Indicators / 5.2 Benign and Failure Cases
Analysis) — 6 평가, 7 논의.

- motivation이 "Motivation"이라는 이름 없이 **약 3.5페이지짜리 측정 연구 절(§4)**로
  들어가 있고, 그 앞에 측정 도구를 만든 절(§3)이 따로 있다. 기여 목록의 첫 항목도
  "The first systematic study of how to efficiently schedule LLM serving requests in a
  cluster (§4)"다 (p.56, PDF 3쪽) — **characterization 자체를 기여로 세운다.**
- 소절 제목이 **주장문이거나**(4.2 "Load-balancing Alone is Insufficient for LLM",
  §5 "Simple Multiplication May Be All You Need" — 논문 제목도 주장문이다) 검토 대상을
  지목한다(4.4–4.6 "The Case of X"). 기존 접근 하나당 소절 하나, 각 소절 안에서
  pseudocode 그림 + "**Cons #1.** ... **Cons #2.** ..." 굵은 글씨 소제목으로 결함을
  번호 매겨 해부한다 (p.60–62, PDF 7–9쪽).
- 실험 설정은 §4.1 한 소절에 몰아 둔다 (testbed, 모델, trace 4종, trace scaling 규칙) —
  그 뒤의 모든 motivation 그림이 이 설정을 공유한다 (p.58, PDF 5쪽).

### 구성 요약

| | motivation의 위치 | background와의 경계 | 실험 설정 설명 위치 |
|---|---|---|---|
| QoServe | §2 안의 2.4 (한 소절) | 같은 절 안에서 2.1(정의) → 2.2–2.3(현황·문제) → 2.4(측정) | 그림 캡션과 본문에 최소한만 |
| AdaGen | §2.2 + §3 (두 절) | §2.1 Preliminaries가 background | motivation 본문 안에 인라인 (모델·GPU·trace·Poisson rate까지) |
| JITServe | §2 전체 | background는 intro·§3으로 흡수 | Appendix A로 미룸 |
| LMetric | §4 전체 (독립 절) | §2가 background, §3이 측정 도구 | §4.1 전용 소절 |

---

## 2. Introduction의 문단별 흐름

네 논문 모두 문단 7–9개이고 역할 배치가 거의 같다. 공통 골격:
**배경(1) → 기존 접근의 한계(1–2, 한계 하나당 문단 하나) → "In this paper, we present
X ... through N key ideas"(1) → 각 key idea의 challenge와 해법(1–2) → 평가 수치(1) →
기여 bullet 목록 → (선택) 로드맵 한 문단.** 기여는 **네 논문 모두 정확히 3개**다.

### QoServe (p.1492–1493, PDF 1–2쪽)

1. 배경: "Large language models (LLMs) have transformed applications across diverse
   domains including conversational assistants, coding assistants, content generation,
   and summarization. These applications can have very different latency requirements..."
2. 기존 접근의 한계 1 (구조적): "Current LLM serving solutions primarily adopt a
   coarse-grained categorization, segregating requests into two broad service classes...
   This siloed deployment, however, creates several inefficiencies..." — 한계를 수치로
   구체화한다 ("e.g., 28% lower as shown in Figure 4", 그림 forward reference).
3. 기존 접근의 한계 2 (overload): "Furthermore, current inference systems struggle under
   load fluctuations and overload conditions. ... Neither strategy adequately manages
   the complex trade-offs between throughput, latency, and fairness during such demand
   surges."
4. 시스템 선언: "In this paper, we present QoServe, a QoS-driven LLM inference serving
   system that addresses these limitations through **two key ideas**." — 두 아이디어를
   이탤릭 구절로 박는다 ("co-scheduling requests with diverse QoS targets on a shared
   rather than siloed infrastructure", "allows graceful service degradation during
   overload conditions").
5. key idea 1의 challenge와 해법: "Efficiently supporting multiple QoS classes on a
   shared serving instance poses significant challenges. One approach is to use the
   smallest chunk size... However, this would result in low throughput..." — 순진한
   해법을 먼저 세우고 그 결함으로 자기 설계를 정당화하는 형태.
6. key idea 2의 challenge와 해법 + 수치: "...QoServe consistently meets latency targets
   for over 95% of requests..."
7. 기여 3개 (번호 목록).
8. 로드맵: "The rest of the paper is structured as follows. (§2) outlines..."

### AdaGen (p.1111–1113, PDF 1–3쪽)

1. 배경: "Large Language Models (LLMs) have revolutionized applications across diverse
   domains such as healthcare and software development."
2. 시스템 모델 정의: cluster scheduler 대 instance scheduler를 이탤릭으로 정의하고, 기존
   cluster scheduler가 load balancing을 목표로 한다고 정리한다. — **용어 정의가 intro
   문단 2에 온다.**
3. 핵심 반박: "Though load-balancing can achieve minimal inference latency for a
   traditional deep learning (DL) workload consisting of requests with uniform
   characteristics (e.g., image recognition), our experimental analysis on real traces
   finds that only load-balancing is not enough to achieve minimal latency (or maximal
   SLO attainment) for an LLM workload (§2.2), where the requests can have diverse
   characteristics." — 통념을 양보절로 인정한 뒤 자기 측정으로 뒤집고, 근거 절 번호를
   바로 단다.
4. 핵심 개념 도입: compute layout을 이탤릭으로 정의하고 "While load-balancing ensures
   optimized resource utilization, it is the compute layout that determines the
   time-to-first-[decode]-token (TTFT) and time-between-[decode]-tokens (TBT) latencies
   of each request."
5. 반례 하나를 그림과 함께 손으로 계산: Llumnix가 내는 layout 대 더 나은 layout을
   iteration 수(2.33 대 2.17)로 비교한다 (Fig. 1). — intro 안에서 worked example을
   완주하는 것이 이 논문의 특징이다.
6. 시스템 선언: "To address this challenge, in this paper, we propose AdaGen, a
   workload-adaptive cluster scheduler designed to minimize latency and thus maximize
   SLO attainment by leveraging the diversity pattern present in the requests in order
   to optimize the compute layouts across multiple instances."
7–8. 설계 요약 (multi-step 구조, simulation 기반 추정).
9. 기여 3개 (번호 목록). 로드맵 문단은 없다.

### JITServe (p.825–826, PDF 2–3쪽)

1. 배경: "As large language models (LLMs) enable language-driven interaction between
   humans and intelligent agents, modern applications increasingly go beyond conventional
   chatbot scenarios like ChatGPT." — agentic 워크로드로 즉시 좁힌다.
2. 자기 측정으로 만든 분류: "Our analysis of millions of LLM requests across real-world
   applications—corroborated by user studies with hundreds of LLM users and extensive
   discussions with service providers—reveals that requests fall into three dominant
   patterns (§2.1): (i) Latency-sensitive requests: ... (ii) Deadline-sensitive
   requests: ... (iii) Compound requests: ..." — intro 문단 하나에 taxonomy 전체를
   (i)(ii)(iii)로 넣고 각 항에 예시 인용을 단다.
3. 목표 지표 정당화: service goodput 최대화가 왜 목표인지, 전용 클러스터가 왜 비현실적인지.
4. 기존 것의 한계: "existing LLM serving systems remain misaligned with service goodput.
   Instead, they typically optimize for aggregate serving throughput, average request
   completion time, or latency-sensitive requests only, which we prove can yield
   arbitrarily poor service goodput (Appendix E.1)." — 한계 주장에 증명 부록을 단다.
5. 시스템 선언: "This paper introduces JITServe, an SLO-aware serving system designed to
   maximize service goodput across diverse LLM workloads. At its core, JITServe employs
   a Just-in-Time (JIT) scheduling principle: it leverages imprecise request information
   ... and progressively refines these estimates as generation unfolds."
6. 기술 1 (불확실성 두 종류와 각각의 해법: QRF 상한 추정, pattern graph).
7. 기술 2 (scheduling이 왜 2차원 문제인지 → GMAX 알고리즘).
8. 구현·평가 수치: "JITServe improves service goodput by 1.4×–6.3×, achieving
   28.5%–83.2% resource savings for equivalent goodput."
9. 기여 3개 (bullet 목록).

### LMetric (p.55–56, PDF 2–3쪽)

1. 문제 선언으로 시작 — 배경 문단이 없다: "This paper studies how to efficiently route
   LLM requests to a cluster of serving instances—the minimal LLM engine deployment
   unit." 이어서 global scheduler를 정의한다.
2. 왜 중요한가: "Providing an effective scheduling policy is crucial for cluster-level
   LLM serving."
3. 왜 어려운가 — 두 목표의 긴장: "Achieving a good LLM-specific scheduling policy is
   non-trivial: First, considering only load balancing across instances—which is adopted
   by a recent state-of-the-art serving system vLLM and traditional request routing—is
   insufficient. ... However, incorporating only KV$-aware indicators into scheduling
   decisions (e.g., the KV$ hit ratio if routing a request to an instance) is also
   insufficient, because it biases requests towards instances with KV$ hits and hurts
   load balancing across instances (§4.3)."
4–6. 기존 결합 방식 세 가지를 문단 하나씩 해부: linear combination(하이퍼파라미터 튜닝
   필요), filter-based(여전히 튜닝 필요 + load balancing으로 편향), simulation-based
   (모델·하드웨어별 시뮬레이터 개발 비용, 부정확하면 성능 하락). 각 문단이 "(§4.4)"처럼
   해당 측정 소절을 가리킨다.
7. 해법 선언: "In this paper, we show that using the multiplication of one indicator for
   KV$-awareness and one indicator for load balancing as the scheduling score can
   effectively combine the two objectives without complex hyperparameter tuning or any
   simulator. The key idea is to replace the addition operation in a linear combination
   with multiplication."
8. 정직한 한정: "Making this simple method work well in practice requires care." — 지표
   선택이 중요하다는 것과 실패 조건이 존재한다는 것을 intro에서 미리 인정한다.
9. 평가 수치 + 배포 사실: "...reduce TTFT by 92% and 39%, and TPOT by 24% and 51%,
   compared to vLLM-v1 and an in-production scheduler... LMetric has been deployed in
   production in BAILIAN on hundreds of GPUs."
10. 기여 3개 (bullet). 그 뒤에 범위 한정 문단: "**Discussion: PD-colocation vs.
   PD-disaggregation.** We focus on PD-colocated serving in this paper... We discuss how
   our observations and solutions apply to PD-disaggregation in §7." — 심사에서 나올
   반론 하나를 intro 끝에서 미리 닫는다.

---

## 3. Motivation 절의 구성

### 주장 하나당 그림 하나인가

- **LMetric**: 그렇다, 가장 엄격하다. §4.2부터 소절마다 주장 하나(기존 접근 하나의 결함)
  가 있고, 그 소절의 그림들이 전부 그 주장만 뒷받침한다. pseudocode 그림(Fig. 6, 13, 14)
  으로 검토 대상 정책을 먼저 고정하고, 측정 그림으로 결함을 보인다. 하이퍼파라미터
  민감도는 λ를 4개 값으로 sweep한 4 trace × P50/P90/P99 격자 그림 하나로 보인다
  (Fig. 11, p.60, PDF 7쪽).
- **QoServe**: motivation 측정은 그림 하나(Fig. 2)에 4개 패널(median latency / tail
  latency / deadline violation / long-job violation)로 압축했고, 캡션이 5문장짜리
  요약이다: "FCFS breaks down very quickly because urgent requests can be stalled by
  non-urgent ones. Deadline-aware policies like EDF are better than FCFS, but cannot
  gracefully degrade at high loads because of intense queue buildup. ..." (p.1494,
  PDF 3쪽) — **캡션만 읽어도 절의 결론이 복원되게 쓴다.**
- **AdaGen**: 주장 하나당 "worked example 그림 + 실측 그림" 쌍이다. §2.2에서 Fig. 2
  (반례 손계산) → Fig. 3(exhaustive search 실측), §3.1에서 Fig. 4(반례) → Fig. 5–6
  (실측), §3.2에서 Fig. 7–8(실측). 반례를 먼저 보여 메커니즘을 이해시키고 실측으로
  일반성을 보이는 2단 구성이다.
- **JITServe**: 주장 하나당 그림 또는 표 하나. taxonomy는 Fig. 1(그림 설명) + Table 1
  (사용자 설문 수치), 불확실성 주장은 Fig. 2(a) LLM call 수 CDF + Fig. 2(b) 길이 예측
  오차 CDF, 기존 시스템의 실패는 Fig. 3(Sarathi 대 Autellix 대 oracle) 하나다.

### 각 소절 첫 문장이 주장문인가

- LMetric: 소절 제목 자체가 주장이거나("Load-balancing Alone is Insufficient"), 첫
  굵은 소제목이 "Cons #N."으로 주장을 연다. AdaGen: 첫 문장은 서술이고 주장은 끝의
  Observation 박스에 온다. JITServe: 굵은 run-in 소제목("Pervasive Request
  Uncertainties.", "Misaligned Service Goodput and Inefficiency in Existing Solutions.")
  이 주장을 압축하고, 이어지는 첫 문장이 풀어 쓴다. QoServe: 소절 제목은 명사구,
  본문 첫 문장이 주장("Despite their theoretical foundations, we observe that these
  scheduling approaches fundamentally struggle when applied to large language model (LLM)
  inference workloads.", p.1495, PDF 4쪽).

### 실험 설정을 어디서 설명하나

§1의 표 참조. 넷이 다 다르지만 공통점은 **motivation 절의 모든 그림이 설정 하나를
공유하고, 그 설정을 한 곳에만 적는다**는 것이다. AdaGen만 인라인으로 다 적는데
(p.1114, PDF 4쪽: "Each model was using 2 instances. For Llama3-8B, each instance was
1 Nvidia H100 GPU. ... we followed prior works [1, 2] to generate the arrival times
using Poisson distribution with request rate=10 reqs/s.") 그 대신 문단 하나로 끝낸다.

---

## 4. 문장 스타일 — 실제 문장들

### 주장을 여는 방식

수치로 열지 않는다. **일반 진술로 열고, 같은 문단 안에서 수치·그림 참조로 즉시
구체화한다.** 예:

- AdaGen: "These results demonstrate that the existing schedulers provide sub-optimal
  latency performance." 다음 문장에서 "setting the P90 TTFT of the optimal policy as
  the TTFT SLO results in only 25% and 45% SLO attainment for round-robin and Llumnix,
  respectively—indicating a 2×–3.6× improvement in TTFT SLO attainment." (p.1114, PDF 4쪽)
- LMetric: "As shown in Figure 7, adding KV$-awareness to a load-balancing-only policy
  improves the average TTFT by 84% and the average TPOT by 17%, which is as expected
  because it increases the KV$ hit ratio as profiled in Figure 8." (p.59, PDF 6쪽) —
  수치, 그림 근거, 메커니즘 설명("because...")을 한 문장에 넣는다.

### 상투 표현 (실제 빈도가 높은 것)

- "we observe that ..." (QoServe p.1495), "we found that ..." (LMetric p.56, 60),
  "Our analysis reveals ..." (AdaGen p.1112), "our experimental analysis on real traces
  finds that ..." (AdaGen p.1112), "Our studies show that ..." (JITServe p.826).
- 결과 문장: "These results demonstrate that ..." (AdaGen p.1114), "As shown in
  Figure N, ..." (전부), "Figure N shows/presents ..." (전부).
- 한계 인정·추측에는 hedging을 명시한다: "We hypothesize that this is because the
  optimal weight may vary over time." (LMetric p.60, PDF 7쪽) — 근거 없는 설명은
  hypothesize로 표시하고 단정하지 않는다.
- 전환: "To address this challenge, ..." (AdaGen, JITServe), "However, ..." 로 순진한
  해법을 기각 (QoServe p.1493: "One approach is to use the smallest chunk size...
  However, this would result in low throughput and high cost for all service classes.").

### 능동/수동, 문장 길이

- 거의 전부 we-능동태다. 수동태는 설정 서술에만 온다 ("Request arrival times are
  generated using a Poisson distribution", QoServe p.1499).
- 문장 길이는 중간(15–30단어)이 주고, 핵심 주장 문장은 길어도 콜론·대시로 구조를 준다.
  LMetric의 긴 문장 예: "Achieving a good LLM-specific scheduling policy is non-trivial:
  First, ... is insufficient. This is because ... (§4.2). ... However, ... is also
  insufficient, because ... (§4.3)." — 긴 주장은 "First / However" + "This is because"
  + 절 번호 참조로 지탱한다.

### 이름 붙이기

새 메커니즘·개념은 **첫 등장에서 이탤릭으로 이름 붙이고 그 뒤 같은 이름만 쓴다**:
deadline slack, eager relegation, hybrid prioritization, selective preemption (QoServe);
compute layout, selective distributed execution (AdaGen); pattern graphs, margin goodput,
minimum serving bandwidth (JITServe); KV$ hotspots, indicator factory (LMetric).
LMetric은 지표에도 굵은 고유명을 준다 (**P-token**, **BS**). — FluidServe로 치면 게이트,
class pinning, prefix-aware charging에 영어 고유명을 정해 두고 전체에서 그 이름만 쓰는 것.

---

## 5. Top-down 정도

- **절 첫머리의 절 요약**: JITServe가 가장 명시적이다 — §2 첫 문장이 절 지도다: "We
  begin with our real-world studies of LLM requests (§2.1), which reveal new challenges
  that motivate our work (§2.2)." (p.826, PDF 3쪽). AdaGen §3도 같은 형태: "Towards the
  design, we performed extensive experiments using real traces and derived several
  insights ... Below, we describe the details of each insight." (p.1114). QoServe §3,
  §4와 LMetric §3, §5도 첫 문단이 절 전체를 요약한다. **소절로 바로 들어가는 절이 없다.**
- **소절 제목**: LMetric만 주장문 제목을 쓰고 나머지 셋은 명사구다. 대신 명사구 제목을
  쓰는 논문은 주장을 다른 장치에 싣는다 — AdaGen은 Observation 박스, JITServe는 굵은
  run-in 소제목, QoServe는 소절 첫 문장.
- **그림 캡션**: QoServe와 JITServe는 캡션을 주장문으로 쓴다 (JITServe Fig. 3: "Existing
  advances face significant performance drops due to growing LLM request diversity.",
  p.828). AdaGen·LMetric은 캡션을 서술("Compute layouts of 2 instances for workload_2
  ...")로 쓰고 주장은 본문에 둔다.
- **Evaluation의 질문 목록**: QoServe가 번호 질문 5개로 연다 (p.1498). 우리 실험 기록
  (EXP 파일들이 사전 질문을 적는 방식)과 같은 형태라 옮기기 쉽다.

---

## 6. Challenge / limitation 절의 구성

- **AdaGen §3.3 "Challenges"** (p.1116–1117, PDF 6–7쪽): challenge를 (i)(ii)(iii) 세
  개로 나누고, (i)는 다시 sub-bullet 두 개로 쪼갠다. 각 항이 "~하려면 ~가 필요한데
  그것이 왜 비싼가" 형태의 한 문장 단락이다. 예: "(iii) Realizing these insights
  requires analyzing compute layouts, but generating them via actual execution is
  infeasible during scheduling. How can compute layouts be efficiently approximated
  without real execution?" — **challenge를 의문문으로 끝내고**, §4의 소절이 하나씩 답한다.
- **JITServe**: challenge 절이 따로 없고, §2.2 제목이 "Challenges and Limitations of
  Existing Solutions"다. 굵은 run-in 두 개("Pervasive Request Uncertainties.",
  "Misaligned Service Goodput and Inefficiency in Existing Solutions.")로 나누고, 절
  끝에서 요구 조건 3개로 변환한다.
- **LMetric**: limitation을 "Cons #N."으로 번호 매기는 것이 사실상 challenge 절이다.
  같은 Cons 이름("Cons #1. Requires workload-specific hyperparameter tuning" /
  "Cons #2. Sub-optimal performance")을 §4.4와 §4.5에서 **반복 사용**해서, 서로 다른
  기존 접근이 같은 축에서 실패한다는 것을 제목만으로 보이게 한다 (p.60, PDF 7쪽).
- **QoServe §2.3 "Deployment Challenges"**: 굵은 run-in 두 개("Resource provisioning
  and utilization.", "Lack of graceful service degradation.")로 나눈 정성적 서술이다.

---

## 7. 공통 패턴과 논문별 차이

### 공통 (4/4)

1. 기여 목록은 정확히 3개다. 각 항이 절 번호를 단다.
2. intro의 시스템 선언 문단은 "In this paper, we present/propose/show ..." + "N key
   ideas/insights" 구조다. key idea 개수를 명시하고 이탤릭으로 박는다.
3. 기존 접근의 한계는 **한계 하나당 문단 하나**이고, 문단 안에 근거가 되는 자기 측정의
   절 번호 또는 그림 번호를 단다. 인용만으로 한계를 주장하는 문단이 없다.
4. 굵은 run-in 소제목(마침표로 끝나는 굵은 구절 + 이어지는 본문)을 background·motivation
   ·design 전반에서 쓴다. 소절보다 한 단계 아래의 구조화 장치다.
5. 모든 정량 주장은 "무엇 대비 몇 %/몇 배"까지 같은 문장에 적는다. baseline 이름 없이
   개선율만 적는 문장이 없다.
6. 1페이지에 개요/teaser 그림(Figure 1)이 있다. QoServe는 4패널 headline 결과, AdaGen은
   worked example, JITServe는 요청 패턴 3종 그림, LMetric은 prefill/decode 배경 그림.
7. 절 첫머리에 절 요약 문단이 있다 (§5 참조).

### 차이

| 축 | QoServe | AdaGen | JITServe | LMetric |
|---|---|---|---|---|
| motivation의 증거 형태 | 기존 정책 4종 비교 그림 1개 | worked example + exhaustive search | 자기 워크로드 연구(설문·trace 분석) | 기존 접근 계열별 측정 해부 |
| 주장문을 싣는 장치 | 소절 첫 문장 | Observation/Key Takeaway 박스 | 굵은 run-in + 주장문 캡션 | 소절 제목 + Cons #N |
| challenge → design 연결 | 느슨함 (Summary 문단) | (i)(ii)(iii) ↔ §4 단계 1:1 | 요구 조건 3개 ↔ §4.1–4.3 1:1 | Cons ↔ §5의 설계 선택 1:1 |
| intro 배경 문단 | 있음 (LLM 일반) | 있음 + 시스템 모델 정의 | 있음 (agentic으로 좁힘) | **없음** — 문제 선언으로 시작 |
| 로드맵 문단 | 있음 | 없음 | 없음 | 없음 |

---

## 8. FluidServe 논문에 적용할 때

routing/admission 계층 논문이라는 점에서 특히 맞는 패턴. 각 항에 출처를 단다.

1. **intro를 배경이 아니라 문제 선언으로 열 수 있다** (LMetric p.55). 우리도 "이 논문은
   여러 SLO 클래스의 요청을 고정된 엔진 fleet 위에서 어느 인스턴스로 보내고 무엇을
   받아들일지 결정하는 문제를 다룬다"로 열고, cluster 앞단 global scheduler라는 시스템
   모델을 문단 1에서 정의하는 형태가 가능하다. LLM 일반론 배경 문단은 없어도 된다.
2. **긴장 구도를 intro 문단 3에 명시한다** (LMetric p.55: "Achieving a good LLM-specific
   scheduling policy is non-trivial: First, ... is insufficient. However, ... is also
   insufficient."). 우리 것으로는 "load balancing만으로는 클래스 간 간섭을 못 막고,
   정적 클래스 파티션은 부하 변동에서 용량을 버린다"가 같은 형태의 두 축이다. 각 축에
   그것을 측정한 motivation 소절 번호를 단다.
3. **기존 접근 계열을 소절 하나씩 측정으로 해부하고 Cons 이름을 재사용한다** (LMetric
   §4.4–4.6, p.60–62). 우리 arm 이름 규약(Llumnix / Llumnix SLO / PolyServe / llm-d /
   vLLM router)이 이미 계열 하나씩이다. 같은 결함 축(예: "클래스 간 간섭을 못 막는다" /
   "포화에서 용량을 버린다")을 제목으로 반복하면 표 없이도 비교가 된다. 단, 우리 저장소
   규칙대로 각 수치에 분모(admitted/offered)와 반복 횟수를 같이 적는다.
4. **motivation의 요구 조건을 절 끝에 bullet로 뽑고 design 절 첫 문장이 그것을 되풀이하며
   소절에 대응시킨다** (JITServe p.828–829). `motivation.md`가 이미 "요구 조건 넷 ↔ 설계
   부분" 표를 갖고 있으므로, JITServe의 "SLO-aware LLM serving must address three
   fundamental challenges: (1) ... (§4.1), (2) ... (§4.2), (3) ... (§4.3)" 문장 형태로
   옮기면 된다.
5. **주장 하나당 그림 하나 + 캡션을 주장문으로** (QoServe Fig. 2 p.1494, JITServe Fig. 3
   p.828). motivation 그림 다섯 개의 정본 README가 이미 그림마다 "주장"을 적어 두었으므로,
   그 주장 문장을 캡션 첫 문장으로 옮기고, 캡션 나머지에 조건·분모·반복 횟수를 적는다.
6. **실험 설정은 characterization 절 첫 소절에 한 번만** (LMetric §4.1 p.58). 우리
   motivation 그림들이 같은 클러스터·같은 워크로드 설정을 공유하므로 "4× A100 엔진,
   Llama-3.1-70B, 클래스 3종(chat/deepresearch/swe), trace와 rate sweep 정의"를 §3 첫
   소절에 몰고, 각 그림에서는 조건 차이만 적는다. swe SLO 예산이 arm에 따라 두 판
   (m1/m1f)이라는 것도 이 소절에 적어야 §3 전체에서 반복하지 않는다.
7. **양보절로 통념을 인정한 뒤 측정으로 뒤집는 문장형** (AdaGen p.1112: "Though
   load-balancing can achieve minimal inference latency for a traditional DL workload
   ..., our experimental analysis on real traces finds that only load-balancing is not
   enough ..."). 우리 §3.1(우위 전부가 chat 항이라는 분해)이나 "격리를 구성하지 않고
   결과로 얻는다" 주장을 열 때 같은 형태를 쓴다 — "정적 파티션이 격리를 보장하는 것은
   맞지만, 우리 측정에서는 그 격리가 포화 구간에서 SLO 달성 요청 수를 줄인다" 식으로.
8. **범위 한정을 intro 끝에 굵은 run-in으로** (LMetric "Discussion: PD-colocation vs.
   PD-disaggregation." p.56). 우리가 미리 닫아야 하는 반론 — 고정 fleet 가정, 엔진 레벨
   SLO 스케줄러와의 관계(related-works-review.md §9가 정본), migration을 안 쓰는 것 —
   을 각각 이 형태의 한 문단으로 intro 또는 §2 끝에 둔다. `motivation_v3.md` §5(반론
   여섯)의 앞 항목들을 여기로 옮기는 것이 자연스럽다.

그 밖의 일반 권고: 기여는 3개로 압축하고 각 항에 절 번호를 달 것(4/4 공통), evaluation은
번호 질문 목록으로 열 것(QoServe p.1498 — EXP 파일의 사전 질문을 그대로 옮길 수 있다),
새 메커니즘 이름(게이트, class pinning, prefix-aware charging 등)은 첫 등장에서 이탤릭
정의 후 전체에서 같은 이름만 쓸 것(4/4 공통).
