"""FastAPI dependencies for "who is calling, and are they allowed?".

An endpoint declares what it needs as a parameter, for example:

    def my_endpoint(user: StaffUser): ...

FastAPI runs the matching check first. If the check fails, the endpoint
never runs, and the caller gets a 401 (not signed in) or 403 (not allowed).
This keeps permission rules in one place instead of repeated in every endpoint.
"""

from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.database import get_db
from app.core.errors import forbidden, unauthorized
from app.models import User, UserRole
from app.services import auth as auth_service

DbSession = Annotated[Session, Depends(get_db)]


def get_current_user(request: Request, db: DbSession) -> User:
    """Read the session cookie and return the signed-in user."""
    token = request.cookies.get(get_settings().session_cookie_name)
    if not token:
        raise unauthorized()
    user = auth_service.get_user_for_token(db, token)
    if user is None:
        raise unauthorized("Your session has expired. Please sign in again.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_onboarded_user(user: CurrentUser) -> User:
    """Signed in AND has finished onboarding (name and roll number).
    Needed before reporting or claiming anything."""
    if not user.is_onboarded:
        raise forbidden("Please complete your profile first.")
    return user


OnboardedUser = Annotated[User, Depends(get_onboarded_user)]


def require_staff(user: OnboardedUser) -> User:
    """Guards and the admin: they share every item and claim power."""
    if not user.is_staff:
        raise forbidden("Only security staff can do this.")
    return user


StaffUser = Annotated[User, Depends(require_staff)]


def require_admin(user: OnboardedUser) -> User:
    """Only the admin: assigning and removing guards."""
    if user.role != UserRole.ADMIN:
        raise forbidden("Only the admin can do this.")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
