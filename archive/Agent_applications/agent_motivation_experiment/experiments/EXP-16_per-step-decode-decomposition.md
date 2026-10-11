# EXP-16 — Per-step decode-latency decomposition (T_schedule / KV / prefill / batch)

## Why
높은 request rate에서 ITL(decode step time)이 커지는데, 그 원인이
(1) 배치가 쓰는 KV cache 크기, (2) 배치 크기(요청 수), (3) prefill queueing에
의한 prefill interleaving, (4) 스케줄러 CPU 시간(T_schedule) 중 무엇인지를
**한 decode step 단위로 직접 계측**해서 분해한다. 지금까지는 클라이언트측
inter-token gap(`tbt_events.jsonl`)만 있어 네트워크/SSE 지터가 섞였고,
"prefill blocking vs T_schedule"을 구분할 수 없었다.

목표는 계수 정밀 추정이 아니라 **각 항이 실제로 존재하고 분리 가능한지(=모델링
가능성)** 를 판정하는 것.

## Hypothesis
한 decode step 벽시계 시간:

    step_wall ≈ max(T_schedule, T_forward)          # async scheduling: CPU/GPU overlap
    T_forward ≈ C0 + a·KV_tokens + b·N_decode + c·P_tokens

- a·KV : memory-bound attention KV read (배치 KV 총토큰에 비례) — 주 결정항 예상
- b·N_decode : decode compute (배치 요청 수) — ≈ 0 예상
- c·P_tokens : chunked prefill FLOPs (그 step에 얹힌 prefill 토큰) — tail 유발
- T_schedule : 수 ms 상수 예상(수백 ms blocking의 원인이 될 수 없음)

## Instrumentation (신규)
`patches/vllm-sched/llumnix_sched.py :: InstrumentedScheduler(AsyncScheduler)`
- **stock FIFO(fcfs) 순서 그대로 + step마다 로깅** (정책이 아니라 계측기).
  순서 로직 0 → 엔진 physics만 격리. vLLM 소스 수정 없음(`--scheduler-cls` 훅).
- hot path는 `deque.append` 하나, 데몬 스레드가 1초마다 flush → 스케줄링 블로킹 0.
- `SCHED_STEP_LOG` env로 on/off (미설정 시 순수 AsyncScheduler, 오버헤드 0).
- per-step JSONL row:
  `step, t_wall, t_schedule_us(=schedule() 본문시간=T_schedule 직접값),
   interval_ms(=enter-to-enter 간격=엔진측 ground-truth step ITL, 지터 없음),
   kv_tokens(Σ running.num_computed_tokens), n_running, n_waiting,
   n_decode, n_prefill_reqs, prefill_tokens_step(=P_tokens), total_sched_tokens`
- 배선: `k8s/exp07/set_engine_sched.py --policy instrumented --step-log <path>`
  (쓰기 가능한 hostPath `/opt/llumnix-sched-out` → 컨테이너 `/sched-out`; 컨테이너
  root라 DirectoryOrCreate로 충분). 4개 엔진이 한 파일에 append하지만 라인<4KB라
  O_APPEND 원자성으로 JSON 깨짐 0%.

## Exact settings
- 엔진: Llama-3.1-70B-Instruct, TP2 × 4 engines, max-model-len 40960, async-scheduling,
  chunked prefill(max_num_batched_tokens=8192), pool≈584928 tok/engine.
- 스케줄러: InstrumentedScheduler(FIFO), **migration OFF**, admission OFF.
- workload: `mixed_request_level_poisson` mix A(1:1:1 chat:deepresearch:swe),
  5분 load, warmup 60s, grace 60s. per-class SLO budget는 스트림에 실리지만
  FIFO는 무시(byte-identical 스트림).
- 조건마다 **엔진 cold-restart**. 분석 window = arrival-aligned [t0+60, t1−30].

