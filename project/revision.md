# 리비전 작업 기록

## 지금 상태

- **2026-10-11~12** — NSDI '27 제출(2026-09-23) 후 리비전 준비. 저장소를 정리했다: 기록 전부를 `archive/`로 옮기고
  `project/` 문서 다섯을 새로 썼다. 지운 것은 없다. 브랜치 `revision/reorg`(두 저장소 모두)에 커밋했고
  — 바깥 `4c3798f`·`5aaf1bc`·`df6d26d`, 안쪽 `c3d1c26` — **push는 사용자가 한다.**
- **`system.md`·`evidence.md`는 에이전트가 쓴 초안**이다. 아래 확인 항목 중 "직접 대조"로 표시한 것만 정리 담당이
  다시 확인했다. 리비전 중 틀린 곳이 보이면 바로 고친다.
- 제출 논문 PDF `ms_dev/submitted_version/`은 git에서 뺐다(`.gitignore`, 사용자 결정). 텍스트 추출본은
  `/home/nxclab/scratch/paper.txt`(`pdftotext -layout`, 두 단이 섞여 있다).
- **클러스터**: 2026-10-10에 다시 세웠다. k3s + Llumnix(Llama-3.1-70B × 4, TP=2), 스케줄러 `6dc9f035`, 게이트웨이
  `0efb664a`. llm-d 스택은 아직 안 세웠다. 도는 실험 없음.
- **재현**: EXP-108 정적 35 req/s(FluidServe·PolyServe 각 2회)와 EXP-109 한 시간 trace(FluidServe 1회)가 논문 값과
  반복 간 편차 안에서 일치(`evidence.md` 재현 확인 절).
- **데이터**: 논문 run 대부분(EXP-108 이후)은 맥 SSD에만 있다. 가져오는 시점은 사용자가 정한다.
- **실험 대시보드(2026-10-12, 사용자 요청)**: `dashboard/`, 브랜치 `feat/dashboard`(바깥 저장소). 읽기 전용 웹 페이지
  여섯(상태, 실시간, 결과, 비교, 논문 대조, 실험 기록)을 127.0.0.1:8080에 띄운다 — `dashboard/run.sh start`, 노트북에서
  `ssh -L 8080:localhost:8080`. 점수는 analysis_scripts의 ladder95 함수를 그대로 부르고, 재현 run 다섯의 값이
  `evidence.md` §8과 같다(`dashboard/tests/test_known_runs.py`). 실험 중에는 스케줄러의 FluidServe 내부 값과 그 run이 쓴
  바이너리 md5·플래그를 대시보드가 따로 기록한다(run 디렉토리에는 없는 값, 사용자 결정으로 실험 중에도 켠다).
  설계·확장 방법은 `dashboard/DESIGN.md`. 검증용으로 4분 smoke run 둘(`dash1012r1`, `dash1012br1`, FluidServe 35 req/s)을
  돌렸다 — 연구용 run이 아니므로 evidence.md에 넣지 않는다.
- **GPU 운영(2026-10-13)**: 서버 공지에 따라 실험하지 않는 동안 GPU 8장을 Sionna 워크로드에 넘긴다 —
  `ms_dev/scripts/gpu_idle.sh start|stop|status`. Sionna는 `~/NXC_GPU`(공유 경로 사본)와 `~/sionna/.venv`에 설치했고,
  설치가 `~/.bashrc`에 `LD_LIBRARY_PATH` 한 줄을 더했다(원본 `/home/nxclab/tools/bin-backup/bashrc.pre-sionna`).
  **2026-10-13 18:33부터 Sionna 모드**(엔진 0). 같은 날 17:10~17:37에는 powertest 프로젝트가 엔진을 내리고 GPU를 빌렸다.
- **다음**: 아래 확인 항목을 검토하고 리비전 범위를 정한다.

---

## 확인 항목 — 정리하면서 발견한 것 (사용자 확인 필요)

