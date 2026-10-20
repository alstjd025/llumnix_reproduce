// ⑥ 실험 기록: 새 기록(EXP-141~)과 과거 기록(archive, EXP-00~140). 고르면 본문과 그 실험의 run.
import { api, esc, fmt, badge, when, resizableColumns } from "../lib/util.js";
import { noteBox } from "../lib/notes.js";

export async function render(el, { kind, file }) {
  if (kind) return detail(el, kind, file);
  const [d, notes] = await Promise.all([api("/experiments"), api("/notes").catch(() => ({}))]);
  const memo = x => notes.exp?.[x.file.replace(/\.md$/, "")];
  const sec = (k, title, open) => {
    const xs = d.filter(x => x.kind === k);
    return `<details ${open ? "open" : ""}><summary><h2 style="display:inline">${title} (${xs.length})</h2></summary>
      <div class="panel scroll" style="margin-top:8px"><table data-cols="exp-${k}"><tr><th>기록</th><th>제목</th><th>run 시각</th><th class="num">이 서버의 run</th><th>결과 절</th><th>메모</th></tr>
      ${xs.map(x => `<tr><td class="mono"><a href="#/exp/${k}/${encodeURIComponent(x.file)}">${esc(x.file.replace(/\.md$/, ""))}</a></td>
        <td>${esc(x.title)}${x.prefixes?.length ? `<div class="muted mono">세션 접두어 ${x.prefixes.map(esc).join(", ")}</div>` : ""}</td>
        <td class="mono" style="white-space:nowrap">${x.first_ts ? when(x.first_ts) + (x.last_ts - x.first_ts > 600 ? "<br>~ " + when(x.last_ts) : "") : ""}</td>
        <td class="num">${x.n_runs || ""}</td><td>${x.has_result ? badge("ok", "있음") : badge("na", "없음")}</td><td class="muted">${memo(x) ? esc(memo(x).first) : ""}</td></tr>`).join("") || '<tr><td colspan="6" class="muted">없음</td></tr>'}
      </table></div></details>`;
  };
  el.innerHTML = `<h1>실험 기록</h1>
    <div class="note">새 실험은 Agent_applications/agent_motivation_experiment/experiments/EXP-NN_*.md(EXP-141부터)에 <b>실행 전에</b> 판정 규칙과 함께 쓴다.
      기록에 <span class="mono">세션 접두어: \`접두어\`</span> 줄을 넣으면 그 접두어의 run이 여기에 이어진다(과거 기록은 exp번호 관례로 잇는다).</div>
    ${sec("new", "새 기록", true)}${sec("archive", "과거 기록 (archive, 2026-10-11 이전의 사실)", false)}`;
  el.querySelectorAll("table[data-cols]").forEach(t => resizableColumns(t, t.dataset.cols));
}

async function detail(el, kind, file) {
  const d = await api(`/experiments/${kind}/${encodeURIComponent(file)}`);
  const html = window.marked ? window.marked.parse(d.text) : `<pre>${esc(d.text)}</pre>`;
  el.innerHTML = `<h1 class="mono">${esc(file)}</h1>
    ${kind === "archive" ? `<div class="check warn">${badge("warn", "archive")} 2026-10-11 이전의 기록이다. 그 날짜의 사실이고 정정되지 않은 수치가 섞여 있으므로, 수치는 project/evidence.md와 대조한 뒤에 쓴다.</div>` : ""}
    <h2>메모 <span class="muted" style="font-weight:400;font-size:12px">— project/notes/exp/에 파일로 남는다</span></h2><div id="e-note"></div>
    <h2>이 실험의 run (${d.runs.length})</h2>
    <div class="panel scroll">${d.runs.length ? `<table id="e-runs"><tr><th>run</th><th>시작</th><th>arm</th><th class="num">req/s</th><th class="num">모든 도착 %</th><th class="num">admitted %</th><th class="num">거절 %</th><th class="num">goodput</th><th>상태</th></tr>
      ${d.runs.map(r => `<tr class="${r.excluded?.length ? "excluded" : ""}"><td class="mono"><a href="#/run/${encodeURIComponent(r.name)}">${esc(r.name)}</a></td><td class="mono" style="white-space:nowrap">${when(r.start_ts)}</td><td>${esc(r.arm_label || "")}</td>
      <td class="num">${fmt(r.rate, 1)}</td><td class="num">${fmt(r.offered)}</td><td class="num">${fmt(r.admitted)}</td><td class="num">${fmt(r.rejected_pct)}</td><td class="num">${fmt(r.goodput_tok_s, 0)}</td>
      <td>${esc(r.score_state || r.state)}${r.excluded?.length ? " " + badge("warn", "제외 목록") : ""}</td></tr>`).join("")}</table>` : `<span class="muted">이 서버에 run이 없다(논문 run 대부분은 맥 SSD에 있다)</span>`}</div>
    <h2>기록</h2><div class="panel md">${html}</div>`;
  noteBox(el.querySelector("#e-note"), "exp", file.replace(/\.md$/, ""));
  const t = el.querySelector("#e-runs"); if (t) resizableColumns(t, "exp-runs");
}
