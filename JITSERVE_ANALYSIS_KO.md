# JITServe 분석 노트 (Llumnix 대체 후보 검토)

> 작성: 2026-07-24, 대상 머신: 8× NVIDIA B200 (Blackwell, sm_100), torch 2.10 / CUDA 13.1
> 목적: 현재 "Llumnix + vLLM 4-instance" 구성을 JITServe 멀티인스턴스로 대체 가능한지 평가

## 1. JITServe는 무엇인가 (Llumnix와의 근본 차이)

- 논문: **JITServe (NSDI'26)** — SLO-aware LLM Serving with Imprecise Request Information (arXiv:2504.20068)
- 저자: Wei Zhang, Zhiyu Wu 외 (UIUC MLSys)

| 항목 | Llumnix (현재) | JITServe |
|---|---|---|
| 역할 | **cross-instance 오케스트레이터** — N개 vLLM 인스턴스에 dispatch + live migration | **single-instance intra-engine 스케줄러** — vLLM 엔진 *내부* 스케줄러를 SLO-aware로 교체 |
| 멀티인스턴스 | 태생이 멀티인스턴스 | **없음.** `python -m jitserve.server` = 엔진 1개 = GPU 1개(또는 TP 그룹) |
| 핵심 기여 | 요청 이주/부하분산 | 한 엔진 안에서 SLO 기반 preemption/스케줄링 + QRF 출력길이 예측(imprecise info) |
| 대상 워크로드 | 일반 서빙 | ToT / DeepResearch 등 **그래프 구조 요청** (graph matching으로 stage 예측) |

**핵심:** JITServe에는 Llumnix가 하는 인스턴스 간 라우터/마이그레이션 계층이 **아예 없다.**
"JITServe를 멀티인스턴스로" = N개의 독립 JITServe 서버를 띄우고 그 앞에 라우터를 직접 붙여야 함.
마이그레이션 불가, 각 서버는 자기 배치 안에서만 SLO 스케줄링.

## 2. 저장소 구조

```
jitserve/
  server.py                     # vLLM AsyncLLMEngine 위 FastAPI 서버 (엔트리포인트)
  request_info.py               # RequestInfo, RequestType
  scheduler/
    policy.py         (340줄)   # 정책 (jitserve/ltr/fcfs/vllm/srtf/autellix)
    slo_scheduler.py  (891줄)   # SLO-aware 스케줄러 — vLLM 내부 Scheduler API에 깊게 결합
  slo_tracker/slo_tracker.py    # SLO 추적
  request_analyzer/
    prediction.py               # QRF 길이예측 서버 (socket, port 65433)
    graph_context.py            # ToT/DeepResearch 그래프 매칭
    deepresearch_trace_reader.py, similarity.py
third_party/vllm/               # vendored vLLM 스냅샷 (아래 3번 참고)
scripts/                        # benchmark_e2e.sh, benchmark_e2e_burst.sh, deepresearch-benchmark.sh, tot-benchmark.sh
benchmark/                      # trace 생성 도구 + 벤치 클라이언트
traces/                         # lmsys.json, deepresearch_filter.jsonl 등
assets/                         # QRF 모델/vectorizer (HF에서 별도 다운로드)
```

## 3. 🚨 최대 blocker: B200에서 vendored vLLM이 안 돈다

- **vendored vLLM 스냅샷 = commit `32176fe` (2024-10-27), torch `2.5.0`, CUDA 12, xformers 0.0.28** (`third_party/vllm/README.md`, `requirements-cuda.txt`)
- 이 머신: **B200 (Blackwell, sm_100)** — CUDA 13.1 / torch 2.10 스택 필요. sm_100 커널을 요구.
- 2024-10 vLLM 스냅샷 + torch 2.5.0에는 **Blackwell(sm_100) 커널이 없음.** FlashAttention/커스텀 커널이 B200에서 빌드·실행 불가.
- → 이것이 진짜 gating 이슈. 멀티인스턴스 라우팅보다 **이 vLLM 호환성 벽이 먼저**다.

## 4. 실행 파이프라인 (원본 아티팩트 기준)

1. `pip uninstall vllm -y` (충돌 방지) → `third_party/vllm/`에서 `pip install -e .` (vendored 빌드)
2. `pip install -e .` (루트, jitserve editable)
3. QRF 모델 다운로드: `huggingface-cli download En-2863/jitserve-qrf-length-predictor --local-dir assets/qrf/`
4. QRF 예측서버: `python jitserve/request_analyzer/prediction.py` (port 65433 리슨)
5. 서버: `python -m jitserve.server --scheduling-policy jitserve --model meta-llama/Llama-3.1-8B-Instruct --port 8000 ...`
6. 벤치: `bash scripts/benchmark_e2e.sh` (trace 생성 → 서버 40s 대기 → benchmark_scheduler.py 클라이언트)

- server.py 주요 인자: `--host/--port` (기본 8000), `--scheduling-policy`, `--penalty-factor`, `--max-num-seqs`, `--enable-chunked-prefill`, `--disable-prediction`, `--use-all-node`
- 정책: `jitserve`(=SLO), `oracle`(jitserve + 예측 비활성), `vllm`(=fcfs + chunked-prefill off), `fcfs`, `ltr`, `srtf`, `autellix`

## 5. 멀티인스턴스로 만들려면 (설계 방향)

JITServe 자체엔 라우터가 없으므로 우리가 앞단을 붙여야 함:

- **A. 충실 재현:** vendored vLLM을 B200에서 빌드 → 사실상 JITServe 스케줄러 훅을 최신 Blackwell 지원 vLLM으로 **포팅** 필요. slo_scheduler가 vLLM 내부 Scheduler API에 깊게 결합되어 있어, 그 사이 ~1년치 vLLM API 변화 흡수 = 큰 작업.
- **B. 실용 멀티인스턴스:** `CUDA_VISIBLE_DEVICES=i --port 800i`로 JITServe 서버 4개 기동 → 이 레포에 이미 있는 gateway/router를 앞단에 재사용해 dispatch. 단 이것도 엔진이 B200에서 떠야 하므로 결국 A의 vLLM 벽을 먼저 넘어야 함.

## 6. 결론 / 상태

- JITServe는 Llumnix의 **직접 대체가 아님** (intra-engine 스케줄러 vs cross-instance 오케스트레이터).
- 이 B200 머신에서 원본 그대로는 **vLLM 호환성 때문에 기동 불가 가능성 높음.**
- **의사결정 결과(2026-07-24):** JITServe 진행 보류. 대신 ASPLOS'26 **Niyama/QOServe** (microsoft/sarathi-serve, `niyama_asplos2026` 브랜치) 검토로 전환. (본 문서는 기록용으로 보존)
