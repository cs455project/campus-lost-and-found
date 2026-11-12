"""Unit tests: pure logic tested on its own, with no database, API or network.

Objects (users, items, claims) are built in memory and never saved, so each
test checks one rule in isolation. Run with:  pytest tests/test_unit_logic.py
"""

from datetime import date

import pytest
from app.core.errors import AppError
from pydantic import ValidationError

from app.core import security
from app.core.config import get_settings
from app.models import Category, Claim, ClaimStatus, Item, ItemKind, ItemPhoto, ItemStatus, User, UserRole
from app.schemas.claim import ClaimIn
from app.schemas.item import CloseLostIn, LostItemIn
from app.schemas.user import OnboardingIn
from app.services import auth, items, storage
from app.services import claims as claims_service
from app.services.ai import semantic
from app.services.ai.embeddings import HashingEmbedder

S = ClaimStatus


# ---------------- helpers ----------------

def student(uid=1):
    return User(id=uid, email=f"s{uid}@iitk.ac.in", role=UserRole.STUDENT)


def guard(uid=90):
    return User(id=uid, email=f"g{uid}@gmail.com", role=UserRole.GUARD)


def item(kind=ItemKind.FOUND, status=ItemStatus.IN_CUSTODY, reporter_id=1, slug="electronics", **kw):
    it = Item(kind=kind, status=status, reporter_id=reporter_id, title="Black calculator",
              location_text="L7", happened_on=date(2026, 10, 1), **kw)
    it.category = Category(id=1, name=slug.title(), slug=slug)
    return it


# ---------------- security helpers (app/core/security.py) ----------------

def test_login_code_is_always_six_digits():
    codes = [security.generate_login_code() for _ in range(500)]
    assert all(len(c) == 6 and c.isdigit() for c in codes)


def test_login_codes_are_not_repeated_in_a_row():
    codes = {security.generate_login_code() for _ in range(200)}
    assert len(codes) > 190  # a million possibilities; repeats are rare


def test_session_tokens_are_long_random_and_url_safe():
    a, b = security.generate_session_token(), security.generate_session_token()
    assert a != b and len(a) >= 43
    assert all(ch.isalnum() or ch in "-_" for ch in a)


def test_hash_is_deterministic_hex_sha256_and_never_the_input():
    h = security.hash_secret("123456")
    assert h == security.hash_secret("123456")
    assert len(h) == 64 and int(h, 16) >= 0 and "123456" not in h


def test_secrets_match_only_for_the_right_value():
    h = security.hash_secret("042917")
    assert security.secrets_match("042917", h)
    assert not security.secrets_match("042918", h)
    assert not security.secrets_match("", h)


def test_hash_depends_on_the_secret_key(monkeypatch):
    before = security.hash_secret("123456")
    monkeypatch.setattr(get_settings(), "secret_key", "another-secret-key-of-at-least-32-chars!!")
    assert security.hash_secret("123456") != before


# ---------------- upload checks (app/services/storage.py) ----------------

@pytest.mark.parametrize("head, expected", [
    (b"\xff\xd8\xff\xe0" + b"\0" * 20, ("image/jpeg", ".jpg")),
    (b"\x89PNG\r\n\x1a\n" + b"\0" * 20, ("image/png", ".png")),
    (b"RIFF\x24\0\0\0WEBPVP8 " + b"\0" * 10, ("image/webp", ".webp")),
])
def test_real_image_types_are_recognised(head, expected):
    assert storage.detect_image_type(head) == expected


@pytest.mark.parametrize("head", [
    b"GIF89a" + b"\0" * 20,               # GIF is not accepted
    b"MZ\x90\x00" + b"\0" * 20,           # a Windows .exe renamed to .jpg
    b"%PDF-1.7" + b"\0" * 20,             # a PDF
    b"<svg xmlns='http://www.w3.org/2000/svg'><script>",  # SVG can carry scripts
    b"RIFF\x24\0\0\0WAVEfmt ",            # RIFF, but audio not WebP
    b"",                                   # empty file
])
def test_non_images_and_risky_types_are_rejected(head):
    assert storage.detect_image_type(head) is None


