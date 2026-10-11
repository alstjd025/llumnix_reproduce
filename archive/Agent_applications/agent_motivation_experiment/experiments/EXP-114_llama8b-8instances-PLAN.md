# EXP-114 (계획) — 8 인스턴스 × TP=1, Llama-3.1-8B-Instruct

**2026-09-04 작성, 실행 전.** 사용자 지시: 8 인스턴스 구성을 **Llama-3.1-8B 먼저**, 그 다음
**Qwen2.5-14B-Instruct**. 이 파일은 압축 뒤에도 이어서 할 수 있도록 계획과 근거를 담는다.

## 0. 지금 클러스터 상태 (시작 전)

함대는 **Qwen2.5-72B-Instruct, 4 인스턴스 × TP=2** 이고 EXP-113 이 끝나 비어 있다.
`python3 ms_dev/scripts/switch_model.py --verify` 로 확인한다.
되돌리려면 `--to llama31-70b-b200-tp2` 또는 `--to qwen25-72b-b200-tp2`.

## 1. 이번엔 변수가 둘이다 — 그것을 밝히고 시작한다

지금까지는 한 번에 하나만 바꿨다(Llama-70B → Qwen-72B, 함대 모양 동일). 이번엔 **모델
크기(70B → 8B)와 함대 모양(4×TP2 → 8×TP1)이 함께** 바뀐다. 8B 를 TP=2 로 4 대 올리는 것은
가중치가 15 GB 인데 두 장을 쓰는 낭비이고, **8 인스턴스가 이 실험의 질문**이므로 섞는 것이
맞다. **대신 결과를 쓸 때 "두 변수가 함께 바뀌었다"를 반드시 적는다** — 어느 쪽 때문인지
가르려면 8B 를 4×TP2 로도 돌리는 조건이 따로 필요하다(지금 계획에는 없다).

## 2. 인스턴스 4 대가 코드에 박혀 있다 — 측정 전에 고친다

**⚠ 가장 위험한 것**: `run_experiment.py` 의 `--engine-ports` 기본값이
**`8000,8001,8002,8003`** 이다. 안 고치면 **여덟 엔진 중 넷의 지표가 수집되지 않고 성능
수치는 정상으로 보인다.** EXP-96 에서 메트릭이 조용히 유실된 것과 같은 형태다.

그 밖에 고쳐야 하는 곳:

| 파일 | 무엇 |
|---|---|
| `ms_dev/scripts/llmd_endpoints.py:33` | `ENGINE_PORTS = (8000..8003)` — llm-d 를 8 대에 태우려면 필수 |
| `k8s/exp07/run_exp{68,89,108,109,113}_llmd.sh` | 각 `ENGINE_PORTS="8000 8001 8002 8003"` |
| `k8s/exp07/run_exp111_profile.sh` | `wait_engine` 이 네 포트만 확인 |
| `analysis_scripts/request_level/plot_kv_flows.py`, `plot_kv_tank.py`, `plot_per_engine_slo_signals.py`, `plot_slo_vs_throughput.py`, `analyze_tbt_drivers.py` | `ENGINE_PORTS` 고정 |
| `analysis_scripts/request_level/exp41_engine_view.py:118` | `ENG_C` 엔진별 색 표가 네 개 |

**엔진 수에서 유도하도록 고친다**(하드코딩을 하나 더 만들지 않는다).

## 3. 순서

**1 단계 — 배포와 계측을 8 대로 (측정 없음).**
`switch_model.py` 표에 `llama31-8b-b200-tp1` 행 추가: `model=meta-llama/Llama-3.1-8B-Instruct`,
`tp=1, dp=8`, `max_model_len=40960`(모델은 131,072 까지 되지만 우리 워크로드 최대가 입력
16,696 + 출력 7,830 = 17,588 이라 여유가 충분하고, 다른 조건과 같은 천장을 쓰는 편이 비교에
낫다), `profile_dir=llama31-8b-b200-tp1`. 스냅샷 경로는 표에 핀으로 박는다.
엔진 시작 스크립트의 GPU 배분은 `GPU_START = TP_SIZE * i` 라 TP=1·DP=8 이면 GPU 0~7 로
자동으로 맞는다. **전환 뒤 여덟 포트 전부가 `meta-llama/Llama-3.1-8B-Instruct` 를 보고하는지
확인한다** — "네 포트가 답한다" 로는 교체 직전의 옛 파드가 통과시킨 전례가 있다.

