"""Tests for the timed jobs and the Returned Items page.

The trick: every job takes `at`, the time to treat as "now". Instead of
waiting 60 days, a test calls run_all(db, now() + 61 days) — a fake clock.
"""

import io
from datetime import timedelta

import pytest

from app.core.clock import now, today
from app.models import Category, Claim, ClaimProof, Item, Office, UserRole
from app.services import email as email_service
from app.services import jobs

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100
DESCRIPTION = "Black Wildcraft bag, my name is written inside the front pocket."


def later(**delta):
    return now() + timedelta(**delta)


@pytest.fixture
def place(db):
    bags = Category(name="Test Bags", slug="test-bags", sort_order=1)
    phones = Category(name="Test Phones", slug="test-phones", sort_order=2)
    office = Office(name="Test Main", location_description="Main gate", opening_hours="24 hours")
    db.add_all([bags, phones, office])
    db.commit()
    return {"bags": bags.id, "phones": phones.id, "office": office.id}


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "send",
                        lambda to, mail: sent.append((to, mail.subject, email_service.render_text(mail))))
    return sent


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT, name=None):
        count["n"] += 1
        email = f"jobperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"J{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=name or f"Person {count['n']}", **extra)
        client = sign_in(email)
        client.email = email
        return client

    return _person


def report_found(client, place, category="bags", title="Black backpack"):
    body = {"title": title, "description": "A backpack.", "category_id": place[category],
            "location_text": "Library", "happened_on": str(today()), "dropoff_office_id": place["office"]}
    return client.post("/items/found", json=body).json()["id"]


def receive(guard, item_id, place):
    assert guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["office"]}).status_code == 200


def approved_claim(owner, guard, item_id):
    claim_id = owner.post(f"/items/{item_id}/claims", json={"description": DESCRIPTION}).json()["id"]
    guard.post(f"/staff/claims/{claim_id}/start-review")
    assert guard.post(f"/staff/claims/{claim_id}/approve", json={}).status_code == 200
    return claim_id


def returned_item(person, place, **kwargs):
    """A found item that went all the way: reported, received, claimed, handed over."""
    finder, guard, owner = person(), person(UserRole.GUARD, name="Ramesh Kumar"), person(name="Asha Verma")
    item_id = report_found(finder, place, **kwargs)
    finder.post(f"/items/{item_id}/photos", files={"file": ("p.png", io.BytesIO(PNG), "image/png")})
    receive(guard, item_id, place)
    claim_id = approved_claim(owner, guard, item_id)
    guard.post(f"/staff/claims/{claim_id}/hand-over", json={"id_checked": True, "note": "ID matched"})
    return {"item": item_id, "claim": claim_id, "finder": finder, "guard": guard, "owner": owner}


# ---------------- Drop-off reminder (24 hours) ----------------


def test_dropoff_reminder_after_24_hours_once(db, person, place, emails):
    finder = person()
    report_found(finder, place)

    assert jobs.send_dropoff_reminders(db, later(hours=23)) == 0
    assert jobs.send_dropoff_reminders(db, later(hours=25)) == 1
    assert emails[-1][0] == finder.email
    assert "Test Main" in emails[-1][2]
    # Sent once only, however often the worker runs.
    assert jobs.send_dropoff_reminders(db, later(hours=48)) == 0


def test_no_dropoff_reminder_once_handed_in(db, person, place, emails):
    finder, guard = person(), person(UserRole.GUARD)
    receive(guard, report_found(finder, place), place)
    assert jobs.send_dropoff_reminders(db, later(hours=25)) == 0


# ---------------- Collection reminder (3 days after approval) ----------------


def test_collect_reminder_after_3_days_once(db, person, place, emails):
    finder, guard, owner = person(), person(UserRole.GUARD), person()
    item_id = report_found(finder, place)
    receive(guard, item_id, place)
    approved_claim(owner, guard, item_id)

    assert jobs.send_collect_reminders(db, later(days=2)) == 0
    assert jobs.send_collect_reminders(db, later(days=3, minutes=1)) == 1
    assert emails[-1][0] == owner.email and "student ID card" in emails[-1][2]
    assert jobs.send_collect_reminders(db, later(days=5)) == 0


# ---------------- Review reminder (claim waiting 3 days) ----------------


def test_review_reminder_goes_to_all_staff_once(db, person, place, emails):
    finder, guard, admin, owner = person(), person(UserRole.GUARD), person(UserRole.ADMIN), person()
    item_id = report_found(finder, place)
    receive(guard, item_id, place)
    claim_id = owner.post(f"/items/{item_id}/claims", json={"description": DESCRIPTION}).json()["id"]

    assert jobs.send_review_reminders(db, later(days=2)) == 0
    assert jobs.send_review_reminders(db, later(days=3, minutes=1)) == 1
    recipients = {to for to, _, _ in emails}
    assert recipients == {guard.email, admin.email}  # staff only, not students
    assert f"CLM-{claim_id:04d}" in emails[-1][2]
    assert jobs.send_review_reminders(db, later(days=6)) == 0


