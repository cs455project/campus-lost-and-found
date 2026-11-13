"""Tests for the server command line (app/cli.py): the commands the team runs
on the server, e.g. `python -m app.cli make-admin someone@iitk.ac.in`.

The commands open their own database sessions; `session_factory` makes those
sessions part of the test's transaction, so everything is undone afterwards.
"""

import io
import sys
from datetime import timedelta

import pytest
from PIL import Image
from sqlalchemy import delete, select, update

from app import cli
from app.core.clock import now, today
from app.core.config import get_settings
from app.models import (
    AiJob,
    Category,
    CustodyEventType,
    EmailLog,
    Item,
    ItemKind,
    ItemPhoto,
    ItemStatus,
    Office,
    OwnerAlert,
    User,
    UserRole,
)
from app.services import storage
from app.services.ai import idcard, llm
from app.services.ai.embeddings import get_embedder
from app.services.ai.semantic import item_text


@pytest.fixture(autouse=True)
def cli_sessions(monkeypatch, session_factory):
    monkeypatch.setattr(cli, "SessionLocal", session_factory)


def run_cli(monkeypatch, *args):
    """Run `python -m app.cli <args>` in-process."""
    monkeypatch.setattr(sys, "argv", ["python -m app.cli", *args])
    cli.main()


def category(db, slug) -> Category:
    return db.scalar(select(Category).where(Category.slug == slug))


def an_office(db) -> Office:
    return db.scalar(select(Office).where(Office.is_active).order_by(Office.id))


def new_item(db, reporter, kind=ItemKind.FOUND, status=ItemStatus.IN_CUSTODY, slug="bags", **fields) -> Item:
    item = Item(kind=kind, status=status, title=fields.pop("title", "Black backpack"),
                description="A black backpack with a laptop sleeve.", category_id=category(db, slug).id,
                location_text="Library", happened_on=fields.pop("happened_on", today()),
                reporter_id=reporter.id, **fields)
    if kind == ItemKind.FOUND:  # found items always name a drop-off office (database rule)
        item.dropoff_office_id = an_office(db).id
    if status in (ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING):
        item.current_office_id = item.dropoff_office_id
    db.add(item)
    db.commit()
    return item


def add_photo(db, item) -> ItemPhoto:
    out = io.BytesIO()
    Image.new("RGB", (64, 48), (200, 200, 200)).save(out, format="JPEG")
    name = storage.save(out.getvalue(), "jpg")
    photo = ItemPhoto(item_id=item.id, stored_name=name, content_type="image/jpeg",
                      size_bytes=len(out.getvalue()), uploaded_by_id=item.reporter_id)
    db.add(photo)
    db.commit()
    db.refresh(item)
    return photo


@pytest.fixture
def reporter(make_user):
    return make_user("cli.reporter@iitk.ac.in", full_name="CLI Reporter", roll_number="CLI001")


# ---------------- make-admin ----------------


def test_make_admin_creates_a_new_admin_with_a_normalised_email(db, capsys):
    cli.make_admin("  New.Admin@Gmail.com ")
    user = db.scalar(select(User).where(User.email == "new.admin@gmail.com"))
    assert user is not None and user.role == UserRole.ADMIN
    assert "new.admin@gmail.com is now the admin" in capsys.readouterr().out


def test_make_admin_promotes_an_existing_student_and_keeps_their_details(db, make_user):
    student = make_user("promote.me@iitk.ac.in", full_name="Asha Rao", roll_number="210001")
    cli.make_admin("promote.me@iitk.ac.in")
    db.refresh(student)
    assert student.role == UserRole.ADMIN
    assert (student.full_name, student.roll_number) == ("Asha Rao", "210001")
    assert db.scalar(select(User).where(User.email == "promote.me@iitk.ac.in")).id == student.id


def test_make_admin_refuses_other_email_domains(db, monkeypatch):
    with pytest.raises(SystemExit) as stopped:
        run_cli(monkeypatch, "make-admin", "someone@yahoo.com")
    assert str(stopped.value.code).startswith("Error:")
    assert db.scalar(select(User).where(User.email == "someone@yahoo.com")) is None


# ---------------- item numbers ----------------


