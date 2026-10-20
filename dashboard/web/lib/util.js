// 페이지들이 같이 쓰는 작은 도구: API 호출, 숫자 형식, 검사 결과 표시.

export async function api(path) {
  const r = await fetch("/api" + path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path}: ${r.status} ${await r.text()}`);
  return r.json();
}

export const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

export function fmt(x, d = 1) {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  return Number(x).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
}

export const badge = (st, text) => `<span class="badge b-${st}">${esc(text ?? st)}</span>`;

const ORDER = { fail: 3, warn: 2, na: 1, ok: 0 };
export function worst(checks) {
  return (checks || []).reduce((w, c) => (ORDER[c.status] > ORDER[w] ? c.status : w), "ok");
}

// 검사 하나: 판정, 제목, 요약. 펼치면 이 검사가 생긴 이유(why)와 근거 행(evidence).
export function checkHtml(c, open = false) {
  return `<div class="check ${c.status}"><details ${open ? "open" : ""}>
    <summary>${badge(c.status)} <b>${esc(c.title)}</b> — ${esc(c.summary)}</summary>
    <div class="why">왜 보는가: ${esc(c.why)}</div>
    ${c.evidence?.length ? `<pre>${c.evidence.map(esc).join("\n")}</pre>` : ""}
  </details></div>`;
}

export function ago(sec) {
  if (sec == null) return "—";
  if (sec < 90) return `${Math.round(sec)}초 전`;
  if (sec < 5400) return `${Math.round(sec / 60)}분 전`;
  if (sec < 172800) return `${Math.round(sec / 3600)}시간 전`;
  return `${Math.round(sec / 86400)}일 전`;
}

// 시각(epoch 초) → "10-20 16:37". 브라우저의 시간대로 보여 준다.
export function when(ts, withYear = false) {
  if (!ts) return "—";
  const d = new Date(ts * 1000), p = n => String(n).padStart(2, "0");
  return `${withYear ? d.getFullYear() + "-" : ""}${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

// 표의 열 너비를 마우스로 끌어 바꾼다. 머리 칸(th) 오른쪽 끝을 끈다. 너비는 브라우저에 저장해 다음에도 쓴다(storeKey별).
// 표를 innerHTML로 다시 그리는 페이지는 그릴 때마다 다시 부른다. th에 data-k가 있으면 그것을, 없으면 순서를 열 이름으로 쓴다.
export function resizableColumns(table, storeKey) {
  const ths = [...table.querySelectorAll("tr:first-child > th")];
  if (!ths.length) return;
  const key = (th, i) => th.dataset.k || String(i);
  let saved = {};
  try { saved = JSON.parse(localStorage.getItem("colw." + storeKey) || "{}"); } catch (e) { }
  const apply = () => {
    if (!Object.keys(saved).length) { table.style.tableLayout = ""; table.style.width = ""; table.classList.remove("resized"); return; }
    table.classList.add("resized");
    ths.forEach((th, i) => { th.style.width = (saved[key(th, i)] || th.offsetWidth) + "px"; });
    table.style.tableLayout = "fixed";
    table.style.width = ths.reduce((a, th) => a + parseFloat(th.style.width), 0) + "px";
  };
  apply();
  ths.forEach((th, i) => {
    th.style.position = "relative";
    const g = document.createElement("span");
    g.className = "col-grip"; g.title = "끌어서 너비 조절 · 두 번 누르면 원래대로";
    th.appendChild(g);
    g.addEventListener("click", e => e.stopPropagation());     // 머리 칸을 누르면 정렬하는 페이지가 있다
    g.addEventListener("dblclick", e => {
      e.stopPropagation(); saved = {};
      try { localStorage.removeItem("colw." + storeKey); } catch (e2) { }
      ths.forEach(t => { t.style.width = ""; }); apply();
    });
    g.addEventListener("mousedown", e => {
      e.preventDefault(); e.stopPropagation();
      if (!Object.keys(saved).length) ths.forEach((t, j) => { saved[key(t, j)] = t.offsetWidth; });
      const x0 = e.clientX, w0 = th.offsetWidth;
      const move = ev => { saved[key(th, i)] = Math.max(40, w0 + ev.clientX - x0); apply(); };
      const up = () => {
        document.removeEventListener("mousemove", move); document.removeEventListener("mouseup", up);
        try { localStorage.setItem("colw." + storeKey, JSON.stringify(saved)); } catch (e2) { }
      };
      document.addEventListener("mousemove", move); document.addEventListener("mouseup", up);
    });
  });
}
