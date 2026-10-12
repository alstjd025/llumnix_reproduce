"""상태 검사 등록소.

검사 하나는 함수 하나다. 이 패키지 안의 아무 모듈에나 아래처럼 쓰면 자동으로 등록되어 화면에 나온다.

    @check("engines_up", scope="cluster", title="엔진 4대가 모두 응답한다",
           why="파드의 startup probe는 8000번만 본다. 나머지가 아직 가중치를 올리는 중에 조건이 시작된 일이 있다")
    def engines_up(ctx):
        ...
        return Result("fail", "8002가 응답하지 않는다", evidence=["8002: connection refused"])

scope="cluster" 검사는 ctx로 아무것도 받지 않고(필요한 것은 sources에서 직접 읽는다),
scope="run" 검사는 ctx로 run 정보(dict: name, dir, info, score)를 받는다.

판정은 셋: ok / warn / fail. 판정할 수 없으면(데이터가 없음) "na".
why에는 **그 검사가 생긴 과거 사고**를 적는다. 화면에 경고와 같이 나오므로, 읽는 사람이 왜 이 경고가 중요한지 바로 안다.
"""
import importlib
import pkgutil
import traceback
from dataclasses import dataclass, field

REGISTRY = []


@dataclass
class Result:
    status: str                 # ok | warn | fail | na
    summary: str
    evidence: list = field(default_factory=list)


def check(id, scope, title, why):
    def deco(fn):
        REGISTRY.append(dict(id=id, scope=scope, title=title, why=why, fn=fn))
        return fn
    return deco


def _load_all():
    for m in pkgutil.iter_modules(__path__):
        importlib.import_module(f"{__name__}.{m.name}")


def evaluate(scope, ctx=None):
    out = []
    for c in REGISTRY:
        if c["scope"] != scope:
            continue
        try:
            r = c["fn"](ctx)
        except Exception as e:
            r = Result("na", f"검사 자체가 실패했다: {type(e).__name__}: {e}",
                       evidence=traceback.format_exc().splitlines()[-4:])
        if r is None:
            continue
        out.append(dict(id=c["id"], title=c["title"], why=c["why"], status=r.status,
                        summary=r.summary, evidence=[str(x) for x in r.evidence][:50]))
    return out


_load_all()
