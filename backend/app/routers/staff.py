"""Staff desk endpoints. Every endpoint requires StaffUser (a guard or the
admin), so students get 403. The rules live in services/custody.py."""

from typing import Annotated, Literal

from fastapi import APIRouter, Query, status

from app.core.deps import DbSession, StaffUser
from app.schemas.item import ItemDetailOut
from app.schemas.staff import DisposeIn, ReceiveIn, StaffItemPage, TransferIn, WalkInIn
from app.services import custody
from app.services import items as items_service

router = APIRouter(prefix="/staff", tags=["staff"])


@router.get("/items", response_model=StaffItemPage)
def staff_items(
    user: StaffUser,
    db: DbSession,
    stage: Literal["waiting", "held", "closed"] = "waiting",
    office_id: int | None = None,
    q: Annotated[str | None, Query(max_length=100, description="Words, or a reference like FND-0031")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=50)] = 20,
):
    """Found items for the staff desk.
    waiting = reported, not yet handed in; held = at an office; closed = returned, disposed or donated."""
    search = q.strip() if q else None
    return custody.list_for_staff(db, stage, office_id, search or None, page, page_size)


@router.post("/items/walk-in", response_model=ItemDetailOut, status_code=status.HTTP_201_CREATED)
def register_walk_in(data: WalkInIn, user: StaffUser, db: DbSession):
    """Register an item someone handed in without an online report."""
    item = custody.register_walk_in(db, user, data)
    return items_service.detail_for(db, item, user)


@router.post("/items/{item_id}/receive", response_model=ItemDetailOut)
def receive_item(item_id: int, data: ReceiveIn, user: StaffUser, db: DbSession):
    item = items_service.get_visible_item(db, item_id, user)
    custody.receive_item(db, item, user, data)
    return items_service.detail_for(db, item, user)


@router.post("/items/{item_id}/transfer", response_model=ItemDetailOut)
def transfer_item(item_id: int, data: TransferIn, user: StaffUser, db: DbSession):
    item = items_service.get_visible_item(db, item_id, user)
    custody.transfer(db, item, user, data)
    return items_service.detail_for(db, item, user)


@router.post("/items/{item_id}/dispose", response_model=ItemDetailOut)
def dispose_item(item_id: int, data: DisposeIn, user: StaffUser, db: DbSession):
    """Mark an unclaimed item as DISPOSED or DONATED. A note is required."""
    item = items_service.get_visible_item(db, item_id, user)
    custody.dispose(db, item, user, data)
    return items_service.detail_for(db, item, user)
