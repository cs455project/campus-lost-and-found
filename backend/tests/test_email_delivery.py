"""Tests for how emails leave the server (services/email.py): sending over
SMTP, what happens when the mail server fails, the email log, and the
console preview used in development. A fake SMTP server stands in for Gmail."""

import logging
import smtplib
from typing import ClassVar

import pytest
from sqlalchemy import select

from app.core import database
from app.core.config import get_settings
from app.models import EmailLog
from app.services import email as email_service
from app.services.email import Email, EmailNotSent

MAIL = Email(subject="Your item was handed in (FND-0042)", heading="Good news",
             paragraphs=["A guard received your item."], kind="notice", ref="FND-0042")


class FakeSMTP:
    """Records the conversation; `fail_at` makes one step raise."""

    instances: ClassVar[list] = []
    fail_at: ClassVar[str | None] = None

    def __init__(self, host, port, timeout):
        self.host, self.port, self.timeout = host, port, timeout
        self.steps, self.sent = [], []
        FakeSMTP.instances.append(self)
        if FakeSMTP.fail_at == "connect":
            raise ConnectionRefusedError("connection refused")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.steps.append("quit")
        return False

    def starttls(self):
        self.steps.append("starttls")

    def login(self, user, password):
        self.steps.append(("login", user, password))
        if FakeSMTP.fail_at == "login":
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    def send_message(self, message):
        self.steps.append("send")
        self.sent.append(message)


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.fail_at = [], None
    monkeypatch.setattr(email_service.smtplib, "SMTP", FakeSMTP)
    settings = get_settings()
    for name, value in {"email_backend": "smtp", "smtp_host": "smtp.gmail.com", "smtp_port": 587,
                        "smtp_user": "lostfound.portal@gmail.com", "smtp_password": "app-password",
                        "email_from": ""}.items():
        monkeypatch.setattr(settings, name, value)
    return FakeSMTP


def test_smtp_encrypts_logs_in_and_sends(smtp, email_log):
    email_service.send("owner@iitk.ac.in", MAIL)
    (server,) = smtp.instances
    assert (server.host, server.port, server.timeout) == ("smtp.gmail.com", 587, 20)
    # TLS before the password goes over the wire.
    assert server.steps == ["starttls", ("login", "lostfound.portal@gmail.com", "app-password"), "send", "quit"]
    (message,) = server.sent
    assert message["To"] == "owner@iitk.ac.in"
    assert message["Subject"] == MAIL.subject
    assert message["From"] == f"{get_settings().portal_name} <lostfound.portal@gmail.com>"
    assert email_log == [("owner@iitk.ac.in", "notice", "FND-0042", "sent", None)]


def test_a_full_from_address_is_used_as_it_is(smtp, monkeypatch):
    monkeypatch.setattr(get_settings(), "email_from", "Campus Security <security@iitk.ac.in>")
    email_service.send("owner@iitk.ac.in", MAIL)
    assert smtp.instances[0].sent[0]["From"] == "Campus Security <security@iitk.ac.in>"


@pytest.mark.parametrize("fail_at, error_type", [("login", "SMTPAuthenticationError"),
                                                 ("connect", "ConnectionRefusedError")])
def test_a_mail_server_failure_is_reported_and_logged(smtp, email_log, fail_at, error_type):
    smtp.fail_at = fail_at
    with pytest.raises(EmailNotSent, match=error_type):
        email_service.send("owner@iitk.ac.in", MAIL)
    ((to, _kind, _ref, status, error),) = email_log
    assert (to, status) == ("owner@iitk.ac.in", "failed") and error.startswith(error_type)


def test_send_quietly_never_raises(smtp, email_log):
    smtp.fail_at = "login"
    email_service.send_email("finder@iitk.ac.in", "Reminder", "Please hand in the item.\n\nThank you.")
    assert email_log[0][3] == "failed"  # the failure is logged, the caller carries on


