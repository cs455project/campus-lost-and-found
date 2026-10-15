"""Signing in with a one-time email code, and keeping people signed in.

The flow:
  1. request_login_code: student enters their email -> we email a 6-digit code
  2. verify_login_code:  student enters the code -> we find or create their
                         account and start a session
  3. the session token goes into a cookie; every later request is matched
     to the user by get_user_for_token
  4. end_session on sign out
"""

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, bad_request, forbidden, too_many_requests
from app.core.security import (
    generate_login_code,
    generate_session_token,
    hash_secret,
    secrets_match,
)
from app.models import LoginCode, User, UserSession
from app.services import email as email_service

log = logging.getLogger(__name__)


def now() -> datetime:
    """The current time in UTC. All times are stored with their time zone,
    so comparisons are always correct."""
    return datetime.now(UTC)


def normalize_email(raw: str) -> str:
    return raw.strip().lower()


def _domain(email: str) -> str:
    # The part after the LAST "@", compared exactly, so "x@evil-iitk.ac.in"
    # or "x@iitk.ac.in.evil.com" never count as iitk.ac.in.
    return email.rsplit("@", 1)[-1]


def _staff_domains() -> set[str]:
    raw = get_settings().staff_email_domains
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def check_allowed_domain(email: str) -> None:
    """Students: only the campus domain."""
    domain = get_settings().allowed_email_domain
    if _domain(email) != domain:
        raise bad_request(f"Please use your @{domain} email address.")


def check_staff_email(email: str) -> None:
    """Guards and the admin: the campus domain, or a staff domain (gmail.com)."""
    allowed = {get_settings().allowed_email_domain, *_staff_domains()}
    if _domain(email) not in allowed:
        raise bad_request("Use an " + " or ".join(f"@{d}" for d in sorted(allowed)) + " email address.")


def check_can_sign_in(db: Session, email: str) -> None:
    """Who may sign in with this email?
      - any @iitk.ac.in address (new accounts start as students)
      - a staff-domain address (e.g. @gmail.com) ONLY if that account is
        already a guard or the admin. Nobody can become staff by signing
        in with Gmail: the admin must add them first.
    """
    domain = _domain(email)
    if domain == get_settings().allowed_email_domain:
        return
    if domain in _staff_domains():
        user = db.scalar(select(User).where(User.email == email))
        if user is not None and user.is_staff:
            return
    raise bad_request(
        f"Please use your @{get_settings().allowed_email_domain} email address. "
        "Other addresses work only for security staff added by the admin."
    )


def request_login_code(db: Session, raw_email: str) -> None:
    settings = get_settings()
    email = normalize_email(raw_email)
    check_can_sign_in(db, email)
    current = now()

    # ---- Rate limits: stop someone flooding an inbox with codes ----
    latest = db.scalar(
        select(func.max(LoginCode.created_at)).where(LoginCode.email == email)
    )
    if latest is not None:
        wait = settings.login_code_resend_seconds - (current - latest).total_seconds()
        if wait > 0:
            raise too_many_requests(
                f"Please wait {int(wait) + 1} seconds before requesting another code."
            )

    sent_last_hour = db.scalar(
        select(func.count())
        .select_from(LoginCode)
        .where(LoginCode.email == email, LoginCode.created_at > current - timedelta(hours=1))
    )
    if sent_last_hour >= settings.login_code_max_per_hour:
        raise too_many_requests("Too many codes requested. Please try again in an hour.")

    # ---- Only the newest code works: retire any older unused ones ----
    db.execute(
        update(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None))
        .values(consumed_at=current)
    )

    code = generate_login_code()
    db.add(
        LoginCode(
            email=email,
            code_hash=hash_secret(code),
            expires_at=current + timedelta(minutes=settings.login_code_minutes),
            created_at=current,
        )
    )
    # Save before sending: if saving fails, no email goes out with a code
    # that doesn't exist.
    db.commit()
    try:
        email_service.send_login_code(email, code, settings.login_code_minutes)
    except email_service.EmailNotSent as error:
        log.error("Sign-in code for %s not sent: %s", email, error)
        raise AppError(503, "We couldn't send the email just now. Please try again in a minute.") from error


def verify_login_code(db: Session, raw_email: str, code: str) -> User:
    """Check the code. On success, return the user (creating the account
    on their first sign-in). On failure, raise an error explaining why."""
    settings = get_settings()
    email = normalize_email(raw_email)
    # Checked again here: a guard may have been removed after the code was sent.
    check_can_sign_in(db, email)
    current = now()

    login_code = db.scalar(
        select(LoginCode)
        .where(LoginCode.email == email, LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
        # Lock this row until we commit, so two simultaneous guesses
        # can't both slip past the attempts limit.
        .with_for_update()
    )

    if login_code is None or login_code.expires_at <= current:
        raise bad_request("This code has expired or was replaced. Request a new one.")

    if login_code.attempts >= settings.login_code_max_attempts:
        raise bad_request("Too many wrong attempts. Request a new code.")

    if not secrets_match(code.strip(), login_code.code_hash):
        login_code.attempts += 1
        left = settings.login_code_max_attempts - login_code.attempts
        db.commit()  # save the failed attempt before refusing
        if left == 0:
            raise bad_request("Too many wrong attempts. Request a new code.")
        raise bad_request(f"Incorrect code. {left} attempt(s) left.")

    # Correct code: it can never be used again.
    login_code.consumed_at = current

    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        # First sign-in: everyone starts as a student. Guards and the admin
        # are promoted separately.
        user = User(email=email)
        db.add(user)
    elif not user.is_active:
        db.commit()  # still mark the code as used
        raise forbidden("This account has been deactivated. Contact the security office.")

    db.commit()
    return user


def start_session(db: Session, user: User) -> str:
    """Create a session and return the token for the cookie. Only the
    token's hash is stored; the plain token exists only in the browser."""
    token = generate_session_token()
    db.add(
        UserSession(
            token_hash=hash_secret(token),
            user_id=user.id,
            expires_at=now() + timedelta(days=get_settings().session_days),
        )
    )
    db.commit()
    return token


def get_user_for_token(db: Session, token: str) -> User | None:
    """Find the signed-in user for a cookie token, or None if the session
    doesn't exist, has expired, or the user was deactivated."""
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == hash_secret(token))
    )
    if session is None or session.expires_at <= now():
        return None
    if not session.user.is_active:
        return None
    return session.user


def end_session(db: Session, token: str) -> None:
    db.execute(delete(UserSession).where(UserSession.token_hash == hash_secret(token)))
    db.commit()
