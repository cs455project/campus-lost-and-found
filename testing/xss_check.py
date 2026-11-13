"""Open an item page as a signed-in student and report whether any injected script ran."""
import os
import sys

from playwright.sync_api import sync_playwright

item_id, token = sys.argv[1], sys.argv[2]
with sync_playwright() as p:
    browser = p.chromium.launch()
    ctx = browser.new_context()
    ctx.add_cookies([{"name": "lf_session", "value": token, "domain": "127.0.0.1", "path": "/"}])
    page = ctx.new_page()
    fired = []
    page.on("dialog", lambda d: (fired.append(d.message), d.dismiss()))
    page.goto(os.environ.get("WEB_URL", "http://127.0.0.1:3000") + f"/items/{item_id}")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(1500)
    shown = "<script>" in page.locator("h1").inner_text()
    print(("NO_SCRIPT_RAN" if not fired else "SCRIPT_RAN " + str(fired)) + f"; payload shown as text: {shown}")
    browser.close()
