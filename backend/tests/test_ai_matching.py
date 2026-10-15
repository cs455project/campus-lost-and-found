"""Tests for AI Phase A: the job queue and semantic top-K matching.

The tests use the small built-in "hashing" embedder (set in conftest.py),
so they need no model download and give the same results every time.
Jobs are run by calling queue.run_all() directly, the way the background
runner does every few seconds.
"""

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.core.clock import today
from app.core.config import get_settings
from app.models import AiJob, Category, Item, MatchSuggestion, Office, UserRole
from app.services.ai import embeddings, queue


@pytest.fixture
def place(db):
    bags = Category(name="AI Bags", slug="ai-bags", sort_order=1)
    bottles = Category(name="AI Bottles", slug="ai-bottles", sort_order=2)
    office = Office(name="AI Office", location_description="Gate", opening_hours="24 hours")
    db.add_all([bags, bottles, office])
    db.commit()
    return {"bags": bags.id, "bottles": bottles.id, "office": office.id}


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT):
        count["n"] += 1
        email = f"aiperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"A{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=f"Person {count['n']}", **extra)
        return sign_in(email)

    return _person


BACKPACK = {"title": "Black Wildcraft backpack", "colour": "Black", "brand": "Wildcraft",
            "description": "Black Wildcraft backpack with a laptop sleeve and a torn zip."}
BOTTLE = {"title": "Blue steel water bottle", "colour": "Blue", "brand": "Milton",
          "description": "Blue Milton steel bottle with a dent on the cap."}


def body(place, details, category="bags", days_ago=0, **extra):
    return {**details, "category_id": place[category], "location_text": "Lecture Hall Complex",
            "happened_on": str(today() - timedelta(days=days_ago)), **extra}


def report_lost(client, place, details=BACKPACK, **kw):
    return client.post("/items/lost", json=body(place, details, **kw)).json()["id"]


def report_held(finder, guard, place, details=BACKPACK, **kw):
    item_id = finder.post("/items/found", json=body(place, details, dropoff_office_id=place["office"], **kw)).json()["id"]
    assert guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["office"]}).status_code == 200
    return item_id


def similar(client, item_id):
    response = client.get(f"/items/{item_id}/similar")
    assert response.status_code == 200
    return response.json()


def test_lost_report_gets_the_matching_found_item(db, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    found_id = report_held(finder, guard, place)
    lost_id = report_lost(owner, place)

    assert similar(owner, lost_id)["status"] == "pending"  # the AI hasn't run yet
    queue.run_all(db)

    result = similar(owner, lost_id)
    assert result["enabled"] is True and result["status"] == "ready"
    top = result["matches"][0]
    assert top["item"]["id"] == found_id
    assert top["score"] > 0.5
    assert "same category" in top["reason"].lower()


def test_matches_are_stored_in_both_directions(db, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    lost_id = report_lost(owner, place)
    found_id = report_held(finder, guard, place)
    queue.run_all(db)
    # The found item (seen by staff) lists the lost report, even though the
    # lost report was made first.
    assert [m["item"]["id"] for m in similar(guard, found_id)["matches"]] == [lost_id]
    assert [m["item"]["id"] for m in similar(owner, lost_id)["matches"]] == [found_id]


def test_unrelated_items_are_not_suggested(db, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place, BOTTLE, category="bottles")
    lost_id = report_lost(owner, place)
    queue.run_all(db)
    assert similar(owner, lost_id)["matches"] == []


def test_found_item_appears_only_after_a_guard_receives_it(db, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    lost_id = report_lost(owner, place)
    found_id = finder.post("/items/found", json=body(place, BACKPACK, dropoff_office_id=place["office"])).json()["id"]
    queue.run_all(db)
    assert similar(owner, lost_id)["matches"] == []  # not handed in yet: private

    guard.post(f"/staff/items/{found_id}/receive", json={"office_id": place["office"]})
    queue.run_all(db)
    assert [m["item"]["id"] for m in similar(owner, lost_id)["matches"]] == [found_id]


def test_only_the_top_k_are_kept(db, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    for _ in range(get_settings().match_top_k + 2):
        report_held(finder, guard, place)
    lost_id = report_lost(owner, place)
    queue.run_all(db)
    assert len(similar(owner, lost_id)["matches"]) == get_settings().match_top_k


def test_found_before_it_was_lost_is_not_a_match(db, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    report_held(finder, guard, place, days_ago=20)  # found 20 days ago...
    lost_id = report_lost(owner, place)  # ...but lost today
    queue.run_all(db)
    assert similar(owner, lost_id)["matches"] == []


def test_saving_twice_queues_one_job(db, person, place):
    owner = person()
    lost_id = report_lost(owner, place)
    owner.put(f"/items/{lost_id}", json=body(place, {**BACKPACK, "colour": "Dark grey"}))
    waiting = db.scalar(select(func.count()).select_from(AiJob)
                        .where(AiJob.item_id == lost_id, AiJob.status == "queued"))
    assert waiting == 1


def test_editing_a_report_updates_its_matches(db, person, place):
    owner, finder, guard = person(), person(), person(UserRole.GUARD)
    bottle_id = report_held(finder, guard, place, BOTTLE, category="bottles")
    lost_id = report_lost(owner, place)
    queue.run_all(db)
    assert similar(owner, lost_id)["matches"] == []

    # The owner realises they lost the bottle, not the bag.
    owner.put(f"/items/{lost_id}", json=body(place, BOTTLE, category="bottles"))
    queue.run_all(db)
    assert [m["item"]["id"] for m in similar(owner, lost_id)["matches"]] == [bottle_id]


def test_cancelled_found_report_disappears_from_suggestions(db, person, place):
    owner, finder = person(), person()
    lost_id = report_lost(owner, place)
    found_id = finder.post("/items/found", json=body(place, BACKPACK, dropoff_office_id=place["office"])).json()["id"]
    queue.run_all(db)
    assert db.scalar(select(func.count()).select_from(MatchSuggestion)
                     .where(MatchSuggestion.candidate_id == found_id)) == 1
    assert finder.delete(f"/items/{found_id}").status_code == 204
    assert db.scalar(select(func.count()).select_from(MatchSuggestion)
                     .where(MatchSuggestion.candidate_id == found_id)) == 0
    assert similar(owner, lost_id)["matches"] == []


def test_a_failing_embedder_marks_the_item_failed_but_the_page_works(db, person, place):
    class Broken:
        name = "broken"

        def embed(self, texts):
            raise RuntimeError("model crashed")

    owner = person()
    lost_id = report_lost(owner, place)
    embeddings.set_embedder(Broken())
    try:
        queue.run_all(db)
    finally:
        embeddings.set_embedder(None)
    assert similar(owner, lost_id) == {"enabled": True, "status": "failed", "matches": []}
    job = db.scalar(select(AiJob).where(AiJob.item_id == lost_id))
    assert job.status == "failed" and "model crashed" in job.last_error


def test_report_text_never_includes_personal_details(db, person, place):
    from app.services.ai.semantic import item_text

    owner = person()
    lost_id = report_lost(owner, place)
    text = item_text(db.get(Item, lost_id)).lower()
    assert "wildcraft" in text and "aiperson" not in text and "@" not in text
