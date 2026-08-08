# FluidServe v0.2 — 무엇인가, 무엇이 아닌가

**태그**: `fluidserve-v0.2` (llumnix, Agent_applications 양쪽)
**날짜**: 2026-08-08
**바이너리**: `bin/scheduler-exp07` (`md5 a96dac128ee7eeeed51949fa42910b58`, 파드에서
`md5sum /proc/1/exe`로 검증)
**프로파일**: `deploy/profiling/llama31-70b-b200-tp2/fluidserve.json` — 2026-08-02판
(`435f3d3`). v0.1.1 이후 안 바뀌었다.
**앞 버전**: [fluidserve-v0.1.2.md](fluidserve-v0.1.2.md) (같은 날 몇 시간 전, 태그
`fluidserve-v0.1.2`, 바이너리 `5a572dc2`)

> **왜 0.1.3이 아니라 0.2인가.** v0.1.2 문서 §3이 "prefix 인식을 기본으로 켜면 v0.1.3"이라고
> 적어 두었는데, **그 판단을 바꿨다.** 이 저장소가 v0.1.1에서 세운 기준은 **"기본 설정에서
> 결정 규칙이 바뀌면 minor, 정책이 읽는 입력이나 계측만 바뀌면 patch"**다. prefix 인식을
> 기본으로 켜면 `feasible` 판정이 계산하는 값 자체가 달라지고, 무엇보다 **도착 프롬프트의
> 청구액이 처음으로 "어느 인스턴스를 보고 있는가"의 함수가 된다.** 그 전까지 청구액은
> 요청의 성질이었다. 입력 하나가 바뀐 것이 아니라 **판정에 들어가는 양의 정의가 바뀐
> 것**이므로 minor다.

---

## 1. v0.1.2에서 바뀐 것 — 딱 하나, 기본값

```
--fluidserve-prefix-aware   false  →  true
```

**코드는 한 줄도 안 바뀌었다.** 메커니즘은 v0.1.2 §2.3에 이미 있었고 플래그로 꺼져 있었을
뿐이다. 바뀐 것은 **아무 플래그도 안 주고 띄웠을 때 무엇이 되는가**이다.

기동 줄로 확인한 두 상태:

```
플래그 없음      → ... gateslack=1.000, prefix=true,  prefixcalib=true, prefixblock=16, ...
FS_PREFIX=false → ... gateslack=1.000, prefix=false, prefixcalib=true, prefixblock=16, ...
```

### 1.1 왜 켰나 (EXP-69)

대조군과 처리군이 **환경변수 하나만 다르고** 바이너리·예산·용량 모델·결정 단계가 전부 같다.
3 rate × 2반복. 요청 단위 offered 달성률:

| req/s | prefix 끔 | **prefix 켬** | 이득 | goodput | 거절률 | admitted |
|---|---|---|---|---|---|---|
| 35 | 64.2 (63.4~65.0) | **77.6** (76.9~78.4) | **+13.5** | +15.2% | 25.3 → **16.0%** | 86.5 → 92.8 |
| 45 | 49.3 (48.9~49.6) | **59.4** (59.4~59.5) | **+10.2** | +13.9% | 43.2 → **35.4%** | 87.9 → 92.9 |
| 55 | 38.0 (35.6~40.4) | **48.3** (47.4~49.1) | **+10.3** | +21.7% | 53.7 → **46.3%** | 83.6 → 91.2 |

**네 지표가 전부 같은 방향이고 이득이 두 arm의 반복 폭(0.1~4.9점)보다 훨씬 크다. 측정된
손해가 없다.** 비용은 재서 안다 — 프롬프트 해시가 5.2~50.7 µs, 인스턴스당 조회가
0.42~4.5 µs이므로 인스턴스 넷을 보는 요청 하나가 최악에 약 70 µs다.

### 1.2 ⚠ 이름을 조심한다 — "prefix-aware routing"이 아니다

EXP-69 §3.2가 기제를 갈랐다. prefix 인식을 켜면 **엔진이 보고하는 prefix cache hit rate가
45·55 req/s에서 오히려 1.5%p 낮은데**(23.9 → 22.3, 24.8 → 23.3) 거절이 7%p 줄고 prompt·생성
토큰이 둘 다 약 10% 늘었다.

