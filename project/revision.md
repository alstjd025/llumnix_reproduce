# 리비전 작업 기록

## 지금 상태

- **2026-10-11~12** — NSDI '27 제출(2026-09-23) 후 리비전 준비. 저장소를 정리했다: 기록 전부를 `archive/`로 옮기고
  `project/` 문서 다섯을 새로 썼다. 지운 것은 없다. 브랜치 `revision/reorg`(두 저장소 모두)에 커밋했고
  — 바깥 `4c3798f`·`5aaf1bc`·`df6d26d`, 안쪽 `c3d1c26` — **push는 사용자가 한다.**
- **`system.md`·`evidence.md`는 에이전트가 쓴 초안**이다. 아래 확인 항목 중 "직접 대조"로 표시한 것만 정리 담당이
  다시 확인했다. 리비전 중 틀린 곳이 보이면 바로 고친다.
- 제출 논문 PDF `ms_dev/submitted_version/`은 git에서 뺐다(`.gitignore`, 사용자 결정). 텍스트 추출본은
  `/home/nxclab/scratch/paper.txt`(`pdftotext -layout`, 두 단이 섞여 있다).
- **클러스터**: 2026-10-10에 다시 세웠다. k3s + Llumnix(Llama-3.1-70B × 4, TP=2). llm-d 스택은 아직 안 세웠다.
  **2026-10-20부터 `bin/`의 바이너리가 바뀌었다**(아래 EXP-141·142 줄) — 논문 run의 바이너리(스케줄러 `6dc9f035`, 게이트웨이
  `0efb664a`)는 `/home/nxclab/tools/bin-backup/*.pre-exp141`. 같은 GPU를 쓰는 다른 프로젝트(powertest)가 엔진을 내렸다
  올리고 Sionna를 끄고 켠다.
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
- **EXP-141·142·143 끝 (2026-10-20, 바깥·안쪽 저장소 브랜치 `revision_scheduler`, 23:35부터 다시 Sionna 모드)**
  - **EXP-141**(정적 35 req/s × 3회): 정확 장부(`fsexact`)는 모든 도착 기준 "차이 없음"(75.8 대 77.3)이지만 deepresearch admitted
    64.0%로 무너졌다 — 원인은 진행 토큰 수 j가 엔진 대기열의 요청도 세는 것(R32)과 첫 토큰 마감이 feasibility에 없는 것(R9).
    Llumnix SLO + 처리 중 기준(`slot75inc`)은 admitted 38.3 → 25.8로 **나빠졌다**(Q2-a 예상과 반대). 상세는 EXP-141 기록 6절.
  - **EXP-142**(정적 3회, 스케줄러 `3251836a`): 엔진 대기 요청 제외(`--exact-progress`, `fsexactprog`)로 deepresearch가 96.0%까지
    회복했으나 판정 기준 98에 못 미쳤다. 여기에 첫 토큰 마감 조건(`fsexactprogdl`)을 더하면 deepresearch 100.0, 모든 도착 75.6으로
    논문 구성(76.2)과 구별되지 않는다. 첫 시도는 엔진 요청 ID 형식(`cmpl-<id>-0`)을 처리하지 못해 smoke에서 멈췄다(기록 5절).
  - **EXP-143**(한 시간 trace, arm마다 1회): 원본 두 arm이 논문 그림 1(EXP-109 반복 2)과 goodput 1% 안팎으로 맞았다. 처리 중 기준은
    여기서는 Llumnix SLO를 **개선했다**(모든 도착 27.9 → 44.0) — 정적 실험과 반대 방향이고 원인은 아직 안 봤다. 고친 FluidServe는
    76.5 대 78.6으로 구별할 수 없고 deepresearch 95.6.
  - 바이너리: `bin/`에 지금 스케줄러 `3251836a`, 게이트웨이 `590242b0`. 논문 run의 것(스케줄러 `6dc9f035`, 게이트웨이 `0efb664a`)은
    `tools/bin-backup/*.pre-exp141`. 소스는 그 뒤 UUID ID 처리를 한 번 더 고쳤다(`41a667f`, 아직 빌드·배포 안 함).
- **백업(2026-10-24)**: EXP-141~143 run 31개(44 GB)와 대시보드 스케줄러 기록, Figure 1 입력 CSV를 Lustre
  `/NHNHOME/share/nxclab_backup_1024/`에 복사하고 원본과 대조했다(`MANIFEST.txt`). 원본은 지우지 않았다.
