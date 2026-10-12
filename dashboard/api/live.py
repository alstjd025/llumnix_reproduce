"""② 실시간 페이지: 체인 스크립트, 러너 파드, 진행 중인 run과 그 검사.
그래프 데이터는 결과 페이지와 같은 /api/runs/{name}/series를 쓴다(진행 중이면 중간 결과 interim이 붙는다)."""
import os
import time

from fastapi import APIRouter

import checks
from api import jsonable
from checks.cluster import _pod_problem
from config import CFG
from sources import chains, dispatch, k8s, runs, timeseries

router = APIRouter()


def _runner_pods():
    ps = k8s.pods()
    out = []
    for p in ps.get("items", []):
        n = p["metadata"]["name"]
        if n.startswith(CFG["kube"]["runner_job_prefix"]):
            out.append(dict(name=n, phase=p["status"].get("phase"), problem=_pod_problem(p),
                            started=p["status"].get("startTime")))
    return sorted(out, key=lambda x: x["started"] or "", reverse=True)


@router.get("/live")
def live():
    now = time.time()
    rs = runs.list_runs()
    active = next((r for r in rs if r["state"] == "running"), None)
    recent = active or next((r for r in rs if r["idle_s"] < 3600), None)
    ch = chains.list_chains()
    pods = _runner_pods()
    out = dict(now=now, chains=ch, runner_pods=pods, active=active, recent=recent)
    if active:
        d = runs.run_dir(active["name"])
        sm = os.path.join(d, "server_metrics")
        t0 = timeseries.run_t0(sm)
        cfg = runs.run_config(d) or {}
        eng = {}
        for port in CFG["kube"]["engine_ports"]:
            s = timeseries.engine_series(sm, port, t0 or 0)
            if s:
                eng[str(port)] = s
        ctx = dict(run=active, cfg=cfg, t0=t0, now=now, engines=eng, dispatch=dispatch.parse(d),
                   chains=ch, runner_pods=pods)
        dur = cfg.get("duration_min")
        warm = cfg.get("warmup_sec") or 0
        out["progress"] = dict(t0=t0, elapsed_s=(now - t0) if t0 else None,
                               planned_s=(dur * 60 + warm) if dur else None, duration_min=dur, warmup_s=warm)
        out["checks"] = checks.evaluate("live", ctx)
    else:
        out["checks"] = checks.evaluate("live", dict(run={}, chains=ch, runner_pods=pods, now=now, t0=None))
    return jsonable(out)
