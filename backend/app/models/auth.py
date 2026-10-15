"""Tables for signing in: one-time login codes and signed-in sessions."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base
from app.models.user import User


class LoginCode(Base):
    """A 6-digit code emailed to someone who wants to sign in.

    Rows are kept (not deleted) after use, so we can count how many codes
    an address requested recently and limit spamming.
    """

    __tablename__ = "login_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Not a foreign key to users: the person may not have an account yet.
    # Their account is created only after they enter the right code.
    email: Mapped[str] = mapped_column(String(254), index=True)
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # Set when the code is used, or replaced by a newer code.
    # A code with consumed_at filled in can never be used again.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserSession(Base):
    """One signed-in browser. The browser's cookie holds a random token;
    this table holds only its hash.

    Why store sessions in the database (instead of a self-contained token
    like JWT)? Signing out, or an admin deactivating someone, takes effect
    immediately: delete the row and that cookie stops working.
    """

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # CASCADE: deleting a user deletes their sessions too.
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    user: Mapped[User] = relationship()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
