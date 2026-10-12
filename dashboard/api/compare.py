"""④ 비교: 고른 run들을 (arm, 워크로드, 도착률)로 묶고, 칸마다 반복 평균·범위·반복 수를 낸다. 기준 arm과 평균 차이가
두 칸의 반복 범위 중 큰 것보다 작으면 "차이 없음"으로 표시한다(작업 규칙: 반복 간 편차보다 작은 차이는 차이가 아니다).

같은 세션 안의 run끼리만 비교해야 한다(exp-run 스킬 §6). 고른 run들의 날짜가 이틀 넘게 벌어지면 경고한다."""
import fnmatch
from collections import defaultdict

from fastapi import APIRouter

from api import jsonable
from sources import runs, scoring

router = APIRouter()
METRICS = ["offered", "admitted", "rejected_pct", "goodput_tok_s", "all_arrivals"]


@router.get("/compare")
def compare(q: str, ref: str = "", exclude_listed: bool = True):
    """q: 공백으로 나눈 glob 여러 개(run 이름에 대해, 하나라도 맞으면 포함). 예: "*rpro1010r[12]_* *rpro1010pr*"."""
    pats = [p if any(ch in p for ch in "*?[") else f"*{p}*" for p in q.split()]
    rows = [r for r in runs.list_runs() if r["state"] == "done" and any(fnmatch.fnmatch(r["name"], p) for p in pats)]
    cells = defaultdict(list)
    skipped = []
    for r in rows:
        if exclude_listed and r["excluded"]:
            skipped.append(dict(name=r["name"], why="제외 목록: " + " / ".join(r["excluded"])))
            continue
        st, sc = scoring.status(r["name"], runs.run_dir(r["name"]))
        if st != "scored":
            skipped.append(dict(name=r["name"], why=f"채점 상태 {st}"))
            continue
        # 측정 시간이 다르면 같은 조건이 아니다(4분 smoke와 8분 run을 한 칸에 넣지 않는다)
        key = (r["arm"], r["arm_label"], r.get("workload") or "", r.get("rate"), r.get("duration_min"))
        cells[key].append(dict(name=r["name"], date=r.get("date"), **{m: sc.get(m) for m in METRICS}))
    out = []
    for (arm, label, wl, rate, dur), rs in cells.items():
        c = dict(arm=arm, arm_label=label if dur in (None, 8, 8.0) else f"{label} ({dur:g}분)", workload=wl, rate=rate,
                 duration_min=dur, n=len(rs), runs=[x["name"] for x in rs])
        for m in METRICS:
            v = [x[m] for x in rs if x[m] is not None]
            c[m] = dict(mean=sum(v) / len(v), min=min(v), max=max(v), vals=v) if v else None
        out.append(c)
    out.sort(key=lambda c: (c["rate"] or 0, c["arm_label"] or ""))
    # 기준 arm과의 비교: 같은 도착률의 칸끼리
    ref = ref or next((c["arm"] for c in out if c["arm_label"] == "FluidServe"), out[0]["arm"] if out else "")
    for c in out:
        base = next((b for b in out if b["arm"] == ref and b["rate"] == c["rate"]
                     and b["duration_min"] == c["duration_min"]), None)
        c["vs_ref"] = {}
        if not base or base is c:
            continue
        for m in METRICS:
            a, b = c.get(m), base.get(m)
            if not a or not b:
                continue
            spread = max(a["max"] - a["min"], b["max"] - b["min"])
            diff = a["mean"] - b["mean"]
            verdict = ("반복 부족" if min(c["n"], base["n"]) < 2 else
                       "차이 없음" if abs(diff) <= spread else "차이")
            c["vs_ref"][m] = dict(diff=diff, spread=spread, verdict=verdict,
                                  ratio=(a["mean"] / b["mean"]) if b["mean"] else None)
    dates = sorted({r.get("date") for r in rows if r.get("date")})
    warn = []
    if len(dates) > 1 and int(dates[-1]) - int(dates[0]) > 2:
        warn.append(f"run 날짜가 {dates[0]}~{dates[-1]}로 벌어져 있다. 다른 세션의 arm끼리는 비교가 아니다"
                    f"(세션 간 이동이 최대 4.6점).")
    return jsonable(dict(cells=out, ref=ref, skipped=skipped, warnings=warn, metrics=METRICS))
