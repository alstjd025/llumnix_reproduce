# Agent Notes — llumnix_reproduce

Llumnix(Go 컨트롤플레인: scheduler + gateway) 포크. 여기에 라우팅/admission 계층의
연구용 정책을 구현하고, 부하 실험은 별도 저장소 `Agent_applications/`(자체 git, 이
저장소의 `.gitignore` 대상)에서 돌린다.

## 복귀 절차 — 대화가 잘리거나 compaction된 뒤 여기부터 (사용자가 말하지 않아도 수행)

맥락이 사라진 상태에서 이어서 일하려면 **순서대로 세 가지만** 하면 된다.

**1. 무엇을 하고 있었는지 읽는다**

| 읽을 것 | 무엇이 있나 |
|---|---|
| [ms_dev/notes/fluidserve-v0.1.md](ms_dev/notes/fluidserve-v0.1.md) | **여기부터 읽는다.** v0.1의 자족적 명세 — 결정 규칙, 무엇을 측정하고 무엇을 설정하는가, 남은 상수 9개, 실측 결과, **v0.1이 아닌 것** |
| [ms_dev/notes/fluidserve-implementation.md](ms_dev/notes/fluidserve-implementation.md) | 시간순 경위. §13 v9~v18과 반증된 가정, §15~§22 v19~v22, §21 보류 항목 |
| implementation.md **§32** | **여기부터 읽는다. 기록된 TBT가 실제의 1/1.92였다 — §1~§31의 모든 attainment가 예산 약 2배로 판정된 것이다.** 세 방향 검증과 반증 시도는 §32.2·§32.7 |
| implementation.md **§33** | 설계 검토 — 포화에서 정책이 하는 일, `gate_allowance` 50ms 고정, agent 클래스 실패의 원인 |
| implementation.md **§31** | 그 이전의 상태 요약(정책 불변, 반증된 가정 여섯 개). **§31.7의 다음 계획은 §32와 EXP-39가 대체했다** |
| implementation.md **마지막 절** | 그 이후에 일어난 일. 항상 문서 끝이 가장 최신이다 |
| **정책 상태** | **EXP-27 이후 바뀌지 않았다.** v25~v28 네 개를 시도해 전부 기각(§31.1) |
| **닫힌 미해결** | §24의 "8ms 과대예측"은 **존재하지 않았다**(§27) — 통계량 불일치. §32가 같은 결론을 다른 방향에서 확인한다: 엔진 50.5ms와 모델 50.2ms가 처음부터 맞았고 `capacity_correction`이 0.995다 |
| **남은 방향** | ① EXP-38에 36·40 req/s를 추가해 knee 확정(약 2.8h, 헤드라인 숫자에 직접 영향) ② **EXP-39** 실제 60분 구간 재생 + `slo-hold35` arm. **버스트성 sweep은 철회**했다 — 실제 trace가 초 단위로 버스트하지 않는다(Poisson의 1.24~1.96배) |
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
- **세션을 넘는 비교는 무효다.** arm은 반드시 같은 세션 안에 있어야 하고, 반복을
  바깥 루프로 돌린다.
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

## 문서 지도

| 문서 | 내용 |
|---|---|
| [POLYSERVE_DESIGN_KO.md](POLYSERVE_DESIGN_KO.md) | PolyServe 이식 설계 (정본) |
| [POLYSERVE_PROGRESS.md](POLYSERVE_PROGRESS.md) | PolyServe 구현 시간순 기록, 함정 |
| [ms_dev/notes/fluidserve-v0.1.md](ms_dev/notes/fluidserve-v0.1.md) | **FluidServe v0.1 명세 (정본)** |
| [ms_dev/notes/fluidserve-design.md](ms_dev/notes/fluidserve-design.md) | FluidServe 설계 원안 |
| [ms_dev/notes/fluidserve-implementation.md](ms_dev/notes/fluidserve-implementation.md) | FluidServe 구현 결정 기록 |
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
- 선재 문제: `pkg/cms/cms_read_client_test.go`가 `NewCMSReadClient` 인자 개수 불일치로
  `go vet ./...`을 실패시킨다. 이 저장소 작업과 무관하며 미수정 상태다. 빌드/테스트는
  패키지를 지정해서 돌린다(`go test ./pkg/scheduler/...`).

## 작업 규칙

- 실험 결과 디렉토리와 기존 소스는 삭제하지 않는다. 큰 작업은 별도 브랜치에서 한다.
- 실험 기록은 `Agent_applications/.../experiments/EXP-NN_*.md`에 **실행 전/중**에 쓴다.
- 분석 스크립트 수정 후에는 `python -m py_compile`로 문법 확인.
