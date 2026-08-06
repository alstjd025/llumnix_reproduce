#!/usr/bin/env bash
# PreToolUse (Bash): 파일을 지우거나 덮어쓰는 명령에 작업 규칙을 붙인다.
#
# 왜 있는가: CLAUDE.md 작업 규칙 1번이 "사용자의 명시적 지시 없이는 어떤 파일도
# 삭제하지 않는다"인데, 삭제는 되돌릴 수 없으므로 규칙을 읽었는지에 의존하지 않는다.
# 차단하지 않는다(exit 0).
set -uo pipefail

payload=$(cat)
cmd=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""')
[ -z "$cmd" ] && exit 0

hit=""
case "$cmd" in
  *"rm -r"*|*"rm -f"*|*"rm "*)   hit="rm" ;;
esac
case "$cmd" in
  *"git clean"*)                 hit="git clean" ;;
  *"find "*-delete*)             hit="find -delete" ;;
  *"truncate "*)                 hit="truncate" ;;
esac
[ -z "$hit" ] && exit 0

# 결과 디렉토리를 향하면 더 강하게 말한다
scope="이 저장소"
case "$cmd" in
  *results/*|*shards/*|*tbt_events*)  scope="실험 결과 디렉토리" ;;
  *bin-backup*)                       scope="바이너리 백업" ;;
esac

ctx=$(printf '%s로 %s를 지우려고 한다.\n\n작업 규칙: 사용자의 명시적 지시 없이는 어떤 파일도 삭제하지 않는다. 실험 결과 디렉토리, 소스, 산출물, 중간 파일, 로그, 폐기된 것처럼 보이는 run 전부 해당한다. 지워야 한다고 생각되면 먼저 묻는다. 덮어쓰기와 이름이 겹치는 출력 디렉토리에 쓰는 것도 같은 규칙을 따른다.\n\n사용자가 이번 대화에서 명시적으로 지시했으면 그대로 진행한다. 그런 문장이 없으면 실행하지 말고 묻는다.' \
      "$hit" "$scope")

jq -n --arg c "$ctx" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse", additionalContext:$c}}'
exit 0