| # | 무엇 | 왜 중요한가 | 근거 |
|---|---|---|---|
| R1 | ~~Figure 1을 만든 스크립트가 저장소에 없다~~ → **다른 사람이 만든 그림이라 이 저장소에 파일이 없는 것이 정상이다(2026-10-12 사용자 확인).** 수치 대조는 R26 | 해결 | 사용자 |
| R2 | **Table 1(워크로드 프롬프트·응답 길이의 중앙값·P90·표준편차) 수치의 출처를 찾지 못했다** | 워크로드를 정의하는 표 | 저장소 전체에서 `556`, `1,580` 등을 검색함 |
| R3 | **EXP-138(Figure 16·17이 쓰는 run `260914_0539_exp138r1_noaffnocaphour_shift`)은 실험 기록 파일이 없다** | affinity의 효과를 보이는 그림 둘의 근거가 기록 없이 1회(`r1`)뿐일 수 있다 | archive 전체에 `EXP-138` 언급 없음. 드라이버 `k8s/exp07/run_exp138_noaffnocap_hour.sh`만 있다 |
| R4 | **논문은 한 시간 trace를 "production-derived Azure trace"라고 쓰는데, 옛 기록은 그 trace의 클래스 구성 이동을 "Azure 근거를 넘는 stress test"라고 적었다** | 평가의 주 워크로드를 어떻게 부르느냐의 정확성 문제 | `traces/dynamic/canonical/dyn60_shift_m2Am1B_b1045.csv`, 워크로드 설정의 `_comment`, archive의 EXP-93과 STATUS.md |
| R5 | **Figure 4·17(`fig_class_mix_hour.py`의 두 변형)을 그린 정확한 명령이 기록돼 있지 않다** | 다시 그릴 때 옵션을 CSV·PDF와 대조해 되살려야 한다 | `paper_figures/README.md` |
| R6 | **논문 구성(arm `fsv3capgnofrct75`)이 컴파일 기본값과 다르다** — 기본값으로 배포하면 논문 결과가 나오지 않는다 | 공개·재현 시 문제. 플래그 전체는 `system.md` | archive의 `fluidserve-v0.4.md` §2 |
| R7 | **엔진 `--api-server-count`가 git에 없다**(실행 중 클러스터에 직접 패치) | EXP-131~139의 엔진 설정을 확정할 수 없다 | `reproduce.md` §6.1 |

### `system.md`를 쓰면서 나온 항목 — 논문 서술과 코드의 차이

"직접 대조"는 정리 담당이 파일을 다시 열어 확인한 것, 나머지는 `system.md` §7·§8의 근거를 따른다.

| # | 무엇 | 확인 | 근거 |
|---|---|---|---|
| R8 | **Table 1의 SWE-agent 행과 Search-arena 행이 서로 바뀐 것으로 보인다.** 응답 길이 중앙값/P90이 논문은 SWE-agent 971/1,233, Search-arena 517/648인데, 정책이 읽는 길이 프로파일은 swe 486/645, deepresearch 973/1,246이다. 프롬프트 길이도 같은 방향으로 바뀐 것으로 보인다(swe 입력 평균 6,812 토큰, deepresearch 약 4.1k인데 표는 SWE 3,993, Search-arena 5,725) | 직접 대조 (응답 길이) | `deploy/profiling/llama31-70b-b200-tp2/fluidserve.json`의 `classes[]`; 입력 길이는 archive의 워크로드 기록 ⚠ |
| R9 | **Algorithm 1은 TTFT 마감을 feasibility 조건으로 쓰는데, 논문 구성에서는 꺼져 있다**(`--fluidserve-deadline-feasible=false`). TTFT 추정은 계속 기다릴지(hold) 정하는 데와 거절 사유 표시에만 쓰인다. feasibility는 토큰당 속도 게이트, 상주 요청 보호 조건, KV 판정 셋이다 | 직접 대조 | `pkg/scheduler/policy/fluidserve.go:2180-2183` |
| R10 | 논문은 KV 판정이 남은 생성 전체의 최대 점유를 본다고 쓰는데, 코드는 100 iteration 지평 안에서 끊고, 방출 추정에 평균 − 1.65 표준편차를 쓴다(논문에 없음) | system.md | §7 |
| R11 | 논문은 workload class와 SLO class를 일부러 분리한다고 쓰는데, 코드는 길이 프로파일과 예산을 같은 TBT tier 키로 찾는다. swe는 tier 키 25로 보내고 실제 예산 75 ms는 스케줄러 플래그로 준다 | system.md | §3·§7 |
| R12 | 논문은 클래스별 출력 길이 분포를 "완료된 요청에서" 유지한다고 쓰는데, 논문 구성에서는 2026-08-02에 만든 오프라인 파일이다 | system.md | §3.3·§7 |
| R13 | 논문 구성에서 켜져 있는데 논문에 설명이 없는 메커니즘: 인스턴스별 iteration 시간 보정, prefix 인식과 그 보정, 명목 예산에만 붙는 0.9 여유, 예산을 이미 못 지키는 상주 요청의 제외, 클래스별 인스턴스 상한(guardrail) | system.md | §5·§7 |
| R14 | 논문의 결정 비용 192 µs는 요청 하나가 아니라 스케줄링 호출 하나의 값이다(보류된 요청은 다시 묻기 때문에 요청당 1.4~1.8회). 그리고 `-v 0`에서 1회 잰 값이고, 성능 실험은 전부 `-v 4`(약 320 µs)로 돌았다 | system.md | §8.10, archive의 EXP-140 |
| R15 | Figure 16·17의 "FluidServe w/o Affinity"는 EXP-138이고, **클래스 선호와 인스턴스 상한을 둘 다 끈** 구성이다 | system.md | §8.2 |
| R16 | Figure 15(지평 민감도)의 지평 플래그는 최소 다섯 군데에서 읽힌다. 지평을 바꾸면 투영 창만이 아니라 속도 추정의 prefill 비중 등도 같이 바뀐다 | system.md | §8.2, archive의 EXP-133 |
| R17 | "5.6K line"은 2026-09-09 커밋 기준 FluidServe 파일 여섯(테스트 제외)의 줄 수 5,614다. 논문 바이너리의 소스는 5,414줄, 지금 소스는 5,925줄, 주석·빈 줄을 빼면 약 2.7~2.9K | 직접 대조 (5,614) | system.md §2.1 |

