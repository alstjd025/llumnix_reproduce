# EXP-56 — turning the class preference off: is a fully mixed fleet worse?

## 1. Why

The system's headline claim has two halves and only one is measured.

**A static partition is worse** — established. PolyServe carries a 48x imbalance
between its busiest and least busy engine, runs chat at 96.4 and 101.0 ms per
token against a 50 ms budget on two engines while a third sits at batch 42, and
returns 3,192 goodput tokens per second against FluidServe's 17,967 (EXP-54).

**A fully mixed fleet is worse** — **not established.** Llumnix SLO mixes evenly
(each class 25–27% concentrated, against 25% for a perfectly even spread) and
scores 39.0 against 69.6, but it differs from FluidServe in several other ways at
once: no per-request conversion of the budget into a per-token allowance, a
different admission rule, no holding at the gateway.

`--fluidserve-enable-affinity=false` isolates exactly one difference. It removes
both places a class preference acts — the ordering of the feasible set by
`classShare`, and the class term in the damage estimate — and the ordering falls
back to free space, which is a load-balancing rule. Everything else is identical:
same binary, same budgets, same capacity model, same admission ladder.

EXP-54 could not answer this because **both of its repeats separated.** The
concentration of deep research on one engine reached 99–100% in the early windows
of both, so there was no unseparated run to compare against.

## 2. What the answer decides

Section 58.3 of `fluidserve-implementation.md` attributes the score to the
separation:

> chat만 남은 세 엔진에서 chat의 토큰당 시간이 40~42 ms로 예산 안이다.
> **분리가 chat 페이스를 예산 안에 두는 기전이다.**

Section 3 of `fluidserve-how-it-works.md` §6 gives a different mechanism, from
the gate arithmetic:

```
chat이 있는 엔진:  게이트 = min(50, 61.9, 100) × 0.9 = 45.0 ms
chat이 없는 엔진:  게이트 = min(61.9, 100)      × 0.9 = 55.7 ms
```

and the fleet at saturation delivers 55.6 ms. Under that reading the separation
is not about a homogeneous batch being faster; it is about **keeping a place
where loose-budget work is admissible at all.**

The two readings predict different things when the preference is removed, and
the experiment is designed to tell them apart.

## 3. Hypotheses and judgement rules — written before the run

### H1. A fully mixed fleet is worse

**Prediction**: at 45 req/s the ablated arm loses **5 points or more** of offered
attainment against the paired baseline in the same session.

**Refutation**: a loss under 2 points, or a gain. That result would say the class
preference is worth nothing on this workload, and §6 of the how-it-works document
would have to be rewritten — the separation would be an artefact rather than a
mechanism.

The threshold is 5 because the within-session repeat spread at 45 req/s is up to
4.2 points on this workload, and a difference has to clear that to be read.

### H2. The mechanism is the gate, not batch homogeneity

**Prediction if the gate reading is right**: with the preference off,
`gate_allowance_ms` reads 50.0 on all four engines essentially all the time (the
"chat-free engine" fraction goes to near zero), and the route share falls. Chat's
per-token time need not move much.

**Prediction if the homogeneity reading is right**: the gate readings are similar
to the baseline and **chat's per-token time rises** while the route share does not
collapse.

**Neither**: the score falls without either signature. That would mean the
mechanism is not understood, and the next step is instrumentation rather than
another arm.

These are pre-registered as mutually exclusive so the result cannot be read both
ways afterwards.

### H3. The separation is what makes the 45 req/s outcome bistable

**Prediction**: with the preference off there is **no bimodality** — the three
repeats at 45 req/s land close together, because the positive feedback that
amplifies an arbitrary initial imbalance is gone. The mean may be low.

**Refutation**: the ablated arm is itself bimodal. That would locate the
bistability somewhere other than the class preference.

## 4. Design

| | |
|---|---|
| arms | `fluidserve` (baseline, v0.1.1 defaults) and `fsnoaff` (`--fluidserve-enable-affinity=false`), **interleaved in one session** so the pair shares any session offset |
| static | 45 and 55 req/s, **3 repeats**, 8 minutes per condition, engine cold-restarted per condition. 12 conditions |
| dynamic | `dyn60_short_m123`, one hour, **1 repeat per arm**. 2 conditions |
| fixed | migration off, stock vLLM FIFO engine, no engine-side admission, KV admission threshold 0 |
| binary | `f88e9430f21dc74a410a7091ebdea218` — the same one EXP-53 and EXP-54 ran |
| ablations pinned | `FS_CLASS_HARM=false` in both arms. Not because false is the intended setting but because every FluidServe condition it is being read against ran that way; turning it on is EXP-43 |

