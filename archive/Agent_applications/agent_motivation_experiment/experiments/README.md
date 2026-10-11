# Experiment Log — Llumnix migration / throughput / goodput

This folder is the **running record** of every experiment driven against the
Llumnix serving stack (NXC13 k3s). Each experiment has its own `EXP-NN_*.md`
capturing **why** it was run, **what we expected to see**, the **exact settings**,
**how** it was executed, and the **observed result**. Write the file *before/while*
running, not after, so the intent is on record.

Result artifacts (metrics.csv, server_metrics/, analysis CSVs) live under
`../results/<run>/`; these MD files hold the human-readable rationale + findings
and point at the relevant run dirs.

## ⚠️ Ground rule: every condition restarts the whole engine

**Each experimental condition MUST cold-restart the entire serving stack before
it runs** (engine pod `neutral-0` deleted → LWS recreates; scheduler + gateway
rollout-restarted). This guarantees every condition starts from an identical cold
state — empty KV/prefix cache, zeroed Prometheus counters — so results are
**completely independent**. Concretely: in a λ-sweep, **every λ gets its own fresh
restart** (not just once at the start).

- This is enforced by `--restart-per-condition` (env `RESTART_PER_CONDITION=1` in
  `k8s/runner-job.yaml`). **All experiments here run with it ON.** ~150s restart
  cost per condition is accepted as the price of independence.
- Never reuse a warm stack across conditions for a recorded experiment.

## Standard setup (unless an experiment says otherwise)

- **Runner**: in-cluster pod (`k8s/runner-job.yaml`), reaches gateway/scheduler/
  engines via k8s DNS (no port-forward bottleneck). Launch:
  `kubectl -n llumnix delete job bench-runner --ignore-not-found && kubectl apply -f k8s/runner-job.yaml`.
- **Serving**: neutral, 4 instances × TP=2. Model history: 8B (EXP-00~02) →
  Meta-Llama-3-70B (EXP-03~05) → Llama-3.1-70B-Instruct / max-model-len 40960
  (EXP-06+). Migration `backend:"migration"`, `--disable-custom-all-reduce`
  (실측상 부하 중 실질 KV 전송 ≈ 0 — EXP-07 §3 참고).
- **Default dispatch policy**: `load-balance` (scheduler). Some experiments change
  it to `flood`/`round-robin` via a scheduler config edit (documented per-exp).
- **Rescheduling (migration)**: `neutral_load,neutral_failover`, colocated loop
  500ms, neutral-load-threshold 0.003, load-balance-threshold 0.1 (deployment
  values, deliberately low so migration is observable).

## Cross-experiment analysis standard (2026-07-16)

- **모든 실험은 단일 모델(Llama-3.1-70B)**: EXP-04/05는 구 3-70B 시절이라
  legacy — chat 참조는 EXP-12(+EXP-11 재절단)가 정본.
- **표준 분석 창 = 도착-앵커 [60s, 340s]** (warmup 60s 제외 + 본 300s − 끝
  20s). 더 긴 run은 `plot_slo_vs_throughput.py --steady-anchor arrival
  --steady-max-s 360`으로 재절단해 비교한다. 과부하엔 정상상태가 없어 창
  길이가 다르면 감쇠 궤적의 다른 지점을 평균하게 되기 때문.

## Metrics & analysis

- Per-request: `results/<run>/metrics.csv` (TTFT, TBT, e2e latency, tokens, success).
- Server-side time series: `results/<run>/server_metrics/*.jsonl` →
  `analysis_scripts/parse_llumnix_metrics.py` → `analysis/llumnix_server_metrics{,_summary}.csv`.
- Application throughput/latency: `analysis_scripts/request_level/parse_request_summary.py`.
- Cross-λ: `analysis_scripts/request_level/summarize_lambda_sweep.py`.
- Migration ground truth: `results/<run>/server_metrics/migration_events.log` +
  `scheduler_rescheduling_total`. (Note: the counter is only exposed once a
  rescheduling has actually occurred.)

## Index

> **EXP-05→12 아크 전체 요약(인사이트·방법·세팅·표준)**:
> [SUMMARY_exp05-12_kv-admission.md](SUMMARY_exp05-12_kv-admission.md)

