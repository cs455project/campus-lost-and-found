"""Timed jobs: things that must happen N hours or days after something else.

Nobody clicks a button for these; the worker (app/worker.py) runs
run_all() every few minutes, and each job does whatever has become due.

  Job                        Due when
  ------------------------   -------------------------------------------------
  send_dropoff_reminders     a found report is 24 h old and not handed in
  send_collect_reminders     a claim was approved 3 days ago, not collected
  send_review_reminders      a claim has waited 3 days for a guard's review
  expire_claims              an approval passed its 7-day collection deadline
  delete_old_proofs          a claim closed 2 days ago: its proof images go
  purge_returned             an item was returned 60 days ago: it goes completely

Two ideas make this safe:

1. Every job takes `at`, the time to treat as "now". The worker passes the
   real time; tests pass a time days in the future (a "fake clock"), so
   "after 60 days" can be tested in a second.

2. Each reminder is sent AT MOST ONCE. We first record "sent at" in the
   database and commit, and only then send the email. If two workers run
   at the same time, FOR UPDATE SKIP LOCKED makes the second one skip rows
   the first is already handling. (The trade-off: if the email server fails
   after the commit, that one reminder is lost rather than sent twice.)
"""

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.clock import local
from app.models import (
    Claim,
    ClaimProof,
    ClaimStatus,
    Item,
    ItemKind,
    ItemPhoto,
    ItemStatus,
    User,
    UserRole,
)
from app.services import claims as claims_service
from app.services import email as email_service
from app.services import matching, storage
from app.services.returned import KEEP_DAYS

log = logging.getLogger(__name__)

DROPOFF_REMINDER_AFTER = timedelta(hours=24)
COLLECT_REMINDER_AFTER = timedelta(days=3)
REVIEW_REMINDER_AFTER = timedelta(days=3)
DELETE_PROOFS_AFTER = timedelta(days=2)
PURGE_RETURNED_AFTER = timedelta(days=KEEP_DAYS)
BATCH = 200  # at most this many rows per job per run, so one run never takes too long


def _when(moment: datetime) -> str:
    return local(moment).strftime("%d %b %Y, %I:%M %p")


# ---------------- Reminders ----------------


def send_dropoff_reminders(db: Session, at: datetime) -> int:
    items = list(db.scalars(
        select(Item).where(
            Item.kind == ItemKind.FOUND,
            Item.status == ItemStatus.AWAITING_DROPOFF,
            Item.created_at <= at - DROPOFF_REMINDER_AFTER,
            Item.dropoff_reminder_sent_at.is_(None),
        ).limit(BATCH).with_for_update(skip_locked=True)
    ))
    for item in items:
        item.dropoff_reminder_sent_at = at
    db.commit()  # recorded BEFORE emailing: never sent twice

    for item in items:
        office = item.dropoff_office
        email_service.send_email(
            item.reporter.email,
            "Reminder: please hand in the item you found",
            f"You reported finding \"{item.title}\" ({item.ref}), but it hasn't been handed in yet.\n\n"
            f"Please take it to {office.name} ({office.location_description}; open {office.opening_hours}).\n"
            "Until a guard receives it, the owner can't see it on the Found page.",
        )
    return len(items)


def send_collect_reminders(db: Session, at: datetime) -> int:
    claims = list(db.scalars(
        select(Claim).where(
            Claim.status == ClaimStatus.APPROVED,
            Claim.approved_at <= at - COLLECT_REMINDER_AFTER,
            Claim.collect_reminder_sent_at.is_(None),
        ).limit(BATCH).with_for_update(skip_locked=True)
    ))
    for claim in claims:
        claim.collect_reminder_sent_at = at
    db.commit()

    for claim in claims:
        office = claim.item.current_office
        email_service.send_email(
            claim.claimant.email,
            "Reminder: collect your item",
            f"Your claim {claim.ref} for \"{claim.item.title}\" was approved, but the item hasn't been "
            f"collected yet.\n\nCollect it from {office.name} ({office.location_description}; open "
            f"{office.opening_hours}) by {_when(claim.collect_by)}, with your student ID card.\n"
            "After that the approval expires.",
        )
    return len(claims)


