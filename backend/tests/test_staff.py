"""Tests for the staff desk: receiving, walk-ins, transfers, disposal,
custody history, the staff item list and who may do what."""

import pytest

from app.core.clock import today
from app.models import Category, Office, UserRole


@pytest.fixture
def place(db):
    bags = Category(name="Test Bags", slug="test-bags", sort_order=1)
    main = Office(name="Test Main", location_description="Gate", opening_hours="24 hours", is_main=False)
    sub = Office(name="Test Sub", location_description="Library", opening_hours="8-8")
    closed = Office(name="Test Closed", location_description="x", opening_hours="x", is_active=False)
    db.add_all([bags, main, sub, closed])
    db.commit()
    return {"bags": bags.id, "main": main.id, "sub": sub.id, "closed": closed.id}


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT, name=None):
        count["n"] += 1
        email = f"staffperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"S{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=name or f"Person {count['n']}", **extra)
        return sign_in(email)

    return _person


def item_fields(place, **changes):
    return {
        "title": "Black backpack",
        "description": "Wildcraft backpack with a laptop sleeve.",
        "category_id": place["bags"],
        "location_text": "Library",
        "happened_on": str(today()),
        **changes,
    }


def report_found(client, place, **changes):
    body = {**item_fields(place), "dropoff_office_id": place["main"], **changes}
    return client.post("/items/found", json=body).json()["id"]


# ---------------- Permissions ----------------


def test_students_cannot_use_the_staff_desk(person, place):
    student = person()
    item_id = report_found(student, place)
    assert student.get("/staff/items").status_code == 403
    assert student.post(f"/staff/items/{item_id}/receive", json={"office_id": place["main"]}).status_code == 403
    assert student.post("/staff/items/walk-in", json={**item_fields(place), "office_id": place["main"]}).status_code == 403


def test_admin_has_every_guard_power(person, place):
    admin = person(UserRole.ADMIN)
    item_id = report_found(person(), place)
    response = admin.post(f"/staff/items/{item_id}/receive", json={"office_id": place["main"]})
    assert response.status_code == 200


# ---------------- Receiving ----------------


def test_receive_records_who_where_and_when(person, place):
    finder, guard = person(), person(UserRole.GUARD, name="Ramesh Kumar")
    item_id = report_found(finder, place)

    body = guard.post(f"/staff/items/{item_id}/receive",
                      json={"office_id": place["main"], "condition_note": "Zip slightly torn"}).json()
    assert body["status"] == "IN_CUSTODY"
    assert body["current_office"]["name"] == "Test Main"
    assert body["received_by_name"] == "Ramesh Kumar"

    [event] = body["custody_events"]
    assert event["event_type"] == "RECEIVED"
    assert event["to_office"]["name"] == "Test Main"
    assert event["actor_name"] == "Ramesh Kumar"
    assert event["note"] == "Zip slightly torn"


def test_receive_at_a_different_office_than_chosen(person, place):
    guard = person(UserRole.GUARD)
    item_id = report_found(person(), place)  # finder chose main
    body = guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["sub"]}).json()
    assert body["current_office"]["name"] == "Test Sub"


def test_receive_only_once_and_only_open_offices(person, place):
    guard = person(UserRole.GUARD)
    item_id = report_found(person(), place)
    closed = guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["closed"]})
    assert closed.status_code == 400
    guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["main"]})
    again = guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["main"]})
    assert again.status_code == 403


# ---------------- Walk-ins ----------------


def test_walk_in_goes_straight_to_the_found_page(person, place):
    guard, student = person(UserRole.GUARD, name="Ramesh Kumar"), person()
    response = guard.post("/staff/items/walk-in", json={
        **item_fields(place, title="Grey phone"),
        "office_id": place["sub"],
        "handed_in_by": "Canteen staff",
    })
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "IN_CUSTODY"
    assert body["current_office"]["name"] == "Test Sub"
    assert body["received_by_name"] == "Ramesh Kumar"
    assert body["custody_events"][0]["event_type"] == "REGISTERED"
    assert body["custody_events"][0]["note"] == "Handed in by: Canteen staff"

    found = student.get("/items", params={"kind": "FOUND"}).json()
    assert [i["title"] for i in found["items"]] == ["Grey phone"]


def test_walk_in_needs_an_office(person, place):
    guard = person(UserRole.GUARD)
    response = guard.post("/staff/items/walk-in", json=item_fields(place))
    assert response.status_code == 422


