// ④ 비교: arm × 도착률 격자. 칸마다 반복 평균 [최소~최대] (n), 기준 arm과의 차이와 판정.
import { api, esc, fmt, badge } from "../lib/util.js";
import { xyLines, timeLines, cdf, ENGINE_COLORS } from "../lib/charts.js";
import { chartBox } from "../lib/runviews.js";

const LABEL = { offered: "모든 도착 기준 달성률 (%)", admitted: "admitted 기준 달성률 (%)", rejected_pct: "거절률 (%)",
  goodput_tok_s: "token goodput (tok/s)", all_arrivals: "모든 도착 기준, 미완료 포함 (%)" };
const DIGITS = { goodput_tok_s: 0 };

async function renderGrid(el) {
  const saved = (() => { try { return JSON.parse(sessionStorage.getItem("cmp") || "{}"); } catch (e) { return {}; } })();
  el.innerHTML = `
    <div class="toolbar">
      <input type="text" id="q" size="50" placeholder="run 이름 glob, 공백으로 여러 개 (예: *exp108r[12]_*)" value="${esc(saved.q || "rpro1010")}">
      <select id="m">${Object.entries(LABEL).map(([k, v]) => `<option value="${k}" ${k === (saved.m || "offered") ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select id="ref"></select>
      <label><input type="checkbox" id="ex" ${saved.ex === false ? "" : "checked"}> 제외 목록의 run 빼기</label>
      <button id="go">보기</button></div>
    <div class="note">칸 = (arm, 도착률). 값은 반복 평균, 괄호는 [최소~최대]와 반복 수. 기준 arm과의 평균 차이가 두 칸의 반복 범위 중 큰 것 이하이면
      "차이 없음"으로 판정합니다. 반복이 2회이면 범위가 편차를 작게 잡으므로, 판정 문턱으로는 기록된 편차(1.4~3.3점, 세션 간 4.6점)도 함께 확인합니다.</div>
    <div id="cmp-body"></div>`;
  const $ = s => el.querySelector(s);
  const go = async () => {
    const q = $("#q").value.trim(), m = $("#m").value, ex = $("#ex").checked;
    try { sessionStorage.setItem("cmp", JSON.stringify({ q, m, ex, ref: $("#ref").value })); } catch (e) { }
    const d = await api(`/compare?q=${encodeURIComponent(q)}&ref=${encodeURIComponent($("#ref").value || saved.ref || "")}&exclude_listed=${ex}`);
    // 열 하나 = (arm, 측정 시간). 같은 arm이라도 측정 시간이 다르면 다른 조건이다
    const ck = c => `${c.arm}|${c.duration_min}`;
    const arms = [...new Map(d.cells.map(c => [ck(c), c.arm_label])).entries()];
    const refArms = [...new Map(d.cells.map(c => [c.arm, c.arm_label.replace(/ \(.*분\)$/, "")])).entries()];
    $("#ref").innerHTML = refArms.map(([a, l]) => `<option value="${esc(a)}" ${a === d.ref ? "selected" : ""}>기준: ${esc(l)}</option>`).join("");
    const rates = [...new Set(d.cells.map(c => c.rate))].sort((a, b) => (a ?? -1) - (b ?? -1));
    const cell = (a, r) => d.cells.find(c => ck(c) === a && c.rate === r);
    const dg = DIGITS[m] ?? 1;
    const td = c => {
      if (!c || !c[m]) return `<td class="num muted">—</td>`;
      const v = c[m], vr = c.vs_ref[m];
      return `<td class="num"><b>${fmt(v.mean, dg)}</b> <span class="muted">[${fmt(v.min, dg)}~${fmt(v.max, dg)}] n=${c.n}</span>
        ${vr ? `<div>${badge(vr.verdict === "차이" ? "warn" : vr.verdict === "차이 없음" ? "ok" : "na", vr.verdict)}
          <span class="muted">Δ ${vr.diff >= 0 ? "+" : ""}${fmt(vr.diff, dg)}${vr.ratio ? ` · ×${fmt(vr.ratio, 2)}` : ""}</span></div>` : ""}</td>`;
    };
    $("#cmp-body").innerHTML = `
      ${d.warnings.map(w => `<div class="check warn">${badge("warn")} ${esc(w)}</div>`).join("")}
      <div class="panel" style="margin-top:8px"><div class="chart tall" id="cmp-chart"></div></div>
      <h2>${esc(LABEL[m])}</h2>
      <div class="panel scroll"><table><tr><th>req/s</th>${arms.map(([a, l]) => `<th class="num">${esc(l)}<div class="mono muted">${esc(a.split("|")[0])}</div></th>`).join("")}</tr>
        ${rates.map(r => `<tr><td>${r == null ? "trace" : fmt(r, 1)}</td>${arms.map(([a]) => td(cell(a, r))).join("")}</tr>`).join("")}</table></div>
      ${d.skipped.length ? `<h2>뺀 run (${d.skipped.length})</h2><div class="panel"><pre class="mono">${d.skipped.map(s => esc(s.name + "  — " + s.why)).join("\n")}</pre></div>` : ""}`;
    const ser = [];
    arms.forEach(([a, l], i) => {
      const cs = d.cells.filter(c => ck(c) === a && c[m] && c.rate != null).sort((x, y) => x.rate - y.rate);
      ser.push({ name: l, kind: "line", color: ENGINE_COLORS[i], pts: cs.map(c => [c.rate, c[m].mean]) });
      ser.push({ name: l + " (반복)", kind: "scatter", color: ENGINE_COLORS[i], pts: cs.flatMap(c => c[m].vals.map(v => [c.rate, v])) });
    });
    if (ser.some(s => s.pts.length)) xyLines($("#cmp-chart"), { title: LABEL[m] + " — 선은 평균, 점은 반복 하나", xname: "도착률 (req/s)", yname: "", series: ser });
    else $("#cmp-chart").parentElement.style.display = "none";
  };
  $("#go").addEventListener("click", go);
  $("#q").addEventListener("keydown", e => e.key === "Enter" && go());
  ["#m", "#ref", "#ex"].forEach(s => $(s).addEventListener("change", go));
  await go();
}


// ── 두 run 비교 ────────────────────────────────────────────────────────────
// 끝나고 채점된 run 두 개를 골라 점수·클래스별 결과·지연 분위수를 한 표에, 시간에 따른 그래프를 한 그래프에 겹쳐 그립니다.
const RUN_COLORS = ["#4e79a7", "#e15759"];
const G2 = "pair-time";
const SAVE = "cmp-pair";

function binned(t, y, bin) {             // 1초 간격 계열을 bin초 평균으로
  const m = new Map();
  t.forEach((x, i) => { if (y[i] == null) return; const k = Math.floor(x / bin); const c = m.get(k) || [0, 0]; c[0] += y[i]; c[1]++; m.set(k, c); });
  const ks = [...m.keys()].sort((a, b) => a - b);
  return { t: ks.map(k => k * bin), y: ks.map(k => m.get(k)[0] / m.get(k)[1]) };
}
function fleet(engines, key, how, bin = 10) {  // 엔진 계열을 bin초로 맞춘 뒤 합 또는 평균
  const per = Object.values(engines).map(e => binned(e.t, e[key] || [], bin));
  const acc = new Map();
  per.forEach(s => s.t.forEach((x, i) => { const c = acc.get(x) || [0, 0]; c[0] += s.y[i]; c[1]++; acc.set(x, c); }));
  const ts = [...acc.keys()].sort((a, b) => a - b);
  return { t: ts, y: ts.map(x => how === "sum" ? acc.get(x)[0] : acc.get(x)[0] / acc.get(x)[1]) };
}
function rolling(arr, w) { return arr.map((_, i) => { let s = 0, n = 0; for (let j = Math.max(0, i - w + 1); j <= i; j++) { s += arr[j]; n++; } return s / n; }); }

function metricRows(A, B, LA, LB) {
  const sa = A.score || {}, sb = B.score || {};
  const pc = (s, c, k) => s.per_class?.[c]?.[k];
  const q = (L, c, key, i) => L?.cdf?.[c]?.[key]?.[i];
  const rows = [
    ["전체", "모든 도착 기준 달성률 (%)", sa.all_arrivals, sb.all_arrivals, 1],
    ["전체", "admitted 기준 달성률 (%)", sa.admitted, sb.admitted, 2],
    ["전체", "거절률 (%)", sa.rejected_pct, sb.rejected_pct, 1],
    ["전체", "token goodput (tok/s)", sa.goodput_tok_s, sb.goodput_tok_s, 0],
    ["전체", "preemption (엔진 합)", A.preemptions, B.preemptions, 0],
  ];
  for (const c of ["chat", "deepresearch", "swe"]) {
    rows.push([c, "모든 도착 기준 달성률 (%)", pc(sa, c, "all_arrivals"), pc(sb, c, "all_arrivals"), 1]);
    rows.push([c, "admitted 기준 달성률 (%)", pc(sa, c, "admitted"), pc(sb, c, "admitted"), 2]);
    rows.push([c, "거절률 (%)", pc(sa, c, "rejected_pct"), pc(sb, c, "rejected_pct"), 1]);
    rows.push([c, "TTFT p50 / p90 / p99 (s)", [50, 90, 99].map(i => q(LA, c, "ttft_s", i)), [50, 90, 99].map(i => q(LB, c, "ttft_s", i)), 2]);
    rows.push([c, "평균 토큰 사이 시간 p50 / p90 / p99 (ms)", [50, 90, 99].map(i => q(LA, c, "itl_ms", i)), [50, 90, 99].map(i => q(LB, c, "itl_ms", i)), 1]);
  }
  return rows;
}

function cellVal(v, d) { return Array.isArray(v) ? v.map(x => fmt(x, d)).join(" / ") : fmt(v, d); }
function cellDiff(a, b, d) {
  if (Array.isArray(a)) return a.map((x, i) => (x == null || b[i] == null) ? "—" : `${b[i] - x >= 0 ? "+" : ""}${fmt(b[i] - x, d)}`).join(" / ");
  if (a == null || b == null) return "—";
  return `${b - a >= 0 ? "+" : ""}${fmt(b - a, d)}`;
}

async function renderPair(el) {
  const saved = (() => { try { return JSON.parse(sessionStorage.getItem(SAVE) || "{}"); } catch (e) { return {}; } })();
  const list = (await api("/runs")).filter(r => r.state === "done" && r.score_state === "scored");
  const opt = list.map(r => `<option value="${esc(r.name)}">${esc(r.arm_label)} · ${esc(r.session)}</option>`).join("");
  const def = (k, i) => saved[k] || list[i]?.name || "";
  el.innerHTML = `
    <div class="toolbar">
      <label>A <input type="text" id="ra" size="52" list="runlist" value="${esc(def("a", 1))}"></label>
      <label>B <input type="text" id="rb" size="52" list="runlist" value="${esc(def("b", 0))}"></label>
      <button id="pgo">비교</button>
      <datalist id="runlist">${opt}</datalist></div>
    <div class="note">끝나고 채점된 run 두 개를 비교합니다. 표의 Δ는 B − A입니다. 시간축은 각 run의 수집 시작을 0으로 맞춘 것이고,
      엔진 그래프는 엔진 4대의 합(요청 수·토큰·preemption) 또는 평균(KV·토큰 사이 시간·prefix 적중률)을 10초 단위로 보여 줍니다.
      클래스별 달성률·거절률은 도착 시각 10초 단위 결과를 1분 이동 평균한 값입니다.</div>
    <div id="pair-body"></div>`;
  const $ = s => el.querySelector(s);
  const go = async () => {
    const a = $("#ra").value.trim(), b = $("#rb").value.trim();
    try { sessionStorage.setItem(SAVE, JSON.stringify({ a, b })); } catch (e) { }
    const body = $("#pair-body");
    if (!a || !b) { body.innerHTML = `<div class="panel muted">run 두 개를 고르면 비교합니다.</div>`; return; }
    body.innerHTML = `<div class="panel muted">불러오는 중입니다.</div>`;
    const get = u => api(u).catch(e => ({ error: String(e) }));
    const [A, B, LA, LB, SA, SB] = await Promise.all([get(`/runs/${encodeURIComponent(a)}`), get(`/runs/${encodeURIComponent(b)}`),
      get(`/runs/${encodeURIComponent(a)}/latency`), get(`/runs/${encodeURIComponent(b)}/latency`),
      get(`/runs/${encodeURIComponent(a)}/series`), get(`/runs/${encodeURIComponent(b)}/series`)]);
    const lab = (R, n) => `${R.info?.arm_label || ""} · ${n}`;
    const rows = metricRows(A, B, LA.error ? null : LA, LB.error ? null : LB);
    body.innerHTML = `
      <div class="panel scroll"><table>
        <tr><th>구분</th><th>지표</th><th class="num" style="color:${RUN_COLORS[0]}">A</th><th class="num" style="color:${RUN_COLORS[1]}">B</th><th class="num">Δ (B − A)</th></tr>
        <tr><td></td><td class="muted">run</td><td class="num mono"><a href="#/run/${encodeURIComponent(a)}">${esc(a)}</a><div class="muted">${esc(A.info?.arm_label || "")}</div></td>
          <td class="num mono"><a href="#/run/${encodeURIComponent(b)}">${esc(b)}</a><div class="muted">${esc(B.info?.arm_label || "")}</div></td>
          <td class="num"><a href="#/diff/${encodeURIComponent(a)}/${encodeURIComponent(b)}">설정 비교</a></td></tr>
        ${rows.map(([g, k, va, vb, d]) => `<tr><td>${esc(g)}</td><td>${esc(k)}</td><td class="num">${cellVal(va, d)}</td><td class="num">${cellVal(vb, d)}</td><td class="num">${cellDiff(va, vb, d)}</td></tr>`).join("")}
      </table></div>
      <h2>클래스별 결과 (시간축)</h2><div class="grid g2" id="pair-cls"></div>
      <h2>지연 분포</h2><div class="grid g2" id="pair-lat"></div>
      <h2>엔진과 게이트웨이 (시간축)</h2><div class="grid g2" id="pair-eng"></div>`;
    const two = (fa, fb) => [{ name: "A", ...fa, color: RUN_COLORS[0] }, { name: "B", ...fb, color: RUN_COLORS[1] }];
    // 클래스별 달성률·거절률
    const cls = $("#pair-cls");
    if (!LA.error && !LB.error) {
      for (const c of Object.keys(LA.outcome).filter(c => LB.outcome[c])) {
        const ser = (L, k) => { const o = L.outcome[c]; const tot = o.met.map((_, i) => o.met[i] + o.missed[i] + o.rejected[i] + o.errored[i]);
          const num = k === "met" ? o.met : o.rejected; return { t: L.t, y: rolling(num, 6).map((v, i) => { const d = rolling(tot, 6)[i]; return d ? 100 * v / d : null; }) }; };
        timeLines(chartBox(cls, "", "met-" + c), { title: `${c} — 모든 도착 기준 달성률 (%)`, yname: "%", ymax: 100, group: G2, series: two(ser(LA, "met"), ser(LB, "met")) });
        timeLines(chartBox(cls, "", "rej-" + c), { title: `${c} — 거절률 (%)`, yname: "%", ymax: 100, group: G2, series: two(ser(LA, "rej"), ser(LB, "rej")) });
      }
      const lat = $("#pair-lat");
      for (const c of Object.keys(LA.cdf).filter(c => LB.cdf[c])) {
        const B_ = LA.budgets?.[c] || {};
        cdf(chartBox(lat, "", "ttft-" + c), { title: `${c} — TTFT 누적분포 (예산 ${B_.ttft_s ?? "?"} s)`, xname: "s", q: LA.q,
          series: [{ name: "A", x: LA.cdf[c].ttft_s, color: RUN_COLORS[0] }, { name: "B", x: LB.cdf[c].ttft_s, color: RUN_COLORS[1] }],
          marks: B_.ttft_s ? [{ x: B_.ttft_s, label: "예산" }] : [], xmax: B_.ttft_s ? B_.ttft_s * 2 : undefined });
        cdf(chartBox(lat, "", "itl-" + c), { title: `${c} — 요청별 평균 토큰 사이 시간 누적분포 (예산 ${B_.tbt_ms ?? "?"} ms)`, xname: "ms", q: LA.q,
          series: [{ name: "A", x: LA.cdf[c].itl_ms, color: RUN_COLORS[0] }, { name: "B", x: LB.cdf[c].itl_ms, color: RUN_COLORS[1] }],
          marks: B_.tbt_ms ? [{ x: B_.tbt_ms, label: "예산" }] : [], xmax: B_.tbt_ms ? B_.tbt_ms * 1.6 : undefined });
      }
    } else cls.innerHTML = `<div class="panel muted">채점 결과가 없는 run이 있어 클래스별 그래프를 그리지 못했습니다.</div>`;
    // 엔진·게이트웨이
    const eng = $("#pair-eng");
    if (SA.engines && SB.engines) {
      const E = [["kv_pct", "mean", "KV cache 사용률 (엔진 평균, %)", "%"], ["running", "sum", "running 요청 수 (엔진 합)", "요청"],
        ["waiting", "sum", "waiting 요청 수 (엔진 큐 합)", "요청"], ["gen_tok_per_s", "sum", "생성 토큰 (엔진 합)", "tok/s"],
        ["itl_ms", "mean", "토큰 사이 시간, 엔진 보고값 (엔진 평균)", "ms"], ["preempt_per_s", "sum", "preemption (엔진 합)", "회/s"],
        ["prefix_hit_pct", "mean", "prefix cache 적중률 (엔진 평균, %)", "%"]];
      for (const [k, how, title, y] of E) timeLines(chartBox(eng, "", "e-" + k), { title, yname: y, group: G2, series: two(fleet(SA.engines, k, how), fleet(SB.engines, k, how)) });
      if (SA.gateway && SB.gateway) {
        const gw = (S, k) => binned(S.gateway.t, S.gateway[k], 10);
        timeLines(chartBox(eng, "", "gw-p"), { title: "게이트웨이 대기 요청", yname: "요청", group: G2, series: two(gw(SA, "pending"), gw(SB, "pending")) });
      }
    } else eng.innerHTML = `<div class="panel muted">server_metrics가 없는 run이 있습니다.</div>`;
  };
  $("#pgo").addEventListener("click", go);
  ["#ra", "#rb"].forEach(x => $(x).addEventListener("keydown", e => e.key === "Enter" && go()));
  await go();
}

export async function render(el) {
  let mode = "pair";
  try { mode = sessionStorage.getItem("cmp-mode") || "pair"; } catch (e) { }
  el.innerHTML = `<h1>비교</h1>
    <div class="toolbar"><button id="m-pair">두 run 비교</button><button id="m-grid">arm × 도착률 격자</button></div>
    <div id="cmp-mode"></div>`;
  const box = el.querySelector("#cmp-mode");
  const show = async m => {
    try { sessionStorage.setItem("cmp-mode", m); } catch (e) { }
    el.querySelector("#m-pair").classList.toggle("active", m === "pair");
    el.querySelector("#m-grid").classList.toggle("active", m === "grid");
    box.innerHTML = "";
    if (m === "pair") await renderPair(box); else await renderGrid(box);
  };
  el.querySelector("#m-pair").addEventListener("click", () => show("pair"));
  el.querySelector("#m-grid").addEventListener("click", () => show("grid"));
  await show(mode);
}
