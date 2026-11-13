"""Tests for the fixes made after the security review and the adversarial
tests (testing/adversarial_tests.py):

- photo metadata (GPS location) removed on upload       (ADV-17)
- security headers on every API response                 (ADV-16)
- API docs switched off in production                    (ADV-21)
- temporary mail-server failures retried                 (failed emails were never retried)
- no "PLACEHOLDER" office directions in emails           (seen in a real email, FND-0740)
- the AI agent's limits are settings, not constants
"""

import importlib
import io
import smtplib
from typing import ClassVar

import pytest
from PIL import Image, PngImagePlugin
from sqlalchemy import select

from app import cli, main, seed
from app.core.config import get_settings
from app.models import Office
from app.services import email as email_service
from app.services import storage
from app.services.ai import agent
from app.services.email import Email, EmailNotSent
from tests.test_claims import (  # noqa: F401  (fixtures)
    approve,
    claim,
    emails,
    held_item,
    person,
    place,
)

GPS = 0x8825
ORIENTATION = 0x0112


def phone_photo(orientation=6, trailing=b""):
    """A JPEG like a phone's: EXIF with GPS, camera make and rotation, plus XMP."""
    image = Image.new("RGB", (320, 240))
    for x in range(0, 320, 3):
        for y in range(0, 240, 3):
            image.putpixel((x, y), (x % 256, y % 256, (x * y) % 256))
    exif = Image.Exif()
    exif[ORIENTATION] = orientation
    exif[0x010F] = "Samsung"
    exif[GPS] = {1: "N", 2: (26.0, 30.0, 52.0), 3: "E", 4: (80.0, 13.0, 59.0)}
    out = io.BytesIO()
    image.save(out, "JPEG", quality=90, exif=exif,
               xmp=b"<x:xmpmeta><exif:GPSLatitude>26,30.86N</exif:GPSLatitude></x:xmpmeta>")
    return out.getvalue() + trailing


# ---------------- photo metadata ----------------


def test_jpeg_loses_gps_and_xmp_but_keeps_its_picture_and_rotation():
    original = phone_photo()
    cleaned = storage.strip_metadata(original, "image/jpeg")
    with Image.open(io.BytesIO(cleaned)) as after, Image.open(io.BytesIO(original)) as before:
        assert dict(after.getexif()) == {ORIENTATION: 6}  # only the rotation is left
        assert not after.getexif().get_ifd(GPS)
        assert after.tobytes() == before.tobytes()  # not re-encoded: same pixels
    assert b"Samsung" not in cleaned and b"GPSLatitude" not in cleaned


def test_an_upright_photo_keeps_no_exif_at_all():
    cleaned = storage.strip_metadata(phone_photo(orientation=1), "image/jpeg")
    with Image.open(io.BytesIO(cleaned)) as after:
        assert not after.getexif()


def test_data_after_the_picture_is_dropped():
    # Some phones append a second picture (with its own EXIF) after the first.
    cleaned = storage.strip_metadata(phone_photo(trailing=b"\xff\xd8SECOND-IMAGE-GPS\xff\xd9"), "image/jpeg")
    assert b"SECOND-IMAGE" not in cleaned
    assert cleaned.endswith(b"\xff\xd9")


def test_png_and_webp_are_saved_again_without_metadata():
    image = Image.new("RGB", (60, 40), (10, 200, 30))
    info = PngImagePlugin.PngInfo()
    info.add_text("Location", "26.51N 80.23E")
    png = io.BytesIO()
    image.save(png, "PNG", pnginfo=info)
    cleaned = storage.strip_metadata(png.getvalue(), "image/png")
    assert b"26.51N" in png.getvalue() and b"26.51N" not in cleaned
    with Image.open(io.BytesIO(cleaned)) as after:
        assert after.size == (60, 40) and after.tobytes() == image.tobytes()  # PNG stays lossless

    exif = Image.Exif()
    exif[GPS] = {1: "N", 2: (26.0, 30.0, 0.0)}
    webp = io.BytesIO()
    image.save(webp, "WEBP", exif=exif)
    with Image.open(io.BytesIO(storage.strip_metadata(webp.getvalue(), "image/webp"))) as after:
        assert not after.getexif()


def test_padding_bytes_between_segments_are_handled():
    padded = phone_photo()[:2] + b"\xff\xff" + phone_photo()[2:]  # allowed by the JPEG standard
    with Image.open(io.BytesIO(storage.strip_metadata(padded, "image/jpeg"))) as after:
        assert dict(after.getexif()) == {ORIENTATION: 6}


