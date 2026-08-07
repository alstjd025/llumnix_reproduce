#!/usr/bin/env bash
# SessionStart / PreCompact: 지금 상태를 생성해서 컨텍스트에 넣는다.
#
# 왜 있는가: 상태(무엇이 도는가, 어느 절이 정본인가)는 CLAUDE.md에서 빼서
# ms_dev/notes/STATUS.md로 옮겼다. 그 파일을 읽는 것이 모델의 재량에 달려 있으면
# compaction 직후나 새 세션에서 낡은 상태로 답할 수 있으므로, 세션이 시작될 때와
# compaction 직전에 하네스가 직접 넣는다.
#
# 손으로 쓴 STATUS.md §1과 달리 아래 "지금 클러스터" 절은 매번 새로 조회하므로
# 낡을 수 없다. 둘이 어긋나면 조회한 쪽이 사실이다.
set -uo pipefail
REPO=/home/nxclab/llumnix_reproduce
STATUS="$REPO/ms_dev/notes/STATUS.md"
RESULTS="$REPO/Agent_applications/agent_motivation_experiment/results"

payload=$(cat 2>/dev/null || true)
event=$(printf '%s' "$payload" | jq -r '.hook_event_name // "SessionStart"' 2>/dev/null || echo SessionStart)

{
  echo "# 지금 상태 (llumnix_reproduce) — 훅이 세션 시작·compaction 직전에 넣는다"
  echo
  echo "## 손으로 관리하는 요약 (STATUS.md §1)"
  echo
  if [ -r "$STATUS" ]; then
    sed -n '/^## §1/,/^## §2/p' "$STATUS" | sed '$d'
  else
    echo "STATUS.md를 못 읽었다: $STATUS"
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
      echo "⚠ STATUS.md가 마지막으로 고쳐진 뒤에 결과 디렉토리가 ${n}개 생겼다."
      echo "  위의 '손으로 관리하는 요약'은 그만큼 낡았을 수 있다. 판단하기 전에"
      echo "  ms_dev/notes/fluidserve-implementation.md의 마지막 절을 읽고 STATUS.md를 갱신한다."
    fi
  fi

  echo
  echo "규칙(함정 A~F, 판정 규칙, 서술 규칙)은 CLAUDE.md에 있고 항상 로드된다."
  echo "이력(§32~§62 절별 요약)은 STATUS.md §3에 있으니 필요하면 그 파일을 읽는다."
} > /tmp/claude-state-brief.$$ 2>/dev/null

jq -n --arg e "$event" --rawfile c /tmp/claude-state-brief.$$ \
  '{hookSpecificOutput:{hookEventName:$e, additionalContext:$c}}'
rm -f /tmp/claude-state-brief.$$
exit 0
