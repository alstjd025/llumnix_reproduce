#!/usr/bin/env bash
# PreToolUse (Bash|Edit|Write): 측정 경로를 건드리는 도구 호출에 경고를 붙인다.
#
# 왜 있는가: CLAUDE.md 함정 C("실험이 도는 동안 하지 말 것")는 항상 로드되지만,
# 실제로 §59.7·§62.6에서 "적혀 있는데 안 봤다"가 두 번 일어났다. 이 훅은 규칙을
# 읽는 것에 의존하지 않고, 그 행동을 하려는 순간에 도는 실험이 있는지를 조회해서
# 사실로 알려 준다. 차단하지 않는다(exit 0) — 판단은 여전히 사람과 모델이 한다.
set -uo pipefail
REPO=/home/nxclab/llumnix_reproduce

payload=$(cat)
tool=$(printf '%s' "$payload" | jq -r '.tool_name // ""')
raw=$(printf '%s' "$payload"  | jq -r '.tool_input.command // ""')
file=$(printf '%s' "$payload" | jq -r '.tool_input.file_path // ""')

# heredoc 본문은 실행되는 명령이 아니다 — 커밋 메시지에 bin/scheduler-exp07이라고
# 쓴 것만으로 경고가 뜨면 오탐이 쌓여 경고를 무시하게 된다(guard-delete.sh와 같은 이유).
cmd=$(printf '%s\n' "$raw" | awk '
  /<<-?'"'"'?[A-Za-z_][A-Za-z0-9_]*'"'"'?/ && !inhere { inhere=1; next }
  inhere { if ($0 ~ /^[A-Za-z_][A-Za-z0-9_]*$/) inhere=0; next }
  { print }
')
subject="$cmd $file"

# 측정 경로: 도는 sweep이 호출하는 것 전부 (CLAUDE.md 함정 C)
#
# Bash는 그 경로에 **쓰는** 명령일 때만 본다. 복귀 절차 2단계가
# `ls -l --time-style=... bin/scheduler-exp07`이라 읽기까지 걸면 복귀할 때마다 뜬다.
writes=0
case "$cmd" in
  *"cp "*|*"mv "*|*"install "*|*"go build"*|*">"*|*"tee "*|*"rsync"*|*"ln -"*|*"chmod"*|*"truncate"*) writes=1 ;;
esac

hit=""
if [ "$writes" = 1 ]; then
  case "$cmd" in
    *bin/scheduler-exp07*|*bin/gateway-exp10*|*"-o bin/"*)      hit="배포 바이너리" ;;
  esac
fi
case "$file" in
  */k8s/exp07/*.sh|/home/nxclab/tools/*.sh)                     hit="드라이버 스크립트" ;;
  */ms_dev/scripts/set_scheduler_profiling.py)                  hit="정책·프로파일 주입 스크립트" ;;
  */workloads/*|*/run_experiment.py)                            hit="러너가 /work로 읽는 워크로드 소스" ;;
esac
[ -z "$hit" ] && exit 0

jobs=$(timeout 8 kubectl -n llumnix get jobs --no-headers 2>/dev/null \
       | awk '/bench-runner/ && $2 != "Complete" {print "    " $0}')
if [ -n "$jobs" ]; then
  running=$(printf '실행 중인 러너 Job이 있다:\n%s' "$jobs")
else
  running="실행 중인 bench-runner Job은 보이지 않는다(조회 시점 기준). 연쇄 스크립트가 sleep으로 대기 중일 수 있으므로 pgrep -af 'exp[0-9]*' 도 함께 본다."
fi

ctx=$(printf '%s를 건드리려고 한다. 이것은 측정 경로다 — 실험이 도는 동안 바꾸면 조건마다 다른 코드로 측정된다.\n\n%s\n\n측정 경로는 bin/과 workloads/만이 아니라 도는 sweep이 호출하는 것 전부다: 드라이버 스크립트의 arm 정의와 set_scheduler_profiling.py가 여기 들어간다. 스냅샷 패턴은 이것을 막아 주지 않는다. 자세한 것은 CLAUDE.md 함정 C.' \
      "$hit" "$running")

jq -n --arg c "$ctx" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse", additionalContext:$c}}'
exit 0
