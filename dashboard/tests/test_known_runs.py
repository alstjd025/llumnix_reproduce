"""답을 아는 run으로 대시보드의 수치를 검증한다. 대시보드가 떠 있어야 한다(./run.sh start).

기대값은 project/evidence.md §8(2026-10-10 재현 확인)에 적힌 값이다. 그 표는 같은 채점 스크립트를 명령줄에서 돌려
만든 것이므로, 대시보드가 같은 함수를 같은 설정으로 부르고 있다면 소수 첫째 자리까지 같아야 한다.
채점 코드나 sources/를 고친 뒤에는 이것을 돌린다.

  /home/nxclab/tools/dashboard-venv/bin/python tests/test_known_runs.py
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8080/api"

# run 이름 → {항목: (기대값, 허용 오차)}
EXPECT = {
    "261010_0038_rpro1010r1_fsv3capgnofrct75_t75_rpm_2100":
        dict(offered=(77.5, 0.05), admitted=(99.7, 0.05), rejected_pct=(22.3, 0.05), goodput_tok_s=(13818, 1)),
    "261010_0053_rpro1010r2_fsv3capgnofrct75_t75_rpm_2100":
        dict(offered=(76.1, 0.05), admitted=(99.9, 0.05), rejected_pct=(23.8, 0.05), goodput_tok_s=(13643, 1)),
    "261010_0218_rpro1010pr1_polyservept75_t75fair_rpm_2100":
        dict(offered=(45.3, 0.05), admitted=(50.7, 0.05), rejected_pct=(10.6, 0.05), goodput_tok_s=(9818, 1)),
    "261010_0238_rpro1010pr2_polyservept75_t75fair_rpm_2100":
        dict(offered=(42.5, 0.05), admitted=(48.0, 0.05), rejected_pct=(11.5, 0.05), goodput_tok_s=(9475, 1)),
    "261010_0253_rpro1010hr1_fsv3capgnofrct75_shift":
        dict(offered=(79.3, 0.05), admitted=(99.9, 0.05), goodput_tok_s=(11591, 1),
             chat_offered=(83.6, 0.05), deepresearch_offered=(70.1, 0.05), swe_offered=(72.9, 0.05)),
}


def get(p):
    with urllib.request.urlopen(BASE + p, timeout=120) as r:
        return json.load(r)


def main():
    fails = 0
    for name, exp in EXPECT.items():
        d = get(f"/runs/{name}")
        s = d.get("score") or {}
        if d.get("score_state") != "scored":
            print(f"SKIP {name}: {d.get('score_state')}")
            fails += 1
            continue
        for k, (want, tol) in exp.items():
            got = s.get(k)
            ok = got is not None and abs(got - want) <= tol
            fails += not ok
            print(f"{'ok  ' if ok else 'FAIL'} {name[:40]:<40} {k:<22} want {want:>9} got {got}")
        # 이 run들은 정상 run이므로 run 검사가 fail을 내면 검사 쪽이 틀린 것이다
        bad = [c["id"] for c in d.get("checks") or [] if c["status"] == "fail"]
        if bad:
            fails += 1
            print(f"FAIL {name[:40]:<40} 정상 run인데 검사가 fail: {bad}")
    # 결정 로그: 재현 run 1의 FluidServe 결정 수는 로그를 grep으로 센 값(route 13,193 / shed 3,667)과 같아야 한다
    ser = get("/runs/261010_0038_rpro1010r1_fsv3capgnofrct75_t75_rpm_2100/series")
    tot = ser["dispatch"]["decisions"]["totals"]
    route = sum(v.get("route", 0) for v in tot.values())
    shed = sum(n for v in tot.values() for k, n in v.items() if k.startswith("shed"))
    for k, got, want in (("route", route, 13193), ("shed", shed, 3667)):
        fails += got != want
        print(f"{'ok  ' if got == want else 'FAIL'} decisions {k}: want {want} got {got}")
    print("\nALL OK" if not fails else f"\n{fails} FAILED")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
