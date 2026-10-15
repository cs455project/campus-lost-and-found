"""The security staff's work on found items: receiving them, registering
walk-ins, moving them between offices, and disposing of unclaimed ones.

Every action here:
  1. checks the item is in the right status for it,
  2. changes the item,
  3. adds a row to the custody history (custody_events),
all inside one database transaction (one commit), so the item and its
history can never disagree.

Guards and the admin can do all of this, at every office.
"""

import math
import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.clock import now
from app.core.errors import bad_request, forbidden
from app.models import (
    CustodyEvent,
    CustodyEventType,
    Item,
    ItemKind,
    ItemStatus,
    Office,
    User,
)
from app.models.item import HELD_STATUSES
from app.schemas.staff import DisposeIn, ReceiveIn, TransferIn, WalkInIn
from app.services import items as items_service
from app.services import matching

CLOSED_STATUSES = [ItemStatus.RETURNED, ItemStatus.DISPOSED, ItemStatus.DONATED]

# The three tabs of the staff desk, and which statuses each one shows.
STAGES = {
    "waiting": [ItemStatus.AWAITING_DROPOFF],
    "held": HELD_STATUSES,
    "closed": CLOSED_STATUSES,
}


# ---------------- Who may do what ----------------


def _staff_found(item: Item, user: User) -> bool:
    return user.is_staff and item.kind == ItemKind.FOUND


def can_receive(item: Item, user: User) -> bool:
    return _staff_found(item, user) and item.status == ItemStatus.AWAITING_DROPOFF


def can_transfer(item: Item, user: User) -> bool:
    # Not while a claim is in progress: the claimant has been told where it is
    # (claims arrive in Phase 5, which decides how a move would be handled).
    return _staff_found(item, user) and item.status == ItemStatus.IN_CUSTODY


def can_dispose(item: Item, user: User) -> bool:
    # Only unclaimed items: nobody is waiting to collect it.
    return _staff_found(item, user) and item.status == ItemStatus.IN_CUSTODY


def _require(allowed: bool, message: str) -> None:
    if not allowed:
        raise forbidden(message)


def _open_office(db: Session, office_id: int) -> Office:
    office = db.get(Office, office_id)
    if office is None or not office.is_active:
        raise bad_request("Choose an open security office.")
    return office


def _log(db: Session, item: Item, event_type: CustodyEventType, actor: User,
         from_office: Office | None = None, to_office: Office | None = None,
         note: str | None = None) -> None:
    db.add(CustodyEvent(
        item=item,
        event_type=event_type,
        from_office=from_office,
        to_office=to_office,
        actor=actor,
        note=note,
    ))


# ---------------- Actions ----------------


def receive_item(db: Session, item: Item, actor: User, data: ReceiveIn) -> Item:
    """A finder hands in an item they reported online."""
    _require(can_receive(item, actor), "Only found items waiting for drop-off can be received.")
    office = _open_office(db, data.office_id)

    item.status = ItemStatus.IN_CUSTODY
    # Set relationships (not just id columns), so the object in memory is
    # up to date straight away.
    item.current_office = office
    item.received_by = actor
    item.received_at = now()
    _log(db, item, CustodyEventType.RECEIVED, actor, to_office=office, note=data.condition_note)
    db.commit()
    matching.notify_saved(db, item)
    return item


def register_walk_in(db: Session, actor: User, data: WalkInIn) -> Item:
    """Someone handed an item in at the desk without reporting it online.
    The guard reports it themselves, and it's held at an office immediately."""
    _require(actor.is_staff, "Only security staff can register walk-in items.")
    office = _open_office(db, data.office_id)
    # Same checks as a normal report (category exists, date not in the future...).
    items_service._check_common(db, data)

    fields = data.model_dump(exclude={"office_id", "handed_in_by"})
    item = Item(
        kind=ItemKind.FOUND,
        status=ItemStatus.IN_CUSTODY,
        reporter=actor,
        dropoff_office=office,
        current_office=office,
        received_by=actor,
        received_at=now(),
        **fields,
    )
    db.add(item)
    note = f"Handed in by: {data.handed_in_by}" if data.handed_in_by else "Walk-in, no online report"
    _log(db, item, CustodyEventType.REGISTERED, actor, to_office=office, note=note)
    db.commit()
    matching.notify_saved(db, item)
    return item


