"""System tests: the whole TraceBack stack, driven through a real browser.

Stack under test (started separately, production mode):
  Next.js build (next start, :3000)  ->  /api proxy  ->  FastAPI (uvicorn, :8000)
  + background worker (python -m app.worker)  ->  PostgreSQL 16 (fresh database)
Emails run in console mode; sign-in codes are read from the backend log.

Run (Linux/macOS):  bash start_stack.sh  then  python system_tests.py
(writes results.json and screenshots/; needs: pip install playwright pillow && playwright install chromium)
"""

import json
import os
import re
import time
import traceback
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from PIL import Image, ImageDraw
from playwright.sync_api import expect, sync_playwright

BASE = os.environ.get("WEB_URL", "http://127.0.0.1:3000")
OUT = Path(__file__).resolve().parent
BACKEND_LOG = Path(os.environ.get("BACKEND_LOG", OUT / "run" / "backend.log"))
WORKER_LOG = Path(os.environ.get("WORKER_LOG", OUT / "run" / "worker.log"))
SHOTS = OUT / "screenshots"
SHOTS.mkdir(exist_ok=True)
results = []
ctx_data = {}

STUDENT_A = "owner.sys@iitk.ac.in"     # loses a calculator
STUDENT_B = "finder.sys@iitk.ac.in"    # finds it
ADMIN = "admin.test@gmail.com"         # made admin with: python -m app.cli make-admin
GUARD = "guard.sys@gmail.com"
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
TODAY = date.today().isoformat()


def case(cid, title):
    def wrap(fn):
        def run(*args):
            start = time.time()
            try:
                note = fn(*args) or ""
                results.append({"id": cid, "title": title, "result": "PASS", "seconds": round(time.time() - start, 1), "note": note})
                print(f"PASS {cid} {title} {note}")
            except Exception as exc:  # noqa: BLE001
                results.append({"id": cid, "title": title, "result": "FAIL", "seconds": round(time.time() - start, 1),
                                "note": f"{type(exc).__name__}: {str(exc)[:300]}"})
                print(f"FAIL {cid} {title}\n{traceback.format_exc()[-1500:]}")
                for p in args:
                    if hasattr(p, "screenshot"):
                        p.screenshot(path=str(SHOTS / f"{cid}-failure.png"), full_page=True)
        return run
    return wrap


def code_for(email, after=0):
    """The newest sign-in code printed for this address (console email mode)."""
    for _ in range(40):
        text = BACKEND_LOG.read_text(errors="ignore")
        found = re.findall(rf"EMAIL to: {re.escape(email)}\nSubject: (\d{{6}}) is your", text)
        if len(found) > after:
            return found[-1]
        time.sleep(0.25)
    raise AssertionError(f"no sign-in code printed for {email}")


def codes_sent(email):
    return len(re.findall(rf"EMAIL to: {re.escape(email)}\nSubject: \d{{6}} is your", BACKEND_LOG.read_text(errors="ignore")))


def sign_in(page, email, staff=False):
    before = codes_sent(email)
    page.goto(f"{BASE}/login")
    if staff:
        page.get_by_text("Admin / Guard").click()
    page.fill("#email", email)
    page.locator("form button[type=submit]").click()
    page.wait_for_selector("#code")
    page.fill("#code", code_for(email, before))
    page.locator("form button[type=submit]").click()
    page.wait_for_url(re.compile(r"/(onboarding)?$"), timeout=15000)


def onboard(page, name, roll=None, phone="9876543210"):
    page.wait_for_url(re.compile(r"/onboarding$"))
    page.fill("#full_name", name)
    if roll:
        page.fill("#roll_number", roll)
    page.fill("#phone", phone)
    page.get_by_role("button", name="Save profile").click()
    page.wait_for_url(re.compile(r"/$"), timeout=15000)


def fill_report(page, title, desc, colour, brand, place, day, photo=None, office=None):
    page.fill("#title", title)
    page.select_option("#category_id", label="Electronics")
    page.fill("#colour", colour)
    page.fill("#brand", brand)
    page.fill("#description", desc)
    page.fill("#location_text", place)
    page.fill("#happened_on", day)
    if office:
        page.select_option("#dropoff_office_id", label=office)
    if photo:
        page.set_input_files("#photos", str(photo))


