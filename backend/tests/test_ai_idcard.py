"""Tests for AI Phase D: finding the owner of a found ID card.

A FakeOCR stands in for RapidOCR (returns chosen text lines), and a FakeLLM
for the agent. What's tested: the detection rules, who gets emailed and
when, the link to the owner's own lost report, photo privacy, and that the
LLM never sees a roll number or an email address.
"""

import io
import json

import pytest
from PIL import Image
from sqlalchemy import select

from app.core.clock import today
from app.core.config import get_settings
from app.models import Category, Item, MatchSuggestion, Office, OwnerAlert, UserRole
from app.services import email as email_service
from app.services.ai import idcard, llm, queue
from app.services.ai.idcard import Identifier, find_identifiers

CARD = ["INDIAN INSTITUTE OF TECHNOLOGY KANPUR", "Name: Rahul Kumar", "Roll No: 220345",
        "Valid upto 2027", "Kalyanpur, Kanpur 208016", "Phone 9876543210"]


# ---------------- Detection rules (plain code, no AI) ----------------


def test_a_labelled_roll_number_is_found():
    assert find_identifiers(CARD) == [Identifier("ROLL", "220345")]


@pytest.mark.parametrize("lines, roll", [
    (["RollNo:220345"], "220345"),           # OCR drops the spaces
    (["ROLL NUMBER - 21104001"], "21104001"),  # PG rolls are longer
    (["Roll No.", "22O34S"], None),          # S is not a digit: not trusted
    (["Roll No.", "22O345"], "220345"),      # label and number on two lines, O read for 0
    (["Roll: 22l345"], "221345"),            # l read for 1
])
def test_roll_numbers_survive_common_ocr_mistakes(lines, roll):
    rolls = [i.value for i in find_identifiers(lines) if i.kind == "ROLL"]
    assert rolls == ([roll] if roll else [])


def test_numbers_without_a_roll_label_are_ignored():
    # PIN codes, phone numbers and years are not roll numbers.
    assert find_identifiers(["Kanpur 208016", "Ph: 9876543210", "Batch 2022", "Emp No 220345"]) == []


def test_only_emails_on_allowed_domains_are_used():
    lines = ["Email: rahulk22 @ iitk.ac.in", "someone@gmail.com", "prof@cse.iitk.ac.in"]
    assert find_identifiers(lines) == [Identifier("EMAIL", "rahulk22@iitk.ac.in"),
                                       Identifier("EMAIL", "prof@cse.iitk.ac.in")]


def test_an_email_label_glued_on_by_ocr_is_removed():
    assert find_identifiers(["Emailrahulk22@iitk.ac.in"]) == [Identifier("EMAIL", "rahulk22@iitk.ac.in")]
    assert find_identifiers(["E-mail:rahulk22@iitk.ac.in"]) == [Identifier("EMAIL", "rahulk22@iitk.ac.in")]


def test_identifiers_are_masked_for_display():
    assert Identifier("ROLL", "220345").hint == "roll no. ending 45"
    assert Identifier("EMAIL", "rahulk22@iitk.ac.in").hint == "ra•••@iitk.ac.in"


# ---------------- Fixtures ----------------


class FakeOCR:
    name = "fake-ocr"

    def __init__(self, lines=CARD):
        self.lines, self.calls = lines, 0

    def read(self, path):
        self.calls += 1
        return list(self.lines)


@pytest.fixture
def ocr():
    def _use(engine):
        idcard.set_ocr(engine)
        return engine
    yield _use
    idcard.reset_ocr()


@pytest.fixture
def place(db, monkeypatch):
    ids = Category(name="AI ID cards", slug="ai-ids", sort_order=1)
    wallets = Category(name="AI Wallets", slug="ai-wallets", sort_order=2)
    office = Office(name="AI Office", location_description="Main gate", opening_hours="24 hours")
    db.add_all([ids, wallets, office])
    db.commit()
    monkeypatch.setattr(get_settings(), "id_card_categories", "ai-ids")
    monkeypatch.setattr(get_settings(), "ai_skip_photo_categories", "ai-ids")
    monkeypatch.setattr(get_settings(), "agent_matching", "off")
    return {"ids": ids.id, "wallets": wallets.id, "office": office.id}


@pytest.fixture
def person(make_user, sign_in):
    count = {"n": 0}

    def _person(role=UserRole.STUDENT, roll=None):
        count["n"] += 1
        email = f"cardperson{count['n']}@iitk.ac.in"
        extra = {"roll_number": roll or f"C{count['n']:04d}"} if role == UserRole.STUDENT else {}
        make_user(email, role=role, full_name=f"Person {count['n']}", **extra)
        return sign_in(email)

    return _person


