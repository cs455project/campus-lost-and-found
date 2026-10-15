"""Shapes for managing staff (guards and admins)."""

from typing import Literal

from pydantic import BaseModel, EmailStr

StaffRole = Literal["GUARD", "ADMIN"]


class StaffAddIn(BaseModel):
    email: EmailStr
    role: StaffRole = "GUARD"
    office_id: int | None = None  # where they usually work (optional)


class StaffUpdateIn(BaseModel):
    """Send only what changes: {"role": "ADMIN"}, {"office_id": 3}, or both.
    {"office_id": null} clears the usual office."""

    role: StaffRole | None = None
    office_id: int | None = None
