"""③ 결과 페이지: run 목록, run 하나의 상세(점수·검사·시계열·지연 분포), 두 run의 설정 비교."""
import hashlib
import json
import os
import threading
import time

import numpy as np
from fastapi import APIRouter, HTTPException

import checks
from api import jsonable
from config import CFG, HERE
from sources import dispatch, live, runs, scoring, timeseries

router = APIRouter()
_summary_lock = threading.Lock()


def _checks_version():
    h = hashlib.md5()
    for d in ("checks", "sources"):
        for f in sorted(os.listdir(os.path.join(HERE, d))):
            if f.endswith(".py"):
                h.update(open(os.path.join(HERE, d, f), "rb").read())
    return h.hexdigest()[:12]


def _dir(name):
    try:
        return runs.run_dir(name)
    except FileNotFoundError:
        raise HTTPException(404, f"no run {name}")


def engine_totals(d):
    sm = os.path.join(d, "server_metrics")
    t0 = timeseries.run_t0(sm)
    out = {}
    for port in CFG["kube"]["engine_ports"]:
        s = timeseries.engine_series(sm, port, t0 or 0)
        if s:
            out[str(port)] = s["totals"]
    return out


def summary(name, info=None):
    """채점 결과 + run 검사 + 엔진 누적값. 끝난 run은 디스크에 캐시한다(점수 캐시 키와 검사 코드 md5가 같으면 재사용)."""
    d = _dir(name)
    info = info or dict(runs.parse_name(name), name=name, excluded=runs.exclusion_reasons(name))
    st, score = scoring.status(name, d)
    if st != "scored":
        return dict(score_state=st, score=score, checks=None, engines=None)
    p = os.path.join(scoring.cache_dir(name), "summary.json")
    key = [score.get("scored_at"), _checks_version(), info.get("excluded")]
    try:
        c = json.load(open(p))
        if c.get("key") == key:
            return c
    except (OSError, ValueError):
        pass
    with _summary_lock:
        eng = engine_totals(d)
        req = scoring.requests_table(name)
        ctx = dict(name=name, dir=d, info=info, score=score, requests=req, engines=eng)
        out = jsonable(dict(key=key, score_state=st, score={k: v for k, v in score.items() if k != "trace"},
                            checks=checks.evaluate("run", ctx), engines=eng,
                            preemptions=sum((e.get("preemptions") or 0) for e in eng.values()) if eng else None))
        tmp = p + ".tmp"
        json.dump(out, open(tmp, "w"))
        os.replace(tmp, p)
    return out


@router.get("/runs")
def list_runs():
    out = []
    for r in runs.list_runs():
        row = dict(r)
        if r["state"] == "done":
            s = summary(r["name"], r)
            row["score_state"] = s["score_state"]
            sc = s.get("score") or {}
            for k in ("offered", "admitted", "rejected_pct", "goodput_tok_s", "n", "error"):
                row[k] = sc.get(k)
            row["preemptions"] = s.get("preemptions")
            row["checks"] = [dict(id=c["id"], status=c["status"], title=c["title"], summary=c["summary"])
                             for c in (s.get("checks") or [])]
        else:
            row["score_state"] = r["state"]
        out.append(row)
    return jsonable(out)


@router.get("/runs/{name}")
def run_detail(name):
    d = _dir(name)
    info = dict(runs.parse_name(name), name=name, excluded=runs.exclusion_reasons(name))
    st, age = runs.state_of(d)
    info.update(state=st, idle_s=age)
    s = summary(name, info) if st == "done" else dict(score_state=st)
    return jsonable(dict(info=info, config=runs.run_config(d), **s))


@router.get("/runs/{name}/series")
def run_series(name):
    """시계열 전부. 시간축은 수집기가 첫 줄을 쓴 시각을 0으로 한 초 단위다."""
    d = _dir(name)
    sm = os.path.join(d, "server_metrics")
    t0 = timeseries.run_t0(sm)
    if t0 is None:
        return dict(t0=None)
    running = runs.state_of(d)[0] == "running"
    disp = dispatch.summarize(d, t0, live_now=time.time() if running else None)
    inst_port = (disp or {}).get("inst_port", {})
    engines = {}
    for port in CFG["kube"]["engine_ports"]:
        s = timeseries.engine_series(sm, port, t0)
        if s:
            s.pop("totals", None)
            engines[str(port)] = s
    out = dict(t0=t0, running=running, engines=engines, gateway=timeseries.gateway_series(sm, t0),
               scheduler=timeseries.scheduler_series(sm, t0, inst_port), dispatch=disp)
    if running:
        out["interim"] = live.interim(d, t0)
    return jsonable(out)


