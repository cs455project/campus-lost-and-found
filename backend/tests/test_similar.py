"""Tests for "Similar items found".

There is no real matcher yet, so these tests plug in a FAKE matcher that
returns whatever we tell it. That lets us check the safety rules in
services/matching.py: whatever a matcher suggests, users only ever see
public items of the opposite kind, and a broken matcher never breaks a page.
"""

import pytest

from app.core.clock import today
from app.models import Category, Office, UserRole
from app.services import matching
from app.services.matching import Match


class FakeMatcher:
    enabled = True

    def __init__(self):
        self.suggest: list[Match] = []
        self.saved: list[int] = []
        self.crash = False

    def find_similar(self, db, item, limit):
        if self.crash:
            raise RuntimeError("model server down")
        return self.suggest

    def item_saved(self, db, item):
        if self.crash:
            raise RuntimeError("model server down")
        self.saved.append(item.id)

    def item_removed(self, item_id):
        pass


@pytest.fixture
def fake():
    matcher = FakeMatcher()
    matching.set_matcher(matcher)
    yield matcher
    matching.set_matcher(None)  # back to the setting (semantic) for other tests


@pytest.fixture
def place(db):
    bags = Category(name="Test Bags", slug="test-bags", sort_order=1)
    office = Office(name="Test Main", location_description="Gate", opening_hours="24 hours")
    db.add_all([bags, office])
    db.commit()
    return {"bags": bags.id, "office": office.id}


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT):
        count["n"] += 1
        email = f"simperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"M{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=f"Person {count['n']}", **extra)
        return sign_in(email)

    return _person


def fields(place, title="Black backpack"):
    return {"title": title, "description": "A backpack.", "category_id": place["bags"],
            "location_text": "Library", "happened_on": str(today())}


def lost(client, place, title="Black backpack"):
    return client.post("/items/lost", json=fields(place, title)).json()["id"]


def found(client, place, title="Black backpack"):
    return client.post("/items/found", json={**fields(place, title), "dropoff_office_id": place["office"]}).json()["id"]


def held(finder, guard, place, title="Black backpack"):
    item_id = found(finder, place, title)
    guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["office"]})
    return item_id


# ---------------- The placeholder (MATCHING_BACKEND=placeholder) ----------------


def test_placeholder_says_matching_is_off(person, place):
    matching.set_matcher(matching.PlaceholderMatcher())  # MATCHING_BACKEND=placeholder
    try:
        owner = person()
        lost_id = lost(owner, place)
        assert owner.get(f"/items/{lost_id}").json()["can_see_similar"] is True
        assert owner.get(f"/items/{lost_id}/similar").json() == {"enabled": False, "status": None, "matches": []}
    finally:
        matching.set_matcher(None)


# ---------------- Who can see suggestions ----------------


def test_only_reporter_and_staff_see_suggestions(person, place):
    owner, stranger, guard = person(), person(), person(UserRole.GUARD)
    lost_id = lost(owner, place)
    assert stranger.get(f"/items/{lost_id}").json()["can_see_similar"] is False
    assert stranger.get(f"/items/{lost_id}/similar").status_code == 403
    assert guard.get(f"/items/{lost_id}/similar").status_code == 200


def test_no_suggestions_once_closed(person, place):
    owner = person()
    lost_id = lost(owner, place)
    owner.post(f"/items/{lost_id}/close", json={"outcome": "RECOVERED"})
    assert owner.get(f"/items/{lost_id}/similar").status_code == 403


# ---------------- Safety rules around any matcher ----------------


def test_shows_matcher_results_best_first(fake, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    lost_id = lost(owner, place)
    a = held(finder, guard, place, "Backpack A")
    b = held(finder, guard, place, "Backpack B")
    fake.suggest = [Match(a, 0.4), Match(b, 0.9, "same category")]

    body = owner.get(f"/items/{lost_id}/similar").json()
    assert body["enabled"] is True
    assert [m["item"]["id"] for m in body["matches"]] == [b, a]
    assert body["matches"][0]["reason"] == "same category"
    # Cards never carry who reported the item.
    assert "reporter" not in body["matches"][0]["item"]


def test_hidden_and_wrong_items_are_dropped(fake, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    lost_id = lost(owner, place)
    good = held(finder, guard, place)
    not_handed_in = found(finder, place)  # hidden until a guard receives it
    other_lost = lost(person(), place)  # same kind: a lost item can't match a lost item
    fake.suggest = [Match(good, 0.5), Match(not_handed_in, 0.99), Match(other_lost, 0.98),
                    Match(lost_id, 1.0),  # the item itself
                    Match(999999, 0.97),  # doesn't exist
                    Match(good, 0.1)]  # duplicate

    body = owner.get(f"/items/{lost_id}/similar").json()
    assert [m["item"]["id"] for m in body["matches"]] == [good]
    assert body["matches"][0]["score"] == 0.5  # the better of the two duplicates


def test_scores_are_clamped_and_results_limited(fake, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    lost_id = lost(owner, place)
    ids = [held(finder, guard, place, f"Bag {n}") for n in range(8)]
    fake.suggest = [Match(i, 5.0 - n) for n, i in enumerate(ids)]  # scores far above 1

    matches = owner.get(f"/items/{lost_id}/similar").json()["matches"]
    assert len(matches) == matching.MAX_RESULTS
    assert all(0.0 <= m["score"] <= 1.0 for m in matches)


def test_found_item_gets_lost_reports(fake, person, place):
    finder, owner = person(), person()
    found_id = found(finder, place)  # the finder sees suggestions even before drop-off
    lost_id = lost(owner, place)
    fake.suggest = [Match(lost_id, 0.8)]
    assert [m["item"]["id"] for m in finder.get(f"/items/{found_id}/similar").json()["matches"]] == [lost_id]


def test_broken_matcher_never_breaks_pages(fake, person, place):
    fake.crash = True
    owner = person()
    response = owner.post("/items/lost", json=fields(place))  # item_saved crashes
    assert response.status_code == 201  # ...but the report is still saved
    lost_id = response.json()["id"]
    assert owner.get(f"/items/{lost_id}/similar").json()["matches"] == []


def test_matcher_is_told_about_new_items(fake, person, place):
    owner = person()
    lost_id = lost(owner, place)
    assert lost_id in fake.saved