### `evidence.md`를 쓰면서 나온 항목 — 논문 수치와 데이터의 차이

| # | 무엇 | 확인 | 근거 |
|---|---|---|---|
| R18 | **R8의 Table 1 행 바뀜이 두 번째 출처로도 확인된다.** archive의 그림 README "Length distributions" 절(EXP-53, 15 req/s, 27,560건)에서 3,993 / 7,519 / 2,411 / 971 / 1,233 / 274는 deepresearch 값이고 5,725 / … / 119는 swe 값이다. Figure 6(b)의 출력 중앙값(Search Arena 969, SWE 489)도 같은 방향이다. chat 행은 맞다 | evidence.md (R8과 독립) | evidence.md Table 1 절 |
| R19 | **request goodput의 정의가 논문(§6.1: 마지막 토큰이 마감 안)과 채점 코드(토큰의 95% 이상이 마감 안)에서 다르다** | evidence.md | `analysis_scripts/request_level/deadline_ladder_attainment.py` |
| R20 | **Figure 15·18은 다른 그림과 채점 규칙이 다르다** — `ladder95`가 아니라 옛 규칙(TTFT와 평균 토큰 간 시간). 같은 run이 한 규칙에서 13,365, 다른 규칙에서 13,932 tok/s다 | evidence.md | evidence.md Fig 15·18 절 |
| R21 | **Figure 15에서 배포값 지평 100 iteration이 최적이 아니다.** Llama-70B goodput이 지평 25: 14,394, 50: 14,328, 100: 13,246 tok/s(각 2회 평균), Qwen-72B도 25·50이 100보다 높다 | 직접 대조 | `paper_figures/exp131_horizon_goodput.csv` |
| R22 | **Figure 16·17의 "w/o Affinity"(EXP-138, 클래스 선호와 인스턴스 상한을 둘 다 끔, 1회, 실험 기록 없음)가 배포 구성보다 token goodput이 약간 높고 거절률이 낮다**(11,682 대 11,586~11,636 tok/s, 16.2% 대 19.9~20.6%). 논문이 이 그림으로 주장하는 것(KV 점유·batch가 커진다)은 데이터와 맞지만(KV p50 46.8% 대 67.1~67.4%), goodput 차이는 반복 1회라 판정할 수 없다 | 직접 대조 | `paper_figures/kv_batch_attain_4arms.csv` |
| R23 | **Figure 18의 SLO 배수 1.3에서 PolyServe가 FluidServe와 같거나 앞선다**(모든 도착 대비 83.8 대 81.9 / 81.2, PolyServe 1회). 본문은 "approach"라고 쓴다 | 직접 대조 | `paper_figures/exp139_slo_scale_goodput.csv` |
| R24 | **서론의 "1.42–3.11×"(합성 부하 token goodput)는 vLLM을 빼고 admission control이 있는 기준선 셋에 대한 35~70 req/s 범위다.** 문장은 vLLM도 나열한다. vLLM 대비로는 4.6~23배 | evidence.md | evidence.md 본문 수치 절 |
| R25 | **Figure 11·12의 (c) Llama-8B 패널**: 예산을 절반으로 줄인 조건이고(표준 예산에서는 PolyServe가 앞섰다, 1회), vLLM run은 도착의 59.4%가 클라이언트 연결 고갈로 끝났다(부하 생성기가 무너진 구간). 헤드라인 3.67×는 이 패널의 FluidServe 대 Llumnix\*이고 Llumnix\*는 요청의 11%가 거절 외의 오류로 실패했다 | evidence.md | evidence.md Fig 11·12 절 |
| R26 | Figure 1의 다섯 점 중 넷은 EXP-109 반복 2와 1~1.5% 안에서 맞지만, vLLM의 y값(24%)은 어떤 정의(admitted 45.4%, 미완료를 실패로 센 21.7%)와도 맞지 않는다. 스크립트가 없어 PDF의 벡터 좌표에서 읽은 값이다 | evidence.md ⚠ | R1과 같은 그림 |
| R27 | 한 시간 trace의 token goodput "1.10–4.04×"와 기준선별 범위는 Llama-70B 패널(llm-d 대비 1.41배, PolyServe 대비 1.35배)만 이 서버의 데이터로 확인된다. Qwen 반복 2와 Llama-8B는 맥 SSD 데이터가 필요하다 | evidence.md ⚠ | — |
| R28 | "Llumnix\*가 원래 Llumnix보다 낫다"는 옛 규칙으로 채점한 EXP-53에만 기대고, 부하 균등화 Llumnix는 논문 규칙으로 잰 적이 없다 | evidence.md | — |
| R29 | 단일 반복인 그림·칸: Figure 1·4·5·17 전부, Figure 18의 0.9·1.1배 칸 전부와 1.3배의 PolyServe·Llumnix\* | evidence.md | evidence.md 각 그림 절 |

