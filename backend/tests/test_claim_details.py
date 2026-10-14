"""US-09 / SCRUM-41 and US-15 / SCRUM-46: identifying details on a claim.

FR-CLM-02  a claim carries identifying details remembered by the claimant
FR-CLM-03  they are never shown on the public listing
FR-CLM-11  competing claimants never see each other's details
FR-CLM-12  verdicts are visible to staff only
FR-CUS-06  a guard can record details checked in person
"""

import pytest

from app.core.clock import today
from app.models import Category, Office, UserRole
from app.services import email as email_service

DETAILS = ["Sticker of a cat on the back", "Rs 200 and a library card inside", "Zip pull is broken"]


@pytest.fixture(autouse=True)
def no_email(monkeypatch):
    monkeypatch.setattr(email_service, "send_email", lambda *args, **kwargs: None)


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT, name=None):
        count["n"] += 1
        email = f"detailperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"D{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=name or f"Detail Person {count['n']}", **extra)
        return sign_in(email)

    return _person


@pytest.fixture
def held_item(db, person):
    bags = Category(name="Detail Bags", slug="detail-bags", sort_order=1)
    office = Office(name="Detail Office", location_description="Gate", opening_hours="24 hours")
    db.add_all([bags, office])
    db.commit()
    finder, guard = person(), person(UserRole.GUARD, name="Guard Singh")
    body = {"title": "Blue bag", "description": "Blue Skybag.", "category_id": bags.id, "location_text": "LHC",
            "happened_on": str(today()), "dropoff_office_id": office.id}
    item_id = finder.post("/items/found", json=body).json()["id"]
    assert guard.post(f"/staff/items/{item_id}/receive", json={"office_id": office.id}).status_code == 200
    return {"id": item_id, "guard": guard}


def claim(client, item_id, details=DETAILS, description="It's my bag."):
    return client.post(f"/items/{item_id}/claims", json={"description": description, "details": details})


def test_claim_stores_details_for_the_claimant(person, held_item):
    owner = person()
    body = claim(owner, held_item["id"]).json()
    assert [d["text"] for d in body["details"]] == DETAILS
    assert all(d["source"] == "CLAIMANT" for d in body["details"])
    # The claimant never sees verdicts, even null ones are just "not shown".
    assert all(d["verdict"] is None and d["checked_by_name"] is None for d in body["details"])
    assert body["can_check_details"] is False


def test_details_are_optional(person, held_item):
    response = person().post(f"/items/{held_item['id']}/claims", json={"description": "Mine"})
    assert response.status_code == 201
    assert response.json()["details"] == []


@pytest.mark.parametrize("bad", [
    ["   "],                                  # blank
    ["a" * 201],                              # too long
    ["Red strap", "red strap"],               # same detail twice
    ["1", "2", "3", "4", "5", "6"],           # more than 5
])
def test_invalid_details_are_refused(person, held_item, bad):
    assert claim(person(), held_item["id"], details=bad).status_code == 422


def test_details_are_trimmed(person, held_item):
    body = claim(person(), held_item["id"], details=["  red strap  "]).json()
    assert body["details"][0]["text"] == "red strap"


def test_details_never_appear_on_the_item_page(person, held_item):
    claim(person(), held_item["id"])
    page = person().get(f"/items/{held_item['id']}").text
    for detail in DETAILS:
        assert detail not in page


def test_competing_claimant_cannot_open_another_claim(person, held_item):
    first = claim(person(), held_item["id"]).json()
    rival = person()
    assert claim(rival, held_item["id"], details=["Something else"]).status_code == 201
    assert rival.get(f"/claims/{first['id']}").status_code == 404


def test_guard_checks_details_only_during_review(person, held_item):
    owner, guard = person(), held_item["guard"]
    body = claim(owner, held_item["id"]).json()
    detail_id = body["details"][0]["id"]
    url = f"/staff/claims/{body['id']}/details/{detail_id}"

    # Not before review starts.
    assert guard.put(url, json={"verdict": "MATCH"}).status_code == 403
    assert guard.post(f"/staff/claims/{body['id']}/start-review").json()["can_check_details"] is True

    checked = guard.put(url, json={"verdict": "MATCH"}).json()
    first = checked["details"][0]
    assert first["verdict"] == "MATCH" and first["checked_by_name"] == "Guard Singh" and first["checked_at"]

    # The claimant still doesn't see the verdict.
    mine = owner.get(f"/claims/{body['id']}").json()
    assert mine["details"][0]["verdict"] is None

    # Clearing a mistaken check.
    cleared = guard.put(url, json={"verdict": None}).json()["details"][0]
    assert cleared["verdict"] is None and cleared["checked_by_name"] is None

    # Not after a decision.
    guard.put(url, json={"verdict": "MATCH"})
    guard.post(f"/staff/claims/{body['id']}/approve", json={})
    assert guard.put(url, json={"verdict": "NO_MATCH"}).status_code == 403


def test_students_cannot_check_details(person, held_item):
    owner = person()
    body = claim(owner, held_item["id"]).json()
    held_item["guard"].post(f"/staff/claims/{body['id']}/start-review")
    url = f"/staff/claims/{body['id']}/details/{body['details'][0]['id']}"
    assert owner.put(url, json={"verdict": "MATCH"}).status_code == 403


def test_detail_from_another_claim_is_not_found(person, held_item):
    guard = held_item["guard"]
    one = claim(person(), held_item["id"]).json()
    two = claim(person(), held_item["id"], details=["Other"]).json()
    guard.post(f"/staff/claims/{one['id']}/start-review")
    url = f"/staff/claims/{one['id']}/details/{two['details'][0]['id']}"
    assert guard.put(url, json={"verdict": "MATCH"}).status_code == 404


def test_guard_records_a_detail_told_in_person(person, held_item):
    guard = held_item["guard"]
    body = claim(person(), held_item["id"], details=[]).json()
    guard.post(f"/staff/claims/{body['id']}/start-review")
    guard.post(f"/staff/claims/{body['id']}/request-visit", json={"note": "Come in with the bill"})
    added = guard.post(f"/staff/claims/{body['id']}/details",
                       json={"text": "Knew the laptop serial ends 4X9", "verdict": "MATCH"})
    assert added.status_code == 201, added.text
    detail = added.json()["details"][0]
    assert detail["source"] == "DESK" and detail["verdict"] == "MATCH"
    assert guard.post(f"/staff/claims/{body['id']}/details", json={"text": " ", "verdict": "MATCH"}).status_code == 422
