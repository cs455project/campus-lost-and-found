"""The ai_jobs queue.

Why a queue? AI work (embedding, later calling an LLM) can take seconds.
The website must never wait for it, so saving a report only ADDS a job
(instant), and a background loop runs it shortly after. The item page
shows "looking for matches..." meanwhile.

Safe with several runners at once (the API's background loop and the
worker): a runner takes a job with FOR UPDATE SKIP LOCKED, so two runners
never take the same job, and marks it "running" before doing the work.
"""

import logging
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import now
from app.models import AiJob

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)  # a "running" job older than this was interrupted

# kind -> function(db, job) doing the work. Filled in by semantic.py etc.
HANDLERS: dict[str, Callable[[Session, AiJob], None]] = {}


def enqueue(db: Session, kind: str, item_id: int) -> None:
    """Add a job unless the same one is already waiting (the partial unique
    index makes the database skip duplicates). The caller commits."""
    db.execute(
        insert(AiJob)
        .values(kind=kind, item_id=item_id, status="queued", attempts=0)
        .on_conflict_do_nothing(index_elements=["kind", "item_id"], index_where=text("status = 'queued'"))
    )


def _requeue_stale(db: Session) -> None:
    db.execute(
        update(AiJob)
        .where(AiJob.status == "running", AiJob.created_at < now() - STALE_AFTER)
        .values(status="failed", last_error="Interrupted (runner stopped while working).", finished_at=now())
    )
    db.commit()


def run_next(db: Session) -> bool:
    """Run one waiting job. Returns False when the queue is empty."""
    job = db.scalar(
        select(AiJob).where(AiJob.status == "queued").order_by(AiJob.id).limit(1)
        .with_for_update(skip_locked=True)
    )
    if job is None:
        db.rollback()
        return False
    job.status = "running"
    job.attempts += 1
    db.commit()  # other runners now skip it

    handler = HANDLERS.get(job.kind)
    try:
        if handler is None:
            raise RuntimeError(f"No handler for job kind {job.kind}")
        handler(db, job)
        job.status = "done"
        job.last_error = None
    except Exception as error:  # one bad job must not stop the queue
        db.rollback()
        log.exception("AI job %s (%s) failed", job.id, job.kind)
        job = db.get(AiJob, job.id)
        job.last_error = f"{type(error).__name__}: {error}"[:500]
        job.status = "failed"
        on_failure = FAILURE_HANDLERS.get(job.kind)
        if on_failure:
            on_failure(db, job)
    job.finished_at = now()
    db.commit()
    return True


# kind -> function(db, job) run when a job fails for good.
FAILURE_HANDLERS: dict[str, Callable[[Session, AiJob], None]] = {}


def run_all(db: Session, limit: int = 50) -> int:
    """Run waiting jobs until the queue is empty (or `limit` jobs ran)."""
    _requeue_stale(db)
    done = 0
    while done < limit and run_next(db):
        done += 1
    return done
