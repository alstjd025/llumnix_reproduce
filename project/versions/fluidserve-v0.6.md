# FluidServe v0.6 — 무엇인가, 무엇이 아닌가

**이름**: v0.6 (arm `fsg10dl`; 실험 중에는 "v0.6-후보4"로 불렀다). **2026-11-17 채택(사용자 결정).**
**바이너리**: 스케줄러 `7dc2355c` (v0.5.8, `/home/nxclab/tools/staging/scheduler-gateutil`, 바깥 `8e0fe5e`) + 아래 두 플래그, 게이트웨이 `05745270` (v0.5와 같다). 파드 안 `md5sum /proc/1/exe`로 확인.
**프로파일**: `deploy/profiling/llama31-70b-b200-tp2/` — v0.4·v0.5와 같다.
**앞 버전**: v0.5 ([fluidserve-v0.5.md](fluidserve-v0.5.md)). v0.6-후보1·2(정점 KV 예측 + 여유 기준 보호)는 채택하지 않았고, v0.6은 그 위가 아니라 **v0.5 위에서** 만들었다(2026-11-12 사용자 결정).

> **왜 minor인가.** 기본 구성의 결정 규칙이 두 군데 바뀐다: 토큰당 시간 판정선의 비율, 그리고 첫 토큰 마감이 인스턴스의 가능/불가 판정에 들어가는 것.

## 1. v0.5에서 바뀐 것: 둘

| 무엇 | v0.5 | v0.6 | 플래그 |
|---|---|---|---|
| 토큰당 시간 판정선 | min(새 요청의 TBT budget, 인스턴스에서 처리 중인 가장 빡빡한 클래스의 TBT budget) **× 0.9** | 같은 식 **× 1.0** | `--fluidserve-gate-utilisation=1.0` (`FS_GATE_UTIL=1.0`) |
| 같은 값을 쓰는 곳 | KV 속도 상한(그 판정선을 지킬 수 있는 최대 KV), instance cap의 판정선 변화 비용: 0.9 | 둘 다 1.0 | 같은 플래그 |
| 그대로인 곳 | — | instance cap의 클래스별 수요 추정(0.9 유지), 처리 중 요청 보호(평균 iteration 시간 ≤ 처리 중 요청들의 남은 허용치 최솟값) | |
| 첫 토큰 마감 | 인스턴스를 고를 때는 보지 않는다(pend할지 거절할지 정할 때만) | **기다린 시간 + 예측 prefill 시간(자기 prefill + 앞에 쌓인 prefill) > TTFT budget이면 그 인스턴스는 불가** | `--fluidserve-deadline-feasible=true` (`FS_DEADLINE_FEASIBLE=true`). v0.3부터 있던 플래그로 v0.4·v0.5에서는 꺼져 있었다 |

그 밖의 플래그는 v0.5와 같다(arm 정의: `k8s/exp07/run_exp162_hour.sh`의 `fsg10dl`):
`FS_PREFIX=true FS_FORCE_MARGIN=false FS_CLASS_HARM=false FS_OWN_BUDGET_GATE=false FS_AFFINITY_METRIC=count FS_PER_INSTANCE_CORR=true FS_MEMORY_PACE_CAP=true
FS_PREFILL_INTERLEAVE=true FS_INSTANCE_CAP=true FS_CAP_WINDOW_MULT=3.0 FS_FORCE=false FS_SWE_TBT_MS=75 FS_EXACT_RESIDENCY=1 FS_EXACT_PROGRESS=1`, horizon 100, z 1.65,
게이트웨이 `--wait-scheduling-timeout 35000ms`. 기동 줄 확인값: `gateutil=1.00`, `deadlinefeasible=true`, `deadlinedelay=false`. 워크로드는 swe 첫 토큰 7 s 설정(`..._t75*.json`)이어야 한다(`traps.md`, EXP-158~160 정정).

## 2. 근거 (ladder95, 같은 세션의 v0.5와 2회씩)