### 2026-10-12 이후 리비전 작업 중에 나온 항목

| # | 무엇 | 확인 | 근거 |
|---|---|---|---|
| R30 | **FluidServe의 처리 중인 요청 장부는 관측이 아니라 추정이다.** full-mode의 엔진 상태에는 running 요청의 ID가 없고(`cms.proto` `InstanceStatus`, 대기 요청 ID만 있다) 완료 신호도 없다. 장부(`fluidserve_registry.go` `reconcile`)는 클래스 출력 길이 분포로 "끝났을 것"을 판정하거나, 장부 수가 엔진의 running 수보다 많으면 **먼저 보낸 요청부터** 지운다. 그래서 수는 맞아도 누가 남았는지가 틀릴 수 있고(나중에 보낸 짧은 요청이 끝났는데 먼저 보낸 긴 요청을 지움), 그 인스턴스의 가장 빡빡한 예산(gate)이 틀린 집합에서 계산된다. demo1016r1(35 req/s 8분)에서 장부에서 빠진 13,078건 중 12,626건(96%)이 수 맞추기, 452건이 길이 분포 판정이었다. 논문은 처리 중인 요청을 추적한다고 서술한다 | 직접 대조(코드·계수) | `revision_scheduler` 브랜치에서 수정(아래 결정 기록) |
| R31 | **Llumnix\*는 새 요청 자신의 TPOT 예산으로만 판정한다**(`filters.go` `thresholdFor`, 저장소가 07-28에 넣은 요청별 예산). 인스턴스에서 처리 중인 요청의 예산은 보지 않는다. llm-d(`podMinTPOTSLO`)와 FluidServe는 처리 중인 요청 중 가장 빡빡한 예산을 쓴다. 드라이버 `run_exp108_t75.sh`의 `slo` arm 주석("전역값 한 쌍")은 07-28 변경 전의 설명으로 낡았다 | 직접 대조 | `revision_scheduler`에 변형 추가 |

---

## 결정 기록

### 2026-10-11 — 저장소 정리

