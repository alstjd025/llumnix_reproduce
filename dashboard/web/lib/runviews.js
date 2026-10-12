// run 하나의 그래프 묶음. 결과 페이지(끝난 run)와 실시간 페이지(진행 중인 run)가 같이 쓴다.
// 그래프 상자는 key로 찾아 다시 쓰므로, 같은 컨테이너에 다시 그리면 새로 만들지 않고 데이터만 바뀐다.
import { esc, fmt } from "./util.js";
import { timeLines, timeBars, cdf, ENGINE_COLORS } from "./charts.js";

export const G = "run-time";
export const OUTCOME = [["met", "성공", "#59a14f"], ["missed", "SLO 위반", "#e15759"], ["rejected", "거절", "#bab0ac"],
  ["errored", "오류", "#b07aa1"], ["cutoff", "미완료", "#edc948"]];
export const CLASS_COLORS = { chat: "#4e79a7", deepresearch: "#f28e2b", swe: "#59a14f", "?": "#bab0ac" };

export function chartBox(parent, cls = "", key = null) {
  key = key || `k${parent.children.length}`;
  const hit = parent.querySelector(`:scope > [data-key="${key}"] > .chart`);
  if (hit) return hit;
  const box = document.createElement("div");
  box.className = "panel";
  box.dataset.key = key;
  box.innerHTML = `<div class="chart ${cls}"></div>`;
  parent.appendChild(box);
  return box.firstChild;
}

