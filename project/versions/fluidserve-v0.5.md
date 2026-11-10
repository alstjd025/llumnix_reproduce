# FluidServe v0.5 — 무엇인가, 무엇이 아닌가

**이름**: v0.5 (지금까지 대화와 기록에서 "대조군", "정확 장부 + 엔진 대기 요청 제외", arm `fsexactprog`로 부르던 구성)
**날짜**: 2026-11-05 (EXP-147에서 처음 이 바이너리 조합으로 측정)
**바이너리**: 스케줄러 `3a1d2354` (`/home/nxclab/tools/staging/scheduler-notifyfix`, 바깥 `revision_scheduler` `c0fb7ab`까지),
게이트웨이 `05745270` (`/home/nxclab/tools/staging/gateway-notifyfix2`, `5dec8d6`까지). 둘 다 파드 안 `md5sum /proc/1/exe`로 확인.
**프로파일**: `deploy/profiling/llama31-70b-b200-tp2/` — v0.4와 같다.
**앞 버전**: v0.4 (`6dc9f035`, `archive/llumnix_reproduce/ms_dev/notes/fluidserve-v0.4.md`, `project/system.md`)

> **왜 minor인가.** 결정 규칙의 식은 v0.4와 같지만, 그 식에 들어가는 **"지금 이 인스턴스에서 처리 중인 요청"의 정의가 바뀌었다.**
> v0.4는 엔진이 알려 주는 처리 중 요청 수에 맞춰 목록을 잘라 내는 추정으로 누가 처리 중인지를 정했고, v0.5는 게이트웨이의 완료 통지를 받아야 목록에서 지운다.
> 허용 속도(가장 빡빡한 예산), outflow, 클래스 선호가 모두 이 목록에서 계산되므로 판정에 들어가는 양의 정의가 바뀐 것이다.

## 1. v0.4에서 바뀐 것

| 무엇 | v0.4 | v0.5 | 플래그 | 커밋 |
|---|---|---|---|---|
| 처리 중 목록 | 엔진의 running 수에 맞춰 추정(가장 큰 j부터 잘라 냄, 생존확률 < 0.02, 20분 나이 제한) | 게이트웨이 `/complete` 통지로 지움(20분 나이 제한은 통지 누락 대비로만 남음) | `--exact-residency`(`FS_EXACT_RESIDENCY=1`), 게이트웨이 `--notify-completion` | `bae80bb`, `f62c2bf` |
| 진행 토큰 수 j | 배치 뒤 엔진 step 수 | 엔진이 대기 중이라고 보고한 step은 빼고, 대기 중인 요청은 outflow에서 뺀다 | `--exact-progress`(`FS_EXACT_PROGRESS=1`) | `0f650c4`, `7c5f8ad`, `41a667f`(요청 ID 형식) |
| 완료 통지 경로 | — | 통지를 그 요청을 배치한 스케줄러 파드로 직접 보냄(응답 헤더 `X-Llumnix-Scheduler-Endpoint`) | 항상 | `c0fb7ab` |
| 통지 조건 | — | keepalive 재연결로 게이트웨이 ID가 바뀌어도 통지를 보냄 | 항상 | `5dec8d6` |
| 실험 운영 | — | 러너가 재시작 뒤 종료 중인 이전 스케줄러·게이트웨이 파드를 지우고 warmup 시작 | 안쪽 `42296c0` | |

그 밖의 플래그는 v0.4와 같다(arm 정의: `k8s/exp07/run_exp155_hour.sh`의 `fsexactprog`):
`FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true
FS_PREFILL_INTERLEAVE=true FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false FS_SWE_TBT_MS=75`, horizon 100, z 1.65, 게이트웨이 `--wait-scheduling-timeout 35000ms`.

## 2. 측정 (한 시간 trace `dyn60_shift_m2Am1B_b1045`, ladder95)