def make_photo():
    path = OUT / "calculator.jpg"
    img = Image.new("RGB", (640, 480), (30, 30, 30))
    d = ImageDraw.Draw(img)
    d.rectangle((180, 60, 460, 420), fill=(10, 10, 10), outline=(200, 200, 200), width=4)
    for r in range(5):
        for c in range(4):
            d.rectangle((200 + c * 62, 200 + r * 42, 250 + c * 62, 230 + r * 42), fill=(90, 90, 90))
    img.save(path, "JPEG")
    return path


# ---------------- cases ----------------

@case("SYS-01", "Health check through the website proxy, and sign-in required for private pages")
def t01(page):
    with urllib.request.urlopen(f"{BASE}/api/health", timeout=5) as r:
        body = json.load(r)
    assert body == {"status": "ok", "database": "ok"}, body
    page.goto(f"{BASE}/my-reports")
    page.wait_for_url(re.compile(r"/login$"), timeout=15000)
    page.screenshot(path=str(SHOTS / "SYS-01-login.png"))
    return "health ok; /my-reports redirected to /login"


@case("SYS-02", "Student signs in with an emailed code and completes onboarding")
def t02(page):
    sign_in(page, STUDENT_A)
    onboard(page, "Owner Sys", roll="230001")
    expect(page.locator("body")).to_contain_text("Owner")
    page.screenshot(path=str(SHOTS / "SYS-02-home.png"))
    return "code read from email log; profile saved; home page shown"


@case("SYS-03", "Student reports a lost item through the form")
def t03(page):
    page.goto(f"{BASE}/report/lost")
    fill_report(page, "Black Casio calculator", "fx-991EX scientific calculator with a scratch on the lid",
                "Black", "Casio", "Lecture Hall Complex L7", YESTERDAY)
    page.get_by_role("button", name="Submit lost item").click()
    page.wait_for_url(re.compile(r"/items/\d+\?new=1"), timeout=15000)
    ctx_data["lost_id"] = int(re.search(r"/items/(\d+)", page.url).group(1))
    expect(page.locator("body")).to_contain_text("Black Casio calculator")
    page.screenshot(path=str(SHOTS / "SYS-03-lost-report.png"), full_page=True)
    return f"lost report created (item {ctx_data['lost_id']})"


@case("SYS-04", "Second student reports the found item with a photo; it stays hidden until handed in")
def t04(page_b, page_a, photo):
    sign_in(page_b, STUDENT_B)
    onboard(page_b, "Finder Sys", roll="230002", phone="9876500000")
    page_b.goto(f"{BASE}/report/found")
    fill_report(page_b, "Casio calculator", "Black Casio fx-991EX scientific calculator, scratched lid",
                "Black", "Casio", "L7 back row", TODAY, photo=photo, office="Main Security Office")
    page_b.get_by_role("button", name="Submit found item").click()
    page_b.wait_for_url(re.compile(r"/items/\d+\?new=1"), timeout=20000)
    assert "photos_failed" not in page_b.url, page_b.url
    ctx_data["found_id"] = int(re.search(r"/items/(\d+)", page_b.url).group(1))
    expect(page_b.locator("img").first).to_be_visible()
    page_b.screenshot(path=str(SHOTS / "SYS-04-found-report.png"), full_page=True)
    page_a.goto(f"{BASE}/found")
    page_a.wait_for_load_state("networkidle")
    assert "Casio calculator" not in page_a.locator("main").inner_text(), "item visible before hand-in"
    return f"found item {ctx_data['found_id']} with photo; not on Found page before hand-in"


@case("SYS-05", "Admin signs in as staff and receives the item at the security office")
def t05(page):
    sign_in(page, ADMIN, staff=True)
    onboard(page, "Admin Test", phone="9876511111")
    page.goto(f"{BASE}/staff")
    expect(page.locator("main")).to_contain_text("Casio calculator")
    page.goto(f"{BASE}/items/{ctx_data['found_id']}")
    page.get_by_role("button", name="Receive item").click()
    expect(page.locator("main")).to_contain_text(re.compile("Main Security Office"), timeout=10000)
    page.screenshot(path=str(SHOTS / "SYS-05-received.png"), full_page=True)
    return "staff desk listed the item; received at Main Security Office"


