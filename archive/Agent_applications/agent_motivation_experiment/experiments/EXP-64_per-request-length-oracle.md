# EXP-64 — what would per-request length knowledge buy?

Written before the run. 2026-08-07 01:50 KST.

## 1. The question

**We already predict; the question is the granularity.** Reading a class's
output-length distribution off past traffic and using it for the next request is
a prediction — it assumes the future looks like the past. What distinguishes us
from the systems we are compared against is not that they predict and we do not,
it is **how finely**: ours is conditioned on the class, Scorpio's on a 100-bin
classifier over the request, AdaGen's on a DistillBERT regression at 76.4%
accuracy, and SLOs-Serve assumes the decode length is known exactly.

*(This paragraph was rewritten on 2026-08-07. It previously said we have no
predictor, which is wrong and would have been read as a claim that we use no
historical information at all.)*

So a reviewer can ask two things and we can answer neither today:

1. **Would a finer-grained predictor help us?** If it would help a lot, the
   design is leaving something on the table and the paper should say so.
2. **Is our class-conditional estimate the reason we win?** If per-request truth
   adds nothing, then the answer is no, and the comparison against systems with
   finer predictors is not an information-advantage comparison.

**Building a predictor answers neither cleanly**, because a negative result would
be attributable to the predictor being bad. **Giving the policy the answer does.**
Whatever the gain from near-perfect per-request length knowledge turns out to be,
no predictor can beat it, so it is an upper bound on the whole question.

EXP-63 was built to measure the cost of the class-level estimate being *biased*
and did not manage it: scaling one class also re-weights the classes against each
other, so what moved was separation rather than accuracy (§65.7). Its replacement,
scaling all three classes together, is deferred. This experiment measures the
other half — the value of removing the estimate's *per-request variance* — and
carries the same hazard, which is why H3 below is a validity condition.

## 2. What "oracle" means here, exactly

The hint is **the output length the same request produced in an earlier run of
the same condition**, joined on `(task_id, call_index)`.

**It is not perfect and the paper must say by how much.** The same input
produces a different output length between runs even with a fixed seed, because
continuous batching changes the reduction order and flips near-ties: over 9,660
matched requests the inputs were 100% identical and the outputs were identical
**65.7%** of the time (chat 72.4 / deepresearch 44.2 / swe 40.9), with
`|difference|` p50 of 0 to 4 tokens and p90 of 28 to 122
(`fluidserve-implementation.md`, trap D).

So this is **a predictor about as accurate as a predictor could plausibly be**,
not an oracle in the strict sense. That is the honest description and it is also
the more useful one: it bounds what a real predictor could achieve.

**`(task_id, call_index)` is unique only in the eight-minute static conditions.**
On the one-hour trace 95,558 of 106,116 rows share that pair because tasks are
replayed hundreds of times, so this experiment is static-only.

## 3. How the hint reaches the policy — and why the workload does not change

The OpenAI completion API's **`user`** field is a free-form string that the
gateway already declares and re-marshals, and that vLLM accepts and ignores for
generation. The client packs `len:<tokens>` into it.

**This matters more than it sounds.** The obvious alternative, `max_tokens`,
would truncate generation, which changes the workload and makes the hint an
upper bound rather than a value. With `user` the two arms emit **byte-identical
requests** and differ only in whether the scheduler reads the field, so the
comparison has no workload confound at all.

| component | change |
|---|---|
| `pkg/types/scheduling_request.go` | `PredictedOutputTokens int` and a parser for the `len:<n>` encoding, rejecting anything else rather than guessing |
| `pkg/gateway/load-balancer/scheduler_client.go` | populate it from `cr.User` |
| `cmd/config/config.go` | `--fluidserve-oracle-length`, default **false** |
| `pkg/scheduler/policy/fluidserve.go` | when the flag is on and the request carries a positive hint, the request's expected remaining tokens and completion probability come from the hint instead of the class distribution — **for resident requests as well as the arriving one**, since the feasibility test is about what the incumbents still have to produce |
| workload | write `user="len:<n>"` from a lookup table built from a prior run |
| analysis | `ms_dev/scripts/build_length_oracle.py` builds the table from a result directory |

**Default off means the control arm is the same policy every experiment since
EXP-27 measured**, and a request without a parseable hint falls back to the class
distribution, so a missing table entry degrades to today's behaviour rather than
to zero.

## 4. Design

| | |
|---|---|
| rates | 45 and 60 req/s |
| arms | `fluidserve` (control, class distribution) and `fsoracle` (per-request hint) |
| repeats | 2 |
| conditions | 8, eight minutes each |
| mix | m1, stock engine ordering, migration off |
| oracle source | a prior `fluidserve` run at the same rate and mix, named in the experiment record |
| coverage check | the fraction of requests that carried a parseable hint, printed per condition; below 95% the condition is invalid |

Both arms send the `user` field. Only the flag differs. That way a mistake in
building the table cannot make the two arms differ in what they sent.

## 5. Hypotheses and judgement rules

### H1 — the gain is small

**Prediction**: `fsoracle` is within **3 points** of the control at both rates.

**Grounds**: the policy uses the length estimate through `E[L−j | L>j]`
multiplied by a pace, and the classes are chosen so that the within-class spread
is much smaller than the between-class spread (chat p50 389 / p90 766,
deepresearch p50 973 / p90 1246). If that is right, the class label already
carries most of the information and per-request truth adds little.

**Refutation, and the more consequential outcome**: the gain exceeds **5 points**
at either rate. Then per-request length prediction is a real lever we are not
pulling, the comparison against Scorpio and AdaGen becomes an
information-disadvantage comparison rather than the reverse, and the paper has to
say so. **Record it as a finding, not a failure** — it would point at a concrete
next version of the design.

### H2 — if there is a gain, it is concentrated in the class with the widest spread

