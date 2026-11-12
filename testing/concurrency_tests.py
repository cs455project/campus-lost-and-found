"""Concurrency tests: fire simultaneous requests at TraceBack and check that
the data stays correct (no double actions, no limits slipping, no 500s).

Each race is repeated over several fresh items/claims, because a race only
shows up when requests really overlap. Requests are released together with a
threading.Barrier, each from its own HTTP connection.

Run after start_stack.sh, with the backend virtualenv:
    python concurrency_tests.py   -> concurrency_results.json
Needs: httpx, pillow, and psql access to the test database (PSQL, TEST_DB env).
"""

import io
import json
import os
import re
import statistics
import subprocess
import threading
import time
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import httpx
from PIL import Image

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
OUT = Path(__file__).resolve().parent
LOG = Path(os.environ.get("BACKEND_LOG", OUT / "run" / "backend.log"))
PSQL = os.environ.get("PSQL", "psql -h localhost -U postgres").split()
TEST_DB = os.environ.get("TEST_DB", "lostfound_test_system")
BACKEND = OUT.parent / "backend"
BACKEND_PY = os.environ.get("BACKEND_PYTHON", str(BACKEND / ".venv" / "bin" / "python"))
TRIALS = int(os.environ.get("TRIALS", "10"))
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()
results, load_rows = [], []


# ---------------- helpers ----------------

def sql(query):
    out = subprocess.run(PSQL + ["-d", TEST_DB, "-At", "-c", query], capture_output=True, text=True, check=True)
    return [line.split("|") for line in out.stdout.strip().splitlines() if line]


def code_for(email, before):
    for _ in range(60):
        found = re.findall(rf"EMAIL to: {re.escape(email)}\nSubject: (\d{{6}}) is your", LOG.read_text(errors="ignore"))
        if len(found) > before:
            return found[-1]
        time.sleep(0.2)
    raise AssertionError("no code for " + email)


def codes_sent(email):
    return len(re.findall(rf"EMAIL to: {re.escape(email)}\nSubject: \d{{6}} is your", LOG.read_text(errors="ignore")))


def session(email, name=None, roll=None, phone="9876540000"):
    """Sign in through the API; return the session cookie."""
    c = httpx.Client(base_url=API, timeout=30)
    before = codes_sent(email)
    assert c.post("/auth/request-code", json={"email": email}).status_code == 202
    r = c.post("/auth/verify-code", json={"email": email, "code": code_for(email, before)})
    assert r.status_code == 200, r.text
    if not r.json()["is_onboarded"]:
        body = {"full_name": name or email.split("@")[0], "phone": phone}
        if roll:
            body["roll_number"] = roll
        assert c.post("/users/me/onboarding", json=body).status_code == 200
    return c.cookies.get("lf_session")


def client(cookie=None):
    return httpx.Client(base_url=API, timeout=60, cookies={"lf_session": cookie} if cookie else None)


def together(calls):
    """Run the calls at the same instant, one thread and one connection each.
    Each call is (cookie, method, path, kwargs). Returns status codes in order."""
    barrier = threading.Barrier(len(calls))
    out = [None] * len(calls)
    clients = [client(c[0]) for c in calls]

    def run(i):
        cookie, method, path, kw = calls[i]
        barrier.wait()
        try:
            out[i] = clients[i].request(method, path, **kw).status_code
        except Exception as exc:  # noqa: BLE001
            out[i] = f"error: {type(exc).__name__}"

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(calls))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for c in clients:
        c.close()
    return out


def jpeg():
    buf = io.BytesIO()
    Image.new("RGB", (200, 150), (40, 40, 40)).save(buf, "JPEG")
    return buf.getvalue()


def record(cid, title, expected, trials, observed, ok, severity="", note=""):
    results.append({"id": cid, "title": title, "expected": expected, "trials": trials, "observed": observed,
                    "result": "PASS" if ok else "FAIL", "severity": "" if ok else severity, "note": note})
    print(("PASS " if ok else "FAIL ") + cid, title, "->", observed)


# ---------------- main ----------------