- **기록은 바깥 저장소 `archive/` 한곳에**(사용자 결정). 안쪽 저장소의 기록 1,036개는 복사·md5 대조 후 안쪽에서
  `git rm`했다(그 저장소의 git 이력에는 남는다). 원래 경로를 보존: `archive/<저장소>/<원래 경로>`.
- **바깥 `.gitignore`가 `*.csv`, `*.txt`, `*.jsonl`, `*.log`, `AGENTS.md`와 `bin/`·`logs/`·`env/` 같은 디렉토리를
  무시하므로**, `archive/.gitignore`에 전부 다시 포함하는 규칙을 두었다. 없었으면 그림 옆 CSV 등이 git에 안 들어간
  채 안쪽에서 지워질 뻔했다.
- **남긴 코드는 의존을 추적해서 골랐다**: 논문 그림 12개의 스크립트(10개)에서 import·경로·캐시 생성기를 재귀로
  따라가 Python 파일 40개(그림 스크립트와 보조 모듈 17, 분석 스크립트 17, 러너 모듈 6), 논문 실험(EXP-64·77·108~110·113·125·126·131·132·136~139)의 드라이버 18개와 그 드라이버가
  실제로 실행하는 템플릿 8개, 워크로드 설정 43개. 측정 입력(`workloads/`, `traces/`, 남긴 워크로드 설정)은 바이트
  단위로 그대로 두었다.
- `ms_dev/notes/excluded_runs.tsv`는 기록이 아니라 분석 입력이라 `ms_dev/data/`로 옮겼다(`exp131_figures.py`가 읽는다).
- 남은 코드의 주석 속 `ms_dev/notes/` 경로는 `archive/llumnix_reproduce/ms_dev/notes/`로 바꿨다(동작 무변경, Go 빌드와
  정책 테스트 통과).
- `CLAUDE.md`를 1,320줄에서 약 190줄로 줄였다. 실험 운영 함정 A~F와 판정 규칙 전문은 지우지 않고
  `.claude/skills/exp-run/traps.md`로 옮겼다. 세션 시작 훅은 이 파일의 "지금 상태" 절을 넣는다.
- 새 실험 기록은 `Agent_applications/agent_motivation_experiment/experiments/`에 EXP-141부터 쓴다.

### 2026-10-19 — 처리 중인 요청을 관측으로 추적한다 (브랜치 `revision_scheduler`)

- **방식**: 게이트웨이가 요청의 응답이 끝날 때(성공·실패 모두) 스케줄러에 `/complete`를 보낸다(게이트웨이
  `--notify-completion`). 엔진은 고치지 않는다 — 논문의 "without modifying the serving engine"을 유지하고, llm-d의 EPP와
  Llumnix lite-mode가 쓰는 것과 같은 방식이다(사용자 결정, 엔진이 running ID를 보고하는 방식과 비교한 뒤).
  기존 `/release`는 건드리지 않는다(다른 정책의 CMS local account에 영향을 주므로).
- **장부의 구간**: 스케줄러가 보낸 순간부터 완료 통지까지. 엔진 대기열에 있는 요청도 포함(사용자 결정).
  완료 통지가 배치 기록보다 먼저 와도 이기도록 완료를 5분 기억하고, 통지가 오지 않는 경우는 20분 나이 제한으로 지운다
  (`pkg/scheduler/policy/residency.go`).
- **FluidServe** `--exact-residency`: 장부에서 요청이 빠지는 것은 완료 통지(그리고 엔진 재시작, 나이 제한)뿐이다.
  길이 분포 판정과 수 맞추기는 하지 않는다. 진행 토큰 수 j와 decode 시작 시각은 지금처럼 step 번호로 추정한다
  (사용자 결정: 바꾸는 것을 "누가 처리 중인가" 하나로).
- **Llumnix\* 변형** `--slo-incumbent-tpot-gate`: TPOT 필터의 기준을 0.9 × min(새 요청의 TPOT 예산, 그 인스턴스에서
  처리 중인 요청들의 **명목** TPOT 예산 최솟값)으로 바꾼다. TTFT 필터와 한 단계 TPOT 예측은 그대로(사용자 결정).
- 세 플래그 모두 기본 꺼짐이고, 꺼 두면 지금 동작과 같다. 단위 테스트 `residency_test.go`(옛 장부가 먼저 보낸 요청을
  지우는 것도 재현한다). 실험(arm, 조건)은 아직 정하지 않았다.
