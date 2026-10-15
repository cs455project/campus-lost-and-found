"""Tests for signing in with an email code."""

from datetime import timedelta

from sqlalchemy import select, update

from app.core.config import get_settings
from app.models import LoginCode, User
from app.services.auth import now

STUDENT = "riya@iitk.ac.in"


def wrong_code(right: str) -> str:
    """A 6-digit code guaranteed to differ from the right one."""
    return f"{(int(right) + 1) % 1_000_000:06d}"


def test_other_email_domains_are_refused(make_client):
    client = make_client()
    for email in ["riya@gmail.com", "riya@evil-iitk.ac.in", "riya@iitk.ac.in.evil.com"]:
        response = client.post("/auth/request-code", json={"email": email})
        assert response.status_code == 400, email
        assert "@iitk.ac.in" in response.json()["detail"]


def test_full_sign_in_creates_a_student_account(make_client, sent_codes, db):
    client = make_client()
    # Mixed case and spaces are normalized to one account.
    client.post("/auth/request-code", json={"email": "  Riya@IITK.ac.in "})
    response = client.post("/auth/verify-code", json={"email": STUDENT, "code": sent_codes[STUDENT]})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == STUDENT
    assert body["role"] == "STUDENT"
    assert body["is_onboarded"] is False

    # The cookie is set and identifies the user on later requests.
    assert get_settings().session_cookie_name in client.cookies
    assert client.get("/auth/me").json()["email"] == STUDENT


def test_codes_are_stored_only_as_hashes(make_client, sent_codes, db):
    make_client().post("/auth/request-code", json={"email": STUDENT})
    stored = db.scalar(select(LoginCode).where(LoginCode.email == STUDENT))
    assert stored.code_hash != sent_codes[STUDENT]
    assert len(stored.code_hash) == 64


def test_a_code_works_only_once(make_client, sent_codes):
    client = make_client()
    client.post("/auth/request-code", json={"email": STUDENT})
    code = sent_codes[STUDENT]
    assert client.post("/auth/verify-code", json={"email": STUDENT, "code": code}).status_code == 200
    again = client.post("/auth/verify-code", json={"email": STUDENT, "code": code})
    assert again.status_code == 400


def test_wrong_code_counts_down_then_locks(make_client, sent_codes):
    client = make_client()
    client.post("/auth/request-code", json={"email": STUDENT})
    right = sent_codes[STUDENT]

    first = client.post("/auth/verify-code", json={"email": STUDENT, "code": wrong_code(right)})
    assert first.status_code == 400
    assert "4 attempt(s) left" in first.json()["detail"]

    for _ in range(4):
        client.post("/auth/verify-code", json={"email": STUDENT, "code": wrong_code(right)})

    # Even the right code no longer works after 5 wrong attempts.
    locked = client.post("/auth/verify-code", json={"email": STUDENT, "code": right})
    assert locked.status_code == 400
    assert "Too many wrong attempts" in locked.json()["detail"]


def test_expired_code_is_refused(make_client, sent_codes, db):
    client = make_client()
    client.post("/auth/request-code", json={"email": STUDENT})
    db.execute(update(LoginCode).values(expires_at=now() - timedelta(seconds=1)))
    response = client.post("/auth/verify-code", json={"email": STUDENT, "code": sent_codes[STUDENT]})
    assert response.status_code == 400
    assert "expired" in response.json()["detail"]


def test_asking_again_too_soon_is_refused(make_client):
    client = make_client()
    client.post("/auth/request-code", json={"email": STUDENT})
    response = client.post("/auth/request-code", json={"email": STUDENT})
    assert response.status_code == 429


def test_a_new_code_replaces_the_old_one(make_client, sent_codes, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_code_resend_seconds", 0)
    client = make_client()
    client.post("/auth/request-code", json={"email": STUDENT})
    old = sent_codes[STUDENT]
    client.post("/auth/request-code", json={"email": STUDENT})
    new = sent_codes[STUDENT]

    if old != new:  # (1 in a million chance they're equal by luck)
        assert client.post("/auth/verify-code", json={"email": STUDENT, "code": old}).status_code == 400
    assert client.post("/auth/verify-code", json={"email": STUDENT, "code": new}).status_code == 200


def test_hourly_limit(make_client, monkeypatch):
    monkeypatch.setattr(get_settings(), "login_code_resend_seconds", 0)
    client = make_client()
    for _ in range(5):
        assert client.post("/auth/request-code", json={"email": STUDENT}).status_code == 202
    assert client.post("/auth/request-code", json={"email": STUDENT}).status_code == 429


def test_not_signed_in_gets_401(make_client):
    assert make_client().get("/auth/me").status_code == 401


def test_logout_ends_the_session(sign_in):
    client = sign_in(STUDENT)
    stolen_cookie = dict(client.cookies)
    assert client.post("/auth/logout").status_code == 204
    assert client.get("/auth/me").status_code == 401
    # The old cookie value is dead on the server too, not just deleted in the browser.
    client.cookies.update(stolen_cookie)
    assert client.get("/auth/me").status_code == 401


def test_deactivated_user_is_signed_out_and_cannot_sign_in(
    sign_in, make_client, sent_codes, db, monkeypatch
):
    monkeypatch.setattr(get_settings(), "login_code_resend_seconds", 0)
    client = sign_in(STUDENT)
    db.execute(update(User).where(User.email == STUDENT).values(is_active=False))
    assert client.get("/auth/me").status_code == 401

    other = make_client()
    other.post("/auth/request-code", json={"email": STUDENT})
    response = other.post("/auth/verify-code", json={"email": STUDENT, "code": sent_codes[STUDENT]})
    assert response.status_code == 403
