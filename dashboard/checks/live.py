"""진행 중인 run의 검사. 실시간 페이지가 5초마다 돌린다. ctx는 dict:
  run(runs.list_runs의 한 행), cfg(run_config.json), t0, now, engines(엔진별 시계열), dispatch(dispatch.parse 결과),
  chains(체인 목록), runner_pods
exp-run 스킬 §4c: 감시가 성공만 보면 멈춘 것을 잡지 못한다. 그래서 여기 검사는 모두 "지금도 움직이는가"를 묻는다.
"""
import numpy as np

from checks import Result, check
from config import CFG


def _phase(ctx):
    """수집 시작 후 경과 시간으로 본 구간. 수집기는 엔진 재시작 직후 돌기 시작하지만 러너는 처음 warmup_sec 동안
    warmup_rpm(기본 60 rpm = 1 req/s)만 보낸다. 측정이 끝난 뒤에는 남은 요청이 빠져나가는 drain 구간이다.
    이 두 구간에서 목표 도착률이나 토큰 생성을 요구하면 정상인 run이 fail로 보인다(2026-10-12 smoke에서 확인)."""
    cfg, t0 = ctx.get("cfg") or {}, ctx.get("t0")
    if not t0:
        return "unknown", None
    el = ctx["now"] - t0
    warm = float(cfg.get("warmup_sec") or 0)
    dur = cfg.get("duration_min")
    if el < warm + 75:
        return "warmup", el
    if dur and el > warm + dur * 60 + 10:
        return "drain", el
    return "measure", el


def _last(xs, n):
    v = [x for x in (xs or [])[-n:] if x is not None]
    return v


@check("live_chain", "live", "체인 스크립트가 살아 있다",
       "EXP-63에서 체인이 두 번 죽었고 각각 4.5시간, 2.6시간 뒤에 발견됐다. 성공한 조건 수를 세는 검사는 멈춘 것을 잡지 못한다")
def live_chain(ctx):
    ch = ctx.get("chains") or []
    if not ch:
        return Result("na", "최근 24시간 안에 바뀐 체인 로그가 없다")
    top = ch[0]
    ev = [f"{c['name']}: {c['state']} (pid {', '.join(str(p['pid']) for p in c['pids']) or '없음'})" for c in ch[:5]]
    if top["state"] == "died":
        return Result("fail", f"{top['name']}: 프로세스가 없는데 체인 종료 표시도 ABORT 줄도 없다 — 중간에 죽었다", ev + top["tail"][-8:])
    if top["state"] == "aborted":
        return Result("fail", f"{top['name']}: 스크립트가 자기 검사에 걸려 스스로 멈췄다(ABORT/FAILED) — 원인을 봐야 한다", ev + top["tail"][-8:])
    if top["marks"].get("abort") or top["marks"].get("failed"):
        bad = (top["marks"].get("abort") or []) + (top["marks"].get("failed") or [])
        return Result("warn", f"{top['name']}: 로그에 ABORT/FAILED 줄이 있다", ev + bad)
    return Result("ok", f"{top['name']}: {top['state']}", ev)


@check("live_runner", "live", "러너 파드가 돌고 있다",
       "ConfigMap이 없어 러너 파드가 CreateContainerConfigError로 59분 동안 멈춰 있었다(2026-10-10)")
def live_runner(ctx):
    pods = ctx.get("runner_pods") or []
    act = [p for p in pods if p["phase"] in ("Pending", "Running")]
    ev = [f"{p['name']}: {p['phase']} {p.get('problem') or ''}" for p in pods[:5]]
    if any(p.get("problem") for p in act):
        return Result("fail", "러너 파드에 문제가 있다", ev)
    if len(act) > 1:
        return Result("fail", f"러너가 {len(act)}개 동시에 돈다", ev)
    return Result("ok" if act else "na", "1개 실행 중" if act else "실행 중인 러너 없음", ev)


@check("live_engines", "live", "엔진 4대가 지금도 토큰을 만든다",
       "엔진 하나가 멈추거나 처음부터 뜨지 않은 채로 조건이 진행되면 바깥에서는 정상처럼 보인다(exp-run 스킬 §4)")
def live_engines(ctx):
    eng = ctx.get("engines") or {}
    if not eng:
        return Result("na", "엔진 메트릭이 아직 없다")
    ph, el = _phase(ctx)
    if ph == "warmup" and el < 60:
        return Result("na", f"시작 직후({el:.0f}초) — 부하가 아직 거의 없다")
    if ph == "drain":
        return Result("na", "측정이 끝났다 — 엔진이 쉬는 것이 정상이다")
    ev, dead = [], []
    for port, s in eng.items():
        g = _last(s.get("gen_tok_per_s"), 30)
        lag = ctx["now"] - (ctx["t0"] + (s["t"][-1] or 0)) if s.get("t") else None
        mean = float(np.mean(g)) if g else 0.0
        ev.append(f"{port}: 최근 30초 평균 {mean:,.0f} tok/s, 마지막 기록 {lag:.0f}초 전" if lag is not None else f"{port}: 기록 없음")
        if mean <= 0 or (lag is not None and lag > 30):
            dead.append(port)
    return Result("fail" if dead else "ok", f"멈춘 엔진: {', '.join(dead)}" if dead else f"{len(eng)}대 모두 생성 중", ev)


@check("live_arrivals", "live", "스케줄러에 도착하는 요청 수가 목표 도착률에 맞다",
       "부하 생성기가 목표 도착률을 내지 못하면 그 조건은 이름과 다른 부하를 잰다. 반대로 목표보다 훨씬 많으면 클라이언트가 "
       "재시도를 반복하는 것이다(EXP-54)")
def live_arrivals(ctx):
    p, tgt = ctx.get("dispatch"), ctx["run"].get("rate")
    if not p:
        return Result("na", "결정 로그가 아직 없다")
    # 도착 = 스케줄러가 처음 본 요청. routing 줄과 FluidServe의 shed 줄(거절된 요청은 routing 줄이 없다)
    seen = {}
    for ts, r, *_ in p["routes"]:
        seen.setdefault(r, ts)
    for ts, r, tier, waited, dec, *_ in p["fs"]:
        if dec == "shed":
            seen.setdefault(r, ts - (waited or 0) / 1000.0)
    ph, el = _phase(ctx)
    if ph in ("warmup", "drain"):
        return Result("na", {"warmup": f"warm-up 구간({el:.0f}초) — 러너가 목표보다 낮은 도착률로 보낸다",
                             "drain": "측정이 끝나고 남은 요청이 빠져나가는 구간"}[ph])
    ts = np.array([t for t in seen.values() if t])
    win = ts[ts > ctx["now"] - 70]
    win = win[win <= ctx["now"] - 10]   # 로그 수집이 몇 초 늦으므로 최근 10초는 뺀다
    got = len(win) / 60.0
    ev = [f"최근 60초(10초 전까지) 스케줄러가 처음 본 요청 {len(win)}건 = {got:.1f} req/s"]
    if not tgt:
        return Result("ok", f"{got:.1f} req/s (trace run — 목표가 시간에 따라 바뀐다)", ev)
    off = (got - tgt) / tgt
    st = "ok" if abs(off) <= 0.15 else ("fail" if got > 1.5 * tgt or got < 0.5 * tgt else "warn")
    return Result(st, f"{got:.1f} req/s (목표 {tgt:.1f}, {100 * off:+.0f}%)", ev)
