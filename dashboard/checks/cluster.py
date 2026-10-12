"""클러스터 상태 검사. 실험을 걸기 전에 전부 ok여야 한다. 항목의 대부분은 드라이버의 check_stack과 같다
(k8s/exp07/run_exp108_t75.sh)."""
from checks import Result, check
from config import CFG, binary_name, path
from sources import host, k8s

KC = CFG["kube"]
EXP = CFG["expected_stack"]


def _pods(prefix):
    ps = k8s.pods()
    if "error" in ps:
        raise RuntimeError(ps["error"])
    return [p for p in ps.get("items", []) if p["metadata"]["name"].startswith(prefix)]


def _pod_problem(p):
    """파드가 정상이 아닌 이유 하나, 정상이면 None."""
    st = p.get("status", {})
    for cs in st.get("containerStatuses", []) or []:
        w = (cs.get("state") or {}).get("waiting")
        if w:
            return f"{cs['name']}: {w.get('reason')} {w.get('message', '')[:120]}"
        if not cs.get("ready"):
            return f"{cs['name']}: not ready"
    if st.get("phase") not in ("Running", "Succeeded"):
        return f"phase={st.get('phase')}"
    return None


def _restarts(p):
    return sum(cs.get("restartCount", 0) for cs in p.get("status", {}).get("containerStatuses", []) or [])


@check("scheduler_pods", "cluster", "스케줄러 파드가 하나이고 정상이다",
       "새 바이너리가 기동 중 panic해 CrashLoopBackOff가 되어도 옛 파드는 Running으로 남아 클러스터가 정상으로 "
       "보인다. 드라이버는 이것을 느린 rollout과 구분하지 못했다(traps.md 함정 B)")
def scheduler_pods(_):
    ps = _pods(KC["scheduler_deploy"] + "-")
    ev = [f"{p['metadata']['name']}: {_pod_problem(p) or 'ok'}, restarts={_restarts(p)}" for p in ps]
    bad = [p for p in ps if _pod_problem(p)]
    if not ps:
        return Result("fail", "스케줄러 파드가 없다", ev)
    if bad:
        return Result("fail", f"{len(bad)}개 파드가 정상이 아니다 — 옛 파드가 대신 돌고 있을 수 있다", ev)
    if len(ps) > 1:
        return Result("warn", f"스케줄러 파드가 {len(ps)}개다 — rollout이 끝나지 않았다", ev)
    if _restarts(ps[0]) > 0:
        return Result("warn", f"재시작 {_restarts(ps[0])}회", ev)
    return Result("ok", "파드 1개, Ready", ev)


def _exe_check(prefix, file_key, container=None):
    ps = [p for p in _pods(prefix) if not _pod_problem(p)]
    want = host.file_md5(path(file_key))
    ev = [f"{path(file_key)}: {want[:8] if want else '없음'} ({binary_name(want) or '알려지지 않은 md5'})"]
    if not ps:
        return Result("fail", "정상인 파드가 없다", ev)
    worst = "ok"
    for p in ps:
        got = k8s.pod_exe_md5(p["metadata"]["name"], container).get("md5")
        ev.append(f"{p['metadata']['name']} /proc/1/exe: {got[:8] if got else '?'} "
                  f"({binary_name(got) or '알려지지 않은 md5'})")
        if got != want:
            worst = "fail"
        elif not binary_name(got) and worst == "ok":
            worst = "warn"
    msg = {"ok": "실행 중인 바이너리 = bin/의 파일",
           "warn": "bin/의 파일과 같지만 config.yaml의 known_binaries에 없는 md5다",
           "fail": "실행 중인 바이너리가 bin/의 파일과 다르다 — 재시작 전이거나 설치가 실패했다"}[worst]
    return Result(worst, msg, ev)


@check("scheduler_binary", "cluster", "스케줄러가 실행 중인 바이너리",
       "bin/은 hostPath로 마운트되어 있어 cp로 덮어쓰면 Text file busy로 실패하고, spec을 읽어서는 이를 알 수 없다. "
       "파드 안 md5sum /proc/1/exe가 유일한 확인 수단이다")
def scheduler_binary(_):
    return _exe_check(KC["scheduler_deploy"] + "-", "deployed_scheduler")


@check("gateway_binary", "cluster", "게이트웨이가 호스트에서 빌드한 바이너리다",
       "레지스트리 이미지의 게이트웨이에는 우리 수정이 없다. 드라이버 check_stack 항목")
def gateway_binary(_):
    d = k8s.deploy(KC["gateway_deploy"])
    if "error" in d:
        return Result("na", d["error"])
    cmd = (k8s.container_spec(d).get("command") or [""])[0]
    if cmd != EXP["gateway_command"]:
        return Result("fail", f"command가 {cmd}다 (기대 {EXP['gateway_command']})")
    return _exe_check(KC["gateway_deploy"] + "-", "deployed_gateway")


@check("scheduler_flags", "cluster", "스케줄러 정책과 KV admission",
       "KV 사용률 기반 admission(--admission-kv-usage-threshold)이 켜지면 정책 앞에 admission 층이 하나 더 생겨 "
       "비교가 오염된다. 드라이버 check_stack 항목")