**Prediction**: whatever gain appears, deepresearch shows more of it than chat,
because its p90/p50 ratio is the one the class mean serves worst in absolute
tokens.

### H3 — the mechanism is admission, not placement. **This is now a validity condition, not a hypothesis**

**EXP-63 showed why.** Scaling one class's length distribution moved offered
attainment by up to 5.6 points, and the movement was not accuracy at all: the
length profile is an input to "how loaded is this instance" as well as to "how
much more will this request produce", so inflating one class's lengths protects
the instances holding it and changes how strongly classes separate. Effective
instances per class tracked the scale factor at −0.897 and the score tracked
separation at −0.955 while demand did not move (§65.6, §65.7).

**Per-request truth does the same thing at request granularity.** A deepresearch
request whose real output is 1,400 tokens rather than the class mean of 985 makes
its instance look busier; one at 600 makes it look freer. So a gain here could be
accuracy or could be a separation shift, exactly as in EXP-63.

**Prediction, and the condition the run must satisfy to mean anything**: the
rejection rate moves and **effective instances per class move by less than 0.3**
between the two arms.

**If separation moves by 0.3 or more, this experiment did not measure accuracy
either**, and the answer needs the code split instead — a different length source
for `overIncumbents` than for the request's own feasibility test. Record that
outcome as a finding about the design rather than as a result about prediction.

**Prediction**: the rejection rate changes more than the per-engine class
composition. A better length estimate sharpens the feasibility test, which
decides whether a request can be served at all; it does not change which
instances a class prefers.

**Refutation**: the effective instances per class move by more than 0.3 while the
rejection rate does not move. That would mean the hint is acting somewhere other
than where it was wired in and the wiring must be re-read.

## 6. What this cannot answer

- **It does not deliver a predictor.** A deployed system would have to predict
  the length from the prompt, and the accuracy achievable there is 76.4% in
  AdaGen's measurement, well below the 65.7%-exact / few-token-error hint used
  here. **The result bounds what finer granularity is worth; it does not build
  it.**
- **It may not isolate accuracy**, for the reason in H3. The separation numbers
  have to be read before the score is interpreted.
- **It says nothing about the profile being biased**, which is EXP-63.
- **Static only**, for the join-key reason in §2.

## 7. 실행 전 갱신 — 무엇이 아직 맞고 무엇이 낡았나 (2026-08-11 확인)

**이 문서는 2026-08-07에 쓰였고 아직 한 번도 안 돌았다.** 그 사이에 워크로드가 바뀌었고(2026-08-08
부하 생성기 수정) 실험이 여덟 개 더 끝났다. **결과를 보고 규칙을 고치는 것이 아니라, 결과가 없는
상태에서 전제를 다시 확인한 것이다.**

### 7.1 아직 맞는 것

**전제(클래스 안의 퍼짐이 클래스 사이의 퍼짐보다 작다)가 수정 후 워크로드에서도 그대로다.**
§5가 근거로 든 값과 오늘 잰 값이 거의 같다(EXP-73의 `fspfx` @ 35 req/s, 2반복):

| class | §5가 인용한 값 | 2026-08-11 실측 | p90/p50 |
|---|---|---|---|
| chat | p50 389 / p90 766 | **p50 384 / p90 757** | 1.97 |
| deepresearch | p50 973 / p90 1246 | **p50 970 / p90 1219** | 1.26 |
| swe | — | p50 492 / p90 629 | 1.28 |

**`user` 필드로 힌트를 넣는 방법도 그대로 유효하다** — 두 arm이 바이트 단위로 같은 요청을 보내고
스케줄러가 그 필드를 읽는지만 다르므로 워크로드 교란이 없다.

**H3(분리가 움직이면 정확도를 잰 것이 아니다)은 여전히 유효 조건이다.** EXP-63이 그것 때문에
재려던 것을 못 쟀다.

### 7.2 ⚠ 오라클이 §2가 적은 것보다 정확하다 — 다시 쟀다

§2는 **65.7%가 완전히 같다**고 적었는데 그것은 **수정 이전 워크로드**의 값이다(함정 D).
수정 후 워크로드에서 같은 방식으로 다시 쟀다(EXP-73의 `fspfx` 두 반복을 `(task_id, call_index)`로
맞춰 붙임):

| | 짝지은 요청 | **완전히 같음** | \|차이\| p50 | p90 | 최대 |
|---|---|---|---|---|---|
| 25 req/s 전체 | 11,147 | **92.1%** | 0 | 0 | 623 |
| 　chat | 8,808 | 94.1% | 0 | 0 | 623 |
| 　deepresearch | 1,684 | 85.0% | 0 | 27 | 495 |
| 　swe | 655 | 84.7% | 0 | 16 | 509 |
| 35 req/s 전체 | 11,539 | **94.0%** | 0 | 0 | 905 |

**65.7% → 92~94%다.** 힌트가 예상보다 참값에 가깝고, **상한으로서 더 강하다** — "이보다 정확한
예측기는 없다"고 말할 때 그 상한이 더 높은 자리에 있다. §2의 65.7%는 **인용하면 안 된다.**

### 7.3 고쳐야 하는 설계 넷

**① 도착률 45와 60 → 25와 35로 바꾼다.** 60은 지금 격자(10·15·20·25·35·45·55·70)에 없고,
무릎이 28.1 req/s로 내려왔다. **그리고 EXP-78이 admission의 몫을 rate별로 쟀는데 45에서
0.8점이다**(모든 도착 분모). **H3이 "이 힌트는 admission을 통해 작용한다"고 말하므로, 45에서
재면 힌트가 작용할 자리가 거의 없는 곳에서 재게 된다.** 25(admission이 18.1점)와 35(4.4점)가
맞다.

**② 대조군 arm 이름이 바뀌었다.** §4가 `fluidserve`를 대조군으로 적었는데 **그 이름은 지금
prefix 회계를 끈 ablation**이다. 배포 기본 구성은 **`fspfx`**이고 대조군은 그것이어야 한다.

