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

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.clock import now
from app.models import AiJob

log = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=10)  # a job "running" longer than this was interrupted
MAX_ATTEMPTS = 3  # an interrupted job is run again, up to this many tries in all

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


def _requeue_stale(db: Session) -> int:
    """Jobs left "running" by a runner that was killed (a crash, a restart,
    a deploy) are queued again, so the report still gets analysed. After
    MAX_ATTEMPTS tries the job fails for good, and the item says so.

    (Before the failure tests, an interrupted job was simply marked failed:
    the report never got its matches and its page kept saying "looking for
    matches". It was also judged by the time it was QUEUED, not started, so
    a job that had waited long in the queue could be taken for interrupted
    the moment it began. Failure test FR-06.)"""
    stale = list(db.scalars(
        select(AiJob).where(AiJob.status == "running",
                            func.coalesce(AiJob.started_at, AiJob.created_at) < now() - STALE_AFTER)
        .with_for_update(skip_locked=True)
    ))
    for job in stale:
        waiting = db.scalar(select(AiJob.id).where(AiJob.kind == job.kind, AiJob.item_id == job.item_id,
                                                   AiJob.status == "queued"))
        if waiting is not None:
            # A newer request for the same item is already queued: it will do the work.
            job.status, job.finished_at = "failed", now()
            job.last_error = "Interrupted; a newer job for this item is queued."
        elif job.attempts < MAX_ATTEMPTS:
            job.status, job.started_at = "queued", None
            job.last_error = f"Interrupted (runner stopped while working); queued again after try {job.attempts}."
            log.warning("AI job %s was interrupted; queued again (try %s of %s)", job.id, job.attempts + 1,
                        MAX_ATTEMPTS)
        else:
            job.status, job.finished_at = "failed", now()
            job.last_error = f"Interrupted {job.attempts} times; given up."
            on_failure = FAILURE_HANDLERS.get(job.kind)
            if on_failure:
                on_failure(db, job)
    db.commit()
    return len(stale)


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
    job.started_at = now()
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
