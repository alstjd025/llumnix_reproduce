// ④ 비교: arm × 도착률 격자. 칸마다 반복 평균 [최소~최대] (n), 기준 arm과의 차이와 판정.
import { api, esc, fmt, badge } from "../lib/util.js";
import { xyLines, ENGINE_COLORS } from "../lib/charts.js";

const LABEL = { offered: "모든 도착 기준 달성률 (%)", admitted: "admitted 기준 달성률 (%)", rejected_pct: "거절률 (%)",
  goodput_tok_s: "token goodput (tok/s)", all_arrivals: "모든 도착 기준, 미완료 포함 (%)" };
const DIGITS = { goodput_tok_s: 0 };

export async function render(el) {
  const saved = (() => { try { return JSON.parse(sessionStorage.getItem("cmp") || "{}"); } catch (e) { return {}; } })();
  el.innerHTML = `<h1>비교</h1>
    <div class="toolbar">
      <input type="text" id="q" size="50" placeholder="run 이름 glob, 공백으로 여러 개 (예: *exp108r[12]_*)" value="${esc(saved.q || "rpro1010")}">
      <select id="m">${Object.entries(LABEL).map(([k, v]) => `<option value="${k}" ${k === (saved.m || "offered") ? "selected" : ""}>${v}</option>`).join("")}</select>
      <select id="ref"></select>
      <label><input type="checkbox" id="ex" ${saved.ex === false ? "" : "checked"}> 제외 목록의 run 빼기</label>
      <button id="go">보기</button></div>
    <div class="note">칸 = (arm, 도착률). 값은 반복 평균, 괄호는 [최소~최대]와 반복 수. 기준 arm과의 평균 차이가 두 칸의 반복 범위 중 큰 것 이하이면
      "차이 없음"이다. 반복이 2회면 범위가 편차를 작게 잡으므로, 판정 문턱으로는 기록된 편차(1.4~3.3점, 세션 간 4.6점)도 같이 본다.</div>
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
