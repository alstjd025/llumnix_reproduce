#!/usr/bin/env bash
# 실험하지 않는 동안 GPU 8장을 Sionna 워크로드에 넘기고, 실험할 때 Llumnix 엔진으로 되돌린다.
#
#   ms_dev/scripts/gpu_idle.sh start     엔진을 내리고 Sionna를 띄운다
#   ms_dev/scripts/gpu_idle.sh stop      Sionna를 끄고 엔진을 되돌린 뒤, 엔진 4대 응답·바이너리·GPU 온도까지 확인한다
#   ms_dev/scripts/gpu_idle.sh status
#
# 왜 필요한가 (2026-10-13): 서버 공지 — GPU를 연구에 쓰지 않는 동안에는 Sionna 워크로드
# (/NHNHOME/share/server_setup/NXC_GPU, 사본 ~/NXC_GPU)를 반드시 돌린다. 그런데 vLLM 엔진 4대(TP=2)가 쉬는 동안에도
# GPU마다 166/179 GiB를 잡고 있어서, 엔진을 내리지 않으면 Sionna가 쓸 메모리가 없다.
#
# 엔진을 내리는 방법은 leaderworkerset llumnix/neutral의 replicas를 0으로 하는 것이다(같은 서버의 powertest 프로젝트가
# 2026-10-13에 같은 방식으로 GPU를 빌렸다). 스케줄러·게이트웨이·redis는 그대로 둔다. 되돌리면 neutral-0이 같은 템플릿으로
# 새로 만들어지고, 엔진 4대가 모두 응답하기까지 약 8~9분이 걸린다.
#
# stop 뒤 GPU 온도를 기다리는 이유: Sionna는 GPU를 계속 최대 부하로 돌린다. 논문 run은 GPU가 쉬던 상태(27~29 °C)에서
# 시작했으므로, 온도가 COOL_C 아래로 내려올 때까지 기다려 측정 조건을 맞춘다.
set -uo pipefail

NS=llumnix
LWS=neutral
SIONNA_DIR=$HOME/NXC_GPU
WORKER_PATTERN=/tmp/sionna_stress/worker.py
LOGDIR=/home/nxclab/scratch/sionna
COOL_C=${COOL_C:-35}            # stop 뒤 모든 GPU가 이 온도(°C) 이하가 될 때까지 기다린다
COOL_TIMEOUT=${COOL_TIMEOUT:-1800}
ENGINE_TIMEOUT=${ENGINE_TIMEOUT:-1500}
EXPECT_POWER_W=${EXPECT_POWER_W:-1000}
REPO=/home/nxclab/llumnix_reproduce
mkdir -p "$LOGDIR"

say() { echo "[gpu_idle $(date '+%m-%d %H:%M:%S')] $*"; }
die() { say "ABORT: $*"; exit 1; }

workers() { pgrep -f "$WORKER_PATTERN" 2>/dev/null | wc -l; }
replicas() { kubectl -n $NS get lws $LWS -o jsonpath='{.spec.replicas}' 2>/dev/null; }
gpu_mem_max() { nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits | sort -n | tail -1; }
gpu_temp_max() { nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader,nounits | sort -n | tail -1; }

# 도는 실험이 있는가: 러너 파드가 Pending/Running이거나, 드라이버 스크립트(run_exp*.sh)가 살아 있다.
experiment_running() {
  local pods chains
  pods=$(kubectl -n $NS get pods --no-headers 2>/dev/null | awk '$1 ~ /^bench-runner/ && ($3=="Running" || $3=="Pending" || $3 ~ /Init|Creating/)' | wc -l)
  chains=$(pgrep -f "run_exp[0-9a-z_]*\.sh" 2>/dev/null | wc -l)
  [ "$pods" -gt 0 ] || [ "$chains" -gt 0 ]
}