# ---------------- Transfers and disposal ----------------


def received_item(guard, place, finder):
    item_id = report_found(finder, place)
    guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["sub"]})
    return item_id


def test_transfer_moves_the_item_and_logs_it(person, place):
    guard = person(UserRole.GUARD)
    item_id = received_item(guard, place, person())

    same = guard.post(f"/staff/items/{item_id}/transfer", json={"to_office_id": place["sub"]})
    assert same.status_code == 400

    body = guard.post(f"/staff/items/{item_id}/transfer",
                      json={"to_office_id": place["main"], "note": "Weekly collection"}).json()
    assert body["current_office"]["name"] == "Test Main"
    moved = body["custody_events"][-1]
    assert moved["event_type"] == "TRANSFERRED"
    assert moved["from_office"]["name"] == "Test Sub"
    assert moved["to_office"]["name"] == "Test Main"


def test_cannot_transfer_before_receiving(person, place):
    guard = person(UserRole.GUARD)
    item_id = report_found(person(), place)
    assert guard.post(f"/staff/items/{item_id}/transfer", json={"to_office_id": place["sub"]}).status_code == 403


def test_dispose_and_donate(person, place):
    guard, student = person(UserRole.GUARD), person()
    item_id = received_item(guard, place, person())

    no_note = guard.post(f"/staff/items/{item_id}/dispose", json={"outcome": "DONATED"})
    assert no_note.status_code == 422

    body = guard.post(f"/staff/items/{item_id}/dispose",
                      json={"outcome": "DONATED", "note": "Given to the NSS clothes drive"}).json()
    assert body["status"] == "DONATED"
    assert body["current_office"] is None
    last = body["custody_events"][-1]
    assert last["event_type"] == "DONATED" and last["from_office"]["name"] == "Test Sub"

    # Gone from the Found page, hidden from students, and final.
    assert student.get("/items", params={"kind": "FOUND"}).json()["total"] == 0
    assert student.get(f"/items/{item_id}").status_code == 404
    again = guard.post(f"/staff/items/{item_id}/dispose", json={"outcome": "DISPOSED", "note": "again"})
    assert again.status_code == 403


# ---------------- What staff see ----------------


def test_staff_detail_shows_reporter_and_history_students_do_not(person, place):
    guard, student = person(UserRole.GUARD), person()
    finder = person(name="Aman Verma")
    item_id = received_item(guard, place, finder)

    staff_view = guard.get(f"/items/{item_id}").json()
    assert staff_view["reporter"]["full_name"] == "Aman Verma"
    assert staff_view["can_transfer"] and staff_view["can_dispose"]

    public_view = student.get(f"/items/{item_id}").json()
    assert public_view["reporter"] is None
    assert public_view["custody_events"] == []
    assert not public_view["can_transfer"]


# ---------------- The staff list ----------------


def test_staff_list_stages_offices_and_reference_search(person, place):
    guard, finder = person(UserRole.GUARD), person()
    waiting_main = report_found(finder, place, title="Blue bottle")
    waiting_sub = report_found(finder, place, title="Red umbrella", dropoff_office_id=place["sub"])
    held = received_item(guard, place, finder)

    def ids(**params):
        return [i["id"] for i in guard.get("/staff/items", params=params).json()["items"]]

    assert set(ids(stage="waiting")) == {waiting_main, waiting_sub}
    assert ids(stage="waiting", office_id=place["sub"]) == [waiting_sub]
    assert ids(stage="held") == [held]
    assert ids(stage="held", office_id=place["main"]) == []

    # A finder at the desk quotes their reference.
    assert ids(stage="waiting", q=f"FND-{waiting_main:04d}") == [waiting_main]
    assert ids(stage="waiting", q=str(waiting_main)) == [waiting_main]
    assert ids(stage="waiting", q="umbrel") == [waiting_sub]

    # The staff list includes who reported it (students never get this).
    row = guard.get("/staff/items", params={"stage": "waiting", "q": "umbrella"}).json()["items"][0]
    assert row["reporter"]["email"] == "staffperson2@iitk.ac.in"

    guard.post(f"/staff/items/{held}/dispose", json={"outcome": "DISPOSED", "note": "Broken, unclaimed"})
    assert ids(stage="closed") == [held]
    assert ids(stage="closed", office_id=place["sub"]) == [held]  # where it last was


def test_staff_list_rejects_unknown_stage(person):
    assert person(UserRole.GUARD).get("/staff/items", params={"stage": "lost"}).status_code == 422
