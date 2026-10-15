"""Users: students, guards and the admin all live in one table,
told apart by their role."""

import enum

from sqlalchemy import Boolean, CheckConstraint, Enum, ForeignKey, String, true
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin
from app.models.office import Office


class UserRole(enum.StrEnum):
    STUDENT = "STUDENT"
    GUARD = "GUARD"  # manages items and claims at every office
    ADMIN = "ADMIN"  # everything a guard can do, plus assigning/removing guards


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Always stored in lowercase, so "Riya@IITK.ac.in" and "riya@iitk.ac.in"
    # are the same account. unique=True also creates an index for fast login lookups.
    email: Mapped[str] = mapped_column(String(254), unique=True)

    # Filled in during onboarding, after the first sign-in.
    full_name: Mapped[str | None] = mapped_column(String(120))
    # Guards match this against the student ID card at collection.
    roll_number: Mapped[str | None] = mapped_column(String(20), unique=True)
    phone: Mapped[str | None] = mapped_column(String(20))
    hall: Mapped[str | None] = mapped_column(String(60))
    room: Mapped[str | None] = mapped_column(String(20))

    # native_enum=False + create_constraint=True stores the role as plain text
    # with a CHECK constraint allowing only these three values. That's easier
    # to change in a later migration than a PostgreSQL ENUM type.
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            native_enum=False,
            create_constraint=True,
            length=10,
            validate_strings=True,
        ),
        default=UserRole.STUDENT,
        server_default=UserRole.STUDENT.value,
    )

    # Where a guard usually works. Informational only: guards can act at
    # every office. SET NULL: deleting an office doesn't delete its guards.
    office_id: Mapped[int | None] = mapped_column(
        ForeignKey("offices.id", ondelete="SET NULL"), index=True
    )
    office: Mapped[Office | None] = relationship()

    # Deactivate instead of deleting, so their reports and history stay intact.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())

    __table_args__ = (
        # The database itself rejects mixed-case emails, as a safety net in
        # case some code forgets to lowercase before saving.
        CheckConstraint("email = lower(email)", name="email_lowercase"),
    )

    @property
    def is_staff(self) -> bool:
        """Guards and the admin share every item and claim power."""
        return self.role in (UserRole.GUARD, UserRole.ADMIN)

    @property
    def is_onboarded(self) -> bool:
        """A user must complete onboarding before reporting or claiming
        items. Everyone needs a name; students also need a roll number,
        which guards check against the ID card. Staff have no roll number."""
        if not self.full_name:
            return False
        return self.is_staff or bool(self.roll_number)

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email} {self.role}>"
