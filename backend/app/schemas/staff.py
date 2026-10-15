"""Shapes for the staff desk: receiving, walk-ins, transfers, disposal, history."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.item import ItemCardOut, PersonBrief, _ItemIn, _tidy
from app.schemas.user import OfficeBrief


class _TidyNotes(BaseModel):
    @field_validator("condition_note", "handed_in_by", "note", mode="before", check_fields=False)
    @classmethod
    def tidy(cls, value):
        return _tidy(value)


class ReceiveIn(_TidyNotes):
    # The office where the guard is taking it in. Usually the one the finder
    # chose, but finders sometimes go to a different office.
    office_id: int
    condition_note: str | None = Field(default=None, max_length=300)


class WalkInIn(_ItemIn, _TidyNotes):
    """A guard registering an item someone handed in without an online report."""

    office_id: int
    handed_in_by: str | None = Field(default=None, max_length=120)


class TransferIn(_TidyNotes):
    to_office_id: int
    note: str | None = Field(default=None, max_length=300)


class DisposeIn(_TidyNotes):
    outcome: Literal["DISPOSED", "DONATED"]
    # Required: disposing of something is final, so say why (and where it went).
    note: str = Field(min_length=1, max_length=300)


class StaffItemOut(ItemCardOut):
    """A row on the staff desk: the public card plus what staff need."""

    dropoff_office: OfficeBrief | None
    reporter: PersonBrief
    received_at: datetime | None
    updated_at: datetime


class StaffItemPage(BaseModel):
    items: list[StaffItemOut]
    total: int
    page: int
    page_size: int
    total_pages: int