Rates 45 and 55 are chosen because the gate arithmetic binds in a narrow band:
below it the 45 ms gate passes anyway and above it nothing passes, so an effect
that exists only in the band would be invisible at 20 or 80. EXP-54's minute-level
series puts that band around a delivered pace of 45–56 ms, which is 45–60 req/s.

Estimated 5 hours.

## 5. What is being measured

Beyond the standard set:

- `scheduler_fluidserve_gate_allowance_ms` per instance — the fraction of samples
  above 50.5 ms is "an engine with no chat resident", which is the state variable
  section 52 found predicts the 45 req/s outcome 24 times out of 24.
- decision mix (route / pend / shed / force).
- per-window class concentration **and which engine holds it** — pooled over a
  run this statistic cannot see a moving assignment (§59.3).
- chat's per-token time per engine.
- preemption per engine and fleet total.

## 6. Result (2026-08-05, 12/12 static conditions; the hour trace is 1/2)

판독기: `analysis_scripts/request_level/exp56_affinity.py`. 표는
`results/aggregate_analysis/exp56/exp56_conditions.csv`.

> **⚠ 정정 (2026-08-05 10:50).** 처음에 "`fsnoaff/full`이 시작 직후 **0행**으로 죽었다"고
> 적었다. **틀렸다.** 그 run은 요청 **292,854건을 51.3분에 걸쳐 만들었고**, 죽은 것은
> 마지막 **병합 단계**다 — 워커별 shard가 `shards/`에 그대로 있었는데 `metrics.csv`가
> 헤더만 남아 있어서 "아무것도 안 만든 run"으로 읽혔다.
> `analysis_scripts/recover_unmerged_shards.py`로 복구했다.
> **판정을 바꾸는 정정이다** — §6.7에 한 시간 비교가 새로 들어가고, 그 결과가 정적
> 조건과 크게 다르다.
> **일반 규칙**: `metrics.csv`가 헤더만 있는 run을 실패로 기록하기 전에 `shards/`를 연다.

### 6.1 조건별 (정적, 3반복 × 2 rate × 2 arm)

| rate | rep | arm | offered | admitted | 거절% | goodput | chat ITL | chat 없는 엔진% | route% |
|---|---|---|---|---|---|---|---|---|---|
| 45 | 1 | FluidServe | **98.9** | 99.7 | 0.8 | 20,099 | 37.2 | 27.9 | 90.1 |
| 45 | 1 | 선호 off | 86.7 | 96.3 | 9.8 | 17,889 | 44.1 | 0.5 | 10.8 |
| 45 | 2 | FluidServe | **93.4** | 98.5 | 5.2 | 19,233 | 38.6 | 21.6 | 39.1 |
| 45 | 2 | 선호 off | 86.5 | 96.1 | 9.8 | 17,911 | 44.0 | 0.2 | 10.4 |
| 45 | 3 | FluidServe | **90.4** | 97.5 | 7.1 | 18,686 | 42.4 | 2.7 | 14.7 |
| 45 | 3 | 선호 off | 87.0 | 96.9 | 10.0 | 17,975 | 44.1 | 0.0 | 10.0 |
| 55 | 1 | FluidServe | **69.8** | 92.2 | 23.9 | 18,108 | 44.8 | 1.6 | 5.9 |
| 55 | 1 | 선호 off | 64.5 | 87.8 | 26.0 | 17,111 | 45.4 | 0.1 | 5.2 |
| 55 | 2 | FluidServe | **67.9** | 91.0 | 24.9 | 17,774 | 44.1 | 1.2 | 6.8 |
| 55 | 2 | 선호 off | 64.7 | 88.8 | 26.6 | 17,086 | 45.1 | 0.0 | 5.4 |
| 55 | 3 | FluidServe | **70.1** | 97.8 | 27.7 | 18,603 | 43.0 | 23.9 | 12.2 |
| 55 | 3 | 선호 off | 64.2 | 86.4 | 25.3 | 16,936 | 45.4 | 0.0 | 5.1 |