@router.get("/runs/{name}/latency")
def run_latency(name):
    """클래스별 TTFT·토큰 사이 시간의 분포(분위수 101개)와, 10초 단위 결과(ladder95 성공/실패/거절/미완료)."""
    _dir(name)
    req = scoring.requests_table(name)
    if req is None:
        raise HTTPException(409, "not scored yet")
    sc = scoring.cached_score(name, runs.run_dir(name)) or {}
    t0_series = timeseries.run_t0(os.path.join(runs.run_dir(name), "server_metrics"))
    shift = (sc.get("t0_requests") or 0) - (t0_series or 0) if sc.get("t0_requests") and t0_series else 0.0
    qs = np.linspace(0, 1, 101)
    cdf, outcome = {}, {}
    bin_s = 10
    nb = int((req["rel"].max() + shift) // bin_s) + 1 if len(req) else 0
    for c, sub in req.groupby("class"):
        served = sub[~sub["rejected"].astype(bool) & ~sub["errored"].astype(bool)]
        cdf[c] = dict(
            ttft_s=np.nanquantile(served["first_token_latency"], qs).tolist() if served["first_token_latency"].notna().any() else None,
            itl_ms=np.nanquantile(served["itl_ms"], qs).tolist() if served["itl_ms"].notna().any() else None,
            n=int(len(served)))
        b = ((sub["rel"] + shift) // bin_s).astype(int).clip(0, max(nb - 1, 0))
        cut = sub["cutoff"].astype(bool)
        rej = sub["rejected"].astype(bool) & ~cut
        err = sub["errored"].astype(bool) & ~rej & ~cut
        met = sub["ladder_ok"].astype(bool) & ~rej & ~err & ~cut
        miss = ~met & ~rej & ~err & ~cut
        outcome[c] = {k: np.bincount(b[m], minlength=nb).tolist()
                      for k, m in (("met", met), ("missed", miss), ("rejected", rej), ("errored", err), ("cutoff", cut))}
    return jsonable(dict(q=qs.tolist(), cdf=cdf, bin_s=bin_s, t=[i * bin_s for i in range(nb)], outcome=outcome,
                         budgets=CFG["classes"]))


def _flatten(x, pre=""):
    out = {}
    if isinstance(x, dict):
        for k, v in x.items():
            out.update(_flatten(v, f"{pre}.{k}" if pre else str(k)))
    elif isinstance(x, list) and len(x) <= 20 and all(not isinstance(v, (dict, list)) for v in x):
        out[pre] = json.dumps(x)
    elif isinstance(x, list):
        for i, v in enumerate(x):
            out.update(_flatten(v, f"{pre}[{i}]"))
    else:
        out[pre] = x
    return out


@router.get("/diff")
def diff(a: str, b: str):
    """두 run의 run_config.json을 펼쳐서 다른 항목만 보여 준다. 이름에서 읽은 arm·도착률도 같이 비교한다.
    ⚠ 스케줄러 바이너리 md5와 플래그는 run 디렉토리에 기록되지 않는다(단계 4에서 대시보드가 기록하기 시작한다)."""
    ca, cb = runs.run_config(_dir(a)) or {}, runs.run_config(_dir(b)) or {}
    fa, fb = _flatten(ca), _flatten(cb)
    fa.update({f"(이름).{k}": v for k, v in runs.parse_name(a).items()})
    fb.update({f"(이름).{k}": v for k, v in runs.parse_name(b).items()})
    skip = {"created_at", "server_session_name", "server_session_remote_dir", "server_session_local_dir",
            "(이름).date", "(이름).time"}
    rows, same = [], 0
    for k in sorted(set(fa) | set(fb)):
        if k in skip:
            continue
        va, vb = fa.get(k, "(없음)"), fb.get(k, "(없음)")
        if va == vb:
            same += 1
        else:
            rows.append(dict(key=k, a=va, b=vb))
    return jsonable(dict(a=a, b=b, differ=rows, same=same))
