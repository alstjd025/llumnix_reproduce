// 사용자 메모 상자. run 페이지와 실험 기록 페이지에 붙는다. 저장하면 project/notes/<runs|exp>/<이름>.md 파일이 된다.
// Ctrl/Cmd+S로도 저장한다. 저장하지 않은 채 페이지를 떠나려 하면 묻는다.
import { esc, ago } from "./util.js";

export async function noteBox(el, kind, key) {
  const url = `/api/notes/${kind}/${encodeURIComponent(key)}`;
  let base = null, saved = "";
  el.innerHTML = `<div class="panel note-box">
      <textarea rows="5" placeholder="이 ${kind === "run" ? "run" : "실험"}에 대한 메모(Markdown). 저장하면 파일로 남는다"></textarea>
      <div class="toolbar"><button>저장</button><span class="muted st"></span><span class="muted mono path"></span></div></div>`;
  const ta = el.querySelector("textarea"), btn = el.querySelector("button"), st = el.querySelector(".st"), pth = el.querySelector(".path");
  const show = (d) => {
    base = d.mtime; saved = d.text;
    st.textContent = d.exists ? `저장됨 · ${ago(Date.now() / 1000 - d.mtime)}` : "메모 없음";
    pth.textContent = d.path || "";
  };
  const load = async () => {
    const r = await fetch(url, { cache: "no-store" });
    const d = await r.json(); ta.value = d.text; show(d);
    ta.rows = Math.max(5, Math.min(30, d.text.split("\n").length + 1));
  };
  const save = async () => {
    btn.disabled = true; st.textContent = "저장 중…";
    try {
      const r = await fetch(url, { method: "PUT", headers: { "Content-Type": "application/json" },
                                   body: JSON.stringify({ text: ta.value, base_mtime: base }) });
      if (r.status === 409) { st.innerHTML = `<b style="color:var(--fail)">저장 안 됨</b> — 다른 탭에서 바뀌었다. 지금 내용을 복사해 둔 뒤 새로고침한다`; return; }
      if (!r.ok) throw new Error(await r.text());
      show(await r.json());
    } catch (e) { st.innerHTML = `<b style="color:var(--fail)">저장 실패</b> ${esc(e.message)}`; }
    finally { btn.disabled = false; }
  };
  btn.onclick = save;
  ta.addEventListener("input", () => { if (ta.value !== saved) st.textContent = "저장 안 됨"; });
  ta.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key === "s") { e.preventDefault(); save(); } });
  const guard = e => { if (ta.isConnected && ta.value !== saved) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", guard);
  await load();
}