**③ H1의 "3점 이내"가 어느 분모인지 적어야 한다.** EXP-78 §7.7이 거절을 켜고 끄는 비교에서
분모가 결론을 바꾼다는 것을 보였다. 이 실험은 두 arm 다 거절을 켜므로 그 문제가 작지만,
**힌트가 거절률을 움직이는 것이 H3의 예측**이므로 **모든 도착 분모와 받아들인 것 분모를 둘 다
낸다.**

**④ 오라클 표의 출처**를 수정 후 run으로 정한다: **EXP-73의 `fspfx` @ 25·35 req/s**(2반복,
같은 arm·같은 mix·같은 바이너리 계열). §4가 "a prior `fluidserve` run"이라고 적은 것을 이것으로
바꾼다.

### 7.4 구현이 하나도 안 돼 있다

§3의 여섯 항목 중 **어느 것도 코드에 없다**(2026-08-11 확인: `--fluidserve-oracle-length`,
`PredictedOutputTokens`, `build_length_oracle.py`, 워크로드의 `user="len:<n>"` 전부 없음).

| 무엇 | 어디 | 크기 |
|---|---|---|
| `PredictedOutputTokens` + `len:<n>` 파서 | `pkg/types/scheduling_request.go` | 작음 |
| `cr.User`에서 채우기 | `pkg/gateway/load-balancer/scheduler_client.go` | 작음 |
| `--fluidserve-oracle-length` (기본 false) | `cmd/config/config.go` | 작음 |
| 힌트를 길이 분포 대신 쓰기 (**상주 요청도**) | `pkg/scheduler/policy/fluidserve.go` | **가장 큼** |
| `user="len:<n>"` 써 보내기 | 워크로드 | 중간 |
| 표 생성기 | `ms_dev/scripts/build_length_oracle.py` | 중간 |

**조건은 8개(2 rate × 2 arm × 2반복) 약 1.8시간**이고, 시간은 코드 쪽이 더 든다.

### 7.5 ⚠ 커버리지가 rate에 따라 갈린다 — 35 req/s는 사전 문턱을 못 넘는다 (2026-08-12 측정)

§4가 **"parseable 힌트를 가진 요청의 비율이 95% 미만이면 그 조건은 무효"**로 정해 두었다.
그 값을 실제로 재 보니 두 rate가 갈린다.

| rate | 표 출처 | 표 항목 | **커버리지** | 그 조건의 거절률 |
|---|---|---|---|---|
| 25 req/s | 앞선 run 1개 | 11,802 | **97.9%** | 1.9% |
| | 앞선 run 2개 합집합 | 11,979 | **99.3%** | |
| 35 req/s | 앞선 run 1개 | 13,491 | **80.0%** | 14.6% |
| | 앞선 run 2개 합집합 | 15,855 | **94.0%** | |

**이유는 표의 정의에 있다.** 표는 **출력을 낸 요청만** 담는다 — 거절되거나 실패한 요청은 길이가
없으므로 담을 수 없다. 그래서 **커버리지의 상한이 대략 (1 − 거절률 − 미완료율)**이고, 거절이
14.6%인 35 req/s에서는 한 run으로 80%가 최대다.

> ⚠ **그리고 빠지는 것이 하필 거절된 요청들이다.** 이 실험이 다루는 것이 판정 조건의 정확도인데,
> **판정에서 갈린 요청이 표에서 빠진다.** 즉 힌트가 있는 요청은 앞선 run에서 받아들여진 것들로
> 치우쳐 있고, 이 치우침은 합집합으로 줄일 수는 있어도 없앨 수는 없다.

**저부하 run으로 표를 만드는 길은 막혀 있다 (측정으로 배제).** 10 req/s는 거절이 없으니 거의
모든 요청의 길이를 담을 것 같지만:

| 저부하(10 req/s) 표를 쓰면 | 25 req/s | 35 req/s |
|---|---|---|
| 커버리지 | 40.3% | **28.8%** |
| 같은 요청의 길이가 완전히 같은 비율 | 62.5% | **62.1%** |
| \|차이\| p90 | 74 | 73 토큰 |

**커버리지가 낮은 이유**는 8분 동안 10 req/s가 만드는 요청이 4,800건뿐이라 35 req/s의 16,900건
대부분을 만난 적이 없어서다. **일치도가 낮은 이유가 더 중요하다** — 같은 부하 안에서는 92~94%가
완전히 같은데 부하가 다르면 62%다. **출력 길이가 부하에 의존한다.** 연속 배칭에서 배치 구성이
바뀌면 근소한 차이의 토큰 선택이 뒤집히는데, 부하가 다르면 배치 구성이 계통적으로 달라진다.

> **그러므로 오라클은 "같은 조건의 앞선 run"에서만 만들 수 있다.** 이것은 이 실험 설계의 제약이
> 아니라 **이 워크로드에서 "요청의 출력 길이"라는 양 자체의 성질**이고, 배포된 시스템이 예측기를
> 학습시킬 때도 같은 제약을 받는다 — **부하가 다른 구간에서 모은 표본은 62%만 맞는다.**

### 7.6 그래서 정하는 것

1. **표는 rate마다 따로, 그 rate의 앞선 run 두 개의 합집합으로 만든다.** 25에서 99.3%,
   35에서 94.0%.
2. **95% 문턱은 그대로 두고, 35 req/s가 그것을 못 넘는다는 사실을 결과와 함께 적는다.**
   문턱을 결과가 안 나온 상태에서 내리는 것은 규칙을 쉽게 만드는 변경이므로 하지 않는다.
   **35의 결과는 "커버리지 94.0%"를 옆에 달고 읽는다** — 힌트가 없는 6%는 대조군과 같은 동작을
   하므로, 그만큼 **처리군이 대조군 쪽으로 끌려간다.** 즉 **35에서 측정되는 이득은 과소평가**다.
