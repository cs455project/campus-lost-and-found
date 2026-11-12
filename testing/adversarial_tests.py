"""Adversarial tests: attack the running TraceBack stack the way a malicious
student (or an outsider) would, and record whether each attack is stopped.

Targets the API directly (http://127.0.0.1:8000, the same API Caddy serves at
/api in production) and the website (http://127.0.0.1:3000) for headers.
Run after start_stack.sh, with the backend virtualenv:  python adversarial_tests.py
-> adversarial_results.json (needs httpx, pillow; the XSS check needs playwright)
"""

import io
import json
import os
import re
import resource
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
from PIL import Image

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
WEB = os.environ.get("WEB_URL", "http://127.0.0.1:3000")
OUT = Path(__file__).resolve().parent
BACKEND = OUT.parent / "backend"
LOG = Path(os.environ.get("BACKEND_LOG", OUT / "run" / "backend.log"))
BACKEND_PY = os.environ.get("BACKEND_PYTHON", str(BACKEND / ".venv" / "bin" / "python"))
BROWSER_PY = os.environ.get("BROWSER_PYTHON", sys.executable)
results = []
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()


def record(cid, category, attack, expected, actual, passed, severity_if_fail="", note=""):
    results.append({"id": cid, "category": category, "attack": attack, "expected": expected,
                    "actual": actual, "result": "PASS" if passed else "FAIL",
                    "severity": "" if passed else severity_if_fail, "note": note})
    print(("PASS " if passed else "FAIL ") + cid, attack, "->", actual)


def code_for(email, before):
    for _ in range(40):
        found = re.findall(rf"EMAIL to: {re.escape(email)}\nSubject: (\d{{6}}) is your", LOG.read_text(errors="ignore"))
        if len(found) > before:
            return found[-1]
        time.sleep(0.25)
    raise AssertionError("no code for " + email)


def sent(email):
    return len(re.findall(rf"EMAIL to: {re.escape(email)}\nSubject: \d{{6}} is your", LOG.read_text(errors="ignore")))


def user(email, name, roll=None, phone="9876540000"):
    c = httpx.Client(base_url=API, timeout=20)
    before = sent(email)
    r = c.post("/auth/request-code", json={"email": email})
    assert r.status_code == 202, r.text
    r = c.post("/auth/verify-code", json={"email": email, "code": code_for(email, before)})
    assert r.status_code == 200, r.text
    if not r.json()["is_onboarded"]:
        body = {"full_name": name, "phone": phone}
        if roll:
            body["roll_number"] = roll
        r = c.post("/users/me/onboarding", json=body)
        assert r.status_code == 200, r.text
    return c


def jpeg(size=(400, 300), color=(20, 20, 20), exif=None):
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "JPEG", exif=exif or b"")
    return buf.getvalue()