- **EXP-144~155 (2026-11-04~10, 안쪽 기록 `experiments/EXP-144`~`155`, 바깥·안쪽 브랜치 `revision_scheduler`)**. 모두 Llama-70B × 4, 한 시간 trace는
  `dyn60_shift_m2Am1B_b1045`(EXP-152는 기존 데이터 분석만).
  - **완료 통지가 빠져 끝난 요청이 처리 중 목록에 남던 문제(R33·R34) 해결**: 통지를 그 요청을 배치한 스케줄러 파드로 직접 보내고(`c0fb7ab`), keepalive 재연결 뒤에도
    보내며(`5dec8d6`), 러너가 측정 전에 종료 중인 이전 파드를 지운다(안쪽 `42296c0`). EXP-147 이후 모든 run에서 남는 기록 0건.
  - **EXP-147**: 그 상태에서 `fsexactprog`(통지로 목록 유지 + 엔진 대기 요청 제외)의 한 시간 점수 79.4, deepresearch admitted 100(10월 EXP-143은 남는 기록 때문에 95.6).
    첫 토큰 마감 조건(`fsexactprogdl`)은 아홉 지표 모두 차이 없음.
  - **EXP-148**: horizon 25·50은 100보다 모든 도착 +5.7~+6.6(chat·swe를 더 받음, h25는 deepresearch −7.4), h200은 받아들인 요청이 마감을 못 지킴(deepresearch admitted 73).
  - **EXP-149·150(shadow 판정)·151(분모만 바꾸기)**: horizon 효과는 거의 전부 토큰당 시간 추정의 "대기 prefill 청크 수 ÷ h"에서 온다(분모만 25·50으로 바꿔도 EXP-148 효과의 87~107 %).
  - **EXP-152(분석)**: 그 효과는 예측 정확도가 아니라 배분 — 긴 프롬프트가 chat 인스턴스로 덜 간다(25M → 12M 토큰). 처리 중 chat은 ladder 여유가 2.7초 이상(p1).
  - **EXP-153(제거 후보 장부)·154(A/B)**: 미리 잡는 KV 청구와 지연 보정은 지워도 아홉 지표 모두 차이 없음(사용자 결정으로 일단 둔다). KV 투영을 지우면 더 엄격해진다.
    같은 구성(h100)의 한 시간 run 13개 중 4개가 마지막 구간에서 갈린다(엔진 하나가 deepresearch 쪽으로 넘어가 chat 거절 급증) — shadow 계산과 무관.
  - **EXP-155(horizon을 예측 정확도 통제 값으로)**: KV 투영은 h에 비례해 낙관적이었다(끝점 −0.5 % at h25 → −15.9 % at h200; 실제 KV는 평평). 정점 KV 예측(도착 지수 평균 10초 +
    실측 오차로 여유) + 여유 기준 보호(P1)를 구현(`81aeace`). 예측 쏠림 없음·admitted 100은 성립, h25 = h100 배분은 같고 h200은 아직 배분이 움직인다.
    **그러나 지금 모델보다 모든 도착 −15.7**: 예측이 정확해지자 판정선(평균 iteration 시간 ≤ 예산 × 0.9, KV 0.95, z 1.65)이 필요보다 엄격하다는 것이 드러났다.
- **EXP-156·157 (2026-11-10~11)**: 판정선을 위반이 실제로 시작되는 지점에서 다시 정했다(EXP-156, 기존 데이터: 토큰당 시간은 ladder 허용치 = 예산 + ladder 여유 ÷ 남은 토큰이 실제 경계이고,
  preemption은 KV 정점이 0.99에 닿을 때만). 그것을 넣은 **v0.6-후보2**(v0.6-후보1 + ladder 판정선 + KV 한도 0.98)를 한 시간 trace에서 v0.5와 같은 세션 2회씩 비교했다(EXP-157).
  **채택하지 않음**: h100 모든 도착 67.3 대 v0.5 79.3(아홉 지표 중 모든 도착·거절률·chat·swe 모든 도착 넷이 나쁨, 다섯은 차이 없음), preemption 3~29 대 636~690. v0.6-후보1보다 약 3점 회복에 그쳤다.
  거절이 판정선에서 slack 보호의 일회성 지연 시험(새 prefill 지연 > 처리 중 요청들의 최소 ladder 여유)과 정점 KV 한도로 옮겨 갔다 — ladder 판정선이 여유를 다 쓰게 허용하므로 여유가 1초 미만인 요청이
  인스턴스-snapshot의 절반에 생긴다. 예측 쏠림(전체)과 admitted ≥ 98은 성립. h200에서는 클래스별 KV 예측 쏠림(chat 인스턴스 +5 %)이 배분 이동과 같은 방향. 마지막 구간 갈림은 0/8.
- **EXP-158·159 (2026-11-12~14)**: v0.5로 돌아가 한 가지씩 바꾸는 방향(2026-11-12 사용자 결정; 클래스 분리는 판정선의 부수 효과로 두되 실험마다 분리 지표로 확인).
  - **EXP-158(shadow, v0.5.7)**: v0.5를 돌리며 같은 상태에서 판정 변형을 다시 계산했다(결정은 v0.5). 판정선 0.9 → 1.0(G1.0)·h 없는 budget 판정(U, 지속 부하 + 요청마다 늦은 토큰 수)·둘 다(U+G1.0)는
    v0.5가 받아 마감을 지킨 요청을 더 거절하지 않고(≤ 0.05 %) 거절된 요청 대부분에 받을 수 있는 순간이 있었다. 정점 KV 예측(M0·M1)은 그런 요청을 5~42 % 거절해 탈락. 그 run은 마지막 구간 갈림(모든 도착 75.5).
  - **EXP-159(v0.6-후보3 = v0.5 + 판정선 1.0, 2회씩)**: 모든 도착 77.95 → 82.93, 거절률 21.8 → 16.4 %, chat·swe 모든 도착 +7.3·+10.3, 분리 유지. 그러나 deepresearch 모든 도착 −5.2, swe admitted −3.5로
    **채택하지 않음**(사전 규칙). deepresearch·swe 인스턴스의 판정선이 swe budget 75 ms와 같아져 긴 prefill 지연을 흡수할 여유가 없어졌다 — v0.5의 0.9는 그 몫이었다. 다음 후보는 그 몫을 고정 비율 대신 실제 prefill 간섭 지연으로 계산하는 U+G1.0.
- **⚠ 정정 (2026-11-16)**: EXP-158(`fsshadowv`)·159(`fsg10`)·160(`fsg10dl`)에서 새로 만든 arm이 드라이버의 워크로드 설정 선택 목록에 빠져 swe TTFT budget **11.8 s** 설정
  (`mix_dyn60_shift_m2Am1B_b1045.json`)으로 돌았다. 같은 실험의 v0.5 arm과 EXP-147~157의 모든 arm은 **7 s**(`..._t75.json`). 그래서 위 EXP-158 run의 점수 해석과 EXP-159의 "후보3 채택하지 않음" 판정은
  무효이고(swe·deepresearch 쪽 차이가 설정 차이로 생겼을 수 있다), EXP-160의 첫 토큰 판정이 swe를 막지 못한 것도 이 때문이다(판정이 11.8 s 기준이었다). EXP-161에서 올바른 설정으로 다시 잰다.
