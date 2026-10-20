"""진행 중인 run 동안 run 디렉토리에 남지 않는 것을 대시보드가 따로 기록한다(2026-10-12 사용자 결정: 실험 중에도 켠다).

1. FluidServe 내부 값: 스케줄러 /metrics의 scheduler_fluidserve_* (histogram bucket 제외, 약 200개 값)를 interval_s마다.
   러너 수집기(llumnix_metrics.py)는 정해진 목록(SCHEDULER_METRICS)만 남겨서 이 값들이 run 디렉토리에 없다.
   예: 인스턴스별 예측 iteration 시간(predicted_step_ms)과 실측(observed_step_ms), 허용 속도(gate_allowance_ms),
   클래스별 인스턴스 상한(instcap_limit).
2. 스택 스냅샷: run이 시작될 때와, run 도중 스케줄러 파드가 바뀔 때마다 그 파드의 /proc/1/exe md5, deployment 인자,
   게이트웨이 command·인자·md5, 엔진 env. run 디렉토리에는 그 run이 쓴 바이너리와 플래그가 남지 않는다(드라이버 로그에만 있다).

기록 위치: <cache>/recorder/<run 이름>/fluidserve.jsonl, stack.json. results/ 안에는 쓰지 않는다.

측정에 주는 영향: 스케줄러 프로세스에 HTTP 요청이 interval_s마다 하나 더 들어간다. 러너 수집기가 이미 같은 엔드포인트를
1초마다 읽고 있으므로 그 위에 1/interval_s만큼 더해지는 것이다. 대시보드가 떠 있지 않으면 기록되지 않는다.
"""
import json
import os
import threading
import time
import urllib.request

from config import CFG, path
from sources import k8s, runs

RC = CFG.get("recorder", {})
_state = dict(active=None, checked=0.0, sched_pod=None, last_error=None, samples=0)


def rec_dir(name):
    return os.path.join(path("cache"), "recorder", name)


def _scheduler_url():
    svc = k8s.cached("svc:scheduler", 60, lambda: json.loads(k8s._run(["get", "svc", CFG["kube"]["scheduler_deploy"], "-o", "json"])))
    ip = svc.get("spec", {}).get("clusterIP")
    port = (svc.get("spec", {}).get("ports") or [{}])[0].get("port", 8088)
    return f"http://{ip}:{port}/metrics" if ip else None


def scrape():
    """scheduler_fluidserve_* 값을 {이름|라벨: 값}으로. FluidServe가 아닌 정책이면 빈 dict."""
    url = _scheduler_url()
    if not url:
        return {}
    with urllib.request.urlopen(url, timeout=3) as r:
        text = r.read().decode(errors="replace")
    out = {}
    for line in text.splitlines():
        if not line.startswith("scheduler_fluidserve_") or "_bucket" in line:
            continue
        try:
            key, val = line.rsplit(" ", 1)
            v = float(val)
        except ValueError:
            continue
        if "{" in key:
            name, labels = key[:-1].split("{", 1)
            key = name + "|" + labels.replace('"', "")
        out[key] = v
    return out


def _stack_snapshot():
    snap = dict(t=time.time())
    try:
        ps = [p for p in k8s.pods(ttl=0).get("items", []) if p["metadata"]["name"].startswith(CFG["kube"]["scheduler_deploy"] + "-")
              and p.get("status", {}).get("phase") == "Running"]
        snap["scheduler_pods"] = [p["metadata"]["name"] for p in ps]
        snap["scheduler_md5"] = {p["metadata"]["name"]: k8s.pod_exe_md5(p["metadata"]["name"], ttl=0).get("md5") for p in ps}
        d = k8s.deploy(CFG["kube"]["scheduler_deploy"], ttl=0)
        snap["scheduler_args"] = k8s.container_spec(d).get("args") or []
        g = k8s.deploy(CFG["kube"]["gateway_deploy"], ttl=0)
        gc = k8s.container_spec(g)
        snap["gateway_command"] = gc.get("command")
        snap["gateway_args"] = gc.get("args") or []
        gp = [p["metadata"]["name"] for p in k8s.pods(ttl=0).get("items", [])
              if p["metadata"]["name"].startswith(CFG["kube"]["gateway_deploy"] + "-") and p.get("status", {}).get("phase") == "Running"]
        snap["gateway_md5"] = {n: k8s.pod_exe_md5(n, ttl=0).get("md5") for n in gp}
        lw = k8s.lws(ttl=0)
        c = k8s.container_spec(lw, "vllm")
        snap["engine_env"] = {e["name"]: e.get("value") for e in c.get("env", []) or [] if "value" in e}
    except Exception as e:
        snap["error"] = f"{type(e).__name__}: {e}"
    return snap


def _write_stack(name, snap):
    d = rec_dir(name)
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "stack.json")
    try:
        cur = json.load(open(p))
    except (OSError, ValueError):
        cur = {"snapshots": []}
    cur["snapshots"].append(snap)
    json.dump(cur, open(p + ".tmp", "w"), indent=1)
    os.replace(p + ".tmp", p)


def _loop():
    iv = float(RC.get("interval_s", 2))
    while True:
        t0 = time.time()
        try:
            if t0 - _state["checked"] > 10:   # 진행 중인 run 찾기는 10초마다(디렉토리를 훑어야 해서)
                rs = runs.list_runs()
                # 수집기가 지금도 쓰고 있는 run만 진행 중으로 본다(gateway.jsonl이 15초 안에 바뀜). "2분 동안 파일이 안
                # 바뀌면 끝" 기준을 쓰면, run이 끝난 뒤 2분 동안 다음 조건의 스케줄러 값이 이 run에 기록된다
                # (2026-10-20 exp141r1 FluidServe run에 63개가 잘못 들어갔다).
                act = None
                for r in rs:
                    if r["state"] != "running":
                        continue
                    gw = os.path.join(runs.run_dir(r["name"]), "server_metrics", "gateway.jsonl")
                    if os.path.exists(gw) and t0 - os.path.getmtime(gw) < 15:
                        act = r["name"]
                        break
                _state["checked"] = t0
                if act != _state["active"]:
                    _state.update(active=act, sched_pod=None, samples=0)
                if act:
                    snap = None
                    pods = [p["metadata"]["name"] for p in k8s.pods(ttl=0).get("items", [])
                            if p["metadata"]["name"].startswith(CFG["kube"]["scheduler_deploy"] + "-")
                            and p.get("status", {}).get("phase") == "Running"]
                    if sorted(pods) != _state["sched_pod"]:
                        snap = _stack_snapshot()
                        _state["sched_pod"] = sorted(pods)
                        _write_stack(act, snap)
            if _state["active"]:
                vals = scrape()
                if vals:
                    d = rec_dir(_state["active"])
                    os.makedirs(d, exist_ok=True)
                    with open(os.path.join(d, "fluidserve.jsonl"), "a") as f:
                        f.write(json.dumps(dict(t=time.time(), **vals)) + "\n")
                    _state["samples"] += 1
            _state["last_error"] = None
        except Exception as e:
            _state["last_error"] = f"{type(e).__name__}: {e}"
        time.sleep(max(0.2, iv - (time.time() - t0)))


def start():
    if RC.get("enabled", True):
        threading.Thread(target=_loop, daemon=True, name="recorder").start()


def status():
    return dict(enabled=RC.get("enabled", True), interval_s=RC.get("interval_s", 2), **_state)