**배치가 캐시 친화적으로 바뀐 것이 아니라, prefill 비용이 정확해져서 admission이 덜
보수적이 된 것이다.** 코드가 그것을 예고하고 있었다 — prefix는 `sortCandidates`의 점수
`w·share + (1−w)·room` 어느 항에도 안 들어가고 `feasible` 판정과 TTFT 추정에만 들어간다.

> **쓸 수 있는 이름: "도착 프롬프트를 그 인스턴스가 실제로 계산할 부분만큼 청구한다."**
> 쓰면 안 되는 이름: "prefix-aware routing", "캐시가 있는 곳으로 보낸다".

---

## 2. ⚠ arm 이름 하나가 두 설정을 뜻하지 않게 막았다

기본값을 뒤집으면 **`FS_PREFIX`를 설정하지 않는 arm이 전부 조용히 처리군이 된다.**
`set_scheduler_profiling.py`가 이번 호출이 설정하지 않은 ablation을 컴파일 기본값으로
돌려보내기 때문이다. 실제로 그런 arm이 네 드라이버에 있었고, 그중 하나가 **EXP-69의
대조군**이다.

그대로 두면 `results/*_fluidserve_*` 디렉토리가 **v0.2 이전과 이후에 다른 설정**을 뜻하게
된다. 이 저장소가 §32(같은 이름의 두 양)·§40·§55에서 반복해서 당한 실패다.

**그래서 네 드라이버의 `fluidserve` arm에 `FS_PREFIX=false`를 명시로 박았다.**

| 드라이버 | arm | 지금 |
|---|---|---|
| `run_exp67_prefix.sh` | `fluidserve` | `FS_PREFIX=false` 명시 |
| `run_exp53_compare.sh` | `fluidserve` | 같음 |
| `run_exp22_fluidserve.sh` | `fluidserve` | 같음 |
| `run_exp30_dynamic.sh` | `fluidserve` | 같음 |

**그래서 v0.2부터 arm 이름의 뜻은 이렇다.**

| arm 이름 | 무엇인가 |
|---|---|
| **`fspfx`** | **배포 기본 설정.** `FS_PREFIX=true`를 명시로 설정한다(기본값이어도 명시한다 — 함정 A) |
| `fluidserve` | **prefix를 끈 ablation.** 이 이름의 모든 기존 결과와 같은 설정이다 |

⚠ **논문에서 "FluidServe"는 `fspfx` arm의 수치다.** `fluidserve` arm은 이제 ablation이고,
그것이 EXP-69에서 쓰인 방식과 같다.

---

## 3. v0.1.2에서 그대로인 것

결정 단계(ROUTE / PEND / SHED / FORCE), `feasible`의 네 조건, 용량 모델, 흐름 회계, 정렬
규칙, 상수 아홉 개, 클래스 출력 길이 분포 — 전부 그대로다.

나머지 두 메커니즘도 기본값이 종전 동작 그대로다.

| 플래그 | 기본값 | 기본값에서 |
|---|---|---|
| `--fluidserve-affinity-weight` | 1.0 | EXP-58 이전의 사전식 정렬과 동일 |
| `--fluidserve-class-pin` | "" | 후보를 안 거름. **배포 기능이 아니라 계측 장치다** |
| **`--fluidserve-prefix-aware`** | **true** | **← 이 버전에서 바뀐 것** |
| `--fluidserve-prefix-calibration` | true | 보정을 곱한다 |
| `--fluidserve-prefix-block-tokens` | 16 | vLLM의 블록 크기와 맞아야 한다 |
| `--fluidserve-prefix-capacity` | 500000 | 색인이 드는 블록 수 상한(LRU) |

결정 경로를 위에서 아래로 읽으려면 [fluidserve-how-it-works.md](fluidserve-how-it-works.md)
**§0**이 요청 하나를 여섯 단계로 통과시킨다.

---

## 4. 실측 (2026-08-08, 수정 후 워크로드)

⚠ **v0.1.1 문서 §4와 나란히 놓을 수 없다.** 2026-08-08 11:00에 부하 생성기의 워커별
프롬프트 중복 결함을 고쳤고(모든 프롬프트가 정확히 12번씩 나가던 것), 엔진 prefix cache hit
rate가 83~86%에서 22~39%로 내려갔다. 경위는 v0.1.2 문서 §0과
[fluidserve-prefix.md](fluidserve-prefix.md) §8.