export function drawSeries(body, d) {
  const eng = body.querySelector("#eng"), dec = body.querySelector("#dec"), occ = body.querySelector("#occ");
  if (!d || d.error || !d.t0) { eng.innerHTML = `<div class="panel muted">server_metrics가 없다 ${esc(d?.error || "")}</div>`; return; }
  const ports = Object.keys(d.engines);
  const ser = key => ports.map((p, i) => ({ name: p, t: d.engines[p].t, y: d.engines[p][key], color: ENGINE_COLORS[i] }));
  timeLines(chartBox(eng, "", "kv"), { title: "KV cache 사용률", yname: "%", series: ser("kv_pct"), group: G, ymax: 100 });
  timeLines(chartBox(eng, "", "running"), { title: "running 요청 수", yname: "요청", series: ser("running"), group: G });
  timeLines(chartBox(eng, "", "waiting"), { title: "waiting 요청 수 (엔진 큐)", yname: "요청", series: ser("waiting"), group: G });
  timeLines(chartBox(eng, "", "gen"), { title: "생성 토큰 (5초 이동평균)", yname: "tok/s", series: ser("gen_tok_per_s"), group: G });
  timeLines(chartBox(eng, "", "itl"), { title: "토큰 사이 시간, 엔진 보고값 (30초 창 평균)", yname: "ms", series: ser("itl_ms"), group: G,
    marks: [{ y: 50, label: "chat 50" }, { y: 75, label: "swe 75" }, { y: 100, label: "deepresearch 100" }] });
  timeLines(chartBox(eng, "", "preempt"), { title: "preemption (5초 이동평균)", yname: "회/s", series: ser("preempt_per_s"), group: G });
  timeLines(chartBox(eng, "", "prefix"), { title: "prefix cache 적중률 (30초 창)", yname: "%", series: ser("prefix_hit_pct"), group: G, ymax: 100 });
  if (d.gateway) timeLines(chartBox(eng, "", "gateway"), { title: "게이트웨이 처리 중·대기 요청", yname: "요청", group: G,
    series: [{ name: "처리 중", t: d.gateway.t, y: d.gateway.current }, { name: "대기", t: d.gateway.t, y: d.gateway.pending }] });
  if (d.scheduler) {
    const ins = Object.keys(d.scheduler.instances).sort();
    timeLines(chartBox(eng, "", "batch"), { title: "decode batch 크기 (스케줄러가 본 값)", yname: "요청", group: G,
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
    if (!dec.querySelector("#dec-g")) dec.innerHTML = `<div class="note" id="dec-note"></div><div class="grid g4" id="dec-g"></div>
      <div class="panel" style="margin-top:8px" id="dec-tbl"></div>`;
    dec.querySelector("#dec-note").textContent = shedNote + " 10초 단위.";
    dec.querySelector("#dec-tbl").innerHTML = `<table><tr><th>클래스</th>${kinds.map(k => `<th class="num">${esc(k)}</th>`).join("")}</tr>
      ${cls.map(c => `<tr><td>${esc(c)}</td>${kinds.map(k => `<td class="num">${fmt(tot[c][k], 0)}</td>`).join("")}</tr>`).join("")}</table>
      ${ds.waited ? `<div class="note" style="margin-top:8px">FluidServe가 결정 전에 요청을 붙잡아 둔 시간(waited, ms):
        ${Object.entries(ds.waited).map(([c, w]) => `${esc(c)} — ${fmt(w.held_pct)}%가 기다렸고, p50 ${fmt(w.q["0.5"], 0)} · p90 ${fmt(w.q["0.9"], 0)} · p99 ${fmt(w.q["0.99"], 0)} · 최대 ${fmt(w.q["1.0"], 0)}`).join("<br>")}</div>` : ""}`;
    const kc = k => (k === "route" ? "#59a14f" : k.startsWith("shed") ? ["#e15759", "#ff9da7", "#9c755f", "#b07aa1"][kinds.filter(x => x.startsWith("shed")).indexOf(k) % 4] : "#bab0ac");
    for (const c of cls) {
      timeBars(chartBox(dec.querySelector("#dec-g"), "short", "dec-" + c), { title: c, yname: "요청/10s", t: ds.decisions.t, group: G,
        series: kinds.map(k => ({ name: k, y: ds.decisions.counts[c][k], color: kc(k) })) });
    }
  }
  if (ds?.occupancy) {
    for (const [inst, byc] of Object.entries(ds.occupancy.instances)) {
      timeLines(chartBox(occ, "short", "occ-" + inst), { title: `인스턴스 ${inst}`, yname: "요청", group: G, stack: true,
        series: Object.entries(byc).map(([c, y]) => ({ name: c, t: ds.occupancy.t, y, color: CLASS_COLORS[c] })) });
    }
  } else occ.innerHTML = `<div class="panel muted">routing 줄이나 request_ids.jsonl이 없다</div>`;
}

export function drawLatency(box, d) {
  if (!d) { box.innerHTML = `<div class="panel muted">채점이 끝나면 나온다</div>`; return; }
  const B = d.budgets, cls = Object.keys(d.cdf);
  cdf(chartBox(box, "", "cdf-ttft"), { title: "TTFT 누적분포 · 점선 = 예산 " + cls.map(c => `${c} ${B[c]?.ttft_s}s`).join(", "), xname: "s", q: d.q,
    series: cls.map(c => ({ name: c, x: d.cdf[c].ttft_s, color: B[c]?.color })),
    marks: cls.map(c => ({ series: c, x: B[c]?.ttft_s, label: `${c} ${B[c]?.ttft_s}s` })),
    xmax: Math.max(...cls.map(c => (B[c]?.ttft_s || 0) * 2.5)) });
  cdf(chartBox(box, "", "cdf-itl"), { title: "요청별 평균 토큰 사이 시간 누적분포 · 점선 = 예산 " + cls.map(c => `${c} ${B[c]?.tbt_ms}ms`).join(", "), xname: "ms", q: d.q,
    series: cls.map(c => ({ name: c, x: d.cdf[c].itl_ms, color: B[c]?.color })),
    marks: cls.map(c => ({ series: c, x: B[c]?.tbt_ms, label: `${c} ${B[c]?.tbt_ms}` })),
    xmax: Math.max(...cls.map(c => (B[c]?.tbt_ms || 0) * 2.5)) });
  outcomeBars(box, d, "ladder95, 최종 채점")
}

// 클래스마다 도착 시각별 결과 막대. 끝난 run(최종 채점)과 진행 중인 run(중간 결과)이 같이 쓴다.
export function outcomeBars(box, d, note) {
  let g = box.querySelector(":scope > .outcome-grid");
  if (!g) { g = document.createElement("div"); g.className = "grid g4 outcome-grid"; g.style.gridColumn = "1 / -1"; box.appendChild(g); }
  for (const c of Object.keys(d.outcome)) {
    timeBars(chartBox(g, "short", "out-" + c), { title: `${c} — 도착 시각별 결과 (${note})`, yname: `요청/${d.bin_s}s`, t: d.t, group: G,
      series: OUTCOME.filter(([k]) => d.outcome[c][k]).map(([k, label, color]) => ({ name: label, y: d.outcome[c][k], color })) });
  }
}