- **EXP-161 (2026-11-17, 모든 arm swe 7 s 설정으로 다시 잼, 같은 세션 2회씩)**: v0.6-후보3(판정선 0.9 → 1.0)은 모든 도착 77.98 → 82.90, 거절률 −5.2, chat·swe 모든 도착 +7.3·+5.2이지만
  swe admitted 99.65 → 97.90으로 나빠 **채택하지 않음**(받은 swe의 첫 토큰·토큰 간격 초과가 29~38 → 216~219건). v0.6-후보4(+ 첫 토큰 마감 판정)는 swe 위반을 v0.5 수준으로 되돌렸지만
  이득도 줄어 아홉 지표 모두 v0.5와 **차이 없음**(모든 도착 80.69, +2.71). 두 후보는 반복 간 0.2~0.4점으로 안정적이고, v0.5는 마지막 구간 갈림으로 76~80점을 오간다. 분리는 둘 다 유지.
- **EXP-162 (2026-11-17, swe가 많은 한 시간 trace: 같은 도착 시각, swe 5 : deepresearch 3 : chat 2, swe 반복 번호)**: v0.6-후보4가 v0.5보다 모든 도착 57.04 → 65.05(+8.0), 거절률 −8.1,
  클래스별 모든 도착 +12.4·+5.9·+7.5로 좋고 admitted는 넷 다 차이 없음(99.98 → 99.87). 늘어난 위반은 swe 평균 토큰 간격 초과 70여 건(받은 swe의 0.25 %). **후보4를 v0.6으로 채택(2026-11-17 사용자 결정, `project/versions/fluidserve-v0.6.md`). 이후 실험의 기준선은 v0.6.**
  다음은 단일 클래스 trace(chat만 / swe만 / deepresearch만)로 v0.6 보정(EXP-163) → 도착률 조절 → v0.5와 v0.6 비교 → horizon·예측기 재검토.
- **EXP-163·164 (2026-11-17~18, 클래스 하나만 있는 한 시간 trace, v0.5 대 v0.6 2회씩; chat은 도착률 2.5배)**: chat 2.5배는 세 지표 모두 차이 없음(v0.6 +3.1 방향).
  swe만은 v0.6이 모든 도착 +4.3·거절 −5.6이지만 **admitted 99.98 → 97.68로 나쁨** — 판정선 75 ms에 실제 iteration 79 ms(v0.5도 판정선 67.5 ms에 실제 75 ms: 받는 순간의 추정이 받은 뒤의 실제보다
  낮은 것은 두 버전 공통이고 v0.5의 10 % 여유가 그것을 흡수했다). deepresearch만은 두 버전 모두 preemption 1.2만 회(KV 판정이 너무 많이 받음)이고, v0.5는 첫 토큰 실패로 admitted 81.8,
  v0.6은 첫 토큰 마감 판정으로 97.4(모든 도착 +5.6). **다음: 예측기 — 받는 순간의 토큰당 시간 추정이 실제보다 낮은 원인, KV 판정이 deepresearch에서 과하게 받는 원인.**
- **EXP-165 (2026-11-18~19, 같은 단일 클래스 trace에서 기준선, 같은 세션 2회씩; llm-d는 이미지 버전 문제로 아직)**: Llumnix SLO(리비전에서 바꾼 처리 중 요청 기준 판정 —
  단일 클래스에서는 결정을 바꿀 수 없어 논문의 Llumnix SLO와 같다)와 PolyServe(논문 구성). 공정 비교용 설정(swe `tbt_ms` 75)을 단일 클래스에도 새로 만들었다(EXP-164 드라이버는 shift에서만 골랐다).
  admitted: chat 2.5배 PolyServe 29.1 / Llumnix SLO 31.3 (v0.6 99.9), swe만 99.8 / 4.8 (v0.6 97.7), deepresearch만 100.0 / 0.95 (v0.6 97.4). 모든 도착은 세 trace 모두 v0.6이 가장 높다
  (68.2 / 54.0 / 38.1 대 PolyServe 19.9 / 42.1 / 31.7). Llumnix SLO는 KV admission이 없어 swe·deepresearch에서 KV 100 %, preemption 9천·2만 회, 받은 요청 거의 전부가 첫 토큰 실패.
  PolyServe는 KV 투영이 실제보다 커서(EXP-127과 같은 원인) 엔진 KV 44~71 %에서 거절(거절 판정의 89~100 %가 memory) — 그래서 swe·deepresearch에서 받은 것을 v0.6보다 2.1~2.7점 더 지키고
  6~12점 덜 받는다. chat 2.5배에서는 두 기준선 모두 받은 chat의 약 2/3가 평균 토큰 간격으로 실패(prefill이 늘리는 decode iteration을 판정이 반영하지 않는다).
  **발견**: 한 시간 trace 드라이버가 PolyServe tier 표를 기본 설정에서 만들어 swe 기대 출력을 494 대신 463토큰으로 투영하던 결함(`run_exp109_hour.sh`부터; 정적 드라이버는 정상) —
  `run_exp165_hour.sh`에서 고치고 검사를 넣었다(사용자 지시). 논문 PolyServe 한 시간 run이 이 표로 돌았는지는 맥 SSD의 run으로 확인해야 한다(`evidence.md` §6 12번).
  **llm-d (2026-11-19, 같은 세 trace 2회씩)**: 논문 run의 llm-d 버전을 기록에서 찾아(EPP llm-d-router 커밋 `644a8856` — EXP-114 기록, 그 `:main` 이미지는 태그가 없어 같은 커밋을
  upstream Dockerfile 절차로 다시 빌드했다; 절차는 08-06 이미지 바이너리를 바이트 단위로 재현; 예측기 `ec7626b`) `deploy/llmd/router-paper.yaml`·`predictor-paper.yaml`로 세웠다.
  첫 시도는 예측기를 `04e44cd`로 잘못 잡아 학습이 매번 실패(EPP 예측 10 ms 고정)해서 무효로 하고, 드라이버에 run 중 학습 확인을 넣었다. 결과 admitted: chat 2.5배 99.5, swe만 93.6(89.7·97.6), deepresearch만 86.7.
  chat에서는 v0.6과 admitted가 같고 거절률 +6.7·goodput −8.8 %(예측기가 90번째 백분위수를 학습해 덜 받는다고 추정 → EXP-166에서 중앙값으로 확인 중). deepresearch만에서는 KV admission이 없어
  preemption 3,700~4,200회, 거절 82.6 %로 다섯 정책 중 가장 많이 거절한다. llm-d는 반복 간 편차가 크다(swe admitted 7.8점, deepresearch 모든 도착 6.5점; r1만 엔진 두 대 쏠림).
  **다섯 정책 종합(세션이 다른 비교)**: v0.6이 세 trace 모두에서 모든 도착·goodput이 가장 높고, admitted는 swe만·deepresearch만에서 PolyServe(엔진 KV를 44~71 %만 쓴다)가 v0.6보다 2~3점 높다.
  **사용자 결정 대기**: PolyServe의 구조적 KV 투영(EXP-127, 2026-09-11 "논문에 충실" 결정으로 유지)을 그대로 둘지.