def send_review_reminders(db: Session, at: datetime) -> int:
    """One summary email to every guard and the admin, listing the claims
    that have waited 3 days for review (each claim is listed only once)."""
    claims = list(db.scalars(
        select(Claim).where(
            Claim.status.in_([ClaimStatus.SUBMITTED, ClaimStatus.UNDER_REVIEW]),
            Claim.created_at <= at - REVIEW_REMINDER_AFTER,
            Claim.review_reminder_sent_at.is_(None),
        ).order_by(Claim.id).limit(BATCH).with_for_update(skip_locked=True)
    ))
    for claim in claims:
        claim.review_reminder_sent_at = at
    db.commit()
    if not claims:
        return 0

    lines = "\n".join(f"- {c.ref}: \"{c.item.title}\" ({c.item.ref}), sent {_when(c.created_at)}" for c in claims)
    staff = db.scalars(select(User).where(User.role.in_([UserRole.GUARD, UserRole.ADMIN])))
    for person in staff:
        email_service.send_email(
            person.email,
            f"{len(claims)} claim(s) waiting for review",
            f"These claims have been waiting more than 3 days:\n\n{lines}\n\nOpen the Claims desk to review them.",
        )
    return len(claims)


# ---------------- Expiry and clean-up ----------------


def expire_claims(db: Session, at: datetime) -> int:
    return claims_service.expire_overdue(db, at)


def delete_old_proofs(db: Session, at: datetime) -> int:
    """Proof images are private evidence: deleted 2 days after the claim closes."""
    proofs = list(db.scalars(
        select(ClaimProof).join(Claim)
        .where(Claim.closed_at.is_not(None), Claim.closed_at <= at - DELETE_PROOFS_AFTER)
        .limit(BATCH)
    ))
    names = [proof.stored_name for proof in proofs]
    db.execute(delete(ClaimProof).where(ClaimProof.id.in_([p.id for p in proofs])))
    db.commit()
    # Files go only after the rows are gone, so a page never links to a missing file.
    for name in names:
        storage.delete(name, folder="proofs")
    return len(proofs)


def purge_returned(db: Session, at: datetime) -> int:
    """Delete returned items 60 days after the return: the item, its photos,
    custody history, claims, claim history and proofs. The database removes
    the related rows itself (ON DELETE CASCADE); we remove the files."""
    ids = list(db.scalars(
        select(Item.id).where(
            Item.status == ItemStatus.RETURNED,
            Item.returned_at <= at - PURGE_RETURNED_AFTER,
        ).limit(BATCH)
    ))
    if not ids:
        return 0
    photo_files = list(db.scalars(select(ItemPhoto.stored_name).where(ItemPhoto.item_id.in_(ids))))
    proof_files = list(db.scalars(
        select(ClaimProof.stored_name).join(Claim).where(Claim.item_id.in_(ids))
    ))
    db.execute(delete(Item).where(Item.id.in_(ids)))
    db.commit()

    for name in photo_files:
        storage.delete(name, folder="items")
    for name in proof_files:
        storage.delete(name, folder="proofs")
    for item_id in ids:
        matching.notify_removed(item_id)  # the matcher forgets it too
    return len(ids)


# ---------------- Run everything ----------------

JOBS = [
    send_dropoff_reminders,
    send_collect_reminders,
    send_review_reminders,
    expire_claims,
    delete_old_proofs,
    purge_returned,
]


def run_all(db: Session, at: datetime) -> dict[str, int]:
    """Run every job once. One failing job is logged and the others still run."""
    done = {}
    for job in JOBS:
        try:
            done[job.__name__] = job(db, at)
        except Exception:
            db.rollback()
            log.exception("Job %s failed", job.__name__)
            done[job.__name__] = -1
    return done
