// ⑤ 논문 대조: claims.yaml의 항목마다 논문 값과 이 서버의 run으로 다시 계산한 값.
import { api, esc, fmt, badge } from "../lib/util.js";

const ST = { "일치": "ok", "차이": "fail", "일부 run만": "warn", "채점 대기": "na", "데이터 없음": "na" };

export async function render(el) {
  el.innerHTML = `<h1>논문 대조</h1>
    <div class="note">dashboard/claims.yaml에 적은 항목(그림 14·13은 그림 CSV에서 자동으로 펼친다)을 근거 run으로 다시 채점해 논문 값과 비교한다.
      ladder95 항목만 있다 — 그림 15·18은 평균 규칙이라 넣지 않았다. 논문 run 대부분은 맥 SSD에 있어서 지금은 "데이터 없음"이 많다.
      정의를 고치려면 claims.yaml을 고친다. 값의 정본은 project/evidence.md다.</div>
    <div class="toolbar" id="cl-sum"></div><div id="cl-body" class="muted">불러오는 중…</div>`;
  const d = await api("/claims");
  const counts = {};
  d.forEach(x => (counts[x.status] = (counts[x.status] || 0) + 1));
  let filt = d.some(x => x.status !== "데이터 없음") ? "있는 것" : "전부";
  const draw = () => {
    el.querySelector("#cl-sum").innerHTML = ["있는 것", "전부", ...Object.keys(ST)].map(k =>
      `<button data-f="${k}" ${k === filt ? 'style="border-color:var(--accent)"' : ""}>${k} ${k === "전부" ? d.length : k === "있는 것" ? d.filter(x => x.status !== "데이터 없음").length : counts[k] || 0}</button>`).join("");
    const rows = d.filter(x => filt === "전부" || (filt === "있는 것" ? x.status !== "데이터 없음" : x.status === filt));
    const groups = [...new Set(rows.map(x => x.group))];
    el.querySelector("#cl-body").className = "";
    el.querySelector("#cl-body").innerHTML = groups.map(g => `<h2>${esc(g)}</h2><div class="panel scroll"><table>
      <tr><th>항목</th><th class="num">논문</th><th class="num">다시 계산</th><th class="num">Δ</th><th class="num">허용</th><th class="num">run</th><th>상태</th></tr>
      ${rows.filter(x => x.group === g).map(x => `<tr><td>${esc(x.label)}<div class="muted mono" style="font-size:11px">${esc(x.metric)} · ${esc(x.runs)} · ${esc(x.evidence)}</div>
        ${x.values.length ? `<details><summary class="muted" style="font-size:11px">run별 값</summary><pre class="mono">${x.values.map(([n, v]) => `${esc(n)}  ${fmt(v, 2)}`).join("\n")}</pre></details>` : ""}</td>
        <td class="num">${fmt(x.paper, 2)}</td><td class="num">${fmt(x.recomputed, 2)}</td><td class="num">${x.delta == null ? "—" : (x.delta >= 0 ? "+" : "") + fmt(x.delta, 2)}</td>
        <td class="num">±${fmt(x.tol, 2)}</td><td class="num">${x.values.length}${x.n ? " / " + x.n : ""}</td><td>${badge(ST[x.status], x.status)}</td></tr>`).join("")}
      </table></div>`).join("") || `<div class="panel muted">해당 항목 없음</div>`;
  };
  el.querySelector("#cl-sum").addEventListener("click", e => { const b = e.target.closest("button"); if (b) { filt = b.dataset.f; draw(); } });
  draw();
}