### 6.2 H1 — 확인된다 (45 req/s), 55에서는 문턱 아래

| rate | 반복 1 | 반복 2 | 반복 3 | 평균 | 판정 |
|---|---|---|---|---|---|
| 45 | +12.2 | +6.9 | +3.4 | **+7.5** | **확인** (문턱 5.0) |
| 55 | +5.3 | +3.2 | +6.0 | **+4.8** | 문턱 사이 (2.0~5.0) |

**여섯 쌍 전부 기준선이 앞선다.** 55의 평균이 문턱을 0.2점 못 넘었으므로 사전 규칙대로
"확인"이라고 쓰지 않는다 — 다만 **부호가 여섯 번 다 같고 반증 조건(2.0 미만 또는 이득)은
한 번도 만족되지 않았다.**

**그러므로 "완전히 섞인 fleet이 더 나쁘다"는 이제 우리 자신의 ablation으로 뒷받침된다.**
Llumnix SLO를 근거로 들 필요가 없어졌다 — 그 arm은 여러 가지가 동시에 다르다.

### 6.3 기전이 작동했다는 직접 증거 — 선호가 분리를 만들고, 끄면 사라진다

창 60초, 창마다 그 클래스를 가장 많이 든 인스턴스의 점유율, 그 중앙값.
**네 인스턴스에 고르게 퍼지면 25.0%다.**

| rate | arm | chat | deepresearch | swe |
|---|---|---|---|---|
| 45 | FluidServe | **51.5** | **61.2** | **63.3** |
| 45 | 선호 off | 27.4 | 29.4 | 30.5 |
| 55 | FluidServe | **43.5** | **45.1** | **52.5** |
| 55 | 선호 off | 28.9 | 33.3 | 31.5 |

**선호를 끈 arm은 세 클래스 전부 균등 분포에서 3~8점 안이다.** 즉 이 arm은 실제로
부하 균등화처럼 행동하고 있고, 무엇을 껐는지에 대한 의심의 여지가 없다.

### 6.4 H2 — **사전 등록이 틀렸다. 두 신호가 상호배타가 아니다**

사전에 게이트 읽기와 균질성 읽기를 **상호배타로 등록**했다. 둘 다 나왔다.

| | 45 req/s | | 55 req/s | |
|---|---|---|---|---|
| | FluidServe | 선호 off | FluidServe | 선호 off |
| chat 없는 엔진 (샘플 %) | 17.4 | **0.2** | 8.9 | **0.1** |
| route 결정 비율 | 48.0 | **10.4** | 8.3 | **5.2** |
| **← 여기까지 게이트 신호** | | | | |
| chat 토큰당 시간 (ms) | 39.4 | **44.1** | 44.0 | **45.3** |
| **← 이것이 균질성 신호** | | | | |
| incumbents로 막힌 비율 | 1.4 | **5.2** | 3.3 | **7.9** |

**그러므로 "신경 하나"가 아니라 같은 현상의 두 면이다.** 분리가 생기면 동시에 두 가지가
일어난다 — ① chat이 없는 인스턴스가 생겨 거기서는 허용 속도가 chat의 50 ms가 아니라
61.9나 100 ms가 되고, 그래서 예산이 느슨한 클래스가 라우팅 판정을 통과한다(route 48.0
대 10.4), ② chat만 있는 인스턴스에서는 배치에 큰 KV 발자국을 가진 요청이 섞이지 않아
chat의 토큰당 시간이 39.4 ms로 떨어진다(섞이면 44.1).

**사전 등록의 오류는 두 기전이 같은 원인(분리)에서 나온다는 것을 못 본 것이다.**
"둘 중 어느 것이냐"로 물었는데 올바른 질문은 "각각 얼마나 기여하나"였고, 그것은 이
설계로는 못 가른다 — 가르려면 분리를 만들되 게이트만 chat 값으로 고정하는 arm이 필요하다.

**부수 확인**: 선호를 끄면 **incumbents 때문에 막히는 비율이 3.7배 오른다**(1.4 → 5.2).