@pytest.mark.parametrize("value, number", [
    ("735", 735), ("0735", 735), ("FND-0735", 735), ("lst-0012", 12), (" #42 ", 42),
])
def test_item_numbers_are_accepted_in_every_usual_form(value, number):
    assert cli.parse_item(value) == number


@pytest.mark.parametrize("value", ["abc", "FND-", "12a", ""])
def test_anything_else_is_not_an_item_number(value):
    with pytest.raises(SystemExit, match="not an item number"):
        cli.parse_item(value)


# ---------------- demo-items and ai-reindex ----------------


def test_demo_items_creates_found_items_at_an_office_and_open_lost_reports(db, capsys):
    cli.demo_items(6)
    demo = db.scalar(select(User).where(User.email == "demo.reporter@iitk.ac.in"))
    items = list(db.scalars(select(Item).where(Item.reporter_id == demo.id).order_by(Item.id)))
    found = [i for i in items if i.kind == ItemKind.FOUND]
    lost = [i for i in items if i.kind == ItemKind.LOST]
    assert (len(found), len(lost)) == (4, 2)  # every third one is a lost report
    for item in found:
        assert item.status == ItemStatus.IN_CUSTODY
        assert item.current_office_id is not None and item.current_office_id == item.dropoff_office_id
        assert [e.event_type for e in item.custody_events] == [CustodyEventType.REGISTERED]
    assert all(i.status == ItemStatus.OPEN for i in lost)
    assert all(i.happened_on <= today() for i in items)
    # demo_items finishes with ai-reindex: every new item is queued for matching.
    queued = set(db.scalars(select(AiJob.item_id).where(AiJob.status == "queued")))
    assert queued == {i.id for i in items}
    assert "Created 6 demo items" in capsys.readouterr().out


def test_demo_items_reuses_the_demo_reporter(db):
    cli.demo_items(1)
    cli.demo_items(1)
    assert len(list(db.scalars(select(User).where(User.email == "demo.reporter@iitk.ac.in")))) == 1


def test_demo_items_asks_for_the_seed_when_there_are_no_offices(db):
    db.execute(update(Office).values(is_active=False))
    db.commit()
    with pytest.raises(SystemExit, match="app.seed"):
        cli.demo_items(3)


def test_reindex_queues_only_reports_that_are_still_active(db, reporter, capsys):
    held = new_item(db, reporter)
    lost = new_item(db, reporter, kind=ItemKind.LOST, status=ItemStatus.OPEN, title="Lost keys", slug="keys")
    returned = new_item(db, reporter, status=ItemStatus.RETURNED, title="Old bottle")
    closed = new_item(db, reporter, kind=ItemKind.LOST, status=ItemStatus.CLOSED, title="Old wallet")
    cli.reindex()
    queued = set(db.scalars(select(AiJob.item_id).where(AiJob.status == "queued")))
    assert queued == {held.id, lost.id}
    for item in (held, lost, returned, closed):
        db.refresh(item)
    assert (held.ai_status, lost.ai_status) == ("pending", "pending")
    assert returned.ai_status is None and closed.ai_status is None
    assert "Queued 2 items" in capsys.readouterr().out


# ---------------- email-log ----------------


@pytest.fixture
def logged_emails(db):
    db.execute(delete(EmailLog))
    rows = [
        EmailLog(to_email="owner@iitk.ac.in", subject="Your ID card may have been found", kind="owner_alert",
                 ref="FND-0735", status="sent"),
        EmailLog(to_email="other@iitk.ac.in", subject="A match for your report", kind="match",
                 ref="FND-0012", status="failed", error="SMTPAuthenticationError: bad password"),
        EmailLog(to_email="owner@iitk.ac.in", subject="Your sign-in code is ••••••", kind="login_code",
                 status="printed"),
    ]
    db.add_all(rows)
    db.commit()
    return rows


def test_email_log_lists_the_latest_emails_oldest_first_with_errors(logged_emails, capsys):
    cli.email_log(20, None, None)
    out = capsys.readouterr().out
    assert out.index("Your ID card may") < out.index("A match for") < out.index("Your sign-in code")
    assert "FAILED" in out and "error: SMTPAuthenticationError: bad password" in out


def test_email_log_shows_only_the_last_n(logged_emails, capsys):
    cli.email_log(1, None, None)
    out = capsys.readouterr().out
    assert "Your sign-in code" in out and "A match for" not in out