| # | File | Status | One-line |
|---|---|---|---|
| 00 | [EXP-00_migration-mechanism.md](EXP-00_migration-mechanism.md) | done | migration 파이프라인 발화 확인 (실부하 중 실질 전송 ≈ 0) |
| 02 | [EXP-02_throughput-goodput-rate-sweep.md](EXP-02_throughput-goodput-rate-sweep.md) | done | 8B rate sweep + control-plane CPU 병목(500m→8core) 발견 |
| 03 | [EXP-03_70b-kv-saturation.md](EXP-03_70b-kv-saturation.md) | done | 70B 전환으로 KV 포화 실험 가능화 |
| 04 | [EXP-04_8192conc-chat-sweep.md](EXP-04_8192conc-chat-sweep.md) | **legacy(3-70B)** | 8192 동시성 chat sweep — 60→80 req/s 붕괴 절벽, herd 발견 |
| 05 | [EXP-05_warmup-chat-sweep.md](EXP-05_warmup-chat-sweep.md) | **legacy(3-70B)** | warmup ramp + 무제어 chat baseline (KV 수위 곡선, 수조 분석 데이터) |
| 06 | [EXP-06_swe-tool-delay-sweep.md](EXP-06_swe-tool-delay-sweep.md) | done | SWE 체인 무제어 baseline — 2단계 붕괴, job goodput 역행 |
| 07 | [EXP-07_kv-threshold-admission.md](EXP-07_kv-threshold-admission.md) | done | KV 점유율 θ admission (chat, 3 rate × θ) — θ\*=0.6, 예상 밖 근사-최적 |
| 08 | [EXP-08_kv-threshold-full-sweep.md](EXP-08_kv-threshold-full-sweep.md) | done | θ=0.6 chat 전체 sweep — 용량 클램프 min(1, 60/rate) 밀착 |
| 09 | [EXP-09_swe-kv-admission-sweep.md](EXP-09_swe-kv-admission-sweep.md) | done | SWE × 4θ — 전이 성립, chain-kill 4.6×, ITL CDF와 SLO-정의 의존 |
| 10 | [EXP-10_swe-request-level-replay.md](EXP-10_swe-request-level-replay.md) | done | SWE open-loop request-level replay λ sweep (admission 없음) — 되먹임 제거한 순수 붕괴 곡선 |
| 11 | [EXP-11_chat-deep-overload.md](EXP-11_chat-deep-overload.md) | done | chat deep-overload — 기울기 ~3ns/tok 일치로 법칙 워크로드-불변 확인; chat은 질량 축적 20× 느려 붕괴역 도달 불가 |
| 12 | [EXP-12_chat-baseline-31.md](EXP-12_chat-baseline-31.md) | done | chat baseline 3.1-70B 재실험 (exp05 대체; 단일 모델화, 표준 창 [60,340]) |
| 13 | [EXP-13_searcharena-deep-research-sweep.md](EXP-13_searcharena-deep-research-sweep.md) | done | deep-research(중간 길이, 입력 ~4k tok) 무제어 rate sweep — SLO knee 13 req/s·그 위 절벽, throughput은 붕괴 않고 포화-평탄(chat-side); 큐-질량 법칙 3번째 검증 |
| 14 | [EXP-14_workload-mix.md](EXP-14_workload-mix.md) | done | chat+deep-research+SWE mix 3비율 rate sweep — knee가 토큰 질량 따라 이동(C13<A20<B38), 클래스 함께 붕괴(간섭), throughput 붕괴 강도 SWE지분 비례(C−49/A−40/B−20%); per-request→엔진 귀속 신규 |
| 16 | [EXP-16_per-step-decode-decomposition.md](EXP-16_per-step-decode-decomposition.md) | done | decode step 분해(InstrumentedScheduler per-step 계측) — T_schedule 무시가능, KV 주항, prefill=full-chunk tail |
| 21 | [EXP-21_polyserve-routing.md](EXP-21_polyserve-routing.md) | done | **라우팅** 축 실험(엔진 stock FIFO 고정): PolyServe tier 격리 vs load-balance — chat/dr 전 rate 100%, goodput 30req/s에서 FIFO 5.2배·QoServe 4.1배, 엔진 스케줄러 5종은 모두 ~14req/s에서 붕괴하나 PolyServe만 단조증가. 이득 출처는 tier 파티션(admission은 고부하에서 무력화) |

분석 노트: [ANALYSIS_kv-tank-flow.md](ANALYSIS_kv-tank-flow.md) (KV 수조/유량),
[ANALYSIS_why-not-full-kv.md](ANALYSIS_why-not-full-kv.md) (TBT–KV 선형 법칙),
[DEV_multiprocess-load-generator.md](DEV_multiprocess-load-generator.md) (부하기 MP 개조),
[DEV_dynamic-trace-mix.md](DEV_dynamic-trace-mix.md) (1시간 동적 trace: Azure 모양 rate
+ 시간가변 믹스 — 배관 완료, 클러스터 미검증).
