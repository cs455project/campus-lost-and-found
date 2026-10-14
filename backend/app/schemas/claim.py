"""Shapes for claims: what students and staff send, and what they get back."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import ClaimStatus, DetailSource, DetailVerdict
from app.models.claim import MAX_DETAILS
from app.schemas.item import PersonBrief, _tidy
from app.schemas.reference import CategoryOut, OfficeOut


def _strip(value):
    return value.strip() if isinstance(value, str) else value


# ---------------- Incoming ----------------


DETAIL_MAX_LENGTH = 200


def _clean_detail(value):
    value = _strip(value)
    if not isinstance(value, str) or not value:
        raise ValueError("A detail can't be blank.")
    if len(value) > DETAIL_MAX_LENGTH:
        raise ValueError(f"Keep each detail under {DETAIL_MAX_LENGTH} characters.")
    return value


class ClaimIn(BaseModel):
    # At least 1 character; the more detail (what's inside, marks...), the better.
    description: str = Field(min_length=1, max_length=2000)
    # FR-CLM-02: separate things only the owner would know, e.g. "sticker of a
    # cat on the back", "Rs 200 and a library card inside". A guard checks each
    # one against the item. Optional for backward compatibility, but claims
    # without details score 0 for M (see services/verification_score.py).
    details: list[str] = Field(default_factory=list, max_length=MAX_DETAILS)

    @field_validator("description", mode="before")
    @classmethod
    def strip(cls, value):
        return _strip(value)

    @field_validator("details", mode="before")
    @classmethod
    def clean_details(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value  # pydantic reports the type error
        cleaned = [_clean_detail(v) for v in value]
        if len({v.casefold() for v in cleaned}) != len(cleaned):
            raise ValueError("Each detail should be different.")
        return cleaned


class DetailVerdictIn(BaseModel):
    """A guard's check of one detail. null clears a mistaken check."""

    verdict: DetailVerdict | None


class DeskDetailIn(BaseModel):
    """A detail the claimant told a guard in person (office visit), recorded
    with the guard's verdict straight away (FR-CUS-06)."""

    text: str
    verdict: DetailVerdict

    @field_validator("text", mode="before")
    @classmethod
    def clean(cls, value):
        return _clean_detail(value)


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


class ClaimDetailOut(BaseModel):
    id: int
    text: str
    source: DetailSource
    # ---- Staff only (null for the claimant) ----
    verdict: DetailVerdict | None
    checked_by_name: str | None
    checked_at: datetime | None


class ScoreOut(BaseModel):
    """C = 50M + 20R + 30H (staff only). Advisory: it orders the review
    queue and never decides ownership (FR-SCORE-04)."""

    score: float
    m: float
    r: float
    h: float
    matched: int  # details a guard marked MATCH
    checked: int  # details a guard has checked either way
    total: int  # all details on the claim
    weights: dict[str, int]
    scored_at: datetime | None


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
    details: list[ClaimDetailOut]
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
    # Record MATCH / NO_MATCH on details (while under review or after a visit).
    can_check_details: bool
    score: ScoreOut | None


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
    score: float | None  # staff lists only


class ClaimPage(BaseModel):
    items: list[ClaimRowOut]
    total: int
    page: int
    page_size: int
    total_pages: int
