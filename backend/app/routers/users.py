"""Profile endpoints for the signed-in user."""

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.schemas.user import OnboardingIn, ProfileUpdateIn, UserOut
from app.services import users as users_service

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/me/onboarding", response_model=UserOut)
def complete_onboarding(data: OnboardingIn, user: CurrentUser, db: DbSession):
    """First-time profile. Name and roll number can't be changed afterwards."""
    return users_service.complete_onboarding(db, user, data)


@router.patch("/me", response_model=UserOut)
def update_profile(data: ProfileUpdateIn, user: CurrentUser, db: DbSession):
    """Change phone, hall or room. Send only the fields you want to change."""
    return users_service.update_profile(db, user, data)