## Phase 1 — 단일 30 req/s (rpm 1800)  ✅ 완료
run: `results/260723_0601_exp16_instr_rpm_1800_rpm_1800`
분석: `analysis_scripts/request_level/plot_exp16_perstep.py` → `figs/exp16/`
window 8032 steps, interval p50=95.8 / p90=435.9 / p99=731.4 ms.

결과:
1. **T_schedule = 죽음**: p50=1.8ms, p99=6.6ms = step p99의 **0.9%**. 큐 깊이
   0→600 내내 ~2ms 평평(median flat). → ④(큐질량 CPU 오버헤드)로 ITL 상승을
   설명하던 이전 가설 **확정 기각**. 수백 ms blocking은 CPU가 아님.
2. **KV 항 존재(decode-only steps, prefill 혼입 제거)**: interval p50이 KV band
   따라 단조 상승 — 37.5(<1.5Mtok) → 52.9 → 75.4 → 96.1 → 105.4(>4.5Mtok) ms.
   → "빠른 모드(37ms)"의 정체는 **낮은 KV step**. (분리축은 prefill 유무가 아니라 KV.)
3. **Prefill 항, full chunk에서만 크게 물림(비선형)**: prefill≥7500 tok step의
   interval p90=680ms(baseline~95ms 대비 +585ms). 작은 interleave(1–500 tok)는
   p50=89ms로 거의 안 더함. → bimodal의 600–700ms 꼬리 = full prefill chunk step.
4. **batch count 독립기여 ≈ 0**: 회귀 b=−0.13 ms/req.
5. **이 한 점은 포화**(KV p50=3.95 / p90=4.70 Mtok) → KV·batch 공선(VIF 10.9),
   회귀 R²=0.083. **항은 다 보이나 계수는 안 갈림.**

판정: `step ≈ C0 + a·KV(+ c·prefill_chunk)` 구조 **검증**, T_schedule 무시가능.
단일 포화점에선 계수 미식별 → **rate sweep(decoupling)** 필요.

## Phase 2 — rate sweep 6/10/13/18/22/(30)/40/50 req/s  ✅ 완료
러너: `k8s/exp07/run_exp16_sweep.sh`. 분석: `plot_exp16_sweep.py` → `figs/exp16_sweep/`.
runs: `results/*exp16_instr_rpm_{360,600,780,1080,1320,(1800),2400,3000}_rpm_*`
(Phase1의 30 req/s=rpm1800도 glob에 포함되어 총 8점). 각 5분, ~150k steps.

**kv_tokens 보정(중요)**: `kv_tokens` = **running-set context mass** (Σ running
context length). resident KV pool(~0.585M tok/engine)까지는 실제 resident KV와
같지만, 그 이상에선 vLLM이 admit 후 preempt(KV free, self.running엔 잔류)하므로
**preempted 요청 context까지 합산해 과대계상**. preempt 시작 ≈ rate 10–13
(kv_tokens가 0.585M 초과). n_waiting은 전 rate p50=0 — 대기는 waiting 큐가 아니라
running 안 preemption 형태(admission gate off).

결과:
1. **KV collapse (핵심)**: decode-only(prefill=0) step interval-vs-kv_tokens 곡선이
   **6→50 req/s 8개 rate 전부 한 선에 붕괴** — kv_tokens 0.2→~3.5Mtok에서 20→~90ms
   단조. **step 시간은 batch 상태(KV mass)의 함수이고 offered-rate와 무관.** fast
   mode = 낮은 KV step일 뿐, 별도 rate 효과 없음.
   - *caveat*: 이 워크로드는 median ctx/req≈9.7k로 거의 일정 → kv_tokens ∝ n_running.
     따라서 이 sweep은 rate-invariance/collapse는 증명하나 **KV-read vs batch-count를
     분리하진 못함**(둘이 비례). KV-not-count는 [[ANALYSIS_why-not-full-kv]]의
     cross-workload(chat 짧음 vs swe 긺, KV축 collapse/count축 7.5× 분리)가 이미 확립.
