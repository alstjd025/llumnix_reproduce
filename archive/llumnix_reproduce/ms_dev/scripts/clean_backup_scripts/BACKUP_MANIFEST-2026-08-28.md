# BACKUP_MANIFEST — /NHNHOME/NXC_ROOT/NXC13/home_backup_2026-08-28/
#
# ms_dev/notes/RESTORE-2026-08-28.md 가 "전송이 끝났는지는 이 파일로 확인한다"고 적어
# 두었으나 만들어지지 않아서, 2026-08-28 오후에 사후로 만들었다.
#
# 사후로 만들었다는 것이 이 파일의 한계다: 전송 중에 기록한 것이 아니라 끝난 뒤에 센
# 것이므로 "여기 적힌 것이 실제로 있다"는 말할 수 있어도 "여기 없는 것이 원래 없었다"는
# 말할 수 없다. 그래서 §4 가 있다.

생성 시각: 2026-08-28 15:32:50 KST   호스트: NXC13

## 1. 최상위 구성
항목                   크기  파일수  최종수정
cluster_state              704K          4  08-28 13:15
dotfiles                   784M       3210  08-28 13:15
llumnix_reproduce          6.8G      34072  08-28 12:51
results_priority           151G    5588037  08-27 21:02
tools                      4.5G      14671  08-28 12:51
(합계)                   187G    6428914

## 2. 저장소 사본의 커밋 — ⚠ 확인이 필요한 항목
   백업 llumnix_reproduce : 3d6134f STATUS: EXP-107 closed, the final candidate holds the additive prediction, and swe is the one open decision
   백업 Agent_applications: deb5fc8 EXP-107 final verdict: the combined arm lands exactly on the additive prediction
   이 파일을 만든 시점의 살아 있는 트리:
        llumnix_reproduce : 07baa1d EXP-107T: an explicit per-token budget for a tier whose key is not its budget
        Agent_applications: 9391cc6 EXP-107 sections 8-10: the corrected s1 diagnosis, the b40 verdict, and the per-token form for the agent class

   ⚠ 두 사본은 2026-08-28 12:51 것이고 그날 오후 커밋이 들어 있지 않다. 복원 뒤에는
     GitHub 에서 fetch 해 앞선 쪽을 취한다. 사본만 믿으면 그날 오후 작업을 잃는다.
     origin: https://github.com/alstjd025/llumnix_reproduce.git
             https://github.com/alstjd025/Agent_applications.git

## 3. git 으로 복구되지 않는 것 — 체크섬 (살아 있는 트리에서 잰 값)
   8585d01ce0994237ac6f7707b441b127  Agent_applications/agent_motivation_experiment/workloads/codingagent_request_level_poisson/data/README.md
   2eda3b39ac36fc34307b6d1db5bde683  Agent_applications/agent_motivation_experiment/workloads/codingagent_request_level_poisson/data/transcript_swe_calls.jsonl
   62304fb17d2c6eab5bd6c18339b98b1b  Agent_applications/agent_motivation_experiment/workloads/codingagent_request_level_poisson/data/transcript_swe_calls_mix1500.jsonl
   40868c465c49793a6deb0256c86f0bd0  Agent_applications/agent_motivation_experiment/workloads/codingagent_request_level_poisson/data/transcript_swe_short7k_mix1500.jsonl
   lib/sglang : 78M (git 미추적, 사본이 유일본)
   bin/       : 79M (배포 바이너리)
   traces/    : 1.8G

## 4. 백업에 있는 것과 없는 것

   ⚠ 이 절의 첫 판은 틀렸다. RESTORE-2026-08-28.md 의 계획 문장("results_priority 는
   논문이 참조하는 run") 을 그대로 옮겨 적고 실제로 세지 않았다. 세어 보니 다르다.

   results_priority/ 에 실제로 들어 있는 것 (151 GB, 5,588,037 파일)
     aggregate_analysis/  paper_experiment/  results/
     tbt_events.jsonl : 44 개 109.7 GB — 계획에서 "제외"라고 적었지만 실제로는 들어갔다.
       전체 61 개 154.3 GB 중 44 개다. 어느 17 개가 빠졌는지는 세지 않았다.
     agent_logs 디렉토리 : 1,016 개 — 이것도 계획과 달리 들어갔다. 파일 수 558 만의
       대부분이 이것이다.

   백업에 없는 것
     results/ 중 results_priority/ 에 선택되지 않은 run.
     gotmp/.
     RESTORE-2026-08-28.md 가 "여유가 되면" 넣겠다고 적은 results_rest/ 와 hf-cache/.
       hf-cache 가 없으면 엔진 기동에서 모델을 다시 내려받아야 한다.

   교훈: 계획 문서에 적힌 제외 목록과 실제로 전송된 것이 다르다. 복원 뒤에 "없다"고
   판단하기 전에 세어 볼 것 — 그리고 그것이 이 파일이 있어야 하는 이유다.

   ⚠ 복원 전에 확인할 것
     .claude/ 사본은 2026-08-28 13:07 시점이다 (그날 오후 세션 기록이 빠져 있다).
       사용자 판단으로 갱신하지 않기로 했다.
     2026-08-28 오후에 돈 EXP-107T 조건들의 결과는 어디에도 없다.

## 5. 복원 뒤 첫 검증
   1) 저장소   : 두 저장소에서 git fetch 후, 백업 사본과 origin 중 앞선 쪽을 취한다.
   2) 워크로드 : §3 의 md5 를 대조한다. swe transcript 는 2026-08-27 에 한 번 지워진 적이
                 있고, 그때 복구 검증은 "경로가 같다"가 아니라 이미 돌아간 run 의
                 metrics.csv 와 요청 단위로 대조하는 것이었다 (CLAUDE.md 함정 B).
   3) 클러스터 : cluster_state/ 의 yaml 과 대조한다.
   4) 배포     : 파드 안에서 md5sum /proc/1/exe — 그것만이 authority 다.
