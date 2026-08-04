# Agent Notes — llumnix_reproduce

Llumnix(Go 컨트롤플레인: scheduler + gateway) 포크. 여기에 라우팅/admission 계층의
연구용 정책을 구현하고, 부하 실험은 별도 저장소 `Agent_applications/`(자체 git, 이
저장소의 `.gitignore` 대상)에서 돌린다.

## 복귀 절차 — 대화가 잘리거나 compaction된 뒤 여기부터 (사용자가 말하지 않아도 수행)

맥락이 사라진 상태에서 이어서 일하려면 **순서대로 세 가지만** 하면 된다.

**1. 무엇을 하고 있었는지 읽는다**

| 읽을 것 | 무엇이 있나 |
|---|---|
| [ms_dev/notes/fluidserve-v0.1.1.md](ms_dev/notes/fluidserve-v0.1.1.md) | **여기부터 읽는다.** 현재 상태의 자족적 명세 — v0.1에서 **결정 규칙은 한 줄도 안 바뀌었고** 바뀐 것은 길이 프로파일 하나와 계측. 실측 표(정적·한 시간), **v0.1.1이 아닌 것**, 그리고 **미해결 일곱 개를 우선순위로**(1순위가 45 req/s의 두 상태 산포) |
| [ms_dev/notes/fluidserve-v0.1.md](ms_dev/notes/fluidserve-v0.1.md) | 앞 버전. **결정 규칙·용량 모델·상수 아홉 개는 여기가 정본.** v0.1의 자족적 명세 — 결정 규칙, 무엇을 측정하고 무엇을 설정하는가, 남은 상수 9개, 실측 결과, **v0.1이 아닌 것** |
| [ms_dev/notes/fluidserve-implementation.md](ms_dev/notes/fluidserve-implementation.md) | 시간순 경위. §13 v9~v18과 반증된 가정, §15~§22 v19~v22, §21 보류 항목 |
| implementation.md **§59** | **여기부터 읽는다. 지금 상태의 정본.** EXP-54 반복 2. **점수가 대단히 잘 재현된다** — offered **FluidServe 69.6±0.3 / Llumnix SLO 39.0±0.2 / PolyServe 17.1±0.5**, goodput **17,967±106 / 11,545±55 / 3,192±7**, 총 처리량은 18,911 / 16,176 / 18,212로 **3.8% 차이인데 goodput 5.6배**. **59.2: 클래스 분리는 두 반복 모두 일어나고(창별 최대 점유율 99~100%) 다른 것은 어느 엔진이냐다** — 반복 1은 60분 내내 엔진 1, 반복 2는 엔진 2 → 3 → 1로 두 번 옮겨 감. **59.3은 정정 절이다**: "분리가 재현되지 않는다"고 먼저 썼는데 **한 시간 전체로 풀링한 집중도가 정체가 움직이는 양을 못 재기 때문**이었다(§40·§55·§32와 같은 계열). **그림이 표를 먼저 반증했다.** 새 규칙: **집중되는 대상의 정체가 움직일 수 있으면 전 구간 풀링으로 집중도를 재지 않는다.** §58.3의 인과 주장은 **확인도 반증도 안 된 상태**이고 재려면 `--fluidserve-enable-affinity=false` ablation이 필요하다. **59.5: preemption은 fleet 총계로는 재현되고(2,965 대 2,922) 엔진별 배분은 완전히 달라진다.** **59.7 확인 필요 둘**: 기동 줄이 50개 조건 전부 `classharm=false`인데 소스·바이너리·배포스크립트·파드args 넷 다 true; migration 켠 arm도 요청 이동 0건 |
| implementation.md **§58** | 그 앞. EXP-54 반복 1. §58.1 총 생산량 4% 차이에 goodput 5.7배. **§58.3의 관측은 유효하나**(반복 1은 dr이 60분 내내 엔진 8001) **인과 주장은 확인도 반증도 안 된 상태**(§59.3). **58.4: Llumnix 기본 arm 중단** — 거절하지 않으니 40분에 포화되고 **부하 생성기가 임시 포트를 고갈**(Errno 99 62,114건, 다른 arm 0건), 클라이언트 수정 전에는 못 잰다. PolyServe는 chat 엔진 둘이 **96.4·101.0ms**에 큐 445·1,121, 엔진 불균형 **48배**(FluidServe 4.5배) |
| implementation.md **§57** | 그 앞. EXP-53 네 정책 정적 sweep(2반복 64조건). offered 45 req/s에서 **FluidServe 90.2 / Llumnix SLO 52.4 / Llumnix 32.1 / PolyServe 27.5**, 70에서 **51.9 / 21.4 / 3.9 / 14.4**, goodput 70에서 **19,435 / 11,737 / 802 / 3,965**. **총 생산량은 37% 차이인데 goodput은 24배** — 프로젝트 출발 주장이 정책 비교로 재현됨. **확인 필요 둘**: migration arm 구분이 주장대로가 아닐 수 있음(스케줄러 재배치 루프가 엔진 설정과 무관하게 돎), Llumnix SLO의 admitted 상승은 chat만 88%까지 거절해 분모가 바뀐 것 |
| implementation.md **§51~§56** | 그 앞. §51 후보 C 재측정(정적 +10.6·preemption −33%인데 동적 −4.4로 기각). §52 45 req/s **쌍안정**과 상태 변수(chat 없는 엔진 유무, 24대24 예외 없음). §53 C가 붕괴를 없앰(0/8 대 8/20)—affinity가 feasible 안에서만 도니 게이트가 닫히면 분리가 안 생긴다. §54 게이트를 **축(gateSlack)** 으로. §55 `c_kv` 1/1.6 주장 정정(실제 1/1.26). §56 축은 단조롭지 않고 양 끝으로 붕괴 |
| implementation.md **§51(구)** | EXP-50 — 후보 C를 수정 프로파일 위에서 다시. **정적 +10.6점**(60 req/s에서 58.6 → 69.2, 산포 0.1, 거절률 하락, goodput +15%, dr 84.7→100.0, swe 22.0→82.9). **EXP-46의 기각 사유였던 preemption 6,583은 철회된다** — 올바른 입력 위에서 C는 2,794 → **1,873으로 33% 줄인다.** **새 기각 사유는 chat이다**: 한 시간에서 offered 69.7 → 65.3, 거절 27.0 → 30.7, chat **−14.4**(요청의 76.9%), dr +16.9, swe +48.0. **게이트는 두 일을 겸하고 있었다 — 서빙 가능한 dr을 거절하는 일과 가장 빡빡한 클래스를 위해 용량을 남기는 일. C는 둘 다 없앤다.** 다음은 §51.5의 둘(후보 B, 게이트에 클래스별 하한) |
| implementation.md **§50** | 그 앞의 정본. EXP-49 — **H2 기각**(preemption 2,724 → 1,748로 36% 줄지만 offered 2.4점·goodput 3.4% 손해, 규칙은 500 미만을 요구). **투영 자체는 고쳐졌다**(배포된 투영 평균오차 −26,841 → +13,341, MAE −28%) — 그런데 점수가 나빠졌다. 그리고 계수기가 처음으로 **무엇이 배치를 막는지**를 세었다: **페이스 게이트 90.8%, memory 7.2%**. **§47.3의 "재고 조건으로 유량을 통제한다"는 진단은 구속력 없는 항을 겨냥하고 있었다.** 게이트를 바꾸는 유일한 제안이 **후보 C**이고 EXP-50이 수정 프로파일 위에서 다시 잰다 |
| implementation.md **§49** | 그 앞의 정본. EXP-48 결과 — **길이 프로파일 수정이 지금까지의 어떤 정책 변경보다 크다.** 정적 60 req/s에서 offered가 네 번 재서 36.6이던 것이 **56.8·56.9**(산포 0.1), goodput 12,466 → 18,694. 한 시간 trace에서 59.7 → **70.2**(산포 1.1), admitted **95.7**(이 trace 최고), 거절률은 오히려 하락. **§36·§44가 하루 들여 분해했던 50~56분 손실이 16.5 → 35.9로 Llumnix SLO의 17.3을 처음 넘는다.** Llumnix SLO 대비 +21.2 → **+31.9점**. 그런데 **preemption이 1,471~1,852 → 2,776으로 올랐고**, 사전 판정 규칙("1,400 초과면 도착 항 결여가 주원인")대로 **H2가 다음이다**(EXP-49 진행 중) |
| implementation.md **§48** | 그 앞의 정본. 투영을 처음으로 실제 미래와 대조했다 — 배포된 `proj`가 **88.5%의 경우 과소예측**(평균 −101,633 토큰, MAE 106k)이고 **투영을 안 하는 것(MAE 29k)보다 나쁘다.** 편향은 엔진이 찰수록 커진다(−7.1% → −22.7%). 원인 둘: ① **deepresearch 길이 프로파일이 3.4배 짧았다**(282 대 985) — `5fa82f8`이 07-29에 워크로드를 바꿨는데 프로파일을 안 고쳤다. **고쳤고 EXP-48이 재고 있다.** ② **흐름 수지에 도착 항이 없다**(horizon당 약 48건, 약 83,000 토큰). **후보 H는 §48.1에서 철회**, 다음은 **H2**(관측 KV 변화율) |
| implementation.md **§44~§47** | 그 앞의 정본. §44 50~56분 격차는 전부 deepresearch. §45 **"후보 A가 preemption을 0으로 유지한다"는 철회** — 0/0/1,899. §46 EXP-47: **한 상태 읽기 안에서 뷰는 갱신된다**(두 번째 이후 배치의 headroom이 평균 −57k) → in-flight 가설 반증. §47 **preemption은 FORCE가 아니라 엔진이 채워지는 동안** 일어난다(arm 셋·엔진 다섯에서 예외 없음) |
| implementation.md **§39~§43** | §39 **후보 A 채택 — EXP-27 이후 첫 정책 변경**(60 req/s에서 offered 36.1→49.3, goodput +33%). §40 §36.3의 원인 설명 **철회**(평균끼리 나눈 값이었다). §41 한 갱신 주기에 여러 건이 나간다는 측정. §42 §41.4 **정정**(in-flight 계정은 설계상 그 창을 덮는다). §43 EXP-43 null + **후보 여섯 개의 현황표(43.4)** |
| implementation.md **§32** | **기록된 TBT가 실제의 1/1.92였다 — §1~§31의 모든 attainment가 예산 약 2배로 판정된 것이다.** 세 방향 검증과 반증 시도는 §32.2·§32.7 |
| implementation.md **§33** | 설계 검토 — 포화에서 정책이 하는 일, `gate_allowance` 50ms 고정, agent 클래스 실패의 원인 |
| implementation.md **§34** | 60 req/s에서 무엇이 무너지는가. 배치는 45와 같고 페이스만 4.5ms 올라 chat 예산을 넘는다. **1ms = 총계 7.7점의 절벽.** 34.4는 배포된 `c_kv`가 실측의 1/1.6이라는 부수 발견 |
| implementation.md **§35** | EXP-40 — 우위가 엔진 때문인지. **아니다**(격차 변화가 산포 안). 사전 예상이 틀렸고 그 이유가 §33.1을 뒷받침한다 |
| implementation.md **§36** | **EXP-41(한 시간 동적 trace 두 개).** 두 trace 모두 +21.2/+26.4점. **클래스 packing이 작동한다는 첫 직접 증거**(azcode에서 chat 전용 엔진 두 개, chat ITL 26.6~32.6ms, chat 45.3→85.4)와 **그 대가**(dr 전용 엔진이 preemption 5,513회). 설계 결함 둘의 원인이 36.3(`capMem`에 전방 모델 없음)·36.4(포화에서 게이트 50 고정)이고 **36.3은 판정 규칙까지 미리 적어 뒀다.** 36.5는 36.3의 원인을 처음에 잘못 귀속한 것의 정정 |
| implementation.md **§31** | 그 이전의 상태 요약(정책 불변, 반증된 가정 여섯 개). **§31.7의 다음 계획은 §32와 EXP-39가 대체했다** |
| implementation.md **마지막 절** | 그 이후에 일어난 일. 항상 문서 끝이 가장 최신이다 |
| implementation.md **§37** | **다음에 무엇을 고칠지가 여기 있다.** 세 실패(높은 rate에서의 하락 / 8003 과부하 / 후반부 역전)의 원인 규명. 셋이 만나는 곳은 **route 결정 비율**(15 req/s에서 99.7% → 60에서 1.5%). §37.5는 ROUTE와 FORCE가 같은 예산에 다른 기준을 쓴다는 코드 불일치. **§37.6에 후보 넷과 판정 규칙이 실행 전에 적혀 있다** |
| **정책 상태** | **후보 A가 들어갔다**(`--fluidserve-force-margin`, 코드 기본값은 아직 off, 실험에서 on). 그 전에는 EXP-27 이후 불변이었고 v25~v28 네 개를 전부 기각했다(§31.1) |
| **닫힌 미해결** | §24의 "8ms 과대예측"은 **존재하지 않았다**(§27) — 통계량 불일치. §32가 같은 결론을 다른 방향에서 확인한다: 엔진 50.5ms와 모델 50.2ms가 처음부터 맞았고 `capacity_correction`이 0.995다 |
| **남은 방향** | **§51.5의 둘.** 원하는 것은 도착 요청을 자기 예산으로 판정하되(C의 옳은 절반) 지연을 못 흡수하는 클래스용 용량은 남기는 게이트다. ① **후보 B**(SHED에 KV 부담, 사용자 제안) — 전제를 수정 프로파일 위에서 재측정 ② **게이트에 클래스별 하한** — `gateAllowance`가 우연히 하던 일을 거절이 아니라 예약으로 명시 ③ knee 확정(36·40·45 req/s, 반복 4회+) |
| **후보 현황** | **H0**(길이 프로파일 수정) **채택 — 최대 이득**(정적 +20.2, 동적 +10.4, 정책 코드 0줄). **C** 정적 +10.6·preemption −33%인데 **동적 총점 −4.4(chat −14.4)로 기각** — 등가중으로는 +16.8. **H2 기각**(투영은 고쳤으나 offered −2.4). **A** 정적 채택·동적 무효. **E** null. **D·F·G·H** 폐기/철회. **B** 미착수 — **다음** |
| **엔진 스케줄러** | QoServe(Niyama) 이식은 `patches/vllm-sched/deadline_sched.py`, 원본 대조는 `ms_dev/notes/qoserve-niyama-fidelity.md`. `SCHED_EXTRA_ARGS`로 켠다. **우리 정책이 엔진 큐를 비워 두므로 unit 4(동적 청킹) 하나만 작동한다** |
| `Agent_applications/.../experiments/EXP-NN_*.md` 중 번호가 가장 큰 것 | 지금 돌고 있거나 마지막으로 돌린 실험의 설계·판정 규칙 |

