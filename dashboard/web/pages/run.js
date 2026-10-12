// ③ run 하나의 상세. 위에서부터: 점수 → 검사 → 엔진 시계열 → 스케줄러 결정 → 인스턴스별 클래스 구성 → 지연 분포 → 설정.
// 시간축 그래프는 모두 같은 group이라 하나를 확대하면 나머지도 같이 움직인다.
import { api, esc, fmt, badge, checkHtml } from "../lib/util.js";
import { timeLines, timeBars, cdf, ENGINE_COLORS } from "../lib/charts.js";

const G = "run-time";
const OUTCOME = [["met", "성공", "#59a14f"], ["missed", "SLO 위반", "#e15759"], ["rejected", "거절", "#bab0ac"],
  ["errored", "오류", "#b07aa1"], ["cutoff", "미완료", "#edc948"]];

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
    <h2>run_config.json</h2><div class="panel"><details><summary>펼치기</summary><pre class="mono">${esc(JSON.stringify(d.config, null, 1))}</pre></details></div>`;

  const [series, lat] = await Promise.all([
    api(`/runs/${encodeURIComponent(name)}/series`).catch(e => ({ error: String(e) })),
    d.score_state === "scored" ? api(`/runs/${encodeURIComponent(name)}/latency`).catch(() => null) : null,
  ]);
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

function chartBox(parent, cls = "") {
  const box = document.createElement("div");
  box.className = "panel";
  box.innerHTML = `<div class="chart ${cls}"></div>`;
  parent.appendChild(box);
  return box.firstChild;
}

function drawSeries(body, d) {
  const eng = body.querySelector("#eng"), dec = body.querySelector("#dec"), occ = body.querySelector("#occ");
  if (!d || d.error || !d.t0) { eng.innerHTML = `<div class="panel muted">server_metrics가 없다 ${esc(d?.error || "")}</div>`; return; }
  const ports = Object.keys(d.engines);
  const ser = key => ports.map((p, i) => ({ name: p, t: d.engines[p].t, y: d.engines[p][key], color: ENGINE_COLORS[i] }));
  timeLines(chartBox(eng), { title: "KV cache 사용률", yname: "%", series: ser("kv_pct"), group: G, ymax: 100 });
  timeLines(chartBox(eng), { title: "running 요청 수", yname: "요청", series: ser("running"), group: G });
  timeLines(chartBox(eng), { title: "waiting 요청 수 (엔진 큐)", yname: "요청", series: ser("waiting"), group: G });
  timeLines(chartBox(eng), { title: "생성 토큰 (5초 이동평균)", yname: "tok/s", series: ser("gen_tok_per_s"), group: G });
  timeLines(chartBox(eng), { title: "토큰 사이 시간, 엔진 보고값 (30초 창 평균)", yname: "ms", series: ser("itl_ms"), group: G,
    marks: [{ y: 50, label: "chat 50" }, { y: 75, label: "swe 75" }, { y: 100, label: "deepresearch 100" }] });
  timeLines(chartBox(eng), { title: "preemption (5초 이동평균)", yname: "회/s", series: ser("preempt_per_s"), group: G });
  timeLines(chartBox(eng), { title: "prefix cache 적중률 (30초 창)", yname: "%", series: ser("prefix_hit_pct"), group: G, ymax: 100 });
  if (d.gateway) timeLines(chartBox(eng), { title: "게이트웨이 처리 중·대기 요청", yname: "요청", group: G,
    series: [{ name: "처리 중", t: d.gateway.t, y: d.gateway.current }, { name: "대기", t: d.gateway.t, y: d.gateway.pending }] });
  if (d.scheduler) {
    const ins = Object.keys(d.scheduler.instances).sort();
    timeLines(chartBox(eng), { title: "decode batch 크기 (스케줄러가 본 값)", yname: "요청", group: G,
      series: ins.map((p, i) => ({ name: p, t: d.scheduler.t, y: d.scheduler.instances[p].decode_batch_size, color: ENGINE_COLORS[ports.indexOf(p) >= 0 ? ports.indexOf(p) : i] })) });
  }

  const ds = d.dispatch;
  if (!ds || !ds.decisions) { dec.innerHTML = `<div class="panel muted">결정 로그가 없다</div>`; }
  else {
    const kinds = ds.decisions.kinds, cls = Object.keys(ds.decisions.counts).filter(c => c !== "?" || Object.values(ds.decisions.counts["?"]).some(v => v.some(x => x)));
    const tot = ds.decisions.totals;
    const shedNote = ds.policy === "fluidserve"
      ? "FluidServe 결정 줄(route = 인스턴스에 보냄, shed:<사유> = 거절). 클래스는 결정 줄의 tier로 정한다."
      : "이 정책은 결정 줄을 쓰지 않으므로 routing 줄만 센다. 거절은 아래 '지연 분포와 결과'의 막대에 있다.";
    dec.innerHTML = `<div class="note">${shedNote} 10초 단위.</div><div class="grid g4" id="dec-g"></div>
      <div class="panel" style="margin-top:8px"><table><tr><th>클래스</th>${kinds.map(k => `<th class="num">${esc(k)}</th>`).join("")}</tr>
      ${cls.map(c => `<tr><td>${esc(c)}</td>${kinds.map(k => `<td class="num">${fmt(tot[c][k], 0)}</td>`).join("")}</tr>`).join("")}</table>
      ${ds.waited ? `<div class="note" style="margin-top:8px">FluidServe가 결정 전에 요청을 붙잡아 둔 시간(waited, ms):
        ${Object.entries(ds.waited).map(([c, w]) => `${esc(c)} — ${fmt(w.held_pct)}%가 기다렸고, p50 ${fmt(w.q["0.5"], 0)} · p90 ${fmt(w.q["0.9"], 0)} · p99 ${fmt(w.q["0.99"], 0)} · 최대 ${fmt(w.q["1.0"], 0)}`).join("<br>")}</div>` : ""}</div>`;
    const kc = k => (k === "route" ? "#59a14f" : k.startsWith("shed") ? ["#e15759", "#ff9da7", "#9c755f", "#b07aa1"][kinds.filter(x => x.startsWith("shed")).indexOf(k) % 4] : "#bab0ac");
    for (const c of cls) {
      timeBars(chartBox(dec.querySelector("#dec-g"), "short"), { title: c, yname: "요청/10s", t: ds.decisions.t, group: G,
        series: kinds.map(k => ({ name: k, y: ds.decisions.counts[c][k], color: kc(k) })) });
    }
  }
  if (ds?.occupancy) {
    const colors = { chat: "#4e79a7", deepresearch: "#f28e2b", swe: "#59a14f" };
    for (const [inst, byc] of Object.entries(ds.occupancy.instances)) {
      timeLines(chartBox(occ, "short"), { title: `인스턴스 ${inst}`, yname: "요청", group: G, stack: true,
        series: Object.entries(byc).map(([c, y]) => ({ name: c, t: ds.occupancy.t, y, color: colors[c] })) });
    }
  } else occ.innerHTML = `<div class="panel muted">routing 줄이나 request_ids.jsonl이 없다</div>`;
}

function drawLatency(box, d) {
  if (!d) { box.innerHTML = `<div class="panel muted">채점이 끝나면 나온다</div>`; return; }
  const B = d.budgets, cls = Object.keys(d.cdf);
  cdf(chartBox(box), { title: "TTFT 누적분포 · 점선 = 예산 " + cls.map(c => `${c} ${B[c]?.ttft_s}s`).join(", "), xname: "s", q: d.q,
    series: cls.map(c => ({ name: c, x: d.cdf[c].ttft_s, color: B[c]?.color })),
    marks: cls.map(c => ({ series: c, x: B[c]?.ttft_s, label: `${c} ${B[c]?.ttft_s}s` })),
    xmax: Math.max(...cls.map(c => (B[c]?.ttft_s || 0) * 2.5)) });
  cdf(chartBox(box), { title: "요청별 평균 토큰 사이 시간 누적분포 · 점선 = 예산 " + cls.map(c => `${c} ${B[c]?.tbt_ms}ms`).join(", "), xname: "ms", q: d.q,
    series: cls.map(c => ({ name: c, x: d.cdf[c].itl_ms, color: B[c]?.color })),
    marks: cls.map(c => ({ series: c, x: B[c]?.tbt_ms, label: `${c} ${B[c]?.tbt_ms}` })),
    xmax: Math.max(...cls.map(c => (B[c]?.tbt_ms || 0) * 2.5)) });
  const g = document.createElement("div");
  g.className = "grid g4"; g.style.gridColumn = "1 / -1";
  box.appendChild(g);
  for (const c of cls) {
    timeBars(chartBox(g, "short"), { title: `${c} — 도착 시각별 결과 (ladder95)`, yname: "요청/10s", t: d.t, group: G,
      series: OUTCOME.map(([k, label, color]) => ({ name: label, y: d.outcome[c][k], color })) });
  }
}
