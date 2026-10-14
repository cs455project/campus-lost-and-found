"""Ownership claims: a student says "this found item is mine" and proves it.

A claim moves through these statuses (see services/claims.py for who may
make each move):

  SUBMITTED -> UNDER_REVIEW -> APPROVED -> COLLECTED
                  |    ^           |
                  v    |           +-> EXPIRED   (not collected within 7 days)
            NEEDS_MORE_INFO        +-> REJECTED  (failed the ID check at the desk)
            (visit the office)
  Any open claim -> REJECTED (by staff) or WITHDRAWN (by the claimant)
  Other open claims -> CLOSED when the item is returned to someone else
"""

import enum
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, Float, ForeignKey, Index, String, Text, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.item import Item
from app.models.user import User


class ClaimStatus(enum.StrEnum):
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    NEEDS_MORE_INFO = "NEEDS_MORE_INFO"  # claimant must visit the office
    APPROVED = "APPROVED"  # waiting for the claimant to collect it
    COLLECTED = "COLLECTED"  # handed over: the item is returned
    REJECTED = "REJECTED"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"  # approved but not collected in time
    CLOSED = "CLOSED"  # the item went to someone else


OPEN_CLAIM_STATUSES = [
    ClaimStatus.SUBMITTED,
    ClaimStatus.UNDER_REVIEW,
    ClaimStatus.NEEDS_MORE_INFO,
    ClaimStatus.APPROVED,
]


def _text_enum(name: str) -> Enum:
    return Enum(ClaimStatus, name=name, native_enum=False, create_constraint=True,
                length=16, validate_strings=True)


def _open_list() -> str:
    return ", ".join(f"'{s}'" for s in OPEN_CLAIM_STATUSES)


class Claim(TimestampMixin, Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    item: Mapped[Item] = relationship()
    claimant_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    claimant: Mapped[User] = relationship(foreign_keys=[claimant_id])

    # The claimant's proof, in their own words. Compared with the item's
    # item itself by staff. Never shown to other students.
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[ClaimStatus] = mapped_column(_text_enum("claim_status"), default=ClaimStatus.SUBMITTED)

    # The latest message from staff to the claimant: what to bring to the
    # office, or why the claim was rejected.
    staff_message: Mapped[str | None] = mapped_column(String(500))

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collect_by: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handed_over_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    handed_over_by: Mapped[User | None] = relationship(foreign_keys=[handed_over_by_id])
    # When the claim reached a final status. Proof images are deleted 2 days
    # after this (by the worker).
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Reminders, each sent at most once (see services/jobs.py).
    collect_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- Verification score C = 50M + 20R + 30H (US-22) ----
    # Staff only. Orders the Claims desk; never decides anything.
    # See services/verification_score.py for how each part is worked out.
    score: Mapped[float | None] = mapped_column(Float)
    score_m: Mapped[float | None] = mapped_column(Float)
    score_r: Mapped[float | None] = mapped_column(Float)  # frozen when the claim is sent
    score_h: Mapped[float | None] = mapped_column(Float)  # frozen when the claim is sent
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    events: Mapped[list["ClaimEvent"]] = relationship(
        back_populates="claim", order_by="ClaimEvent.id", cascade="all, delete-orphan", passive_deletes=True
    )
    proofs: Mapped[list["ClaimProof"]] = relationship(
        back_populates="claim", order_by="ClaimProof.id", cascade="all, delete-orphan", passive_deletes=True
    )
    # Identifying details (FR-CLM-02), each checked by a guard against the item.
    details: Mapped[list["ClaimDetail"]] = relationship(
        back_populates="claim", order_by="ClaimDetail.id", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (
        # At most ONE approved claim per item: the database itself refuses a
        # second, even if two guards click "Approve" at the same moment.
        Index("uq_claims_one_approved_per_item", "item_id", unique=True,
              postgresql_where=text("status = 'APPROVED'")),
        # One open claim per person per item (they can claim again after
        # withdrawing or being rejected, if new evidence turns up).
        Index("uq_claims_one_open_per_person", "item_id", "claimant_id", unique=True,
              postgresql_where=text(f"status IN ({_open_list()})")),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 100)", name="score_range"),
        CheckConstraint(
            "(score_m IS NULL OR score_m BETWEEN 0 AND 1) AND (score_r IS NULL OR score_r BETWEEN 0 AND 1) "
            "AND (score_h IS NULL OR score_h BETWEEN 0 AND 1)",
            name="score_factors_range",
        ),
    )

    @property
    def ref(self) -> str:
        return f"CLM-{self.id:04d}"

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_CLAIM_STATUSES


class ClaimEvent(Base):
    """Append-only history of a claim: every status change, who made it, and why."""

    __tablename__ = "claim_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    claim: Mapped[Claim] = relationship(back_populates="events")
    from_status: Mapped[ClaimStatus | None] = mapped_column(_text_enum("claim_event_from"))
    to_status: Mapped[ClaimStatus] = mapped_column(_text_enum("claim_event_to"))
    # NULL actor = the system (e.g. an approval expiring).
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor: Mapped[User | None] = relationship()
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClaimProof(Base):
    """A proof image (e.g. an old photo with the item, a receipt). Private:
    only the claimant and staff can open it."""

    __tablename__ = "claim_proofs"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    claim: Mapped[Claim] = relationship(back_populates="proofs")
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(30))
    size_bytes: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DetailVerdict(enum.StrEnum):
    MATCH = "MATCH"  # the guard checked the item: this detail is true
    NO_MATCH = "NO_MATCH"  # the guard checked the item: this detail is wrong


class DetailSource(enum.StrEnum):
    CLAIMANT = "CLAIMANT"  # written by the claimant on the claim form
    DESK = "DESK"  # told to a guard in person and recorded at the office


MAX_DETAILS = 5
CHECKABLE_STATUSES = [ClaimStatus.UNDER_REVIEW, ClaimStatus.NEEDS_MORE_INFO]


class ClaimDetail(Base):
    """One identifying detail the claimant remembers about the item, e.g.
    "name written inside the front pocket" (FR-CLM-02).

    Private (FR-CLM-03, FR-CLM-11): only the claimant and staff can read the
    text, and only staff see the verdict. A guard compares each detail with
    the item they are holding and records MATCH or NO_MATCH. The fraction of
    matches is M in the verification score (services/verification_score.py).
    """

    __tablename__ = "claim_details"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), index=True)
    claim: Mapped[Claim] = relationship(back_populates="details")
    source: Mapped[DetailSource] = mapped_column(
        Enum(DetailSource, name="claim_detail_source", native_enum=False, create_constraint=True,
             length=8, validate_strings=True),
        default=DetailSource.CLAIMANT,
    )
    text: Mapped[str] = mapped_column(String(200))
    # NULL until a guard has checked it.
    verdict: Mapped[DetailVerdict | None] = mapped_column(
        Enum(DetailVerdict, name="claim_detail_verdict", native_enum=False, create_constraint=True,
             length=8, validate_strings=True)
    )
    checked_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    checked_by: Mapped[User | None] = relationship()
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