**2. 지금 클러스터에서 뭐가 도는지 확인한다**

```bash
kubectl -n llumnix get jobs | grep bench-runner        # 실험이 도는 중인가
ls -dt Agent_applications/agent_motivation_experiment/results/* | head -5
ls -l --time-style=+%m%d_%H:%M bin/scheduler-exp07     # 배포된 바이너리 시각
git log --oneline -5 && (cd Agent_applications && git log --oneline -5)
```

**실험이 돌고 있으면 `bin/`을 덮어쓰지 말고 실행 중인 셸 스크립트를 편집하지 말 것**
(아래 함정 참조). 끝날 때까지 소스만 고친다. 다음 실험을 미리 걸어두려면
`/home/nxclab/tools/staging/`에 빌드해 두고, 앞 실험이 끝나기를 기다렸다가 배포·실행하는
연쇄 스크립트를 쓴다(`/home/nxclab/tools/exp27_after_exp25.sh`가 그 예).

**3. 결과를 볼 때 반드시 지키는 규칙 — 전부 실제로 틀렸던 것들이다**

- **조건당 1회 측정으로 판정하지 않는다.** 반복 간 산포는 조건에 따라 0.0~4.2점이다
  (EXP-38 실측: FluidServe 0.0/0.0/0.1/1.2, Llumnix SLO 0.0/0.0/4.2/1.2, 15~60 req/s).
  세션 간 이동은 이 워크로드에서 **최대 4.6점**이다 — 예전에 인용하던 5~17점은 다른
  워크로드이거나 v19에서 제거된 원인의 값이다. 평균 차이가 산포보다 작으면 "차이 없음".
