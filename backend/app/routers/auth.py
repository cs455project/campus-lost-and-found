"""Sign-in endpoints: request a code, verify it, see who I am, sign out."""

from fastapi import APIRouter, Request, Response, status

from app.core.config import get_settings
from app.core.deps import CurrentUser, DbSession
from app.schemas.auth import CodeSentOut, RequestCodeIn, VerifyCodeIn
from app.schemas.user import UserOut
from app.services import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/request-code", response_model=CodeSentOut, status_code=status.HTTP_202_ACCEPTED)
def request_code(data: RequestCodeIn, db: DbSession):
    """Step 1: email a 6-digit code. In development it's printed in the
    terminal running the API."""
    auth_service.request_login_code(db, data.email)
    minutes = get_settings().login_code_minutes
    return {
        "message": f"We sent a 6-digit code to your email. It expires in {minutes} minutes.",
        "expires_in_minutes": minutes,
    }


@router.post("/verify-code", response_model=UserOut)
def verify_code(data: VerifyCodeIn, response: Response, db: DbSession):
    """Step 2: check the code. On success, set the session cookie and
    return the user. If `is_onboarded` is false, show the onboarding form next."""
    user = auth_service.verify_login_code(db, data.email, data.code)
    token = auth_service.start_session(db, user)

    settings = get_settings()
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_days * 24 * 60 * 60,  # in seconds
        httponly=True,  # page JavaScript can't read it, so injected scripts can't steal it
        secure=settings.cookie_secure,  # https only, on the deployed site
        samesite="lax",  # other websites can't make the browser send it with their requests
        path="/",
    )
    return user


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    """Who is signed in? 401 if nobody."""
    return user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: DbSession):
    """Sign out: delete the session in the database and remove the cookie.
    Safe to call even when not signed in."""
    settings = get_settings()
    token = request.cookies.get(settings.session_cookie_name)
    if token:
        auth_service.end_session(db, token)
    response.delete_cookie(settings.session_cookie_name, path="/")
