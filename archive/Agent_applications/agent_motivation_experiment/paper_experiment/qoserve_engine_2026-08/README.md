# qoserve_engine_2026-08 — 배포된 라우터에 deadline 인식 엔진 스케줄러를 붙였을 때

**논문에서 "엔진 스케줄러는 순서를 바꿀 수 있지만 구성을 바꾸지 못한다"를 뒷받침하는 결과.**
EXP-81. 정본은 `experiments/EXP-81_vllmrouter-qoserve.md` §6.

| | |
|---|---|
| run 수 | **34** = 처리군 16 + 대조군 16 + 세션 점검 2 |
| 도착률 | 10 · 15 · 20 · 25 · 35 · 45 · 55 · 70 req/s — **메인 sweep과 같은 격자** |
| 반복 | 2 |
| 조건 길이 | 8분, 조건마다 엔진과 제어평면을 cold restart |

## 세 arm이 무엇인가

| arm | 라우터 | 엔진 | 출처 |
|---|---|---|---|
| **vLLM router + FIFO** | `cache_aware` (PyPI `vllm-router`의 기본 정책) | stock vLLM V1 | EXP-77. **메인 sweep의 그 arm 그대로**이고 여기서 다시 돌리지 않았다 |
| **vLLM router + QoServe** | 같음 | Niyama 이식 (`--scheduling-policy priority --scheduler-cls deadline_sched.DeadlineScheduler`) | EXP-81 |
| 세션 점검, FIFO | 같음 | stock | EXP-81. 35 req/s만 |

**세션 점검이 왜 있나.** 대조군은 3~6일 전 세션의 것이다. 이 저장소는 세션이 다르다는 이유로
비교를 버리지 않되 **읽으려는 양의 반복 간 편차를 알고 그보다 큰 차이만 읽는다.** 그것과 별개로
35 req/s에서 FIFO를 이번 세션에 두 번 더 돌려 재현되는지 봤다:

| | offered | 모든 도착 | 미완료 | goodput |
|---|---|---|---|---|
| 이번 세션 | 8.1 / 8.2 | 6.3 / 6.4 | 22.0 / 22.1% | 1,406 / 1,443 |
| sweep (3~6일 전) | 7.8 / 8.6 | 6.1 / 6.8 | 21.8 / 21.6% | 1,362 / 1,511 |

**이번 세션의 두 값이 sweep의 두 값 안에 들어간다.** 안 맞았다면 엔진 스케줄러가 아니라 스택이
달라진 것이므로 전부 다시 돌려야 했다.

## 결과 요약 (자세한 것은 EXP-81 §6)

| req/s | FIFO | +QoServe | 차이 | preemption |
|---|---|---|---|---|
| 10 · 15 · 20 | 99.5 ~ 97.9 | 같음 | **±0.2** | 0 → 0 |
| 25 | 64.3 (폭 6.0) | 61.6 (폭 0.3) | −2.7 (폭 안) | 0 → 0 |
| 35 | 6.4 | **24.8** | **+18.4** | **3,422 → 0** |
| 45 | 2.3 | **12.6** | **+10.3** | **4,018 → 0** |
| 55 | 1.5 | **6.5** | **+5.0** | **3,909 → 0** |
| 70 | 1.0 | 2.6 | +1.6 | **4,029 → 0** |

**90% 유지 도착률: FIFO 21.4 → QoServe 21.3** (FluidServe는 28.0).

> **깊은 과부하의 goodput을 3~5배로 만들고 미완료를 21.7%에서 8.6%로 줄이면서도, 시스템이
> 규칙 지키기를 그만두는 지점은 0.1 req/s도 안 움직인다.**

## ⚠ 이 데이터로 하면 안 되는 것

- **"엔진 스케줄러가 쓸모없다"고 읽지 않는다.** 과부하에서 낭비를 줄이는 것은 실제 값어치이고
  우리 것과 직교한다. 주장은 좁게 **"용량을 못 옮긴다"**다.
- **이득을 relegation에 귀속하지 않는다.** 원본 relegation은 요청을 거절하지 않고 한 번만 뒤로
  미는 것이라(대조 문서 §unit 5) 18.4점을 만들 수 없다. **이득은 unit 4(동적 prefill 청크
  크기)가 recompute preemption을 없앤 것**이고 preemption 표가 그것을 그대로 보여준다.
- **차이를 relegation 하나에 귀속하지 않는다.** 처리군은 `--scheduling-policy priority`로
  엔진의 대기 큐 자료구조까지 바뀌므로 차이는 **unit 2(EDF 정렬) + 3(slack 재정렬) +
  4(동적 청크) + 5(강등)의 묶음**이다.
- **Niyama 전체가 아니다.** unit 6(선형 batch-time 예측기)이 미이식이고, `_relegate_waiting`이
  대기 힙의 **앞 32개만** 검사한다(원본은 전체). 35 req/s에서 엔진별 대기가 74~218건이므로
  큐의 15~43%만 본 것이다. **"앞 32개만 검사하는 이식이 이만큼 한다"로 읽는다.**
- **한 시간 동적 trace가 아니다.**

## ⚠ 한 조건은 두 번 걸렸다

반복 1의 70 req/s가 처음에 `engines NOT serving after 1200s: [8003]`으로 죽었다. 결과
디렉토리가 안 생겨 오염은 없고, 엔진을 QoServe로 되돌려 그 조건만 다시 돌려 채웠다
(`/home/nxclab/tools/exp81_fill70.sh`). 함정 F가 같은 종류의 사고를 셋 적고 있다.

## 다시 만들고 확인하는 법

```bash
python3 paper_experiment/verify.py qoserve_engine_2026-08 \
    --glob "results/*exp81r[12]_vllmcacheqoserve_m1_rpm_*"
python3 analysis_scripts/request_level/all_arrivals_attainment.py \
    --runs 'results/*exp81r[12]_vllmcacheqoserve_m1_rpm_*' --since 260807_1900
```