- **클라이언트가 기록한 지표를 서버가 보고하는 값과 대조한다.** `tbt_mean_ms`가 실제의
  1/1.92였다(§32). 지금은 분석이 `(e2e − ttft)/(output_tokens − 1)`로 유도하므로 과거
  run도 다시 채점된다. **2026-07-30 이전 문서의 attainment 수치를 인용할 때는 어느
  지표인지 밝힌다.**
- **세션이 아니라 반복이 기준이다 (2026-08-01 규칙 변경).** 예전 규칙은 "세션을 넘는
  비교는 무효"였는데, 근거보다 강한 규칙이었다. 같은 설정이면 세션이 달라도 같은 값이
  나온다는 것을 이번 주에 두 번 쟀다 — EXP-41과 EXP-44의 `fluidserve`(같은 trace, 하루
  차이)가 offered 59.7 대 **59.2**, admitted 84.3 대 83.7, 거절 29.1 대 29.3%, goodput
  15,643 대 15,472이고, EXP-38과 EXP-42의 FluidServe(60 req/s)가 offered 35.2 대 36.1,
  goodput 12,359 대 12,401, chat ITL 49.9 대 49.9다.
  **같은 세션에 두 arm을 놓는 것의 실제 이득은 두 arm이 공유하는 오프셋이 차분에서
  상쇄되는 것**(통계에서 blocking)이고, 같은 정밀도를 더 싸게 얻는 **설계상의 이점이지
  유효성 조건이 아니다.** 진짜 문제는 세션이 아니라 **arm당 1회**였다 — 그러면 세션
  효과와 arm 효과를 분리할 방법이 없다. 반복이 여러 세션에 걸쳐 있으면 세션 효과는
  평균으로 흡수되고 오차막대에 들어간다.
  → **지금 규칙**: 같은 세션에 놓을 수 있으면 그렇게 하되(싸니까), 세션이 다르다는
  이유로 비교를 버리지 않는다. **읽으려는 양의 반복 산포를 알고, 그보다 큰 차이만
  읽는다.** 산포를 줄이려면 반복을 늘린다.
  ⚠ **양마다 다르다.** 위의 같은 두 run에서 점수는 0.5점 이내인데 **preemption은
  1,471 → 1,852로 26% 움직였다.** 총계 지표는 세션을 잘 건너가고, 특정 엔진에서
  일어나는 사건은 안 건너간다. 산포는 양별로 따로 안다.
- **분모를 두 개 다 본다 (2026-07-28 변경).** 주 지표는 **admitted**(시스템이 받아들인
  요청이 분모), 그 옆에 **offered**(도착한 모든 요청, 거절은 위반). admitted만 읽으면
  전부 거절하는 정책이 최고점을 받으므로 **거절률과 token goodput을 반드시 같이 본다.**
  `exp23_rate_sweep.py`가 네 개를 한 표에 출력한다.
  → 2026-07-28 이전 기록의 "served" 수치는 구현 결함으로 사실상 offered 수치다(§16.5).
