// 두 run의 설정 비교: run_config.json을 펼쳐서 다른 항목만.
import { api, esc } from "../lib/util.js";

export async function render(el, { a, b }) {
  const d = await api(`/diff?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`);
  const v = x => esc(typeof x === "string" ? x : JSON.stringify(x));
  el.innerHTML = `<h1>설정 비교</h1>
    <div class="panel"><div>A <a class="mono" href="#/run/${encodeURIComponent(a)}">${esc(a)}</a></div>
      <div>B <a class="mono" href="#/run/${encodeURIComponent(b)}">${esc(b)}</a></div></div>
    <div class="note">run_config.json과 이름에서 읽은 값의 비교. 같은 항목 ${d.same}개는 생략했다.
      ⚠ 스케줄러 바이너리 md5와 플래그는 run 디렉토리에 기록되지 않아 여기서 비교되지 않는다(드라이버 로그에만 있다).</div>
    <div class="panel scroll"><table class="diff"><tr><th>항목</th><th>A</th><th>B</th></tr>
    ${d.differ.map(r => `<tr><td class="mono">${esc(r.key)}</td><td class="a mono">${v(r.a)}</td><td class="b mono">${v(r.b)}</td></tr>`).join("")
      || '<tr><td colspan="3" class="muted">다른 항목이 없다</td></tr>'}</table></div>`;
}
