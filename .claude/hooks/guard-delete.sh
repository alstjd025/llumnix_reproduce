#!/usr/bin/env bash
# PreToolUse (Bash): 파일을 지우거나 덮어쓰는 명령에 작업 규칙을 붙인다.
#
# 왜 있는가: CLAUDE.md 작업 규칙 1번이 "사용자의 명시적 지시 없이는 어떤 파일도
# 삭제하지 않는다"인데, 삭제는 되돌릴 수 없으므로 규칙을 읽었는지에 의존하지 않는다.
# 차단하지 않는다(exit 0).
set -uo pipefail

payload=$(cat)
raw=$(printf '%s' "$payload" | jq -r '.tool_input.command // ""')
[ -z "$raw" ] && exit 0

# heredoc 본문과 따옴표 안의 글자는 실행되는 명령이 아니다. 이것을 빼지 않으면
# 커밋 메시지에 "git clean"이라고 쓴 것만으로 경고가 뜨고, 그런 오탐이 쌓이면
# 경고 자체를 무시하게 된다(2026-08-06에 실제로 걸렸다).
cmd=$(printf '%s\n' "$raw" | awk '
  /<<-?'"'"'?[A-Za-z_][A-Za-z0-9_]*'"'"'?/ && !inhere { inhere=1; next }
  inhere { if ($0 ~ /^[A-Za-z_][A-Za-z0-9_]*$/) inhere=0; next }
  { print }
')

# 명령 위치(줄 머리 또는 ; && || | 뒤)에 있는 토큰만 본다
hit=""
detect() { printf '%s\n' "$cmd" | grep -qE "(^|[;&|]|\`|\\\$\()[[:space:]]*(sudo[[:space:]]+)?$1([[:space:]]|$)"; }
detect 'rm'            && hit="rm"
detect 'truncate'      && hit="truncate"
printf '%s\n' "$cmd" | grep -qE "(^|[;&|])[[:space:]]*git[[:space:]]+clean" && hit="git clean"
printf '%s\n' "$cmd" | grep -qE "(^|[;&|])[[:space:]]*find[[:space:]].*-delete" && hit="find -delete"
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
