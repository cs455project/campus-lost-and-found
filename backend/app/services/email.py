"""Sending emails, in the TraceBack style.

Every email has two versions in one message:
  - HTML: a white card with a heading, short paragraphs, an optional big
    code, an optional table of details, an optional button, and a footer
    with the TraceBack logo (embedded in the email, so it shows without
    loading anything from the internet).
  - plain text: the same words, for mail apps that don't show HTML.

EMAIL_BACKEND=console (default): nothing is sent. The text version is
  printed in the terminal running the API, and the HTML version is saved in
  backend/.cache/emails/ so you can open it in a browser to see the design.
EMAIL_BACKEND=smtp: really sent, e.g. through Gmail:
  SMTP_HOST=smtp.gmail.com  SMTP_PORT=587  SMTP_USER=<the Gmail address>
  SMTP_PASSWORD=<a Gmail App Password, NOT the normal password>
"""

import logging
import re
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, make_msgid
from html import escape
from pathlib import Path

from app.core.config import BACKEND_DIR, get_settings

log = logging.getLogger(__name__)

LOGO_FILE = Path(__file__).resolve().parent.parent / "static" / "traceback-logo.png"
PREVIEW_DIR = BACKEND_DIR / ".cache" / "emails"

BRAND = "#2c41c4"
INK = "#111827"
SOFT = "#4b5563"
FAINT = "#6b7280"
RULE = "#e5e7eb"


class EmailNotSent(Exception):
    """The mail server refused or couldn't be reached."""


@dataclass
class Email:
    """The content of one email. Only `subject` and `heading` are required."""

    subject: str
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    code: str | None = None  # shown big, e.g. a sign-in code
    details: list[tuple[str, str]] = field(default_factory=list)  # label, value rows
    steps: list[str] = field(default_factory=list)  # numbered "what to do next"
    button: tuple[str, str] | None = None  # (label, url)
    note: str | None = None  # small grey text at the end
    # For the email log only (not shown): what kind of email, about which item.
    kind: str = "notice"  # login_code, id_card, match, notice
    ref: str | None = None  # e.g. "FND-0735"


# ---------------- Building the two versions ----------------


def footer_text() -> str:
    settings = get_settings()
    return (f"This is an auto-generated email from {settings.portal_name}, the {settings.portal_tagline} "
            "portal. Please do not reply to it.")


def render_text(mail: Email) -> str:
    settings = get_settings()
    parts = [mail.heading, ""]
    parts += [p + "\n" for p in mail.paragraphs]
    if mail.code:
        parts += [f"    {mail.code}", ""]
    if mail.details:
        width = max(len(label) for label, _ in mail.details)
        parts += [f"{label.ljust(width)} : {value}" for label, value in mail.details] + [""]
    if mail.steps:
        parts += ["What to do next:"] + [f"  {n}. {step}" for n, step in enumerate(mail.steps, 1)] + [""]
    if mail.button:
        parts += [f"{mail.button[0]}: {mail.button[1]}", ""]
    if mail.note:
        parts += [mail.note, ""]
    parts += ["--", f"{settings.portal_name} · {settings.portal_tagline}", footer_text()]
    return "\n".join(parts)


