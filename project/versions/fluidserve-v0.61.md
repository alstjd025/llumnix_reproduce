# FluidServe v0.61 — 무엇인가, 무엇이 아닌가

**이름**: v0.61 (실험 중 이름 "D3", arm `fsxdual`). **2026-11-24 사용자 결정**: "이 D3를 v0.61로 하자. 0.7로 확정짓기에는 아직 검증하고 스터디해 볼 것들이 남았다."
번호 규칙상 결정 규칙이 바뀌었으므로 minor(v0.7)에 해당하지만, 검증이 끝날 때까지 v0.61로 부른다.
**바이너리**: 스케줄러 `1de94045` (`/home/nxclab/tools/staging/scheduler-exp177-dual`, 바깥 저장소 브랜치 `exp177-dual` 커밋 `3ba37e7`; 그 브랜치의 다음 커밋 `1a8ca1e`은 T 모드를 더한 것으로
v0.61의 결정은 같다), 게이트웨이 `3590c1af`(`gateway-connreuse2`, scheduler 연결 재사용). 파드 안 `md5sum /proc/1/exe`로 확인.
**플래그**: v0.6(`fsg10dl`)의 플래그 전부 + `FS_INSTANCE_CAP=false`(`--fluidserve-class-instance-cap=false`) + `FS_KV_PRICE=dual`(`--fluidserve-kv-price=dual`).
arm 파일 `/home/nxclab/scratch/exp172/arms/fsxdual.env`, 드라이버 `run_exp172_hour.sh`(8B)·`run_exp175_hour.sh`(70B)·`run_exp176_hour.sh`(8B 절반 budget).
**앞 버전**: v0.6. 중간 단계로 후보 3(EXP-172, 바이너리 `5d52124e`)과 후보 A(EXP-177, `1b89579b`)를 거쳤다.

## 1. v0.6에서 바뀐 것

| 판정 | v0.6 | v0.61 |
|---|---|---|
| 1. 토큰 간격 판정(gate) | 넣은 뒤 평균 step 시간 ≤ 1.0 × min(새 요청 budget, 처리 중 요청들의 명목 budget 최솟값) | 같음 |
| 2. 처리 중 요청 보호 | 넣은 뒤 평균 step 시간 ≤ 처리 중 요청들의 남은 허용치 최솟값 | 같음 |
| 3. 첫 토큰 마감 | 기다린 시간 + 예측 prefill 시간 ≤ TTFT budget | 같음 |
| 4. 메모리 판정 | h step 끝점 예측(유출을 미리 뺌) + 청구 ≤ min(capKv, 0.95 × pool 환산) | **물리 KV + 아직 계산되지 않은 prompt + (prompt − prefix hit) ≤ pool**, capKv 판정은 유지 |
| 5. 사용량 문턱 판정 | 없음 | **λ × c + μ × a ≤ 1** (KV × 시간 c, GPU 시간 a; 문턱 변수 λ·μ는 받지 못한 수요로 생기고 움직인다) |
| 클래스별 인스턴스 상한 | 켬 | 끔 |
| 동점 정렬 | 같은 클래스 수 → KV 여유 | 같은 클래스 수 → λ × c + μ × a → KV 여유 |

정의와 수식: `project/evidence.md` §13, `Agent_applications/agent_motivation_experiment/experiments/EXP-177_overnight-candidates.md`(A, T, D3 절). 새 상수는 없다;
메모리 판정에서 h, z = 1.65, 0.95가 빠졌다. 문턱 변수의 시간 상수 H = h × step에 h가 남고, 속도 판정(1·2번)의 평균 step 시간 계산에 h step 예측(proj)이 남는다.

## 2. 근거 (ladder95)

| 실험 | 함대·budget | v0.61 | 비교 |
|---|---|---|---|
| EXP-178 | 8B 표준, 2회 | 모든 도착 88.83 / 89.53, admitted 98.79 / 99.28 | 후보 A와 모든 지표 같음; PolyServe(77.95) 대비 나쁜 지표 없음 |
| EXP-177b | 8B 절반, 1회 | 87.43, admitted 99.80 | PolyServe 74.88, v0.6 75.45(EXP-176) |
| EXP-178b | 70B 표준, 2회 | 84.82 / 84.33, admitted 99.97 / 99.95 | v0.6(EXP-161)과 모든 도착·admitted·거절률·token goodput 차이 없음; chat 모든 도착 +11.2(좋음), deepresearch −17.1(나쁨). PolyServe(EXP-175) 대비 +30.8 |

## 3. 알려진 성질과 아직 남은 것

- 요청 하나의 가치를 모두 1로 둔다(요청 수 목표). 그래서 요청 하나당 자원을 가장 많이 쓰는 deepresearch를 더 거절한다(8B 약 40 %, 70B r1 47 %). PolyServe보다는 deepresearch를 더 처리한다.
- 70B에서는 v0.6보다 나아지지 않았다: 처리한 토큰 총량이 같고 늘어난 요청 수(+3.9)는 세션 간 문턱 안이며, deepresearch를 17점 덜 처리한다(`evidence.md` §13.3).
- 남은 것: 8B 절반 budget 2회째, 다른 trace, 판정 단계 사이의 중복 정리(2026-11-24 사용자 요청), 목표(가치)를 바꾼 변형.
