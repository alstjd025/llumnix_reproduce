# FluidServe — 처음 보는 사람을 위한 개요

이 문서는 저장소를 처음 여는 사람이 **무엇을 만들었고, 어디에 무엇이 있고, 무엇부터 읽어야 하는지**를
10분 안에 파악하도록 쓴 것이다. 세부는 같은 디렉토리의 다른 문서로 넘긴다.

## 1. 무엇을 하는 시스템인가

LLM 서비스 하나가 SLO(service-level objective)가 서로 다른 요청을 함께 받는다. 이 프로젝트의 워크로드는
세 클래스다.

| 클래스 | 원천 데이터 | SLO (4 인스턴스 기본값, 논문 Table 2) |
|---|---|---|
| chat | ShareGPT 대화 | TTFT 5 s, 토큰당 50 ms |
| deepresearch | Search Arena 기반 합성 요청 | TTFT 10 s, 토큰당 100 ms |
| swe (coding agent) | SWE-bench 기반 agent 호출 기록 | TTFT 7 s, 토큰당 75 ms |

같은 인스턴스에 놓인 요청들은 하나의 decode batch에서 처리되고, batch의 한 iteration이 모든 요청을 한 토큰씩
진행시킨다. 그래서 **iteration 시간이 batch 안에서 가장 빡빡한 토큰당 예산을 넘지 않아야 한다.** chat 요청이
하나라도 있는 인스턴스는 batch 크기나 KV cache 점유율이 낮아도 50 ms보다 느려질 수 없고, 그만큼 더 받을 수
있는 요청이 줄어든다. 즉 인스턴스가 더 받아들일 수 있는 양(논문의 serving capacity)은 부하가 아니라 **그
인스턴스에 지금 있는 요청들의 SLO 구성과 진행 상태**로 정해지고, 요청이 진행되면서 계속 바뀐다.

기존 cluster scheduler는 두 방향으로 이것을 놓친다.
- **부하 균등화 계열**(Llumnix, llm-d, vLLM router): 인스턴스의 batch 크기·KV 점유율 같은 지금 상태로 routing하므로,
  토큰당 예산이 빡빡한 요청을 모든 인스턴스에 퍼뜨려 모든 batch의 허용 속도를 낮춘다.
- **클래스 분할 계열**(PolyServe): 클래스마다 인스턴스를 나눠 간섭은 없지만, 클래스별 수요가 움직이면 한 쪽
  인스턴스는 포화되고 다른 쪽은 남는다.

**FluidServe**는 요청이 도착할 때마다 각 후보 인스턴스에 대해 "이 요청을 더하면 가까운 미래(기본 100
iteration)에 그 인스턴스의 decode iteration 속도가, 그 인스턴스에 있게 될 모든 요청의 허용 속도 안에 머무는가"를
투영해서 판정한다(receding-horizon feasibility analysis). 되는 인스턴스가 있으면 그중 같은 SLO 클래스가 이미
많은 곳을 우선하고(demand-aware SLO affinity), 없으면 TTFT 마감이 허락하는 동안 기다렸다가 다시 판정하고,
그래도 안 되면 거절한다(admission control). 무엇이 정확히 어떻게 계산되는지는 `system.md`.

논문: NSDI '27 Fall submission #515, `ms_dev/submitted_version/nsdi27fall-paper515.pdf`(2026-09-23 제출).

## 2. 실험 환경

- B200 8장 한 노드에 Llama-3.1-70B-Instruct 인스턴스 4대(각 TP=2). 일부 실험은 Qwen2.5-72B(4대)와
  Llama-3.1-8B(8대, TP=1).
- 그 노드는 GPU Docker 컨테이너이고, 그 안에 단일 노드 k3s를 세워 Llumnix를 배포한다. 엔진은 수정하지 않은
  vLLM(이미지 `llumnix/vllm:20260306-165123`)이고 migration은 끈다.
