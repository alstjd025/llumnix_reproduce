# archive/ — 2026-10-11 이전 기록의 입구

이 디렉토리는 **2026-10-11 정리 때 작업 트리에서 옮긴 기록 전부**다. 지운 것은 없고 내용은 한 글자도
바꾸지 않았다(예외: 코드 주석 속 경로 문자열 `ms_dev/notes/` → `archive/llumnix_reproduce/ms_dev/notes/`,
이것은 남은 코드 쪽만 바꿨다). 지금 상태는 `project/`에 있고, 여기는 **근거를 확인해야 할 때만** 연다.

## 1. 읽기 전에 알아야 할 것

- **원래 경로를 그대로 보존했다**: `archive/<저장소 이름>/<원래 경로>`.
  - 바깥 저장소 `llumnix_reproduce/...` → `archive/llumnix_reproduce/...`
  - 안쪽 저장소 `Agent_applications/...` → `archive/Agent_applications/...`
  - 옛 문서의 링크 `ms_dev/notes/STATUS.md`는 `archive/llumnix_reproduce/ms_dev/notes/STATUS.md`다.
- **옮긴 파일 목록**: `MOVED_2026-10-11_llumnix_reproduce.tsv`(79개, git rename),
  `MOVED_2026-10-11_Agent_applications.tsv`(1,036개, md5 포함 — 안쪽 저장소에서는 `git rm`했고 그
  저장소의 git 이력에는 그대로 남아 있다).
- **여기 있는 수치는 그 날짜의 사실이다.** 나중에 정정된 값이 섞여 있다. 정정 목록은
  `llumnix_reproduce/ms_dev/notes/retracted.tsv`이고, 지금 쓰는 값은 `project/evidence.md`에 있다.
  특히 `fluidserve-implementation-facts.md` §8의 결정 비용 0.013 ms는 인용 금지다(EXP-140의 정정값 192 µs).
- 저장소 루트의 `.ignore`가 이 디렉토리를 기본 검색에서 뺀다. 여기서 찾을 때는
  `rg --no-ignore-dot <패턴> archive/` 또는 `grep -rn <패턴> archive/`.

## 2. 큰 문서 — 무엇이 어디 있나

| 문서 (`llumnix_reproduce/ms_dev/notes/` 아래) | 내용 |
|---|---|
| `STATUS.md` | 상태 문서(2,862줄). 맨 위가 제출 시점(2026-09-23), 그 아래가 날짜별 이전 상태. §3이 implementation.md 절별 요약 |
| `fluidserve-v0.4.md` | 마지막 버전 명세(2026-08-28). 채택 결정 셋과 **컴파일 기본값이 채택 구성과 다르다**는 §2 |
| `fluidserve-system-design.md` | 논문 design 절용 완전 참조(코드 실독 기준, 2026-08-28) |
| `fluidserve-implementation-facts.md` | 논문 implementation 절용 사실 모음(2026-09-09 작성, 09-23 갱신). §8 결정 비용은 낡았다 |
| `fluidserve-v0.1.md` ~ `v0.3.md`, `fluidserve-design.md`, `fluidserve-how-it-works.md` | 앞선 명세들과 설계 원안. 결정 규칙·상수 아홉 개의 원 정본은 v0.1 |
| `fluidserve-implementation.md` | 시간순 구현 기록 §1~§66(7,227줄, 2026-08-09에 멈춤) |
| `motivation.md`, `motivation_v2.md`, `motivation_v3.md`, `paper-outline.md` | motivation 논증과 논문 구도(2026-08-13~16). 제출본과 다를 수 있다 |
| `related-works-review.md`, `slosserve-comparison.md`, `why-the-routing-layer.md`, `related-works-writing-style.md` | 관련 연구 검토 |
| `polyserve-fidelity.md`, `llmd-baseline.md`, `vllm-router-baseline.md`, `qoserve-niyama-fidelity.md` | 기준선 이식의 원문 대조와 계획 — **리뷰어가 기준선 공정성을 물으면 여기가 근거** |
| `tail-latency.md`, `tail-plan.md`, `tail-and-tradeoff-answers.md` | tail latency 반론에 대한 답 |
| `class-instance-cap-design.md`, `prefill-budget-design.md`, `fluidserve-prefix.md`, `swe-rejection.md` | 개별 메커니즘 설계·분석 |
| `numbers.tsv`, `retracted.tsv` | 문서 간 수치 대조표와 정정 목록(생성·검사 스크립트는 `ms_dev/scripts/collect_numbers.py`, `check_numbers.py`) |
| `paper-draft-sec2-3.tex` | 2026-08-10의 §2·§3 초고(형식 참고용) |
| 6월 셋업 기록 (`llumnix-k3s-setup-notes.md`, `nxc7-*.md`, `llumnix-*.md`) | k3s를 Docker 컨테이너 안에서 세울 때의 우회 일곱 가지. **재구축 스크립트 `ms_dev/scripts/00~02`가 이것을 코드로 담고 있다** |

