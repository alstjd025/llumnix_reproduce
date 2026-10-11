# Dev note — 동적 trace (Azure 모양 rate + 시간가변 믹스)

**Date**: 2026-07-26 · **Commits**: `0cd492a` (Agent_applications), `71a47a3` (llumnix)
**상태**: 배관 구현 완료, **클러스터 미검증**. 분석 스크립트 미작성.

## 왜

EXP-14/17/21은 전부 **정적 조건의 격자**다 — 조건 하나 = rate 하나 = 믹스 하나 = 5분.
실제 서빙 클러스터는 그렇지 않고, 특히 PolyServe의 **dynamic repartitioner**
(`repartitionPeriod=10s`, `stableRounds=2`)는 수요가 움직이지 않으면 사실상 놀고 있다.
EXP-21의 이득이 tier 파티션에서 나왔다는 결론([EXP-21](EXP-21_polyserve-routing.md))은
믹스가 고정이었기 때문에 나온 것이고, 수요가 시간에 따라 움직일 때도 유지되는지는
아직 모른다. 1시간 동안 rate와 믹스가 함께 움직이는 연속 run이 그걸 처음 시험한다.

## 만든 것

### 1. trace 생성기 `traces/dynamic/build_dynamic_mix_trace.py`

Azure LLM Inference 2024 conv+code 분당 카운트를 도너로 쓴다. 실측 모양(이번에 재측정):

| | 분당 평균 | p95/p5 | 시간내 분단위 CV |
|---|---|---|---|
| conv | 2709 (45/s) | 2.80 | 0.075 |
| code | 1667 (28/s) | 16.5 | 0.130 |
| conv+code | 4376 (73/s) | 3.59 | — |

- **시간 압축**: 도너 N일 → run 1시간 (기본 4일 = 96×) → **피크 N개**. 하루가 15분.
- **창 시작 06:00**: 결합 시계열의 일일 최저 시각. 자정에서 시작하면 가장 큰 피크가
  **종료 3분 전**에 걸려 백로그를 드레인하는 중에 측정이 끝난다. 06:00 시작이면
  run이 조용하게 시작해 조용하게 끝난다. (첫 버전에서 실제로 이 결함이 나왔다.)
- **분위수(rank) 매핑** → [10,50] req/s. 선형 스트레치가 아닌 이유: Azure conv+code의
  동적 범위는 p95/p5 ≈ 3.6배인데 우리가 쓰려는 대역은 5배다. 선형이면 대역을 못 채우거나
  노이즈 바닥을 과장한다. rank 변환은 **시간적 순서와 자기상관을 보존**하지만
  **rate 분포의 모양은 보존하지 않는다** — 각 rate에 머무는 시간이 균등해진다.
  (부수 효과로 rate-binning 분석의 조건수가 좋아진다.)
  → 서술은 "Azure 모양을 우리 클러스터에 맞게 축소", **"Azure trace"가 아니다.**
- **도착**: 1초 bin별 Poisson(λ_i), bin 내 균등 배치.
- **믹스 일정**: A→C→B→A 15분 구간 (EXP-14 비율 그대로). 구간 경계가 골짜기에 떨어져
  **믹스마다 피크 하나와 골짜기 하나를 온전히 겪는다** — rate와 믹스가 교락되지 않는
  균형 설계. 구간별 클래스 시퀀스는 `build_class_sequence`를 그대로 재사용해
  실현 비율이 구간 안에서 정확하다.
- **warmup**: trace 앞에 60s @ rate_min을 붙이고 `phase=warmup`으로 태깅.
  (trace-replay 모드에는 rate 모드의 `--warmup-rpm`이 없다.)

산출물 3개: canonical `.csv` / `.plan.json`(λ(t)·구간별 목표·실현 비율 = 분석의 정답지)
/ `.png`(1시간 클러스터를 쓰기 전에 곡선을 눈으로 확인).

생성해둔 것 (CSV는 repo-wide `*.csv` gitignore 대상, 생성기가 결정적이라 재생성 가능):

| stem | 도착 | 평균 | 용도 |
|---|---|---|---|
| `dyn60_azure4d` | 108,417 | 29.95 req/s | 본 run |
| `dyn06_azure4d_smoke` | 11,540 | 30.41 req/s | 7분 배관 검증용 |

### 2. 시간가변 믹스 (`class_plan_file`)

`mixed_request_level_poisson`의 믹스는 run 단위 고정이었다(`build_class_sequence`가
가중치 하나로 전체 시퀀스 생성). 시간축을 넣기 위해 **도착별 클래스를 오프라인에서
확정**해 trace CSV의 `class` 컬럼에 넣고, 워크로드가 같은 파일에서 읽는다.

- 러너는 여전히 `arrival_s`만 읽는다 → **"trace는 타이밍만" 계약 유지**
  (TRACE_FORMAT.md가 추가 컬럼은 명시적으로 무시한다고 규정).
- 대신 **불변식**: 파일이 `arrival_s`로 **순증가**해야 한다. 러너가 offset을 정렬하고
  나머지 컬럼을 버리므로, 파일 순서와 정렬 순서가 어긋나면 **모든 요청의 클래스가
  조용히 잘못 붙는다** — 에러 없이, 그럴듯한 결과로. 생성기가 쓸 때 강제하고
  `mixplan.load_class_plan`이 읽을 때 재검사한다.