- **EXP-166 (2026-11-19, llm-d 예측기를 중앙값으로, chat 2.5배 2회)**: admitted 99.47 → 95.97 **나쁨**, 모든 도착·거절률·goodput은 차이 없음(p50 r1의 처음 15분 거절 65~93 %로 반복 범위가 14점).
  p50 예측은 실측과 맞지만(TPOT 47 대 47~49 ms) 그것을 SLO와 여유 없이 비교해 받은 chat의 평균 토큰 간격이 budget을 넘는 비율이 12 → 37 %. 더 받지도 않았다(r2 거절률 35.5 대 p90 38). "llm-d가 chat에서
  v0.6보다 덜 받는 것은 p90 여유 때문"이라는 추정은 틀렸다 — p90 여유는 llm-d의 admitted를 지키고 있었다.
- **EXP-167 (2026-11-19~20, llm-d p50을 swe만·deepresearch만에서, 섞인 논문 trace에서 p90 대 p50, 2회씩)**: 섞인 trace에서 p50은 모든 도착 +6.3·거절률 −9.5·chat·swe 모든 도착 +12.1·+6.8로 좋지만
  admitted −4.6·chat admitted −6.5(받은 chat의 36 %가 평균 간격 budget 초과)·deepresearch 모든 도착 −11.6으로 나쁘고 goodput은 차이 없음. swe만에서는 admitted 93.6 → 67.6, deepresearch만은 차이 없음.
  **논문 구성 p90이 llm-d에게 더 나은 설정.** **논문 llm-d 재현**: p90 섞인 trace 모든 도착 56.76(54.99·58.52) 대 논문 53.0(49.9·56.3), 거절률 42.4 대 45.7, chat·swe 범위 안 — 재현. deepresearch만 +9.9로 이번이 높다(원인 미확인).
  다섯 정책 비교표: EXP-165·166·167 기록.
- **원인 분석 확장 (2026-11-20, EXP-169 §5, 하위 분석 넷)**: 섞인 trace와 단일 클래스 trace 모두에서 정책별 원인을 각 정책의 판정 로그·엔진 지표·코드로 확인했다. 공통 원인 넷 —
  ① 예측하는 step이 실제와 다르다(PolyServe·Llumnix SLO는 decode만의 프로파일이라 실측의 0.46~0.77, 차이는 받은 뒤 성장이 아니라 prefill 간섭; llm-d p50은 중앙값으로 SLO 바로 아래까지 채움; FluidServe는 같은 초에 몰린 도착·h 동안 도착을 빼서 swe에서 +4~5 ms),
  ② KV를 세는 방식이 실제와 다르다(PolyServe 논리 토큰 + 받으려는 요청의 기대 출력 × batch 전체 → 물리 30~60 %에서 멈춤; Llumnix SLO는 KV를 안 봄 → 1.00; FluidServe는 끝날 요청의 KV를 미리 빼고 대기 prefill을 빼서 0.995),
  ③ 클래스가 섞이면 판정이 가장 빡빡한 클래스로 내려간다(llm-d `podMinTPOTSLO`, Llumnix SLO 변경 구성, PolyServe promotion이 chat 50 ms를 swe·deepresearch에 적용 — FluidServe는 클래스를 모아 75·100 ms 엔진을 만든다, 섞인 trace에서의 주된 차이),
  ④ 경로 선택이 한 엔진에 몰고 그 엔진이 전체 판정을 정한다(llm-d `least` + 포화 평균, PolyServe의 고정된 tier 배분).
  정정: Llumnix SLO 거절은 100 % 게이트웨이 5초 포기; llm-d 예측기는 KV·대기 수를 입력으로 쓴다; llm-d와 클라이언트는 요청 번호로 정확히 맞출 수 있다; FluidServe "iteration 예측/실측 1.001"은 현재 상태의 재구성이라 받은 뒤의 예측 정확도가 아니다; FluidServe 첫 토큰 예측은 판정 구간에서 2.2~3.4 s 비관적.
  섞인 trace Llumnix SLO 변경 구성은 deepresearch admitted 29.6 → 99.7, chat 55.2 → 38.5(실패가 deepresearch 첫 토큰에서 chat 평균 간격으로 옮겨감; 각 1 run).
