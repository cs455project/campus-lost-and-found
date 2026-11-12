"""Tests for the programs that run beside the API: the worker (timed jobs and
the AI queue, app/worker.py), the AI background runner (services/ai/runner.py)
and the seed script (app/seed.py)."""

import logging
import sys
import threading
from datetime import timedelta

import pytest
from sqlalchemy import delete, select, update

from app import seed, worker
from app.core.clock import now, today
from app.core.config import get_settings
from app.models import AiJob, Category, Item, ItemKind, ItemStatus, Office
from app.services.ai import queue, runner
from app.services.ai.semantic import JOB_KIND

# ---------------- seed ----------------


def test_seed_adds_nothing_when_everything_exists(db):
    assert seed.seed_categories(db) == 0
    assert seed.seed_offices(db) == 0


def test_seed_adds_back_a_missing_category_in_its_place(db):
    db.execute(delete(Category).where(Category.slug == "other"))
    db.flush()
    assert seed.seed_categories(db) == 1
    db.flush()
    other = db.scalar(select(Category).where(Category.slug == "other"))
    assert other.name == "Other"
    assert other.sort_order == 10 * len(seed.CATEGORIES)  # last in the filter list
    assert seed.seed_categories(db) == 0  # running again adds nothing


def test_seed_adds_a_missing_office_with_its_details(db):
    db.execute(update(Office).where(Office.name == "Security Sub-office D").values(name="Old sub-office"))
    db.flush()
    assert seed.seed_offices(db) == 1
    db.flush()
    office = db.scalar(select(Office).where(Office.name == "Security Sub-office D"))
    assert not office.is_main and office.opening_hours == "8:00 AM - 8:00 PM"
    assert seed.seed_offices(db) == 0


def test_seed_main_reports_what_it_added(db, monkeypatch, session_factory, capsys):
    monkeypatch.setattr(seed, "SessionLocal", session_factory)
    db.execute(delete(Category).where(Category.slug.in_(["other", "spectacles"])))
    db.commit()
    seed.main()
    assert "Added 2 categories and 0 offices." in capsys.readouterr().out
    seed.main()
    assert "Added 0 categories and 0 offices." in capsys.readouterr().out


def test_seed_lists_are_consistent():
    slugs = [slug for _name, slug in seed.CATEGORIES]
    assert len(slugs) == len(set(slugs))
    assert sum(1 for office in seed.OFFICES if office["is_main"]) == 1


# ---------------- worker: timed jobs ----------------


@pytest.fixture
def worker_sessions(monkeypatch, session_factory):
    monkeypatch.setattr(worker, "SessionLocal", session_factory)


def test_run_once_runs_every_job_without_a_failure(worker_sessions):
    done = worker.run_once()
    assert set(done) == {"send_dropoff_reminders", "send_collect_reminders", "send_review_reminders",
                         "expire_claims", "delete_old_proofs", "purge_returned"}
    assert all(count >= 0 for count in done.values())  # -1 would mean a job failed


def test_run_once_sends_a_reminder_that_is_due(db, worker_sessions, monkeypatch, make_user, email_log):
    finder = make_user("worker.finder@iitk.ac.in", full_name="Finder", roll_number="W0001")
    office = db.scalar(select(Office).where(Office.is_active).order_by(Office.id))
    bags = db.scalar(select(Category).where(Category.slug == "bags"))
    item = Item(kind=ItemKind.FOUND, status=ItemStatus.AWAITING_DROPOFF, title="Blue bottle",
                description="Steel bottle.", category_id=bags.id, location_text="Gym", happened_on=today(),
                reporter_id=finder.id, dropoff_office_id=office.id)
    db.add(item)
    db.commit()
    # The finder hasn't handed it in for two days.
    monkeypatch.setattr(worker, "now", lambda: now() + timedelta(days=2))
    done = worker.run_once()
    assert done["send_dropoff_reminders"] == 1
    db.refresh(item)
    assert item.dropoff_reminder_sent_at is not None
    assert [row[0] for row in email_log] == ["worker.finder@iitk.ac.in"]
    # The next run doesn't remind again.
    assert worker.run_once()["send_dropoff_reminders"] == 0


def test_run_once_logs_what_it_did(worker_sessions, monkeypatch, caplog):
    monkeypatch.setattr(worker, "run_all", lambda db, at: {"send_dropoff_reminders": 2, "purge_returned": 0})
    with caplog.at_level(logging.INFO, logger="worker"):
        worker.run_once()
    assert "send_dropoff_reminders': 2" in caplog.text and "purge_returned" not in caplog.text
    monkeypatch.setattr(worker, "run_all", lambda db, at: {"purge_returned": 0})
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="worker"):
        worker.run_once()
    assert "nothing was due" in caplog.text


# ---------------- worker: command line and loop ----------------


