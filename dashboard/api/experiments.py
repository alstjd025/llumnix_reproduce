"""⑥ 실험 기록: EXP-*.md 목록과 본문, 그 실험의 run과 점수."""
from fastapi import APIRouter, HTTPException

from api import jsonable
from sources import experiments, runs, scoring

router = APIRouter()


@router.get("/experiments")
def list_exps():
    rs = runs.list_runs()
    out = []
    for e in experiments.list_experiments():
        e = dict(e)
        e["n_runs"] = sum(experiments.run_matches(e, r) for r in rs)
        out.append(e)
    return jsonable(out)


@router.get("/experiments/{kind}/{f}")
def one(kind: str, f: str):
    exps = {(e["kind"], e["file"]): e for e in experiments.list_experiments()}
    e = exps.get((kind, f))
    if not e:
        raise HTTPException(404, f)
    rows = []
    for r in runs.list_runs():
        if not experiments.run_matches(e, r):
            continue
        row = dict(name=r["name"], arm_label=r["arm_label"], rate=r.get("rate"), state=r["state"], excluded=r["excluded"])
        if r["state"] == "done":
            st, sc = scoring.status(r["name"], runs.run_dir(r["name"]))
            row["score_state"] = st
            if sc and not sc.get("error"):
                row.update({k: sc.get(k) for k in ("offered", "admitted", "rejected_pct", "goodput_tok_s", "n")})
        rows.append(row)
    return jsonable(dict(meta=e, text=experiments.read(kind, f), runs=rows))