@pytest.mark.parametrize("folder", ["../app", "items/../../", "secrets", ""])
def test_only_known_upload_folders_can_be_used(folder):
    with pytest.raises(ValueError):
        storage._folder(folder)


# ---------------- e-mail rules (app/services/auth.py) ----------------

def test_emails_are_trimmed_and_lowercased():
    assert auth.normalize_email("  Soumya.S@IITK.AC.IN ") == "soumya.s@iitk.ac.in"


def test_campus_email_is_accepted_for_students():
    auth.check_allowed_domain("someone@iitk.ac.in")


@pytest.mark.parametrize("email", [
    "x@evil-iitk.ac.in", "x@iitk.ac.in.evil.com", "x@gmail.com", "x@cse.iitk.ac.in.com",
    "x@iitk.ac.in@gmail.com",  # two @ signs: the last part decides
])
def test_look_alike_domains_are_rejected_for_students(email):
    with pytest.raises(AppError) as err:
        auth.check_allowed_domain(email)
    assert err.value.status_code == 400


def test_staff_may_use_campus_or_gmail_but_nothing_else():
    auth.check_staff_email("guard@gmail.com")
    auth.check_staff_email("guard@iitk.ac.in")
    for bad in ("guard@yahoo.com", "guard@gmail.com.evil.org"):
        with pytest.raises(AppError):
            auth.check_staff_email(bad)


# ---------------- claim state machine (app/services/claims.py) ----------------

def claim(status, claimant_id=1):
    return Claim(status=status, claimant_id=claimant_id)


@pytest.mark.parametrize("frm, to", [k for k, v in claims_service.TRANSITIONS.items() if v == claims_service.STAFF])
def test_every_staff_move_is_allowed_for_staff_only(frm, to):
    c = claim(frm)
    assert claims_service._allowed(c, to, guard())
    assert not claims_service._allowed(c, to, student(1))   # not even the claimant
    assert not claims_service._allowed(c, to, None)          # not the system


@pytest.mark.parametrize("frm", [S.SUBMITTED, S.UNDER_REVIEW, S.NEEDS_MORE_INFO, S.APPROVED])
def test_only_the_claimant_can_withdraw_their_claim(frm):
    c = claim(frm, claimant_id=1)
    assert claims_service._allowed(c, S.WITHDRAWN, student(1))
    assert not claims_service._allowed(c, S.WITHDRAWN, student(2))
    assert not claims_service._allowed(c, S.WITHDRAWN, guard())


def test_only_the_system_can_expire_an_approved_claim():
    c = claim(S.APPROVED)
    assert claims_service._allowed(c, S.EXPIRED, None)
    assert not claims_service._allowed(c, S.EXPIRED, guard())
    assert not claims_service._allowed(c, S.EXPIRED, student(1))


@pytest.mark.parametrize("frm, to", [
    (S.SUBMITTED, S.APPROVED),      # skipping review
    (S.SUBMITTED, S.COLLECTED),     # skipping review and approval
    (S.UNDER_REVIEW, S.COLLECTED),  # handing over before approval
    (S.REJECTED, S.APPROVED),       # reopening a rejected claim
    (S.COLLECTED, S.REJECTED),      # undoing a hand-over
    (S.WITHDRAWN, S.SUBMITTED),
    (S.EXPIRED, S.APPROVED),
    (S.NEEDS_MORE_INFO, S.APPROVED),
])
def test_impossible_jumps_are_refused_for_everyone(frm, to):
    c = claim(frm)
    for actor in (guard(), student(1), None):
        assert not claims_service._allowed(c, to, actor)


def test_closed_states_have_no_way_out():
    finals = {S.COLLECTED, S.REJECTED, S.WITHDRAWN, S.EXPIRED, S.CLOSED}
    assert not [k for k in claims_service.TRANSITIONS if k[0] in finals]


