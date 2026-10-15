"""Profile rules: onboarding, and what a user may change afterwards.

Name and roll number are set once, during onboarding, and then locked.
Guards compare them with the student ID card when handing over an item,
so a student must not be able to change them to match someone else's
claim. Phone, hall and room stay editable.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict
from app.models import User
from app.schemas.user import OnboardingIn, ProfileUpdateIn


def complete_onboarding(db: Session, user: User, data: OnboardingIn) -> User:
    if user.is_onboarded:
        raise bad_request("Your profile is already complete.")

    # Students must give a roll number; guards and the admin don't have one.
    if not user.is_staff and not data.roll_number:
        raise bad_request("Please enter your roll number.")

    if data.roll_number:
        taken = db.scalar(
            select(User.id).where(User.roll_number == data.roll_number, User.id != user.id)
        )
        if taken is not None:
            raise conflict("This roll number is already registered to another account.")
        user.roll_number = data.roll_number

    # A name that's already set stays locked. This happens when a guard is
    # removed and becomes a student: they only need to add a roll number.
    if not user.full_name:
        user.full_name = data.full_name
    user.phone = data.phone
    user.hall = data.hall
    user.room = data.room
    db.commit()
    return user


def update_profile(db: Session, user: User, data: ProfileUpdateIn) -> User:
    # exclude_unset: only change the fields actually sent in the request,
    # so sending just {"phone": "..."} doesn't wipe the hall and room.
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    db.commit()
    return user
