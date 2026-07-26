# Agent Notes — llumnix_reproduce

Llumnix(Go 컨트롤플레인: scheduler + gateway) 포크. 여기에 라우팅/admission 계층의
연구용 정책을 구현하고, 부하 실험은 별도 저장소 `Agent_applications/`(자체 git, 이
저장소의 `.gitignore` 대상)에서 돌린다.

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
- **실험이 도는 동안 `bin/`의 바이너리를 덮어쓰지 말 것.** `--restart-per-condition`이
  조건마다 `rollout restart scheduler,gateway`를 하므로, sweep 중간에 새 바이너리를
  넣으면 **조건마다 다른 코드**로 측정된다. 소스는 고쳐도 되지만 빌드 산출물은
  실험이 끝난 뒤에 교체한다(급하면 다른 경로로 빌드).
- **실행 중인 셸 스크립트를 편집하지 말 것.** bash는 파일 오프셋을 기억한 채 이어
  읽으므로, 실행 도중 앞부분에 줄을 넣으면 엉뚱한 블록으로 점프한다. 실제로
  `run_exp22_fluidserve.sh`에 case 하나를 추가했다가 실행 중이던 smoke가 sweep 블록으로
  넘어가 의도치 않은 arm이 시작됐다. 편집은 실행이 끝난 뒤에 한다.
- 선재 문제: `pkg/cms/cms_read_client_test.go`가 `NewCMSReadClient` 인자 개수 불일치로
  `go vet ./...`을 실패시킨다. 이 저장소 작업과 무관하며 미수정 상태다. 빌드/테스트는
  패키지를 지정해서 돌린다(`go test ./pkg/scheduler/...`).

## 작업 규칙

- 실험 결과 디렉토리와 기존 소스는 삭제하지 않는다. 큰 작업은 별도 브랜치에서 한다.
- 실험 기록은 `Agent_applications/.../experiments/EXP-NN_*.md`에 **실행 전/중**에 쓴다.
- 분석 스크립트 수정 후에는 `python -m py_compile`로 문법 확인.
