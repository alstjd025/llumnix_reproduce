"""⑤ 논문 대조: claims.yaml의 항목마다 근거 run을 이 서버에서 찾아 다시 계산한 값과 논문 값을 비교한다.
정의와 상태의 뜻은 claims.yaml 머리말."""
import fnmatch
import os

import pandas as pd
import yaml
from fastapi import APIRouter

from api import jsonable
from config import HERE, path
from sources import runs, scoring

router = APIRouter()


def _metric(score, key):
    v = score
    for k in key.split("."):
        if not isinstance(v, dict):
            return None
        v = v.get(k)
    return v


def _expand():
    """claims.yaml → 항목 목록(dict: id, figure, evidence, label, paper, metric, runs(glob 또는 이름), n, tol)."""
    spec = yaml.safe_load(open(os.path.join(HERE, "claims.yaml"), encoding="utf-8"))
    items = []
    for src in spec.get("csv_sources", []):
        df = pd.read_csv(os.path.join(path("exp_root"), src["file"]))
        for _, row in df.iterrows():
            keyvals = [row[k] for k in src["key_columns"]]
            for mname, m in src["metrics"].items():
                if "run_column" in src:
                    runs_glob = row[src["run_column"]]
                    n = 1
                    cls = row.get("cls")
                    metric = m["metric_all"] if cls == "all" else m["metric_by_cls"].format(cls=cls)
                else:
                    rate = float(row["req_per_s"])
                    ov = src.get("overrides", {}).get(f"{row['arm']}|{rate}")
                    runs_glob = ov or src["runs"].get(row["arm"], "").format(rpm=int(round(rate * 60)))
                    n = int(row[src["n_column"]]) if src.get("n_column") else None
                    metric = mname
                items.append(dict(id=f"{src['id']}:" + ":".join(str(x) for x in keyvals) + f":{mname}",
                                  group=src["figure"], evidence=src["evidence"],
                                  label=" · ".join(f"{k}={v}" for k, v in zip(src["key_columns"], keyvals)) + f" · {m['label']}",
                                  paper=float(row[m["column"]]), metric=metric, runs=runs_glob, n=n, tol=m["tol"]))
    for c in spec.get("claims", []):
        items.append(dict(id=c["id"], group=c["figure"].split(" — ")[0], evidence=c["evidence"], label=c["figure"],
                          paper=c["paper"], metric=c["metric"], runs=c["runs"], n=c.get("n"), tol=c["tol"]))
    return items


@router.get("/claims")
def claims():
    names = [r["name"] for r in runs.list_runs()]
    out = []
    for it in _expand():
        matched = [n for n in names if fnmatch.fnmatch(n, it["runs"])]
        vals, pending = [], []
        for n in matched:
            st, sc = scoring.status(n, runs.run_dir(n))
            if st == "scored":
                v = _metric(sc, it["metric"])
                if v is not None:
                    vals.append((n, v))
            else:
                pending.append(n)
        rec = sum(v for _, v in vals) / len(vals) if vals else None
        if not matched:
            status = "데이터 없음"
        elif not vals:
            status = "채점 대기"
        elif abs(rec - it["paper"]) <= it["tol"]:
            status = "일치"
        else:
            status = "차이"
        if vals and it.get("n") and len(vals) < it["n"] and status != "차이":
            status = "일부 run만"
        out.append(dict(it, matched=matched, values=vals, pending=pending, recomputed=rec,
                        delta=(rec - it["paper"]) if rec is not None else None, status=status))
    return jsonable(out)