- **EXP-168 (2026-11-20, 섞인 논문 trace, 기준선 셋 × 2회; FluidServe는 다시 돌리지 않음 — 사용자 결정)**: PolyServe 53.70 / admitted 65.58 / goodput 8,653, Llumnix SLO 논문 27.69 / 45.97 / 6,786,
  Llumnix SLO 처리 중 요청 기준 43.58 / 51.62 / 7,773 — 논문 값(EXP-109) 둘 다 재현(`evidence.md` §4.9a). 처리 중 요청 기준은 논문 구성보다 열 지표 중 아홉이 좋고 chat admitted만 55.2 → 38.7로 나쁘다.
  v0.6(EXP-161, 80.69 / 99.90 / 11,764)과 비교하면 세 기준선 모두 모든 도착·goodput이 나쁘고, PolyServe만 swe 모든 도착에서 +4.8로 앞선다(논문의 "PolyServe performs best for the Agent workload"와 같은 방향).
- **EXP-170 (2026-11-20~21, 인스턴스 8대 Llama-3.1-8B, 표준 budget, 논문 6.2배 한 시간 trace, v0.6 대 PolyServe 각 2회)**: v0.6이 **진다** — 모든 도착 63.87 대 73.48(−9.6),
  거절률 33.8 대 23.5 %, chat·swe 모든 도착 −15.5·−25.3, swe·deepresearch admitted −4.0·−2.2, goodput 68,782 대 72,580 tok/s(−5.2 %); v0.6이 나은 것은 deepresearch 모든 도착 +17.7과
  chat admitted +1.8, admitted 전체는 차이 없음. EXP-125(v0.4)의 역전이 v0.6에서도 같은 방향. v0.6 엔진은 분 20~30·50~60에 8대 중 7~8대가 KV ≥ 0.973, preemption 53.5k / 53.7k(PolyServe 569 / 298,
  KV 0.5, ITL 28 ms로 생성 토큰 3~6 % 더). 메모리 판정이 포화 표본의 83 %에서 통과 — 이미 cap_mem을 넘어 있어도 h 동안 끝날 KV 8 %를 미리 빼기 때문. 엔진당 동시 처리 최대 1,332(`--max-num-seqs 4096` 미달).
  새로 찾은 원인 후보: 클래스별 인스턴스 상한이 "통과한 인스턴스가 있는데 상한이 지워 갈 곳이 없어진" 주기 8.0만 / 9.7만(chat 거절 12.7만 / 14.1만). 70B로 되돌림 확인(spec 저장본과 동일, Sionna).
- **EXP-171 (2026-11-20~21, 분석만, 에이전트 다섯)**: 논문 수준 문제·원칙·해결책. FluidServe는 점수를 거의 전부 거절로 잃고, 실제 남은 여유는 70B 2~4점·8B 9~10점. 8B 손실 경로 셋 —
  인스턴스 상한, KV 투영 +11 % 낙관(끝날 KV를 미리 빼고 그 사이 도착이 채움), KV 부족 시 클래스를 고르지 않음(chat이 KV의 16 %인데 거절의 65 %). 제안: 원칙 P1 약속한 일에서 출발(미래 도착 제외; EXP-155가
  도착을 넣어 −15.7), P2 한계마다 되돌릴 수 없는 구간의 실측 오차 분위수 = 여유(요청마다 슬랙 하나), P3 자원이 묶이면 수명 비용당 가치로 받음; "ε가 knob이고 h는 한계마다 정해진다". ladder95에서는 첫 토큰
  초과가 실패와 다르다(8B deepresearch 84 %가 그래도 성공). **사용자 결정 대기**: h 원칙 수정, 비용 기준 클래스 선택(2026-09 결정과 충돌), 다음 실험(8B에서 상한 끄기 A1 / v0.7 A2). 보고 전문 `results/aggregate_analysis/exp171/`.
- **EXP-172 (2026-11-21, 8B × 8 표준 budget, 사용자 부재 중 진행: "8B에서도 70B같은 결과", 단순·일반·조정값을 늘리지 않는 해결책, 에이전트로 후보 여럿)**: 후보 넷(+조합 둘)을 구현해 13 run.
  **인스턴스별 KV 가격(후보 3, 3회)이 v0.6의 격차를 메운다** — 모든 도착 76.00(72.5~81.6) 대 PolyServe 73.48·v0.6 63.87, goodput 76,211 대 72,580·68,782; PolyServe와는 모든 도착·admitted·거절률·goodput 차이 없음, v0.6보다 +12.1.
  가격: 부족도 u = (물리 KV + 미계산 prefill) ÷ pool로 쌍대 상승(Kelly / Low–Lapsley), 기대 수명 KV 비용이 큰 요청부터 거절, 새 조정값 없음. **반복 범위 9.1점으로 불안정**(초반·끝 15분), admitted 93.7.
  반증: 상한만 끄기(A1, 차이 없음 — EXP-171의 "상한이 주원인"은 틀림), AIMD KV 창(preemption −70~83 %인데 점수 그대로, 손실 신호가 preemption의 0.3~1.5 %만 잡음), 약속한 일 기반 KV 판정(요청이 대기열에 묶임, 56.27), 첫 토큰 판정 보정(오프라인), 첫 토큰 모델 수정(EXP-118 붕괴 재현, 43.08).
  **첫 토큰 예측의 비관은 prefill 처리량을 넘는 수용을 막는 사실상의 제어**라 그것만 고치면 무너진다. 운영: 디스크 부족 축출 뒤 옛 결과 172개를 `/NHNHOME/nxclab_results_overflow/`로 옮기고 심볼릭 링크(지운 것 없음). 70B 복구 확인.
