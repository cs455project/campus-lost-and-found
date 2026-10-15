"""Ownership claims: the rules.

The most important idea here is the TRANSITIONS table below. It lists every
status change that is allowed, and who may make it. Every action goes
through _move(), which checks the table, so an impossible jump (for example
SUBMITTED straight to COLLECTED) can never happen, whatever the frontend sends.

Submitting a claim never approves anything: a guard must review it, and the
owner must still pass an ID-card check at the desk before the item is handed over.
"""

import math
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.clock import local, now
from app.core.errors import bad_request, conflict, forbidden, not_found
from app.models import (
    OPEN_CLAIM_STATUSES,
    Claim,
    ClaimEvent,
    ClaimProof,
    ClaimStatus,
    CustodyEvent,
    CustodyEventType,
    Item,
    ItemKind,
    ItemStatus,
    User,
)
from app.models.item import HELD_STATUSES
from app.services import email as email_service
from app.services import storage

S = ClaimStatus
COLLECT_DAYS = 7  # an approved claimant has this long to collect the item
MAX_PROOFS = 3

STAFF, CLAIMANT, SYSTEM = "staff", "claimant", "system"

# (from status, to status) -> who may make that move.
TRANSITIONS = {
    (S.SUBMITTED, S.UNDER_REVIEW): STAFF,
    (S.UNDER_REVIEW, S.NEEDS_MORE_INFO): STAFF,  # "please visit the office"
    (S.NEEDS_MORE_INFO, S.UNDER_REVIEW): STAFF,  # after the visit; NOT the claimant
    (S.UNDER_REVIEW, S.APPROVED): STAFF,
    (S.APPROVED, S.COLLECTED): STAFF,  # handed over after the ID check
    (S.SUBMITTED, S.REJECTED): STAFF,
    (S.UNDER_REVIEW, S.REJECTED): STAFF,
    (S.NEEDS_MORE_INFO, S.REJECTED): STAFF,
    (S.APPROVED, S.REJECTED): STAFF,  # e.g. failed the ID check at the desk
    (S.SUBMITTED, S.WITHDRAWN): CLAIMANT,
    (S.UNDER_REVIEW, S.WITHDRAWN): CLAIMANT,
    (S.NEEDS_MORE_INFO, S.WITHDRAWN): CLAIMANT,
    (S.APPROVED, S.WITHDRAWN): CLAIMANT,
    (S.APPROVED, S.EXPIRED): SYSTEM,  # not collected within 7 days
    (S.SUBMITTED, S.CLOSED): SYSTEM,  # the item went to someone else
    (S.UNDER_REVIEW, S.CLOSED): SYSTEM,
    (S.NEEDS_MORE_INFO, S.CLOSED): SYSTEM,
}


def _allowed(claim: Claim, to: ClaimStatus, user: User | None) -> bool:
    who = TRANSITIONS.get((claim.status, to))
    if who == STAFF:
        return user is not None and user.is_staff
    if who == CLAIMANT:
        return user is not None and user.id == claim.claimant_id
    return who == SYSTEM and user is None


def _move(db: Session, claim: Claim, to: ClaimStatus, actor: User | None, note: str | None = None) -> None:
    """Change a claim's status: check it's allowed, record it in the history,
    and keep the item's status in step. Does not commit."""
    if not _allowed(claim, to, actor):
        raise forbidden(f"A claim that is {claim.status.lower().replace('_', ' ')} can't be moved to "
                        f"{to.lower().replace('_', ' ')} by you.")
    db.add(ClaimEvent(claim=claim, from_status=claim.status, to_status=to, actor=actor, note=note))
    claim.status = to
    if to not in OPEN_CLAIM_STATUSES:
        claim.closed_at = now()
    _sync_item(db, claim.item)


def _sync_item(db: Session, item: Item) -> None:
    """An item held at an office shows "claim in progress" while any claim on
    it is open, and goes back to "at a security office" when none are."""
    if item.status not in HELD_STATUSES:
        return
    db.flush()
    has_open = db.scalar(
        select(func.count()).select_from(Claim)
        .where(Claim.item_id == item.id, Claim.status.in_(OPEN_CLAIM_STATUSES))
    )
    item.status = ItemStatus.CLAIM_PENDING if has_open else ItemStatus.IN_CUSTODY


