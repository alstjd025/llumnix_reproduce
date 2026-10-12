"""진행 중인 run의 중간 결과.

run이 도는 동안 러너는 load worker마다 shard 파일을 쓰고(results/<run>/shards/), run이 끝날 때 하나로 합친다.
  metrics.p<k>.csv          요청이 끝날 때마다 한 행 (거절·오류 포함)
  tbt.p<k>.jsonl            스트리밍된 요청마다 한 줄 (토큰 chunk별 도착 시각)
  request_ids.p<k>.jsonl    요청 번호 ↔ task_id
이 모듈은 이 파일들을 늘어난 부분만 이어 읽어, 끝난 요청을 도착 시각 30초 단위로 클래스별 결과(성공/SLO 위반/거절/오류)로 센다.

판정은 채점과 같은 함수를 쓴다(deadline_ladder_attainment.ladder_ok와 BUDGETS). 그래도 이 값은 **최종 점수가 아니다**:
아직 끝나지 않은 요청이 빠져 있고(긴 요청일수록 늦게 들어온다), warm-up 60 s와 drain 20 s도 빼지 않았다.
"""
import csv
import glob
import io
import json
import os
import sys
import threading
from collections import OrderedDict

from config import CFG, path

for k, v in CFG["scoring"]["env"].items():
    os.environ.setdefault(k, v)
sys.path.insert(0, path("analysis"))
from deadline_ladder_attainment import BUDGETS, ladder_ok  # noqa: E402
from plot_per_engine_attainment import class_of  # noqa: E402

FRAC = CFG["scoring"]["frac"]
_cache = OrderedDict()
_lock = threading.Lock()


def _truthy(v):
    return str(v).strip().lower() in ("true", "1", "yes")


class _Interim:
    def __init__(self):
        self.off = {}
        self.header = {}
        self.tbt = {}      # (task_id, call_index, iteration) → (class, start_time, ok)
        self.rows = {}     # 같은 키 → (class, start_time, rejected, errored)
        self.lock = threading.Lock()

    def _new(self, p):
        try:
            size = os.path.getsize(p)
        except OSError:
            return b""
        off = self.off.get(p, 0)
        if size <= off:
            return b""
        with open(p, "rb") as f:
            f.seek(off)
            data = f.read()
        end = data.rfind(b"\n") + 1
        self.off[p] = off + end
        return data[:end]

    def update(self, run_dir):
        sh = os.path.join(run_dir, "shards")
        for p in sorted(glob.glob(os.path.join(sh, "tbt.p*.jsonl"))):
            for line in self._new(p).splitlines():
                if b'"agent": "request"' not in line:
                    continue
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                c = class_of(r.get("task_id"))
                if c not in BUDGETS:
                    continue
                ok, _, _ = ladder_ok(r.get("chunk_events") or [], *BUDGETS[c], FRAC)
                self.tbt[(r.get("task_id"), r.get("call_index"), r.get("iteration"))] = (c, r.get("start_time"), ok)
        for p in sorted(glob.glob(os.path.join(sh, "metrics.p*.csv"))):
            data = self._new(p)
            if not data:
                continue
            text = data.decode(errors="replace")
            if p not in self.header:
                first, _, text = text.partition("\n")
                self.header[p] = next(csv.reader([first]))
            for row in csv.DictReader(io.StringIO(text), fieldnames=self.header[p]):
                if row.get("agent") == "job_summary":
                    continue
                try:
                    st = float(row.get("start_time") or "nan")
                    key = (row.get("task_id"), int(float(row.get("call_index") or 0)), int(float(row.get("iteration") or 0)))
                except ValueError:
                    continue
                rej = _truthy(row.get("is_rejected"))
                err = (_truthy(row.get("is_error")) or _truthy(row.get("is_timeout"))) and not rej
                self.rows[key] = (class_of(row.get("task_id")), st, rej, err)

    def summary(self, t0, bin_s=30):
        """도착 시각 bin_s초 단위, 클래스별 결과 수. 성공·위반은 tbt 기록에서, 거절·오류는 metrics 행에서 센다."""
        classes = list(CFG["classes"])
        pts = []
        for k, (c, st, rej, err) in self.rows.items():
            if rej:
                pts.append((c, st, "rejected"))
            elif err:
                pts.append((c, st, "errored"))
            elif k in self.tbt:
                pts.append((c, st, "met" if self.tbt[k][2] else "missed"))
            else:
                pts.append((c, st, "missed"))  # 성공으로 끝났다고 기록됐는데 토큰 기록이 없다 = 관측된 출력이 없다(채점과 같다)
        pts = [x for x in pts if x[1] == x[1] and x[1] is not None]
        if not pts or t0 is None:
            return None
        nb = int((max(x[1] for x in pts) - t0) // bin_s) + 1
        kinds = ["met", "missed", "rejected", "errored"]
        out = {c: {k: [0] * nb for k in kinds} for c in classes}
        for c, st, k in pts:
            b = int((st - t0) // bin_s)
            if c in out and 0 <= b < nb:
                out[c][k][b] += 1
        totals = {c: {k: sum(v) for k, v in kd.items()} for c, kd in out.items()}
        for c, t in totals.items():
            n = sum(t.values())
            t["n"] = n
            t["offered_pct"] = 100.0 * t["met"] / n if n else None
            served = t["met"] + t["missed"]
            t["admitted_pct"] = 100.0 * t["met"] / served if served else None
        return dict(bin_s=bin_s, t=[i * bin_s for i in range(nb)], outcome=out, totals=totals, n=len(pts))


def interim(run_dir, t0):
    with _lock:
        st = _cache.pop(run_dir, None) or _Interim()
        _cache[run_dir] = st
        while len(_cache) > 3:
            _cache.popitem(last=False)
    with st.lock:
        st.update(run_dir)
        return st.summary(t0)