def render_html(mail: Email, logo_src: str | None) -> str:
    settings = get_settings()
    font = "font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;"
    rows = [f'<h1 style="margin:0 0 16px;{font}font-size:22px;line-height:1.3;color:{INK};">'
            f"{escape(mail.heading)}</h1>"]
    for paragraph in mail.paragraphs:
        rows.append(f'<p style="margin:0 0 14px;{font}font-size:15px;line-height:1.6;color:{SOFT};">'
                    f"{escape(paragraph)}</p>")
    if mail.code:
        rows.append(
            f'<div style="margin:6px 0 20px;padding:16px 0;text-align:center;background:#edf0fe;'
            f'border-radius:10px;font-family:Consolas,Menlo,monospace;font-size:32px;font-weight:700;'
            f'letter-spacing:8px;color:{BRAND};">{escape(mail.code)}</div>')
    if mail.details:
        cells = "".join(
            f'<tr><td width="1%" style="padding:8px 28px 8px 0;{font}font-size:14px;color:{FAINT};white-space:nowrap;'
            f'vertical-align:top;border-top:1px solid {RULE};">{escape(label)}</td>'
            f'<td style="padding:8px 0;{font}font-size:14px;color:{INK};font-weight:600;'
            f'border-top:1px solid {RULE};">{escape(value)}</td></tr>'
            for label, value in mail.details)
        rows.append(f'<table role="presentation" cellpadding="0" cellspacing="0" width="100%" '
                    f'style="margin:4px 0 20px;border-bottom:1px solid {RULE};">{cells}</table>')
    if mail.steps:
        items = "".join(f'<li style="margin:0 0 6px;">{escape(step)}</li>' for step in mail.steps)
        rows.append(f'<p style="margin:0 0 6px;{font}font-size:15px;font-weight:700;color:{INK};">What to do next</p>'
                    f'<ol style="margin:0 0 20px;padding-left:20px;{font}font-size:15px;line-height:1.6;'
                    f'color:{SOFT};">{items}</ol>')
    if mail.button:
        label, url = mail.button
        rows.append(f'<p style="margin:4px 0 22px;"><a href="{escape(url, quote=True)}" style="display:inline-block;'
                    f'padding:12px 22px;background:{BRAND};color:#ffffff;text-decoration:none;border-radius:8px;'
                    f'{font}font-size:15px;font-weight:700;">{escape(label)}</a></p>')
    if mail.note:
        rows.append(f'<p style="margin:0;{font}font-size:13px;line-height:1.5;color:{FAINT};">'
                    f"{escape(mail.note)}</p>")

    logo = (f'<img src="{logo_src}" width="36" height="36" alt="" '
            f'style="display:block;border:0;border-radius:9px;">' if logo_src else "")
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"></head>
<body style="margin:0;padding:0;background:#f4f5f8;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f5f8;">
<tr><td align="center" style="padding:28px 12px;">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         style="max-width:560px;background:#ffffff;border:1px solid {RULE};border-radius:14px;">
    <tr><td style="padding:30px 32px 26px;">{"".join(rows)}</td></tr>
    <tr><td style="padding:18px 32px 24px;border-top:1px solid {RULE};">
      <table role="presentation" cellpadding="0" cellspacing="0"><tr>
        <td style="padding-right:12px;vertical-align:middle;">{logo}</td>
        <td style="vertical-align:middle;{font}">
          <div style="font-size:16px;font-weight:800;color:{BRAND};">{escape(settings.portal_name)}</div>
          <div style="font-size:11px;letter-spacing:1.5px;text-transform:uppercase;color:{FAINT};">
            {escape(settings.portal_tagline)}</div>
        </td></tr></table>
      <p style="margin:14px 0 0;{font}font-size:12px;line-height:1.5;color:{FAINT};">{escape(footer_text())}</p>
    </td></tr>
  </table>