status() {
  say "Sionna worker 수: $(workers)  (GPU 8장 × 8 = 64가 정상)"
  say "엔진 LWS replicas: $(replicas)  (1 = 엔진이 떠 있다, 0 = 내려가 있다)"
  kubectl -n $NS get pods --no-headers 2>/dev/null | awk '$1 ~ /^neutral/' | sed 's/^/    /'
  nvidia-smi --query-gpu=index,utilization.gpu,memory.used,temperature.gpu,power.draw,power.limit --format=csv,noheader | sed 's/^/    GPU /'
  local last; last=$(ls -t "$LOGDIR"/sionna_*.log 2>/dev/null | head -1)
  [ -n "$last" ] && say "최근 Sionna 로그: $last"
}

start() {
  [ "${1:-}" = "--force" ] || ! experiment_running || die "실험이 돌고 있다(러너 파드나 run_exp*.sh). 끝난 뒤에 다시 하거나 --force"
  [ "$(workers)" -eq 0 ] || die "Sionna가 이미 돌고 있다 ($(workers) workers)"
  [ -f "$HOME/sionna/.venv/bin/activate" ] || die "Sionna가 설치되어 있지 않다 (~/NXC_GPU/install_sionna.sh)"

  if [ "$(replicas)" != "0" ]; then
    say "엔진을 내린다: kubectl scale lws $LWS --replicas=0"
    kubectl -n $NS scale lws $LWS --replicas=0 || die "scale 실패"
  fi
  say "GPU 메모리가 빌 때까지 기다린다(모든 GPU 2 GiB 미만)"
  for i in $(seq 1 60); do
    m=$(gpu_mem_max); [ "$m" -lt 2048 ] && break; sleep 5
  done
  [ "$(gpu_mem_max)" -lt 2048 ] || die "5분이 지나도 GPU 메모리가 비지 않았다 (최대 $(gpu_mem_max) MiB). 엔진은 내려가 있다 — stop으로 되돌릴 수 있다"

  local log=$LOGDIR/sionna_$(date +%m%d_%H%M).log
  say "Sionna를 띄운다 (GPU 8장 × worker 8, 45초 간격으로 늘어난다). 로그 $log"
  # ./run_sionna_rt_corrected.sh 로 실행해야 stop_sionna_rt.sh가 실행 스크립트 프로세스를 찾는다(그 스크립트의 LAUNCHER_PATTERNS)
  (cd "$SIONNA_DIR" && setsid nohup ./run_sionna_rt_corrected.sh > "$log" 2>&1 < /dev/null &)
  sleep 30
  local w; w=$(workers)
  [ "$w" -gt 0 ] || { tail -20 "$log"; die "worker가 뜨지 않았다"; }
  say "worker $w개 실행 중. 전부(64개) 올라오기까지 약 6분(8번째 worker가 7×45초 뒤 시작)"
  say "되돌리려면: $0 stop"
}