**2 단계 — 2 분짜리 조건 하나로 계측을 검증한다.**
`server_metrics/` 에 **engine_8000~8007 여덟 개가 다 생기는지** 본다. 이걸 먼저 안 하면 본
측정이 끝난 뒤에 절반이 없는 것을 알게 된다.

**3 단계 — 프로파일 재측정 (EXP-111 과 같은 절차, 약 1.5 시간).**
`measure_ttft_sweep.py` 로 `ttft.json`, 계측 스케줄러를 켠 step dump run 으로 `tpot.json` 과
`fluidserve.json`. 정책은 `load-balance`(FluidServe 로 돌리면 지금 만드는 표를 필요로 하는
순환이 생긴다). 출력 디렉토리는 `deploy/profiling/llama31-8b-b200-tp1/`.
**TP=1 이라 decode step law 가 달라진다** — tensor-parallel 통신이 없어지므로 계수가 바뀐다.

**4 단계 — 예산 결정. 여기서 사용자 판단이 필요하다.**
지금 클래스 예산(chat 50 / swe 75 / deepresearch 100 ms per token)은 **Llama-70B 의 유휴
decode ITL 에서 유도한 값**이다(m1 의 25 ms 가 EXP-16 에서 잰 유휴 ITL 중앙값이었다). 8B 는
훨씬 빠르므로 **그 예산이 거저 지켜져 모든 정책이 100% 에 붙고 sweep 이 아무것도 구별하지
못할 수 있다.** 3 단계가 끝나면 8B 의 유휴 ITL 실측값이 나오므로 그때 고른다:

- **예산 고정** — "같은 약속을 더 빠른 엔진에 건다". 70B·72B 결과와 직접 비교되지만 정책
  구별력이 사라질 수 있다.
- **ITL 비로 재조정** — 정책이 같은 난이도의 문제를 풀지만 앞선 결과와 나란히 놓을 수 없고
  숫자의 근거를 논문에서 설명해야 한다.

**숫자 없이 미리 고르지 않는다.**

**5 단계 — 무릎 실측(EXP-112 절차) → 도착률 격자 확정 → 한 시간 다섯 arm 2 반복.**
Qwen 에서 쓴 것과 같은 사슬이다: 프로파일에서 용량 배수를 유도 → 몇 점으로 무릎 확인 →
그 비로 `scale_trace.py --down-method thin` 으로 hour trace 를 조정 → 다섯 arm.
⚠ **hour trace 를 조정하면 `class_plan_file` 을 그 trace 로 가리키는 워크로드 설정과
`.plan.json` 을 같이 만들어야 한다**(EXP-113 에서 둘 다 걸렸다).

## 4. 판정 규칙 — 실행 전에 적는다

**무엇이 나오면 이 실험이 실패인가:**
- 2 단계에서 engine_8004~8007 이 안 생기면 **본 측정을 걸지 않는다.**
- 4 단계에서 다섯 arm 이 전부 offered 95 이상이면 **예산이 이 모델에서 구속력이 없다는
  뜻**이고, 그 조건으로 정책을 비교한 표는 만들지 않는다(예산을 다시 정하고 되돌아간다).
- 무릎이 격자의 양 끝 밖에 있으면 격자를 다시 잡는다(EXP-112 에서 네 임계가 전부 격자 안에
  들어오는 것을 확인하고 진행했다).

## 5. 그 다음

같은 절차를 **Qwen2.5-14B-Instruct**(8 인스턴스 × TP=1, 가중치 28 G, 토큰당 KV 192 KiB,
ctx 32,768)로 반복한다. 그때는 함대 모양이 이미 8 대이므로 **변수가 모델 하나뿐**이다.