@pytest.mark.parametrize("cut", [lambda d: d[: len(d) // 2], lambda d: d[:-2]], ids=["half", "no-end-marker"])
def test_a_damaged_photo_with_metadata_is_refused_not_shared(cut):
    from app.core.errors import AppError

    with pytest.raises(AppError, match="damaged"):
        storage.strip_metadata(cut(phone_photo()), "image/jpeg")


def test_a_file_pillow_cannot_read_is_left_as_it_is():
    fake = b"\xff\xd8\xff\xe0" + b"0" * 50
    assert storage.strip_metadata(fake, "image/jpeg") == fake


def test_uploaded_item_photos_reach_other_students_without_gps(person, held_item):  # noqa: F811
    guard, other = held_item["guard"], person()
    # The guard photographs the item at the office; then every student can see it.
    response = guard.post(f"/items/{held_item['id']}/photos",
                           files={"file": ("IMG_2041.jpg", io.BytesIO(phone_photo()), "image/jpeg")})
    assert response.status_code == 201, response.text
    photo_id = response.json()["photo_ids"][0]
    download = other.get(f"/items/{held_item['id']}/photos/{photo_id}")  # the item is at the office: public
    assert download.status_code == 200
    with Image.open(io.BytesIO(download.content)) as image:
        assert not image.getexif().get_ifd(GPS)


def test_claim_proofs_are_cleaned_too(db, person, held_item):  # noqa: F811
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    upload = owner.post(f"/claims/{claim_id}/proofs",
                        files={"file": ("bill.jpg", io.BytesIO(phone_photo()), "image/jpeg")})
    assert upload.status_code == 201, upload.text
    proof = owner.get(f"/claims/{claim_id}/proofs/{upload.json()['proof_ids'][0]}")
    with Image.open(io.BytesIO(proof.content)) as image:
        assert not image.getexif().get_ifd(GPS)


# ---------------- security headers and API docs ----------------


EXPECTED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "content-security-policy": "frame-ancestors 'none'",
}


def test_every_api_response_carries_the_security_headers(make_client, person, held_item):  # noqa: F811
    responses = [
        make_client().get("/health"),                       # public
        make_client().get("/auth/me"),                      # 401
        person().get(f"/items/{held_item['id']}"),          # a page of data
        person().get("/items/999999"),                      # 404
    ]
    for response in responses:
        for name, value in EXPECTED_HEADERS.items():
            assert response.headers.get(name) == value, (response.request.url, name)


def test_photos_are_served_with_nosniff(person, held_item):  # noqa: F811
    photo_id = held_item["guard"].post(f"/items/{held_item['id']}/photos",
                           files={"file": ("a.jpg", io.BytesIO(phone_photo()), "image/jpeg")}).json()["photo_ids"][0]
    response = person().get(f"/items/{held_item['id']}/photos/{photo_id}")
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.fixture
def app_without_docs(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_docs", False)
    yield importlib.reload(main).app
    monkeypatch.undo()
    importlib.reload(main)  # back to the default app for the other tests


def test_api_docs_can_be_switched_off(app_without_docs):
    from fastapi.testclient import TestClient

    with TestClient(app_without_docs) as client:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404
        assert client.get("/health").status_code in (200, 503)  # the API itself still works


def test_api_docs_are_on_by_default_for_development(make_client):
    assert make_client().get("/openapi.json").status_code == 200


# ---------------- email retries ----------------


class FlakySMTP:
    """Fails the first `failures` connections with `error`, then works."""

    error: ClassVar[Exception] = smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
    failures: ClassVar[int] = 1
    attempts: ClassVar[int] = 0
    sent: ClassVar[list] = []

    def __init__(self, host, port, timeout):
        FlakySMTP.attempts += 1
        if FlakySMTP.attempts <= FlakySMTP.failures:
            raise FlakySMTP.error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self):
        pass

    def login(self, user, password):
        pass

    def send_message(self, message):
        FlakySMTP.sent.append(message["To"])


@pytest.fixture
def flaky_smtp(monkeypatch):
    FlakySMTP.attempts, FlakySMTP.sent = 0, []
    monkeypatch.setattr(email_service.smtplib, "SMTP", FlakySMTP)
    pauses = []
    monkeypatch.setattr(email_service.time, "sleep", pauses.append)
    settings = get_settings()
    for name, value in {"email_backend": "smtp", "smtp_host": "smtp.gmail.com", "smtp_user": "portal@gmail.com",
                        "smtp_password": "app-password", "email_retries": 2, "email_retry_seconds": 2.0}.items():
        monkeypatch.setattr(settings, name, value)
    return pauses


MAIL = Email(subject="Your claim is approved", heading="Good news", kind="notice", ref="CLM-0007")


@pytest.mark.parametrize("error", [
    smtplib.SMTPServerDisconnected("Connection unexpectedly closed"),
    ConnectionResetError("Connection reset by peer"),
    smtplib.SMTPResponseException(421, b"Service not available, try again later"),
])
def test_a_temporary_failure_is_retried_and_the_email_goes_out(flaky_smtp, email_log, error):
    FlakySMTP.error, FlakySMTP.failures = error, 2
    email_service.send("owner@iitk.ac.in", MAIL)
    assert FlakySMTP.attempts == 3 and FlakySMTP.sent == ["owner@iitk.ac.in"]
    assert flaky_smtp == [2.0, 4.0]  # waits longer each time
    assert [row[3] for row in email_log] == ["sent"]  # one log row, for the email that went out


def test_after_the_last_try_the_failure_is_reported(flaky_smtp, email_log):
    FlakySMTP.error, FlakySMTP.failures = smtplib.SMTPServerDisconnected("closed"), 99
    with pytest.raises(EmailNotSent, match="SMTPServerDisconnected"):
        email_service.send("owner@iitk.ac.in", MAIL)
    assert FlakySMTP.attempts == 3
    assert [row[3] for row in email_log] == ["failed"]


def test_a_wrong_password_is_not_retried(flaky_smtp, email_log):
    FlakySMTP.error = smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")
    FlakySMTP.failures = 99
    with pytest.raises(EmailNotSent, match="SMTPAuthenticationError"):
        email_service.send("owner@iitk.ac.in", MAIL)
    assert FlakySMTP.attempts == 1 and flaky_smtp == []


# ---------------- office directions ----------------


def test_an_office_without_directions_is_named_with_its_hours_only():
    office = Office(name="Main Security Office", location_description="PLACEHOLDER: update with real directions",
                    opening_hours="24 hours")
    assert office.directions == ""
    assert office.place() == "Main Security Office (open 24 hours)"
    office.location_description = "Next to the main gate"
    assert office.place() == "Main Security Office (Next to the main gate; open 24 hours)"


def test_approval_email_never_shows_placeholder_text(db, person, held_item, emails):  # noqa: F811
    office = db.scalar(select(Office).where(Office.name == "Test Main"))
    office.location_description = "PLACEHOLDER: update with real directions"
    db.commit()
    owner = person()
    claim_id = claim(owner, held_item["id"]).json()["id"]
    assert approve(held_item["guard"], claim_id).status_code == 200
    (_to, subject, body), = [e for e in emails if "approved" in e[1]]
    assert "PLACEHOLDER" not in body
    assert "Collect it from Test Main (open 24 hours)" in body


def test_seed_clears_placeholder_directions_left_by_older_versions(db):
    db.add(Office(name="Old Seeded Office", location_description="PLACEHOLDER: update with real directions",
                  opening_hours="9 to 5"))
    db.flush()
    assert seed.clear_placeholder_directions(db) >= 1
    assert db.scalar(select(Office.location_description).where(Office.name == "Old Seeded Office")) == ""
    assert seed.clear_placeholder_directions(db) == 0
    assert all(office["location_description"] == "" for office in seed.OFFICES)


def test_set_office_command_sets_directions_hours_and_phone(db, monkeypatch, session_factory, capsys):
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    cli.set_office("main security office", where="Near the main gate, opposite the shopping complex",
                   hours="24 hours", phone="0512 259 0000")
    office = db.scalar(select(Office).where(Office.name == "Main Security Office"))
    db.refresh(office)
    assert office.location_description == "Near the main gate, opposite the shopping complex"
    assert office.contact_phone == "0512 259 0000"
    assert "Main Security Office (Near the main gate" in capsys.readouterr().out


def test_set_office_with_an_unknown_name_lists_the_offices(monkeypatch, session_factory):
    monkeypatch.setattr(cli, "SessionLocal", session_factory)
    with pytest.raises(Exception, match="No office called 'Library'.*Main Security Office"):
        cli.set_office("Library", where="x", hours=None, phone=None)


# ---------------- agent settings ----------------


def test_when_the_agent_is_asked_follows_the_settings(monkeypatch):
    clear_winner = [(0.92, "a"), (0.60, "b")]
    close_call = [(0.81, "a"), (0.76, "b")]
    assert not agent.is_ambiguous(clear_winner)
    assert agent.is_ambiguous(close_call)
    monkeypatch.setattr(get_settings(), "agent_close_scores", 0.02)
    assert not agent.is_ambiguous(close_call)  # tuned without a code change
    monkeypatch.setattr(get_settings(), "agent_unsure_below", 0.95)
    assert agent.is_ambiguous(clear_winner)
