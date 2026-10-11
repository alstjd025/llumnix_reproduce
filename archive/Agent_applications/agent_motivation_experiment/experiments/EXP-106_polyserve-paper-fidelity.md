# EXP-106 — PolyServe 기준선에 논문의 메커니즘을 되살린다

**상태**: 구현 완료, **아직 걸지 않았다** (2026-08-27 16:0x KST 기준 EXP-105가 도는 중)
**바이너리**: `/home/nxclab/tools/staging/scheduler-polyserve-paper`, md5 `47f3e2b5`
**설정 편집**: `/home/nxclab/tools/staging/polyserve-paper/`에 대기, `apply.sh`가 넣는다
**대조 문서**: [`ms_dev/notes/polyserve-fidelity.md`](../../../ms_dev/notes/polyserve-fidelity.md) §9

---

## 1. 무엇을 묻는 실험인가

**"정적 클래스 파티션은 용량을 옮길 수 없다"는 우리 주장이, 그 기준선에게 논문이 가진
메커니즘을 전부 주고도 성립하는가.**

지금 표에 실려 있는 PolyServe는 그 질문에 답하지 못한다. 그 arm에는 **작동하는 admission
control이 없고**(거절률이 여덟 도착률 전부에서 0.0%다), **격리가 탄력적이지 않으며**
(오토스케일링을 범위에서 뺐다), **promotion이 없다**(자기 tier가 꽉 차도 다른 tier의 유휴
서버를 못 쓴다). 심사에서 "기준선을 약하게 세워 놓고 이겼다"는 반문이 나오면 지금은 답할
말이 없다.

**그러므로 이 실험의 값어치는 우리가 이기는 것이 아니라, 기준선이 세진 뒤에도 격차가 남는지를
아는 것이다.** 격차가 사라지면 그것도 결과다 — 그때 우리 주장은 "라우팅 계층이 필요하다"가
아니라 "PolyServe를 제대로 구현하면 충분하다"로 바뀌어야 하고, 그 편이 틀린 주장을 논문에
싣는 것보다 낫다.

## 2. 무엇을 바꿨나

여섯 스위치, 전부 기본값이 종전 동작이다. 자세한 것은 대조 문서 §9.2.

| 스위치 | 논문 | 무엇이 달라지나 |
|---|---|---|
| `--polyserve-admission-binds` | §4.3·§4.6 | 아무도 안 받아들이면 강제 배치 대신 보류, 자기 TTFT 예산이 소진되면 거절 |
| `--polyserve-steady-state-ignores-prefill` | §4.5 | 정상상태 추정에서 prefill 항을 뺀다 |
| `--polyserve-lazy-promotion` | §4.4 | 자기 tier가 꽉 찼을 때만 더 빡빡한 tier로, 호스트 예산으로 판정 |
| `--polyserve-partition=elastic` | §4.3 | 수요 계산을 없애고 idle pool로 (+1 = 보류, −1 = 마지막 서버가 빔) |
| `--polyserve-prefer-loaded` | §4.3 | admission 통과자 중 가장 부하 높은 서버 |
| `--polyserve-kv-admission` | (시뮬레이터가 모델링) | 최대 KV를 인스턴스의 KV 용량과 비교 |

**그리고 워크로드 설정을 `mix_short_m1_slofair.json`(m1f)로 바꾼다.** 지금까지 PolyServe는
balanced(m1)로 돌면서 swe를 **토큰당 25 ms**로 판정했는데, swe의 진짜 목표는 전체 시간
30초다. 25는 EXP-16에서 잰 유휴 디코드 ITL 중앙값이고 FluidServe는 그것을 tier 이름으로만
쓴다. 프로파일 표를 풀면 swe의 요청당 KV 7,306 토큰에서 **25 ms는 배치 23까지, 30초를 속도로
환산한 55.7 ms는 배치 186까지** 허용한다 — **7~9배다.** 지금까지는 admission이 결정을 안
바꿔서 드러나지 않았고, 구속력을 갖게 하는 순간 그 값이 그대로 swe의 수용량이 된다.

⚠ **m1f로 옮기면 tier가 chat 50 / swe 52 / dr 100이 된다** — chat과 swe가 2 ms 떨어진다.
**PolyServe의 분류 축(토큰당 예산)으로는 이 워크로드의 세 클래스를 셋으로 나눌 수 없다는
뜻이고, 그것 자체가 논문에 적을 결과다.** 이식의 결함이 아니라 그 설계의 성질이다.

⚠ **swe의 전체 시간 예산이 30초로 남을지는 EXP-105가 정한다.** 40초가 되면 m1f의 `tbt_ms`도
같이 움직여야 한다((40,000 − 2,500)/494 = 75.9). tier 표를 워크로드 설정에서 유도하도록
바꿨으므로 그 파일 하나만 고치면 된다.

## 3. arm

| arm | 설정 |
|---|---|
| `polyserve` | 종전 그대로. **m1**, 스위치 전부 기본값. 세션 대조용 |
| `polyservep` | 여섯 스위치 전부 켬 + **m1f**. 논문 충실판 |
| `polyservep_noadm` | `polyservep`에서 `admission-binds`만 끔 |
| `polyservep_nopool` | `polyservep`에서 `partition=demand` (선택 규칙은 최고부하 유지) |

**⚠ `elastic`은 `prefer-loaded` 없이 쓰지 않는다** — 최저부하 선택이면 마지막 서버가 비지
않아 −1이 한 번도 안 일어나고, 배분이 처음 도달한 값에 굳는다. 그 조합은 결과를 idle pool에
대한 증거로 오독하게 만든다. 바이너리가 그 조합에 경고를 찍는다.