# ---------------- Claim expiry (7 days) ----------------


def test_approval_expires_after_7_days(db, person, place, emails):
    finder, guard, owner = person(), person(UserRole.GUARD), person()
    item_id = report_found(finder, place)
    receive(guard, item_id, place)
    claim_id = approved_claim(owner, guard, item_id)

    jobs.run_all(db, later(days=6))
    assert owner.get(f"/claims/{claim_id}").json()["status"] == "APPROVED"
    jobs.run_all(db, later(days=7, minutes=1))
    assert owner.get(f"/claims/{claim_id}").json()["status"] == "EXPIRED"
    assert guard.get(f"/items/{item_id}").json()["status"] == "IN_CUSTODY"


# ---------------- Proof images (2 days after the claim closes) ----------------


def test_proofs_deleted_two_days_after_claim_closes(db, person, place, temp_uploads):
    finder, guard, owner = person(), person(UserRole.GUARD), person()
    item_id = report_found(finder, place)
    receive(guard, item_id, place)
    claim_id = owner.post(f"/items/{item_id}/claims", json={"description": DESCRIPTION}).json()["id"]
    owner.post(f"/claims/{claim_id}/proofs", files={"file": ("p.png", io.BytesIO(PNG), "image/png")})
    stored = db.get(Claim, claim_id).proofs[0].stored_name
    proof_file = temp_uploads / "proofs" / stored
    assert proof_file.exists()

    # Still open: proofs are kept however long the review takes.
    assert jobs.delete_old_proofs(db, later(days=30)) == 0

    owner.post(f"/claims/{claim_id}/withdraw")
    assert jobs.delete_old_proofs(db, later(days=1)) == 0
    assert jobs.delete_old_proofs(db, later(days=2, hours=1)) == 1
    assert not proof_file.exists()
    assert db.query(ClaimProof).filter_by(claim_id=claim_id).count() == 0
    assert owner.get(f"/claims/{claim_id}").json()["proof_ids"] == []


# ---------------- Returned Items page ----------------


def test_returned_page_public_view(person, place):
    done = returned_item(person, place)
    student = person()
    page = student.get("/items/returned").json()
    assert page["total"] == 1
    row = page["items"][0]
    assert row["id"] == done["item"]
    assert row["returned_from"]["name"] == "Test Main"
    assert row["returned_at"] is not None
    assert row["cover_photo_id"] is not None
    assert row["staff"] is None  # students don't see who collected it
    # The item page and its photo can be opened, but not the staff history.
    detail = student.get(f"/items/{done['item']}").json()
    assert detail["custody_events"] == []
    assert student.get(f"/items/{done['item']}/photos/{row['cover_photo_id']}").status_code == 200


def test_returned_page_staff_view(person, place):
    done = returned_item(person, place)
    row = done["guard"].get("/items/returned").json()["items"][0]
    assert row["staff"]["collected_by"] == "Asha Verma"
    assert row["staff"]["claim_ref"] == f"CLM-{done['claim']:04d}"
    assert row["staff"]["handed_over_by"] == "Ramesh Kumar"
    assert row["staff"]["note"] == "ID matched"


def test_returned_page_category_filter(person, place):
    returned_item(person, place, category="phones", title="Phone")
    viewer = person()
    assert viewer.get("/items/returned?category=test-phones").json()["total"] == 1
    assert viewer.get("/items/returned?category=test-bags").json()["total"] == 0


# ---------------- 60-day purge ----------------


def test_returned_items_deleted_after_60_days(db, person, place, temp_uploads):
    done = returned_item(person, place)
    photo_files = list((temp_uploads / "items").iterdir())
    assert photo_files

    assert jobs.purge_returned(db, later(days=59)) == 0
    assert jobs.purge_returned(db, later(days=60, hours=1)) == 1

    # Ask the database itself (db.get could answer from the session's memory).
    assert db.query(Item).filter_by(id=done["item"]).count() == 0
    assert db.query(Claim).filter_by(id=done["claim"]).count() == 0  # claims and history go too
    assert not any(f.exists() for f in photo_files)
    assert done["guard"].get(f"/items/{done['item']}").status_code == 404
    assert done["guard"].get("/items/returned").json()["total"] == 0


def test_purge_leaves_unreturned_items_alone(db, person, place):
    finder, guard = person(), person(UserRole.GUARD)
    item_id = report_found(finder, place)
    receive(guard, item_id, place)
    assert jobs.purge_returned(db, later(days=365)) == 0
    assert db.query(Item).filter_by(id=item_id).count() == 1


# ---------------- The runner ----------------


def test_one_failing_job_does_not_stop_the_others(db, person, place, emails, monkeypatch):
    def broken(db, at):
        raise RuntimeError("boom")

    monkeypatch.setattr(jobs, "JOBS", [broken, jobs.send_dropoff_reminders])
    report_found(person(), place)
    done = jobs.run_all(db, later(hours=25))
    assert done == {"broken": -1, "send_dropoff_reminders": 1}
