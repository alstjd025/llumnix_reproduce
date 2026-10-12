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
import glob
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


class _State:
    """run 하나의 로그·request_ids를 어디까지 읽었는지. 진행 중인 run은 늘어난 부분만 읽는다."""

    def __init__(self):
        self.off = {}          # 파일 경로 → 읽은 바이트 수
        self.inst_port, self.routes, self.fs, self.req = {}, [], [], {}
        self.lock = threading.Lock()


def _new_lines(st, p):
    try:
        size = os.path.getsize(p)
    except OSError:
        return []
    off = st.off.get(p, 0)
    if size < off:      # 파일이 새로 쓰였다(병합 등) — 처음부터
        off = 0
    if size == off:
        return []
    with open(p, "rb") as f:
        f.seek(off)
        data = f.read()
    end = data.rfind(b"\n") + 1   # 다 쓰이지 않은 마지막 줄은 다음 번에
    st.off[p] = off + end
    return data[:end].decode(errors="replace").splitlines()


def parse(run_dir):
    """run 하나의 결정 로그와 request_ids를 읽는다. 끝난 run은 request_ids.jsonl을, 진행 중인 run은
    shards/request_ids.p*.jsonl을 읽는다(러너가 run이 끝날 때 shard를 합친다)."""
    log = os.path.join(run_dir, "server_metrics", "scheduler_dispatch.log")
    if not os.path.exists(log):
        return None
    with _lock:
        st = _cache.pop(run_dir, None) or _State()
        _cache[run_dir] = st
        while len(_cache) > 8:
            _cache.popitem(last=False)
    with st.lock:
        year = _year(os.path.basename(run_dir))
        for line in _new_lines(st, log):
            if "utils.go" in line and "dispatch request" in line:
                m = _UTILS.search(line)
                if m:
                    st.routes.append((_ts(line, year), m.group(1), m.group(2)))
            elif "fsplacement" in line:
                m = _FS.search(line)
                if m:
                    try:
                        waited = float(m.group(3))
                    except ValueError:
                        waited = np.nan
                    st.fs.append((_ts(line, year), m.group(1), int(m.group(2)), waited, m.group(4), m.group(5),
                                  m.group(6)))
            elif "api_server_port" in line:
                m = _CMS.search(line)
                if m:
                    st.inst_port[m.group(1)] = int(m.group(2))
        rid = os.path.join(run_dir, "request_ids.jsonl")
        sources = [rid] if os.path.exists(rid) else sorted(glob.glob(os.path.join(run_dir, "shards", "request_ids.p*.jsonl")))
        for p in sources:
            for line in _new_lines(st, p):
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                st.req[d["request_id"].removeprefix("cmpl-")] = (d.get("task_id"), d.get("start_time"), d.get("end_time"))
        return dict(inst_port=dict(st.inst_port), routes=list(st.routes), fs=list(st.fs), req=dict(st.req))


def summarize(run_dir, t0, bin_s=10, occ_step_s=5, live_now=None):
    """화면용 요약: 10초 단위 결정 수, 인스턴스별 클래스 구성, FluidServe의 대기 시간 분포.

    live_now(epoch초)를 주면 진행 중인 run으로 본다. 그때는 routing됐는데 request_ids에 아직 없는 요청(러너는 요청이
    끝날 때 기록한다)을 지금도 인스턴스에서 처리 중인 것으로 센다. 클래스는 FluidServe 결정 줄의 tier에서 가져오고,
    결정 줄이 없는 정책이면 "?"로 둔다. 끝난 run에서는 그런 요청을 뺀다(run 경계에서 잘린 요청이다)."""
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
    tier_of = {x[1]: x[2] for x in p["fs"]} if live_now else {}
    for ts, r, inst in p["routes"]:
        if ts is None:
            continue
        v = req.get(r)
        if v and v[2] is not None:
            spans.append((inst, class_of(v[0]), ts - t0, float(v[2]) - t0))
        elif live_now:
            spans.append((inst, class_of_tier(tier_of[r]) if r in tier_of else "?", ts - t0,
                          live_now - t0 + occ_step_s))  # 끝 시각을 한 칸 뒤로: 마지막 격자점에서도 처리 중으로 센다
    if spans and t0:
        tmax = max(s[3] for s in spans)
        if live_now:
            tmax = min(tmax, live_now - t0)
        grid = np.arange(0, tmax + occ_step_s, occ_step_s)
        occ = {"t": grid.tolist(), "instances": {}}
        for inst in sorted({s[0] for s in spans}, key=lambda i: inst_port.get(i, 0)):
            name = str(inst_port.get(inst, inst))
            occ["instances"][name] = {}
            for c in classes + (["?"] if any(x[1] == "?" for x in spans) else []):
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
