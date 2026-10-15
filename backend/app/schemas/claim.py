"""Shapes for claims: what students and staff send, and what they get back."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ClaimStatus
from app.schemas.item import PersonBrief, _tidy
from app.schemas.reference import CategoryOut, OfficeOut


def _strip(value):
    return value.strip() if isinstance(value, str) else value


# ---------------- Incoming ----------------


class ClaimIn(BaseModel):
    # At least 1 character; the more detail (what's inside, marks...), the better.
    description: str = Field(min_length=1, max_length=2000)

    @field_validator("description", mode="before")
    @classmethod
    def strip(cls, value):
        return _strip(value)


class OptionalNoteIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def tidy(cls, value):
        return _tidy(value)


class RequiredNoteIn(BaseModel):
    """For decisions the claimant must understand: a rejection reason, what
    to bring to the office, what they explained in person."""

    note: str = Field(min_length=1, max_length=500)

    @field_validator("note", mode="before")
    @classmethod
    def strip(cls, value):
        return _strip(value)


class HandOverIn(OptionalNoteIn):
    # The guard must tick "I checked the student ID card". Literal[True]
    # means the API refuses anything else, including false or a missing value.
    id_checked: Literal[True]


# ---------------- Outgoing ----------------


class ClaimItemBrief(BaseModel):
    """The item being claimed, as the claim page shows it."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    ref: str
    title: str
    status: str
    category: CategoryOut
    cover_photo_id: int | None
    current_office: OfficeOut | None


class ClaimEventOut(BaseModel):
    id: int
    from_status: ClaimStatus | None
    to_status: ClaimStatus
    actor_name: str | None
    note: str | None
    created_at: datetime


class ClaimantOut(PersonBrief):
    phone: str | None


class OtherClaimOut(BaseModel):
    id: int
    ref: str
    status: ClaimStatus
    claimant_name: str | None
    created_at: datetime


class ClaimOut(BaseModel):
    id: int
    ref: str
    status: ClaimStatus
    description: str
    item: ClaimItemBrief
    proof_ids: list[int]
    staff_message: str | None
    created_at: datetime
    approved_at: datetime | None
    collect_by: datetime | None
    collected_at: datetime | None
    events: list[ClaimEventOut]
    is_mine: bool
    # Claimant actions
    can_withdraw: bool
    can_add_proof: bool
    # Another claim on the same item is approved, so this one waits.
    on_hold: bool
    # ---- Staff only (empty / false for the claimant) ----
    claimant: ClaimantOut | None
    other_claims: list[OtherClaimOut]
    can_start_review: bool
    can_request_info: bool
    can_resume_review: bool
    can_approve: bool
    can_reject: bool
    can_hand_over: bool


class ClaimRowOut(BaseModel):
    """One line in a list of claims."""

    id: int
    ref: str
    status: ClaimStatus
    item: ClaimItemBrief
    claimant_name: str | None
    claimant_roll: str | None
    created_at: datetime
    updated_at: datetime
    collect_by: datetime | None
    on_hold: bool


class ClaimPage(BaseModel):
    items: list[ClaimRowOut]
    total: int
    page: int
    page_size: int
    total_pages: int