- **EXP-175 (2026-11-22 23:34 시작, 70B, 진행 중 — 8B에서 PolyServe를 앞선 후보 3(EXP-172의 인스턴스별 KV 가격)이 70B 한 시간 trace에서도 앞서는가; 사용자 지시)**:
  70B 함대(4대, TP=2), 논문의 섞인 한 시간 trace(변형 `shift`), 게이트웨이는 연결 재사용 바이너리 `3590c1af`. 대기열(각 약 75분): PolyServe r1 → 후보 3 r1 → 후보 3 r2 → PolyServe r2 →
  v0.6에서 클래스별 인스턴스 상한만 끈 것(`fsxnocap`) r1·r2(후보 3이 v0.6에서 두 가지 — 상한 끔, KV 가격 — 를 바꾼 것이라 어느 쪽 효과인지 나누려고 넣었다). **v0.6은 다시 돌리지 않고** EXP-161의 2회(80.69 / 99.90 / 거절 19.0 %)를
  세션 간 문턱으로 쓴다; 측정 경로가 그때와 같은지는 이 체인의 PolyServe가 EXP-168 PolyServe(53.70)와 같은지로 확인한다. 드라이버는 새 파일 `k8s/exp07/run_exp175_hour.sh`(`run_exp172_hour.sh` 사본 — `fsx*` arm이 변형 `shift`에서
  v0.6과 같은 `…_t75.json`을 읽게 한 것 하나만 다르다). 판정 규칙 (가)~(마)와 실행 전 예측(후보 3 ≈ v0.6에서 상한을 끈 것, 모든 도착 77~83; 70B에서는 KV 가격이 거의 0이라 PolyServe 대비 우위는 v0.6이 이미 가진 것)은
  `EXP-175_candidate3-on-70b.md`. 체인 `/home/nxclab/scratch/exp175/run.sh`, 로그 `/home/nxclab/tools/exp175_chain.log`.
- **EXP-174 (2026-11-22, 8B, 끝남 — 게이트웨이가 스케줄러 연결을 재사용하게 고친 뒤 재측정)**: **결과(한 시간 trace `shift62`, ladder95, 여섯 run 전부 대체 배치 0건)** —
  모든 도착 / admitted / 거절률 / goodput: v0.6(1회) 63.53 / 96.56 / 34.04 % / 68,535 tok/s, PolyServe(2회) 77.95 / 99.13 / 21.29 % / 74,226, **후보 3(2회) 83.94 / 95.90 / 12.29 % / 81,880**, B+C2(1회) 66.09 / 99.40 / 33.38 % / 68,806.
  ① 수정이 동작한다(대체 배치 0). ② v0.6은 게이트웨이를 바꿔도 어느 지표도 달라지지 않는다(옛 게이트웨이 63.87 / 96.76 / 33.83 %) → **8B에서 v0.6이 PolyServe에 지는 것(모든 도착 −14.42점)은 측정 결함이 아니다.**
  ③ 후보 3은 PolyServe보다 모든 도착 +5.99, 거절률 −9.00, goodput +10.3 %이고 admitted는 3.23점 낮다(preemption 약 51k회/run, PolyServe 0회). 후보 3의 반복 범위가 9.13점(옛 게이트웨이) → 0.68점 — 옛 편차는 대체 배치 때문이었다.
  ④ B+C2는 preemption을 52k → 10k로 줄이고 admitted를 99.40으로 올리지만 거절률이 v0.6과 같아(33.38 %) 모든 도착은 차이 없음. 클래스별 값과 지표별 판정 전체는 `EXP-174_gateway-connection-reuse-remeasure.md`, **수치의 정본은 `project/evidence.md` §10**(2026-11-23에 8B 절을 새로 만들었다 — 옛 게이트웨이의 PolyServe·후보 3 값은 인용하지 않는 값으로 따로 적었다). 아래는 실행 전에 적은 내용이다.
  EXP-173에서 찾은 결함을 고친 뒤의 재측정: EXP-173에서 찾은 결함(게이트웨이의 스케줄러 호출이 임시 포트 고갈로 실패하면
  요청이 admission 판정 없이 round-robin으로 엔진에 들어가고 채점에서는 받아들인 요청으로 세어지는 것)을 고쳤다. **수정(바깥 `02ee187`)**: 스케줄러 client가 Go 기본 Transport(호스트당 유휴 연결 2개) 대신
  연결을 재사용한다(호스트당 유휴 연결 1,000개·유휴 제한 5분 — 같은 게이트웨이의 엔진 전달 client와 같은 값이라 새 조정 값은 없다. 호출 제한 시간 2초는 그대로). 대체 배치 동작 자체는 upstream 그대로 두고
  횟수를 `gateway_scheduling_fallback_total` counter로 내보내 run마다 기록한다(수집기 `llumnix_metrics.py`, 안쪽 `d11a12c`). 단위 시험: 동시 200 × 20회 호출에서 연 연결 수 186개(기본 Transport 2,832개).
  게이트웨이 바이너리 `tools/staging/gateway-connreuse2`(`3590c1af`) — **`bin/`의 70B 게이트웨이(`590242b0`)는 그대로**이고 8B 체인이 run 동안만 넣었다가 되돌린다. **실제 함대에서 대체 배치가 0이 되는지는 첫 run(PolyServe r1)이 확인한다.**
  대기열(각 약 71분): PolyServe r1 → 후보 3(EXP-172, 인스턴스별 KV 가격) r1 → B+C2(EXP-173, KV 유입 속도 밸브 + Little의 법칙 첫 토큰) r1 → PolyServe r2 → 후보 3 r2 → v0.6 대조 1회(게이트웨이 변경이 v0.6을 움직이지 않는지).
  판정 규칙(run 유효성: counter ≤ 받아들인 요청의 0.1 %, 2,000건 초과면 체인 정지; 비교 다섯 개의 지표별 문턱)은 실행 전에 `EXP-174_gateway-connection-reuse-remeasure.md`에 썼다. 체인 `/home/nxclab/scratch/exp174/run.sh`, 로그 `/home/nxclab/tools/exp174_chain.log`.