def test_email_log_filters_by_item_and_by_address(logged_emails, capsys):
    cli.email_log(20, "735", None)
    out = capsys.readouterr().out
    assert "Your ID card may" in out and "A match for" not in out
    cli.email_log(20, None, " OWNER@iitk.ac.in ")
    out = capsys.readouterr().out
    assert out.count("owner@iitk.ac.in") == 2 and "other@iitk.ac.in" not in out
    cli.email_log(20, "999", None)
    assert "No emails logged for that filter." in capsys.readouterr().out


def test_email_log_says_when_nothing_was_sent_yet(db, capsys):
    db.execute(delete(EmailLog))
    db.commit()
    cli.email_log(20, None, None)
    assert "No emails logged yet." in capsys.readouterr().out


# ---------------- item-check: why an owner email was or wasn't sent ----------------


def verdict(capsys, item) -> str:
    cli.item_check(item.ref)
    out = capsys.readouterr().out
    assert out.startswith(f"{item.ref}: {item.title}")
    return out.split("=> ", 1)[1]


def id_card(db, reporter, **fields) -> Item:
    return new_item(db, reporter, slug="id-cards-documents", title="Student ID card", **fields)


def alert(db, item, status="sent") -> OwnerAlert:
    row = OwnerAlert(item_id=item.id, id_type="ROLL", id_hint="roll no. ending 45",
                     target_email="220345@iitk.ac.in", status=status,
                     sent_at=now() if status == "sent" else None)
    db.add(row)
    db.commit()
    return row


def test_item_check_explains_lost_reports_and_other_categories(db, reporter, capsys):
    lost = new_item(db, reporter, kind=ItemKind.LOST, status=ItemStatus.OPEN)
    assert "LOST report" in verdict(capsys, lost)
    bag = new_item(db, reporter)
    assert "Not in the ID card category" in verdict(capsys, bag)


def test_item_check_follows_an_id_card_through_each_stage(db, reporter, capsys):
    card = id_card(db, reporter)
    assert "No photo" in verdict(capsys, card)

    add_photo(db, card)
    card.ai_status = "pending"
    db.commit()
    assert "still working" in verdict(capsys, card)

    card.ai_status = "failed"
    db.commit()
    assert f"ai-run {card.ref}" in verdict(capsys, card)

    card.ai_status = "done"
    db.commit()
    assert "photo was not read" in verdict(capsys, card)

    card.ocr_photos_key = "1"
    db.commit()
    assert f"ocr-check {card.ref}" in verdict(capsys, card)  # read, but no owner found


def test_item_check_waits_for_the_guard_before_emailing(db, reporter, capsys):
    card = id_card(db, reporter, status=ItemStatus.AWAITING_DROPOFF, ai_status="done", ocr_photos_key="1")
    add_photo(db, card)
    alert(db, card, status="pending")
    assert "waits until a guard RECEIVES" in verdict(capsys, card)


@pytest.mark.parametrize("log_status, expected", [
    (None, "before email logging existed"),
    ("failed", "Sending FAILED"),
    ("printed", "only printed in the terminal"),
    ("sent", "The email was sent"),
])
def test_item_check_reads_the_email_log_for_a_held_card(db, reporter, capsys, log_status, expected):
    db.execute(delete(EmailLog))
    card = id_card(db, reporter, ai_status="done", ocr_photos_key="1")
    add_photo(db, card)
    alert(db, card, status="sent")
    if log_status:
        db.add(EmailLog(to_email="220345@iitk.ac.in", subject="Your ID card", kind="owner_alert",
                        ref=card.ref, status=log_status, error="timeout" if log_status == "failed" else None))
        db.commit()
    out = verdict(capsys, card)
    assert expected in out


def test_item_check_shows_the_details_it_based_the_verdict_on(db, reporter, capsys):
    card = id_card(db, reporter, ai_status="done", ocr_photos_key="1")
    add_photo(db, card)
    alert(db, card, status="sent")
    cli.item_check(str(card.id))
    out = capsys.readouterr().out
    assert "FOUND / IN_CUSTODY at" in out
    assert "(ID card category: read by OCR)" in out
    assert "photos        : 1" in out
    assert "OCR           : read" in out
    assert "owner alert   : roll no. ending 45 -> 220345@iitk.ac.in: sent" in out


