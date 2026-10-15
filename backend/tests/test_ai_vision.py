"""Tests for AI Phase C: describing item photos.

A FakeVision stands in for Gemini (no API calls, no quota). Real small
images are made with Pillow, because the app shrinks photos before sending.
"""

import io

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.clock import today
from app.core.config import get_settings
from app.models import AgentRun, Category, Item, Office, UserRole
from app.services.ai import queue, vision


def jpeg(colour=(10, 10, 10), size=(1600, 1200)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, format="JPEG")
    return out.getvalue()


@pytest.fixture
def place(db, monkeypatch):
    bags = Category(name="AI Bags", slug="ai-bags", sort_order=1)
    ids = Category(name="AI ID cards", slug="ai-ids", sort_order=2)
    office = Office(name="AI Office", location_description="Gate", opening_hours="24 hours")
    db.add_all([bags, ids, office])
    db.commit()
    monkeypatch.setattr(get_settings(), "ai_skip_photo_categories", "ai-ids")
    return {"bags": bags.id, "ids": ids.id, "office": office.id}


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT):
        count["n"] += 1
        email = f"visionperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": f"V{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=f"Person {count['n']}", **extra)
        return sign_in(email)

    return _person


class FakeVision:
    model = "fake-vision"

    def __init__(self, answer=None, fail=False):
        self.answer = answer or {
            "object": "backpack", "colour": "black", "brand": "Wildcraft",
            "marks": ["torn zip", "keychain"], "description": "A black Wildcraft backpack with a torn zip.",
            "contains_personal_info": False}
        self.fail, self.calls = fail, []

    def describe(self, images, report):
        self.calls.append((images, report))
        if self.fail:
            raise RuntimeError("gemini down")
        return self.answer


@pytest.fixture
def use_vision():
    def _use(client):
        vision.set_vision(client)
        return client
    yield _use
    vision.reset_vision()


def report(client, place, category="bags", title="Bag"):
    return client.post("/items/lost", json={
        "title": title, "description": "a bag", "category_id": place[category],
        "location_text": "LHC", "happened_on": str(today())}).json()["id"]


def add_photo(client, item_id, data=None):
    response = client.post(f"/items/{item_id}/photos",
                           files={"file": ("p.jpg", io.BytesIO(data or jpeg()), "image/jpeg")})
    assert response.status_code == 201


def test_photos_get_an_ai_description_shown_on_the_item_page(db, person, place, use_vision):
    fake = use_vision(FakeVision())
    owner = person()
    item_id = report(owner, place)
    add_photo(owner, item_id)
    queue.run_all(db)

    detail = owner.get(f"/items/{item_id}").json()
    assert detail["ai_description"] == "A black Wildcraft backpack with a torn zip."
    assert detail["ai_marks"] == ["torn zip", "keychain"]
    # The photo was shrunk before sending, and nothing personal was sent.
    images, sent_report = fake.calls[0]
    assert max(Image.open(io.BytesIO(images[0])).size) <= vision.MAX_SIDE
    assert "visionperson" not in str(sent_report) and "id" not in sent_report
    run = db.scalar(select(AgentRun).where(AgentRun.item_id == item_id))
    assert run.outcome == "ok" and run.llm_calls == 1 and run.steps[0]["tool"] == "describe_photos"


def test_the_ai_description_helps_matching(db, person, place, use_vision):
    """The finder typed only "bag", but the photo shows Wildcraft: that word is now in the
    text used for matching."""
    from app.services.ai.semantic import item_text

    from app.services.ai.embeddings import get_embedder

    use_vision(FakeVision())
    owner = person()
    item_id = report(owner, place)
    add_photo(owner, item_id)
    queue.run_all(db)
    item = db.get(Item, item_id)
    db.refresh(item, ["embedding"])
    text = item_text(item)
    assert "wildcraft" in text.lower()
    # The stored vector was made from that text, AI description included.
    assert item.embedding == pytest.approx(get_embedder().embed([text])[0], abs=1e-5)


def test_same_photos_are_described_only_once(db, person, place, use_vision):
    fake = use_vision(FakeVision())
    owner = person()
    item_id = report(owner, place)
    add_photo(owner, item_id)
    queue.run_all(db)
    # Editing only the text: the photos are the same, so no new AI call.
    owner.put(f"/items/{item_id}", json={"title": "Black bag", "description": "a black bag",
                                         "category_id": place["bags"], "location_text": "LHC",
                                         "happened_on": str(today())})
    queue.run_all(db)
    assert len(fake.calls) == 1
    # A new photo: described again.
    add_photo(owner, item_id, jpeg((200, 0, 0)))
    queue.run_all(db)
    assert len(fake.calls) == 2


def test_id_card_photos_are_never_sent_to_the_ai(db, person, place, use_vision):
    fake = use_vision(FakeVision())
    owner = person()
    item_id = report(owner, place, category="ids", title="Student ID card")
    add_photo(owner, item_id)
    queue.run_all(db)
    assert fake.calls == []
    assert owner.get(f"/items/{item_id}").json()["ai_description"] is None


def test_a_photo_with_personal_info_gets_no_description(db, person, place, use_vision):
    use_vision(FakeVision({"description": "A phone showing a name", "contains_personal_info": True}))
    owner = person()
    item_id = report(owner, place)
    add_photo(owner, item_id)
    queue.run_all(db)
    assert owner.get(f"/items/{item_id}").json()["ai_description"] is None
    assert db.get(Item, item_id).ai_attributes == {"personal_info": True}


def test_vision_failure_leaves_no_description_but_matching_still_runs(db, person, place, use_vision):
    use_vision(FakeVision(fail=True))
    owner = person()
    item_id = report(owner, place)
    add_photo(owner, item_id)
    queue.run_all(db)
    detail = owner.get(f"/items/{item_id}").json()
    assert detail["ai_description"] is None
    assert owner.get(f"/items/{item_id}/similar").json()["status"] == "ready"
    run = db.scalar(select(AgentRun).where(AgentRun.item_id == item_id))
    assert run.outcome == "error" and "gemini down" in run.error


def test_removing_all_photos_removes_the_description(db, person, place, use_vision):
    use_vision(FakeVision())
    owner = person()
    item_id = report(owner, place)
    add_photo(owner, item_id)
    queue.run_all(db)
    photo_id = owner.get(f"/items/{item_id}").json()["photo_ids"][0]
    owner.delete(f"/items/{item_id}/photos/{photo_id}")
    queue.run_all(db)
    assert owner.get(f"/items/{item_id}").json()["ai_description"] is None


def test_json_answers_are_parsed_even_with_extra_text():
    assert vision.parse_json('```json\n{"object": "bottle"}\n```') == {"object": "bottle"}
    with pytest.raises(ValueError):
        vision.parse_json("I cannot see an item.")
