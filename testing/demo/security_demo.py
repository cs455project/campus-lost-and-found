"""Live demo: a signed-in STUDENT tries things only guards or admins may do,
plus a few classic attacks. TraceBack must refuse every one.

Uses only the Python standard library, so it runs on any laptop:

    python testing/demo/security_demo.py ^
        --api https://16-176-186-149.sslip.io/api ^
        --student <lf_session cookie of a student>

Optional: --other-claim CLM-0013  (a claim made by ANOTHER student; the
student must not be able to open it).

Safe to run on the live site: every attack is aimed so that, if it were
refused (as expected), nothing changes; claim and item numbers that don't
exist (999999) are used for the guard actions, so even the role check is
tested without touching a real claim.

Get the cookie: sign in as a student, F12 -> Application -> Cookies ->
copy the value of "lf_session".
"""

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request

if os.name == "nt":
    os.system("")  # turns on colours in the Windows console
GREEN, RED, DIM, BOLD, END = "\033[32m", "\033[31m", "\033[2m", "\033[1m", "\033[0m"


def call(api, method, path, cookie=None, body=None):
    headers = {"User-Agent": "traceback-security-demo", "Content-Type": "application/json"}
    if cookie:
        headers["Cookie"] = f"lf_session={cookie}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(api.rstrip("/") + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, dict(r.headers), raw
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def detail(raw):
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        return ""
    d = body.get("detail") if isinstance(body, dict) else None
    if isinstance(d, list):  # validation errors
        d = "; ".join(str(x.get("msg")) for x in d)
    return str(d or "")[:90]


results = []


def check(name, attack, ok, got):
    results.append(ok)
    mark = f"{GREEN}BLOCKED{END}" if ok else f"{RED}NOT BLOCKED{END}"
    print(f"{BOLD}{len(results):>2}. {name}{END}\n    {DIM}{attack}{END}\n    -> {got}   [{mark}]\n")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", required=True, help="e.g. https://16-176-186-149.sslip.io/api")
    p.add_argument("--student", required=True, help="lf_session cookie of a signed-in student")
    p.add_argument("--other-claim", help="a claim made by another student (id or CLM-xxxx)")
    a = p.parse_args()
    api, me_cookie = a.api, a.student

    status, _, raw = call(api, "GET", "/auth/me", me_cookie)
    if status != 200:
        raise SystemExit(f"The student cookie doesn't work (HTTP {status}). Copy lf_session again.")
    me = json.loads(raw)
    print(f"\nSigned in as: {me.get('full_name')}  role={me.get('role')}\n" + "-" * 64 + "\n")

    # ---- Authentication ----
    s, _, r = call(api, "GET", "/auth/me")
    check("No sign-in at all", "GET /auth/me without a cookie", s == 401, f"HTTP {s} {detail(r)}")

    s, _, r = call(api, "GET", "/auth/me", "forged-" + "a" * 40)
    check("Forged session cookie", "GET /auth/me with a made-up cookie", s == 401, f"HTTP {s} {detail(r)}")

    # ---- Student calling guard-only APIs ----
    s, _, r = call(api, "GET", "/staff/claims", me_cookie)
    check("Student opens the guards' claim queue", "GET /staff/claims", s == 403, f"HTTP {s} {detail(r)}")

    s, _, r = call(api, "POST", "/staff/claims/999999/approve", me_cookie, {"note": None})
    check("Student approves a claim (guard only)", "POST /staff/claims/999999/approve", s == 403,
          f"HTTP {s} {detail(r)}")

    s, _, r = call(api, "POST", "/staff/claims/999999/hand-over", me_cookie, {"note": None})
    check("Student hands over an item (guard only)", "POST /staff/claims/999999/hand-over", s == 403,
          f"HTTP {s} {detail(r)}")

    s, _, r = call(api, "POST", "/staff/items/999999/receive", me_cookie, {"office_id": 1})
    check("Student marks an item as received (guard only)", "POST /staff/items/999999/receive", s == 403,
          f"HTTP {s} {detail(r)}")

    # ---- Student calling admin-only APIs ----
    s, _, r = call(api, "GET", "/admin/staff", me_cookie)
    check("Student lists all guards and admins (admin only)", "GET /admin/staff", s == 403,
          f"HTTP {s} {detail(r)}")

    s, _, r = call(api, "POST", "/admin/staff", me_cookie, {"email": me.get("email"), "role": "ADMIN"})
    check("Student makes THEMSELVES an admin", "POST /admin/staff {my email, role: ADMIN}", s == 403,
          f"HTTP {s} {detail(r)}")

    # ---- Mass assignment: sneak a role into the profile update ----
    s, _, r = call(api, "PATCH", "/users/me", me_cookie, {"role": "ADMIN"})
    _, _, after = call(api, "GET", "/auth/me", me_cookie)
    role_after = json.loads(after).get("role")
    check("Student adds role=ADMIN to a profile update", "PATCH /users/me {role: ADMIN}",
          s in (400, 422) and role_after == me.get("role"),
          f"HTTP {s} {detail(r)}  | role afterwards: {role_after}")

    # ---- Someone else's claim (IDOR) ----
    if a.other_claim:
        cid = int(a.other_claim.upper().removeprefix("CLM-"))
        s, _, r = call(api, "GET", f"/claims/{cid}", me_cookie)
        check("Student opens ANOTHER student's claim", f"GET /claims/{cid}", s == 404,
              f"HTTP {s} {detail(r)} (to this student it doesn't exist)")

    # ---- SQL injection in search ----
    q = urllib.parse.quote("' OR '1'='1'; DROP TABLE items; --")
    s, _, r = call(api, "GET", f"/items?kind=FOUND&q={q}", me_cookie)
    try:
        total = json.loads(r).get("total")
    except ValueError:
        total = None
    check("SQL injection in the search box", "GET /items?q=' OR '1'='1'; DROP TABLE items; --",
          s == 200 and total is not None, f"HTTP {s}, results: {total} (treated as plain search words, no error)")

    # ---- Information leaks ----
    s1, _, _ = call(api, "GET", "/docs")
    s2, _, _ = call(api, "GET", "/openapi.json")
    check("Browse the API documentation", "GET /docs and /openapi.json", s1 == 404 and s2 == 404,
          f"HTTP {s1} and {s2} (hidden in production)")

    _, h, _ = call(api, "GET", "/health")
    h = {k.lower(): v for k, v in h.items()}
    wanted = ["x-content-type-options", "x-frame-options", "referrer-policy"]
    present = [k for k in wanted if k in h]
    check("Security headers on every response", "nosniff, frame protection, referrer policy",
          len(present) == len(wanted), "present: " + ", ".join(present))

    blocked = sum(results)
    colour = GREEN if blocked == len(results) else RED
    print("-" * 64 + f"\n{colour}{BOLD}{blocked} of {len(results)} attacks blocked{END}\n")


if __name__ == "__main__":
    main()
