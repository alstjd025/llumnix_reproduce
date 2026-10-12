"""실험 기록(EXP-*.md)과 그 실험의 run을 잇는다.

  새 기록: Agent_applications/agent_motivation_experiment/experiments/EXP-141 이후
  과거 기록: archive/Agent_applications/agent_motivation_experiment/experiments/ (EXP-00~140, 읽기 전용)

run과 잇는 규칙(둘 중 하나라도 맞으면):
  1. 기록 안의 "세션 접두어:" 줄에 backtick으로 적은 문자열이 run 이름의 세션 칸(세 번째 칸)에 들어 있다.
     새 실험은 이 줄을 쓴다(experiments/README.md).
  2. 세션 칸이 exp<번호>로 시작하고 그 뒤가 숫자가 아니다(EXP-108 ↔ exp108r1, exp108smoke; exp1081은 아니다).
     과거 기록은 이 관례로 이름을 지었다.
"""
import os
import re

from config import path

ROOTS = [("new", os.path.join(path("exp_root"), "experiments")),
         ("archive", os.path.join(path("repo"), "archive", "Agent_applications", "agent_motivation_experiment", "experiments"))]
_NUM = re.compile(r"^EXP-(\d+)")
_PREFIX_LINE = re.compile(r"세션 접두어\s*[:：](.*)")


def _meta(p):
    try:
        text = open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    title = next((ln.lstrip("# ").strip() for ln in text.splitlines() if ln.startswith("#")), os.path.basename(p))
    prefixes = []
    for ln in text.splitlines():
        m = _PREFIX_LINE.search(ln)
        if m:
            prefixes += re.findall(r"`([^`]+)`", m.group(1))
    return dict(title=title, prefixes=prefixes, size=len(text), has_result=bool(re.search(r"^#+.*(결과|[Rr]esult)", text, re.M)))


def list_experiments():
    out = []
    for kind, root in ROOTS:
        try:
            files = sorted(f for f in os.listdir(root) if f.startswith("EXP-") and f.endswith(".md"))
        except OSError:
            continue
        for f in files:
            m = _NUM.match(f)
            meta = _meta(os.path.join(root, f)) or {}
            out.append(dict(file=f, kind=kind, num=int(m.group(1)) if m else None, mtime=os.path.getmtime(os.path.join(root, f)), **meta))
    return sorted(out, key=lambda x: (-(x["num"] or -1), x["file"]))


def read(kind, f):
    root = dict(ROOTS)[kind]
    p = os.path.join(root, os.path.basename(f))
    return open(p, encoding="utf-8", errors="replace").read()


def run_matches(exp, run):
    sess = (run.get("session") or "").lower()
    if any(p.lower() in sess for p in exp.get("prefixes") or []):
        return True
    n = exp.get("num")
    return n is not None and re.match(rf"^exp{n}(?!\d)", sess) is not None