### 4.1 정적, m1 믹스, 8분 조건, 2반복 (EXP-68·EXP-69)

요청 단위 offered 달성률. 괄호는 두 반복의 폭.

| req/s | **FluidServe v0.2** (`fspfx`) | prefix 끈 ablation | llm-d |
|---|---|---|---|
| 35 | **77.6** (76.9~78.4) | 64.2 (63.4~65.0) | 42.9 (**35.2~50.6**) |
| 45 | **59.4** (59.4~59.5) | 49.3 (48.9~49.6) | 24.9 (24.2~25.7) |
| 55 | **48.3** (47.4~49.1) | 38.0 (35.6~40.4) | 21.0 (18.1~23.8) |

**llm-d 대비 +34.7 / +34.5 / +27.3점**이고 차이가 두 arm 중 큰 반복 폭의 2.3~23배다.
**그중 prefix 인식이 +13.5 / +10.2 / +10.3점(30~39%)이고 나머지는 기본 정책이 만든다** —
prefix를 끈 ablation만으로도 llm-d를 세 rate 전부에서 +21.2 / +24.3 / +17.0점 앞선다.

| req/s | token goodput v0.2 / ablation / llm-d | 거절률 v0.2 / ablation / llm-d |
|---|---|---|
| 35 | **13,147** / 11,417 / 8,295 | **16.0%** / 25.3% / 51.8% |
| 45 | **13,003** / 11,418 / 6,940 | **35.4%** / 43.2% / 71.5% |
| 55 | **13,184** / 10,834 / 7,168 | **46.3%** / 53.7% / 76.5% |

그림: `results/aggregate_analysis/exp69_control`(세 arm), `.../exp68sweep`(두 arm).

### 4.2 엔진이 무엇을 하고 있었나 (55 req/s)

| | 네 엔진 실행 배치 합 | 클래스당 유효 인스턴스 | 엔진 prefix hit |
|---|---|---|---|
| **FluidServe v0.2** | **775** | **2.93** | 23.3% |
| llm-d | 318 | 3.60 | 38.9% |

**llm-d는 함대를 채우지 않은 채 76.5%를 거절한다** — 한 엔진이 그 구간의 5.7%를 배치 5건
미만으로 보낸다. **클래스 분리는 워크로드 수정 뒤에도 남는다**(2.93 대 3.60).

⚠ **우리 쪽 비대칭은 종류가 다르고 유리한 관측이 아니다**: 엔진 하나가 **KV 99.5%로 포화**된
채 요청 20.3건을 큐에 세우는 동안 나머지 셋은 44~51%에 큐가 1건 미만이다. §6.1이 그 자리다.

### 4.3 한 시간 동적 trace — **이 워크로드에서는 아직 안 돌렸다**

chat 42,357 대화와 swe transcript 18,154건이 필요한데 short7k에는 1,500건뿐이다. **새로
만드는 것이 별도 작업**이다. v0.1.1 문서 §4.2의 한 시간 값은 수정 전 워크로드다.

---

## 5. v0.2가 **아닌** 것

- **prefix 인식이 라우팅을 바꾸는 시스템이 아니다** — 판정과 TTFT 추정에만 들어간다(§1.2).
- **엔진에 캐시 상태를 묻는 시스템이 아니다.** 스케줄러가 자기 색인을 들고, 자기가 보낸
  것만 기록한다. **그러므로 축출을 볼 수 없고**, 그 오차는 보정 계수가 흡수한다.
- **migration을 쓰는 시스템이 아니다.** 어느 arm에서도 완료된 적이 없다 — 켠 조건에서 22번
  시도되어 22번 실패했고 측정 창 안에서는 시도조차 없었다.
- **한 시간 동적 trace에서 이 워크로드로 검증된 시스템이 아니다**(§4.3).
- **엔진 수가 넷이 아닌 곳에서 검증된 시스템이 아니다.** 전 실험이 네 대다.
- **요청 단위 길이 예측기를 쓰는 시스템이 아니다.** 클래스마다 분포 하나를 두고 그 클래스의
  모든 요청에 같은 값을 쓴다. **그렇다고 "예측하지 않는다"고 쓰면 안 된다** — 분포를 쓰는
  것 자체가 과거로 미래를 예측하는 것이다.
- **prefix 인식이 워크로드 하나에서만 검증된 것이다**(§6.3).

