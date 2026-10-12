// 페이지 등록과 이동. 새 페이지를 추가하려면 pages/에 모듈을 만들고 아래 PAGES에 한 줄을 더한다.
// 페이지 모듈은 { render(el, params) }를 내보내고, 주기적으로 갱신한다면 정리 함수를 반환한다.
import * as status from "./pages/status.js";
import * as runs from "./pages/runs.js";
import * as run from "./pages/run.js";
import * as diff from "./pages/diff.js";

const PAGES = [
  { route: /^#\/status$/, nav: "상태", href: "#/status", mod: status },
  { route: /^#\/runs$/, nav: "결과", href: "#/runs", mod: runs },
  { route: /^#\/run\/(.+)$/, mod: run, keys: ["name"] },
  { route: /^#\/diff\/([^/]+)\/([^/]+)$/, mod: diff, keys: ["a", "b"] },
];

let cleanup = null;

function navigate() {
  const h = location.hash || "#/status";
  const main = document.getElementById("main");
  if (cleanup) { try { cleanup(); } catch (e) { console.error(e); } cleanup = null; }
  echarts.getInstanceByDom && document.querySelectorAll("[_echarts_instance_]").forEach(el => echarts.getInstanceByDom(el)?.dispose());
  main.innerHTML = "";
  for (const p of PAGES) {
    const m = h.match(p.route);
    if (!m) continue;
    const params = {};
    (p.keys || []).forEach((k, i) => (params[k] = decodeURIComponent(m[i + 1])));
    document.querySelectorAll("#nav a").forEach(a => a.classList.toggle("active", h.startsWith(a.getAttribute("href"))
      || (a.getAttribute("href") === "#/runs" && /^#\/(run|diff)\//.test(h))));
    Promise.resolve(p.mod.render(main, params)).then(c => (cleanup = c || null))
      .catch(e => { main.innerHTML = `<div class="panel">페이지 오류: ${e}</div>`; console.error(e); });
    return;
  }
  location.hash = "#/status";
}

document.getElementById("nav").innerHTML = PAGES.filter(p => p.nav).map(p => `<a href="${p.href}">${p.nav}</a>`).join("");
window.addEventListener("hashchange", navigate);
setInterval(() => (document.getElementById("clock").textContent = new Date().toLocaleString("ko-KR")), 1000);
navigate();
