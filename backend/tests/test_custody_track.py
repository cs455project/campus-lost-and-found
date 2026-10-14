"""US-13 / SCRUM-17: the SIS Guard custody track.

FR-CUS-01/02: a found report goes on the Guard track and waits for drop-off
              (AWAITING_DROPOFF, the D1 "AWAITING_DEPOSIT" state).
FR-CUS-08 / BR-02: bicycle reports always use the Guard track.
"""

import pytest

from app.core.clock import today
from app.models import Category, Item, ItemStatus, Office, UserRole
from app.seed import seed_categories
from app.services import items as items_service


@pytest.fixture
def place(db):
    cycle = Category(name="Test Cycle", slug="test-cycle", sort_order=1, custody_required=True)
    keys = Category(name="Test Keys", slug="test-keys", sort_order=2)
    office = Office(name="Track Office", location_description="Gate", opening_hours="24 hours")
    db.add_all([cycle, keys, office])
    db.commit()
    return {"cycle": cycle, "keys": keys, "office": office.id}


@pytest.fixture
def student(make_user, sign_in):
    make_user("tracker@iitk.ac.in", full_name="Track Student", roll_number="T0001")
    return sign_in("tracker@iitk.ac.in")


def found_body(category_id, office_id, **extra):
    return {"title": "Hero cycle", "description": "Black Hero cycle with a basket.", "category_id": category_id,
            "location_text": "Hall 3 stand", "happened_on": str(today()), "dropoff_office_id": office_id, **extra}


def test_bicycle_report_is_always_on_guard_track(student, place, db):
    response = student.post("/items/found", json=found_body(place["cycle"].id, place["office"]))
    assert response.status_code == 201, response.text
    item = db.get(Item, response.json()["id"])
    assert item.status == ItemStatus.AWAITING_DROPOFF
    assert items_service.custody_track_for(place["cycle"]) == items_service.GUARD_TRACK


def test_finder_cannot_skip_the_guard_track(student, place, db):
    # Extra fields asking for another track or status are ignored, never trusted.
    body = found_body(place["cycle"].id, place["office"], status="IN_CUSTODY", track="DIRECT")
    response = student.post("/items/found", json=body)
    assert response.status_code == 201
    assert db.get(Item, response.json()["id"]).status == ItemStatus.AWAITING_DROPOFF


def test_other_categories_use_guard_track_too(student, place, db):
    response = student.post("/items/found", json=found_body(place["keys"].id, place["office"], title="Keys"))
    assert db.get(Item, response.json()["id"]).status == ItemStatus.AWAITING_DROPOFF


def test_awaiting_item_is_not_claimable_until_a_guard_receives_it(student, place, make_user, sign_in):
    item_id = student.post("/items/found", json=found_body(place["cycle"].id, place["office"])).json()["id"]
    make_user("owner.track@iitk.ac.in", full_name="Owner", roll_number="T0002")
    owner = sign_in("owner.track@iitk.ac.in")
    # Not on the public Found page yet, so it looks like it doesn't exist.
    assert owner.post(f"/items/{item_id}/claims", json={"description": "Mine"}).status_code == 404

    make_user("guard.track@gmail.com", role=UserRole.GUARD, full_name="Guard")
    guard = sign_in("guard.track@gmail.com")
    assert guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["office"]}).status_code == 200
    assert owner.post(f"/items/{item_id}/claims", json={"description": "Mine"}).status_code == 201


def test_categories_endpoint_shows_custody_rule(student, place):
    rows = {c["slug"]: c for c in student.get("/categories").json()}
    assert rows["test-cycle"]["custody_required"] is True
    assert rows["test-keys"]["custody_required"] is False


def test_seed_marks_cycle_as_custody_required(db):
    seed_categories(db)
    db.flush()
    cycle = db.query(Category).filter_by(slug="cycle").one()
    assert cycle.custody_required is True
    assert db.query(Category).filter_by(slug="keys").one().custody_required is False
