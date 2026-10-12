"""scheduler_dispatch.log에서 요청 하나하나의 routing과 FluidServe 결정을 읽는다.

이 로그는 러너가 run 동안 스케줄러 파드의 로그를 받아 둔 것이다(llumnix_metrics.py, capture_logs). 쓰는 줄은 셋이다.

  utils.go       "dispatch request <id> to neutral instance <inst>"   모든 정책에 공통. 요청이 어느 인스턴스로 갔는가
  fluidserve.go  "dispatch request <id> fsplacement tier=.. waited=.. ... decision=route|shed inst=.. [reason=..]"
                 FluidServe만. 결정 종류, 기다린 시간(waited, ms), 거절 사유
  cms_read_client.go "instanceID=<inst>, ... api_server_port:<port>"  인스턴스 번호 → 엔진 포트

요청 번호는 request_ids.jsonl(러너가 쓴, 요청 번호 ↔ task_id ↔ 시작·끝 시각)과 맞춰 붙인다.
로그 시각은 glog 형식(I1010 07:42:29.273358)이고 UTC다. 연도는 run 이름의 YYMMDD에서 가져온다.
클래스는 task_id 접두어로 정하며, 그 규칙은 analysis_scripts의 class_of를 그대로 쓴다.
"""
import calendar
import json
import os
import re
import sys
import threading
from collections import OrderedDict

import numpy as np

from config import CFG, class_of_tier, path

sys.path.insert(0, path("analysis"))
from plot_per_engine_attainment import class_of  # noqa: E402  (채점과 같은 클래스 규칙)

_GLOG = re.compile(r"^[IWEF](\d\d)(\d\d) (\d\d):(\d\d):(\d\d)\.(\d+)")
_UTILS = re.compile(r"dispatch request (\S+) to neutral instance (\d+)")
_FS = re.compile(r"dispatch request (\S+) fsplacement tier=(\d+) waited=(\S+) .*?decision=(\w+) inst=(\d+)"
                 r"(?: reason=(\S+))?")
_CMS = re.compile(r"instanceID=(\d+),.*?api_server_port:(\d+)")

_cache = OrderedDict()
_lock = threading.Lock()


def _ts(line, year):
    m = _GLOG.match(line)
    if not m:
        return None
    mo, d, h, mi, s, frac = m.groups()
    return calendar.timegm((year, int(mo), int(d), int(h), int(mi), int(s))) + float("0." + frac)


def _year(run_name):
    try:
        return 2000 + int(run_name[:2])
    except ValueError:
        return 2026


def parse(run_dir):
    """run 하나의 로그 전체를 해석한다. 결과는 파일 크기·수정 시각이 같으면 재사용한다(진행 중인 run은 매번 다시 읽는다)."""
    log = os.path.join(run_dir, "server_metrics", "scheduler_dispatch.log")
    rid = os.path.join(run_dir, "request_ids.jsonl")
    try:
        key = (log, os.path.getsize(log), os.path.getmtime(log),
               os.path.getsize(rid) if os.path.exists(rid) else 0)
    except OSError:
        return None
    with _lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    year = _year(os.path.basename(run_dir))
    inst_port, routes, fs = {}, [], []
    with open(log, errors="replace") as f:
        for line in f:
            if "utils.go" in line and "dispatch request" in line:
                m = _UTILS.search(line)
                if m:
                    routes.append((_ts(line, year), m.group(1), m.group(2)))
            elif "fsplacement" in line:
                m = _FS.search(line)
                if m:
                    try:
                        waited = float(m.group(3))
                    except ValueError:
                        waited = np.nan
                    fs.append((_ts(line, year), m.group(1), int(m.group(2)), waited, m.group(4), m.group(5),
                               m.group(6)))
            elif "api_server_port" in line:
                m = _CMS.search(line)
                if m:
                    inst_port[m.group(1)] = int(m.group(2))
    req = {}
    if os.path.exists(rid):
        for line in open(rid):
            try:
                d = json.loads(line)
            except ValueError:
                continue
            req[d["request_id"].removeprefix("cmpl-")] = (d.get("task_id"), d.get("start_time"), d.get("end_time"))
    out = dict(inst_port=inst_port, routes=routes, fs=fs, req=req)
    with _lock:
        _cache[key] = out
        while len(_cache) > 8:
            _cache.popitem(last=False)
    return out