> ⚠ **2026-08-06 정정.** 처음에 이 줄의 설명을 "선호를 끄는 것은 damage 추정의 클래스 항도
> 같이 제거하므로 예상되는 방향"이라고 적었다. **틀렸고, 두 가지 이유로 틀렸다.**
>
> 1. **그 항은 두 arm 모두에서 이미 꺼져 있었다.** 이 실험의 두 arm은 다른 모든 FluidServe
>    arm과 같이 `FS_CLASS_HARM=false`를 고정한다(§4의 설정 표에 그렇게 적혀 있다). 그러므로
>    `--fluidserve-enable-affinity=false`가 그 항을 제거할 수 있는 상태가 아니었다.
> 2. **그 항은 이 값에 작용할 수 없다.** `harm`은 **아무것도 feasible하지 않을 때 FORCE
>    목적지를 고르는 정렬 기준**이고, "incumbents 때문에 막혔다"는 **feasible 판정 자체가
>    실패한 횟수**다. 두 양은 코드에서 다른 자리에 있다.
>
> **올바른 설명**: 클래스가 섞이면 한 인스턴스 위에 예산이 서로 다른 요청들이 같이 있게
> 되고, 그러면 `overIncumbents`가 보는 "거기 사는 요청 중 가장 빡빡한 허용치"가 더 자주
> 낮아진다. 그래서 새 요청을 넣었을 때의 평균 step 시간이 그 허용치를 넘는 경우가 늘어난다.
> **분리가 그 조건을 완화한다**는 것이 이 값이 말하는 것이고, damage 추정과는 무관하다.
>
> 어떻게 틀릴 수 있었나: 플래그 이름이 두 곳을 동시에 끄도록 되어 있어서 **그 두 곳이 이
> 실험에서 실제로 어떤 상태였는지를 설정 표에서 안 읽고 코드의 의도만 보고 썼다.** 이
> 저장소의 "설정을 바꿨으면 스케줄러가 실제로 읽은 값을 확인한다"가 결과 해석에도 적용된다.

### 6.5 H3 — 확인된다. 선호가 산포를 만든다

| rate | arm | 반복별 offered | 산포 |
|---|---|---|---|
| 45 | FluidServe | 98.9 / 93.4 / 90.4 | **8.5** |
| 45 | 선호 off | 86.7 / 86.5 / 87.0 | **0.6** |
| 55 | FluidServe | 69.8 / 67.9 / 70.1 | **2.2** |
| 55 | 선호 off | 64.5 / 64.7 / 64.2 | **0.5** |

**두 rate 모두 선호를 끄면 반복이 붙는다.** 반증 조건(끈 arm 자체가 이봉)은 만족되지
않았다.

**그리고 무엇이 산포를 만드는지가 보인다.** 45 req/s의 여섯 조건(양 arm)에서 **점수와
"chat이 없는 인스턴스가 존재한 샘플 비율"의 상관이 +0.953**이다. 기준선의 세 반복이
그 축을 따라 늘어선다:

| offered | route% | chat 없는 엔진% | dr 집중도% |
|---|---|---|---|
| 98.9 | 90.1 | 27.9 | 83.6 |
| 93.4 | 39.1 | 21.6 | 59.5 |
| 90.4 | 14.7 | 2.7 | 40.5 |

**즉 45 req/s의 결과는 "두 상태"라기보다 하나의 연속 축 위의 서로 다른 지점이고, 축은
분리가 얼마나 형성됐는가다.** 선호를 끈 arm은 그 축의 바닥(0.0~0.5%)에 고정된다.
⚠ 55 req/s에서는 같은 상관이 +0.638로 약하고, 전 조건을 풀링하면 +0.444다 —
**rate가 지배하므로 이 상관은 rate 안에서만 읽는다.**

### 6.7 한 시간 trace — 이득이 정적 조건의 3분의 1 아래로 줄어든다

> **2026-08-05 22:51 갱신.** 첫 시도의 `fsnoaff/full`은 노드가 DiskPressure에 걸려 51.3분에서
> 잘렸고, **두 arm을 한 세션에서 온전한 61.0분으로 다시 돌렸다**(`exp56h`). 아래는 그 값이고,
> 잘린 쌍은 대체됐다. 잘린 쌍으로 계산한 값은 +1.8이었으므로 **결론은 안 바뀌었다.**

