#!/usr/bin/env bash
# 대시보드 시작·중지·상태.  ./run.sh start | stop | restart | status | log
# 127.0.0.1:8080에만 연다. 노트북에서: ssh -L 8080:localhost:8080 <이 서버>  →  http://localhost:8080
#
# 프로세스 그룹 하나로 띄우고 그 PGID를 pidfile에 적는다. 중지는 그룹 전체에 신호를 보내므로 채점 worker까지 같이 끝난다.
# (pkill -f로 이름을 찾으면 그 이름을 명령줄에 가진 셸 자신도 죽는다 — exp-run 스킬 §3)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
PY=/home/nxclab/tools/dashboard-venv/bin/python
PIDF=/home/nxclab/scratch/dashboard.pid
LOG=/home/nxclab/scratch/dashboard.log

alive() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null; }

case "${1:-status}" in
  start)
    if alive; then echo "already running (pgid $(cat "$PIDF"))"; exit 0; fi
    cd "$HERE"
    setsid "$PY" server.py >> "$LOG" 2>&1 < /dev/null &
    echo $! > "$PIDF"
    sleep 3
    if alive && curl -s -m3 -o /dev/null http://127.0.0.1:8080/api/ping; then
      echo "started (pgid $(cat "$PIDF")), log $LOG"
    else
      echo "failed to start; last log lines:"; tail -20 "$LOG"; exit 1
    fi ;;
  stop)
    if alive; then kill -TERM -- "-$(cat "$PIDF")" 2>/dev/null; sleep 1; echo stopped; else echo "not running"; fi
    rm -f "$PIDF" ;;
  restart) "$0" stop; "$0" start ;;
  status) if alive; then echo "running (pgid $(cat "$PIDF"))"; else echo "not running"; fi ;;
  log) tail -50 "$LOG" ;;
  *) echo "usage: $0 start|stop|restart|status|log"; exit 2 ;;
esac