---

## 6. 미해결 (우선순위 순)

### 6.1 게이트가 병목이고, 지금 가장 큰 설계 축이다

EXP-67b가 `infeasible_total{reason}`을 세어 **gate 92~96%, memory 0%**를 냈다. **prefill을
정확히 만들고 나니 남은 것이 게이트다.** EXP-68의 엔진 계층이 같은 곳을 가리킨다(§4.2).

`gate = min(요청 자신의 명목 예산, 인스턴스 최솟값 × gateSlack)`이고 `gateSlack`의 배포값이
1.0이라 **인스턴스 최솟값이 구속한다.** `gateAllowance`가 그 인스턴스에 있는 것들의 명목
예산 최솟값이므로 **chat이 하나라도 있으면 50 ms**가 되고, chat이 도착의 76.9%라 run 시작
몇 초 만에 그렇게 된다.

**양 끝이 반대 방향으로 실패하고 둘 다 측정돼 있다.**

- `gateSlack = 1.0`: EXP-41의 한 시간 50~56분에서 `gateAllowance`가 네 인스턴스 전부 정확히
  50.0인데 실제 전달 속도는 55.6이고 `tightestAllowance`는 68.3~72.7이었다. **예산 100 ms짜리
  deepresearch가 45.0으로 판정받아 네 대 전부에서 실패**하고 10초 예산 중 8.85초를 붙들려
  있다가 15.7%가 거절됐다.
- 먼 쪽 끝(`ownBudgetGate` 또는 `gateSlack ≥ 2`): EXP-50이 한 시간에 **4.4점**을 잃었다.
  느슨한 클래스에 내준 용량이 도착의 76.9%인 chat에서 나오기 때문이다.

**어느 끝도 답이 아니고, 기본값의 선택은 함대를 어느 집계로 채점하는가에 대한 진술이다.**

### 6.2 블록 크기가 엔진과 맞는지 확인하는 장치가 없다

`--fluidserve-prefix-block-tokens`가 16이고 vLLM의 블록 크기와 맞아야 한다. 엔진 설정이
바뀌면 조용히 안 맞는다. **안 맞으면 hit이 0에 가까워져 v0.1.2의 동작으로 돌아갈 뿐 틀린
답을 내지는 않지만**, 그때 이득이 사라진 것을 알아챌 방법이 지금 없다.
→ 엔진의 블록 크기를 읽어 대조하거나, hit rate가 바닥이면 경고를 낸다.

### 6.3 prefix 인식을 워크로드 하나에서만 쟀다

EXP-67(수정 전 워크로드)과 EXP-69(수정 후) 둘 다 같은 믹스·같은 세 클래스다. **다른 믹스나
다른 프롬프트 구조에서 이득이 유지되는지 모른다.** 특히 swe의 공통 시스템 프롬프트가
구조적 공유의 73.2%를 만드는데, 그 길이를 줄이면 이득이 어디로 가는지 안 쟀다.

### 6.4 task 단위 지역성을 이 워크로드에서 잴 수 없다

`sortCandidates`가 prefix를 안 보므로 "캐시가 있는 곳으로 모이는가"가 열린 질문인데,
**이 워크로드에서는 어느 방향으로도 측정되지 않는다.** 수정 전에는 사본 12개가 동시에 와서
흩을 수밖에 없었고, 수정 후에는 task가 요청을 하나만 낸다 — 55 req/s에서 요청 두 개 이상인
task가 **8~92개**뿐이다(전체 약 25,000 요청 중).
→ **EXP-67 H2의 값을 인용하면 안 된다.**

### 6.5 프로파일이 얼마나 어긋나도 되는지 모른다

dr 프로파일이 실제의 3.4배 틀렸을 때 정적 60 req/s에서 **20.2점**을 잃은 점이 하나 있다.
EXP-63이 그 사이를 메우려 했으나 실패했다 — 한 클래스만 스케일하면 클래스 간 상대 가중치가
같이 바뀌어서 정확도가 아니라 분리 세기를 잰 것이 됐다. **"프로파일이 ±X% 어긋나면 Y점"이라는
문장은 아직 쓸 수 없다.**

### 6.6 45 req/s의 반복 간 편차