def test_worker_once_runs_the_jobs_then_the_ai_queue_and_stops(monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "run_once", lambda: calls.append("jobs"))
    monkeypatch.setattr(worker, "run_ai_jobs", lambda: calls.append("ai"))
    monkeypatch.setattr(sys, "argv", ["python -m app.worker", "--once"])
    worker.main()
    assert calls == ["jobs", "ai"]


def test_worker_loop_keeps_its_schedule_survives_ai_errors_and_stops_on_ctrl_c(monkeypatch, caplog):
    clock = {"t": 1000.0}
    sleeps = []
    calls = {"jobs": 0, "ai": 0}

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise KeyboardInterrupt
        clock["t"] += 60  # each AI round takes a minute in this fake clock

    def ai_round():
        calls["ai"] += 1
        if calls["ai"] == 1:
            raise RuntimeError("database restarted")

    monkeypatch.setattr(worker.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(worker.time, "sleep", fake_sleep)
    monkeypatch.setattr(worker, "run_once", lambda: calls.__setitem__("jobs", calls["jobs"] + 1))
    monkeypatch.setattr(worker, "run_ai_jobs", ai_round)
    monkeypatch.setattr(get_settings(), "ai_poll_seconds", 3.0)
    monkeypatch.setattr(sys, "argv", ["python -m app.worker", "--every", "100"])
    with caplog.at_level(logging.INFO):
        worker.main()  # returns after Ctrl+C
    # Timed jobs at t=1000 and t=1120 (not at t=1060, before 100 s had passed).
    assert calls == {"jobs": 2, "ai": 3}
    assert sleeps == [3.0, 3.0, 3.0]
    assert "AI queue round failed" in caplog.text  # logged, and the loop went on
    assert "Worker stopped." in caplog.text


# ---------------- AI runner ----------------


def test_run_pending_runs_every_queued_job(db, monkeypatch, session_factory, make_user):
    monkeypatch.setattr(runner, "SessionLocal", session_factory)
    seen = []
    # A stand-in for the real analysis, so this test is only about the runner.
    monkeypatch.setitem(queue.HANDLERS, JOB_KIND, lambda job_db, job: seen.append(job.item_id))
    owner = make_user("runner.owner@iitk.ac.in", full_name="Owner", roll_number="R0001")
    bags = db.scalar(select(Category).where(Category.slug == "bags"))
    items = [Item(kind=ItemKind.LOST, status=ItemStatus.OPEN, title=f"Lost thing {n}", description="x",
                  category_id=bags.id, location_text="Library", happened_on=today(), reporter_id=owner.id)
             for n in range(2)]
    db.add_all(items)
    db.flush()
    for item in items:
        queue.enqueue(db, JOB_KIND, item.id)
    db.commit()
    assert runner.run_pending() == 2
    assert seen == [items[0].id, items[1].id]
    statuses = set(db.scalars(select(AiJob.status).where(AiJob.item_id.in_([i.id for i in items]))))
    assert statuses == {"done"}
    assert runner.run_pending() == 0  # nothing left


def test_background_runner_keeps_going_after_a_failed_round_and_stops_cleanly(monkeypatch, caplog):
    rounds = {"n": 0}
    third_round = threading.Event()

    def fake_round():
        rounds["n"] += 1
        if rounds["n"] == 1:
            raise RuntimeError("database restarted")
        if rounds["n"] >= 3:
            third_round.set()
        return 1

    monkeypatch.setattr(runner, "run_pending", fake_round)
    monkeypatch.setattr(runner, "get_embedder", lambda: None)
    background = runner.BackgroundRunner(every_seconds=0.01)
    with caplog.at_level(logging.INFO):
        background.start()
        assert third_round.wait(timeout=5)
        background.stop()
    assert not background._thread.is_alive()
    assert "AI runner round failed" in caplog.text
    assert "1 job(s) done" in caplog.text


def test_background_runner_works_even_if_the_embedder_fails_to_load(monkeypatch, caplog):
    ran = threading.Event()

    def broken_embedder():
        raise OSError("model download failed")

    monkeypatch.setattr(runner, "get_embedder", broken_embedder)
    monkeypatch.setattr(runner, "run_pending", lambda: ran.set() or 0)
    background = runner.BackgroundRunner(every_seconds=0.01)
    background.start()
    assert ran.wait(timeout=5)
    background.stop()
    assert "Embedder failed to load" in caplog.text


def test_the_api_starts_its_own_runner_only_when_asked(monkeypatch):
    monkeypatch.setattr(runner, "run_pending", lambda: 0)
    monkeypatch.setattr(runner, "get_embedder", lambda: None)
    monkeypatch.setattr(get_settings(), "ai_worker_in_api", False)
    assert runner.start_in_api() is None

    monkeypatch.setattr(get_settings(), "ai_worker_in_api", True)
    monkeypatch.setattr(get_settings(), "ai_poll_seconds", 0.01)
    started = runner.start_in_api()
    try:
        assert isinstance(started, runner.BackgroundRunner)
        assert started._thread.is_alive() and started._thread.daemon
        assert started.every == 0.01
    finally:
        started.stop()
    assert not started._thread.is_alive()