def _approved_claim(db: Session, item_id: int) -> Claim | None:
    return db.scalar(select(Claim).where(Claim.item_id == item_id, Claim.status == S.APPROVED))


# ---------------- Who may do what (for the page's buttons) ----------------


def can_claim(db: Session, item: Item, user: User) -> bool:
    """A student, a found item at an office, not reported by you, and no open
    claim of yours on it. Staff can't claim: they're the ones who check claims."""
    if user.is_staff:
        return False
    if item.kind != ItemKind.FOUND or item.status not in HELD_STATUSES or item.reporter_id == user.id:
        return False
    return my_open_claim(db, item, user) is None


def my_open_claim(db: Session, item: Item, user: User) -> Claim | None:
    return db.scalar(select(Claim).where(
        Claim.item_id == item.id, Claim.claimant_id == user.id, Claim.status.in_(OPEN_CLAIM_STATUSES)
    ))


def latest_claim_of(db: Session, item: Item, user: User) -> dict | None:
    claim = db.scalar(select(Claim).where(Claim.item_id == item.id, Claim.claimant_id == user.id)
                      .order_by(Claim.id.desc()).limit(1))
    return {"id": claim.id, "ref": claim.ref, "status": claim.status} if claim else None


def claims_on_item(db: Session, item: Item) -> list[dict]:
    """Every claim on an item, oldest first (for staff)."""
    return [
        {"id": c.id, "ref": c.ref, "status": c.status, "claimant_name": c.claimant.full_name,
         "created_at": c.created_at}
        for c in db.scalars(select(Claim).where(Claim.item_id == item.id).order_by(Claim.id))
    ]


def get_claim(db: Session, claim_id: int, user: User) -> Claim:
    """The claimant and staff can open a claim; for anyone else it doesn't exist."""
    claim = db.get(Claim, claim_id)
    if claim is None or not (user.is_staff or claim.claimant_id == user.id):
        raise not_found("No claim with that number.")
    return claim


# ---------------- Claimant actions ----------------


def submit(db: Session, item: Item, user: User, description: str) -> Claim:
    if user.is_staff:
        raise forbidden("Security staff can't claim items. Sign in with a student account.")
    if item.reporter_id == user.id:
        raise bad_request("You reported this item, so you can't claim it.")
    if item.kind != ItemKind.FOUND or item.status not in HELD_STATUSES:
        raise bad_request("Only found items held at a security office can be claimed.")
    if my_open_claim(db, item, user) is not None:
        raise conflict("You already have an open claim on this item.")

    claim = Claim(item=item, claimant=user, description=description, status=S.SUBMITTED)
    db.add(claim)
    db.add(ClaimEvent(claim=claim, from_status=None, to_status=S.SUBMITTED, actor=user))
    _sync_item(db, item)
    db.commit()
    return claim


def can_add_proof(claim: Claim, user: User) -> bool:
    # Only before a guard starts reviewing, so the evidence doesn't change mid-review.
    return claim.claimant_id == user.id and claim.status == S.SUBMITTED and len(claim.proofs) < MAX_PROOFS


def add_proof(db: Session, claim: Claim, user: User, data: bytes, content_type: str, extension: str) -> None:
    if not can_add_proof(claim, user):
        if len(claim.proofs) >= MAX_PROOFS:
            raise bad_request(f"A claim can have at most {MAX_PROOFS} proof images.")
        raise forbidden("Proof can only be added by the claimant before review starts.")
    stored = storage.save(data, extension, folder="proofs")
    db.add(ClaimProof(claim=claim, stored_name=stored, content_type=content_type, size_bytes=len(data)))
    try:
        db.commit()
    except Exception:
        storage.delete(stored, folder="proofs")
        raise
    db.refresh(claim)


def withdraw(db: Session, claim: Claim, user: User) -> Claim:
    _move(db, claim, S.WITHDRAWN, user, "Withdrawn by the claimant")
    db.commit()
    return claim


# ---------------- Staff actions ----------------


def start_review(db: Session, claim: Claim, staff: User) -> Claim:
    _move(db, claim, S.UNDER_REVIEW, staff)
    db.commit()
    return claim


