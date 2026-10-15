"""Shared building blocks for every database table."""

from datetime import datetime

from sqlalchemy import DateTime, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Give every constraint and index a predictable name, e.g. "uq_users_email".
# Without this, PostgreSQL invents random names, and later Alembic
# migrations can't reliably find them to change or drop them.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Every model inherits from this. Alembic reads Base.metadata to know
    which tables should exist."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    """Adds created_at and updated_at columns to any model that includes it.

    The times come from the database server's clock (func.now()), not from
    the user's device, so they can't be faked and are always consistent.
    timezone=True stores the time zone too, so times are unambiguous.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
