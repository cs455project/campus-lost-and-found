"""Tests for ownership claims: submitting, proof images, review, office
visits, approval, hand-over, several claims on one item, expiry, and who
may see or do what."""

import io
from datetime import timedelta

import pytest

from app.core.clock import now, today
from app.models import Category, Claim, ClaimStatus, Office, UserRole
from app.services import claims as claims_service
from app.services import email as email_service

DESCRIPTION = "Black Wildcraft bag, my name is written inside the front pocket."
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 100


@pytest.fixture
def place(db):
    bags = Category(name="Test Bags", slug="test-bags", sort_order=1)
    office = Office(name="Test Main", location_description="Main gate", opening_hours="24 hours")
    db.add_all([bags, office])
    db.commit()
    return {"bags": bags.id, "office": office.id}


@pytest.fixture
def emails(monkeypatch):
    """Collect emails instead of sending them: a list of (to, subject, body)."""
    sent = []
    monkeypatch.setattr(email_service, "send",
                        lambda to, mail: sent.append((to, mail.subject, email_service.render_text(mail))))
    return sent


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT, name=None):
        count["n"] += 1
        email = f"claimperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"C{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=name or f"Person {count['n']}", **extra)
        return sign_in(email)

    return _person


@pytest.fixture
def held_item(person, place):
    """A found item reported by a finder and received at the office by a guard."""
    finder, guard = person(), person(UserRole.GUARD, name="Ramesh Kumar")
    body = {
        "title": "Black backpack", "description": "Wildcraft backpack.", "category_id": place["bags"],
        "location_text": "Library", "happened_on": str(today()), "dropoff_office_id": place["office"],
    }
    item_id = finder.post("/items/found", json=body).json()["id"]
    assert guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["office"]}).status_code == 200
    return {"id": item_id, "finder": finder, "guard": guard}


def claim(client, item_id, description=DESCRIPTION):
    return client.post(f"/items/{item_id}/claims", json={"description": description})


def approve(guard, claim_id):
    assert guard.post(f"/staff/claims/{claim_id}/start-review").status_code == 200
    return guard.post(f"/staff/claims/{claim_id}/approve", json={"note": "Matches the item"})


# ---------------- Submitting ----------------


