"""Security (SIS) offices: one main office plus a few sub-offices,
where students drop off found items and collect claimed ones."""

from sqlalchemy import Boolean, Index, String, false, true
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


class Office(TimestampMixin, Base):
    __tablename__ = "offices"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    # Directions students can follow, e.g. "Ground floor, near the library entrance".
    location_description: Mapped[str] = mapped_column(String(255))
    opening_hours: Mapped[str] = mapped_column(String(100))
    contact_phone: Mapped[str | None] = mapped_column(String(20))
    is_main: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Closing an office hides it from new drop-offs but keeps its history.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    __table_args__ = (
        # A "partial unique index": uniqueness is checked only on rows where
        # is_main is true. So any number of sub-offices, but the database
        # itself refuses a second main office.
        Index(
            "uq_offices_one_main",
            "is_main",
            unique=True,
            postgresql_where=(is_main == true()),
        ),
    )

    def __repr__(self) -> str:
        return f"<Office {self.id} {self.name!r}>"
