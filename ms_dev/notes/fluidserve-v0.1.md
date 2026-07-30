# FluidServe v0.1 — 무엇인가, 무엇이 아닌가

**태그**: `fluidserve-v0.1` (llumnix, Agent_applications 양쪽)
**날짜**: 2026-07-28
**바이너리**: `scheduler-exp07-v22` (`md5 57af20bfb26e681b67f050336b80f3d9`)

> **⚠ 이 문서의 attainment 수치는 정정 전 지표로 계산된 것이다 (2026-07-30).**
> 기록된 `tbt_mean_ms`가 실제 토큰 간 지연의 1/1.92였고, 규칙이 `TTFT AND 평균 TBT`
> 이므로 chat은 50ms 대신 실질 96ms, deepresearch는 100ms 대신 192ms로 판정되었다
> (implementation.md §32). 재측정은 필요 없고 `exp23_rate_sweep.py`를 다시 돌리면
> 되지만, **아래 표를 아직 갱신하지 않았다.** 정정된 수치는 EXP-38 §4에 있다.
> agent 클래스(E2E 판정), 거절률, 결정 구성, 배치 크기는 영향이 없다.

이 문서는 **시간순 기록이 아니라 현재 상태의 명세**다. 어떻게 여기까지 왔는지는
[fluidserve-implementation.md](fluidserve-implementation.md)에 있고, 이 문서는
"지금 무엇이 구현되어 있고, 무엇에 의존하며, 무엇이 아직 아닌가"만 답한다.

---

## 1. 한 문장

**클래스에 서버를 배정하지 않고, 각 요청이 어느 인스턴스에서 자기 지연 예산을
지킬 수 있는지를 계획 지평 위에서 계산해 라우팅과 admission을 같은 양으로 결정한다.**

정적 파티션(PolyServe)과의 구조적 차이는 여기서 나온다. 파티션은 "이 서버는 chat의
것"이라고 정하고 나면 chat이 그 서버의 용량에 갇히고 다른 서버의 남는 지연 예산을
쓸 수 없다. FluidServe는 배정이 없으므로 그 제약이 없고, 대신 **매 요청마다 게이트를
통과해야 한다**는 제약을 진다.

---

## 2. 결정 규칙 (사다리)

각 단계가 **하나의 양**으로 결정된다. 가중합이 없고 따라서 가중치도 없다.

```
feasible 인스턴스가 있다        → ROUTE.  그 중 이 클래스를 가장 많이 든 곳,
                                 동률이면 여유 공간이 큰 곳
없는데 아직 기다릴 수 있다      → PEND.   게이트웨이가 붙잡고 재시도마다 다시 묻는다
없고 시간도 없고,
  지금 배치해도 자기 예산을 놓친다 → SHED.  즉시 거절(503, 재시도 없음)
그 외                          → FORCE.  가장 덜 파괴하는 곳에 배치
```

**feasible의 정의** — 세 조건을 모두 만족:

1. `meanAfter ≤ gateAfter` — 배치 후 예측 평균 iteration이, 그 인스턴스 위 요청들이
   **약속받은** 가장 빡빡한 pace 이내 (공칭값이라 인스턴스가 늦어져도 안 움직인다)
2. `meanAfter ≤ tightestAllowance` — 아직 예산을 지킬 수 있는 요청 중 **남은** 여유가
   가장 적은 것을 넘기지 않는다 (지금 위험한 요청 보호)
3. `newKv ≤ capMem` — 물리 메모리 한도

1은 안정적인 용량 회계, 2는 현재 위험 보호. **둘을 분리한 이유**: 하나로 합치면
늦어진 인스턴스가 스스로를 닫아버리는 되먹임이 생겨 fleet이 무너진다(실측).

**class affinity를 feasible 집합 안에서만 적용하는 것이 핵심 안전장치다.** 선호는
순서를 정할 수 있어도 **용량을 넘겨 배치할 수는 없다**. 이것이 없던 초기 버전에서
한 엔진에 5,569건이 쌓이고 나머지 셋이 27분간 놀았다.

---

## 3. 용량 모델 (C3)

```
디코드 step   t_dec(M, n) = c0 + c_kv·M + c_n·n
prefill step  t_pre(chunk)                     ← 프로파일 테이블에서 조회
혼합 step     t_pre + t_dec − c0               ← 같은 forward pass, 고정비는 한 번
지평 평균     mean = [(k − s_p)·t_dec + s_p·(t_pre + t_dec − c0)] / k
```

