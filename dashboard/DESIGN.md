# 실험 대시보드 — 설계와 확장 방법

실험 파이프라인(클러스터 상태, 진행 중인 실험, 끝난 run의 점수와 시계열)을 브라우저 하나로 보기 위한 도구다.
2026-10-12 사용자 요청으로 만들었다. 사용자 한 사람이 SSH 터널로 접속해서 보는 것을 전제로 한다.

## 쓰는 법

```bash
cd /home/nxclab/llumnix_reproduce/dashboard
./run.sh start            # stop | restart | status | log
# 노트북에서:  ssh -L 8080:localhost:8080 <이 서버>  →  http://localhost:8080
```

- 127.0.0.1에만 연다. 인증이 없으므로 바깥 인터페이스에 열지 않는다(`config.yaml`의 `server.host`).
- 가상환경은 `/home/nxclab/tools/dashboard-venv`(시스템 Python의 pandas·numpy를 같이 쓴다). 로그는
  `/home/nxclab/scratch/dashboard.log`, 캐시는 `/home/nxclab/scratch/dashboard-cache/`.

## 지키는 원칙

1. **읽기만 한다.** kubectl은 `get`·`logs`와 `exec … md5sum /proc/1/exe`만 실행할 수 있다(`sources/k8s.py`가 다른 명령을
   예외로 막는다). 결과 디렉토리(`results/`) 안에는 아무것도 쓰지 않는다. 측정 경로의 파일(`bin/`, `workloads/`,
   `run_experiment.py` 등)은 읽기만 한다. 실험을 시작·중지하는 기능은 넣지 않는다(2026-10-12 사용자 결정).
2. **수치는 논문과 같은 코드에서 나온다.** 채점은 `analysis_scripts/request_level/deadline_ladder_attainment.py`의
   `score_run`(ladder95)과 `exp22_fluidserve.load_run`을 그대로 부른다(`sources/scoring.py`). 대시보드에서 채점 규칙을
   다시 구현하지 않는다. 같은 이름의 양이 서로 다른 정의로 계산되는 일(클라이언트 TBT 1/1.92 사고)을 막기 위해서다.
3. **끝난 run은 한 번만 채점한다.** 캐시 키는 metrics.csv·tbt_events.jsonl의 크기와 수정 시각, 그리고 채점 스크립트들의 md5다.
   채점 스크립트를 고치면 모든 run이 자동으로 다시 채점된다. 채점은 별도 프로세스에서 하므로 웹 서버가 멈추지 않는다.
4. **바뀌는 값은 `config.yaml` 한 곳에 둔다.** 경로, arm 이름 → 표시 이름, 클래스 예산, 알려진 바이너리 md5, 기대하는 스택 상태.

## 구조

```
config.yaml / config.py   설정
server.py                 api/의 router를 등록하고 web/을 내보내고, 끝난 run을 채점 대기열에 넣는다(30초마다)
sources/                  데이터 읽기. 파일 하나 = 데이터 원천 하나. 결과는 dict나 DataFrame만 반환한다
  k8s.py                  kubectl(읽기 전용 wrapper)과 짧은 TTL 캐시
  host.py                 nvidia-smi, 디스크, 파일 md5, 엔진 /health
  runs.py                 결과 디렉토리 목록, 이름 해석, 상태(running/done/incomplete), 제외 목록
  timeseries.py           server_metrics/*.jsonl (늘어난 부분만 이어 읽는다)
  dispatch.py             scheduler_dispatch.log의 routing·FluidServe 결정, 인스턴스별 클래스 구성
  scoring.py              ladder95 채점(별도 프로세스)과 캐시
checks/                   상태 검사. 파일 안의 함수 하나 = 검사 하나
  cluster.py              실험 전 확인(바이너리 md5, 파드, 엔진 4/4, migration, 러너 Job, …)
  run.py                  run 하나의 확인(도착률, 엔진 수, 오류 종류, 클라이언트·엔진 TBT 대조, …)
api/                      HTTP API. 모듈 하나 = 화면 하나
web/                      index.html, app.js(페이지 등록), lib/(API·차트 도구), pages/(페이지 하나 = 파일 하나)
tests/                    test_known_runs.py(답을 아는 run으로 수치 검증), screenshot.py(화면과 콘솔 오류 확인)
```

## 확장하는 법

**검사를 하나 추가한다** — `checks/`의 아무 파일(새 파일도 된다)에 함수 하나를 쓴다. 자동으로 등록되어 화면에 나온다.