- **EXP-173 (2026-11-22 밤, 8B, 사용자 개념 "KV를 유량으로 — 들어올 양도 세고 밸브를 조절, 첫 토큰은 Little의 법칙"을 하나씩 검증; 에이전트 셋)**:
  **가장 큰 발견은 측정 경로 결함**: 8B 함대에서 보류가 많은 정책은 게이트웨이의 스케줄러 호출이 임시 포트 고갈(`connect: cannot assign requested address`, 기본 Transport의 호스트당 유휴 연결 2개)로 실패해
  **round-robin으로 대체 배치**된다(게이트웨이 로그로 확인, `traps.md` 함정 G). EXP-170 PolyServe 8B는 받아들인 요청의 7~10 %, EXP-172·173의 KV를 조이는 후보는 20~54 %가 admission 없이 들어갔고 후보 3의 반복 간 편차도 이와 같이 움직였다.
  70B 전체·v0.6 8B는 0 → 논문 결과는 영향 없음. 수정(게이트웨이 연결 재사용)·재측정은 사용자 승인(2026-11-22) → 바로 위 EXP-174.
  유량 KV 판정(F1 과거 h 유입 더하기, A 그 수정판, B KV 유입 속도 밸브, L 수준 판정)은 스케줄러가 받은 요청을 98~99.7 % 지키지만 우회를 빼면 모든 도착 44~61로 v0.6보다 낮다(KV를 낮은 수준에 묶어 덜 받는다).
  Little의 법칙 첫 토큰(C2)은 KV가 넘친 채로는 낙관적(실제가 더 긴 비율 80 %), KV 밸브와 함께(B+C2)면 정확(p90 오차 +0.47 s)하지만 덜 받는다. C r1은 순 처리 속도 추정이 0에 고착되어 무효(C2로 수정).
  "1번 결함(보낸 요청이 KV에서 빠진다)"은 결함이 아니었다(장부로 이미 들어감). 기록 `EXP-173_flow-concept.md`.
- **문제 분석 (2026-11-20, 원인 규명과 그림은 EXP-169 기록): FluidServe가 기준선보다 나은 이유 — 엔진을 SLO를 지키는 한계 바로 아래에서 돌린다.** 원인 요약: PolyServe는 KV를 prefix 공유를 푼 논리 토큰 + 기대 출력 전체로 계산해 물리 KV 38~62 %에서 멈추고(swe 논리/물리 2.43배), 정상상태 step 예측이 prefill을 빼 실측의 58~71 %라 chat에서 넘긴다; Llumnix SLO는 한 step decode 예측이 실측의 51~77 %이고 KV 판정이 없다; llm-d는 p90 예측으로 덜 받고, 섞인 trace에서는 chat이 모든 엔진에 있어 모든 엔진이 50 ms에 묶이며(`podMinTPOTSLO`), deepresearch만에서는 `least` 전략이 한 엔진에 몰고 포화 평균(엔진별 max(대기/5, KV/0.8)의 평균)이 한 엔진 때문에 1을 넘어 세 엔진이 노는 동안 86 %를 거절한다; llm-d p50은 예측이 실측보다 조금 낮아 받은 chat의 37 %가 budget을 넘는다. 데이터 `analysis_scripts/capacity/engine_work.py`
  (엔진의 vLLM 지표, 시간 5~55분; 결과 `/home/nxclab/scratch/capacity/engine_work.tsv`). 엔진 하나의 출력 처리량은 대략 (동시 처리 요청 수) ÷ (토큰 하나 걸리는 시간)이므로,
  SLO를 지키며 낼 수 있는 최대 처리량은 토큰 간격이 그 엔진에서 처리 중인 가장 빡빡한 클래스의 budget에 닿을 때까지(그리고 KV가 물리 용량에 닿을 때까지) 받는 것이다.
  v0.6은 5분 구간마다 엔진의 토큰 간격이 budget에 붙어 있다(chat 2.5배 47~50 ms / budget 50, 동시 처리 225~273; swe만 77~82 ms / 75, 142~165). 기준선은 두 방향으로 벗어난다:
  ① **덜 받는다** — PolyServe는 KV를 크게 계산해 swe만에서 동시 처리 77·토큰 간격 53 ms(budget 75)·KV 38 %에서 멈추고(goodput v0.6의 77 %), deepresearch만에서 KV 60 %에서 멈춘다;
  llm-d는 p90 예측의 여유로 chat 43~47 ms·swe 62~67 ms·섞인 trace 42~43 ms에서 멈추고(섞인 trace 동시 처리 99~103 대 v0.6 167), deepresearch만에서는 prefix 친화 필터가 엔진 두 대에 몰아 나머지가 논다.
  ② **더 받는다** — Llumnix SLO는 새 요청 하나의 한 step TPOT만 예측하고 KV를 보지 않아 chat 77~87 ms·swe 117 ms(KV 99 %, preemption 9천)·deepresearch KV 100 %(preemption 2만)까지 가고,
  PolyServe는 chat에서 정상상태 추정이 prefill을 빼서 69~75 ms까지 간다. 그러면 요청이 오래 머물러 동시 처리가 370~450으로 늘고 step이 더 느려진다.
  chat 2.5배에서 PolyServe·Llumnix SLO의 엔진 출력은 v0.6과 같은데(20.6~20.8k 대 20.6k tok/s) 마감을 지킨 비율이 37~39 % 대 97 %다 — 처리량이 아니라 운전점이 차이를 만든다.
  마감을 지킨 출력 비율(goodput ÷ 엔진 출력)은 v0.6이 네 trace에서 0.95~0.98, 덜 받는 기준선도 0.94~0.99지만 엔진 출력 자체가 작다.
  v0.6의 남은 약점도 같은 그림에 있다: swe만에서 토큰 간격이 budget을 4~7 ms 넘고(마감 비율 0.95), deepresearch만은 KV에 묶여 preemption이 1.2만 회다(토큰 간격은 73 ms로 budget 100 안이라 대부분 흡수된다).