**평균을 예측하는 이유**: SLO가 평균에 대해 쓰여 있다. 한 step이 오래 걸리는 것은
그 평균을 올리는 만큼만 위반이다.

`s_p` = 지평 k step 중 prefill chunk를 싣는 step 수.
**한 chunk 이하면 1**(호출부가 부분 chunk 단가를 쓰므로), **넘으면 소수를 유지**한다.
100 step 지평에서 1.4를 2로 올림하면 예측 평균에 3.0 ms가 붙고, 그 3.0 ms가 게이트를
가른다(v21).

---

## 4. 흐름 회계 (flux)

```
proj = kvLogical + inflow − outflow
inflow  = nDecode × k                      결정적. 디코드 요청은 step당 토큰 1개
outflow = Σ p_c(j,k)·w_r − z·√Var          확률적. 클래스별 출력 길이 분포에서
```

**outflow를 z=1.65만큼 낮게 편향시킨다.** 방출을 과대평가하면 담을 수 없는 만큼
받아들여 preemption과 붕괴로 끝나고, 회복 비용이 과소평가로 잃는 처리량보다 훨씬 크다.
단방향 편향은 의도적이다.

---

## 5. 무엇을 측정하고 무엇을 설정하는가

이 구분이 v0.1의 설계 원칙이다. **추정기는 자기 출력을 읽으면 안 된다.**

### 엔진이 보고하는 것 (정확, 추정 아님)
`kvLogical`, `kvPhysical`, `nDecode`, `pendingPrefill`, `stepId`, `timestampMs`

### 엔진 관측에서 유도하는 것 (되먹임이 음수라 자기교정)

| 양 | 어떻게 | 왜 이 형태여야 하는가 |
|---|---|---|
| 실측 평균 step | `Δ시각 / Δstep` | 모델이 예측하는 바로 그 양 |
| prefill duty cycle | `(실측 − 디코드전용) / 실측`, **인스턴스별** | fleet 평균이면 모든 후보에서 값이 같아 **정렬에 기여를 못 하고 게이트만 전 클래스에 동시에 조인다** |
| prefill 실측 비율 | 엔진이 실제 계산한 프롬프트 토큰 비율 | 프롬프트를 전액 청구하면 재사용이 있는 워크로드에서 자릿수가 틀린다 |
| 보정계수 | 실측/예측 비율 | 오프라인 법칙과 눈앞의 엔진 사이의 드리프트 |
| **재결정 주기** | 같은 요청 id의 연속 호출 간격 | 게이트웨이 설정을 읽지 않고 **관측**하므로 이식성이 있다 (v22) |

**금지된 형태**(둘 다 실측으로 실패해 제거됨): 라우터 자신의 **배치율**(배치 결정을
통과하는 양의 되먹임), fleet의 **offered rate**(admission 결정을 통과하는 양의
되먹임 — 거절률 s일 때 `offered = served/(1−s)`).

### 오프라인 프로파일 (하드웨어·모델 특성. 튜닝이 아니라 측정)
`ttft.json`, `tpot.json`, `fluidserve.json`
→ (모델 × GPU × TP) 조합이 바뀌면 **재측정**. 절차는 `deploy/profiling/README.md`

### 설정 (SLO 명세. 입력이지 손잡이가 아니다)
`--fluidserve-class-budgets "25:e2e:30000,50:decode,100:decode"`
→ 클래스 개수 제한 없음. 새 클래스는 **출력 길이 분포**만 추가하면 된다

---

## 6. 남은 상수 전부

**결정에 관여하는 것 — 9개. 전부 차원이 없거나 iteration 단위다.**

| 상수 | 값 | 단위 | 이식성 |
|---|---|---|---|
| `horizonSteps` | 100 | **iteration** | 초가 아니라 iteration이라 빠른 하드웨어에서 자동으로 짧아진다 |
| `zSafety` | 1.65 | 신뢰수준 | 통계값 |
| `fsMemorySafety` | 0.95 | 비율 | |
| `fsAllowanceUtilisation` | 0.90 | 비율 | |
| `fsHarmCap` | 10.0 | 비율 상한 | |
| `fsRetireSurvival` | 0.02 | 확률 | |
| `fsCorrectionAlpha` | 0.002 | 샘플당 가중치 | ⚠ 상태 조회 간격에 결합 |
| `fsPrefillAlpha` | 0.01 | 샘플당 가중치 | ⚠ 상태 조회 간격에 결합 |
| `fsPrefillDutyAlpha` | 0.1 | 샘플당 가중치 | ⚠ 상태 조회 간격에 결합 |