3. **판정은 25 req/s에서 한다**(커버리지 99.3%, 그리고 EXP-78이 admission의 몫을 18.1점으로 잰
   자리). 35는 방향 확인용으로 같이 돌린다.

### 7.7 최종 설계 (2026-08-12, 실행 직전)

| | |
|---|---|
| 대조군 | `fspfx` (`oraclelen=false`) |
| 처리군 | `fsoracle` (`oraclelen=true`) — **다른 것은 이 플래그 하나뿐** |
| rate | **25 · 35 req/s** (둘 다 주 실험 격자 위. 무릎 28.1을 감싼다) |
| 반복 | 2 |
| 조건 | **8, 약 1.8시간** |
| 표 | `traces/length_oracle_m1_rpm_{1500,2100}.json`, EXP-73의 `fspfx` 두 반복의 합집합 |
| 커버리지 | 25에서 99.3%, 35에서 94.0% (§7.5) |

**양쪽 arm이 힌트를 똑같이 보낸다.** 워크로드는 표가 있으면 `user="len:<n>"`을 붙이고, 다른 것은
스케줄러가 그 필드를 읽는지뿐이다. 표를 잘못 만들어도 **두 arm이 보낸 것은 같다.**

**판정은 25 req/s에서 한다.** 커버리지가 99.3%이고, EXP-78이 그 도착률에서 admission의 몫을
18.1점으로 쟀으므로 힌트가 작용할 자리가 가장 크다. 35는 방향 확인용이고 커버리지 94.0%를 옆에
적는다.

**주 지표는 EXP-78·EXP-79와 같다**: 모든 도착이 분모, 거절과 미완료를 둘 다 위반. 받아들인 것
분모와 거절률을 같이 낸다. §5의 H1이 정한 3점·5점 문턱은 **이 지표로 읽는다**(§7.3의 ③).

**조건마다 확인하는 것**:
1. 기동 줄의 `oraclelen`이 의도한 값 — 검사기가 대조하고 실패하면 드라이버가 **멈춘다**(2026-08-11에 고쳤다).
2. `[oracle] worker coverage` 줄의 합이 25에서 95% 이상.
3. `client connection exhausted` 0건.

### 7.8 ⚠ 커버리지의 상한이 어디서 오는지 쟀다 — §7.5·§7.6·§7.7의 결론 셋이 바뀐다 (2026-08-12)

§7.5는 표를 **앞선 run 두 개**로 만들고 35 req/s에서 94.0%를 얻었다. 그 수치를 근거로 §7.6이
**"판정은 25에서 한다"**, §7.7이 **"35는 방향 확인용"**으로 정했다. 표를 **그 도착률의 가용 run
전부**로 만들어 다시 재니 세 가지가 달라진다.

**① 8분짜리 run을 25개까지 합쳐도 95.5%에서 멈춘다.** 35 req/s에서 2026-08-08 이후의 모든
8분 조건(`fspfx`·`fsnoshed`·`fsroute`·`fsindep` 여섯 배수·`polyserve`·`slo`·`vllmcache`,
25개)을 합치면 항목이 16,107개이고 도착 16,860건의 **95.5%**다. 25 req/s는 21개 run으로
11,526항목 = **95.6%**다. **길이가 같은 run을 더 넣어도 오르지 않는다** — 이유는 ②에 있고,
**그 이유가 곧 무엇을 넣으면 오르는지도 말해 준다**(⑥).

**② 오르지 않는 이유는 빠지는 것이 "거절된 요청"이 아니라 "마지막 2분에 도착한 요청"이기
때문이다.** 빠진 753건을 도착 시각으로 나누면:

| 도착 시각 | 0~5분 | 6분 | 7분 | **8분** | **9분** |
|---|---|---|---|---|---|
| 표에 있는 비율 | 100.0% | 99.8% | 99.6% | **66.3%** | **8.6%** |

빠진 753건의 **98.3%가 마지막 2분 도착분**이고, 그것들이 그 run에서 어떻게 끝났는지 보면
**620건이 `is_server_terminated`**(부하 창이 닫히면서 스트리밍 중 잘림), 133건이 거절이다.
**도착 순서가 run마다 같으므로 어느 run을 더 넣어도 같은 자리가 잘린다.** 즉 이것은 표본이
모자란 것이 아니라 **8분짜리 조건의 마지막 2분은 어느 run에서도 완결된 길이를 만들지
않는다**는 구조적 성질이다.

**③ 그리고 그 요청들은 점수에 들어가지 않는다.** 처리군 run에서도 같은 자리에서 잘리고,
`attain()`은 미완료를 **양쪽 분모에서 뺀다**(CLAUDE.md의 run-boundary cutoff 규칙).
**채점되는 요청만 놓고 보면 커버리지는 사실상 100%다.**

> **그러므로 §7.5가 걱정한 희석은 일어나지 않는다.** "힌트 없는 6%가 처리군을 대조군 쪽으로
> 끌어당긴다"는 문장은 **그 6%가 채점된다는 전제 위에 있었고, 그 전제가 틀렸다.**

**④ 표의 정확도도 올랐다 — 여러 run의 중앙값을 쓰기 때문이다.** 앞선 run 하나로 만든 표는
같은 요청의 길이를 92~94% 맞혔는데(§7.2), 25개 run의 중앙값을 쓰면 held-out run에 대해
**35 req/s에서 97.2%, 25 req/s에서 95.0%가 완전히 같고 절대오차 중앙값과 p90이 둘 다 0
토큰**이다. 한 run에서 튀는 값이 중앙값에서 지워진다.