def scheduler_flags(_):
    d = k8s.deploy(KC["scheduler_deploy"])
    if "error" in d:
        return Result("na", d["error"])
    args = k8s.container_spec(d).get("args") or []
    pol = k8s.flag_value(args, "--scheduling-policy")
    th = k8s.flag_value(args, "--admission-kv-usage-threshold")
    ev = [f"--scheduling-policy {pol}", f"--admission-kv-usage-threshold {th if th is not None else '(기본값)'}"]
    try:
        if th is not None and float(th) > EXP["kv_admission_threshold"]:
            return Result("fail", f"KV admission이 켜져 있다 (theta={th})", ev)
    except ValueError:
        pass
    return Result("ok", f"정책 {pol}, KV admission 꺼짐", ev)


@check("engine_env", "cluster", "엔진 migration 꺼짐, 엔진 쪽 스케줄링 인자 없음",
       "migration은 정책이 모르게 요청을 다른 인스턴스로 옮기고, 엔진 쪽 admission은 control plane 비교를 오염시킨다. "
       "드라이버 check_stack 항목")
def engine_env(_):
    lw = k8s.lws()
    if "error" in lw:
        return Result("na", lw["error"])
    c = k8s.container_spec(lw, "vllm")
    mig = k8s.env_of(c, "LLUMNIX_ENABLE_MIGRATION")
    extra = k8s.env_of(c, "SCHED_EXTRA_ARGS")
    ev = [f"LLUMNIX_ENABLE_MIGRATION={mig}", f"SCHED_EXTRA_ARGS='{extra}'"]
    bad = []
    if mig != EXP["migration"]:
        bad.append(f"migration={mig}")
    if extra != EXP["sched_extra_args"] and not (extra == "unset" and EXP["sched_extra_args"] == ""):
        bad.append(f"SCHED_EXTRA_ARGS='{extra}'")
    return Result("fail" if bad else "ok", ", ".join(bad) if bad else "둘 다 기대값", ev)


@check("engines_up", "cluster", "엔진 4대가 모두 /health에 응답한다",
       "엔진 파드의 startup probe는 8000번만 본다. 나머지 셋이 아직 가중치를 올리는 중에 조건이 시작되어 "
       "한 sweep에서 14개 조건 중 3개가 엔진이 모자란 채로 돌았다(exp-run 스킬 §4)")
def engines_up(_):
    h = host.engine_health()
    if "error" in h:
        return Result("na", h["error"])
    ev = [f"{h.get('ip')}:{e['port']} {'200' if e['ok'] else e['error']}" for e in h["engines"]]
    n = sum(e["ok"] for e in h["engines"])
    tot = len(h["engines"])
    return Result("ok" if n == tot else "fail", f"{n}/{tot} 응답", ev)


@check("runner_jobs", "cluster", "러너 Job이 멈춰 있지 않고, 동시에 둘 이상 돌지 않는다",
       "ConfigMap이 없어 러너 파드가 CreateContainerConfigError로 59분 동안 멈춰 있었는데 감시 스크립트가 잡지 못했다"
       "(2026-10-10). 러너 두 개가 같은 함대를 동시에 몬 run은 제외 목록에 있다(EXP-114)")
def runner_jobs(_):
    ps = _pods(KC["runner_job_prefix"])
    active = [p for p in ps if p.get("status", {}).get("phase") in ("Pending", "Running")]
    ev = [f"{p['metadata']['name']}: {p['status'].get('phase')} {_pod_problem(p) or ''}" for p in active]
    stuck = [p for p in active if (_pod_problem(p) or "").split(": ")[-1].split(" ")[0] in
             ("CreateContainerConfigError", "ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff",
              "CreateContainerError")]
    if stuck:
        return Result("fail", f"러너 파드 {len(stuck)}개가 멈춰 있다", ev)
    if len(active) > 1:
        return Result("fail", f"러너가 {len(active)}개 동시에 돌고 있다 — 같은 함대를 나눠 쓴다", ev)
    return Result("ok", "러너 1개 실행 중" if active else "실행 중인 러너 없음", ev)


@check("model_configmap", "cluster", "configmap/llumnix-model이 있다",
       "2026-10-10 재구축 때 이 ConfigMap이 없어 러너 파드가 기동하지 못했다")
def model_configmap(_):
    r = k8s.configmap_exists("llumnix-model")
    return Result("ok", "있다") if r.get("exists") else Result("fail", "없다", [r.get("error", "")])


@check("disk_free", "cluster", "디스크 남은 용량",
       "run 하나가 약 1 GB(8분)이고, 노드가 DiskPressure가 되어 후반이 잘린 run이 제외 목록에 있다(EXP-56, EXP-99)")
def disk_free(_):
    ds = host.disks()
    lim = CFG["thresholds"]["disk_free_gb_warn"]
    ev = [f"{d['path']}: {d.get('free_gb', 0):,.0f} GB 남음 / {d.get('total_gb', 0):,.0f} GB"
          if "error" not in d else f"{d['path']}: {d['error']}" for d in ds]
    low = [d for d in ds if d.get("free_gb", 1e9) < lim]
    return Result("warn" if low else "ok", f"{len(low)}곳이 {lim} GB 미만" if low else "충분하다", ev)