- **요청 단위 지표만 보지 않는다.** 넷을 같이 본다.
  `engine_occupancy.py`는 이제 **preemption 횟수와 엔진별 prefix hit rate**도 낸다.
  preemption은 요청 단위 지표에 전혀 안 보이는데 vLLM V1은 recompute로 쫓아내므로
  엔진 시간을 크게 먹는다(PolyServe 3000 rpm 8분에 549회 = 엔진 2대 시간의 약 27%).
  엔진별 prefix hit rate는 **라우팅이 클래스를 분리했는지를 엔진이 직접 보고하는 값**
  이다(PolyServe chat 엔진 94.5% 대 agent 엔진 66.7%; FluidServe는 네 엔진 전부
  63~65% = 섞여 있음). `slo_rule_breakdown.py`는 TTFT/TBT/E2E 중 무엇이 깨졌는지.
- **설정을 바꿨으면 스케줄러가 실제로 읽은 값을 확인한다.**
  `set_scheduler_profiling.py`가 자동으로 대조하고 `verified: …`를 출력한다.
  그 줄이 없으면 그 run은 무효다.

## 스킬 — 실험/기록/그림은 스킬을 먼저 부른다

`.claude/skills/`에 셋이 있다. 각 항목은 **실제로 한 번씩 실패한 것**만 담았다.

| 스킬 | 언제 |
|---|---|
| `exp-run` | 실험을 걸기 전, **설정을 바꾸기 전**, 결과를 판정할 때 |
| `exp-record` | 결과·설계변경·반증·실수를 적을 때, compaction 직전 |
| `exp-plot` | 그림이나 표를 만들 때 |

`exp-run`의 첫 항목이 가장 비쌌던 실수다: **설정을 바꾸기 전에 그 값이 upstream
기본값인지 먼저 확인한다.** `--wait-scheduling-timeout` 5000ms를 "불공정한 비대칭"으로
보고 고쳤는데, grep 한 번이면 그게 게이트웨이 자체 기본값이고 오히려 FluidServe만
비기본값을 쓰고 있었다는 것을 알 수 있었다.

## 서술 규칙 (문서·커밋 메시지·사용자 보고 전부)

**비유나 관용구를 쓰지 말고 일반적으로 통용되는 기술 용어로 쓴다.** 문장이 길어져도
무방하다. 어떤 상태가 되는지를 **왜/어떻게**로 풀어 적는다.

- 쓰지 말 것: "인스턴스가 통째로 인질이 된다", "수조", "눈이 먼다"
- 쓸 것: "그 요청이 완료될 때까지 해당 인스턴스의 cap이 낮게 고정되어 새 요청을
  받을 수 없다"

기준: 그 문장이 논문 본문에 그대로 들어가도 되는가.

**추상적으로 압축한 표현도 같은 규칙에 걸린다** (2026-08-01 사용자 지적). 비유가
아니더라도, 읽는 사람이 원래 사실을 복원할 수 없으면 쓰지 않는다. 한 문장이
길어지더라도 **무엇이 무엇을 어떻게 만드는지를 그대로 적는다.**

| 쓰지 말 것 | 쓸 것 |
|---|---|
| "부호만 뒤집힌 것이다" | "shedding이 예산이 빡빡한 클래스를 먼저 자르는데, 그렇게 남는 요청들이 KV를 더 많이 차지해서 토큰 하나당 시간을 오히려 더 길게 만든다" |
| "래칫이다" | "한 번 섞이고 나면 모든 엔진이 chat을 들게 되고, 그러면 모든 엔진의 허용 속도가 chat 예산인 50 ms가 되고, 그러면 아무것도 route되지 않으므로 분리를 다시 만들 방법이 없다" |
| "전방 모델이 없다" | "지금 상주 중인 요청들에서 잰 값 하나만 쓰고, 그 요청들이 앞으로 만들어낼 토큰은 더하지 않는다" |
| "페이스가 오른다" | "토큰 하나를 만드는 데 걸리는 평균 시간이 길어진다" |
| "용량이 무너진다" | "SLO를 지킨 채 완료되는 요청 수가 초당 39.01건에서 20.69건으로 줄어든다" |

**사용자에게 보고할 때도 같다.** 결론만 압축해 놓으면 사용자가 되물어야 하고, 그
왕복이 낭비다. 처음부터 기전을 풀어서 적는다.

## 문서 지도

| 문서 | 내용 |
|---|---|
| [POLYSERVE_DESIGN_KO.md](POLYSERVE_DESIGN_KO.md) | PolyServe 이식 설계 (정본) |
| [POLYSERVE_PROGRESS.md](POLYSERVE_PROGRESS.md) | PolyServe 구현 시간순 기록, 함정 |
| [ms_dev/notes/polyserve-fidelity.md](ms_dev/notes/polyserve-fidelity.md) | **PolyServe 이식의 원문 대조 (정본).** "Isolation의 대표주자로 세울 수 있는가"에 대한 답 — **세울 수 있으나 이름을 "고정 fleet 위의 정적 클래스 파티션"으로 좁혀야 한다.** ⚠ **§2가 필수 수정**: `set_scheduler_profiling.py`의 `--polyserve-tier-decode-tokens`가 `25:728,50:386,100:275`인데 실측은 `494/428/985`로 **dr이 3.58배 과소**다. §48·§49와 같은 오류가 기준선 쪽에만 남아 있어, 고치기 전 EXP-53의 PolyServe 수치는 인용하면 안 된다 |
| [ms_dev/notes/fluidserve-how-it-works.md](ms_dev/notes/fluidserve-how-it-works.md) | **시스템 전체를 위에서 아래로 설명한 문서.** 처음 이해할 때 여기부터 — 문제 정의, 유연한 격리, 시간·메모리 모델, 결정 사다리, 클래스 분리, 무엇을 측정하고 무엇을 설정하는가, 실측 결과, 미해결. 각 설계 결정에 그것을 정하게 만든 측정이 붙어 있다 |
| [ms_dev/notes/fluidserve-v0.1.md](ms_dev/notes/fluidserve-v0.1.md) | **FluidServe v0.1 명세 (정본)** |
| [ms_dev/notes/fluidserve-design.md](ms_dev/notes/fluidserve-design.md) | FluidServe 설계 원안 |
| [ms_dev/notes/fluidserve-implementation.md](ms_dev/notes/fluidserve-implementation.md) | FluidServe 구현 결정 기록 |
| [ms_dev/notes/related-works-review.md](ms_dev/notes/related-works-review.md) | **관련 연구 검토 (일곱 편)** — §0~§7 SLOs-Serve/PolyServe/AdaGen/Scorpio, §8~§10 JITServe/QoServe/Simple is Better. **§9가 "엔진 레벨 SLO 스케줄러가 있는데 왜 라우팅 계층이 필요한가"에 대한 답이고 논문 motivation의 정본**(순서 대 구성의 구분, 단순 조합 일곱 개의 해부, EXP-40·EXP-25 근거, 최소 조건 셋, §9.7의 미측정 ablation). §11은 SLOs-Serve 요약이고 **정본은 아래 별도 문서**. 원문은 `related_works/*.pdf` |
| [ms_dev/notes/slosserve-comparison.md](ms_dev/notes/slosserve-comparison.md) | **SLOs-Serve 대 FluidServe (정본).** 차별점은 **두 시스템이 같은 min(인스턴스 위 가장 빡빡한 예산)을 지목하고 대응이 반대**라는 것 — 그들은 그 제약 아래에서 토큰 배분을 최적화하고 우리는 제약이 취해지는 집합을 바꾼다(게이트 50.0 → 100.0). **§6에 철회 기록**: "한 번에 평가 대 순차 질의"는 비교 축이 아니다(우리 술어도 인스턴스별 로컬이다). **§10이 baseline 계획** — 새 정책 없이 `affinity=off, pend=off` 조합, 설계서 §6.3의 미측정 칸도 함께 채운다 |
| [ms_dev/notes/qoserve-niyama-fidelity.md](ms_dev/notes/qoserve-niyama-fidelity.md) | QoServe(Niyama) 이식의 원본 대조·수정·한계 |
| [deploy/profiling/README.md](deploy/profiling/README.md) | 지연 프로파일 테이블의 출처·신뢰도 |
| [ms_dev/notes/](ms_dev/notes/) | 클러스터 셋업/배포/장애 기록 |
| `Agent_applications/agent_motivation_experiment/experiments/` | 실험 기록 정본 (EXP-NN) |