**⑤ 클래스별 커버리지는 deepresearch가 가장 낮다** (35 req/s: chat 96.5%, deepresearch
**90.9%**, swe 95.7%). 출력이 가장 긴 클래스라 마지막 2분에 시작한 것이 완결될 확률이 가장
낮다. **H2가 "가장 넓게 퍼진 클래스에 이득이 몰릴 것"이라고 예측한 그 클래스**이므로,
H2를 읽을 때 이 90.9%를 옆에 적는다.

**그래서 바뀌는 것**:

1. **판정을 35 req/s에서 한다.** §7.6이 25로 정한 유일한 이유가 커버리지였는데 두 rate가
   같아졌고(95.5 대 95.6, 채점 대상만 보면 둘 다 사실상 100%), **거절이 실제로 일어나는
   것은 35다**(14.6% 대 1.9%). 25는 같이 돌리고 같이 보고한다.
2. **§4의 "95% 미만이면 무효" 문턱은 두 rate 다 통과한다.** 문턱을 내리지 않았다.
3. **표는 그 도착률의 가용 run 전부의 합집합으로 만들고, 값이 갈리면 중앙값을 쓴다.**
   `build_length_oracle.py`가 여러 run을 받도록 고쳤고, `--coverage-against`로 held-out
   run에 대한 커버리지와 정확도를 같이 낸다.
4. ⚠ **드라이버 `run_exp64_oracle.sh`의 `fsoracle` arm 주석에 적힌 "99.3% / 94.0%"가
   낡았다.** 지금 수집이 도는 중이라 편집하지 않았다(실행 중인 스크립트를 고치지 않는다는
   규칙). **수집이 끝나면 95.6% / 95.5%로 고친다.**

**⑥ 그리고 수집은 정확히 이 상한을 뚫는 방법이었다 — 95.5% → 98.6%.** 수집 run은 같은
35 req/s인데 **16분**이라, 8분 조건의 마지막 2분이 그 run에서는 경계가 아니라 중간이다.
그래서 8분 run에서는 어느 것도 완결시키지 못하는 그 요청들이 16분 run에서는 끝까지 간다.
첫 수집분 하나만 합집합에 더해도:

| 35 req/s, held-out run 하나에 대한 커버리지 | 8분 run 25개 | **+ 16분 수집 1개** |
|---|---|---|
| 전체 도착 | 95.5% | **98.6%** |
| chat | 96.5% | **99.5%** |
| deepresearch | 90.9% | **94.8%** |
| swe | 95.7% | **97.9%** |
| 출력을 낸 요청만 | 96.0% | **99.1%** |
| 완전히 같은 길이를 맞힌 비율 | 97.2% | 97.1% |

**정확도는 그대로이고 커버리지만 올랐다** — 더 넣은 것이 새 요청이지 같은 요청의 다른
관측이 아니기 때문이다. 두 번째 수집분이 들어가면 조금 더 오른다.

> **그래서 §7.5가 "표는 앞선 run 두 개로 만든다"고 정한 것에서 바꿔야 할 것은 개수가
> 아니라 길이였다.** 커버리지를 정하는 것은 합치는 run의 수가 아니라 **그중 가장 긴 run이
> 판정할 조건의 부하 창을 얼마나 넘어가는가**다. 이것은 다른 실험에도 적용된다 —
> **앞선 run으로 표를 만들어 다음 run에 먹이는 설계는 표를 만드는 run이 더 길어야 한다.**

### 7.9 도착률을 넷으로 늘린다 — 그리고 rate마다 커버리지를 따로 재서 통과한 것만 돌린다 (2026-08-12 사용자 지시)

사용자가 **"35 하나만 하기보다 뒤에 rate들도 더"**를 요청했다. 이유가 실험 자체에도 있다 —
**EXP-78이 admission의 값을 받아들인 것 분모에서 +19.9(25 req/s) / +20.3(35) / +33.6(45)로
재서 부하와 함께 커진다는 것을 봤는데**, 길이를 정확히 아는 것이 그 값을 더 키우는지 줄이는지는
도착률 하나로는 답할 수 없다.

**그래서 25 · 35 · 45 · 55 req/s 넷을 돌린다.** 판정은 여전히 35에서 하고(거절 14.6%), 나머지
셋은 곡선을 만든다. **70 req/s는 뺀다** — 그 도착률의 run이 넷뿐이라 표를 만들 표본이 없다.

**rate마다 커버리지를 따로 쟀다** (그 도착률의 8분 run 전부를 합치고, 그중 요청 수가 가장 많은
**정상 길이** run 하나를 빼고 그것에 대해 측정. 정확도는 그 홀드아웃 run의 실제 길이와 표의 값이
완전히 같은 비율):

| 도착률 | 소스 run 수 | **커버리지** | 정확도 | 사전 문턱 95% |
|---|---|---|---|---|
| 25 req/s | 21 | 95.6% | 95.0% | 통과 |
| **35 req/s** | 25 (+16분 수집 1개로 **98.6%**) | 95.5% | 97.2% | 통과 |
| 45 req/s | 20 | 95.2% | 96.9% | 통과 |
| 55 req/s | 9 | **92.9%** | 91.4% | **미달** |

⚠ **홀드아웃을 잘못 고르면 커버리지가 부풀려진다.** 55 req/s를 처음 잴 때 마지막 run을
홀드아웃으로 썼는데 그것이 7.3분(17,884 요청)짜리 짧은 run이었고, 소스는 9분(26,460 요청)이라
**소스가 홀드아웃의 전 구간을 덮어서 99.8%가 나왔다.** 정상 길이 run으로 다시 재니 92.9%다.
**커버리지는 판정할 조건과 같은 길이의 run에 대해 재야 한다.**

**55의 미달도 §7.8과 같은 원인이다** — 도착 분별로 100%(0~5분) → 93.7%(6분) → 58.0%(8분)이고,
빠진 1,873건의 76.4%가 마지막 2분이다. **포화가 심할수록 부하 창이 닫힐 때 아직 안 끝난 요청이
많아서 결손이 더 일찍 시작된다.** 그러므로 대응도 같다: **55 req/s에서 16분짜리 `fsroute` run을
하나 먼저 모은다**(거절이 없어 항목을 지우는 두 번째 경로도 닫힌다).

