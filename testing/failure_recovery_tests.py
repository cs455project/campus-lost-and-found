"""Failure and recovery tests: take parts of TraceBack away while it runs
(the database, the API, the worker, the AI providers, the mail server, then
everything at once) and check that it fails safely and recovers on its own.

What is checked in each case:
  - the failure is reported quickly and cleanly (no hang, no stack trace),
  - nothing is lost or half-saved,
  - when the part comes back, TraceBack works again without manual repair,
  - and how long that takes.

The script runs its own copy of the app on a fresh test database
(TEST_DB, default lostfound_test_failure), set up as in production: two API
processes, the AI queue only in the worker, API docs off. It never touches
real data. Linux/macOS (or WSL).

    PG_STOP="..." PG_START="..." PG_KILL="..." python failure_recovery_tests.py
      PG_STOP   stops PostgreSQL cleanly     (e.g. sudo systemctl stop postgresql)
      PG_START  starts it again              (e.g. sudo systemctl start postgresql)
      PG_KILL   stops it like a power cut    (e.g. pg_ctl -D ... stop -m immediate)
    -> results/failure_recovery_results.json
Needs: the backend virtualenv, a built frontend, httpx, pillow, aiosmtpd, openssl.
"""

import io
import json
import os
import re
import signal
import subprocess
import threading
import time
from datetime import date, timedelta
from pathlib import Path

import httpx
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BACKEND, FRONTEND = ROOT / "backend", ROOT / "frontend"
RUN = HERE / "run"
RUN.mkdir(exist_ok=True)
VENV = BACKEND / ".venv" / "bin"
PY = str(VENV / "python")
API, WEB = "http://127.0.0.1:8000", "http://127.0.0.1:3000"
API_LOG, WORKER_LOG, WEB_LOG = RUN / "fr_backend.log", RUN / "fr_worker.log", RUN / "fr_frontend.log"
INBOX = RUN / "fr_smtp_inbox.txt"
SMTP_PORT = 2526
PSQL = os.environ.get("PSQL", "psql -h localhost -U postgres").split()
TEST_DB = os.environ.get("TEST_DB", "lostfound_test_failure")
PG_STOP, PG_START = os.environ["PG_STOP"], os.environ["PG_START"]
PG_KILL = os.environ.get("PG_KILL", PG_STOP)
YESTERDAY = (date.today() - timedelta(days=1)).isoformat()

BASE_ENV = {
    **os.environ, "TZ": "UTC", "DB_NAME": TEST_DB, "FRONTEND_ORIGIN": WEB, "EMBEDDING_BACKEND": "hashing",
    "EMAIL_BACKEND": "console", "API_DOCS": "false", "AI_WORKER_IN_API": "false", "AI_POLL_SECONDS": "1",
    "PATH": f"{VENV}:{os.environ['PATH']}",
}
results = []


# ---------------- running the parts ----------------

def sh(command, **kw):
    return subprocess.run(command, shell=True, capture_output=True, text=True, **kw)


def pids(pattern):
    out = subprocess.run(["ps", "-eo", "pid,args"], capture_output=True, text=True).stdout
    return [int(line.split()[0]) for line in out.splitlines()[1:] if re.search(pattern, line) and "ps -eo" not in line]


def kill(pattern, sig=signal.SIGKILL):
    """Kill the matching processes and their whole process group (uvicorn's
    worker processes and Next.js's server live in the same group)."""
    for pid in pids(pattern):
        try:
            os.killpg(os.getpgid(pid), sig)
        except ProcessLookupError:
            pass
    for _ in range(50):
        if not pids(pattern):
            return
        time.sleep(0.1)


def spawn(args, cwd, log, env):
    with open(log, "a") as out:
        subprocess.Popen(args, cwd=cwd, stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True)


API_PATTERN, WORKER_PATTERN, WEB_PATTERN = r"uvicorn app\.main:app", r"app\.worker", r"next start|next-server"