@case("SYS-06", "Owner finds the item on the Found page, by search, and in AI suggestions on their report")
def t06(page):
    page.goto(f"{BASE}/found")
    expect(page.locator("main")).to_contain_text("Casio calculator", timeout=10000)
    page.goto(f"{BASE}/found?q=casio")
    expect(page.locator("main")).to_contain_text("Casio calculator")
    page.goto(f"{BASE}/found?q=umbrella")
    page.wait_for_load_state("networkidle")
    assert "Casio calculator" not in page.locator("main").inner_text()
    # The AI worker matches in the background: wait up to 30 s for the suggestion.
    for _ in range(15):
        page.goto(f"{BASE}/items/{ctx_data['lost_id']}")
        page.wait_for_load_state("networkidle")
        similar = page.locator("section[aria-labelledby=similar-heading]")
        if similar.count() and "Casio calculator" in similar.inner_text():
            break
        time.sleep(2)
    else:
        raise AssertionError("found item never appeared in the lost report's suggestions")
    page.screenshot(path=str(SHOTS / "SYS-06-similar.png"), full_page=True)
    return "listed, searchable ('casio' yes, 'umbrella' no), suggested on the lost report"


@case("SYS-07", "Owner claims the found item")
def t07(page):
    page.goto(f"{BASE}/items/{ctx_data['found_id']}/claim")
    page.fill("#description", "It is mine: fx-991EX, scratch on the lid, my initials OS under the battery cover.")
    page.get_by_role("button", name="Send claim").click()
    page.wait_for_url(re.compile(r"/claims/\d+\?new=1"), timeout=15000)
    ctx_data["claim_id"] = int(re.search(r"/claims/(\d+)", page.url).group(1))
    expect(page.locator("main")).to_contain_text("Claim sent")
    page.screenshot(path=str(SHOTS / "SYS-07-claim.png"), full_page=True)
    return f"claim {ctx_data['claim_id']} sent"


@case("SYS-08", "Staff reviews and approves the claim; the owner is emailed")
def t08(page):
    page.goto(f"{BASE}/claims/{ctx_data['claim_id']}")
    page.get_by_role("button", name="Start review").click()
    page.get_by_role("button", name="Approve", exact=True).click()
    page.get_by_role("button", name="Approve claim").click()
    expect(page.locator("main")).to_contain_text("Approved", timeout=10000)
    time.sleep(1)
    log = BACKEND_LOG.read_text(errors="ignore")
    assert re.search(rf"EMAIL to: {re.escape(STUDENT_A)}\nSubject: .*approved", log, re.I), "no approval email"
    page.screenshot(path=str(SHOTS / "SYS-08-approved.png"), full_page=True)
    return "review started, approved; approval email printed for the owner"


@case("SYS-09", "Hand-over needs the ID check; item then appears on the Returned page")
def t09(page):
    page.goto(f"{BASE}/claims/{ctx_data['claim_id']}")
    page.get_by_role("button", name="Hand over item").click()
    expect(page.locator("form button[type=submit]")).to_be_disabled()   # ID check not ticked yet
    page.get_by_label(re.compile("I checked the ID card")).check()
    page.locator("form button[type=submit]").click()
    expect(page.locator("main")).to_contain_text("Handed over", timeout=10000)
    page.goto(f"{BASE}/returned")
    expect(page.locator("main")).to_contain_text("Casio calculator", timeout=10000)
    page.screenshot(path=str(SHOTS / "SYS-09-returned.png"), full_page=True)
    return "button disabled until the ID check is ticked; handed over; listed on Returned"


@case("SYS-10", "Admin adds a guard, who can then sign in to the staff desk")
def t10(admin_page, guard_page):
    admin_page.goto(f"{BASE}/admin/staff")
    admin_page.fill("#staff-email", GUARD)
    admin_page.select_option("#staff-role", value="GUARD")
    admin_page.locator("form button[type=submit]").first.click()
    expect(admin_page.locator("main")).to_contain_text(GUARD, timeout=10000)
    sign_in(guard_page, GUARD, staff=True)
    onboard(guard_page, "Guard Sys", phone="9876522222")
    guard_page.goto(f"{BASE}/staff")
    expect(guard_page.locator("main")).to_contain_text(re.compile("Waiting for drop-off|At offices"))
    return "guard added from Manage staff and signed in to the staff desk"