**밀리초 단위 상수**: `ttftSafetyMs = 300` 하나. prefill 추정 오차용이고, 재결정
주기는 이제 별도로 **관측**해서 뺀다.

**결정에 관여하지 않는 것**: GC 수명(`fsMaxRecordAgeMs`, `fsArrivalTTLMs`),
계측 전용(`fsOfferedWindowMs`, `fsOfferedRateAlpha`), 보정계수 상하한.

⚠ 표시 셋은 [fluidserve-implementation.md §21](fluidserve-implementation.md)에
시간상수 전환이 보류 항목으로 기록되어 있다. **정확도 문제가 아니라 이식성 문제**다.

---

## 7. v0.1이 실측으로 보인 것 (EXP-27 pass 3, 반복 2회)

워크로드: chat 666 / deepresearch 4,074 / **swe 5,930** 토큰(단축본),
믹스 m1 = 입력 토큰 31/37/31%, 엔진 4대(Llama-3.1-70B, B200×2 TP2, stock FIFO,
migration 끔), 조건당 8분, 조건마다 엔진 콜드 재시작.

| req/s | 용량 대비 | arm | req-adm | req-off | eq-adm | eq-off | goodput | 거절% |
|---|---|---|---|---|---|---|---|---|
| 20 | 31% | polyserve | 100.0 ±0.0 | 100.0 | 99.9 | 99.9 | 8,528 | 0.0 |
| | | **fluidserve** | **100.0 ±0.0** | 100.0 | 100.0 | 100.0 | 8,573 | 0.0 |
| 40 | 62% | polyserve | 54.4 ±0.7 | 54.4 | 79.1 | 79.1 | 7,277 | 0.0 |
| | | **fluidserve** | **100.0 ±0.0** | **99.9** | **100.0** | **99.9** | **16,546** | 0.1 |
| 60 | 94% | polyserve | 40.3 ±2.2 | 40.3 | 72.2 | 72.2 | 7,611 | 0.0 |
| | | **fluidserve** | **98.9 ±0.0** | **89.7** | **97.0** | **78.6** | **21,468** | 9.3 |
| 80 | 125% | polyserve | 33.1 ±0.1 | 33.1 | 69.3 | **69.3** | 8,635 | 0.0 |
| | | **fluidserve** | **87.0 ±3.0** | **60.7** | **82.1** | 49.1 | **18,767** | 29.6 |

**네 지표(req-adm / req-off / eq-adm / goodput) × 네 rate 전부에서 이긴다.**

**다섯 번째 조합인 eq-off는 80 req/s에서 진다 (49.1 대 69.3).** 이 표의 최초 판에는
eq-off 열이 없었고, 그 상태에서 "채점 방식을 고를 여지가 없다"고 적었던 것은 **틀린
서술이었다** (2026-07-29 정정). 지는 이유는 채점 규칙의 산술이다. eq-off는 거절을 전부
위반으로 세면서 **세 클래스에 같은 가중치**를 주므로, 아무것도 거절하지 않고 dr 100.0 /
swe 98.6을 지키는 대신 chat만 9.4로 붕괴시킨 PolyServe가 (9.4+100.0+98.6)/3 = 69.3을
받는다. 그 chat이 **요청의 76.9%**라는 사실은 등가중 평균에 들어가지 않는다. 같은 상태를
요청 단위로 세면 33.1이고 FluidServe 87.0에 크게 진다.

즉 이 한 칸은 **거절률 29.6%를 클래스 등가중으로 채점했을 때의 값**이고, 요청 단위로
채점하거나 goodput으로 보면 뒤집힌다. 어느 쪽이 옳은 채점인지는 규칙의 문제이지 측정의
문제가 아니므로, **네 지표를 전부 같이 싣고 이 한 칸이 진다는 것을 명시**한다.

### 메커니즘 (요청 단위 지표가 아니라 엔진이 보고한 것)

40 req/s에서 PolyServe는 **엔진 하나를 max batch(1,024)에 붙여놓고 대기열을 2,300까지
키우면서 나머지 셋을 0 근처**로 둔다. 같은 시각 그 chat 요청들의 **ITL은 48 ms로
50 ms 예산 안**이다 — 엔진이 느린 게 아니라 요청이 줄 서 있고, 남는 엔진 셋은
그것을 받을 지연 예산이 있었다. FluidServe는 네 엔진을 4.6~13.9%로 고르게 쓰고
대기열이 0이다.

