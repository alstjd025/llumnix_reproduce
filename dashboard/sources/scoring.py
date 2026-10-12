"""끝난 run을 논문 주 규칙 ladder95로 채점하고 결과를 캐시에 둔다.

채점은 새로 구현하지 않는다. analysis_scripts/request_level의 함수를 그대로 부른다.
  deadline_ladder_attainment.score_run  ladder95 점수와 요청별 판정(dump_dir)
  exp22_fluidserve.load_run             요청별 TTFT·토큰 사이 시간(itl_ms)·e2e (분포 그래프용)
  run_health.engines_ok, rate_of        상태 검사가 쓰는 엔진 수와 목표 도착률
그래서 대시보드의 수치는 논문 표와 같은 코드에서 나온다.

채점은 별도 프로세스(spawn)에서 한다. 이유 둘: exp22_fluidserve가 import 시점에 환경변수(FS_SWE_*)를 읽으므로
웹 서버 프로세스의 환경과 분리해야 하고, tbt_events.jsonl(run 하나에 약 800 MB)을 읽는 동안 웹 서버가 멈추지 않아야 한다.

캐시 키는 metrics.csv·tbt_events.jsonl의 크기·수정 시각과 채점 스크립트들의 md5다. 채점 스크립트를 고치면
모든 run이 자동으로 다시 채점된다.
"""
import hashlib
import json
import multiprocessing
import os
import threading
import time
import traceback
from concurrent.futures import ProcessPoolExecutor

from config import CFG, path

SCORER_FILES = ["deadline_ladder_attainment.py", "exp22_fluidserve.py", "plot_per_engine_attainment.py",
                "run_health.py"]
_pool = None
_pending = {}
_lock = threading.Lock()


def cache_dir(name):
    return os.path.join(path("cache"), "runs", name)


def _scorer_version():
    h = hashlib.md5()
    for f in SCORER_FILES:
        try:
            h.update(open(os.path.join(path("analysis"), f), "rb").read())
        except OSError:
            pass
    h.update(open(__file__, "rb").read())   # 이 파일(요청 표·클래스별 값을 만드는 코드)이 바뀌어도 다시 채점한다
    h.update(json.dumps(CFG["scoring"], sort_keys=True).encode())
    return h.hexdigest()[:12]


def _input_key(run_dir):
    k = {"scorer": _scorer_version()}
    for f in ("metrics.csv", "tbt_events.jsonl"):
        p = os.path.join(run_dir, f)
        try:
            st = os.stat(p)
            k[f] = [st.st_size, int(st.st_mtime)]
        except OSError:
            k[f] = None
    return k


def cached_score(name, run_dir):
    """캐시가 지금 입력과 맞으면 그 점수, 아니면 None."""
    p = os.path.join(cache_dir(name), "score.json")
    try:
        d = json.load(open(p))
    except (OSError, ValueError):
        return None
    return d if d.get("key") == _input_key(run_dir) else None


def status(name, run_dir):
    s = cached_score(name, run_dir)
    if s:
        return "error" if s.get("error") else "scored", s
    with _lock:
        if name in _pending and not _pending[name].done():
            return "scoring", None
    return "unscored", None


def _init_worker(env, analysis):
    import sys
    os.environ.update(env)
    os.environ.setdefault("PYTHONPYCACHEPREFIX", "/home/nxclab/scratch/pycache")
    sys.dont_write_bytecode = True  # 러너가 root로 만든 __pycache__에 쓰려다 실패하지 않도록
    sys.path.insert(0, analysis)