def test_item_check_of_a_missing_item_stops_with_an_error():
    with pytest.raises(SystemExit, match="no item 999999"):
        cli.item_check("999999")


# ---------------- email-test ----------------


def test_email_test_prints_both_samples_in_console_mode(monkeypatch, capsys, email_log):
    monkeypatch.setattr(get_settings(), "email_backend", "console")
    cli.email_test("tester@iitk.ac.in")
    out = capsys.readouterr().out
    assert "nothing is sent" in out
    assert "sign-in code: OK" in out and "ID card found: OK" in out
    assert [(to, status) for to, _kind, _ref, status, _err in email_log] == [
        ("tester@iitk.ac.in", "printed"), ("tester@iitk.ac.in", "printed")]


def test_email_test_stops_with_advice_when_smtp_is_not_set_up(monkeypatch, email_log):
    monkeypatch.setattr(get_settings(), "email_backend", "smtp")
    monkeypatch.setattr(get_settings(), "smtp_password", "")
    with pytest.raises(SystemExit) as stopped:
        cli.email_test("tester@iitk.ac.in")
    assert "NOT SENT" in str(stopped.value.code) and "App Password" in str(stopped.value.code)
    assert email_log[0][3] == "failed"


# ---------------- ai-check ----------------


def test_ai_check_when_ai_is_switched_off(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "ai_enabled", False)
    cli.ai_check()
    assert "AI_ENABLED=false" in capsys.readouterr().out


