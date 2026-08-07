# FluidServe에 prefix cache 재사용을 넣는다 — 설계 (2026-08-08)

이 문서는 **설계와 그 근거의 정본**이다. 구현의 시간순 기록은
[fluidserve-implementation.md](fluidserve-implementation.md), 실험은
`Agent_applications/.../experiments/EXP-67_*.md`.

## 0. 왜 하는가 — EXP-66이 만든 질문

EXP-66에서 llm-d가 정적 rate sweep의 45 req/s 이상 전 구간에서 우리보다 높았다
(offered 달성률 55 req/s에서 84.3 대 66.4, 70 req/s에서 66.1 대 51.9). 원인을 축 여덟 개로
나눠 검사한 결과가 `llmd-baseline.md` §9.10~§9.11이고, **원인은 클래스 분리가 아니라 prefix
cache 재사용**이었다.

| 70 req/s, 엔진 Prometheus | llm-d | FluidServe |
|---|---|---|
| prefix cache hit rate | **93.5%** | 75.1% |
| 실제로 계산한 prefill (= prompt × (1 − hit)) | 4,585 tok/s | 12,274 tok/s |
| decode | 20,661 tok/s | 16,562 tok/s |
| KV 점유 | 22.2% | 35.9% |

그리고 그 차이는 **task 단위 지역성**에서 온다 — llm-d는 같은 `task_id`의 요청 99.9%를 같은
엔진으로 보내고(task당 유효 엔진 1.00), 우리는 57.8~62.5%(2.08~2.35)다. 우리 클래스 선호는
클래스 수준의 지역성만 주고 그 아래 층에서 캐시가 깨진다.

**예열 주행 때문이 아니다**: cold restart 직후 예열 주행의 첫 1분이 이미 hit 96.2%다.

## 1. 무엇을 바꾸고 무엇을 바꾸지 않는가

지금 도착 프롬프트의 prefill 비용은 이렇게 매겨진다(`fluidserve.go:1360`):

```go
newPending = f.effectivePrefill + float64(req.promptTokens)*p.capacity.prefillFractionOf()
```

`prefillFractionOf()`는 **인자가 없는 fleet 스칼라 하나**다. `notePrefill`이 "엔진이 실제로
계산한 토큰 ÷ 우리가 그 인스턴스로 보낸 프롬프트 토큰"을 500 ms 간격마다 재서 EMA로 접는다.

**이 값의 문제는 정확도가 아니라 자리다.** 두 가지가 섞여 있다.

1. **모든 후보 인스턴스에 같은 값을 쓴다.** 그래서 "이 인스턴스에 이 프롬프트가 캐시돼 있다"와
   "저 인스턴스엔 없다"를 구분하지 못한다. 수준(level)은 맞추지만 후보를 가를 신호가 아니다.
2. **클래스 믹스에 대한 평균이다.** 도착의 76.9%가 chat(평균 입력 673 토큰)인 상태에서 나온
   하나의 비율을, 평균 6,472 토큰인 agent 프롬프트에 그대로 적용한다.

### 바꾸는 것 — "몇 토큰을 계산해야 하는가"

```go
charge(req, inst) = (promptTokens − hitTokens(req, inst)) × κ
```

`hitTokens`는 스케줄러 안의 prefix 색인이 낸다. 이것이 이 변경의 전부다.

### 바꾸지 않는 것 — "그 토큰이 엔진 시간으로 얼마가 되는가"

**청크 단위 prefill이 엔진 안에서 언제 어떻게 끼어드는지는 밖에서 예측할 수 없으므로, 그
부분은 지금처럼 측정값을 쓴다.** 구체적으로 넷 다 그대로 둔다.

