// ③ run 하나의 상세. 위에서부터: 점수 → 검사 → 엔진 시계열 → 스케줄러 결정 → 인스턴스별 클래스 구성 → 지연 분포 → 설정.
// 시간축 그래프는 모두 같은 group이라 하나를 확대하면 나머지도 같이 움직인다.
import { api, esc, fmt, badge, checkHtml } from "../lib/util.js";
import { drawSeries, drawLatency, drawFluidServe, stackHtml } from "../lib/runviews.js";
import { noteBox } from "../lib/notes.js";


export async function render(el, { name }) {
  el.innerHTML = `<h1 class="mono">${esc(name)}</h1><div id="r-body" class="muted">불러오는 중…</div>`;
  const body = el.querySelector("#r-body");
  const d = await api(`/runs/${encodeURIComponent(name)}`);
  const info = d.info, s = d.score || {};
  const classes = Object.keys(s.per_class || {});
  body.className = "";
  body.innerHTML = `
    <div class="toolbar">${badge(info.state === "done" ? "ok" : "warn", { done: "끝남", running: "진행 중", incomplete: "불완전" }[info.state])}
      <span>${esc(info.arm_label || "")} <span class="muted mono">${esc(info.arm || "")}</span></span>
      <span class="muted">세션 ${esc(info.session || "")} · ${info.rate ? fmt(info.rate, 1) + " req/s" : "trace"} · ${d.config?.duration_min ? fmt(d.config.duration_min, 0) + "분" : "길이는 trace가 정한다"} · ${esc(d.config?.workload || "")}</span>
      ${info.excluded?.length ? badge("warn", "제외 목록: " + info.excluded.join(" / ")) : ""}</div>
    <h2>메모 <span class="muted" style="font-weight:400;font-size:12px">— project/notes/runs/에 파일로 남는다</span></h2><div id="r-note"></div>
    ${scoreHtml(d)}
    <h2>검사</h2><div>${(d.checks || []).map(c => checkHtml(c)).join("") || '<span class="muted">채점이 끝나면 나온다</span>'}</div>
    <h2>엔진 <span class="muted" style="font-weight:400;font-size:12px">— 러너 수집기가 1초마다 기록한 vLLM 메트릭. 선 하나가 엔진 하나(포트)</span></h2>
    <div class="grid g2" id="eng"></div>
    <h2>스케줄러 결정 <span class="muted" style="font-weight:400;font-size:12px">— scheduler_dispatch.log</span></h2>
    <div id="dec"></div>
    <h2>인스턴스별 클래스 구성 <span class="muted" style="font-weight:400;font-size:12px">— 그 순간 각 인스턴스에서 처리 중인 요청 수(routing 시각부터 끝난 시각까지)</span></h2>
    <div class="grid g4" id="occ"></div>
    <h2>지연 분포와 결과 <span class="muted" style="font-weight:400;font-size:12px">— 클라이언트 측정, warm-up 60 s·drain 20 s 제외, 거절·오류 제외</span></h2>
    <div class="grid g2" id="lat"></div>
    <h2>FluidServe 내부 값 <span class="muted" style="font-weight:400;font-size:12px">— 스케줄러 /metrics를 대시보드가 2초마다 기록한 것. run 디렉토리에는 없다</span></h2>
    <div class="grid g2" id="fs"></div>
    <h2>이 run이 쓴 스택 <span class="muted" style="font-weight:400;font-size:12px">— 실행 중인 바이너리 md5와 플래그, run 시작과 스케줄러 파드가 바뀔 때마다</span></h2>
    <div id="stack"></div>
    <h2>run_config.json</h2><div class="panel"><details><summary>펼치기</summary><pre class="mono">${esc(JSON.stringify(d.config, null, 1))}</pre></details></div>`;

  noteBox(body.querySelector("#r-note"), "run", name);
  const [series, lat, fs] = await Promise.all([
    api(`/runs/${encodeURIComponent(name)}/series`).catch(e => ({ error: String(e) })),
    d.score_state === "scored" ? api(`/runs/${encodeURIComponent(name)}/latency`).catch(() => null) : null,
    api(`/runs/${encodeURIComponent(name)}/fluidserve`).catch(() => null),
  ]);
  drawFluidServe(body.querySelector("#fs"), fs);
  body.querySelector("#stack").innerHTML = stackHtml(fs?.stack);
  drawSeries(body, series);
  drawLatency(body.querySelector("#lat"), lat);
}

function scoreHtml(d) {
  const s = d.score;
  if (!s || d.score_state !== "scored") return `<div class="panel muted">점수: ${esc(d.score_state)}${s?.error ? " — " + esc(s.error) : ""}</div>`;
  const card = (k, v, sub) => `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${sub || ""}</div></div>`;
  const pc = Object.entries(s.per_class || {}).map(([c, v]) => `<tr><td>${esc(c)}</td><td class="num">${fmt(v.n, 0)}</td>
    <td class="num">${fmt(v.offered)}</td><td class="num">${fmt(v.admitted)}</td><td class="num">${fmt(v.rejected_pct)}</td></tr>`).join("");
  return `<div class="cards">
    ${card("모든 도착 기준 달성률", fmt(s.offered) + "%", "거절·오류=위반, 미완료 제외")}
    ${card("admitted 기준 달성률", fmt(s.admitted) + "%", "받아들인 요청만")}
    ${card("거절률", fmt(s.rejected_pct) + "%", "")}
    ${card("token goodput", fmt(s.goodput_tok_s, 0), "마감 안 토큰/s · 처리량 " + fmt(s.throughput_tok_s, 0))}
    ${card("preemption", fmt(d.preemptions, 0), "엔진 4대 합 · 세션 간 비교 금지")}
    ${card("요청", fmt(s.n, 0), `미완료 ${fmt(s.n_cutoff, 0)} · 실제 ${fmt(s.achieved_rate, 2)} req/s`)}
  </div>
  <div class="panel" style="margin-top:10px"><table><tr><th>클래스</th><th class="num">요청</th><th class="num">모든 도착 %</th><th class="num">admitted %</th><th class="num">거절 %</th></tr>${pc}</table>
  <div class="note">규칙 ${esc(s.rule)} · 채점 ${fmt(s.score_seconds, 1)}초 · ${new Date(s.scored_at * 1000).toLocaleString("ko-KR")}</div></div>`;
}

