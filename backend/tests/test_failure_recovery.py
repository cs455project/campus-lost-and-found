"""Tests for the fixes made after the failure/recovery tests
(testing/failure_recovery_tests.py):

- FR-01: when the database is unreachable, the API answers 503 with a clear
  message at once, instead of a bare 500 and a dropped connection;
- FR-06: an AI job interrupted by a stopped worker is run again (up to 3
  tries), so the report still gets its matches;
- FR-08: a sign-in code that could not be emailed doesn't count against the
  student, so they can try again at once.
"""

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.clock import now, today
from app.core.database import get_db
from app.main import app
from app.models import AiJob, Category, Item, ItemKind, ItemStatus, LoginCode
from app.services import email as email_service
from app.services.ai import queue
from app.services.ai.semantic import JOB_KIND

# ---------------- FR-01: database unreachable ----------------


class DatabaseDown:
    """Stands in for a session when PostgreSQL is stopped."""

    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        return fail


def test_an_unreachable_database_gives_a_clear_503_not_a_500():
    app.dependency_overrides[get_db] = lambda: DatabaseDown()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set("lf_session", "a-session-token")
            responses = [client.get("/auth/me"), client.get("/items/found"),
                         client.post("/auth/request-code", json={"email": "someone@iitk.ac.in"})]
    finally:
        app.dependency_overrides.clear()
    for response in responses:
        assert response.status_code == 503
        assert response.json() == {"detail": "TraceBack is briefly unavailable. Please try again in a moment."}
        assert response.headers["retry-after"] == "5"
        assert response.headers["x-content-type-options"] == "nosniff"  # still has the security headers


class BrokenRule:
    def __getattr__(self, name):
        def fail(*args, **kwargs):
            raise IntegrityError("INSERT ...", {}, Exception("duplicate key value"))

        return fail


def test_other_database_errors_are_not_disguised_as_an_outage():
    # Only "can't reach the database" becomes 503; a real bug still shows as a 500.
    app.dependency_overrides[get_db] = lambda: BrokenRule()
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            client.cookies.set("lf_session", "a-session-token")
            response = client.get("/auth/me")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 500


# ---------------- FR-06: interrupted AI jobs ----------------


@pytest.fixture
def report(db, make_user):
    owner = make_user("queue.owner@iitk.ac.in", full_name="Queue Owner", roll_number="Q0001")
    bags = db.scalar(select(Category).where(Category.slug == "bags"))
    item = Item(kind=ItemKind.LOST, status=ItemStatus.OPEN, title="Black Wildcraft backpack",
                description="Laptop sleeve inside.", category_id=bags.id, location_text="Library",
                happened_on=today(), reporter_id=owner.id, ai_status="pending")
    db.add(item)
    db.commit()
    return item


def interrupted_job(db, item, attempts=1, minutes_ago=11, queued_minutes_ago=None):
    """The state a worker killed while working leaves behind."""
    started = now() - timedelta(minutes=minutes_ago)
    job = AiJob(kind=JOB_KIND, item_id=item.id, status="running", attempts=attempts, started_at=started,
                created_at=now() - timedelta(minutes=queued_minutes_ago or minutes_ago))
    db.add(job)
    db.commit()
    return job


@pytest.fixture
def handled(monkeypatch):
    seen = []
    monkeypatch.setitem(queue.HANDLERS, JOB_KIND, lambda db, job: seen.append(job.item_id))
    return seen


def test_an_interrupted_job_is_run_again(db, report, handled):
    job = interrupted_job(db, report)
    assert queue.run_all(db) == 1
    db.refresh(job)
    assert job.status == "done" and job.attempts == 2
    assert handled == [report.id]
    assert job.last_error is None


def test_after_three_tries_the_job_gives_up_and_the_page_says_so(db, report, handled):
    job = interrupted_job(db, report, attempts=queue.MAX_ATTEMPTS)
    assert queue.run_all(db) == 0
    db.refresh(job)
    db.refresh(report)
    assert job.status == "failed" and "given up" in job.last_error
    assert report.ai_status == "failed"  # no endless "looking for matches"
    assert handled == []


def test_a_job_that_waited_long_in_the_queue_is_not_taken_for_interrupted(db, report, handled):
    # Queued 30 minutes ago (the worker was down), started a minute ago.
    job = interrupted_job(db, report, minutes_ago=1, queued_minutes_ago=30)
    queue.run_all(db)
    db.refresh(job)
    assert job.status == "running" and job.attempts == 1  # left alone: it is still being worked on