def test_submit_claim_marks_item_claim_pending(person, held_item):
    owner = person()
    response = claim(owner, held_item["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["status"] == "SUBMITTED"
    assert body["ref"].startswith("CLM-")
    assert body["is_mine"] and body["can_withdraw"] and body["can_add_proof"]
    # The claimant never sees staff details or other claims.
    assert body["claimant"] is None and body["other_claims"] == []

    item = owner.get(f"/items/{held_item['id']}").json()
    assert item["status"] == "CLAIM_PENDING"
    assert item["can_claim"] is False
    assert item["my_claim"]["ref"] == body["ref"]


def test_description_is_required_but_one_character_is_enough(person, held_item):
    assert claim(person(), held_item["id"], "   ").status_code == 422  # blank (only spaces)
    assert claim(person(), held_item["id"], "x").status_code == 201


def test_finder_cannot_claim_their_own_report(held_item):
    assert claim(held_item["finder"], held_item["id"]).status_code == 400


def test_only_one_open_claim_per_person(person, held_item):
    owner = person()
    assert claim(owner, held_item["id"]).status_code == 201
    assert claim(owner, held_item["id"]).status_code == 409


def test_cannot_claim_item_not_yet_handed_in(person, place):
    finder = person()
    body = {"title": "Phone", "description": "Blue phone.", "category_id": place["bags"],
            "location_text": "Hall 5", "happened_on": str(today()), "dropoff_office_id": place["office"]}
    item_id = finder.post("/items/found", json=body).json()["id"]
    # Not visible to others yet, so it looks like it doesn't exist.
    assert claim(person(), item_id).status_code == 404


# ---------------- Proof images ----------------


def test_proof_images_are_private(person, held_item):
    owner, stranger = person(), person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    response = owner.post(f"/claims/{claim_id}/proofs", files={"file": ("p.png", io.BytesIO(PNG), "image/png")})
    assert response.status_code == 201, response.text
    proof_id = response.json()["proof_ids"][0]

    assert owner.get(f"/claims/{claim_id}/proofs/{proof_id}").status_code == 200
    assert held_item["guard"].get(f"/claims/{claim_id}/proofs/{proof_id}").status_code == 200
    assert stranger.get(f"/claims/{claim_id}/proofs/{proof_id}").status_code == 404
    assert stranger.get(f"/claims/{claim_id}").status_code == 404


def test_no_new_proof_once_review_starts(person, held_item):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    held_item["guard"].post(f"/staff/claims/{claim_id}/start-review")
    response = owner.post(f"/claims/{claim_id}/proofs", files={"file": ("p.png", io.BytesIO(PNG), "image/png")})
    assert response.status_code == 403


# ---------------- Review ----------------


def test_students_cannot_review(person, held_item):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    assert owner.get("/staff/claims").status_code == 403
    assert owner.post(f"/staff/claims/{claim_id}/start-review").status_code == 403
    assert owner.post(f"/staff/claims/{claim_id}/approve", json={}).status_code == 403


def test_staff_see_claimant(person, held_item):
    owner = person(name="Asha Verma")
    claim_id = claim(owner, held_item["id"]).json()["id"]
    body = held_item["guard"].get(f"/claims/{claim_id}").json()
    assert body["claimant"]["full_name"] == "Asha Verma"
    assert body["can_start_review"] and not body["can_approve"]  # must start review first


def test_cannot_skip_steps(person, held_item):
    claim_id = claim(person(), held_item["id"]).json()["id"]
    guard = held_item["guard"]
    # SUBMITTED -> APPROVED is not in the table.
    assert guard.post(f"/staff/claims/{claim_id}/approve", json={}).status_code == 403
    # SUBMITTED -> COLLECTED neither.
    assert guard.post(f"/staff/claims/{claim_id}/hand-over", json={"id_checked": True}).status_code == 403


def test_office_visit_flow(person, held_item, emails):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    guard = held_item["guard"]
    guard.post(f"/staff/claims/{claim_id}/start-review")

    response = guard.post(f"/staff/claims/{claim_id}/request-visit",
                          json={"note": "Bring the bag's purchase bill if you have it."})
    assert response.json()["status"] == "NEEDS_MORE_INFO"
    assert "Test Main" in emails[-1][2]
    # The claimant can't resolve it through the app (no proof uploads now).
    assert owner.get(f"/claims/{claim_id}").json()["can_add_proof"] is False

    # Resuming needs a note of what happened at the visit.
    assert guard.post(f"/staff/claims/{claim_id}/resume", json={}).status_code == 422
    body = guard.post(f"/staff/claims/{claim_id}/resume", json={"note": "Showed the bill."}).json()
    assert body["status"] == "UNDER_REVIEW"
    assert body["events"][-1]["note"] == "Office visit: Showed the bill."


def test_reject_needs_reason_and_frees_item(person, held_item, emails):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    guard = held_item["guard"]
    assert guard.post(f"/staff/claims/{claim_id}/reject", json={}).status_code == 422
    body = guard.post(f"/staff/claims/{claim_id}/reject", json={"note": "Description does not match."}).json()
    assert body["status"] == "REJECTED"
    assert "Description does not match." in emails[-1][2]
    assert owner.get(f"/items/{held_item['id']}").json()["status"] == "IN_CUSTODY"
    # A rejected claimant may try again (e.g. with new evidence).
    assert claim(owner, held_item["id"]).status_code == 201


def test_withdraw_frees_item(person, held_item):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    assert owner.post(f"/claims/{claim_id}/withdraw").json()["status"] == "WITHDRAWN"
    assert owner.get(f"/items/{held_item['id']}").json()["status"] == "IN_CUSTODY"


# ---------------- Approval and hand-over ----------------


def test_approval_emails_office_and_deadline(person, held_item, emails):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    body = approve(held_item["guard"], claim_id).json()
    assert body["status"] == "APPROVED"
    assert body["collect_by"] is not None
    assert body["item"]["current_office"]["name"] == "Test Main"
    assert "Test Main" in emails[-1][2] and "student ID card" in emails[-1][2]


def test_hand_over_requires_id_check(person, held_item):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    approve(held_item["guard"], claim_id)
    guard = held_item["guard"]
    assert guard.post(f"/staff/claims/{claim_id}/hand-over", json={}).status_code == 422
    assert guard.post(f"/staff/claims/{claim_id}/hand-over", json={"id_checked": False}).status_code == 422


def test_hand_over_returns_item_and_closes_other_claims(person, held_item):
    owner, other = person(), person()
    owner_claim = claim(owner, held_item["id"]).json()["id"]
    other_claim = claim(other, held_item["id"]).json()["id"]
    guard = held_item["guard"]
    approve(guard, owner_claim)

    # The other claim waits while one is approved.
    assert other.get(f"/claims/{other_claim}").json()["on_hold"] is True
    guard.post(f"/staff/claims/{other_claim}/start-review")
    assert guard.post(f"/staff/claims/{other_claim}/approve", json={}).status_code == 409

    body = guard.post(f"/staff/claims/{owner_claim}/hand-over", json={"id_checked": True}).json()
    assert body["status"] == "COLLECTED"
    assert body["collected_at"] is not None

    assert other.get(f"/claims/{other_claim}").json()["status"] == "CLOSED"
    item = guard.get(f"/items/{held_item['id']}").json()
    assert item["status"] == "RETURNED"
    assert item["returned_at"] is not None
    assert item["current_office"] is None
    assert item["custody_events"][-1]["event_type"] == "RETURNED"
    # A returned item is listed on the Returned Items page, but can't be claimed.
    assert claim(person(), held_item["id"]).status_code == 400


def test_second_claim_can_be_approved_after_first_is_rejected(person, held_item):
    first, second = person(), person()
    first_claim = claim(first, held_item["id"]).json()["id"]
    second_claim = claim(second, held_item["id"]).json()["id"]
    guard = held_item["guard"]
    approve(guard, first_claim)
    guard.post(f"/staff/claims/{first_claim}/reject", json={"note": "ID card did not match the name."})
    assert approve(guard, second_claim).status_code == 200


def test_database_allows_only_one_approved_claim(db, person, held_item):
    """Even if the service check were bypassed, the partial unique index refuses a second approval."""
    from sqlalchemy.exc import IntegrityError

    first = claim(person(), held_item["id"]).json()["id"]
    second = claim(person(), held_item["id"]).json()["id"]
    db.get(Claim, first).status = ClaimStatus.APPROVED
    db.get(Claim, second).status = ClaimStatus.APPROVED
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------------- Expiry ----------------


def test_overdue_approval_expires(db, person, held_item, emails):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    approve(held_item["guard"], claim_id)
    db.get(Claim, claim_id).collect_by = now() - timedelta(minutes=1)
    db.commit()

    assert claims_service.expire_overdue(db) == 1
    assert owner.get(f"/claims/{claim_id}").json()["status"] == "EXPIRED"
    # Nobody else has an open claim, so the item is free again.
    assert owner.get(f"/items/{held_item['id']}").json()["status"] == "IN_CUSTODY"
    assert "expired" in emails[-1][1].lower()


# ---------------- Lists ----------------


def test_my_claims_and_staff_tabs(person, held_item):
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    mine = owner.get("/claims/mine").json()
    assert [row["id"] for row in mine["items"]] == [claim_id]

    guard = held_item["guard"]
    assert guard.get("/staff/claims?stage=review").json()["total"] == 1
    approve(guard, claim_id)
    assert guard.get("/staff/claims?stage=review").json()["total"] == 0
    assert guard.get("/staff/claims?stage=collect").json()["total"] == 1

    item = guard.get(f"/items/{held_item['id']}").json()
    assert [c["id"] for c in item["claims"]] == [claim_id]


def test_staff_cannot_claim(person, held_item):
    for guard in (held_item["guard"], person(UserRole.ADMIN)):
        detail = guard.get(f"/items/{held_item['id']}").json()
        assert detail["can_claim"] is False  # so the page shows no "Is this yours?" box
        assert claim(guard, held_item["id"]).status_code == 403