def start_api(**extra):
    spawn([str(VENV / "uvicorn"), "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "2"],
          BACKEND, API_LOG, {**BASE_ENV, **extra})
    return wait_for(lambda: get(API + "/health").status_code == 200, 60)


def start_worker(**extra):
    spawn([PY, "-m", "app.worker"], BACKEND, WORKER_LOG, {**BASE_ENV, **extra})


def start_web():
    spawn(["npx", "next", "start", "-H", "127.0.0.1", "-p", "3000"], FRONTEND, WEB_LOG,
          {**BASE_ENV, "BACKEND_URL": API})
    return wait_for(lambda: get(WEB + "/api/health").status_code == 200, 60)


def get(url, **kw):
    try:
        return httpx.get(url, timeout=kw.pop("timeout", 5), **kw)
    except httpx.HTTPError as error:
        return FakeResponse(error)


class FakeResponse:
    """What a client sees when there is no answer at all."""

    def __init__(self, error):
        self.status_code, self.text, self.error = f"no answer ({type(error).__name__})", "", error

    def json(self):
        return {}


def wait_for(check, timeout, step=0.25):
    """Seconds until check() is true, or None after `timeout` seconds."""
    start = time.monotonic()
    while time.monotonic() - start < timeout:
        try:
            if check():
                return round(time.monotonic() - start, 1)
        except Exception:  # noqa: BLE001  (the part may still be down)
            pass
        time.sleep(step)
    return None


def sql(query):
    out = subprocess.run(PSQL + ["-d", TEST_DB, "-At", "-c", query], capture_output=True, text=True, check=True)
    return [line.split("|") for line in out.stdout.strip().splitlines() if line]


def one(query):
    return sql(query)[0][0]


# ---------------- people and items ----------------

def code_for(email, before, source=API_LOG, pattern="EMAIL to: {e}\nSubject: (\\d{{6}}) is your"):
    for _ in range(80):
        found = re.findall(pattern.format(e=re.escape(email)), source.read_text(errors="ignore"))
        if len(found) > before:
            return found[-1]
        time.sleep(0.25)
    raise AssertionError("no sign-in code for " + email)


def codes_sent(email):
    return len(re.findall(rf"EMAIL to: {re.escape(email)}\nSubject: \d{{6}} is your", API_LOG.read_text(errors="ignore")))


def sign_in(email, name, roll=None, phone="9876500000"):
    c = httpx.Client(base_url=API, timeout=30)
    before = codes_sent(email)
    assert c.post("/auth/request-code", json={"email": email}).status_code == 202
    r = c.post("/auth/verify-code", json={"email": email, "code": code_for(email, before)})
    assert r.status_code == 200, r.text
    if not r.json()["is_onboarded"]:
        body = {"full_name": name, "phone": phone, **({"roll_number": roll} if roll else {})}
        assert c.post("/users/me/onboarding", json=body).status_code == 200
    return c


def jpeg(colour=(30, 90, 160)):
    buf = io.BytesIO()
    Image.new("RGB", (400, 300), colour).save(buf, "JPEG")
    return buf.getvalue()


def record(fid, title, failure, expected, observed, ok, measures=None):
    results.append({"id": fid, "title": title, "failure_injected": failure, "expected": expected,
                    "observed": observed, "measures": measures or {}, "result": "PASS" if ok else "FAIL"})
    print(("PASS " if ok else "FAIL ") + fid, title, "->", observed, measures or "", flush=True)


# ---------------- the scenarios ----------------

def fr01_database_down(ctx):
    student = ctx["student"]
    t0 = time.monotonic()
    sh(PG_STOP)
    stop_took = round(time.monotonic() - t0, 1)
    health = timed(lambda: get(API + "/health"))
    web_health = timed(lambda: get(WEB + "/api/health"))
    page = timed(lambda: student.get("/items?kind=FOUND", timeout=15))
    report = timed(lambda: student.post("/items/lost", timeout=15, json=lost_body(ctx, "Outage test umbrella")))
    t1 = time.monotonic()
    sh(PG_START)
    back_after = wait_for(lambda: get(API + "/health").status_code == 200, 60)
    from_db_up = round(time.monotonic() - t1, 1)
    me = student.get("/auth/me")
    partial = int(one("SELECT count(*) FROM items WHERE title = 'Outage test umbrella'"))
    body = page["response"].text
    leaked = "Traceback" in body or "psycopg" in body or "sqlalchemy" in body.lower()
    observed = (f"while down: API /health {health['status']} in {health['s']} s, website /api/health "
                f"{web_health['status']} in {web_health['s']} s, Found page {page['status']} in {page['s']} s "
                f"({page['response'].text[:80]!r}), new report {report['status']}; internals leaked: {leaked}. "
                f"After the restart: healthy {back_after} s after PostgreSQL was started, without restarting the API; "
                f"the student's session still works ({me.status_code}); half-saved reports: {partial}")
    ok = (health["status"] == 503 and max(health["s"], page["s"], report["s"]) < 3 and not leaked
          and back_after is not None and back_after < 15 and me.status_code == 200 and partial == 0)
    record("FR-01", "Database stops while the site is in use", f"PostgreSQL stopped ({stop_took} s), then started again",
           "Requests fail within seconds with a clear message (503), nothing half-saved; the API reconnects "
           "by itself when the database is back", observed, ok,
           {"error_response_s": max(health["s"], page["s"], report["s"]), "recovery_s": back_after})


def fr02_database_restart_under_load(ctx):
    cookie = ctx["student"].cookies.get("lf_session")
    item_id = ctx["held_item"]
    stop_at, duration, users = 8.0, 30.0, 10
    log, late = [], []
    lock = threading.Lock()
    begin = time.monotonic()

    def browse():
        c = httpx.Client(base_url=API, timeout=20, cookies={"lf_session": cookie})
        paths = ["/items?kind=FOUND", f"/items/{item_id}", "/offices", "/categories"]
        n = 0
        while time.monotonic() - begin < duration:
            start = time.monotonic()
            try:
                response = c.get(paths[n % len(paths)])
                status = response.status_code
                if status != 200 and time.monotonic() - begin > 13:
                    with lock:
                        late.append((paths[n % len(paths)], status, response.text[:100]))
            except httpx.HTTPError as error:
                status = type(error).__name__
            with lock:
                log.append((round(start - begin, 2), status, round(time.monotonic() - start, 2)))
            n += 1

    threads = [threading.Thread(target=browse) for _ in range(users)]
    for t in threads:
        t.start()
    time.sleep(stop_at)
    sh(PG_STOP)
    down_at = time.monotonic() - begin
    time.sleep(3)
    sh(PG_START)
    up_at = time.monotonic() - begin
    for t in threads:
        t.join()
    errors = [row for row in log if row[1] != 200]
    if late:
        print("late failures (sample):", late[:5], flush=True)
    last_error = max((row[0] for row in errors), default=None)
    after = [row for row in log if row[0] > up_at + 5]
    slowest = max(row[2] for row in log)
    kinds = {}
    for row in errors:
        kinds[str(row[1])] = kinds.get(str(row[1]), 0) + 1
    observed = (f"{len(log)} requests from {users} users over {duration:.0f} s; database down from {down_at:.1f} s "
                f"to {up_at:.1f} s; {len(errors)} requests failed, the last one started at {last_error} s; "
                f"after the restart: {len(after)} requests, {sum(1 for r in after if r[1] != 200)} failed; "
                f"slowest request {slowest} s; failures by kind {kinds}")
    ok = all(r[1] == 200 for r in after) and slowest < 15 and (last_error is None or last_error < up_at + 5)
    record("FR-02", "Database restarts while 10 students are browsing", "PostgreSQL stopped for 3 s under load",
           "Requests during the restart fail fast; every request after it succeeds; no restart of the API needed",
           observed, ok, {"requests": len(log), "failed": len(errors), "slowest_request_s": slowest,
                          "errors_end_after_db_up_s": None if last_error is None else round(last_error - up_at, 1)})


def fr03_api_crash(ctx):
    student, photo = ctx["student"], ctx["photo"]
    kill(API_PATTERN)
    web = timed(lambda: get(WEB + "/api/health"))
    page = timed(lambda: get(WEB + "/found"))
    t0 = time.monotonic()
    healthy = start_api()
    restart_s = round(time.monotonic() - t0, 1)
    me = student.get("/auth/me")
    item = student.get(f"/items/{ctx['held_item']}")
    image = student.get(f"/items/{ctx['held_item']}/photos/{photo}")
    observed = (f"API processes killed (kill -9). While down: website /api/health {web['status']} in {web['s']} s, "
                f"Found page {page['status']} in {page['s']} s. API healthy {healthy} s after starting again. "
                f"Afterwards: session {me.status_code}, item {item.status_code}, photo {image.status_code} "
                f"({len(image.content)} bytes)")
    ok = (web["s"] < 3 and healthy is not None and me.status_code == 200 and item.status_code == 200
          and image.status_code == 200)
    record("FR-03", "API process crashes", "Both API processes killed with kill -9, then started again "
           "(in production Docker restarts them: restart: unless-stopped)",
           "The website answers at once with an error instead of hanging; after the restart, sessions, "
           "reports and photos are all still there", observed, ok,
           {"error_response_s": max(web["s"], page["s"]), "api_start_s": restart_s})


def fr04_crash_mid_write(ctx):
    student = ctx["student"]
    acked, errors = [], []
    stop = threading.Event()
    lock = threading.Lock()

    def writer(n):
        c = httpx.Client(base_url=API, timeout=10, cookies={"lf_session": student.cookies.get("lf_session")})
        i = 0
        while not stop.is_set():
            title = f"Crash test bottle {n}-{i}"
            try:
                r = c.post("/items/lost", json=lost_body(ctx, title))
                with lock:
                    (acked if r.status_code == 201 else errors).append(title if r.status_code == 201 else r.status_code)
            except httpx.HTTPError as error:
                with lock:
                    errors.append(type(error).__name__)
            i += 1

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    time.sleep(2.0)
    kill(API_PATTERN)
    stop.set()
    for t in threads:
        t.join()
    start_api()
    saved = {row[0] for row in sql("SELECT title FROM items WHERE title LIKE 'Crash test bottle %'")}
    lost_acks = [t for t in acked if t not in saved]
    no_job = int(one("SELECT count(*) FROM items i WHERE i.title LIKE 'Crash test bottle %' "
                     "AND NOT EXISTS (SELECT 1 FROM ai_jobs j WHERE j.item_id = i.id)"))
    no_reporter = int(one("SELECT count(*) FROM items i WHERE i.title LIKE 'Crash test bottle %' "
                          "AND NOT EXISTS (SELECT 1 FROM users u WHERE u.id = i.reporter_id)"))
    observed = (f"{len(acked)} reports confirmed (201) before the crash, {len(errors)} requests cut off; "
                f"saved: {len(saved)}; confirmed but missing: {len(lost_acks)}; saved without their AI job: {no_job}; "
                f"without their reporter: {no_reporter}")
    ok = len(acked) > 0 and not lost_acks and no_job == 0 and no_reporter == 0
    record("FR-04", "API crashes in the middle of saving reports", "API killed while 8 students were sending reports",
           "Every confirmed report is saved; a report cut off is either saved completely (with its AI job) or "
           "not at all", observed, ok, {"confirmed": len(acked), "cut_off": len(errors), "lost": len(lost_acks)})


def fr05_worker_down(ctx):
    kill(WORKER_PATTERN)
    finder, owner = ctx["finder"], ctx["owner"]
    pairs = []
    for n in range(5):
        lost = owner.post("/items/lost", json=lost_body(ctx, f"Grey Wildtrak backpack {n}", colour="Grey",
                                                       brand="Wildtrak")).json()["id"]
        found = finder.post("/items/found", json=found_body(ctx, f"Grey Wildtrak backpack {n}", colour="Grey",
                                                          brand="Wildtrak")).json()["id"]
        ctx["guard"].post(f"/staff/items/{found}/receive", json={"office_id": ctx["office"]})
        pairs.append((lost, found))
    ids = ",".join(str(i) for pair in pairs for i in pair)
    time.sleep(4)
    waiting = int(one(f"SELECT count(*) FROM ai_jobs WHERE item_id IN ({ids}) AND status = 'queued'"))
    visible = owner.get(f"/items/{pairs[0][0]}").status_code
    t0 = time.monotonic()
    start_worker()
    drained = wait_for(lambda: int(one(f"SELECT count(*) FROM ai_jobs WHERE item_id IN ({ids}) "
                                       "AND status <> 'done'")) == 0, 60)
    lost_ids = ",".join(str(lost) for lost, _ in pairs)
    matched = int(one(f"SELECT count(DISTINCT item_id) FROM match_suggestions WHERE item_id IN ({lost_ids})"))
    runs = int(one(f"SELECT count(*) FROM ai_jobs WHERE item_id IN ({ids})"))
    observed = (f"worker stopped; 10 reports still accepted and shown (owner's page {visible}); "
                f"{waiting} AI jobs waiting after 4 s; worker started: queue empty after {drained} s; "
                f"{matched} of 5 lost reports got their match; {runs} jobs in all (each report analysed once)")
    ok = waiting == 10 and visible == 200 and drained is not None and matched == 5 and runs == 10
    record("FR-05", "Background worker is down", "Worker stopped while 10 reports came in, then started",
           "Students can still report; the AI work waits in the queue and is done once, as soon as the worker "
           "is back", observed, ok, {"queued_while_down": waiting, "catch_up_s": drained})
    ctx["catch_up_s"] = drained


def fr06_worker_killed_mid_job(ctx):
    kill(WORKER_PATTERN)
    owner = ctx["owner"]
    item = owner.post("/items/lost", json=lost_body(ctx, "Interrupted job calculator")).json()["id"]
    # The worker takes a job by marking it "running"; then it is killed. To
    # avoid waiting 10 minutes for the job to count as interrupted, its times
    # are moved back 11 minutes - the state a crash 11 minutes ago leaves.
    columns = {row[0] for row in sql("SELECT column_name FROM information_schema.columns "
                                     "WHERE table_name = 'ai_jobs'")}
    moved = "created_at = now() - interval '11 minutes'"
    if "started_at" in columns:
        moved += ", started_at = now() - interval '11 minutes'"
    sql(f"UPDATE ai_jobs SET status = 'running', attempts = 1, {moved} WHERE item_id = {item}")
    start_worker()
    done = wait_for(lambda: one(f"SELECT status FROM ai_jobs WHERE item_id = {item} ORDER BY id DESC LIMIT 1")
                    == "done", 30)
    rows = sql(f"SELECT status, attempts, coalesce(last_error, '') FROM ai_jobs WHERE item_id = {item} ORDER BY id")
    analysed = one(f"SELECT embedding IS NOT NULL FROM items WHERE id = {item}") == "t"
    observed = (f"job states afterwards: {rows}; report analysed for matching: {analysed}; "
                f"{'finished ' + str(done) + ' s after the worker restarted' if done is not None else 'never finished'}")
    ok = done is not None and analysed
    record("FR-06", "Worker is killed while it is working on an AI job",
           "Job left 'running' by a killed worker; worker restarted",
           "The interrupted job is run again automatically (up to 3 tries), so the report still gets its matches",
           observed, ok, {"recovered_s": done})


def fr07_ai_providers_down(ctx):
    kill(WORKER_PATTERN)
    down = {"LLM_API_KEY": "test-key", "LLM_BASE_URL": "http://127.0.0.1:9/v1", "LLM_MODEL": "gpt-oss-20b",
            "LLM_FALLBACK_API_KEY": "test-key", "LLM_FALLBACK_BASE_URL": "http://127.0.0.1:9/v1",
            "LLM_FALLBACK_MODEL": "gemini-2.5-flash", "AGENT_MATCHING": "always", "AI_PHOTO_DESCRIPTIONS": "false"}
    start_worker(**down)
    owner, finder = ctx["owner"], ctx["finder"]
    found = finder.post("/items/found", json=found_body(ctx, "Black Casio fx-991EX calculator", brand="Casio",
                                                       colour="Black")).json()["id"]
    ctx["guard"].post(f"/staff/items/{found}/receive", json={"office_id": ctx["office"]})
    lost = owner.post("/items/lost", json=lost_body(ctx, "Black Casio fx-991EX calculator", brand="Casio",
                                                    colour="Black")).json()["id"]
    done = wait_for(lambda: one(f"SELECT status FROM ai_jobs WHERE item_id = {lost} ORDER BY id DESC LIMIT 1")
                    in ("done", "failed"), 120)
    status = one(f"SELECT status FROM ai_jobs WHERE item_id = {lost} ORDER BY id DESC LIMIT 1")
    run = sql(f"SELECT outcome, coalesce(error, '') FROM agent_runs WHERE item_id = {lost} ORDER BY id DESC LIMIT 1")
    matched = int(one(f"SELECT count(*) FROM match_suggestions WHERE item_id = {lost} AND candidate_id = {found}"))
    page = owner.get(f"/items/{lost}/similar")
    observed = (f"both AI providers unreachable; job {status} after {done} s; agent run outcome: "
                f"{run[0][0] if run else 'none'} ({(run[0][1] if run else '')[:90]}); rule-based match saved: "
                f"{matched == 1}; owner's 'similar items' panel: {page.status_code}, "
                f"{len(page.json()['matches']) if page.status_code == 200 else 0} suggestion(s)")
    ok = status == "done" and run and run[0][0] == "fallback" and matched == 1 and page.status_code == 200
    record("FR-07", "AI providers (Groq and Gemini) are unreachable", "Both LLM addresses point at a closed port",
           "Matching falls back to the rule-based ranking; the report still gets its suggestions; "
           "the failure is logged in the agent's audit log", observed, ok, {"job_s": done})
    kill(WORKER_PATTERN)
    start_worker()


def fr08_mail_server_down(ctx):
    kill(API_PATTERN)
    smtp = {"EMAIL_BACKEND": "smtp", "SMTP_HOST": "127.0.0.1", "SMTP_PORT": str(SMTP_PORT),
            "SMTP_USER": "portal@gmail.com", "SMTP_PASSWORD": "app-password", "EMAIL_FROM": "",
            "EMAIL_RETRY_SECONDS": "1"}
    start_api(**smtp)
    email = f"mailtest.{ctx['run']}@iitk.ac.in"
    c = httpx.Client(base_url=API, timeout=30)

    # 1. No mail server at all.
    first = timed(lambda: c.post("/auth/request-code", json={"email": email}))
    log_row = sql(f"SELECT status, left(coalesce(error, ''), 60) FROM email_logs WHERE to_email = '{email}' "
                  "ORDER BY id DESC LIMIT 1")
    # 2. The mail server comes back; the student tries again straight away.
    server = start_smtp()
    second = c.post("/auth/request-code", json={"email": email})
    code = None
    if second.status_code == 202:
        code = code_for(email, 0, INBOX, "to {e}\n[\\s\\S]*?Subject: (\\d{{6}}) is your")
    verified = c.post("/auth/verify-code", json={"email": email, "code": code}).status_code if code else None
    # 3. The mail server drops out for 1.5 s while a code is being sent: the retry delivers it.
    stop_smtp(server)
    other = f"mailretry.{ctx['run']}@iitk.ac.in"
    threading.Timer(1.5, lambda: ctx.__setitem__("smtp", start_smtp())).start()
    third = timed(lambda: httpx.post(API + "/auth/request-code", json={"email": other}, timeout=30))
    tries = sql(f"SELECT status FROM email_logs WHERE to_email = '{other}' ORDER BY id")
    observed = (f"no mail server: {first['status']} in {first['s']} s "
                f"({first['response'].json().get('detail', '')!r}), logged as {log_row}; "
                f"server back, student retries at once: {second.status_code}, code delivered and accepted: "
                f"{verified}; server down for 1.5 s during a send: {third['status']} after {third['s']} s, "
                f"log {[row[0] for row in tries]}")
    ok = (first["status"] == 503 and first["s"] < 10 and log_row and log_row[0][0] == "failed"
          and second.status_code == 202 and verified == 200 and third["status"] == 202)
    record("FR-08", "Mail server is down when a student signs in",
           "No SMTP server; then the server is restored; then it drops out for 1.5 s during a send",
           "The student gets a clear 'try again' message and can try again at once; the failure is logged; "
           "a short outage is bridged by the automatic retry", observed, ok,
           {"error_response_s": first["s"], "retry_delivery_s": third["s"]})


def fr09_approval_while_mail_down(ctx):
    stop_smtp(ctx.pop("smtp", None))
    guard, owner = ctx["guard"], ctx["owner2"]
    item = ctx["claimable_item"]
    claim_id = owner.post(f"/items/{item}/claims", json={"description": "Blue Milton bottle, dent near the cap"}).json()["id"]
    assert guard.post(f"/staff/claims/{claim_id}/start-review").status_code == 200
    approve = timed(lambda: guard.post(f"/staff/claims/{claim_id}/approve", json={"note": "Matches"}))
    status = owner.get(f"/claims/{claim_id}").json().get("status")
    logged = sql(f"SELECT status FROM email_logs WHERE to_email = '{ctx['owner2_email']}' "
                 "AND subject LIKE '%approved%' ORDER BY id DESC LIMIT 1")
    observed = (f"approve: {approve['status']} in {approve['s']} s; claim as the student sees it: {status}; "
                f"approval email logged as {logged[0][0] if logged else 'nothing'} (shown to staff in "
                "'python -m app.cli email-log')")
    ok = approve["status"] == 200 and status == "APPROVED" and logged and logged[0][0] == "failed"
    record("FR-09", "Mail server is down when a guard approves a claim", "No SMTP server during the approval",
           "The approval is saved (the email is not part of the decision); the failed email is logged",
           observed, ok, {"approve_s": approve["s"]})
    kill(API_PATTERN)
    start_api()


def fr10_power_cut(ctx):
    counts = lambda: tuple(int(one(f"SELECT count(*) FROM {t}")) for t in ("users", "items", "claims", "item_photos"))  # noqa: E731
    before = counts()
    for pattern in (API_PATTERN, WORKER_PATTERN, WEB_PATTERN):
        kill(pattern)
    sh(PG_KILL)
    t0 = time.monotonic()
    sh(PG_START)
    db_up = wait_for(lambda: bool(one("SELECT 1")), 60)
    api_up = start_api()
    start_worker()
    web_up = start_web()
    total = round(time.monotonic() - t0, 1)
    after = counts()
    me = ctx["student"].get("/auth/me").status_code
    photo = ctx["student"].get(f"/items/{ctx['held_item']}/photos/{ctx['photo']}").status_code
    head = one("SELECT version_num FROM alembic_version")
    worker_ok = wait_for(lambda: "Worker started" in WORKER_LOG.read_text(errors="ignore")[-4000:], 30)
    observed = (f"everything killed at once (PostgreSQL in immediate mode, like a power cut). Back up in {total} s "
                f"(database {db_up} s, API {api_up} s, website {web_up} s). Rows (users, items, claims, photos) "
                f"before {before}, after {after}; session {me}; photo {photo}; schema at {head}; worker running: "
                f"{worker_ok is not None}")
    ok = before == after and me == 200 and photo == 200 and web_up is not None and worker_ok is not None
    record("FR-10", "Power cut: every part stops at once", "API, worker, website and PostgreSQL killed together",
           "Everything starts again with no data lost and no manual repair; signed-in users stay signed in",
           observed, ok, {"full_recovery_s": total})


# ---------------- small helpers for the scenarios ----------------

def timed(call):
    start = time.monotonic()
    response = call()
    return {"status": response.status_code, "s": round(time.monotonic() - start, 2), "response": response}


def lost_body(ctx, title, **extra):
    return {"title": title, "description": f"{title}. Lost near the library.", "category_id": ctx["cat"],
            "location_text": "Central Library", "happened_on": YESTERDAY, **extra}


def found_body(ctx, title, **extra):
    return {**lost_body(ctx, title, **extra), "description": f"{title}. Found near the library.",
            "dropoff_office_id": ctx["office"]}


def start_smtp():
    cert, key = RUN / "fr_smtp.crt", RUN / "fr_smtp.key"
    if not cert.exists():
        sh(f"openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj /CN=127.0.0.1 -keyout {key} -out {cert}")
    process = subprocess.Popen([PY, str(HERE / "fake_smtp.py"), str(SMTP_PORT), str(INBOX), str(cert), str(key)],
                               stdout=subprocess.PIPE, text=True, start_new_session=True)
    process.stdout.readline()  # "listening"
    return process


def stop_smtp(process):
    if process:
        process.kill()
        process.wait()


def setup():
    for pattern in (API_PATTERN, WORKER_PATTERN, WEB_PATTERN, r"fake_smtp\.py"):
        kill(pattern)
    for log in (API_LOG, WORKER_LOG, WEB_LOG, INBOX):
        log.write_text("")
    subprocess.run(PSQL + ["-q", "-c", f"DROP DATABASE IF EXISTS {TEST_DB};", "-c",
                           f"CREATE DATABASE {TEST_DB} OWNER lostfound;"], check=True)
    for command in (["alembic", "upgrade", "head"], [PY, "-m", "app.seed"],
                    [PY, "-m", "app.cli", "make-admin", "admin.test@gmail.com"]):
        subprocess.run(command, cwd=BACKEND, env=BASE_ENV, check=True, capture_output=True)
    assert start_api() is not None, "API did not start"
    start_worker()
    assert start_web() is not None, "website did not start"

    run = str(int(time.time()))[-5:]
    admin = sign_in("admin.test@gmail.com", "Admin Test", phone="9876511111")
    assert admin.post("/admin/staff", json={"email": f"guard.fr{run}@gmail.com", "role": "GUARD"}).status_code == 201
    ctx = {"run": run, "guard": sign_in(f"guard.fr{run}@gmail.com", "Guard FR", phone="9876522222"),
           "student": sign_in(f"student.fr{run}@iitk.ac.in", "Student FR", f"S{run}", "9876533333"),
           "finder": sign_in(f"finder.fr{run}@iitk.ac.in", "Finder FR", f"F{run}", "9876544444"),
           "owner": sign_in(f"owner.fr{run}@iitk.ac.in", "Owner FR", f"O{run}", "9876555555"),
           "owner2": sign_in(f"owner2.fr{run}@iitk.ac.in", "Owner Two FR", f"T{run}", "9876566666"),
           "owner2_email": f"owner2.fr{run}@iitk.ac.in"}
    ctx["cat"] = {c["slug"]: c["id"] for c in ctx["student"].get("/categories").json()}["other"]
    ctx["office"] = ctx["student"].get("/offices").json()[0]["id"]
    # A found item with a photo, received at the office (readable by everyone).
    finder, guard = ctx["finder"], ctx["guard"]
    held = finder.post("/items/found", json=found_body(ctx, "Brown leather wallet")).json()["id"]
    upload = finder.post(f"/items/{held}/photos", files={"file": ("wallet.jpg", jpeg(), "image/jpeg")})
    assert upload.status_code == 201, upload.text
    assert guard.post(f"/staff/items/{held}/receive", json={"office_id": ctx["office"]}).status_code == 200
    ctx["held_item"], ctx["photo"] = held, upload.json()["photo_ids"][0]
    bottle = finder.post("/items/found", json=found_body(ctx, "Blue Milton water bottle")).json()["id"]
    assert guard.post(f"/staff/items/{bottle}/receive", json={"office_id": ctx["office"]}).status_code == 200
    ctx["claimable_item"] = bottle
    return ctx


def main():
    print("setting up a fresh copy of the app...", flush=True)
    ctx = setup()
    only = os.environ.get("ONLY", "").lower().split(",")
    for scenario in (fr01_database_down, fr02_database_restart_under_load, fr03_api_crash, fr04_crash_mid_write,
                     fr05_worker_down, fr06_worker_killed_mid_job, fr07_ai_providers_down, fr08_mail_server_down,
                     fr09_approval_while_mail_down, fr10_power_cut):
        if only != [""] and scenario.__name__[:4] not in only:
            continue
        try:
            scenario(ctx)
        except Exception as error:  # noqa: BLE001  (record it and go on)
            record(scenario.__name__[:4].upper().replace("FR", "FR-"), scenario.__name__, "", "", f"script error: "
                   f"{type(error).__name__}: {error}", False)
            sh(PG_START)
            if not pids(API_PATTERN):
                start_api()
            if not pids(WORKER_PATTERN):
                start_worker()
    out = HERE / "results" / "failure_recovery_results.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({"run_at": time.strftime("%Y-%m-%d %H:%M:%S %Z"), "results": results}, indent=1))
    passed = sum(r["result"] == "PASS" for r in results)
    print(f"\n{passed} of {len(results)} failure scenarios recovered as expected")
    for pattern in (API_PATTERN, WORKER_PATTERN, WEB_PATTERN, r"fake_smtp\.py"):
        kill(pattern)


if __name__ == "__main__":
    main()
