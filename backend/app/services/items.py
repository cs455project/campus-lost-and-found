"""Business rules for lost and found items.

Who can see an item?
  - Items on the public pages (found items at an office, open lost reports):
    every signed-in user.
  - Anything else (a found item not yet handed in, a closed lost report...):
    only the person who reported it, and security staff.
  Everyone else gets "not found" (404), not "forbidden" (403), so the API
  doesn't even reveal that the item exists.
"""

import math
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.clock import today
from app.core.config import get_settings
from app.core.errors import bad_request, forbidden, not_found
from app.models import (
    Category,
    Item,
    ItemKind,
    ItemPhoto,
    ItemStatus,
    Office,
    User,
)
from app.models.item import HELD_STATUSES, PUBLIC_STATUSES
from app.schemas.item import CloseLostIn, FoundItemIn, LostItemIn
from app.services import matching, storage
from app.services.locks import lock_item

MAX_DAYS_AGO = 365  # reports about things lost more than a year ago are refused


# ---------------- Checks shared by both report forms ----------------


def _check_common(db: Session, data: LostItemIn | FoundItemIn) -> None:
    category = db.get(Category, data.category_id)
    if category is None or not category.is_active:
        raise bad_request("Choose a category from the list.")
    if data.happened_on > today():
        raise bad_request("The date can't be in the future.")
    if data.happened_on < today() - timedelta(days=MAX_DAYS_AGO):
        raise bad_request("The date can't be more than a year ago.")


# ---------------- Reporting ----------------


def report_lost(db: Session, user: User, data: LostItemIn) -> Item:
    _check_common(db, data)
    item = Item(
        kind=ItemKind.LOST,
        status=ItemStatus.OPEN,
        reporter_id=user.id,
        **data.model_dump(),
    )
    db.add(item)
    db.commit()
    matching.notify_saved(db, item)  # let the matcher (if any) index it
    return item


def _check_office(db: Session, office_id: int) -> None:
    office = db.get(Office, office_id)
    if office is None or not office.is_active:
        raise bad_request("Choose an open security office.")


GUARD_TRACK = "GUARD"


def custody_track_for(category: Category) -> str:
    """Which custody track a new found report uses (US-13, FR-CUS-01/02/08).

    Categories marked custody_required (bicycles, BR-02) must always use the
    SIS Guard track. Every other category also uses it for now: the finder
    names the security office they will hand the item to, and the report
    waits in AWAITING_DROPOFF (the D1 "AWAITING_DEPOSIT" state) until a guard
    confirms receipt. When the direct-handover track (US-10/11) is added, it
    must call this function and refuse DIRECT for custody_required categories.
    """
    return GUARD_TRACK


def report_found(db: Session, user: User, data: FoundItemIn) -> Item:
    _check_common(db, data)
    _check_office(db, data.dropoff_office_id)
    fields = data.model_dump(exclude={"for_lost_id"})

    lost = None
    if data.for_lost_id is not None:
        # "I found this": answering an open lost report.
        lost = db.get(Item, data.for_lost_id)
        if lost is None or lost.kind != ItemKind.LOST or lost.status != ItemStatus.OPEN:
            raise bad_request("That lost report isn't open any more.")
        if lost.reporter_id == user.id:
            raise bad_request("That's your own lost report. Close it instead if you found it.")
        # What the item is comes from the owner's report, not from the form.
        # (The description is the report's plus the finder's optional note.)
        fields.update(title=lost.title, category_id=lost.category_id, colour=lost.colour, brand=lost.brand)

    # Guard track: the item waits for the finder to hand it in at an office.
    if custody_track_for(db.get(Category, fields["category_id"])) != GUARD_TRACK:
        raise bad_request("Only the security-office track is available for found items.")
    item = Item(
        kind=ItemKind.FOUND,
        # Not shown publicly until a guard confirms they have it.
        status=ItemStatus.AWAITING_DROPOFF,
        reporter_id=user.id,
        answers_lost=lost,
        **fields,
    )
    db.add(item)
    db.commit()
    matching.notify_saved(db, item)  # let the matcher (if any) index it
    return item


# ---------------- Who may see and do what ----------------


def is_public(item: Item) -> bool:
    return item.status in PUBLIC_STATUSES[item.kind]


def can_view(item: Item, user: User) -> bool:
    # Returned items are listed on the public Returned Items page, so their
    # page and photos can be opened too.
    returned = item.kind == ItemKind.FOUND and item.status == ItemStatus.RETURNED
    return is_public(item) or returned or user.is_staff or item.reporter_id == user.id


def is_copied_from_lost(item: Item) -> bool:
    """A found item reported with "I found this": its details are the lost
    report's, so the finder can't change them (or its photos)."""
    return item.answers_lost_id is not None


def can_edit(item: Item, user: User) -> bool:
    """The reporter can correct their report while it's still active: a lost
    report that's still open, or a found item not yet handed in. After
    hand-in, security has the item, so the description stays as it is."""
    return (
        item.reporter_id == user.id
        and item.status in (ItemStatus.OPEN, ItemStatus.AWAITING_DROPOFF)
        and not is_copied_from_lost(item)
    )