2. **T_schedule 전 rate 무시가능**: p50 0.1→2.3ms, p99 0.5→7.7ms (rate 6→50). 50
   req/s 깊은 포화에서도 step ITL의 1% 미만. → ④ 확정 기각(rate 전 구간).
3. **Prefill = 포화 조건부 (원인 = prefix cache eviction)**: full-chunk step은 rate
   무관하게 매번 정확히 8192 tok 스케줄(=max_num_batched_tokens 상한, 명목 작업량 일정)
   인데 interval p50이 21ms(6req/s)→607ms(50req/s)로 **29× 변동**. 원인: 저부하엔
   프롬프트 프리픽스가 KV에 캐시로 남아 대부분 cache HIT(runner가 계산 skip)→~20–44ms;
   포화엔 KV pool 압박으로 캐시된 프리픽스가 evict→cache MISS→8192 전체 재계산→~600ms
   (8192×70B TP2 FLOP과 일치). 근거: sched_tok=8192 고정, corr(prefill_tok,interval)≈0,
   sum(interval)≈span(smear 아님), decode 붕괴 유지. **밀려나는 건 decode 요청이 아니라
   prefix 캐시.** (이전 "큰 decode 배치 타고 evict" 서술은 오류 — 40/50의 full-chunk
   step은 kv 0.32M·n_decode 37로 오히려 작았음.) cache-hit 필드 미계측이라 강한 추론.
   회귀 VIF=1.0 → prefill 항은 KV/batch와 직교(독립 식별 가능한 유일한 항).
4. **pool 포화 초과 regime(40/50 req/s)**: decode-only step이 KV 곡선 위로 초과 상승
   (250–345ms @ kv~4Mtok) — 단순 KV-read 아닌 **preemption/recompute churn** 영역.
5. **saturation knee ≈ 22–30 req/s** (batch KV가 ~4–5Mtok로 plateau; step ITL p99가
   ~760ms 천장에 도달).

**판정: 모델링 가능.** `step ≈ C0 + a·(KV mass) + c·prefill_chunk·[saturated]
+ (preemption term, pool 초과 시)`, T_schedule은 상수~2ms로 무시. 분리 상태: T_schedule ✓,
prefill ✓(직교), KV-vs-batch-count는 이 워크로드에선 미분리(ctx 일정)→ Phase 3로 확정.
그림: `figs/exp16_sweep/{exp16_kv_collapse,exp16_vs_rate,exp16_prefill_conditional}.png`.

## Phase 3 — KV-vs-batch-count decoupling  (설계, 미실행)
문제: Phase 2 mix는 요청당 ctx≈9.7k로 거의 일정 → `kv_tokens ∝ n_decode`라 회귀가
a(KV read)와 b(batch count)를 못 가름(pooled VIF 46). 해법 = 배치의 (KV, count)
평면을 **off-diagonal로 채우기**: ctx 길이가 크게 다른 요청을 섞되 배치 조성을 통제.

**옵션 A — reuse, 가벼움 (권장 1차): 워크로드-가변 instrumented sweep**
- 같은 `InstrumentedScheduler`로 **단일 워크로드**를 따로 sweep:
  (i) short-ctx = `sharegpt_request_level_poisson`(짧은 chat turn, ctx~1k),
  (ii) long-ctx = `searcharena_...`(ctx~4k) 또는 `swe`(ctx~10k). 각 3–4 rate.
- per-step decode-only 데이터를 **전부 pool** 후 `interval ~ a·kv_tokens + b·n_decode`.
- 원리: short-run step은 (kv, count)이 완만한 기울기(kv/count≈1k), long-run step은
  가파른 기울기(≈10k) → pooled scatter가 2D를 채워 공선 해소 → a,b 식별. 예측 b≈0.
