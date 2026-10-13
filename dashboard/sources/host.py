"""호스트에서 직접 읽는 것: GPU, 디스크, 파일 md5, 엔진의 /health 응답."""
import hashlib
import os
import shutil
import subprocess
import urllib.request

from sources import k8s
from config import CFG

_md5_cache = {}


def gpus():
    q = "index,name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu,power.limit"
    def f():
        out = subprocess.run(["nvidia-smi", f"--query-gpu={q}", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10).stdout
        rows = []
        for line in out.strip().splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) != 8:
                continue
            rows.append(dict(index=int(p[0]), name=p[1], util=float(p[2]), mem_used_mib=float(p[3]),
                             mem_total_mib=float(p[4]), power_w=float(p[5]), temp_c=float(p[6]), power_limit_w=float(p[7])))
        return {"gpus": rows}
    return k8s.cached("gpus", 3, f)


def disks():
    out = []
    for d in CFG["paths"].get("disks", []):
        try:
            u = shutil.disk_usage(d)
            out.append(dict(path=d, free_gb=u.free / 1e9, total_gb=u.total / 1e9))
        except OSError as e:
            out.append(dict(path=d, error=str(e)))
    return out


def file_md5(p):
    """파일 md5. 크기와 수정 시각이 같으면 다시 계산하지 않는다."""
    try:
        st = os.stat(p)
    except OSError:
        return None
    key = (p, st.st_size, st.st_mtime)
    if key not in _md5_cache:
        h = hashlib.md5()
        with open(p, "rb") as f:
            for b in iter(lambda: f.read(1 << 20), b""):
                h.update(b)
        _md5_cache[key] = h.hexdigest()
    return _md5_cache[key]


def engine_pod_ip():
    ps = k8s.pods()
    for p in ps.get("items", []):
        if p["metadata"]["name"] == CFG["kube"]["engine_pod"]:
            return p.get("status", {}).get("podIP")
    return None


def engine_health():
    """엔진 포트마다 /health가 200을 주는지. 파드의 startup probe는 8000만 보므로 파드가 Ready여도
    나머지 셋이 아직 가중치를 올리는 중일 수 있다(exp-run 스킬 §4, 엔진이 4대보다 적게 뜬 조건)."""
    def f():
        ip = engine_pod_ip()
        res = []
        for port in CFG["kube"]["engine_ports"]:
            ok, err = False, None
            if ip:
                try:
                    with urllib.request.urlopen(f"http://{ip}:{port}/health", timeout=2) as r:
                        ok = r.status == 200
                except Exception as e:
                    err = str(e)[:120]
            res.append(dict(port=port, ok=ok, error=err if not ok else None))
        return {"ip": ip, "engines": res}
    return k8s.cached("engine_health", 5, f)


def sionna_workers():
    """Sionna 워크로드 worker 수(서버 공지: GPU를 연구에 쓰지 않는 동안 돌린다). 프로세스 명령줄로 센다."""
    n = 0
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            if b"/tmp/sionna_stress/worker.py" in open(f"/proc/{pid}/cmdline", "rb").read():
                n += 1
        except OSError:
            pass
    return n
