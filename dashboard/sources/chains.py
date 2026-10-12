"""드라이버 연쇄 스크립트(체인)의 상태. 체인은 `nohup setsid ./run_xxx.sh … > <로그> 2>&1 &`로 띄우므로
로그 파일 하나가 체인 하나다(exp-run 스킬 §3).

살아 있는지는 **그 로그 파일을 stdout으로 열고 있는 프로세스가 있는가**로 판단한다(/proc/<pid>/fd/1). 성공 줄의 수를
세는 방식은 체인이 멈춘 것을 잡지 못한다(EXP-63에서 두 번 몇 시간씩 늦게 발견했다). 이름으로 프로세스를 찾는 방식은
같은 스크립트를 다른 인자로 띄운 체인을 구분하지 못한다.

로그 안의 줄은 드라이버마다 형식이 조금씩 다르므로, 종료·실패를 뜻하는 표시만 찾는다(MARKERS).
"""
import os
import re
import time

from config import CFG

MARKERS = {
    "done": re.compile(r"CHAIN DONE|SMOKE DONE|ALL DONE|\bDONE\b"),
    "abort": re.compile(r"\bABORT\b"),
    "failed": re.compile(r"\bFAILED\b|Traceback|rc=[1-9]"),
    "launched": re.compile(r"\blaunched\b"),
}


def _writers():
    """로그 경로 → 그 파일을 stdout(fd 1)이나 stderr(fd 2)로 연 프로세스 목록."""
    out = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        for fd in ("1", "2"):
            try:
                tgt = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            if tgt.endswith(".log"):
                try:
                    cmd = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode(errors="replace").strip()
                except OSError:
                    cmd = "?"
                out.setdefault(tgt, {})[int(pid)] = cmd
    return out


def _tail(p, n=40, maxbytes=200_000):
    try:
        with open(p, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - maxbytes))
            return f.read().decode(errors="replace").splitlines()[-n:]
    except OSError:
        return []


def list_chains(hours=24):
    """최근 hours시간 안에 바뀐 로그마다: 살아 있는지, 마지막 줄들, 종료·실패 표시."""
    d = CFG["paths"]["chain_logs"]
    now = time.time()
    writers = _writers()
    out = []
    try:
        logs = [os.path.join(d, f) for f in os.listdir(d) if f.endswith(".log")]
    except OSError:
        logs = []
    for p in sorted(logs, key=lambda x: -os.path.getmtime(x)):
        mt = os.path.getmtime(p)
        alive = writers.get(p, {})
        if now - mt > hours * 3600 and not alive:
            continue
        lines = _tail(p, 400)
        marks = {k: [ln for ln in lines if rx.search(ln)][-3:] for k, rx in MARKERS.items()}
        # 정상 종료: 마지막 다섯 줄 안에 DONE 표시가 있고, 그 뒤에 ABORT/FAILED가 없다. 드라이버마다 마지막 줄이
        # "=== … CHAIN DONE"이거나 "[exp108] rep1 <arm> DONE"이어서 형식 하나로는 판정할 수 없다(2026-10-12 smoke).
        last = lines[-5:]
        done_at = max([i for i, ln in enumerate(last) if MARKERS["done"].search(ln)], default=-1)
        finished = done_at >= 0 and not any(MARKERS["abort"].search(ln) or MARKERS["failed"].search(ln)
                                            for ln in last[done_at:])
        if alive:
            state = "running"
        elif finished:
            state = "done"
        else:
            # 프로세스가 없는데 마지막 줄에 체인 종료 표시가 없다 = 중간에 죽었다
            state = "died"
        out.append(dict(log=p, name=os.path.basename(p), mtime=mt, idle_s=now - mt, state=state,
                        pids=[dict(pid=k, cmd=v[:300]) for k, v in alive.items()],
                        tail=lines[-40:], marks=marks))
    return out