def _finder_before_handin(item: Item, user: User) -> bool:
    return item.kind == ItemKind.FOUND and item.status == ItemStatus.AWAITING_DROPOFF and item.reporter_id == user.id


def can_change_dropoff(item: Item, user: User) -> bool:
    """The finder may pick a different office until they've handed it in."""
    return _finder_before_handin(item, user)


def can_cancel(item: Item, user: User) -> bool:
    """The finder may withdraw a found report before handing the item in
    (e.g. they gave it back to the owner directly, or reported by mistake)."""
    return _finder_before_handin(item, user)


def can_manage_photos(item: Item, user: User) -> bool:
    """Who may add or remove photos (ignoring the 3-photo limit)."""
    if item.reporter_id == user.id:
        # The reporter can add photos while their report is still active
        # and, for found items, before a guard has taken it in. Not for an
        # "I found this" report: its details belong to the lost report.
        return item.status in (ItemStatus.OPEN, ItemStatus.AWAITING_DROPOFF) and not is_copied_from_lost(item)
    # Guards may photograph a found item at drop-off or while it's at an office.
    return user.is_staff and item.kind == ItemKind.FOUND and item.status in (
        ItemStatus.AWAITING_DROPOFF, *HELD_STATUSES
    )


def can_add_photos(item: Item, user: User) -> bool:
    return can_manage_photos(item, user) and len(item.photos) < get_settings().max_photos_per_item


def can_close(item: Item, user: User) -> bool:
    return item.kind == ItemKind.LOST and item.status == ItemStatus.OPEN and item.reporter_id == user.id


def get_visible_item(db: Session, item_id: int, user: User) -> Item:
    item = db.get(Item, item_id)
    if item is None or not can_view(item, user):
        raise not_found("No item with that number.")
    return item


def detail_for(db: Session, item: Item, user: User) -> dict:
    """Build the detail response. The reporter and the custody history are
    only copied in for staff; for everyone else those fields stay empty."""
    # Imported here to avoid a circular import (those modules import this one's models).
    from app.services import claims, custody

    my_claim = claims.latest_claim_of(db, item, user)
    staff = user.is_staff
    return {
        **{field: getattr(item, field) for field in (
            "id", "ref", "kind", "status", "title", "description", "category",
            "colour", "brand", "location_text", "happened_on", "created_at",
            "current_office", "cover_photo_id", "dropoff_office", "received_at",
            "returned_at",
        )},
        "current_office_full": item.current_office,
        "photo_ids": [photo.id for photo in item.photos],
        "is_mine": item.reporter_id == user.id,
        "can_add_photos": can_add_photos(item, user),
        "can_remove_photos": can_manage_photos(item, user) and bool(item.photos),
        "can_edit": can_edit(item, user),
        "can_close": can_close(item, user),
        "can_change_dropoff": can_change_dropoff(item, user),
        "can_cancel": can_cancel(item, user),
        # Which lost report it answers: only for the finder and staff.
        "answers_lost_id": item.answers_lost_id if staff or item.reporter_id == user.id else None,
        "answers_lost_ref": (
            item.answers_lost.ref if item.answers_lost and (staff or item.reporter_id == user.id) else None
        ),
        "can_see_similar": matching.can_see_similar(item, user),
        # ---- Claims ----
        "can_claim": claims.can_claim(db, item, user),
        "my_claim": my_claim,
        # ---- Staff only ----
        "reporter": item.reporter if staff else None,
        "received_by_name": item.received_by.full_name if staff and item.received_by else None,
        "custody_events": custody.history(item) if staff else [],
        "can_receive": custody.can_receive(item, user),
        "can_transfer": custody.can_transfer(item, user),
        "can_dispose": custody.can_dispose(item, user),
        "claims": claims.claims_on_item(db, item) if staff else [],
    }


# ---------------- Changes after reporting ----------------


def close_lost(db: Session, item: Item, user: User, data: CloseLostIn) -> Item:
    if not can_close(item, user):
        raise forbidden("Only the person who reported it can close an open lost report.")
    item.status = ItemStatus(data.outcome)
    db.commit()
    return item


def change_dropoff(db: Session, item: Item, user: User, office_id: int) -> Item:
    lock_item(db, item)  # a guard may be receiving it right now
    if not can_change_dropoff(item, user):
        raise forbidden("You can only change the office before you hand the item in.")
    _check_office(db, office_id)
    item.dropoff_office_id = office_id
    db.commit()
    return item


def cancel_found(db: Session, item: Item, user: User) -> None:
    """Delete a found report that was never handed in. Nothing else points
    at it yet (claims only start once a guard holds the item)."""
    lock_item(db, item)  # a guard may be receiving it right now
    if not can_cancel(item, user):
        raise forbidden("You can only cancel a found report before you hand the item in.")
    item_id = item.id
    stored_names = [photo.stored_name for photo in item.photos]
    db.delete(item)
    db.commit()
    for name in stored_names:  # files go only after the rows are gone
        storage.delete(name)
    matching.notify_removed(item_id)


