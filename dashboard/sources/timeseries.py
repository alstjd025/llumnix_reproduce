"""server_metrics/*.jsonl의 시계열. 러너의 수집기(llumnix_metrics.py)가 1초마다 한 줄씩 쓴다.

한 줄의 모양: {"t": epoch초, "ok": bool, "<메트릭>|<라벨>": 값, ...}
진행 중인 run은 파일이 계속 늘어나므로 JsonlTail이 마지막으로 읽은 위치를 기억하고 늘어난 부분만 읽는다.

누적값(counter)은 화면에 그대로 그리면 의미가 없으므로 이웃한 두 줄의 차이를 시간 차이로 나눠 초당 값으로 바꾼다.
"""
import json
import os
import threading

import numpy as np

from collections import OrderedDict

# 파일 경로 → JsonlTail. 여러 run을 열어 보면 계속 늘어나므로 최근에 쓴 MAX_TAILS개만 메모리에 둔다.
MAX_TAILS = 60
_tails = OrderedDict()
_lock = threading.Lock()


class JsonlTail:
    """파일 하나를 읽은 위치까지 기억한다. 파일이 줄었거나(새로 쓴 파일) 바뀌었으면 처음부터 다시 읽는다."""

    def __init__(self, p, keep=None):
        self.p, self.keep = p, keep
        self.off, self.ino, self.rows = 0, None, []
        self.lock = threading.Lock()

    def read(self):
        try:
            st = os.stat(self.p)
        except OSError:
            return self.rows
        if st.st_ino != self.ino or st.st_size < self.off:
            self.off, self.ino, self.rows = 0, st.st_ino, []
        if st.st_size == self.off:
            return self.rows
        with open(self.p, "rb") as f:
            f.seek(self.off)
            data = f.read()
        end = data.rfind(b"\n") + 1  # 아직 다 쓰이지 않은 마지막 줄은 다음 번에 읽는다
        for line in data[:end].splitlines():
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if self.keep:
                d = {k: v for k, v in d.items() if k in ("t", "ok") or self.keep(k)}
            self.rows.append(d)
        self.off += end
        return self.rows


def tail(p, keep=None):
    with _lock:
        t = _tails.pop(p, None) or JsonlTail(p, keep)
        _tails[p] = t
        while len(_tails) > MAX_TAILS:
            _tails.popitem(last=False)
    with t.lock:
        return list(t.rows) if t.read() is not None else []


def _metric(k):
    return k.split("|", 1)[0]


def _label(k, name):
    if "|" not in k:
        return None
    for kv in k.split("|", 1)[1].split(","):
        a, _, b = kv.partition("=")
        if a == name:
            return b
    return None


def _num(v):
    return float(v) if isinstance(v, (int, float)) else np.nan


def _rate(t, v):
    """누적값을 초당 값으로. 첫 점은 NaN. 수집기가 재시작해 값이 줄면 그 점도 NaN."""
    t, v = np.asarray(t, float), np.asarray(v, float)
    out = np.full_like(v, np.nan)
    if len(v) > 1:
        dv, dt = np.diff(v), np.diff(t)
        r = np.where((dt > 0) & (dv >= 0), dv / np.where(dt > 0, dt, 1), np.nan)
        out[1:] = r
    return out


def _ratio_of_deltas(num, den):
    num, den = np.asarray(num, float), np.asarray(den, float)
    out = np.full_like(num, np.nan)
    if len(num) > 1:
        dn, dd = np.diff(num), np.diff(den)
        out[1:] = np.where(dd > 0, dn / np.where(dd > 0, dd, 1), np.nan)
    return out


def _window_ratio(num, den, w):
    """w점 창에서 (num의 증가) / (den의 증가). 누적값 두 개의 비를 창 단위로 낸다(토큰 가중 평균)."""
    num, den = np.asarray(num, float), np.asarray(den, float)
    out = np.full_like(num, np.nan)
    for i in range(len(num)):
        j = max(0, i - w)
        dn, dd = num[i] - num[j], den[i] - den[j]
        if np.isfinite(dn) and np.isfinite(dd) and dd > 0:
            out[i] = dn / dd
    return out


def _clean(a):
    return [None if (x is None or (isinstance(x, float) and not np.isfinite(x))) else round(float(x), 4)
            for x in a]


def _smooth(a, w):
    """w초 이동평균. 1초 간격의 차분 값은 tick마다 크게 출렁여서 그대로는 읽기 어렵다."""
    a = np.asarray(a, float)
    if w <= 1 or len(a) < w:
        return a
    m = np.isfinite(a)
    s = np.convolve(np.where(m, a, 0), np.ones(w), "same")
    c = np.convolve(m.astype(float), np.ones(w), "same")
    return np.where(c > 0, s / np.where(c > 0, c, 1), np.nan)


ENGINE_GAUGES = ["vllm:kv_cache_usage_perc", "vllm:num_requests_running", "vllm:num_requests_waiting"]
ENGINE_COUNTERS = ["vllm:num_preemptions_total", "vllm:generation_tokens_total"]