| 양 | 무엇 | 왜 그대로인가 |
|---|---|---|
| `f.effectivePrefill` | 엔진이 보고하는 대기 중 prefill 토큰 | **이미 엔진 캐시를 반영한 값이다.** 엔진이 "아직 계산 안 한 토큰"을 세어 주므로 여기에 우리 예측을 곱하면 이중 할인이 된다 |
| `f.arrivingPrefill` = `carriedPrefillTokens(prefillDutyOf(id), horizonMs, chunk)` | 측정된 prefill duty cycle을 horizon 만큼 앞으로 투영한 것 | 앞으로 100 iteration 동안 이 인스턴스가 prefill에 얼마를 쓸지는 **도착을 예측해야 알 수 있고**, 그 대신 최근 실측 duty를 쓴다. prefix 정보로 대체할 수 있는 양이 아니다 |
| `prefillSteps()`, `prefillStepMs()` | 토큰 수 → 청크 수 → step 시간 | 엔진의 성질이고 offline law로 적합돼 있다 |
| `costOf()` (KV 발자국) | `promptTokens + growth`, **할인하지 않는다** | latency model이 logical token을 세고 **공유 블록은 그것을 들고 있는 요청 전부에 물린다.** 여기서 할인하면 모델의 단위와 어긋난다 (fluidserve.go:1357의 기존 주석) |

즉 **prefix 정보는 "몇 토큰인가"에만 답하고, "그 토큰이 시간으로 얼마인가"는 지금의 측정
경로가 계속 답한다.** 두 질문은 다른 질문이고 첫 번째만 교체된다.

## 2. κ — 없애지 않고 뜻을 바꾼다

지금 `prefillFraction`이 재는 것은 사실상 **"1 − 실현된 hit rate"**다. 분자가 엔진이 실제로
계산한 토큰이고 분모가 우리가 보낸 프롬프트 토큰 전부이기 때문이다. 여기에 예측 hit을 곱하면
**같은 할인을 두 번** 한다. 그래서 분모를 같이 바꾼다.

| | 지금 | 바꾼 뒤 |
|---|---|---|
| 정의 | `computed / Σ promptTokens` | `computed / Σ charge_predicted` |
| 뜻 | 프롬프트 중 계산되는 비율 (예측기 역할) | **예측이 얼마나 틀렸나** (보정 계수) |
| 초기값 | 1.0 | 1.0 |
| 범위 | [0.02, 1.0] | **[0.25, 4.0]** — 잔차이므로 1을 넘을 수 있어야 한다 |

κ ≈ 1이면 예측이 맞는 것이고, **κ > 1이면 우리가 hit을 과대예측한 것**이다 — 스케줄러는 자기가
보낸 것만 기억하므로 엔진이 축출한 블록을 모르고, 그 대가가 정확히 여기로 나온다.

**없애지 않는 이유**는 이 저장소에서 이미 한 번 비싸게 배운 것이다. EXP-48에서 `proj`(한
horizon 뒤의 KV 점유 예측)를 열 몇 개 후보의 기반으로 쓰면서 **그 예측의 오차를 한 번도 재지
않았고**, 재보니 88.5% 과소예측에 MAE가 아무 투영도 안 하는 것의 3.6배였다. **예측값을 쓰는
항이 있으면 그 예측의 오차를 재는 장치를 같은 커밋에 넣는다.**

`--fluidserve-prefix-calibration=false`로 끌 수 있게 해서 ablation arm을 만든다.

### 신호가 없을 때 지금 동작으로 되돌아간다

토큰 ids가 없거나 색인이 비어 있으면 `hitTokens = 0`이고 charge는 `promptTokens × κ`가 된다.
그러면 κ는 지금의 `prefillFraction`과 **정확히 같은 양**으로 수렴한다. 즉 **신호가 없는 경로는
현재 동작과 같다.** 이것이 이 변경을 안전하게 만드는 성질이므로 검증에서 확인한다.

## 3. 색인 — KVS를 띄우지 않고 스케줄러 안에서

두 가지 길이 있었고 자체 색인을 고른다.