def main():
    run_id = str(int(time.time()))[-5:]
    print("setting up users...")
    admin = session("admin.test@gmail.com", "Admin Test", phone="9876511111")
    a = client(admin)
    for g in ("guard1", "guard2"):
        assert a.post("/admin/staff", json={"email": f"{g}.cc{run_id}@gmail.com", "role": "GUARD"}).status_code == 201
    guard1 = session(f"guard1.cc{run_id}@gmail.com", "Guard One", phone="9876522221")
    guard2 = session(f"guard2.cc{run_id}@gmail.com", "Guard Two", phone="9876522222")
    finder = session(f"finder.cc{run_id}@iitk.ac.in", "Finder", f"F{run_id}", "9876533333")
    ownerA = session(f"owner.a.cc{run_id}@iitk.ac.in", "Owner A", f"A{run_id}", "9876544444")
    ownerB = session(f"owner.b.cc{run_id}@iitk.ac.in", "Owner B", f"B{run_id}", "9876555555")
    f, g1 = client(finder), client(guard1)
    cats = {c["slug"]: c["id"] for c in f.get("/categories").json()}
    offices = [o["id"] for o in f.get("/offices").json()]
    main_office = offices[0]

    def found_item(title, receive=True):
        body = {"title": title, "description": "concurrency test item", "category_id": cats["other"],
                "location_text": "Hall 2", "happened_on": YESTERDAY, "dropoff_office_id": main_office}
        r = f.post("/items/found", json=body)
        assert r.status_code == 201, r.text
        item_id = r.json()["id"]
        if receive:
            assert g1.post(f"/staff/items/{item_id}/receive", json={"office_id": main_office}).status_code == 200
        return item_id

    def claim(owner_cookie, item_id, review=False, approve=False):
        r = client(owner_cookie).post(f"/items/{item_id}/claims", json={"description": "It is mine"})
        assert r.status_code == 201, r.text
        cid = r.json()["id"]
        if review or approve:
            assert g1.post(f"/staff/claims/{cid}/start-review").status_code == 200
        if approve:
            assert g1.post(f"/staff/claims/{cid}/approve", json={"note": None}).status_code == 200
        return cid

    # C-01: the same address asks for a code 10 times at once.
    accepted, created = [], []
    for t in range(TRIALS):
        email = f"flood{t}.cc{run_id}@iitk.ac.in"
        st = together([(None, "POST", "/auth/request-code", {"json": {"email": email}})] * 10)
        accepted.append(st.count(202))
        created.append(int(sql(f"select count(*) from login_codes where email='{email}'")[0][0]))
    record("CON-01", "10 simultaneous code requests for one address",
           "Exactly 1 code sent; the other 9 refused (60 s wait)", TRIALS,
           f"codes sent per trial: {Counter(created)}; accepted per trial: {Counter(accepted)}",
           all(c == 1 for c in created), "Medium",
           note="Each extra code is another email to the same inbox.")

    # C-02: 20 wrong guesses at once on one code, then the right code.
    leaked, final_ok = [], []
    for t in range(TRIALS):
        email = f"guess{t}.cc{run_id}@iitk.ac.in"
        before = codes_sent(email)
        client().post("/auth/request-code", json={"email": email})
        real = code_for(email, before)
        wrong = [f"{(int(real) + i) % 1_000_000:06d}" for i in range(1, 21)]
        together([(None, "POST", "/auth/verify-code", {"json": {"email": email, "code": w}}) for w in wrong])
        attempts = int(sql(f"select attempts from login_codes where email='{email}' order by id desc limit 1")[0][0])
        leaked.append(attempts)
        final_ok.append(client().post("/auth/verify-code", json={"email": email, "code": real}).status_code == 200)
    record("CON-02", "20 simultaneous wrong guesses on one sign-in code",
           "At most 5 guesses counted; the code is then locked", TRIALS,
           f"attempts recorded: {Counter(leaked)}; right code accepted afterwards: {sum(final_ok)} of {TRIALS}",
           all(x <= 5 for x in leaked) and not any(final_ok), "High")

    # C-03: the right code sent 5 times at once.
    wins = []
    for t in range(TRIALS):
        email = f"reuse{t}.cc{run_id}@iitk.ac.in"
        before = codes_sent(email)
        client().post("/auth/request-code", json={"email": email})
        real = code_for(email, before)
        st = together([(None, "POST", "/auth/verify-code", {"json": {"email": email, "code": real}})] * 5)
        wins.append(st.count(200))
    record("CON-03", "The correct code submitted 5 times at once", "Exactly 1 sign-in; the code is single-use",
           TRIALS, f"sign-ins per trial: {Counter(wins)}", all(w == 1 for w in wins), "High")

    # C-04: two guards receive the same item at once.
    oks, events = [], []
    for t in range(TRIALS):
        item = found_item(f"Receive race {t}", receive=False)
        st = together([(c, "POST", f"/staff/items/{item}/receive", {"json": {"office_id": o}})
                       for c, o in ((guard1, offices[0]), (guard2, offices[1]))])
        oks.append(st.count(200))
        events.append(int(sql(f"select count(*) from custody_events where item_id={item} and event_type='RECEIVED'")[0][0]))
    record("CON-04", "Two guards receive the same item at the same moment",
           "One succeeds; one RECEIVED entry in the history", TRIALS,
           f"successful receives per trial: {Counter(oks)}; RECEIVED entries per item: {Counter(events)}",
           all(e == 1 for e in events) and all(o == 1 for o in oks), "Medium",
           note="A second entry would record two guards and two offices for one hand-in.")

    # C-05: one student double-clicks "Send claim" (5 requests at once).
    created, codes = [], Counter()
    for t in range(TRIALS):
        item = found_item(f"Double claim {t}")
        st = together([(ownerA, "POST", f"/items/{item}/claims", {"json": {"description": "mine"}})] * 5)
        codes.update(st)
        created.append(int(sql(f"select count(*) from claims where item_id={item}")[0][0]))
    record("CON-05", "One student sends the same claim 5 times at once",
           "1 claim created; the rest refused with 409, no server errors", TRIALS,
           f"claims created per item: {Counter(created)}; responses: {dict(codes)}",
           all(c == 1 for c in created) and 500 not in codes, "Low",
           note="The database index guarantees one open claim; the question is how the duplicates are answered.")

    # C-06: two guards approve two different claims on one item at once.
    approved = []
    for t in range(TRIALS):
        item = found_item(f"Double approve {t}")
        c1, c2 = claim(ownerA, item, review=True), claim(ownerB, item, review=True)
        together([(guard1, "POST", f"/staff/claims/{c1}/approve", {"json": {"note": None}}),
                  (guard2, "POST", f"/staff/claims/{c2}/approve", {"json": {"note": None}})])
        approved.append(int(sql(f"select count(*) from claims where item_id={item} and status='APPROVED'")[0][0]))
    record("CON-06", "Two guards approve two different claims on one item at once",
           "Only 1 claim approved", TRIALS, f"approved claims per item: {Counter(approved)}",
           all(x == 1 for x in approved), "Critical")

    # C-07: one guard approves while another rejects the same claim.
    split = []
    for t in range(TRIALS):
        item = found_item(f"Approve vs reject {t}")
        c = claim(ownerA, item, review=True)
        together([(guard1, "POST", f"/staff/claims/{c}/approve", {"json": {"note": None}}),
                  (guard2, "POST", f"/staff/claims/{c}/reject", {"json": {"note": "not convincing"}})])
        moves = sql(f"select to_status from claim_events where claim_id={c} and from_status='UNDER_REVIEW'")
        split.append(len(moves))
    record("CON-07", "One guard approves while another rejects the same claim",
           "Exactly one decision recorded", TRIALS, f"decisions recorded per claim: {Counter(split)}",
           all(s == 1 for s in split), "High",
           note="Two decisions mean the history shows both, the claimant may get both emails, and the final status is whichever wrote last.")

    # C-08: two guards hand over the same approved claim at once.
    collected, returned = [], []
    for t in range(TRIALS):
        item = found_item(f"Double hand-over {t}")
        c = claim(ownerA, item, approve=True)
        together([(g, "POST", f"/staff/claims/{c}/hand-over", {"json": {"id_checked": True, "note": None}})
                  for g in (guard1, guard2)])
        collected.append(int(sql(f"select count(*) from claim_events where claim_id={c} and to_status='COLLECTED'")[0][0]))
        returned.append(int(sql(f"select count(*) from custody_events where item_id={item} and event_type='RETURNED'")[0][0]))
    record("CON-08", "Two guards hand over the same item at once",
           "One hand-over: 1 COLLECTED and 1 RETURNED entry", TRIALS,
           f"COLLECTED entries: {Counter(collected)}; RETURNED entries: {Counter(returned)}",
           all(x == 1 for x in collected) and all(x == 1 for x in returned), "High")

    # C-09: the claimant withdraws while a guard hands the item over.
    states = Counter()
    bad = 0
    for t in range(TRIALS):
        item = found_item(f"Withdraw vs hand-over {t}")
        c = claim(ownerA, item, approve=True)
        together([(ownerA, "POST", f"/claims/{c}/withdraw", {}),
                  (guard1, "POST", f"/staff/claims/{c}/hand-over", {"json": {"id_checked": True, "note": None}})])
        cs, its = sql(f"select c.status, i.status from claims c join items i on i.id=c.item_id where c.id={c}")[0]
        states[f"claim {cs} / item {its}"] += 1
        ok = (cs, its) in {("COLLECTED", "RETURNED"), ("WITHDRAWN", "IN_CUSTODY")}
        n_events = int(sql(f"select count(*) from claim_events where claim_id={c} and from_status='APPROVED'")[0][0])
        bad += (not ok) or n_events != 1
    record("CON-09", "Claimant withdraws at the moment a guard hands the item over",
           "Either returned (claim collected) or still held (claim withdrawn), never a mix", TRIALS,
           f"outcomes: {dict(states)}; inconsistent: {bad}", bad == 0, "High")

    # C-10: 6 photos uploaded at once to an item that allows 3.
    counts = []
    for t in range(TRIALS):
        item = found_item(f"Photo race {t}", receive=False)
        together([(finder, "POST", f"/items/{item}/photos", {"files": {"file": (f"p{i}.jpg", jpeg(), "image/jpeg")}})
                  for i in range(6)])
        counts.append(int(sql(f"select count(*) from item_photos where item_id={item}")[0][0]))
    record("CON-10", "6 photos uploaded at once to an item limited to 3", "At most 3 photos stored", TRIALS,
           f"photos stored per item: {Counter(counts)}", all(c <= 3 for c in counts), "Low")

    # C-11: two guards move the same item to different offices at once.
    mismatch = 0
    for t in range(TRIALS):
        item = found_item(f"Transfer race {t}")
        together([(guard1, "POST", f"/staff/items/{item}/transfer", {"json": {"to_office_id": offices[1]}}),
                  (guard2, "POST", f"/staff/items/{item}/transfer", {"json": {"to_office_id": offices[2]}})])
        cur = sql(f"select current_office_id from items where id={item}")[0][0]
        last = sql(f"select to_office_id from custody_events where item_id={item} order by id desc limit 1")[0][0]
        mismatch += cur != last
    record("CON-11", "Two guards move the same item to different offices at once",
           "The item's office matches its newest history entry", TRIALS,
           f"items whose office disagrees with the history: {mismatch} of {TRIALS}", mismatch == 0, "Medium")

    # C-12: 5 workers run the scheduled jobs at the same moment.
    due = []
    for t in range(TRIALS):
        due.append(found_item(f"Reminder race {t}", receive=False))
    sql(f"update items set created_at = now() - interval '2 days' where id in ({','.join(map(str, due))})")
    procs = [subprocess.Popen([BACKEND_PY, "-c", "from app.worker import run_once; run_once()"], cwd=BACKEND,
                              env={**os.environ, "DB_NAME": TEST_DB, "TZ": "UTC"}, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
             for _ in range(5)]
    for p in procs:
        p.wait(timeout=120)
    sent = sql("select count(*) from email_logs where subject = 'Reminder: please hand in the item you found' "
               "and created_at > now() - interval '5 minutes'")[0][0]
    stamped = int(sql(f"select count(*) from items where id in ({','.join(map(str, due))}) and dropoff_reminder_sent_at is not null")[0][0])
    record("CON-12", "5 copies of the worker send drop-off reminders at the same moment",
           f"{TRIALS} reminders, one per item", TRIALS,
           f"reminder emails logged: {sent}; items marked reminded: {stamped}",
           int(sent) == TRIALS and stamped == TRIALS, "Medium",
           note="Uses FOR UPDATE SKIP LOCKED, so each worker takes different rows.")

    # C-13: AI queue with the API's runner + 2 extra worker processes.
    workers = [subprocess.Popen([BACKEND_PY, "-m", "app.worker", "--every", "3600"], cwd=BACKEND,
                                env={**os.environ, "DB_NAME": TEST_DB, "TZ": "UTC"}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
               for _ in range(2)]
    start_id = int(sql("select coalesce(max(id),0) from ai_jobs")[0][0])
    burst = together([(finder, "POST", "/items/found", {"json": {
        "title": f"Blue water bottle {i}", "description": "steel bottle with stickers", "category_id": cats["water-bottles"],
        "location_text": "Sports complex", "happened_on": YESTERDAY, "dropoff_office_id": main_office}}) for i in range(30)])
    deadline = time.time() + 120
    while time.time() < deadline:
        left = int(sql(f"select count(*) from ai_jobs where id>{start_id} and status in ('queued','running')")[0][0])
        if left == 0:
            break
        time.sleep(1)
    rows = sql(f"select status, attempts from ai_jobs where id>{start_id}")
    for w in workers:
        w.terminate()
    statuses = Counter(r[0] for r in rows)
    multi = sum(1 for r in rows if int(r[1]) > 1)
    dupes = sql("select count(*) - count(distinct (item_id, candidate_id)) from match_suggestions")[0][0]
    record("CON-13", "30 reports at once, AI queue served by 3 runners (API + 2 workers)",
           "Every job done exactly once; no duplicate suggestions", 1,
           f"30 reports: {Counter(burst)}; jobs: {dict(statuses)}; jobs run more than once: {multi}; duplicate suggestions: {dupes}",
           burst.count(201) == 30 and set(statuses) == {"done"} and multi == 0 and int(dupes) == 0, "Medium")

    # C-14: load: many students browsing at once.
    students = [session(f"load{i}.cc{run_id}@iitk.ac.in", f"Load {i}", f"L{run_id}{i:02d}", f"98765{60000 + i}")
                for i in range(10)]
    item_ids = [r[0] for r in sql("select id from items where status in ('IN_CUSTODY','CLAIM_PENDING') limit 20")]
    paths = ["/items?kind=FOUND&page=1&page_size=12", "/items?kind=FOUND&q=bottle", "/items?kind=LOST&page=1",
             *[f"/items/{i}" for i in item_ids[:5]], "/auth/me", "/categories"]
    for users in (1, 10, 25, 50, 100):
        lat, errs, done = [], Counter(), [0]
        stop = time.time() + 15
        lock = threading.Lock()

        def browse(n):
            c = client(students[n % len(students)])
            k = n
            while time.time() < stop:
                p = paths[k % len(paths)]
                k += 1
                t0 = time.time()
                try:
                    r = c.get(p)
                    ms = (time.time() - t0) * 1000
                    with lock:
                        lat.append(ms)
                        if r.status_code >= 400:
                            errs[r.status_code] += 1
                except Exception as exc:  # noqa: BLE001
                    with lock:
                        errs[type(exc).__name__] += 1
            c.close()

        threads = [threading.Thread(target=browse, args=(n,)) for n in range(users)]
        t_start = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        span = time.time() - t_start
        lat.sort()
        q = lambda p: round(lat[min(len(lat) - 1, int(p * len(lat)))])  # noqa: E731
        row = {"users": users, "requests": len(lat), "per_second": round(len(lat) / span, 1),
               "p50_ms": q(0.50), "p95_ms": q(0.95), "p99_ms": q(0.99), "errors": sum(errs.values()),
               "error_kinds": dict(errs)}
        load_rows.append(row)
        print("load", row)
    worst = max(load_rows, key=lambda r: r["users"])
    record("CON-14", "1 to 100 students browsing at once for 15 s each",
           "No errors; 95% of requests under 1 s at 50 users", 5,
           "; ".join(f"{r['users']} users: {r['per_second']}/s, p95 {r['p95_ms']} ms, errors {r['errors']}" for r in load_rows),
           all(r["errors"] == 0 for r in load_rows) and next(r for r in load_rows if r["users"] == 50)["p95_ms"] < 1000,
           "Medium")

    (OUT / "concurrency_results.json").write_text(json.dumps({"results": results, "load": load_rows}, indent=1))
    print(f"\n{sum(r['result'] == 'PASS' for r in results)} of {len(results)} concurrency checks passed")


if __name__ == "__main__":
    main()