## 빌드 / 배포

호스트에 go·docker가 없어 툴체인을 직접 설치했다.

```bash
export PATH=/home/nxclab/tools/go/bin:$PATH
export GOPROXY="https://proxy.golang.org,direct" GOFLAGS=-mod=mod
export TMPDIR=/home/nxclab/tools/gotmp GOTMPDIR=/home/nxclab/tools/gotmp  # /tmp가 noexec

CGO_ENABLED=0 go build -buildvcs=false -o bin/scheduler-exp07 ./cmd/scheduler
go build -buildvcs=false \
  -ldflags="-extldflags '-L./lib/sglang/sgl-model-gateway/bindings/golang/lib/'" \
  -o bin/gateway-exp10 ./cmd/gateway
```

`bin/`이 스케줄러 파드의 `/exp07bin`으로 hostPath 마운트되어 있다. 바이너리를 덮어쓰고
파드를 재시작하면 반영된다. 기존 바이너리 백업 위치는 `/home/nxclab/tools/bin-backup/`.

정책 전환/프로파일 주입은 `ms_dev/scripts/set_scheduler_profiling.py`.

## 반복해서 걸렸던 함정

- `kubectl logs deploy/<name>`은 Terminating 중인 옛 파드를 고를 수 있다. 롤아웃 직후에는
  파드 이름을 직접 지정하고, 목록은 `--sort-by=.metadata.creationTimestamp`로 정렬한다.
- `GetLatencyPredictor`가 `sync.Once`로 프로파일을 한 번만 읽는다. ConfigMap을 바꿔도
  프로세스 재시작 없이는 반영되지 않는다.
- 새 스케줄링 정책을 추가하면 `verifySchedulingPolicy` 화이트리스트에도 넣어야 한다.
  빠지면 기동 시 panic이고 유닛 테스트로는 잡히지 않는다.
- 스케줄러를 `-v 4`로 띄우면 초당 수백 줄이 나와 컨테이너 로그가 1분 남짓만 남는다.
  메커니즘 확인은 로그가 아니라 메트릭이나 분석 산출물로 한다.
- **게이트웨이 버퍼 큐가 워커 5개다**(`--wait-queue-threads 5`, `--max-queue-size 512`).
  워커는 요청이 엔드포인트를 받을 때까지 묶이므로, 배치를 미루는 정책(FluidServe의
  보유-재시도)에서는 **동시 보유 요청이 5건으로 제한**되고 나머지는 큐에서 수십 초를
  기다린다. `gateway_pending_requests`가 515에 고정되면 이 상태다. 4096/16384로 올린다
  (`set_scheduler_profiling.py`가 모든 정책에 적용).
- **Go bool 플래그는 `--flag=value` 한 덩어리로 넣어야 한다.** `--flag false`로 쓰면
  pflag가 플래그를 **true로** 설정하고 `"false"`는 위치 인자로 흘려버린다. 배포 spec에도
  남고 rollout도 성공하므로 **조용히 반대로 동작한다.** 이것 때문에 EXP-25의 ablation arm이
  대조군과 동일한 설정으로 4시간 돌았다(shed 껐다는 arm에서 shed 15,723건).
  `set_scheduler_profiling.py`의 `set_flag`가 이제 bool을 등호형으로 쓰고, 적용 후
  스케줄러 로그의 `FluidServe dispatch policy created` 줄을 되읽어 대조한다.
- **ablation 플래그가 배포에 눌러앉는다 (2026-08-01 발견, 고침).** `set_scheduler_profiling.py`가
  spec을 다시 쓸 때 ablation 플래그 이름들을 **보존 대상**에 넣어 두어서, 한 arm이 쓴 값이
  그 뒤 모든 조건에 남았다. `FS_CLASS_HARM=false`를 쓴 arm 하나(2026-07-28 10:28
  `exp27p2r1_fluidserveflat_m1`) 때문에 **그 뒤 61개 조건 전부**(EXP-27 pass 2~4,
  EXP-28~38, EXP-40, EXP-41)가 `classharm=false`로 돌았다. 지금은 이번 호출이 설정하지
  않은 ablation은 **제거**되어 컴파일 기본값으로 돌아가고, 기본값으로 남은 것들을
  출력한다. **기동 줄은 처음부터 사실을 말하고 있었다 — 없었던 것은 그 줄과 arm의
  의도를 대조하는 단계다.** 자세한 것은 implementation.md §38.
- **두 적합(fit)을 비교할 때는 두 적합이 같은 표본 정의를 쓰는지 먼저 확인한다 (2026-08-03).**
  §34.4가 "배포된 `c_kv`가 실측의 1/1.6"이라고 적었는데, 배포된 계수는 **디코드 전용
  step**에서, 재적합은 **prefill이 섞인 틱 게이지**에서 나온 것이었다. prefill 시간이
  KV와 상관돼 KV 계수에 흡수된다. 같은 표본을 `prefill_duty < 0.02`로 자르면 2.27e-5가
  **1.62e-5**가 되고 R²가 0.31 → 0.58로 오른다. **실제 차이는 1/1.26이다**(§55).
  같은 문서에 적힌 "배포값" 숫자 자체도 파일과 달랐다 — **인용하기 전에 파일을 연다.**
  §32(클라이언트 TBT 1/1.92)·§40(평균끼리 나눔)과 같은 모양이다: **같은 이름의 두 양이
  같은 정의로 만들어졌는지 확인하지 않은 것.**