| | upstream KVS 경로 | **자체 색인 (고름)** |
|---|---|---|
| 무엇을 보나 | 엔진/KV store의 실제 블록 소유 | 스케줄러가 **자기가 보낸 것** |
| 필요한 것 | vineyard/mooncake 메타데이터 서비스 | 없음 |
| 켰을 때 서비스가 없으면 | `kvs.CreateOrGetClient` 실패 → **스케줄러 기동 panic** (scheduling_policy.go:153-165) | 해당 없음 |
| 조회 비용 | upstream 주석이 **"ms-level latency"**라고 적고, 그래서 cms 락을 풀었다 다시 잡는다 | µs 단위 |
| 축출을 아나 | 안다 | **모른다** → κ가 그 대가를 잰다 |

**우리는 PEND recheck 때문에 결정 호출률이 도착률보다 높으므로 ms 단위 조회는 감당이 안 된다.**
3000 rpm에서 agent 요청이 약 16초 붙들려 있다가 거절되면서 그 재질의율이 스케줄러를 포화시킨
적이 이미 있다(`canWait` 주석). llm-d도 메타데이터 서비스에 묻지 않고 자기가 라우팅한 것을
기억하는 방식이고, 그래서 비교도 대칭이 된다.

### 구조

```
prefixIndex
  blockTokens int              한 블록의 토큰 수. 엔진(vLLM)의 기본 블록이 16이므로 16
  capacity    int              LRU가 들고 있을 최대 블록 수
  m           map[uint64]entry 블록 해시 -> {인스턴스 비트마스크, LRU 원소}
  order       list.List        LRU
```

- **해시는 사슬형**이다: `h_i = hash(h_{i−1}, tokens[i·B:(i+1)·B])`. 그래야 같은 블록이 다른
  위치에 있을 때 잘못 맞지 않는다. `hasher.TokenHasher`가 같은 규칙을 쓰지만 `[]string`(hex)을
  돌려주어 할당이 많으므로, 같은 사슬 규칙의 uint64 해시를 따로 쓴다.
- **hit 길이는 앞에서부터 연속된 블록만 센다.** 중간에 하나라도 없으면 거기서 멈춘다 —
  prefix cache가 접두사에만 작동하기 때문이다. 이것은 upstream
  `calcInstancesPrefixCacheHitLen`의 규칙과 같고, 그 함수가 `instanceBroken`으로 같은 일을
  한다.
- **기록은 `commit` 시점**이다. PEND와 SHED는 commit하지 않으므로 색인에 아무것도 남기지
  않는다 — 엔진에 가지 않았으니 맞는 동작이다.

### 요청당 한 번만 해싱한다

`calculateMetrics`가 `fluidserveRequest`를 **스케줄링 호출마다** 새로 만든다. PEND recheck도
그 경로를 다시 탄다. 그래서 해싱을 거기 두면 붙들린 요청 하나가 수십 번 해싱된다.

→ 해시는 `requestRegistry`에 요청 id로 캐시한다. 레지스트리는 이미 `arrivedMs`,
`lastSeenMs`를 요청 id로 들고 있고 5분 TTL로 GC하며, SHED 경로에 `forget(id)`가 있다.
**조회(블록 → 인스턴스)는 recheck마다 다시 한다** — 그동안 다른 요청이 어디로 갔는지가
바뀌므로 다시 해야 맞다. 비싼 쪽만 캐시하고 싼 쪽은 매번 하는 것이 llm-d의 producer(46,179회)
대 filter(35,726회) 분리와 같은 형태다.

### 비용 추정

llm-d의 같은 기계를 **우리 환경에서 잰 값**이 있다(EXP-66 70 req/s, EPP 플러그인 시간):

| | 총 CPU-초 | 평균 µs/호출 | 비중 |
|---|---|---|---|
| prefix 기계 전체 | 2.97 | 69 | **0.34%** |
| 예측기(우리는 안 가져옴) | 744.05 | — | 85.7% |
| 전체 | 868.18 | | |

