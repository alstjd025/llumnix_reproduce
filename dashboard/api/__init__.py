"""HTTP API. 모듈 하나가 화면 하나(또는 데이터 묶음 하나)를 맡는다. 각 모듈의 `router`를 server.py가 /api 아래에 등록한다.

numpy 값은 JSON으로 바로 나가지 않으므로 응답 전에 jsonable()을 거친다.
"""
import math

import numpy as np


def jsonable(x):
    if isinstance(x, dict):
        return {str(k): jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [jsonable(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (np.floating, float)):
        f = float(x)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(x, np.bool_):
        return bool(x)
    if isinstance(x, np.ndarray):
        return jsonable(x.tolist())
    return x
