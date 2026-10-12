"""run 하나에 대한 검사. 채점이 끝난 뒤에 돈다. ctx는 dict:
  name, dir, info(runs.parse_name 결과), score(score.json), requests(요청별 표, pandas), engines(엔진별 누적값)

앞의 다섯은 analysis_scripts/request_level/run_health.py의 flag와 같은 판정이다. 다른 점은 달성률을
ladder95로 본다는 것 하나다(run_health는 옛 규칙의 admitted를 본다).
"""
import numpy as np

from checks import Result, check
from config import CFG

TH = CFG["thresholds"]


def _s(ctx):
    s = ctx.get("score") or {}
    return None if s.get("error") else s


@check("run_rate", "run", "실제 도착률이 이름의 도착률과 맞다",
       "부하 생성기가 목표 도착률을 내지 못하면 그 조건은 이름과 다른 부하를 잰 것이다(run_health.py)")
def run_rate(ctx):
    s = _s(ctx)
    if not s or not s.get("target_rate"):
        return Result("na", "목표 도착률이 이름에 없다(trace run이면 정상)") if s else None
    tgt, got = s["target_rate"], s.get("achieved_rate") or 0
    off = (got - tgt) / tgt
    ev = [f"목표 {tgt:.1f} req/s, 실제 {got:.2f} req/s (warm-up 60 s와 drain 20 s를 뺀 창의 도착 수 / 창 길이)"]
    return Result("fail" if abs(off) > TH["rate_off_frac"] else "ok", f"{100 * off:+.1f}%", ev)


@check("run_engines", "run", "엔진 4대가 끝까지 토큰을 만들었다",
       "포트가 뜨지 않은 엔진도 메트릭 파일은 생긴다(모든 값이 None). 엔진 1대로 8분 동안 돈 조건이 정상처럼 보인 일이 있다")
def run_engines(ctx):
    s = _s(ctx)
    if not s:
        return None
    nf, nl = s.get("engine_files"), s.get("engines_advancing")
    ok = nf == 4 and nl == 4
    return Result("ok" if ok else "fail", f"메트릭 파일 {nf}개, 생성 토큰이 늘어난 엔진 {nl}대")


@check("run_size", "run", "요청 수와 거절률이 정상 범위다",
       "요청 수가 너무 적거나, 모두 거절됐거나, 포화 구간인데 달성률이 정확히 0이나 100이면 정책이 시작되지 않은 경우가 "
       "대부분이었다(run_health.py)")
def run_size(ctx):
    s = _s(ctx)
    if not s:
        return None
    n, rej, adm = s.get("n") or 0, s.get("rejected_pct") or 0, s.get("admitted")
    flags = []
    if n < TH["min_requests"]:
        flags.append(f"요청 {n}건뿐")
    if rej >= 99.9:
        flags.append("전부 거절")
    if n > 1000 and adm == 0.0:
        flags.append("admitted 달성률이 정확히 0")
    if n > 1000 and adm == 100.0 and rej < 1 and (s.get("target_rate") or 0) >= 40:
        flags.append("40 req/s 이상인데 달성률이 정확히 100")
    return Result("fail" if flags else "ok", "; ".join(flags) or f"요청 {n:,}건, 거절 {rej:.1f}%")


@check("run_excluded", "run", "제외 목록에 없다",
       "ms_dev/data/excluded_runs.tsv: 오염되었거나 잘린 run. 지우지 않고 집계에서만 뺀다")
def run_excluded(ctx):
    why = (ctx.get("info") or {}).get("excluded") or []
    return Result("warn", "제외 목록에 있다", why) if why else Result("ok", "없다")


@check("run_errors", "run", "오류로 끝난 요청이 적다",
       "거절이 아닌 오류만 센다. admission control이 없는 arm에서 클라이언트 포트가 소진되어 오류가 쏟아지고, 그 오류가 정책의 실패로 읽힌 일이 있다"
       "(EXP-54: cannot assign requested address 62,114건). 오류는 종류별로 본다")