# ---------------- who may see and do what (app/services/items.py) ----------------

def test_public_found_item_is_visible_to_any_student():
    assert items.can_view(item(status=ItemStatus.IN_CUSTODY, reporter_id=5), student(1))


def test_found_item_awaiting_dropoff_is_hidden_from_other_students():
    it = item(status=ItemStatus.AWAITING_DROPOFF, reporter_id=5)
    assert not items.can_view(it, student(1))
    assert items.can_view(it, student(5))   # the finder
    assert items.can_view(it, guard())      # security


def test_closed_lost_report_is_only_visible_to_its_owner_and_staff():
    it = item(kind=ItemKind.LOST, status=ItemStatus.CLOSED, reporter_id=5)
    assert not items.can_view(it, student(1))
    assert items.can_view(it, student(5)) and items.can_view(it, guard())


def test_id_card_photos_are_hidden_from_other_students():
    card = item(slug="id-cards-documents", reporter_id=5)
    assert not items.photos_visible(card, student(1))
    assert items.photos_visible(card, student(5)) and items.photos_visible(card, guard())
    assert items.photos_visible(item(slug="electronics", reporter_id=5), student(1))


@pytest.mark.parametrize("status, editable", [
    (ItemStatus.AWAITING_DROPOFF, True), (ItemStatus.IN_CUSTODY, False),
    (ItemStatus.CLAIM_PENDING, False), (ItemStatus.RETURNED, False),
])
def test_finder_can_edit_only_before_hand_in(status, editable):
    assert items.can_edit(item(status=status, reporter_id=1), student(1)) is editable


def test_nobody_else_can_edit_a_report_and_copied_reports_are_locked():
    assert not items.can_edit(item(status=ItemStatus.AWAITING_DROPOFF, reporter_id=5), student(1))
    copied = item(status=ItemStatus.AWAITING_DROPOFF, reporter_id=1, answers_lost_id=7)
    assert not items.can_edit(copied, student(1))
    assert not items.can_manage_photos(copied, student(1))


def test_only_the_owner_can_close_an_open_lost_report():
    lost = item(kind=ItemKind.LOST, status=ItemStatus.OPEN, reporter_id=1)
    assert items.can_close(lost, student(1))
    assert not items.can_close(lost, student(2)) and not items.can_close(lost, guard())
    assert not items.can_close(item(kind=ItemKind.LOST, status=ItemStatus.RECOVERED, reporter_id=1), student(1))


def test_guards_can_photograph_found_items_only_while_held():
    assert items.can_manage_photos(item(status=ItemStatus.IN_CUSTODY, reporter_id=5), guard())
    assert not items.can_manage_photos(item(status=ItemStatus.RETURNED, reporter_id=5), guard())
    assert not items.can_manage_photos(item(kind=ItemKind.LOST, status=ItemStatus.OPEN, reporter_id=5), guard())


def test_photo_limit_of_three_is_enforced():
    it = item(status=ItemStatus.AWAITING_DROPOFF, reporter_id=1)
    it.photos = [ItemPhoto(stored_name=f"{i}.jpg") for i in range(2)]
    assert items.can_add_photos(it, student(1))
    it.photos.append(ItemPhoto(stored_name="3.jpg"))
    assert not items.can_add_photos(it, student(1))


@pytest.mark.parametrize("total, size, pages", [(0, 10, 0), (1, 10, 1), (10, 10, 1), (21, 10, 3)])
def test_page_count(total, size, pages):
    assert items._page([], total, 1, size)["total_pages"] == pages


# ---------------- matching maths (app/services/ai/semantic.py, embeddings.py) ----------------

@pytest.mark.parametrize("gap, score", [(0, 1.0), (15, 0.5), (30, 0.0), (90, 0.0)])
def test_date_score_fades_over_thirty_days(gap, score):
    assert semantic._date_score(date(2026, 10, 1), date.fromordinal(date(2026, 10, 1).toordinal() + gap)) == pytest.approx(score)