| (61.0분, 한 세션, arm당 1회) | offered | admitted | 거절% | goodput | chat ITL | chat 없는 인스턴스% | route% |
|---|---|---|---|---|---|---|---|
| FluidServe | **69.8** | 95.7 | 27.0 | 17,850 | 41.2 | 24.6 | 19.5 |
| 선호 off | 67.7 | 94.2 | 28.0 | 17,383 | 43.3 | 13.4 | 17.3 |
| **차이** | **+2.1** | +1.5 | −1.0 | +467 | −2.1 | +11.2 | +2.2 |

**FluidServe의 69.8은 EXP-54의 69.6 ±0.3과 맞는다** — 세션이 다른데도 같은 값이 나오므로
이 한 시간 조건은 세션을 잘 건너간다.

> **정적 45 req/s에서 +7.5점이던 것이 움직이는 워크로드 한 시간에서는 +2.1점이다.**

**왜 줄어드는가.** 게이트 산술은 좁은 띠에서만 구속력을 갖는다 — 도착률이 낮으면 어차피
전부 route되고, 아주 높으면 아무것도 안 된다. 정적 45·55 req/s는 **그 띠 안에 놓으려고
고른 값**이다. 한 시간 trace의 도착률은 13.8~73.8 req/s로 오가므로 **띠 밖에서 보내는
시간이 길다.**

같은 것이 계측에도 보인다: 선호를 끈 arm의 "chat 없는 인스턴스" 비율이 정적에서는
0.0~0.5%인데 **한 시간에서는 13.4%**다. 부하가 낮은 구간에서는 상주 요청이 적어 선호가
없어도 우연히 chat 없는 인스턴스가 생긴다. **선호가 만들어 줄 것이 이미 있는 구간에서는
선호가 할 일이 없다.**

⚠ **한 시간은 여전히 arm당 1회다.** 이 조건의 반복 산포는 EXP-41과 EXP-44에서 0.5점 이내로
측정된 적이 있고 위의 69.8 대 69.6도 그것과 맞지만, **+2.1을 확립된 값으로 쓰려면 반복이
더 필요하다.**

### 6.8 그래서 H1의 최종 형태

**"완전히 섞인 fleet이 더 나쁘다"는 성립하되, 크기가 운전 구간에 강하게 의존한다.**

| 조건 | 이득 | 반복 |
|---|---|---|
| 정적 45 req/s | **+7.5** | 3 |
| 정적 55 req/s | +4.8 | 3 |
| 한 시간 동적 (61.0분) | **+2.1** | **1** |

논문에 쓸 때 **정적 값만 인용하면 과대 진술이다.** 세 줄을 같이 적고, 게이트 산술이
구속력을 갖는 띠가 좁다는 것을 기전으로 함께 쓴다.

### 6.9 이 실험이 답하지 않은 것

- **한 시간 반복이 1회다.** 61.0분 온전한 쌍으로 다시 돌렸으므로 잘림 문제는 닫혔고,
  남은 것은 반복 수뿐이다.
- **§58.3의 인과 주장**("분리가 chat 페이스를 예산 안에 두는 기전이다")은 이제 **정적
  조건에서 뒷받침된다** — 분리를 끄면 chat 토큰당 시간이 39.4 → 44.1 ms로 오르고, 한
  시간에서도 40.4 → 42.9로 같은 방향이다. 다만 그것이 **점수 이득의 얼마를 설명하는지**는
  §6.4의 이유로 못 가른다.
- 두 기전의 기여도 분해에는 arm 하나가 더 필요하다(분리는 유지하되 게이트를 chat 값으로
  고정).

## 7. Note — a false alarm that was raised and withdrawn before this ran

While preparing this experiment the scheduler's start-up line was found to report
`classharm=false` in all 50 FluidServe conditions of EXP-51 through EXP-54, while
the source default, the deployed binary's own `--help`, the deploy script's own
output and the running pod's arguments all said `true`. It was recorded as an
unexplained contradiction that might mean the start-up line could not be trusted.

**It was none of those.** The driver pins `FS_CLASS_HARM=false` on every FluidServe
arm, and the comment above that line says why: every FluidServe condition from
2026-07-28 through EXP-46 ran with it false because the flag stuck in the
deployment spec, so the baseline is pinned to false to stay comparable with
everything recorded against it. Passing the variable explicitly in both
directions was tested here and the start-up line follows it exactly.

**What was missed is written in CLAUDE.md already**: the measurement path is not
only the binary and the workload but everything a running sweep calls, and the
driver's arm definitions are part of it. Four sources were checked and the one
that sets the value was not.