```python
@check("run_xxx", "run", "화면에 나올 제목(무엇이 참이어야 하는가)",
       "이 검사가 생긴 과거 사고. 경고가 뜨면 화면에 같이 나온다")
def run_xxx(ctx):          # cluster 검사는 ctx가 None, run 검사는 dict(name, dir, info, score, requests, engines)
    return Result("ok" | "warn" | "fail" | "na", "한 줄 요약", evidence=["근거 행", ...])
```

검사를 추가하면 **답을 아는 run에 한 번 돌려 본다**(정상 run에서 fail이 나면 검사가 틀린 것이다 — `tests/test_known_runs.py`가
정상 run 다섯에서 fail이 없는지 확인한다). 검사 코드가 바뀌면 run 검사 캐시는 자동으로 무효가 된다.

**데이터 원천을 추가한다** — `sources/`에 모듈을 만들고 dict를 반환하는 함수를 둔다. 웹에 직접 의존하지 않게 한다.

**API를 추가한다** — `api/`에 모듈을 만들고 `router = APIRouter()`를 둔다. server.py는 고치지 않는다.
응답 전에 `api.jsonable()`을 거친다(numpy 값과 NaN을 JSON으로 바꾼다).

**페이지를 추가한다** — `web/pages/<이름>.js`에 `export async function render(el, params)`를 쓰고(주기적으로 갱신하면 정리 함수를
반환), `web/app.js`의 `PAGES`에 한 줄을 더한다. 그래프는 `lib/charts.js`의 `timeLines`·`timeBars`·`cdf`만 쓴다.
시간축 그래프에 같은 `group`을 주면 확대·커서가 같이 움직인다.

**바꾼 뒤 확인** — `./run.sh restart`, `tests/test_known_runs.py`, 그리고 화면을 바꿨으면
`PLAYWRIGHT_BROWSERS_PATH=/home/nxclab/tools/pw-browsers /home/nxclab/tools/dashboard-venv/bin/python tests/screenshot.py <디렉토리> '#/runs'`
(콘솔 오류가 있으면 종료 코드 1).

## 알아 둘 함정

- ECharts에서 두 축이 모두 `value`이면 쌓기(stack)가 가로축에 적용된다. 시간축은 `type: "time"`(ms)로 둔다(`lib/charts.js`).
  쌓은 그래프에서는 `sampling`(점 솎아 내기)을 끈다. 계열마다 다른 점이 남아 쌓기가 어긋난다.
- 러너는 거절된 요청에도 `is_error`를 표시한다. 오류를 셀 때는 `errored & ~rejected`.
- 수집기는 cold restart 전부터 돌기 때문에 `scheduler.jsonl`에 재시작 전 인스턴스 번호가 섞여 있다. 그 run의 routing에 쓰인
  인스턴스만 남긴다.
- 스케줄러 로그 시각(glog)은 UTC이고 연도가 없다. 연도는 run 이름의 YYMMDD에서 가져온다.
- `pkill -f <이름>`은 그 이름을 명령줄에 가진 셸 자신도 죽인다. 중지는 `run.sh stop`(pidfile의 프로세스 그룹)으로 한다.
- 시스템의 오래된 `websockets` 패키지가 uvicorn과 맞지 않아 `ws="none"`으로 띄운다(웹소켓을 쓰지 않는다).

## 계획 (2026-10-12)

| 단계 | 내용 | 상태 |
|---|---|---|
| 1 | 구조, ① 상태, ③ 결과(목록·상세·설정 비교) | 완료 — 재현 run 다섯의 점수가 evidence.md §8과 일치 |
| 2 | ② 실시간(진행 중인 run과 드라이버 스크립트), 결정 분석 | |
| 3 | ④ 비교(arm × 도착률, 반복 간 편차), ⑤ 논문 대조(`claims.yaml`) | |
| 4 | FluidServe 내부 값 기록(스케줄러 /metrics, 실험 중에도 켠다 — 사용자 결정), ⑥ 실험 기록, run별 바이너리 md5·플래그 기록 | |

**run 디렉토리에 없는 정보** — 그 run이 쓴 스케줄러 바이너리 md5와 플래그, FluidServe 내부 값(`scheduler_fluidserve_*`
약 60개: 예측·실측 iteration 시간, 허용 속도, 클래스별 cap)은 run 디렉토리에 남지 않는다. 러너 수집기
(`llumnix_metrics.py`의 `SCHEDULER_METRICS`)가 정해진 목록만 남기기 때문이다. 측정 경로를 고치지 않고 단계 4에서
대시보드가 별도로 기록한다.
