"""Tables used by the AI agent (see docs/AI.md).

ai_jobs: a queue. Saving a report adds a job; a background loop (in the API
  or the worker) picks jobs up and runs the AI on them. The website never
  waits for the AI: the item page shows "looking for matches..." until the
  job is done.

match_suggestions: the result: for an item, which items of the opposite
  kind might be the same thing, with a score and a reason. Stored in BOTH
  directions, so a lost report and the found item it matches each list
  the other.

agent_runs: the AI audit log. One row per agent run: which model, every
  tool it called (with arguments and a short result), how it ended.

match_notifications: one row per (lost report, found item) the owner was
  emailed about, so the same match is never emailed twice.

owner_alerts (Phase D): an owner identified from the text on a found ID card
  (a roll number or an IITK email address) and whether they were emailed.
  Pending until the item is at a security office, then sent once.
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

JOB_KINDS = ("ITEM_ANALYSE",)
JOB_STATUSES = ("queued", "running", "done", "failed")


def _sql_list(values) -> str:
    return ", ".join(f"'{v}'" for v in values)


class AiJob(Base):
    __tablename__ = "ai_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(10), default="queued")
    attempts: Mapped[int] = mapped_column(default=0)
    last_error: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"kind IN ({_sql_list(JOB_KINDS)})", name="kind"),
        CheckConstraint(f"status IN ({_sql_list(JOB_STATUSES)})", name="status"),
        # At most one WAITING job per item and kind: saving a report three
        # times quickly queues one job, not three.
        Index("uq_ai_jobs_one_queued", "kind", "item_id", unique=True,
              postgresql_where=text("status = 'queued'")),
        Index("ix_ai_jobs_queued", "id", postgresql_where=text("status = 'queued'")),
    )


class MatchSuggestion(Base):
    __tablename__ = "match_suggestions"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The item whose page shows the suggestion...
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    # ...and the suggested item (always the opposite kind).
    candidate_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    score: Mapped[float] = mapped_column(Float)
    reason: Mapped[str | None] = mapped_column(String(300))
    # "basic" = local embeddings + rules. "ai" = ranked by the agent (Phase B).
    method: Mapped[str] = mapped_column(String(10), default="basic")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_match_suggestions_pair", "item_id", "candidate_id", unique=True),
        CheckConstraint("item_id <> candidate_id", name="not_self"),
        CheckConstraint("score >= 0 AND score <= 1", name="score_range"),
    )


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ai_jobs.id", ondelete="SET NULL"))
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    # "ok": the agent finished. "fallback": the LLM was unavailable, so the
    # rule-based matching ran instead. "error": something else went wrong.
    outcome: Mapped[str] = mapped_column(String(10))
    provider: Mapped[str | None] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(80))
    llm_calls: Mapped[int] = mapped_column(default=0)
    # [{"tool": "...", "args": {...}, "result": "short summary"}, ...]
    steps: Mapped[list] = mapped_column(JSONB, default=list)
    summary: Mapped[str | None] = mapped_column(String(1000))
    error: Mapped[str | None] = mapped_column(String(500))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("outcome IN ('ok', 'fallback', 'error')", name="outcome"),
        Index("ix_agent_runs_started_at", "started_at"),
    )


class MatchNotification(Base):
    __tablename__ = "match_notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    lost_item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    found_item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("uq_match_notifications_pair", "lost_item_id", "found_item_id", unique=True),
    )


OWNER_ID_TYPES = ("ROLL", "EMAIL")
OWNER_ALERT_STATUSES = ("pending", "sent")


class OwnerAlert(Base):
    __tablename__ = "owner_alerts"

    id: Mapped[int] = mapped_column(primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    # What was read on the card: a roll number or an email address.
    id_type: Mapped[str] = mapped_column(String(10))
    # Masked, for guards ("roll no. ending 45", "ra•••@iitk.ac.in").
    id_hint: Mapped[str] = mapped_column(String(80))
    # Who gets the email, and their account if they have one.
    target_email: Mapped[str] = mapped_column(String(254))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"id_type IN ({_sql_list(OWNER_ID_TYPES)})", name="id_type"),
        CheckConstraint(f"status IN ({_sql_list(OWNER_ALERT_STATUSES)})", name="status"),
        # One email per person per item, even if the card shows both their
        # roll number and their email address.
        Index("uq_owner_alerts_item_target", "item_id", "target_email", unique=True),
    )