- **워크로드를 바꾸면 프로파일도 같이 바꿔야 한다 (2026-08-02 발견).**
  `deploy/profiling/.../fluidserve.json`의 `classes[]`는 클래스별 출력 길이 분포이고
  정책의 `completionProb`·`expectedToks`가 전부 여기서 온다. 이 파일은 `d4e8250`(07-26)에서
  EXP-21 로그로 만들어졌고 **그때는 맞았는데**, `Agent_applications 5fa82f8`(07-29 16:35 KST)이
  searcharena 보고서 구조를 4→7절로 늘려 deepresearch 출력이 **282 → 985 토큰**이 되는
  동안 그대로 있었다. 그래서 `outflow`가 그 클래스에서 3.4배 과대예측됐다.
  **그 커밋 메시지는 중앙값 249→942를 이미 측정해 적어 뒀다** — 없었던 것은 워크로드
  변경과 프로파일 사이의 연결이다. 워크로드 생성기를 고치면
  `ms_dev/scripts/gen_fluidserve_profile.py`를 다시 돌린다. 재생성할 때 **`classes[]`만
  갈아끼운다** — `decode_step_law`·`prefill_step_law`는 엔진의 성질이라 같이 바꾸면
  한 번에 두 가지를 바꾸는 것이 된다.
  **⚠ 2026-08-04에 같은 오류가 PolyServe 쪽에 그대로 남아 있는 것을 발견했다.**
  `set_scheduler_profiling.py`의 `--polyserve-tier-decode-tokens`가 `25:728,50:386,100:275`인데
  실측은 `494/428/985`다(dr 3.58배 과소, swe 1.5배 과대). 이 값이 §4.5 admission의 최대 KV와
  **재분할기의 tier별 수요 추정**(= 파티션 자체)에 둘 다 들어간다. **같은 양이 두 곳에 따로
  적혀 있어서 한쪽만 갱신됐다** — 고치면서 `fluidserve.json`에서 읽도록 배관을 합칠 것.
  자세한 것은 `ms_dev/notes/polyserve-fidelity.md` §2.
- **모델이 내놓는 양이 맞는지는 그 양의 미래와 대조해서 잰다 (2026-08-02).** `proj`는
  "한 horizon 뒤의 KV 점유량" 예측인데 **그게 맞는지를 한 번도 재지 않은 채** 열 몇 개의
  후보를 그 위에 세웠다. 재는 방법은 있었다 — 스케줄러가 `projected_kv_tokens`와
  `obs_kv_tokens`를 인스턴스별로 남기므로 시각 t의 예측을 t+horizon의 실측과 짝지으면
  된다(`analysis_scripts/request_level/exp48_projection_error.py`). 결과는 88.5% 과소예측,
  MAE가 **아무 투영도 안 하는 것의 3.6배**였다. **예측값을 쓰는 항이 있으면 그 예측의
  오차를 먼저 잰다.**
- **결과 디렉토리 이름의 시각은 KST도 UTC도 아니다 — 러너 파드가 UTC−7이다 (2026-08-02 확인).**
  `results/260801_0857_...`은 **2026-08-02 00:57 KST**에 시작한 run이다. **차이는 16시간이고
  날짜도 하루 어긋난다.** 확인 방법: 00:56 KST에 건 EXP-48이 `260801_0857`로 적혔다.
  호스트에는 `US/Pacific` tzdata가 없어서 `TZ=US/Pacific date`가 조용히 UTC로 떨어지므로
  그걸로 대조하면 안 되고, `TZ=UTC date`에서 7시간을 빼거나 KST에서 16시간을 뺀다.
  **디렉토리 이름으로 "언제 돌았나"를 읽을 때마다 16시간을 더한다.**
- **설정을 적용했다는 것과 반영됐다는 것은 다르다.** 스케줄러 로그의 시작 줄이 유일한
  authority다. spec을 읽는 것으로는 위 함정을 못 잡는다. 그 줄은 `-v 4`에서 몇 초 만에
  tail 밖으로 밀려나므로 **전체 로그를 읽어야 한다**(`kubectl logs <pod>`, `--tail` 없이).
- **실험이 도는 동안 `bin/`의 바이너리를 덮어쓰지 말 것.** `--restart-per-condition`이
  조건마다 `rollout restart scheduler,gateway`를 하므로, sweep 중간에 새 바이너리를
  넣으면 **조건마다 다른 코드**로 측정된다. 소스는 고쳐도 되지만 빌드 산출물은
  실험이 끝난 뒤에 교체한다(급하면 다른 경로로 빌드).
- **실행 중인 셸 스크립트를 편집하지 말 것.** bash는 파일 오프셋을 기억한 채 이어
  읽으므로, 실행 도중 앞부분에 줄을 넣으면 엉뚱한 블록으로 점프한다. 실제로
  `run_exp22_fluidserve.sh`에 case 하나를 추가했다가 실행 중이던 smoke가 sweep 블록으로
  넘어가 의도치 않은 arm이 시작됐다. 편집은 실행이 끝난 뒤에 한다.
  **2026-08-01에 또 걸렸다** — EXP-42가 도는 중에 `run_exp27_mixsweep.sh`에 `fsah` arm을
  추가했더니 실행 중이던 인스턴스가 `syntax error near unexpected token 'do'`로 죽었다.
  그 run은 오류가 조건이 끝난 뒤에 났고 보관된 배포 spec으로 플래그가 맞았음을 확인해서
  **데이터는 살았지만 그건 운이었다.** 다음 실험을 미리 준비해야 하면 **스크립트를
  스냅샷으로 복사해 그 사본을 실행한다**(`exp43_class_harm.sh`가 그 예: `cp` 후 `bash -n`으로
  파싱 확인하고 사본을 돌린다). 그러면 원본을 언제 고쳐도 도는 실험에 영향이 없다.
- **대기 중인 연쇄 스크립트도 실행 중인 스크립트다.** `sleep` 루프에서 기다리는 중이어도
  bash는 그 뒤를 아직 안 읽었으므로 편집하면 같은 문제가 난다. 고쳐야 하면 **먼저 죽이고,
  고치고, 다시 띄운다**(`pkill -f "exp43_clas[s]_harm.sh"` — 대괄호는 자기 자신을 죽이지
  않기 위한 것).
