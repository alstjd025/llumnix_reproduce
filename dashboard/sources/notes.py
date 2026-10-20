"""사용자 메모(2026-10-20 사용자 요청: run·실험마다 메모를 남기고 파일로 보존한다).

메모 하나 = Markdown 파일 하나. <notes>/runs/<run 이름>.md, <notes>/exp/<실험 기록 파일 이름>.md.
config.yaml의 paths.notes가 바깥 저장소의 project/notes라서 git으로 이력이 남는다(커밋·push는 사용자가 한다).

대시보드가 파일을 쓰는 곳은 캐시와 여기뿐이다. 지우지 않는다: 메모를 비워도 빈 파일로 남는다(CLAUDE.md 작업 규칙).
덮어쓰기 충돌: 화면이 메모를 불러온 시점의 수정 시각(base_mtime)을 같이 보내고, 그 뒤 파일이 바뀌었으면 쓰지 않는다
(탭 두 개에서 같은 메모를 고칠 때 앞의 저장을 잃지 않게 한다).
"""
import os
import re
import time

from config import path

KINDS = {"run": "runs", "exp": "exp"}
KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,200}$")   # 경로 문자(/ ..)가 들어갈 수 없다


class Conflict(Exception):
    pass


def _file(kind, key):
    if kind not in KINDS:
        raise ValueError(f"kind {kind!r}")
    key = re.sub(r"\.md$", "", key)
    if not KEY.match(key) or ".." in key:
        raise ValueError(f"key {key!r}")
    return os.path.join(path("notes"), KINDS[kind], key + ".md")


def read(kind, key):
    p = _file(kind, key)
    try:
        st = os.stat(p)
        return dict(text=open(p, encoding="utf-8").read(), mtime=st.st_mtime, exists=True, path=p)
    except FileNotFoundError:
        return dict(text="", mtime=None, exists=False, path=p)


def write(kind, key, text, base_mtime):
    p = _file(kind, key)
    cur = read(kind, key)
    if cur["exists"] and (base_mtime is None or abs(cur["mtime"] - float(base_mtime)) > 1e-6):
        raise Conflict(cur)
    if not cur["exists"] and not text.strip():
        return cur                                   # 없는 메모를 빈 채로 만들지 않는다
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + f".tmp{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") or not text else text + "\n")
    os.replace(tmp, p)
    return read(kind, key)


def listing():
    """{kind: {key: (mtime, 첫 줄)}} — 목록 화면에 메모가 있는지 표시하는 데 쓴다. 빈 메모는 뺀다."""
    out = {}
    for kind, sub in KINDS.items():
        d = os.path.join(path("notes"), sub)
        m = {}
        if os.path.isdir(d):
            for f in os.listdir(d):
                if not f.endswith(".md"):
                    continue
                p = os.path.join(d, f)
                try:
                    t = open(p, encoding="utf-8").read().strip()
                except OSError:
                    continue
                if t:
                    m[f[:-3]] = dict(mtime=os.path.getmtime(p), first=t.splitlines()[0][:120])
        out[kind] = m
    return out