def request_office_visit(db: Session, claim: Claim, staff: User, note: str) -> Claim:
    """The proof isn't conclusive: the claimant must come to the office in
    person. They can't resolve this through the app."""
    _move(db, claim, S.NEEDS_MORE_INFO, staff, note)
    claim.staff_message = note
    db.commit()
    office = claim.item.current_office
    _email(claim, "Please visit the security office about your claim",
           f"We need more information to check your claim {claim.ref} for \"{claim.item.title}\".\n\n"
           f"{note}\n\n"
           f"Please visit {office.name} in person ({office.location_description}; open {office.opening_hours}) "
           "and bring your student ID card.")
    return claim


def resume_review(db: Session, claim: Claim, staff: User, note: str) -> Claim:
    """After the office visit: record what the claimant explained, then decide."""
    _move(db, claim, S.UNDER_REVIEW, staff, f"Office visit: {note}")
    db.commit()
    return claim


def approve(db: Session, claim: Claim, staff: User, note: str | None) -> Claim:
    # Lock the item row until we commit. If two guards approve two different
    # claims at the same moment, the second waits here, then sees the first
    # approval below and stops. (The partial unique index is a second guard.)
    db.execute(select(Item.id).where(Item.id == claim.item_id).with_for_update())
    if _approved_claim(db, claim.item_id) is not None:
        raise conflict("Another claim on this item is already approved. Wait until it's collected, "
                       "rejected or expired.")
    _move(db, claim, S.APPROVED, staff, note)
    claim.approved_at = now()
    claim.collect_by = claim.approved_at + timedelta(days=COLLECT_DAYS)
    claim.staff_message = note
    db.commit()

    office = claim.item.current_office
    _email(claim, "Your claim is approved: collect your item",
           f"Good news: your claim {claim.ref} for \"{claim.item.title}\" is approved.\n\n"
           f"Collect it from {office.name} ({office.location_description}; open {office.opening_hours}) "
           f"by {local(claim.collect_by).strftime('%d %b %Y, %I:%M %p')}.\n"
           "Bring your student ID card: only you can collect it.")
    return claim


def reject(db: Session, claim: Claim, staff: User, reason: str) -> Claim:
    _move(db, claim, S.REJECTED, staff, reason)
    claim.staff_message = reason
    db.commit()
    _email(claim, "Your claim was not approved",
           f"Your claim {claim.ref} for \"{claim.item.title}\" was not approved.\n\nReason: {reason}")
    return claim


def hand_over(db: Session, claim: Claim, staff: User, note: str | None) -> Claim:
    """The approved claimant is at the desk and their ID card matches.
    The item is returned, and every other open claim on it is closed."""
    item = claim.item
    office = item.current_office  # load before changing anything (autoflush)
    _move(db, claim, S.COLLECTED, staff, note or "ID card checked")
    claim.collected_at = now()
    claim.handed_over_by = staff

    for other in db.scalars(select(Claim).where(
        Claim.item_id == item.id, Claim.id != claim.id, Claim.status.in_(OPEN_CLAIM_STATUSES)
    )):
        _move(db, other, S.CLOSED, None, "The item was returned to its owner.")

    item.status = ItemStatus.RETURNED
    item.returned_at = claim.collected_at  # the server's clock, not the browser's
    item.current_office = None
    db.add(CustodyEvent(item=item, event_type=CustodyEventType.RETURNED, from_office=office, actor=staff,
                        note=f"Returned to {claim.claimant.full_name} ({claim.claimant.roll_number or 'staff'}), "
                             f"claim {claim.ref}"))
    db.commit()
    return claim


def expire_overdue(db: Session, at: datetime | None = None) -> int:
    """Approved claims not collected in time become EXPIRED, so the item is
    free for other claimants again. The worker runs this every few minutes;
    `at` lets tests pretend it's days later."""
    at = at or now()
    overdue = list(db.scalars(select(Claim).where(Claim.status == S.APPROVED, Claim.collect_by < at)))
    for claim in overdue:
        _move(db, claim, S.EXPIRED, None, f"Not collected within {COLLECT_DAYS} days.")
    db.commit()
    for claim in overdue:
        _email(claim, "Your approved claim has expired",
               f"Your claim {claim.ref} for \"{claim.item.title}\" expired because the item wasn't collected "
               f"within {COLLECT_DAYS} days. If it's still yours, contact the security office.")
    return len(overdue)


def _email(claim: Claim, subject: str, body: str) -> None:
    email_service.send_email(claim.claimant.email, subject, body)


# ---------------- Reading ----------------