**체인이 rate마다 스스로 판정한다.** `/home/nxclab/tools/exp64_main.sh`가:
1. 55 수집을 먼저 돌리고,
2. rate마다 **정상 길이 run 하나를 빼고 표를 만들어 커버리지를 재고**,
3. **95% 미만이면 그 rate를 건너뛰고 이유와 수치를 출력하고**,
4. 통과한 rate만 **홀드아웃까지 포함한 표**(측정한 커버리지보다 항목이 많으므로 그 값은 하한)로
   본 실험을 돌린다.

**건너뛰기가 중단보다 나은 이유**: 힌트가 없는 요청은 클래스 분포로 되돌아가고 그것이 대조군의
동작이므로, **커버리지가 낮은 표는 실패하지 않고 처리군을 조용히 대조군으로 만든다.** 그런 rate를
빼고 나머지를 얻는 것이, 전부 멈추는 것보다도 전부 희석된 채 도는 것보다도 낫다.

**조건 수**: 통과하는 rate마다 `fspfx` 대 `fsoracle` × 2반복 = 4조건. 넷 다 통과하면 16조건,
**약 3.3시간**. 반복 1이 모든 rate를 먼저 돌고 반복 2가 시작하므로, 중간에 끊겨도 **전부의 1반복**이
남는다.

#### 7.9.1 ⚠ 커버리지를 재는 홀드아웃은 **판정할 조건과 같은 길이여야 한다** — 양쪽으로 틀렸다

체인의 첫 실행에서 홀드아웃을 **"요청 수가 가장 많은 run"**으로 골랐다. 짧은 run을 고르는 것을
막으려는 규칙이었는데, **16분 수집 run이 생긴 뒤로는 그것이 뽑힌다.** 8분짜리 소스는 16분 run의
뒷부분을 덮을 수 없으므로 커버리지가 실제보다 훨씬 낮게 읽혔고, **체인이 35와 55를 건너뛰었다.**

| 같은 표, 홀드아웃만 다름 | 35 req/s | 55 req/s |
|---|---|---|
| **16분 수집 run**에 대해 (첫 실행) | **88.8%** → 건너뜀 | **52.2%** → 건너뜀 |
| **7.3분짜리 짧은 run**에 대해 | — | **99.8%** (부풀려짐) |
| **8분 정상 run**에 대해 (고친 뒤) | **99.2%** | **96.4%** |

**같은 표의 커버리지가 52.2%에서 99.8%까지 읽힌다.** 홀드아웃이 짧으면 소스가 그 전 구간을
덮어 부풀려지고, 길면 소스가 덮을 수 없는 구간이 생겨 깎인다. **둘 다 표의 성질이 아니라
홀드아웃 선택의 성질이다.**

→ **고친 규칙**: 그 도착률이 8분 동안 만드는 요청 수(`rpm × 8`)의 **±20% 안에 드는 run 중
가장 큰 것**을 홀드아웃으로 쓴다. 잘린 run과 수집 run이 둘 다 빠진다.
→ 조건은 하나도 안 돌았고 표만 다시 만들었으므로 **데이터 오염은 없다.**
→ 이것은 §55(표본 정의가 다른 두 적합을 비교)·§32(같은 이름의 두 양)와 같은 계열이다:
**"커버리지"라는 하나의 이름이 홀드아웃에 따라 다른 양을 가리키고 있었다.**

#### 7.9.2 ⚠ 정확도도 홀드아웃에 따라 크게 흔들린다 — 45 req/s에서 80.0%

같은 45 req/s 표를 `exp74r2_fspfx_m1f`에 대해 재면 **96.9%**가 완전히 같은데,
`exp68sr2_llmdslo_m1f`에 대해 재면 **80.0%**다. 체인이 뽑은 홀드아웃이 후자라서 로그에는
80.0%가 남는다. **커버리지는 두 경우 모두 95.0%로 같다** — 즉 표가 답을 주는 요청 집합은
같은데 그 답이 맞는 비율만 다르다.

이것이 사실이면 **§7.2의 "arm이 달라도 길이는 같다(94.1~95.0%)"가 부하가 높을 때는 약해진다**는
뜻이고, 그렇다면 **표를 여러 정책의 run으로 만드는 것 자체에 대가가 있다.** 지금은 관찰이고
측정이 아니다 — 홀드아웃 하나씩의 비교라 llm-d라서 다른 것인지 그 run이 특이한 것인지 가릴 수
없다. **본 실험이 끝난 뒤 45 req/s에서 arm별로 길이 일치도를 다시 재서 갈라야 한다.**
⚠ **판정에 쓰는 35 req/s에서는 96.9%이고 문제가 없다.**

## 8. Result

> **한 줄**: 요청마다 출력 길이를 정확히 알려 줘도 **네 도착률 전부에서 차이가 사전 문턱 3점
> 안이고 부호도 일정하지 않다.** 판정 도착률 35 req/s에서 **−0.2점**(반복 폭 3.7). **H1 확인.**

### 8.1 결과 (2026-08-12 05:39, 16조건 = 4 도착률 × 2 arm × 2반복)

주 지표는 사전에 정한 **모든 도착이 분모, 거절과 미완료를 둘 다 위반**. 괄호는 두 반복의 폭
(반복이 둘이므로 이것은 두 값의 차이이지 분산의 추정이 아니고, **바닥**으로만 쓴다).