def test_date_phrase_is_computed_not_guessed():
    lost = item(kind=ItemKind.LOST, status=ItemStatus.OPEN)
    found = item(status=ItemStatus.IN_CUSTODY)
    found.happened_on = date(2026, 10, 1)
    assert semantic.date_phrase(lost, found) == "found the same day it was lost"
    found.happened_on = date(2026, 10, 2)
    assert semantic.date_phrase(lost, found) == "found 1 day after it was lost"
    found.happened_on = date(2026, 10, 4)
    assert semantic.date_phrase(found, lost) == "found 3 days after it was lost"  # order doesn't matter
    found.happened_on = date(2026, 9, 25)
    assert semantic.date_phrase(lost, found) is None  # found before it was lost


def test_matching_text_contains_item_details_but_nothing_personal():
    it = item(colour="Black", brand="Casio", description="fx-991 with my name sticker")
    it.reporter = User(email="secret.person@iitk.ac.in", full_name="Secret Person", phone="+919876543210")
    text = semantic.item_text(it)
    assert "Casio" in text and "Black" in text and "place: L7" in text
    assert "secret" not in text.lower() and "9876543210" not in text


def test_hashing_embedder_vectors_are_unit_length_and_repeatable():
    emb = HashingEmbedder()
    a1, a2 = emb.embed(["black casio calculator"]) * 1, emb.embed(["black casio calculator"])
    assert a1 == a2
    assert sum(v * v for v in a1[0]) == pytest.approx(1.0, abs=1e-5)
    assert len(a1[0]) == HashingEmbedder.DIM


def test_similar_descriptions_score_higher_than_unrelated_ones():
    emb = HashingEmbedder()
    q, near, far = emb.embed(["black casio calculator", "casio calculator black colour", "blue water bottle"])
    dot = lambda x, y: sum(a * b for a, b in zip(x, y))  # noqa: E731
    assert dot(q, near) > dot(q, far)


def test_empty_text_gives_a_zero_vector_not_an_error():
    assert not any(HashingEmbedder().embed([""])[0])


# ---------------- input validation (app/schemas) ----------------

BASE = {"title": "Calculator", "description": "Black Casio", "category_id": 1,
        "location_text": "L7", "happened_on": "2026-10-01"}


def test_report_text_is_tidied_and_empty_optionals_become_none():
    data = LostItemIn(**{**BASE, "title": "  Black   calculator ", "colour": "   ", "brand": ""})
    assert data.title == "Black calculator" and data.colour is None and data.brand is None


@pytest.mark.parametrize("field, value", [
    ("title", ""), ("title", "   "), ("title", "x" * 121), ("description", "   "),
    ("description", "x" * 1001), ("location_text", ""), ("happened_on", "not-a-date"),
    ("category_id", "abc"),
])
def test_bad_report_fields_are_rejected(field, value):
    with pytest.raises(ValidationError):
        LostItemIn(**{**BASE, field: value})


@pytest.mark.parametrize("raw, stored", [
    ("9876543210", "+919876543210"), ("98765 43210", "+919876543210"),
    ("98765-43210", "+919876543210"), ("+919876543210", "+919876543210"),
])
def test_phone_numbers_are_stored_in_one_format(raw, stored):
    assert OnboardingIn(full_name="A", phone=raw).phone == stored


@pytest.mark.parametrize("raw", ["12345", "5876543210", "98765432101", "abcdefghij", "", None])
def test_invalid_or_missing_phone_numbers_are_rejected(raw):
    with pytest.raises(ValidationError):
        OnboardingIn(full_name="A", phone=raw)


def test_blank_claim_and_unknown_close_outcome_are_rejected():
    with pytest.raises(ValidationError):
        ClaimIn(description="    ")
    with pytest.raises(ValidationError):
        ClaimIn(description="x" * 2001)
    with pytest.raises(ValidationError):
        CloseLostIn(outcome="DELETED")