우리는 게이트웨이가 토큰화를 이미 해 주므로 그보다 싸야 한다. 블록 16 토큰이면 chat 42개,
deepresearch 272개, swe 405개이고 map 조회가 ~50 ns이니 요청당 2~20 µs다.

메모리는 **요청 수가 아니라 서로 다른 프롬프트 내용에 비례한다.** 8분 70 req/s 조건에서 요청
33,660건에 입력 토큰 56.7 M인데, **서로 다른 (task, call) 조합은 2,805개에 4.73 M 토큰**이다
(재생 배수 12.0배). 블록 16 토큰이면 서로 다른 블록의 상한이 0.30 M개이고, 블록당 해시 8 B +
마스크 4 B + LRU 연결 ~48 B로 잡아 **약 19 MB**다.
⚠ **한 시간 trace에서는 다시 재야 한다** — 2,805는 task 풀의 일부만 본 값이다.

## 4. 고치는 곳

| # | 위치 | 변경 |
|---|---|---|
| 1 | `fluidserve_prefix.go` (신규) | 색인, 사슬 해시, `hitTokens`, `note` |
| 2 | `fluidserve_registry.go` | `promptHashes(id, tokenIds)` — 요청당 한 번 계산하고 캐시 |
| 3 | `fluidserve.go` `fluidserveRequest` | `promptHashes []uint64` 필드 |
| 4 | `fluidserve.go:343` `calculateMetrics` | 레지스트리에서 해시를 받아 ctx에 넣는다 |
| 5 | `fluidserve.go:1360` `evaluate` | `newPending`의 프롬프트 항을 인스턴스별 charge로 |
| 6 | `fluidserve.go:1882` `prefillEstimateMs` | 같은 charge를 쓴다 (TTFT 추정) |
| 7 | `fluidserve.go:1936` `commit` | `note(hashes, instance)` |
| 8 | `fluidserve_capacity.go` | κ 재정의: 분모가 예측 charge, 범위 [0.25, 4.0] |
| 9 | `fluidserve.go` `reportLoop` | 예측 hit ratio와 κ를 **둘 다** 내보낸다 |
| 10 | `cmd/config/config.go` | 플래그 넷 |

**건드리지 않는 것**: `costOf`, `sortCandidates`, `classShare`, 게이트웨이, 클라이언트,
`schedulingCtx.prefixHitTokens`(upstream KVS 경로 전용이므로 상호작용을 만들지 않는다).

### 플래그

| 플래그 | 기본 | 뜻 |
|---|---|---|
| `--fluidserve-prefix-aware` | **false** | 이 기능 전체. 기본이 false여야 기존 arm이 그대로 재현된다 |
| `--fluidserve-prefix-block-tokens` | 16 | 엔진 vLLM 블록과 맞춘다 |
| `--fluidserve-prefix-capacity` | 500000 | LRU 블록 수 |
| `--fluidserve-prefix-calibration` | true | κ를 charge에 곱할지. ablation용 |

## 5. 예상되는 것과 그것이 틀렸음을 보이는 것 (EXP-67의 사전 등록)

| # | 예상 | 무엇이 나오면 틀린 것인가 |
|---|---|---|
| H1 | 엔진별 prefix hit rate가 75.1% → **85% 이상**으로 오른다 | 안 오르면 신호가 결정에 도달하지 못한 것이다. 아래 "가장 큰 불확실성" 참조 |
| H2 | task당 유효 엔진 수가 2.08 → **1.6 이하**로 내려간다 | 안 내려가면 순위가 여전히 클래스 항에 지배되는 것이다 |
| H3 | 45~70 req/s에서 offered 달성률이 **반복 간 편차보다 크게** 오른다 | 편차 안이면 이득이 없다 |
| H4 | κ가 1.0 ± 0.25 안에 머문다 | 1.5를 넘으면 축출을 못 따라가는 것이고 색인 용량이나 TTL을 손봐야 한다 |
| H5 | `infeasible_total{reason}`의 구성이 gate/incumbents에서 **memory 쪽으로** 이동한다 | prefill 항만 느슨해지고 KV는 그대로이므로 이렇게 되어야 한다. 안 되면 charge가 결정을 안 바꾼 것이다 |