| 도착률 | | `fspfx` 대조 | `fsoracle` 처리 | 차이 |
|---|---|---|---|---|
| 25 | 모든 도착 | 94.0 (0.2) | 93.7 (0.1) | **−0.3** |
| | 받아들인 것 | 98.1 | 97.7 | −0.4 |
| | 거절 / goodput | 2.0% / 11,856 | 2.0% / 11,843 | |
| **35 (판정)** | 모든 도착 | **76.5 (1.8)** | **76.3 (3.7)** | **−0.2** |
| | 받아들인 것 | 96.1 | 95.0 | −1.1 |
| | 거절 / goodput | 18.5% / 13,189 | 17.7% / 13,165 | |
| 45 | 모든 도착 | 57.6 (1.1) | 58.2 (3.1) | **+0.6** |
| | 받아들인 것 | 92.3 | 93.5 | +1.2 |
| | 거절 / goodput | 35.9% / 12,818 | 35.8% / 12,965 | |
| 55 | 모든 도착 | 47.5 (0.9) | 45.5 (4.7) | **−2.0** |
| | 받아들인 것 | 91.0 | 91.8 | +0.8 |
| | 거절 / goodput | 46.1% / 13,162 | 48.9% / 12,804 | |

**사전 규칙(§5): 3점 이내면 H1 확인, 5점 넘으면 반증.** 네 도착률의 차이가 −0.3 / −0.2 / +0.6 /
−2.0으로 **전부 3점 안이고, 부호가 두 번 뒤집힌다.** 판정 도착률에서는 차이 −0.2가 처리군 자신의
반복 폭 3.7의 **5%**다. **H1 확인.**

**유효성 검사 통과**: 판정 도착률에서 실제 커버리지 **99.2%**(chat 99.8 / deepresearch 96.3 /
swe 98.5), 두 arm 동일. 러너가 `[oracle] loaded 30,853 entries`를 찍었고, 표를 못 읽으면
워크로드가 종료하므로 **조용히 대조군이 되는 경로는 막혀 있다.**

### 8.2 이것이 논문에서 하는 일

**요청 단위 길이 예측이 이 설계에 더해 줄 것이 없다는 것을 상한으로 보였다.** 예측기를 만들어
비교했다면 나쁜 결과가 예측기 탓인지 알 수 없었을 텐데, 정답을 주고도 차이가 없으므로 **어떤
예측기도 이 축에서 얻을 것이 없다.**

→ **심사에서 나올 반론 하나가 닫힌다**: Scorpio·AdaGen처럼 요청마다 출력 길이를 예측하는
시스템과 비교할 때 **"우리는 클래스 분포 하나만 쓰므로 정보에서 불리한 비교였다"**고 말할 수
없다. 정보를 완전하게 줘도 결과가 같다.
→ **뒤집어 말하면 이 워크로드에서 admission을 정하는 것은 요청 하나의 길이가 아니라
인스턴스 위의 집합**이다. 클래스 분포 하나로도 그 집합의 총량은 충분히 맞는다.

⚠ **이 문장의 범위**: 이 워크로드(chat 77.0% / deepresearch 15.4% / swe 7.7%)와 이 설계
(FluidServe v0.2의 판정 조건)에 대한 것이다. 길이 분산이 더 크거나 클래스가 덜 갈리는
워크로드에서는 다시 재야 한다.

### 8.3 H2는 확인되지 않았다 — 이득이 어느 클래스에도 몰리지 않는다

H2는 **"이득이 있다면 길이 분포가 가장 넓은 클래스(deepresearch)에 몰릴 것"**이라고 예측했다.
클래스별 주 지표(2반복 평균, 처리군 − 대조군):

| 도착률 | chat | deepresearch | swe |
|---|---|---|---|
| 25 | −0.6 | −0.7 | **+3.4** |
| 35 | −0.8 | +1.3 | **+3.6** |
| 45 | +0.7 | +0.1 | −0.3 |
| 55 | −3.6 | **+4.3** | +0.4 |

**deepresearch에 몰리지 않는다** — 25에서 −0.7, 45에서 +0.1이다. 일관된 것이 하나 있는데
**H2가 지목한 클래스가 아니라 swe**다: 낮은 두 도착률에서 +3.4와 +3.6이고, 같은 자리에서
**swe 거절률이 17.8 → 16.7%와 66.5 → 58.8%로 내려간다.**

**그런데 그것이 총계에 남지 않는다.** swe는 요청의 7.7%이고 chat이 77.0%라서, swe가 3.6점
오르는 동안 chat이 0.8점 내려가면 총계는 −0.2가 된다. **클래스 균등 평균으로 보면 다른 답이
나오는 자리이고, 이 저장소의 헤드라인은 요청 단위이므로 위 표가 정본이다.**

⚠ **swe의 개선도 확정이 아니다** — 클래스별 반복 폭을 따로 내지 않았고, swe는 요청 수가 1,296건뿐
이라 반복 간 흔들림이 총계보다 크다.

### 8.4 ⚠ H3의 유효성 조건이 반복 2에서 깨졌다

H3은 가설이 아니라 **유효성 조건**으로 적혀 있었다: **클래스별 유효 인스턴스 수가 두 arm 사이에서
0.3 미만으로 움직여야** 이 비교가 admission을 재는 것이지 배치를 재는 것이 아니다.
판정 도착률에서 잰 값(`separation_measures.py`, 창별 중앙값):

| | chat | deepresearch | swe |
|---|---|---|---|
| 반복 1 대조 → 처리 | 2.72 → 2.67 (**0.05**) | 3.49 → 3.53 (**0.04**) | 2.63 → 2.57 (**0.06**) |
| 반복 2 대조 → 처리 | 2.78 → 3.02 (**0.24**) | 3.44 → 3.53 (**0.09**) | 2.64 → **3.54 (0.90)** |

**반복 1은 조건을 넉넉히 만족하고 반복 2의 swe가 0.90으로 깬다.** 같은 run에서 **chat이 하나도
없는 (인스턴스, 시각) 쌍의 비율이 23.5%에서 3.4%로 무너지고** 인스턴스당 유효 클래스 수가
1.30에서 1.63으로 오른다 — **그 run에서는 클래스가 섞였다.** 그리고 그 run이 처리군 두 반복 중
점수가 높은 쪽(78.2)이다.