def detail_for(db: Session, claim: Claim, user: User) -> dict:
    staff = user.is_staff
    mine = claim.claimant_id == user.id
    approved_other = _approved_claim(db, claim.item_id)
    on_hold = claim.is_open and approved_other is not None and approved_other.id != claim.id

    other_claims = []
    if staff:
        other_claims = [
            {"id": c.id, "ref": c.ref, "status": c.status, "claimant_name": c.claimant.full_name,
             "created_at": c.created_at}
            for c in db.scalars(select(Claim).where(Claim.item_id == claim.item_id, Claim.id != claim.id)
                                .order_by(Claim.id))
        ]

    def staff_can(to: ClaimStatus) -> bool:
        return staff and TRANSITIONS.get((claim.status, to)) == STAFF

    return {
        "id": claim.id,
        "ref": claim.ref,
        "status": claim.status,
        "description": claim.description,
        "item": claim.item,
        "proof_ids": [p.id for p in claim.proofs],
        "staff_message": claim.staff_message,
        "created_at": claim.created_at,
        "approved_at": claim.approved_at,
        "collect_by": claim.collect_by,
        "collected_at": claim.collected_at,
        "events": [
            {"id": e.id, "from_status": e.from_status, "to_status": e.to_status,
             "actor_name": e.actor.full_name if e.actor else None, "note": e.note, "created_at": e.created_at}
            for e in claim.events
        ],
        "is_mine": mine,
        "can_withdraw": mine and (claim.status, S.WITHDRAWN) in TRANSITIONS,
        "can_add_proof": can_add_proof(claim, user),
        "on_hold": on_hold,
        "claimant": claim.claimant if staff else None,
        "other_claims": other_claims,
        "can_start_review": staff_can(S.UNDER_REVIEW) and claim.status == S.SUBMITTED,
        "can_request_info": staff_can(S.NEEDS_MORE_INFO),
        "can_resume_review": staff_can(S.UNDER_REVIEW) and claim.status == S.NEEDS_MORE_INFO,
        "can_approve": staff_can(S.APPROVED) and approved_other is None,
        "can_reject": staff_can(S.REJECTED),
        "can_hand_over": staff_can(S.COLLECTED),
    }


# Tabs of the staff Claims desk.
STAFF_STAGES = {
    "review": [S.SUBMITTED, S.UNDER_REVIEW],
    "visit": [S.NEEDS_MORE_INFO],
    "collect": [S.APPROVED],
    "closed": [S.COLLECTED, S.REJECTED, S.WITHDRAWN, S.EXPIRED, S.CLOSED],
}


def _rows(db: Session, filters: list, order, page: int, page_size: int) -> dict:
    total = db.scalar(select(func.count()).select_from(Claim).where(*filters))
    claims = db.scalars(
        select(Claim).where(*filters).order_by(*order).offset((page - 1) * page_size).limit(page_size)
        .options(selectinload(Claim.item).selectinload(Item.photos), selectinload(Claim.claimant))
    ).all()
    approved_items = set(db.scalars(select(Claim.item_id).where(
        Claim.status == S.APPROVED, Claim.item_id.in_([c.item_id for c in claims])
    )))
    rows = [
        {
            "id": c.id, "ref": c.ref, "status": c.status, "item": c.item,
            "claimant_name": c.claimant.full_name, "claimant_roll": c.claimant.roll_number,
            "created_at": c.created_at, "updated_at": c.updated_at, "collect_by": c.collect_by,
            "on_hold": c.status in (S.SUBMITTED, S.UNDER_REVIEW, S.NEEDS_MORE_INFO)
            and c.item_id in approved_items,
        }
        for c in claims
    ]
    return {"items": rows, "total": total, "page": page, "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0}


def list_mine(db: Session, user: User, page: int, page_size: int) -> dict:
    return _rows(db, [Claim.claimant_id == user.id], [Claim.created_at.desc(), Claim.id.desc()], page, page_size)


def list_for_staff(db: Session, stage: str, page: int, page_size: int) -> dict:
    # Oldest first for work queues (first come, first served); newest first for closed.
    order = [Claim.updated_at.desc(), Claim.id.desc()] if stage == "closed" else [Claim.created_at, Claim.id]
    if stage == "collect":
        order = [Claim.collect_by, Claim.id]  # soonest deadline first
    return _rows(db, [Claim.status.in_(STAFF_STAGES[stage])], order, page, page_size)