| 그 밖 | 내용 |
|---|---|
| `llumnix_reproduce/ms_dev/scripts/clean_backup_scripts/` | **결과 데이터가 지금 어디 있는지의 정본**(노드·Lustre·맥 SSD), 백업·삭제·복원 기록 전부, `RESTORE-2026-08-28.md` |
| `llumnix_reproduce/*.md` | PolyServe 이식 설계·진행, Niyama·JITServe 분석(2026-07) |
| `Agent_applications/agent_motivation_experiment/paper_figures/README.md` | **그림 출처의 정본(3,207줄)**: 그림마다 run 집합, n, 채점 규칙, 캡션이 반드시 적어야 할 것, 말하면 안 되는 것. 옛 그림 PDF·CSV 약 400개도 같은 디렉토리 |
| `Agent_applications/agent_motivation_experiment/paper_evaluation_2026-08/` | evaluation 절 계획(2026-08-21~24) |
| `Agent_applications/agent_motivation_experiment/paper_experiment/` | 2026-08 정적 sweep의 run 고정 명세와 `verify.py` |
| `Agent_applications/agent_motivation_experiment/results/aggregate_analysis/` | git이 추적하던 탐색 분석 README와 빌드 스크립트 |
| `Agent_applications/agent_motivation_experiment/{k8s,analysis_scripts,workload_configs}/` | 논문 실험에 쓰이지 않은 드라이버 약 110개, 분석 스크립트 약 160개, 워크로드 설정 15개 |
| `Agent_applications/agent_motivation_experiment/{legacy,claude,figs,admission_control,experiment_insturctions,housekeeping}/`, `CLAUDE.md`, `AGENTS.md`, `README.md` | SGLang·Halo 시절(2026-05~07)의 코드와 문서 |
| `Agent_applications/agent_motivation_experiment/c -l)|` | 2026-08-03에 실수로 커밋된 셸 명령 조각 한 줄. 지우지 않고 옮겼다 |

## 3. 실험 기록 EXP-00 ~ EXP-140

`●` = 제출한 논문의 그림·본문 수치가 그 실험의 run을 쓴다. **EXP-138은 기록 파일이 없다**(드라이버
`k8s/exp07/run_exp138_noaffnocap_hour.sh`와 그림 스크립트에만 나온다). 같은 디렉토리의 `README.md`(07-26에
멈춘 옛 색인), `DEV_*`, `ANALYSIS_*`, `SUMMARY_*`도 여기 있다.

