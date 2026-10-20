"""결과 디렉토리 목록과 run 하나의 기본 정보(이름 해석, 상태, run_config.json, 제외 여부)."""
import json
import os
import re
import time

from config import CFG, RUN_NAME, arm_label, path


def results_dir():
    return path("results")


def run_dir(name):
    d = os.path.join(results_dir(), os.path.basename(name))
    if not os.path.isdir(d):
        raise FileNotFoundError(name)
    return d


def excluded():
    """ms_dev/data/excluded_runs.tsv: 첫 칸이 run 이름이나 그 일부, 둘째 칸이 제외 사유.
    같은 run이 두 줄에 있을 수 있다(그림 하나에서만 빼는 경우). 사유를 전부 모은다."""
    rows = []
    try:
        for line in open(path("excluded_runs"), encoding="utf-8"):
            if not line.strip() or line.startswith("#"):
                continue
            key, _, why = line.rstrip("\n").partition("\t")
            rows.append((key.strip(), why.strip()))
    except OSError:
        pass
    return rows


def exclusion_reasons(name, rows=None):
    rows = excluded() if rows is None else rows
    return [why for key, why in rows if key and key in name]


def parse_name(name):
    m = RUN_NAME.match(name)
    if not m:
        return dict(session=None, arm=None, arm_label=None, rpm=None, rate=None, workload=None)
    g = m.groupdict()
    rpm = re.search(r"_rpm_(\d+)", name)
    rest = g["rest"]
    return dict(
        date=g["date"], time=g["time"], session=g["session"], arm=g["arm"],
        arm_label=arm_label(g["arm"]),
        workload=re.sub(r"_?rpm_\d+$", "", rest) or None,
        rpm=int(rpm.group(1)) if rpm else None,
        rate=int(rpm.group(1)) / 60.0 if rpm else None,
    )


def latest_mtime(d):
    """run 디렉토리 안 파일들 중 가장 최근 수정 시각. agent_logs/는 파일이 만 개가 넘어서 디렉토리 자체의
    수정 시각만 본다(새 파일이 생기면 디렉토리 시각이 바뀐다)."""
    newest = os.stat(d).st_mtime
    for root in (d, os.path.join(d, "server_metrics")):
        try:
            for e in os.scandir(root):
                try:
                    newest = max(newest, e.stat().st_mtime)
                except OSError:
                    pass
        except OSError:
            pass
    return newest


def state_of(d, now=None):
    """running: 최근 settle_s 안에 파일이 바뀌었다. done: 멈췄고 metrics.csv에 데이터가 있다.
    incomplete: 멈췄는데 metrics.csv가 없거나 비어 있다(러너가 중간에 죽은 run)."""
    now = now or time.time()
    age = now - latest_mtime(d)
    if age < CFG["scoring"]["settle_s"]:
        return "running", age
    m = os.path.join(d, "metrics.csv")
    if os.path.isfile(m) and os.path.getsize(m) > 2000:
        return "done", age
    return "incomplete", age


def start_ts(d):
    """수집기(server_metrics/gateway.jsonl) 첫 줄의 t. 없으면 run_config.json의 수정 시각."""
    try:
        with open(os.path.join(d, "server_metrics", "gateway.jsonl"), encoding="utf-8") as f:
            return float(json.loads(f.readline())["t"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        return os.path.getmtime(os.path.join(d, "run_config.json"))
    except OSError:
        return None


def run_config(d):
    try:
        return json.load(open(os.path.join(d, "run_config.json")))
    except (OSError, ValueError):
        return None


def list_runs():
    out = []
    excl = excluded()
    now = time.time()
    try:
        names = sorted((e.name for e in os.scandir(results_dir()) if e.is_dir()), reverse=True)
    except OSError:
        names = []
    for n in names:
        if not RUN_NAME.match(n):  # exp07_meta처럼 run이 아닌 디렉토리
            continue
        d = os.path.join(results_dir(), n)
        st, age = state_of(d, now)
        info = parse_name(n)
        info.update(name=n, state=st, idle_s=age, excluded=exclusion_reasons(n, excl))
        # 실험 시각: 이름의 날짜·시각은 러너 파드의 시계(UTC-7)라서 쓰지 않고, 수집기가 처음 남긴 epoch 시각을 쓴다.
        # 끝 시각은 디렉토리 안 파일의 마지막 수정 시각. 화면은 브라우저의 시간대로 보여 준다.
        info["start_ts"] = start_ts(d)
        info["end_ts"] = now - age
        cfg = run_config(d)
        if cfg:
            info["duration_min"] = cfg.get("duration_min")
            info["created_at"] = cfg.get("created_at")
            info["mode"] = cfg.get("mode")
            if info.get("rpm") is None and cfg.get("request_rate_per_min"):
                info["rpm"] = cfg["request_rate_per_min"]
                info["rate"] = cfg["request_rate_per_min"] / 60.0
        out.append(info)
    return out