- **다음 (2026-11-10)**: ① 판정선을 SLO 위반·preemption이 실제로 시작되는 지점에서 다시 정하기(기존 데이터로 먼저). ② h200 배분 이동 원인. ③ 마지막 구간 갈림을 만드는 결정.
  ④ ~~버전 이름 정리~~ → 완료(2026-11-10): `project/versions/`. **v0.4 = 제출 논문, v0.5 = 지금까지의 "대조군"(`fsexactprog`), v0.6-후보1 = EXP-155의 새 모델.**
- **다음 (2026-10-24에 적은 것, 위 줄이 대체)**: ① EXP-142 r2·r3에서 deepresearch가 실패한 원인(엔진 대기 요청 제외만 켰을 때). ② 처리 중 기준이 정적에서는 나쁘고
  한 시간 trace에서는 좋은 이유. ③ 클러스터 HBM pool 아이디어(chat 인스턴스 KV 60%가 비어 있음)의 측정 — 2026-10-20 대화에서
  재사용률을 재는 실험부터 하자고 제안했다(결정 안 됨). ④ 아래 확인 항목을 검토해 리비전 범위를 정한다.

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
| R32 | **FluidServe의 진행 토큰 수 j는 "보낸 뒤 엔진 step 수"라 엔진 대기열·preemption 중인 요청도 토큰을 만드는 것으로 센다**(`fluidserve_registry.go` `reconcile`, `system.md` 8.4). 그 j로 KV outflow(곧 끝나서 나갈 KV)를 예측한다. 논문 구성에서는 장부를 엔진 running 수에 맞추며 j가 큰 요청부터 지우는 추정이 이 오차를 우연히 상쇄했다. 장부만 정확히 하면(EXP-141 `fsexact`) 메모리가 한계인 인스턴스의 outflow가 2.7배, 투영 KV가 실제보다 21% 낮아져 deepresearch를 과하게 받고 TTFT가 무너진다. 엔진이 매 상태마다 보내는 대기 요청 ID로 고친 `--exact-progress`를 EXP-142에서 잰다. 논문 식 2(KV 투영)의 정확성 서술과 직결된다 | EXP-141 r1 직접 분석 | EXP-141 기록 6절, EXP-142 |
| R33 | **정확 장부에는 엔진에 없는데 남는 기록이 약 1% 생기고, 그 기록이 outflow 과대 예측(2~3배)의 대부분이다**(EXP-144, 2026-11-03). 3분 이후 장부가 정답보다 100~145건 많고 그 수는 일정한데, 남은 기록은 j가 계속 커져 p = 1·KV(prompt + j)로 outflow에 들어가 기록값과 재현값의 차이가 시간에 따라 커진다(8분 run 끝에 5.3배). 처리 중인 요청의 j 과대는 논문 구성에서 +2.6%로 작다 — **R32의 "옛 장부가 j 과대를 상쇄했다"는 해석을 정정**: 옛 장부의 count-trim이 지운 것은 주로 이런 남는 기록이다. 생존함수 자체는 맞고(실제 j를 넣으면 0.97배, calibration 차이 0.01~0.04), 논문 구성의 0.75배 보수성은 거의 전부 z = 1.65 하한이다. 남는 기록이 생기는 원인(게이트웨이 완료 통지 누락인지 스케줄러 쪽인지)은 미확인 — 게이트웨이 로그를 남기는 smoke로 가린다 | EXP-144 분석 | EXP-144 기록 4절 |
| R34 | **notified residency의 남는 기록은 스케줄러 교체 중 완료 통지가 이전 스케줄러 파드로 가서 생긴다**(EXP-145 19절, 2026-11-04 요청 ID로 확인). 게이트웨이는 `/schedule`과 `/complete`를 같은 Service 주소(`scheduler:8088`)로 보내는데 연결이 달라 서로 다른 파드에 닿을 수 있다. 이전 파드는 200을 돌려주므로 흔적이 없다. 10월 run들(EXP-141~143)은 run마다 스케줄러를 다시 띄우는 순간이 부하 상승과 겹쳐 100~200건이 생겼고, 그것이 정확 장부 arm의 deepresearch 붕괴(64%, 96%, 95.6%)의 주된 원인으로 보인다 — 남는 기록이 없던 run은 같은 구성에서 deepresearch 100%. 논문 구성(inferred residency)에는 해당 없음. 고칠 방향: 운영(이전 파드가 사라진 뒤 측정 시작) 또는 시스템(통지를 그 요청을 스케줄링한 파드로, 종료 중 파드의 `/complete` 거절, 엔진 running ID로 목록 대조) | EXP-145 | EXP-145 11~19절 |

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