→ **그러므로 "이 실험은 admission만 잰 것이다"라고 쓸 수 없다.** 쓸 수 있는 것은
**"차이가 없고, 두 반복 중 하나에서는 배치까지 달라졌는데도 없었다"**이다. 결론의 방향은
영향받지 않지만 기제 문장은 못 쓴다.
→ 남는 질문: **왜 처리군에서만 그 흔들림이 나오는가.** 처리군의 반복 폭이 네 도착률 중 셋에서
대조군보다 2~5배 크다(3.7 대 1.8, 3.1 대 1.1, 4.7 대 0.9). **정확한 길이를 주면 결과가 더
불안정해진다**는 관찰인데, 반복이 둘뿐이라 폭은 두 값의 차이일 뿐이고 **측정이 아니라 관찰이다.**
확인하려면 반복을 늘려야 한다.

### 8.5 §8.0의 기제 가설은 반증됐다

§8.0이 반복 1의 35 req/s 한 쌍만 보고 **"정확한 길이를 주면 상주 요청의 남은 일이 크게 잡혀
admission이 보수적이 되고 chat 거절이 는다"**고 적었다. **반복 2가 반대다** — 처리군이 15.2%로
대조군 19.1%보다 **덜** 거절했고, 2반복 평균은 17.7 대 18.5%로 처리군이 낮다.

**즉 §8.0의 −2.9점과 chat 거절 +3.6점은 둘 다 반복 하나의 잡음이었다.** 반복 2가 +2.6점으로
반대 방향이었다. **가설을 결과 전에 적어 둔 덕분에 그것이 잡음에 맞춘 설명이었다는 것이 그대로
남는다** — §8.0은 지우지 않고 이 절이 무엇을 취소하는지 적어 둔다.

⚠ **`projected_kv_tokens`로 예측 오차를 재는 후속 실험은 이제 근거가 약하다.** 그 실험을 하려던
이유가 §8.0의 가설을 확인하는 것이었는데 가설이 없어졌다. 여전히 물어볼 만한 것은
**"오라클이 점유 예측을 실제로 더 정확하게 만들었는가"**이고(만들었는데도 점수가 같다면 그것이
H1을 더 강하게 만든다), 그것은 `-v 4`로 두 조건이면 된다. **우선순위는 낮다.**

### 8.0 진행 중 관찰 — 반복 1의 35 req/s 한 쌍 (2026-08-12 02:05). **판정 아님**

> ⚠ **이 절의 수치와 기제 가설은 §8.5가 취소했다.** 반복 하나의 잡음이었다. 지우지 않는 이유는
> 가설을 결과 전에 적었다는 기록 자체가 방법의 일부이기 때문이다.

⚠ **이 절은 판정이 아니다.** 사전 규칙은 2반복을 요구하고, 이 도착률의 반복 간 편차가 EXP-73에서
**3.8점**이었다. 아래 차이는 그보다 작다. 기제 가설을 반복 2 전에 적어 두려고 남긴다.

| 반복 1, 35 req/s | offered | admitted | **모든 도착(주 지표)** | 거절 | goodput |
|---|---|---|---|---|---|
| `fspfx` 대조 | 78.9 | 96.5 | **77.4** | 17.9% | 13,309 |
| `fsoracle` 처리 | 76.0 | 95.7 | **74.5** | 20.2% | 12,867 |

**유효성 검사 통과**: 실제 커버리지가 두 arm 모두 **99.2%**(chat 99.8 / deepresearch 96.3 /
swe 98.5), 러너가 `[oracle] loaded 30,853 entries`를 찍었고 표를 못 읽으면 워크로드가 종료하도록
되어 있다. 두 arm의 도착 집합이 같으므로 커버리지도 같다.

**늘어난 거절이 전부 chat이다**:

| 클래스 | 요청 수 | 대조 거절률 | 처리 거절률 |
|---|---|---|---|
| chat | 12,972 (77.0%) | 13.5% | **17.1%** |
| deepresearch | 2,592 | 14.2% | 13.5% |
| swe | 1,296 | 67.1% | 65.6% |

**기제 가설 (아직 측정 안 됨)**: 정책은 상주 요청의 남은 토큰을 클래스 분포의 조건부 기댓값
`expectedRemaining(j)`로 쓰는데, 오라클을 켜면 `max(1, 오라클 − j)`가 된다. **이미 j 토큰을
만들고 살아남은 요청은 길이가 긴 쪽으로 치우쳐 있으므로**, 클래스 분포는 그 요청들의 남은 일을
계통적으로 적게 잡고 오라클은 정확히 잡는다. 그러면 **투영된 KV 점유가 커지고 판정 조건을
통과하는 인스턴스가 줄어 거절이 는다.** chat이 요청의 77%라 총계를 chat이 움직인다.

⚠ **이 가설은 이 run들로 확인할 수 없다** — `projected_kv_tokens`와 `obs_kv_tokens`는 `-v 4`에서만
로그에 남는데 이 조건들은 기본 verbosity로 돌았다. 확인하려면 **두 arm을 `-v 4`로 한 조건씩 더
돌려 `exp48_projection_error.py`로 예측 오차를 재야 한다**(EXP-48이 대조군에서 88.5% 과소예측을
쟀던 그 방법).
→ 만약 오라클이 예측을 **더 정확하게** 만들면서 달성률을 **낮춘다면**, 그것은
**"점유 예측을 정확하게 하면 admission이 더 보수적이 되고, 그 대가가 정확도의 이득보다 크다"**는
결과이고 H1보다 강한 문장이다. **지금은 한 쌍의 관찰이다.**

(실행 후에 채운다)