| 실험 | trace | 결과 |
|---|---|---|
| EXP-158 | shadow(v0.5 위에서 같은 상태로 다시 판정) | 판정선 1.0 변형은 v0.5가 받아 마감을 지킨 요청을 0 % 더 거절, v0.5가 거절한 요청의 96~99 %에 받을 수 있는 순간이 있었음, chat을 새 인스턴스로 보내는 결정 13 → 3 |
| EXP-161 | chat이 많은 원래 한 시간 trace(`dyn60_shift_m2Am1B_b1045`) | 아홉 지표 모두 v0.5와 "차이 없음", 나빠진 지표 없음. 모든 도착 77.98 → 80.69(+2.7; v0.5가 마지막 구간 갈림으로 76.3~79.7을 오가 반복 범위 안), 반복 간 0.2점으로 안정. 판정선 1.0만(v0.6-후보3)은 swe admitted −1.74로 나빴고, 첫 토큰 판정이 swe의 첫 토큰 초과를 108·72 → 14·17건(v0.5 12·12)으로 되돌렸다 |
| EXP-162 | swe가 많은 trace(같은 도착 시각, swe 5 : deepresearch 3 : chat 2, swe 반복 번호) | 모든 도착 57.04 → 65.05(+8.0), 거절률 −8.1, 클래스별 모든 도착 +12.4·+5.9·+7.5 "다르다(좋음)", admitted 넷 모두 "차이 없음"(99.98 → 99.87), goodput +13 % |

채택 판단: EXP-161의 사전 규칙만으로는 "차이 없음 → v0.5 유지"였다. 두 trace를 함께 보아 나빠진 지표가 어느 쪽에도 없고 swe가 많은 trace에서 다섯 지표가 좋아졌으므로 사용자가 채택했다(2026-11-17).
EXP-159·160은 후보 arm이 swe 첫 토큰 11.8 s 설정으로 돌아 무효이고 근거에 넣지 않는다.

## 3. 알려진 성질과 한계

- **swe의 평균 토큰 간격 초과가 남는다**: deepresearch·swe 인스턴스의 판정선이 swe budget 75 ms와 같아져, 긴 prefill(3,700~6,800토큰)이 만드는 iteration 출렁임을 흡수할 여유가 없다. 받은 swe의 0.25 %(EXP-162 70~73건, EXP-161 19~44건). v0.5의 0.9는 그 인스턴스에서 이 출렁임을 흡수하는 몫이었다.
- **첫 토큰 예측이 비관적이다**: 첫 토큰을 넘긴 swe에서도 예측이 실제보다 1.3~1.5 s 길었다. 그래서 첫 토큰 문제가 없는 chat도 일부 막아, 원래 trace에서 판정선 1.0만일 때보다 chat 거절률이 약 3점 높다(EXP-161: 11.3 → 14.5 %).
- **preemption이 늘었다**: 같은 세션 v0.5 대비 EXP-161 680·976 → 1,612·1,728회, EXP-162 156·177 → 353·348회. 받은 요청의 마감에는 나타나지 않았다.
- **horizon 의존은 v0.5와 같다**: 판정의 "prefill 간섭 지연 ÷ h", 대기 prefill 추정의 "h 동안 도착할 prefill"(첫 토큰 판정에도 쓰인다), KV 끝점 투영, 새 요청의 KV 청구(min(h, 기대 출력)). horizon·예측기 재검토는 v0.6 위에서 단일 클래스 trace부터 한다(EXP-163~).
- 이 바이너리에는 shadow 판정 변형 코드(`--fluidserve-shadow-variants`)도 들어 있지만 v0.6에서는 꺼져 있다.

## 4. 측정

| 실험 | run | 모든 도착 | admitted | 비고 |
|---|---|---|---|---|
| EXP-161 | 2 | 81.00 · 80.83 (대시보드 80.78 · 80.60) | 99.92 · 99.87 | 원래 trace, 후보4 이름 |
| EXP-162 | 2 | 64.99 · 65.11 | 99.87 · 99.88 | swe가 많은 trace, 후보4 이름 |
| EXP-163 | 3 | (진행 중) | | 단일 클래스 trace 보정(chat만 / swe만 / deepresearch만) |
