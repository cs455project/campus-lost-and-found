"""The TraceBack email format: both versions, the footer, the logo, the sender."""

import pytest

from app.core.config import get_settings
from app.services import email as email_service
from app.services.email import Email, build_message, render_html, render_text

MAIL = Email(subject="Subject", heading="Heading <b>", paragraphs=["First paragraph."], code="123456",
             details=[("Item", "ID card (FND-0001)")], steps=["Do this."], button=("Open", "http://x/items/1"),
             note="A note.")


def test_text_version_has_everything_and_the_auto_generated_footer():
    text = render_text(MAIL)
    for part in ("Heading <b>", "First paragraph.", "123456", "ID card (FND-0001)", "1. Do this.",
                 "http://x/items/1", "A note.", "auto-generated", "TraceBack"):
        assert part in text


def test_html_escapes_text_and_shows_the_portal_name():
    html = render_html(MAIL, "cid:logo")
    assert "Heading &lt;b&gt;" in html and "<b>" not in html.split("<h1")[1].split("</h1>")[0].replace("&lt;b&gt;", "")
    assert "TraceBack" in html and "IIT Kanpur Lost &amp; Found" in html and "auto-generated" in html


def test_message_has_text_html_and_the_embedded_logo(monkeypatch):
    monkeypatch.setattr(get_settings(), "smtp_user", "portal@gmail.com")
    monkeypatch.setattr(get_settings(), "email_from", "")
    message = build_message("someone@iitk.ac.in", MAIL)
    assert message["From"] == "TraceBack <portal@gmail.com>"
    types = [part.get_content_type() for part in message.walk()]
    assert "text/plain" in types and "text/html" in types and "image/png" in types
    html = message.get_body(("html",)).get_content()
    logo = next(p for p in message.walk() if p.get_content_type() == "image/png")
    assert "cid:" + logo["Content-ID"].strip("<>") in html


def test_smtp_without_a_password_fails_clearly(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "email_backend", "smtp")
    monkeypatch.setattr(settings, "smtp_password", "")
    with pytest.raises(email_service.EmailNotSent):
        email_service.send("a@iitk.ac.in", MAIL)
    assert email_service.send_quietly("a@iitk.ac.in", MAIL) is False  # background senders don't crash


def test_login_says_so_when_the_email_cannot_be_sent(make_client, monkeypatch):
    def broken(*args):
        raise email_service.EmailNotSent("SMTPAuthenticationError")
    monkeypatch.setattr(email_service, "send_login_code", broken)
    response = make_client().post("/auth/request-code", json={"email": "someone@iitk.ac.in"})
    assert response.status_code == 503 and "couldn't send the email" in response.json()["detail"]


def test_every_attempt_is_logged_without_the_sign_in_code(email_log, monkeypatch):
    email_service.send_login_code("a@iitk.ac.in", "482913", 10)  # console mode
    assert email_log[-1] == ("a@iitk.ac.in", "login_code", None, "printed", None)
    monkeypatch.setattr(get_settings(), "email_backend", "smtp")
    monkeypatch.setattr(get_settings(), "smtp_password", "")
    with pytest.raises(email_service.EmailNotSent):
        email_service.send("b@iitk.ac.in", Email(subject="S", heading="H", kind="id_card", ref="FND-0001"))
    to, kind, ref, status, error = email_log[-1]
    assert (to, kind, ref, status) == ("b@iitk.ac.in", "id_card", "FND-0001", "failed") and "SMTP" in error
    assert email_service.safe_subject(Email(subject="482913 is your TraceBack sign-in code", heading="",
                                            kind="login_code")) == "•••••• is your TraceBack sign-in code"


def test_the_log_row_is_stored(db):
    from sqlalchemy import select

    from app.models import EmailLog

    email_service.record_email("c@iitk.ac.in", Email(subject="123456 is your code", heading="", kind="login_code"),
                               "sent", db=db)
    row = db.scalar(select(EmailLog).where(EmailLog.to_email == "c@iitk.ac.in"))
    assert row.status == "sent" and row.subject == "•••••• is your code" and row.kind == "login_code"