- 논문 평가의 기준선 넷: **vLLM\***(arm `vllmcachet75`, vLLM router의 `cache_aware` 정책 이식),
  **Llumnix\***(arm `slot75`, Llumnix의 SLO 인식 변형 — 저장소 문서에서는 "Llumnix SLO"), **llm-d**(arm `llmdslot75`, predicted-latency
  scheduling, 별도 스택), **PolyServe**(arm `polyservept75`, 정적 클래스 분할에 원 논문의 메커니즘 여섯을 넣은 이식).
  Llumnix의 부하 균등화 정책(arm `loadbalance`, 문서에서는 "Llumnix")은 motivation 쪽 그림에만 나온다.
- 부하: 정적 rate sweep(Poisson 도착, 도착률 고정, 8분)과 한 시간 동적 trace(`traces/dynamic/canonical/
  dyn60_shift_m2Am1B_b1045.csv`: 도착률 10.8~45.0 req/s, 클래스 구성이 15분마다 바뀜). ⚠ 논문은 이 trace를
  "production-derived Azure trace"라고 쓰는데, 옛 기록은 클래스 구성의 이동 폭이 Azure 근거를 넘는 stress
  test라고 적었다 — `revision.md`의 확인 항목.

환경을 다시 세우는 절차와 마지막 재현 확인은 `reproduce.md`.

## 3. 저장소 지도

두 개의 git 저장소다. 바깥 저장소가 안쪽을 `.gitignore`로 제외하므로 커밋은 따로 한다.

```
llumnix_reproduce/                     바깥 저장소 (정책 코드)
├─ CLAUDE.md                           작업 규칙
├─ project/                            ★ 지금 상태 문서 (이 디렉토리)
├─ archive/                            2026-10-11 이전 기록 전부, archive/INDEX.md부터
├─ pkg/scheduler/policy/fluidserve*.go FluidServe 정책
├─ pkg/scheduler/policy/polyserve*.go, vllmcache.go, ...   비교 대상 이식
├─ cmd/                                scheduler·gateway 진입점과 플래그(cmd/config/config.go)
├─ deploy/neutral/...                  Llumnix 배포 매니페스트 (실제로 쓰는 것: full-mode-scheduling/load-balance)
├─ deploy/llmd/                        llm-d 스택
├─ deploy/profiling/<모델>/            정책이 읽는 지연·길이 프로파일 (모델·하드웨어마다)
├─ ms_dev/scripts/                     클러스터 구축(00~02), 정책·프로파일 적용(set_scheduler_profiling.py),
│                                      모델 전환(switch_model.py), 프로파일 생성
├─ ms_dev/data/excluded_runs.tsv       분석에서 제외할 run 목록 (분석 스크립트가 읽는다)
├─ ms_dev/submitted_version/           제출한 논문 PDF
├─ patches/                            엔진·게이트웨이 빌드용 패치 (vllm-sched의 client index 수정은 엔진 기동 때 적용)
└─ Agent_applications/                 안쪽 저장소 (부하 생성과 분석)
   └─ agent_motivation_experiment/
      ├─ run_experiment.py             러너: 부하 생성, 조건마다 클러스터 재시작, 메트릭 수집
      ├─ workloads/, workload_configs/ 워크로드 정의와 설정 (측정 입력 — 고치지 않는다)
      ├─ traces/                       도착 trace (측정 입력)
      ├─ k8s/exp07/                    논문 실험의 드라이버 18개와 러너 Job 템플릿 8개
      ├─ analysis_scripts/             채점(ladder95)과 그림이 쓰는 분석 코드
      ├─ paper_figures/                논문 그림 12개, 스크립트, 그린 값 CSV
      ├─ experiments/                  새 실험 기록 (EXP-141부터)
      └─ results/                      run 결과 (대부분 이 서버에 없다 — reproduce.md)
```

## 4. 무엇부터 읽나

1. `revision.md` — 지금 무엇을 하고 있고 무엇이 미해결인가
2. `system.md` — 구현이 실제로 무엇을 하는가 (논문 구성의 플래그 전체, 알려진 결함)
3. `evidence.md` — 논문의 각 그림·수치가 어느 run에서 왔고 무엇을 같이 적어야 하는가
4. `reproduce.md` — 환경 재구축과 실험 실행

과거의 설계 논의, 반증된 가설, 실험 140개의 기록은 `archive/`에 있다. 필요할 때만 `archive/INDEX.md`부터 연다.