| 실험 | run | 모든 도착 | admitted | deepresearch admitted | 비고 |
|---|---|---|---|---|---|
| EXP-147 | 2 | 79.3 · 79.4 | 99.97 · 99.98 | 100.0 · 100.0 | |
| EXP-151 대조 | 2 | 79.6 · 79.2 | 99.96 | 100.0 | |
| EXP-154 대조 | 2 | 79.7 · 79.2 | 99.98 · 99.96 | 100.0 | |
| EXP-155 대조 | 2 | 79.4 · 80.0 | 99.93 · 99.97 | 99.9~100 | |
| (EXP-149·150·153) | 3 | 79.4 · 75.7 · 76.3 | | | 같은 결정 로직 + shadow 계산(v0.5.1·0.5.3). 150·153은 마지막 구간 갈림 |

- 끝난 요청이 목록에 남는 기록: EXP-147 이후 모든 run 0건.
- **마지막 구간 갈림**: 같은 구성의 한 시간 run 13개(EXP-147~154) 중 4개에서 45분 이후 엔진 하나가 deepresearch 쪽으로 넘어가 chat 거절이 급증하고 모든 도착이 76 안팎으로 떨어진다.
  갈리지 않은 run은 79.2~80.0에 모인다. 반복 간 편차를 읽을 때 이 확률(약 1/3)을 같이 적는다.
- 정적 표준 혼합(35 req/s)·deepresearch·swe만 혼합: EXP-142(정적, 바이너리 `3251836a` — 통지 누락 수정 전), EXP-146 B(`3a1d2354` + 게이트웨이 `f674aa25`, 재연결 수정 전).

## 3. 알려진 성질 (v0.5에서 측정)

- KV 투영은 horizon에 비례해 낙관적이다(실제 KV는 평평한데 일어나지 않는 감소를 예측; h100에서 끝점 −6.7 %, 정점 −9.5 %; EXP-155 0단계).
- 토큰당 시간 추정의 "대기 prefill 청크 수 ÷ h"가 사실상 클래스 분리 강도를 정한다(EXP-149~152). 분모만 50으로 바꾸면(v0.5 + pace horizon 50) 모든 도착 +5.9(EXP-151).
- 반복 prefill 몫을 실제보다 작게 잡는 것과 판정선(평균 iteration 시간 ≤ 예산 × 0.9)이 필요보다 엄격한 것이 서로 상쇄되어 지금 점수가 나온다(EXP-155).
- 미리 잡는 KV 청구(min(h, 기대 출력))와 지연 보정은 지워도 결과가 같다(EXP-153·154). 사용자 결정으로 일단 남겨 둔다.

## 4. v0.5.x — 기본값이 꺼진 플래그만 더한 바이너리

플래그를 켜지 않으면 v0.5와 같은 결정을 내린다(각각 단위 테스트로 확인). 실험에서는 아래 플래그를 켜서 썼다.

| 이름 | 스케줄러 | 커밋 | 더한 것 | 쓴 실험 |
|---|---|---|---|---|
| v0.5.1 | `783a574e` | `83299fb` | `--fluidserve-shadow-horizon`(같은 상태에서 다른 h로 다시 판정, 결정은 그대로) | EXP-149·150 |
| v0.5.2 | `4585b1f4` | `c8c831e` | `--fluidserve-pace-horizon`(토큰당 시간 추정의 분모만 따로) | EXP-151 |
| v0.5.3 | `302638b0` | `a21491c` | `--fluidserve-shadow-ledger`(제거 후보 장부) | EXP-153 |
| v0.5.4 | `359aac2c` | `37e4d52` | `--fluidserve-charge-prompt-only`, `--fluidserve-latency-correction` | EXP-154 |
| v0.5.5 | `5f6fa02a` | `81aeace` | `--fluidserve-peak-forecast`, `--fluidserve-slack-protect`(켜면 v0.6-후보1) | EXP-155 |

주의: shadow 계산(v0.5.1의 shadow, v0.5.3의 장부)을 켜면 결정 로직은 같지만 결정 1회 시간이 약 0.27 ms → 0.68~0.79 ms로 늘어난다.
