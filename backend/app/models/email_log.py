"""email_logs: one row per email the portal tried to send.

Who it went to, what it was about, and whether it worked, so "did the owner
get the email?" can be answered (python -m app.cli email-log). The email's
text is NOT stored, and sign-in codes are masked in the subject, so this
table never contains anything that lets someone sign in.
"""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class EmailLog(Base):
    __tablename__ = "email_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    to_email: Mapped[str] = mapped_column(String(254), index=True)
    subject: Mapped[str] = mapped_column(String(300))
    # login_code, id_card, match, notice (reminders, claim updates)
    kind: Mapped[str] = mapped_column(String(20))
    # The item the email is about (FND-0735), if any.
    ref: Mapped[str | None] = mapped_column(String(20), index=True)
    # sent (accepted by the mail server), printed (EMAIL_BACKEND=console), failed
    status: Mapped[str] = mapped_column(String(10))
    error: Mapped[str | None] = mapped_column(String(500))

    __table_args__ = (
        CheckConstraint("status IN ('sent', 'printed', 'failed')", name="status"),
        Index("ix_email_logs_created_at", "created_at"),
    )