stop() {
  if [ "$(workers)" -gt 0 ]; then
    say "Sionna를 끈다"
    # 종료 스크립트는 신호를 보낸 뒤 1~2초만 보고 "남아 있다"고 실패한다. worker는 GPU 메모리를 내려놓느라
    # 몇 초 더 걸리므로(2026-10-20 실제로 이것 때문에 체인이 멈췄다) 그 판정을 믿지 않고 직접 기다린다.
    "$SIONNA_DIR/stop_sionna_rt.sh" >/dev/null 2>&1 || true
    for i in $(seq 1 30); do [ "$(workers)" -eq 0 ] && break; sleep 2; done
    if [ "$(workers)" -gt 0 ]; then
      "$SIONNA_DIR/stop_sionna_rt.sh" --force >/dev/null 2>&1 || true
      for i in $(seq 1 30); do [ "$(workers)" -eq 0 ] && break; sleep 2; done
    fi
    [ "$(workers)" -eq 0 ] || die "Sionna worker $(workers)개가 2분이 지나도 남아 있다"
    say "Sionna 종료 확인"
  else
    say "Sionna가 돌고 있지 않다"
  fi
  for i in $(seq 1 60); do [ "$(gpu_mem_max)" -lt 2048 ] && break; sleep 5; done
  [ "$(gpu_mem_max)" -lt 2048 ] || say "경고: GPU 메모리가 아직 비지 않았다 (최대 $(gpu_mem_max) MiB) — 다른 프로세스가 GPU를 쓰고 있을 수 있다"

  if [ "$(replicas)" != "1" ]; then
    say "엔진을 되돌린다: kubectl scale lws $LWS --replicas=1"
    kubectl -n $NS scale lws $LWS --replicas=1 || die "scale 실패"
  fi
  say "엔진 4대가 /health에 응답할 때까지 기다린다(최대 ${ENGINE_TIMEOUT}초, 보통 8~9분)"
  local t0=$SECONDS ok=0 ip
  while [ $((SECONDS - t0)) -lt "$ENGINE_TIMEOUT" ]; do
    ip=$(kubectl -n $NS get pod neutral-0 -o jsonpath='{.status.podIP}' 2>/dev/null)
    ok=0
    if [ -n "$ip" ]; then
      for p in 8000 8001 8002 8003; do
        [ "$(curl -s -m2 -o /dev/null -w '%{http_code}' "http://$ip:$p/health")" = 200 ] && ok=$((ok + 1))
      done
    fi
    [ "$ok" -eq 4 ] && break
    sleep 15
  done
  [ "$ok" -eq 4 ] || die "엔진이 ${ENGINE_TIMEOUT}초 안에 4대 모두 뜨지 않았다 ($ok/4). kubectl -n $NS get pods 로 확인"
  say "엔진 4/4 응답 ($(( (SECONDS - t0) / 60 ))분)"

  # 실행 중인 바이너리가 bin/의 파일과 같은지 (CLAUDE.md: 확인은 파드 안 md5sum /proc/1/exe 하나뿐이다)
  local pod want got
  pod=$(kubectl -n $NS get pods --no-headers | awk '$1 ~ /^scheduler-/ && $3=="Running"{print $1; exit}')
  want=$(md5sum "$REPO/bin/scheduler-exp07" | cut -c1-8)
  got=$(kubectl -n $NS exec "$pod" -- md5sum /proc/1/exe 2>/dev/null | cut -c1-8)
  [ "$want" = "$got" ] && say "스케줄러 바이너리 $got = bin/scheduler-exp07" || say "경고: 스케줄러 /proc/1/exe $got ≠ bin/scheduler-exp07 $want"

  # 전력 제한: 다른 프로젝트가 바꿨다 되돌리는 값이라 확인한다
  local bad; bad=$(nvidia-smi --query-gpu=index,power.limit --format=csv,noheader,nounits | awk -F', ' -v w="$EXPECT_POWER_W" '$2+0 != w+0 {print "GPU"$1"="$2"W"}')
  [ -z "$bad" ] && say "GPU 전력 제한 모두 ${EXPECT_POWER_W} W" || say "경고: 전력 제한이 ${EXPECT_POWER_W} W가 아니다: $bad"

  say "GPU 온도가 ${COOL_C} °C 이하가 될 때까지 기다린다(최대 ${COOL_TIMEOUT}초). 지금 최고 $(gpu_temp_max) °C"
  t0=$SECONDS
  while [ "$(gpu_temp_max)" -gt "$COOL_C" ] && [ $((SECONDS - t0)) -lt "$COOL_TIMEOUT" ]; do sleep 20; done
  [ "$(gpu_temp_max)" -le "$COOL_C" ] && say "GPU 최고 $(gpu_temp_max) °C — 실험을 걸 수 있다" \
    || say "경고: ${COOL_TIMEOUT}초가 지나도 최고 $(gpu_temp_max) °C — 실험 전에 확인한다"
}

case "${1:-status}" in
  start) shift; start "${1:-}" ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start [--force] | stop | status"; exit 2 ;;
esac