def main():
    anon = httpx.Client(base_url=API, timeout=20)
    cats = {c["slug"]: c["id"] for c in user("setup.adv@iitk.ac.in", "Setup Adv", "240000").get("/categories").json()}
    victim = user("victim.adv@iitk.ac.in", "Victim Adv", "240001", "9876541111")
    attacker = user("attacker.adv@iitk.ac.in", "Attacker Adv", "240002", "9876542222")
    admin = user("admin.test@gmail.com", "Admin Test")
    offices = victim.get("/offices").json()
    main_office = next(o["id"] for o in offices if o["is_main"])
    item = lambda title, cat="electronics", **kw: {"title": title, "description": "test item", "category_id": cats[cat],  # noqa: E731
                                                  "location_text": "Hall 5", "happened_on": YESTERDAY, **kw}

    # Victim's data the attacker will go after.
    hidden = victim.post("/items/found", json=item("Victim wallet (not handed in)", "wallets-purses", dropoff_office_id=main_office)).json()
    idcard = victim.post("/items/found", json=item("Student ID card", "id-cards-documents", dropoff_office_id=main_office)).json()
    victim.post(f"/items/{idcard['id']}/photos", files={"file": ("card.jpg", jpeg(), "image/jpeg")})
    admin.post(f"/staff/items/{idcard['id']}/receive", json={"office_id": main_office})
    idcard = victim.get(f"/items/{idcard['id']}").json()
    lost = victim.post("/items/lost", json=item("Victim headphones")).json()
    finder = user("finder.adv@iitk.ac.in", "Finder Adv", "240003", "9876543333")
    held = finder.post("/items/found", json=item("Grey umbrella", "other", dropoff_office_id=main_office)).json()
    admin.post(f"/staff/items/{held['id']}/receive", json={"office_id": main_office})
    claim = victim.post(f"/items/{held['id']}/claims", json={"description": "Mine, has a broken rib"}).json()
    victim.post(f"/claims/{claim['id']}/proofs", files={"file": ("p.jpg", jpeg(), "image/jpeg")})
    claim = victim.get(f"/claims/{claim['id']}").json()

    # ---------------- A. Authentication ----------------
    email = "brute.adv@iitk.ac.in"
    before = sent(email)
    anon.post("/auth/request-code", json={"email": email})
    real = code_for(email, before)
    wrong = [f"{(int(real) + i) % 1_000_000:06d}" for i in range(1, 6)]
    codes = [anon.post("/auth/verify-code", json={"email": email, "code": w}).status_code for w in wrong]
    final = anon.post("/auth/verify-code", json={"email": email, "code": real}).status_code
    record("ADV-01", "Authentication", "Guess the 6-digit sign-in code (5 wrong tries, then the right one)",
           "Code locked after 5 wrong tries; even the right code then fails", f"wrong tries: {codes}; right code afterwards: {final}",
           final != 200 and all(c in (400, 401, 429) for c in codes), "High")

    r1 = anon.post("/auth/request-code", json={"email": "flood.adv@iitk.ac.in"}).status_code
    r2 = anon.post("/auth/request-code", json={"email": "flood.adv@iitk.ac.in"}).status_code
    record("ADV-02", "Authentication", "Flood a mailbox by requesting codes repeatedly",
           "Second request within 60 s refused (429)", f"{r1} then {r2}", r1 == 202 and r2 == 429, "Medium")

    bad = ["x@evil-iitk.ac.in", "x@iitk.ac.in.evil.com", "x@gmail.com", "x@iitk.ac.іn",
           "x@iitk.ac.in\nBcc: spam@evil.com", "x@evil.com\n@iitk.ac.in", "\"x@iitk.ac.in\"@evil.com"]
    got = {b: anon.post("/auth/request-code", json={"email": b}).status_code for b in bad}
    record("ADV-03", "Authentication", "Sign in with look-alike domains, a Cyrillic і, an unknown Gmail, or email-header injection (newline + Bcc)",
           "All refused (400/422); no email sent", ", ".join(f"{v}" for v in got.values()),
           all(v in (400, 422) for v in got.values()), "High")

    forged = httpx.Client(base_url=API, cookies={"lf_session": "A" * 43}).get("/auth/me").status_code
    stolen = victim.cookies.get("lf_session")
    tampered = httpx.Client(base_url=API, cookies={"lf_session": stolen[:-1] + ("A" if stolen[-1] != "A" else "B")}).get("/auth/me").status_code
    record("ADV-04", "Authentication", "Use a made-up or tampered session cookie", "401 Not signed in",
           f"made-up: {forged}; one character changed: {tampered}", forged == 401 and tampered == 401, "Critical")

    c = httpx.Client(base_url=API)
    before = sent("cookie.adv@iitk.ac.in")
    c.post("/auth/request-code", json={"email": "cookie.adv@iitk.ac.in"})
    r = c.post("/auth/verify-code", json={"email": "cookie.adv@iitk.ac.in", "code": code_for("cookie.adv@iitk.ac.in", before)})
    sc = r.headers.get("set-cookie", "")
    flags = [f for f in ("HttpOnly", "SameSite=lax", "Path=/") if f.lower() in sc.lower()]
    token = c.cookies.get("lf_session")
    c.post("/auth/logout")
    after = httpx.Client(base_url=API, cookies={"lf_session": token}).get("/auth/me").status_code
    record("ADV-05", "Authentication", "Inspect the session cookie; reuse it after sign-out",
           "HttpOnly + SameSite; token useless after logout (Secure flag comes from COOKIE_SECURE=true on the server)",
           f"flags: {', '.join(flags)}; reuse after logout: {after}", len(flags) == 3 and after == 401, "High")

    # ---------------- B. Access control ----------------
    staff_calls = {
        "list staff desk": attacker.get("/staff/items?stage=waiting").status_code,
        "receive item": attacker.post(f"/staff/items/{hidden['id']}/receive", json={"office_id": main_office}).status_code,
        "start review": attacker.post(f"/staff/claims/{claim['id']}/start-review").status_code,
        "approve claim": attacker.post(f"/staff/claims/{claim['id']}/approve", json={"note": None}).status_code,
        "dispose item": attacker.post(f"/staff/items/{held['id']}/dispose", json={"outcome": "DISPOSED", "note": "attack"}).status_code,
    }
    record("ADV-06", "Access control", "Student calls security-staff endpoints directly", "403 for every call",
           ", ".join(f"{k}: {v}" for k, v in staff_calls.items()), all(v == 403 for v in staff_calls.values()), "Critical")

    admin_calls = {
        "make self admin": attacker.post("/admin/staff", json={"email": "attacker.adv@iitk.ac.in", "role": "ADMIN"}).status_code,
        "list staff": attacker.get("/admin/staff").status_code,
    }
    record("ADV-07", "Access control", "Student calls admin endpoints to promote themselves", "403",
           ", ".join(f"{k}: {v}" for k, v in admin_calls.items()), all(v == 403 for v in admin_calls.values()), "Critical")

    attacker.patch("/users/me", json={"role": "ADMIN", "roll_number": "999999", "full_name": "Changed", "email": "x@gmail.com"})
    me = attacker.get("/auth/me").json()
    record("ADV-08", "Access control", "Mass assignment: send role=ADMIN, a new email, name and roll in a profile update",
           "Role, email, name and roll number unchanged", f"role={me['role']}, email={me['email']}, name={me['full_name']}, roll={me['roll_number']}",
           me["role"] == "STUDENT" and me["email"] == "attacker.adv@iitk.ac.in" and me["roll_number"] == "240002" and me["full_name"] == "Attacker Adv", "Critical")

    idor = {
        "read hidden found report": attacker.get(f"/items/{hidden['id']}").status_code,
        "read victim's claim": attacker.get(f"/claims/{claim['id']}").status_code,
        "download victim's proof image": attacker.get(f"/claims/{claim['id']}/proofs/{claim['proof_ids'][0]}").status_code if claim.get("proof_ids") else "n/a",
        "download ID-card photo": attacker.get(f"/items/{idcard['id']}/photos/{idcard['photo_ids'][0]}").status_code if idcard.get("photo_ids") else "n/a",
        "edit victim's lost report": attacker.put(f"/items/{lost['id']}", json=item("hacked")).status_code,
        "close victim's lost report": attacker.post(f"/items/{lost['id']}/close", json={"outcome": "CLOSED"}).status_code,
        "delete victim's found report": attacker.delete(f"/items/{hidden['id']}").status_code,
        "withdraw victim's claim": attacker.post(f"/claims/{claim['id']}/withdraw").status_code,
        "add photo to victim's report": attacker.post(f"/items/{lost['id']}/photos", files={"file": ("a.jpg", jpeg(), "image/jpeg")}).status_code,
    }
    ok = all(v in (403, 404) for v in idor.values())
    record("ADV-09", "Access control", "IDOR: change ids in the URL to read or change another student's items, claims, proofs and ID-card photos",
           "403/404 for all 9 attempts, victim's data unchanged",
           ", ".join(f"{k}: {v}" for k, v in idor.items()), ok and victim.get(f"/items/{lost['id']}").json()["title"] == "Victim headphones", "Critical")

    created = attacker.post("/items/lost", json={**item("Mass-assigned item"), "status": "RECOVERED", "reporter_id": 1,
                                                  "id": 99999, "ai_description": "fake", "kind": "FOUND"}).json()
    record("ADV-10", "Access control", "Mass assignment on a new report (status, kind, reporter_id, id, ai_description)",
           "Extra fields ignored", f"status={created.get('status')}, kind={created.get('kind')}, id={created.get('id')}",
           created.get("status") == "OPEN" and created.get("kind") == "LOST" and created.get("id") != 99999, "High")

    own = attacker.post("/items/found", json=item("Attacker's own find", "other", dropoff_office_id=main_office)).json()
    admin.post(f"/staff/items/{own['id']}/receive", json={"office_id": main_office})
    a = attacker.post(f"/items/{own['id']}/claims", json={"description": "mine"}).status_code
    b1 = attacker.post(f"/items/{held['id']}/claims", json={"description": "mine"}).status_code
    b2 = attacker.post(f"/items/{held['id']}/claims", json={"description": "mine again"}).status_code
    record("ADV-11", "Access control", "Claim an item you found yourself; claim the same item twice",
           "Own find refused; second open claim refused", f"own find: {a}; first claim: {b1}; second: {b2}",
           a in (400, 403, 409) and b1 == 201 and b2 in (400, 409), "Medium")

    # ---------------- C. Injection ----------------
    before_total = victim.get("/items?kind=LOST").json()["total"]
    sqli = ["' OR 1=1 --", "x'); DROP TABLE items; --", "headphones' UNION SELECT email FROM users --", "\\'; SELECT pg_sleep(5); --", "%' OR '%'='"]
    res = {}
    for s in sqli:
        t = time.time()
        r = victim.get("/items", params={"kind": "LOST", "q": s})
        res[s] = (r.status_code, r.json().get("total") if r.status_code == 200 else None, round(time.time() - t, 2))
    after_total = victim.get("/items?kind=LOST").json()["total"]
    record("ADV-12", "Injection", "SQL injection in the search box (OR 1=1, DROP TABLE, UNION, pg_sleep)",
           "200 with no extra rows, no delay, tables intact",
           "; ".join(f"{k!r}: {v[0]}, {v[1]} rows, {v[2]} s" for k, v in res.items()) + f"; lost items before/after: {before_total}/{after_total}",
           all(v[0] == 200 and (v[1] or 0) <= 1 and v[2] < 2 for v in res.values()) and before_total == after_total, "Critical")

    p = {"kind=FOUND' OR '1'='1": victim.get("/items", params={"kind": "FOUND' OR '1'='1"}).status_code,
         "category=x' OR 1=1--": victim.get("/items", params={"kind": "FOUND", "category": "x' OR 1=1--"}).json().get("total"),
         "item id 1 OR 1=1": victim.get("/items/1 OR 1=1").status_code,
         "page_size=1000000": victim.get("/items", params={"kind": "FOUND", "page_size": 1000000}).status_code,
         "page=-1": victim.get("/items", params={"kind": "FOUND", "page": -1}).status_code}
    record("ADV-13", "Injection", "Injection and abuse through other parameters (kind, category, id, page size)",
           "422 validation errors or 0 results", ", ".join(f"{k}: {v}" for k, v in p.items()),
           p["kind=FOUND' OR '1'='1"] == 422 and p["category=x' OR 1=1--"] == 0 and p["item id 1 OR 1=1"] == 422
           and p["page_size=1000000"] == 422 and p["page=-1"] == 422, "High")

    xss_title = "<img src=x onerror=alert(document.cookie)><script>alert(1)</script>"
    x = attacker.post("/items/lost", json={**item(xss_title[:120]), "description": "<svg onload=alert(2)>"}).json()
    xss_id = x.get("id")
    stored = victim.get(f"/items/{xss_id}").json().get("title")
    results_tmp = {"stored_as_text": stored == xss_title[:120].strip()}
    # Render the item page in a real browser as the victim: did any script run?
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        code = subprocess.run([BROWSER_PY, str(OUT / "xss_check.py"),
                               str(xss_id), victim.cookies.get("lf_session")], capture_output=True, text=True, timeout=120)
        results_tmp["browser"] = code.stdout.strip() or code.stderr.strip()[-200:]
    except Exception as exc:  # noqa: BLE001
        results_tmp["browser"] = f"not run: {exc}"
    record("ADV-14", "Injection", "Stored XSS: script and onerror payloads in an item title and description, viewed by another student",
           "Stored as plain text; nothing runs in the browser", f"stored as text: {results_tmp['stored_as_text']}; browser: {results_tmp['browser']}",
           results_tmp["stored_as_text"] and results_tmp["browser"].startswith("NO_SCRIPT_RAN"), "High")

    # ---------------- D. File uploads ----------------
    target = attacker.post("/items/found", json=item("Upload target", "other", dropoff_office_id=main_office)).json()["id"]
    up = lambda name, data, ctype: attacker.post(f"/items/{target}/photos", files={"file": (name, data, ctype)}).status_code  # noqa: E731
    uploads = {
        "PHP web shell named .jpg": up("shell.php.jpg", b"<?php system($_GET['c']); ?>", "image/jpeg"),
        "SVG with script": up("x.svg", b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", "image/svg+xml"),
        "Windows .exe renamed": up("photo.jpg", b"MZ\x90\x00" + b"\0" * 200, "image/jpeg"),
        "HTML page": up("x.html", b"<html><script>alert(1)</script></html>", "text/html"),
        "empty file": up("e.jpg", b"", "image/jpeg"),
        "6 MB file (limit 5 MB)": up("big.jpg", b"\xff\xd8\xff\xe0" + b"\0" * (6 * 1024 * 1024), "image/jpeg"),
    }
    record("ADV-15", "File upload", "Upload a web shell, SVG with script, renamed .exe, HTML, empty and oversized files",
           "All rejected with 400", ", ".join(f"{k}: {v}" for k, v in uploads.items()), all(v == 400 for v in uploads.values()), "High")

    poly = b"\xff\xd8\xff\xe0" + b"\0" * 16 + b"<script>alert(1)</script><?php echo 1; ?>"
    s1 = up("../../app/main.py", poly, "image/jpeg")
    detail = attacker.get(f"/items/{target}").json()
    photo = detail["photo_ids"][-1] if detail.get("photo_ids") else None
    served = attacker.get(f"/items/{target}/photos/{photo}") if photo else None
    record("ADV-16", "File upload", "Polyglot file (JPEG header + script/PHP) named ../../app/main.py",
           "May be stored as an image, but under a random name and served as image/jpeg with nosniff, so it can't run",
           f"upload: {s1}; served as: {served.headers.get('content-type') if served else '-'}; nosniff: {served.headers.get('x-content-type-options') if served else '-'}",
           s1 == 201 and served is not None and served.headers.get("content-type") == "image/jpeg"
           and served.headers.get("x-content-type-options") == "nosniff", "Medium",
           note="Server never uses the uploaded file name; content-type comes from the magic bytes.")

    gps = Image.Exif()
    gps[0x8825] = {1: "N", 2: (26.0, 30.0, 45.0), 3: "E", 4: (80.0, 13.0, 55.0)}  # IIT Kanpur-ish
    gps_img = jpeg(exif=gps.tobytes())
    t2 = attacker.post("/items/found", json=item("GPS test", "other", dropoff_office_id=main_office)).json()["id"]
    attacker.post(f"/items/{t2}/photos", files={"file": ("gps.jpg", gps_img, "image/jpeg")})
    admin.post(f"/staff/items/{t2}/receive", json={"office_id": main_office})
    pid = victim.get(f"/items/{t2}").json()["photo_ids"][0]
    downloaded = victim.get(f"/items/{t2}/photos/{pid}").content
    has_gps = 0x8825 in Image.open(io.BytesIO(downloaded)).getexif()
    record("ADV-17", "File upload", "Upload a phone photo with GPS location in its EXIF data; download it as another student",
           "Location data removed before the photo is shared", f"GPS still in the downloaded photo: {has_gps}",
           not has_gps, "Medium", note="Finders' photos are shown to every student once the item is at an office.")

    # Decompression bomb: tiny PNG file, huge picture. Upload, then measure what the AI photo step would need.
    bomb = io.BytesIO()
    Image.new("L", (12000, 12000), 0).save(bomb, "PNG", optimize=True)
    bomb_bytes = bomb.getvalue()
    s_bomb = up("bomb.png", bomb_bytes, "image/png")
    (OUT / "bomb.png").write_bytes(bomb_bytes)
    probe = subprocess.run([BACKEND_PY, "-c",
                            "import resource,sys,warnings;warnings.simplefilter('ignore');sys.path.insert(0,'.');"
                            "from app.services.ai.vision import shrink;"
                            f"shrink('{OUT}/bomb.png');print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss//1024)"],
                           capture_output=True, text=True, cwd=str(BACKEND))
    peak = probe.stdout.strip() or probe.stderr.strip()[-200:]
    record("ADV-18", "File upload", f"Decompression bomb: a {len(bomb_bytes) // 1024} KB PNG that is 12,000 × 12,000 pixels",
           "Refused by a pixel limit, or processed in bounded memory",
           f"upload: {s_bomb}; memory it would need to decode if accepted: {peak} MB",
           not (s_bomb == 201 and str(peak).isdigit() and int(peak) > 300), "Medium",
           note="The AI photo step (AI_PHOTO_DESCRIPTIONS, on in production) decodes the full image; a few such uploads can exhaust a 2 GB server.")

    # ---------------- E. Web and server hardening ----------------
    page = httpx.get(f"{WEB}/login")
    hdrs = {h: page.headers.get(h) for h in ("content-security-policy", "x-frame-options", "x-content-type-options",
                                             "referrer-policy", "strict-transport-security", "x-powered-by")}
    missing = [h for h in ("content-security-policy", "x-frame-options", "x-content-type-options", "referrer-policy") if not hdrs[h]]
    record("ADV-19", "Hardening", "Check the website's security headers (clickjacking, sniffing, CSP)",
           "CSP or X-Frame-Options, nosniff and Referrer-Policy set; no X-Powered-By",
           f"missing: {', '.join(missing) or 'none'}; X-Powered-By: {hdrs['x-powered-by']}",
           not missing and not hdrs["x-powered-by"], "Low",
           note="HSTS is added by Caddy only if configured; the deploy Caddyfile doesn't set it.")

    pre = httpx.options(f"{API}/auth/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    acao = pre.headers.get("access-control-allow-origin")
    record("ADV-20", "Hardening", "Cross-origin request from another website (CORS)", "Other origins not allowed",
           f"Access-Control-Allow-Origin for evil.example: {acao}", acao in (None, ""), "High")

    docs = {p: anon.get(p).status_code for p in ("/docs", "/openapi.json", "/redoc")}
    record("ADV-21", "Hardening", "Look for public API documentation", "Docs disabled in production",
           ", ".join(f"{k}: {v}" for k, v in docs.items()), all(v == 404 for v in docs.values()), "Low",
           note="Reachable on the live site at /api/docs and /api/openapi.json; lists every endpoint.")

    errs = {"broken JSON": anon.post("/auth/request-code", content=b"{not json", headers={"content-type": "application/json"}),
            "unknown route": anon.get("/../../etc/passwd"),
            "wrong type": victim.get("/items/abc")}
    leak = any(re.search(r"Traceback|File \"/|sqlalchemy|psycopg", r.text) for r in errs.values())
    record("ADV-22", "Hardening", "Send malformed input to provoke error pages", "Short error messages, no stack traces or internals",
           ", ".join(f"{k}: {r.status_code}" for k, r in errs.items()) + f"; internals leaked: {leak}",
           not leak and all(r.status_code < 500 for r in errs.values()), "Medium")

    t = time.time()
    statuses = [httpx.post(f"{API}/auth/verify-code", json={"email": "victim.adv@iitk.ac.in", "code": f"{i:06d}"}).status_code for i in range(30)]
    record("ADV-23", "Authentication", "Online brute force against a real account with 30 rapid code guesses (no code requested)",
           "Every guess refused; no sign-in", f"30 guesses in {round(time.time() - t, 1)} s; statuses: {sorted(set(statuses))}",
           200 not in statuses, "Critical",
           note="There is no per-IP rate limit; each code allows only 5 tries and expires in 10 minutes, so guessing stays impractical.")

    (OUT / "adversarial_results.json").write_text(json.dumps(results, indent=1))
    print(f"\n{sum(r['result'] == 'PASS' for r in results)} of {len(results)} attacks stopped")


if __name__ == "__main__":
    main()