원인은 정적 배정의 산술이다. m1에서 chat은 **요청의 76.9%**인데 요청당 server-seconds가
작아 수요 추정이 `chat 0.062 / swe 0.121 / dr 0.048`로 나오고, 배정이 (swe 2 / chat 1 /
dr 1)이 되어 **요청의 77%가 서버 1대에 들어간다.**

### goodput 곡선이 봉우리다

60 req/s에서 21,468로 정점, 80에서 18,767로 하강. 디코드 법칙으로 계산한 m1의
포화점이 **약 64 req/s**이고 정점이 60에 있다 — 모델과 측정이 일치한다.
PolyServe는 이 형태가 없다: 40 req/s(용량의 62%)부터 평평하다.

---

## 8. v0.1이 **아닌** 것

### 목표 미달 구간
**80 req/s(용량의 125%)에서 87.0 ±3.0**으로 "모든 rate에서 90% 이상" 목표에 3점 모자란다.
결정의 84.4%가 보유이고, 받아들인 chat의 TTFT p90이 예산을 조금 넘는다.
**용량 이내(60 req/s까지)에서는 목표를 완전히 달성**한다.

### 측정 가능한 효과가 없는 메커니즘
v20(다른 클래스가 든 인스턴스를 damage 추정에서 보호)은 이 워크로드에서
**켠 것 62.2 대 끈 것 62.7, 산포 ±0.2 대 ±0.9**로 차이가 없다. 근거였던 산포
±23.6점은 옛 워크로드(swe 22.5k)의 것이고 여기서는 그 불안정성 자체가 없다.
**제거 후보이나, 옛 워크로드에서 확인 후 결정한다.**

### 지원하지 않는 SLO 형태
평균 기반 지연 SLO만 지원한다(`e2e` 전체 예산, `decode` 토큰당 pace + TTFT 임계).
**p99 TBT 같은 분위수 SLO는 용량 모델이 평균만 예측하므로 분산 모델이 필요하다.**
처리량 SLO, 공정성 제약도 없다.

### 측정하지 않은 것
- **믹스가 조건 안에서 바뀌는 경우** (EXP-28 설계 완료, 미실행)
- **1시간 Azure 동적 trace** (rate와 믹스가 동시에 움직임)
- **m2/m3 믹스를 v22로** (v19+v20까지만 측정)
- **엔진 수를 바꿨을 때** (4대 고정)
- **migration을 켰을 때** (전 실험 migration 끔)

---

## 9. 재현 방법

```bash
# 스케줄러 빌드
export PATH=/home/nxclab/tools/go/bin:$PATH GOFLAGS=-mod=mod
export TMPDIR=/home/nxclab/tools/gotmp GOTMPDIR=/home/nxclab/tools/gotmp
CGO_ENABLED=0 go build -buildvcs=false -o bin/scheduler-exp07 ./cmd/scheduler
#   bin/이 파드에 hostPath로 마운트되어 있고 파드가 그 파일을 실행 중이라
#   덮어쓰기는 "text file busy"로 실패한다. 복사 후 rename할 것.

# 워크로드 (단축 transcript는 git 대상이 아니고 스크립트로 재생성)
python workloads/codingagent_request_level_poisson/build_short_transcript.py \
  --in  results/exp10_transcript/transcript_swe_calls_mix1500.jsonl \
  --out results/exp10_transcript/transcript_swe_short7k_mix1500.jsonl \
  --target-mean-tokens 7000

# 실험
/home/nxclab/tools/exp27_pass3.sh m1 "1200,2400,3600,4800" 8 2

# 채점과 그림
python analysis_scripts/request_level/exp23_rate_sweep.py --runs 'results/*exp27p3*' --out-dir <dir>
python analysis_scripts/request_level/exp27_figures.py --out-dir <dir>
```

**배포가 반영됐는지 확인하는 유일한 방법**은 파드 안에서 실행 중인 바이너리를 읽는 것이다:
`kubectl -n llumnix exec <pod> -- md5sum /proc/1/exe`. 파일 시각이나 deployment spec으로는
잡히지 않는 실패가 실제로 있었다.

---

## 10. 관련 문서

| 문서 | 내용 |
|---|---|
| [fluidserve-implementation.md](fluidserve-implementation.md) | 시간순 구현 기록. §13 이전 상태, §15~§20 v19~v22, §21 보류 항목 |
| [fluidserve-design.md](fluidserve-design.md) | 설계 원안과 부록 B(원안 대비 무엇이 유지·변경됐는가) |
| `Agent_applications/.../experiments/EXP-25`, `EXP-27` | 실험 설계·판정규칙·결과 |
| `deploy/profiling/README.md` | 프로파일 테이블의 출처와 신뢰도 |