def summarize(run_dir, t0, bin_s=10, occ_step_s=5):
    """화면용 요약: 10초 단위 결정 수, 인스턴스별 클래스 구성, FluidServe의 대기 시간 분포."""
    p = parse(run_dir)
    if p is None:
        return None
    req, inst_port = p["req"], p["inst_port"]
    classes = list(CFG["classes"])

    def cls_of_req(r):
        v = req.get(r)
        return class_of(v[0]) if v else None

    # 1. 결정. FluidServe면 결정 줄(route/shed+사유)을, 아니면 routing 줄을 route로 센다.
    #    FluidServe 결정 줄의 tier로도 클래스를 알 수 있어서, request_ids에 없는(거절된) 요청도 클래스가 붙는다.
    events = []
    if p["fs"]:
        for ts, r, tier, waited, dec, inst, reason in p["fs"]:
            c = class_of_tier(tier) or cls_of_req(r)
            kind = dec if dec != "shed" else f"shed:{reason or '?'}"
            events.append((ts, c, kind))
    else:
        for ts, r, inst in p["routes"]:
            events.append((ts, cls_of_req(r), "route"))
    dec = None
    if events and t0:
        tmax = max(e[0] for e in events if e[0]) - t0
        nb = int(tmax // bin_s) + 1
        kinds = sorted({e[2] for e in events})
        counts = {c: {k: [0] * nb for k in kinds} for c in classes + ["?"]}
        for ts, c, k in events:
            if ts is None:
                continue
            b = int((ts - t0) // bin_s)
            if 0 <= b < nb:
                counts[c if c in classes else "?"][k][b] += 1
        dec = dict(bin_s=bin_s, t=[i * bin_s for i in range(nb)], kinds=kinds, counts=counts,
                   totals={c: {k: sum(v) for k, v in kc.items()} for c, kc in counts.items()})

    # 2. 인스턴스별 클래스 구성: routing 시각부터 그 요청이 끝난 시각(request_ids.end_time)까지 그 인스턴스에 있다고 본다.
    occ = None
    spans = []
    for ts, r, inst in p["routes"]:
        v = req.get(r)
        if not v or ts is None or v[2] is None:
            continue
        spans.append((inst, class_of(v[0]), ts - t0, float(v[2]) - t0))
    if spans and t0:
        tmax = max(s[3] for s in spans)
        grid = np.arange(0, tmax + occ_step_s, occ_step_s)
        occ = {"t": grid.tolist(), "instances": {}}
        for inst in sorted({s[0] for s in spans}, key=lambda i: inst_port.get(i, 0)):
            name = str(inst_port.get(inst, inst))
            occ["instances"][name] = {}
            for c in classes:
                ss = [(a, b) for i, cc, a, b in spans if i == inst and cc == c]
                if not ss:
                    occ["instances"][name][c] = [0] * len(grid)
                    continue
                a = np.sort(np.array([x[0] for x in ss]))
                b = np.sort(np.array([x[1] for x in ss]))
                occ["instances"][name][c] = (np.searchsorted(a, grid, "right") -
                                             np.searchsorted(b, grid, "right")).tolist()

    # 3. FluidServe가 요청을 붙잡아 둔 시간(waited, ms)의 클래스별 분위수. 0이면 도착 즉시 결정됐다.
    waited = None
    if p["fs"]:
        waited = {}
        for c in classes:
            w = np.array([x[3] for x in p["fs"] if class_of_tier(x[2]) == c and np.isfinite(x[3])])
            if len(w):
                qs = [0.5, 0.9, 0.99, 1.0]
                waited[c] = dict(n=int(len(w)), held_pct=float(100 * (w > 0).mean()),
                                 q={str(q): float(np.quantile(w, q)) for q in qs})

    return dict(inst_port={k: v for k, v in inst_port.items()}, decisions=dec, occupancy=occ, waited=waited,
                policy="fluidserve" if p["fs"] else "other", n_routes=len(p["routes"]), n_fs=len(p["fs"]))