def run_errors(ctx):
    r = ctx.get("requests")
    if r is None or "errored" not in r:
        return None
    n = len(r)
    # 러너는 거절된 요청에도 is_error를 표시한다. 거절은 admission control의 정상 동작이므로 여기서는 뺀다.
    e = r[r["errored"].astype(bool) & ~r["rejected"].astype(bool)]
    if not len(e):
        return Result("ok", "오류 0건")
    ev = []
    if "error_msg" in e:
        ev = [f"{c:,}  {str(m)[:140]}" for m, c in e["error_msg"].fillna("(빈 메시지)").value_counts().head(8).items()]
    pct = 100 * len(e) / max(n, 1)
    return Result("warn" if pct > 1 else "ok", f"{len(e):,}건 ({pct:.2f}%)", ev)


@check("run_load_spike", "run", "1분 창의 요청 시작 수가 목표 도착률을 크게 넘지 않는다",
       "요청 시작 수가 trace가 내는 도착률보다 많으면 그 구간은 시스템이 아니라 재시도를 반복하는 클라이언트를 잰 것이다"
       "(EXP-54: trace는 50/s인데 시작 수가 170/s)")
def run_load_spike(ctx):
    s, r = _s(ctx), ctx.get("requests")
    if not s or r is None or not s.get("target_rate"):
        return None
    tgt = s["target_rate"]
    per_min = np.bincount((r["rel"] // 60).astype(int)) / 60.0
    per_min = per_min[1:-1] if len(per_min) > 2 else per_min  # 잘린 첫·마지막 창 제외
    if not len(per_min):
        return None
    mx = float(per_min.max())
    ev = [f"1분 창별 요청 시작 수(req/s): " + ", ".join(f"{x:.1f}" for x in per_min)]
    return Result("fail" if mx > 1.5 * tgt else "ok", f"최대 {mx:.1f} req/s (목표 {tgt:.1f})", ev)


@check("run_tbt_crosscheck", "run", "클라이언트가 잰 토큰 사이 시간이 엔진이 보고한 값과 맞다",
       "클라이언트 TBT가 2026-07-30까지 실제의 1/1.92로 기록됐다. 엔진 값과 비율이 1에서 벗어나면 같은 이름의 두 양이 "
       "다른 정의로 만들어진 것이다(exp-run 스킬 §6b)")
def run_tbt_crosscheck(ctx):
    r, eng = ctx.get("requests"), ctx.get("engines") or {}
    if r is None or not eng or "latency" not in r:
        return None
    ok = r[~r["rejected"].astype(bool) & ~r["errored"].astype(bool)].dropna(
        subset=["latency", "first_token_latency", "output_tokens"])
    ok = ok[ok["output_tokens"] > 1]
    if not len(ok):
        return None
    client = 1000 * (ok["latency"] - ok["first_token_latency"]).sum() / (ok["output_tokens"] - 1).sum()
    vals = [(e.get("itl_ms_mean"), e.get("gen_tokens")) for e in eng.values() if e.get("itl_ms_mean")]
    if not vals:
        return Result("na", "엔진 inter-token latency가 기록되지 않았다")
    w = sum(g or 0 for _, g in vals)
    server = sum(v * (g or 0) for v, g in vals) / w if w else float(np.mean([v for v, _ in vals]))
    ratio = client / server
    ev = [f"클라이언트: Σ(e2e − TTFT) / Σ(출력 토큰 − 1) = {client:.1f} ms (거절·오류 제외 요청)",
          f"엔진: vllm:inter_token_latency_seconds 합/개수, 엔진별 생성 토큰으로 가중 평균 = {server:.1f} ms",
          f"비율 {ratio:.3f}"]
    return Result("ok" if 0.8 <= ratio <= 1.25 else "warn", f"클라이언트 {client:.1f} ms / 엔진 {server:.1f} ms", ev)