def test_ai_check_without_keys(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    monkeypatch.setattr(llm, "configured_providers", list)
    cli.ai_check()
    assert "No AI keys found" in capsys.readouterr().out


def test_ai_check_reports_each_provider_separately(monkeypatch, capsys):
    monkeypatch.setattr(get_settings(), "ai_enabled", True)
    good = llm.Provider("groq", "https://groq.example", "k1", "model-a")
    lazy = llm.Provider("lazy", "https://lazy.example", "k2", "model-b")
    broken = llm.Provider("gemini", "https://gemini.example", "k3", "model-c")
    monkeypatch.setattr(llm, "configured_providers", lambda: [good, lazy, broken])

    class FakeClient:
        def __init__(self, providers):
            (self.provider,) = providers

        def chat(self, messages, tools):
            assert tools[0]["function"]["name"] == "ping"
            if self.provider.name == "gemini":
                raise RuntimeError("401 invalid API key")
            calls = [{"function": {"name": "ping"}}] if self.provider.name == "groq" else []
            return {"role": "assistant", "content": "", "tool_calls": calls}, self.provider.name, self.provider.model

    monkeypatch.setattr(llm, "LLMClient", FakeClient)
    cli.ai_check()
    lines = capsys.readouterr().out.splitlines()
    assert "groq" in lines[0] and "OK, tool calling works" in lines[0]
    assert "lazy" in lines[1] and "did not call the tool" in lines[1]
    assert "gemini" in lines[2] and "FAILED: 401 invalid API key" in lines[2]


# ---------------- ai-run ----------------


@pytest.fixture
def no_llm(monkeypatch):
    monkeypatch.setattr(get_settings(), "agent_matching", "off")  # ai_run changes it; restored afterwards
    llm.set_llm(None)
    yield
    llm.reset_llm()


def test_ai_run_without_an_llm_saves_the_rule_based_matches(db, reporter, make_user, capsys, no_llm):
    finder = make_user("cli.finder@iitk.ac.in", full_name="Finder", roll_number="CLI002")
    found = new_item(db, finder, title="Black Wildcraft backpack")
    embedder = get_embedder()  # the found item was analysed earlier, as the AI queue would have
    found.embedding, found.embedding_model = embedder.embed([item_text(found)])[0], embedder.name
    db.commit()
    lost = new_item(db, reporter, kind=ItemKind.LOST, status=ItemStatus.OPEN, title="Black Wildcraft backpack",
                    happened_on=today() - timedelta(days=1))
    cli.ai_run(lost.id)
    out = capsys.readouterr().out
    assert out.startswith(f"{lost.ref} (LOST): Black Wildcraft backpack")
    assert "The agent did not run" in out
    assert "Saved matches:" in out and found.ref in out.split("Saved matches:")[1]
    db.refresh(lost)
    assert lost.embedding is not None and lost.embedding_model  # the embedding was saved


def test_ai_run_of_a_missing_item_stops_with_an_error(no_llm):
    with pytest.raises(SystemExit, match="no item 999999"):
        cli.ai_run(999999)


# ---------------- ocr-check ----------------


class FakeOCR:
    name = "fake-ocr"

    def __init__(self, lines):
        self.lines = lines

    def read(self, path):
        return list(self.lines)


@pytest.fixture
def ocr():
    def _use(engine):
        idcard.set_ocr(engine)
    yield _use
    idcard.reset_ocr()


def test_ocr_check_on_a_photo_shows_the_text_and_who_would_be_emailed(tmp_path, monkeypatch, capsys, ocr):
    monkeypatch.setattr(get_settings(), "roll_email_pattern", "{roll}@iitk.ac.in")
    photo = tmp_path / "card.jpg"
    Image.new("RGB", (64, 48)).save(photo)
    ocr(FakeOCR(["INDIAN INSTITUTE OF TECHNOLOGY KANPUR", "Roll No: 220345"]))
    cli.ocr_check(str(photo))
    out = capsys.readouterr().out
    assert "Roll No: 220345" in out
    assert "ROLL  220345" in out
    assert "220345@iitk.ac.in   (roll number)" in out


def test_ocr_check_says_when_no_owner_can_be_found(tmp_path, capsys, ocr):
    photo = tmp_path / "blurry.jpg"
    Image.new("RGB", (64, 48)).save(photo)
    ocr(FakeOCR([]))
    cli.ocr_check(str(photo))
    out = capsys.readouterr().out
    assert "No text could be read" in out and "No roll number" in out


def test_ocr_check_on_an_item_reads_its_photos_and_notes_the_category(db, reporter, capsys, ocr):
    bag = new_item(db, reporter)
    add_photo(db, bag)
    ocr(FakeOCR(["Wildcraft"]))
    cli.ocr_check(bag.ref)
    out = capsys.readouterr().out
    assert f"{bag.ref} (FOUND, Bags): Black backpack, 1 photo(s)" in out
    assert "Phase D does not read this item" in out


def test_ocr_check_stops_on_a_missing_file_or_missing_ocr(tmp_path, ocr):
    with pytest.raises(SystemExit, match="no file"):
        cli.ocr_check(str(tmp_path / "nothing.jpg"))
    photo = tmp_path / "card.jpg"
    Image.new("RGB", (64, 48)).save(photo)
    ocr(None)
    with pytest.raises(SystemExit, match="OCR is not available"):
        cli.ocr_check(str(photo))


def test_ocr_check_of_a_missing_item_stops_with_an_error(ocr):
    with pytest.raises(SystemExit, match="no item FND-999999"):
        cli.ocr_check("FND-999999")


# ---------------- argument parsing ----------------


@pytest.mark.parametrize("args, function, expected", [
    (["make-admin", "a@iitk.ac.in"], "make_admin", ("a@iitk.ac.in",)),
    (["demo-items"], "demo_items", (30,)),
    (["demo-items", "--count", "5"], "demo_items", (5,)),
    (["ai-reindex"], "reindex", ()),
    (["ai-check"], "ai_check", ()),
    (["ai-run", "FND-0007"], "ai_run", (7,)),
    (["email-log", "--last", "5", "--ref", "FND-1", "--to", "x@iitk.ac.in"], "email_log", (5, "FND-1", "x@iitk.ac.in")),
    (["email-log"], "email_log", (20, None, None)),
    (["item-check", "LST-0003"], "item_check", ("LST-0003",)),
    (["email-test", "me@gmail.com"], "email_test", ("me@gmail.com",)),
    (["ocr-check", "card.jpg"], "ocr_check", ("card.jpg",)),
])
def test_each_command_calls_its_function_with_the_parsed_arguments(monkeypatch, args, function, expected):
    calls = []
    monkeypatch.setattr(cli, function, lambda *a: calls.append(a))
    run_cli(monkeypatch, *args)
    assert calls == [expected]


def test_a_command_is_required(monkeypatch, capsys):
    with pytest.raises(SystemExit) as stopped:
        run_cli(monkeypatch)
    assert stopped.value.code == 2  # argparse usage error
    assert "required" in capsys.readouterr().err
