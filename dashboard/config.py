"""config.yaml을 읽어 CFG 하나로 내놓는다. 다른 모듈은 값을 직접 적지 않고 여기서 읽는다."""
import os
import re

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
CFG = yaml.safe_load(open(os.path.join(HERE, "config.yaml"), encoding="utf-8"))

# YAML이 앞자리가 숫자인 md5 키(예: 063e1c85)를 정수나 실수로 읽지 않도록 문자열로 맞춘다.
CFG["known_binaries"] = {str(k): v for k, v in CFG.get("known_binaries", {}).items()}


def path(name):
    return CFG["paths"][name]


def arm_label(arm):
    return CFG["arms"].get(arm, arm)


def class_of_tier(tier):
    for c, v in CFG["classes"].items():
        if int(v["tier"]) == int(tier):
            return c
    return None


def binary_name(md5):
    if not md5:
        return None
    return CFG["known_binaries"].get(md5[:8])


# run 디렉토리 이름: 261010_0038_rpro1010r1_fsv3capgnofrct75_t75_rpm_2100
#                    날짜(YYMMDD, UTC-7) 시각 세션접두어 arm 나머지(워크로드 이름, 도착률)
RUN_NAME = re.compile(r"^(?P<date>\d{6})_(?P<time>\d{4})_(?P<session>[^_]+)_(?P<arm>[^_]+)_?(?P<rest>.*)$")
