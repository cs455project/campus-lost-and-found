"""Lost and found items, and their photos.

One table holds both kinds. `kind` says which it is:
  LOST:  someone reports something they've lost
  FOUND: someone reports something they found and is handing in

Each kind has its own allowed statuses (checked by the database itself):

  FOUND: AWAITING_DROPOFF -> IN_CUSTODY -> CLAIM_PENDING -> RETURNED
                                      \\-> DISPOSED / DONATED (unclaimed)
  LOST:  OPEN -> RECOVERED (got it back) or CLOSED (gave up)
"""

import enum
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, REAL, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.category import Category
from app.models.office import Office
from app.models.user import User


class ItemKind(enum.StrEnum):
    LOST = "LOST"
    FOUND = "FOUND"


class ItemStatus(enum.StrEnum):
    # Found items
    AWAITING_DROPOFF = "AWAITING_DROPOFF"  # reported, not yet handed to a guard
    IN_CUSTODY = "IN_CUSTODY"  # held at an office, shown on the Found page
    CLAIM_PENDING = "CLAIM_PENDING"  # someone has claimed it (Phase 5)
    RETURNED = "RETURNED"  # handed back to its owner
    DISPOSED = "DISPOSED"  # unclaimed, thrown away
    DONATED = "DONATED"  # unclaimed, given away
    # Lost items
    OPEN = "OPEN"  # still missing, shown on the Lost page
    RECOVERED = "RECOVERED"  # owner got it back
    CLOSED = "CLOSED"  # owner stopped looking


FOUND_STATUSES = [
    ItemStatus.AWAITING_DROPOFF,
    ItemStatus.IN_CUSTODY,
    ItemStatus.CLAIM_PENDING,
    ItemStatus.RETURNED,
    ItemStatus.DISPOSED,
    ItemStatus.DONATED,
]
LOST_STATUSES = [ItemStatus.OPEN, ItemStatus.RECOVERED, ItemStatus.CLOSED]

# Statuses anyone signed in may see on the public Found and Lost pages.
PUBLIC_STATUSES = {
    ItemKind.FOUND: [ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING],
    ItemKind.LOST: [ItemStatus.OPEN],
}
# Found-item statuses where the item is physically at an office.
HELD_STATUSES = [ItemStatus.IN_CUSTODY, ItemStatus.CLAIM_PENDING]


def _sql_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _text_enum(enum_class, name: str, length: int) -> Enum:
    """Store an enum as plain text, with a CHECK constraint on its values."""
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=length,
        validate_strings=True,
    )


