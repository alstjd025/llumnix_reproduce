# static_sweep_2026-08 — 다섯 정책의 정적 rate sweep

**논문 인트로의 용량 그림과 평가 절의 주 표가 여기서 나온다.**

| | |
|---|---|
| run 수 | **80** = 다섯 정책 × 여덟 도착률 × **2반복** |
| 도착률 | 10 · 15 · 20 · 25 · 35 · 45 · 55 · 70 req/s |
| 조건 길이 | 8분, 조건마다 엔진과 제어평면을 cold restart |
| 모델 | Llama-3.1-70B-Instruct, 인스턴스 넷 |
| 워크로드 | `mixed_request_level_poisson`, m1(균형) — 요청 수 chat 10 : deepresearch 2 : swe 1 |
| 고정된 파일 | `manifest.tsv`(run 한 줄씩), `table.csv`(계산된 지표), `data/<run>/metrics.csv`, `scheduler_specs/` |

## 어느 실험이 어느 칸을 채웠나

| 실험 | 무엇 | 언제 |
|---|---|---|
| **EXP-68 / 69** | FluidServe와 llm-d의 35 · 45 · 55 · 70 req/s, 2반복 | 2026-08-08 |
| **EXP-70** | FluidServe와 llm-d의 10 · 15 · 20 · 25 req/s, 1반복 | 2026-08-08 |
| **EXP-72** | PolyServe와 Llumnix SLO의 여덟 도착률, 1반복 | 2026-08-09 |
| **EXP-77** | vLLM router(`cache_aware`)의 여덟 도착률, 2반복 | 2026-08-10~11 |
| **EXP-80** | **위에서 1반복이던 칸 23개**를 채워 전부 2반복으로 | 2026-08-13 |

정확한 대응은 `manifest.tsv`의 `experiment` 열에 run마다 적혀 있다. 이 표는 읽기 위한 것이고
**정본은 그 파일이다.**

## ⚠ 워크로드 경계

**2026-08-08 11:00 KST 이후의 run만 들어 있다.** 그때 부하 생성기의 워커별 데이터셋 분할이
고쳐졌고(그 전에는 워커 12개가 같은 프롬프트 열을 보내 모든 프롬프트가 정확히 12번씩 나갔다)
chat 풀이 1,000 → 10,000 대화가 됐다. 엔진의 prefix cache hit rate가 83~86%에서 28.9%로
떨어지므로 **그 전후는 다른 워크로드이고 한 표에 넣으면 안 된다.**

⚠ **결과 디렉토리 이름의 시각은 러너 파드의 UTC−7이라 KST보다 16시간 뒤진다.** 그래서 경계가
`260807_1900`이다. EXP-80을 계획할 때 이것을 `260808`로 잘라 **유효한 llm-d 조건 여섯 개를
버렸고**, 그 잘못된 개수 위에서 세운 계획을 손으로 대조하기 전까지 알아채지 못했다.
`manifest.tsv`의 `started_kst` 열은 그래서 **KST로 변환해서 적는다.**

## 정책마다 어떤 설정을 받았나

`manifest.tsv`의 `policy_flags` 열이 그때 실제로 걸려 있던 스케줄러 인자 전부이고,
`scheduler_specs/`에 배포 spec 원본이 있다. 요약하면:

| 정책 | arm 이름 | 워크로드 설정 | 비고 |
|---|---|---|---|
| FluidServe v0.2 | `fspfx` | m1 | prefix 인식 켬, `--fluidserve-class-budgets 25:e2e:30000` |
| vLLM router | `vllmcache` | m1 | PyPI `vllm-router`의 기본 정책 `cache_aware` 이식 |
| PolyServe | `polyserve` | m1 | 고정 fleet 위의 정적 클래스 파티션 |
| Llumnix SLO | `slo` | **m1f** | |
| llm-d | `llmdslo` | **m1f** | Llumnix 스케줄러를 쓰지 않는다 — `scheduler_specs/epp_config_*.yaml`이 그 자리다 |

⚠ **Llumnix SLO와 llm-d만 `m1f`를 받는다.** 둘 다 전체 시간 예산을 표현할 수 없어서 swe의
25 ms tier 키를 토큰당 목표로 곧이곧대로 읽는데, 그러면 그 클래스의 98%를 거절한다.
`m1f`는 같은 30초 예산을 (2,500 ms, 토큰당 52 ms)로 바꿔 적은 것이다. **채점은 어느 설정으로
돌리든 swe를 전체 시간 30초로 하므로 달성률은 같은 기준 위에 있다.**

## 무엇이 이 데이터로 만들어지나

| 산출물 | 스크립트 |
|---|---|
| 인트로 용량 그림 (막대 / 곡선) | `paper_figures/fig_intro_capacity.py` |
| 세 분모 표 (offered · admitted · 모든 도착) | `analysis_scripts/request_level/all_arrivals_attainment.py --since 260807_1900` |
| 표준 그림 세트 | `analysis_scripts/redraw_static_sweep_workload2026-08-08.sh` |

**그림을 다시 그리기 전에 검증기를 돌린다:**

```bash
python3 paper_experiment/verify.py static_sweep_2026-08 \
    --glob "results/*exp80r2_fspfx_m1_rpm_*" --glob "results/*exp77r*_vllmcache_m1_rpm_*"
```

## 이 데이터가 말하지 않는 것

- **한 시간 동적 trace가 아니다.** 정적 조건은 도착률이 고정이고 클래스 비율도 고정이다.
  믹스가 시간에 따라 움직일 때의 결과는 EXP-71에 있다.
- **믹스는 m1 하나다.** chat 비중을 낮춘 m4·m5는 EXP-75가 정찰만 했다.
- **반복은 둘이다.** 폭은 두 값의 차이이지 분산의 추정이 아니므로 **바닥으로만** 쓴다.
- **70 req/s는 포화 한참 뒤다.** 기준선 넷이 거기서 1.3~2.6%이므로 곡선의 그 끝은 순서를
  가르지 못한다.
