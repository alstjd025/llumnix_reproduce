// ② 실시간: 체인 스크립트, 러너 파드, 진행 중인 run. 요약은 5초, 그래프는 10초마다 갱신한다.
// 그래프는 같은 요소에 다시 그리므로 확대 상태가 유지된다.
import { api, esc, fmt, badge, worst, checkHtml, ago } from "../lib/util.js";
import { drawSeries, outcomeBars } from "../lib/runviews.js";

export async function render(el) {
  el.innerHTML = `<h1>실시간</h1>
    <div id="lv-head" class="muted">불러오는 중…</div>
    <h2>검사 <span class="muted" style="font-weight:400;font-size:12px">— 모두 "지금도 움직이는가"를 묻는다. 5초마다</span></h2><div id="lv-checks"></div>
    <div id="lv-run" style="display:none">
      <h2>중간 결과 <span class="muted" style="font-weight:400;font-size:12px">— 최종 점수가 아니다: 끝난 요청만 세고(긴 요청일수록 늦게 들어온다), warm-up·drain을 빼지 않았다. 판정은 채점과 같은 ladder95 함수</span></h2>
      <div class="panel" id="lv-interim"></div><div class="grid g4" id="lv-outcome" style="margin-top:10px"></div>
      <h2>엔진</h2><div class="grid g2" id="eng"></div>
      <h2>스케줄러 결정</h2><div id="dec"></div>
      <h2>인스턴스별 클래스 구성 <span class="muted" style="font-weight:400;font-size:12px">— 아직 끝나지 않은 요청은 지금도 처리 중으로 센다</span></h2><div class="grid g4" id="occ"></div>
    </div>
    <h2>체인 스크립트 <span class="muted" style="font-weight:400;font-size:12px">— /home/nxclab/tools/*.log 중 최근 24시간. 그 로그를 stdout으로 연 프로세스가 있으면 살아 있다</span></h2><div id="lv-chains"></div>
    <h2>러너 파드</h2><div class="panel" id="lv-pods"></div>`;
  const $ = s => el.querySelector(s);
  const openChains = new Set();
  let active = null, busy = false;

  const head = async () => {
    const d = await api("/live");
    active = d.active;
    const pr = d.progress;
    const pct = pr?.planned_s ? Math.min(100, (100 * pr.elapsed_s) / pr.planned_s) : null;
    $("#lv-head").className = "";
    $("#lv-head").innerHTML = active
      ? `<div class="panel"><div>${badge("warn", "진행 중")} <a class="mono" href="#/run/${encodeURIComponent(active.name)}">${esc(active.name)}</a></div>
          <div style="margin-top:6px">${esc(active.arm_label || "")} · ${active.rate ? fmt(active.rate, 1) + " req/s" : "trace"} · 세션 ${esc(active.session || "")}</div>
          <div style="margin-top:6px">수집 시작 후 ${fmt((pr?.elapsed_s || 0) / 60, 1)}분${pr?.planned_s ? ` / 예정 ${fmt(pr.planned_s / 60, 1)}분(측정 ${pr.duration_min}분 + warm-up ${fmt(pr.warmup_s, 0)}초, 엔진 재시작 시간은 빠져 있다)` : ""}</div>
          ${pct != null ? `<div style="height:6px;background:var(--line);border-radius:3px;margin-top:6px"><div style="height:6px;width:${pct}%;background:var(--accent);border-radius:3px"></div></div>` : ""}</div>`
      : `<div class="panel">진행 중인 run 없음.${d.recent ? ` 최근: <a class="mono" href="#/run/${encodeURIComponent(d.recent.name)}">${esc(d.recent.name)}</a> (${ago(d.recent.idle_s)} 끝남)` : ""}</div>`;
    $("#lv-checks").innerHTML = `<div class="toolbar">${badge(worst(d.checks))}</div>` + d.checks.map(c => checkHtml(c)).join("");
    $("#lv-chains").querySelectorAll("details[open]").forEach(x => openChains.add(x.dataset.log));
    $("#lv-chains").innerHTML = d.chains.length ? d.chains.map((c, i) => {
      const st = { running: "ok", done: "na", aborted: "fail", died: "fail" }[c.state];
      const bad = [...(c.marks.abort || []), ...(c.marks.failed || [])];
      return `<div class="check ${st}"><details data-log="${esc(c.log)}" ${openChains.has(c.log) || (i === 0 && !openChains.size) ? "open" : ""}>
        <summary>${badge(st, { running: "살아 있음", done: "끝남", aborted: "스스로 멈춤", died: "죽음" }[c.state])} <b class="mono">${esc(c.name)}</b>
          <span class="muted">마지막 기록 ${ago(c.idle_s)}${c.pids.length ? " · pid " + c.pids.map(p => p.pid).join(", ") : ""}</span>
          ${bad.length ? badge("warn", "ABORT/FAILED 줄 " + bad.length) : ""}</summary>
        ${c.pids.length ? `<div class="why mono">${c.pids.map(p => esc(p.cmd)).join("<br>")}</div>` : ""}
        <pre>${c.tail.map(esc).join("\n")}</pre></details></div>`;
    }).join("") : `<div class="panel muted">최근 24시간 안에 바뀐 체인 로그가 없다</div>`;
    $("#lv-pods").innerHTML = d.runner_pods.length
      ? `<table><tr><th>이름</th><th>phase</th><th>문제</th><th>시작</th></tr>${d.runner_pods.map(p => `<tr><td class="mono">${esc(p.name)}</td><td>${esc(p.phase)}</td><td>${esc(p.problem || "")}</td><td class="mono">${esc(p.started || "")}</td></tr>`).join("")}</table>`
      : `<span class="muted">러너 파드 없음</span>`;
  };

  const graphs = async () => {
    if (!active || busy) { $("#lv-run").style.display = active ? "" : "none"; return; }
    busy = true;
    try {
      const d = await api(`/runs/${encodeURIComponent(active.name)}/series`);
      $("#lv-run").style.display = "";
      drawSeries(el.querySelector("#lv-run"), d);
      const it = d.interim;
      if (it) {
        $("#lv-interim").innerHTML = `<table><tr><th>클래스</th><th class="num">끝난 요청</th><th class="num">성공</th><th class="num">SLO 위반</th><th class="num">거절</th><th class="num">오류</th>
          <th class="num">모든 도착 기준 %</th><th class="num">admitted 기준 %</th></tr>
          ${Object.entries(it.totals).map(([c, t]) => `<tr><td>${esc(c)}</td><td class="num">${fmt(t.n, 0)}</td><td class="num">${fmt(t.met, 0)}</td><td class="num">${fmt(t.missed, 0)}</td>
          <td class="num">${fmt(t.rejected, 0)}</td><td class="num">${fmt(t.errored, 0)}</td><td class="num">${fmt(t.offered_pct)}</td><td class="num">${fmt(t.admitted_pct)}</td></tr>`).join("")}</table>`;
        outcomeBars($("#lv-outcome"), it, "중간 결과");
      } else $("#lv-interim").innerHTML = `<span class="muted">shards/에 끝난 요청이 아직 없다</span>`;
    } catch (e) { console.warn(e); }
    busy = false;
  };

  await head();
  await graphs();
  const t1 = setInterval(() => head().catch(console.warn), 5000);
  const t2 = setInterval(graphs, 10000);
  return () => { clearInterval(t1); clearInterval(t2); };
}