</td></tr></table>
</body></html>"""


def build_message(to: str, mail: Email) -> EmailMessage:
    settings = get_settings()
    message = EmailMessage()
    sender = settings.email_from or settings.smtp_user
    message["From"] = formataddr((settings.portal_name, sender)) if "<" not in sender else sender
    message["To"] = to
    message["Subject"] = mail.subject
    message.set_content(render_text(mail))
    if LOGO_FILE.exists():
        cid = make_msgid(domain="traceback.local")
        message.add_alternative(render_html(mail, "cid:" + cid[1:-1]), subtype="html")
        # The logo travels inside the email ("related" part), so mail apps show
        # it without downloading anything.
        message.get_payload()[1].add_related(LOGO_FILE.read_bytes(), "image", "png", cid=cid)
    else:
        message.add_alternative(render_html(mail, None), subtype="html")
    return message


# ---------------- Sending ----------------


def send(to: str, mail: Email) -> None:
    """Send (or, in console mode, print) one email, and log the attempt."""
    settings = get_settings()
    if settings.email_backend == "console":
        _preview(to, mail)
        _record(to, mail, "printed")
        return
    try:
        if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
            raise EmailNotSent("EMAIL_BACKEND=smtp needs SMTP_HOST, SMTP_USER and SMTP_PASSWORD in backend/.env")
        message = build_message(to, mail)
        try:
            # timeout: don't let a slow mail server freeze the request forever.
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as server:
                server.starttls()  # encrypt the connection before logging in
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
        except (smtplib.SMTPException, OSError) as error:
            raise EmailNotSent(f"{type(error).__name__}: {error}") from error
    except EmailNotSent as error:
        _record(to, mail, "failed", str(error))
        raise
    _record(to, mail, "sent")


def safe_subject(mail: Email) -> str:
    """The subject for logs: sign-in codes are replaced by dots."""
    if mail.kind == "login_code":
        return re.sub(r"\d", "•", mail.subject)
    return mail.subject


def record_email(to: str, mail: Email, status: str, error: str | None = None, db=None) -> None:
    """Write one row to email_logs and one line to the terminal. Never raises:
    a logging problem must not stop an email or a page."""
    line = f"EMAIL {status.upper()}: to {to} | {safe_subject(mail)}" + (f" | {mail.ref}" if mail.ref else "")
    if error:
        log.error("%s | %s", line, error)
    else:
        log.info(line)
        if status == "sent":
            print(line, flush=True)  # visible in the uvicorn terminal even without logging set up
    try:
        from app.core.database import SessionLocal
        from app.models import EmailLog

        row = EmailLog(to_email=to[:254], subject=safe_subject(mail)[:300], kind=mail.kind[:20],
                       ref=mail.ref, status=status, error=(error or None) and error[:500])
        if db is not None:
            db.add(row)
            db.flush()
            return
        with SessionLocal() as own:  # its own short transaction: works from any thread
            own.add(row)
            own.commit()
    except Exception as exc:  # noqa: BLE001 (the log is best effort)
        log.warning("Could not write the email log: %s", exc)


_record = record_email  # tests replace this with a collector


def _preview(to: str, mail: Email) -> None:
    """Console mode: print the text, save the HTML for viewing in a browser."""
    saved = ""
    try:
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        slug = re.sub(r"[^a-z0-9]+", "-", mail.subject.lower()).strip("-")[:50]
        path = PREVIEW_DIR / f"{datetime.now():%Y%m%d-%H%M%S}-{slug}.html"
        logo = LOGO_FILE.resolve().as_uri() if LOGO_FILE.exists() else None
        path.write_text(render_html(mail, logo), encoding="utf-8")
        saved = f"\n(HTML version saved to {path})"
    except OSError:
        pass
    print("\n" + "=" * 64 + f"\nEMAIL to: {to}\nSubject: {mail.subject}\n\n{render_text(mail)}{saved}\n"
          + "=" * 64 + "\n", flush=True)


def send_quietly(to: str, mail: Email) -> bool:
    """For notices sent in the background (reminders, AI alerts): a mail
    problem is logged, never allowed to break the job that sends it."""
    try:
        send(to, mail)
        return True
    except EmailNotSent as error:
        log.error("Email to %s (%s) not sent: %s", to, mail.subject, error)
        return False


def send_email(to: str, subject: str, body: str) -> None:
    """For simple notices: `body` is plain text; blank lines separate paragraphs."""
    paragraphs = [" ".join(p.split()) for p in body.split("\n\n") if p.strip()]
    send_quietly(to, Email(subject=subject, heading=subject, paragraphs=paragraphs))


def item_link(item_id: int) -> str:
    return f"{get_settings().frontend_origin.rstrip('/')}/items/{item_id}"


# ---------------- The emails ----------------


def send_login_code(to: str, code: str, minutes_valid: int) -> None:
    name = get_settings().portal_name
    send(to, Email(
        kind="login_code",
        subject=f"{code} is your {name} sign-in code",
        heading="Your sign-in code",
        paragraphs=[f"Use this code to sign in to {name}:"],
        code=code,
        details=[("Valid for", f"{minutes_valid} minutes"), ("Can be used", "once")],
        note="If you did not try to sign in, ignore this email. Nobody can sign in without this code, "
             "and never share it with anyone, including security staff.",
    ))
