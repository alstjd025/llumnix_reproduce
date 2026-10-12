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
