import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import UserRole

PHONE_PATTERN = re.compile(r"^(\+91)?[6-9]\d{9}$")  # Indian mobile numbers
ROLL_PATTERN = re.compile(r"^[A-Z0-9]{4,15}$")


def _blank_to_none(value):
    """Treat "" or "   " from an empty form field as "not given"."""
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


class OfficeBrief(BaseModel):
    # from_attributes: build this from a database object (office.id, office.name).
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class UserOut(BaseModel):
    """What the API returns about a user. Fields not listed here are
    never sent, even if the database row has them."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None
    roll_number: str | None
    phone: str | None
    hall: str | None
    room: str | None
    role: UserRole
    office: OfficeBrief | None
    is_onboarded: bool


class _EditableFields(BaseModel):
    """Fields a user can set during onboarding and change later."""

    phone: str | None = None
    hall: str | None = Field(default=None, max_length=60)
    room: str | None = Field(default=None, max_length=20)

    @field_validator("phone", "hall", "room", mode="before")
    @classmethod
    def blank_to_none(cls, value):
        return _blank_to_none(value)

    @field_validator("phone")
    @classmethod
    def check_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        digits = re.sub(r"[\s-]", "", value)  # allow "98765 43210" or "98765-43210"
        if not PHONE_PATTERN.match(digits):
            raise ValueError("Enter a valid 10-digit Indian mobile number.")
        return "+91" + digits[-10:]  # store every number in one format


def _require_phone(value: str | None) -> str:
    if value is None:
        raise ValueError("Enter your mobile number.")
    return value


class OnboardingIn(_EditableFields):
    full_name: str = Field(min_length=1, max_length=120)
    # Required for students; the service checks that, since staff have none.
    roll_number: str | None = None
    # Required for everyone (students, guards, admins): security staff call
    # the owner, and students need a way to reach the desk. No default, so
    # leaving it out is an error too.
    phone: str | None

    @field_validator("phone")
    @classmethod
    def phone_is_required(cls, value: str | None) -> str:
        return _require_phone(value)

    @field_validator("full_name", mode="before")
    @classmethod
    def tidy_name(cls, value):
        if isinstance(value, str):
            return " ".join(value.split())  # trim and collapse extra spaces
        return value

    @field_validator("roll_number", mode="before")
    @classmethod
    def tidy_roll(cls, value):
        value = _blank_to_none(value)
        return value.upper() if isinstance(value, str) else value

    @field_validator("roll_number")
    @classmethod
    def check_roll(cls, value: str | None) -> str | None:
        if value is not None and not ROLL_PATTERN.match(value):
            raise ValueError("Roll number should be 4-15 letters or digits.")
        return value


class ProfileUpdateIn(_EditableFields):
    """Only phone, hall and room. Name and roll number are deliberately
    missing: after onboarding they're locked."""

    # forbid: sending {"full_name": ...} here is an error, not silently ignored.
    model_config = ConfigDict(extra="forbid")

    @field_validator("phone")
    @classmethod
    def phone_cannot_be_removed(cls, value: str | None) -> str:
        # Only checked when "phone" is sent: it may be changed, not emptied.
        return _require_phone(value)