class Item(TimestampMixin, Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[ItemKind] = mapped_column(_text_enum(ItemKind, "item_kind", 5))
    status: Mapped[ItemStatus] = mapped_column(_text_enum(ItemStatus, "item_status", 20))

    # ---- What it is ----
    title: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"))
    category: Mapped[Category] = relationship()
    colour: Mapped[str | None] = mapped_column(String(40))
    brand: Mapped[str | None] = mapped_column(String(60))

    # ---- Where and when it was lost or found ----
    location_text: Mapped[str] = mapped_column(String(200))
    happened_on: Mapped[date] = mapped_column(Date)

    # ---- Who reported it ----
    reporter_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    reporter: Mapped[User] = relationship(foreign_keys=[reporter_id])

    # ---- Found items only ----
    # The office the finder said they'd take it to.
    dropoff_office_id: Mapped[int | None] = mapped_column(ForeignKey("offices.id"))
    dropoff_office: Mapped[Office | None] = relationship(foreign_keys=[dropoff_office_id])
    # Where it is right now (set when a guard receives it; changes on transfer).
    current_office_id: Mapped[int | None] = mapped_column(ForeignKey("offices.id"), index=True)
    current_office: Mapped[Office | None] = relationship(foreign_keys=[current_office_id])
    received_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    received_by: Mapped[User | None] = relationship(foreign_keys=[received_by_id])
    # When it was handed back to its owner. Returned records are deleted
    # 60 days after this moment (by the worker, app/worker.py).
    returned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set when the finder pressed "I found this" on a lost report: which
    # report it answers. The details were copied from that report, so the
    # finder can't edit them (services/items.py, can_edit).
    answers_lost_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="SET NULL"))
    answers_lost: Mapped["Item | None"] = relationship(remote_side="Item.id", foreign_keys=[answers_lost_id])
    # When the "please hand it in" reminder was emailed to the finder.
    # Set once, so the reminder is never sent twice.
    dropoff_reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # ---- AI (see docs/AI.md) ----
    # Where the AI is with this item: "pending" (queued), "done", "failed".
    # NULL = never analysed (e.g. created before the AI existed).
    ai_status: Mapped[str | None] = mapped_column(String(10))
    ai_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The item's text turned into numbers (an "embedding"), so similar items
    # can be found by meaning, not just exact words. deferred: not loaded
    # with the item, only when matching needs it.
    embedding: Mapped[list[float] | None] = mapped_column(ARRAY(REAL), deferred=True)
    # Which model made the embedding: embeddings from different models
    # can't be compared, so we only compare items with the same model.
    embedding_model: Mapped[str | None] = mapped_column(String(80))
    # Phase C: what the AI saw in the photos (shown as "AI description").
    ai_description: Mapped[str | None] = mapped_column(Text)
    # Structured version: {"object", "colour", "brand", "marks": [...]}, or
    # {"personal_info": true} when the photo showed personal information.
    ai_attributes: Mapped[dict | None] = mapped_column(JSONB)
    # Which photos were described (their ids), so the same photos are never
    # sent to the AI twice.
    ai_photos_key: Mapped[str | None] = mapped_column(String(200))
    # Phase D: which photos were read for an owner's roll number or email
    # (local OCR), so the same photos are never read twice.
    ocr_photos_key: Mapped[str | None] = mapped_column(String(200))

    # ---- Search ----
    # PostgreSQL keeps this column up to date by itself ("generated column"):
    # a list of the words in the item's text, with endings removed
    # ("wallets" -> "wallet"), so searches match word forms.
    # deferred=True: don't load it with the item; it's only used for searching.
    search_vector: Mapped[str] = mapped_column(
        TSVECTOR,
        Computed(
            "to_tsvector('english', "
            "coalesce(title, '') || ' ' || coalesce(description, '') || ' ' || "
            "coalesce(brand, '') || ' ' || coalesce(colour, '') || ' ' || "
            "coalesce(location_text, ''))",
            persisted=True,
        ),
        deferred=True,
    )

    # Oldest first. The model lives in custody.py; the string name avoids a
    # circular import between the two files.
    custody_events: Mapped[list["CustodyEvent"]] = relationship(  # noqa: F821
        back_populates="item", order_by="CustodyEvent.id",
        cascade="all, delete-orphan", passive_deletes=True,
    )

    photos: Mapped[list["ItemPhoto"]] = relationship(
        back_populates="item", order_by="ItemPhoto.id", cascade="all, delete-orphan"
    )

    __table_args__ = (
        # Each kind may only use its own statuses.
        CheckConstraint(
            f"(kind = 'FOUND' AND status IN ({_sql_list(FOUND_STATUSES)})) OR "
            f"(kind = 'LOST' AND status IN ({_sql_list(LOST_STATUSES)}))",
            name="status_matches_kind",
        ),
        # Found items must name a drop-off office; lost items never have one.
        CheckConstraint(
            "(kind = 'FOUND') = (dropoff_office_id IS NOT NULL)",
            name="dropoff_office_only_for_found",
        ),
        # An item held at an office must say which office.
        CheckConstraint(
            f"status NOT IN ({_sql_list(HELD_STATUSES)}) OR current_office_id IS NOT NULL",
            name="held_items_have_office",
        ),
        # ---- Indexes for the Found and Lost pages ----
        # Main listing: "found items, at an office, newest first".
        # Column order matters: equality filters first (kind, status), then
        # the sort order, so PostgreSQL reads rows already in page order.
        Index("ix_items_kind_status_created", "kind", "status", "created_at", "id"),
        # The category filter: "found items in Electronics, newest first".
        Index("ix_items_kind_category_created", "kind", "category_id", "created_at"),
        # GIN index: the right index type for "which rows contain these words".
        Index("ix_items_search_vector", "search_vector", postgresql_using="gin"),
        # Returned Items page (newest return first) and the 60-day purge.
        # Partial: only returned items are in this index, so it stays small.
        Index("ix_items_returned_at", "returned_at", postgresql_where=text("status = 'RETURNED'")),
    )

    @property
    def ref(self) -> str:
        """Short reference people can quote, e.g. FND-0042 or LST-0007."""
        prefix = "FND" if self.kind == ItemKind.FOUND else "LST"
        return f"{prefix}-{self.id:04d}"

    @property
    def cover_photo_id(self) -> int | None:
        # ID cards and documents never get a thumbnail in lists: the photo
        # shows someone's name, roll number and face (see photos_visible).
        if self.has_private_photos:
            return None
        return self.photos[0].id if self.photos else None

    @property
    def has_private_photos(self) -> bool:
        """True for categories whose photos show personal data (ID cards,
        documents): only the reporter and security staff may see them."""
        from app.core.config import get_settings

        slugs = {s.strip() for s in get_settings().id_card_categories.split(",") if s.strip()}
        return bool(self.category and self.category.slug in slugs)

    def __repr__(self) -> str:
        return f"<Item {self.ref} {self.status}>"


class ItemPhoto(Base):
    """A photo of an item. The image file is stored on disk (later in cloud
    storage); this row records where, plus its type and size."""

    __tablename__ = "item_photos"

    id: Mapped[int] = mapped_column(primary_key=True)
    # CASCADE: deleting an item deletes its photo rows.
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    item: Mapped[Item] = relationship(back_populates="photos")
    # A random file name we choose (never the uploader's own file name).
    stored_name: Mapped[str] = mapped_column(String(80), unique=True)
    content_type: Mapped[str] = mapped_column(String(30))
    size_bytes: Mapped[int]
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