수정 전 워크로드에서 같은 설정 세 반복이 90.4~98.9로 **8.5점** 흔들렸다. 원인은 선호가 양의
되먹임이기 때문이다. ⚠ **새 워크로드에서는 훨씬 작다** — v0.2가 45 req/s에서 59.4~59.5(0.1점),
ablation이 48.9~49.6(0.8점)이다. **재생을 없앤 것이 편차를 줄였을 가능성이 있으나 반복이
둘뿐이라 확정하지 못한다.**

### 6.7 한 시간 trace, 함대 크기, 믹스 비율

셋 다 열려 있다. 함대 두 대(`DP_SIZE_LOCAL=2`, 도착률 절반)가 지금 하드웨어로 가능하고
motivation의 축 2를 시험하는 유일한 경로다.

---

## 7. 재현 방법

```bash
# 빌드 (툴체인 경로는 CLAUDE.md의 "빌드 / 배포")
export PATH=/home/nxclab/tools/go/bin:$PATH
export GOPROXY="https://proxy.golang.org,direct" GOFLAGS=-mod=mod
export TMPDIR=/home/nxclab/tools/gotmp GOTMPDIR=/home/nxclab/tools/gotmp
go build -buildvcs=false -o bin/scheduler-exp07 ./cmd/scheduler
md5sum bin/scheduler-exp07   # a96dac128ee7eeeed51949fa42910b58

# 배포하고 파드 안에서 검증한다 (spec을 읽는 것으로는 못 잡는다)
kubectl -n llumnix rollout restart deploy/scheduler
POD=$(kubectl -n llumnix get pods -l app=scheduler \
      --sort-by=.metadata.creationTimestamp -o jsonpath='{.items[-1].metadata.name}')
kubectl -n llumnix exec "$POD" -- md5sum /proc/1/exe

# 기본값이 실제로 켜져 있는지 — 기동 줄이 유일한 authority
python3 ms_dev/scripts/set_scheduler_profiling.py --policy fluidserve | grep "prefix="
#   ... gateslack=1.000, prefix=true, prefixcalib=true, ...

# 배포 기본 설정 arm
cd Agent_applications/agent_motivation_experiment/k8s/exp07
SESSION_PREFIX=expNNr1 ./run_exp67_prefix.sh arm fspfx m1 2700 8

# prefix를 끈 ablation
SESSION_PREFIX=expNNr1 ./run_exp67_prefix.sh arm fluidserve m1 2700 8

# 채점 (PRERUN 디렉토리를 반드시 뺀다 — llm-d 예열 주행이 글롭에 섞인다)
cd ../..
python3 analysis_scripts/request_level/exp22_fluidserve.py \
  --runs $(ls -d results/*expNN*_rpm_* | grep -v PRERUN) \
  --out-dir results/aggregate_analysis/expNN
```

⚠ **채점표가 첫 열에 찍는 `eqmix_off`는 클래스 균등 평균이다.** 이 문서의 수치는 전부
**요청 단위**다. 이 워크로드에서 chat이 요청의 76.9%라 두 값이 크게 갈린다(35 req/s에서
64.9 대 77.6).

---

## 8. 관련 문서

| 문서 | 무엇 |
|---|---|
| [fluidserve-how-it-works.md](fluidserve-how-it-works.md) | **§0이 요청 하나를 여섯 단계로 통과시킨다.** §2.4가 기준선 넷과의 차이, §3.9가 prefix 인식 |
| [fluidserve-v0.1.md](fluidserve-v0.1.md) | 결정 규칙·용량 모델·상수 아홉 개의 정본 |
| [fluidserve-v0.1.2.md](fluidserve-v0.1.2.md) | 앞 버전. 같은 코드에 prefix가 꺼져 있던 상태 |
| [fluidserve-v0.1.1.md](fluidserve-v0.1.1.md) | **§4의 실측은 수정 전 워크로드다** |
| [fluidserve-prefix.md](fluidserve-prefix.md) | prefix 인식의 설계와 §8의 워크로드 결함 |
| [fluidserve-implementation.md](fluidserve-implementation.md) | 시간순 기록. 끝이 항상 최신 |
| [motivation.md](motivation.md) | 이 설계를 정당화하는 논증 |
| [llmd-baseline.md](llmd-baseline.md) | llm-d 기준선 |
| `experiments/EXP-58/59/67/67b/68/69_*.md` | 이 버전을 만든 실험들 |