def test_an_interrupted_job_gives_way_to_a_newer_one_for_the_same_item(db, report, handled):
    old = interrupted_job(db, report)
    queue.enqueue(db, JOB_KIND, report.id)  # the report was edited meanwhile
    db.commit()
    queue.run_all(db)
    db.refresh(old)
    assert old.status == "failed" and "newer job" in old.last_error
    assert handled == [report.id]  # the work is done once, by the newer job


def test_a_job_records_when_it_started(db, report, handled):
    queue.enqueue(db, JOB_KIND, report.id)
    db.commit()
    queue.run_all(db)
    job = db.scalar(select(AiJob).where(AiJob.item_id == report.id))
    assert job.started_at is not None and job.finished_at >= job.started_at


# ---------------- FR-08: sign-in code that couldn't be emailed ----------------


def test_a_code_that_could_not_be_sent_does_not_make_the_student_wait(db, make_client, monkeypatch, sent_codes):
    def mail_server_down(to, code, minutes_valid):
        raise email_service.EmailNotSent("ConnectionRefusedError: [Errno 111] Connection refused")

    working = email_service.send_login_code  # the sent_codes collector
    monkeypatch.setattr(email_service, "send_login_code", mail_server_down)
    client = make_client()
    failed = client.post("/auth/request-code", json={"email": "student.mail@iitk.ac.in"})
    assert failed.status_code == 503
    assert failed.json()["detail"] == "We couldn't send the email just now. Please try again."
    assert db.scalar(select(LoginCode).where(LoginCode.email == "student.mail@iitk.ac.in")) is None

    monkeypatch.setattr(email_service, "send_login_code", working)  # the mail server is back
    retry = client.post("/auth/request-code", json={"email": "student.mail@iitk.ac.in"})
    assert retry.status_code == 202  # straight away, no "please wait 60 seconds"
    code = sent_codes["student.mail@iitk.ac.in"]
    assert client.post("/auth/verify-code", json={"email": "student.mail@iitk.ac.in", "code": code}).status_code == 200


# ---------------- FR-04: a new report and its AI job are saved together ----------------


def test_a_new_report_is_saved_with_its_ai_job_in_one_commit(db, make_user, sign_in, monkeypatch):
    from app.services import matching
    from app.services.ai import semantic

    make_user("atomic.owner@iitk.ac.in", full_name="Atomic Owner", roll_number="T0001")
    client = sign_in("atomic.owner@iitk.ac.in")
    bags = db.scalar(select(Category).where(Category.slug == "bags"))
    monkeypatch.setattr(matching, "get_matcher", lambda: semantic.SemanticMatcher())
    commits = []
    real_commit = type(db).commit

    def counting_commit(session):
        commits.append(1)
        return real_commit(session)

    monkeypatch.setattr(type(db), "commit", counting_commit)
    body = {"title": "Steel bottle", "description": "Milton, dented.", "category_id": bags.id,
            "location_text": "Gym", "happened_on": str(today())}
    response = client.post("/items/lost", json=body)
    assert response.status_code == 201
    # One commit for the report and its AI job: a crash can't split them.
    assert len(commits) == 1
    assert db.scalar(select(AiJob).where(AiJob.item_id == response.json()["id"])) is not None


def test_a_failing_matcher_never_loses_the_report(db, make_user, sign_in, monkeypatch):
    from app.services import matching

    class Broken:
        def item_saved(self, db, item):
            queue.enqueue(db, JOB_KIND, item.id)
            raise RuntimeError("embedding model failed to load")

    make_user("broken.owner@iitk.ac.in", full_name="Broken Owner", roll_number="T0002")
    client = sign_in("broken.owner@iitk.ac.in")
    bags = db.scalar(select(Category).where(Category.slug == "bags"))
    monkeypatch.setattr(matching, "get_matcher", lambda: Broken())
    body = {"title": "Blue umbrella", "description": "Folding.", "category_id": bags.id,
            "location_text": "LHC", "happened_on": str(today())}
    response = client.post("/items/lost", json=body)
    assert response.status_code == 201
    item_id = response.json()["id"]
    assert db.get(Item, item_id) is not None  # the report is saved...
    assert db.scalar(select(AiJob).where(AiJob.item_id == item_id)) is None  # ...the matcher's half-work is not
