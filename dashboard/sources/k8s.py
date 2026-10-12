"""kubectl 조회. 대시보드는 읽기 전용이므로 상태를 바꾸는 명령은 이 모듈을 통해 실행될 수 없다.

허용하는 것: `get`, `logs`, 그리고 파드 안에서 `md5sum /proc/1/exe`를 실행하는 `exec` 하나.
`/proc/1/exe`의 md5는 그 파드가 실제로 실행 중인 바이너리를 알려 주는 유일한 값이다. bin/의 파일이나
deployment spec을 읽는 것으로는 설치에 실패한 파일이나 CrashLoopBackOff에 빠진 새 파드를 잡지 못한다
(exp-run 스킬 traps.md 함정 B).
"""
import json
import subprocess
import time

from config import CFG

NS = CFG["kube"]["namespace"]
_ALLOWED = {"get", "logs"}
_cache = {}


class ReadOnlyViolation(RuntimeError):
    pass


def _run(args, timeout=15):
    if not args or (args[0] not in _ALLOWED and args[:1] != ["exec"]):
        raise ReadOnlyViolation(f"kubectl {args[:1]} is not a read-only verb")
    if args[0] == "exec" and args[-2:] != ["md5sum", "/proc/1/exe"]:
        raise ReadOnlyViolation("exec is allowed only for md5sum /proc/1/exe")
    p = subprocess.run(["kubectl", "-n", NS] + args, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip()[:500])
    return p.stdout


def cached(key, ttl, fn):
    """같은 화면 갱신 안에서 검사 여러 개가 같은 kubectl 결과를 쓰도록 ttl초 동안 재사용한다."""
    now = time.time()
    hit = _cache.get(key)
    if hit and now - hit[0] < ttl:
        return hit[1]
    try:
        val = fn()
    except Exception as e:  # 실패도 캐시한다. 그래야 kubectl이 멈췄을 때 검사마다 15초씩 기다리지 않는다
        val = {"error": str(e)}
    _cache[key] = (now, val)
    return val


def get_json(kind, ttl=5):
    return cached(f"get:{kind}", ttl, lambda: json.loads(_run(["get", kind, "-o", "json"])))


def pods(ttl=5):
    return get_json("pods", ttl)


def deploy(name, ttl=5):
    return cached(f"deploy:{name}", ttl, lambda: json.loads(_run(["get", "deploy", name, "-o", "json"])))


def lws(name="neutral", ttl=10):
    return cached(f"lws:{name}", ttl, lambda: json.loads(_run(["get", "lws", name, "-o", "json"])))


def configmap_exists(name, ttl=30):
    def f():
        _run(["get", "configmap", name, "-o", "name"])
        return {"exists": True}
    return cached(f"cm:{name}", ttl, f)


def pod_exe_md5(pod, container=None, ttl=30):
    def f():
        args = ["exec", pod] + (["-c", container] if container else []) + ["--", "md5sum", "/proc/1/exe"]
        return {"md5": _run(args, timeout=10).split()[0]}
    return cached(f"md5:{pod}:{container}", ttl, f)


def logs_tail(pod, lines=200, ttl=5):
    return cached(f"logs:{pod}:{lines}", ttl,
                  lambda: {"text": _run(["logs", pod, f"--tail={lines}"], timeout=10)})


def container_spec(obj, name=None):
    """deployment나 lws에서 컨테이너 하나의 spec을 꺼낸다."""
    spec = obj.get("spec", {})
    tmpl = spec.get("template") or spec.get("leaderWorkerTemplate", {}).get("workerTemplate", {})
    cs = tmpl.get("spec", {}).get("containers", [])
    if name:
        cs = [c for c in cs if c.get("name") == name]
    return cs[0] if cs else {}


def env_of(container, key, default="unset"):
    for e in container.get("env", []) or []:
        if e.get("name") == key:
            return e.get("value", "")
    return default


def flag_value(args, flag):
    """['--x', 'v'] 또는 ['--x=v'] 형태 둘 다에서 값을 꺼낸다."""
    for i, a in enumerate(args or []):
        if a == flag and i + 1 < len(args):
            return args[i + 1]
        if a.startswith(flag + "="):
            return a.split("=", 1)[1]
    return None