### 가장 큰 불확실성 — 순위에는 안 들어간다

`sortCandidates`의 점수는 `w·share + (1−w)·room`이고 **기본값 w = 1.0**이다(플래그
`--fluidserve-affinity-weight` 기본 1.0). `room`은 `capKv`/`capMem`/`newKv`로 만들어지는데
`capKv`는 인스턴스의 현재 큐만 보고 도착 요청의 프롬프트를 안 보며(`maxKvForAllowance`가
`f.effectivePrefill`만 받는다) `newKv`의 `cost`는 위에서 정한 대로 할인하지 않는다.
**그래서 prefix 신호는 feasibility 판정과 TTFT 판정에만 들어가고 feasible 안에서의 순위에는
안 들어간다.**

결과: **캐시가 있는 인스턴스와 없는 인스턴스가 둘 다 feasible이면 클래스 점유율이 높은 쪽으로
간다.** 지역성은 feasibility가 구속력을 가질 때만 생긴다. llm-d는 거절률 0.5%인 45 req/s에서도
task당 유효 엔진이 1.00인데, 이 변경만으로는 저부하에서 지역성이 안 생기고 그러면 부하가
올라올 때 캐시가 이미 흩어져 있어 씨앗이 없다.

**H1과 H2가 이것을 잰다.** 둘 다 실패하면 선택지는 `score`에 세 번째 항을 넣는 것인데,
**그 순간 "예측 정확도 개선"이 "새 목적함수"가 되어 성격이 바뀐다** — 지금 논지는 "우리는
판정 조건 하나를 정확하게 만들 뿐이고 배치는 그 결과다"이고, 세 번째 항은 그 문장을 깬다.
그래서 그것은 별도 결정으로 남기고 이 변경에는 넣지 않는다.

## 6. 논지에 미치는 영향

`sortCandidates`의 주석이 지금 논지의 정본이다:

> "Filling one instance with one class until it can take no more is what produces a separation
> without any instance being assigned to a class, and the feasibility test is what stops the
> filling."

- **구조는 유지되고 사례가 둘이 된다.** prefix 지역성도 어떤 인스턴스를 어떤 task에
  할당해서 얻는 것이 아니라 판정 조건에서 나온다.
- **바뀌는 것은 "무엇으로 모으는가"다.** 지금 근거는 하나 — 인스턴스의 허용 점유는 그 위 가장
  빡빡한 pace가 정하므로 클래스를 섞으면 양방향으로 용량을 낭비한다. 여기에 "같은 프롬프트
  계열을 모으면 prefill 계산이 준다"가 더해지면 **모으는 축이 둘이고 서로 다른 분할을 선호할
  수 있다.** 클래스가 같아도 task가 다르면 prefix는 안 겹친다. 지금 데이터가 그 상태다 —
  70 req/s에서 클래스 유효 인스턴스 2.41, task 유효 인스턴스 2.08.
- **논문에서 오독을 만들 수 있다.** "우리 격리는 클래스 격리다"라고 쓰는데 이득의 상당 부분이
  prefix 지역성이면 틀린 서술이다. **EXP-56을 `{클래스 선호 on/off} × {prefix on/off}` 2×2로
  확장해 기여를 갈라야 한다.**

## 7. 재현성이 나빠진다

`sortCandidates`가 인스턴스 id로 결정적 tiebreak를 두는 것은 주석대로 "a run can be
reproduced"를 위한 것이다. prefix 색인은 **이력에 의존하는 상태**라 같은 설정의 두 run이 더
갈라진다. 출력 토큰이 run마다 65.7%만 같다는 것을 이미 알고 있고 여기에 라우팅 이력 의존성이
더해지므로, **arm당 2회로 판정하던 것이 부족할 수 있다.** EXP-67에서 편차를 먼저 잰다.