- `--load-procs n`에서 부모는 워커 k에게 `offsets[k::n]`을 준다. 워커의 j번째 draw는
  전역 도착 `k + j*n`. **wall-clock이 아니라 전역 인덱스로 색인**하는 이유: 오픈루프
  드라이버가 밀리면 지난 도착을 연속 제출하는데, 그래도 클래스↔도착 짝은 불변이다.
- `class_plan_file`이 있으면 `mix` 블록을 **대체**한다(둘 다 선언하면 조용히 어긋날 수 있음).

### 3. repartition telemetry (`71a47a3`)

`tierRepartitioner`는 결정을 klog에만 남겼다. 5분 조건에는 충분하지만 1시간 run은
파드 로그 버퍼보다 오래 산다 — 그리고 그 run의 요점이 바로 **배정이 수요를 따라가는지**다.

- `scheduler_polyserve_tier_servers{tpot_slo_ms}`, `scheduler_polyserve_tier_demand{...}`,
  `scheduler_polyserve_live_servers`
- **매 윈도우 발행** (서버가 움직인 윈도우만이 아니라). 평평한 구간이 hysteresis가
  잡고 있다는 증거라서, 아무것도 안 바뀔 때 게이지가 멈추면 측정 대상이 사라진다.
  `defer`로 걸어 early return(live 0, hysteresis 미충족)에서도 표본이 나온다.
- `current`가 아니라 `demand`를 순회 — 조용해진 tier가 시계열에서 빠지지 않고
  **뺏긴 서버 수를 계속 보고**한다.
- 클라이언트 수집기 allowlist(`llumnix_metrics.py` `SCHEDULER_METRICS`)에 추가.

### 4. k8s job 템플릿 `k8s/exp07/runner-dyn.template.yaml`

exp21 템플릿과의 차이는 전부 의도적이며 파일 주석에 적혀 있다. 함정 하나:
**trace-replay가 llumnix에서 cold restart를 걸려면 `--restart-server`와
`--restart-per-condition`이 둘 다 필요**하다 (trace-replay 분기는 전자로 게이트하고
`_maybe_restart_llumnix`는 후자를 요구). 하나만 주면 조용히 warm fleet을 재사용한다.
잡 시작 시 워크로드의 `class_plan_file`과 `--trace-file`이 같은 파일인지 확인하고
다르면 abort — 어긋나면 의도와 다른 믹스로 한 시간을 태운다.

## 결정 기록 (사용자 승인)

| 결정 | 선택 | 대안과 이유 |
|---|---|---|
| rate 매핑 | 분위수(rank) | 선형 스트레치는 10–50 대역을 못 채움; 합성 곡선은 "실제 trace" 서술 불가 |
| 믹스 구동 | 구간별 A→C→B→A, rate와 위상 어긋나게 | 피크 정렬은 교락; Azure code-share 유도는 "3~4번 변화"를 정확히 못 지킴 |
| 클래스 계획 위치 | trace CSV의 `class` 컬럼 | config 분리는 파일 2개가 짝맞아야 하고 실현 비율이 어긋날 여지 |
| arm 구성 | **보류** — 사용자 시스템 설계/구현 후 테스트 | |

풀 크기는 **바꾸지 않았다**(chat 1000 conv, swe 1500 transcript). replay가 바이트
동일이라 첫 한 바퀴 이후는 어차피 prefix-cache 히트 영역이고, 풀을 키우면
EXP-14/17/21과의 비교 가능성만 잃는다. 1시간 순환: chat ~40×, swe ~25×, dr 없음(60k).

## 검증 상태

**검증됨**: 12-proc round-robin의 전역 인덱스 재구성이 108k행 plan을 정확히 복원
(로컬 시뮬레이션). Go 빌드·vet·`pkg/scheduler/policy` 테스트 통과. 템플릿 YAML 렌더.

**미검증 (클러스터에서 한 번도 안 돌림)**:
- `--mode trace-replay`는 **이 프로젝트에서 처음 쓰이는 모드**다.
- 실제 워커 프로세스에서 class plan이 붙는지 (로컬 시뮬레이션만 통과).
- 새 gauge가 스크레이프에 나오는지.
- trace 내장 warmup이 rate 모드 warmup을 대체하기에 충분한지.
- **종료 드레인**: trace 드라이버는 `--post-duration-grace`를 무시하고 in-flight를
  전부 기다린다(`ex.shutdown(wait=True)`). 과부하 꼬리가 길면 잡이 오래 매달린다.
  잡 타임아웃이 유일한 backstop.

## 남은 일

1. **7분 smoke** (`dyn06_azure4d_smoke`, arm=polyserve — 새 gauge를 쓰는 유일한 정책).
2. **분석 스크립트**. 표준 rate-sweep 그림 세트는 "run 하나 = rate 하나"를 전제해
   그대로 못 쓴다([[exp-figure-set-standard]] 메모 참고). 필요한 것:
   - 시간분해: offered/realised rate, 클래스별 슬라이딩(60s/10s, arrival-anchored)
     attainment, KV p90·inflight·preemption, **tier 배정 시계열**, 믹스 구간 밴드
   - **rate-binning**: 윈도우를 순간 offered rate로 묶어 attainment-vs-rate 곡선을
     한 run에서 복원 → EXP-14/21 sweep 곡선과 직접 겹침
   - 구간별/전체 요약: 클래스별·등가중 attainment, goodput tok/s
     (등가중을 쓰는 이유는 EXP-21의 분모 구성 함정 참고)
3. arm 구성 결정 (사용자 시스템 구현 후).
