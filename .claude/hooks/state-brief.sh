#!/usr/bin/env bash
# SessionStart / PreCompact: 지금 상태를 생성해서 컨텍스트에 넣는다.
#
# 왜 있는가: 상태(지금 무엇을 하고 있는가)는 CLAUDE.md가 아니라 project/revision.md의
# "## 지금 상태" 절에 둔다(2026-10-11 정리 전에는 ms_dev/notes/STATUS.md였고 지금은
# archive/에 있다). 그 파일을 읽는 것이 모델의 재량에 달려 있으면 compaction 직후나
# 새 세션에서 낡은 상태로 답할 수 있으므로, 세션 시작과 compaction 직전에 하네스가 넣는다.
#
# 손으로 쓴 그 절과 달리 아래 "지금 클러스터" 절은 매번 새로 조회하므로 낡을 수 없다.
# 둘이 어긋나면 조회한 쪽이 사실이다.
set -uo pipefail
REPO=/home/nxclab/llumnix_reproduce
STATUS="$REPO/project/revision.md"
RESULTS="$REPO/Agent_applications/agent_motivation_experiment/results"

payload=$(cat 2>/dev/null || true)
event=$(printf '%s' "$payload" | jq -r '.hook_event_name // "SessionStart"' 2>/dev/null || echo SessionStart)

# PreCompact는 hookSpecificOutput을 받지 않는다(받으면 JSON 검증 오류로 훅이 실패한다, 2026-10-12).
# compaction 직후에 SessionStart(source=compact)가 다시 돌면서 같은 내용을 넣으므로 여기서는 아무것도 내지 않는다.
[ "$event" = "PreCompact" ] && exit 0

{
  echo "# 지금 상태 (llumnix_reproduce) — 훅이 세션 시작·compaction 직전에 넣는다"
  echo
  echo "## 손으로 관리하는 요약 (project/revision.md의 '지금 상태' 절)"
  echo
  if [ -r "$STATUS" ]; then
    awk '/^## 지금 상태/{p=1; print; next} p && /^## /{exit} p' "$STATUS"
  else
    echo "project/revision.md를 못 읽었다: $STATUS"
  fi

  echo
  echo "## 지금 클러스터·저장소 (이 값들은 방금 조회한 것이다)"
  echo
  echo '```'
  echo "--- 러너 Job (bench-runner, 미완료만)"
  timeout 8 kubectl -n llumnix get jobs --no-headers 2>/dev/null \
    | awk '/bench-runner/ && $2 != "Complete" {print}' | head -10 \
    || echo "(kubectl 조회 실패)"
  echo "--- 연쇄 스크립트: 최근 24시간 로그마다 살아 있나 / 마지막 종료성 줄"
  # 개수를 세는 점검은 정지를 못 잡는다. 로그가 있는데 프로세스가 없으면 그 자체가 신호다.
  # 이것 없이 EXP-63이 2026-08-07에 두 번 조용히 죽었고 4.5시간·2.6시간 뒤에 발견됐다.
  found=0
  for lg in $(find /home/nxclab/tools -maxdepth 1 -name 'exp*.log' -mmin -1440 2>/dev/null | sort); do
    found=1
    nm=$(basename "$lg" .log)
    if pgrep -f "[e]xp${nm#exp}[_a-z0-9]*\.sh" >/dev/null 2>&1 \
       || pgrep -af '\.sh' 2>/dev/null | grep -vE 'pgrep|shell-snapshots|claude' | grep -q "$nm"; then
      state="RUNNING"
    elif grep -qE "=== .*DONE" "$lg" 2>/dev/null \
         && [ "$(grep -c 'ABORT' "$lg" 2>/dev/null)" = 0 ]; then
      state="finished"
    else
      state="** NOT RUNNING, no clean DONE **"
    fi
    last=$(grep -E "ABORT|FAILED|!!!|=== .*DONE" "$lg" 2>/dev/null | tail -1 | cut -c1-90)
    echo "  $nm: $state${last:+  | last: $last}"
  done
  [ "$found" = 1 ] || echo "  (최근 24시간 안에 만들어진 연쇄 로그 없음)"
  pgrep -af 'exp[0-9]+[a-z]*.*\.sh' 2>/dev/null \
    | grep -vE 'pgrep|shell-snapshots|claude' | head -5 || true
  echo "--- 최근 결과 디렉토리 5개 (이름의 시각은 UTC-7, KST는 +16h)"
  ls -dt "$RESULTS"/*/ 2>/dev/null | xargs -r -n1 basename \
    | grep -E '^[0-9]{6}_' | head -5
  echo "--- 배포된 바이너리"
  ls -l --time-style=+%m-%d\ %H:%M "$REPO/bin/scheduler-exp07" "$REPO/bin/gateway-exp10" 2>/dev/null \
    | awk '{print $NF, $(NF-2), $(NF-1)}'
  echo "--- git (llumnix_reproduce)"
  git -C "$REPO" log --oneline -3 2>/dev/null
  echo "--- git (Agent_applications)"
  git -C "$REPO/Agent_applications" log --oneline -3 2>/dev/null
  echo '```'

  # STATUS.md가 마지막 결과보다 오래됐으면 낡았을 수 있다고 말한다
  if [ -r "$STATUS" ]; then
    newest=$(ls -dt "$RESULTS"/*/ 2>/dev/null | head -1)
    if [ -n "$newest" ] && [ "$newest" -nt "$STATUS" ]; then
      n=$(find "$RESULTS" -maxdepth 1 -type d -newer "$STATUS" 2>/dev/null | wc -l)
      echo
      echo "⚠ project/revision.md가 마지막으로 고쳐진 뒤에 결과 디렉토리가 ${n}개 생겼다."
      echo "  위의 '손으로 관리하는 요약'은 그만큼 낡았을 수 있다. 판단하기 전에"
      echo "  그 run들이 무엇이었는지 확인하고 revision.md의 '지금 상태' 절을 갱신한다."
    fi
  fi

  echo
  echo "작업 규칙은 CLAUDE.md, 지금 상태 문서는 project/, 실험 운영 함정은 exp-run 스킬에 있다."
  echo "과거 기록은 archive/에 있고 필요할 때만 archive/INDEX.md부터 연다."
} > /tmp/claude-state-brief.$$ 2>/dev/null

jq -n --arg e "$event" --rawfile c /tmp/claude-state-brief.$$ \
  '{hookSpecificOutput:{hookEventName:$e, additionalContext:$c}}'
rm -f /tmp/claude-state-brief.$$
exit 0