@pytest.fixture
def emails(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, "send",
                        lambda to, mail: sent.append((to, mail.subject, email_service.render_text(mail))))
    return sent


def jpeg() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (640, 400), (240, 240, 240)).save(out, format="JPEG")
    return out.getvalue()


def found_with_photo(db, finder, place, category="ids", title="Student ID card"):
    item_id = finder.post("/items/found", json={
        "title": title, "description": "Found on the ground", "category_id": place[category],
        "location_text": "Main gate", "happened_on": str(today()),
        "dropoff_office_id": place["office"]}).json()["id"]
    response = finder.post(f"/items/{item_id}/photos", files={"file": ("c.jpg", io.BytesIO(jpeg()), "image/jpeg")})
    assert response.status_code == 201
    queue.run_all(db)
    return item_id


def receive(db, guard, item_id, place):
    assert guard.post(f"/staff/items/{item_id}/receive", json={"office_id": place["office"]}).status_code == 200
    queue.run_all(db)


# ---------------- Who gets emailed, and when ----------------


def test_owner_is_emailed_once_the_card_is_at_an_office(db, person, place, ocr, emails):
    engine = ocr(FakeOCR())
    finder, guard = person(), person(UserRole.GUARD)
    item_id = found_with_photo(db, finder, place)

    # Still with the finder: the owner is found but not emailed yet.
    alert = db.scalar(select(OwnerAlert).where(OwnerAlert.item_id == item_id))
    assert (alert.target_email, alert.status, alert.user_id) == ("220345@iitk.ac.in", "pending", None)
    assert emails == []

    receive(db, guard, item_id, place)
    assert [to for to, _, _ in emails] == ["220345@iitk.ac.in"]
    to, subject, body = emails[0]
    assert "roll no. ending 45" in body and "220345" not in body  # masked
    assert "AI Office" in body and "This is mine" in body

    # Analysed again (e.g. a guard adds a photo): no second email, no second OCR.
    calls = engine.calls
    item = db.get(Item, item_id)
    from app.services import matching
    matching.notify_saved(db, item)
    queue.run_all(db)
    assert len(emails) == 1 and engine.calls == calls


def test_a_registered_student_is_emailed_at_their_account_and_linked_to_their_report(db, person, place, ocr, emails):
    ocr(FakeOCR())
    owner = person(roll="220345")
    lost_id = owner.post("/items/lost", json={
        "title": "My ID card", "description": "Lost my institute card", "category_id": place["ids"],
        "location_text": "OAT", "happened_on": str(today())}).json()["id"]
    queue.run_all(db)
    finder, guard = person(), person(UserRole.GUARD)
    item_id = found_with_photo(db, finder, place)
    receive(db, guard, item_id, place)

    assert [to for to, _, _ in emails] == ["cardperson1@iitk.ac.in"]  # one email, not two
    link = db.scalar(select(MatchSuggestion).where(MatchSuggestion.item_id == lost_id,
                                                   MatchSuggestion.candidate_id == item_id))
    assert link.score == 1.0 and link.method == "idcard"
    similar = owner.get(f"/items/{lost_id}/similar").json()
    assert similar["matches"][0]["item"]["id"] == item_id and similar["matches"][0]["method"] == "idcard"

    # The link survives the lost report being analysed again.
    from app.services import matching
    matching.notify_saved(db, db.get(Item, lost_id))
    queue.run_all(db)
    assert db.scalar(select(MatchSuggestion.score).where(MatchSuggestion.item_id == lost_id,
                                                         MatchSuggestion.candidate_id == item_id)) == 1.0


def test_the_finder_is_never_emailed_about_their_own_details(db, person, place, ocr, emails):
    ocr(FakeOCR(["Roll No: 330001"]))
    finder, guard = person(roll="330001"), person(UserRole.GUARD)
    item_id = found_with_photo(db, finder, place)
    receive(db, guard, item_id, place)
    assert emails == []
    assert db.scalar(select(OwnerAlert).where(OwnerAlert.item_id == item_id)) is None


def test_ocr_not_installed_changes_nothing(db, person, place, ocr, emails):
    ocr(None)
    finder = person()
    item_id = found_with_photo(db, finder, place)
    item = db.get(Item, item_id)
    assert item.ai_status == "done" and item.ocr_photos_key is None


