"""① 상태 페이지: 클러스터 검사, GPU, 디스크, 엔진, 스케줄러 플래그."""
import time

from fastapi import APIRouter

import checks
from api import jsonable
from config import CFG
from sources import host, k8s, recorder

router = APIRouter()


@router.get("/status")
def status():
    d = k8s.deploy(CFG["kube"]["scheduler_deploy"])
    args = k8s.container_spec(d).get("args") or [] if "error" not in d else []
    pods = k8s.pods()
    pod_rows = []
    for p in pods.get("items", []):
        st = p.get("status", {})
        cs = st.get("containerStatuses", []) or []
        pod_rows.append(dict(name=p["metadata"]["name"], phase=st.get("phase"),
                             ready=f"{sum(c.get('ready', False) for c in cs)}/{len(cs)}",
                             restarts=sum(c.get("restartCount", 0) for c in cs),
                             started=st.get("startTime")))
    return jsonable(dict(
        now=time.time(),
        checks=checks.evaluate("cluster"),
        gpus=host.gpus().get("gpus", []),
        disks=host.disks(),
        engines=host.engine_health(),
        pods=pod_rows,
        scheduler_args=args,
        recorder=recorder.status(),
    ))


@router.get("/ping")
def ping():
    return {"ok": True}