- 위험: long 요청이 steady-state 배치를 지배(짧은 건 256 step, 긴 건 8k step 잔류)해
  각 run 내부에선 여전히 ctx가 한쪽으로 쏠릴 수 있음 → VIF가 충분히 안 떨어지면 옵션 B.

**옵션 B — gold standard, 신규 코드: closed-loop 2-class concurrency grid**
- 정확히 `n_S`개 short + `n_L`개 long 요청을 **폐루프로 유지**(완료 시 같은 class 재투입)
  하는 소형 워크로드. 배치 조성을 직접 고정 → (count=n_S+n_L, KV≈n_S·L_s+n_L·L_L)을
  격자로 스캔.
- 요청 shape: Short = prompt≈1k / max_tokens 256, Long = prompt≈16k / max_tokens 256
  (긴 prompt로 KV 확보, gen은 짧게). prompt는 고정 코퍼스를 정확 길이로 절단, prefix
  cache 영향 배제 위해 요청마다 unique prefix.
- grid: n_S∈{0,96,192,288} × n_L∈{0,12,24,48}, (0,0) 제외 ≈15 cell. cell당 3–4분
  steady, instrumented, migration off, admission off, cell 간 drain/cold-restart.
- 회귀: 전 cell의 decode-only(prefill=0) step을 pool → `interval ~ a·kv + b·count`.
  격자가 off-diagonal이라 VIF 낮음 → a,b 확정. 예측 b≈0, a>0(≈20–30 ms/Mtok).
- 부수 수확: 긴 prompt cell에서 full-chunk prefill cost도 (KV 포화도별로) 정밀 측정.

권장: 먼저 A(반나절, 코드 0) → VIF 안 떨어지면 B(폐루프 클라이언트 신규).

### Phase 3 결과 (옵션 A 완료)
runs: `results/*exp16dec_{chat_rpm_1800,chat_rpm_3600,swe_rpm_360,swe_rpm_780,swe_rpm_1320}_*`
분석: `plot_exp16_decouple.py` → `figs/exp16_decouple/`.
- 단일 워크로드 내부는 여전히 KV∝count(chat VIF 22, swe VIF 247)로 계수 못 믿음.
- **pooled(chat+swe) VIF 46→1.3** → 분리 성공: **a(KV)=29.9 ms/Mtok, b(count)=0.055 ms/req.**
- 포화 배치(KV~5Mtok,count~600)에서 KV항~150ms vs count항~33ms → **KV 주항(~4.5×), count는
  작지만 0 아님** ("b≈0" 이전 진술 교정). pooled R²=0.36(선형모델 한계) — 계수는 방향성.

### Saturation 메커니즘 (vLLM 카운터로 확증) — 결과 3/4의 실체
`plot_exp16_saturation_mechanism.py` / `plot_exp16_three_factors.py` → `figs/exp16_sweep/`.
KV 54%→78%(rate 22→30)에서 동시에: **prefix cache hit 90→60%(evict)** + **preemption 0→4/s**.
요청당 prefill 시간(vLLM `request_prefill_time`) 116→829ms. 이 둘이 결과 3의 "포화 조건부
prefill 폭발"의 실제 원인 (내 초기 "큰 decode 배치 타고" 서술은 오류 — 40/50의 full-chunk
step은 preempt로 KV 비워져 batch가 오히려 작았음).

**preemption 정의 (vLLM `v1/core/sched/scheduler.py` 소스):** decode 스텝마다 running 각
요청에 다음 토큰용 KV 블록 할당 시도 → `kv_cache_manager.allocate_slots`가 None(pool 꽉 참)
이면 희생 요청 선택(FIFO=`self.running.pop()` 마지막) → `_preempt_request`: `kv_cache_manager
.free(req)` + `num_computed_tokens=0` + waiting 큐 맨 앞으로. 재개 시 프롬프트+생성분 전체
recompute. 트리거는 prefill이 아니라 **KV 고갈**. 카운터 `vllm:num_preemptions_total`.