def update_report(db: Session, item: Item, user: User, data: LostItemIn) -> Item:
    """The reporter corrects their report. The same checks as reporting apply.
    (The search text updates by itself: it's a generated column.)"""
    lock_item(db, item)  # a guard may be receiving it right now
    if not can_edit(item, user):
        raise forbidden("You can only edit your own report while it's still open "
                        "(for found items: before you hand it in).")
    _check_common(db, data)
    for field, value in data.model_dump().items():
        setattr(item, field, value)
    db.commit()
    matching.notify_saved(db, item)  # what it matches may have changed
    return item


def remove_photo(db: Session, item: Item, user: User, photo_id: int) -> None:
    photo = db.get(ItemPhoto, photo_id)
    if photo is None or photo.item_id != item.id:
        raise not_found("No photo with that number.")
    if not can_manage_photos(item, user):
        raise forbidden("You can't change this item's photos now.")
    stored_name = photo.stored_name
    item.photos.remove(photo)  # delete-orphan: the row is deleted
    db.commit()
    storage.delete(stored_name)  # the file goes only after the row is gone
    matching.notify_saved(db, item)


def add_photo(db: Session, item: Item, user: User, data: bytes, content_type: str, extension: str) -> ItemPhoto:
    if not can_add_photos(item, user):
        if len(item.photos) >= get_settings().max_photos_per_item:
            raise bad_request(f"An item can have at most {get_settings().max_photos_per_item} photos.")
        raise forbidden("You can't add photos to this item now.")

    stored_name = storage.save(data, extension)
    photo = ItemPhoto(
        item_id=item.id,
        stored_name=stored_name,
        content_type=content_type,
        size_bytes=len(data),
        uploaded_by_id=user.id,
    )
    db.add(photo)
    try:
        db.commit()
    except Exception:
        storage.delete(stored_name)  # don't leave an orphan file behind
        raise
    db.refresh(item)
    matching.notify_saved(db, item)  # a new photo can change what it matches
    return photo


# ---------------- Listing with filters and pages ----------------


# A word, optionally starting with "-" to exclude it. Only letters and digits
# are kept, so nothing the user types can break the query syntax.
_SEARCH_WORD = re.compile(r"(-?)([^\W_]+)")
MIN_PREFIX_LENGTH = 2


def build_search_query(text: str) -> str | None:
    """Turn what the user typed into a PostgreSQL search query.

    "sci calc -broken"  ->  "sci:* & calc:* & !broken"

    - Each word matches as a prefix (":*"), so "sci" finds "scientific".
    - All words must match ("&").
    - "-word" excludes items containing that word ("!").
    - Single letters are skipped: "a:*" would match almost everything.
    Returns None if nothing usable was typed.
    """
    parts = []
    for minus, word in _SEARCH_WORD.findall(text.lower()):
        if len(word) < MIN_PREFIX_LENGTH:
            continue
        parts.append(f"!{word}" if minus else f"{word}:*")
    return " & ".join(parts) or None


def list_public(
    db: Session,
    kind: ItemKind,
    category_slug: str | None,
    search: str | None,
    page: int,
    page_size: int,
) -> dict:
    """One page of the Found or Lost page.

    1. Build the filters (kind, public statuses, category, search words).
    2. Count every matching item, so the page knows how many pages exist.
    3. Fetch only this page's rows: ORDER BY newest, then OFFSET/LIMIT.
    Both queries use the same filters, so the count always matches.
    """
    filters = [Item.kind == kind, Item.status.in_(PUBLIC_STATUSES[kind])]

    if category_slug:
        category_id = db.scalar(select(Category.id).where(Category.slug == category_slug))
        if category_id is None:
            return _page([], 0, page, page_size)  # unknown category: nothing matches
        filters.append(Item.category_id == category_id)

    tsquery = build_search_query(search) if search else None
    if tsquery:
        # @@ means "does the item's text match the query". to_tsquery also
        # removes word endings, so "calculators" still finds "calculator".
        filters.append(Item.search_vector.op("@@")(func.to_tsquery("english", tsquery)))

    total = db.scalar(select(func.count()).select_from(Item).where(*filters))

    rows = db.scalars(
        select(Item)
        .where(*filters)
        # id breaks ties, so two items created at the same moment never swap
        # places between pages.
        .order_by(Item.created_at.desc(), Item.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        # Load photos, categories and offices for all rows in a few queries,
        # instead of one extra query per item (the "N+1 problem").
        .options(
            selectinload(Item.photos),
            selectinload(Item.category),
            selectinload(Item.current_office),
        )
    ).all()
    return _page(list(rows), total, page, page_size)


def list_mine(db: Session, user: User, page: int, page_size: int) -> dict:
    filters = [Item.reporter_id == user.id]
    total = db.scalar(select(func.count()).select_from(Item).where(*filters))
    rows = db.scalars(
        select(Item)
        .where(*filters)
        .order_by(Item.created_at.desc(), Item.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .options(selectinload(Item.photos), selectinload(Item.category), selectinload(Item.current_office))
    ).all()
    return _page(list(rows), total, page, page_size)


def _page(items: list[Item], total: int, page: int, page_size: int) -> dict:
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": math.ceil(total / page_size) if total else 0,
    }
