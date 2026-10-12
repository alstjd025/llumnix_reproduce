// ① 상태: 실험을 걸기 전에 보는 페이지. 5초마다 갱신.
import { api, esc, fmt, badge, worst, checkHtml } from "../lib/util.js";

export async function render(el) {
  el.innerHTML = `<h1>상태</h1><div id="st-body" class="muted">불러오는 중…</div>`;
  const body = el.querySelector("#st-body");
  let open = new Set();
  const draw = async () => {
    let d;
    try { d = await api("/status"); } catch (e) { body.innerHTML = `<div class="panel">${esc(e)}</div>`; return; }
    body.querySelectorAll(".check details[open]").forEach(x => open.add(x.dataset.id));
    const w = worst(d.checks);
    const gpu = d.gpus.map(g => `<tr><td>${g.index}</td><td class="num">${fmt(g.util, 0)}%</td>
      <td class="num">${fmt(g.mem_used_mib / 1024, 0)} / ${fmt(g.mem_total_mib / 1024, 0)} GiB</td>
      <td class="num">${fmt(g.power_w, 0)} W</td><td class="num">${fmt(g.temp_c, 0)} °C</td></tr>`).join("");
    const pods = d.pods.map(p => `<tr><td class="mono">${esc(p.name)}</td><td>${esc(p.phase)}</td><td>${p.ready}</td>
      <td class="num">${p.restarts}</td><td class="mono">${esc(p.started || "")}</td></tr>`).join("");
    const fs = d.scheduler_args.filter(a => a.startsWith("--")).length;
    body.innerHTML = `
      <div class="toolbar">${badge(w, w === "ok" ? "실험을 걸 수 있는 상태" : w === "fail" ? "문제 있음" : "확인 필요")}
        <span class="muted">마지막 조회 ${new Date(d.now * 1000).toLocaleTimeString("ko-KR")} · 5초마다 갱신</span></div>
      <h2>검사</h2>
      <div>${d.checks.map(c => checkHtml(c).replace("<details", `<details data-id="${c.id}"`)).join("")}</div>
      <div class="grid g2">
        <div><h2>GPU</h2><div class="panel"><table><tr><th>#</th><th class="num">사용률</th><th class="num">메모리</th><th class="num">전력</th><th class="num">온도</th></tr>${gpu}</table></div></div>
        <div><h2>파드 (namespace llumnix)</h2><div class="panel scroll"><table><tr><th>이름</th><th>phase</th><th>ready</th><th class="num">재시작</th><th>시작</th></tr>${pods}</table></div></div>
      </div>
      <h2>스케줄러 deployment의 인자 (${fs}개 플래그)</h2>
      <div class="panel"><details><summary>펼치기</summary><pre class="mono">${esc(pairArgs(d.scheduler_args))}</pre></details>
      <div class="note">deployment spec에 적힌 값이다. 프로세스가 실제로 읽은 값은 아니다(실행 중인 바이너리는 위 검사의 /proc/1/exe md5로 확인한다).</div></div>`;
    body.querySelectorAll(".check details").forEach(x => { if (open.has(x.dataset.id)) x.open = true; });
    open = new Set();
  };
  await draw();
  const t = setInterval(draw, 5000);
  return () => clearInterval(t);
}

function pairArgs(a) {
  const out = [];
  for (let i = 0; i < a.length; i++) {
    if (a[i].startsWith("--") && !a[i].includes("=") && i + 1 < a.length && !a[i + 1].startsWith("--")) { out.push(`${a[i]} ${a[i + 1]}`); i++; }
    else out.push(a[i]);
  }
  return out.join("\n");
}