def _work(run_dir, out_dir, frac, key):
    """worker 프로세스 안에서 돈다."""
    import numpy as np
    import pandas as pd
    t_start = time.time()
    os.makedirs(out_dir, exist_ok=True)
    try:
        from deadline_ladder_attainment import score_run
        from exp22_fluidserve import load_run
        from run_health import engines_ok, rate_of
        name = os.path.basename(run_dir)
        res = score_run(run_dir, frac, dump_dir=out_dir)  # out_dir/<name>.csv에 요청별 판정
        if res is None:
            raise RuntimeError("score_run returned None (tbt_events.jsonl 또는 metrics.csv가 없거나 비었다)")
        verd = pd.read_csv(os.path.join(out_dir, name + ".csv"))
        lr = load_run(run_dir)
        cols = ["task_id", "call_index", "iteration", "first_token_latency", "itl_ms", "latency", "output_tokens", "error_msg"]
        req = verd.merge(lr[[c for c in cols if c in lr.columns]], on=["task_id", "call_index", "iteration"],
                         how="left") if lr is not None else verd
        req.to_csv(os.path.join(out_dir, "requests.csv.gz"), index=False)
        os.remove(os.path.join(out_dir, name + ".csv"))

        # 클래스별 세 분모. 정의는 score_run과 같다: 미완료(cutoff)는 둘 다에서 빠지고,
        # offered는 거절·오류를 위반으로 세고, admitted는 거절을 분모에서 뺀다.
        live = req[~req["cutoff"]]
        # 모든 도착 기준(미완료 포함): 그림 13의 "도착 대비 %"와 같은 분모. 성공 = ladder95 통과이고 거절·오류·미완료가 아닌 것
        met_all = req["ladder_ok"] & ~req["rejected"] & ~req["errored"] & ~req["cutoff"]
        per_class = {}
        for c, sub in live.groupby("class"):
            served = sub[~sub["rejected"]]
            miss = ~sub["ladder_ok"]
            per_class[c] = dict(
                n=int(len(sub)),
                offered=float(100 * (~(miss | sub["rejected"] | sub["errored"])).mean()),
                admitted=float(100 * served["ladder_ok"].mean()) if len(served) else None,
                rejected_pct=float(100 * sub["rejected"].mean()),
                all_arrivals=float(100 * met_all[req["class"] == c].mean()),
                n_all=int((req["class"] == c).sum()),
            )
        span = float(req["rel"].max() - req["rel"].min()) if len(req) else 0.0
        nf, nlive = engines_ok(run_dir)
        out = {k: (None if isinstance(v, float) and not np.isfinite(v) else
                   (float(v) if isinstance(v, (np.floating, np.integer)) else v)) for k, v in res.items()}
        out.update(per_class=per_class, all_arrivals=float(100 * met_all.mean()), n_cutoff=int(req["cutoff"].sum()), window_s=span,
                   achieved_rate=(len(req) / span if span > 0 else None), target_rate=rate_of(run_dir),
                   engine_files=nf, engines_advancing=nlive, rule=CFG["scoring"]["rule"],
                   # 요청 표의 rel은 첫 요청 시작 시각 기준이다. 서버 메트릭 그래프(수집기 시작 기준)와 맞추려면 이 값이 필요하다
                   t0_requests=float(lr["t0"].iloc[0]) if lr is not None and len(lr) else None)
    except Exception as e:
        out = {"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
    out["key"] = key
    out["scored_at"] = time.time()
    out["score_seconds"] = round(time.time() - t_start, 1)
    tmp = os.path.join(out_dir, "score.json.tmp")
    json.dump(out, open(tmp, "w"), indent=1)
    os.replace(tmp, os.path.join(out_dir, "score.json"))
    return out


def pool():
    global _pool
    if _pool is None:
        _pool = ProcessPoolExecutor(max_workers=CFG["scoring"]["workers"],
                                    mp_context=multiprocessing.get_context("spawn"),
                                    initializer=_init_worker,
                                    initargs=(CFG["scoring"]["env"], path("analysis")))
    return _pool


def submit(name, run_dir):
    """채점을 예약한다. 이미 예약됐거나 캐시가 맞으면 아무것도 하지 않는다."""
    with _lock:
        if name in _pending and not _pending[name].done():
            return
        if cached_score(name, run_dir):
            return
        _pending[name] = pool().submit(_work, run_dir, cache_dir(name), CFG["scoring"]["frac"],
                                       _input_key(run_dir))


def requests_table(name):
    import pandas as pd
    p = os.path.join(cache_dir(name), "requests.csv.gz")
    return pd.read_csv(p) if os.path.exists(p) else None
