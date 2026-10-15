"""The Returned Items page: found items that went back to their owners.

Everyone signed in sees what was returned, where from and when. Staff also
see who collected it, the claim, the guard who handed it over, and notes.
Nobody ever sees proof images here.

Records disappear completely 60 days after returned_at (services/jobs.py).
"""

import math

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import (
    Category,
    Claim,
    ClaimStatus,
    CustodyEvent,
    CustodyEventType,
    Item,
    ItemKind,
    ItemStatus,
    User,
)

KEEP_DAYS = 60  # shown here for this long, then deleted for good


def list_returned(db: Session, user: User, category_slug: str | None, page: int, page_size: int) -> dict:
    filters = [Item.kind == ItemKind.FOUND, Item.status == ItemStatus.RETURNED]
    if category_slug:
        category_id = db.scalar(select(Category.id).where(Category.slug == category_slug))
        if category_id is None:
            return _page([], 0, page, page_size)
        filters.append(Item.category_id == category_id)

    total = db.scalar(select(func.count()).select_from(Item).where(*filters))
    items = list(db.scalars(
        select(Item).where(*filters)
        .order_by(Item.returned_at.desc(), Item.id.desc())  # uses ix_items_returned_at
        .offset((page - 1) * page_size).limit(page_size)
        .options(selectinload(Item.photos), selectinload(Item.category))
    ))
    ids = [item.id for item in items]

    # Where each item was handed over: the office on its RETURNED custody event.
    # One query for the whole page, not one per item.
    events = {
        event.item_id: event
        for event in db.scalars(
            select(CustodyEvent)
            .where(CustodyEvent.item_id.in_(ids), CustodyEvent.event_type == CustodyEventType.RETURNED)
            .options(selectinload(CustodyEvent.from_office))
        )
    }
    claims = {}
    if user.is_staff:
        claims = {
            claim.item_id: claim
            for claim in db.scalars(
                select(Claim)
                .where(Claim.item_id.in_(ids), Claim.status == ClaimStatus.COLLECTED)
                .options(selectinload(Claim.claimant), selectinload(Claim.handed_over_by),
                         selectinload(Claim.events))
            )
        }

    rows = []
    for item in items:
        event = events.get(item.id)
        row = {
            **{field: getattr(item, field) for field in (
                "id", "ref", "kind", "status", "title", "description", "category", "colour", "brand",
                "location_text", "happened_on", "created_at", "cover_photo_id", "returned_at",
            )},
            "current_office": None,
            "returned_from": event.from_office if event else None,
            "staff": None,
        }
        claim = claims.get(item.id)
        if claim is not None:
            handover = next((e for e in reversed(claim.events) if e.to_status == ClaimStatus.COLLECTED), None)
            row["staff"] = {
                "claim_id": claim.id,
                "claim_ref": claim.ref,
                "collected_by": claim.claimant.full_name,
                "collected_by_roll": claim.claimant.roll_number,
                "handed_over_by": claim.handed_over_by.full_name if claim.handed_over_by else None,
                "note": handover.note if handover else None,
            }
        rows.append(row)
    return _page(rows, total, page, page_size)


def _page(rows: list, total: int, page: int, page_size: int) -> dict:
    return {"items": rows, "total": total, "page": page, "page_size": page_size,
            "total_pages": math.ceil(total / page_size) if total else 0}