def transfer(db: Session, item: Item, actor: User, data: TransferIn) -> Item:
    """Move an item between offices, e.g. a sub-office sends it to the main office."""
    _require(can_transfer(item, actor), "Only items at an office with no claim in progress can be moved.")
    to_office = _open_office(db, data.to_office_id)
    from_office = item.current_office
    if from_office is not None and from_office.id == to_office.id:
        raise bad_request(f"The item is already at {to_office.name}.")

    item.current_office = to_office
    _log(db, item, CustodyEventType.TRANSFERRED, actor, from_office=from_office,
         to_office=to_office, note=data.note)
    db.commit()
    return item


def dispose(db: Session, item: Item, actor: User, data: DisposeIn) -> Item:
    """Unclaimed item thrown away or donated. Final: it leaves the Found page."""
    _require(can_dispose(item, actor), "Only unclaimed items at an office can be disposed of or donated.")
    from_office = item.current_office
    event_type = CustodyEventType(data.outcome)

    item.status = ItemStatus(data.outcome)
    # It's no longer at any office. (Kept in from_office of the history.)
    item.current_office = None
    _log(db, item, event_type, actor, from_office=from_office, note=data.note)
    db.commit()
    return item


# ---------------- Reading ----------------


def history(item: Item) -> list[dict]:
    return [
        {
            "id": event.id,
            "event_type": event.event_type,
            "from_office": event.from_office,
            "to_office": event.to_office,
            "actor_name": event.actor.full_name if event.actor else None,
            "note": event.note,
            "created_at": event.created_at,
        }
        for event in item.custody_events
    ]


# "FND-0031", "fnd31" or just "31" -> 31
_REF = re.compile(r"^\s*(?:fnd-?)?0*(\d{1,9})\s*$", re.IGNORECASE)


def list_for_staff(
    db: Session,
    stage: str,
    office_id: int | None,
    search: str | None,
    page: int,
    page_size: int,
) -> dict:
    """The staff desk. Office filter means: the office the finder chose
    (waiting), where it is now (held), or where it last was (closed)."""
    filters = [Item.kind == ItemKind.FOUND, Item.status.in_(STAGES[stage])]

    if office_id is not None:
        if stage == "waiting":
            filters.append(Item.dropoff_office_id == office_id)
        elif stage == "held":
            filters.append(Item.current_office_id == office_id)
        else:
            # Closed items are at no office; use the office of their last event.
            last_office = (
                select(CustodyEvent.from_office_id)
                .where(CustodyEvent.item_id == Item.id)
                .order_by(CustodyEvent.id.desc())
                .limit(1)
                .scalar_subquery()
            )
            filters.append(last_office == office_id)

    if search:
        ref = _REF.match(search)
        if ref:
            # The finder at the desk says "my reference is FND-0031".
            filters.append(Item.id == int(ref.group(1)))
        else:
            tsquery = items_service.build_search_query(search)
            if tsquery:
                filters.append(Item.search_vector.op("@@")(func.to_tsquery("english", tsquery)))

    # Waiting and closed: newest first. Held: longest-held first, which is
    # what you want when deciding what to dispose of.
    order = {
        "waiting": [Item.created_at.desc(), Item.id.desc()],
        "held": [Item.received_at.asc(), Item.id.asc()],
        "closed": [Item.updated_at.desc(), Item.id.desc()],
    }[stage]

    total = db.scalar(select(func.count()).select_from(Item).where(*filters))
    rows = db.scalars(
        select(Item)
        .where(*filters)
        .order_by(*order)
        .offset((page - 1) * page_size)
        .limit(page_size)
        .options(
            selectinload(Item.photos),
            selectinload(Item.category),
            selectinload(Item.current_office),
            selectinload(Item.dropoff_office),
            selectinload(Item.reporter),
        )
    ).all()
    return {
        "items": list(rows),
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }
