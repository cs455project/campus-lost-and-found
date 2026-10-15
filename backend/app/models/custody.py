"""Custody history: every time a found item changes hands or place.

Rows are only ever added, never changed or deleted (an "append-only log").
So the history can always answer: where was this item, who had it, and when?
"""

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.office import Office
from app.models.user import User


class CustodyEventType(enum.StrEnum):
    RECEIVED = "RECEIVED"  # a guard took in an item the finder reported online
    REGISTERED = "REGISTERED"  # a guard registered a walk-in item (no online report)
    TRANSFERRED = "TRANSFERRED"  # moved from one office to another
    RETURNED = "RETURNED"  # handed back to its owner (Phase 5)
    DISPOSED = "DISPOSED"  # unclaimed, thrown away
    DONATED = "DONATED"  # unclaimed, given away


class CustodyEvent(Base):
    __tablename__ = "custody_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    item: Mapped["Item"] = relationship(back_populates="custody_events")  # noqa: F821
    event_type: Mapped[CustodyEventType] = mapped_column(
        Enum(
            CustodyEventType,
            name="custody_event_type",
            native_enum=False,
            create_constraint=True,
            length=12,
            validate_strings=True,
        )
    )
    # Where the item was before and after. A receipt has only "to";
    # disposal has only "from"; a transfer has both.
    from_office_id: Mapped[int | None] = mapped_column(ForeignKey("offices.id"))
    from_office: Mapped[Office | None] = relationship(foreign_keys=[from_office_id])
    to_office_id: Mapped[int | None] = mapped_column(ForeignKey("offices.id"))
    to_office: Mapped[Office | None] = relationship(foreign_keys=[to_office_id])
    # The staff member who did it. SET NULL keeps the history if their
    # account is ever deleted.
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor: Mapped[User | None] = relationship()
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