@case("SYS-11", "Student cannot open staff pages; sign-out ends the session")
def t11(page):
    page.goto(f"{BASE}/staff")
    page.wait_for_load_state("networkidle")
    assert "/staff" not in page.url or "Waiting for drop-off" not in page.locator("body").inner_text()
    page.goto(f"{BASE}/")
    page.get_by_role("button", name=re.compile("Sign out", re.I)).click()
    page.wait_for_url(re.compile(r"/login$"), timeout=10000)
    page.goto(f"{BASE}/my-claims")
    page.wait_for_url(re.compile(r"/login$"), timeout=10000)
    return "staff desk not shown to a student; after sign-out private pages go to /login"


@case("SYS-12", "Background worker is running its scheduled jobs")
def t12():
    log = WORKER_LOG.read_text(errors="ignore")
    assert log.strip(), "worker log empty"
    assert "Traceback" not in log, log[-800:]
    return "worker started and ran without errors: " + " | ".join(log.strip().splitlines()[-2:])[:160]


@case("SYS-13", "Pages fit a phone screen (390 px) without sideways scrolling")
def t13(browser):
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True)
    page = ctx.new_page()
    sign_in(page, "mobile.sys@iitk.ac.in")
    onboard(page, "Mobile Sys", roll="230003", phone="9876533333")
    widths = {}
    for path in ["/", "/found", "/lost", f"/items/{ctx_data['found_id']}", "/report/lost", "/returned"]:
        page.goto(f"{BASE}{path}")
        page.wait_for_load_state("networkidle")
        widths[path] = page.evaluate("document.documentElement.scrollWidth")
    page.goto(f"{BASE}/found")
    page.screenshot(path=str(SHOTS / "SYS-13-mobile-found.png"))
    ctx.close()
    wide = {k: v for k, v in widths.items() if v > 392}
    assert not wide, f"pages wider than the screen: {wide}"
    return "all 6 pages <= 390 px wide"


@case("SYS-14", "No browser errors on the main pages")
def t14(page, errors):
    for path in ["/", "/found", "/lost", "/returned", f"/items/{ctx_data['found_id']}", f"/claims/{ctx_data['claim_id']}"]:
        page.goto(f"{BASE}{path}")
        page.wait_for_load_state("networkidle")
    assert not errors, errors[:5]
    return "0 console errors and 0 uncaught exceptions on 6 pages"


@case("SYS-15", "Response times under light load")
def t15(page):
    # Page loads (browser, signed in) and API latency (50 sequential requests via the proxy).
    loads = {}
    for path in ["/", "/found", f"/items/{ctx_data['found_id']}"]:
        times = []
        for _ in range(5):
            t = time.time()
            page.goto(f"{BASE}{path}")
            page.wait_for_load_state("networkidle")
            times.append((time.time() - t) * 1000)
        loads[path] = round(sorted(times)[2])
    cookies = "; ".join(f"{c['name']}={c['value']}" for c in page.context.cookies())
    lat = []
    for _ in range(50):
        req = urllib.request.Request(f"{BASE}/api/items?kind=FOUND&page=1&page_size=12", headers={"Cookie": cookies})
        t = time.time()
        with urllib.request.urlopen(req, timeout=10) as r:
            assert r.status == 200
            r.read()
        lat.append((time.time() - t) * 1000)
    lat.sort()
    ctx_data["perf"] = {"page_median_ms": loads, "api_p50_ms": round(lat[24]), "api_p95_ms": round(lat[47])}
    assert max(loads.values()) < 3000 and lat[47] < 500, ctx_data["perf"]
    return json.dumps(ctx_data["perf"])


def main():
    photo = make_photo()
    with sync_playwright() as p:
        browser = p.chromium.launch()
        a_ctx, b_ctx, admin_ctx, guard_ctx = (browser.new_context(viewport={"width": 1280, "height": 900}) for _ in range(4))
        a, b, admin, guard = a_ctx.new_page(), b_ctx.new_page(), admin_ctx.new_page(), guard_ctx.new_page()
        errors = []
        a.on("console", lambda m: m.type == "error" and errors.append(m.text))
        a.on("pageerror", lambda e: errors.append(str(e)))

        t01(a); t02(a); t03(a); t04(b, a, photo); t05(admin); t06(a); t07(a)
        t08(admin); t09(admin); t10(admin, guard)
        errors.clear(); t14(a, errors)
        t15(a); t11(a); t12(); t13(browser)
        browser.close()
    (OUT / "results.json").write_text(json.dumps({"results": results, "context": ctx_data}, indent=1))
    print(f"\n{sum(r['result'] == 'PASS' for r in results)} of {len(results)} passed")


if __name__ == "__main__":
    main()
