# DEV — per-request → engine attribution

**날짜**: 2026-07-20 · **상태**: implementing · **동기**: EXP-13 리뷰에서 "엔진별
SLO attainment을 그려달라"는 요청이 나왔는데 **불가능**했다. 이유 2가지:

1. 클라이언트는 gateway만 보므로 `metrics.csv`에 요청→엔진 정보가 없다.
2. 서버측 엔진 metric은 TTFT/ITL이 `sum`+`count`(평균)뿐이고 histogram bucket이
   없어 "TTFT≤5s인 요청 비율"을 엔진별로 계산할 수 없다.

그래서 EXP-13에서는 대체물(엔진별 mean TTFT/ITL vs rate,
`plot_per_engine_slo_signals.py`)만 그렸다. 이 문서는 **진짜 per-request 엔진
귀속**을 만드는 작업 기록이다.

## 발견 — 필요한 조각이 이미 다 존재한다 (2026-07-20 실측)

게이트웨이 바이너리를 다시 빌드할 필요가 없다. 세 조각을 이으면 된다:

| 조각 | 출처 | 실측 예시 |
|---|---|---|
| ① 요청 uuid | **응답 스트림 청크의 `id`** (스트리밍에도 들어옴) | `cmpl-efd027be-894d-...` |
| ② uuid → instance_id | **스케줄러 로그** `utils.go:207` | `[Schedule] dispatch request <uuid> to neutral instance 1784541941884358327 for prefill` |
| ③ instance_id → 포트 | **스케줄러 로그** `cms_read_client.go:382` | `instanceID=1784541941884358327, metadata=... api_server_port:8002` |

→ 체인: `task_id → uuid → instance_id → api_server_port(8000-8003)` → 기존
`server_metrics/engine_<port>.jsonl` 시계열과 조인 가능.

주의: gateway 응답 헤더에는 인스턴스 정보가 **없다**(확인함). `system_fingerprint`도
빈 문자열. 그래서 ①은 본문 `id`에서 얻어야 한다.

## 설계

### A. 클라이언트: 요청 uuid 캡처 (sidecar, 스키마 무변경)

`metrics.csv`에 컬럼을 추가하면 기존 run과의 호환이 깨지므로(프로젝트 규칙:
"run output schema 임의 변경 금지, 새 분석은 새 CSV 추가") **sidecar 파일**로
분리한다.

- `LlumnixCompletionsLLM`(`workloads/swe_bench_coding/agent.py`)이 첫 청크의
  `id`를 보관 → `invoke_with_tracking`이 호출 종료 시 sidecar에 1행 기록.
- 출력: `request_ids.jsonl` — `{task_id, call_index, request_id, t_start, t_end}`.
- MP(24 프로세스) 대응: 워커별 `request_ids.p<k>.jsonl` 샤드로 쓰고 부모가
  기존 샤드 병합 로직과 같은 방식으로 합친다.

### B. 서버: 스케줄러 dispatch 로그 캡처

`llumnix_metrics.py` 수집기가 run 동안 스케줄러 로그를 스트리밍해
`server_metrics/scheduler_dispatch.log`로 저장한다.

- 필터: `[Schedule] dispatch request` (②) + `refreshInstanceMetadata` (③) +
  migration 관련 라인.
- 조건별 cold restart 이후 수집기가 뜨므로 로그 스트림도 그 시점에 시작한다.
- 전체 로그는 filter 라인이 대부분이라 매우 크므로 **반드시 grep 필터** 후 저장.

### C. 분석: 조인 스크립트

`analysis_scripts/request_level/build_request_engine_map.py`
- 입력: `request_ids.jsonl` + `server_metrics/scheduler_dispatch.log`
- 출력: `analysis/request_engine.csv` —
  `task_id, request_id, instance_id, engine_port, migrated`
- 그 다음 `metrics.csv`와 `task_id`로 조인하면 **엔진별 SLO attainment(%)**를
  정확히 계산할 수 있다.

### D. 마이그레이션 주의

Llumnix는 요청을 인스턴스 간 migrate 한다. dispatch 라인은 **최초 배치**만
말해준다. 따라서 귀속은 "최초 dispatch 엔진"으로 정의하고, migration 로그에
등장한 요청은 `migrated=True`로 표시해 분석에서 구분/제외할 수 있게 한다.

## 검증 계획

단일 워크로드 smoke(searcharena 2 req/s × 2분)로:
- `request_ids.jsonl` 행 수 ≈ 요청 수
- dispatch 로그에서 uuid 매칭률(≥95% 기대; 나머지는 reject/컷)
- 4개 포트에 고르게 분포(로드밸런싱 대칭 — EXP-13의 per-engine 신호와 일치해야)
- 조인 후 엔진별 attainment이 fleet attainment과 정합
