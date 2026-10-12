"""화면 확인: 각 페이지를 headless 브라우저로 열어 스크린샷을 찍고, 브라우저 콘솔 오류를 출력한다.

  PLAYWRIGHT_BROWSERS_PATH=/home/nxclab/tools/pw-browsers \
    /home/nxclab/tools/dashboard-venv/bin/python tests/screenshot.py <출력 디렉토리> [hash ...]

콘솔 오류가 하나라도 있으면 종료 코드 1.
"""
import os
import sys

from playwright.sync_api import sync_playwright

out = sys.argv[1]
pages = sys.argv[2:] or ["#/status", "#/runs"]
os.makedirs(out, exist_ok=True)
errors = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    pg.on("console", lambda m: m.type == "error" and errors.append(f"{pg.url}: {m.text}"))
    pg.on("pageerror", lambda e: errors.append(f"{pg.url}: {e}"))
    for h in pages:
        pg.goto(f"http://127.0.0.1:8080/{h}")
        pg.wait_for_timeout(4000)
        fn = os.path.join(out, h.strip("#/").replace("/", "_")[:80] + ".png")
        pg.screenshot(path=fn, full_page=True)
        print("wrote", fn)
    b.close()
for e in errors:
    print("CONSOLE ERROR", e)
sys.exit(1 if errors else 0)