## 4. 실행 전에 적는 판정 규칙

**주 지표는 점수가 아니라 기전이다.** 지금 이 arm의 문제는 점수가 낮은 것이 아니라
**메커니즘이 돌지 않는 것**이므로, 먼저 확인할 것은 "논문의 각 단계가 실제로 일어났는가"다.

### 4.1 유효성 검사 — 하나라도 실패하면 그 run은 판정에 쓰지 않는다

1. **`scheduler_polyserve_placement_total{stage="forced"}`가 0이어야 한다** (`polyservep`에서).
   0이 아니면 `admission-binds`가 안 먹은 것이고, 그러면 그 조건은 대조군과 같은 설정이다.
2. **거절률이 0.0%가 아니어야 한다.** 여전히 0.0%면 보류가 한 번도 deadline까지 가지 않은
   것이므로, `refused_total`의 사유 분포를 먼저 보고 원인을 적는다.
3. **`scheduler_polyserve_placement_total`이 결과 디렉토리에 실제로 저장돼 있어야 한다.**
   Go가 내보내도 `llumnix_metrics.py`의 목록에 없으면 유실된다(EXP-96에서 카운터 한 벌을
   그렇게 잃었다). 본 실험 전에 짧은 조건 하나로 `grep -c`로 확인한다.
4. **기동 줄에 여섯 스위치가 전부 찍혀야 한다** — `admissionbinds=true, steadynoprefill=true,
   promotion=true, partition=elastic, preferloaded=true, kvadmission=true`.
5. **tier 표가 `52:...`를 포함해야 한다** (m1f를 읽었다는 증거). `25:`면 balanced를 읽은 것이다.

### 4.2 기전에 대해 미리 적는 예상과, 그것이 틀렸다고 말해 줄 값

| # | 예상 | 반증 조건 |
|---|---|---|
| H1 | admission이 실제로 구속한다: `own_tier` 배치가 전체의 100%가 아니고, `pending`과 `refused`가 둘 다 0이 아니다 | `pending` + `refused` = 0이면 admission이 여전히 아무것도 안 막는 것 |
| H2 | 우리 규모(서버 4대·tier 3개)에서는 이용률을 promotion이 만든다: `promotion` 배치 수가 `scale_up` 배치 수보다 **많다** | `scale_up`이 더 많으면 pool이 예상보다 자주 비어 있지 않다는 뜻이고, §9.4의 서술을 고쳐야 한다 |
| H3 | pool은 대부분 비어 있다: `scheduler_polyserve_pool_servers`의 중앙값이 0 | 0이 아니면 함대가 이 부하에서 남아돈다는 뜻이므로 도착률을 다시 고른다 |
| H4 | 배분이 `25ms=2 50ms=1 100ms=1`에서 움직인다 — 사건 기반은 실제로 보류가 생기는 tier에 서버를 주는데, 수요 모형은 TPOT이 빡빡한 tier에 준다 | 안 움직이면 두 배분 규칙이 이 워크로드에서 같은 답을 낸다는 것이고, 그것도 적을 결과다 |
| H5 | KV 판정 조건이 preemption을 크게 줄인다 (지금 8분 조건에서 549회) | 안 줄면 preemption의 원인이 배치 결정이 아니라 다른 데 있다 |

### 4.3 점수는 어떻게 읽나

**세 가지를 같이 낸다** — offered 달성률(모든 도착이 분모), admitted 달성률, 거절률, token
goodput. 지금 이 arm은 거절률이 0.0%라 offered와 admitted가 같은데, **이 변경의 요점이 그
둘을 갈라놓는 것**이므로 하나만 보면 변경의 방향을 못 읽는다.

**그리고 이 arm의 종전 수치와 나란히 놓지 않는다.** 워크로드 설정(m1 → m1f)이 바뀌므로
같은 표에 들어갈 수 없다. **세션 대조를 위해 `polyserve`(종전 설정)를 같은 세션에 한 조건
넣는다.**

⚠ **EXP-53·57·71·80·86의 PolyServe 열은 이 변경 이후 값과 같은 표에 못 들어간다.**
`retracted.tsv`에 넣는 것은 재측정이 끝난 뒤에 한다 — 지금 넣으면 아직 대체값이 없다.

### 4.4 이 실험이 답하지 않는 것

- **PD-disaggregation**은 여전히 범위 밖이다. 논문의 이득은 그쪽이 더 크다(1.23× 대 1.18×).
- **§4.7의 dynamic chunking**은 엔진 쪽 메커니즘이고 우리는 엔진을 stock으로 고정했다.
  그러므로 이 실험은 "고정 8,192 청크의 co-location에서" 성립하는 답이다.
- **채점은 여전히 순간 기준**(TTFT 임계 + 평균 토큰당 시간, swe는 전체 시간)이고 논문의
  누적 마감이 아니다. 네 arm을 같은 규칙으로 채점하므로 비교의 공정성 문제는 아니지만,
  PolyServe의 메커니즘이 누적 마감을 전제로 설계됐다는 것은 그대로 적는다.

## 5. 실행 순서

1. EXP-105가 끝난다.
2. `/home/nxclab/tools/staging/polyserve-paper/apply.sh` — 러너 Job이 하나라도 돌면 거부한다.
3. 백업이 없으면 만든다: `cp -p bin/scheduler-exp07 /home/nxclab/tools/bin-backup/scheduler-exp07.pre-polyserve`
4. 배포는 `cp` 아니라 `mv`로 (`Text file busy`).
5. **짧은 조건 하나** — 기동 줄, tier 표, 계측 저장 여부를 §4.1로 확인한다.
6. 그 다음에 본 sweep.