| 실험 | 논문 | 제목 (파일 첫 제목줄) | 상태 줄 (파일에 있으면) |
|---|---|---|---|
| [EXP-00](Agent_applications/agent_motivation_experiment/experiments/EXP-00_migration-mechanism.md) |  | EXP-00 — Migration mechanism verification (no code change) | in progress · **Date**: 2026-07-06 |
| [EXP-02](Agent_applications/agent_motivation_experiment/experiments/EXP-02_throughput-goodput-rate-sweep.md) |  | EXP-02 — Throughput / goodput rate-sweep to fleet overload | in progress · **Date**: 2026-07-06 · **Dispatch**: load-balance (reference) |
| [EXP-03](Agent_applications/agent_motivation_experiment/experiments/EXP-03_70b-kv-saturation.md) |  | EXP-03/04 — 70B switch: reaching real GPU-KV saturation | done · **Date**: 2026-07-08 · **Model**: Meta-Llama-3-70B-Instruct (4×TP2, gpu-mem-util 0. |
| [EXP-04](Agent_applications/agent_motivation_experiment/experiments/EXP-04_8192conc-chat-sweep.md) |  | EXP-04 — chat rate sweep @ client concurrency 8192 (70B) | DONE (2026-07-08) · **Data**: `results/exp04_final_8192conc/` |
| [EXP-05](Agent_applications/agent_motivation_experiment/experiments/EXP-05_warmup-chat-sweep.md) |  | EXP-05 — warmup ramp + chat 11-rate sweep (무제어 chat baseline) | DONE (2026-07-08) · **Data**: `results/*exp05_warmup_rpm_{300..6000}` |
| [EXP-06](Agent_applications/agent_motivation_experiment/experiments/EXP-06_swe-tool-delay-sweep.md) |  | EXP-06 — SWE tool-delay chain sweep (무제어 agent baseline) | DONE (2026-07-09) · **Data**: `results/*exp06_swe_sweep_rpm_{15..300}` |
| [EXP-07](Agent_applications/agent_motivation_experiment/experiments/EXP-07_kv-threshold-admission.md) |  | EXP-07 — KV-occupancy thresholding admission (motivation: 정적 수위 임계의 한계) | DONE (12/12 conditions, 2026-07-14; results §4) · |
| [EXP-08](Agent_applications/agent_motivation_experiment/experiments/EXP-08_kv-threshold-full-sweep.md) |  | EXP-08 — θ=0.6 고정 전체 rate sweep (EXP-05 시나리오 + KV admission) | DONE (11/11, 2026-07-14) · **Follows**: EXP-07 · |
| [EXP-09](Agent_applications/agent_motivation_experiment/experiments/EXP-09_swe-kv-admission-sweep.md) |  | EXP-09 — SWE tool-delay × KV admission θ ∈ {0.3, 0.4, 0.5, 0.6} | DONE (2026-07-15, 4θ × 11 rate = 44조건) · |
| [EXP-10](Agent_applications/agent_motivation_experiment/experiments/EXP-10_swe-request-level-replay.md) |  | EXP-10 — SWE open-loop request-level replay λ sweep (admission 없음) | **날짜**: 2026-07-15 · done · **브랜치**: `feat/exp07-kv-admission` |
| [EXP-11](Agent_applications/agent_motivation_experiment/experiments/EXP-11_chat-deep-overload.md) |  | EXP-11 — chat deep-overload extension (exp05 계승, queue-bound 영역 관측) | **날짜**: 2026-07-16 · running · **브랜치**: `feat/exp07-kv-admission` |
| [EXP-12](Agent_applications/agent_motivation_experiment/experiments/EXP-12_chat-baseline-31.md) |  | EXP-12 — chat 무제어 baseline 재실험 (3.1-70B 단일 모델화) | **날짜**: 2026-07-16 · running · **브랜치**: `feat/exp07-kv-admission` |
| [EXP-13](Agent_applications/agent_motivation_experiment/experiments/EXP-13_searcharena-deep-research-sweep.md) |  | EXP-13 — Search Arena deep-research 워크로드 무제어 rate sweep | **날짜**: 2026-07-20 · running · **브랜치**: `feat/exp07-kv-admission` |
| [EXP-14](Agent_applications/agent_motivation_experiment/experiments/EXP-14_workload-mix.md) |  | EXP-14 — 워크로드 mix rate sweep (chat + deep-research + SWE) | **날짜**: 2026-07-20 · implementing · **브랜치**: `feat/exp07-kv-admission` |
| [EXP-16](Agent_applications/agent_motivation_experiment/experiments/EXP-16_per-step-decode-decomposition.md) |  | EXP-16 — Per-step decode-latency decomposition (T_schedule / KV / prefill / batch) |  |
| [EXP-21](Agent_applications/agent_motivation_experiment/experiments/EXP-21_polyserve-routing.md) |  | EXP-21 — PolyServe routing vs stock Llumnix load-balance (mix A) |  |
| [EXP-22](Agent_applications/agent_motivation_experiment/experiments/EXP-22_fluidserve-routing.md) |  | EXP-22 — FluidServe vs PolyServe on a dynamic trace |  |
| [EXP-23](Agent_applications/agent_motivation_experiment/experiments/EXP-23_admission-rate-sweep.md) |  | EXP-23 — 과부하에서의 admission: FluidServe 대 PolyServe rate sweep | 설계 완료, 실행 대기 (EXP-22 v10 확인 후) |
| [EXP-24](Agent_applications/agent_motivation_experiment/experiments/EXP-24_replicates.md) |  | EXP-24 — 반복 측정: 산포를 먼저 재고 그다음에 비교한다 | 실행 중 (2026-07-27 시작) |
| [EXP-25](Agent_applications/agent_motivation_experiment/experiments/EXP-25_routing-vs-admission.md) |  | EXP-25 — 라우팅과 admission을 분리한다 | 실행 중 (2026-07-27 시작, 약 4시간) |
| [EXP-26](Agent_applications/agent_motivation_experiment/experiments/EXP-26_partition-must-move.md) |  | EXP-26 — 정적 파티션이 못 따라가는 조건을 만든다 | 설계 완료, 실행 대기 (EXP-25 결과 확인 후) |
| [EXP-27](Agent_applications/agent_motivation_experiment/experiments/EXP-27_short-swe-mix-sweep.md) |  | EXP-27 — swe 입력을 줄이고, 믹스를 입력 토큰 기준으로 정의한다 | 실행 중 (2026-07-28 04:40 KST 시작, 약 3.9시간). 세션 접두사 `exp27r1` |
| [EXP-28](Agent_applications/agent_motivation_experiment/experiments/EXP-28_llumnix-slo-baseline.md) |  | EXP-28 — Llumnix 자체 SLO 정책을 baseline으로 | 공정 설정 sweep 실행 중 (2026-07-29 00:38 KST 시작, ~01:30 종료) |
| [EXP-28](Agent_applications/agent_motivation_experiment/experiments/EXP-28_mix-changes-during-the-run.md) |  | EXP-28 — 믹스가 조건 안에서 바뀔 때 정적 파티션이 무엇을 지불하는가 | 설계 완료, trace·설정 생성 완료. **EXP-27 결과를 본 뒤 실행** |
| [EXP-29](Agent_applications/agent_motivation_experiment/experiments/EXP-29_prediction-decomposition.md) |  | EXP-29 — 예측 반복시간이 실측보다 8 ms 높은 지점을 계측으로 특정한다 | 2026-07-29 진행 중 |
| [EXP-30](Agent_applications/agent_motivation_experiment/experiments/EXP-30_dynamic-trace.md) |  | EXP-30 — 부하율과 믹스가 같이 움직이는 워크로드에서의 세 정책 | 2026-07-29 설계 완료, 실행 대기 (EXP-29의 수정이 먼저) |
| [EXP-34](Agent_applications/agent_motivation_experiment/experiments/EXP-34_flux-decision-share.md) |  | EXP-34 — fluidic KV 투영이 결정의 몇 %를 바꾸는가 | 2026-07-29 완료 |
| [EXP-38](Agent_applications/agent_motivation_experiment/experiments/EXP-38_three-policy-longer-output.md) |  | EXP-38 — three policies in one session on the longer-output workload |  |
| [EXP-39](Agent_applications/agent_motivation_experiment/experiments/EXP-39_time-varying-load.md) |  | EXP-39 — time-varying load from a real trace |  |
| [EXP-40](Agent_applications/agent_motivation_experiment/experiments/EXP-40_qoserve-engine-scheduler.md) |  | EXP-40 — does FluidServe's advantage survive a deadline-aware engine? |  |
| [EXP-41](Agent_applications/agent_motivation_experiment/experiments/EXP-41_one-hour-dynamic.md) |  | EXP-41 — the two control planes on an hour of moving load |  |
| [EXP-42](Agent_applications/agent_motivation_experiment/experiments/EXP-42_force-margin.md) |  | EXP-42 — candidate A: the same allowance margin on the forced-placement test |  |
| [EXP-43](Agent_applications/agent_motivation_experiment/experiments/EXP-43_class-harm.md) |  | EXP-43 — candidate E: turn the class term in the damage estimate back on |  |
| [EXP-44](Agent_applications/agent_motivation_experiment/experiments/EXP-44_force-margin-dynamic.md) |  | EXP-44 — candidate A on an hour of moving load |  |
| [EXP-45](Agent_applications/agent_motivation_experiment/experiments/EXP-45_three-arm-dynamic.md) |  | EXP-45 — Llumnix SLO on the `full` hour, and a second repeat of the candidate-A pair |  |
| [EXP-46](Agent_applications/agent_motivation_experiment/experiments/EXP-46_own-budget-gate.md) |  | EXP-46 — candidate C: judge the arriving request against its own budget |  |
| [EXP-47](Agent_applications/agent_motivation_experiment/experiments/EXP-47_placement-probe.md) |  | EXP-47 — does the second placement in one engine step see the first? |  |
| [EXP-48](Agent_applications/agent_motivation_experiment/experiments/EXP-48_length-profile.md) |  | EXP-48 — the deep research length profile was three and a half times short |  |
| [EXP-49](Agent_applications/agent_motivation_experiment/experiments/EXP-49_kv-slope-projection.md) |  | EXP-49 — candidate H2: project from the rate the occupancy is observed to be moving at |  |
| [EXP-50](Agent_applications/agent_motivation_experiment/experiments/EXP-50_own-budget-gate-corrected.md) |  | EXP-50 — candidate C again, on the corrected length profile and with the binding term now known |  |
| [EXP-51](Agent_applications/agent_motivation_experiment/experiments/EXP-51_gate-bistability.md) |  | EXP-51 — 45 req/s is bistable, and the state variable is whether any engine is free of chat |  |
| [EXP-52](Agent_applications/agent_motivation_experiment/experiments/EXP-52_gate-slack.md) |  | EXP-52 — the gate as one axis with a stated default, not two options |  |
| [EXP-53](Agent_applications/agent_motivation_experiment/experiments/EXP-53_four-policy-sweep.md) |  | EXP-53 — four control planes on one static rate sweep |  |
| [EXP-54](Agent_applications/agent_motivation_experiment/experiments/EXP-54_four-policy-hour.md) |  | EXP-54 — the four control planes on the hour-long dynamic trace |  |
| [EXP-55](Agent_applications/agent_motivation_experiment/experiments/EXP-55_single-class-knees.md) |  | EXP-55 — where each class saturates, and what runs out first |  |
| [EXP-56](Agent_applications/agent_motivation_experiment/experiments/EXP-56_affinity-ablation.md) |  | EXP-56 — turning the class preference off: is a fully mixed fleet worse? |  |
| [EXP-57](Agent_applications/agent_motivation_experiment/experiments/EXP-57_polyserve-refit.md) |  | EXP-57 — PolyServe measured again on a length profile that matches the workload | 2026-08-05 01:34 KST에 EXP-56 뒤로 연쇄 예약. 드라이버 |
| [EXP-58](Agent_applications/agent_motivation_experiment/experiments/EXP-58_affinity-weight-axis.md) |  | EXP-58 — the class preference as a degree, so that separation becomes an axis |  |
| [EXP-59](Agent_applications/agent_motivation_experiment/experiments/EXP-59_class-pin-endpoint.md) |  | EXP-59 — pinning classes to instances, inside our own system |  |
| [EXP-60](Agent_applications/agent_motivation_experiment/experiments/EXP-60_pin-on-the-moving-mix.md) |  | EXP-60 — the pinned allocation on the trace whose mix moves |  |
| [EXP-61](Agent_applications/agent_motivation_experiment/experiments/EXP-61_loadbalance-qoserve.md) |  | EXP-61 — a deadline-aware engine scheduler under a router that leaves it work |  |
| [EXP-62](Agent_applications/agent_motivation_experiment/experiments/EXP-62_polyserve-qoserve-three-way.md) |  | EXP-62 — a deadline-aware engine scheduler under a static class partition, and the three-way comparison |  |
| [EXP-63](Agent_applications/agent_motivation_experiment/experiments/EXP-63_profile-sensitivity.md) |  | EXP-63 — how much does the policy lose when its length profile is wrong? |  |
| [EXP-64](Agent_applications/agent_motivation_experiment/experiments/EXP-64_per-request-length-oracle.md) | ● | EXP-64 — what would per-request length knowledge buy? |  |
| [EXP-66](Agent_applications/agent_motivation_experiment/experiments/EXP-66_llm-d-baseline.md) |  | EXP-66 — llm-d predicted-latency를 기준선으로 | 실행 전. 이 파일은 **결과가 나오기 전에** 쓴다. |
| [EXP-67](Agent_applications/agent_motivation_experiment/experiments/EXP-67_prefix-aware-fluidserve.md) |  | EXP-67 — FluidServe가 prefix cache 재사용을 보면 어떻게 되는가 | 구현·단위 테스트 완료(`e594d8a`), **2026-08-08 02:00 KST에 실행을 걸어 둠** |
| [EXP-68](Agent_applications/agent_motivation_experiment/experiments/EXP-68_workload-deduplication.md) |  | EXP-68 — 워커별 프롬프트 중복을 없앤 워크로드에서 다시 잰다 | 워크로드 수정 적용·smoke 검증 완료(`0cef289`). **70 req/s 첫 탐침 실행 중**(§4). |
| [EXP-69](Agent_applications/agent_motivation_experiment/experiments/EXP-69_prefix-control.md) |  | EXP-69 — prefix 인식의 대조군: 새 워크로드에서 그 이득이 얼마인가 | 완료 (2026-08-08 20:19 KST, 6조건). |
| [EXP-70](Agent_applications/agent_motivation_experiment/experiments/EXP-70_knee-probe.md) |  | EXP-70 — 무릎이 어디인가, 그리고 5 baseline sweep의 격자를 무엇으로 할 것인가 | **8조건 전부 완료.** `llm-d @15 req/s`가 엔진 기동 실패로 한 번 빠졌고(§5) |
| [EXP-71](Agent_applications/agent_motivation_experiment/experiments/EXP-71_hour-trace-v02.md) |  | EXP-71 — 한 시간 trace: 네 컨트롤플레인, 무릎을 오르내리는 대역에서 | **완료 (2026-08-09 20:20 KST). 네 arm × 2반복 = 8 run.** |
| [EXP-72](Agent_applications/agent_motivation_experiment/experiments/EXP-72_polyserve-slo-static.md) |  | EXP-72 — PolyServe와 Llumnix SLO의 정적 sweep, 수정 후 워크로드에서 | 완료 (2026-08-10 00:25 KST). **16조건 = 2 arm × 8 rate × 1반복.** |
| [EXP-73](Agent_applications/agent_motivation_experiment/experiments/EXP-73_attribution-ladder.md) |  | EXP-73 — 보유(PEND)와 클래스 선호가 각각 얼마인가, 그리고 둘이 서로 돕는가 | **2026-08-09 22:07 KST에 걸었다. EXP-72 종료를 프로세스 생사로 기다리는 중.** |
| [EXP-74](Agent_applications/agent_motivation_experiment/experiments/EXP-74_swe-budget-symmetry.md) |  | EXP-74 — swe 열의 차이가 정책인가 예산 설정인가 | EXP-73 종료를 기다리는 체인에 걸어 뒀다(2026-08-10 02:15 KST 작성). |
| [EXP-75](Agent_applications/agent_motivation_experiment/experiments/EXP-75_mix-ratio-scout.md) |  | EXP-75 — 우위가 chat 비중의 함수인가 (정찰 pass) | EXP-74 뒤에 이어 걸어 뒀다(2026-08-10 02:15 KST 작성). |
| [EXP-77](Agent_applications/agent_motivation_experiment/experiments/EXP-77_vllm-router-cache-aware.md) | ● | EXP-77 — vLLM router의 기본 정책 `cache_aware`를 다섯 번째 기준선으로 세운다 |  |
| [EXP-78](Agent_applications/agent_motivation_experiment/experiments/EXP-78_admission-axis.md) |  | EXP-78 — admission을 끄면 얼마나 잃는가, 그리고 라우팅만으로는 어디까지 가는가 |  |
| [EXP-79](Agent_applications/agent_motivation_experiment/experiments/EXP-79_independent-combination.md) |  | EXP-79 — 거절과 배치가 같은 양을 써야 하는가 (독립 결합) |  |
| [EXP-80](Agent_applications/agent_motivation_experiment/experiments/EXP-80_static-sweep-repeat2.md) |  | EXP-80 — 정적 sweep 두 번째 반복 (llm-d · PolyServe · Llumnix SLO) | 실행 전 작성 (2026-08-13 15:30 KST). 결과는 §6. |
| [EXP-81](Agent_applications/agent_motivation_experiment/experiments/EXP-81_vllmrouter-qoserve.md) |  | EXP-81 — 배포된 라우터에 deadline 인식 엔진 스케줄러를 붙이면 얼마나 따라오는가 | 실행 전 작성 (2026-08-14). 결과는 §6. |
| [EXP-82](Agent_applications/agent_motivation_experiment/experiments/EXP-82_gateway-throttle.md) |  | EXP-82 — 게이트웨이의 CPU 정지를 없애고 정적 sweep을 다시 잰다 |  |
| [EXP-83](Agent_applications/agent_motivation_experiment/experiments/EXP-83_hold-window-control.md) |  | EXP-83 — FluidServe를 게이트웨이 출고 기본 창으로 돌린다 |  |
| [EXP-84](Agent_applications/agent_motivation_experiment/experiments/EXP-84_gateway-two-by-two.md) |  | EXP-84 — 게이트웨이 대기 창의 두 값을 하나씩 갈라 잰다 |  |
| [EXP-85](Agent_applications/agent_motivation_experiment/experiments/EXP-85_fluidserve-qoserve-unit4.md) |  | EXP-85 — FluidServe에 QoServe unit 4를 얹으면 요청 안 p90이 예산에 들어오는가 |  |
| [EXP-86](Agent_applications/agent_motivation_experiment/experiments/EXP-86_gateway-throttle-clean-baselines.md) |  | EXP-86 — 기준선 셋을 게이트웨이 CPU 정지 오염 없이 다시 잰다 |  |
| [EXP-87](Agent_applications/agent_motivation_experiment/experiments/EXP-87_deadline-in-feasibility.md) |  | EXP-87 — 첫 토큰 마감을 feasibility에 넣으면 무엇이 달라지는가 |  |
| [EXP-88](Agent_applications/agent_motivation_experiment/experiments/EXP-88_routing-only-full-grid.md) |  | EXP-88 — 아무도 거절하지 않을 때, 목적지를 고르는 규칙만으로 얼마나 갈라지는가 |  |
| [EXP-89](Agent_applications/agent_motivation_experiment/experiments/EXP-89_llmd-third-repeat.md) |  | EXP-89 — llm-d 기준선의 저부하 네 점에 반복을 하나 더한다 |  |
| [EXP-90](Agent_applications/agent_motivation_experiment/experiments/EXP-90_profile-robustness.md) |  | EXP-90 — 길이 프로파일의 robustness: 트래픽이 얼마나 있어야 하고, 낡으면 무슨 일이 나는가 |  |
| [EXP-91](Agent_applications/agent_motivation_experiment/experiments/EXP-91_affinity-on-the-hour.md) |  | EXP-91 — 클래스 선호(affinity)는 믹스가 움직이는 한 시간 trace에서 이득인가 손해인가 |  |
| [EXP-92](Agent_applications/agent_motivation_experiment/experiments/EXP-92_affinity-on-the-even-mix.md) |  | EXP-92 — 클래스 선호의 효과는 믹스 수준의 함수인가 |  |
| [EXP-93](Agent_applications/agent_motivation_experiment/experiments/EXP-93_mix-shift-stress.md) |  | EXP-93 — 믹스가 균등 구간을 통과하는 stress trace: 믹스 이동은 시스템이 고르지 않은 용량 변화다 |  |
| [EXP-94](Agent_applications/agent_motivation_experiment/experiments/EXP-94_affinity-weight-sweep.md) |  | EXP-94 — 클래스 선호의 세기 축: 이득이 있는 구간과 그것을 파괴하는 것 |  |
| [EXP-95](Agent_applications/agent_motivation_experiment/experiments/EXP-95_pin-ladder.md) |  | EXP-95 — 클래스 선호가 푸는 엔진이 한 대뿐인 것이 한계인가 |  |
| [EXP-96](Agent_applications/agent_motivation_experiment/experiments/EXP-96_affinity-normalisation.md) |  | EXP-96 — 클래스 선호의 정규화를 고치고, 제대로 작동할 때 이득이 있는지 본다 |  |
| [EXP-97](Agent_applications/agent_motivation_experiment/experiments/EXP-97_normalisation-on-the-hour.md) |  | EXP-97 — 정규화를 고친 클래스 선호를 한 시간 믹스 이동 trace에서 잰다 | 끝남 (2026-08-25 04:10 KST, 4/4). 배포 바이너리 `c40896c` 판. |
| [EXP-98](Agent_applications/agent_motivation_experiment/experiments/EXP-98_per-instance-correction-and-pace-cap.md) |  | EXP-98 — 보정 계수를 인스턴스별로, 그리고 메모리 판정이 속도 상한을 읽게 | 실행 전에 이 문서를 쓴다. 배포 바이너리 `3022e001`(파드에서 `md5sum /proc/1/exe`로 확인), |
| [EXP-100](Agent_applications/agent_motivation_experiment/experiments/EXP-100_first-token-deadline-retry.md) |  | EXP-100 — 첫 토큰 마감을 feasibility 판정에 넣는다 (EXP-87 재시도) | 실행 전에 이 문서를 쓴다. EXP-99 뒤에 이어 걸었다. 바이너리 `3022e001` 그대로이고 |
| [EXP-101](Agent_applications/agent_motivation_experiment/experiments/EXP-101_placement-delay-in-the-deadline-test.md) |  | EXP-101 — 첫토큰 마감 판정이 읽는 추정값이 늦어질 요청을 알아볼 수 있는가 |  |
| [EXP-103](Agent_applications/agent_motivation_experiment/experiments/EXP-103_interleave-aware-first-token-estimate.md) |  | EXP-103 — 첫토큰 추정값에 빠져 있던 항을 넣는다: 큐는 prefill step 비용이 아니라 엔진이 prefill에 내주는 속도로 빠진다 |  |
| [EXP-104](Agent_applications/agent_motivation_experiment/experiments/EXP-104_v03-class-preference.md) |  | EXP-104 — FluidServe v0.3에서 클래스 선호를 켜고 끈다 |  |
| [EXP-105](Agent_applications/agent_motivation_experiment/experiments/EXP-105_swe-budget-40s.md) |  | EXP-105 — swe의 E2E 예산을 30초에서 40초로 올리면 클래스 선호의 효과가 달라지는가 |  |
| [EXP-106](Agent_applications/agent_motivation_experiment/experiments/EXP-106_polyserve-paper-fidelity.md) |  | EXP-106 — PolyServe 기준선에 논문의 메커니즘을 되살린다 | 구현 완료, **아직 걸지 않았다** (2026-08-27 16:0x KST 기준 EXP-105가 도는 중) |
| [EXP-107](Agent_applications/agent_motivation_experiment/experiments/EXP-107_class-instance-cap.md) |  | EXP-107 — class-instance cap과 force 분기 제거, mix-shift 한 시간 trace |  |
| [EXP-108](Agent_applications/agent_motivation_experiment/experiments/EXP-108_per-token-swe-rebaseline.md) | ● | EXP-108 — 정적 rate sweep을 swe의 per-token 예산 형태로 다시 잰다 | 2026-08-31 09:02 KST 시작, 도는 중 (64조건, 약 19시간 예상) |
| [EXP-109](Agent_applications/agent_motivation_experiment/experiments/EXP-109_per-token-swe-hour-trace.md) | ● | EXP-109 — 한 시간 mix-shift trace를 swe의 per-token 예산 형태로 다시 잰다 | 2026-09-02 14:58 KST 완료 (10/10). 다섯 arm × 2반복. |
| [EXP-110](Agent_applications/agent_motivation_experiment/experiments/EXP-110_llmd-lowrate-split.md) | ● | EXP-110 — llm-d 가 10 req/s 에서 두 갈래로 갈리는 것의 원인 |  |
| [EXP-111](Agent_applications/agent_motivation_experiment/experiments/EXP-111_qwen25-72b-profile.md) |  | EXP-111 — Qwen2.5-72B-Instruct 로 모델을 바꾸고 오프라인 프로파일을 다시 재다 |  |
| [EXP-112](Agent_applications/agent_motivation_experiment/experiments/EXP-112_qwen-knee.md) |  | EXP-112 — Qwen2.5-72B 에서 FluidServe 의 용량 무릎을 실측 |  |
| [EXP-113](Agent_applications/agent_motivation_experiment/experiments/EXP-113_qwen-hour-five-arms.md) | ● | EXP-113 — 다섯 컨트롤플레인, Qwen2.5-72B, 한 시간 mix-shift trace |  |
| [EXP-114](Agent_applications/agent_motivation_experiment/experiments/EXP-114_llama8b-8instances-PLAN.md) |  | EXP-114 (계획) — 8 인스턴스 × TP=1, Llama-3.1-8B-Instruct |  |
| [EXP-114](Agent_applications/agent_motivation_experiment/experiments/EXP-114_llama8b-8instances.md) |  | EXP-114 — 여덟 인스턴스(TP=1)에서의 Llama-3.1-8B |  |
| [EXP-116](Agent_applications/agent_motivation_experiment/experiments/EXP-116_eight-instance-static-sweep.md) |  | EXP-116 — the eight-instance static rate sweep that has never been run |  |
| [EXP-117](Agent_applications/agent_motivation_experiment/experiments/EXP-117_overnight-findings.md) |  | EXP-117 — 2026-09-10 밤의 다각도 조사 |  |
| [EXP-118](Agent_applications/agent_motivation_experiment/experiments/EXP-118_first-token-estimate-corrections.md) |  | EXP-118 — 첫토큰 예측의 두 수정 |  |
| [EXP-120](Agent_applications/agent_motivation_experiment/experiments/EXP-120_memory-brake.md) |  | EXP-120 — 고친 첫토큰 예측 + 메모리 제동 |  |
| [EXP-121](Agent_applications/agent_motivation_experiment/experiments/EXP-121_halved-per-token-budgets.md) |  | EXP-121 — 토큰당 예산을 절반으로 낮춰 구속하는 천장을 바꾼다 |  |
| [EXP-122](Agent_applications/agent_motivation_experiment/experiments/EXP-122_kv-arrivals-term-DESIGN.md) |  | EXP-122 (설계 노트) — KV 투영의 도착 항, per-instance 도착률로 |  |
| [EXP-123](Agent_applications/agent_motivation_experiment/experiments/EXP-123_halved-first-token-budgets.md) |  | EXP-123 — 첫토큰 예산도 절반으로 낮춰, EXP-121과 짝을 이뤄 두 축을 분리한다 |  |
| [EXP-124](Agent_applications/agent_motivation_experiment/experiments/EXP-124_kv-arrivals-term.md) |  | EXP-124 — KV 투영의 네 번째 항(도착 항)을 켠다, 창 길이 둘과 메모리 천장 둘 |  |
| [EXP-125](Agent_applications/agent_motivation_experiment/experiments/EXP-125_arrivals-term-hour-trace.md) | ● | EXP-125 — KV 투영의 도착 항을 한 시간 mix-shift trace에서 잰다 (N=1) | 준비 완료, 미실행. |
| [EXP-126](Agent_applications/agent_motivation_experiment/experiments/EXP-126_halved-slo-hour-trace.md) | ● | EXP-126 — the one-hour trace with both SLO halves, arrivals term on and off, against PolyServe |  |
| [EXP-127](Agent_applications/agent_motivation_experiment/experiments/EXP-127_polyserve-memory-accounting.md) |  | EXP-127 — does PolyServe's memory test refuse while the pool is half empty? |  |
| [EXP-128](Agent_applications/agent_motivation_experiment/experiments/EXP-128_vllm-router-halved-hour.md) |  | EXP-128 — the vLLM router at the halved budgets, and how an arm that never refuses is read |  |
| [EXP-129](Agent_applications/agent_motivation_experiment/experiments/EXP-129_capacity-model-diagnosis.md) |  | EXP-129 — Qwen2.5-14B 8인스턴스 함대의 decode step 법칙이 왜 맞지 않는가 |  |
| [EXP-129](Agent_applications/agent_motivation_experiment/experiments/EXP-129_qwen25-14b-8instances.md) |  | EXP-129 — Qwen2.5-14B-Instruct on eight instances: the fourth and last model |  |
| [EXP-130](Agent_applications/agent_motivation_experiment/experiments/EXP-130_small-model-screen.md) |  | EXP-130 — a twenty-minute screen for whether an engine can be profiled at all |  |
| [EXP-131](Agent_applications/agent_motivation_experiment/experiments/EXP-131_leave-one-out-ablation.md) | ● | EXP-131 — 채택 구성에서 메커니즘을 하나씩 뺀다 (leave-one-out) |  |
| [EXP-132](Agent_applications/agent_motivation_experiment/experiments/EXP-132_fluidserve-variations-hour.md) | ● | EXP-132 — 한 시간 trace 위의 FluidServe 변형 셋 |  |
| [EXP-133](Agent_applications/agent_motivation_experiment/experiments/EXP-133_cumulative-ablation.md) |  | EXP-133 — 누적 ablation: 우리 것만 순서대로 벗겨 llm-d 쪽으로 수렴시킨다 |  |
| [EXP-134](Agent_applications/agent_motivation_experiment/experiments/EXP-134_instance-cap-alone.md) |  | EXP-134 — 배포 설정에서 클래스별 인스턴스 상한 하나만 뺀다 |  |
| [EXP-135](Agent_applications/agent_motivation_experiment/experiments/EXP-135_instance-cap-hour.md) |  | EXP-135 — 클래스별 인스턴스 상한을 한 시간 trace 에서 뺀다 |  |
| [EXP-136](Agent_applications/agent_motivation_experiment/experiments/EXP-136_qwen-horizon.md) | ● | EXP-136 — 지평 곡선과 KV 투영이 두 번째 모델에서도 같은 모양인가 |  |
| [EXP-137](Agent_applications/agent_motivation_experiment/experiments/EXP-137_affinity-and-cap-together.md) | ● | EXP-137 — 클래스를 읽는 메커니즘 둘을 같이 끄면 |  |
| [EXP-139](Agent_applications/agent_motivation_experiment/experiments/EXP-139_slo-scaling.md) | ● | EXP-139 — 35 req/s에서 SLO를 0.7 / 0.9 / 1.1 / 1.3배로: 정책의 순위가 우리가 고른 예산의 산물인가 |  |
| [EXP-140](Agent_applications/agent_motivation_experiment/experiments/EXP-140_control-plane-overhead.md) | ● | EXP-140 — 제어평면이 결정 하나에 쓰는 시간, 그리고 그것이 부하에 따라 어떻게 자라나 |  |