def engine_series(sm_dir, port, t0, smooth=5):
    rows = tail(os.path.join(sm_dir, f"engine_{port}.jsonl"))
    rows = [r for r in rows if r.get("ok")]
    if not rows:
        return None
    t = np.array([r["t"] for r in rows], float)

    def col(name):
        k = next((k for k in rows[-1] if _metric(k) == name), None)
        return np.array([_num(r.get(k)) for r in rows], float) if k else np.full(len(rows), np.nan)

    out = {"t": _clean(t - t0)}
    out["kv_pct"] = _clean(100 * col("vllm:kv_cache_usage_perc"))
    out["running"] = _clean(col("vllm:num_requests_running"))
    out["waiting"] = _clean(col("vllm:num_requests_waiting"))
    out["preempt_per_s"] = _clean(_smooth(_rate(t, col("vllm:num_preemptions_total")), smooth))
    out["gen_tok_per_s"] = _clean(_smooth(_rate(t, col("vllm:generation_tokens_total")), smooth))
    out["prefix_hit_pct"] = _clean(100 * _smooth(_ratio_of_deltas(col("vllm:prefix_cache_hits_total"),
                                                                  col("vllm:prefix_cache_queries_total")), 30))
    # 엔진이 보고한 토큰 사이 시간(ms). 30초 창의 (합의 증가 / 개수의 증가)라 그 창에서 나온 모든 토큰의 평균이다.
    # 비율의 평균이 아니라 합의 비율로 낸다: 30초 창의 (합의 증가) / (개수의 증가). 1초마다 비율을 먼저 구해 평균하면
    # preemption 등으로 토큰이 거의 나오지 않은 초의 큰 값이 같은 무게로 들어가 deepresearch 인스턴스에서 144~171 ms로
    # 부풀었다(토큰 가중 값은 72~74 ms, 2026-10-16 확인).
    out["itl_ms"] = _clean(1000 * _window_ratio(col("vllm:inter_token_latency_seconds_sum"),
                                                col("vllm:inter_token_latency_seconds_count"), 30))
    # 마지막 값과 누적 합계: 표에 쓴다
    pre = col("vllm:num_preemptions_total")
    gen = col("vllm:generation_tokens_total")
    out["totals"] = dict(
        preemptions=float(np.nanmax(pre) - np.nanmin(pre)) if np.isfinite(pre).any() else None,
        gen_tokens=float(np.nanmax(gen) - np.nanmin(gen)) if np.isfinite(gen).any() else None,
        itl_ms_mean=_run_mean_ratio(col("vllm:inter_token_latency_seconds_sum"),
                                    col("vllm:inter_token_latency_seconds_count"), 1000),
        last_t=float(t[-1]),
        advancing=bool(np.isfinite(gen).any() and np.nanmax(gen) > np.nanmin(gen)),
    )
    return out


def _run_mean_ratio(s, c, scale=1.0):
    s, c = s[np.isfinite(s)], c[np.isfinite(c)]
    if len(s) < 2 or len(c) < 2 or c[-1] - c[0] <= 0:
        return None
    return float(scale * (s[-1] - s[0]) / (c[-1] - c[0]))


def gateway_series(sm_dir, t0):
    rows = [r for r in tail(os.path.join(sm_dir, "gateway.jsonl")) if r.get("ok")]
    if not rows:
        return None
    t = np.array([r["t"] for r in rows], float)
    return {"t": _clean(t - t0),
            "current": _clean([_num(r.get("gateway_current_requests")) for r in rows]),
            "pending": _clean([_num(r.get("gateway_pending_requests")) for r in rows])}


SCHED_KEEP = ("instance_cms_decode_batch_size", "instance_cms_running_requests", "instance_cms_waiting_requests")


def scheduler_series(sm_dir, t0, inst_to_port):
    """스케줄러가 본 인스턴스별 decode batch 크기·running·waiting. 인스턴스 번호를 엔진 포트로 바꿔 엔진 그래프와 맞춘다."""
    rows = [r for r in tail(os.path.join(sm_dir, "scheduler.jsonl"),
                            keep=lambda k: _metric(k) in SCHED_KEEP) if r.get("ok")]
    if not rows:
        return None
    t = np.array([r["t"] for r in rows], float)
    out = {"t": _clean(t - t0), "instances": {}}
    keys = set()
    for r in rows[-5:]:
        keys.update(k for k in r if "|" in k)
    for k in sorted(keys):
        inst = _label(k, "instance_id")
        # 수집기는 cold restart 전부터 돌기 때문에 재시작 전 인스턴스 번호가 섞여 있다. 이 run의 routing에
        # 쓰인 인스턴스(포트를 아는 것)만 남긴다.
        if inst_to_port and inst not in inst_to_port:
            continue
        name = inst_to_port.get(inst, inst)
        out["instances"].setdefault(str(name), {})[_metric(k).replace("instance_cms_", "")] = \
            _clean([_num(r.get(k)) for r in rows])
    return out


def run_t0(sm_dir):
    """모든 그래프의 시간 0. 수집기가 처음 쓴 줄의 시각이다(게이트웨이 파일 → 엔진 파일 순으로 찾는다)."""
    for f in ("gateway.jsonl", "engine_8000.jsonl", "scheduler.jsonl"):
        p = os.path.join(sm_dir, f)
        try:
            with open(p) as fh:
                return float(json.loads(fh.readline())["t"])
        except (OSError, ValueError, KeyError):
            continue
    return None