# ---------------- When is a photo read? ----------------


def test_only_the_id_card_category_is_read(db, person, place, ocr, monkeypatch):
    engine = ocr(FakeOCR())
    monkeypatch.setattr(get_settings(), "agent_matching", "always")  # even with the agent on
    found_with_photo(db, person(), place, category="wallets", title="Wallet with my ID card")
    assert engine.calls == 0


def test_the_agent_never_sees_the_identifiers(db, person, place, ocr, monkeypatch):
    ocr(FakeOCR(["Roll No: 220345", "Email: rahulk22@iitk.ac.in"]))
    monkeypatch.setattr(get_settings(), "agent_matching", "always")
    person().post("/items/lost", json={
        "title": "Lost ID card", "description": "Institute card", "category_id": place["ids"],
        "location_text": "Hall 3", "happened_on": str(today())})
    seen = []

    class Agent:
        def chat(self, messages, tools):
            seen.append(json.dumps(messages) + json.dumps(tools))
            return ({"role": "assistant", "content": "", "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "finish", "arguments": "{}"}}]},
                "fake", "fake-model")

    llm.set_llm(Agent())
    try:
        item_id = found_with_photo(db, person(), place)
    finally:
        llm.reset_llm()
    assert seen and db.scalar(select(OwnerAlert).where(OwnerAlert.item_id == item_id)) is not None
    everything_sent = " ".join(seen)
    assert "220345" not in everything_sent and "rahulk22" not in everything_sent
    assert "scan_for_owner_id" not in everything_sent


@pytest.mark.parametrize("lines, registered_roll, expected", [
    (["Roll No: 220345"], None, ["220345@iitk.ac.in"]),                      # roll only
    (["Email: rahulk22@iitk.ac.in"], None, ["rahulk22@iitk.ac.in"]),          # email only
    (["Roll No: 220345", "rahulk22@iitk.ac.in"], None, ["rahulk22@iitk.ac.in"]),  # both: the email, once
    (["Roll No: 220345", "rahulk22@iitk.ac.in"], "220345", ["cardperson1@iitk.ac.in"]),  # registered wins
])
def test_who_gets_the_email(db, person, place, ocr, lines, registered_roll, expected):
    ocr(FakeOCR(lines))
    if registered_roll:
        person(roll=registered_roll)
    item_id = found_with_photo(db, person(), place)
    targets = list(db.scalars(select(OwnerAlert.target_email).where(OwnerAlert.item_id == item_id)))
    assert targets == expected


def test_lost_reports_are_never_read(db, person, place, ocr):
    engine = ocr(FakeOCR())
    owner = person()
    item_id = owner.post("/items/lost", json={
        "title": "ID card", "description": "Lost", "category_id": place["ids"],
        "location_text": "OAT", "happened_on": str(today())}).json()["id"]
    owner.post(f"/items/{item_id}/photos", files={"file": ("c.jpg", io.BytesIO(jpeg()), "image/jpeg")})
    queue.run_all(db)
    assert engine.calls == 0
    assert idcard.scan(db, db.get(Item, item_id))["scanned"] is False


# ---------------- Privacy of ID card photos ----------------


def test_id_card_photos_are_only_shown_to_the_finder_and_staff(db, person, place, ocr):
    ocr(FakeOCR())
    finder, other, guard = person(), person(), person(UserRole.GUARD)
    item_id = found_with_photo(db, finder, place)
    receive(db, guard, item_id, place)
    photo_id = db.get(Item, item_id).photos[0].id

    seen_by_other = other.get(f"/items/{item_id}").json()
    assert seen_by_other["photo_ids"] == [] and seen_by_other["photos_hidden"] is True
    assert seen_by_other["cover_photo_id"] is None and seen_by_other["owner_alerts"] == []
    assert other.get(f"/items/{item_id}/photos/{photo_id}").status_code == 404
    listed = other.get("/items", params={"kind": "FOUND"}).json()["items"]
    assert all(row["cover_photo_id"] is None for row in listed if row["id"] == item_id)

    assert finder.get(f"/items/{item_id}/photos/{photo_id}").status_code == 200
    staff_view = guard.get(f"/items/{item_id}").json()
    assert staff_view["photo_ids"] == [photo_id]
    assert staff_view["owner_alerts"] == [{"id_type": "ROLL", "id_hint": "roll no. ending 45",
                                           "registered": False, "status": "sent",
                                           "sent_at": staff_view["owner_alerts"][0]["sent_at"]}]
    assert "220345" not in json.dumps(staff_view)