- **연쇄 스크립트의 완료 마커도 "끝난 Job이 남는다"와 같은 함정이다 (2026-08-02).**
  `exp49_h2.sh`는 `grep -q "=== EXP-48 PART 2 DONE" exp48.log`로 앞 실험을 기다리도록
  썼는데, 로그는 append-only라 **아무것도 못 돌리고 죽은 시도가 남긴 마커에 걸린다.**
  실제로 스케줄러가 CrashLoopBackOff이던 시도가 `rep1 fluidserve/full FAILED` 직후
  `=== EXP-48 PART 2 DONE`을 찍었고, 그걸 기다렸다면 EXP-49가 즉시 시작해 **죽은 클러스터
  위에서 돌았을 것이다.** 기다릴 때는 **문자열 존재가 아니라 개수**를 보거나
  (`[ "$(grep -c ...)" -ge 2 ]`), 마커에 실행마다 다른 식별자를 넣는다. 그리고 **마커는
  "스크립트가 끝났다"이지 "실험이 돌았다"가 아니므로**, 조건이 실제로 결과 디렉토리를
  만들었는지 따로 확인한다.
- **드라이버에 고정하는 플래그는 그 드라이버가 돌릴 바이너리에 있어야 한다 (2026-08-02).**
  EXP-48이 도는 중에 EXP-49용 `fskv` arm을 추가하면서 대조군 arm에도
  `FS_KV_SLOPE=false`를 고정했는데, `--fluidserve-kv-slope-projection`은 **아직 배포되지
  않은 EXP-49 바이너리에만 있다.** pflag는 모르는 플래그를 만나면 종료하므로 스케줄러가
  CrashLoopBackOff에 빠졌고, 그 뒤 모든 조건이 기동 줄을 못 읽어 실패했다. 드라이버는
  그것을 `scheduler pod ... reported no policy`로 보고하는데 **롤아웃이 느린 경우와
  구분이 안 된다.** 플래그를 켜지 않으면(env 미설정) `set_scheduler_profiling.py`가
  명령줄에서 아예 빼므로 두 바이너리 모두에서 동작한다 — **새 플래그는 그 바이너리를
  배포하는 실험에서만 고정한다.**
- **측정 경로는 `bin/`과 `workloads/`만이 아니다 — 도는 sweep이 호출하는 것 전부다 (2026-08-02).**
  드라이버 스크립트의 arm 정의와 `set_scheduler_profiling.py`가 여기 들어간다. **스냅샷
  패턴은 이걸 막아 주지 않는다**: 스냅샷은 연쇄 시작 때 한 번 뜨지만 그 안의 arm 정의는
  조건마다 다시 읽히고, `set_scheduler_profiling.py`는 스냅샷이 아니라 원본을 부른다.
  실험이 도는 동안에는 **다음 실험용 arm 추가도 하지 않는다.**
- **`kubectl wait --for=condition=complete`는 Job이 *실패*하면 영원히 안 돌아온다 (2026-08-02).**
  Failed는 `complete` 조건을 만족시키지 않으므로 자기 `--timeout`(우리 스크립트에서 300m)을
  다 채운다. EXP-48 repeat 2에서 엔진 8002가 cold restart 뒤 1200초 안에 안 올라와 러너가
  rc=1로 끝났는데, sweep 스크립트는 **이미 Failed 상태인 Job을 다섯 시간 기다릴 참이었다.**
  두 드라이버에 `wait_job`을 넣어 `Complete`와 `Failed`를 **둘 다** 폴링하고, Failed면 1을
  반환해 연쇄가 다음으로 넘어가게 했다. 데이터 오염은 없다 — 그 조건은 부하를 만들기 전에
  죽어서 결과 디렉토리 자체가 안 생겼다.
- **엔진 넷 중 하나가 cold restart에서 안 돌아오는 일이 있다 (2026-08-02).** `neutral-0`의
  API 서버 넷 중 8002만 `Application startup complete`를 안 찍고 멈췄다(나머지 셋은 찍었다).
  러너의 `llumnix_deploy.restart_llumnix`가 1200초를 기다리다 `engines NOT serving: [8002]`로
  포기한다. 우리 변경과 무관한 기동 실패이고, **그 조건을 다시 돌리면 된다.** 로그에 잔뜩
  나오는 `PeerManager._main: AttributeError: 'KVCacheConfig' object has no attribute 'list'`는
  migration을 껐는데도 계속 나오는 별개의 잡음이라 이것과 무관하다.
- **거절하지 않는 정책을 포화까지 밀면 부하 생성기가 먼저 무너진다 (2026-08-04, EXP-54).**
  Llumnix load-balance arm이 40분에 네 엔진을 포화시키자 오래 큐에 있던 스트림이 끊겼고,
  클라이언트가 그 실패를 **연결 수준 실패로 인식하지 못해**(서버 종료 키워드 목록에
  `cannot assign requested address`가 없다) 비스트리밍 재시도로 **연결을 하나 더 열었다.**
  그렇게 임시 포트 약 28,000개가 고갈되어 `[Errno 99] Cannot assign requested address`가
  62,114건 나왔고, 실패가 즉시 돌아오니 **시도율이 170/s로 읽혔다 — trace의 도착률이 아니라
  클라이언트가 헛도는 속도다.** 다른 세 arm에는 이 오류가 0건이다.
  → **거절률 0%인 arm에서는 `error_msg`를 종류별로 세고, "시작된 호출/초"가 trace의
  도착률을 넘는지 본다.** 넘으면 그 구간은 정책이 아니라 부하 생성기를 재고 있다.
  고치려면 키워드 목록에 `cannot assign requested address`·`max retries exceeded`를 넣고
  러너의 `net.ipv4.ip_local_port_range`를 넓힌다. **거절은 재시도되지 않는다** — 처리기가
  거절을 먼저 분기하고 어댑터는 `max_retries=0`이다. 메시지의 "Max retries exceeded"는
  requests가 연결 실패에 붙이는 기본 문구다. 자세한 것은 implementation.md §58.4.
- **정체가 움직이는 대상의 집중도를 전 구간 풀링으로 재지 않는다 (2026-08-04, §59.3).**
  EXP-54 반복 2에서 "각 클래스가 한 엔진에 몰린 최대 비율"을 한 시간 전체로 계산했더니
  deepresearch가 32.6%(균등 25%)로 나와 **"분리가 재현되지 않는다"고 기록했는데 틀렸다.**
  창별로 다시 보면 **매 순간 99~100%로 집중돼 있었고 집중된 엔진이 두 번 옮겨 갔을 뿐**
  이다(엔진 2 → 3 → 1). 위치가 움직이면 전 구간 합산 분포는 퍼진 것처럼 읽힌다.
  → **집중도는 창별로 재고, 창마다 어느 대상인지도 같이 낸다.** §40(평균끼리 나눔)·
  §55(표본 정의가 다른 두 적합)·§32(같은 이름의 두 양)와 같은 계열이다.
  → 그리고 이 경우 **그림이 표를 먼저 반증했다** — 창별로 그린 패널에서 두 반복 곡선이
  붙어 다녔는데 표를 먼저 믿었다. **생성한 그림을 읽고 표와 대조한 뒤에 결론을 쓴다.**