def test_without_the_logo_file_the_html_has_no_embedded_image(monkeypatch, tmp_path):
    monkeypatch.setattr(email_service, "LOGO_FILE", tmp_path / "missing-logo.png")
    message = email_service.build_message("owner@iitk.ac.in", MAIL)
    text, html = message.get_payload()
    assert text.get_content_type() == "text/plain" and html.get_content_type() == "text/html"
    assert "cid:" not in html.get_content()
    assert "A guard received your item." in html.get_content()


# ---------------- the email log ----------------


def test_the_log_row_goes_into_its_own_session_when_none_is_given(db, monkeypatch, session_factory):
    monkeypatch.setattr(database, "SessionLocal", session_factory)
    email_service.record_email("owner@iitk.ac.in", MAIL, "failed", "SMTPServerDisconnected: closed")
    row = db.scalar(select(EmailLog).where(EmailLog.to_email == "owner@iitk.ac.in").order_by(EmailLog.id.desc()))
    assert (row.subject, row.kind, row.ref, row.status) == (MAIL.subject, "notice", "FND-0042", "failed")
    assert row.error == "SMTPServerDisconnected: closed"


def test_long_values_are_cut_to_fit_the_log(db):
    long_error = "x" * 900
    mail = Email(subject="S" * 400, heading="", kind="a-very-long-kind-name-indeed", ref="FND-0001")
    email_service.record_email("o" * 300 + "@iitk.ac.in", mail, "failed", long_error, db=db)
    row = db.scalar(select(EmailLog).where(EmailLog.ref == "FND-0001").order_by(EmailLog.id.desc()))
    assert (len(row.to_email), len(row.subject), len(row.kind), len(row.error)) == (254, 300, 20, 500)


def test_a_broken_log_never_stops_an_email(monkeypatch, caplog):
    def no_database():
        raise OSError("database is down")

    monkeypatch.setattr(database, "SessionLocal", no_database)
    with caplog.at_level(logging.WARNING):
        email_service.record_email("owner@iitk.ac.in", MAIL, "sent")  # must not raise
    assert "Could not write the email log: database is down" in caplog.text


def test_the_terminal_line_hides_sign_in_codes(db, capsys, caplog):
    code_mail = Email(subject="482913 is your TraceBack sign-in code", heading="", kind="login_code")
    with caplog.at_level(logging.INFO):
        email_service.record_email("student@iitk.ac.in", code_mail, "sent", db=db)
        email_service.record_email("student@iitk.ac.in", code_mail, "failed", "timeout", db=db)
    printed = capsys.readouterr().out
    assert "EMAIL SENT: to student@iitk.ac.in | •••••• is your TraceBack sign-in code" in printed
    assert "482913" not in printed and "482913" not in caplog.text
    assert "EMAIL FAILED" in caplog.text and "timeout" in caplog.text
    rows = list(db.scalars(select(EmailLog.subject).where(EmailLog.to_email == "student@iitk.ac.in")))
    assert rows and all("482913" not in subject for subject in rows)


# ---------------- console preview ----------------


def test_console_mode_prints_the_email_and_saves_an_html_preview(monkeypatch, capsys, tmp_path, email_log):
    monkeypatch.setattr(get_settings(), "email_backend", "console")
    monkeypatch.setattr(email_service, "PREVIEW_DIR", tmp_path / "previews")
    email_service.send("owner@iitk.ac.in", MAIL)
    out = capsys.readouterr().out
    assert "EMAIL to: owner@iitk.ac.in" in out and f"Subject: {MAIL.subject}" in out
    (saved,) = (tmp_path / "previews").iterdir()
    assert saved.name.endswith("-your-item-was-handed-in-fnd-0042.html")
    assert "Good news" in saved.read_text(encoding="utf-8")
    assert email_log[0][3] == "printed"


def test_console_mode_still_prints_when_the_preview_cannot_be_saved(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(get_settings(), "email_backend", "console")
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("a file where the folder should be")
    monkeypatch.setattr(email_service, "PREVIEW_DIR", blocker)
    email_service.send("owner@iitk.ac.in", MAIL)
    out = capsys.readouterr().out
    assert "EMAIL to: owner@iitk.ac.in" in out and "HTML version saved" not in out
