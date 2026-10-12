"""대시보드 진입점. 하는 일은 셋뿐이다: api/ 아래 모듈의 router를 등록하고, web/을 정적 파일로 내보내고,
끝난 run을 백그라운드에서 채점 대기열에 넣는다.

새 API를 추가하려면 api/에 모듈 하나를 만들고 `router = APIRouter()`를 두면 된다. 여기는 고치지 않는다.

  /home/nxclab/tools/dashboard-venv/bin/python server.py      (또는 run.sh)
"""
import importlib
import os
import pkgutil
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("PYTHONPYCACHEPREFIX", "/home/nxclab/scratch/pycache")

import uvicorn  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

import api  # noqa: E402
from config import CFG  # noqa: E402
from sources import recorder, runs, scoring  # noqa: E402

app = FastAPI(title="FluidServe experiments", docs_url="/api/docs")
for m in pkgutil.iter_modules(api.__path__):
    mod = importlib.import_module(f"api.{m.name}")
    if hasattr(mod, "router"):
        app.include_router(mod.router, prefix="/api")

app.mount("/static", StaticFiles(directory=os.path.join(HERE, "web")), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(HERE, "web", "index.html"), headers={"Cache-Control": "no-store"})


def _score_loop():
    """끝난(done) run 가운데 캐시가 없거나 낡은 것을 채점 대기열에 넣는다. 진행 중인 run은 건드리지 않는다."""
    while True:
        try:
            for r in runs.list_runs():
                if r["state"] == "done":
                    scoring.submit(r["name"], runs.run_dir(r["name"]))
        except Exception as e:  # 한 번 실패해도 다음 주기에 다시 시도한다
            print(f"[score-loop] {type(e).__name__}: {e}", file=sys.stderr)
        time.sleep(30)


@app.on_event("startup")
def _start():
    os.makedirs(os.path.join(CFG["paths"]["cache"], "runs"), exist_ok=True)
    threading.Thread(target=_score_loop, daemon=True).start()
    recorder.start()


if __name__ == "__main__":
    uvicorn.run(app, host=CFG["server"]["host"], port=CFG["server"]["port"], log_level="warning", ws="none")
