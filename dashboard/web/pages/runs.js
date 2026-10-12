// ③ 결과: run 목록. 글자로 거르고, 열 머리를 누르면 정렬하고, 두 개를 고르면 설정을 비교한다.
import { api, esc, fmt, badge, worst, ago } from "../lib/util.js";

const COLS = [
  { k: "name", t: "run", f: r => `<a href="#/run/${encodeURIComponent(r.name)}" class="mono">${esc(r.name)}</a>` },
  { k: "session", t: "세션" },
  { k: "arm_label", t: "arm", f: r => `${esc(r.arm_label || "")}${r.arm_label !== r.arm ? `<div class="muted mono">${esc(r.arm || "")}</div>` : ""}` },
  { k: "rate", t: "req/s", num: true, f: r => fmt(r.rate, 1) },
  { k: "duration_min", t: "분", num: true, f: r => fmt(r.duration_min, 0) },
  { k: "offered", t: "모든 도착 %", num: true, f: r => fmt(r.offered), tip: "ladder95, 거절·오류=위반, 미완료 제외" },
  { k: "admitted", t: "admitted %", num: true, f: r => fmt(r.admitted), tip: "ladder95, 거절·미완료를 분모에서 뺀다" },
  { k: "rejected_pct", t: "거절 %", num: true, f: r => fmt(r.rejected_pct) },
  { k: "goodput_tok_s", t: "goodput tok/s", num: true, f: r => fmt(r.goodput_tok_s, 0), tip: "마감을 지킨 토큰 수 / 측정 창" },
  { k: "preemptions", t: "preempt", num: true, f: r => fmt(r.preemptions, 0), tip: "엔진 4대 합. 세션을 넘어 비교하지 않는다" },
  { k: "n", t: "요청", num: true, f: r => fmt(r.n, 0) },
  { k: "_check", t: "검사", f: r => checkCell(r) },
];

function checkCell(r) {
  if (r.score_state !== "scored") return badge("na", { scoring: "채점 중", unscored: "대기", running: "진행 중", incomplete: "불완전", error: "채점 실패" }[r.score_state] || r.score_state);
  const bad = (r.checks || []).filter(c => c.status !== "ok" && c.status !== "na");
  const w = worst(r.checks);
  return badge(w, bad.length ? `${bad.length}건` : "ok") + (bad.length ? `<div class="muted" style="font-size:11px">${bad.map(c => esc(c.title)).join("<br>")}</div>` : "")
    + (r.excluded?.length ? `<div>${badge("warn", "제외 목록")}</div>` : "");
}

export async function render(el) {
  el.innerHTML = `<h1>결과</h1>
    <div class="toolbar"><input type="text" id="q" placeholder="run 이름·세션·arm으로 거르기" size="40">
      <button id="cmp" disabled>고른 두 run의 설정 비교</button><span id="cnt" class="muted"></span></div>
    <div class="note">점수는 논문 주 규칙 ladder95(토큰 i의 마감 = TTFT 예산 + i × 토큰당 예산, 토큰의 95% 이상이 마감 안이면 성공)이고
      analysis_scripts의 채점 함수를 그대로 불러 계산한다. 끝난 run은 백그라운드에서 자동으로 채점된다(run 하나에 약 7초, 한 시간 run은 더 길다).
      한 조건의 1회 측정으로 판정하지 않는다 — 반복 간 편차가 1.4~3.3점이다.</div>
    <div class="panel scroll"><table id="tbl"></table></div>`;
  let rows = [], sortK = null, sortDir = -1;
  const sel = new Set();
  const q = el.querySelector("#q"), tbl = el.querySelector("#tbl"), cmp = el.querySelector("#cmp");
  q.value = sessionStorage.getItem("runs.q") || "";
  const draw = () => {
    const s = q.value.trim().toLowerCase();
    try { sessionStorage.setItem("runs.q", q.value); } catch (e) { }
    let rs = rows.filter(r => !s || [r.name, r.session, r.arm, r.arm_label].join(" ").toLowerCase().includes(s));
    if (sortK) rs = [...rs].sort((a, b) => ((a[sortK] ?? -Infinity) > (b[sortK] ?? -Infinity) ? 1 : -1) * sortDir);
    el.querySelector("#cnt").textContent = `${rs.length} / ${rows.length} run`;
    tbl.innerHTML = `<tr><th></th>${COLS.map(c => `<th data-k="${c.k}" class="${c.num ? "num" : ""}" title="${esc(c.tip || "")}">${c.t}${sortK === c.k ? (sortDir > 0 ? " ▲" : " ▼") : ""}</th>`).join("")}</tr>` +
      rs.map(r => `<tr class="${r.excluded?.length ? "excluded" : ""}"><td><input type="checkbox" data-n="${esc(r.name)}" ${sel.has(r.name) ? "checked" : ""}></td>
        ${COLS.map(c => `<td class="${c.num ? "num" : ""}">${c.f ? c.f(r) : esc(r[c.k] ?? "")}</td>`).join("")}</tr>`).join("");
    cmp.disabled = sel.size !== 2;
  };
  tbl.addEventListener("click", e => {
    const th = e.target.closest("th[data-k]");
    if (th) { const k = th.dataset.k; sortDir = sortK === k ? -sortDir : -1; sortK = k; draw(); }
    const cb = e.target.closest("input[type=checkbox]");
    if (cb) { cb.checked ? sel.add(cb.dataset.n) : sel.delete(cb.dataset.n); cmp.disabled = sel.size !== 2; }
  });
  q.addEventListener("input", draw);
  cmp.addEventListener("click", () => { const [a, b] = [...sel]; location.hash = `#/diff/${encodeURIComponent(a)}/${encodeURIComponent(b)}`; });
  const load = async () => { rows = await api("/runs"); draw(); };
  await load();
  const t = setInterval(load, 20000);
  return () => clearInterval(t);
}