- **그림 스크립트가 조용히 한 종류의 run을 통째로 건너뛴다 (2026-08-04).**
  `plot_ratesweep_split.py`·`exp38_policy_compare.py`가 디렉토리 이름의 `_rpm_(\d+)`로
  조건을 찾는데, 한 시간 동적 trace 디렉토리에는 그게 없다. 앞의 것은 `ValueError`로 죽고
  뒤의 것은 **아무 말 없이 빈 결과를 낸다.** 그래서 EXP-41 이후 모든 한 시간 trace가
  **엔진 레이어 그림 없이** 기록됐다. 셋 다 고쳤다(rate 파싱 실패 시 디렉토리 이름을 태그로).
  → **그림을 만든 뒤 arm 이름과 점 개수가 의도대로인지 확인한다**(exp-plot 스킬의 마지막 절).
- **끝난 Job이 k8s에 남는다.** `kubectl delete job`을 안 하면 `Complete` 상태로 계속
  조회된다. 연쇄 스크립트에서 "앞 실험이 끝났나"를 `kubectl get jobs | grep -q
  "bench-runner-exp"`로 물으면 **8일 전 끝난 `bench-runner-exp13-sweep`에 걸려 영원히
  기다린다.** 2026-07-29에 EXP-34가 이것으로 35분 대기만 하다 아무것도 안 돌았다.
  대기 조건은 **그 스크립트가 만드는 Job 이름만** 매칭하도록 좁힌다
  (`bench-runner-exp(27|30|31)`). 결과가 오염되지는 않는다 — 아무것도 실행되지 않을 뿐이다.
- **`pkill -f <이름>`은 자기를 실행한 셸도 죽인다.** 그 셸의 명령줄에 같은 문자열이 들어
  있기 때문이다. `pkill -f "exp34_flux[c]ount"`처럼 대괄호로 자기 매칭을 피한다.
- **같은 입력이어도 출력 토큰 수는 run마다 달라진다.** seed를 고정해도 그렇다.
  `(task_id, iteration)`으로 매칭한 9,660건에서 **입력은 100% 동일한데 출력은 65.7%만
  동일**했다(chat 72.4 / dr 44.2 / swe 40.9%). 연속 배칭에서 같은 요청이 매번 다른 배치
  구성으로 계산되어 부동소수점 reduction 순서가 바뀌고, 확률이 비슷한 토큰에서 선택이
  뒤집히기 때문이다. **배치 서빙의 본질적 성질이며 설정 결함이 아니다.** 차이는 대개
  작지만(|차이| p50 = 0~4 토큰) 꼬리가 있다(p90 28~122, 최대 2,904).
  → **조건당 1회 측정으로 판정하지 않는 이유가 하나 더 있다**: 도착 순서뿐 아니라 출력
  길이 자체가 run마다 다르다. E2E 예산을 쓰는 swe가 가장 크게 흔들린다.
- **러너의 `/work`는 이 실험 저장소를 hostPath로 마운트한다.** 조건마다 새 Job이 소스를
  다시 읽으므로, 실험이 도는 동안 `workloads/`나 `run_experiment.py`를 고치면 **조건마다
  다른 코드로 측정된다.** `bin/`의 바이너리에 적용하던 규칙이 워크로드 소스에도 똑같이
  적용된다. `analysis_scripts/`는 측정 경로가 아니므로 고쳐도 안전하다.
- **클라이언트가 기록한 지표를 엔진·스케줄러가 보고하는 값과 대조한다.**
  `tbt_mean_ms`가 실제 토큰당 시간의 **1/1.92**로 기록되고 있었다(§32). 청크를 문맥에서
  떼어 따로 토큰화한 개수로 나누기 때문이다. 스케줄러의 `observed_step_ms`(50.5ms)와
  모델의 `predicted_step_ms`(50.2ms)는 서로 맞았고 클라이언트 값(26.1ms)만 이상치였다.
  **두 계층이 같은 이름으로 다른 양을 부르고 있는지 먼저 확인한다.** 분석은
  `(e2e − ttft)/(output_tokens − 1)`로 고쳤고 `FS_LEGACY_TBT=1`로 과거 수치를 재현한다.
- **한 시간짜리 trace에서는 `(task_id, call_index)`가 요청을 식별하지 못한다.** 같은
  task가 수백 번 replay되므로 `request_engine.csv` 106,116행 중 **95,558행이 그 쌍을
  공유한다.** 그 쌍으로만 조인하면 행이 10배로 불어난다(179,418 → 1,240,040).
  `start_time`을 2초 nearest로 붙여 구분하고, **거절된 요청은 dispatch 기록이 없어
  이웃의 id에 잘못 붙으므로 조인 왼쪽에서 빼야 한다.** 그렇게 하면 admitted 전량이
  100% 매칭된다(`exp41_engine_view.py:attribute_engines`). 8분짜리 정적 조건에서는
  중복이 없어 이 함정이 안 보인다.
- 선재 문제: `pkg/cms/cms_read_client_test.go`가 `NewCMSReadClient` 인자 개수 불일치로
  `go vet ./...`을 실패시킨다. 이 저장소 작업과 무관하며 미수정 상태다. 빌드/테스트는
  패키지를 지정해서 돌린다(`go test ./pkg/scheduler/...`).

## 작업 규칙

- **사용자의 명시적 지시 없이는 어떤 파일도 삭제하지 않는다.** 실험 결과 디렉토리,
  소스, 산출물, 중간 파일, 로그, 폐기된 것처럼 보이는 run 전부 해당한다. "이건 버려도
  될 것 같다"는 판단으로 지우지 않고, 지워야 한다고 생각되면 **먼저 묻는다.** 덮어쓰기와
  `git clean`, `rm -rf`, 이름이 겹치는 출력 디렉토리에 쓰는 것도 같은 규칙을 따른다.
  (2026-07-29에 폐기 판정한 결과 디렉토리 11개를 삭제한 적이 있는데, 그때는 사용자가
  "버려야 하는 저 중간것들은 그냥 버려"라고 명시적으로 지시했다. 그런 문장이 없으면
  삭제하지 않는다.)
- 큰 작업은 별도 브랜치에서 한다.
- 실험 기록은 `Agent_applications/.../experiments/EXP-NN_*.md`에 **실행 전/중**에 쓴다.
- 분석 스크립트 수정 후에는 `python -m py_compile`로 문법 확인.
